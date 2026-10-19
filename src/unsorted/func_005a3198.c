/*
 * 0x005a3198 - map a type id (0..24) plus a sub-kind to a small category code.
 * Original is a 25-entry jump table; the real function spans 0x005a3198..0x005a323c
 * (0xa4 bytes; the listed size 0x40 only covers the dispatch and part of the table).
 * difftest: PASS (see report)
 */
#include "types.h"

static const u8 s_category[25] = {
    2, 0, 0, 2, 5, 5, 4, 4, 4, 5, 4, 5, 5,
    2, 0xff, 2, 0, 2, 5, 2, 5, 2, 2, 2, 2,
};

s32 func_005a3198(u32 type, s32 sub)
{
    if (type >= 25)
        return 0;

    if (type == 14) {
        if (sub == 0)
            return 2;
        if (sub == 1 || sub == 2)
            return 5;
        return 0;
    }

    return s_category[type];
}
