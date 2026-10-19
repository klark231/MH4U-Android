#!/usr/bin/env python3
"""Compare two system-call traces of the same program (mh4u_runner --trace-svc), thread by
thread, and show where they first differ.

Threads interleave differently on two CPU backends, but each thread should make the same
calls, from the same places, with the same arguments. The first call that differs within a
thread points at the code that ran between it and the previous (matching) call.

usage: compare_svc_traces.py reference.txt candidate.txt [--context 3] [--ignore-args]
Trace lines: "<thread> <svc> pc=<hex> lr=<hex> <r0> <r1> <r2> <r3> t=<ticks>".
Exit status 0 when every thread matches (up to the shorter trace if --prefix is given).
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Call:
    svc: str
    pc: str
    lr: str
    args: tuple[str, ...]
    ticks: int
    line: int

    def key(self, with_args: bool) -> tuple:
        return (self.svc, self.pc, self.lr, self.args if with_args else ())

    def __str__(self) -> str:
        return f"svc {self.svc} pc={self.pc} lr={self.lr} {' '.join(self.args)} (line {self.line}, t={self.ticks})"


def parse(path: str) -> dict[str, list[Call]]:
    threads: dict[str, list[Call]] = {}
    with open(path) as f:
        for n, line in enumerate(f, 1):
            parts = line.split()
            if len(parts) < 9:
                continue
            tid, svc, pc, lr = parts[0], parts[1], parts[2][3:], parts[3][3:]
            ticks = int(parts[8][2:]) if parts[8].startswith("t=") else 0
            threads.setdefault(tid, []).append(Call(svc, pc, lr, tuple(parts[4:8]), ticks, n))
    return threads


def compare(ref: dict[str, list[Call]], cand: dict[str, list[Call]], with_args: bool = True,
            prefix: bool = False) -> list[dict]:
    """One report per thread that differs: thread, index of the first difference, the two
    calls (None past the end of a trace)."""
    out = []
    for tid in sorted(set(ref) | set(cand), key=lambda t: int(t) if t.isdigit() else 0):
        a, b = ref.get(tid, []), cand.get(tid, [])
        n = min(len(a), len(b))
        first = next((i for i in range(n) if a[i].key(with_args) != b[i].key(with_args)), None)
        if first is None and (len(a) == len(b) or prefix):
            continue
        if first is None:
            first = n
        out.append({"thread": tid, "index": first, "ref": a[first] if first < len(a) else None,
                    "cand": b[first] if first < len(b) else None, "ref_len": len(a), "cand_len": len(b)})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("reference")
    ap.add_argument("candidate")
    ap.add_argument("--context", type=int, default=3, help="matching calls to show before a difference")
    ap.add_argument("--ignore-args", action="store_true", help="compare only svc, pc and lr")
    ap.add_argument("--prefix", action="store_true", help="a thread may stop earlier in one trace")
    args = ap.parse_args(argv)
    ref, cand = parse(args.reference), parse(args.candidate)
    diffs = compare(ref, cand, not args.ignore_args, args.prefix)
    for tid in sorted(set(ref) | set(cand), key=lambda t: int(t) if t.isdigit() else 0):
        print(f"thread {tid}: {len(ref.get(tid, []))} vs {len(cand.get(tid, []))} calls")
    for d in diffs:
        i, a = d["index"], ref.get(d["thread"], [])
        print(f"\nthread {d['thread']} differs at call {i}:")
        for c in a[max(0, i - args.context):i]:
            print(f"    same  {c}")
        print(f"    ref   {d['ref'] or '(trace ends)'}")
        print(f"    cand  {d['cand'] or '(trace ends)'}")
    print("\nall threads match" if not diffs else f"\n{len(diffs)} thread(s) differ")
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main())
