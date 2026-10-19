/*
 * 0x002f8910: returns 1 if an entry is enabled (flag byte == 0) and its 16-bit id is one
 *   of a fixed set {0x41, 0x47, 0x9E, 0x9F, 0xA1, 0xAB, 0xAC}; otherwise 0.
 *   r0 (this/unused) is ignored; r1 points at the entry.
 * Inferred layout of the entry:
 *   struct Entry {
 *       u8  flag;   // +0x00  must be 0
 *       u8  pad;    // +0x01
 *       u16 id;     // +0x02
 *   };
 * difftest: PASS 500/500 (args=2, ret=int, ptr_args=[1])
 */
#include "types.h"

typedef struct {
    u8 flag;
    u8 pad;
    u16 id;
} Entry_002f8910;

int func_002f8910(void *self, const Entry_002f8910 *entry)
{
    (void)self;
    if (entry->flag != 0)
        return 0;

    switch (entry->id) {
    case 0x41:
    case 0x47:
    case 0x9E:
    case 0x9F:
    case 0xA1:
    case 0xAB:
    case 0xAC:
        return 1;
    default:
        return 0;
    }
}
