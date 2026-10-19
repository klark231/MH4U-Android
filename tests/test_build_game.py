"""tools/build_game.py end to end on a synthetic dump (no game data)."""
import os
import shutil
import struct
import subprocess
import sys

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "tools"))

pytestmark = pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")


def fake_exefs(code: bytes) -> bytes:
    header = bytearray(0x200)
    struct.pack_into("<8sII", header, 0, b".code", 0, len(code))
    return bytes(header) + code + bytes(-len(code) % 0x200)


def test_build_game_synthetic(tmp_path):
    import build_game
    import make_cxi
    # ARM code: mov r0, #42; bx lr / add r0, r0, r1; bx lr
    text = struct.pack("<4I", 0xE3A0002A, 0xE12FFF1E, 0xE0800001, 0xE12FFF1E).ljust(0x1000, b"\0")
    rodata, data = bytes(0x1000), bytes(0x1000)
    exheader = bytearray(0x800)
    exheader[0:8] = b"SYNTH\0\0\0"
    exheader[0x0D] = 0                                         # .code not compressed
    struct.pack_into("<III", exheader, 0x10, 0x00100000, 1, len(text))
    struct.pack_into("<III", exheader, 0x20, 0x00101000, 1, len(rodata))
    struct.pack_into("<III", exheader, 0x30, 0x00102000, 1, len(data))
    struct.pack_into("<Q", exheader, 0x200, 0x0004000000ABCD00)
    cxi = tmp_path / "synthetic.cxi"
    cxi.write_bytes(make_cxi.make_cxi(bytes(exheader), fake_exefs(text + rodata + data)))
    functions = tmp_path / "functions.tsv"
    functions.write_text("address\tsize\tmode\tkind\tname\tstatus\towner\tnotes\n"
                         "0x00100000\t0x8\tarm\tunknown\t\ttodo\t\t\n0x00100008\t0x8\tarm\tunknown\t\ttodo\t\t\n")
    install = tmp_path / "azahar-user"
    rc = build_game.main(["--dump", str(cxi), "--out", str(tmp_path / "out"), "--opt", "O0", "--any-version",
                          "--functions", str(functions), "--extra", "", "--hooks", "", "--install", str(install),
                          "-j", "2"])
    assert rc == 0
    name = build_game.library_name(0x0004000000ABCD00)
    lib = install / "recomp" / name
    assert lib.exists()
    if shutil.which("llvm-nm"):
        syms = subprocess.run(["llvm-nm", "-D", "--defined-only", str(lib)], capture_output=True, text=True).stdout
        assert "rc_code_init" in syms
    table = (tmp_path / "out" / "recomp" / "src" / "table.c").read_text()
    assert "rc_title_id = 0x0004000000abcd00ull" in table and "f_00100008" in table


def test_build_game_applies_code_mods(tmp_path, capsys):
    """A code mod in Azahar's mods folder (here an IPS patch that turns add into sub) is
    applied before recompiling, so the library's code CRC is the modded code's; --no-mods
    builds the plain code."""
    import zlib
    import build_game
    import make_cxi
    text = struct.pack("<4I", 0xE3A0002A, 0xE12FFF1E, 0xE0800001, 0xE12FFF1E).ljust(0x1000, b"\0")
    exheader = bytearray(0x800)
    exheader[0:8] = b"SYNTH\0\0\0"
    struct.pack_into("<III", exheader, 0x10, 0x00100000, 1, len(text))
    struct.pack_into("<III", exheader, 0x20, 0x00101000, 1, 0x1000)
    struct.pack_into("<III", exheader, 0x30, 0x00102000, 1, 0x1000)
    struct.pack_into("<Q", exheader, 0x200, 0x0004000000ABCD00)
    cxi = tmp_path / "synthetic.cxi"
    cxi.write_bytes(make_cxi.make_cxi(bytes(exheader), fake_exefs(text + bytes(0x2000))))
    functions = tmp_path / "functions.tsv"
    functions.write_text("address\tsize\tmode\tkind\tname\tstatus\towner\tnotes\n"
                         "0x00100000\t0x8\tarm\tunknown\t\ttodo\t\t\n0x00100008\t0x8\tarm\tunknown\t\ttodo\t\t\n")
    install = tmp_path / "azahar-user"
    mods = install / "load" / "mods" / "0004000000ABCD00" / "exefs"
    mods.mkdir(parents=True)
    (mods / "code.ips").write_bytes(b"PATCH" + (10).to_bytes(3, "big") + (1).to_bytes(2, "big") + b"\x40EOF")
    args = ["--dump", str(cxi), "--opt", "O0", "--any-version", "--functions", str(functions), "--extra", "",
            "--hooks", "", "--install", str(install), "-j", "2"]
    table = tmp_path / "out" / "recomp" / "src" / "table.c"
    assert build_game.main(args + ["--out", str(tmp_path / "out")]) == 0
    out = capsys.readouterr().out
    assert "code mods: " in out and "code: 1 bytes changed" in out and "changes the game's code" in out
    modded = text[:10] + b"\x40" + text[11:]                  # sub r0, r0, r1
    assert f"rc_text_crc32 = 0x{zlib.crc32(modded):08x}u" in table.read_text()
    assert build_game.main(args + ["--out", str(tmp_path / "out"), "--no-mods"]) == 0
    assert "code mods" not in capsys.readouterr().out
    assert f"rc_text_crc32 = 0x{zlib.crc32(text):08x}u" in table.read_text()


def test_fetch_compiler_checks_the_download(tmp_path):
    """A downloaded compiler is unpacked only when its SHA-256 is the expected one; a URL with
    none to check against is refused, and the default URL comes with its own."""
    import hashlib
    import zipfile
    import build_game
    archive = tmp_path / "srv" / "llvm-mingw-test.zip"
    archive.parent.mkdir()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(f"llvm-mingw-test/bin/{build_game.MINGW_CLANG}", "#!/bin/sh\n")
    url = archive.resolve().as_uri()
    with pytest.raises(SystemExit, match="SHA-256"):
        build_game.fetch_compiler(str(tmp_path / "bad"), url, "0" * 64)
    assert not os.listdir(tmp_path / "bad")                      # nothing left behind
    with pytest.raises(SystemExit, match="no SHA-256"):
        build_game.fetch_compiler(str(tmp_path / "none"), url)
    good = hashlib.sha256(archive.read_bytes()).hexdigest()
    cc = build_game.fetch_compiler(str(tmp_path / "good"), url, good)
    assert cc.endswith(os.path.join("llvm-mingw-test", "bin", build_game.MINGW_CLANG))
    assert len(build_game.LLVM_MINGW_SHA256) == 64 and build_game.LLVM_MINGW in build_game.LLVM_MINGW_URL


def test_fetch_compiler_from_zip(tmp_path):
    """--fetch-compiler unpacks an llvm-mingw-style zip and finds its clang (here a stand-in
    script that runs the host clang), and reuses it next time without the zip."""
    import zipfile
    import build_game
    exe = ".exe" if sys.platform.startswith("win") else ""
    archive = tmp_path / "llvm-mingw-test.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(f"llvm-mingw-test/bin/{build_game.MINGW_CLANG}{exe}", '#!/bin/sh\nexec clang "$@"\n')
        z.writestr("llvm-mingw-test/include/readme.txt", "headers")
    toolchain = tmp_path / "toolchain"
    cc = build_game.fetch_compiler(str(toolchain), str(archive))
    assert cc == str(toolchain / "llvm-mingw-test" / "bin" / (build_game.MINGW_CLANG + exe))
    assert os.access(cc, os.X_OK)
    archive.unlink()
    assert build_game.find_compiler(None, str(toolchain), False) in (cc, "clang", "cc")
    assert build_game.fetch_compiler(str(toolchain), str(archive)) == cc
    if not sys.platform.startswith("win"):
        out = subprocess.run([cc, "--version"], capture_output=True, text=True)
        assert out.returncode == 0 and "clang" in out.stdout


def test_zig_wheel_choice():
    """The ziglang wheel for this machine is picked from PyPI's file list."""
    import build_game
    index = {"urls": [{"filename": f"ziglang-0.13.0-py3-none-{tag}.whl", "url": tag} for tag in (
        "macosx_10_9_x86_64", "macosx_11_0_arm64", "manylinux_2_12_x86_64.manylinux2010_x86_64",
        "manylinux_2_17_aarch64.manylinux2014_aarch64", "win_amd64")] + [{"filename": "ziglang-0.13.0.tar.gz"}]}
    assert build_game.zig_wheel(index, "linux", "x86_64")["url"].startswith("manylinux_2_12_x86_64")
    assert build_game.zig_wheel(index, "linux", "aarch64")["url"].startswith("manylinux_2_17_aarch64")
    assert build_game.zig_wheel(index, "linux", "AMD64")["url"].startswith("manylinux_2_12_x86_64")
    assert build_game.zig_wheel(index, "darwin", "arm64")["url"] == "macosx_11_0_arm64"
    assert build_game.zig_wheel(index, "darwin", "x86_64")["url"] == "macosx_10_9_x86_64"
    with pytest.raises(SystemExit):
        build_game.zig_wheel(index, "linux", "riscv64")


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX shell scripts")
def test_fetch_zig_from_local_index(tmp_path, monkeypatch):
    """--fetch-compiler without clang on Linux: the wheel named by a (here local) PyPI index is
    checked against its SHA-256, unpacked, and wrapped in a `zigcc` script that runs `zig cc`
    (here a stand-in zig that runs the host clang); a later run finds it without downloading."""
    import hashlib
    import json
    import zipfile
    import build_game
    wheel = tmp_path / "ziglang-0.13.0-py3-none-manylinux_2_12_x86_64.whl"
    with zipfile.ZipFile(wheel, "w") as z:
        z.writestr("ziglang/zig", '#!/bin/sh\n[ "$1" = cc ] || exit 9\nshift\nexec clang "$@"\n')
        # (the wrapper also passes -fno-sanitize=undefined -g0, which clang accepts)
        z.writestr("ziglang/lib/readme.txt", "headers")
    sha = hashlib.sha256(wheel.read_bytes()).hexdigest()
    index = tmp_path / "index.json"
    entry = {"filename": wheel.name, "url": wheel.as_uri(), "digests": {"sha256": sha}}
    index.write_text(json.dumps({"urls": [entry]}))
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"urls": [dict(entry, digests={"sha256": "0" * 64})]}))
    with pytest.raises(SystemExit):
        build_game.fetch_zig(str(tmp_path / "bad-toolchain"), bad.as_uri(), "linux", "x86_64")
    toolchain = tmp_path / "toolchain"
    cc = build_game.fetch_zig(str(toolchain), index.as_uri(), "linux", "x86_64")
    assert cc == str(toolchain / "zigcc") and os.access(cc, os.X_OK)
    out = subprocess.run([cc, "--version"], capture_output=True, text=True)
    assert out.returncode == 0 and "clang" in out.stdout
    wheel.unlink()                                   # found again without the download
    monkeypatch.setattr(build_game.shutil, "which", lambda name: None)
    assert build_game.find_compiler(None, str(toolchain), False) == cc
    with pytest.raises(SystemExit, match="fetch-compiler"):
        build_game.find_compiler(None, str(tmp_path / "empty"), False)


def test_default_user_dir(monkeypatch):
    import build_game
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", "/data")
    assert build_game.default_user_dir() == os.path.join("/data", "azahar-emu")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", "C:/Users/x/AppData/Roaming")
    assert build_game.default_user_dir() == os.path.join("C:/Users/x/AppData/Roaming", "Azahar")


def test_build_game_records_code_mods(tmp_path):
    """--install writes what the library was built from next to it, for tools/mods.py status."""
    import json
    import build_game
    import make_cxi
    import mods
    text = struct.pack("<4I", 0xE3A0002A, 0xE12FFF1E, 0xE0800001, 0xE12FFF1E).ljust(0x1000, b"\0")
    exheader = bytearray(0x800)
    exheader[0:8] = b"SYNTH\0\0\0"
    struct.pack_into("<III", exheader, 0x10, 0x00100000, 1, len(text))
    struct.pack_into("<III", exheader, 0x20, 0x00101000, 1, 0x1000)
    struct.pack_into("<III", exheader, 0x30, 0x00102000, 1, 0x1000)
    struct.pack_into("<Q", exheader, 0x200, mods.PROGRAM_ID)
    cxi = tmp_path / "synthetic.cxi"
    cxi.write_bytes(make_cxi.make_cxi(bytes(exheader), fake_exefs(text + bytes(0x2000))))
    functions = tmp_path / "functions.tsv"
    functions.write_text("address\tsize\tmode\tkind\tname\tstatus\towner\tnotes\n"
                         "0x00100000\t0x8\tarm\tunknown\t\ttodo\t\t\n0x00100008\t0x8\tarm\tunknown\t\ttodo\t\t\n")
    install = tmp_path / "azahar-user"
    exefs = install / "load" / "mods" / "0004000000126300" / "exefs"
    exefs.mkdir(parents=True)
    (exefs / "code.ips").write_bytes(b"PATCH" + (10).to_bytes(3, "big") + (1).to_bytes(2, "big") + b"\x40EOF")
    args = ["--dump", str(cxi), "--opt", "O0", "--any-version", "--functions", str(functions), "--extra", "",
            "--hooks", "", "--install", str(install), "-j", "2", "--out", str(tmp_path / "out")]
    assert build_game.main(args) == 0
    lib = install / "recomp" / build_game.library_name(mods.PROGRAM_ID)
    record = json.loads((install / "recomp" / "0004000000126300.build.json").read_text())
    assert record["changes_code"] is True and record["text_size"] == 0x1000 and record["code_mods"][0]["kind"] == "ips"
    assert mods.code_library_state(str(install), str(lib))["state"] == "ok"
    (exefs / "code.ips").write_bytes(b"PATCH" + (11).to_bytes(3, "big") + (1).to_bytes(2, "big") + b"\x40EOF")
    assert mods.code_library_state(str(install), str(lib))["state"] == "rebuild"
    assert build_game.main(args + ["--no-mods"]) == 0
    record = json.loads((install / "recomp" / "0004000000126300.build.json").read_text())
    assert record["code_mods"] == [] and record["changes_code"] is False


def arm_branch(op: int, at: int, target: int) -> int:
    """B (op 0xEA) or BL (op 0xEB) from `at` to `target`."""
    return op << 24 | ((target - at - 8) >> 2) & 0xFFFFFF


def cave_program(base: int = 0x00100000) -> tuple[bytes, bytes, int]:
    """A tiny program whose code (.text) is 0x20 bytes of its 0x1000-byte page, and an IPS
    code mod in the style of the community's "code cave" mods: it patches two instructions
    to branch into new code it puts in the padding after the code (0x800 into the page),
    one with a BL (a call that returns) and one with a B (a hook that branches back).
    Returns (the page, the IPS patch, the size of .text)."""
    nop = 0xE1A00000
    text = [0xE52DE004, 0xE3A00001, nop, 0xE49DF004,       # f: push {lr}; mov r0, #1; <bl cave_a>; pop {pc}
            0xE3A01005, nop, 0xE0800001, 0xE12FFF1E]       # g: mov r1, #5; <b cave_b>; back: add r0, r0, r1; bx lr
    page = struct.pack(f"<{len(text)}I", *text).ljust(0x1000, b"\0")
    cave = struct.pack("<4I", 0xE2800029, 0xE12FFF1E,       # cave_a: add r0, r0, #41; bx lr
                       0xE0811001, arm_branch(0xEA, base + 0x80C, base + 0x18))  # cave_b: add r1, r1, r1; b back

    def record(offset: int, data: bytes) -> bytes:
        return offset.to_bytes(3, "big") + len(data).to_bytes(2, "big") + data
    ips = (b"PATCH" + record(0x08, struct.pack("<I", arm_branch(0xEB, base + 0x08, base + 0x800)))
           + record(0x14, struct.pack("<I", arm_branch(0xEA, base + 0x14, base + 0x808)))
           + record(0x800, cave) + b"EOF")
    return page, ips, 4 * len(text)


def test_build_game_recompiles_a_code_cave(tmp_path, capsys):
    """A code mod that puts new code after the game's code, in the rest of the code's last
    page, gets that code recompiled with the rest: the library covers the page to its end
    (rc_text_size, its CRC32, the build record's code_size). Without the mod, the range is
    the code alone and the generated code is byte for byte what it was."""
    import json
    import zlib
    import build_game
    import code_mods
    import make_cxi
    import mods
    page, ips, text_size = cave_program()
    exheader = bytearray(0x800)
    exheader[0:8] = b"SYNTH\0\0\0"
    struct.pack_into("<III", exheader, 0x10, 0x00100000, 1, text_size)
    struct.pack_into("<III", exheader, 0x20, 0x00101000, 1, 0x1000)
    struct.pack_into("<III", exheader, 0x30, 0x00102000, 1, 0x1000)
    struct.pack_into("<Q", exheader, 0x200, mods.PROGRAM_ID)
    cxi = tmp_path / "synthetic.cxi"
    cxi.write_bytes(make_cxi.make_cxi(bytes(exheader), fake_exefs(page + bytes(0x2000))))
    functions = tmp_path / "functions.tsv"
    functions.write_text("address\tsize\tmode\tkind\tname\tstatus\towner\tnotes\n"
                         "0x00100000\t0x10\tarm\tunknown\t\ttodo\t\t\n0x00100010\t0x10\tarm\tunknown\t\ttodo\t\t\n")
    install = tmp_path / "azahar-user"
    args = ["--dump", str(cxi), "--opt", "O0", "--any-version", "--functions", str(functions), "--extra", "",
            "--hooks", "", "--tunables", "", "--install", str(install), "-j", "2", "--out", str(tmp_path / "out")]
    src = tmp_path / "out" / "recomp" / "src"

    assert build_game.main(args) == 0                     # no mods yet
    capsys.readouterr()
    plain = {p.name: p.read_text() for p in src.iterdir()}
    assert f"rc_text_size = 0x{text_size:08x}u" in plain["table.c"]
    assert f"rc_text_crc32 = 0x{zlib.crc32(page[:text_size]):08x}u" in plain["table.c"]
    assert "f_00100800" not in plain["table.c"]

    exefs = install / "load" / "mods" / f"{mods.PROGRAM_ID:016X}" / "exefs"
    exefs.mkdir(parents=True)
    (exefs / "code.ips").write_bytes(ips)
    assert build_game.main(args) == 0
    out = capsys.readouterr().out
    assert "after the code, in its last page: 15 bytes changed" in out and "recompiling 0x1000 bytes" in out
    modded = code_mods.apply_ips(page, ips)
    table = (src / "table.c").read_text()
    assert "rc_text_size = 0x00001000u" in table and f"rc_text_crc32 = 0x{zlib.crc32(modded):08x}u" in table
    assert "{0x00100800u, 0, f_00100800}" in table        # the BL's target: a function of its own
    code = (src / "f00100000.c").read_text()
    assert "L_00100808" in code                           # the B's target: part of the function it hooks
    report = (tmp_path / "out" / "recomp" / "report.txt").read_text()
    assert "trap_instructions: 0" in report and "problems: 0" in report
    record = json.loads((install / "recomp" / f"{mods.PROGRAM_ID:016X}.build.json").read_text())
    assert record["code_size"] == 0x1000 and record["text_size"] == text_size and record["changes_code"] is True

    shutil.rmtree(install / "load")                       # and without the mod again: as before
    assert build_game.main(args) == 0
    assert {p.name: p.read_text() for p in src.iterdir()} == plain
    record = json.loads((install / "recomp" / f"{mods.PROGRAM_ID:016X}.build.json").read_text())
    assert record["code_size"] == text_size
