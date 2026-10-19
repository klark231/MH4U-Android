"""Static recompiler tests (no game data).

1. Instruction-level fuzzing (ARM, VFP, media, Thumb) against Unicorn's ARM11 MPCore.
2. A synthetic program built with clang (recursion, switch jump tables, function
   pointers, struct copies, VFP, 64-bit division helpers, ARM<->Thumb calls) is
   recompiled as a whole and every function is compared against Unicorn.
"""
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(__file__)
TOOLS = os.path.join(HERE, "..", "tools")
sys.path.insert(0, TOOLS)

needed = ["clang", "ld.lld", "llvm-nm", "llvm-objcopy"]
pytestmark = pytest.mark.skipif(any(shutil.which(t) is None for t in needed),
                                reason="clang/lld toolchain not installed")

from recomp import fuzz  # noqa: E402


@pytest.mark.parametrize("mode,only,seed", [("arm", None, 11), ("arm", "vfp", 12), ("arm", "vfpvec", 16),
                                            ("arm", "media", 13), ("arm", "mul", 14), ("thumb", None, 15)])
def test_instruction_fuzz(mode, only, seed):
    insns, stats, failures = fuzz.fuzz(mode, 300, 4, seed, only)
    assert len(insns) >= 250
    assert not failures, {f"{w:08x}": items[0][0] for w, items in list(failures.items())[:5]}
    assert stats["pass"] > 0


PROGRAM = r"""
typedef unsigned int u32;
struct Vec { float x, y, z; };
int g_counter;
static int helper(int x) { return x * 3 + 1; }
int fib(int n) { return n < 2 ? n : fib(n - 1) + fib(n - 2); }
int dispatch(int op, int a, int b) {
    switch (op) {
    case 0: return a + b;   case 1: return a - b;   case 2: return a * b;
    case 3: return a & b;   case 4: return a | b;   case 5: return a ^ b;
    case 6: return a << (b & 31); case 7: return helper(a); case 8: return fib(a & 7);
    default: return -1;
    }
}
typedef int (*binop)(int, int);
static int add(int a, int b) { return a + b; }
static int sub(int a, int b) { return a - b; }
binop ops[2] = { add, sub };
int apply(int which, int a, int b) { return ops[which & 1](a, b); }
float dot(const struct Vec *a, const struct Vec *b) { return a->x * b->x + a->y * b->y + a->z * b->z; }
long long div64(long long a, long long b) { return a / (b | 1) + a % (b | 3); }
unsigned udiv(unsigned a, unsigned b) { return a / (b | 1) + a % (b | 2); }
void copy_vec(struct Vec *d, const struct Vec *s) { *d = *s; g_counter++; }
__attribute__((target("thumb"))) int thumb_sum(const int *p, int n) {
    int s = 0;
    for (int i = 0; i < (n & 15); i++) s += p[i];
    return s + fib(5);
}
int call_thumb(const int *p) { return thumb_sum(p, 7) + 1; }
double conv(int x, float f) { return (double)x * f; }
int clamp_sum(const short *v, int n) {
    int s = 0;
    for (int i = 0; i < (n & 31); i++) { s += v[i]; if (s > 1000) s = 1000; if (s < -1000) s = -1000; }
    return s;
}
"""


def _recompile_and_compare(tmp_path, sources, cases=12, seed=9):
    """Build `sources` (name -> text, .c or .S) at 0x00100000, recompile every function
    symbol and compare each one against Unicorn. Returns {address: verdict}."""
    from recomp import build as rbuild
    from recomp.analysis import Image
    from recomp.gametest import BUF_BASE, BUF_SIZE, STACK_SIZE, STACK_TOP, TLS, Tester, function_verdict
    from recomp.harness.machines import Region
    import random
    import recompile
    import difftest as dt

    base = 0x00100000
    objs = []
    for name, text in sources.items():
        src = tmp_path / name
        src.write_text(text)
        obj = tmp_path / (name + ".o")
        flags = [f for f in dt.CFLAGS if f != "-O2"] + ["-O1"]
        subprocess.run(["clang", *flags, "-c", str(src), "-o", str(obj)], check=True)
        objs.append(str(obj))
    rt, elf, binp = tmp_path / "rt.o", tmp_path / "p.elf", tmp_path / "p.bin"
    subprocess.run(["clang", *dt.CFLAGS, "-c", dt.RT_SOURCE, "-o", str(rt)], check=True)
    script = tmp_path / "link.ld"
    script.write_text(dt.LINKER_SCRIPT.replace(f"0x{dt.CAND_BASE:08x}", f"0x{base:08x}"))
    subprocess.run(["ld.lld", "-T", str(script), "--entry=0", *objs, str(rt), "-o", str(elf)], check=True)
    subprocess.run(["llvm-objcopy", "-O", "binary", str(elf), str(binp)], check=True)
    code = binp.read_bytes()
    entries = {}
    for line in subprocess.run(["llvm-readelf", "-s", str(elf)], capture_output=True, text=True,
                               check=True).stdout.splitlines():
        parts = line.split()
        if len(parts) >= 8 and parts[3] == "FUNC":
            a = int(parts[1], 16)
            entries[a & ~1] = "thumb" if a & 1 else "arm"
    text_end = max(entries) + 0x1000
    img = Image(code, base, min(len(code), text_end - base))
    out = tmp_path / "recomp"
    res = recompile.recompile(img, entries, str(out), log=lambda *a: None)
    assert res["report"]["trap_instructions"] == 0, res["problems"]
    assert not res["problems"], res["problems"]
    lib = rbuild.build(str(out), 2, "O1", ["RC_CHECK_RETURNS"], "prog.so", [rbuild.HARNESS])
    size = (len(code) + 0x10000 + 0xFFF) & ~0xFFF
    rw = [Region(base, size, True, code.ljust(size, b"\0")),
          Region(STACK_TOP - STACK_SIZE, STACK_SIZE, True, bytes(STACK_SIZE)),
          Region(BUF_BASE, BUF_SIZE, True, bytes(BUF_SIZE)),
          Region(TLS, 0x1000, True, bytes(0x1000))]
    tester = Tester(lib, [], rw, seed=3)
    rng = random.Random(seed)
    verdicts = {}
    for addr, mode in tester.funcs:
        outs = [tester.run_case(addr, bool(mode), rng) for _ in range(cases)]
        verdicts[addr] = (function_verdict([o for o, _ in outs]),
                          [n for o, n in outs if not o.startswith(("pass", "agree", "protocol"))][:1],
                          sum(o.startswith("pass") for o, _ in outs))
    return res, entries, verdicts


def test_synthetic_program_recompiles_exactly(tmp_path):
    res, entries, verdicts = _recompile_and_compare(tmp_path, {"p.c": PROGRAM})
    assert res["report"]["jump_tables"] >= 1
    bad = {hex(k): v for k, v in verdicts.items() if v[0] not in ("pass", "agree-fault-only", "inconclusive")}
    assert not bad, bad
    assert sum(v[0] == "pass" for v in verdicts.values()) >= len(entries) // 2


# VFP short-vector mode (LEN=3, as in the game's audio mixers) and an RVCT-fplib-style
# special-case helper that reads a descriptor after its call site and "returns" into one of
# several continuations in its caller.
VECTOR_AND_MRET = r"""
    .syntax unified
    .arm
    .text
    .global vec_kernel
    .type vec_kernel, %function
vec_kernel:                         @ r0 = dst (25 words), r1 = src (8 floats + scalar)
    push    {r4, lr}
    vpush   {d8-d15}
    vmrs    ip, fpscr
    str     ip, [sp, #-4]!
    bic     ip, ip, #0x370000
    orr     ip, ip, #0x30000
    vmsr    fpscr, ip
    vldr    s0, [r1, #32]
    vldr    s1, [r1, #4]
    vldmia  r1, {s8-s15}
    vmul.f32 s16, s8, s0            @ vector * scalar
    vadd.f32 s20, s12, s8           @ vector + vector
    vmla.f32 s16, s20, s0
    vsub.f32 s14, s16, s20          @ destination wraps inside its bank: s14 s15 s8 s9
    vneg.f32 s24, s16
    vabs.f32 s28, s1                @ scalar source broadcast to s28-s31
    vcvt.s32.f32 s2, s16            @ conversions are always scalar
    vstmia  r0!, {s8-s15}
    vstmia  r0!, {s16-s31}
    vstr    s2, [r0]
    ldr     ip, [sp], #4
    vmsr    fpscr, ip
    vadd.f32 s9, s10, s11           @ scalar again
    vstr    s9, [r0, #4]
    vpop    {d8-d15}
    pop     {r4, pc}

    .global special
    .type special, %function
special:                            @ descriptor: one 3-bit code per input class (r0 & 3)
    ldr     r6, [lr], #4
    and     r5, r0, #3
    add     r5, r5, r5, lsl #1
    lsr     r6, r6, r5
    and     r6, r6, #7
    subs    ip, r6, #4
    bhs     1f
    add     ip, lr, r6, lsl #2      @ codes 0-3: continue in the caller
    pop     {r4, r5, r6, lr}
    bx      ip
1:  add     pc, pc, ip, lsl #2      @ codes 4-7: handled here, return to the caller's caller
    nop
    b       2f
    b       3f
    b       4f
    b       5f
2:  mov     r0, #100
    pop     {r4, r5, r6, pc}
3:  mov     r0, #101
    pop     {r4, r5, r6, pc}
4:  mov     r0, #102
    pop     {r4, r5, r6, pc}
5:  mov     r0, #103
    pop     {r4, r5, r6, pc}

    .global mret_caller
    .type mret_caller, %function
mret_caller:
    push    {r4, r5, r6, lr}
    bl      special
    .word   0x00000E43              @ class 0 -> 3, 1 -> 0, 2 -> 1, 3 -> 7
    add     r0, r0, #10
    add     r0, r0, #20
    bx      lr
    mvn     r0, r0
    bx      lr
"""


def test_vector_mode_and_multi_return_helpers(tmp_path):
    res, entries, verdicts = _recompile_and_compare(tmp_path, {"v.S": VECTOR_AND_MRET}, cases=48)
    funcs = res["funcs"]
    by_name = {}
    for line in subprocess.run(["llvm-readelf", "-s", str(tmp_path / "p.elf")], capture_output=True,
                               text=True, check=True).stdout.splitlines():
        parts = line.split()
        if len(parts) >= 8 and parts[3] == "FUNC":
            by_name[parts[7]] = int(parts[1], 16)
    vk, sp, mc = by_name["vec_kernel"], by_name["special"], by_name["mret_caller"]
    assert funcs[vk].vector_insns > 10
    assert funcs[sp].mret and len(funcs[sp].jtabs) == 1
    assert list(funcs[mc].mret_calls.values()) == [[mc + 12, mc + 16, mc + 24]]
    assert verdicts[sp][0] == "protocol"
    for a in (vk, mc):
        assert verdicts[a][0] == "pass", (hex(a), verdicts[a])
    assert verdicts[mc][2] == 48


# Hand-written code in the style of the game's video decoder: LR set once by `add lr, pc`,
# then a conditional branch or a jump through a table of offsets is the call; a routine
# picks its tail through a table of negative offsets placed after the code it jumps to,
# with the index already scaled.
COMPUTED_CALLS = r"""
    .syntax unified
    .arm
    .text
fill0:
    mov     r6, #1
    str     r6, [r11]
    mov     pc, lr
fill1:
    mov     r6, #2
    str     r6, [r11]
    mov     pc, lr
fill2:
    mov     r6, #3
    str     r6, [r11, #4]
    mov     pc, lr
fill3:                              @ unused
    mov     r6, #4
    str     r6, [r11, #8]
    mov     pc, lr
fill_table:
    .word   fill0 - fill_table
    .word   fill1 - fill_table
    .word   fill2 - fill_table
    .word   fill1 - fill_table          @ the same routine twice
    .global fill_dispatch
    .type fill_dispatch, %function
fill_dispatch:                      @ r6: selector, r11: destination
    adr     r5, fill_table
    mov     r8, #0
    lsls    r7, r6, #31
    addne   r8, r8, #8
    tst     r6, #2
    addne   r8, r8, #4
    ldr     r6, [r5, r8]
    add     pc, r5, r6

    .global block_decode
    .type block_decode, %function
block_decode:                       @ r12: routine, r4: flags, r11: destination
    push    {r4, r5, r6, r7, r8, lr}
    and     r12, r12, #3
    tst     r4, #1
    add     lr, pc, #12             @ both calls below return to 1:
    bne     special
    adr     r5, call_table
    ldr     r6, [r5, r12, lsl #2]
    add     pc, r6, r5
1:  add     r0, r6, #1
    pop     {r4, r5, r6, r7, r8, pc}
call_table:
    .word   r_a - call_table
    .word   r_b - call_table
    .word   fill_dispatch - call_table
    .word   r_c - call_table
r_a:
    mov     r6, #10
    mov     pc, lr
r_b:
    mov     r6, #20
    str     r6, [r11, #12]
    mov     pc, lr
r_c:
    mov     r6, #30
    mov     pc, lr
special:
    mov     r6, #40
    mov     pc, lr

    .global cond_call
    .type cond_call, %function
cond_call:                          @ LR set only when the call happens
    push    {r4, r6, lr}
    cmp     r4, #0
    addeq   lr, pc, #4
    beq     r_a
    add     r6, r6, #5
    add     r0, r6, #3
    pop     {r4, r6, pc}

    .global far_return
    .type far_return, %function
far_return:                         @ LR in two steps, as for a return point out of reach
    push    {r4, r5, r6, r7, lr}
    and     r12, r12, #3
    add     lr, pc, #8
    add     lr, lr, #4
    adr     r5, call_table
    ldr     r6, [r5, r12, lsl #2]
    add     pc, r6, r5
    add     r0, r6, #2
    pop     {r4, r5, r6, r7, pc}

    .global frame_owner
    .type frame_owner, %function
frame_owner:                        @ r12: size class; the routines it jumps to end its frame
    push    {r10, r11, r12, lr}
    bl      frame_helper            @ LR now points here, not at our caller
    adr     r4, tail_a - 8          @ a target too far for one immediate: two adds
    add     r4, r4, #8
    cmp     r12, #4
    movle   pc, r4
    adr     r4, tail_b
    cmp     r12, #8
    movle   pc, r4
    b       tail_c
frame_helper:
    mov     r10, #7
    bx      lr
tail_a:
    push    {r0, r1, r2, r3}
    add     r0, r0, r10
    str     r0, [sp]
    pop     {r0, r1, r2, r3, r10, r11, r12, pc}
tail_b:
    push    {r0, r1, r2, r3}
    sub     r0, r0, r10
    str     r0, [sp]
    pop     {r0, r1, r2, r3, r10, r11, r12, pc}
tail_c:
    push    {r0, r1, r2, r3}
    eor     r0, r0, r10
    str     r0, [sp]
    pop     {r0, r1, r2, r3, r10, r11, r12, pc}
"""


def test_computed_calls_and_negative_offset_tables(tmp_path):
    res, entries, verdicts = _recompile_and_compare(tmp_path, {"c.S": COMPUTED_CALLS}, cases=48)
    by_name = {}
    for line in subprocess.run(["llvm-readelf", "-s", str(tmp_path / "p.elf")], capture_output=True,
                               text=True, check=True).stdout.splitlines():
        parts = line.split()
        if len(parts) >= 8 and parts[3] in ("FUNC", "NOTYPE"):
            by_name[parts[7]] = int(parts[1], 16)
    funcs = res["funcs"]
    fd, bd, fo, fr, cc = (by_name[n] for n in ("fill_dispatch", "block_decode", "frame_owner", "far_return",
                                               "cond_call"))
    assert [len(t) for t in funcs[fd].ijump_targets.values()] == [4]
    assert len(funcs[bd].link_calls) == 2 and set(funcs[bd].link_calls.values()) == {bd + 32}
    assert list(funcs[fr].link_calls.values()) == [fr + 28] and not funcs[fr].ijump_targets
    assert funcs[cc].link_calls == {cc + 12: cc + 20}
    assert by_name["tail_a"] in funcs and by_name["tail_b"] in funcs     # constant computed jumps
    for a in (fd, bd, fo, fr, cc):
        assert verdicts[a][0] == "pass", (hex(a), verdicts[a])


def test_init_array_entries():
    """Static constructors from RVCT's relative init table (synthetic image)."""
    import struct
    import recompile
    from recomp.analysis import Image
    base = 0x00100000
    code = bytearray(0x1000)
    hws = [0x4C08, 0x447C, 0x4D08, 0x447D,              # ldr r4,[pc,#0x20]; add r4,pc; ldr r5,...; add r5,pc
           0x6820, 0x1900, 0x4780, 0x1D24, 0x42AC, 0xD1F9]
    struct.pack_into(f"<{len(hws)}H", code, 0, *hws)
    struct.pack_into("<I", code, 0x24, 0x100100 - 0x100006)  # table start, relative to add r4,pc
    struct.pack_into("<I", code, 0x28, 0x100108 - 0x10000A)  # table end
    struct.pack_into("<ii", code, 0x100, 0x100200 - 0x100100, 0x100301 - 0x100104)
    found = recompile.init_array_entries(Image(bytes(code), base, len(code)))
    assert found == {0x100200: "arm", 0x100300: "thumb"}


def test_data_pointer_to_a_function_another_one_falls_into():
    """RVCT ends a function that hands over to the next one with `pop {r4, lr}; mov r0, r0`
    and falls into it; a vtable can still point at that next function (0x00b159e0 in the
    game, found when the interpreter had to run it). A number that happens to point after
    an ordinary instruction stays out."""
    import struct
    import recompile
    from recomp.analysis import Analyzer, Image
    base = 0x00100000
    words = [0xE92D4010, 0xE1A04000, 0xE2844001, 0xE8BD4010, 0xE1A00000,   # a: push; ...; pop {r4, lr}; nop
             0xE2800001, 0xE12FFF1E,                                         # b: add r0, r0, #1; bx lr
             0xE92D4010, 0xE1A04000, 0xE2844001, 0xE8BD8010]                 # c: push; mov; add; pop {r4, pc}
    code = bytearray(0x200)
    struct.pack_into(f"<{len(words)}I", code, 0, *words)
    a, b, c = base, base + 0x14, base + 0x1c
    struct.pack_into("<3I", code, 0x100, a, b, c + 8)       # a vtable, and a number into c
    img = Image(bytes(code), base, 0x100)
    an = Analyzer(img)
    entries = {a: "arm", c: "arm"}
    first = an.analyze_all(entries)
    cover, fall_into = recompile.code_cover(first)
    assert b in fall_into["arm"] and c + 8 in fall_into["arm"]
    found = recompile.data_pointer_entries(img, an, [(base + 0x100, 12)], dict(entries), cover, fall_into)
    assert found == {b: "arm"}


def test_code_cave_runs_like_the_patched_code(tmp_path):
    """A community code mod's "code cave": two instructions of the code patched to branch
    into new code in the padding after it (the rest of the code's last page), one with a BL
    (a call that returns) and one with a B (a hook that branches back into the code). With
    the range taken to the end of the page (recompile.code_size) the cave is recompiled
    with the rest, without traps, and each function gives what Unicorn gives."""
    import random
    import struct
    import recompile
    from recomp import build as rbuild
    from recomp.analysis import Image
    from recomp.gametest import BUF_BASE, BUF_SIZE, STACK_SIZE, STACK_TOP, TLS, Tester
    from recomp.harness.machines import Region

    base = 0x00100000

    def branch(op, at, target):
        return op << 24 | ((target - at - 8) >> 2) & 0xFFFFFF
    words = [0xE52DE004, 0xE3A00001, branch(0xEB, base + 0x08, base + 0x800), 0xE49DF004,  # f: bl cave_a
             0xE3A01005, branch(0xEA, base + 0x14, base + 0x808), 0xE0800001, 0xE12FFF1E]   # g: b cave_b
    page = bytearray(struct.pack(f"<{len(words)}I", *words).ljust(0x1000, b"\0"))
    struct.pack_into("<4I", page, 0x800, 0xE2800029, 0xE12FFF1E,                 # cave_a: add r0, #41; bx lr
                     0xE0811001, branch(0xEA, base + 0x80C, base + 0x18))       # cave_b: add r1, r1; b back

    class Seg:
        def __init__(self, address, size):
            self.address, self.size, self.padded_size = address, size, (size + 0xFFF) & ~0xFFF

    class Header:
        text, ro = Seg(base, 4 * len(words)), Seg(base + 0x1000, 0x1000)
    assert recompile.code_size(Header, bytes(page)) == 0x1000
    assert recompile.code_size(Header, bytes(page[:0x20]).ljust(0x2000, b"\0")) == 0x20   # zeros: as before

    out = tmp_path / "recomp"
    res = recompile.recompile(Image(bytes(page), base, 0x1000), {base: "arm", base + 0x10: "arm"}, str(out),
                              log=lambda *a: None)
    assert res["report"]["trap_instructions"] == 0 and not res["problems"], res["problems"]
    assert base + 0x800 in res["funcs"]                     # the BL's target
    assert base + 0x808 in res["funcs"][base + 0x10].labels  # the B's: part of the function it hooks
    lib = rbuild.build(str(out), 2, "O1", ["RC_CHECK_RETURNS"], "cave.so", [rbuild.HARNESS])
    size = 0x10000
    rw = [Region(base, size, True, bytes(page).ljust(size, b"\0")),
          Region(STACK_TOP - STACK_SIZE, STACK_SIZE, True, bytes(STACK_SIZE)),
          Region(BUF_BASE, BUF_SIZE, True, bytes(BUF_SIZE)),
          Region(TLS, 0x1000, True, bytes(0x1000))]
    tester = Tester(lib, [], rw, seed=5)
    rng = random.Random(7)
    assert sorted(a for a, _ in tester.funcs) == [base, base + 0x10, base + 0x800]
    for addr, mode in tester.funcs:
        outcomes = [tester.run_case(addr, bool(mode), rng)[0] for _ in range(8)]
        assert all(o.startswith("pass") for o in outcomes), (hex(addr), outcomes)
    # without the cave in the range the hook is a branch to nowhere, as before
    res = recompile.recompile(Image(bytes(page), base, 0x20), {base: "arm", base + 0x10: "arm"},
                              str(tmp_path / "plain"), log=lambda *a: None)
    assert base + 0x800 not in res["funcs"] and res["report"]["trap_instructions"] == 1
