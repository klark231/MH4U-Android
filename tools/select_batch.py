#!/usr/bin/env python3
"""Pick a batch of functions for worker agents and mark them in config/functions.tsv.

Candidates: status=todo, category=unknown, leaf functions (no direct callees in the
Ghidra call graph), ARM mode, within a size range, without indirect branches, system
calls or (by default) VFP instructions, and *testable*: difftest's input generator can
run the original without faulting in most cases.

usage: select_batch.py --batch pilot-1 --count 10 [--min-size 0x20] [--max-size 0x100]
           [--export build/export/functions.tsv] [--allow-vfp] [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import random
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_function_table as bft  # noqa: E402
import codebin2elf  # noqa: E402
import exheader as exh  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def arm_words(code: bytes, base: int, addr: int, size: int):
    for a in range(addr, addr + size - 3, 4):
        yield a, struct.unpack_from("<I", code, a - base)[0]


def is_simple(code: bytes, base: int, addr: int, size: int, allow_vfp: bool) -> bool:
    for _a, w in arm_words(code, base, addr, size):
        cond = w >> 28
        if cond == 0xF:
            continue                                   # unconditional space: rare, ignore
        if (w & 0x0FFFFFF0) == 0x012FFF30:             # BLX Rm: indirect call
            return False
        if (w & 0x0FFFFFF0) == 0x012FFF10 and (w & 0xF) != 14:   # BX Rm (not lr): indirect jump
            return False
        if (w & 0x0F000000) == 0x0F000000:             # SVC
            return False
        if (w & 0x0E10F000) == 0x0410F000 and (w & 0x0F7F0000) != 0x041D0000:  # LDR pc, [...] (not pop {pc})
            return False
        if (w & 0x0FFFFFF0) == 0x01A0F000 and (w & 0xF) != 14:   # MOV pc, Rm (not lr)
            return False
        if not allow_vfp and (w & 0x0E000E00) in (0x0C000A00, 0x0E000A00):  # cp10/cp11
            return False
    return True


def testable(code: bytes, header: exh.ExHeader, addr: int, seed: int = 1) -> tuple[bool, tuple[int, ...], int]:
    import difftest as dt
    m = dt.Machine(code, header.text.address, header.bss_address + header.bss_size, None)
    slots, faults = dt.profile_pointer_args(m, addr, False, 4, 0, seed, trials=16)
    return faults <= 4, slots, faults


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--batch", required=True)
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--min-size", type=lambda s: int(s, 0), default=0x20)
    ap.add_argument("--max-size", type=lambda s: int(s, 0), default=0x100)
    ap.add_argument("--allow-vfp", action="store_true")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--export", default=os.path.join(ROOT, "build", "export", "functions.tsv"))
    ap.add_argument("--table", default=os.path.join(ROOT, "config", "functions.tsv"))
    ap.add_argument("--exheader", default=os.path.join(ROOT, "baserom", "update_v130", "exheader.bin"))
    ap.add_argument("--code", default=os.path.join(ROOT, "baserom", "update_v130", "code.bin"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    with open(args.exheader, "rb") as f:
        header = exh.parse(f.read())
    with open(args.code, "rb") as f:
        code = codebin2elf.load_code(header, f.read())
    leaves = {int(r["address"], 16) for r in bft.read_tsv(args.export) if r["callees"] == "0"}
    rows = bft.read_tsv(args.table)
    pool = [r for r in rows
            if r["status"] == "todo" and r["category"] == "unknown" and r["mode"] == "arm"
            and int(r["address"], 16) in leaves
            and args.min_size <= int(r["size"], 16) <= args.max_size
            and is_simple(code, header.text.address, int(r["address"], 16), int(r["size"], 16), args.allow_vfp)]
    rng = random.Random(args.seed)
    rng.shuffle(pool)
    picked = []
    for r in pool:
        ok, slots, _ = testable(code, header, int(r["address"], 16))
        if ok:
            picked.append((r, slots))
        if len(picked) == args.count:
            break
    picked.sort(key=lambda p: int(p[0]["address"], 16))
    print(f"pool after static filters: {len(pool)}; picked {len(picked)}")
    for r, slots in picked:
        print(f"  {r['address']}  size {r['size']:>6}  likely pointer args: {list(slots) or 'none'}")
        r["status"], r["batch"] = "assigned", args.batch
    if not args.dry_run:
        bft.write_tsv(rows, args.table)
        print(f"marked as assigned to {args.batch} in {args.table}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
