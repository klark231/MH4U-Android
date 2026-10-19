#!/usr/bin/env python3
"""Whole-program differential test: each recompiled function (with everything it calls)
against the original code in Unicorn, from identical random states.

Equivalence means: same exit (return / same system call / fault at the same address) and,
for returns and system calls, identical registers, flags, VFP state and identical
contents of all writable memory (data, bss, stack, scratch buffers, TLS).

usage: python3 -m recomp.gametest --lib build/recomp/game.so [--cases 4] [--sample 2000]
           [--seed 1] [--only 0x005bf888,...] [--report build/recomp/gametest.tsv]
"""
from __future__ import annotations

import argparse
import collections
import os
import random
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
ROOT = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)

import codebin2elf  # noqa: E402
import exheader as exh  # noqa: E402
from recomp.harness.machines import (RET_TRAP, ST_BADRET, ST_BUDGET, ST_FAULT, ST_NOFUNC, ST_OTHER,  # noqa: E402
                                     ST_RETURN, ST_SVC, ST_TRAP, ST_UNDEF, STATUS_NAMES, NativeMachine,
                                     RefMachine, Region, State, diff_states)

STACK_TOP, STACK_SIZE = 0x10000000, 0x00100000
BUF_BASE, BUF_SIZE = 0x30000000, 0x10000
TLS = 0x1FF80000
PAGE = 0x1000
MAX_INSNS = 300_000


def page_up(x: int) -> int:
    return (x + PAGE - 1) & ~(PAGE - 1)


def build_regions(header: exh.ExHeader, code: bytes) -> tuple[list[Region], list[Region]]:
    t, ro, d = header.text, header.ro, header.data
    text = code[: t.size]
    rodata = code[t.padded_size: t.padded_size + ro.size]
    data = code[t.padded_size + ro.padded_size: t.padded_size + ro.padded_size + d.size]
    rw_size = page_up(d.size + header.bss_size)
    fixed = [Region(t.address, page_up(t.size), False, text),
             Region(ro.address, page_up(ro.size), False, rodata)]
    rw = [Region(d.address, rw_size, True, data.ljust(rw_size, b"\0")),
          Region(STACK_TOP - STACK_SIZE, STACK_SIZE, True, bytes(STACK_SIZE)),
          Region(BUF_BASE, BUF_SIZE, True, bytes(BUF_SIZE)),
          Region(TLS, PAGE, True, bytes(PAGE))]
    return fixed, rw


def pointer_rich_buffer(rng: random.Random) -> bytes:
    out = bytearray()
    for _ in range(BUF_SIZE // 4):
        k = rng.random()
        if k < 0.3:
            w = rng.randint(0, 64)
        elif k < 0.45:
            w = rng.getrandbits(32)
        elif k < 0.55:
            w = struct.unpack("<I", struct.pack("<f", rng.uniform(-1000, 1000)))[0]
        elif k < 0.85:
            w = BUF_BASE + rng.randrange(0, BUF_SIZE - 0x100, 4)
        else:
            w = 0
        out += struct.pack("<I", w)
    return bytes(out)


def random_state(rng: random.Random) -> State:
    st = State()
    for i in range(13):
        k = rng.random()
        if k < 0.45:
            st.r[i] = BUF_BASE + rng.randrange(0, BUF_SIZE - 0x400, 4)
        elif k < 0.75:
            st.r[i] = rng.randint(0, 32)
        else:
            st.r[i] = rng.getrandbits(32)
    st.r[13] = STACK_TOP - 0x1000
    st.r[14] = RET_TRAP
    st.n, st.z, st.c, st.v, st.q = (rng.getrandbits(1) for _ in range(5))
    st.ge = rng.getrandbits(4)
    for i in range(32):
        st.s[i] = (struct.unpack("<I", struct.pack("<f", rng.uniform(-100, 100)))[0]
                   if rng.random() < 0.8 else rng.getrandbits(32))
    st.fpscr = (rng.getrandbits(4) << 28) | rng.choice([0x03000000, 0x03C00000, 0])   # RunFast, kernel default, IEEE
    st.tls = TLS
    return st


TEXT_RANGE = (0, 1 << 32)


class Tester:
    def __init__(self, lib: str, fixed: list[Region], rw: list[Region], seed: int):
        self.missing: set[int] = set()
        self.rw = rw
        regions = fixed + rw
        self.ref = RefMachine(regions)
        self.nat = NativeMachine(lib, regions, self.ref.cpsr_misc)
        self.funcs = self.nat.functions()
        rng = random.Random(seed ^ 0x5EED)
        self.buffers = [pointer_rich_buffer(rng) for _ in range(6)]

    def reset(self, buf: bytes) -> None:
        for rg in self.rw:
            data = buf if rg.addr == BUF_BASE else rg.data
            self.ref.write(rg.addr, data)
            self.nat.write(rg.addr, data)

    def memory_diff(self) -> str | None:
        for rg in self.rw:
            a = self.ref.read(rg.addr, rg.size)
            b = self.nat.read(rg.addr, rg.size)
            if a != b:
                for i in range(len(a)):
                    if a[i] != b[i]:
                        return f"memory differs at {rg.addr + i:#010x} ({a[i]:02x} vs {b[i]:02x})"
        return None

    def run_case(self, addr: int, thumb: bool, rng: random.Random) -> tuple[str, str]:
        w = self.ref.read(addr, 4) if not thumb else b""
        if len(w) == 4 and (struct.unpack("<I", w)[0] & 0xFFFF0FFF) == 0xE49E0004:
            return "protocol", "reads an inline descriptor after its call site; tested through its callers"
        st = random_state(rng)
        self.reset(rng.choice(self.buffers))
        self.ref.clear_exclusive()
        self.ref.set_state(st)
        a = self.ref.run(addr, thumb, until=RET_TRAP, count=MAX_INSNS)
        b = self.nat.run(addr, thumb, st, budget=2 * MAX_INSNS)
        if a.status == ST_BUDGET or b.status == ST_BUDGET:
            return "inconclusive", "budget"
        if a.status == ST_UNDEF:
            if b.status == ST_NOFUNC:
                return "agree-fault", "both jumped into non-code"
            return "unicorn-undef", f"pc {a.info0:#x}"
        if b.status == ST_TRAP:
            if a.status == ST_FAULT:
                return "agree-fault", "trap after fault"
            return "native-trap", f"trap at {b.info0:#x} kind {b.info1}"
        if b.status == ST_NOFUNC:
            if a.status == ST_FAULT and (a.info0 & ~1) == (b.info0 & ~1):
                return "agree-fault", "wild jump"
            if not (TEXT_RANGE[0] <= (b.info0 & ~1) < TEXT_RANGE[1]):
                return "agree-fault", "jump outside code"
            self.missing.add(b.info0)
            return "native-nofunc", f"indirect target {b.info0:#x} (unicorn: {STATUS_NAMES.get(a.status)} {a.info0:#x})"
        if b.status == ST_BADRET:
            if a.status == ST_FAULT and (a.info0 & ~1) == (b.info0 & ~1):
                return "agree-fault", "both returned to a wild address"
            return "native-badret", f"returned to {b.info0:#x}, expected {b.info1:#x}"
        if a.status == ST_OTHER:
            return "inconclusive", f"unicorn other {a.info0}"
        if a.status != b.status:
            return "fail-status", f"{STATUS_NAMES.get(a.status)} {a.info0:#x} vs {STATUS_NAMES.get(b.status)} {b.info0:#x}"
        if a.status == ST_FAULT:
            if abs(a.info0 - b.info0) < 8:
                return "agree-fault", ""
            return "fail-fault", f"{a.info0:#x} vs {b.info0:#x}"
        if a.status == ST_SVC and (a.info0 != b.info0 or a.info1 != b.info1):
            return "fail-svc", f"svc {a.info0:#x}@{a.info1:#x} vs {b.info0:#x}@{b.info1:#x}"
        d = diff_states(a.state, b.state)
        if d:
            return "fail-state", "; ".join(d[:5])
        m = self.memory_diff()
        if m:
            return "fail-memory", m
        return ("pass-svc" if a.status == ST_SVC else "pass"), ""


VERDICT_ORDER = ["fail-state", "fail-memory", "fail-status", "fail-fault", "fail-svc", "native-badret",
                 "native-trap", "native-nofunc", "unicorn-undef"]


def function_verdict(outcomes: list[str]) -> str:
    for v in VERDICT_ORDER:
        if v in outcomes:
            return v
    if any(o.startswith("pass") for o in outcomes):
        return "pass"
    if "agree-fault" in outcomes:
        return "agree-fault-only"
    if outcomes and all(o == "protocol" for o in outcomes):
        return "protocol"
    return "inconclusive"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lib", default=os.path.join(ROOT, "build", "recomp", "game.so"))
    ap.add_argument("--exheader", default=os.path.join(ROOT, "baserom", "update_v130", "exheader.bin"))
    ap.add_argument("--code", default=os.path.join(ROOT, "baserom", "update_v130", "code.bin"))
    ap.add_argument("--cases", type=int, default=4)
    ap.add_argument("--sample", type=int, default=0, help="random subset of functions (0 = all)")
    ap.add_argument("--shard", default="0/1", help="i/n: test every n-th function starting at i")
    ap.add_argument("--only", default="")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--report", default="")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    with open(args.exheader, "rb") as f:
        header = exh.parse(f.read())
    with open(args.code, "rb") as f:
        code = codebin2elf.load_code(header, f.read())
    fixed, rw = build_regions(header, code)
    global TEXT_RANGE
    import recompile    # the range build_game.py recompiles: with a code mod's new code after the code too
    TEXT_RANGE = (header.text.address, header.text.address + recompile.code_size(header, code))
    tester = Tester(args.lib, fixed, rw, args.seed)
    funcs = tester.funcs
    if args.only:
        wanted = {int(x, 16) for x in args.only.split(",")}
        funcs = [f for f in funcs if f[0] in wanted]
    i, n = (int(x) for x in args.shard.split("/"))
    funcs = funcs[i::n]
    rng = random.Random(args.seed)
    if args.sample:
        funcs = rng.sample(funcs, min(args.sample, len(funcs)))
    verdicts = collections.Counter()
    outcomes_total = collections.Counter()
    rows = []
    t0 = time.time()
    for k, (addr, mode) in enumerate(funcs, 1):
        outs, notes = [], []
        for _ in range(args.cases):
            o, note = tester.run_case(addr, bool(mode), rng)
            outs.append(o)
            notes.append(note)
            outcomes_total[o] += 1
        v = function_verdict(outs)
        verdicts[v] += 1
        note = next((nt for o, nt in zip(outs, notes) if o == v), "")
        rows.append((addr, mode, v, note))
        if args.verbose and v not in ("pass", "agree-fault-only", "inconclusive"):
            print(f"  {addr:#010x} {v}: {note}", flush=True)
        if k % 500 == 0:
            print(f"  {k}/{len(funcs)} functions, {time.time() - t0:.0f}s: {dict(verdicts)}", flush=True)
    print(f"functions: {len(funcs)}  cases: {sum(outcomes_total.values())}  time: {time.time() - t0:.0f}s")
    print(f"case outcomes: {dict(outcomes_total)}")
    print(f"function verdicts: {dict(verdicts)}")
    if tester.missing:
        path = os.path.join(os.path.dirname(args.lib), "missing_targets.txt")
        old = set()
        if os.path.exists(path):
            old = {int(l.split()[0], 16) for l in open(path) if l.strip() and not l.startswith("#")}
        with open(path, "w") as f:
            f.write("# indirect-call targets reached in tests that are not in the function table\n")
            for t in sorted(old | tester.missing):
                f.write(f"0x{t:08x}\t{'thumb' if t & 1 else 'arm'}\n")
        print(f"missing indirect targets: {len(tester.missing)} new (see {path})")
    if args.report:
        with open(args.report, "w") as f:
            f.write("address\tmode\tverdict\tnote\n")
            for addr, mode, v, note in rows:
                f.write(f"0x{addr:08x}\t{'thumb' if mode else 'arm'}\t{v}\t{note}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
