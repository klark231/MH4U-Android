#!/usr/bin/env python3
"""Wrap an exheader and ExeFS (and optionally a RomFS) from your own dump into a decrypted
NCCH executable (.cxi) that emulators can boot.

Used to run the game's code in the PC port's test runner when only the update's ExeFS is
at hand (no RomFS: the game runs until it first needs its files). The output contains
game code: keep it in build/ (git-ignored), never share it.

usage: make_cxi.py --exheader exheader.bin --exefs exefs.bin [--romfs romfs.bin]
                   [--program-id 0004000000126300] -o build/mh4u.cxi
"""
from __future__ import annotations

import argparse
import hashlib
import struct
import sys

MEDIA_UNIT = 0x200
EXHEADER_SIZE = 0x400          # the hashed part; the access descriptor follows it


def _pad(data: bytes) -> bytes:
    return data + bytes(-len(data) % MEDIA_UNIT)


def make_cxi(exheader: bytes, exefs: bytes, romfs: bytes = b"", program_id: int | None = None,
             product_code: str = "CTR-P-CTAP") -> bytes:
    if len(exheader) != 2 * EXHEADER_SIZE:
        raise ValueError(f"exheader must be 0x800 bytes, got 0x{len(exheader):X}")
    if program_id is None:
        # ARM11 local system capabilities start with the program id (exheader + 0x200)
        (program_id,) = struct.unpack_from("<Q", exheader, 0x200)
    exefs = _pad(exefs)
    romfs = _pad(romfs) if romfs else b""
    exefs_off = MEDIA_UNIT + len(exheader)                      # header, then exheader
    romfs_off = exefs_off + len(exefs)
    total = romfs_off + len(romfs)

    hdr = bytearray(MEDIA_UNIT)
    hdr[0x100:0x104] = b"NCCH"
    struct.pack_into("<I", hdr, 0x104, total // MEDIA_UNIT)
    struct.pack_into("<Q", hdr, 0x108, program_id)                # partition id
    struct.pack_into("<H", hdr, 0x112, 2)                         # NCCH version
    struct.pack_into("<Q", hdr, 0x118, program_id)
    hdr[0x150:0x160] = product_code.encode("ascii")[:16].ljust(16, b"\0")
    hdr[0x160:0x180] = hashlib.sha256(exheader[:EXHEADER_SIZE]).digest()
    struct.pack_into("<I", hdr, 0x180, EXHEADER_SIZE)
    # flags: content platform CTR, type executable + data, NoCrypto (+ NoMountRomFs)
    flags = bytearray(8)
    flags[4], flags[5] = 1, 0x03
    flags[7] = 0x04 | (0 if romfs else 0x02)
    hdr[0x188:0x190] = flags
    struct.pack_into("<IIII", hdr, 0x1A0, exefs_off // MEDIA_UNIT, len(exefs) // MEDIA_UNIT, 1, 0)
    hdr[0x1C0:0x1E0] = hashlib.sha256(exefs[:MEDIA_UNIT]).digest()
    if romfs:
        struct.pack_into("<III", hdr, 0x1B0, romfs_off // MEDIA_UNIT, len(romfs) // MEDIA_UNIT, 1)
        hdr[0x1E0:0x200] = hashlib.sha256(romfs[:MEDIA_UNIT]).digest()
    return bytes(hdr) + exheader + exefs + romfs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--exheader", required=True)
    ap.add_argument("--exefs", required=True)
    ap.add_argument("--romfs")
    ap.add_argument("--program-id", type=lambda s: int(s, 16))
    ap.add_argument("-o", "--out", required=True)
    args = ap.parse_args(argv)
    with open(args.exheader, "rb") as f:
        exheader = f.read()
    with open(args.exefs, "rb") as f:
        exefs = f.read()
    romfs = b""
    if args.romfs:
        with open(args.romfs, "rb") as f:
            romfs = f.read()
    data = make_cxi(exheader, exefs, romfs, args.program_id)
    with open(args.out, "wb") as f:
        f.write(data)
    print(f"wrote {args.out} ({len(data):#x} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
