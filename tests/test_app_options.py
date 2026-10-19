"""port/app/ui/options.cpp: how the app's in-game menu reads mh4u.ini and edits it in place
(comments, order and line endings kept, new keys at the end), on synthetic files."""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "port", "app")

CXX = next((c for c in ("c++", "clang++", "g++") if shutil.which(c)), None)
pytestmark = pytest.mark.skipif(CXX is None, reason="needs a C++ compiler")

DRIVER = r"""
#include <cstdio>
#include <fstream>
#include <iterator>
#include <map>
#include <sstream>
#include <string>
#include "ui/options.h"

static std::string Load(const char* path) {
    std::ifstream file(path, std::ios::binary);
    return std::string(std::istreambuf_iterator<char>(file), std::istreambuf_iterator<char>());
}

// read FILE: the values, a "key<TAB>value" line each
// write FILE OUT KEY=VALUE...: the file with those values, into OUT
int main(int argc, char** argv) {
    const std::string mode = argv[1];
    const std::string text = Load(argv[2]);
    if (mode == "read") {
        for (const auto& [key, value] : App::Ui::ReadOptions(text)) {
            std::printf("%s\t%s\n", key.c_str(), value.c_str());
        }
        return 0;
    }
    std::map<std::string, std::string> values;
    for (int i = 4; i < argc; ++i) {
        const std::string arg = argv[i];
        values[arg.substr(0, arg.find('='))] = arg.substr(arg.find('=') + 1);
    }
    std::ofstream(argv[3], std::ios::binary) << App::Ui::WriteOptions(text, values);
    return 0;
}
"""


@pytest.fixture(scope="module")
def driver(tmp_path_factory):
    work = tmp_path_factory.mktemp("options")
    src = work / "driver.cpp"
    src.write_text(DRIVER)
    exe = work / "driver"
    subprocess.run([CXX, "-std=c++17", "-I", APP, str(src), os.path.join(APP, "ui", "options.cpp"),
                    "-o", str(exe)], check=True)
    return exe


def read(driver, tmp_path, text: bytes) -> dict[str, str]:
    path = tmp_path / "in.ini"
    path.write_bytes(text)
    out = subprocess.run([str(driver), "read", str(path)], capture_output=True, text=True,
                         check=True).stdout
    return dict(line.split("\t", 1) for line in out.splitlines())


def write(driver, tmp_path, text: bytes, **values: str) -> bytes:
    path, out = tmp_path / "in.ini", tmp_path / "out.ini"
    path.write_bytes(text)
    subprocess.run([str(driver), "write", str(path), str(out),
                    *(f"{k}={v}" for k, v in values.items())], check=True)
    return out.read_bytes()


SAMPLE = (b"# MH4U PC port options.\n"
          b"# fps = 30 in a comment\n"
          b"fps = 60\n"
          b"\n"
          b"  widescreen = 16:9   # the shape\n"
          b"touch_overlay = top-right 30 85\n"
          b"dither=off#tight\n"
          b"fps = 90\n")


def test_read_like_the_hooks(driver, tmp_path):
    values = read(driver, tmp_path, SAMPLE)
    assert values == {"fps": "90", "widescreen": "16:9", "touch_overlay": "top-right 30 85",
                      "dither": "off"}


def test_write_in_place(driver, tmp_path):
    out = write(driver, tmp_path, SAMPLE, fps="120", widescreen="21:9", dither="on", bloom="off",
                haze="on")
    assert out == (b"# MH4U PC port options.\n"
                   b"# fps = 30 in a comment\n"
                   b"fps = 120\n"
                   b"\n"
                   b"  widescreen = 21:9   # the shape\n"
                   b"touch_overlay = top-right 30 85\n"
                   b"dither = on #tight\n"
                   b"fps = 120\n"
                   b"bloom = off\n"
                   b"haze = on\n")
    assert read(driver, tmp_path, out)["fps"] == "120"


def test_line_endings_kept(driver, tmp_path):
    assert write(driver, tmp_path, b"a = 1\r\nb = 2", b="3", c="4") == b"a = 1\r\nb = 3\r\nc = 4\r\n"


def test_empty_and_unchanged(driver, tmp_path):
    assert write(driver, tmp_path, b"", fps="60") == b"fps = 60\n"
    assert write(driver, tmp_path, SAMPLE) == SAMPLE
