#!/usr/bin/env python3
"""Instruction-level differential fuzzing: recompiled C vs Unicorn (ARM11 MPCore).

Random encodings that the translator accepts as straight-line code are placed one per
slot in a synthetic code region, each translated into its own C function, compiled into
a shared library with the native harness, and executed one instruction at a time from
identical random states on both machines. Registers, flags, VFP registers, FPSCR and a
64 KiB scratch buffer must match exactly (or both must fault at the same address).

usage: python3 -m recomp.fuzz --mode arm --count 3000 --cases 16 [--seed 1] [--only vfp]
"""
from __future__ import annotations

import argparse
import collections
import os
import random
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from recomp.arm import ArmTranslator  # noqa: E402
from recomp.thumb import ThumbTranslator  # noqa: E402
from recomp.emit import straight_line  # noqa: E402
from recomp.harness.machines import (ST_FAULT, ST_RETURN, ST_UNDEF, STATUS_NAMES, NativeMachine,  # noqa: E402
                                     RefMachine, Region, State, diff_states)
from recomp.insn import OP  # noqa: E402

FUZZ_BASE = 0x00100000
BUF_BASE, BUF_SIZE = 0x30000000, 0x10000
TLS = 0x1FF80000
RUNTIME = os.path.join(HERE, "runtime")
HARNESS = os.path.join(HERE, "harness", "harness.c")
CFLAGS = ["-O1", "-fPIC", "-shared", "-ffp-contract=off", "-frounding-math", "-fno-strict-aliasing", "-fwrapv",
          "-Wno-unused-variable", "-Wno-unused-label", "-DRC_NO_BUDGET", f"-I{RUNTIME}"]


def class_of(word: int) -> str:
    if word >> 28 == 0xF:
        return "uncond"
    op = (word >> 25) & 7
    if op in (6, 7) and ((word >> 8) & 0xE) == 0xA:
        return "vfp"
    if op == 3 and word & 0x10:
        return "media"
    if op == 0 and (word & 0x90) == 0x90:
        return "mul/extra"
    return {0: "dp", 1: "dp-imm", 2: "ldst", 3: "ldst", 4: "ldm", 5: "branch", 6: "coproc", 7: "coproc"}[op]


def vfp_transfer_word(rng: random.Random) -> int:
    cond = 0xE if rng.random() < 0.7 else rng.randrange(15)
    rt, rt2, v, bit = rng.randrange(15), rng.randrange(15), rng.randrange(16), rng.getrandbits(1)
    form = rng.randrange(4)
    if form == 0:      # VMOV core <-> single
        w = (0xE << 24) | (rng.getrandbits(1) << 20) | (v << 16) | (rt << 12) | (0xA << 8) | (bit << 7) | 0x10
    elif form == 1:    # VMRS / VMSR FPSCR
        w = (0xE << 24) | (7 << 21) | (rng.getrandbits(1) << 20) | (1 << 16) | (rng.randrange(16) << 12) | (0xA << 8) | 0x10
    elif form == 2:    # VMOV core <-> half of a double
        w = (0xE << 24) | (rng.getrandbits(1) << 21) | (rng.getrandbits(1) << 20) | (v << 16) | (rt << 12) | (0xB << 8) | 0x10
    else:              # VMOV two cores <-> double / two singles
        w = (0xC4 << 20) | (rng.getrandbits(1) << 20) | (rt2 << 16) | (rt << 12) | (rng.choice([0xA, 0xB]) << 8) | (bit << 5) | 0x10 | v
    return (cond << 28) | w


def random_word(rng: random.Random, only: str | None) -> int:
    if only == "vfp" and rng.random() < 0.3:
        return vfp_transfer_word(rng)
    if only == "vfpvec":    # VFP data processing (CDP to cp10/cp11)
        cond = 0xE if rng.random() < 0.8 else rng.randrange(15)
        return (cond << 28) | 0x0E000000 | (rng.getrandbits(32) & 0x00FFF0EF) | (rng.choice([0xA, 0xB]) << 8)
    w = rng.getrandbits(32)
    if rng.random() < 0.7:
        w = (w & 0x0FFFFFFF) | 0xE0000000
    if only == "vfp" or (only is None and rng.random() < 0.15):
        # coprocessor 10/11 space
        w = (w & 0xF1FFF0FF) | (rng.choice([0x0C, 0x0D, 0x0E]) << 24) | (rng.choice([0xA, 0xB]) << 8)
    elif only == "media":
        w = (w & 0xF1FFFFFF) | (0x3 << 25) | 0x10
    elif only == "mul":
        w = (w & 0xF0FFFF0F) | 0x90
    return w


def random_state(rng: random.Random) -> State:
    st = State()
    for i in range(15):
        k = rng.random()
        if k < 0.4:
            st.r[i] = BUF_BASE + 0x8000 + rng.randrange(-0x800, 0x800, 4)
        elif k < 0.5:
            st.r[i] = BUF_BASE + 0x8000 + rng.randrange(-0x800, 0x800)
        elif k < 0.75:
            st.r[i] = rng.randint(0, 64)
        else:
            st.r[i] = rng.getrandbits(32)
    st.r[13] = BUF_BASE + 0x8000 + rng.randrange(-0x400, 0x400, 8)
    st.n, st.z, st.c, st.v, st.q = (rng.getrandbits(1) for _ in range(5))
    st.ge = rng.getrandbits(4)
    for i in range(32):
        k = rng.random()
        if k < 0.4:
            st.s[i] = struct.unpack("<I", struct.pack("<f", rng.uniform(-1e6, 1e6)))[0]
        elif k < 0.5:
            st.s[i] = rng.choice([0, 0x80000000, 0x7F800000, 0xFF800000, 0x7FC00000, 0x7F800001, 0xFFA00000,
                                  0x00000001, 0x807FFFFF, 0x00400000, 0x00800000, 0x3F800000, 0x7F7FFFFF])
        elif k < 0.6:   # tiny normals: products and quotients underflow into the denormal range
            v = rng.choice([1e-20, 1e-19, 3e-38, 1.2e-38]) * rng.uniform(-4, 4)
            st.s[i] = struct.unpack("<I", struct.pack("<f", v))[0]
        else:
            st.s[i] = rng.getrandbits(32)
    for i in range(16):   # some double-precision special values (d0-d15 overlay s0-s31)
        if rng.random() < 0.25:
            hi, lo = rng.choice([(0x7FF00000, 0), (0xFFF00000, 0), (0x7FF80000, 0), (0x7FF00000, 1), (0xFFF40000, 5),
                                 (0x000FFFFF, 0xFFFFFFFF), (0x80000000, 1), (0x00100000, 0), (0x80000000, 0),
                                 (0x1FF00000, rng.getrandbits(32)), (0x9FE00000, rng.getrandbits(32)),
                                 (0x36A00000, 0), (0x38100000, 0)])
            st.s[2 * i], st.s[2 * i + 1] = lo, hi
    st.fpscr = ((rng.getrandbits(4) << 28) | rng.choice([0, 0, 0x03000000, 0x01000000, 0x02000000])
                | (rng.choice([0, 0, 1, 2, 3]) << 22))     # rounding mode: nearest, +inf, -inf, zero
    st.tls = TLS
    return st


def build_library(insns, workdir: str) -> str:
    lines = ['#include "recomp.h"']
    for i, ins in enumerate(insns):
        lines.append(f"static void fz_{i}(RCtx *ctx) {{ {' '.join(straight_line(ins))} }}")
    lines.append("const RFuncEntry rc_func_table[] = {")
    for i, ins in enumerate(insns):
        lines.append(f"  {{0x{ins.addr:08x}u, {1 if ins.mode == 'thumb' else 0}, fz_{i}}},")
    lines.append("};")
    lines.append(f"const u32 rc_func_count = {len(insns)};")
    lines += ["const u32 rc_hook_addrs[] = {0};", "const u32 rc_hook_count = 0;", "RHookFn rc_hooks[1];",
              "u32 rc_tunables[1];", "const u32 rc_tunable_addrs[] = {0};",
              "const char *const rc_tunable_names[] = {0};", "const u32 rc_tunable_count = 0;"]
    src = os.path.join(workdir, "fuzz.c")
    with open(src, "w") as f:
        f.write("\n".join(lines) + "\n")
    lib = os.path.join(workdir, "fuzz.so")
    r = subprocess.run(["clang", *CFLAGS, HARNESS, src, "-o", lib, "-lm"], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr[-4000:])
    return lib


def _image_readers(code: bytes):
    def read32(a: int):
        off = a - FUZZ_BASE
        if 0 <= off <= len(code) - 4:
            return struct.unpack_from("<I", code, off)[0]
        return None

    def read16(a: int):
        off = a - FUZZ_BASE
        if 0 <= off <= len(code) - 2:
            return struct.unpack_from("<H", code, off)[0]
        return None
    return read16, read32


def fuzz(mode: str, count: int, cases: int, seed: int, only: str | None, verbose: int = 0,
         words: list[int] | None = None):
    rng = random.Random(seed)
    thumb = mode == "thumb"
    chosen = list(words or [])
    vecs: dict[int, int] = {}          # slot -> FPSCR LEN/STRIDE bits the slot runs under
    if thumb:
        probe = ThumbTranslator(lambda a: probe_hw if a == FUZZ_BASE else None, lambda a: None)
        while len(chosen) < count:
            probe_hw = rng.getrandbits(16)
            if probe.decode(FUZZ_BASE).kind == OP:
                chosen.append(probe_hw)
        code = b"".join(struct.pack("<H", w) for w in chosen)
        step = 2
    else:
        probe = ArmTranslator(lambda a: None)
        while len(chosen) < count:
            w = random_word(rng, only)
            # short-vector mode: random LEN (stride 1), up to a whole register bank.
            # Unicorn (QEMU 5.0) gets double-precision VMOV/VABS/VNEG/VSQRT vectors wrong
            # (it writes element 0's result into the source bank), so those stay scalar.
            vec = rng.randint(1, 3 if (w >> 8) & 1 else 7) << 16 if only == "vfpvec" else 0
            if vec and (w >> 8) & 1 and (w & 0x00B00040) == 0x00B00040 and (w >> 16) & 0xE == 0:
                vec = 0
            if probe.decode(FUZZ_BASE, w, vec).kind == OP:
                vecs[len(chosen)] = vec
                chosen.append(w)
        code = b"".join(struct.pack("<I", w) for w in chosen)
        step = 4
    code_size = (len(code) + 0x1FFF) & ~0xFFF
    read16, read32 = _image_readers(code)
    if thumb:
        tr = ThumbTranslator(read16, read32)
        insns = [tr.decode(FUZZ_BASE + 2 * i) for i in range(len(chosen))]
    else:
        tr = ArmTranslator(read32)
        insns = [tr.decode(FUZZ_BASE + 4 * i, w, vecs.get(i, 0)) for i, w in enumerate(chosen)]
    insns = [ins for ins in insns if ins.kind == OP and ins.size == step]
    regions = [Region(FUZZ_BASE, code_size, False, code), Region(BUF_BASE, BUF_SIZE, True), Region(TLS, 0x1000, True)]
    with tempfile.TemporaryDirectory(prefix="fuzz-") as tmp:
        lib = build_library(insns, tmp)
        ref = RefMachine(regions)
        nat = NativeMachine(lib, regions, ref.cpsr_misc)
        nat.lib.rh_set_check_return(0)      # single instructions, not functions
        stats = collections.Counter()
        failures = collections.defaultdict(list)
        for ins in insns:
            vec = vecs.get((ins.addr - FUZZ_BASE) // step, 0)
            for _ in range(cases):
                st = random_state(rng)
                st.fpscr |= vec
                buf = rng.randbytes(BUF_SIZE)
                ref.write(BUF_BASE, buf)
                nat.write(BUF_BASE, buf)
                ref.clear_exclusive()
                ref.set_state(st)
                a = ref.run(ins.addr, thumb, until=ins.addr + ins.size)
                b = nat.run(ins.addr, thumb, st)
                if a.status == ST_UNDEF:
                    stats["unicorn-undef"] += 1
                    failures[ins.word].append(("decoder accepts an encoding ARM11 treats as undefined", st))
                    break
                if a.status != b.status:
                    stats["status"] += 1
                    failures[ins.word].append((f"status {STATUS_NAMES.get(a.status)} vs {STATUS_NAMES.get(b.status)} "
                                               f"(info {a.info0:#x} vs {b.info0:#x})", st))
                    continue
                if a.status == ST_FAULT:
                    if abs(a.info0 - b.info0) >= 8:
                        stats["fault-addr"] += 1
                        failures[ins.word].append((f"fault address {a.info0:#x} vs {b.info0:#x}", st))
                    else:
                        stats["agree-fault"] += 1
                    continue
                if a.status != ST_RETURN:
                    stats[f"both-{STATUS_NAMES.get(a.status)}"] += 1
                    continue
                d = diff_states(a.state, b.state)
                if ref.read(BUF_BASE, BUF_SIZE) != nat.read(BUF_BASE, BUF_SIZE):
                    d.append("memory differs")
                if d:
                    stats["state"] += 1
                    failures[ins.word].append(("; ".join(d[:6]), st))
                else:
                    stats["pass"] += 1
    return insns, stats, failures


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mode", default="arm")
    ap.add_argument("--count", type=int, default=2000)
    ap.add_argument("--cases", type=int, default=16)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--only", choices=["vfp", "vfpvec", "media", "mul"])
    ap.add_argument("--word", action="append", default=[], help="always include this encoding (hex)")
    ap.add_argument("-v", "--verbose", action="count", default=0)
    args = ap.parse_args(argv)
    words = [int(w, 16) for w in args.word]
    insns, stats, failures = fuzz(args.mode, args.count, args.cases, args.seed, args.only, args.verbose, words)
    by_class = collections.Counter("thumb" if args.mode == "thumb" else class_of(ins.word) for ins in insns)
    print(f"instructions: {len(insns)}  by class: {dict(by_class)}")
    print(f"results: {dict(stats)}")
    bad_classes = collections.Counter("thumb" if args.mode == "thumb" else class_of(w) for w in failures)
    print(f"failing encodings: {len(failures)}  by class: {dict(bad_classes)}")
    try:
        from capstone import CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_THUMB, Cs
        md = Cs(CS_ARCH_ARM, CS_MODE_THUMB if args.mode == "thumb" else CS_MODE_ARM)
    except ImportError:
        md = None
    for w, items in list(failures.items())[: 40 if args.verbose else 15]:
        dis = ""
        if md:
            d = list(md.disasm(struct.pack("<H" if args.mode == "thumb" else "<I", w), FUZZ_BASE))
            dis = f"{d[0].mnemonic} {d[0].op_str}" if d else "?"
        print(f"  {w:08x} {dis:<40} {items[0][0]}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
