"""port/unix/build-code-library.sh, the Linux and macOS packages' one-step build of the code
library: it finds Python, runs the packaged build_game.py on the player's update and writes a
first mh4u.ini. Run here with a stand-in build_game.py that records its arguments."""
import os
import platform
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "port", "unix", "build-code-library.sh")

pytestmark = pytest.mark.skipif(shutil.which("sh") is None or platform.system() == "Windows",
                                reason="needs a POSIX shell")

STUB = """import sys
open(sys.argv[0] + ".args", "w").write("\\n".join(sys.argv[1:]))
sys.exit(int(open(sys.argv[0] + ".rc").read()) if __import__("os").path.exists(sys.argv[0] + ".rc") else 0)
"""


def package(tmp_path, with_tools=True):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    shutil.copy(SCRIPT, pkg / "build-code-library.sh")
    if with_tools:
        tools = pkg / "mh4u-tools" / "tools"
        tools.mkdir(parents=True)
        (tools / "build_game.py").write_text(STUB)
    return pkg


def run(pkg, tmp_path, *args):
    # a PATH whose python3 is this interpreter (3.10 or later, as the script wants)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    py = bin_dir / "python3"
    if not py.exists():
        py.symlink_to(sys.executable)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path / "home"),
           "XDG_DATA_HOME": str(tmp_path / "data"), "MH4U_NO_PAUSE": "1"}
    return subprocess.run(["sh", str(pkg / "build-code-library.sh"), *args], capture_output=True,
                          text=True, env=env, stdin=subprocess.DEVNULL, timeout=60)


def user_dir(tmp_path):
    if platform.system() == "Darwin":
        return tmp_path / "home" / "Library" / "Application Support" / "Azahar"
    return tmp_path / "data" / "azahar-emu"


def test_needs_the_packaged_tools(tmp_path):
    r = run(package(tmp_path, with_tools=False), tmp_path, "update.cia")
    assert r.returncode == 1 and "mh4u-tools/tools/build_game.py" in r.stdout


def test_missing_update(tmp_path):
    r = run(package(tmp_path), tmp_path, str(tmp_path / "missing.cia"))
    assert r.returncode == 1 and "Can't find" in r.stdout and "missing.cia" in r.stdout


def test_builds_and_writes_first_options(tmp_path):
    pkg = package(tmp_path)
    cia = tmp_path / "MH4U update v1.3.0.cia"
    cia.write_bytes(b"not really a CIA")
    r = run(pkg, tmp_path, str(cia))
    assert r.returncode == 0, r.stdout + r.stderr
    args = (pkg / "mh4u-tools" / "tools" / "build_game.py.args").read_text().split("\n")
    assert args == ["--dump", str(cia), "--install", "--fetch-compiler"]
    ini = user_dir(tmp_path) / "mh4u.ini"
    text = ini.read_text()
    assert "fps = 60" in text and "touch_overlay = top-right 30 85" in text
    assert "File > Install CIA" in r.stdout
    # a second run keeps the player's own options
    ini.write_text("fps = 120\n")
    assert run(pkg, tmp_path, str(cia)).returncode == 0
    assert ini.read_text() == "fps = 120\n"


def test_build_failure(tmp_path):
    pkg = package(tmp_path)
    (pkg / "mh4u-tools" / "tools" / "build_game.py.rc").write_text("3")
    cia = tmp_path / "update.cia"
    cia.write_bytes(b"x")
    r = run(pkg, tmp_path, str(cia))
    assert r.returncode == 1 and "Something went wrong" in r.stdout
    assert not (user_dir(tmp_path) / "mh4u.ini").exists()
