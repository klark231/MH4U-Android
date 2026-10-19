import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import exheader as exh  # noqa: E402
import find_missing_functions as fmf  # noqa: E402

BASE = 0x00100000
PUSH, RET, NOP = 0xE92D4010, 0xE12FFF1E, 0xE1A00000


def header():
    seg = exh.CodeSegment
    return exh.ExHeader("T", 0, 0, seg(BASE, 1, 0x100), 0, seg(BASE + 0x1000, 1, 0x20), seg(BASE + 0x2000, 1, 0x20),
                        0, None)


def image(words: dict[int, int]) -> bytes:
    buf = bytearray(0x2020)
    for a, w in words.items():
        struct.pack_into("<I", buf, a - BASE, w)
    return bytes(buf)


def test_finds_gap_prologues_and_pointed_functions():
    code = image({
        BASE + 0x00: PUSH, BASE + 0x04: RET,           # known function
        BASE + 0x10: PUSH, BASE + 0x14: RET,           # missed, nothing points at it
        BASE + 0x40: PUSH, BASE + 0x44: RET,           # missed, referenced from rodata (a vtable)
        BASE + 0x80: NOP,                              # gap without a prologue
        BASE + 0x1000: BASE + 0x40,                    # rodata pointer
        BASE + 0x2000: BASE + 0x00,                    # data pointer to a known function
    })
    found = fmf.find_missing(code, header(), [(BASE, 8)])
    assert found == {BASE + 0x10: "gap-prologue", BASE + 0x40: "gap-prologue+data-pointer"}


def test_prologue_inside_known_function_is_ignored():
    code = image({BASE: PUSH, BASE + 0x10: PUSH})
    assert fmf.find_missing(code, header(), [(BASE, 0x100)]) == {}


def test_gaps():
    assert fmf.gaps([(0x10, 0x10), (0x18, 0x10), (0x40, 4)], 0, 0x50) == [(0, 0x10), (0x28, 0x40), (0x44, 0x50)]
