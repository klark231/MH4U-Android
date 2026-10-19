/*
 * 0x0066cf48 - look up a key in a fixed 6-entry {key, value} table; returns value or -1.
 * The first argument (r0) is unused (likely `this` of a method).
 * difftest: PASS (see report)
 *
 * data_00e4fc94: struct { s32 key; s32 value; } [6]
 */
#include "types.h"

typedef struct {
    s32 key;
    s32 value;
} KeyValue;

extern const KeyValue data_00e4fc94[6];

s32 func_0066cf48(void *self, s32 key)
{
    s32 i;

    (void)self;
    for (i = 0; i < 6; i++) {
        if (data_00e4fc94[i].key == key)
            return data_00e4fc94[i].value;
    }
    return -1;
}
