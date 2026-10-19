/* CRC-32 helpers.
 *
 * crc32_string @ 0x005bf888 (USA v1.3.0) — difftest: 500/500, pointer args [0]
 * Used by MtDTI::MtDTI to derive class ids: id = crc32_string(name, ~0) & 0x7FFFFFFF.
 */
#include "types.h"

extern const u32 crc32_table[256]; /* 0x00e30340 */

u32 crc32_string(const char *s, u32 crc) {
    for (; *s != '\0'; s++) {
        crc = crc32_table[(crc ^ (u8)*s) & 0xFF] ^ (crc >> 8);
    }
    return crc;
}
