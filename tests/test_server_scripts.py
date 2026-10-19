"""The multiplayer server scripts in server/: host-room.sh (Azahar's dedicated room with MH4U's
settings, kept private), run-lobby.sh (the lobby), the room container's entrypoint (docker stop
passed on to the room as q + Enter) and e2e_room.py's command line; run with a stand-in
azahar-room that records its arguments. The Windows scripts need PowerShell and the Docker files
Docker, so for those only their settings are checked."""
import os
import platform
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "server")
HOST_ROOM = os.path.join(SERVER, "unix", "host-room.sh")
RUN_LOBBY = os.path.join(SERVER, "unix", "run-lobby.sh")
ENTRYPOINT = os.path.join(SERVER, "docker", "room-entrypoint.sh")

posix = pytest.mark.skipif(shutil.which("sh") is None or platform.system() == "Windows",
                           reason="needs a POSIX shell")

# azahar-room's stand-in: its arguments, one per line, then (STUB_WAIT) a line from its console
STUB = """#!/bin/sh
printf '%s\\n' "$@" >"$0.args"
if [ -n "${STUB_WAIT:-}" ]; then
    read -r line
    printf '%s\\n' "$line" >"$0.input"
fi
exit "${STUB_CODE:-0}"
"""
MH4U_ARGS = ["--preferred-app", "Monster Hunter 4 Ultimate",
             "--preferred-app-id", "0004000000126300"]


def stub_room(folder):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "azahar-room"
    path.write_text(STUB)
    path.chmod(0o755)
    return path


def environment(tmp_path, **extra):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    if not (bin_dir / "python3").exists():
        (bin_dir / "python3").symlink_to(sys.executable)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path / "home"),
           "MH4U_SERVER_DIR": str(tmp_path / "server"), "TMPDIR": str(tmp_path)}
    env.update(extra)
    return env


def host_room(tmp_path, *args, script=HOST_ROOM, **env):
    return subprocess.run(["sh", script, *args], capture_output=True, text=True, timeout=30,
                          env=environment(tmp_path, **env), stdin=subprocess.DEVNULL)


def recorded(stub):
    return (stub.parent / "azahar-room.args").read_text().splitlines()


def options(args):
    return dict(zip(args[::2], args[1::2]))


@posix
def test_host_room_defaults(tmp_path):
    stub = stub_room(tmp_path / "bin")
    r = host_room(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    server = tmp_path / "server"
    assert recorded(stub) == ["--room-name", "MH4U Gathering Hall", "--port", "24872",
                              "--max_members", "16", *MH4U_ARGS,
                              "--ban-list-file", str(server / "room-ban-list.txt")]
    assert (server / "room-ban-list.txt").read_text() == "CitraRoom-BanList-1\n"
    assert (server / "host-room.ini").exists() and "Made" in r.stdout
    assert "Unlisted" in r.stdout and "Direct Connect" in r.stdout


@posix
def test_host_room_settings_and_overrides(tmp_path):
    stub = stub_room(tmp_path / "bin")
    server = tmp_path / "server"
    server.mkdir()
    (server / "host-room.ini").write_bytes(
        b"# mine\r\nport = 24900\r\npassword = pw\r\ndescription =  Low rank, any weapon \r\n"
        b"ban_list = /elsewhere/bans.txt\r\nwhat = ever")
    r = host_room(tmp_path, "name=Luis C's Hall", "lobby_url=http://lobby.example.org:24872",
                  "username=Room One", "token=abc=def", "ban_list=bans.txt")
    assert r.returncode == 0, r.stdout + r.stderr
    got = options(recorded(stub))
    assert got == {"--room-name": "Luis C's Hall", "--port": "24900", "--max_members": "16",
                   "--preferred-app": "Monster Hunter 4 Ultimate",
                   "--preferred-app-id": "0004000000126300",
                   "--ban-list-file": str(server / "bans.txt"),
                   "--room-description": "Low rank, any weapon", "--password": "pw",
                   "--web-api-url": "http://lobby.example.org:24872", "--username": "Room One",
                   "--token": "abc=def"}
    assert "Unknown setting: what" in r.stdout and "Listed in the lobby" in r.stdout


@posix
def test_host_room_lobby_needs_name_and_token(tmp_path):
    stub = stub_room(tmp_path / "bin")
    r = host_room(tmp_path, "lobby_url=http://lobby.example.org:24872", "username=Room One")
    assert r.returncode == 0
    assert "--web-api-url" not in recorded(stub) and "needs username and token" in r.stdout


@posix
def test_host_room_refuses_bad_settings(tmp_path):
    stub_room(tmp_path / "bin")
    for bad in ["port=0", "port=abc", "port=70000", "max_members=1", "max_members=300",
                "name=", "not-a-setting"]:
        r = host_room(tmp_path, bad)
        assert r.returncode == 1, bad


@posix
def test_host_room_finds_azahar_room(tmp_path):
    package = tmp_path / "package"
    (package / "server").mkdir(parents=True)
    shutil.copy(HOST_ROOM, package / "server" / "host-room.sh")
    stub = stub_room(package)  # in the folder above the script, as in a package
    r = host_room(tmp_path, script=str(package / "server" / "host-room.sh"))
    assert r.returncode == 0 and recorded(stub)[:2] == ["--room-name", "MH4U Gathering Hall"]
    elsewhere = stub_room(tmp_path / "elsewhere")
    r = host_room(tmp_path, script=str(package / "server" / "host-room.sh"),
                  AZAHAR_ROOM=str(elsewhere))
    assert r.returncode == 0 and recorded(elsewhere)
    # Azahar's Linux bundle makes it an AppImage
    appimage = tmp_path / "linux"
    appimage.mkdir()
    shutil.copy(HOST_ROOM, appimage / "host-room.sh")
    os.rename(stub_room(appimage), appimage / "azahar-room.AppImage")
    r = host_room(tmp_path, script=str(appimage / "host-room.sh"))
    assert r.returncode == 0 and (appimage / "azahar-room.AppImage.args").exists()


@posix
@pytest.mark.skipif(shutil.which("azahar-room") is not None, reason="azahar-room is installed")
def test_host_room_without_azahar_room(tmp_path):
    r = host_room(tmp_path)
    assert r.returncode == 1 and "Can't find azahar-room" in r.stdout


@posix
def test_run_lobby_commands(tmp_path):
    r = host_room(tmp_path, "add-user", "Hunter One", script=RUN_LOBBY)
    assert r.returncode == 0, r.stderr
    token = r.stdout.strip()
    assert len(token) >= 20
    r = host_room(tmp_path, "list-users", script=RUN_LOBBY)
    assert r.stdout.splitlines()[0].startswith("Hunter One")
    assert (tmp_path / "server" / "lobby" / "users.json").exists()
    assert token not in (tmp_path / "server" / "lobby" / "users.json").read_text()


@posix
def test_host_room_settings_are_private(tmp_path):
    stub_room(tmp_path / "bin")
    server = tmp_path / "server"
    assert host_room(tmp_path).returncode == 0
    for path, mode in [(server, 0o700), (server / "host-room.ini", 0o600),
                       (server / "room-ban-list.txt", 0o600)]:
        assert stat.S_IMODE(path.stat().st_mode) == mode, path
    # one made by an older version, readable by all, is made private
    (server / "host-room.ini").chmod(0o644)
    assert host_room(tmp_path).returncode == 0
    assert stat.S_IMODE((server / "host-room.ini").stat().st_mode) == 0o600


def run_entrypoint(tmp_path, **env):
    """The entrypoint in the background, as the container's first process: returned with the
    lines it prints, once it says the room has started (its trap is in by then)."""
    stub = stub_room(tmp_path / "bin")
    data = tmp_path / "data"
    data.mkdir()
    proc = subprocess.Popen(["sh", ENTRYPOINT], cwd=data, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            env=environment(tmp_path, STUB_WAIT="1", STUB_CODE="3", **env))
    lines = []
    reader = threading.Thread(target=lambda: lines.extend(proc.stdout), daemon=True)
    reader.start()
    deadline = time.monotonic() + 20
    while not any(line.startswith("Room started") for line in lines):
        assert time.monotonic() < deadline and proc.poll() is None, lines
        time.sleep(0.02)
    return proc, stub, data, lines


@posix
def test_room_entrypoint_stops_the_room_with_q(tmp_path):
    proc, stub, data, lines = run_entrypoint(
        tmp_path, ROOM_NAME="Night Hunts", ROOM_PORT="24900", ROOM_PASSWORD="pw",
        ROOM_MAX_MEMBERS="4", LOBBY_URL="http://lobby:24872", LOBBY_USERNAME="Room Host",
        LOBBY_TOKEN="secret")
    proc.send_signal(signal.SIGTERM)  # docker stop
    assert proc.wait(10) == 3  # the room's own exit code
    assert (stub.parent / "azahar-room.input").read_text() == "q\n"
    assert options(recorded(stub)) == {
        "--room-name": "Night Hunts", "--port": "24900", "--max_members": "4",
        "--preferred-app": "Monster Hunter 4 Ultimate", "--preferred-app-id": "0004000000126300",
        "--ban-list-file": str(data / "ban-list.txt"), "--password": "pw",
        "--web-api-url": "http://lobby:24872", "--username": "Room Host", "--token": "secret"}
    assert (data / "ban-list.txt").read_text() == "CitraRoom-BanList-1\n"
    assert stat.S_IMODE((data / "ban-list.txt").stat().st_mode) == 0o600
    assert any("Announcing the room to http://lobby:24872" in line for line in lines)


@posix
def test_room_entrypoint_unlisted(tmp_path):
    proc, stub, data, lines = run_entrypoint(tmp_path, LOBBY_URL="http://lobby:24872")
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(10) == 3
    got = options(recorded(stub))
    assert got["--room-name"] == "MH4U Gathering Hall" and got["--port"] == "24872"
    assert "--web-api-url" not in got and any("unlisted" in line for line in lines)


def read(*path):
    with open(os.path.join(ROOT, *path), encoding="utf-8") as f:
        return f.read()


def test_docker_files():
    room = read("server", "docker", "Dockerfile.room")
    # the port's patch (its fixes to Azahar's rooms) goes into the room, on a pinned image
    assert "git -C /src apply /tmp/azahar.patch" in room
    assert "!port/azahar/azahar.patch" in read("server", "docker", "Dockerfile.room.dockerignore")
    assert "opensauce04/azahar-build-environment:latest@sha256:" in room
    # the lobby's health checks go to /health, which doesn't log them; logs are rotated
    assert "127.0.0.1:24872/health" in read("server", "docker", "Dockerfile.lobby")
    compose = read("server", "docker", "compose.yaml")
    assert compose.count("logging: *logging") == 2 and 'max-size: "10m"' in compose
    # no default name for the room in the lobby (docs/SERVER.md, Moderators)
    assert "\nLOBBY_USERNAME=\n" in read("server", "docker", "example.env")


def test_windows_scripts_use_the_same_settings():
    ps1 = read("server", "windows", "host-room.ps1")
    for text in ['"--max_members"', '"--preferred-app", "Monster Hunter 4 Ultimate"',
                 '"--preferred-app-id", "0004000000126300"', '"--ban-list-file"',
                 '"--web-api-url"', "port = 24872", "max_members = 16", "CitraRoom-BanList-1",
                 "azahar-room.exe", "MH4U Server",
                 # arguments quoted by the C runtime's rules, not by Windows PowerShell 5.1
                 "ConvertTo-Argument $_", "System.Diagnostics.ProcessStartInfo"]:
        assert text in ps1, text
    assert "& $room @roomArgs" not in ps1
    assert "host-room.ps1" in read("server", "windows", "Host room.bat")
    bat = read("server", "windows", "Run lobby.bat")
    assert r"..\lobby\mh4u_lobby.py" in bat and r"MH4U Server\lobby" in bat


def test_windows_scripts_use_the_same_settings():
    with open(os.path.join(SERVER, "windows", "host-room.ps1"), encoding="utf-8") as f:
        ps1 = f.read()
    for text in ['"--max_members"', '"--preferred-app", "Monster Hunter 4 Ultimate"',
                 '"--preferred-app-id", "0004000000126300"', '"--ban-list-file"',
                 '"--web-api-url"', "port = 24872", "max_members = 16", "CitraRoom-BanList-1",
                 "azahar-room.exe", "MH4U Server"]:
        assert text in ps1, text
    with open(os.path.join(SERVER, "windows", "Host room.bat"), encoding="utf-8") as f:
        assert "host-room.ps1" in f.read()
    with open(os.path.join(SERVER, "windows", "Run lobby.bat"), encoding="utf-8") as f:
        bat = f.read()
    assert r"..\lobby\mh4u_lobby.py" in bat and r"MH4U Server\lobby" in bat


def test_e2e_script_command_line():
    r = subprocess.run([sys.executable, os.path.join(SERVER, "tests", "e2e_room.py"), "--help"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and "--room-bin" in r.stdout
