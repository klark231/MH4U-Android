/*
 * 0x00adb688: map a small enum value (0..4) to a constant; anything else gives 8.
 *   Compiled as an unsigned jump table (cmp r0,#5 / ldrlo pc,...).
 *   Note: the real function body is 0x48 bytes; the recorded size 0x34 cuts off
 *   the last two cases (0x00adb6c0 and the default at 0x00adb6c8).
 *
 * difftest: PASS 500/500, args=1, ret=int
 */
#include "types.h"

int func_00adb688(u32 kind)
{
    switch (kind) {
    case 1:  return 14;
    case 2:  return 10;
    case 3:  return 6;
    case 4:  return 9;
    case 0:
    default: return 8;
    }
}
