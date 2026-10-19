#!/usr/bin/env python3
"""Annotated disassembly of one function, for humans and worker agents.

Resolves PC-relative literal loads and ADR to their values, names known symbols
(config/symbols.txt) and MT classes, and shows strings they point at.

usage: disasm.py 0x005bf888 [--size 0x4c] [--thumb]
       (size/mode default to config/functions.tsv)
"""
from __future__ import annotations

import argparse
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_function_table as bft  # noqa: E402
import codebin2elf  # noqa: E402
import exheader as exh  # noqa: E402

from capstone import CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_THUMB, Cs  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRINTABLE = re.compile(rb"[\x20-\x7e]{3,}")


def load_names(symbols_path: str, classes_path: str | None) -> dict[int, str]:
    names = {a: n for a, n in bft.load_symbols(symbols_path).items()}
    if classes_path and os.path.exists(classes_path):
        for r in bft.read_tsv(classes_path):
            names.setdefault(int(r["dti"], 16), f"{r['name']}::DTI")
    return names


def describe(value: int, code: bytes, base: int, names: dict[int, str]) -> str:
    parts = []
    if value in names:
        parts.append(names[value])
    off = value - base
    if 0 <= off < len(code):
        end = code.find(b"\0", off, off + 80)
        raw = code[off:end] if end > off else b""
        if raw and PRINTABLE.fullmatch(raw):
            parts.append(repr(raw.decode()))
    return f"  ; = 0x{value:08x}" + (f" {' '.join(parts)}" if parts else "")


def disassemble(code: bytes, base: int, addr: int, size: int, thumb: bool,
                names: dict[int, str]) -> list[str]:
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB if thumb else CS_MODE_ARM)
    out = []
    covered = addr
    end = addr + size
    while covered < end:
        decoded = list(md.disasm(code[covered - base: end - base], covered))
        if not decoded:
            # Undecodable word (literal pool / jump-table entry): show it and resume after it.
            step = 2 if thumb else 4
            if covered + 4 <= end:
                w = struct.unpack_from("<I", code, covered - base)[0]
                out.append(f"{covered:08x}: {w:08x}  .word    0x{w:08x}"
                           + describe(w, code, base, names)[len(f'  ; = 0x{w:08x}'):])
            covered += step
            continue
        covered = _emit(decoded, out, code, base, thumb, names)
    return out


def _emit(decoded, out, code, base, thumb, names) -> int:
    covered = decoded[0].address
    for i in decoded:
        note = ""
        m = re.fullmatch(r"(r\d+|sb|sl|fp|ip|lr), \[pc, #(-?0x[0-9a-f]+|-?\d+)\]", i.op_str)
        if i.mnemonic.startswith("ldr") and m:
            pc = (i.address + (4 if thumb else 8)) & ~3
            lit = pc + int(m.group(2), 0)
            if base <= lit < base + len(code) - 3:
                note = describe(struct.unpack_from("<I", code, lit - base)[0], code, base, names)
        m = re.fullmatch(r"(r\d+|ip|lr), pc, #(0x[0-9a-f]+|\d+)", i.op_str)
        if i.mnemonic in ("add", "sub", "adr") and m:
            pc = i.address + (4 if thumb else 8)
            v = pc + int(m.group(2), 0) if i.mnemonic != "sub" else pc - int(m.group(2), 0)
            note = describe(v, code, base, names)
        if i.mnemonic in ("bl", "blx", "b") and i.op_str.startswith("#"):
            t = int(i.op_str[1:], 0)
            if t in names:
                note = f"  ; {names[t]}"
        out.append(f"{i.address:08x}: {i.bytes.hex():<8}  {i.mnemonic:<8} {i.op_str}{note}")
        covered = i.address + i.size
    return covered


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("address", type=lambda s: int(s, 0))
    ap.add_argument("--size", type=lambda s: int(s, 0))
    ap.add_argument("--thumb", action="store_true")
    ap.add_argument("--exheader", default=os.path.join(ROOT, "baserom", "update_v130", "exheader.bin"))
    ap.add_argument("--code", default=os.path.join(ROOT, "baserom", "update_v130", "code.bin"))
    ap.add_argument("--functions", default=os.path.join(ROOT, "config", "functions.tsv"))
    ap.add_argument("--symbols", default=os.path.join(ROOT, "config", "symbols.txt"))
    ap.add_argument("--classes", default=os.path.join(ROOT, "config", "mt_classes.tsv"))
    args = ap.parse_args(argv)

    with open(args.exheader, "rb") as f:
        header = exh.parse(f.read())
    with open(args.code, "rb") as f:
        code = codebin2elf.load_code(header, f.read())
    size, thumb = args.size, args.thumb
    if size is None and os.path.exists(args.functions):
        for r in bft.read_tsv(args.functions):
            if int(r["address"], 16) == args.address:
                size, thumb = int(r["size"], 16), thumb or r["mode"] == "thumb"
                break
    if size is None:
        print("error: unknown function size; pass --size", file=sys.stderr)
        return 2
    names = load_names(args.symbols, args.classes)
    for line in disassemble(code, header.text.address, args.address, size, thumb, names):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
