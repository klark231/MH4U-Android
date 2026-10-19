#!/usr/bin/env python3
"""BLZ (backward LZ77) decompression, as used for 3DS ExeFS `.code`.

Format (all little-endian), stored at the very end of the compressed buffer:

    u32 buffer_top_and_bottom   # bits 0-23: "top"  = length of the compressed region,
                                #            measured from the end of the buffer
                                # bits 24-31: "bottom" = length of the footer area
                                #            (8 bytes + up to 3 bytes of padding)
    u32 original_bottom         # extra bytes: decompressed_size - compressed_size

Bytes before the compressed region are stored uncompressed. Decompression runs
backwards from the end of the buffer: a flag byte supplies 8 bits (MSB first);
0 = copy one literal byte, 1 = back-reference of 2 bytes:
    size = (hi >> 4) + 3, disp = (((hi & 0xF) << 8) | lo) + 3
which copies `size` bytes from `dst + disp` (already-written, higher addresses).
"""
from __future__ import annotations

import struct
import sys

FOOTER_SIZE = 8


class BLZError(ValueError):
    pass


def is_probably_compressed(data: bytes) -> bool:
    """Heuristic check of the footer; the exheader flag is the authoritative source."""
    if len(data) < FOOTER_SIZE:
        return False
    top_bottom, _ = struct.unpack_from("<II", data, len(data) - FOOTER_SIZE)
    top, bottom = top_bottom & 0xFFFFFF, top_bottom >> 24
    return FOOTER_SIZE <= bottom <= FOOTER_SIZE + 3 and bottom <= top <= len(data)


def decompress(data: bytes) -> bytes:
    size = len(data)
    if size < FOOTER_SIZE:
        raise BLZError("buffer too small for a BLZ footer")

    top_bottom, original_bottom = struct.unpack_from("<II", data, size - FOOTER_SIZE)
    top = top_bottom & 0xFFFFFF
    bottom = top_bottom >> 24
    if not (FOOTER_SIZE <= bottom <= FOOTER_SIZE + 3) or top < bottom or top > size:
        raise BLZError(f"invalid BLZ footer (top=0x{top:X}, bottom=0x{bottom:X}, size=0x{size:X})")

    out_size = size + original_bottom
    out = bytearray(out_size)
    out[:size] = data

    src = size - bottom   # read cursor (exclusive), moves down
    dst = out_size        # write cursor (exclusive), moves down
    end = size - top      # compressed region starts here

    while src > end:
        src -= 1
        flags = data[src]
        for _ in range(8):
            if flags & 0x80 == 0:
                if src - end < 1 or dst - end < 1:
                    raise BLZError("literal runs past start of compressed region")
                src -= 1
                dst -= 1
                out[dst] = data[src]
            else:
                if src - end < 2:
                    raise BLZError("back-reference runs past start of compressed region")
                src -= 2
                hi, lo = data[src + 1], data[src]
                length = (hi >> 4) + 3
                disp = (((hi & 0x0F) << 8) | lo) + 3
                if dst - end < length:
                    raise BLZError("back-reference would write below compressed region")
                if dst + disp > out_size:
                    raise BLZError("back-reference points past end of output")
                for _ in range(length):
                    dst -= 1
                    out[dst] = out[dst + disp]
            flags = (flags << 1) & 0xFF
            if src <= end:
                break

    if dst != src:
        # Well-formed data meets the untouched raw prefix exactly. Reference
        # implementations don't treat this as fatal, so only warn.
        print(f"blz: warning: decompression ended misaligned (src=0x{src:X}, dst=0x{dst:X})",
              file=sys.stderr)
    return bytes(out)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"usage: {argv[0]} <compressed code.bin> <output>", file=sys.stderr)
        return 2
    with open(argv[1], "rb") as f:
        data = f.read()
    result = decompress(data)
    with open(argv[2], "wb") as f:
        f.write(result)
    print(f"0x{len(data):X} -> 0x{len(result):X} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
