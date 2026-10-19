#!/usr/bin/env python3
"""Recover MT Framework class info (DTI: name, parent, size, id) from an ARM code.bin.

MT Framework registers every class at startup with a constructor call like

    MtDTI::MtDTI(MtDTI *this, const char *name, MtDTI *parent, u32 size,
                 u32 arg4, u32 flags, u32 id)      // last three on the stack

This tool finds that constructor automatically (the most common call target right
after code takes the address of a class-name string), then:
  1. emulates every function that calls it in Unicorn, recording the real arguments
     and reading back the id the constructor stores (DTI + 0x1C);
  2. falls back to a static register scan for call sites the emulation misses.

usage: mt_dti.py --exheader exheader.bin --code code.bin -o config/mt_classes.tsv
"""
from __future__ import annotations

import argparse
import collections
import os
import re
import struct
import sys
import zlib
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import codebin2elf  # noqa: E402
import exheader as exh  # noqa: E402

CLASS_NAME_RE = re.compile(rb"[A-Za-z][A-Za-z0-9_]*(::[A-Za-z][A-Za-z0-9_]*)*")
ID_OFFSET = 0x1C


def mt_hash(name: str) -> int:
    """Class id MT Framework derives from a name when none is given: CRC-32 without the
    final inversion, truncated to 31 bits. Matches 989/992 classes in MH4U v1.3.0; the
    rest pass an explicit id. Archive (.arc) entries identify resource types this way."""
    return ~zlib.crc32(name.encode()) & 0x7FFFFFFF


@dataclass
class DTI:
    address: int
    name: str
    parent: int | None
    size: int | None
    stack: list[int] = field(default_factory=list)
    id: int | None = None
    call: int | None = None
    source: str = ""


class Image:
    def __init__(self, code: bytes, base: int, text_size: int, mem_end: int):
        self.code, self.base, self.text_size, self.mem_end = code, base, text_size, mem_end

    def u32(self, addr: int) -> int:
        return struct.unpack_from("<I", self.code, addr - self.base)[0]

    def cstr(self, addr: int | None, limit: int = 128) -> str | None:
        if addr is None:
            return None
        off = addr - self.base
        if not 0 <= off < len(self.code):
            return None
        end = self.code.find(b"\0", off, off + limit)
        raw = self.code[off:end] if end > off else b""
        return raw.decode() if raw and CLASS_NAME_RE.fullmatch(raw) else None

    def arm_words(self):
        for off in range(0, self.text_size - 3, 4):
            yield self.base + off, struct.unpack_from("<I", self.code, off)[0]


# ── ARM decoding helpers (only what this tool needs) ─────────────────────────

def _rot_imm(w: int) -> int:
    rot, imm = ((w >> 8) & 0xF) * 2, w & 0xFF
    return ((imm >> rot) | (imm << (32 - rot))) & 0xFFFFFFFF if rot else imm


def adr_target(addr: int, w: int) -> int | None:
    """ADD/SUB Rd, PC, #imm (unconditional) -> address it computes."""
    if w >> 28 != 0xE:
        return None
    if (w & 0x0FFF0000) == 0x028F0000:
        return (addr + 8 + _rot_imm(w)) & 0xFFFFFFFF
    if (w & 0x0FFF0000) == 0x024F0000:
        return (addr + 8 - _rot_imm(w)) & 0xFFFFFFFF
    return None


def bl_target(addr: int, w: int) -> int | None:
    if w >> 28 != 0xE or (w & 0x0F000000) != 0x0B000000:
        return None
    imm = w & 0xFFFFFF
    if imm & 0x800000:
        imm -= 1 << 24
    return (addr + 8 + imm * 4) & 0xFFFFFFFF


def is_function_prologue(w: int) -> bool:
    return (w & 0xFFFF4000) == 0xE92D4000 or w == 0xE52DE004   # push {...,lr} / str lr,[sp,#-4]!


def find_function_start(img: Image, addr: int, max_back: int = 0x4000) -> int | None:
    for a in range(addr - 4, max(img.base, addr - max_back) - 1, -4):
        if is_function_prologue(img.u32(a)):
            return a
    return None


# ── Step 0: find the constructor ─────────────────────────────────────────────

def detect_ctor(img: Image, window: int = 12) -> int:
    votes: collections.Counter[int] = collections.Counter()
    for addr, w in img.arm_words():
        t = adr_target(addr, w)
        if t is None or (w >> 12) & 0xF != 1 or img.cstr(t) is None:   # name goes in r1
            continue
        for a in range(addr + 4, min(addr + 4 * window, img.base + img.text_size), 4):
            bt = bl_target(a, img.u32(a))
            if bt is not None:
                votes[bt] += 1
                break
    if not votes:
        raise ValueError("no DTI constructor candidate found")
    return votes.most_common(1)[0][0]


def find_callers(img: Image, target: int) -> list[int]:
    return [a for a, w in img.arm_words() if bl_target(a, w) == target]


# ── Step 1: emulation ────────────────────────────────────────────────────────

def emulate(img: Image, ctor: int, starts: list[int], max_insns: int = 300_000) -> dict[int, DTI]:
    from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_ARM, Uc, UcError
    from unicorn.arm_const import (UC_ARM_REG_C1_C0_2, UC_ARM_REG_FPEXC, UC_ARM_REG_LR,
                                   UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                   UC_ARM_REG_R3, UC_ARM_REG_SP)

    page = 0x1000
    mu = Uc(UC_ARCH_ARM, UC_MODE_ARM)
    mem_end = (img.mem_end + page - 1) & ~(page - 1)
    mu.mem_map(img.base, mem_end - img.base)
    mu.mem_write(img.base, img.code)
    stack_top, ret, scratch = 0x0F000000, 0x0E000000, 0x08000000
    mu.mem_map(stack_top - 0x100000, 0x100000)
    mu.mem_map(ret, page)
    mu.mem_map(scratch, 0x02000000)            # somewhere for stray pointers to land
    mu.reg_write(UC_ARM_REG_C1_C0_2, mu.reg_read(UC_ARM_REG_C1_C0_2) | (0xF << 20))
    mu.reg_write(UC_ARM_REG_FPEXC, 0x40000000)  # enable VFP

    found: dict[int, DTI] = {}

    def read_str(addr: int) -> str | None:
        try:
            raw = bytes(mu.mem_read(addr, 128)).split(b"\0")[0]
        except UcError:
            return None
        return raw.decode() if raw and CLASS_NAME_RE.fullmatch(raw) else None

    def hook(uc, _addr, _size, _data):
        r0, r1, r2, r3 = (uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1,
                                                   UC_ARM_REG_R2, UC_ARM_REG_R3))
        name = read_str(r1)
        if name:
            sp = uc.reg_read(UC_ARM_REG_SP)
            stack = list(struct.unpack("<3I", bytes(uc.mem_read(sp, 12))))
            found[r0] = DTI(r0, name, r2 or None, r3, stack, call=uc.reg_read(UC_ARM_REG_LR) - 4,
                            source="emu")

    mu.hook_add(UC_HOOK_CODE, hook, begin=ctor, end=ctor)
    for start in starts:
        for reg in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3):
            mu.reg_write(reg, scratch)
        mu.reg_write(UC_ARM_REG_SP, stack_top - 0x1000)
        mu.reg_write(UC_ARM_REG_LR, ret)
        try:
            mu.emu_start(start, ret, count=max_insns)
        except UcError:
            pass                                 # keep whatever was captured before the fault
    for d in found.values():                     # the real ctor ran: read back the stored id
        try:
            d.id = struct.unpack("<I", bytes(mu.mem_read(d.address + ID_OFFSET, 4)))[0]
        except UcError:
            pass
    return found


# ── Step 2: static fallback ──────────────────────────────────────────────────

def static_resolve(img: Image, call: int, window: int = 16) -> DTI | None:
    """Track r0-r3 through simple constant-producing instructions before the call."""
    regs: dict[int, int] = {}
    for a in range(call - 4 * window, call, 4):
        w = img.u32(a)
        if w >> 28 != 0xE:
            regs.clear()                         # conditional code: give up on precision
            continue
        rd = (w >> 12) & 0xF
        t = adr_target(a, w)
        if t is not None:
            regs[rd] = t
        elif (w & 0x0F7F0000) == 0x051F0000:     # LDR Rd, [PC, #+/-imm12]
            imm = w & 0xFFF
            regs[rd] = img.u32(a + 8 + (imm if w & 0x00800000 else -imm))
        elif (w & 0x0FEF0000) == 0x03A00000:     # MOV Rd, #imm
            regs[rd] = _rot_imm(w)
        elif (w & 0x0FEF0000) == 0x03E00000:     # MVN Rd, #imm
            regs[rd] = ~_rot_imm(w) & 0xFFFFFFFF
        elif bl_target(a, w) is not None or (w & 0x0E000000) == 0x0A000000:
            regs.clear()                         # branch / call clobbers r0-r3
        elif (w & 0x0E100000) in (0x04000000, 0x08000000) or (w & 0x0E1000F0) == 0x000000F0:
            pass                                 # STR / STM / STRD: don't write registers
        else:
            regs.pop(rd, None)                   # anything else writing Rd: unknown now
    name = img.cstr(regs.get(1))
    if 0 not in regs or name is None:
        return None
    return DTI(regs[0], name, regs.get(2) or None, regs.get(3), call=call, source="static")


# ── Putting it together ──────────────────────────────────────────────────────

def recover(img: Image, ctor: int | None = None) -> tuple[int, dict[int, DTI], int]:
    ctor = ctor if ctor is not None else detect_ctor(img)
    calls = find_callers(img, ctor)
    starts = sorted({s for s in (find_function_start(img, c) for c in calls) if s is not None})
    classes = emulate(img, ctor, starts)
    covered = {d.call for d in classes.values()}
    for c in calls:
        if c not in covered:
            d = static_resolve(img, c)
            if d is not None and d.address not in classes:
                classes[d.address] = d
    return ctor, classes, len(calls)


def write_tsv(classes: dict[int, DTI], path: str) -> None:
    def h(v):
        return "" if v is None else f"0x{v:08x}"
    by_addr = classes
    with open(path, "w", encoding="utf-8") as f:
        f.write("# MT Framework classes recovered by tools/mt_dti.py\n")
        f.write("dti\tname\tparent\tparent_name\tsize\tid\tsource\n")
        for d in sorted(classes.values(), key=lambda d: d.name.lower()):
            pname = by_addr[d.parent].name if d.parent in by_addr else ""
            f.write(f"{h(d.address)}\t{d.name}\t{h(d.parent)}\t{pname}\t"
                    f"{'' if d.size is None else hex(d.size)}\t{h(d.id)}\t{d.source}\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--exheader", required=True)
    ap.add_argument("--code", required=True)
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--ctor", type=lambda s: int(s, 0), help="skip auto-detection")
    args = ap.parse_args(argv)

    with open(args.exheader, "rb") as f:
        header = exh.parse(f.read())
    with open(args.code, "rb") as f:
        code = codebin2elf.load_code(header, f.read())
    img = Image(code, header.text.address, header.text.size, header.bss_address + header.bss_size)

    ctor, classes, ncalls = recover(img, args.ctor)
    write_tsv(classes, args.output)
    roots = [d for d in classes.values() if d.parent is None]
    orphans = collections.Counter(d.parent for d in classes.values()
                                  if d.parent is not None and d.parent not in classes)
    print(f"DTI constructor: 0x{ctor:08x}  ({ncalls} call sites)")
    print(f"classes: {len(classes)}  (emulated {sum(d.source == 'emu' for d in classes.values())}, "
          f"static {sum(d.source == 'static' for d in classes.values())})")
    print(f"roots: {', '.join(sorted(d.name for d in roots)[:10])}")
    print(f"parents not recovered: {len(orphans)} (covering {sum(orphans.values())} classes)")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
