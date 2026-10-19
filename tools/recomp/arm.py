"""ARM-state decoder/translator: ARMv6K + VFPv2 encodings -> C statements.

Every instruction becomes an `Insn` whose `body` is a list of C statements operating on
the RCtx in recomp.h. Control flow (branches, calls, returns, jump tables) is described
by the Insn kind and turned into C by recomp.emit once function analysis has decided
what each target is. Encodings we don't translate, or that are UNPREDICTABLE, become
TRAP instructions; compilers don't emit those.
"""
from __future__ import annotations

from typing import Callable

from .insn import (AL, B, BL, ICALL, IJUMP, JTAB, OP, RET, SVC, TRAP, Insn, Unpredictable,
                   Unsupported, hexc, ror32, sext)

TRAP_UNDEF, TRAP_UNSUPPORTED, TRAP_BKPT = 1, 2, 3
SHIFT_NAMES = ("lsl", "lsr", "asr", "ror")


def popcount(x: int) -> int:
    return bin(x).count("1")


class ArmTranslator:
    """Decode one ARM word at a time. `read32(addr)` returns a word of the image or None
    (used to fold PC-relative literal loads into constants)."""

    def __init__(self, read32: Callable[[int], int | None]):
        self.read32 = read32
        self._vec = 0

    # ── entry point ──────────────────────────────────────────────────────
    def decode(self, addr: int, word: int, vec: int = 0) -> Insn:
        """`vec` is FPSCR & 0x370000 (LEN and STRIDE) in effect at this instruction; it
        only changes VFP data-processing instructions (short-vector mode)."""
        ins = Insn(addr, 4, word, "arm")
        self._vec = vec
        try:
            self._decode(ins)
        except Unpredictable as e:
            self._trap(ins, TRAP_UNSUPPORTED, f"unpredictable: {e}")
        except Unsupported as e:
            self._trap(ins, TRAP_UNSUPPORTED, f"unsupported: {e}")
        return ins

    @staticmethod
    def _trap(ins: Insn, code: int, reason: str) -> None:
        ins.kind, ins.imm, ins.trap_reason, ins.body = TRAP, code, reason, []
        ins.pcexpr = None

    def _decode(self, ins: Insn) -> None:
        w = ins.word
        cond = w >> 28
        if cond == 0xF:
            return self._unconditional(ins)
        ins.cond = cond
        op = (w >> 25) & 7
        if op == 0:
            if (w & 0x90) == 0x90:
                if (w >> 5) & 3 == 0:
                    return self._multiply_swap_excl(ins)
                return self._ldst_extra(ins)
            if (w & 0x01900000) == 0x01000000:
                return self._misc(ins)
            return self._dataproc(ins)
        if op == 1:
            if (w & 0x01900000) == 0x01000000:
                return self._msr_imm(ins)
            return self._dataproc(ins)
        if op == 2:
            return self._ldst(ins)
        if op == 3:
            if w & 0x10:
                return self._media(ins)
            return self._ldst(ins)
        if op == 4:
            return self._ldm_stm(ins)
        if op == 5:
            target = (ins.addr + 8 + (sext(w & 0xFFFFFF, 24) << 2)) & 0xFFFFFFFF
            ins.kind = BL if (w >> 24) & 1 else B
            ins.target, ins.target_mode = target, "arm"
            return None
        if op == 6:
            return self._coproc_ls(ins)
        if (w >> 24) & 1:
            ins.kind, ins.imm = SVC, w & 0xFFFFFF
            return None
        return self._coproc(ins)

    # ── helpers ──────────────────────────────────────────────────────────
    @staticmethod
    def rr(n: int, pc: int) -> str:
        return hexc(pc) if n == 15 else f"R({n})"

    def _literal(self, addr: int, size: int = 4, signed: bool = False) -> str | None:
        v = self.read32(addr & ~3)
        if v is None:
            return None
        if size == 4:
            if addr & 3:
                return None
            return hexc(v)
        shift = (addr & 3) * 8
        if size == 2:
            if addr & 1 or (addr & 3) == 3:
                return None
            v = (v >> shift) & 0xFFFF
            if signed and v & 0x8000:
                v -= 0x10000
        else:
            v = (v >> shift) & 0xFF
            if signed and v & 0x80:
                v -= 0x100
        return hexc(v)

    def _shifter(self, w: int, pc: int) -> tuple[str, str | None]:
        """Operand 2 of data processing: (value, carry-out or None for unchanged)."""
        if w & (1 << 25):
            rot = ((w >> 8) & 0xF) * 2
            val = ror32(w & 0xFF, rot)
            return hexc(val), (f"{val >> 31}u" if rot else None)
        rm, typ = w & 0xF, (w >> 5) & 3
        if w & 0x10:
            rs = (w >> 8) & 0xF
            if rm == 15 or rs == 15:
                raise Unpredictable("PC in register-shifted operand")
            name = SHIFT_NAMES[typ]
            return (f"rc_{name}_r(R({rm}), R({rs}))",
                    f"rc_{name}_rc(R({rm}), R({rs}), RC_FC)")
        imm = (w >> 7) & 0x1F
        return self._imm_shift(self.rr(rm, pc), typ, imm)

    @staticmethod
    def _imm_shift(x: str, typ: int, imm: int) -> tuple[str, str | None]:
        if typ == 0:
            if imm == 0:
                return x, None
            return f"({x} << {imm})", f"(({x} >> {32 - imm}) & 1)"
        if typ == 1:
            if imm == 0:
                return "0u", f"({x} >> 31)"
            return f"({x} >> {imm})", f"(({x} >> {imm - 1}) & 1)"
        if typ == 2:
            if imm == 0:
                return f"(u32)((s32){x} >> 31)", f"({x} >> 31)"
            return f"(u32)((s32){x} >> {imm})", f"(({x} >> {imm - 1}) & 1)"
        if imm == 0:
            return f"((RC_FC << 31) | ({x} >> 1))", f"({x} & 1)"
        return f"rc_ror({x}, {imm})", f"(({x} >> {imm - 1}) & 1)"

    # ── data processing ──────────────────────────────────────────────────
    LOGICAL = {0: "a & b", 1: "a ^ b", 8: "a & b", 9: "a ^ b", 12: "a | b", 13: "b", 14: "a & ~b", 15: "~b"}
    ARITH_S = {2: "rc_subs(ctx, a, b, 1)", 3: "rc_subs(ctx, b, a, 1)", 4: "rc_adds(ctx, a, b, 0)",
               5: "rc_adds(ctx, a, b, RC_FC)", 6: "rc_subs(ctx, a, b, RC_FC)",
               7: "rc_subs(ctx, b, a, RC_FC)", 10: "rc_subs(ctx, a, b, 1)", 11: "rc_adds(ctx, a, b, 0)"}
    ARITH = {2: "a - b", 3: "b - a", 4: "a + b", 5: "a + b + RC_FC", 6: "a + ~b + RC_FC",
             7: "b + ~a + RC_FC"}

    def _dataproc(self, ins: Insn) -> None:
        w = ins.word
        opc, s, rn, rd = (w >> 21) & 0xF, (w >> 20) & 1, (w >> 16) & 0xF, (w >> 12) & 0xF
        pc = ins.addr + 8
        is_test = 8 <= opc <= 11
        logical = opc in self.LOGICAL
        imm_form = bool(w & (1 << 25))
        reg_shift = not imm_form and bool(w & 0x10)
        if is_test and not s:
            raise Unsupported("test without S")
        if reg_shift and (rd == 15 or rn == 15):
            raise Unpredictable("PC with register-shifted operand")
        if rd == 15 and not is_test:
            if s:
                raise Unsupported("exception return")
            if opc == 13 and not imm_form and (w & 0xFFF) == 14:
                ins.kind, ins.pcexpr = RET, "R(14)"
                return
            if (opc == 4 and rn == 15 and not imm_form and not reg_shift and (w & 0x60) == 0
                    and ((w >> 7) & 0x1F) == 2 and (w & 0xF) != 15):
                ins.kind, ins.jt_kind, ins.jt_index, ins.jt_base = JTAB, "addpc", w & 0xF, pc
                return
        if is_test and rd != 0:
            raise Unpredictable("test with Rd != 0 (SBZ)")
        if opc in (13, 15) and rn != 0:
            raise Unpredictable("MOV/MVN with Rn != 0 (SBZ)")
        val, carry = self._shifter(w, pc)
        need_c = bool(s and logical and carry is not None)
        body = [f"u32 b = {val};"]
        if need_c:
            body.append(f"u32 sc = {carry};")
        if opc not in (13, 15):
            body.append(f"u32 a = {self.rr(rn, pc)};")
        if logical:
            expr = self.LOGICAL[opc]
            body.append(f"u32 r = {expr};")
            if s:
                body.append("RC_NZ(r);")
                if need_c:
                    body.append("RC_FC = sc;")
        else:
            expr = self.ARITH_S[opc] if s else self.ARITH[opc]
            body.append(f"u32 r = {expr};")
        if is_test:
            body.append("(void)r;")
        elif rd == 15:
            ins.kind, ins.pcexpr = IJUMP, "r"
        else:
            body.append(f"R({rd}) = r;")
        ins.body = body

    # ── multiply / swap / exclusive ──────────────────────────────────────
    def _multiply_swap_excl(self, ins: Insn) -> None:
        w = ins.word
        if (w >> 24) & 1 == 0:
            sub, s = (w >> 21) & 7, (w >> 20) & 1
            r19, r15, rs, rm = (w >> 16) & 0xF, (w >> 12) & 0xF, (w >> 8) & 0xF, w & 0xF
            if 15 in (r19, rs, rm) or (sub in (1, 2) or sub >= 4) and r15 == 15:
                raise Unpredictable("PC in multiply")
            if sub == 0 and r15 != 0:
                raise Unpredictable("MUL SBZ")
            if sub in (0, 1):
                acc = f" + R({r15})" if sub == 1 else ""
                ins.body = [f"u32 r = R({rm}) * R({rs}){acc};"]
                if s:
                    ins.body.append("RC_NZ(r);")
                ins.body.append(f"R({r19}) = r;")
                return
            if sub == 2:
                if s:
                    raise Unsupported("UMAAL with S")
                if r19 == r15:
                    raise Unpredictable("UMAAL RdHi==RdLo")
                ins.body = [f"u64 p = (u64)R({rm}) * (u64)R({rs}) + (u64)R({r15}) + (u64)R({r19});",
                            f"R({r15}) = (u32)p;", f"R({r19}) = (u32)(p >> 32);"]
                return
            if sub == 3:
                raise Unsupported("MLS (ARMv6T2)")
            signed, acc = (w >> 22) & 1, (w >> 21) & 1
            hi, lo = r19, r15
            if hi == lo:
                raise Unpredictable("long multiply RdHi==RdLo")
            prod = (f"(u64)((s64)(s32)R({rm}) * (s64)(s32)R({rs}))" if signed
                    else f"(u64)R({rm}) * (u64)R({rs})")
            body = [f"u64 p = {prod};"]
            if acc:
                body.append(f"p += ((u64)R({hi}) << 32) | (u64)R({lo});")
            if s:
                body.append("RC_FN = (u32)(p >> 63); RC_FZ = (p == 0);")
            body += [f"R({lo}) = (u32)p;", f"R({hi}) = (u32)(p >> 32);"]
            ins.body = body
            return
        rn, rt, r2 = (w >> 16) & 0xF, (w >> 12) & 0xF, w & 0xF
        if (w >> 23) & 1 == 0:
            if (w >> 20) & 3 or (w >> 8) & 0xF:
                raise Unsupported("undefined in swap space")
            if 15 in (rn, rt, r2) or rn in (rt, r2):
                raise Unpredictable("SWP registers")
            if (w >> 22) & 1:
                ins.body = [f"u32 a = R({rn});", "u32 t = rd8(ctx, a);", f"wr8(ctx, a, R({r2}));", f"R({rt}) = t;"]
            else:
                ins.body = [f"u32 a = R({rn});", "u32 t = rd32(ctx, a);", f"wr32(ctx, a, R({r2}));", f"R({rt}) = t;"]
            return
        op = (w >> 20) & 7
        if rn == 15:
            raise Unpredictable("PC base in exclusive")
        load = op & 1
        size = {0: 4, 2: 8, 4: 1, 6: 2}[op & 6]
        if load and (w & 0xFFF) != 0xF9F or not load and (w & 0xF00) != 0xF00:
            raise Unpredictable("exclusive SBO bits")
        if load:
            if rt == 15 or (size == 8 and (rt & 1 or rt == 14)):
                raise Unpredictable("LDREX registers")
            body = [f"u32 a = R({rn});", "ctx->excl_addr = a; ctx->excl_valid = 1;"]
            if size == 8:
                body += [f"u32 v0 = rd32(ctx, a), v1 = rd32(ctx, a + 4);", f"R({rt}) = v0;", f"R({rt + 1}) = v1;"]
            else:
                fn = {4: "rd32", 1: "rd8", 2: "rd16"}[size]
                body.append(f"R({rt}) = {fn}(ctx, a);")
            ins.body = body
            return
        rd = rt
        rt = r2
        if 15 in (rd, rt) or rd in (rn, rt) or (size == 8 and (rt & 1 or rt == 14 or rd == rt + 1)):
            raise Unpredictable("STREX registers")
        if size == 8:
            store = f"wr32(ctx, a, R({rt})); wr32(ctx, a + 4, R({rt + 1}));"
        else:
            fn = {4: "wr32", 1: "wr8", 2: "wr16"}[size]
            store = f"{fn}(ctx, a, R({rt}));"
        ins.body = [f"u32 a = R({rn});",
                    f"if (ctx->excl_valid && ctx->excl_addr == a) {{ {store} R({rd}) = 0; }} else {{ R({rd}) = 1; }}",
                    "ctx->excl_valid = 0;"]

    # ── misc (MRS/MSR/BX/BLX/CLZ/QADD/SMLAxy...) ────────────────────────
    def _misc(self, ins: Insn) -> None:
        w = ins.word
        op2, op = (w >> 4) & 0xF, (w >> 21) & 3
        if op2 == 0:
            if (w >> 22) & 1:
                raise Unsupported("SPSR access")
            if op & 1 == 0:
                rd = (w >> 12) & 0xF
                if rd == 15:
                    raise Unpredictable("MRS PC")
                if (w >> 16) & 0xF != 0xF or w & 0xFFF:
                    raise Unpredictable("MRS SBO/SBZ")
                ins.body = [f"R({rd}) = RC_CPSR();"]
                return
            mask, rm = (w >> 16) & 0xF, w & 0xF
            if rm == 15:
                raise Unpredictable("MSR PC")
            if (w >> 12) & 0xF != 0xF or (w >> 4) & 0xFF:
                raise Unpredictable("MSR SBO/SBZ")
            if mask & 3:
                raise Unsupported("MSR control/extension fields")
            ins.body = [f"RC_SET_CPSR(R({rm}), {mask});"]
            return
        if op2 in (1, 2, 3) and op == 1 and (w >> 8) & 0xFFF != 0xFFF:
            raise Unpredictable("BX/BLX SBO")
        if op2 in (1, 2) and op == 1:
            rm = w & 0xF
            if rm == 14:
                ins.kind, ins.pcexpr = RET, "R(14)"
            elif rm == 15:
                raise Unpredictable("BX PC")
            else:
                ins.kind, ins.pcexpr, ins.body = IJUMP, "t_pc", [f"u32 t_pc = R({rm});"]
            return
        if op2 == 1 and op == 3:
            rd, rm = (w >> 12) & 0xF, w & 0xF
            if 15 in (rd, rm):
                raise Unpredictable("CLZ PC")
            if (w >> 16) & 0xF != 0xF or (w >> 8) & 0xF != 0xF:
                raise Unpredictable("CLZ SBO")
            ins.body = [f"R({rd}) = rc_clz(R({rm}));"]
            return
        if op2 == 3 and op == 1:
            rm = w & 0xF
            if rm == 15:
                raise Unpredictable("BLX PC")
            ins.kind, ins.pcexpr, ins.body = ICALL, "t_pc", [f"u32 t_pc = R({rm});"]
            return
        if op2 == 5:
            rn, rd, rm = (w >> 16) & 0xF, (w >> 12) & 0xF, w & 0xF
            if 15 in (rn, rd, rm):
                raise Unpredictable("PC in saturating op")
            if (w >> 8) & 0xF:
                raise Unpredictable("QADD SBZ")
            if op == 0:
                ins.body = [f"R({rd}) = rc_qadd(ctx, R({rm}), R({rn}));"]
            elif op == 1:
                ins.body = [f"R({rd}) = rc_qsub(ctx, R({rm}), R({rn}));"]
            elif op == 2:
                ins.body = [f"u32 dbl = rc_qadd(ctx, R({rn}), R({rn}));", f"R({rd}) = rc_qadd(ctx, R({rm}), dbl);"]
            else:
                ins.body = [f"u32 dbl = rc_qadd(ctx, R({rn}), R({rn}));", f"R({rd}) = rc_qsub(ctx, R({rm}), dbl);"]
            return
        if op2 == 7:
            if op == 1:
                self._trap(ins, TRAP_BKPT, "bkpt")
                return
            raise Unsupported("SMC")
        if (op2 & 9) == 8:
            x, y = (w >> 5) & 1, (w >> 6) & 1
            rd, rn, rs, rm = (w >> 16) & 0xF, (w >> 12) & 0xF, (w >> 8) & 0xF, w & 0xF
            if 15 in (rd, rs, rm) or (op != 3 and not (op == 1 and x) and rn == 15):
                raise Unpredictable("PC in halfword multiply")
            if (op == 3 or (op == 1 and x)) and rn != 0:
                raise Unpredictable("SMULxy/SMULWy SBZ")
            hm = f"(s32)(s16)(R({rm}) >> 16)" if x else f"(s32)(s16)R({rm})"
            hs = f"(s32)(s16)(R({rs}) >> 16)" if y else f"(s32)(s16)R({rs})"
            if op == 0:
                ins.body = [f"s64 t = (s64)({hm} * {hs}) + (s64)(s32)R({rn});",
                            f"R({rd}) = (u32)t;", "if (t != (s64)(s32)(u32)t) RC_FQ = 1;"]
            elif op == 1:
                if x == 0:
                    ins.body = [f"s64 t = (((s64)(s32)R({rm}) * (s64){hs}) >> 16) + (s64)(s32)R({rn});",
                                f"R({rd}) = (u32)t;", "if (t != (s64)(s32)(u32)t) RC_FQ = 1;"]
                else:
                    ins.body = [f"R({rd}) = (u32)(((s64)(s32)R({rm}) * (s64){hs}) >> 16);"]
            elif op == 2:
                hi, lo = rd, rn
                if hi == lo:
                    raise Unpredictable("SMLAL<x><y> RdHi==RdLo")
                ins.body = [f"u64 acc = (((u64)R({hi}) << 32) | (u64)R({lo})) + (u64)(s64)({hm} * {hs});",
                            f"R({lo}) = (u32)acc;", f"R({hi}) = (u32)(acc >> 32);"]
            else:
                ins.body = [f"R({rd}) = (u32)({hm} * {hs});"]
            return
        raise Unsupported(f"misc op2={op2} op={op}")

    def _msr_imm(self, ins: Insn) -> None:
        w = ins.word
        if (w >> 21) & 1 == 0:
            raise Unsupported("MOVW/MOVT (ARMv6T2)")
        if (w >> 22) & 1:
            raise Unsupported("MSR SPSR")
        mask = (w >> 16) & 0xF
        if (w >> 12) & 0xF != 0xF:
            raise Unpredictable("MSR immediate SBO")
        if mask == 0:
            if (w & 0xF00) == 0 and (w & 0xFF) <= 4:
                ins.body = []          # NOP / YIELD / WFE / WFI / SEV
                return
            raise Unsupported("hint")
        if mask & 3:
            raise Unsupported("MSR control/extension fields")
        imm = ror32(w & 0xFF, ((w >> 8) & 0xF) * 2)
        ins.body = [f"RC_SET_CPSR({hexc(imm)}, {mask});"]

    # ── loads and stores ─────────────────────────────────────────────────
    def _ldst(self, ins: Insn) -> None:
        w = ins.word
        reg = bool(w & (1 << 25))
        p, u, byte, wb, load = (w >> 24) & 1, (w >> 23) & 1, (w >> 22) & 1, (w >> 21) & 1, (w >> 20) & 1
        rn, rt = (w >> 16) & 0xF, (w >> 12) & 0xF
        pc = ins.addr + 8
        wback = (p == 0) or wb
        if reg:
            rm = w & 0xF
            if rm == 15:
                raise Unpredictable("PC as offset register")
            off, _ = self._imm_shift(f"R({rm})", (w >> 5) & 3, (w >> 7) & 0x1F)
            if wback and rm == rn:
                raise Unpredictable("writeback with Rm==Rn")
        else:
            off = hexc(w & 0xFFF)
        if wback and (rn == 15 or rn == rt):
            raise Unpredictable("writeback with Rn==PC or Rn==Rt")
        sign = "+" if u else "-"
        # literal (PC-relative, immediate, no writeback): fold into a constant
        if rn == 15 and not reg and p == 1:
            addr = (pc + (w & 0xFFF) if u else pc - (w & 0xFFF)) & 0xFFFFFFFF
            if load:
                lit = self._literal(addr, 1 if byte else 4)
                if not byte and addr % 4 == 0:
                    ins.literal = self.read32(addr)
                if rt == 15:
                    if byte:
                        raise Unpredictable("LDRB PC")
                    v = self.read32(addr) if addr % 4 == 0 else None
                    if v is None:
                        ins.kind, ins.pcexpr, ins.body = IJUMP, "t_pc", [f"u32 t_pc = rd32(ctx, {hexc(addr)});"]
                        return
                    ins.kind, ins.target = B, v & ~1
                    ins.target_mode = "thumb" if v & 1 else "arm"
                    return
                if lit is not None:
                    ins.body = [f"R({rt}) = {lit};"]
                    return
        body = [f"u32 base = {self.rr(rn, pc)};", f"u32 oa = base {sign} {off};"]
        a = "oa" if p else "base"
        if load:
            fn = "rd8" if byte else "rd32"
            body.append(f"u32 v = {fn}(ctx, {a});")
            if wback:
                body.append(f"R({rn}) = oa;")
            if rt == 15:
                if byte:
                    raise Unpredictable("LDRB PC")
                if rn == 13 and p == 0 and not reg:
                    ins.kind = RET
                elif rn == 15 and reg and p == 1 and u == 1 and not wb and (w & 0xFF0) == 0x100:
                    ins.kind, ins.jt_kind, ins.jt_index, ins.jt_base = JTAB, "ldrpc", w & 0xF, pc
                    ins.body = []
                    return
                else:
                    ins.kind = IJUMP
                    if reg and p == 1 and u == 1 and not wb and (w & 0xFF0) == 0x100:
                        # possible jump table through a base register set by ADR
                        ins.jt_kind, ins.jt_index, ins.jt_base = "ldrreg", w & 0xF, rn
                ins.pcexpr = "v"
            else:
                body.append(f"R({rt}) = v;")
        else:
            if rt == 15 and byte:
                raise Unpredictable("STRB PC")
            fn = "wr8" if byte else "wr32"
            body.append(f"{fn}(ctx, {a}, {self.rr(rt, pc)});")
            if wback:
                body.append(f"R({rn}) = oa;")
        ins.body = body

    def _ldst_extra(self, ins: Insn) -> None:
        w = ins.word
        p, u, imm, wb, load = (w >> 24) & 1, (w >> 23) & 1, (w >> 22) & 1, (w >> 21) & 1, (w >> 20) & 1
        rn, rt, sh = (w >> 16) & 0xF, (w >> 12) & 0xF, (w >> 5) & 3
        pc = ins.addr + 8
        if p == 0 and wb:
            raise Unsupported("LDRHT/STRHT")
        wback = (p == 0) or wb
        if imm:
            offv = ((w >> 4) & 0xF0) | (w & 0xF)
            off = hexc(offv)
        else:
            rm = w & 0xF
            if rm == 15:
                raise Unpredictable("PC as offset register")
            if (w >> 8) & 0xF:
                raise Unsupported("SBZ bits set")
            off = f"R({rm})"
            if wback and rm == rn:
                raise Unpredictable("writeback with Rm==Rn")
        dual = (not load) and sh in (2, 3)
        if dual and (rt & 1 or rt == 14):
            raise Unpredictable("LDRD/STRD odd register")
        if wback and (rn == 15 or rn == rt or (dual and rn == rt + 1)):
            raise Unpredictable("writeback with Rn==PC or Rn==Rt")
        sign = "+" if u else "-"
        if load and rt == 15:
            raise Unpredictable("halfword load to PC")
        if not imm and dual and (w & 0xF) in (rt, rt + 1) and sh == 2:
            raise Unpredictable("LDRD with Rm==Rt")
        # literal halfword/signed loads and LDRD
        if rn == 15 and imm and p == 1:
            addr = (pc + offv if u else pc - offv) & 0xFFFFFFFF
            if load:
                size, signed = {1: (2, False), 2: (1, True), 3: (2, True)}[sh]
                lit = self._literal(addr, size, signed)
                if lit is not None:
                    ins.body = [f"R({rt}) = {lit};"]
                    return
            elif sh == 2:
                lo, hi = self._literal(addr), self._literal(addr + 4)
                if lo is not None and hi is not None:
                    ins.body = [f"R({rt}) = {lo};", f"R({rt + 1}) = {hi};"]
                    return
        body = [f"u32 base = {self.rr(rn, pc)};", f"u32 oa = base {sign} {off};"]
        a = "oa" if p else "base"
        if load:
            fn = {1: "rd16", 2: "rds8", 3: "rds16"}[sh]
            body.append(f"u32 v = {fn}(ctx, {a});")
            if wback:
                body.append(f"R({rn}) = oa;")
            body.append(f"R({rt}) = v;")
        elif sh == 1:
            if rt == 15:
                raise Unpredictable("STRH PC")
            body.append(f"wr16(ctx, {a}, R({rt}));")
            if wback:
                body.append(f"R({rn}) = oa;")
        elif sh == 2:
            body.append(f"u32 v0 = rd32(ctx, {a}), v1 = rd32(ctx, {a} + 4);")
            if wback:
                body.append(f"R({rn}) = oa;")
            body += [f"R({rt}) = v0;", f"R({rt + 1}) = v1;"]
        else:
            body.append(f"wr32(ctx, {a}, R({rt})); wr32(ctx, {a} + 4, R({rt + 1}));")
            if wback:
                body.append(f"R({rn}) = oa;")
        ins.body = body

    def _ldm_stm(self, ins: Insn) -> None:
        w = ins.word
        p, u, s, wb, load = (w >> 24) & 1, (w >> 23) & 1, (w >> 22) & 1, (w >> 21) & 1, (w >> 20) & 1
        rn, regs = (w >> 16) & 0xF, w & 0xFFFF
        if s:
            raise Unsupported("LDM/STM with S bit")
        if rn == 15 or regs == 0:
            raise Unpredictable("LDM/STM base PC or empty list")
        n = popcount(regs)
        if load and wb and regs & (1 << rn):
            raise Unpredictable("LDM writeback with Rn in list")
        if not load and wb and regs & (1 << rn) and (regs & ((1 << rn) - 1)):
            raise Unpredictable("STM writeback with Rn not lowest")
        start = {(0, 1): "a", (1, 1): "a + 4", (0, 0): f"a - {4 * n - 4}", (1, 0): f"a - {4 * n}"}[(p, u)]
        body = [f"u32 a = R({rn});", f"u32 s = {start};"]
        k = 0
        pcv = None
        for r in range(16):
            if not regs & (1 << r):
                continue
            addr = f"s + {4 * k}" if k else "s"
            if load:
                if r == 15:
                    body.append(f"u32 t_pc = rd32(ctx, {addr});")
                    pcv = "t_pc"
                else:
                    body.append(f"R({r}) = rd32(ctx, {addr});")
            else:
                body.append(f"wr32(ctx, {addr}, {self.rr(r, ins.addr + 8)});")
            k += 1
        if wb:
            body.append(f"R({rn}) = a {'+' if u else '-'} {4 * n};")
        ins.body = body
        if pcv:
            ins.pcexpr = pcv
            ins.kind = RET if (rn == 13 and p == 0 and u == 1 and wb) else IJUMP

    # ── media (ARMv6 SIMD, pack/extend/saturate/reverse, signed multiplies) ─
    def _media(self, ins: Insn) -> None:
        w = ins.word
        op1, op2 = (w >> 20) & 0x1F, (w >> 5) & 7
        rn, rd, rm = (w >> 16) & 0xF, (w >> 12) & 0xF, w & 0xF
        if op1 >> 3 == 0:
            kind = op1 & 7
            if kind in (0, 4) or op2 in (5, 6):
                raise Unsupported("undefined parallel op")
            if 15 in (rn, rd, rm):
                raise Unpredictable("PC in parallel op")
            if (w >> 8) & 0xF != 0xF:
                raise Unpredictable("parallel op SBO")
            ins.body = [f"R({rd}) = rc_parallel(ctx, {kind}, {op2}, R({rn}), R({rm}));"]
            return
        if op1 >> 3 == 1:
            return self._media_pack(ins, op1, op2, rn, rd, rm)
        if op1 >> 3 == 2:
            return self._media_mul(ins, op1, op2)
        if op1 == 0b11000 and op2 == 0:
            rd, ra, rm, rn = (w >> 16) & 0xF, (w >> 12) & 0xF, (w >> 8) & 0xF, w & 0xF
            if 15 in (rd, rm, rn):
                raise Unpredictable("PC in USAD8")
            acc = "" if ra == 15 else f" + R({ra})"
            ins.body = [f"R({rd}) = rc_usad8(R({rn}), R({rm})){acc};"]
            return
        if op1 == 0b11111 and op2 == 7:
            self._trap(ins, TRAP_UNDEF, "udf")
            return
        raise Unsupported(f"media op1={op1:05b} op2={op2:03b}")

    def _media_pack(self, ins: Insn, op1: int, op2: int, rn: int, rd: int, rm: int) -> None:
        w = ins.word
        rot = ((w >> 10) & 3) * 8
        src = f"rc_ror(R({rm}), {rot})" if rot else f"R({rm})"

        def need(*regs):
            if 15 in regs:
                raise Unpredictable("PC in pack/extend")

        if op1 in (0b01010, 0b01011, 0b01110, 0b01111) and op2 & 1 == 0:
            sat, imm5, sh = (w >> 16) & 0x1F, (w >> 7) & 0x1F, (w >> 6) & 1
            need(rd, rm)
            if sh:
                val = f"(s64)(s32)rc_asr(R({rm}), {imm5 or 32})"
            else:
                val = f"(s64)(s32)(R({rm}) << {imm5})"
            if op1 <= 0b01011:
                ins.body = [f"R({rd}) = rc_ssat(ctx, {val}, {sat + 1});"]
            else:
                ins.body = [f"R({rd}) = rc_usat(ctx, {val}, {sat});"]
            return
        if op1 == 0b01000:
            if op2 & 1 == 0:
                need(rn, rd, rm)
                imm5, tb = (w >> 7) & 0x1F, (w >> 6) & 1
                if tb:
                    ins.body = [f"R({rd}) = (R({rn}) & 0xFFFF0000u) | (rc_asr(R({rm}), {imm5 or 32}) & 0xFFFFu);"]
                else:
                    ins.body = [f"R({rd}) = (R({rn}) & 0xFFFFu) | ((R({rm}) << {imm5}) & 0xFFFF0000u);"]
                return
            if op2 == 3:
                need(rd, rm)
                if (w >> 8) & 3:
                    raise Unpredictable("extend SBZ")
                lo = f"(u32)(s32)(s8)t"
                hi = f"(u32)(s32)(s8)(t >> 16)"
                if rn == 15:
                    ins.body = [f"u32 t = {src};", f"R({rd}) = ({lo} & 0xFFFFu) | ({hi} << 16);"]
                else:
                    ins.body = [f"u32 t = {src};",
                                f"R({rd}) = ((R({rn}) + {lo}) & 0xFFFFu) | (((R({rn}) >> 16) + {hi}) << 16);"]
                return
            if op2 == 5:
                need(rn, rd, rm)
                if (w >> 8) & 0xF != 0xF:
                    raise Unpredictable("SEL SBO")
                ins.body = [f"R({rd}) = rc_sel(ctx, R({rn}), R({rm}));"]
                return
        if op2 == 1 and op1 in (0b01010, 0b01110) and (w >> 8) & 0xF != 0xF:
            raise Unpredictable("SSAT16/USAT16 SBO")
        if op2 in (1, 5) and op1 in (0b01011, 0b01111) and ((w >> 8) & 0xF != 0xF or (w >> 16) & 0xF != 0xF):
            raise Unpredictable("REV SBO")
        if op2 == 3 and (w >> 8) & 3:
            raise Unpredictable("extend SBZ")
        if op1 == 0b01010 and op2 == 1:
            need(rd, rm)
            sat = ((w >> 16) & 0xF) + 1
            ins.body = [f"u32 lo = rc_ssat(ctx, (s64)(s16)R({rm}), {sat}) & 0xFFFFu;",
                        f"u32 hi = rc_ssat(ctx, (s64)(s16)(R({rm}) >> 16), {sat}) & 0xFFFFu;",
                        f"R({rd}) = lo | (hi << 16);"]
            return
        if op1 == 0b01110 and op2 == 1:
            need(rd, rm)
            sat = (w >> 16) & 0xF
            ins.body = [f"u32 lo = rc_usat(ctx, (s64)(s16)R({rm}), {sat}) & 0xFFFFu;",
                        f"u32 hi = rc_usat(ctx, (s64)(s16)(R({rm}) >> 16), {sat}) & 0xFFFFu;",
                        f"R({rd}) = lo | (hi << 16);"]
            return
        ext = {(0b01010, 3): "(u32)(s32)(s8)", (0b01011, 3): "(u32)(s32)(s16)",
               (0b01110, 3): "(u32)(u8)", (0b01111, 3): "(u32)(u16)"}.get((op1, op2))
        if ext:
            need(rd, rm)
            val = f"{ext}({src})"
            ins.body = [f"R({rd}) = {val};" if rn == 15 else f"R({rd}) = R({rn}) + {val};"]
            return
        if op1 == 0b01100 and op2 == 3:
            need(rd, rm)
            if rn == 15:
                ins.body = [f"R({rd}) = ({src}) & 0x00FF00FFu;"]
            else:
                ins.body = [f"u32 t = {src};",
                            f"R({rd}) = ((R({rn}) + (t & 0xFF)) & 0xFFFFu) | (((R({rn}) >> 16) + ((t >> 16) & 0xFF)) << 16);"]
            return
        if op1 == 0b01011 and op2 == 1:
            need(rd, rm)
            ins.body = [f"R({rd}) = rc_rev(R({rm}));"]
            return
        if op1 == 0b01011 and op2 == 5:
            need(rd, rm)
            ins.body = [f"u32 x = R({rm});", f"R({rd}) = ((x >> 8) & 0x00FF00FFu) | ((x << 8) & 0xFF00FF00u);"]
            return
        if op1 == 0b01111 and op2 == 5:
            need(rd, rm)
            ins.body = [f"u32 x = R({rm});", f"R({rd}) = (u32)(s32)(s16)(u16)(((x >> 8) & 0xFF) | ((x & 0xFF) << 8));"]
            return
        raise Unsupported(f"pack op1={op1:05b} op2={op2:03b}")

    def _media_mul(self, ins: Insn, op1: int, op2: int) -> None:
        w = ins.word
        rd, ra, rm, rn = (w >> 16) & 0xF, (w >> 12) & 0xF, (w >> 8) & 0xF, w & 0xF
        swap = (w >> 5) & 1
        if 15 in (rd, rm, rn):
            raise Unpredictable("PC in media multiply")
        m = f"rc_ror(R({rm}), 16)" if swap else f"R({rm})"
        prods = [f"u32 mm = {m};",
                 f"s32 p1 = (s32)(s16)R({rn}) * (s32)(s16)mm;",
                 f"s32 p2 = (s32)(s16)(R({rn}) >> 16) * (s32)(s16)(mm >> 16);"]
        if op1 == 0b10000 and op2 in (0, 1, 2, 3):
            sub = op2 >= 2
            comb = "(s64)p1 - (s64)p2" if sub else "(s64)p1 + (s64)p2"
            if ra == 15:
                if sub:
                    ins.body = prods + [f"R({rd}) = (u32)({comb});"]
                else:
                    ins.body = prods + [f"s64 t = {comb};", f"R({rd}) = (u32)t;",
                                        "if (t != (s64)(s32)(u32)t) RC_FQ = 1;"]
            else:
                ins.body = prods + [f"s64 t = {comb} + (s64)(s32)R({ra});", f"R({rd}) = (u32)t;",
                                    "if (t != (s64)(s32)(u32)t) RC_FQ = 1;"]
            return
        if op1 == 0b10100 and op2 in (0, 1, 2, 3):
            hi, lo = rd, ra
            if lo == 15 or hi == lo:
                raise Unpredictable("SMLALD registers")
            comb = "(s64)p1 - (s64)p2" if op2 >= 2 else "(s64)p1 + (s64)p2"
            ins.body = prods + [f"u64 acc = (((u64)R({hi}) << 32) | (u64)R({lo})) + (u64)({comb});",
                                f"R({lo}) = (u32)acc;", f"R({hi}) = (u32)(acc >> 32);"]
            return
        if op1 == 0b10101 and op2 in (0, 1, 6, 7):
            rnd = "0x80000000ull" if swap else "0ull"
            prod = f"(u64)((s64)(s32)R({rn}) * (s64)(s32)R({rm}))"
            if op2 in (0, 1):
                if ra == 15:
                    ins.body = [f"u64 t = {prod} + {rnd};", f"R({rd}) = (u32)(t >> 32);"]
                else:
                    ins.body = [f"u64 t = ((u64)R({ra}) << 32) + {prod} + {rnd};", f"R({rd}) = (u32)(t >> 32);"]
            else:
                if ra == 15:
                    raise Unpredictable("SMMLS Ra==PC")
                ins.body = [f"u64 t = ((u64)R({ra}) << 32) - {prod} + {rnd};", f"R({rd}) = (u32)(t >> 32);"]
            return
        raise Unsupported(f"media multiply op1={op1:05b} op2={op2:03b}")

    # ── coprocessors: CP15 and VFP ───────────────────────────────────────
    def _coproc(self, ins: Insn) -> None:
        w = ins.word
        cp = (w >> 8) & 0xF
        if cp == 15 and w & 0x10:
            load, opc1, crn, rt = (w >> 20) & 1, (w >> 21) & 7, (w >> 16) & 0xF, (w >> 12) & 0xF
            opc2, crm = (w >> 5) & 7, w & 0xF
            if rt == 15:
                raise Unsupported("MRC to APSR")
            if opc1 == 0 and crn == 13 and crm == 0:
                if opc2 == 3 and load:
                    ins.body = [f"R({rt}) = ctx->tpidruro;"]
                    return
                if opc2 == 2:
                    ins.body = [f"R({rt}) = ctx->tpidrurw;" if load else f"ctx->tpidrurw = R({rt});"]
                    return
            if not load and opc1 == 0 and crn == 7 and ((crm == 10 and opc2 in (4, 5)) or (crm == 5 and opc2 == 4)):
                ins.body = []          # barriers
                return
            raise Unsupported(f"cp15 c{crn} c{crm} {opc1} {opc2}")
        if cp in (10, 11):
            if w & 0x10:
                return self._vfp_xfer(ins)
            return self._vfp_dp(ins)
        raise Unsupported(f"coprocessor {cp}")

    @staticmethod
    def _sreg(v: int, bit: int) -> int:
        return (v << 1) | bit

    @staticmethod
    def _dreg(v: int, bit: int) -> int:
        if bit:
            raise Unsupported("d16-d31 (VFPv3)")
        return v

    def _vector(self, sz: int, d: int, n: int, m: int) -> list[tuple[int, int, int]]:
        """Register triples, one per element, for a vectorisable VFP operation under the
        current FPSCR LEN/STRIDE (VFPv2 short vectors). Banks are s0-s7, s8-s15, ... for
        single precision and d0-d3, d4-d7, ... for double; element registers wrap within
        their bank. A destination in bank 0 makes the operation scalar; a second operand
        in bank 0 is a scalar used for every element. Elements are computed in order."""
        length = ((self._vec >> 16) & 7) + 1
        if length == 1:
            return [(d, n, m)]
        stride_field = (self._vec >> 20) & 3
        if stride_field not in (0, 3):
            raise Unpredictable("FPSCR.Stride")
        stride = 1 if stride_field == 0 else 2
        size = 4 if sz else 8
        if length * stride > size:
            raise Unpredictable("short vector longer than a register bank")
        bank = ~(size - 1)
        if d & bank == 0:
            return [(d, n, m)]

        def adv(r: int, i: int) -> int:
            return (r & bank) | ((r + i * stride) & (size - 1))
        m_scalar = m & bank == 0
        return [(adv(d, i), adv(n, i), m if m_scalar else adv(m, i)) for i in range(length)]

    def _vfp_dp(self, ins: Insn) -> None:
        w = ins.word
        if (w >> 24) & 0xF != 0xE:
            raise Unsupported("VFP")
        sz = (w >> 8) & 1
        D, N, M = (w >> 22) & 1, (w >> 7) & 1, (w >> 5) & 1
        vd, vn, vm = (w >> 12) & 0xF, (w >> 16) & 0xF, w & 0xF
        opc = (((w >> 23) & 1) << 3) | (((w >> 21) & 1) << 2) | (((w >> 20) & 1) << 1) | ((w >> 6) & 1)
        T, suf = ("dw", "64") if sz else ("sw", "32")
        F = lambda r: f"ctx->f.{T}[{r}]"  # noqa: E731
        same_size = not (opc == 15 and (vn in (7, 8, 12, 13)))
        if same_size:
            if sz:
                d, m = self._dreg(vd, D), self._dreg(vm, M)
            else:
                d, m = self._sreg(vd, D), self._sreg(vm, M)
        if opc != 15 and (opc & 0xE) != 0xE:
            n = self._dreg(vn, N) if sz else self._sreg(vn, N)
            if opc > 8:
                raise Unsupported(f"VFP opcode {opc}")
            fs = "ctx->fpscr"

            def expr(d: int, n: int, m: int) -> str:
                mul = f"vmul{suf}({fs}, {F(n)}, {F(m)})"
                return {
                    0: f"vadd{suf}({fs}, {F(d)}, {mul})",                          # VMLA
                    1: f"vadd{suf}({fs}, {F(d)}, vneg{suf}({mul}))",               # VMLS
                    2: f"vadd{suf}({fs}, vneg{suf}({F(d)}), {mul})",               # VNMLS
                    3: f"vadd{suf}({fs}, vneg{suf}({F(d)}), vneg{suf}({mul}))",    # VNMLA
                    4: mul,                                                         # VMUL
                    5: f"vneg{suf}({mul})",                                         # VNMUL
                    6: f"vadd{suf}({fs}, {F(n)}, {F(m)})",
                    7: f"vsub{suf}({fs}, {F(n)}, {F(m)})",
                    8: f"vdiv{suf}({fs}, {F(n)}, {F(m)})",
                }[opc]
            ins.body = [f"{F(dd)} = {expr(dd, nn, mm)};" for dd, nn, mm in self._vector(sz, d, n, m)]
            return
        if opc != 15:
            raise Unsupported(f"VFP opcode {opc}")
        # extension instructions: opcode in Vn and bit 7
        x = vn
        nbit = N
        if x in (0, 1):     # VMOV / VABS / VNEG / VSQRT (vectorisable; VCMP and VCVT are not)
            op = {(0, 0): lambda r: F(r), (0, 1): lambda r: f"vabs{suf}({F(r)})",
                  (1, 0): lambda r: f"vneg{suf}({F(r)})", (1, 1): lambda r: f"vsqrt{suf}(ctx->fpscr, {F(r)})"}[(x, nbit)]
            ins.body = [f"{F(dd)} = {op(mm)};" for dd, _, mm in self._vector(sz, d, d, m)]
            return
        if x == 4:
            ins.body = [f"RC_SET_FPFLAGS(vcmp{suf}(ctx->fpscr, {F(d)}, {F(m)}));"]
            return
        if x == 5:
            if vm or M:
                raise Unsupported("VCMP #0 with Vm")
            ins.body = [f"RC_SET_FPFLAGS(vcmp{suf}(ctx->fpscr, {F(d)}, 0));"]
            return
        if x == 7 and nbit:
            if sz:      # F64 -> F32
                dd, mm = self._sreg(vd, D), self._dreg(vm, M)
                ins.body = [f"ctx->f.sw[{dd}] = vcvt_f32_f64(ctx->fpscr, ctx->f.dw[{mm}]);"]
            else:       # F32 -> F64
                dd, mm = self._dreg(vd, D), self._sreg(vm, M)
                ins.body = [f"ctx->f.dw[{dd}] = vcvt_f64_f32(ctx->fpscr, ctx->f.sw[{mm}]);"]
            return
        if x == 8:
            mm = self._sreg(vm, M)
            src = f"(s32)ctx->f.sw[{mm}]" if nbit else f"ctx->f.sw[{mm}]"
            if sz:
                dd = self._dreg(vd, D)
                ins.body = [f"ctx->f.dw[{dd}] = d2u((double){src});"]
            else:
                dd = self._sreg(vd, D)
                ins.body = [f"ctx->f.sw[{dd}] = f2u((float){src});"]
            return
        if x in (12, 13):
            dd = self._sreg(vd, D)
            val = (f"rc_d2d(ctx->fpscr, ctx->f.dw[{self._dreg(vm, M)}])" if sz
                   else f"rc_s2d(ctx->fpscr, ctx->f.sw[{self._sreg(vm, M)}])")
            fn = "rc_f2s" if x == 13 else "rc_f2u"
            ins.body = [f"ctx->f.sw[{dd}] = {fn}({val}, {nbit});"]
            return
        raise Unsupported(f"VFP extension opcode {x}")

    def _vfp_xfer(self, ins: Insn) -> None:
        w = ins.word
        if (w >> 24) & 0xF != 0xE or (w & 0x6F) != 0:
            raise Unsupported("VFP register transfer form")
        cp = (w >> 8) & 0xF
        opc1, load, rt = (w >> 21) & 7, (w >> 20) & 1, (w >> 12) & 0xF
        vn, nbit = (w >> 16) & 0xF, (w >> 7) & 1
        if cp == 10 and opc1 == 0:
            if rt == 15:
                raise Unpredictable("VMOV with PC")
            s = self._sreg(vn, nbit)
            ins.body = [f"R({rt}) = ctx->f.sw[{s}];" if load else f"ctx->f.sw[{s}] = R({rt});"]
            return
        if cp == 10 and opc1 == 7:
            if vn != 1:
                raise Unsupported("VMRS/VMSR on non-FPSCR register")
            if load:
                if rt == 15:
                    ins.body = ["u32 f = ctx->fpscr;",
                                "RC_FN = f >> 31; RC_FZ = (f >> 30) & 1; RC_FC = (f >> 29) & 1; RC_FV = (f >> 28) & 1;"]
                else:
                    ins.body = [f"R({rt}) = ctx->fpscr;"]
            else:
                if rt == 15:
                    raise Unpredictable("VMSR from PC")
                ins.body = [f"rc_write_fpscr(ctx, R({rt}));"]
            return
        if cp == 11 and (opc1 & 6) == 0:
            if rt == 15:
                raise Unpredictable("VMOV scalar with PC")
            d = self._dreg(vn, nbit)
            half = 2 * d + (opc1 & 1)
            ins.body = [f"R({rt}) = ctx->f.sw[{half}];" if load else f"ctx->f.sw[{half}] = R({rt});"]
            return
        raise Unsupported(f"VFP transfer cp{cp} opc1={opc1}")

    def _coproc_ls(self, ins: Insn) -> None:
        w = ins.word
        cp = (w >> 8) & 0xF
        if cp not in (10, 11):
            raise Unsupported(f"coprocessor {cp} load/store")
        sz = cp & 1
        if (w >> 21) & 0x7F == 0b1100010:
            return self._vfp_two_regs(ins, sz)
        p, u, D, wb, load = (w >> 24) & 1, (w >> 23) & 1, (w >> 22) & 1, (w >> 21) & 1, (w >> 20) & 1
        rn, vd, imm8 = (w >> 16) & 0xF, (w >> 12) & 0xF, w & 0xFF
        pc = ins.addr + 8
        if p == 1 and wb == 0:            # VLDR / VSTR
            if rn == 15:
                base = hexc(((pc & ~3) + imm8 * 4 if u else (pc & ~3) - imm8 * 4) & 0xFFFFFFFF)
                addr = base
                body = []
            else:
                body = [f"u32 ea = R({rn}) {'+' if u else '-'} {imm8 * 4};"]
                addr = "ea"
            if sz:
                d = self._dreg(vd, D)
                if load:
                    body.append(f"ctx->f.dw[{d}] = (u64)rd32(ctx, {addr}) | ((u64)rd32(ctx, {addr} + 4) << 32);")
                else:
                    body.append(f"wr32(ctx, {addr}, ctx->f.sw[{2 * d}]); wr32(ctx, {addr} + 4, ctx->f.sw[{2 * d + 1}]);")
            else:
                s = self._sreg(vd, D)
                body.append(f"ctx->f.sw[{s}] = rd32(ctx, {addr});" if load else f"wr32(ctx, {addr}, ctx->f.sw[{s}]);")
            ins.body = body
            return
        if not ((p == 0 and u == 1) or (p == 1 and u == 0 and wb == 1)):
            raise Unsupported("VLDM/VSTM addressing mode")
        if rn == 15:
            raise Unpredictable("VLDM/VSTM with PC base")
        if imm8 == 0:
            raise Unpredictable("VLDM/VSTM with no registers")
        if sz:
            first = self._dreg(vd, D)
            count = imm8 // 2
            words = [2 * (first + i) + h for i in range(count) for h in (0, 1)]
            if count == 0:
                raise Unpredictable("FLDMX/FSTMX with no registers")
            if first + count > 16:
                raise Unpredictable("VLDM/VSTM past d15")
        else:
            first = self._sreg(vd, D)
            words = list(range(first, first + imm8))
            if first + imm8 > 32:
                raise Unpredictable("VLDM/VSTM past s31")
        body = [f"u32 a = R({rn});", f"u32 s = {'a' if u else f'a - {imm8 * 4}'};"]
        for k, sw in enumerate(words):
            addr = f"s + {4 * k}" if k else "s"
            body.append(f"ctx->f.sw[{sw}] = rd32(ctx, {addr});" if load else f"wr32(ctx, {addr}, ctx->f.sw[{sw}]);")
        if wb:
            body.append(f"R({rn}) = a {'+' if u else '-'} {imm8 * 4};")
        ins.body = body

    def _vfp_two_regs(self, ins: Insn, sz: int) -> None:
        w = ins.word
        if (w & 0xD0) != 0x10:
            raise Unsupported("VMOV two-register form")
        to_core, rt2, rt = (w >> 20) & 1, (w >> 16) & 0xF, (w >> 12) & 0xF
        vm, mbit = w & 0xF, (w >> 5) & 1
        if 15 in (rt, rt2) or (to_core and rt == rt2):
            raise Unpredictable("VMOV two registers")
        if sz:
            m = self._dreg(vm, mbit)
            lo, hi = 2 * m, 2 * m + 1
        else:
            lo = self._sreg(vm, mbit)
            hi = lo + 1
            if hi > 31:
                raise Unpredictable("VMOV s31/s32")
        if to_core:
            ins.body = [f"u32 v0 = ctx->f.sw[{lo}], v1 = ctx->f.sw[{hi}];", f"R({rt}) = v0;", f"R({rt2}) = v1;"]
        else:
            ins.body = [f"u32 v0 = R({rt}), v1 = R({rt2});", f"ctx->f.sw[{lo}] = v0;", f"ctx->f.sw[{hi}] = v1;"]

    # ── unconditional space ──────────────────────────────────────────────
    def _unconditional(self, ins: Insn) -> None:
        w = ins.word
        if (w & 0x0E000000) == 0x0A000000:
            h = (w >> 24) & 1
            ins.kind = BL
            ins.target = (ins.addr + 8 + (sext(w & 0xFFFFFF, 24) << 2) + (h << 1)) & 0xFFFFFFFF
            ins.target_mode = "thumb"
            return
        if (w & 0xFD70F000) == 0xF550F000 and not ((w >> 25) & 1 and w & 0x10):
            ins.body = []              # PLD
            return
        if w == 0xF57FF01F:
            ins.body = ["ctx->excl_valid = 0;"]   # CLREX
            return
        raise Unsupported(f"unconditional {w:08x}")
