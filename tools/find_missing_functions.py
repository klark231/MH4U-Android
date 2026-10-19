#!/usr/bin/env python3
"""Find likely ARM functions a disassembler missed, for a second Ghidra pass.

Ghidra mostly discovers functions by following calls, so code reached only through
vtables or function-pointer tables is left undefined. This tool reads Ghidra's
functions.tsv export plus the code, and lists addresses that:
  * start with an ARM prologue (push {..., lr} / str lr, [sp, #-4]!) inside a gap
    between known functions in .text, or
  * are pointed to by an aligned word in .rodata/.data and start with a prologue.

The output is plain addresses (no game data), consumed by
tools/ghidra/CreateFunctionsFromList.java.

usage: find_missing_functions.py --exheader exheader.bin --code code.bin \
           --functions functions.tsv -o missing_functions.txt
"""
from __future__ import annotations

import argparse
import csv
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import codebin2elf  # noqa: E402
import exheader as exh  # noqa: E402


def is_prologue(w: int) -> bool:
    return (w & 0xFFFF4000) == 0xE92D4000 or w == 0xE52DE004


def load_functions(path: str) -> list[tuple[int, int]]:
    with open(path, encoding="utf-8") as f:
        return sorted((int(r["address"], 16), int(r["size"], 16))
                      for r in csv.DictReader(f, delimiter="\t"))


def gaps(funcs: list[tuple[int, int]], start: int, end: int) -> list[tuple[int, int]]:
    out, cur = [], start
    for addr, size in funcs:
        if addr >= end:
            break
        if addr > cur:
            out.append((cur, addr))
        cur = max(cur, addr + size)
    if cur < end:
        out.append((cur, end))
    return out


def find_missing(code: bytes, header: exh.ExHeader,
                 funcs: list[tuple[int, int]]) -> dict[int, str]:
    base = header.text.address
    text_end = base + header.text.size

    def u32(a: int) -> int:
        return struct.unpack_from("<I", code, a - base)[0]

    known = {a for a, _ in funcs}
    found: dict[int, str] = {}
    gap_list = gaps(funcs, base, text_end)
    for lo, hi in gap_list:
        for a in range((lo + 3) & ~3, hi - 3, 4):
            if is_prologue(u32(a)):
                found[a] = "gap-prologue"

    def in_gap(a: int) -> bool:
        # binary search over sorted gaps
        lo_i, hi_i = 0, len(gap_list)
        while lo_i < hi_i:
            mid = (lo_i + hi_i) // 2
            g_lo, g_hi = gap_list[mid]
            if a < g_lo:
                hi_i = mid
            elif a >= g_hi:
                lo_i = mid + 1
            else:
                return True
        return False

    for seg in (header.ro, header.data):
        for a in range(seg.address, seg.address + seg.size - 3, 4):
            v = u32(a)
            if base <= v < text_end and v % 4 == 0 and v not in known and in_gap(v) \
                    and is_prologue(u32(v)):
                found[v] = "data-pointer" if v not in found else "gap-prologue+data-pointer"
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--exheader", required=True)
    ap.add_argument("--code", required=True)
    ap.add_argument("--functions", required=True, help="functions.tsv from ExportForAgents")
    ap.add_argument("-o", "--output", required=True)
    args = ap.parse_args(argv)

    with open(args.exheader, "rb") as f:
        header = exh.parse(f.read())
    with open(args.code, "rb") as f:
        code = codebin2elf.load_code(header, f.read())
    funcs = load_functions(args.functions)
    found = find_missing(code, header, funcs)

    with open(args.output, "w", encoding="utf-8") as f:
        f.write("# likely functions missed by the first analysis pass (tools/find_missing_functions.py)\n")
        for a in sorted(found):
            f.write(f"0x{a:08x}\t{found[a]}\n")
    by_kind: dict[str, int] = {}
    for k in found.values():
        by_kind[k] = by_kind.get(k, 0) + 1
    print(f"known functions: {len(funcs)}  candidates: {len(found)}  {by_kind}")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
