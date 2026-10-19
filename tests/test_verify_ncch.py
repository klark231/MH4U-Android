"""tools/verify_ncch.py on synthetic NCCHs with a real IVFC hash tree (no game data)."""
import hashlib
import io
import os
import struct
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "tools"))


def _align(x, a):
    return (x + a - 1) & ~(a - 1)


def _hashes(data, block):
    return b"".join(hashlib.sha256(data[i:i + block].ljust(block, b"\0")).digest()
                    for i in range(0, len(data), block))


def make_romfs(level3: bytes, block_log2: int = 12) -> bytes:
    """A RomFS image laid out like the console's: IVFC header, master hash, level 3, then
    levels 1 and 2."""
    block = 1 << block_log2
    l2 = _hashes(level3, block)
    l1 = _hashes(l2, block)
    master = _hashes(l1, block)
    header = bytearray(0x60)
    header[:4] = b"IVFC"
    struct.pack_into("<II", header, 4, 0x10000, len(master))
    for i, size in enumerate((len(l1), len(l2), len(level3))):
        struct.pack_into("<QQI", header, 0x0C + i * 0x18, 0, size, block_log2)
    l3_off = _align(0x60 + len(master), block)
    l1_off = _align(l3_off + len(level3), block)
    l2_off = _align(l1_off + len(l1), block)
    image = bytearray(_align(l2_off + len(l2), 0x200))
    image[:0x60] = header
    image[0x60:0x60 + len(master)] = master
    image[l3_off:l3_off + len(level3)] = level3
    image[l1_off:l1_off + len(l1)] = l1
    image[l2_off:l2_off + len(l2)] = l2
    return bytes(image)


def make_exefs(files: dict) -> bytes:
    header = bytearray(0x200)
    body = b""
    for i, (name, data) in enumerate(files.items()):
        struct.pack_into("<8sII", header, i * 16, name.encode(), len(body), len(data))
        header[0xC0 + (9 - i) * 32:0xC0 + (10 - i) * 32] = hashlib.sha256(data).digest()
        body += data + bytes(-len(data) % 0x200)
    return bytes(header) + body


def make_dump():
    import make_cxi
    exheader = bytearray(0x800)
    struct.pack_into("<Q", exheader, 0x200, 0x0004000000ABCD00)
    exefs = make_exefs({".code": bytes(range(256)) * 20, "icon": b"\x01" * 0x36C0})
    level3 = bytes((i * 7) & 0xFF for i in range(0x5432))
    return bytearray(make_cxi.make_cxi(bytes(exheader), exefs, make_romfs(level3)))


def run(data):
    import verify_ncch
    f = io.BytesIO(bytes(data))
    return {name: (ok, detail) for name, ok, detail in verify_ncch.verify(f, verify_ncch.locate(f))}


def test_intact_dump_passes():
    results = run(make_dump())
    assert all(ok for ok, _ in results.values()), results
    assert set(results) >= {"exheader", "ExeFS header", "ExeFS .code", "ExeFS icon", "RomFS header",
                             "RomFS level 1", "RomFS level 2", "RomFS level 3"}
    assert results["RomFS level 3"][1] == "6/6 blocks of 0x1000"


def test_damage_is_found():
    import extract_ncch as en
    data = make_dump()
    ncch = en.parse_ncch(io.BytesIO(bytes(data)), 0)
    romfs = ncch.romfs_offset
    data[romfs + 0x2000 + 0x123] ^= 0xFF            # inside level 3 (the file system)
    results = run(data)
    assert results["RomFS level 3"] == (False, "5/6 blocks of 0x1000")
    assert results["RomFS level 1"][0] and results["RomFS level 2"][0]
    data = make_dump()
    data[ncch.exefs_offset + 0x200 + 5] ^= 1          # inside .code
    results = run(data)
    assert not results["ExeFS .code"][0] and results["ExeFS icon"][0]
