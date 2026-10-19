#!/usr/bin/env python3
"""Query or update config/functions.tsv (orchestrator only).

usage:
  fnstatus.py show 0x005bf888
  fnstatus.py set 0x005bf888 --status done --batch pilot-0 --notes "difftest 1000/1000" [--name crc32_string]
  fnstatus.py summary
"""
from __future__ import annotations

import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_function_table as bft  # noqa: E402

DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "functions.tsv")


def find(rows: list[dict], addr: int) -> dict:
    for r in rows:
        if int(r["address"], 16) == addr:
            return r
    raise SystemExit(f"error: no function at 0x{addr:08x} in the table")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--table", default=DEFAULT)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("show")
    s.add_argument("address", type=lambda x: int(x, 0))
    u = sub.add_parser("set")
    u.add_argument("address", type=lambda x: int(x, 0))
    for col in ("status", "batch", "notes", "name", "category"):
        u.add_argument(f"--{col}")
    sub.add_parser("summary")
    args = ap.parse_args(argv)

    rows = bft.read_tsv(args.table)
    if args.cmd == "show":
        r = find(rows, args.address)
        print("\t".join(f"{k}={v}" for k, v in r.items()))
    elif args.cmd == "set":
        r = find(rows, args.address)
        for col in ("status", "batch", "notes", "name", "category"):
            v = getattr(args, col)
            if v is not None:
                r[col] = v
        bft.write_tsv(rows, args.table)
        print("\t".join(f"{k}={v}" for k, v in r.items()))
    else:
        by_status = collections.Counter(r["status"] for r in rows)
        by_cat = collections.Counter(r["category"] for r in rows)
        done_bytes = sum(int(r["size"], 16) for r in rows if r["status"] == "done")
        print(f"{len(rows)} functions | status: {dict(by_status)} | category: {dict(by_cat)}")
        print(f"decompiled bytes: 0x{done_bytes:x}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
