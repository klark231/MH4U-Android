"""tools/sevenzip.py: reading .7z archives with the standard library, on synthetic archives
written by tests/sevenzip_writer.py (never game data)."""
import io
import os
import random
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
sys.path.insert(0, HERE)
import sevenzip  # noqa: E402
from sevenzip_writer import ATTR_UNIX, make_7z, number  # noqa: E402

RND = random.Random(7)
FILES = [("a/one.txt", b"hello " * 3000), ("a/b/two.bin", bytes(RND.getrandbits(8) for _ in range(50000))),
         ("empty.txt", b""), ("folder", None), ("c/three.dat", b"\x00\x01\x02\x03" * 4000),
         ("ünïcode name.png", b"\x89PNG fake")]
METHODS = ("copy", "lzma2", "lzma", "bcj+lzma", "delta+lzma2", "deflate", "bzip2")


def unpack(path):
    with open(path, "rb") as f:
        a = sevenzip.Archive(f)
        a.check_supported()
        got = {}
        a.extract(lambda e, chunk: got.setdefault(e.name, bytearray()).extend(chunk))
        out = {}
        for e in a.entries:
            out[e.name] = None if e.is_dir else bytes(got.get(e.name, b""))
        return a, out


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("solid", [True, False])
@pytest.mark.parametrize("encoded", [False, True])
def test_round_trip(tmp_path, method, solid, encoded):
    path = make_7z(tmp_path / "x.7z", FILES, method=method, solid=solid, encode_header=encoded)
    a, out = unpack(path)
    assert out == dict(FILES)
    assert a.unpacked_size() == sum(len(d) for _, d in FILES if d)
    assert sevenzip.main(["list", str(path)]) == 0
    assert sevenzip.main(["test", str(path)]) == 0


def test_numbers():
    for n in (0, 1, 127, 128, 300, 16383, 16384, 1 << 20, (1 << 32) + 5, (1 << 56) - 1, 1 << 60):
        assert sevenzip._Reader(number(n)).number() == n


def test_damaged_data_is_caught(tmp_path):
    path = make_7z(tmp_path / "x.7z", FILES, method="copy", corrupt_crc=True)
    with open(path, "rb") as f:
        a = sevenzip.Archive(f)
        with pytest.raises(sevenzip.Error) as e:
            a.extract(lambda *_: None)
    assert e.value.code == "crc"


def test_truncated_and_not_7z(tmp_path):
    path = make_7z(tmp_path / "x.7z", FILES)
    data = path.read_bytes()
    (tmp_path / "cut.7z").write_bytes(data[:len(data) // 2])
    with open(tmp_path / "cut.7z", "rb") as f, pytest.raises(sevenzip.Error) as e:
        sevenzip.Archive(f)
    assert e.value.code == "truncated"
    with pytest.raises(sevenzip.Error) as e:
        sevenzip.Archive(io.BytesIO(b"PK\x03\x04 not a 7z at all, but long enough......"))
    assert e.value.code == "format"
    flipped = bytearray(data)
    flipped[-3] ^= 0xFF                          # in the header: its CRC catches it
    with pytest.raises(sevenzip.Error) as e:
        sevenzip.Archive(io.BytesIO(bytes(flipped)))
    assert e.value.code == "crc"


def test_unsupported_methods_are_named(tmp_path):
    path = make_7z(tmp_path / "x.7z", FILES, method="aes")
    with open(path, "rb") as f:
        a = sevenzip.Archive(f)                 # it can be listed
        assert "AES (a password)" in a.methods()
        with pytest.raises(sevenzip.Error) as e:
            a.check_supported()
    assert e.value.code == "unsupported" and "AES" in e.value.message


def test_links_and_folders_are_marked(tmp_path):
    link = ATTR_UNIX | (0o120777 << 16)
    path = make_7z(tmp_path / "x.7z", [("target.txt", b"t"), ("link", b"target.txt", link), ("d", None)])
    with open(path, "rb") as f:
        a = sevenzip.Archive(f)
    by_name = {e.name: e for e in a.entries}
    assert by_name["link"].is_link and not by_name["target.txt"].is_link
    assert by_name["d"].is_dir and not by_name["target.txt"].is_dir


def test_extract_only_some(tmp_path):
    path = make_7z(tmp_path / "x.7z", FILES)
    with open(path, "rb") as f:
        a = sevenzip.Archive(f)
        want = [e for e in a.entries if e.name == "c/three.dat"]
        got = {}
        a.extract(lambda e, chunk: got.setdefault(e.name, bytearray()).extend(chunk), want)
    assert {k: bytes(v) for k, v in got.items()} == {"c/three.dat": dict(FILES)["c/three.dat"]}
