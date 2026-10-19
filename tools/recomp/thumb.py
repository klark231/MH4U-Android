"""Thumb-state decoder/translator: ARMv6 Thumb (Thumb-1 + v6 additions) -> C statements."""
from __future__ import annotations

from typing import Callable

from .insn import (AL, B, BL, ICALL, IJUMP, OP, RET, SVC, TRAP, Insn, Unpredictable, Unsupported,
                   hexc, sext)

TRAP_UNDEF, TRAP_UNSUPPORTED, TRAP_BKPT = 1, 2, 3


class ThumbTranslator:
    def __init__(self, read16: Callable[[int], int | None], read32: Callable[[int], int | None]):
        self.read16 = read16
        self.read32 = read32

    def decode(self, addr: int) -> Insn:
        hw = self.read16(addr)
        if hw is None:
            ins = Insn(addr, 2, 0, "thumb")
            ins.kind, ins.imm, ins.trap_reason = TRAP, TRAP_UNSUPPORTED, "outside image"
            return ins
        ins = Insn(addr, 2, hw, "thumb")
        try:
            self._decode(ins, hw)
        except Unpredictable as e:
            self._trap(ins, TRAP_UNSUPPORTED, f"unpredictable: {e}")
        except Unsupported as e:
            self._trap(ins, TRAP_UNSUPPORTED, f"unsupported: {e}")
        return ins

    @staticmethod
    def _trap(ins: Insn, code: int, reason: str) -> None:
        ins.kind, ins.imm, ins.trap_reason, ins.body, ins.pcexpr = TRAP, code, reason, [], None

    def _literal(self, addr: int) -> str | None:
        v = self.read32(addr)
        return None if v is None else hexc(v)

    def _decode(self, ins: Insn, hw: int) -> None:
        pc = ins.addr + 4
        top5 = hw >> 11
        if top5 <= 2:
            return self._shift_imm(ins, hw, top5)
        if top5 == 3:
            i, sub = (hw >> 10) & 1, (hw >> 9) & 1
            rn, rs, rd = (hw >> 6) & 7, (hw >> 3) & 7, hw & 7
            b = hexc(rn) if i else f"R({rn})"
            fn = f"rc_subs(ctx, a, b, 1)" if sub else "rc_adds(ctx, a, b, 0)"
            ins.body = [f"u32 a = R({rs}), b = {b};", f"R({rd}) = {fn};"]
            return
        if top5 <= 7:
            op, rd, imm = (hw >> 11) & 3, (hw >> 8) & 7, hw & 0xFF
            if op == 0:
                ins.body = [f"u32 r = {hexc(imm)};", "RC_NZ(r);", f"R({rd}) = r;"]
            elif op == 1:
                ins.body = [f"(void)rc_subs(ctx, R({rd}), {hexc(imm)}, 1);"]
            elif op == 2:
                ins.body = [f"R({rd}) = rc_adds(ctx, R({rd}), {hexc(imm)}, 0);"]
            else:
                ins.body = [f"R({rd}) = rc_subs(ctx, R({rd}), {hexc(imm)}, 1);"]
            return
        if hw >> 10 == 0b010000:
            return self._alu(ins, hw)
        if hw >> 10 == 0b010001:
            return self._hireg(ins, hw, pc)
        if top5 == 0b01001:
            rd = (hw >> 8) & 7
            addr = (pc & ~3) + (hw & 0xFF) * 4
            lit = self._literal(addr)
            ins.literal = self.read32(addr)
            ins.body = [f"R({rd}) = {lit};" if lit else f"R({rd}) = rd32(ctx, {hexc(addr)});"]
            return
        if hw >> 12 == 0b0101:
            op, rm, rn, rd = (hw >> 9) & 7, (hw >> 6) & 7, (hw >> 3) & 7, hw & 7
            a = f"u32 a = R({rn}) + R({rm});"
            ins.body = [a, {
                0: f"wr32(ctx, a, R({rd}));", 1: f"wr16(ctx, a, R({rd}));", 2: f"wr8(ctx, a, R({rd}));",
                3: f"R({rd}) = rds8(ctx, a);", 4: f"R({rd}) = rd32(ctx, a);", 5: f"R({rd}) = rd16(ctx, a);",
                6: f"R({rd}) = rd8(ctx, a);", 7: f"R({rd}) = rds16(ctx, a);"}[op]]
            return
        if hw >> 13 == 0b011:
            byte, load, imm5, rn, rd = (hw >> 12) & 1, (hw >> 11) & 1, (hw >> 6) & 31, (hw >> 3) & 7, hw & 7
            off = imm5 if byte else imm5 * 4
            a = f"u32 a = R({rn}) + {off};"
            if load:
                ins.body = [a, f"R({rd}) = {'rd8' if byte else 'rd32'}(ctx, a);"]
            else:
                ins.body = [a, f"{'wr8' if byte else 'wr32'}(ctx, a, R({rd}));"]
            return
        if hw >> 12 == 0b1000:
            load, imm5, rn, rd = (hw >> 11) & 1, (hw >> 6) & 31, (hw >> 3) & 7, hw & 7
            a = f"u32 a = R({rn}) + {imm5 * 2};"
            ins.body = [a, f"R({rd}) = rd16(ctx, a);" if load else f"wr16(ctx, a, R({rd}));"]
            return
        if hw >> 12 == 0b1001:
            load, rd, imm = (hw >> 11) & 1, (hw >> 8) & 7, (hw & 0xFF) * 4
            a = f"u32 a = R(13) + {imm};"
            ins.body = [a, f"R({rd}) = rd32(ctx, a);" if load else f"wr32(ctx, a, R({rd}));"]
            return
        if hw >> 12 == 0b1010:
            sp, rd, imm = (hw >> 11) & 1, (hw >> 8) & 7, (hw & 0xFF) * 4
            ins.body = [f"R({rd}) = R(13) + {imm};" if sp else f"R({rd}) = {hexc((pc & ~3) + imm)};"]
            return
        if hw >> 12 == 0b1011:
            return self._misc(ins, hw)
        if hw >> 12 == 0b1100:
            return self._ldm_stm(ins, hw)
        if hw >> 12 == 0b1101:
            cond = (hw >> 8) & 0xF
            if cond == 0xE:
                self._trap(ins, TRAP_UNDEF, "udf")
                return
            if cond == 0xF:
                ins.kind, ins.imm = SVC, hw & 0xFF
                return
            ins.kind, ins.cond = B, cond
            ins.target, ins.target_mode = (pc + (sext(hw & 0xFF, 8) << 1)) & 0xFFFFFFFF, "thumb"
            return
        if top5 == 0b11100:
            ins.kind = B
            ins.target, ins.target_mode = (pc + (sext(hw & 0x7FF, 11) << 1)) & 0xFFFFFFFF, "thumb"
            return
        if top5 == 0b11110:
            hw2 = self.read16(ins.addr + 2)
            hi = sext(hw & 0x7FF, 11) << 12
            if hw2 is not None and hw2 >> 11 in (0b11111, 0b11101):
                ins.size = 4
                ins.word = hw | (hw2 << 16)
                target = (pc + hi + ((hw2 & 0x7FF) << 1)) & 0xFFFFFFFF
                ins.kind = BL
                if hw2 >> 11 == 0b11111:
                    ins.target, ins.target_mode = target, "thumb"
                else:
                    if hw2 & 1:
                        raise Unsupported("BLX suffix with bit 0 set")
                    ins.target, ins.target_mode = target & ~3, "arm"
                return
            raise Unsupported("BL prefix without suffix")
        raise Unsupported(f"thumb {hw:04x}")

    def _shift_imm(self, ins: Insn, hw: int, op: int) -> None:
        imm, rm, rd = (hw >> 6) & 31, (hw >> 3) & 7, hw & 7
        x = f"R({rm})"
        if op == 0:
            if imm == 0:
                ins.body = [f"u32 r = {x};", "RC_NZ(r);", f"R({rd}) = r;"]
                return
            v, c = f"({x} << {imm})", f"(({x} >> {32 - imm}) & 1)"
        elif op == 1:
            v, c = (("0u", f"({x} >> 31)") if imm == 0 else (f"({x} >> {imm})", f"(({x} >> {imm - 1}) & 1)"))
        else:
            v, c = ((f"(u32)((s32){x} >> 31)", f"({x} >> 31)") if imm == 0
                    else (f"(u32)((s32){x} >> {imm})", f"(({x} >> {imm - 1}) & 1)"))
        ins.body = [f"u32 sc = {c};", f"u32 r = {v};", "RC_NZ(r);", "RC_FC = sc;", f"R({rd}) = r;"]

    def _alu(self, ins: Insn, hw: int) -> None:
        op, rm, rd = (hw >> 6) & 0xF, (hw >> 3) & 7, hw & 7
        a, b = f"R({rd})", f"R({rm})"
        logical = {0: f"{a} & {b}", 1: f"{a} ^ {b}", 12: f"{a} | {b}", 13: f"{a} * {b}",
                   14: f"{a} & ~{b}", 15: f"~{b}"}
        if op in logical:
            ins.body = [f"u32 r = {logical[op]};", "RC_NZ(r);", f"R({rd}) = r;"]
            return
        if op in (2, 3, 4, 7):
            name = {2: "lsl", 3: "lsr", 4: "asr", 7: "ror"}[op]
            ins.body = [f"u32 x = {a}, s = {b};", f"u32 sc = rc_{name}_rc(x, s, RC_FC);",
                        f"u32 r = rc_{name}_r(x, s);", "RC_NZ(r);", "RC_FC = sc;", f"R({rd}) = r;"]
            return
        if op == 5:
            ins.body = [f"R({rd}) = rc_adds(ctx, {a}, {b}, RC_FC);"]
        elif op == 6:
            ins.body = [f"R({rd}) = rc_subs(ctx, {a}, {b}, RC_FC);"]
        elif op == 8:
            ins.body = [f"u32 r = {a} & {b};", "RC_NZ(r);"]
        elif op == 9:
            ins.body = [f"R({rd}) = rc_subs(ctx, 0, {b}, 1);"]
        elif op == 10:
            ins.body = [f"(void)rc_subs(ctx, {a}, {b}, 1);"]
        else:
            ins.body = [f"(void)rc_adds(ctx, {a}, {b}, 0);"]

    def _hireg(self, ins: Insn, hw: int, pc: int) -> None:
        op, h1 = (hw >> 8) & 3, (hw >> 7) & 1
        rm = (hw >> 3) & 0xF
        rd = (h1 << 3) | (hw & 7)
        src = hexc(pc) if rm == 15 else f"R({rm})"
        if op == 3:
            if hw & 7:
                raise Unpredictable("BX/BLX SBZ")
            if h1:
                if rm == 15:
                    raise Unpredictable("BLX PC")
                ins.kind, ins.pcexpr, ins.body = ICALL, "t_pc", [f"u32 t_pc = R({rm});"]
                return
            if rm == 14:
                ins.kind, ins.pcexpr = RET, "R(14)"
            elif rm == 15:
                ins.kind, ins.target, ins.target_mode = B, pc & ~3, "arm"
            else:
                ins.kind, ins.pcexpr, ins.body = IJUMP, "t_pc", [f"u32 t_pc = R({rm});"]
            return
        if op != 2 and h1 == 0 and rm < 8:
            raise Unpredictable("hi-register ADD/CMP with two low registers")
        if op == 1:
            if rd == 15:
                raise Unpredictable("CMP PC")
            ins.body = [f"(void)rc_subs(ctx, R({rd}), {src}, 1);"]
            return
        dst = hexc(pc) if rd == 15 else f"R({rd})"
        val = f"{dst} + {src}" if op == 0 else src
        if rd == 15:
            if op == 2 and rm == 14:
                ins.kind, ins.pcexpr = RET, "R(14)"
                return
            ins.kind, ins.pcexpr, ins.body = IJUMP, "t_pc", [f"u32 t_pc = ({val}) | 1u;"]
            return
        ins.body = [f"R({rd}) = {val};"]

    def _misc(self, ins: Insn, hw: int) -> None:
        if hw >> 8 == 0b10110000:
            imm = (hw & 0x7F) * 4
            ins.body = [f"R(13) = R(13) {'-' if hw & 0x80 else '+'} {imm};"]
            return
        if hw >> 8 == 0b10110010:
            op, rm, rd = (hw >> 6) & 3, (hw >> 3) & 7, hw & 7
            ext = ["(u32)(s32)(s16)", "(u32)(s32)(s8)", "(u32)(u16)", "(u32)(u8)"][op]
            ins.body = [f"R({rd}) = {ext}R({rm});"]
            return
        if hw >> 9 in (0b1011010, 0b1011110):
            pop, extra, regs = (hw >> 11) & 1, (hw >> 8) & 1, hw & 0xFF
            lst = [r for r in range(8) if regs & (1 << r)]
            if not lst and not extra:
                raise Unpredictable("empty PUSH/POP")
            n = len(lst) + extra
            if pop:
                body = ["u32 a = R(13);"]
                for k, r in enumerate(lst):
                    body.append(f"R({r}) = rd32(ctx, a + {4 * k});")
                if extra:
                    body.append(f"u32 t_pc = rd32(ctx, a + {4 * len(lst)});")
                body.append(f"R(13) = a + {4 * n};")
                ins.body = body
                if extra:
                    ins.kind, ins.pcexpr = RET, "t_pc"
            else:
                body = [f"u32 a = R(13) - {4 * n};"]
                for k, r in enumerate(lst):
                    body.append(f"wr32(ctx, a + {4 * k}, R({r}));")
                if extra:
                    body.append(f"wr32(ctx, a + {4 * len(lst)}, R(14));")
                body.append("R(13) = a;")
                ins.body = body
            return
        if hw >> 8 == 0b10111010:
            op, rm, rd = (hw >> 6) & 3, (hw >> 3) & 7, hw & 7
            if op == 0:
                ins.body = [f"R({rd}) = rc_rev(R({rm}));"]
            elif op == 1:
                ins.body = [f"u32 x = R({rm});", f"R({rd}) = ((x >> 8) & 0x00FF00FFu) | ((x << 8) & 0xFF00FF00u);"]
            elif op == 3:
                ins.body = [f"u32 x = R({rm});",
                            f"R({rd}) = (u32)(s32)(s16)(u16)(((x >> 8) & 0xFF) | ((x & 0xFF) << 8));"]
            else:
                raise Unsupported("REV op 2")
            return
        if hw >> 8 == 0b10111110:
            self._trap(ins, TRAP_BKPT, "bkpt")
            return
        raise Unsupported(f"thumb misc {hw:04x}")

    def _ldm_stm(self, ins: Insn, hw: int) -> None:
        load, rn, regs = (hw >> 11) & 1, (hw >> 8) & 7, hw & 0xFF
        lst = [r for r in range(8) if regs & (1 << r)]
        if not lst:
            raise Unpredictable("empty LDMIA/STMIA")
        body = [f"u32 a = R({rn});"]
        if load:
            for k, r in enumerate(lst):
                body.append(f"R({r}) = rd32(ctx, a + {4 * k});")
            if rn not in lst:
                body.append(f"R({rn}) = a + {4 * len(lst)};")
        else:
            if rn in lst and lst[0] != rn:
                raise Unpredictable("STMIA with base not lowest")
            for k, r in enumerate(lst):
                body.append(f"wr32(ctx, a + {4 * k}, R({r}));")
            body.append(f"R({rn}) = a + {4 * len(lst)};")
        ins.body = body
