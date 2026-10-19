"""port/app/invite.cpp: the invite codes the app's hosts share (mh4u://HOST:PORT?password=...)
and its players paste, in the in-game menu, the launcher's Online screen and --join; and the
kinds of network a host's addresses belong to."""
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
#include <cstdlib>
#include <string>
#include "invite.h"

// parse TEXT: ok<TAB>host<TAB>port<TAB>password<TAB>code, or error<TAB>message
// format HOST PORT PASSWORD: the code
// kind ADDRESS: the network kind
int main(int argc, char** argv) {
    const std::string mode = argv[1];
    if (mode == "parse") {
        const auto room = App::Invite::Parse(argv[2]);
        if (room.ok) {
            std::printf("ok\t%s\t%d\t%s\t%d\n", room.host.c_str(), room.port, room.password.c_str(),
                        room.code ? 1 : 0);
        } else {
            std::printf("error\t%s\n", room.error.c_str());
        }
    } else if (mode == "format") {
        std::printf("%s\n", App::Invite::Format(argv[2], std::atoi(argv[3]), argv[4]).c_str());
    } else {
        std::printf("%s\n", App::Invite::NetworkKind(argv[2]).c_str());
    }
    return 0;
}
"""


@pytest.fixture(scope="module")
def driver(tmp_path_factory):
    work = tmp_path_factory.mktemp("invite")
    src = work / "driver.cpp"
    src.write_text(DRIVER)
    exe = work / "driver"
    subprocess.run([CXX, "-std=c++17", "-I", APP, str(src), os.path.join(APP, "invite.cpp"), "-o", str(exe)],
                   check=True)
    return exe


def run(driver, *args) -> list[str]:
    out = subprocess.run([str(driver), *args], capture_output=True, text=True, check=True).stdout
    return out.rstrip("\n").split("\t")


def parse(driver, text):
    fields = run(driver, "parse", text)
    if fields[0] == "ok":
        return fields[1], int(fields[2]), fields[3], fields[4] == "1"
    return fields[1]


@pytest.mark.parametrize("text, expected", [
    ("mh4u://203.0.113.7:24872", ("203.0.113.7", 24872, "", True)),
    ("  mh4u://203.0.113.7:24900/  ", ("203.0.113.7", 24900, "", True)),
    ("MH4U://hunts.example.org", ("hunts.example.org", 24872, "", True)),
    ("mh4u://203.0.113.7:24872?password=night%20hunts%26more", ("203.0.113.7", 24872, "night hunts&more", True)),
    ("mh4u://10.0.0.2:24872?pw=a+b", ("10.0.0.2", 24872, "a b", True)),
    ("mh4u://10.0.0.2:24872?later=1&password=x", ("10.0.0.2", 24872, "x", True)),
    # in a chat message
    ("Join me! mh4u://198.51.100.4:24872?password=abc (Low Rank)", ("198.51.100.4", 24872, "abc", True)),
    ('"mh4u://198.51.100.4:2000"', ("198.51.100.4", 2000, "", True)),
    # plain addresses
    ("198.51.100.4:24873", ("198.51.100.4", 24873, "", False)),
    ("my-pc.tail1234.ts.net", ("my-pc.tail1234.ts.net", 24872, "", False)),
])
def test_parse(driver, text, expected):
    assert parse(driver, text) == expected


@pytest.mark.parametrize("text, message", [
    ("", "Nothing to join"),
    ("   ", "Nothing to join"),
    ("mh4u://", "no address"),
    ("mh4u://host:port", "must be a number"),
    ("mh4u://host:70000", "between 1 and 65535"),
    ("mh4u://host:0", "between 1 and 65535"),
    ("host:", "must be a number"),
    ("http://host:24872/lobby", "isn't an invite"),
    ("two words", "isn't an invite"),
    ("mh4u://[::1]:24872", "IPv6"),
    ("mh4u://bad$host:24872", "isn't an address"),
])
def test_parse_errors(driver, text, message):
    result = parse(driver, text)
    assert isinstance(result, str) and message in result, result


@pytest.mark.parametrize("host, port, password", [
    ("203.0.113.7", 24872, ""),
    ("hunts.example.org", 24900, "secret"),
    ("10.0.0.2", 1, "sp ace&amp=?#%/ü"),
])
def test_format_round_trip(driver, host, port, password):
    code = run(driver, "format", host, str(port), password)[0]
    assert code.startswith(f"mh4u://{host}:{port}")
    assert " " not in code
    assert ("?password=" in code) == bool(password)
    assert parse(driver, code) == (host, port, password, True)


def test_format_is_one_word(driver):
    code = run(driver, "format", "h", "24872", "a b&c")[0]
    assert code == "mh4u://h:24872?password=a%20b%26c"


@pytest.mark.parametrize("address, kind", [
    ("127.0.0.1", "loopback"), ("192.168.1.20", "lan"), ("10.1.2.3", "lan"), ("172.16.0.1", "lan"),
    ("172.31.255.1", "lan"), ("172.32.0.1", "public"), ("100.64.0.1", "tailscale"),
    ("100.127.1.1", "tailscale"), ("100.128.0.1", "public"), ("26.12.0.5", "radmin"),
    ("169.254.3.4", "link-local"), ("203.0.113.7", "public"), ("1.2.3", ""), ("1.2.3.4.5", ""),
    ("256.1.1.1", ""), ("a.b.c.d", ""), ("", ""),
])
def test_network_kind(driver, address, kind):
    assert run(driver, "kind", address) == [kind]
