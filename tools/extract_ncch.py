#!/usr/bin/env python3
"""Extract exheader / ExeFS / .code (and optionally RomFS) from your own 3DS dump.

Accepts a decrypted NCSD (.3ds/.cci, e.g. GodMode9 ".trim.3ds"), CIA (.cia, e.g. an
update title) or bare NCCH (.cxi/.cfa). Only the main content (partition / content
index 0) is extracted. Nothing is decrypted here: this tool never handles keys. If the
input is encrypted it says so; decrypt it on your 3DS with GodMode9 first.

usage: extract_ncch.py <dump> -o <outdir> [--romfs]

Writes to <outdir>:
    exheader.bin   0x800 bytes (SCI + ACI + access descriptor)
    exefs.bin      raw ExeFS
    code.bin       ExeFS ".code" as stored (BLZ-compressed if the exheader says so)
    romfs.bin      only with --romfs (can be GBs)
    info.txt       what was found

Format references: https://www.3dbrew.org/wiki/NCSD, /NCCH, /ExeFS, /CIA, /Title_metadata
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
from dataclasses import dataclass
from typing import BinaryIO

MEDIA_UNIT = 0x200
EXHEADER_FULL_SIZE = 0x800
CHUNK = 1 << 20


class DumpError(ValueError):
    pass


def _read_at(f: BinaryIO, offset: int, size: int) -> bytes:
    f.seek(offset)
    data = f.read(size)
    if len(data) != size:
        raise DumpError(f"file truncated: wanted 0x{size:X} bytes at 0x{offset:X}")
    return data


def _align(x: int, a: int) -> int:
    return (x + a - 1) & ~(a - 1)


# ── Container detection ─────────────────────────────────────────────────────

def detect(f: BinaryIO) -> str:
    head = _read_at(f, 0, 0x200)
    if head[0x100:0x104] == b"NCSD":
        return "ncsd"
    if head[0x100:0x104] == b"NCCH":
        return "ncch"
    if struct.unpack_from("<I", head, 0)[0] == 0x2020:
        return "cia"
    raise DumpError("not an NCSD (.3ds/.cci), CIA or NCCH file")


def ncsd_partition0(f: BinaryIO) -> tuple[int, int]:
    hdr = _read_at(f, 0, 0x200)
    off_mu, size_mu = struct.unpack_from("<II", hdr, 0x120)
    if size_mu == 0:
        raise DumpError("NCSD partition 0 is empty")
    return off_mu * MEDIA_UNIT, size_mu * MEDIA_UNIT


@dataclass(frozen=True)
class CiaContent0:
    offset: int
    size: int
    encrypted: bool
    title_id: int
    title_version: int


def cia_content0(f: BinaryIO) -> CiaContent0:
    hdr = _read_at(f, 0, 0x20)
    hdr_size, _type, _ver, cert_size, tik_size, tmd_size, _meta, _content_size = struct.unpack_from(
        "<IHHIIIIQ", hdr, 0)
    cert_off = _align(hdr_size, 64)
    tik_off = _align(cert_off + cert_size, 64)
    tmd_off = _align(tik_off + tik_size, 64)
    content_off = _align(tmd_off + tmd_size, 64)

    tmd = _read_at(f, tmd_off, tmd_size)
    (sig_type,) = struct.unpack_from(">I", tmd, 0)
    sig_len = {0x10000: 0x200 + 0x3C, 0x10001: 0x100 + 0x3C, 0x10002: 0x3C + 0x40,
               0x10003: 0x200 + 0x3C, 0x10004: 0x100 + 0x3C, 0x10005: 0x3C + 0x40}.get(sig_type)
    if sig_len is None:
        raise DumpError(f"unknown TMD signature type 0x{sig_type:X}")
    th = 4 + sig_len                       # TMD header start
    (title_id,) = struct.unpack_from(">Q", tmd, th + 0x4C)
    (title_version,) = struct.unpack_from(">H", tmd, th + 0x9C)
    (count,) = struct.unpack_from(">H", tmd, th + 0x9E)
    chunks = th + 0xC4 + 0x24 * 64
    # Contents are stored in chunk-record order; find index 0 and its offset.
    pos = content_off
    for i in range(count):
        _cid, index, ctype, size = struct.unpack_from(">IHHQ", tmd, chunks + i * 0x30)
        if index == 0:
            return CiaContent0(pos, size, bool(ctype & 0x1), title_id, title_version)
        pos += size
    raise DumpError("CIA has no content with index 0")


# ── NCCH ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Ncch:
    base: int
    program_id: int
    flags: bytes
    exheader_size: int
    exefs_offset: int
    exefs_size: int
    romfs_offset: int
    romfs_size: int

    @property
    def no_crypto(self) -> bool:
        return bool(self.flags[7] & 0x04)


def parse_ncch(f: BinaryIO, base: int) -> Ncch:
    hdr = _read_at(f, base, 0x200)
    if hdr[0x100:0x104] != b"NCCH":
        raise DumpError(f"no NCCH magic at 0x{base:X}")
    (program_id,) = struct.unpack_from("<Q", hdr, 0x118)
    (exh_size,) = struct.unpack_from("<I", hdr, 0x180)
    flags = hdr[0x188:0x190]
    unit = MEDIA_UNIT << flags[6]
    exefs_off, exefs_size = struct.unpack_from("<II", hdr, 0x1A0)
    romfs_off, romfs_size = struct.unpack_from("<II", hdr, 0x1B0)
    return Ncch(base, program_id, flags, exh_size, exefs_off * unit, exefs_size * unit,
                romfs_off * unit, romfs_size * unit)


def exefs_files(exefs: bytes) -> dict[str, bytes]:
    files = {}
    for i in range(10):
        name, off, size = struct.unpack_from("<8sII", exefs, i * 16)
        name = name.rstrip(b"\0").decode("ascii", errors="replace")
        if name and size:
            start = 0x200 + off
            if start + size > len(exefs):
                raise DumpError(f"ExeFS entry {name!r} runs past end of ExeFS")
            files[name] = exefs[start:start + size]
    return files


def _copy_range(f: BinaryIO, offset: int, size: int, path: str) -> None:
    f.seek(offset)
    with open(path, "wb") as out:
        remaining = size
        while remaining:
            block = f.read(min(CHUNK, remaining))
            if not block:
                raise DumpError("file truncated while copying RomFS")
            out.write(block)
            remaining -= len(block)


# ── Main ─────────────────────────────────────────────────────────────────────

def extract(path: str, outdir: str, with_romfs: bool = False) -> list[str]:
    info: list[str] = [f"source        : {os.path.basename(path)}"]
    with open(path, "rb") as f:
        kind = detect(f)
        info.append(f"container     : {kind}")
        if kind == "ncsd":
            base, _ = ncsd_partition0(f)
        elif kind == "cia":
            c = cia_content0(f)
            info.append(f"CIA title id  : {c.title_id:016X}  version {c.title_version} "
                        f"(v{c.title_version >> 10}.{(c.title_version >> 4) & 0x3F}.{c.title_version & 0xF})")
            if c.encrypted:
                # The TMD's "encrypted" bit is only a label, and some tools leave it set after
                # decrypting. What matters is the data: content that is really title-key
                # encrypted is scrambled from its first byte, so a plain NCCH header there
                # means it isn't. Keys are never touched either way.
                if _read_at(f, c.offset + 0x100, 4) != b"NCCH":
                    raise DumpError("CIA content 0 is encrypted (title key). Decrypt the CIA "
                                    "with GodMode9 on your 3DS first.")
                info.append("note          : TMD marks content 0 encrypted, but its NCCH is "
                            "plain; continuing")
            base = c.offset
        else:
            base = 0

        n = parse_ncch(f, base)
        info.append(f"program id    : {n.program_id:016X}")
        info.append(f"NCCH crypto   : {'none (decrypted)' if n.no_crypto else 'ENCRYPTED'}")
        if not n.no_crypto:
            raise DumpError("NCCH is encrypted. Decrypt the dump with GodMode9 on your 3DS first "
                            "(the tool never handles keys).")
        if n.exheader_size == 0:
            raise DumpError("NCCH has no exheader (is this a data-only .cfa?)")

        exheader = _read_at(f, base + 0x200, EXHEADER_FULL_SIZE)
        exefs = _read_at(f, base + n.exefs_offset, n.exefs_size)
        files = exefs_files(exefs)
        if ".code" not in files:
            raise DumpError(f"ExeFS has no .code (found: {', '.join(files) or 'nothing'})")

        os.makedirs(outdir, exist_ok=True)
        with open(os.path.join(outdir, "exheader.bin"), "wb") as o:
            o.write(exheader)
        with open(os.path.join(outdir, "exefs.bin"), "wb") as o:
            o.write(exefs)
        with open(os.path.join(outdir, "code.bin"), "wb") as o:
            o.write(files[".code"])
        info.append(f"ExeFS files   : {', '.join(f'{k} (0x{len(v):X})' for k, v in files.items())}")
        info.append(f"code compressed (exheader flag): {bool(exheader[0x0D] & 1)}")
        info.append(f"RomFS         : 0x{n.romfs_size:X} bytes at 0x{base + n.romfs_offset:X}")
        if with_romfs and n.romfs_size:
            _copy_range(f, base + n.romfs_offset, n.romfs_size, os.path.join(outdir, "romfs.bin"))
            info.append("RomFS written : romfs.bin")

    with open(os.path.join(outdir, "info.txt"), "w") as o:
        o.write("\n".join(info) + "\n")
    return info


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dump")
    ap.add_argument("-o", "--outdir", required=True)
    ap.add_argument("--romfs", action="store_true", help="also extract the RomFS (large)")
    args = ap.parse_args(argv)
    try:
        info = extract(args.dump, args.outdir, args.romfs)
    except DumpError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print("\n".join(info))
    return 0


if __name__ == "__main__":
    sys.exit(main())
