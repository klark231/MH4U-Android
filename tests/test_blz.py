import os
import random
import struct
import sys

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
sys.path.insert(0, HERE)

import blz  # noqa: E402
from blz_encoder import compress  # noqa: E402


def test_hand_built_stream():
    # Literals Z, Y, X then an 18-byte back-reference with disp 3 -> "XYZ" * 7
    prefix = b"RAW!"
    region = bytes([0x00, 0xF0]) + b"XYZ" + bytes([0x10])
    top, bottom = len(region) + 8, 8
    original_bottom = 21 - (len(region) + 8)
    data = prefix + region + struct.pack("<II", (bottom << 24) | top, original_bottom)
    assert blz.decompress(data) == prefix + b"XYZ" * 7


@pytest.mark.parametrize("seed", range(5))
@pytest.mark.parametrize("raw_prefix", [0, 64])
def test_round_trip_random(seed, raw_prefix):
    rng = random.Random(seed)
    words = [bytes(rng.randrange(256) for _ in range(rng.randrange(2, 9))) for _ in range(24)]
    data = b"".join(rng.choice(words) for _ in range(800))
    packed = compress(data, raw_prefix=raw_prefix)
    assert len(packed) < len(data)
    assert blz.is_probably_compressed(packed)
    assert blz.decompress(packed) == data


def test_rejects_garbage_footer():
    with pytest.raises(blz.BLZError):
        blz.decompress(b"\x00" * 32)
