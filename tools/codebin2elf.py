#!/usr/bin/env python3
"""Convert a 3DS ExeFS `.code` (code.bin) + exheader into an ELF for disassemblers.

The ELF has one PT_LOAD per segment (text R-X, rodata R--, data RW- with bss
folded into its memory size) and matching sections, so Ghidra/IDA/Binja map
everything at the real runtime addresses. Load in Ghidra as ARM:LE:32:v6.

usage: codebin2elf.py --exheader exheader.bin --code code.bin -o out.elf
"""
from __future__ import annotations

import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blz  # noqa: E402
import exheader as exh  # noqa: E402

EM_ARM = 40
ET_EXEC = 2
PT_LOAD = 1
PF_X, PF_W, PF_R = 1, 2, 4
SHT_PROGBITS, SHT_STRTAB, SHT_NOBITS = 1, 3, 8
SHF_WRITE, SHF_ALLOC, SHF_EXECINSTR = 1, 2, 4
EF_ARM_EABI_VER5 = 0x05000000

EHDR_SIZE, PHDR_SIZE, SHDR_SIZE = 52, 32, 40
ALIGN = 0x1000


def _align(x: int, a: int = ALIGN) -> int:
    return (x + a - 1) & ~(a - 1)


def split_segments(header: exh.ExHeader, code: bytes) -> tuple[bytes, bytes, bytes]:
    text_off = 0
    ro_off = header.text.padded_size
    data_off = ro_off + header.ro.padded_size
    needed = data_off + header.data.size
    if len(code) < needed:
        raise ValueError(
            f"code.bin is 0x{len(code):X} bytes but the exheader needs at least 0x{needed:X}. "
            "Is it still compressed, or from a different version than the exheader?")
    return (code[text_off:text_off + header.text.size],
            code[ro_off:ro_off + header.ro.size],
            code[data_off:data_off + header.data.size])


def build_elf(header: exh.ExHeader, code: bytes) -> bytes:
    text, ro, data = split_segments(header, code)

    # (name, addr, payload, memsz, pflags, sh_flags, sh_type)
    segs = []
    if text:
        segs.append((".text", header.text.address, text, len(text), PF_R | PF_X,
                     SHF_ALLOC | SHF_EXECINSTR, SHT_PROGBITS))
    if ro:
        segs.append((".rodata", header.ro.address, ro, len(ro), PF_R, SHF_ALLOC, SHT_PROGBITS))
    if data or header.bss_size:
        segs.append((".data", header.data.address, data, len(data) + header.bss_size, PF_R | PF_W,
                     SHF_ALLOC | SHF_WRITE, SHT_PROGBITS))

    shstr = bytearray(b"\0")
    def add_name(name: str) -> int:
        off = len(shstr)
        shstr.extend(name.encode() + b"\0")
        return off

    phnum = len(segs)
    offset = _align(EHDR_SIZE + phnum * PHDR_SIZE)
    blobs: list[tuple[int, bytes]] = []
    phdrs = bytearray()
    shdrs = bytearray(SHDR_SIZE)  # null section
    for name, addr, payload, memsz, pflags, shflags, shtype in segs:
        phdrs += struct.pack("<IIIIIIII", PT_LOAD, offset, addr, addr, len(payload), memsz, pflags, ALIGN)
        shdrs += struct.pack("<IIIIIIIIII", add_name(name), shtype, shflags, addr, offset,
                             len(payload), 0, 0, 4, 0)
        blobs.append((offset, payload))
        offset = _align(offset + len(payload))

    if header.bss_size:
        bss_off = blobs[-1][0] + len(blobs[-1][1])
        shdrs += struct.pack("<IIIIIIIIII", add_name(".bss"), SHT_NOBITS, SHF_ALLOC | SHF_WRITE,
                             header.bss_address, bss_off, header.bss_size, 0, 0, 4, 0)

    shstrndx = len(shdrs) // SHDR_SIZE
    shstr_name = add_name(".shstrtab")
    shstr_off = offset
    shdrs += struct.pack("<IIIIIIIIII", shstr_name, SHT_STRTAB, 0, 0, shstr_off, len(shstr), 0, 0, 1, 0)
    shoff = _align(shstr_off + len(shstr), 4)
    shnum = len(shdrs) // SHDR_SIZE

    ident = b"\x7fELF" + bytes([1, 1, 1, 0]) + bytes(8)
    ehdr = ident + struct.pack("<HHIIIIIHHHHHH", ET_EXEC, EM_ARM, 1, header.text.address,
                               EHDR_SIZE, shoff, EF_ARM_EABI_VER5, EHDR_SIZE, PHDR_SIZE, phnum,
                               SHDR_SIZE, shnum, shstrndx)

    out = bytearray(shoff + len(shdrs))
    out[0:EHDR_SIZE] = ehdr
    out[EHDR_SIZE:EHDR_SIZE + len(phdrs)] = phdrs
    for off, payload in blobs:
        out[off:off + len(payload)] = payload
    out[shstr_off:shstr_off + len(shstr)] = shstr
    out[shoff:shoff + len(shdrs)] = shdrs
    return bytes(out)


def load_code(header: exh.ExHeader, raw: bytes) -> bytes:
    if header.code_compressed:
        try:
            return blz.decompress(raw)
        except blz.BLZError:
            # Already decompressed by the extraction tool? Fall through if sizes fit.
            expected = header.text.padded_size + header.ro.padded_size + header.data.size
            if len(raw) >= expected:
                return raw
            raise
    return raw


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--exheader", required=True)
    ap.add_argument("--code", required=True)
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--dump-decompressed", metavar="PATH",
                    help="also write the decompressed code.bin here")
    args = ap.parse_args(argv)

    with open(args.exheader, "rb") as f:
        header = exh.parse(f.read())
    with open(args.code, "rb") as f:
        code = load_code(header, f.read())

    if args.dump_decompressed:
        os.makedirs(os.path.dirname(os.path.abspath(args.dump_decompressed)), exist_ok=True)
        with open(args.dump_decompressed, "wb") as f:
            f.write(code)

    elf = build_elf(header, code)
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "wb") as f:
        f.write(elf)
    print(exh.describe(header))
    print(f"wrote {args.output} (0x{len(elf):X} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
