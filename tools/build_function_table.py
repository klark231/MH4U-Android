#!/usr/bin/env python3
"""Build the orchestrator's status table, config/functions.tsv.

Inputs: Ghidra's functions.tsv export, the code, config/symbols.txt and optionally an
existing config/functions.tsv (its status/batch/notes columns are preserved).

Columns: address, size, mode, category, name, status, batch, notes
  category  sdk       contains a system call (svc): Nintendo SDK, replaced by the port layer
            dti-init  registers MT Framework classes (calls the DTI constructor)
            unknown   not classified yet
  status    todo | replace (sdk) | done | blocked | ...   (free-form after creation)

usage: build_function_table.py --exheader exheader.bin --code code.bin \
           --functions functions.tsv [--symbols config/symbols.txt] \
           [--dti-ctor 0x005bfa94] -o config/functions.tsv
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
import mt_dti  # noqa: E402

COLUMNS = ["address", "size", "mode", "category", "name", "status", "batch", "notes"]
KEEP_FROM_EXISTING = ("status", "batch", "notes")


def load_symbols(path: str | None) -> dict[int, str]:
    out: dict[int, str] = {}
    if not path or not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line:
                addr, name = line.split()[:2]
                out[int(addr, 16)] = name
    return out


def contains_svc(code: bytes, base: int, addr: int, size: int, thumb: bool) -> bool:
    off, end = addr - base, addr - base + size
    if off < 0 or end > len(code):
        return False
    if thumb:
        return any(code[i + 1] == 0xDF for i in range(off, end - 1, 2))
    return any(code[i + 3] == 0xEF and code[i + 3] >> 4 == 0xE
               for i in range((off + 3) & ~3, end - 3, 4))


def calls_target(code: bytes, base: int, addr: int, size: int, target: int) -> bool:
    off = addr - base
    for i in range((off + 3) & ~3, min(off + size, len(code)) - 3, 4):
        w = struct.unpack_from("<I", code, i)[0]
        if mt_dti.bl_target(base + i, w) == target:
            return True
    return False


def build(code: bytes, header: exh.ExHeader, functions: list[dict], symbols: dict[int, str],
          dti_ctor: int | None, existing: dict[int, dict]) -> list[dict]:
    base = header.text.address
    rows = []
    for f in functions:
        addr, size = int(f["address"], 16), int(f["size"], 16)
        thumb = f["mode"] == "thumb"
        if contains_svc(code, base, addr, size, thumb):
            category = "sdk"
        elif dti_ctor is not None and addr != dti_ctor and calls_target(code, base, addr, size, dti_ctor):
            category = "dti-init"
        else:
            category = "unknown"
        row = {
            "address": f"0x{addr:08x}", "size": f"0x{size:x}", "mode": f["mode"],
            "category": category, "name": symbols.get(addr, ""),
            "status": "replace" if category == "sdk" else "todo", "batch": "", "notes": "",
        }
        old = existing.get(addr)
        if old:
            for k in KEEP_FROM_EXISTING:
                if old.get(k):
                    row[k] = old[k]
        rows.append(row)
    rows.sort(key=lambda r: int(r["address"], 16))
    return rows


def read_tsv(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader((line for line in f if not line.startswith("#")), delimiter="\t"))


def write_tsv(rows: list[dict], path: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("# Function status table (single writer: orchestrator). Built by tools/build_function_table.py\n")
        w = csv.DictWriter(f, fieldnames=COLUMNS, delimiter="\t", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--exheader", required=True)
    ap.add_argument("--code", required=True)
    ap.add_argument("--functions", required=True)
    ap.add_argument("--symbols", default=os.path.join(os.path.dirname(__file__), "..", "config", "symbols.txt"))
    ap.add_argument("--dti-ctor", type=lambda s: int(s, 0))
    ap.add_argument("-o", "--output", required=True)
    args = ap.parse_args(argv)

    with open(args.exheader, "rb") as f:
        header = exh.parse(f.read())
    with open(args.code, "rb") as f:
        code = codebin2elf.load_code(header, f.read())
    existing = {int(r["address"], 16): r for r in read_tsv(args.output)} if os.path.exists(args.output) else {}
    rows = build(code, header, read_tsv(args.functions), load_symbols(args.symbols), args.dti_ctor, existing)
    write_tsv(rows, args.output)

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["category"]] = counts.get(r["category"], 0) + 1
    print(f"{len(rows)} functions: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    print(f"named: {sum(1 for r in rows if r['name'])}")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
