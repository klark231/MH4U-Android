import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import extract_ncch as ex  # noqa: E402

PROGRAM_ID = 0x0004000000ABC000
CODE = b"CODE" * 0x100
ICON = b"ICON" * 0x40
ROMFS = b"IVFC" + b"R" * 0x3FC


def make_ncch(no_crypto: bool = True) -> bytes:
    exheader = bytearray(0x800)
    exheader[0:8] = b"TESTAPP\0"
    exheader[0x0D] = 1
    # ExeFS: header + .code + icon, each padded to 0x200
    exefs_hdr = bytearray(0x200)
    struct.pack_into("<8sII", exefs_hdr, 0, b".code", 0, len(CODE))
    struct.pack_into("<8sII", exefs_hdr, 16, b"icon", len(CODE), len(ICON))
    exefs = bytes(exefs_hdr) + CODE + ICON
    exefs = exefs.ljust((len(exefs) + 0x1FF) & ~0x1FF, b"\0")

    exefs_off = 0x200 + 0x800                   # header, exheader
    romfs_off = exefs_off + len(exefs)
    hdr = bytearray(0x200)
    hdr[0x100:0x104] = b"NCCH"
    struct.pack_into("<I", hdr, 0x104, (romfs_off + len(ROMFS)) // 0x200)
    struct.pack_into("<Q", hdr, 0x118, PROGRAM_ID)
    struct.pack_into("<I", hdr, 0x180, 0x400)
    hdr[0x188 + 7] = 0x04 if no_crypto else 0x00
    struct.pack_into("<II", hdr, 0x1A0, exefs_off // 0x200, len(exefs) // 0x200)
    struct.pack_into("<II", hdr, 0x1B0, romfs_off // 0x200, len(ROMFS) // 0x200)
    return bytes(hdr) + bytes(exheader) + exefs + ROMFS


def make_ncsd(ncch: bytes) -> bytes:
    hdr = bytearray(0x4000)                      # partitions start at 0x4000 on carts
    hdr[0x100:0x104] = b"NCSD"
    struct.pack_into("<II", hdr, 0x120, 0x4000 // 0x200, len(ncch) // 0x200)
    return bytes(hdr) + ncch


def make_cia(ncch: bytes, encrypted: bool = False) -> bytes:
    def pad64(b: bytes) -> bytes:
        return b.ljust((len(b) + 63) & ~63, b"\0")

    certs, ticket = b"C" * 0xA00, b"T" * 0x350
    th = 4 + 0x100 + 0x3C                        # RSA-2048 signature
    tmd = bytearray(th + 0xC4 + 0x24 * 64 + 0x30 * 2)
    struct.pack_into(">I", tmd, 0, 0x10004)
    struct.pack_into(">Q", tmd, th + 0x4C, PROGRAM_ID)
    struct.pack_into(">H", tmd, th + 0x9C, (1 << 10) | (3 << 4))   # v1.3.0
    struct.pack_into(">H", tmd, th + 0x9E, 2)
    chunks = th + 0xC4 + 0x24 * 64
    manual = b"M" * 0x200                        # a content stored *before* index 0
    struct.pack_into(">IHHQ", tmd, chunks, 1, 1, 0, len(manual))
    struct.pack_into(">IHHQ", tmd, chunks + 0x30, 0, 0, 1 if encrypted else 0, len(ncch))
    header = bytearray(0x2020)
    struct.pack_into("<IHHIIIIQ", header, 0, 0x2020, 0, 0, len(certs), len(ticket), len(tmd), 0,
                     len(manual) + len(ncch))
    return pad64(bytes(header)) + pad64(certs) + pad64(ticket) + pad64(bytes(tmd)) + manual + ncch


def check_outputs(out, info):
    assert (out / "code.bin").read_bytes() == CODE
    assert (out / "exheader.bin").read_bytes()[0:7] == b"TESTAPP"
    assert len((out / "exheader.bin").read_bytes()) == 0x800
    assert any("decrypted" in line for line in info)


def test_bare_ncch(tmp_path):
    p = tmp_path / "game.cxi"
    p.write_bytes(make_ncch())
    check_outputs(tmp_path / "out", ex.extract(str(p), str(tmp_path / "out")))


def test_ncsd_with_romfs(tmp_path):
    p = tmp_path / "game.3ds"
    p.write_bytes(make_ncsd(make_ncch()))
    info = ex.extract(str(p), str(tmp_path / "out"), with_romfs=True)
    check_outputs(tmp_path / "out", info)
    assert (tmp_path / "out" / "romfs.bin").read_bytes() == ROMFS


def test_cia_finds_content_index_0(tmp_path):
    p = tmp_path / "update.cia"
    p.write_bytes(make_cia(make_ncch()))
    info = ex.extract(str(p), str(tmp_path / "out"))
    check_outputs(tmp_path / "out", info)
    assert any("v1.3.0" in line for line in info)


def test_encrypted_ncch_is_refused(tmp_path):
    p = tmp_path / "enc.3ds"
    p.write_bytes(make_ncsd(make_ncch(no_crypto=False)))
    with pytest.raises(ex.DumpError, match="encrypted"):
        ex.extract(str(p), str(tmp_path / "out"))


def test_title_key_encrypted_cia_is_refused(tmp_path):
    # really encrypted content is scrambled from its first byte: no NCCH magic to be seen
    scrambled = bytes(b ^ 0xA5 for b in make_ncch())
    p = tmp_path / "enc.cia"
    p.write_bytes(make_cia(scrambled, encrypted=True))
    with pytest.raises(ex.DumpError, match="GodMode9"):
        ex.extract(str(p), str(tmp_path / "out"))


def test_decrypted_cia_with_stale_encrypted_flag_is_accepted(tmp_path):
    # some tools decrypt the content but leave the TMD's "encrypted" bit set
    p = tmp_path / "stale.cia"
    p.write_bytes(make_cia(make_ncch(), encrypted=True))
    info = ex.extract(str(p), str(tmp_path / "out"))
    check_outputs(tmp_path / "out", info)
    assert any("plain" in line for line in info)


def test_garbage_is_rejected(tmp_path):
    p = tmp_path / "junk.bin"
    p.write_bytes(b"\0" * 0x400)
    with pytest.raises(ex.DumpError):
        ex.extract(str(p), str(tmp_path / "out"))
