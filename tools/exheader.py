#!/usr/bin/env python3
"""Parser for the 3DS NCCH extended header (exheader) — system control info part.

Layout of the first 0x40 bytes (SCI), little-endian:
    0x00  char[8]  application title
    0x08  u8[5]    reserved
    0x0D  u8       flags (bit 0: ExeFS .code is compressed, bit 1: SD application)
    0x0E  u16      remaster version
    0x10  CodeSetInfo text   (u32 address, u32 size in pages, u32 size in bytes)
    0x1C  u32      stack size
    0x20  CodeSetInfo ro
    0x2C  u32      reserved
    0x30  CodeSetInfo data
    0x3C  u32      bss size
The program ID / title ID of the ACI lives at 0x200 (u64).
Reference: https://www.3dbrew.org/wiki/NCCH/Extended_Header
"""
from __future__ import annotations

import struct
import sys
from dataclasses import dataclass

PAGE_SIZE = 0x1000
SCI_SIZE = 0x40
ACI_PROGRAM_ID_OFFSET = 0x200


@dataclass(frozen=True)
class CodeSegment:
    address: int
    num_pages: int
    size: int

    @property
    def padded_size(self) -> int:
        return self.num_pages * PAGE_SIZE


@dataclass(frozen=True)
class ExHeader:
    title: str
    flags: int
    remaster_version: int
    text: CodeSegment
    stack_size: int
    ro: CodeSegment
    data: CodeSegment
    bss_size: int
    program_id: int | None

    @property
    def code_compressed(self) -> bool:
        return bool(self.flags & 0x1)

    @property
    def bss_address(self) -> int:
        return self.data.address + self.data.size


def _segment(buf: bytes, offset: int) -> CodeSegment:
    return CodeSegment(*struct.unpack_from("<III", buf, offset))


def parse(buf: bytes) -> ExHeader:
    if len(buf) < SCI_SIZE:
        raise ValueError(f"exheader too small (0x{len(buf):X} bytes)")
    title = buf[0:8].split(b"\0", 1)[0].decode("ascii", errors="replace")
    flags = buf[0x0D]
    (remaster,) = struct.unpack_from("<H", buf, 0x0E)
    (stack,) = struct.unpack_from("<I", buf, 0x1C)
    (bss,) = struct.unpack_from("<I", buf, 0x3C)
    program_id = None
    if len(buf) >= ACI_PROGRAM_ID_OFFSET + 8:
        (program_id,) = struct.unpack_from("<Q", buf, ACI_PROGRAM_ID_OFFSET)
    return ExHeader(
        title=title,
        flags=flags,
        remaster_version=remaster,
        text=_segment(buf, 0x10),
        stack_size=stack,
        ro=_segment(buf, 0x20),
        data=_segment(buf, 0x30),
        bss_size=bss,
        program_id=program_id,
    )


def describe(h: ExHeader) -> str:
    def seg(name: str, s: CodeSegment) -> str:
        return (f"  {name:<5} addr=0x{s.address:08X} size=0x{s.size:08X} "
                f"pages=0x{s.num_pages:X} (end=0x{s.address + s.size:08X})")
    lines = [
        f"title        : {h.title}",
        f"program id   : {h.program_id:016X}" if h.program_id is not None else "program id   : (exheader truncated)",
        f"remaster ver : {h.remaster_version}",
        f"code compressed: {h.code_compressed}",
        f"stack size   : 0x{h.stack_size:X}",
        "segments:",
        seg("text", h.text),
        seg("ro", h.ro),
        seg("data", h.data),
        f"  bss   addr=0x{h.bss_address:08X} size=0x{h.bss_size:08X}",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <exheader.bin>", file=sys.stderr)
        sys.exit(2)
    with open(sys.argv[1], "rb") as f:
        print(describe(parse(f.read())))
