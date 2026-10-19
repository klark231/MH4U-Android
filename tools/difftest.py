#!/usr/bin/env python3
"""difftest: check that candidate C behaves like an original function in code.bin.

The candidate is compiled with clang for armv6k and linked so that every function it
calls resolves to its real address in the game (names from config/symbols.txt), plus a
tiny runtime for compiler helpers (tools/difftest_rt.c). Original and candidate then
run in Unicorn from identical CPU/memory states on many randomized inputs, and the
return value plus every byte either version wrote (outside the stack) must match.

Pointer arguments are detected automatically: the argument profile (which of r0-r3
are pointers to scratch buffers) that lets the *original* run without faulting wins.
Cases where the original itself faults are skipped, not counted as passes.

usage: difftest.py 0x005bf888 src/util/crc32.c --func crc32_string --args 2 [--ret int]
       [--ptr-args auto|none|0,1] [--fargs N] [--cases 500] [--seed 1] [--json]
Exit status 0 = equivalent on every compared case.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import random
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import codebin2elf  # noqa: E402
import exheader as exh  # noqa: E402

from unicorn import (UC_ARCH_ARM, UC_HOOK_MEM_INVALID, UC_HOOK_MEM_WRITE,  # noqa: E402
                     UC_MODE_ARM, Uc, UcError)
from unicorn.arm_const import (UC_ARM_REG_C1_C0_2, UC_ARM_REG_D0,  # noqa: E402
                               UC_ARM_REG_FPEXC, UC_ARM_REG_LR, UC_ARM_REG_PC,
                               UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
                               UC_ARM_REG_S0, UC_ARM_REG_SP)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RT_SOURCE = os.path.join(HERE, "difftest_rt.c")

CAND_BASE, CAND_SIZE = 0x02000000, 0x00100000
BUF_BASE, BUF_SLOT, BUF_COUNT = 0x30000000, 0x1000, 8
STACK_TOP, STACK_SIZE = 0x0F000000, 0x00100000
RET_TRAP = 0x0E000000
MAX_INSNS = 2_000_000
ARG_REGS = (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)
CFLAGS = ["--target=armv6k-none-eabi", "-marm", "-mfloat-abi=hard", "-mfpu=vfpv2", "-O2",
          "-ffreestanding", "-fno-builtin", "-nostdlib", "-fno-exceptions",
          "-fno-unwind-tables", "-fno-asynchronous-unwind-tables", "-fno-common"]
LINKER_SCRIPT = f"""
SECTIONS {{
  . = 0x{CAND_BASE:08x};
  .text : {{ *(.text .text.*) }}
  .rodata : {{ *(.rodata .rodata.*) }}
  .data : {{ *(.data .data.*) }}
  .bss : {{ *(.bss .bss.*) *(COMMON) }}
  /DISCARD/ : {{ *(.ARM.exidx*) *(.ARM.attributes) *(.comment) *(.note*) }}
}}
"""
C_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class DifftestError(RuntimeError):
    pass


# ── Building the candidate ───────────────────────────────────────────────────

def tool(*names: str) -> str:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    raise DifftestError(f"required tool not found: {' or '.join(names)}")


def load_symbols(path: str | None) -> dict[str, int]:
    out: dict[str, int] = {}
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if line:
                    addr, name = line.split()[:2]
                    if C_IDENT.match(name):
                        out[name] = int(addr, 16)
    return out


@dataclass
class Candidate:
    image: bytes
    entry: int


def build_candidate(source: str, func: str, symbols: dict[str, int], include_dirs: list[str],
                    extra_cflags: list[str] | None = None) -> Candidate:
    clang = tool("clang")
    lld = tool("ld.lld")
    nm = tool("llvm-nm")
    objcopy = tool("llvm-objcopy")
    with tempfile.TemporaryDirectory(prefix="difftest-") as tmp:
        cand_o, rt_o = os.path.join(tmp, "cand.o"), os.path.join(tmp, "rt.o")
        incs = [f"-I{d}" for d in include_dirs]
        for src, obj in ((source, cand_o), (RT_SOURCE, rt_o)):
            r = subprocess.run([clang, *CFLAGS, *incs, *(extra_cflags or []), "-c", src, "-o", obj],
                               capture_output=True, text=True)
            if r.returncode:
                raise DifftestError(f"compile failed for {src}:\n{r.stderr}")
        undefined = set(subprocess.run([nm, "-u", "-j", cand_o], capture_output=True, text=True,
                                       check=True).stdout.split())
        provided_by_rt = set(subprocess.run([nm, "-g", "-j", "--defined-only", rt_o],
                                            capture_output=True, text=True, check=True).stdout.split())
        defsyms, missing = [], []
        for name in sorted(undefined - provided_by_rt):
            if name in symbols:
                defsyms.append(f"--defsym={name}=0x{symbols[name]:08x}")
            else:
                missing.append(name)
        if missing:
            raise DifftestError("candidate calls functions with no known address (add them to "
                                f"config/symbols.txt first): {', '.join(missing)}")
        script = os.path.join(tmp, "link.ld")
        with open(script, "w") as f:
            f.write(LINKER_SCRIPT)
        elf = os.path.join(tmp, "cand.elf")
        r = subprocess.run([lld, "-T", script, "-e", func, *defsyms, cand_o, rt_o, "-o", elf],
                           capture_output=True, text=True)
        if r.returncode:
            raise DifftestError(f"link failed:\n{r.stderr}")
        syms = subprocess.run([nm, "--defined-only", elf], capture_output=True, text=True, check=True).stdout
        entry = None
        for line in syms.splitlines():
            parts = line.split()
            if len(parts) == 3 and parts[2] == func and parts[1] in "Tt":
                entry = int(parts[0], 16)
        if entry is None:
            raise DifftestError(f"function {func!r} not found in {source}")
        binp = os.path.join(tmp, "cand.bin")
        subprocess.run([objcopy, "-O", "binary", elf, binp], check=True)
        with open(binp, "rb") as f:
            image = f.read()
    if len(image) > CAND_SIZE:
        raise DifftestError("candidate image too large")
    return Candidate(image, entry)


# ── Emulation ────────────────────────────────────────────────────────────────

@dataclass
class RunResult:
    fault: str | None
    regs: dict[str, int] = field(default_factory=dict)
    writes: dict[int, int] = field(default_factory=dict)


class Machine:
    def __init__(self, code: bytes, base: int, mem_end: int, cand: Candidate | None):
        self.code, self.base = code, base
        self.mem_end = (mem_end + 0xFFF) & ~0xFFF
        mu = self.mu = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        mu.mem_map(base, self.mem_end - base)
        mu.mem_write(base, code)
        mu.mem_map(CAND_BASE, CAND_SIZE)
        if cand:
            mu.mem_write(CAND_BASE, cand.image)
        mu.mem_map(BUF_BASE, BUF_SLOT * BUF_COUNT)
        mu.mem_map(STACK_TOP - STACK_SIZE, STACK_SIZE)
        mu.mem_map(RET_TRAP, 0x1000)
        self._cand_image = cand.image if cand else b""
        self._writes: list[tuple[int, int]] = []
        mu.hook_add(UC_HOOK_MEM_WRITE, self._on_write)
        mu.hook_add(UC_HOOK_MEM_INVALID, lambda *a: False)

    def _on_write(self, _uc, _access, addr, size, _value, _data):
        self._writes.append((addr, size))

    def _pristine(self, addr: int) -> int:
        if self.base <= addr < self.base + len(self.code):
            return self.code[addr - self.base]
        if CAND_BASE <= addr < CAND_BASE + len(self._cand_image):
            return self._cand_image[addr - CAND_BASE]
        return 0

    def run(self, entry: int, thumb: bool, args: list[int], fargs: list[float],
            buffers: bytes, regions: list[tuple[int, bytes]] | None = None) -> RunResult:
        mu = self.mu
        mu.reg_write(UC_ARM_REG_C1_C0_2, mu.reg_read(UC_ARM_REG_C1_C0_2) | (0xF << 20))
        mu.reg_write(UC_ARM_REG_FPEXC, 0x40000000)
        mu.mem_write(BUF_BASE, buffers)
        for addr, blob in regions or []:
            mu.mem_write(addr, blob)
        for reg, v in zip(ARG_REGS, args + [0] * (4 - len(args))):
            mu.reg_write(reg, v & 0xFFFFFFFF)
        for i, fv in enumerate(fargs):
            mu.reg_write(UC_ARM_REG_S0 + i, struct.unpack("<I", struct.pack("<f", fv))[0])
        mu.reg_write(UC_ARM_REG_SP, STACK_TOP - 0x100)
        mu.reg_write(UC_ARM_REG_LR, RET_TRAP)
        self._writes = []
        fault = None
        try:
            mu.emu_start(entry | (1 if thumb else 0), RET_TRAP, count=MAX_INSNS)
            if mu.reg_read(UC_ARM_REG_PC) != RET_TRAP:
                fault = "timeout"
        except UcError as e:
            fault = f"{e} at pc=0x{mu.reg_read(UC_ARM_REG_PC):08x}"
        regs = {"r0": mu.reg_read(UC_ARM_REG_R0), "r1": mu.reg_read(UC_ARM_REG_R1),
                "s0": mu.reg_read(UC_ARM_REG_S0), "d0": mu.reg_read(UC_ARM_REG_D0)}
        stack_lo = STACK_TOP - STACK_SIZE
        touched = {a + i for a, s in self._writes for i in range(s) if not stack_lo <= a + i < STACK_TOP}
        writes = {}
        for a in sorted(touched):
            try:
                writes[a] = mu.mem_read(a, 1)[0]
            except UcError:                  # attempted write to unmapped memory (the fault)
                pass
        touched = set(writes)
        for a in touched:                    # restore image/candidate memory for the next run
            if not BUF_BASE <= a < BUF_BASE + BUF_SLOT * BUF_COUNT:
                mu.mem_write(a, bytes([self._pristine(a)]))
        for addr, blob in regions or []:
            mu.mem_write(addr, bytes(self._pristine(addr + i) for i in range(len(blob))))
        return RunResult(fault, regs, writes)


# ── Inputs ───────────────────────────────────────────────────────────────────

INTERESTING = [0, 1, 2, 3, 4, 7, 8, 0xFF, 0x100, 0x7FFF, 0xFFFF, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF, 0xFFFFFFFE]


def rand_scalar(rng: random.Random) -> int:
    k = rng.random()
    if k < 0.3:
        return rng.choice(INTERESTING)
    if k < 0.6:
        return rng.randint(-256, 256) & 0xFFFFFFFF
    if k < 0.8:
        return rng.randint(0, 0xFFFF)
    return rng.getrandbits(32)


def rand_words(rng: random.Random, size: int) -> bytes:
    out = bytearray()
    for _ in range(size // 4):
        k = rng.random()
        if k < 0.35:
            w = rng.randint(0, 64)
        elif k < 0.55:
            w = rng.getrandbits(32)
        elif k < 0.7:
            w = struct.unpack("<I", struct.pack("<f", rng.uniform(-1000, 1000)))[0]
        elif k < 0.9:
            w = BUF_BASE + rng.randrange(BUF_COUNT) * BUF_SLOT + rng.randrange(0, BUF_SLOT // 2, 4)
        else:
            w = 0
        out += struct.pack("<I", w)
    return bytes(out) + bytes(rng.getrandbits(8) for _ in range(size % 4))


def rand_buffers(rng: random.Random) -> bytes:
    """Buffer slots filled with a mix of small ints, random words, floats, pointers into
    the buffers (for nested structures) and NUL-terminated ASCII text."""
    out = bytearray()
    for slot in range(BUF_COUNT):
        if slot % 4 == 3:                    # one in four slots is a C string
            n = rng.randint(0, 40)
            s = bytes(rng.randint(0x20, 0x7E) for _ in range(n)) + b"\0"
            out += s.ljust(BUF_SLOT, b"\0")
            continue
        out += rand_words(rng, BUF_SLOT)
    return bytes(out)


def make_case(rng: random.Random, nargs: int, ptr_slots: tuple[int, ...], nfargs: int):
    args = []
    for i in range(nargs):
        if i in ptr_slots:
            args.append(BUF_BASE + rng.randrange(BUF_COUNT) * BUF_SLOT + rng.choice([0, 0, 0, 4, 8, 16]))
        else:
            args.append(rand_scalar(rng))
    fargs = [rng.choice([0.0, 1.0, -1.0, 0.5, rng.uniform(-1e4, 1e4)]) for _ in range(nfargs)]
    return args, fargs, rand_buffers(rng)


def profile_pointer_args(m: Machine, entry: int, thumb: bool, nargs: int, nfargs: int,
                         seed: int, trials: int = 24) -> tuple[tuple[int, ...], int]:
    """Pick the pointer/scalar profile under which the original faults least."""
    best = None
    for k in range(nargs + 1):
        for slots in itertools.combinations(range(nargs), k):
            rng = random.Random(seed)
            faults = sum(m.run(entry, thumb, *make_case(rng, nargs, slots, nfargs)).fault is not None
                         for _ in range(trials))
            if best is None or faults < best[1]:
                best = (slots, faults)
            if faults == 0:
                return slots, 0
    return best


# ── Comparison ───────────────────────────────────────────────────────────────

RET_REGS = {"void": [], "int": ["r0"], "i64": ["r0", "r1"], "float": ["s0"], "double": ["d0"]}


@dataclass
class Report:
    ptr_slots: tuple[int, ...]
    compared: int = 0
    skipped: int = 0
    passed: int = 0
    mismatches: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.compared > 0 and self.passed == self.compared


def compare(code: bytes, header_base: int, mem_end: int, orig: int, thumb: bool, cand: Candidate,
            nargs: int, nfargs: int, ret: str, cases: int, seed: int,
            ptr_args: str = "auto", random_regions: list[tuple[int, int]] | None = None) -> Report:
    m = Machine(code, header_base, mem_end, cand)
    if ptr_args == "auto":
        slots, _ = profile_pointer_args(m, orig, thumb, nargs, nfargs, seed)
    elif ptr_args == "none":
        slots = ()
    else:
        slots = tuple(int(x) for x in ptr_args.split(","))
    rep = Report(slots)
    rng = random.Random(seed + 1)
    for _ in range(cases):
        args, fargs, bufs = make_case(rng, nargs, slots, nfargs)
        regions = [(addr, rand_words(rng, size)) for addr, size in random_regions or []]
        a = m.run(orig, thumb, args, fargs, bufs, regions)
        if a.fault:
            rep.skipped += 1
            continue
        b = m.run(cand.entry, False, args, fargs, bufs, regions)
        rep.compared += 1
        problems = []
        if b.fault:
            problems.append(f"candidate faulted: {b.fault}")
        for r in RET_REGS[ret]:
            if a.regs[r] != b.regs[r]:
                problems.append(f"{r}: original 0x{a.regs[r]:x}, candidate 0x{b.regs[r]:x}")
        if a.writes != b.writes:
            diff = sorted(set(a.writes) ^ set(b.writes) | {k for k in set(a.writes) & set(b.writes)
                                                           if a.writes[k] != b.writes[k]})
            problems.append(f"memory differs at {len(diff)} byte(s), first 0x{diff[0]:08x} "
                            f"(original {a.writes.get(diff[0])}, candidate {b.writes.get(diff[0])})")
        if problems:
            if len(rep.mismatches) < 5:
                rep.mismatches.append({"args": [f"0x{x & 0xFFFFFFFF:08x}" for x in args],
                                       "fargs": fargs, "problems": problems})
        else:
            rep.passed += 1
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("address", type=lambda s: int(s, 0))
    ap.add_argument("source")
    ap.add_argument("--func", help="function name in the source (default: from symbols.txt)")
    ap.add_argument("--args", type=int, default=4, help="integer/pointer args in r0-r3")
    ap.add_argument("--fargs", type=int, default=0, help="float args in s0..")
    ap.add_argument("--ret", choices=sorted(RET_REGS), default="int")
    ap.add_argument("--ptr-args", default="auto")
    ap.add_argument("--thumb", action="store_true", help="original is Thumb code")
    ap.add_argument("--cases", type=int, default=500)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--exheader", default=os.path.join(ROOT, "baserom", "update_v130", "exheader.bin"))
    ap.add_argument("--code", default=os.path.join(ROOT, "baserom", "update_v130", "code.bin"))
    ap.add_argument("--symbols", default=os.path.join(ROOT, "config", "symbols.txt"))
    ap.add_argument("-I", dest="includes", action="append", default=[os.path.join(ROOT, "include")])
    ap.add_argument("--sym", action="append", default=[], metavar="NAME=0xADDR",
                    help="extra symbol for linking (e.g. a global not yet in symbols.txt)")
    ap.add_argument("--random-region", action="append", default=[], metavar="ADDR:SIZE",
                    help="fill this memory (e.g. a runtime-filled .bss table) with random data per case")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    with open(args.exheader, "rb") as f:
        header = exh.parse(f.read())
    with open(args.code, "rb") as f:
        code = codebin2elf.load_code(header, f.read())
    symbols = load_symbols(args.symbols)
    for s in args.sym:
        name, _, addr = s.partition("=")
        if not C_IDENT.match(name) or not addr:
            print(f"error: bad --sym {s!r} (expected NAME=0xADDR)", file=sys.stderr)
            return 2
        symbols[name] = int(addr, 0)
    func = args.func or next((n for n, a in symbols.items() if a == args.address), None)
    if not func:
        print("error: pass --func (no name for this address in symbols.txt)", file=sys.stderr)
        return 2
    # The original must not resolve to itself: drop its own name from the link map.
    symbols = {n: a for n, a in symbols.items() if n != func}
    try:
        cand = build_candidate(args.source, func, symbols, args.includes)
        rep = compare(code, header.text.address, header.bss_address + header.bss_size, args.address,
                      args.thumb, cand, args.args, args.fargs, args.ret, args.cases, args.seed,
                      args.ptr_args, [tuple(int(x, 0) for x in r.split(":")) for r in args.random_region])
    except DifftestError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"ok": rep.ok, "address": f"0x{args.address:08x}", "func": func,
                          "ptr_args": list(rep.ptr_slots), "compared": rep.compared,
                          "passed": rep.passed, "skipped": rep.skipped,
                          "mismatches": rep.mismatches}, indent=2))
    else:
        verdict = "PASS" if rep.ok else "FAIL"
        print(f"{verdict} {func} @ 0x{args.address:08x}: {rep.passed}/{rep.compared} cases match, "
              f"{rep.skipped} skipped (original faulted); pointer args: {list(rep.ptr_slots) or 'none'}")
        for mm in rep.mismatches:
            print(f"  args={mm['args']} fargs={mm['fargs']}")
            for p in mm["problems"]:
                print(f"    {p}")
        if rep.compared == 0:
            print("  no case could be compared: the input generator can't drive the original yet")
        elif rep.skipped > rep.compared:
            print("  warning: most cases skipped; coverage is weak")
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
