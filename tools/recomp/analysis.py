"""Function-level analysis: decode reachable code from each entry point, classify branch
targets (local label vs tail call), resolve jump tables, and grow the function set with
newly discovered call targets."""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

from .arm import ArmTranslator
from .insn import (AL, B, BL, ICALL, IJUMP, JTAB, OP, RET, SVC, TRAP, Insn, ror32)
from .thumb import ThumbTranslator

MOV_LR_PC = 0xE1A0E00F
VEC_BITS = 0x00370000            # FPSCR LEN + STRIDE


def is_vmsr_fpscr(ins: Insn) -> bool:
    return ins.mode == "arm" and ins.kind == OP and (ins.word & 0x0FFF0FFF) == 0x0EE10A10
MAX_INSNS_PER_FUNCTION = 200_000
MAX_JUMP_TABLE = 4096


class Image:
    """The program image: `code` holds text|ro|data contiguously from `base`."""

    def __init__(self, code: bytes, base: int, text_size: int):
        self.code, self.base, self.text_size = code, base, text_size
        self.text_end = base + text_size

    def read32(self, a: int) -> int | None:
        off = a - self.base
        if 0 <= off <= len(self.code) - 4 and not a & 3:
            return struct.unpack_from("<I", self.code, off)[0]
        return None

    def read16(self, a: int) -> int | None:
        off = a - self.base
        if 0 <= off <= len(self.code) - 2 and not a & 1:
            return struct.unpack_from("<H", self.code, off)[0]
        return None

    def in_text(self, a: int) -> bool:
        return self.base <= a < self.text_end


@dataclass
class Function:
    entry: int
    mode: str
    insns: dict[int, Insn] = field(default_factory=dict)
    labels: set[int] = field(default_factory=set)
    tails: set[int] = field(default_factory=set)          # B insns that are tail calls
    link_calls: dict[int, int] = field(default_factory=dict)  # B/IJUMP that are calls -> return addr
    ijump_targets: dict[int, list[int]] = field(default_factory=dict)  # local computed-jump targets
    fall_tail: dict[int, int] = field(default_factory=dict)
    jtabs: dict[int, list[int]] = field(default_factory=dict)
    calls: set[tuple[int, str]] = field(default_factory=set)
    problems: list[str] = field(default_factory=list)
    vector_insns: int = 0                                  # instructions run in short-vector mode
    mret: bool = False                                     # returns into its caller's continuations
    mret_calls: dict[int, list[int]] = field(default_factory=dict)  # BL -> continuation addresses


class Analyzer:
    def __init__(self, img: Image):
        self.img = img
        self.arm = ArmTranslator(img.read32)
        self.thumb = ThumbTranslator(img.read16, img.read32)
        self._cache: dict[tuple[int, str, int], Insn] = {}

    def decode(self, addr: int, mode: str, vec: int = 0) -> Insn:
        key = (addr, mode, vec)
        ins = self._cache.get(key)
        if ins is None:
            if mode == "arm":
                w = self.img.read32(addr)
                if w is None or not self.img.in_text(addr):
                    ins = Insn(addr, 4, 0, "arm", kind=TRAP, imm=2, trap_reason="outside text")
                else:
                    ins = self.arm.decode(addr, w, vec)
            else:
                if not self.img.in_text(addr):
                    ins = Insn(addr, 2, 0, "thumb", kind=TRAP, imm=2, trap_reason="outside text")
                else:
                    ins = self.thumb.decode(addr)
            self._cache[key] = ins
        return ins

    # ── jump tables ──────────────────────────────────────────────────────
    def _cmp_bound(self, ins: Insn, start: int | None = None) -> int | None:
        """Number of table entries, from the guarding unsigned `cmp rIdx, #imm`.

        Accepts the index check either as the jump's own condition (ADDLS/LDRLS/ADDCC) or
        as earlier instructions conditional on HI/CS that leave the function or jump to
        the default case (BHI default, BXHI LR, ...). Instructions under that guard
        condition may write any register; others must not touch the index."""
        idx = ins.jt_index
        guard = None
        guard_exit = False
        writes_idx = re.compile(rf"\bR\({idx}\) =")
        origin = ins.addr if start is None else start
        for k in range(1, 97):
            a = origin - 4 * k
            w = self.img.read32(a)
            if w is None:
                return None
            cond = w >> 28
            if (w & 0x0FF0F000) == 0x03500000 and (w >> 16) & 0xF == idx and cond == 0xE:
                imm = ror32(w & 0xFF, ((w >> 8) & 0xF) * 2)
                c = ins.cond if ins.cond != AL else (guard if guard_exit else None)
                if c in (9, 8):          # LS jump, or HI exit: idx <= imm
                    return imm + 1
                if c in (3, 2):          # CC jump, or CS exit: idx < imm
                    return imm
                return None
            if cond in (2, 8) and ins.cond == AL:
                if guard not in (None, cond):
                    return None
                guard = cond
                d = self.arm.decode(a, w)
                if d.kind in (B, RET, IJUMP):
                    guard_exit = True
                continue
            if cond != 0xE:
                return None
            d = self.arm.decode(a, w)
            if d.kind != OP or any(writes_idx.search(line) for line in d.body):
                return None
        return None

    def _adr_base(self, ins: Insn) -> int | None:
        """For `ldr pc, [rB, rI, lsl #2]`: find `adr rB, table` shortly before."""
        rb = ins.jt_base
        for k in range(1, 9):
            a = ins.addr - 4 * k
            w = self.img.read32(a)
            if w is None or w >> 28 != 0xE:
                return None
            if (w & 0x0FFF0000) == (0x028F0000) and (w >> 12) & 0xF == rb:
                return (a + 8 + ror32(w & 0xFF, ((w >> 8) & 0xF) * 2)) & 0xFFFFFFFF
            if (w >> 12) & 0xF == rb:
                return None
        return None

    def _subs_bound(self, ins: Insn, f: "Function") -> int | None:
        """Table jump reached by `and rX, rY, #2^k-1` / `subs rIdx, rX, #c` / `bhs table`:
        the index is below 2^k - c (RVCT fplib special-case dispatch)."""
        idx = ins.jt_index
        for pred in f.insns.values():
            if pred.kind != B or pred.cond != 2 or pred.target != ins.addr or pred.addr in f.tails:
                continue
            w = self.img.read32(pred.addr - 4)
            if w is None or w >> 28 != 0xE or (w & 0x0FF00000) != 0x02500000 or (w >> 12) & 0xF != idx:
                continue
            rx, c = (w >> 16) & 0xF, ror32(w & 0xFF, ((w >> 8) & 0xF) * 2)
            v = self.img.read32(pred.addr - 8)
            if v is None or v >> 28 != 0xE or (v & 0x0FF00000) != 0x02000000 or (v >> 12) & 0xF != rx:
                continue
            mask = ror32(v & 0xFF, ((v >> 8) & 0xF) * 2)
            if mask & (mask + 1) == 0 and mask >= c:
                return mask - c + 1
        return None

    def jump_table(self, ins: Insn, f: "Function | None" = None) -> list[int] | None:
        n = self._cmp_bound(ins)
        if n is None and f is not None:
            # reached through `b table_jump` (the compare sits before the branch)
            for pred in f.insns.values():
                if pred.kind == B and pred.cond == AL and pred.target == ins.addr and pred.addr not in f.tails:
                    n = self._cmp_bound(ins, pred.addr)
                    if n is not None:
                        break
        if n is None and f is not None:
            n = self._subs_bound(ins, f)
        if n is None or n <= 0 or n > MAX_JUMP_TABLE:
            return None
        if ins.jt_kind == "addpc":
            return [ins.jt_base + 4 * i for i in range(n)]
        out = []
        for i in range(n):
            t = self.img.read32(ins.jt_base + 4 * i)
            if t is None or t & 3 or not self.img.in_text(t):
                return None
            out.append(t)
        return out

    # ── one function ─────────────────────────────────────────────────────
    def analyze(self, entry: int, mode: str, entries: dict[int, str]) -> Function:
        """Decode everything reachable from `entry`. The FPSCR vector length/stride is
        tracked along the way (functions are entered with LEN=STRIDE=0, as the procedure
        call standard requires) so VFP instructions in short-vector code are translated
        with the right element count."""
        f = Function(entry, mode, mret=self.inline_arg_callee(entry, mode))
        work: list[tuple[int, int]] = [(entry, 0)]
        vec_at: dict[int, int] = {}
        count = 0
        while work:
            a, vec = work.pop()
            while a not in f.insns:
                count += 1
                if count > MAX_INSNS_PER_FUNCTION:
                    f.problems.append("instruction limit reached")
                    return f
                ins = self.decode(a, mode, vec)
                f.insns[a] = ins
                vec_at[a] = vec
                succ: list[int] = []
                cont = self._step(f, ins, entries, succ)
                work.extend((t, vec) for t in succ)
                if vec and (ins.kind in (BL, ICALL, RET) or a in f.tails or a in f.link_calls):
                    f.problems.append(f"call or return in VFP short-vector mode at {a:#x}")
                if is_vmsr_fpscr(ins):
                    new = self._vmsr_vec(ins, vec)
                    if new is None or (ins.conditional and new != vec):
                        f.problems.append(f"VMSR with unknown vector length at {a:#x}")
                        new = 0
                    vec = new
                if not cont:
                    break
                nxt = a + ins.size
                if nxt != entry and entries.get(nxt) not in (None, mode):
                    break       # code of the other instruction set follows: the call before it doesn't return
                if nxt != entry and entries.get(nxt) == mode:
                    if vec:
                        f.problems.append(f"falls into another function in short-vector mode at {a:#x}")
                    f.fall_tail[a] = nxt
                    f.calls.add((nxt, mode))
                    break
                a = nxt
            else:
                if vec_at[a] != vec:
                    f.problems.append(f"conflicting VFP vector length at {a:#x}")
        f.vector_insns = sum(1 for a, v in vec_at.items() if v)
        return f

    def _vmsr_vec(self, ins: Insn, current: int) -> int | None:
        """FPSCR LEN/STRIDE written by `vmsr fpscr, rT`, from the straight-line code that
        computes rT: `mov rT, #imm`, `bic rT, rT, #0x370000` + `orr rT, rT, #imm`, or the
        restore of a value saved on the stack at entry (`pop {rT}`), which is LEN=0."""
        rt = (ins.word >> 12) & 0xF
        orr = 0
        writes = re.compile(rf"\bR\({rt}\) =")
        for k in range(1, 13):
            a = ins.addr - 4 * k
            w = self.img.read32(a)
            if w is None or w >> 28 != 0xE:
                return None
            rd, rn = (w >> 12) & 0xF, (w >> 16) & 0xF
            imm = ror32(w & 0xFF, ((w >> 8) & 0xF) * 2)
            if rd == rt and (w & 0x0FEF0000) == 0x03A00000:                 # mov rT, #imm
                return (imm | orr) & VEC_BITS
            if rd == rt and rn == rt and (w & 0x0FE00000) == 0x03800000:    # orr rT, rT, #imm
                orr |= imm
                continue
            if rd == rt and rn == rt and (w & 0x0FE00000) == 0x03C00000:    # bic rT, rT, #imm
                return orr & VEC_BITS if imm & VEC_BITS == VEC_BITS else None
            if rd == rt and (w & 0x0FFF0FFF) == 0x049D0004:                 # pop {rT}
                return 0 if not orr and current else None
            d = self.arm.decode(a, w)
            if d.kind != OP or any(writes.search(line) for line in d.body):
                return None
        return None

    def inline_arg_callee(self, addr: int, mode: str) -> bool:
        """Functions entered with `ldr rX, [lr], #4` read a descriptor word placed right
        after the BL that calls them, then continue in the caller at BL+8+4*k (k < 4, with
        the caller's saved registers popped) or return straight to the caller's caller.
        RVCT's floating-point library uses this for its special-case handlers."""
        w = self.img.read32(addr) if mode == "arm" else None
        return w is not None and (w & 0xFFFF0FFF) == 0xE49E0004

    def _link_return(self, f: Function, ins: Insn) -> int | None:
        """If LR was just set to a constant (`mov lr, pc`, `add/sub lr, pc, #imm`, possibly
        adjusted by `add/sub lr, lr, #imm` for a return point beyond one immediate's reach)
        in the same straight-line code, this jump is a call that returns to that constant. A
        conditional jump is then a conditional call (hand-written code sets LR once and
        picks the routine with `beq a` / `bne b`, or a branch then a table jump)."""
        if ins.mode != "arm":
            return None
        adjust = 0
        for k in range(1, 9):
            a = ins.addr - 4 * k
            if a + 4 in f.labels and k > 1:
                return None             # another path joins here
            w = self.img.read32(a)
            if w is None:
                return None
            d = self.decode(a, "arm")
            if d.kind in (BL, ICALL, RET, JTAB, TRAP, SVC) or (d.kind in (B, IJUMP) and not d.conditional):
                return None
            if w == MOV_LR_PC:
                return (a + 8 + adjust) & 0xFFFFFFFF
            if (w & 0x0FEFF000) in (0x028FE000, 0x024FE000, 0x028EE000, 0x024EE000):  # add/sub lr, pc|lr, #imm
                # conditional only right before a jump on the same condition (`addeq lr, pc, #n;
                # beq routine`): LR is set exactly when the call happens
                if w >> 28 != 0xE and not (k == 1 and adjust == 0 and ins.conditional and w >> 28 == ins.cond):
                    return None
                imm = ror32(w & 0xFF, ((w >> 8) & 0xF) * 2)
                imm = imm if (w & 0x00E00000) == 0x00800000 else -imm
                if (w >> 16) & 0xF == 14:
                    adjust += imm
                    continue
                return (a + 8 + imm + adjust) & 0xFFFFFFFF
            if any(re.search(r"\bR\(14\) =", line) for line in d.body):
                return None
        return None

    def _const_target(self, ins: Insn) -> int | None:
        """`add rX, pc, #a` (`sub`), optionally `add rX, rX, #b` (`sub`), then `mov pc, rX`
        or `bx rX`: the jump goes to a constant address (hand-written code reaching a
        routine too far away for one immediate). Only when nothing in between writes rX."""
        w = ins.word
        if ins.mode != "arm":
            return None
        if (w & 0x0FFFFFF0) == 0x01A0F000 or (w & 0x0FFFFFF0) == 0x012FFF10:   # mov pc, rX / bx rX
            reg = w & 0xF
        else:
            return None
        add = 0
        for k in range(1, 7):
            a = ins.addr - 4 * k
            v = self.img.read32(a)
            if v is None:
                return None
            if v >> 28 == 0xE and (v >> 12) & 0xF == reg and (v & 0x0FE00000) in (0x02800000, 0x02400000):
                imm = ror32(v & 0xFF, ((v >> 8) & 0xF) * 2)
                sign = 1 if (v & 0x00E00000) == 0x00800000 else -1
                rn = (v >> 16) & 0xF
                if rn == 15:
                    return (a + 8 + sign * imm + add) & 0xFFFFFFFF
                if rn == reg:
                    add += sign * imm
                    continue
                return None
            d = self.decode(a, "arm")
            if d.kind != OP or any(re.search(rf"\bR\({reg}\) =", line) for line in d.body):
                return None
        return None

    def _offset_table(self, ins: Insn) -> tuple[int, list[int]] | None:
        """`ldr rX, [rB, rI, lsl #2]` (or `[rB, rI]`, the index already scaled) + `add pc,
        rX, rB` with rB = adr table: return (base, targets) for the entries that look like
        code. Offsets are signed: hand-written code (the game's video decoder) also puts a
        table right after the routines it jumps back to. Instructions in between may be
        conditional (they pick the index) but must not touch the table registers."""
        w = ins.word
        if ins.mode != "arm" or (w & 0x0FE0FFF0) != 0x0080F000:   # ADD pc, Rn, Rm (no shift)
            return None
        rn, rm = (w >> 16) & 0xF, w & 0xF
        base_reg = loaded = None
        for k in range(1, 13):
            a = ins.addr - 4 * k
            v = self.img.read32(a)
            if v is None:
                return None
            if loaded is None and v >> 28 == 0xE and (v & 0x0FF00FF0) in (0x07900100, 0x07900000) \
                    and (v >> 12) & 0xF in (rn, rm):
                loaded = (v >> 12) & 0xF
                base_reg = (v >> 16) & 0xF
                if {loaded, base_reg} != {rn, rm}:
                    return None
                continue
            if base_reg is not None and v >> 28 == 0xE and (v >> 12) & 0xF == base_reg \
                    and (v & 0x0FEF0000) in (0x028F0000, 0x024F0000):
                imm = ror32(v & 0xFF, ((v >> 8) & 0xF) * 2)
                base = (a + 8 + imm if (v & 0x00F00000) == 0x00800000 else a + 8 - imm) & 0xFFFFFFFF
                targets = []
                for i in range(256):
                    off = self.img.read32(base + 4 * i)
                    if off is None:
                        break
                    off -= (off & 0x80000000) << 1
                    if not -0x100000 < off < 0x100000:
                        break
                    if 0 <= off <= 4 * i:
                        continue        # an unused slot (0) pointing at the table itself
                    t = (base + off) & 0xFFFFFFFF
                    if not self.plausible_entry(t, "arm"):
                        break
                    targets.append(t)
                return (base, targets) if targets else None
            d = self.decode(a, "arm")
            guarded = (rn, rm) if loaded is None else (base_reg,)
            if d.kind != OP or any(re.search(rf"\bR\({r}\) =", line) for r in guarded for line in d.body):
                return None
        return None

    def _classify(self, f: Function, target: int, tmode: str, entries: dict[int, str]) -> str:
        if tmode != f.mode:
            return "tail"
        if target == f.entry:
            return "goto"
        if entries.get(target) == tmode:
            return "tail"
        if not self.img.in_text(target) or (tmode == "arm" and target & 3) or target & 1:
            return "bad"
        return "goto"

    def _step(self, f: Function, ins: Insn, entries: dict[int, str], work: list[int]) -> bool:
        k = ins.kind
        if k in (OP, SVC):
            return True
        if k == BL:
            f.calls.add((ins.target, ins.target_mode))
            if not ins.conditional and self.inline_arg_callee(ins.target, ins.target_mode):
                desc = self.img.read32(ins.addr + 4) or 0
                codes = {(desc >> (3 * i)) & 7 for i in range(9)}
                conts = sorted(ins.addr + 8 + 4 * c for c in codes if c < 4)
                f.mret_calls[ins.addr] = conts
                for t in conts:
                    f.labels.add(t)
                    work.append(t)
                return False
            return True
        if k == ICALL:
            return True
        if k == B:
            ret = self._link_return(f, ins)
            if ret is not None:
                f.link_calls[ins.addr] = ret
                f.calls.add((ins.target, ins.target_mode))
                if ret != ins.next:
                    f.labels.add(ret)
                    work.append(ret)
                    return ins.conditional
                return True
            cls = self._classify(f, ins.target, ins.target_mode, entries)
            if cls == "goto":
                f.labels.add(ins.target)
                work.append(ins.target)
            elif cls == "tail":
                f.tails.add(ins.addr)
                f.calls.add((ins.target, ins.target_mode))
            else:
                f.problems.append(f"branch to bad target {ins.target:#x} at {ins.addr:#x}")
                ins.kind, ins.imm, ins.trap_reason = TRAP, 4, "bad branch target"
                return False
            return ins.conditional
        if k == RET:
            return ins.conditional
        if k == IJUMP:
            ret = self._link_return(f, ins)
            table = self._offset_table(ins)
            if ret is not None:
                f.link_calls[ins.addr] = ret
                if table:
                    for t in table[1]:
                        f.calls.add((t, "arm"))
                if ret != ins.next:
                    f.labels.add(ret)
                    work.append(ret)
                    return ins.conditional
                return True
            if table and not ins.conditional:
                f.ijump_targets[ins.addr] = table[1]
                for t in table[1]:
                    f.labels.add(t)
                    work.append(t)
                return False
            if ins.jt_kind == "ldrreg" and not ins.conditional:
                base = self._adr_base(ins)
                if base is not None:
                    ins.kind, ins.jt_kind, ins.jt_base, ins.body, ins.pcexpr = JTAB, "ldrpc", base, [], None
                    return self._step(f, ins, entries, work)
            t = self._const_target(ins)
            if t is not None and self.plausible_entry(t, "arm"):
                f.calls.add((t, "arm"))     # still dispatched at run time; now recompiled too
            return ins.conditional
        if k == JTAB:
            targets = self.jump_table(ins, f)
            if targets is None:
                f.problems.append(f"unresolved jump table at {ins.addr:#x}")
                ins.kind, ins.imm, ins.trap_reason = TRAP, 4, "unresolved jump table"
                return False
            f.jtabs[ins.addr] = targets
            for t in targets:
                f.labels.add(t)
                work.append(t)
            return ins.conditional
        return False                      # TRAP

    # ── whole program ────────────────────────────────────────────────────
    def plausible_entry(self, addr: int, mode: str) -> bool:
        if not self.img.in_text(addr) or (mode == "arm" and addr & 3) or addr & 1:
            return False
        return self.decode(addr, mode).kind != TRAP

    def analyze_all(self, entries: dict[int, str], progress=None) -> dict[int, Function]:
        entries = dict(entries)
        funcs: dict[int, Function] = {}
        queue = sorted(entries)
        discovered = 0
        while queue:
            a = queue.pop()
            if a in funcs:
                continue
            f = self.analyze(a, entries[a], entries)
            funcs[a] = f
            for t, m in f.calls:
                if t not in entries and self.plausible_entry(t, m):
                    entries[t] = m
                    queue.append(t)
                    discovered += 1
            if progress and len(funcs) % 2000 == 0:
                progress(len(funcs), len(entries))
        self.discovered = discovered
        return funcs
