/*
 * 0x00683568: look up an entry in a fixed 10-entry table by its two key bytes.
 *   r0 is ignored (likely an unused `this`); r1/r2 are the keys compared against
 *   the first two bytes of each 0x50-byte record. Returns the matching record or NULL.
 *
 * Inferred layout of g_010b4af8 (10 records, 0x50 bytes each):
 *   +0x00 u8 key0
 *   +0x01 u8 key1
 *   +0x02 ... (0x4E bytes, not touched here)
 *
 * difftest: PASS 500/500 (and 3000/3000, seed 7), args=3, ret=int
 * Note: the table lives in .bss (runtime-filled), so under difftest every record
 * is zero and only (key0,key1)==(0,0) exercises the match path.
 */
#include "types.h"

typedef struct {
    u8 key0;
    u8 key1;
    u8 rest[0x4E];
} Entry_010b4af8;

extern Entry_010b4af8 g_010b4af8[10];

Entry_010b4af8 *func_00683568(void *self, u32 key0, u32 key1)
{
    u8 i;

    (void)self;
    for (i = 0; i < 10; i++) {
        Entry_010b4af8 *e = &g_010b4af8[i];
        if (e->key0 == key0 && e->key1 == key1)
            return e;
    }
    return NULL;
}
