#!/usr/bin/env python3
"""Find where a constant lives in the game's code: literal-pool loads, MOV immediates, PICA headers.

usage: find_constants.py --value 0x43C80000          LDR/VLDR literal loads of this 32-bit word
       find_constants.py --float 400.0               the same, given as a float
       find_constants.py --mov 400 [--mov 240]       MOV/MVN rd,#imm sites (ARM)
       find_constants.py --mov-pair 400,240 [--gap 12]
                                                     two MOV immediates at most --gap bytes apart
       find_constants.py --pica-reg 0x41 [--pica-reg 0x68]
                                                     PICA200 register-write headers loaded from pools

Every hit names its containing function (config/functions.tsv, config/symbols.txt). For literal
loads it also says how the static recompiler treats the load (tools/recomp/arm.py, thumb.py):
LDR literals are folded into the generated C, so patching the pool word in guest memory does not
change recompiled code; VLDR literals are read from guest memory at run time, so a data write
to the pool word does take effect.
"""
from __future__ import annotations

import argparse
import bisect
import os
import struct
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@dataclass(frozen=True)
class Function:
    address: int
    size: int
    thumb: bool
    name: str


@dataclass(frozen=True)
class LiteralLoad:
    site: int          # address of the load instruction
    literal: int       # address of the pool word
    kind: str          # "ldr" (ARM), "vldr" (ARM VFP) or "ldr.t" (Thumb)

    @property
    def folded(self) -> bool:
        """True when the recompiler bakes the value into the generated C."""
        return self.kind != "vldr"


@dataclass(frozen=True)
class MovImm:
    site: int
    rd: int
    value: int         # 32-bit result (MVN already inverted)
    mnemonic: str      # "mov" or "mvn"


@dataclass(frozen=True)
class PicaHeader:
    reg: int
    mask: int
    extra: int         # extra parameter words
    sequential: bool


def u32(code: bytes, base: int, addr: int) -> int:
    return struct.unpack_from("<I", code, addr - base)[0]


def u16(code: bytes, base: int, addr: int) -> int:
    return struct.unpack_from("<H", code, addr - base)[0]


def arm_imm(w: int) -> int:
    """Value of an ARM data-processing rotated immediate (bits 0-11)."""
    imm8, rot = w & 0xFF, ((w >> 8) & 0xF) * 2
    return ((imm8 >> rot) | (imm8 << (32 - rot))) & 0xFFFFFFFF if rot else imm8


def float_word(f: float) -> int:
    return struct.unpack("<I", struct.pack("<f", f))[0]


def word_float(w: int) -> float:
    return struct.unpack("<f", struct.pack("<I", w))[0]


def decode_pica_header(w: int) -> PicaHeader | None:
    """Split a PICA200 command header (register id, byte mask, extra words, sequential bit).

    Returns None for words that cannot be a header: no mask bits, register id beyond the
    register file, or set bits in the unused 28-30 range."""
    reg, mask, extra = w & 0xFFFF, (w >> 16) & 0xF, (w >> 20) & 0xFF
    if mask == 0 or reg >= 0x300 or (w >> 28) & 0x7:
        return None
    return PicaHeader(reg, mask, extra, bool(w >> 31))


def _ranges(functions: list[Function] | None, start: int, end: int, thumb: bool):
    if functions is None:
        if not thumb:
            yield start, end
        return
    for f in functions:
        if f.thumb == thumb and f.size:
            lo, hi = max(f.address, start), min(f.address + f.size, end)
            if lo < hi:
                yield lo, hi


def iter_literal_loads(code: bytes, base: int, start: int, end: int,
                       functions: list[Function] | None = None):
    """Yield every PC-relative LDR/VLDR (ARM) and LDR (Thumb-16) with its pool address.

    With a function table, ARM words are decoded only inside ARM functions and halfwords only
    inside Thumb functions; without one, the whole range is decoded as ARM."""
    limit = base + len(code) - 4
    for lo, hi in _ranges(functions, start, end, False):
        for a in range(lo + (-lo & 3), hi - 3, 4):
            w = u32(code, base, a)
            if (w & 0x0F7F0000) == 0x051F0000 and (w >> 28) != 0xF:      # LDR rt,[pc,#+-imm12]
                off = w & 0xFFF
                lit = a + 8 + (off if w & (1 << 23) else -off)
                if base <= lit <= limit:
                    yield LiteralLoad(a, lit, "ldr")
            elif (w & 0x0F3F0E00) == 0x0D1F0A00 and (w >> 28) != 0xF:    # VLDR s/d,[pc,#+-imm8*4]
                off = (w & 0xFF) * 4
                lit = ((a + 8) & ~3) + (off if w & (1 << 23) else -off)
                if base <= lit <= limit:
                    yield LiteralLoad(a, lit, "vldr")
    for lo, hi in _ranges(functions, start, end, True):
        for a in range(lo + (lo & 1), hi - 1, 2):
            h = u16(code, base, a)
            if (h & 0xF800) == 0x4800:                                     # LDR rt,[pc,#imm8*4]
                lit = ((a + 4) & ~3) + (h & 0xFF) * 4
                if base <= lit <= limit:
                    yield LiteralLoad(a, lit, "ldr.t")


def literal_loads(code: bytes, base: int, start: int, end: int, value: int,
                  functions: list[Function] | None = None) -> list[LiteralLoad]:
    """Loads whose pool word equals value."""
    return [h for h in iter_literal_loads(code, base, start, end, functions)
            if u32(code, base, h.literal) == value]


def mov_immediates(code: bytes, base: int, start: int, end: int, values,
                   functions: list[Function] | None = None) -> list[MovImm]:
    """ARM MOV/MVN rd,#imm (any condition, with or without S) producing one of values."""
    want = {v & 0xFFFFFFFF for v in values}
    hits = []
    for lo, hi in _ranges(functions, start, end, False):
        for a in range(lo + (-lo & 3), hi - 3, 4):
            w = u32(code, base, a)
            # cond 001 opcode S Rn Rd imm12 with opcode 1101 (MOV) or 1111 (MVN) and Rn = 0
            if (w >> 28) == 0xF or (w & 0x0FAF0000) != 0x03A00000:
                continue
            mvn = bool(w & 0x00400000)
            v = arm_imm(w)
            v = (~v & 0xFFFFFFFF) if mvn else v
            if v in want:
                hits.append(MovImm(a, (w >> 12) & 0xF, v, "mvn" if mvn else "mov"))
    return hits


def mov_pairs(code: bytes, base: int, start: int, end: int, first: int, second: int,
              gap: int = 12, functions: list[Function] | None = None) -> list[tuple[MovImm, MovImm]]:
    """MOV immediates of first and second, in either order, at most gap bytes apart."""
    hits = mov_immediates(code, base, start, end, (first, second), functions)
    a_sites = [h for h in hits if h.value == first & 0xFFFFFFFF]
    b_sites = sorted((h for h in hits if h.value == second & 0xFFFFFFFF), key=lambda h: h.site)
    keys = [h.site for h in b_sites]
    pairs = []
    for a in a_sites:
        i = bisect.bisect_left(keys, a.site - gap)
        while i < len(b_sites) and b_sites[i].site <= a.site + gap:
            if b_sites[i].site != a.site:
                pairs.append((a, b_sites[i]))
            i += 1
    return sorted(pairs, key=lambda p: min(p[0].site, p[1].site))


def pica_loads(code: bytes, base: int, start: int, end: int, regs,
               functions: list[Function] | None = None) -> list[tuple[LiteralLoad, PicaHeader]]:
    """Literal loads of PICA command headers that write one of regs (ids, e.g. 0x41)."""
    want = set(regs)
    out = []
    for h in iter_literal_loads(code, base, start, end, functions):
        if h.kind == "vldr":
            continue
        hdr = decode_pica_header(u32(code, base, h.literal))
        if hdr and hdr.reg in want:
            out.append((h, hdr))
    return out


class Namer:
    """Containing function of an address, with names from symbols.txt where known."""

    def __init__(self, functions: list[Function]):
        self.functions = sorted(functions, key=lambda f: f.address)
        self.starts = [f.address for f in self.functions]

    def __call__(self, addr: int) -> str:
        i = bisect.bisect_right(self.starts, addr) - 1
        if i >= 0:
            f = self.functions[i]
            if addr < f.address + max(f.size, 4):
                return f"{f.name}+0x{addr - f.address:x}"
        return "?"


def load_functions(functions_path: str, symbols_path: str | None) -> list[Function]:
    import build_function_table as bft
    names = bft.load_symbols(symbols_path) if symbols_path and os.path.exists(symbols_path) else {}
    out = []
    for r in bft.read_tsv(functions_path):
        a = int(r["address"], 16)
        out.append(Function(a, int(r["size"], 16), r["mode"] == "thumb",
                            names.get(a) or r.get("name") or f"FUN_{a:08x}"))
    return out


def load_image(args) -> tuple[bytes, int, int]:
    """(code, base, end of .text)."""
    if args.image:
        with open(args.image, "rb") as f:
            code = f.read()
        return code, args.base, args.text_end or args.base + len(code)
    import codebin2elf
    import exheader as exh
    with open(args.exheader, "rb") as f:
        header = exh.parse(f.read())
    with open(args.code, "rb") as f:
        code = codebin2elf.load_code(header, f.read())
    return code, header.text.address, header.text.address + header.text.size


def _int(s: str) -> int:
    return int(s, 0)


def _mov_text(m: MovImm) -> str:
    signed = m.value - (1 << 32) if m.value & 0x80000000 else m.value
    return f"{m.mnemonic} -> r{m.rd} = {m.value:#x} ({signed})"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--value", type=_int, action="append", default=[])
    ap.add_argument("--float", type=float, action="append", default=[], dest="floats")
    ap.add_argument("--mov", type=_int, action="append", default=[])
    ap.add_argument("--mov-pair")
    ap.add_argument("--gap", type=_int, default=12)
    ap.add_argument("--pica-reg", type=_int, action="append", default=[])
    ap.add_argument("--image", help="decompressed code image (instead of --exheader/--code)")
    ap.add_argument("--base", type=_int, default=0x00100000)
    ap.add_argument("--text-end", type=_int)
    ap.add_argument("--exheader", default=os.path.join(ROOT, "baserom", "update_v130", "exheader.bin"))
    ap.add_argument("--code", default=os.path.join(ROOT, "baserom", "update_v130", "code.bin"))
    ap.add_argument("--functions", default=os.path.join(ROOT, "config", "functions.tsv"))
    ap.add_argument("--symbols", default=os.path.join(ROOT, "config", "symbols.txt"))
    args = ap.parse_args(argv)

    code, base, text_end = load_image(args)
    functions = load_functions(args.functions, args.symbols) if os.path.exists(args.functions) else None
    name = Namer(functions or [])

    for v in args.value + [float_word(f) for f in args.floats]:
        for h in literal_loads(code, base, base, text_end, v, functions):
            how = "folded at recompile" if h.folded else "read at run time"
            print(f"{h.site:#010x}\t{h.kind}\tpool {h.literal:#010x} = {v:#010x} "
                  f"({word_float(v):g}f)\t{how}\t{name(h.site)}")
    if args.mov:
        for m in mov_immediates(code, base, base, text_end, args.mov, functions):
            print(f"{m.site:#010x}\t{_mov_text(m)}\t{name(m.site)}")
    if args.mov_pair:
        a, b = (int(x, 0) for x in args.mov_pair.split(","))
        for x, y in mov_pairs(code, base, base, text_end, a, b, args.gap, functions):
            print(f"{x.site:#010x}\t{_mov_text(x)}\t{y.site:#010x}\t{_mov_text(y)}\t{name(x.site)}")
    if args.pica_reg:
        for h, hdr in pica_loads(code, base, base, text_end, args.pica_reg, functions):
            print(f"{h.site:#010x}\treg {hdr.reg:#05x} mask {hdr.mask:#x} +{hdr.extra} words"
                  f"{' seq' if hdr.sequential else ''}\t{name(h.site)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
