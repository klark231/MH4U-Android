import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import disasm  # noqa: E402
import select_batch  # noqa: E402

BASE = 0x00100000


def image(words, extra=None):
    buf = bytearray(0x100)
    for a, w in words.items():
        struct.pack_into("<I", buf, a - BASE, w)
    for a, b in (extra or {}).items():
        buf[a - BASE:a - BASE + len(b)] = b
    return bytes(buf)


def test_disasm_resolves_literals_and_strings():
    code = image({
        BASE + 0x00: 0xE59F0004,          # ldr r0, [pc, #4]   -> literal at +0x0C
        BASE + 0x04: 0xE28F1004,          # add r1, pc, #4     -> +0x10 "hello"
        BASE + 0x08: 0xE12FFF1E,          # bx lr
        BASE + 0x0C: 0x00100080,          # literal: a named global
    }, {BASE + 0x10: b"hello\0"})
    lines = disasm.disassemble(code, BASE, BASE, 0x0C, False, {0x00100080: "g_counter"})
    assert "g_counter" in lines[0]
    assert "'hello'" in lines[1]
    assert lines[2].split()[2] == "bx"


def test_is_simple_filters():
    plain = image({BASE: 0xE0800001, BASE + 4: 0xE12FFF1E})          # add; bx lr
    blx = image({BASE: 0xE12FFF33, BASE + 4: 0xE12FFF1E})            # blx r3
    svc = image({BASE: 0xEF000032, BASE + 4: 0xE12FFF1E})
    vfp = image({BASE: 0xEE300A01, BASE + 4: 0xE12FFF1E})            # vadd.f32
    pop_pc = image({BASE: 0xE49DF004})                                 # pop {pc}: allowed
    assert select_batch.is_simple(plain, BASE, BASE, 8, False)
    assert not select_batch.is_simple(blx, BASE, BASE, 8, False)
    assert not select_batch.is_simple(svc, BASE, BASE, 8, False)
    assert not select_batch.is_simple(vfp, BASE, BASE, 8, False)
    assert select_batch.is_simple(vfp, BASE, BASE, 8, True)
    assert select_batch.is_simple(pop_pc, BASE, BASE, 4, False)


def test_disasm_resumes_after_data_words():
    # bx lr; an undecodable word (jump-table style data); then real code again.
    code = image({BASE: 0xE12FFF1E, BASE + 4: 0xFFFFFFFF, BASE + 8: 0xE3A0000E, BASE + 12: 0xE12FFF1E})
    lines = disasm.disassemble(code, BASE, BASE, 16, False, {})
    assert [l.split()[2] for l in lines] == ["bx", ".word", "mov", "bx"]
