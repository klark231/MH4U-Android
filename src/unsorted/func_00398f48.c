/*
 * 0x00398f48 - look up an entry through a u16 remap table, returning 0 when
 * the slot is out of range, unmapped (0xFFFF) or past the entry count.
 * difftest: PASS 411/411 (89 skipped), args=2 ret=int ptr_args=[0]
 *
 * Inferred layout (only the fields touched here):
 *   struct Obj {
 *       u8    pad00[0x70];
 *       u32   entryCount;   // 0x70
 *       u8    pad74[0x08];
 *       u32  *entries;      // 0x7C
 *       u16  *remap;        // 0x80  (0xFFFF = unused slot)
 *       u16   remapCount;   // 0x84
 *   };
 */
#include "types.h"

typedef struct Obj_00398f48 {
    u8   pad00[0x70];
    u32  entryCount;
    u8   pad74[0x08];
    u32 *entries;
    u16 *remap;
    u16  remapCount;
} Obj_00398f48;

u32 func_00398f48(Obj_00398f48 *obj, u32 slot)
{
    u16 index;

    if (slot >= obj->remapCount)
        return 0;

    index = obj->remap[slot];
    if (index == 0xFFFF)
        return 0;

    if (obj->entryCount != 0 && index < obj->entryCount)
        return obj->entries[index];

    return 0;
}
