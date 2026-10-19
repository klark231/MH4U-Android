"""tools/icon.py: the app's icon files agree with each other."""
import os
import struct
import sys

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import icon  # noqa: E402


def synthetic_ico(sizes):
    data = struct.pack("<HHH", 0, 1, len(sizes))
    for s in sizes:
        data += struct.pack("<BBBBHHII", 0 if s == 256 else s, 0 if s == 256 else s, 0, 0, 1, 32, 0, 0)
    return data


def test_ico_directory():
    assert icon.ico_sizes(synthetic_ico([16, 32, 256])) == [16, 32, 256]
    with pytest.raises(ValueError):
        icon.ico_sizes(b"\x00\x00\x02\x00\x01\x00")  # a .cur, not an .ico


def test_header_round_trip():
    pixels = bytes(range(256)) * 64  # 64x64x4
    text = icon.write_header(pixels, 64)
    assert icon.header_pixels(text) == pixels
    assert "constexpr int kSize = 64;" in text


def test_committed_icon_files_agree():
    assert icon.check(icon.ICON_DIR) == []
