"""Tests for tools/mt_dti.py on a hand-assembled synthetic ARM image (no game data)."""
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import mt_dti  # noqa: E402

BASE = 0x00100000
CTOR = BASE
DTI_AREA = BASE + 0x8000
MEM_END = BASE + 0x10000

# (name, parent index or None, size)
CLASSES = [
    ("MtObject", None, 0x04),
    ("cResource", 0, 0x68),
    ("rTexture", 1, 0xF0),
    ("nDraw::Resource", 0, 0x10),
]


def ldr_pc(rd, at, target):
    return 0xE59F0000 | rd << 12 | (target - (at + 8))


def adr(rd, at, target):
    off = target - (at + 8)
    assert 0 <= off < 0x100
    return 0xE28F0000 | rd << 12 | off


def mov(rd, imm):
    assert imm < 0x100
    return 0xE3A00000 | rd << 12 | imm


def bl(at, target):
    return 0xEB000000 | (((target - (at + 8)) >> 2) & 0xFFFFFF)


def build_image():
    words = {}
    # Fake constructor: store name/parent/size, and use size as the "id" at +0x1C.
    ctor = [0xE5801004, 0xE5802008, 0xE580300C, 0xE580301C, 0xE12FFF1E]
    for i, w in enumerate(ctor):
        words[CTOR + 4 * i] = w
    blobs = {}
    for i, (name, parent, size) in enumerate(CLASSES):
        f = BASE + 0x100 + i * 0x40
        dti = DTI_AREA + i * 0x20
        words[f + 0x00] = 0xE92D4010                         # push {r4, lr}
        words[f + 0x04] = ldr_pc(0, f + 0x04, f + 0x1C)      # r0 = &dti
        words[f + 0x08] = adr(1, f + 0x08, f + 0x24)         # r1 = name
        words[f + 0x0C] = (ldr_pc(2, f + 0x0C, f + 0x20) if parent is not None else mov(2, 0))
        words[f + 0x10] = mov(3, size)
        words[f + 0x14] = bl(f + 0x14, CTOR)
        words[f + 0x18] = 0xE8BD8010                         # pop {r4, pc}
        words[f + 0x1C] = dti
        words[f + 0x20] = DTI_AREA + parent * 0x20 if parent is not None else 0
        blobs[f + 0x24] = name.encode() + b"\0"
    code = bytearray(0x1000)
    for a, w in words.items():
        struct.pack_into("<I", code, a - BASE, w)
    for a, b in blobs.items():
        code[a - BASE:a - BASE + len(b)] = b
    return mt_dti.Image(bytes(code), BASE, len(code), MEM_END)


def test_detects_constructor():
    assert mt_dti.detect_ctor(build_image()) == CTOR


def test_recovers_hierarchy_by_emulation():
    ctor, classes, ncalls = mt_dti.recover(build_image())
    assert ctor == CTOR and ncalls == len(CLASSES)
    by_name = {d.name: d for d in classes.values()}
    assert set(by_name) == {c[0] for c in CLASSES}
    for i, (name, parent, size) in enumerate(CLASSES):
        d = by_name[name]
        assert d.source == "emu"
        assert d.address == DTI_AREA + i * 0x20
        assert d.size == size
        assert d.id == size                      # read back from DTI + 0x1C after the real ctor ran
        expected_parent = None if parent is None else DTI_AREA + parent * 0x20
        assert d.parent == expected_parent


def test_static_fallback_matches():
    img = build_image()
    for i, (name, parent, size) in enumerate(CLASSES):
        call = BASE + 0x100 + i * 0x40 + 0x14
        d = mt_dti.static_resolve(img, call)
        assert d is not None and d.name == name and d.size == size
        assert d.address == DTI_AREA + i * 0x20


def test_write_tsv_names_parents(tmp_path):
    _, classes, _ = mt_dti.recover(build_image())
    out = tmp_path / "classes.tsv"
    mt_dti.write_tsv(classes, str(out))
    rows = [line.split("\t") for line in out.read_text().splitlines()[2:]]
    parent_of = {r[1]: r[3] for r in rows}
    assert parent_of == {"MtObject": "", "cResource": "MtObject",
                         "rTexture": "cResource", "nDraw::Resource": "MtObject"}


def test_mt_hash():
    # Value observed for "MtObject" in the game's own class registry.
    assert mt_dti.mt_hash("MtObject") == 0x2EA10CEB
