"""Static recompiler: ARM11 (ARMv6K + VFPv2, ARM and Thumb-1) machine code -> C.

Modules:
  insn      shared instruction record
  arm       ARM-state decoder/translator (incl. VFP)
  thumb     Thumb-state decoder/translator
  analysis  per-function control-flow discovery, jump tables, function closure
  emit      C source generation
The C runtime contract is in runtime/recomp.h.
"""
