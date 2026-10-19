/*
 * 0x00d74e48: initialise/reset a small state struct; always returns 1.
 *
 * Struct layout (guess, >= 0x45 bytes):
 *   0x00 u16 unk00       0x04 u16 unk04      0x06 u16 unk06
 *   0x0C..0x3C u32 unk0C[13]   (0x0C,0x10,...,0x3C all cleared)
 *   0x40 u16 unk40
 *   0x42 u8  unk42 = 0x10
 *   0x43 u8  unk43 = 1
 *   0x44 u8  unk44
 *   (0x02, 0x08..0x0B untouched)
 *
 * difftest: PASS (see report)
 */
#include "types.h"

typedef struct {
    u16 unk00;
    u16 unk02;      /* not touched */
    u16 unk04;
    u16 unk06;
    u32 unk08;      /* not touched */
    u32 unk0C[13];  /* 0x0C..0x3F */
    u16 unk40;
    u8  unk42;
    u8  unk43;
    u8  unk44;
} State_d74e48;

int func_00d74e48(State_d74e48 *s)
{
    int i;

    s->unk42 = 0x10;
    s->unk43 = 1;
    s->unk00 = 0;
    s->unk04 = 0;
    s->unk06 = 0;
    s->unk44 = 0;
    for (i = 0; i < 13; i++) {
        s->unk0C[i] = 0;
    }
    s->unk40 = 0;
    return 1;
}
