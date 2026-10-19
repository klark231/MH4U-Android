/*
 * 0x00b991e4: look up a byte value by id in an 18-entry table of 3-byte records.
 *   Returns the record's value byte for the first record whose id equals `id`,
 *   or 0 if none matches. (The original loop is unrolled by two.)
 *
 * data_00ed2440 layout (guess):
 *   struct { u8 id; u8 unk1; u8 value; } [18];   // 3 bytes per entry, 54 bytes total
 *
 * difftest: PASS (see report)
 */
#include "types.h"

typedef struct {
    u8 id;
    u8 unk1;
    u8 value;
} IdByteEntry;

extern const IdByteEntry data_00ed2440[18];

u8 func_00b991e4(u32 id)
{
    int i;

    for (i = 0; i < 18; i++) {
        if (data_00ed2440[i].id == id) {
            return data_00ed2440[i].value;
        }
    }
    return 0;
}
