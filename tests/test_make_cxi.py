"""make_cxi.py builds an NCCH that extract_ncch.py reads back (synthetic data only)."""
import io
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import extract_ncch  # noqa: E402
import make_cxi  # noqa: E402


def fake_exefs(files: dict[str, bytes]) -> bytes:
    header = bytearray(0x200)
    body = b""
    for i, (name, data) in enumerate(files.items()):
        struct.pack_into("<8sII", header, i * 16, name.encode(), len(body), len(data))
        body += data + bytes(-len(data) % 0x200)
    return bytes(header) + body


def test_round_trip():
    exheader = bytearray(0x800)
    struct.pack_into("<Q", exheader, 0x200, 0x0004000000ABCD00)
    exefs = fake_exefs({".code": b"\x01\x02\x03" * 100, "icon": b"I" * 0x40})
    romfs = b"IVFC" + bytes(0x300)
    for with_romfs in (False, True):
        data = make_cxi.make_cxi(bytes(exheader), exefs, romfs if with_romfs else b"")
        f = io.BytesIO(data)
        assert extract_ncch.detect(f) == "ncch"
        n = extract_ncch.parse_ncch(f, 0)
        assert n.program_id == 0x0004000000ABCD00 and n.no_crypto
        assert n.exheader_size == 0x400
        assert data[0x200:0xA00] == bytes(exheader)
        files = extract_ncch.exefs_files(data[n.exefs_offset:n.exefs_offset + n.exefs_size])
        assert files[".code"] == b"\x01\x02\x03" * 100
        assert (n.romfs_size > 0) == with_romfs
        if with_romfs:
            assert data[n.romfs_offset:n.romfs_offset + 4] == b"IVFC"
        assert len(data) % 0x200 == 0


def test_program_id_override():
    data = make_cxi.make_cxi(bytes(0x800), fake_exefs({".code": b"x"}), program_id=0x1234)
    assert extract_ncch.parse_ncch(io.BytesIO(data), 0).program_id == 0x1234
