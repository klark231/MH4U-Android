"""Minimal greedy BLZ encoder used only to generate test fixtures.

Not optimized and not guaranteed safe for in-place decompression on hardware;
do not use it to build real code.bin files.
"""
from __future__ import annotations

import struct

MIN_LEN, MAX_LEN = 3, 18
MIN_DISP, MAX_DISP = 3, 0x1002


def compress(data: bytes, raw_prefix: int = 0) -> bytes:
    prefix, body = data[:raw_prefix], data[raw_prefix:]
    r = body[::-1]  # decoder writes the body back-to-front == r front-to-back
    stream = bytearray()  # in decoder read order (reversed file order)
    i, n = 0, len(r)
    while i < n:
        flag_pos = len(stream)
        stream.append(0)
        flags = 0
        for bit in range(8):
            if i >= n:
                break
            best_len, best_disp = 0, 0
            for disp in range(MIN_DISP, min(MAX_DISP, i) + 1):
                length = 0
                while length < MAX_LEN and i + length < n and r[i + length] == r[i + length - disp]:
                    length += 1
                if length > best_len:
                    best_len, best_disp = length, disp
                    if length == MAX_LEN:
                        break
            if best_len >= MIN_LEN:
                flags |= 0x80 >> bit
                v = ((best_len - 3) << 12) | (best_disp - 3)
                stream += bytes([v >> 8, v & 0xFF])  # hi read first
                i += best_len
            else:
                stream.append(r[i])
                i += 1
        stream[flag_pos] = flags

    region = bytes(stream[::-1])
    pad = (-(len(prefix) + len(region))) % 4
    bottom = 8 + pad
    top = len(region) + bottom
    compressed_size = len(prefix) + len(region) + bottom
    original_bottom = len(data) - compressed_size
    if original_bottom < 0:
        raise ValueError("input does not compress; use more redundant data")
    footer = struct.pack("<II", (bottom << 24) | top, original_bottom)
    return prefix + region + b"\xff" * pad + footer
