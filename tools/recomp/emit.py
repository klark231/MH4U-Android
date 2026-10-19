"""C generation for analysed functions (and for single-instruction fuzz functions)."""
from __future__ import annotations

import re

from .insn import (AL, B, BL, COND_NAMES, ICALL, IJUMP, JTAB, OP, RET, SVC, TRAP, Insn, hexc)

# Put at the top of files of whole functions (after recomp.h): registers and flags live in
# local variables of each function, written back to the RCtx around calls, system calls,
# returns and yields (RC_FLUSH/RC_RELOAD, defined per function).
LOCALS_PREAMBLE = """#undef R
#define R(n) rc_r##n
#undef RC_FN
#define RC_FN rc_fn
#undef RC_FZ
#define RC_FZ rc_fz
#undef RC_FC
#define RC_FC rc_fc
#undef RC_FV
#define RC_FV rc_fv
#undef RC_FQ
#define RC_FQ rc_fq
#undef RC_GE
#define RC_GE rc_ge
#undef RC_FLUSH
#undef RC_RELOAD
"""
FLUSH, RELOAD = "RC_FLUSH();", "RC_RELOAD();"
_USES_FLAGS = re.compile(r"\bRC_F[NZCV]\b|\bC_[A-Z]{2}\b|\brc_adds\b|\brc_subs\b|\bRC_NZ\b|\bRC_CPSR\b|\bRC_SET_CPSR\b")
_USES_Q = re.compile(r"\bRC_FQ\b|\brc_q(add|sub)\b|\brc_[su]sat\b|\brc_sat_s32\b|\bRC_CPSR\b|\bRC_SET_CPSR\b")
_USES_GE = re.compile(r"\bRC_GE\b|\brc_parallel\b|\brc_sel\b|\bRC_CPSR\b|\bRC_SET_CPSR\b")


def fname(addr: int) -> str:
    return f"f_{addr:08x}"


def label(addr: int) -> str:
    return f"L_{addr:08x}"


def wrap(ins: Insn, lines: list[str]) -> list[str]:
    """Put `lines` in a scope, guarded by the instruction's condition."""
    if not lines:
        return []
    inner = " ".join(lines)
    if ins.cond == AL:
        return [f"{{ {inner} }}"]
    return [f"if (C_{COND_NAMES[ins.cond]}) {{ {inner} }}"]


def straight_line(ins: Insn) -> list[str]:
    """C for instructions without control flow decisions (OP / SVC / TRAP)."""
    if ins.kind == OP:
        return wrap(ins, ins.body)
    if ins.kind == SVC:
        # next_pc carries the Thumb bit, so the runtime knows the state to resume in
        return wrap(ins, [FLUSH, f"rc_svc(ctx, {hexc(ins.imm)}, {hexc(ins.next | (1 if ins.mode == 'thumb' else 0))});",
                          RELOAD])
    if ins.kind == TRAP:
        return wrap(ins, [FLUSH, f"rc_trap(ctx, {hexc(ins.addr)}, {ins.imm}); return;"])
    raise ValueError(f"not a straight-line instruction: {ins.kind}")


def comment(ins: Insn) -> str:
    w = f"{ins.word:08x}" if ins.size == 4 else f"{ins.word:04x}"
    return f"/* {ins.addr:08x} {w} */"


# ── whole functions ──────────────────────────────────────────────────────

def _lr_value(ins: Insn) -> int:
    return ins.next | 1 if ins.mode == "thumb" else ins.next


class FunctionEmitter:
    """Turns analysed functions into C. `known` maps entry address -> mode for every
    function that will be emitted, so direct calls can be resolved statically."""

    def __init__(self, known: dict[int, str], hooks: dict[int, int] | None = None):
        self.known = known
        self.hooks = hooks or {}        # entry address -> hook point index (rc_hooks[])

    def call(self, target: int, mode: str) -> str:
        if self.known.get(target) == mode:
            return f"{FLUSH} {fname(target)}(ctx); {RELOAD}"
        return f"{FLUSH} rc_call(ctx, {hexc(target | (1 if mode == 'thumb' else 0))}); {RELOAD}"

    def falls_through(self, f, ins: Insn) -> bool:
        if ins.addr in f.fall_tail:
            return False
        k = ins.kind
        if k == BL and ins.addr in f.mret_calls:
            return False
        if k in (OP, SVC, BL, ICALL):
            return True
        if k == B:
            return f.link_calls.get(ins.addr) == ins.next or ins.conditional
        if k in (RET, IJUMP):
            return f.link_calls.get(ins.addr) == ins.next or ins.conditional
        if k == JTAB:
            return ins.conditional
        return False

    def insn(self, f, ins: Insn) -> list[str]:
        k = ins.kind
        if k in (OP, SVC, TRAP):
            out = straight_line(ins)
        elif k == B:
            if ins.addr in f.link_calls:
                ret = f.link_calls[ins.addr]
                out = wrap(ins, [self.call(ins.target, ins.target_mode)] +
                           ([] if ret == ins.next else [f"goto {label(ret)};"]))
            elif ins.addr in f.tails:
                out = wrap(ins, ["RC_FALL_EXPECT();", self.call(ins.target, ins.target_mode), "return;"])
            elif ins.cond == AL:
                out = [f"goto {label(ins.target)};"]
            else:
                out = [f"if (C_{COND_NAMES[ins.cond]}) goto {label(ins.target)};"]
        elif k == BL and ins.addr in f.mret_calls:
            # the callee continues at one of the continuations after its descriptor word,
            # or returns to our caller (see Analyzer.inline_arg_callee)
            cases = " ".join(f"case {hexc(t)}: goto {label(t)};" for t in f.mret_calls[ins.addr])
            out = wrap(ins, [f"R(14) = {hexc(_lr_value(ins))};", self.call(ins.target, ins.target_mode),
                             f"switch (R(15)) {{ {cases} default: RC_RET(R(15)); }}"])
        elif k == BL:
            out = wrap(ins, [f"R(14) = {hexc(_lr_value(ins))};", self.call(ins.target, ins.target_mode)])
        elif k == ICALL:
            out = wrap(ins, ins.body + [f"u32 t_ = {ins.pcexpr};", f"R(14) = {hexc(_lr_value(ins))};", FLUSH,
                                        "RC_ICALL(t_);", RELOAD])
        elif k == RET:
            out = wrap(ins, ins.body + [f"{'RC_RET_TO' if f.mret else 'RC_RET'}({ins.pcexpr});"])
        elif k == IJUMP:
            if ins.addr in f.link_calls:
                ret = f.link_calls[ins.addr]
                tail = [] if ret == ins.next else [f"goto {label(ret)};"]
                out = wrap(ins, ins.body + [f"u32 t_ = {ins.pcexpr};", FLUSH, "rc_call(ctx, t_);", RELOAD] + tail)
            elif ins.addr in f.ijump_targets:
                cases = " ".join(f"case {hexc(t)}: goto {label(t)};" for t in dict.fromkeys(f.ijump_targets[ins.addr]))
                out = wrap(ins, ins.body + [f"u32 t_ = {ins.pcexpr};",
                                            f"switch (t_) {{ {cases} default: if (RC_IS_RETURN(t_)) RC_RET(t_); "
                                            f"RC_FALL_EXPECT(); {FLUSH} rc_call(ctx, t_); return; }}"])
            elif f.mret:
                out = wrap(ins, ins.body + [f"RC_RET_TO({ins.pcexpr});"])
            else:
                # a jump to our own return address is a return (Thumb `pop {r3}; bx r3`); any
                # other is a tail call: the callee returns where we would (it learns where
                # through expect_next, as LR may hold something else by now)
                out = wrap(ins, ins.body + [f"u32 t_ = {ins.pcexpr};", "if (RC_IS_RETURN(t_)) RC_RET(t_);",
                                            "RC_FALL_EXPECT();", FLUSH, "rc_call(ctx, t_);", "return;"])
        elif k == JTAB:
            cases = " ".join(f"case {i}u: goto {label(t)};" for i, t in enumerate(f.jtabs[ins.addr]))
            out = wrap(ins, [f"switch (R({ins.jt_index})) {{ {cases} default: "
                             f"{FLUSH} rc_trap(ctx, {hexc(ins.addr)}, RC_TRAP_BAD_JUMP); return; }}"])
        else:
            raise ValueError(k)
        if ins.addr in f.fall_tail:
            # running into the next function's code: it returns where we would return
            t = f.fall_tail[ins.addr]
            out = out + ["RC_FALL_EXPECT();", self.call(t, f.mode), "return;"]
        return out

    def function(self, f) -> list[str]:
        addrs = sorted(f.insns)
        labels = set(f.labels)
        entry_jump = bool(addrs) and addrs[0] != f.entry
        if entry_jump:
            labels.add(f.entry)        # body has code below the entry point
        # fallthrough across a gap/overlap needs an explicit goto (and a label)
        gotos = {}
        for i, a in enumerate(addrs):
            ins = f.insns[a]
            if self.falls_through(f, ins):
                nxt = addrs[i + 1] if i + 1 < len(addrs) else None
                if nxt != ins.next:
                    gotos[a] = ins.next
                    labels.add(ins.next)
        # basic blocks for cycle accounting
        starts = labels | {f.entry}
        ticks = {}
        cur, n = None, 0
        for i, a in enumerate(addrs):
            if a in starts or cur is None:
                if cur is not None:
                    ticks[cur] = n
                cur, n = a, 0
            n += 1
            ins = f.insns[a]
            # a block ends at any branch: a taken conditional branch skips the rest
            branch = ins.kind in (B, RET, IJUMP, JTAB) and ins.conditional and a not in f.link_calls
            if not self.falls_through(f, ins) or a in gotos or branch:
                ticks[cur] = n
                cur, n = None, 0
        if cur is not None:
            ticks[cur] = n
        out = []
        if entry_jump:
            out.append(f"    goto {label(f.entry)};")
        for a in addrs:
            ins = f.insns[a]
            if a in labels:
                out.append(f"{label(a)}:;")
            if a in ticks:
                out.append(f"    RC_TICK({ticks[a]});")
            code = self.insn(f, ins)
            if code:
                out.append(f"    {comment(ins)} {' '.join(code)}")
            else:
                out.append(f"    {comment(ins)}")
            if a in gotos:
                if gotos[a] in f.insns:
                    out.append(f"    goto {label(gotos[a])};")
                else:
                    out.append(f"    {FLUSH} rc_trap(ctx, {hexc(gotos[a])}, RC_TRAP_BAD_JUMP); return;")
        # labels for targets that aren't instructions of this function (shouldn't happen)
        for t in sorted(labels - set(addrs)):
            out.append(f"{label(t)}: {FLUSH} rc_trap(ctx, {hexc(t)}, RC_TRAP_BAD_JUMP); return;")
        return self._with_locals(f, out, self.hooks.get(f.entry))

    @staticmethod
    def _with_locals(f, body: list[str], hook: int | None = None) -> list[str]:
        """Wrap the body: registers and flags it touches become locals (see LOCALS_PREAMBLE),
        with this function's RC_FLUSH (write back what it may change) and RC_RELOAD. A hook
        point's body becomes static fb_<addr>, entered through a check of rc_hooks[hook]."""
        text = "\n".join(body)
        used = sorted({int(n) for n in re.findall(r"\bR\((\d+)\)", text)})
        written = sorted({int(n) for n in re.findall(r"\bR\((\d+)\) = ", text)})
        flags = []
        if _USES_FLAGS.search(text):
            flags += ["fn", "fz", "fc", "fv"]
        if _USES_Q.search(text):
            flags.append("fq")
        if _USES_GE.search(text):
            flags.append("ge")
        flush = [f"ctx->r[{n}] = rc_r{n}" for n in written] + [f"ctx->{x} = rc_{x}" for x in flags]
        reload = [f"rc_r{n} = ctx->r[{n}]" for n in used] + [f"rc_{x} = ctx->{x}" for x in flags]
        out = [f"#define RC_FLUSH() ({', '.join(flush) if flush else '(void)0'})",
               f"#define RC_RELOAD() ({', '.join(reload) if reload else '(void)0'})",
               (f"static void fb_{f.entry:08x}(RCtx *ctx) {{" if hook is not None
                else f"void {fname(f.entry)}(RCtx *ctx) {{"), f"    RC_ENTRY(); RC_COUNT({hexc(f.entry)});"]
        if used:
            out.append("    u32 " + ", ".join(f"rc_r{n} = ctx->r[{n}]" for n in used) + ";")
        if flags:
            out.append("    u32 " + ", ".join(f"rc_{x} = ctx->{x}" for x in flags) + ";")
        out += body
        out += ["}", "#undef RC_FLUSH", "#undef RC_RELOAD"]
        if hook is not None:
            out.append(f"void {fname(f.entry)}(RCtx *ctx) {{ RC_HOOK_POINT({hook}, fb_{f.entry:08x}); }}")
        return out
