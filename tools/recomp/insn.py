"""Instruction record shared by the ARM and Thumb translators."""
from __future__ import annotations

from dataclasses import dataclass, field

COND_NAMES = ["EQ", "NE", "CS", "CC", "MI", "PL", "VS", "VC", "HI", "LS", "GE", "LT", "GT", "LE", "AL"]
AL = 14

# kinds
OP = "op"          # straight-line code in `body`
B = "b"            # direct jump to `target` (goto or tail call, decided by analysis)
BL = "bl"          # direct call to `target`
RET = "ret"        # return; `body` may compute `pcexpr`
IJUMP = "ijump"    # computed jump to `pcexpr`
ICALL = "icall"    # computed call to `pcexpr` (BLX Rm); LR is set by the emitter
JTAB = "jtab"      # jump table: `jt_kind` ("addpc"/"ldrpc"), `jt_index` register, `jt_base`
SVC = "svc"        # system call `imm`
TRAP = "trap"      # undefined / unsupported / unpredictable / breakpoint


class Unsupported(Exception):
    """Raised by decoders for encodings we don't translate (becomes a TRAP)."""


class Unpredictable(Unsupported):
    """Architecturally UNPREDICTABLE encodings; compilers never emit these."""


@dataclass
class Insn:
    addr: int
    size: int
    word: int
    mode: str                     # "arm" | "thumb"
    kind: str = OP
    cond: int = AL
    body: list[str] = field(default_factory=list)
    target: int | None = None     # B/BL
    target_mode: str | None = None
    pcexpr: str | None = None     # RET/IJUMP/ICALL
    imm: int = 0                  # SVC number / trap code
    jt_kind: str | None = None
    jt_index: int | None = None
    jt_base: int | None = None
    trap_reason: str = ""
    literal: int | None = None    # word loaded from a PC-relative literal pool
    text: str = ""

    @property
    def next(self) -> int:
        return self.addr + self.size

    @property
    def conditional(self) -> bool:
        return self.cond != AL


def hexc(v: int) -> str:
    return f"0x{v & 0xFFFFFFFF:08x}u"


def ror32(x: int, n: int) -> int:
    n &= 31
    x &= 0xFFFFFFFF
    return ((x >> n) | (x << (32 - n))) & 0xFFFFFFFF if n else x


def sext(v: int, bits: int) -> int:
    v &= (1 << bits) - 1
    return v - (1 << bits) if v & (1 << (bits - 1)) else v
