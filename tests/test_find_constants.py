"""tools/find_constants.py on hand-encoded synthetic code."""
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import find_constants as fc  # noqa: E402

BASE = 0x00100000
F400 = 0x43C80000  # 400.0f


def image(words=None, halfwords=None, size=0x100):
    buf = bytearray(size)
    for a, w in (words or {}).items():
        struct.pack_into("<I", buf, a - BASE, w)
    for a, h in (halfwords or {}).items():
        struct.pack_into("<H", buf, a - BASE, h)
    return bytes(buf)


def test_arm_imm_and_float_helpers():
    assert fc.arm_imm(0xE3A02E19) == 0x190        # mov r2, #400
    assert fc.arm_imm(0xE3A010F0) == 0xF0         # mov r1, #240
    assert fc.arm_imm(0xE3A00000) == 0
    assert fc.float_word(400.0) == F400
    assert fc.word_float(0x43700000) == 240.0


def test_ldr_and_vldr_literals_with_folding_flag():
    code = image({
        BASE + 0x00: 0xE59F0004,   # ldr r0, [pc, #4]    -> pool at +0x0C
        BASE + 0x04: 0xED9F0A01,   # vldr s0, [pc, #4]   -> pool at align4(+0x04 + 8) + 4 = +0x10
        BASE + 0x08: 0xE12FFF1E,   # bx lr
        BASE + 0x0C: F400,
        BASE + 0x10: F400,
        BASE + 0x14: 0xE51F0010,   # ldr r0, [pc, #-0x10] -> +0x0C (negative offset)
    })
    hits = fc.literal_loads(code, BASE, BASE, BASE + 0x18, F400)
    got = {(h.site - BASE, h.literal - BASE, h.kind, h.folded) for h in hits}
    assert got == {(0x00, 0x0C, "ldr", True), (0x04, 0x10, "vldr", False), (0x14, 0x0C, "ldr", True)}
    assert fc.literal_loads(code, BASE, BASE, BASE + 0x18, 0x12345678) == []


def test_function_table_limits_decoding_and_adds_thumb():
    code = image(words={
        BASE + 0x00: 0xE59F0000,   # ARM: ldr r0, [pc, #0] -> pool at +0x08
        BASE + 0x04: 0xE12FFF1E,
        BASE + 0x08: F400,
        BASE + 0x20: 0xE59F0000,   # same pattern outside any function: ignored with a table
        BASE + 0x28: F400,
        BASE + 0x48: F400,         # Thumb pool
    }, halfwords={
        BASE + 0x40: 0x4801,       # Thumb: ldr r0, [pc, #4] -> align4(+0x40 + 4) + 4 = +0x48
        BASE + 0x42: 0x4770,       # bx lr
    })
    funcs = [fc.Function(BASE, 0x08, False, "arm_fn"), fc.Function(BASE + 0x40, 0x08, True, "thumb_fn")]
    hits = fc.literal_loads(code, BASE, BASE, BASE + 0x100, F400, funcs)
    assert {(h.site - BASE, h.kind) for h in hits} == {(0x00, "ldr"), (0x40, "ldr.t")}
    everything = fc.literal_loads(code, BASE, BASE, BASE + 0x100, F400)
    assert {h.site - BASE for h in everything} == {0x00, 0x20}       # no table: ARM only
    name = fc.Namer(funcs)
    assert name(BASE + 0x04) == "arm_fn+0x4"
    assert name(BASE + 0x30) == "?"


def test_mov_immediates_and_pairs():
    code = image({
        BASE + 0x00: 0xE3A02E19,   # mov r2, #0x190
        BASE + 0x04: 0xE3A010F0,   # mov r1, #0xf0
        BASE + 0x08: 0x13A03050,   # movne r3, #0x50 (conditional still counts)
        BASE + 0x0C: 0xE3E01027,   # mvn r1, #0x27  -> -40
        BASE + 0x10: 0xE2802E19,   # add r2, r0, #0x190 (not a MOV)
        BASE + 0x40: 0xE3A0BE19,   # mov fp, #0x190 (no 240 nearby)
    })
    movs = fc.mov_immediates(code, BASE, BASE, BASE + 0x100, (0x190, 0x50, -40))
    assert [(m.site - BASE, m.rd, m.value, m.mnemonic) for m in movs] == [
        (0x00, 2, 0x190, "mov"), (0x08, 3, 0x50, "mov"), (0x0C, 1, 0xFFFFFFD8, "mvn"),
        (0x40, 11, 0x190, "mov")]
    pairs = fc.mov_pairs(code, BASE, BASE, BASE + 0x100, 400, 240, gap=8)
    assert [(a.site - BASE, b.site - BASE) for a, b in pairs] == [(0x00, 0x04)]


def test_pica_headers():
    assert fc.decode_pica_header(0x803F0041) == fc.PicaHeader(0x41, 0xF, 3, True)
    assert fc.decode_pica_header(0x000F0068) == fc.PicaHeader(0x68, 0xF, 0, False)
    assert fc.decode_pica_header(0x00000041) is None      # no byte mask
    assert fc.decode_pica_header(0x400F0041) is None      # reserved bits set
    assert fc.decode_pica_header(0x000F0400) is None      # beyond the register file
    code = image({
        BASE + 0x00: 0xE59F3004,   # ldr r3, [pc, #4] -> +0x0C
        BASE + 0x04: 0xE59F2004,   # ldr r2, [pc, #4] -> +0x10
        BASE + 0x08: 0xE12FFF1E,
        BASE + 0x0C: 0x803F0041,   # VIEWPORT_WIDTH..INVH, 4 words
        BASE + 0x10: 0x000F0111,   # framebuffer flush
    })
    hits = fc.pica_loads(code, BASE, BASE, BASE + 0x14, [0x41, 0x68])
    assert [(h.site - BASE, hdr.reg) for h, hdr in hits] == [(0x00, 0x41)]
