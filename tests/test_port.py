"""PC port tests (no game data).

The test program port/tests/svc_test.c must recompile cleanly, and its code must build
into a code library with tools/recomp/build.py. If a port build is available
(MH4U_RUNNER=path/to/mh4u_runner; see port/README.md), the program also runs under Azahar's kernel with the recompiled code and
with Azahar's JIT, and both must print the same thing.
"""
import os
import re
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(__file__)
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "port", "tests"))

needed = ["clang", "ld.lld", "llvm-objcopy", "llvm-readelf"]
pytestmark = pytest.mark.skipif(any(shutil.which(t) is None for t in needed),
                                reason="clang/lld toolchain not installed")


def test_svc_test_program_recompiles(tmp_path):
    import build_svc_test
    elf = build_svc_test.build(str(tmp_path))
    assert os.path.exists(elf)
    src = os.listdir(tmp_path / "src")
    assert "table.c" in src and any(f.startswith("f") and f.endswith(".c") for f in src)
    # the short-vector routine was found and expanded
    code = "".join((tmp_path / "src" / f).read_text() for f in src if f.endswith(".c"))
    assert "ctx->f.sw[11] = vadd32" in code


def test_code_library_builds(tmp_path):
    import build_svc_test
    from recomp import build as rbuild
    build_svc_test.build(str(tmp_path))
    lib = rbuild.build(str(tmp_path), 2, "O1", ["RC_PAGED"], "code.so", [rbuild.DLL_GLUE],
                       extra_flags=["-fvisibility=hidden"])
    syms = subprocess.run(["llvm-nm", "-D", "--defined-only", lib], capture_output=True, text=True, check=True).stdout
    exported = {line.split()[-1] for line in syms.splitlines()}
    assert "rc_code_init" in exported and "rc_call" not in exported


def emulated_output(binary: str, code: str, elf: str, cpu: str) -> list[str]:
    r = subprocess.run([binary, elf, "--code", code, "--cpu", cpu, "--ms", "5000"], capture_output=True,
                       text=True, timeout=600)
    assert r.returncode == 0, (cpu, r.returncode, r.stdout[-2000:], r.stderr[-2000:])
    lines = []
    for line in (r.stdout + r.stderr).splitlines():
        m = re.search(r"OutputDebugString:\d+: (.*)$", line)
        if m:
            lines.append(m.group(1))
    return lines


@pytest.mark.skipif(not os.environ.get("MH4U_RUNNER"), reason="port not built (set MH4U_RUNNER)")
def test_recompiled_backend_matches_jit(tmp_path):
    binary = os.environ["MH4U_RUNNER"]
    code = os.environ.get("MH4U_TEST_CODE")
    if code:
        elf = os.environ.get("MH4U_TEST_ELF", os.path.join(ROOT, "build", "port-test", "svc_test.elf"))
    else:   # build program and code library here (tools/recomp/build.py --code-library)
        import build_svc_test
        from recomp import build as rbuild
        elf = build_svc_test.build(str(tmp_path))
        code = rbuild.build(str(tmp_path), 2, "O1", ["RC_PAGED"], "code.so", [rbuild.DLL_GLUE],
                            extra_flags=["-fvisibility=hidden"])
    jit = emulated_output(binary, code, elf, "jit")
    recomp = emulated_output(binary, code, elf, "recomp")
    assert jit and jit[0] == "svc_test: start" and jit[-1] == "svc_test: done", jit
    assert recomp == jit


@pytest.mark.skipif(not os.environ.get("MH4U_RUNNER"), reason="port not built (set MH4U_RUNNER)")
def test_interpreter_fallback_matches_jit(tmp_path):
    """Functions missing from the code library (here: whole worker threads and functions
    called through pointers) run in Azahar's interpreter; the output must not change."""
    import build_svc_test
    from recomp import build as rbuild
    binary = os.environ["MH4U_RUNNER"]
    elf = build_svc_test.build(str(tmp_path))
    syms = {}
    for line in subprocess.run(["llvm-readelf", "-s", elf], capture_output=True, text=True, check=True).stdout.splitlines():
        p = line.split()
        if len(p) >= 8 and p[3] == "FUNC":
            syms[p[7]] = int(p[1], 16) & ~1
    drop = {syms[n] for n in ("worker", "op_add", "op_xor", "op_mul")}
    table = tmp_path / "src" / "table.c"
    lines = [ln for ln in table.read_text().splitlines() if not any(f"{{0x{a:08x}u," in ln for a in drop)]
    count = sum(1 for ln in lines if ln.strip().startswith("{0x"))
    table.write_text("\n".join(re.sub(r"rc_func_count = \d+;", f"rc_func_count = {count};", ln) for ln in lines) + "\n")
    code = rbuild.build(str(tmp_path), 2, "O1", ["RC_PAGED"], "code.so", [rbuild.DLL_GLUE],
                        extra_flags=["-fvisibility=hidden"])
    r = subprocess.run([binary, elf, "--code", code, "--cpu", "recomp", "--ms", "5000"], capture_output=True,
                       text=True, timeout=600)
    assert r.returncode == 0, (r.stdout[-2000:], r.stderr[-2000:])
    interpreted = set(re.findall(r"interpreting ([0-9A-F]{8})", r.stdout + r.stderr))
    assert interpreted == {f"{a:08X}" for a in drop}
    assert emulated_output(binary, code, elf, "recomp") == emulated_output(binary, code, elf, "jit")


@pytest.mark.skipif(not os.environ.get("MH4U_RUNNER"), reason="port not built (set MH4U_RUNNER)")
def test_call_counts(tmp_path):
    """A code library built with -D RC_CALL_COUNTS counts every function's calls, and
    --call-counts writes them at an emulated time: the entry point once, each worker thread's entry
    once per thread, the operations as often as the program calls them."""
    import build_svc_test
    from recomp import build as rbuild
    binary = os.environ["MH4U_RUNNER"]
    elf = build_svc_test.build(str(tmp_path))
    code = rbuild.build(str(tmp_path), 2, "O1", ["RC_PAGED", "RC_CALL_COUNTS"], "code.so", [rbuild.DLL_GLUE],
                        extra_flags=["-fvisibility=hidden"])
    syms = {}
    for line in subprocess.run(["llvm-readelf", "-s", elf], capture_output=True, text=True, check=True).stdout.splitlines():
        p = line.split()
        if len(p) >= 8 and p[3] == "FUNC":
            syms[p[7]] = int(p[1], 16) & ~1
    shots = tmp_path / "shots"
    # the program is done after a few emulated milliseconds: the snapshot comes at its end
    r = subprocess.run([binary, elf, "--code", code, "--ms", "5000", "--shots", str(shots),
                        "--call-counts", "4000"], capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, (r.stdout[-2000:], r.stderr[-2000:])
    counts = {int(a, 16): int(n) for a, n in (line.split() for line in (shots / "calls_0004000.txt").read_text().splitlines())}
    assert counts[syms["_start"]] == 1
    assert counts[syms["worker"]] == 3                 # three worker threads
    assert counts[syms["op_add"]] >= 1 and counts[syms["op_mul"]] >= 1
    assert all(n > 0 for n in counts.values())
    # a library without the counters can't give them
    plain = rbuild.build(str(tmp_path), 2, "O1", ["RC_PAGED"], "plain.so", [rbuild.DLL_GLUE],
                         extra_flags=["-fvisibility=hidden"])
    r = subprocess.run([binary, elf, "--code", plain, "--ms", "100", "--call-counts", "50"],
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 1 and "RC_CALL_COUNTS" in r.stderr


@pytest.mark.skipif(not os.environ.get("MH4U_RUNNER"), reason="port not built (set MH4U_RUNNER)")
@pytest.mark.parametrize("extra", [[], ["--draw-window", "50"], ["--async-io"],
                                   ["--shots-from", "500", "--shot-every", "500"],
                                   ["--dump", "500:100000:40"]])
def test_runner_scripted_input_and_screenshots(tmp_path, extra):
    """port/tests/io_test.c reads HID shared memory and draws into the top framebuffer:
    the runner's --input script must reach it and --shots must save what it drew. With
    --draw-window the GPU skips triangles between screenshots; the program writes its
    framebuffer with the CPU, so nothing it does may change. --async-io only changes how
    file reads finish, and the program reads no files. --shots-from moves the first shot;
    --dump writes guest memory (here the program's first instructions) next to the shots."""
    import build_svc_test
    from recomp import build as rbuild
    binary = os.environ["MH4U_RUNNER"]
    elf = build_svc_test.build(str(tmp_path), "io_test")
    code = rbuild.build(str(tmp_path), 2, "O1", ["RC_PAGED"], "code.so", [rbuild.DLL_GLUE],
                        extra_flags=["-fvisibility=hidden"])
    shots = tmp_path / "shots"
    r = subprocess.run([binary, elf, "--code", code, "--ms", "3000", "--user", str(tmp_path / "user"),
                        "--shots", str(shots), "--input",
                        "300:A,600:TOUCH:10:20:100,900:CIRCLE:1:-0.5:200,1200:START"] + extra,
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, (r.stdout[-2000:], r.stderr[-2000:])
    out = [m.group(1) for m in re.finditer(r"OutputDebugString:\d+: (.*)", r.stdout + r.stderr)]
    events = {}
    for line in out:
        m = re.fullmatch(r"(.*) ([0-9a-f]{8})", line)
        if m:
            events[m.group(1)] = int(m.group(2), 16)
    assert 15 <= events["A pressed, frame"] <= 21          # 300 ms at ~60 polls per second
    assert 69 <= events["START pressed, frame"] <= 75      # 1200 ms
    assert events["touch x"] == 10 and events["touch y"] == 20
    assert "circle right" in events and "circle down" in events
    assert (tmp_path / "user" / "nand").is_dir()
    assert (shots / "0000500_top.ppm").exists() == ("--shots-from" in extra)
    if "--dump" in extra:
        dump = (shots / "mem_0000500_00100000.bin").read_bytes()
        assert len(dump) == 0x40 and any(dump)
    data = (shots / "0001000_top.ppm").read_bytes()
    magic, size, _, pixels = data.split(b"\n", 3)
    width, height = map(int, size.split())
    assert (magic, width, height) == (b"P6", 400, 240)

    def pixel(x, y):
        i = (y * width + x) * 3
        return tuple(pixels[i:i + 3])
    assert pixel(100, 5) == (0, 255, 0) and pixel(100, 120) == (255, 0, 0) and pixel(300, 120) == (0, 0, 255)


@pytest.mark.skipif(not os.environ.get("MH4U_RUNNER"), reason="port not built (set MH4U_RUNNER)")
def test_runner_replaces_memory(tmp_path):
    """--replace MS:FROM:TO changes every FROM in the program's writable memory at an emulated
    time: io_test prints its marker variable once it changes. Read-only memory (the code and its
    constants) is left alone, so the program still runs."""
    import build_svc_test
    from recomp import build as rbuild
    binary = os.environ["MH4U_RUNNER"]
    elf = build_svc_test.build(str(tmp_path), "io_test")
    code = rbuild.build(str(tmp_path), 2, "O1", ["RC_PAGED"], "code.so", [rbuild.DLL_GLUE],
                        extra_flags=["-fvisibility=hidden"])
    run = [binary, elf, "--code", code, "--ms", "1500", "--user", str(tmp_path / "user")]
    r = subprocess.run(run + ["--replace", "500:5534484d:04030201"], capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, (r.stdout[-2000:], r.stderr[-2000:])
    assert re.search(r"replace at 5\d\d ms: 1 found", r.stdout)
    assert "marker 01020304" in r.stdout + r.stderr
    r = subprocess.run(run + ["--replace", "500:55:0102"], capture_output=True, text=True, timeout=600)
    assert r.returncode == 1 and "bad --replace" in r.stderr


@pytest.mark.skipif(not os.environ.get("MH4U_RUNNER"), reason="port not built (set MH4U_RUNNER)")
def test_runner_sets_options_while_running(tmp_path):
    """--set MS:KEY=VALUE changes one of the port's options at an emulated time (one player at
    120 fps once a session is together, say); options that only apply at start-up are refused."""
    import build_svc_test
    from recomp import build as rbuild
    binary = os.environ["MH4U_RUNNER"]
    elf = build_svc_test.build(str(tmp_path), "io_test")
    code = rbuild.build(str(tmp_path), 2, "O1", ["RC_PAGED"], "code.so", [rbuild.DLL_GLUE],
                        extra_flags=["-fvisibility=hidden"])
    run = [binary, elf, "--code", code, "--ms", "1200", "--user", str(tmp_path / "user")]
    r = subprocess.run(run + ["--set", "800:refresh_rate=120", "--set", "500:quest_overlay=map 40",
                              "--set", "600:bloom=off", "--set", "700:depth_of_field=off"],
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, (r.stdout[-2000:], r.stderr[-2000:])
    first = re.search(r"set quest_overlay = map 40 at 5\d\d ms", r.stdout)
    second = re.search(r"set refresh_rate = 120 at 8\d\d ms", r.stdout)
    assert first and second and first.start() < second.start(), r.stdout[-2000:]
    # the screen filters are skipped or drawn as the game runs
    assert re.search(r"set bloom = off at 6\d\d ms", r.stdout), r.stdout[-2000:]
    assert re.search(r"set depth_of_field = off at 7\d\d ms", r.stdout), r.stdout[-2000:]
    r = subprocess.run(run + ["--set", "500:widescreen=16:9"], capture_output=True, text=True, timeout=600)
    assert r.returncode == 1 and "widescreen is not an option that can change" in r.stderr
    for bad in ["fps=120", "500:=120", "x:fps=120"]:
        r = subprocess.run([binary, elf, "--set", bad], capture_output=True, text=True, timeout=60)
        assert r.returncode == 1 and "bad --set" in r.stderr, bad


@pytest.mark.skipif(not os.environ.get("MH4U_RUNNER"), reason="port not built (set MH4U_RUNNER)")
def test_runner_installs_without_a_game(tmp_path):
    """--install CIA with no game file installs into the --user folder and exits (the Windows
    package's script installs the update this way), logging to its own file so that it doesn't
    replace Azahar's log in a shared user folder. A missing CIA fails with a message."""
    binary = os.environ["MH4U_RUNNER"]
    user = tmp_path / "user"
    r = subprocess.run([binary, "--install", str(tmp_path / "missing.cia"), "--user", str(user)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and re.search(r"installing .*missing\.cia failed", r.stderr), r.stderr
    assert (user / "log" / "mh4u_runner_log.txt").exists()
    assert not (user / "log" / "azahar_log.txt").exists()
    r = subprocess.run([binary, "--user", str(user)], capture_output=True, text=True, timeout=60)
    assert r.returncode != 0 and "usage" in r.stdout + r.stderr


@pytest.mark.skipif(not os.environ.get("MH4U_RUNNER"), reason="port not built (set MH4U_RUNNER)")
def test_runners_in_lockstep(tmp_path):
    """--lockstep keeps the emulated clocks of runners on one machine together: a runner that
    could go as fast as it likes waits for one that keeps real time, and both finish. --stalls
    reports the steps of emulation that took longer than a given host time."""
    import time
    import build_svc_test
    from recomp import build as rbuild
    binary = os.environ["MH4U_RUNNER"]
    elf = build_svc_test.build(str(tmp_path), "io_test")
    code = rbuild.build(str(tmp_path), 2, "O1", ["RC_PAGED"], "code.so", [rbuild.DLL_GLUE],
                        extra_flags=["-fvisibility=hidden"])
    shared = tmp_path / "clocks"

    def run(speed, index, *extra):
        return subprocess.Popen([binary, elf, "--code", code, "--ms", "1500", "--speed", speed,
                                 "--user", str(tmp_path / f"user{index}"),
                                 "--lockstep", f"{shared}:2:{index}", *extra],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    start = time.monotonic()
    slow = run("100", 0)
    fast = run("0", 1, "--stalls", "0.001")
    out, err = fast.communicate(timeout=600)
    fast_s = time.monotonic() - start
    slow.communicate(timeout=600)
    assert slow.returncode == 0 and fast.returncode == 0, err[-2000:]
    assert fast_s > 1.2                     # held back to the real-time runner's pace
    assert "stall: " in out and "lockstep" not in out
    # alone, the same runner is done long before that
    start = time.monotonic()
    r = subprocess.run([binary, elf, "--code", code, "--ms", "1500", "--user", str(tmp_path / "user9")],
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0 and time.monotonic() - start < fast_s
    r = subprocess.run([binary, elf, "--lockstep", f"{shared}:1:0"], capture_output=True, text=True, timeout=600)
    assert r.returncode == 1 and "bad --lockstep" in r.stderr


@pytest.mark.skipif(not os.environ.get("MH4U_RUNNER"), reason="port not built (set MH4U_RUNNER)")
def test_runners_meet_in_a_room(tmp_path):
    """--room-host makes a network room (Azahar's multiplayer) inside the runner and joins
    it; other runners join with --room-join, which is how two runners play the game's local
    wireless modes together. A room turns away a console it already has, as with two copies
    of one user folder, until one of them takes --new-console-id."""
    import socket
    import build_svc_test
    from recomp import build as rbuild
    binary = os.environ["MH4U_RUNNER"]
    elf = build_svc_test.build(str(tmp_path), "io_test")
    code = rbuild.build(str(tmp_path), 2, "O1", ["RC_PAGED"], "code.so", [rbuild.DLL_GLUE],
                        extra_flags=["-fvisibility=hidden"])
    run = [binary, elf, "--code", code, "--speed", "100"]
    first = subprocess.run(run + ["--ms", "100", "--user", str(tmp_path / "a")], capture_output=True, timeout=600)
    assert first.returncode == 0
    shutil.copytree(tmp_path / "a", tmp_path / "b")
    shutil.copytree(tmp_path / "a", tmp_path / "c")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    # the program runs for 150 frames, ~2.5 s at real-time speed: the room lives that long
    host = subprocess.Popen(run + ["--ms", "5000", "--user", str(tmp_path / "a"), "--room-host", str(port),
                                   "--nick", "alice"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    host_out = []
    for line in host.stdout:
        host_out.append(line)
        if "room: joined" in line or "room: could not" in line:
            break
    assert any(f"room: joined 127.0.0.1:{port} as alice" in line for line in host_out), host_out
    join = ["--room-join", f"127.0.0.1:{port}"]
    copy = subprocess.run(run + ["--ms", "300", "--user", str(tmp_path / "c"), "--nick", "carol"] + join,
                          capture_output=True, text=True, timeout=120)
    guest = subprocess.run(run + ["--ms", "500", "--user", str(tmp_path / "b"), "--nick", "bobby",
                                  "--new-console-id", "--input", "200:A"] + join,
                           capture_output=True, text=True, timeout=120)
    rest, _ = host.communicate(timeout=120)
    assert host.returncode == 0, "".join(host_out) + rest
    assert copy.returncode == 5 and "room: ConsoleIdCollision" in copy.stdout, copy.stdout
    assert guest.returncode == 0, guest.stdout[-2000:] + guest.stderr[-2000:]
    assert re.search(rf"room: joined 127\.0\.0\.1:{port} as bobby, MAC 00:1F:32(:[0-9A-F]{{2}}){{3}}\n", guest.stdout)
    assert "room: members: alice, bobby\n" in guest.stdout
    assert re.search(r"console id [0-9a-f]{16}\n", guest.stdout)
    assert "A pressed, frame" in guest.stdout + guest.stderr      # and the program ran as usual


@pytest.mark.skipif(not os.environ.get("MH4U_RUNNER"), reason="port not built (set MH4U_RUNNER)")
@pytest.mark.parametrize("mode, waits", [("all", True), ("on", False)])
def test_net_sync_waits_for_a_frozen_console(tmp_path, mode, waits):
    """Net sync: the port's consoles in a network room tell each other their emulated clocks,
    and one that has run ahead of a peer it plays with waits for it. A runner frozen for 2 s
    (--freeze, as a PC stalling on shader compilation would) is waited for, about 2 s less the
    200 ms of slack, instead of being left behind. `on` (the default) only syncs with consoles
    that send game data over local wireless, which these test programs don't."""
    import socket
    import build_svc_test
    from recomp import build as rbuild
    binary = os.environ["MH4U_RUNNER"]
    elf = build_svc_test.build(str(tmp_path), "io_test")
    code = rbuild.build(str(tmp_path), 2, "O1", ["RC_PAGED"], "code.so", [rbuild.DLL_GLUE],
                        extra_flags=["-fvisibility=hidden"])
    run = [binary, elf, "--code", code, "--speed", "100", "--log", "*:Warning Frontend:Info Core.ARM11:Info"]
    first = subprocess.run(run + ["--ms", "100", "--user", str(tmp_path / "a")], capture_output=True, timeout=600)
    assert first.returncode == 0
    shutil.copytree(tmp_path / "a", tmp_path / "b")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ, MH4U_NET_SYNC=mode)
    host = subprocess.Popen(run + ["--ms", "4000", "--user", str(tmp_path / "a"), "--room-host", str(port),
                                   "--nick", "alice", "--freeze", "1500:2000"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    host_out = []
    for line in host.stdout:
        host_out.append(line)
        if "room: joined" in line or "room: could not" in line:
            break
    guest = subprocess.run(run + ["--ms", "4000", "--user", str(tmp_path / "b"), "--nick", "bobby",
                                  "--new-console-id", "--room-join", f"127.0.0.1:{port}"],
                           capture_output=True, text=True, timeout=600, env=env)
    rest, _ = host.communicate(timeout=600)
    out = "".join(host_out) + rest
    assert host.returncode == 0 and guest.returncode == 0, out[-2000:] + guest.stdout[-2000:]
    assert re.search(r"freeze at 1[56]\d\d ms for 2000 ms of host time", out)
    guest_out = guest.stdout + guest.stderr
    waited = re.search(r"net sync: waited (\d+) ms at \d+ ms for 00:1F:32", guest_out)
    if waits:
        assert waited and 1200 <= int(waited.group(1)) <= 2500, guest_out[-3000:]
    else:
        assert not waited, guest_out[-3000:]
    r = subprocess.run([binary, elf, "--freeze", "1500"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and "bad --freeze" in r.stderr


@pytest.mark.skipif(not os.environ.get("MH4U_RUNNER"), reason="port not built (set MH4U_RUNNER)")
@pytest.mark.parametrize("spec, size, expected", [
    # 16:9 top screen (424 wide) filling a 1080p window, the touch screen 30% tall in a corner
    ("top-right 30 85", "1920x1080:424", "top 6,0 1914,1080 opacity 1.00\nbottom 1488,0 1920,324 opacity 0.85\n"),
    ("Bottom_Left 25", "1920x1080:424", "top 6,0 1914,1080 opacity 1.00\nbottom 0,810 360,1080 opacity 0.85\n"),
    ("on", "1920x1080:424", "top 6,0 1914,1080 opacity 1.00\nbottom 1488,0 1920,324 opacity 0.85\n"),
    # a 21:9 window: the game centred, the touch screen in the window's corner (over the bar)
    ("top-left 30 100", "2560x1080:424", "top 326,0 2234,1080 opacity 1.00\nbottom 0,0 432,324 opacity 1.00\n"),
    # off: Azahar's own custom layout (its default positions)
    ("off", "1920x1080", "top 0,0 800,480 opacity 1.00\nbottom 80,500 720,980 opacity 1.00\n"),
])
def test_touch_overlay_layout(spec, size, expected):
    """The port's touch_overlay option turns Azahar's custom screen layout into the top
    screen filling the window and the touch screen drawn over it in a corner."""
    r = subprocess.run([os.environ["MH4U_RUNNER"], "--overlay-layout", spec, size], capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout == expected


@pytest.mark.skipif(not os.environ.get("MH4U_RUNNER"), reason="port not built (set MH4U_RUNNER)")
@pytest.mark.parametrize("quest, slot, expected", [
    # the map in the left column's top two slots, at the touch overlay's own scale
    ("map", "0", "top 6,0 1914,1080 opacity 1.00\nbottom 1704,0 1920,184 opacity 0.85\n"
                 "shows 0,36 160,172 of the touch screen\na click at 1812,92 touches 80,104\n"),
    # in the right column's bottom two slots, 40% of the window's height
    ("map 40", "4", "top 6,0 1914,1080 opacity 1.00\nbottom 1412,0 1920,432 opacity 0.85\n"
                    "shows 160,104 320,240 of the touch screen\na click at 1666,216 touches 240,172\n"),
    # off: the whole touch screen
    ("off", "0", "top 6,0 1914,1080 opacity 1.00\nbottom 1488,0 1920,324 opacity 0.85\n"
                 "shows 0,0 320,240 of the touch screen\na click at 1704,162 touches 160,120\n"),
])
def test_quest_map_overlay(quest, slot, expected):
    """With quest_overlay, the touch overlay shows the quest's map panel alone (the touch
    screen's slot grid: two columns of 160 pixels, rows of 68 under a 36-pixel bar), and a
    click on it touches the map where it is on the touch screen."""
    r = subprocess.run([os.environ["MH4U_RUNNER"], "--overlay-layout", "top-right 30 85", "1920x1080:424",
                        quest, slot], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout == expected


@pytest.mark.skipif(not os.environ.get("MH4U_RUNNER"), reason="port not built (set MH4U_RUNNER)")
@pytest.mark.parametrize("quest, slot, target, tslot, expected", [
    # the map placed at the bottom of the window, in the middle
    ("map 40 at 50 100", "0", None, None,
     "top 6,0 1914,1080 opacity 1.00\nbottom 706,648 1214,1080 opacity 0.85\n"
     "shows 0,36 160,172 of the touch screen\na click at 960,864 touches 80,104\n"),
    # the target camera (slot 2 of the default layout) right under the map, at the same scale;
    # a click on it touches the panel, and a drag that started on it stays on it
    ("map", "0", "on", "2",
     "top 6,0 1914,1080 opacity 1.00\nbottom 1704,0 1920,184 opacity 0.85\n"
     "shows 0,36 160,172 of the touch screen\na click at 1812,92 touches 80,104\n"
     "view 1704,184 1920,276 shows 0,172 160,240 of the touch screen\n"
     "a click at 1812,230 touches 80,206\ndragged to 0,1079 it touches 0,239\n"),
    # the map in the bottom-right corner: the target camera goes over it
    ("map 40 at 100 100", "3", "on", "5",
     "top 6,0 1914,1080 opacity 1.00\nbottom 1412,648 1920,1080 opacity 0.85\n"
     "shows 160,36 320,172 of the touch screen\na click at 1666,864 touches 240,104\n"
     "view 1704,556 1920,648 shows 160,172 320,240 of the touch screen\n"
     "a click at 1812,602 touches 240,206\ndragged to 0,1079 it touches 160,239\n"),
    # placed on its own: the top-left corner, 10% of the window's height
    ("map", "0", "on 10 at 0 0", "2",
     "top 6,0 1914,1080 opacity 1.00\nbottom 1704,0 1920,184 opacity 0.85\n"
     "shows 0,36 160,172 of the touch screen\na click at 1812,92 touches 80,104\n"
     "view 0,0 254,108 shows 0,172 160,240 of the touch screen\n"
     "a click at 127,54 touches 80,206\ndragged to 0,1079 it touches 0,239\n"),
    # off, or no target camera panel in the layout: the map alone
    ("map", "0", "off", "2",
     "top 6,0 1914,1080 opacity 1.00\nbottom 1704,0 1920,184 opacity 0.85\n"
     "shows 0,36 160,172 of the touch screen\na click at 1812,92 touches 80,104\n"),
    ("map", "0", "on", "-1",
     "top 6,0 1914,1080 opacity 1.00\nbottom 1704,0 1920,184 opacity 0.85\n"
     "shows 0,36 160,172 of the touch screen\na click at 1812,92 touches 80,104\n"),
])
def test_quest_panel_views(quest, slot, target, tslot, expected):
    """The quest's map placed anywhere in the window (quest_overlay = map SIZE at X Y), and the
    target camera panel in a view of its own (target_overlay): drawn where the options say,
    showing its slot of the touch screen, and a click on it is a tap on the panel."""
    args = [quest, slot] + ([target, tslot] if target else [])
    r = subprocess.run([os.environ["MH4U_RUNNER"], "--overlay-layout", "top-right 30 85", "1920x1080:424", *args],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout == expected


@pytest.mark.skipif(not os.environ.get("MH4U_RUNNER"), reason="port not built (set MH4U_RUNNER)")
@pytest.mark.parametrize("key, value, expected", [
    ("quest_overlay", "map", "map"),
    ("quest_overlay", "on", "map"),
    ("quest_overlay", "MAP 40", "map 40"),
    ("quest_overlay", "map 40 at 98 2", "map 40 at 98 2"),
    ("quest_overlay", "map at 50 100", "map at 50 100"),
    ("quest_overlay", "map 17.53 at 33.333 0.04", "map 17.5 at 33.3 0"),
    ("quest_overlay", "map 40 at 150 -5", "map 40 at 100 0"),
    ("quest_overlay", "map 40 at", "map 40"),
    ("quest_overlay", "off", "off"),
    ("target_overlay", "on", "on"),
    ("target_overlay", "target 10", "on 10"),
    ("target_overlay", "on 12 at 100 45", "on 12 at 100 45"),
    ("target_overlay", "off", "off"),
    ("target_overlay", "", "off"),
])
def test_panel_view_option(key, value, expected):
    """quest_overlay and target_overlay as the hooks read them and the in-game menu writes them
    back to mh4u.ini (to 0.1%, inside the window)."""
    r = subprocess.run([os.environ["MH4U_RUNNER"], "--panel-view", key, value], capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout == expected + "\n"
    again = subprocess.run([os.environ["MH4U_RUNNER"], "--panel-view", key, expected], capture_output=True,
                           text=True, timeout=60)
    assert again.stdout == expected + "\n"


QUEST_FLICKER = ",".join(["map0:2"] + ["none:0.3,map0:0.3"] * 6 + ["map0:1"])


@pytest.mark.skipif(not os.environ.get("MH4U_RUNNER"), reason="port not built (set MH4U_RUNNER)")
@pytest.mark.parametrize("fps", [30, 60, 120, 180])
@pytest.mark.parametrize("spans, expected", [
    # each change of area stops the small panels for a second or two: the map stays
    ("map0:3,none:1.5,map0:3,none:1.8,map0:2", [(0.1, "map in slot 0")]),
    # panels that come and go many times in a few seconds: the map stays
    (QUEST_FLICKER, [(0.1, "map in slot 0")]),
    # the quest menu (or a big panel) has buttons on the touch screen: all of it, at once
    ("map0:3,menu:2,map0:2", [(0.1, "map in slot 0"), (3.1, "whole touch screen"), (5.1, "map in slot 0")]),
    # someone talks: the panels don't run, but the touch screen still shows them
    ("map0:1,drawn:20,map0:1", [(0.1, "map in slot 0")]),
    # a long break with no panels on the touch screen (the quest is over): the whole touch
    # screen after 5 s
    ("map3:2,none:7,map3:2", [(0.1, "map in slot 3"), (7.1, "whole touch screen"), (9.1, "map in slot 3")]),
    ("map0:3,none:8", [(0.1, "map in slot 0"), (8.1, "whole touch screen")]),
    # F12, and a panel layout without the map
    ("map4:2,off:2,map4:2,nomap:1", [(0.1, "map in slot 4"), (2.1, "whole touch screen"),
                                     (4.1, "map in slot 4"), (6.1, "whole touch screen")]),
])
def test_quest_overlay_choice(fps, spans, expected):
    """What the touch overlay shows in a quest, frame by frame: the map while the small panels
    are up, kept through short breaks (a change of area, a cutscene) so that the corner doesn't
    flicker between the map and the whole touch screen; the whole touch screen for the quest
    menu and big panels, and once the panels have been gone for a while. The same at any frame
    rate: the times are emulated seconds."""
    r = subprocess.run([os.environ["MH4U_RUNNER"], "--quest-overlay-frames", str(fps), spans],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    changes = [(float(t), what) for t, what in re.findall(r"^([0-9.]+) s: the (.+)$", r.stdout, re.M)]
    assert [what for _, what in changes] == [what for _, what in expected], r.stdout
    for (t, _), (want, _) in zip(changes, expected):
        assert abs(t - want) <= 1.5 / fps + 1e-6, r.stdout


def _normalized_log(text: str, user_dir: str) -> list[str]:
    """Azahar's log lines (written by its own thread, so the runner's prints can land
    anywhere between them), without timestamps, user paths and the backend's own lines."""
    out = []
    for line in text.splitlines():
        line = re.sub(r"\x1b\[[0-9;]*m", "", line)
        line = re.sub(r"^\[\s*[0-9.]+\]\s*", "", line).replace(user_dir, "<user>")
        line = re.sub(r"Datetime: [0-9/]+ [0-9:]+", "Datetime: <now>", line)
        if not re.match(r"\S+ <\w+> ", line) or line.startswith("Core.ARM11") or "as the user directory" in line:
            continue
        out.append(line)
    return out


@pytest.mark.skipif(not (os.environ.get("MH4U_RUNNER") and os.environ.get("MH4U_GAME")
                         and os.environ.get("MH4U_GAME_CODE")),
                    reason="needs MH4U_RUNNER, MH4U_GAME (your dump as .cxi) and MH4U_GAME_CODE")
def test_game_boot_matches_jit(tmp_path):
    """The game's own code (your dump, never in the repo) logs exactly the same things on
    the recompiled backend as on Azahar's JIT over the first MH4U_GAME_MS of emulated time
    (default 3000): same service calls, same files, same errors."""
    binary, game, code = os.environ["MH4U_RUNNER"], os.environ["MH4U_GAME"], os.environ["MH4U_GAME_CODE"]
    ms = os.environ.get("MH4U_GAME_MS", "3000")
    logs = {}
    for cpu in ("recomp", "jit"):
        user = str(tmp_path / cpu)
        cmd = [binary, game, "--cpu", cpu, "--ms", ms, "--user", user, "--log", "*:Info"]
        if cpu == "recomp":
            cmd += ["--code", code]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        logs[cpu] = _normalized_log(r.stdout + r.stderr, user)
    assert logs["recomp"] == logs["jit"]


def _svc_trace_by_thread(path) -> dict:
    threads = {}
    for line in open(path):
        tid, rest = line.split(" ", 1)
        threads.setdefault(tid, []).append(rest.split(" t=")[0])
    return threads


@pytest.mark.skipif(not os.environ.get("MH4U_RUNNER"), reason="port not built (set MH4U_RUNNER)")
def test_svc_traces_match_jit_per_thread(tmp_path):
    """--trace-svc: every guest thread makes the same system calls, from the same places
    and with the same arguments, on the recompiled code as on Azahar's JIT."""
    import build_svc_test
    from recomp import build as rbuild
    binary = os.environ["MH4U_RUNNER"]
    elf = build_svc_test.build(str(tmp_path))
    code = rbuild.build(str(tmp_path), 2, "O1", ["RC_PAGED"], "code.so", [rbuild.DLL_GLUE],
                        extra_flags=["-fvisibility=hidden"])
    traces = {}
    for cpu in ("recomp", "jit"):
        trace = tmp_path / f"{cpu}.txt"
        r = subprocess.run([binary, elf, "--code", code, "--cpu", cpu, "--ms", "5000", "--trace-svc", str(trace)],
                           capture_output=True, text=True, timeout=600)
        assert r.returncode == 0, (cpu, r.stderr[-2000:])
        traces[cpu] = _svc_trace_by_thread(trace)
    assert len(traces["jit"]) >= 4                        # main thread + 3 workers
    assert traces["recomp"] == traces["jit"]
