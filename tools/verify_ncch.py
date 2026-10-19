#!/usr/bin/env python3
"""Check a decrypted dump against its own hashes.

Every NCCH carries SHA-256 hashes of its exheader, ExeFS (and each ExeFS file) and RomFS
superblock, and the RomFS has a three-level IVFC hash tree over all of its data. Checking
all of them proves a dump wasn't damaged when it was copied around (e.g. moved in parts
between machines).

usage: verify_ncch.py <.cxi | .3ds/.cci | .cia> [--no-romfs]
Works on the game partition of a .3ds/.cci and on content 0 of a .cia. Exit status 0 when
everything matches.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import struct
import sys
from typing import BinaryIO

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import extract_ncch as en  # noqa: E402

MEDIA_UNIT = 0x200
CHUNK = 1 << 22


def _align(x: int, a: int) -> int:
    return (x + a - 1) & ~(a - 1)


def locate(f: BinaryIO) -> int:
    """Offset of the NCCH to check in a .cxi, .3ds/.cci or .cia."""
    kind = en.detect(f)
    if kind == "ncch":
        return 0
    if kind == "ncsd":
        return en.ncsd_partition0(f)[0]
    if kind == "cia":
        content = en.cia_content0(f)
        if content.encrypted:
            raise en.DumpError("the CIA's content is encrypted")
        return content.offset
    raise en.DumpError(f"not an NCCH, NCSD or CIA ({kind})")


def ivfc_levels(header: bytes, romfs_size: int) -> list[tuple[int, int, int]]:
    """(physical offset, size, block size) of IVFC levels 1-3 inside the RomFS. Level 3 (the
    file system itself) comes first after the header and master hash, then levels 1 and 2."""
    if header[:4] != b"IVFC" or struct.unpack_from("<I", header, 4)[0] != 0x10000:
        raise en.DumpError("RomFS has no IVFC header")
    (master_size,) = struct.unpack_from("<I", header, 8)
    levels = []
    for i in range(3):
        _logical, size, bs_log2 = struct.unpack_from("<QQI", header, 0x0C + i * 0x18)
        levels.append((size, 1 << bs_log2))
    (l1_size, l1_bs), (l2_size, l2_bs), (l3_size, l3_bs) = levels
    l3_off = _align(0x60 + master_size, l3_bs)
    l1_off = _align(l3_off + l3_size, l1_bs)
    l2_off = _align(l1_off + l1_size, l2_bs)
    if l2_off + l2_size > romfs_size:
        raise en.DumpError("IVFC levels run past the end of the RomFS")
    return [(l1_off, l1_size, l1_bs), (l2_off, l2_size, l2_bs), (l3_off, l3_size, l3_bs)]


def _read(f: BinaryIO, offset: int, size: int) -> bytes:
    f.seek(offset)
    data = f.read(size)
    if len(data) != size:
        raise en.DumpError(f"file truncated at 0x{offset + len(data):X}")
    return data


def check_level(f: BinaryIO, offset: int, size: int, block: int, hashes: bytes) -> int:
    """Number of bad blocks of a level whose block hashes are `hashes` (a block is hashed
    padded with zeros to the block size)."""
    bad = 0
    count = (size + block - 1) // block
    if len(hashes) < count * 32:
        return count
    f.seek(offset)
    done = 0
    for i in range(count):
        data = f.read(min(block, size - done))
        done += len(data)
        if hashlib.sha256(data.ljust(block, b"\0")).digest() != hashes[i * 32:(i + 1) * 32]:
            bad += 1
    return bad


def verify(f: BinaryIO, base: int, romfs: bool = True) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    hdr = _read(f, base, 0x200)
    ncch = en.parse_ncch(f, base)
    if not ncch.no_crypto:
        raise en.DumpError("the NCCH is encrypted: decrypt it first (GodMode9)")
    unit = MEDIA_UNIT << ncch.flags[6]
    if ncch.exheader_size:
        ok = hashlib.sha256(_read(f, base + 0x200, 0x400)).digest() == hdr[0x160:0x180]
        results.append(("exheader", ok, ""))
    if ncch.exefs_size:
        (region,) = struct.unpack_from("<I", hdr, 0x1A8)
        exefs = _read(f, base + ncch.exefs_offset, ncch.exefs_size)
        ok = hashlib.sha256(exefs[:region * unit]).digest() == hdr[0x1C0:0x1E0]
        results.append(("ExeFS header", ok, ""))
        for i in range(10):
            name, off, size = struct.unpack_from("<8sII", exefs, i * 16)
            if name.strip(b"\0") and size:
                want = exefs[0xC0 + (9 - i) * 32:0xC0 + (10 - i) * 32]
                got = hashlib.sha256(exefs[0x200 + off:0x200 + off + size]).digest()
                results.append((f"ExeFS {name.rstrip(bytes(1)).decode('ascii', 'replace')}", got == want, ""))
    if ncch.romfs_size:
        (region,) = struct.unpack_from("<I", hdr, 0x1B8)
        start = base + ncch.romfs_offset
        superblock = _read(f, start, region * unit)
        results.append(("RomFS header", hashlib.sha256(superblock).digest() == hdr[0x1E0:0x200], ""))
        if romfs:
            levels = ivfc_levels(superblock[:0x60], ncch.romfs_size)
            (master_size,) = struct.unpack_from("<I", superblock, 8)
            parent = _read(f, start + 0x60, master_size)
            for n, (off, size, block) in enumerate(levels, 1):
                bad = check_level(f, start + off, size, block, parent)
                blocks = (size + block - 1) // block
                results.append((f"RomFS level {n}", bad == 0,
                                f"{blocks - bad}/{blocks} blocks of 0x{block:X}"))
                if n < 3:
                    parent = _read(f, start + off, size)
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dump")
    ap.add_argument("--no-romfs", action="store_true", help="skip the RomFS hash tree (it reads everything)")
    args = ap.parse_args(argv)
    try:
        with open(args.dump, "rb") as f:
            results = verify(f, locate(f), not args.no_romfs)
    except en.DumpError as e:
        print(f"error: {e}")
        return 2
    for name, ok, detail in results:
        print(f"{'ok ' if ok else 'BAD'} {name}{'  ' + detail if detail else ''}")
    bad = [r for r in results if not r[1]]
    print("all hashes match" if not bad else f"{len(bad)} check(s) failed")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
