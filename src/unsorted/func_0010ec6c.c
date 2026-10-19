/*
 * 0x0010ec6c: 64-bit arithmetic shift right (RVCT runtime helper, __aeabi_lasr-style).
 *   value in r0:r1 (lo:hi), shift count in r2, result in r0:r1.
 * Mirrors ARM register-shift semantics exactly (only the low byte of the count is
 * used; LSL/LSR by >= 32 give 0, ASR by >= 32 gives the sign fill), so it matches
 * the original even for out-of-range counts.
 * difftest: PASS 500/500 (args=3, ret=i64)
 */
#include "types.h"

static u32 arm_lsr(u32 x, u32 n)
{
    n &= 0xFF;
    return n >= 32 ? 0 : x >> n;
}

static u32 arm_lsl(u32 x, u32 n)
{
    n &= 0xFF;
    return n >= 32 ? 0 : x << n;
}

static s32 arm_asr(s32 x, u32 n)
{
    n &= 0xFF;
    return n >= 32 ? (x >> 31) : (x >> n);
}

s64 func_0010ec6c(u32 lo, s32 hi, u32 shift)
{
    u32 res_lo;
    s32 res_hi;

    if ((s32)(shift - 32) >= 0) {
        /* Whole low word shifted out: low = hi >> (shift - 32), high = sign. */
        res_lo = (u32)arm_asr(hi, shift - 32);
        res_hi = hi >> 31;
    } else {
        res_lo = arm_lsr(lo, shift) | arm_lsl((u32)hi, 32 - shift);
        res_hi = arm_asr(hi, shift);
    }
    return (s64)(((u64)(u32)res_hi << 32) | res_lo);
}
