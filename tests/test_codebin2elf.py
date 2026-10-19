import io
import os
import struct
import subprocess
import sys

from elftools.elf.elffile import ELFFile

HERE = os.path.dirname(__file__)
TOOLS = os.path.join(HERE, "..", "tools")
sys.path.insert(0, TOOLS)
sys.path.insert(0, HERE)

import codebin2elf  # noqa: E402
import exheader as exh  # noqa: E402
from blz_encoder import compress  # noqa: E402

TEXT_ADDR, RO_ADDR, DATA_ADDR = 0x00100000, 0x00103000, 0x00105000
TEXT_SIZE, RO_SIZE, DATA_SIZE, BSS_SIZE = 0x2345, 0x1100, 0x0800, 0x400
PROGRAM_ID = 0x0004000000ABC000


def make_exheader(compressed: bool) -> bytes:
    buf = bytearray(0x400)
    buf[0:8] = b"TESTAPP\0"
    buf[0x0D] = 1 if compressed else 0
    struct.pack_into("<III", buf, 0x10, TEXT_ADDR, 3, TEXT_SIZE)
    struct.pack_into("<I", buf, 0x1C, 0x4000)
    struct.pack_into("<III", buf, 0x20, RO_ADDR, 2, RO_SIZE)
    struct.pack_into("<III", buf, 0x30, DATA_ADDR, 1, DATA_SIZE)
    struct.pack_into("<I", buf, 0x3C, BSS_SIZE)
    struct.pack_into("<Q", buf, 0x200, PROGRAM_ID)
    return bytes(buf)


def make_code() -> tuple[bytes, bytes, bytes, bytes]:
    text = bytes((i * 7) & 0xFF for i in range(TEXT_SIZE))
    ro = b"rodata!" * (RO_SIZE // 7) + b"r" * (RO_SIZE % 7)
    data = bytes((i * 3) & 0xFF for i in range(DATA_SIZE))
    code = (text.ljust(3 * 0x1000, b"\0") + ro.ljust(2 * 0x1000, b"\0") + data)
    return code, text, ro, data


def test_exheader_parse():
    h = exh.parse(make_exheader(True))
    assert h.title == "TESTAPP"
    assert h.code_compressed
    assert h.text == exh.CodeSegment(TEXT_ADDR, 3, TEXT_SIZE)
    assert h.bss_address == DATA_ADDR + DATA_SIZE
    assert h.program_id == PROGRAM_ID


def check_elf(elf_bytes: bytes, text: bytes, ro: bytes, data: bytes) -> None:
    elf = ELFFile(io.BytesIO(elf_bytes))
    assert elf["e_machine"] == "EM_ARM"
    assert elf["e_entry"] == TEXT_ADDR
    secs = {s.name: s for s in elf.iter_sections()}
    assert secs[".text"]["sh_addr"] == TEXT_ADDR and secs[".text"].data() == text
    assert secs[".rodata"]["sh_addr"] == RO_ADDR and secs[".rodata"].data() == ro
    assert secs[".data"]["sh_addr"] == DATA_ADDR and secs[".data"].data() == data
    assert secs[".bss"]["sh_addr"] == DATA_ADDR + DATA_SIZE
    assert secs[".bss"]["sh_size"] == BSS_SIZE
    loads = [s for s in elf.iter_segments() if s["p_type"] == "PT_LOAD"]
    assert [s["p_vaddr"] for s in loads] == [TEXT_ADDR, RO_ADDR, DATA_ADDR]
    assert loads[2]["p_memsz"] == DATA_SIZE + BSS_SIZE
    for s in loads:
        assert s["p_offset"] % 0x1000 == s["p_vaddr"] % 0x1000


def test_build_elf_uncompressed():
    code, text, ro, data = make_code()
    h = exh.parse(make_exheader(False))
    check_elf(codebin2elf.build_elf(h, codebin2elf.load_code(h, code)), text, ro, data)


def test_cli_compressed(tmp_path):
    code, text, ro, data = make_code()
    (tmp_path / "exheader.bin").write_bytes(make_exheader(True))
    (tmp_path / "code.bin").write_bytes(compress(code, raw_prefix=0x200))
    out = tmp_path / "build" / "out.elf"
    subprocess.run([sys.executable, os.path.join(TOOLS, "codebin2elf.py"),
                    "--exheader", str(tmp_path / "exheader.bin"),
                    "--code", str(tmp_path / "code.bin"), "-o", str(out)],
                   check=True, capture_output=True)
    check_elf(out.read_bytes(), text, ro, data)


def test_truncated_code_is_rejected():
    h = exh.parse(make_exheader(False))
    try:
        codebin2elf.split_segments(h, b"\0" * 0x100)
    except ValueError as e:
        assert "exheader needs" in str(e)
    else:
        raise AssertionError("expected ValueError")
