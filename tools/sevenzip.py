#!/usr/bin/env python3
"""Reads .7z archives with Python's standard library alone (lzma, zlib, bz2), for the mod
manager's downloads: texture packs for 3DS emulators are mostly shared as .7z.

It handles what 7-Zip writes by default and the usual variants: LZMA2 and LZMA (with the
BCJ/ARM/... branch filters and Delta before them), Deflate, BZip2 and Copy, solid or not, with
a compressed ("encoded") header. It refuses, with a message that says to unpack the archive
another way: encryption (AES), BCJ2, PPMd, Deflate64, Zstandard and the like, and archives
in several volumes. Format: 7-Zip's DOC/7zFormat.txt.

    python3 tools/sevenzip.py list ARCHIVE.7z
    python3 tools/sevenzip.py test ARCHIVE.7z         # decompress everything, check the CRCs

The archive is read through any seekable binary file, so a reader over HTTP ranges works too
(only the end of the archive is needed to list it).
"""
from __future__ import annotations

import bz2
import lzma
import struct
import sys
import zlib
from dataclasses import dataclass, field

SIGNATURE = b"7z\xbc\xaf\x27\x1c"

# property ids (7zFormat.txt)
K_END, K_HEADER, K_ARCHIVE_PROPERTIES, K_ADDITIONAL_STREAMS, K_MAIN_STREAMS = 0x00, 0x01, 0x02, 0x03, 0x04
K_FILES_INFO, K_PACK_INFO, K_UNPACK_INFO, K_SUBSTREAMS_INFO, K_SIZE, K_CRC = 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A
K_FOLDER, K_CODERS_UNPACK_SIZE, K_NUM_UNPACK_STREAM = 0x0B, 0x0C, 0x0D
K_EMPTY_STREAM, K_EMPTY_FILE, K_ANTI, K_NAME, K_WIN_ATTRIBUTES = 0x0E, 0x0F, 0x10, 0x11, 0x15
K_ENCODED_HEADER, K_DUMMY = 0x17, 0x19

# coders: id -> name
COPY, LZMA1, LZMA2, DEFLATE, BZIP2, DELTA = "copy", "lzma", "lzma2", "deflate", "bzip2", "delta"
_BRANCH = {"x86": lzma.FILTER_X86, "ppc": lzma.FILTER_POWERPC, "ia64": lzma.FILTER_IA64,
           "arm": lzma.FILTER_ARM, "armt": lzma.FILTER_ARMTHUMB, "sparc": lzma.FILTER_SPARC}
METHODS = {
    b"\x00": COPY, b"\x21": LZMA2, b"\x03\x01\x01": LZMA1, b"\x04\x01\x08": DEFLATE, b"\x04\x02\x02": BZIP2,
    b"\x03": DELTA,
    b"\x03\x03\x01\x03": "x86", b"\x03\x03\x02\x05": "ppc", b"\x03\x03\x04\x01": "ia64",
    b"\x03\x03\x05\x01": "arm", b"\x03\x03\x07\x01": "armt", b"\x03\x03\x08\x05": "sparc",
    b"\x04": "x86", b"\x05": "ppc", b"\x06": "ia64", b"\x07": "arm", b"\x08": "armt", b"\x09": "sparc",
}
# what 7-Zip may write that this can't read, by name (for the message)
UNSUPPORTED = {b"\x03\x03\x01\x1b": "BCJ2", b"\x03\x04\x01": "PPMd", b"\x06\xf1\x07\x01": "AES (a password)",
               b"\x04\x01\x09": "Deflate64", b"\x04\xf7\x11\x01": "Zstandard", b"\x0a": "ARM64",
               b"\x0b": "RISC-V", b"\x04\xf7\x11\x04": "LZ4", b"\x04\xf7\x11\x02": "Brotli"}

CHUNK = 1 << 20
MAX_HEADER = 256 << 20     # a header bigger than this is refused (a broken or hostile archive)
MAX_FILES = 2_000_000


class Error(Exception):
    """The archive can't be read (code: "format", "unsupported", "crc", "truncated")."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class Coder:
    method: bytes
    num_in: int
    num_out: int
    props: bytes

    @property
    def name(self) -> str:
        return METHODS.get(self.method) or UNSUPPORTED.get(self.method) or f"method {self.method.hex()}"


@dataclass
class Folder:
    coders: list[Coder]
    bind_pairs: list[tuple[int, int]]   # (in index, out index)
    packed: list[int]                   # in-stream indexes fed by pack streams
    unpack_sizes: list[int] = field(default_factory=list)  # per out stream
    crc: int | None = None
    first_pack: int = 0                 # its first pack stream
    num_substreams: int = 1
    sizes: list[int] = field(default_factory=list)  # its substreams'
    crcs: list[int | None] = field(default_factory=list)

    def main_out(self) -> int:
        bound = {out for _, out in self.bind_pairs}
        free = [i for i in range(sum(c.num_out for c in self.coders)) if i not in bound]
        if len(free) != 1:
            raise Error("format", "a folder with more than one output")
        return free[0]

    @property
    def unpack_size(self) -> int:
        return self.unpack_sizes[self.main_out()]


@dataclass
class Entry:
    name: str
    size: int = 0
    is_dir: bool = False
    is_link: bool = False
    is_anti: bool = False
    crc: int | None = None
    attributes: int | None = None
    folder: int | None = None      # which folder holds its data (None: empty)
    offset: int = 0                # where in that folder's output


class _Reader:
    def __init__(self, data: bytes):
        self.data, self.pos = data, 0

    def byte(self) -> int:
        if self.pos >= len(self.data):
            raise Error("format", "the archive's header ends too early")
        b = self.data[self.pos]
        self.pos += 1
        return b

    def bytes(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.data):
            raise Error("format", "the archive's header ends too early")
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out

    def number(self) -> int:
        first = self.byte()
        mask, value = 0x80, 0
        for i in range(8):
            if not first & mask:
                return value | ((first & (mask - 1)) << (8 * i))
            value |= self.byte() << (8 * i)
            mask >>= 1
        return value

    def count(self, limit: int = MAX_FILES) -> int:
        n = self.number()
        if n > limit:
            raise Error("format", f"implausible count in the archive's header ({n})")
        return n

    def uint32(self) -> int:
        return struct.unpack("<I", self.bytes(4))[0]

    def bits(self, n: int) -> list[bool]:
        out, b, mask = [], 0, 0
        for _ in range(n):
            if not mask:
                b, mask = self.byte(), 0x80
            out.append(bool(b & mask))
            mask >>= 1
        return out

    def defined(self, n: int) -> list[bool]:
        return [True] * n if self.byte() else self.bits(n)

    def digests(self, n: int) -> list[int | None]:
        return [self.uint32() if d else None for d in self.defined(n)]

    def expect(self, prop: int) -> None:
        got = self.byte()
        if got != prop:
            raise Error("format", f"unexpected property {got:#x} in the archive's header (wanted {prop:#x})")


@dataclass
class _Streams:
    pack_pos: int = 0
    pack_sizes: list[int] = field(default_factory=list)
    folders: list[Folder] = field(default_factory=list)


def _read_pack_info(r: _Reader, s: _Streams) -> None:
    s.pack_pos = r.number()
    n = r.count()
    while True:
        prop = r.byte()
        if prop == K_END:
            break
        if prop == K_SIZE:
            s.pack_sizes = [r.number() for _ in range(n)]
        elif prop == K_CRC:
            r.digests(n)
        else:
            raise Error("format", f"unexpected property {prop:#x} in the pack info")
    if len(s.pack_sizes) != n:
        s.pack_sizes = [0] * n


def _read_folder(r: _Reader) -> Folder:
    coders = []
    for _ in range(r.count(64)):
        flag = r.byte()
        if flag & 0x80:
            raise Error("unsupported", "alternative coder methods aren't supported")
        method = r.bytes(flag & 0x0F)
        num_in, num_out = (r.count(64), r.count(64)) if flag & 0x10 else (1, 1)
        props = r.bytes(r.count(1 << 16)) if flag & 0x20 else b""
        coders.append(Coder(method, num_in, num_out, props))
    total_out = sum(c.num_out for c in coders)
    total_in = sum(c.num_in for c in coders)
    pairs = [(r.number(), r.number()) for _ in range(total_out - 1)]
    num_packed = total_in - len(pairs)
    if num_packed < 1:
        raise Error("format", "a folder without packed input")
    if num_packed == 1:
        bound_in = {i for i, _ in pairs}
        packed = [i for i in range(total_in) if i not in bound_in][:1]
    else:
        packed = [r.number() for _ in range(num_packed)]
    return Folder(coders, pairs, packed)


def _read_unpack_info(r: _Reader, s: _Streams) -> None:
    r.expect(K_FOLDER)
    n = r.count()
    if r.byte():
        raise Error("unsupported", "folders stored outside the header aren't supported")
    s.folders = [_read_folder(r) for _ in range(n)]
    r.expect(K_CODERS_UNPACK_SIZE)
    for f in s.folders:
        f.unpack_sizes = [r.number() for _ in range(sum(c.num_out for c in f.coders))]
    while True:
        prop = r.byte()
        if prop == K_END:
            break
        if prop == K_CRC:
            for f, crc in zip(s.folders, r.digests(n)):
                f.crc = crc
        else:
            raise Error("format", f"unexpected property {prop:#x} in the unpack info")


def _read_substreams(r: _Reader, s: _Streams) -> None:
    folders = s.folders
    for f in folders:
        f.num_substreams = 1
    prop = r.byte()
    if prop == K_NUM_UNPACK_STREAM:
        for f in folders:
            f.num_substreams = r.count()
        prop = r.byte()
    sizes_read = prop == K_SIZE
    for f in folders:
        if f.num_substreams == 0:
            f.sizes = []
            continue
        sizes = [r.number() for _ in range(f.num_substreams - 1)] if sizes_read else []
        rest = f.unpack_size - sum(sizes)
        if rest < 0:
            raise Error("format", "substream sizes larger than their folder")
        f.sizes = sizes + [rest]
    if sizes_read:
        prop = r.byte()
    for f in folders:
        f.crcs = [f.crc] if f.num_substreams == 1 and f.crc is not None else [None] * f.num_substreams
    while prop != K_END:
        if prop == K_CRC:
            unknown = [(f, i) for f in folders for i in range(f.num_substreams)
                       if not (f.num_substreams == 1 and f.crc is not None)]
            for (f, i), crc in zip(unknown, r.digests(len(unknown))):
                f.crcs[i] = crc
        else:
            raise Error("format", f"unexpected property {prop:#x} in the substreams info")
        prop = r.byte()


def _read_streams(r: _Reader) -> _Streams:
    s = _Streams()
    substreams = False
    while True:
        prop = r.byte()
        if prop == K_END:
            break
        if prop == K_PACK_INFO:
            _read_pack_info(r, s)
        elif prop == K_UNPACK_INFO:
            _read_unpack_info(r, s)
        elif prop == K_SUBSTREAMS_INFO:
            _read_substreams(r, s)
            substreams = True
        else:
            raise Error("format", f"unexpected property {prop:#x} in the streams info")
    if not substreams:
        for f in s.folders:
            f.num_substreams, f.sizes, f.crcs = 1, [f.unpack_size], [f.crc]
    first = 0
    for f in s.folders:
        f.first_pack = first
        first += len(f.packed)
    if first > len(s.pack_sizes):
        raise Error("format", "more packed streams used than the archive has")
    return s


def _filters(folder: Folder) -> tuple[str, object]:
    """How to decode a folder: ("lzma", filter chain), ("deflate"|"bzip2"|"copy", None)."""
    if len(folder.packed) != 1:
        names = ", ".join(c.name for c in folder.coders)
        raise Error("unsupported", f"compressed with {names}, which needs several packed streams (BCJ2)")
    for c in folder.coders:
        if c.method not in METHODS:
            raise Error("unsupported", f"compressed with {c.name}")
        if c.num_in != 1 or c.num_out != 1:
            raise Error("unsupported", f"compressed with {c.name}, a coder with several streams")
    # the chain from the folder's output back to its packed stream
    in_base, out_base, pos_in, pos_out = [], [], 0, 0
    for c in folder.coders:
        in_base.append(pos_in)
        out_base.append(pos_out)
        pos_in += c.num_in
        pos_out += c.num_out
    chain, out = [], folder.main_out()
    for _ in range(len(folder.coders)):
        idx = out_base.index(out)
        chain.append(folder.coders[idx])
        in_stream = in_base[idx]
        pair = next((p for p in folder.bind_pairs if p[0] == in_stream), None)
        if pair is None:
            break
        out = pair[1]
    if len(chain) != len(folder.coders):
        raise Error("format", "a folder whose coders aren't one chain")
    # chain[0] makes the output; chain[-1] reads the packed stream
    names = [METHODS[c.method] for c in chain]
    if len(chain) == 1 and names[0] in (COPY, DEFLATE, BZIP2):
        return names[0], None
    if names[-1] not in (LZMA1, LZMA2):
        raise Error("unsupported", f"compressed with {' + '.join(reversed(names))}")
    # liblzma's raw chain is in encoding order, LZMA last: the output's coder first, as here
    filters = []
    for c, name in zip(chain, names):
        if name == LZMA2:
            if len(c.props) < 1:
                raise Error("format", "LZMA2 without its properties")
            d = c.props[0] & 0x3F
            if d > 40:
                raise Error("format", "LZMA2 dictionary size out of range")
            dict_size = 0xFFFFFFFF if d == 40 else (2 | (d & 1)) << (d // 2 + 11)
            filters.append({"id": lzma.FILTER_LZMA2, "dict_size": dict_size})
        elif name == LZMA1:
            if len(c.props) < 5:
                raise Error("format", "LZMA without its properties")
            d = c.props[0]
            if d >= 9 * 5 * 5:
                raise Error("format", "LZMA properties out of range")
            lc, lp, pb = d % 9, (d // 9) % 5, d // 45
            dict_size = max(struct.unpack("<I", c.props[1:5])[0], 4096)
            filters.append({"id": lzma.FILTER_LZMA1, "dict_size": dict_size, "lc": lc, "lp": lp, "pb": pb})
        elif name == DELTA:
            filters.append({"id": lzma.FILTER_DELTA, "dist": (c.props[0] + 1) if c.props else 1})
        elif name in _BRANCH:
            f = {"id": _BRANCH[name]}
            if len(c.props) >= 4 and struct.unpack("<I", c.props[:4])[0]:
                f["start_offset"] = struct.unpack("<I", c.props[:4])[0]
            filters.append(f)
        elif name == COPY:
            continue
        else:
            raise Error("unsupported", f"compressed with {' + '.join(reversed(names))}")
    ids = [f["id"] for f in filters]
    if ids.count(lzma.FILTER_LZMA1) + ids.count(lzma.FILTER_LZMA2) != 1 or len(filters) > 4:
        raise Error("unsupported", f"compressed with {' + '.join(reversed(names))}")
    return "lzma", filters


class _Decoder:
    """Streams one folder's output from its packed bytes."""

    def __init__(self, folder: Folder):
        self.kind, filters = _filters(folder)
        if self.kind == "lzma":
            try:
                self.d = lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=filters)
            except lzma.LZMAError as e:
                raise Error("unsupported", f"LZMA settings this Python can't decode ({e})")
        elif self.kind == "deflate":
            self.d = zlib.decompressobj(-15)
        elif self.kind == "bzip2":
            self.d = bz2.BZ2Decompressor()
        else:
            self.d = None
        self.finished = False

    def feed(self, data: bytes, limit: int) -> bytes:
        """Output for more input (at most `limit` bytes; call more(limit) for the rest)."""
        try:
            if self.kind == "lzma":
                return self.d.decompress(data, max_length=limit)
            if self.kind == "deflate":
                return self.d.decompress(self.d.unconsumed_tail + data, limit)
            if self.kind == "bzip2":
                return self.d.decompress(data, max_length=limit)
        except (lzma.LZMAError, zlib.error, OSError, EOFError, ValueError) as e:
            raise Error("crc", f"the data is damaged ({e})")
        return data

    def more(self, limit: int) -> bytes:
        try:
            if self.kind == "lzma":
                if self.d.eof or self.d.needs_input:
                    return b""
                return self.d.decompress(b"", max_length=limit)
            if self.kind == "deflate":
                tail = self.d.unconsumed_tail
                return self.d.decompress(tail, limit) if tail else b""
            if self.kind == "bzip2":
                if self.d.eof or self.d.needs_input:
                    return b""
                return self.d.decompress(b"", max_length=limit)
        except (lzma.LZMAError, zlib.error, OSError, EOFError, ValueError) as e:
            raise Error("crc", f"the data is damaged ({e})")
        return b""

    def finish(self) -> bytes:
        """What is left once all the input was given (Deflate keeps some back)."""
        if self.kind == "deflate" and not self.finished:
            self.finished = True
            try:
                return self.d.flush()
            except zlib.error as e:
                raise Error("crc", f"the data is damaged ({e})")
        return b""


class Archive:
    """A .7z archive: its entries, and their data as streams."""

    def __init__(self, fileobj, size: int | None = None):
        self.f = fileobj
        if size is None:
            fileobj.seek(0, 2)
            size = fileobj.tell()
        self.size = size
        fileobj.seek(0)
        start = fileobj.read(32)
        if len(start) < 32 or start[:6] != SIGNATURE:
            raise Error("format", "not a .7z archive")
        if start[6] != 0:
            raise Error("unsupported", f"a .7z of format version {start[6]}.{start[7]}")
        if zlib.crc32(start[12:32]) != struct.unpack("<I", start[8:12])[0]:
            raise Error("crc", "the archive's start header is damaged")
        offset, length, crc = struct.unpack("<QQI", start[12:32])
        if length == 0:                 # an empty archive
            self.streams = _Streams()
            self.entries, self.folders, self.pack_sizes, self.pack_pos = [], [], [], 0
            return
        if 32 + offset + length > size:
            raise Error("truncated", "the archive is incomplete (a part of a multi-volume archive, or a download "
                                     "that stopped early)")
        if length > MAX_HEADER:
            raise Error("format", "the archive's header is implausibly large")
        header = self._read_at(32 + offset, length)
        if zlib.crc32(header) != crc:
            raise Error("crc", "the archive's header is damaged")
        for _ in range(4):           # an encoded header may be encoded again
            r = _Reader(header)
            prop = r.byte()
            if prop == K_HEADER:
                self._read_header(r)
                return
            if prop != K_ENCODED_HEADER:
                raise Error("format", f"unexpected property {prop:#x} at the archive's header")
            s = _read_streams(r)
            if not s.folders:
                raise Error("format", "an encoded header without data")
            header = b"".join(self._folder_chunks(s, 0, verify=True))
            if len(header) > MAX_HEADER:
                raise Error("format", "the archive's header is implausibly large")
        raise Error("format", "the archive's header is encoded too many times")

    # ── reading ──
    def _read_at(self, pos: int, n: int) -> bytes:
        self.f.seek(pos)
        data = self.f.read(n)
        if len(data) != n:
            raise Error("truncated", "the archive is incomplete")
        return data

    def _pack_offset(self, s: _Streams, index: int) -> int:
        return 32 + s.pack_pos + sum(s.pack_sizes[:index])

    def _folder_chunks(self, s: _Streams, index: int, verify: bool = False):
        """The output of folder `index`, in chunks."""
        folder = s.folders[index]
        decoder = _Decoder(folder)
        pos = self._pack_offset(s, folder.first_pack)
        left_in = s.pack_sizes[folder.first_pack]
        left_out = folder.unpack_size
        crc = 0
        self.f.seek(pos)
        while left_out > 0:
            out = decoder.more(min(left_out, CHUNK))
            if not out and left_in <= 0:
                out = decoder.finish()
                if not out:
                    raise Error("crc", "the data ends before all of it was unpacked (damaged)")
            if not out:
                data = self.f.read(min(left_in, CHUNK))
                if not data:
                    raise Error("truncated", "the archive is incomplete")
                left_in -= len(data)
                out = decoder.feed(data, min(left_out, CHUNK))
                if not out:
                    continue
            if len(out) > left_out:
                out = out[:left_out]
            left_out -= len(out)
            if verify or folder.crc is not None:
                crc = zlib.crc32(out, crc)
            yield out
        if folder.crc is not None and crc != folder.crc:
            raise Error("crc", "the data is damaged (CRC mismatch)")

    def _read_header(self, r: _Reader) -> None:
        streams = _Streams()
        while True:
            prop = r.byte()
            if prop == K_END:
                break
            if prop == K_ARCHIVE_PROPERTIES:
                while r.byte() != K_END:
                    r.bytes(r.count(MAX_HEADER))
            elif prop == K_ADDITIONAL_STREAMS:
                _read_streams(r)
            elif prop == K_MAIN_STREAMS:
                streams = _read_streams(r)
            elif prop == K_FILES_INFO:
                self.entries = self._read_files(r, streams)
            else:
                raise Error("format", f"unexpected property {prop:#x} in the archive's header")
        if not hasattr(self, "entries"):
            self.entries = []
        self.streams = streams
        self.folders, self.pack_sizes, self.pack_pos = streams.folders, streams.pack_sizes, streams.pack_pos

    def _read_files(self, r: _Reader, s: _Streams) -> list[Entry]:
        n = r.count()
        entries = [Entry("") for _ in range(n)]
        empty_stream = [False] * n
        empty_file: list[bool] = []
        anti: list[bool] = []
        while True:
            prop = r.byte()
            if prop == K_END:
                break
            size = r.count(MAX_HEADER)
            data = _Reader(r.bytes(size))
            if prop == K_EMPTY_STREAM:
                empty_stream = data.bits(n)
            elif prop == K_EMPTY_FILE:
                empty_file = data.bits(sum(empty_stream))
            elif prop == K_ANTI:
                anti = data.bits(sum(empty_stream))
            elif prop == K_NAME:
                if data.byte():
                    raise Error("unsupported", "file names stored outside the header aren't supported")
                raw = data.data[data.pos:]
                names = raw.decode("utf-16-le", errors="replace").split("\0")
                for e, name in zip(entries, names):
                    e.name = name
            elif prop == K_WIN_ATTRIBUTES:
                defined = data.defined(n)
                if data.byte():
                    raise Error("unsupported", "attributes stored outside the header aren't supported")
                for e, d in zip(entries, defined):
                    if d:
                        e.attributes = data.uint32()
            # times, start positions, padding (K_DUMMY) and the rest: not needed
        # data: files with a stream take the folders' substreams in order
        substreams = [(fi, i) for fi, f in enumerate(s.folders) for i in range(f.num_substreams)]
        k = e_i = 0
        offsets = [0] * len(s.folders)
        for idx, e in enumerate(entries):
            attr = e.attributes or 0
            e.is_link = bool(attr & 0x8000) and (attr >> 16) & 0o170000 == 0o120000
            if empty_stream[idx]:
                is_empty_file = empty_file[e_i] if e_i < len(empty_file) else False
                e.is_anti = anti[e_i] if e_i < len(anti) else False
                e_i += 1
                e.is_dir = not is_empty_file or bool(attr & 0x10)
                continue
            if k >= len(substreams):
                raise Error("format", "more files than data streams")
            fi, i = substreams[k]
            k += 1
            f = s.folders[fi]
            e.folder, e.size, e.crc, e.offset = fi, f.sizes[i], f.crcs[i], offsets[fi]
            offsets[fi] += e.size
            e.is_dir = bool(attr & 0x10) and e.size == 0
        return entries

    # ── for callers ──
    def methods(self) -> list[str]:
        """The compression methods used, by name."""
        return sorted({c.name for f in self.folders for c in f.coders})

    def check_supported(self) -> None:
        """Raises Error("unsupported", ...) when some data can't be decoded here."""
        for f in self.folders:
            _filters(f)

    def unpacked_size(self) -> int:
        return sum(e.size for e in self.entries if e.folder is not None)

    def extract(self, sink, entries: list[Entry] | None = None) -> None:
        """Decompress folder by folder, calling sink(entry, chunk) for each piece of each file
        (chunk b"" once at the end of each file, after its CRC was checked). Entries not in
        `entries` (when given) are decompressed but not passed on."""
        wanted = None if entries is None else {id(e) for e in entries}
        by_folder: dict[int, list[Entry]] = {}
        for e in self.entries:
            if e.folder is not None:
                by_folder.setdefault(e.folder, []).append(e)
        for fi in sorted(by_folder):
            files = sorted(by_folder[fi], key=lambda e: e.offset)
            if wanted is not None and not any(id(e) in wanted for e in files):
                continue
            it = iter(files)
            current = next(it, None)
            pos, crc, left = 0, 0, current.size if current else 0
            for chunk in self._folder_chunks(self.streams, fi):
                view = memoryview(chunk)
                while view and current is not None:
                    take = view[:left]
                    if take:
                        crc = zlib.crc32(take, crc)
                        if wanted is None or id(current) in wanted:
                            sink(current, bytes(take))
                    view = view[len(take):]
                    left -= len(take)
                    pos += len(take)
                    while current is not None and left == 0:
                        if current.crc is not None and crc != current.crc:
                            raise Error("crc", f"{current.name}: damaged (CRC mismatch)")
                        if wanted is None or id(current) in wanted:
                            sink(current, b"")
                        current = next(it, None)
                        crc, left = 0, current.size if current else 0


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if len(args) != 2 or args[0] not in ("list", "test"):
        print(__doc__.strip().splitlines()[0])
        print("usage: sevenzip.py list|test ARCHIVE.7z", file=sys.stderr)
        return 2
    try:
        with open(args[1], "rb") as f:
            a = Archive(f)
            if args[0] == "list":
                for e in a.entries:
                    kind = "dir " if e.is_dir else "link" if e.is_link else "file"
                    print(f"{kind} {e.size:>12} {e.name}")
                print(f"{len(a.entries)} entries, {a.unpacked_size()} bytes, methods: {', '.join(a.methods())}")
            else:
                a.check_supported()
                a.extract(lambda e, c: None)
                print("ok")
    except (Error, OSError) as e:
        print(f"sevenzip.py: {getattr(e, 'message', e)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
