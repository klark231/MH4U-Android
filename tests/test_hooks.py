"""Hook points: recompiled functions that native code wraps or replaces (RHookFn)."""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

pytestmark = pytest.mark.skipif(not (shutil.which("clang") and shutil.which("ld.lld")),
                                reason="needs clang and ld.lld")

PROGRAM = r"""
__attribute__((noinline)) int add1(int x) { return x + 1; }
__attribute__((noinline)) int twice_add1(int x) { return add1(x) * 2; }
__attribute__((noinline)) int call_through(int (*f)(int), int x) { return f(x) + 3; }
__attribute__((noinline, target("thumb"))) int thumb_sub(int a, int b) { return a - b; }
__attribute__((noinline)) int use_thumb(int a) { return thumb_sub(a, 4) * 3; }
typedef int (*fp)(int);
__attribute__((noinline)) int jump_to(fp f, int x) { return f(x); }
__attribute__((noinline)) int call_jump(fp f, int x) { return jump_to(f, x) + 1; }
__attribute__((noinline)) int screen_rows(void) { return 400; }
__attribute__((noinline)) int clamp_rows(int y) { return y > 400 ? 400 : y; }
"""


def _build(tmp_path, hooks_by_name, tunables=None):
    import difftest as dt
    import recompile
    from recomp import build as rbuild
    from recomp.analysis import Image

    base = 0x00100000
    src, obj = tmp_path / "p.c", tmp_path / "p.o"
    src.write_text(PROGRAM)
    flags = [f for f in dt.CFLAGS if f != "-O2"] + ["-O1"]
    subprocess.run(["clang", *flags, "-c", str(src), "-o", str(obj)], check=True)
    rt, elf, binp = tmp_path / "rt.o", tmp_path / "p.elf", tmp_path / "p.bin"
    subprocess.run(["clang", *dt.CFLAGS, "-c", dt.RT_SOURCE, "-o", str(rt)], check=True)
    script = tmp_path / "link.ld"
    script.write_text(dt.LINKER_SCRIPT.replace(f"0x{dt.CAND_BASE:08x}", f"0x{base:08x}"))
    subprocess.run(["ld.lld", "-T", str(script), "--entry=0", str(obj), str(rt), "-o", str(elf)], check=True)
    subprocess.run(["llvm-objcopy", "-O", "binary", str(elf), str(binp)], check=True)
    code = binp.read_bytes()
    entries, names = {}, {}
    for line in subprocess.run(["llvm-readelf", "-s", str(elf)], capture_output=True, text=True,
                               check=True).stdout.splitlines():
        parts = line.split()
        if len(parts) >= 8 and parts[3] == "FUNC":
            a = int(parts[1], 16)
            entries[a & ~1] = "thumb" if a & 1 else "arm"
            names[parts[7]] = a
    img = Image(code, base, min(len(code), max(entries) + 0x1000 - base))
    hooks = {names[n] & ~1: n for n in hooks_by_name}
    hooks[0x00ABC000] = "not_a_function"
    out = tmp_path / "recomp"
    res = recompile.recompile(img, entries, str(out), log=lambda *a: None, hooks=hooks,
                              tunables=tunables(code, base) if tunables else None)
    lib = rbuild.build(str(out), 2, "O1", ["RC_CHECK_RETURNS"], "prog.so", [rbuild.HARNESS])
    return res, lib, code, names, out


def test_hook_points(tmp_path):
    from recomp.gametest import STACK_SIZE, STACK_TOP
    from recomp.harness.machines import RET_TRAP, ST_RETURN, CRCtx, NativeMachine, Region, State

    res, lib, code, names, out = _build(tmp_path, ["add1", "thumb_sub"])
    assert res["report"]["hook_points"] == 2
    assert res["problems"] == ["hook 0x00abc000 (not_a_function): not a function"]
    table = (out / "src" / "table.c").read_text()
    assert "rc_hook_count = 2;" in table
    add1, thumb_sub = names["add1"] & ~1, names["thumb_sub"] & ~1
    assert f"static void fb_{add1:08x}(RCtx *ctx)" in "".join(p.read_text() for p in (out / "src").glob("f*.c"))

    size = (len(code) + 0x10000 + 0xFFF) & ~0xFFF
    m = NativeMachine(lib, [Region(0x00100000, size, True, code.ljust(size, b"\0")),
                            Region(STACK_TOP - STACK_SIZE, STACK_SIZE, True, bytes(STACK_SIZE))])
    hook_t = ctypes.CFUNCTYPE(None, ctypes.POINTER(CRCtx), ctypes.c_void_p)
    func_t = ctypes.CFUNCTYPE(None, ctypes.POINTER(CRCtx))
    m.lib.rh_set_hook.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    m.lib.rh_set_hook.restype = ctypes.c_int

    def call(fn, *args):
        st = State()
        for i, a in enumerate(args):
            st.r[i] = a & 0xFFFFFFFF
        st.r[13], st.r[14] = STACK_TOP - 0x100, RET_TRAP
        r = m.run(names[fn] & ~1, bool(names[fn] & 1), st)
        assert r.status == ST_RETURN, r
        return r.state.r[0]

    assert call("twice_add1", 5) == 12
    calls = []

    @hook_t
    def wrap(ctx, original):          # runs the game's function, then changes its result
        calls.append(ctx.contents.r[0])
        func_t(original)(ctx)
        ctx.contents.r[0] += 100

    @hook_t
    def replace(ctx, original):       # doesn't run it: returns 7 to the caller
        c = ctx.contents
        c.r[0] = 7
        c.r[15], c.expect_next = c.expect_next or c.r[14], 0

    assert m.lib.rh_set_hook(add1, ctypes.cast(wrap, ctypes.c_void_p)) == 1
    assert m.lib.rh_set_hook(names["twice_add1"] & ~1, ctypes.cast(wrap, ctypes.c_void_p)) == 0
    assert call("twice_add1", 5) == (6 + 100) * 2       # direct call
    assert call("call_through", names["add1"], 1) == 2 + 100 + 3   # indirect call
    assert call("add1", 9) == 110                        # entered directly
    assert calls == [5, 1, 9]
    assert m.lib.rh_set_hook(add1, ctypes.cast(replace, ctypes.c_void_p)) == 1
    assert call("twice_add1", 5) == 14
    assert m.lib.rh_set_hook(add1, None) == 1
    assert call("twice_add1", 5) == 12
    assert m.lib.rh_set_hook(thumb_sub, ctypes.cast(wrap, ctypes.c_void_p)) == 1
    assert call("use_thumb", 10) == (6 + 100) * 3


def test_tunables(tmp_path):
    """Instructions listed as tunables read their immediate from rc_tunables[], which the
    host can change: `mov r0, #400` and the `cmp r0, #400; movgt r0, #400` clamp (the
    game's top-screen size) follow a new value."""
    import struct
    from recomp.gametest import STACK_SIZE, STACK_TOP
    from recomp.harness.machines import RET_TRAP, ST_RETURN, NativeMachine, Region, State

    def find(code, base):
        words = {struct.unpack_from("<I", code, i)[0]: base + i for i in range(0, len(code) - 3, 4)}
        found = {words[w]: (400, "rows") for w in (0xE3A00E19, 0xE3500E19, 0xA3A00E19, 0xC3A00E19) if w in words}
        assert len(found) == 3, found           # mov r0, #400 / cmp r0, #400 / movge (or gt) r0, #400
        return {**found, 0x00ABC100: (5, "missing")}

    res, lib, code, names, out = _build(tmp_path, [], tunables=find)
    assert res["report"]["tunables"] == 3
    assert any("tunable 0x00abc100 (missing)" in p for p in res["problems"])
    table = (out / "src" / "table.c").read_text()
    assert "rc_tunable_count = 3;" in table and '"rows", "rows", "rows"' in table

    size = (len(code) + 0x10000 + 0xFFF) & ~0xFFF
    m = NativeMachine(lib, [Region(0x00100000, size, True, code.ljust(size, b"\0")),
                            Region(STACK_TOP - STACK_SIZE, STACK_SIZE, True, bytes(STACK_SIZE))])

    def call(fn, *args):
        st = State()
        for i, a in enumerate(args):
            st.r[i] = a & 0xFFFFFFFF
        st.r[13], st.r[14] = STACK_TOP - 0x100, RET_TRAP
        r = m.run(names[fn] & ~1, bool(names[fn] & 1), st)
        assert r.status == ST_RETURN, r
        return r.state.r[0]

    assert call("screen_rows") == 400 and call("clamp_rows", 500) == 400 and call("clamp_rows", 410) == 400
    values = (ctypes.c_uint32 * 3).in_dll(m.lib, "rc_tunables")
    assert list(values) == [400, 400, 400]
    for i in range(3):
        values[i] = 424
    assert call("screen_rows") == 424 and call("clamp_rows", 500) == 424 and call("clamp_rows", 410) == 410


def test_stopped_tail_call_leaves_no_state(tmp_path):
    """A run that stops in a tail call (to an address that isn't code) must not leave its
    return address for the next run: the harness starts each run clean."""
    from recomp.gametest import STACK_SIZE, STACK_TOP
    from recomp.harness.machines import RET_TRAP, ST_NOFUNC, ST_RETURN, NativeMachine, Region, State

    res, lib, code, names, out = _build(tmp_path, [])
    size = (len(code) + 0x10000 + 0xFFF) & ~0xFFF
    m = NativeMachine(lib, [Region(0x00100000, size, True, code.ljust(size, b"\0")),
                            Region(STACK_TOP - STACK_SIZE, STACK_SIZE, True, bytes(STACK_SIZE))])

    def run(fn, *args):
        st = State()
        for i, a in enumerate(args):
            st.r[i] = a & 0xFFFFFFFF
        st.r[13], st.r[14] = STACK_TOP - 0x100, RET_TRAP
        return m.run(names[fn] & ~1, bool(names[fn] & 1), st)

    assert run("call_jump", names["add1"], 4).state.r[0] == 6
    assert run("call_jump", 0x00ABC000, 4).status == ST_NOFUNC     # stops inside jump_to's tail call
    r = run("add1", 5)
    assert r.status == ST_RETURN and r.state.r[0] == 6, r
