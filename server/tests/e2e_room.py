#!/usr/bin/env python3
"""End-to-end check of the lobby (server/lobby/mh4u_lobby.py) against Azahar's own code: a
real dedicated room (azahar-room) announces itself to a lobby started here, and each check
looks at what Azahar's client did. Run by hand (pytest doesn't collect it). It needs an
azahar-room built with Azahar's web service (ENABLE_WEB_SERVICE=ON, the default) and, for the
signature check, the openssl command. Nothing here touches game data.

    python3 server/tests/e2e_room.py [--room-bin PATH]
        (default: $AZAHAR_ROOM, or azahar-room on the PATH)

The checks:
 1. the room fetches the lobby's public key (text/plain) as it starts;
 2. it signs in (POST /jwt/internal) and announces itself: GET /lobby lists it with its name,
    description, port, game, member limit, password flag, owner and this machine's address;
 3. it reports its players right away and every 15 s after (POST /lobby/<id>);
 4. a token for its externalGuid, as a joining player would get it, verifies under openssl
    with the PEM the room fetched;
 5. after the lobby restarts and forgets its rooms, the room's next report gets 404 and the
    room announces itself again (up to 15 s);
 6. q + Enter closes the room: it leaves the lobby (DELETE /lobby/<id>) and saves its ban
    list file;
 7. a room that signs in with a taken name and another token is refused and stays unlisted;
 8. for the record (not a pass or fail): whether a Web API URL that says https:// still gets
    plain HTTP from Azahar's web client.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LOBBY = os.path.join(HERE, "..", "lobby", "mh4u_lobby.py")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
MH4U = 0x0004000000126300
results = []


def check(name: str, ok: bool, detail: str = ""):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}{f': {detail}' if detail else ''}", flush=True)


def http(method: str, url: str, headers=None):
    """(status, Content-Type, body); status None when nothing answers."""
    data = b"" if method == "POST" else None
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with OPENER.open(req, timeout=10) as reply:
            return reply.status, reply.headers.get("Content-Type", ""), reply.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get("Content-Type", ""), error.read()
    except OSError:
        return None, "", b""


def wait_for(condition, seconds: float):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(0.25)
    return None


def free_port(kind) -> int:
    with socket.socket(socket.AF_INET, kind) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


class LobbyProcess:
    def __init__(self, folder: str, port: int, log_path: str):
        self.url, self.log_path = f"http://127.0.0.1:{port}", log_path
        self.log = open(log_path, "a")
        self.proc = subprocess.Popen([sys.executable, LOBBY, "--host", "127.0.0.1", "--port",
                                      str(port), "--data", folder],
                                     stdout=self.log, stderr=subprocess.STDOUT)
        if not wait_for(lambda: http("GET", f"{self.url}/lobby")[0] == 200, 30):
            raise SystemExit(f"the lobby didn't start; its log:\n{read(log_path)}")

    def rooms(self) -> list:
        status, _, body = http("GET", f"{self.url}/lobby")
        return json.loads(body)["rooms"] if status == 200 else []

    def stop(self):
        self.proc.terminate()
        self.proc.wait(10)
        self.log.close()


class RoomProcess:
    def __init__(self, binary: str, args: list, folder: str, log_path: str):
        os.makedirs(folder, exist_ok=True)
        # Azahar keeps its user folder (and the room's log file) under these
        env = dict(os.environ, HOME=folder, XDG_DATA_HOME=os.path.join(folder, "data"),
                   XDG_CONFIG_HOME=os.path.join(folder, "config"),
                   XDG_CACHE_HOME=os.path.join(folder, "cache"))
        self.log_path = log_path
        self.log = open(log_path, "w")
        self.proc = subprocess.Popen([binary, *args], stdin=subprocess.PIPE, stdout=self.log,
                                     stderr=subprocess.STDOUT, cwd=folder, env=env)

    def output(self) -> str:
        return read(self.log_path)

    def close(self) -> int | None:
        """q + Enter, as at its console; the exit code, or None if it didn't stop."""
        try:
            self.proc.stdin.write(b"q\n")
            self.proc.stdin.flush()
            return self.proc.wait(20)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()
            return None
        finally:
            self.log.close()


def openssl_verifies(token: str, pem: str, folder: str) -> bool:
    head, body, signature = token.split(".")
    files = {"key.pem": pem.encode(), "signed": f"{head}.{body}".encode(),
             "signature": base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))}
    for name, content in files.items():
        with open(os.path.join(folder, name), "wb") as f:
            f.write(content)
    r = subprocess.run(["openssl", "dgst", "-sha256", "-verify", os.path.join(folder, "key.pem"),
                        "-signature", os.path.join(folder, "signature"),
                        os.path.join(folder, "signed")], capture_output=True, text=True)
    return r.returncode == 0 and "Verified OK" in r.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--room-bin", default=os.environ.get("AZAHAR_ROOM") or
                        shutil.which("azahar-room"), help="the azahar-room executable")
    parser.add_argument("--keep", action="store_true", help="keep the temporary folder and logs")
    args = parser.parse_args()
    if not args.room_bin or not os.path.isfile(args.room_bin):
        print("no azahar-room: give its path with --room-bin (or $AZAHAR_ROOM)")
        return 2
    version = subprocess.run([args.room_bin, "--version"], capture_output=True, text=True)
    print(f"azahar-room: {args.room_bin}\n  {version.stdout.strip()}")

    tmp = tempfile.mkdtemp(prefix="mh4u-lobby-e2e-")
    lobby_port, room_port = free_port(socket.SOCK_STREAM), free_port(socket.SOCK_DGRAM)
    lobby_log = os.path.join(tmp, "lobby.log")
    lobby = LobbyProcess(os.path.join(tmp, "lobby-data"), lobby_port, lobby_log)
    print(f"lobby: {lobby.url} (log {lobby_log})")
    ban_list = os.path.join(tmp, "room", "ban-list.txt")
    room_args = ["--room-name", "MH4U e2e Hall", "--room-description", "an end-to-end check",
                 "--port", str(room_port), "--max_members", "4", "--password", "e2e-password",
                 "--preferred-app", "Monster Hunter 4 Ultimate",
                 "--preferred-app-id", f"{MH4U:016X}", "--ban-list-file", ban_list,
                 "--web-api-url", lobby.url, "--username", "E2E Host", "--token", "e2e-host-token"]
    room = RoomProcess(args.room_bin, room_args, os.path.join(tmp, "room"),
                       os.path.join(tmp, "room.log"))
    print(f"room: UDP {room_port} (log {room.log_path})")
    try:
        listed = wait_for(lambda: lobby.rooms(), 20)
        if "Web Services is not available" in room.output():
            check("azahar-room was built with the web service", False,
                  "it says so itself: build it with ENABLE_WEB_SERVICE=ON")
            return 1

        # 1. the public key, fetched when the room starts (VerifyUserJWT)
        check("the room fetched the lobby's public key",
              "Fetched external JWT public key" in room.output(),
              "\"Fetched external JWT public key\" in its log")

        # 2. the announcement
        if not listed:
            check("the room is listed", False, f"room log:\n{room.output()}")
            return 1
        entry = listed[0]
        expected = {"name": "MH4U e2e Hall", "description": "an end-to-end check",
                    "port": room_port, "preferredGameName": "Monster Hunter 4 Ultimate",
                    "preferredGameId": MH4U, "maxPlayers": 4, "hasPassword": True,
                    "owner": "E2E Host", "address": "127.0.0.1", "players": []}
        wrong = {k: (entry.get(k), v) for k, v in expected.items() if entry.get(k) != v}
        check("the room is listed with the right fields", len(listed) == 1 and not wrong,
              json.dumps(entry) if wrong else
              f"{entry['address']}:{entry['port']}, owner {entry['owner']}, netVersion "
              f"{entry['netVersion']}, externalGuid {entry['externalGuid']}")
        check("netVersion and externalGuid", type(entry["netVersion"]) is int and
              bool(entry["externalGuid"]) and bool(entry["id"]))

        # 3. its reports (the first comes right after the announcement)
        update = f"POST /lobby/{entry['id']} 200 [E2E Host]"
        check("the room reports its players", bool(wait_for(lambda: update in read(lobby_log), 20)),
              update)

        # 4. what a joining player gets for this room, checked as the room checks it
        _, _, jwt = http("POST", f"{lobby.url}/jwt/internal",
                         {"x-username": "E2E Player", "x-token": "e2e-player-token"})
        status, kind, token = http("POST", f"{lobby.url}/jwt/external/{entry['externalGuid']}",
                                   {"Authorization": f"Bearer {jwt.decode()}"})
        pem = http("GET", f"{lobby.url}/jwt/external/key.pem")[2].decode()
        try:
            body = token.decode().split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        except (ValueError, IndexError):
            claims = {}
        check("a player's token for this room", status == 200 and "text/html" in kind and
              claims.get("aud") == f"external-{entry['externalGuid']}" and
              claims.get("iss") == "citra-core", f"HTTP {status}, aud {claims.get('aud')}")
        if shutil.which("openssl"):
            check("... verifies under openssl with the served PEM",
                  bool(claims) and openssl_verifies(token.decode(), pem, tmp))
        else:
            print("SKIP  openssl isn't installed: the signature wasn't checked with it")

        # 5. the lobby restarts: 404 for the room's next report, and it announces itself again
        lobby.stop()
        lobby = LobbyProcess(os.path.join(tmp, "lobby-data"), lobby_port, lobby_log)
        again = wait_for(lambda: lobby.rooms(), 25)
        check("after a lobby restart the room announces itself again",
              bool(again) and again[0]["id"] != entry["id"] and
              f"POST /lobby/{entry['id']} 404" in read(lobby_log),
              "404 for its report, then a new announcement" if again else "not listed again")
        entry = again[0] if again else entry

        # 6. closing it
        code = room.close()
        gone = wait_for(lambda: not lobby.rooms(), 10)
        check("q + Enter closes the room and it leaves the lobby",
              code == 0 and bool(gone) and f"DELETE /lobby/{entry['id']} 200" in read(lobby_log),
              f"exit code {code}")
        saved = os.path.exists(ban_list) and read(ban_list).startswith("CitraRoom-BanList-1")
        check("the room saved its ban list file", saved, ban_list)

        # 7. the room's name with another token: refused, and the room stays unlisted
        wrong = RoomProcess(args.room_bin, [
            "--room-name", "MH4U e2e impostor", "--port", str(free_port(socket.SOCK_DGRAM)),
            "--preferred-app", "Monster Hunter 4 Ultimate", "--preferred-app-id", f"{MH4U:016X}",
            "--web-api-url", lobby.url, "--username", "E2E Host", "--token", "not-its-token"],
            os.path.join(tmp, "room-wrong"), os.path.join(tmp, "room-wrong.log"))
        refused = wait_for(lambda: "UpdateJWT failed" in wrong.output(), 15)
        time.sleep(1)
        check("a room with someone else's name stays unlisted",
              bool(refused) and not lobby.rooms(), "\"UpdateJWT failed\" in its log")
        wrong.close()

        # 8. for the record: https:// in the Web API URL
        other_port = free_port(socket.SOCK_DGRAM)
        https_room = RoomProcess(args.room_bin, [
            "--room-name", "MH4U e2e https", "--port", str(other_port),
            "--preferred-app", "Monster Hunter 4 Ultimate", "--preferred-app-id", f"{MH4U:016X}",
            "--web-api-url", f"https://127.0.0.1:{lobby_port}", "--username", "E2E Https",
            "--token", "e2e-https-token"], os.path.join(tmp, "room-https"),
            os.path.join(tmp, "room-https.log"))
        plain = wait_for(lambda: any(r["port"] == other_port for r in lobby.rooms()), 15)
        https_room.close()
        print(f"NOTE  a Web API URL of https://127.0.0.1:{lobby_port} "
              + ("still reached this plain-HTTP lobby: Azahar's web client doesn't use TLS"
                 if plain else "did not reach this plain-HTTP lobby (TLS, or no connection)"))
    finally:
        if room.proc.poll() is None:
            room.proc.kill()
        lobby.stop()
        if not args.keep and all(results):
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print(f"logs and data kept in {tmp}")
    print(f"{sum(results)} of {len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
