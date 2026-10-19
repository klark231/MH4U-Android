"""Reference (Unicorn, ARM11 MPCore) and native (recompiled C) machines with one state model.

Both sides start from the same `State` and the same memory contents; after running they
report a `Result` (status + final state) that can be compared field by field.
"""
from __future__ import annotations

import ctypes
import os
import struct
from dataclasses import dataclass, field

from unicorn import (UC_ARCH_ARM, UC_HOOK_INTR, UC_HOOK_MEM_INVALID, UC_MODE_ARM, UC_PROT_ALL,
                     UC_PROT_EXEC, UC_PROT_READ, UC_PROT_WRITE, Uc, UcError)
from unicorn import arm_const as A

ST_RETURN, ST_SVC, ST_FAULT, ST_BUDGET, ST_TRAP, ST_BADRET, ST_NOFUNC = range(7)
ST_UNDEF = 10        # Unicorn: undefined instruction
ST_OTHER = 11
STATUS_NAMES = {ST_RETURN: "return", ST_SVC: "svc", ST_FAULT: "fault", ST_BUDGET: "budget",
                ST_TRAP: "trap", ST_BADRET: "bad-return", ST_NOFUNC: "no-func", ST_UNDEF: "undef",
                ST_OTHER: "other"}
RET_TRAP = 0x0E000000
PAGE = 0x1000

REG_IDS = [A.UC_ARM_REG_R0 + i for i in range(13)] + [A.UC_ARM_REG_SP, A.UC_ARM_REG_LR, A.UC_ARM_REG_PC]


@dataclass
class State:
    r: list[int] = field(default_factory=lambda: [0] * 16)
    n: int = 0
    z: int = 0
    c: int = 0
    v: int = 0
    q: int = 0
    ge: int = 0
    s: list[int] = field(default_factory=lambda: [0] * 32)
    fpscr: int = 0
    tls: int = 0

    def copy(self) -> "State":
        return State(list(self.r), self.n, self.z, self.c, self.v, self.q, self.ge, list(self.s),
                     self.fpscr, self.tls)


@dataclass
class Result:
    status: int
    state: State
    info0: int = 0           # svc number / fault address / trap pc
    info1: int = 0


@dataclass
class Region:
    addr: int
    size: int
    writable: bool
    data: bytes = b""        # initial contents (padded with zeros)


# ── Unicorn reference ────────────────────────────────────────────────────

class RefMachine:
    def __init__(self, regions: list[Region]):
        mu = self.mu = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        mu.ctl_set_cpu_model(A.UC_CPU_ARM_11MPCORE)
        self.regions = regions
        for rg in regions:
            prot = UC_PROT_READ | UC_PROT_EXEC | (UC_PROT_WRITE if rg.writable else 0)
            mu.mem_map(rg.addr, rg.size, prot)
            if rg.data:
                mu.mem_write(rg.addr, rg.data)
        mu.mem_map(RET_TRAP, PAGE, UC_PROT_ALL)
        mu.reg_write(A.UC_ARM_REG_C1_C0_2, mu.reg_read(A.UC_ARM_REG_C1_C0_2) | (0xF << 20))
        mu.reg_write(A.UC_ARM_REG_FPEXC, 0x40000000)
        self.cpsr_misc = mu.reg_read(A.UC_ARM_REG_CPSR) & 0x07F0FFFF & ~0x20
        mu.hook_add(UC_HOOK_MEM_INVALID, self._on_invalid)
        mu.hook_add(UC_HOOK_INTR, self._on_intr)
        self._fault = None
        self._svc = None
        # stub used to clear the exclusive monitor between runs: clrex; (stop)
        self.stub = RET_TRAP + 0x100
        mu.mem_write(self.stub, struct.pack("<I", 0xF57FF01F))

    def _on_invalid(self, uc, access, addr, size, value, data):
        if self._fault is None:
            self._fault = addr
        return False

    def _on_intr(self, uc, intno, data):
        if intno == 2:
            pc = uc.reg_read(A.UC_ARM_REG_PC)
            thumb = uc.reg_read(A.UC_ARM_REG_CPSR) & 0x20
            if thumb:
                imm = struct.unpack("<H", bytes(uc.mem_read(pc - 2, 2)))[0] & 0xFF
            else:
                imm = struct.unpack("<I", bytes(uc.mem_read(pc - 4, 4)))[0] & 0xFFFFFF
            self._svc = (imm, pc)
        else:
            self._svc = (-intno, uc.reg_read(A.UC_ARM_REG_PC))
        uc.emu_stop()

    def write(self, addr: int, data: bytes) -> None:
        self.mu.mem_write(addr, data)

    def read(self, addr: int, size: int) -> bytes:
        return bytes(self.mu.mem_read(addr, size))

    def set_state(self, st: State) -> None:
        mu = self.mu
        for i, rid in enumerate(REG_IDS[:15]):
            mu.reg_write(rid, st.r[i] & 0xFFFFFFFF)
        cpsr = mu.reg_read(A.UC_ARM_REG_CPSR)
        cpsr &= ~(0xF8000000 | 0x000F0000 | 0x20)
        cpsr |= (st.n << 31) | (st.z << 30) | (st.c << 29) | (st.v << 28) | (st.q << 27) | (st.ge << 16)
        mu.reg_write(A.UC_ARM_REG_CPSR, cpsr)
        for i in range(32):
            mu.reg_write(A.UC_ARM_REG_S0 + i, st.s[i])
        mu.reg_write(A.UC_ARM_REG_FPSCR, st.fpscr)
        mu.reg_write(A.UC_ARM_REG_C13_C0_3, st.tls)

    def get_state(self) -> State:
        mu = self.mu
        st = State()
        for i, rid in enumerate(REG_IDS):
            st.r[i] = mu.reg_read(rid)
        cpsr = mu.reg_read(A.UC_ARM_REG_CPSR)
        st.n, st.z, st.c, st.v, st.q = (cpsr >> 31) & 1, (cpsr >> 30) & 1, (cpsr >> 29) & 1, (cpsr >> 28) & 1, (cpsr >> 27) & 1
        st.ge = (cpsr >> 16) & 0xF
        st.s = [mu.reg_read(A.UC_ARM_REG_S0 + i) for i in range(32)]
        st.fpscr = mu.reg_read(A.UC_ARM_REG_FPSCR)
        st.tls = mu.reg_read(A.UC_ARM_REG_C13_C0_3)
        return st

    def clear_exclusive(self) -> None:
        self.mu.mem_write(self.stub, struct.pack("<I", 0xF57FF01F))
        try:
            self.mu.emu_start(self.stub, self.stub + 4)
        except UcError:
            pass

    def run(self, entry: int, thumb: bool, until: int = RET_TRAP, count: int = 0) -> Result:
        self._fault = None
        self._svc = None
        mu = self.mu
        try:
            mu.emu_start(entry | (1 if thumb else 0), until, count=count)
            err = None
        except UcError as e:
            err = e
        st = self.get_state()
        if self._svc is not None:
            imm, pc = self._svc
            if imm < 0:
                return Result(ST_OTHER, st, -imm, pc)
            return Result(ST_SVC, st, imm, pc)
        if err is not None:
            if self._fault is not None:
                return Result(ST_FAULT, st, self._fault)
            if err.errno == 10:      # UC_ERR_INSN_INVALID
                return Result(ST_UNDEF, st, st.r[15])
            return Result(ST_OTHER, st, err.errno)
        if (st.r[15] & ~1) != (until & ~1):
            return Result(ST_BUDGET, st)
        return Result(ST_RETURN, st)


# ── native (recompiled) machine ──────────────────────────────────────────

class CRCtx(ctypes.Structure):
    _fields_ = [("r", ctypes.c_uint32 * 16),
                ("fn", ctypes.c_uint32), ("fz", ctypes.c_uint32), ("fc", ctypes.c_uint32), ("fv", ctypes.c_uint32),
                ("fq", ctypes.c_uint32), ("ge", ctypes.c_uint32), ("cpsr_misc", ctypes.c_uint32),
                ("fpscr", ctypes.c_uint32),
                ("f", ctypes.c_uint32 * 32),
                ("tpidrurw", ctypes.c_uint32), ("tpidruro", ctypes.c_uint32),
                ("excl_addr", ctypes.c_uint32), ("excl_valid", ctypes.c_uint32),
                ("budget", ctypes.c_int64),
                ("mem", ctypes.c_void_p), ("pages", ctypes.c_void_p), ("user", ctypes.c_void_p),
                ("expect_next", ctypes.c_uint32)]


class NativeMachine:
    def __init__(self, lib_path: str, regions: list[Region], cpsr_misc: int = 0x10):
        lib = self.lib = ctypes.CDLL(os.path.abspath(lib_path))
        lib.rh_init.restype = ctypes.c_int
        lib.rh_map.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_int]
        lib.rh_write.argtypes = [ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32]
        lib.rh_read.argtypes = [ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32]
        lib.rh_call.argtypes = [ctypes.c_uint32, ctypes.POINTER(CRCtx)]
        lib.rh_call.restype = ctypes.c_int
        for f in ("rh_info0", "rh_info1", "rh_fault_addr", "rh_func_count"):
            getattr(lib, f).restype = ctypes.c_uint32
        if lib.rh_init() != 0:
            raise RuntimeError("could not reserve guest address space")
        self.regions = regions
        lib.rh_map(RET_TRAP, PAGE, 3)          # mirror the reference machine's trap page
        for rg in regions:
            lib.rh_map(rg.addr, rg.size, 3)
            if rg.data:
                lib.rh_write(rg.addr, rg.data, len(rg.data))
            if not rg.writable:
                lib.rh_map(rg.addr, rg.size, 1)
        self.ctx = CRCtx()
        self.cpsr_misc = cpsr_misc

    def functions(self) -> list[tuple[int, int]]:
        out = []
        a, m = ctypes.c_uint32(), ctypes.c_uint32()
        for i in range(self.lib.rh_func_count()):
            self.lib.rh_func_at(i, ctypes.byref(a), ctypes.byref(m))
            out.append((a.value, m.value))
        return out

    def write(self, addr: int, data: bytes) -> None:
        self.lib.rh_write(addr, data, len(data))

    def read(self, addr: int, size: int) -> bytes:
        buf = ctypes.create_string_buffer(size)
        self.lib.rh_read(addr, buf, size)
        return buf.raw

    def run(self, entry: int, thumb: bool, st: State, budget: int = 1 << 40) -> Result:
        c = self.ctx
        for i in range(16):
            c.r[i] = st.r[i] & 0xFFFFFFFF
        c.fn, c.fz, c.fc, c.fv, c.fq, c.ge = st.n, st.z, st.c, st.v, st.q, st.ge
        c.cpsr_misc = self.cpsr_misc
        c.fpscr = st.fpscr
        for i in range(32):
            c.f[i] = st.s[i]
        c.tpidruro = st.tls
        c.excl_valid = 0
        c.expect_next = 0       # a run that stopped inside a tail call leaves it set
        c.budget = budget
        status = self.lib.rh_call(entry | (1 if thumb else 0), ctypes.byref(c))
        out = State(list(c.r), c.fn, c.fz, c.fc, c.fv, c.fq, c.ge, list(c.f), c.fpscr, c.tpidruro)
        if status == ST_FAULT:
            return Result(status, out, self.lib.rh_fault_addr(), self.lib.rh_info0())
        return Result(status, out, self.lib.rh_info0(), self.lib.rh_info1())


def diff_states(a: State, b: State, compare_pc: bool = False) -> list[str]:
    out = []
    for i in range(16 if compare_pc else 15):
        if a.r[i] != b.r[i]:
            out.append(f"r{i}: {a.r[i]:08x} vs {b.r[i]:08x}")
    for name in ("n", "z", "c", "v", "q", "ge"):
        if getattr(a, name) != getattr(b, name):
            out.append(f"{name}: {getattr(a, name)} vs {getattr(b, name)}")
    for i in range(32):
        if a.s[i] != b.s[i]:
            out.append(f"s{i}: {a.s[i]:08x} vs {b.s[i]:08x}")
    # cumulative exception flags (IDC, IXC, UFC, OFC, DZC, IOC) are not modelled
    if (a.fpscr ^ b.fpscr) & ~0x9F:
        out.append(f"fpscr: {a.fpscr:08x} vs {b.fpscr:08x}")
    return out
