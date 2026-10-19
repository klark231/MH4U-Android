"""Tests for tools/find_services.py on synthetic, hand-encoded ARM/Thumb code only."""
import json
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import find_services as fs  # noqa: E402

BASE = 0x00100000
LAYOUT = fs.Layout(BASE, (BASE, BASE + 0x1000), (BASE + 0x1000, BASE + 0x2000),
                   (BASE + 0x2000, BASE + 0x3000), (BASE + 0x3000, BASE + 0x4000))
STR_SRV, STR_GPU = BASE + 0x1000, BASE + 0x1010          # rodata
G_SRV, G_GPU, G_EVENT, G_READY = BASE + 0x3000, BASE + 0x3004, BASE + 0x3008, BASE + 0x2000

# ── a tiny assembler: just the encodings the tests need ───────────────────────────


def push(*regs):
    return 0xE92D0000 | sum(1 << r for r in regs)


def pop(*regs):
    return 0xE8BD0000 | sum(1 << r for r in regs)


def mrc_tls(rd):
    return 0xEE1D0F70 | rd << 12


def ldr_lit(rd, at, lit, cond=0xE):
    return cond << 28 | 0x059F0000 | rd << 12 | (lit - (at + 8))


def ldr(rt, rn, imm=0):
    return 0xE5900000 | rn << 16 | rt << 12 | imm


def str_(rt, rn, imm=0):
    return 0xE5800000 | rn << 16 | rt << 12 | imm


def str_pre_wb(rt, rn, imm):
    return 0xE5A00000 | rn << 16 | rt << 12 | imm


def mov(rd, rm):
    return 0xE1A00000 | rd << 12 | rm


def mov_imm(rd, imm):
    return 0xE3A00000 | rd << 12 | imm


def cmp_imm(rn, imm):
    return 0xE3500000 | rn << 16 | imm


def svc(n):
    return 0xEF000000 | n


def branch(at, target, link=False, cond=0xE):
    return cond << 28 | (0x0B000000 if link else 0x0A000000) | ((target - at - 8) >> 2) & 0xFFFFFF


BX_LR = 0xE12FFF1E
LE = 0xD


class Asm:
    def __init__(self):
        self.buf = bytearray(LAYOUT.bss[0] - BASE)
        self.funcs: dict[int, tuple[int, str]] = {}

    def arm(self, addr, words, size=None):
        for i, w in enumerate(words):
            struct.pack_into("<I", self.buf, addr - BASE + 4 * i, w)
        self.funcs[addr] = (size or 4 * len(words), "arm")

    def thumb(self, addr, halfwords):
        for i, h in enumerate(halfwords):
            struct.pack_into("<H", self.buf, addr - BASE + 2 * i, h)
        self.funcs[addr] = (2 * len(halfwords), "thumb")

    def data(self, addr, raw):
        self.buf[addr - BASE:addr - BASE + len(raw)] = raw

    def image(self):
        return fs.Image(bytes(self.buf), LAYOUT)


def thumb_blx(at, target):
    off = target - (at + 4)
    return [0xF000 | (off >> 12) & 0x7FF, 0xE800 | (off >> 1) & 0x7FF]


# Function addresses of the synthetic program.
F_CONNECT = BASE + 0x000      # svc ConnectToPort wrapper:   (Handle* out, const char* name)
F_SRV_INIT = BASE + 0x040     # ConnectToPort(&G_SRV, "srv:")
F_GSH = BASE + 0x080          # srv:GetServiceHandle request: (Handle* out, name, len, flags)
F_GSH_WRAP = BASE + 0x0D0     # checks a flag, predicated error code, tail call (like the SDK)
F_GPU_INIT = BASE + 0x100     # GetServiceHandle(&G_GPU, "gsp::Gpu", 8, 0) through the wrapper
F_FLUSH = BASE + 0x140        # gsp::Gpu FlushDataCache(addr) on [G_GPU]
F_THUMB = BASE + 0x180        # Thumb code calling F_GPU_INIT with BLX
F_EVENT = BASE + 0x1C0        # svc CreateEvent wrapper: (Handle* out, reset type)
F_EV_INIT = BASE + 0x200      # CreateEvent(&G_EVENT, 0)
F_WAIT = BASE + 0x240         # WaitSynchronization1(G_EVENT, -1), inline svc


def build() -> Asm:
    a = Asm()
    a.arm(F_CONNECT, [0xE52D0004, svc(0x2D), 0xE59D2000, 0xE5821000, 0xE28DD004, BX_LR])
    a.arm(F_SRV_INIT, [
        push(4, 14),
        ldr_lit(0, F_SRV_INIT + 0x04, F_SRV_INIT + 0x14),
        ldr_lit(1, F_SRV_INIT + 0x08, F_SRV_INIT + 0x18),
        branch(F_SRV_INIT + 0x0C, F_CONNECT, link=True),
        pop(4, 15), G_SRV, STR_SRV], size=0x14)
    a.arm(F_GSH, [
        push(4, 5, 14),                                  # 0x00
        mov(5, 0),                                       # 0x04
        mrc_tls(4),                                      # 0x08
        ldr_lit(0, F_GSH + 0x0C, F_GSH + 0x40),          # 0x0C header
        str_pre_wb(0, 4, 0x80),                          # 0x10 cmd[0], r4 = cmdbuf
        ldr(0, 1, 0), str_(0, 4, 4),                     # 0x14 name bytes 0-3 -> cmd[1]
        ldr(0, 1, 4), str_(0, 4, 8),                     # 0x1C name bytes 4-7 -> cmd[2]
        str_(2, 4, 0xC),                                 # 0x24 name length -> cmd[3]
        ldr_lit(0, F_GSH + 0x28, F_GSH + 0x44),          # 0x28 &srv handle
        ldr(0, 0, 0),                                    # 0x2C
        svc(0x32),                                       # 0x30 SendSyncRequest
        ldr(1, 4, 0xC), str_(1, 5, 0),                   # 0x34 *out = response word 3
        pop(4, 5, 15),                                   # 0x3C
        0x00050100, G_SRV], size=0x40)
    a.arm(F_GSH_WRAP, [
        ldr_lit(12, F_GSH_WRAP + 0x00, F_GSH_WRAP + 0x1C),        # service manager ready?
        ldr(12, 12, 0),
        cmp_imm(12, 0),
        ldr_lit(0, F_GSH_WRAP + 0x0C, F_GSH_WRAP + 0x20, cond=LE),   # ldrle r0, =error
        branch(F_GSH_WRAP + 0x10, F_GSH_WRAP + 0x18, cond=LE),     # ble out
        branch(F_GSH_WRAP + 0x14, F_GSH),                         # tail call
        BX_LR,                                                     # out:
        G_READY, 0xD8A067F8], size=0x1C)
    a.arm(F_GPU_INIT, [
        push(4, 14),
        ldr_lit(0, F_GPU_INIT + 0x04, F_GPU_INIT + 0x20),
        ldr_lit(1, F_GPU_INIT + 0x08, F_GPU_INIT + 0x24),
        mov_imm(2, 8), mov_imm(3, 0),
        branch(F_GPU_INIT + 0x14, F_GSH_WRAP, link=True),
        pop(4, 15), 0, G_GPU, STR_GPU], size=0x20)
    a.arm(F_FLUSH, [
        push(4, 14),
        mrc_tls(4),
        ldr_lit(0, F_FLUSH + 0x08, F_FLUSH + 0x28),
        str_pre_wb(0, 4, 0x80),
        str_(1, 4, 4),
        ldr_lit(0, F_FLUSH + 0x14, F_FLUSH + 0x2C),
        ldr(0, 0, 0),
        svc(0x32),
        pop(4, 15), 0, 0x00080082, G_GPU], size=0x28)
    a.thumb(F_THUMB, [0xB500, *thumb_blx(F_THUMB + 2, F_GPU_INIT), 0xBD00])
    a.arm(F_EVENT, [0xE52D0004, svc(0x17), 0xE59D2000, 0xE5821000, 0xE28DD004, BX_LR])
    a.arm(F_EV_INIT, [
        push(4, 14),
        ldr_lit(0, F_EV_INIT + 0x04, F_EV_INIT + 0x14),
        mov_imm(1, 0),
        branch(F_EV_INIT + 0x0C, F_EVENT, link=True),
        pop(4, 15), G_EVENT], size=0x14)
    a.arm(F_WAIT, [
        ldr_lit(0, F_WAIT, F_WAIT + 0x18),
        ldr(0, 0, 0),
        0xE3E02000,                                      # mvn r2, #0
        mov(3, 2),
        svc(0x24),
        BX_LR, G_EVENT], size=0x18)
    a.data(STR_SRV, b"srv:\0")
    a.data(STR_GPU, b"gsp::Gpu\0")
    return a


def program():
    a = build()
    img = a.image()
    return fs.Program(img, a.funcs, fs.scan_callgraph(img, a.funcs)), a


def rows_by(rows, func):
    return [r for r in rows if r["function"] == f"0x{func:08x}"]


# ── decoder ───────────────────────────────────────────────────────────────────────

def test_decoder_core_forms():
    assert fs.decode_arm(mrc_tls(4), BASE).ops == (("tls", 4),)
    assert fs.decode_arm(svc(0x32), BASE).ops == (("svc", 0x32),)
    ins = fs.decode_arm(str_pre_wb(0, 4, 0x80), BASE)
    assert ins.ops[0][0] == "st" and ins.ops[-1][:2] == ("set", 4)       # store, then writeback
    ins = fs.decode_arm(ldr_lit(0, BASE, BASE + 0x10, cond=LE), BASE)
    (op, rt, addr, size), = ins.ops
    assert ins.cond == LE and (op, rt, size) == ("ld", 0, 4)
    assert fs.ev(addr, [None] * 17) == fs.C(BASE + 0x10)               # pc-relative literal
    assert fs.decode_arm(pop(4, 15), BASE).flow == ("ret",)
    assert fs.decode_arm(BX_LR, BASE).flow == ("ret",)
    assert fs.decode_arm(branch(BASE, BASE + 0x40, link=True), BASE).flow == ("bl", BASE + 0x40, "arm")
    assert fs.decode_arm(cmp_imm(0, 1), BASE).sets_flags
    vpush = fs.decode_arm(0xED2D8B02, BASE)                # vpush {d8}: two stores, sp -= 8
    assert [op[0] for op in vpush.ops] == ["st", "st", "set"]


def test_thumb_decoder_blx_and_literal():
    a = Asm()
    a.thumb(BASE, [0x4801, *thumb_blx(BASE + 2, BASE + 0x40), 0xBD00])
    img = a.image()
    ins = fs.decode_thumb(BASE, img.read16)
    assert ins.ops == (("ld", 0, ("k", BASE + 4 + 4), 4),)              # ldr r0, [pc, #4]
    blx = fs.decode_thumb(BASE + 2, img.read16)
    assert blx.size == 4 and blx.flow == ("bl", BASE + 0x40, "arm")
    assert fs.decode_thumb(BASE + 6, img.read16).flow == ("ret",)


# ── interpreter ───────────────────────────────────────────────────────────────────

def test_request_site_header_and_handle():
    prog, _ = program()
    info = prog.analyze(F_FLUSH)
    (site, req), = info.requests.items()
    assert site == F_FLUSH + 0x1C
    assert req.header == fs.C(0x00080082)
    assert req.handle == ("mem", fs.C(G_GPU), 0)
    assert req.cmd[4] == ("arg", 1, 0)                                  # word 1 = second argument


def test_predicated_wrapper_keeps_arguments():
    prog, _ = program()
    info = prog.analyze(F_GSH_WRAP)
    (call,) = [c for c in info.calls.values() if c.target == F_GSH]
    # `ldrle r0, =error; ble out` must not clobber r0 on the path that reaches the tail call
    assert call.args[:4] == (("arg", 0, 0), ("arg", 1, 0), ("arg", 2, 0), ("arg", 3, 0))


def test_svc_wrapper_output_and_summary():
    prog, _ = program()
    info = prog.analyze(F_EVENT)
    assert info.effects == {(F_EVENT + 0xC, ("arg", 0, 0)): ("svcout", (0x17, 1), 0)}
    assert prog.summary_of(info) == ((("arg", 0, 0), ("svcout", (0x17, 1), 0)),)


# ── whole program ─────────────────────────────────────────────────────────────────

def test_whole_program_services_and_commands():
    prog, _ = program()
    prog.run_all()
    assert F_CONNECT in prog.svc_wrappers and prog.svc_wrappers[F_EVENT] == 0x17
    facts = prog.facts()
    assert {prog.provenance_service(v) for v in facts[fs.C(G_SRV)]} == {"srv:"}
    assert {prog.provenance_service(v) for v in facts[fs.C(G_GPU)]} == {"gsp::Gpu"}
    assert set(facts[fs.C(G_EVENT)]) == {("svcout", (0x17, 1), 0)}

    rows = fs.request_rows(prog)
    (flush,) = rows_by(rows, F_FLUSH)
    assert (flush["service"], flush["header"], flush["command"]) == ("gsp::Gpu", "0x00080082", "FlushDataCache")
    assert flush["handle"] == f"0x{G_GPU:08x}"
    (gsh,) = rows_by(rows, F_GSH)
    assert (gsh["service"], gsh["command"]) == ("srv:", "GetServiceHandle")
    assert "names gsp::Gpu" in gsh["note"]


def test_thumb_caller_is_in_call_graph():
    prog, a = program()
    assert F_THUMB in prog.callers[F_GPU_INIT]
    info = prog.analyze(F_THUMB)
    assert [c.target for c in info.calls.values()] == [F_GPU_INIT]


def test_svc_rows_resolve_waited_handle():
    prog, _ = program()
    prog.run_all()
    rows = fs.svc_rows(prog)
    (wait,) = [r for r in rows if r["header"] == "0x24"]
    assert wait["command"] == "WaitSynchronization1" and wait["handle"] == f"[0x{G_EVENT:08x}]"
    (ev,) = [r for r in rows if r["header"] == "0x17"]
    assert ev["note"] == "wrapper"


# ── tables ────────────────────────────────────────────────────────────────────────

def test_command_tables():
    assert fs.command_name("gsp::Gpu", 0x00080082) == ("FlushDataCache", "")
    name, note = fs.command_name("gsp::Gpu", 0x000800C2)                # same id, other params
    assert name == "FlushDataCache~" and "0x00080082" in note
    assert fs.command_name("APT:A", 0x00020080)[0] == "Initialize"      # alias of APT:U
    assert fs.command_name("fs:USER?", 0x080201C2)[0] == "OpenFile"
    assert fs.guess_service(0x080201C2) == "fs:USER"
    assert fs.guess_service(0x00010040) is None                         # used by several services
    for service, table in fs.SERVICE_COMMANDS.items():
        for header in table:
            assert header >> 16, (service, hex(header))


# ── command line ──────────────────────────────────────────────────────────────────

def test_main_writes_tables(tmp_path):
    a = build()
    code = tmp_path / "code.bin"
    code.write_bytes(bytes(a.buf))
    funcs = tmp_path / "functions.tsv"
    funcs.write_text("address\tsize\tmode\n" + "".join(
        f"0x{addr:08x}\t0x{size:x}\t{mode}\n" for addr, (size, mode) in sorted(a.funcs.items())))
    versions = tmp_path / "versions.json"
    seg = {k: [f"0x{getattr(LAYOUT, k)[0]:08x}", f"0x{getattr(LAYOUT, k)[1] - getattr(LAYOUT, k)[0]:x}"]
           for k in ("text", "rodata", "data", "bss")}
    versions.write_text(json.dumps({"versions": [{"target": True, "segments": seg}]}))
    out, handles = tmp_path / "out" / "services.tsv", tmp_path / "handles.tsv"
    rc = fs.main(["--code", str(code), "--exheader", str(tmp_path / "none"), "--versions", str(versions),
                  "--functions", str(funcs), "--callgraph", str(tmp_path / "none.tsv"),
                  "-o", str(out), "--handles", str(handles), "-q"])
    assert rc == 0
    lines = out.read_text().splitlines()
    assert lines[0].split("\t") == list(fs.COLUMNS)
    assert any("\tgsp::Gpu\t0x00080082\tFlushDataCache\t" in line for line in lines)
    assert any("\tsvc\t0x2d\tConnectToPort\t" in line for line in lines)
    assert any(line.startswith(f"0x{G_GPU:08x}\thandle(") and "\tgsp::Gpu\t" in line
               for line in handles.read_text().splitlines())
