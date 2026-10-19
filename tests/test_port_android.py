"""Android port pieces that don't need Azahar or a phone: the fibers' assembly (forced on
with MH4U_FIBER_ASM on hosts that also have ucontext), the input mapping engine, the library
file name for another target."""
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = os.path.join(ROOT, "port")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import build_game  # noqa: E402

CXX = shutil.which("g++") or shutil.which("clang++") or shutil.which("c++")
needs_cxx = pytest.mark.skipif(CXX is None, reason="no C++ compiler")


def build_and_run(tmp_path, sources, extra=()):
    exe = str(tmp_path / "t")
    subprocess.run([CXX, "-std=c++17", "-Wall", f"-I{PORT}/src", *extra, *sources, "-o", exe], check=True)
    return subprocess.run([exe], capture_output=True, text=True)


@needs_cxx
@pytest.mark.skipif(not sys.platform.startswith("linux") or os.uname().machine not in ("x86_64", "aarch64"),
                    reason="the assembly is x86_64 and aarch64 (ELF)")
@pytest.mark.parametrize("flags", [["-O0"], ["-O2"]])
def test_fibers_with_assembly_switch(tmp_path, flags):
    r = build_and_run(tmp_path, [f"{PORT}/tests/fiber_test.cpp", f"{PORT}/src/fiber.cpp"],
                      ["-DMH4U_FIBER_ASM", *flags])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "fiber ok" in r.stdout


@needs_cxx
def test_input_map(tmp_path):
    r = build_and_run(tmp_path, [f"{PORT}/tests/input_map_test.cpp", f"{PORT}/src/input_map.cpp"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "input_map ok" in r.stdout


def test_library_name_for_another_target():
    assert build_game.library_name(0x0004000000126300, "so") == "0004000000126300.so"
    assert build_game.library_name(0x0004000000126300, "dll") == "0004000000126300.dll"
