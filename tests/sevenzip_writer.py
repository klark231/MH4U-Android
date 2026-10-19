"""A small .7z writer for test fixtures (tools/sevenzip.py reads them): the format as 7-Zip
writes it (DOC/7zFormat.txt), with LZMA2, LZMA, BCJ + LZMA, Delta + LZMA2, Deflate, BZip2 or
Copy, solid or a folder per file, and an optional compressed ("encoded") header. Only for
synthetic data in tests; not a general archiver."""
from __future__ import annotations

import bz2
import lzma
import pathlib
import struct
import zlib

SIGNATURE = b"7z\xbc\xaf\x27\x1c"
ATTR_DIR = 0x10
ATTR_UNIX = 0x8000


def number(n: int) -> bytes:
    """7z's variable-length number."""
    for i in range(8):
        if n < (1 << (8 * i + 7 - i)):
            first = ((0xFF << (8 - i)) & 0xFF) | (n >> (8 * i))
            return bytes([first]) + (n & ((1 << (8 * i)) - 1)).to_bytes(i, "little")
    return b"\xff" + n.to_bytes(8, "little")


def bits(values: list[bool]) -> bytes:
    out = bytearray()
    for i in range(0, len(values), 8):
        b = 0
        for j, v in enumerate(values[i:i + 8]):
            if v:
                b |= 0x80 >> j
        out.append(b)
    return bytes(out)


def _lzma2_prop(dict_size: int) -> int:
    for d in range(40):
        if (2 | (d & 1)) << (d // 2 + 11) >= dict_size:
            return d
    return 40


DICT = 1 << 20
LZMA1 = {"id": lzma.FILTER_LZMA1, "dict_size": DICT, "lc": 3, "lp": 0, "pb": 2}
LZMA1_PROPS = bytes([(2 * 5 + 0) * 9 + 3]) + struct.pack("<I", DICT)


def compress(data: bytes, method: str) -> tuple[bytes, list[tuple[bytes, bytes]]]:
    """(packed bytes, coders as (method id, properties) from the output's side to the packed side)."""
    if method == "copy":
        return data, [(b"\x00", b"")]
    if method == "lzma2":
        packed = lzma.compress(data, format=lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_LZMA2, "dict_size": DICT}])
        return packed, [(b"\x21", bytes([_lzma2_prop(DICT)]))]
    if method == "lzma":
        return lzma.compress(data, format=lzma.FORMAT_RAW, filters=[LZMA1]), [(b"\x03\x01\x01", LZMA1_PROPS)]
    if method == "bcj+lzma":
        packed = lzma.compress(data, format=lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_X86}, LZMA1])
        return packed, [(b"\x03\x03\x01\x03", b""), (b"\x03\x01\x01", LZMA1_PROPS)]
    if method == "delta+lzma2":
        packed = lzma.compress(data, format=lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_DELTA, "dist": 4},
                                                                      {"id": lzma.FILTER_LZMA2, "dict_size": DICT}])
        return packed, [(b"\x03", bytes([3])), (b"\x21", bytes([_lzma2_prop(DICT)]))]
    if method == "deflate":
        c = zlib.compressobj(6, zlib.DEFLATED, -15)
        return c.compress(data) + c.flush(), [(b"\x04\x01\x08", b"")]
    if method == "bzip2":
        return bz2.compress(data), [(b"\x04\x02\x02", b"")]
    if method == "aes":         # not encrypted at all: only its method id, which the reader must refuse
        return data, [(b"\x06\xf1\x07\x01", b"\x00")]
    raise ValueError(method)


def _folder(coders: list[tuple[bytes, bytes]]) -> bytes:
    out = bytearray(number(len(coders)))
    for method, props in coders:
        flag = len(method) | (0x20 if props else 0)
        out.append(flag)
        out += method
        if props:
            out += number(len(props)) + props
    for i in range(len(coders) - 1):        # coder i reads what coder i+1 writes
        out += number(i) + number(i + 1)    # (in index, out index)
    return bytes(out)


def _streams_info(pack_pos: int, packs: list[bytes], folders: list[tuple[list, int]],
                  substreams: list[list[bytes]] | None) -> bytes:
    out = bytearray(b"\x06" + number(pack_pos) + number(len(packs)) + b"\x09")
    for p in packs:
        out += number(len(p))
    out += b"\x00"
    out += b"\x07\x0b" + number(len(folders)) + b"\x00"
    for f in folders:
        out += _folder(f[0])
    out += b"\x0c"
    for f in folders:
        out += number(f[1]) * len(f[0])
    if substreams is None:                  # an encoded header: the folder's CRC
        out += b"\x0a\x01" + struct.pack("<I", folders[0][2])
    out += b"\x00"
    if substreams is not None:
        out += b"\x08\x0d"
        for files in substreams:
            out += number(len(files))
        out += b"\x09"
        for files in substreams:
            for f in files[:-1]:
                out += number(len(f))
        out += b"\x0a\x01"
        for files in substreams:
            for f in files:
                out += struct.pack("<I", zlib.crc32(f))
        out += b"\x00"
    out += b"\x00"
    return bytes(out)


def make_7z(path, entries, method: str = "lzma2", solid: bool = True, encode_header: bool = False,
            corrupt_crc: bool = False):
    """entries: [(name, bytes)] for files, (name, None) for folders, (name, bytes, attributes)
    to give attributes (a unix symbolic link: ATTR_UNIX | (0o120777 << 16))."""
    names, datas, attrs = [], [], []
    for e in entries:
        names.append(e[0])
        datas.append(e[1])
        attrs.append(e[2] if len(e) > 2 else (ATTR_DIR if e[1] is None else 0x20))
    with_data = [i for i, d in enumerate(datas) if d]
    groups = [with_data] if solid else [[i] for i in with_data]
    groups = [g for g in groups if g]
    packs, folders, substreams = [], [], []
    for g in groups:
        blob = b"".join(datas[i] for i in g)
        packed, coders = compress(blob, method)
        packs.append(packed)
        folders.append((coders, len(blob)))
        substreams.append([datas[i] for i in g])
    if corrupt_crc and substreams:
        substreams[0][0] = substreams[0][0] + b"!"   # its CRC won't match what unpacks
    header = bytearray(b"\x01")
    if packs:
        header += b"\x04" + _streams_info(0, packs, folders, substreams)
    header += b"\x05" + number(len(names))
    empty = [not d for d in datas]
    if any(empty):
        body = bits(empty)
        header += b"\x0e" + number(len(body)) + body
        empty_file = [datas[i] is not None for i in range(len(datas)) if empty[i]]
        body = bits(empty_file)
        header += b"\x0f" + number(len(body)) + body
    body = b"\x00" + b"".join(n.encode("utf-16-le") + b"\0\0" for n in names)
    header += b"\x11" + number(len(body)) + body
    body = b"\x01\x00" + b"".join(struct.pack("<I", a) for a in attrs)
    header += b"\x15" + number(len(body)) + body
    header += b"\x00\x00"
    data = b"".join(packs)
    header = bytes(header)
    if encode_header:
        packed = lzma.compress(header, format=lzma.FORMAT_RAW, filters=[LZMA1])
        coders = [(b"\x03\x01\x01", LZMA1_PROPS)]
        header = b"\x17" + _streams_info(len(data), [packed], [(coders, len(header), zlib.crc32(header))], None)
        data += packed
    start = struct.pack("<QQI", len(data), len(header), zlib.crc32(header))
    blob = SIGNATURE + b"\x00\x04" + struct.pack("<I", zlib.crc32(start)) + start + data + header
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)
    return path
