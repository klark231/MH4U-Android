/*
 * 0x00556598 - PacketAnalysisData::CreateIndex: append an entry (32 max,
 * 0x18 bytes each) and return its index, or -1 when the table is full.
 * difftest: PASS 500/500 (0 skipped), args=2 ret=int ptr_args=[0]
 *
 * Inferred layout:
 *   struct PacketAnalysisData {
 *       struct { u32 key; u8 rest[0x14]; } entry[32];  // 0x000, stride 0x18
 *       u32 count;                                      // 0x300
 *   };
 * In the full case the original reads a debug-logger pointer (0x01081950)
 * and the logging calls are compiled out; nothing observable remains.
 */
#include "types.h"

#define PACKET_ANALYSIS_MAX_ENTRIES 32

typedef struct PacketAnalysisEntry {
    u32 key;
    u8  rest[0x14];
} PacketAnalysisEntry;

typedef struct PacketAnalysisData {
    PacketAnalysisEntry entry[PACKET_ANALYSIS_MAX_ENTRIES];
    u32 count;
} PacketAnalysisData;

s32 func_00556598(PacketAnalysisData *self, u32 key)
{
    u32 index;

    if (self->count >= PACKET_ANALYSIS_MAX_ENTRIES) {
        /* "PacketAnalysisData::CreateIndex() called, but entry is full." */
        return -1;
    }

    index = self->count;
    self->entry[index].key = key;
    self->count = index + 1;
    return (s32)index;
}
