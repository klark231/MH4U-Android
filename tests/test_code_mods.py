"""tools/code_mods.py: IPS/BPS code patches and Azahar's mod folder, on synthetic data."""
import os
import sys
import zlib

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import code_mods  # noqa: E402


def ips(*records, tail=b""):
    """records: (offset, data) or (offset, count, byte) for a run."""
    out = bytearray(b"PATCH")
    for r in records:
        out += r[0].to_bytes(3, "big")
        if len(r) == 2:
            out += len(r[1]).to_bytes(2, "big") + r[1]
        else:
            out += b"\0\0" + r[1].to_bytes(2, "big") + bytes([r[2]])
    return bytes(out + b"EOF" + tail)


def number(n):
    """BPS variable-length number."""
    out = bytearray()
    while True:
        x = n & 0x7F
        n >>= 7
        if n == 0:
            out.append(0x80 | x)
            return bytes(out)
        out.append(x)
        n -= 1


def bps(source, target, commands, source_crc=None, metadata=b""):
    """commands: ("sr", n) | ("tr", bytes) | ("sc", n, delta) | ("tc", n, delta)."""
    body = bytearray(b"BPS1" + number(len(source)) + number(len(target)) + number(len(metadata)) + metadata)
    for c in commands:
        if c[0] == "sr":
            body += number((c[1] - 1) << 2)
        elif c[0] == "tr":
            body += number(((len(c[1]) - 1) << 2) | 1) + c[1]
        else:
            body += number(((c[1] - 1) << 2) | (2 if c[0] == "sc" else 3))
            body += number(abs(c[2]) << 1 | (1 if c[2] < 0 else 0))
    body += (zlib.crc32(source) if source_crc is None else source_crc).to_bytes(4, "little")
    body += zlib.crc32(target).to_bytes(4, "little")
    return bytes(body + zlib.crc32(bytes(body)).to_bytes(4, "little"))


def test_ips_records_runs_and_eof():
    code = bytes(range(16))
    out = code_mods.apply_ips(code, ips((2, b"\xAA\xBB"), (10, 3, 0x55), tail=b"\0\0\x10"))
    assert out == bytes([0, 1, 0xAA, 0xBB, 4, 5, 6, 7, 8, 9, 0x55, 0x55, 0x55, 13, 14, 15])
    with pytest.raises(code_mods.ModError):
        code_mods.apply_ips(code, ips((15, b"\1\2")))       # past the end
    with pytest.raises(code_mods.ModError):
        code_mods.apply_ips(code, b"NOTIPS")


def test_bps_commands_and_checks():
    source = b"Monster Hunter 4"
    target = b"Monster Hunter Ultimate!!!!"
    # "Monster Hunter " from the source, "Ultimate!" from the patch, then "!" three times from the
    # target itself (a copy that reads what it has just written)
    patch = bps(source, target, [("sr", 15), ("tr", b"Ultimate!"), ("tc", 3, 23)])
    assert code_mods.apply_bps(source, patch) == target
    # a source copy from elsewhere in the source, and a shorter target keeps the code's length
    patch = bps(source, b"Hunter", [("sc", 6, 8)])
    assert code_mods.apply_bps(source, patch) == b"Hunter" + bytes(len(source) - 6)
    with pytest.raises(code_mods.ModError):     # made for another code.bin
        code_mods.apply_bps(source, bps(source, target, [("sr", 15), ("tr", b"Ultimate!"), ("tc", 3, 23)],
                                        source_crc=1))
    with pytest.raises(code_mods.ModError):     # Azahar refuses metadata
        code_mods.apply_bps(source, bps(source, source, [("sr", 16)], metadata=b"x"))


def test_mod_folder_like_azahar(tmp_path):
    base = tmp_path / "load" / "mods" / "0004000000126300"
    assert code_mods.mod_dir(str(tmp_path), 0x0004000E00126300) == str(base)   # an update's mods
    assert code_mods.find_mods(str(tmp_path), 0x0004000000126300) == (None, None)
    (base / "exefs").mkdir(parents=True)
    (base / "code.bps").write_bytes(b"x")
    (base / "exefs" / "code.ips").write_bytes(ips((1, b"\x09")))
    replacement, patch = code_mods.find_mods(str(tmp_path), 0x0004000000126300)
    assert replacement is None and patch == str(base / "exefs" / "code.ips")   # exefs/code.ips first
    (base / "code.bin").write_bytes(bytes(4))
    code, used = code_mods.apply_mods(b"\1\2\3\4", str(tmp_path), 0x0004000000126300)
    assert code == b"\0\x09\0\0" and used == [str(base / "code.bin"), str(base / "exefs" / "code.ips")]
    assert code_mods.describe(b"\1\2\3\4", code, [("code", 0, 2), ("data", 2, 2)]) == \
        ["code: 2 bytes changed", "data: 2 bytes changed"]
