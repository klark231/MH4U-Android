#!/usr/bin/env python3
"""MH4U lobby server: the list of public network rooms that Azahar shows, and a check of names.

MH4U's local play (the Gathering Hall) goes over Azahar's network rooms: a room (azahar-room,
or Azahar's Multiplayer > Create Room) relays all the game traffic between the players, over
UDP. None of that comes here. This server lists the rooms that announce themselves, for
Multiplayer > Browse Public Rooms, and vouches for players' names, so that a room can show who
is who and its host can ban a name rather than an address. It speaks the web API that Azahar's
multiplayer code calls (Azahar's src/web_service and src/network/announce_multiplayer_session):

    GET    /lobby                    the rooms (anyone)
    POST   /lobby                    a room announces itself (signed in; the address the
                                     announcement comes from is the address players get)
    POST   /lobby/<id>               ... then its players, every 15 s (its owner only)
    DELETE /lobby/<id>               ... and closes (its owner only)
    POST   /jwt/internal             a sign-in token (HS256 JWT) for headers x-username, x-token
    POST   /jwt/external/<room uid>  a statement of your name for one room (RS256 JWT)
    GET    /jwt/external/key.pem     the public key the rooms check those statements with
    GET    /profile                  the name you are signed in as
    GET    /health                   "ok", for a container's health check (not logged)
    GET    /ip                       the caller's address as the lobby sees it, and where a room
                                     announced from there would be listed (the port's app shows
                                     a host the address to give friends)

A player's name here is their Azahar username (Emulation > Configure > System), so names follow
Azahar's rule for nicknames: 4 to 20 letters, digits, spaces, '.', '_' or '-'. In open mode the
first sign-in with a name claims it for that token; in invite mode only names the admin added
(add-user) or that were claimed with an invite token (invite) sign in. Tokens are kept only as
salted hashes. A room stays listed while it keeps announcing itself (--room-ttl seconds at most
without news). What a room sends is cleaned to what every Azahar client can show. Connections,
requests, sign-ins, room tokens and rooms are limited per client address and per name. The data
folder (--data) holds users.json, the RSA key (made on the first start) and the sign-in tokens'
secret: keep it, or running rooms stop recognising names.

    python3 mh4u_lobby.py [--port 24872] [--data DIR] [--mode open|invite] [...]   the server
    python3 mh4u_lobby.py add-user NAME [--token T] [--moderator]    prints NAME's token
    python3 mh4u_lobby.py remove-user NAME [--free] | list-users | invite

Standard library only (Python 3.10+). How to run it and point Azahar at it: docs/SERVER.md.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import errno
import hashlib
import hmac
import http.client
import io
import ipaddress
import json
import math
import os
import re
import secrets
import signal
import sys
import threading
import time
import traceback
import unicodedata
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_PORT = 24872         # TCP; Azahar's rooms use UDP 24872 by default: one number for both
ROOM_TTL = 60                # seconds a room stays listed without news (Azahar sends it every 15 s)
NICKNAME = re.compile(r"[A-Za-z0-9._\- ]{4,20}")  # Azahar's (multiplayer/validation.h, room.cpp)
PBKDF2_ITERATIONS = 100_000  # for the stored token hashes
SIGN_IN_SECONDS = 3600       # a sign-in token's life (Azahar asks for a new one when it gets 401)
ROOM_TOKEN_SECONDS = 3600    # a name statement's life (a room checks it when the player joins)
# Connections: a client can't hold more than a few, nor keep one by sending a byte now and then
MAX_CONNECTIONS = 256        # at once, fewer when the process may open fewer files
MAX_CONNECTIONS_PER_ADDRESS = 16
HEAD_SECONDS = 10.0          # a request's line and headers arrive within this (idle time included)
BODY_SECONDS = 10.0          # ... then its body
SEND_SECONDS = 10.0          # ... and the client takes the answer within this
MAX_HEAD = 16 * 1024         # bytes of request line and headers
MAX_BODY = 64 * 1024
ACCEPT_BACKOFF = 0.5         # seconds to wait when the process is out of file descriptors
# The room list: Azahar's own limits (validation.h, Android's NetPlayDialog.kt, network/room.h)
MAX_ROOMS = 500
MAX_ROOMS_PER_ADDRESS = 10
MAX_ROOMS_PER_NAME = 10
MAX_PLAYERS = 254
ROOM_NAME_LENGTH = 20
U32, U64 = 2**32 - 1, 2**64 - 1

JSON = "application/json; charset=utf-8"
HTML = "text/html; charset=utf-8"     # what Azahar wants for both kinds of JWT
PLAIN = "text/plain; charset=utf-8"   # ... and for the public key


LOG_MUTEX = threading.Lock()


def log_to_stderr(line: str):
    # one write per line, one thread at a time: lines of requests at once don't mix
    with LOG_MUTEX:
        sys.stderr.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")
        sys.stderr.flush()


# ── RSA for RS256: key generation, PKCS#1 v1.5 signatures, DER and PEM ──────────────────────

SMALL_PRIMES = [p for p in range(2, 2000) if all(p % d for d in range(2, math.isqrt(p) + 1))]
SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")  # RFC 8017 9.2
RSA_ENCRYPTION = bytes.fromhex("06092a864886f70d010101")  # DER OID 1.2.840.113549.1.1.1


def is_probable_prime(n: int, rounds: int = 40) -> bool:
    """Trial division, then Miller-Rabin with random bases."""
    if n < 2:
        return False
    for p in SMALL_PRIMES:
        if n % p == 0:
            return n == p
    d, s = n - 1, 0
    while d % 2 == 0:
        d, s = d // 2, s + 1
    for _ in range(rounds):
        x = pow(secrets.randbelow(n - 3) + 2, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def random_prime(bits: int) -> int:
    while True:
        # the top two bits set, so that two of these multiply to a full 2 * bits
        n = secrets.randbits(bits) | (3 << (bits - 2)) | 1
        if is_probable_prime(n):
            return n


def der(tag: int, content: bytes) -> bytes:
    """One DER element: tag, length (short or long form), content."""
    n = len(content)
    if n < 0x80:
        return bytes([tag, n]) + content
    size = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([tag, 0x80 | len(size)]) + size + content


def der_int(value: int) -> bytes:
    # big-endian two's complement: a leading zero byte when the top bit is set keeps it positive
    return der(0x02, value.to_bytes(value.bit_length() // 8 + 1, "big"))


def der_read(data: bytes, pos: int):
    """The DER element at pos: (tag, content, position after it)."""
    tag, n = data[pos], data[pos + 1]
    pos += 2
    if n & 0x80:
        size = n & 0x7F
        n, pos = int.from_bytes(data[pos:pos + size], "big"), pos + size
    if pos + n > len(data):
        raise ValueError("truncated DER")
    return tag, data[pos:pos + n], pos + n


def pem_encode(label: str, data: bytes) -> str:
    b64 = base64.b64encode(data).decode()
    lines = [f"-----BEGIN {label}-----", *(b64[i:i + 64] for i in range(0, len(b64), 64)),
             f"-----END {label}-----"]
    return "\n".join(lines) + "\n"


def pem_decode(text: str, label: str) -> bytes:
    begin, end = f"-----BEGIN {label}-----", f"-----END {label}-----"
    start = text.index(begin) + len(begin)
    return base64.b64decode("".join(text[start:text.index(end, start)].split()))


class RSAKey:
    """An RSA key pair: RS256 signatures (RSASSA-PKCS1-v1_5 with SHA-256, RFC 8017 8.2) and its
    PEM forms, "RSA PRIVATE KEY" (PKCS#1) for the data folder and "PUBLIC KEY"
    (SubjectPublicKeyInfo, RFC 5280) for the rooms, which read it with OpenSSL's
    PEM_read_bio_PUBKEY (cpp-jwt)."""

    def __init__(self, n: int, e: int, d: int, p: int, q: int):
        if p * q != n or pow(pow(2, e, n), d, n) != 2:
            raise ValueError("not a consistent RSA key")
        self.n, self.e, self.d, self.p, self.q = n, e, d, p, q
        self.dp, self.dq, self.qinv = d % (p - 1), d % (q - 1), pow(q, -1, p)
        self.size = (n.bit_length() + 7) // 8

    @classmethod
    def generate(cls, bits: int = 2048, e: int = 65537) -> "RSAKey":
        while True:
            p, q = random_prime(bits // 2), random_prime(bits - bits // 2)
            if p != q and math.gcd(e, (p - 1) * (q - 1)) == 1:
                return cls(p * q, e, pow(e, -1, math.lcm(p - 1, q - 1)), p, q)

    def sign(self, message: bytes) -> bytes:
        digest_info = SHA256_DIGEST_INFO + hashlib.sha256(message).digest()
        padded = b"\x00\x01" + b"\xff" * (self.size - len(digest_info) - 3) + b"\x00" + digest_info
        m = int.from_bytes(padded, "big")
        # blinded by a random r, so that the time this takes says nothing about the key
        r = secrets.randbelow(self.n - 2) + 2
        while math.gcd(r, self.n) != 1:
            r = secrets.randbelow(self.n - 2) + 2
        c = m * pow(r, self.e, self.n) % self.n
        # Chinese remainder theorem: two half-size powers instead of one full-size one
        m1, m2 = pow(c, self.dp, self.p), pow(c, self.dq, self.q)
        s = (m2 + self.qinv * (m1 - m2) % self.p * self.q) * pow(r, -1, self.n) % self.n
        if pow(s, self.e, self.n) != m:  # a miscalculation must never leave the server
            raise RuntimeError("RSA signature failed its check")
        return s.to_bytes(self.size, "big")

    def public_pem(self) -> str:
        public_key = der(0x30, der_int(self.n) + der_int(self.e))
        algorithm = der(0x30, RSA_ENCRYPTION + der(0x05, b""))
        return pem_encode("PUBLIC KEY", der(0x30, algorithm + der(0x03, b"\x00" + public_key)))

    def private_pem(self) -> str:
        values = (0, self.n, self.e, self.d, self.p, self.q, self.dp, self.dq, self.qinv)
        return pem_encode("RSA PRIVATE KEY", der(0x30, b"".join(der_int(v) for v in values)))

    @classmethod
    def from_pem(cls, text: str) -> "RSAKey":
        _, body, _ = der_read(pem_decode(text, "RSA PRIVATE KEY"), 0)
        values, pos = [], 0
        while pos < len(body):
            _, value, pos = der_read(body, pos)
            values.append(int.from_bytes(value, "big"))
        return cls(*values[1:6])


# ── JWT (RFC 7519) ──────────────────────────────────────────────────────────────────────────

def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def jwt_encode(claims: dict, alg: str, sign) -> str:
    """header.claims.signature, each part base64url without padding."""
    head = b64url(json.dumps({"alg": alg, "typ": "JWT"}, separators=(",", ":")).encode())
    body = b64url(json.dumps(claims, separators=(",", ":")).encode())
    return f"{head}.{body}.{b64url(sign(f'{head}.{body}'.encode()))}"


def hs256(secret: bytes):
    return lambda data: hmac.new(secret, data, hashlib.sha256).digest()


def jwt_hs256_claims(token: str, secret: bytes, now: float) -> dict | None:
    """The claims of one of this lobby's sign-in tokens, if it is genuine and hasn't expired."""
    try:
        head, body, signature = token.split(".")
        if not hmac.compare_digest(hs256(secret)(f"{head}.{body}".encode()), unb64url(signature)):
            return None
        header, claims = json.loads(unb64url(head)), json.loads(unb64url(body))
    except (ValueError, RecursionError):
        return None
    if not (isinstance(header, dict) and header.get("alg") == "HS256" and isinstance(claims, dict)):
        return None
    exp = claims.get("exp")
    return claims if type(exp) is int and exp > now else None


# ── Files in the data folder ────────────────────────────────────────────────────────────────

def write_file(path: str, text: str, private: bool = False):
    """Writes path whole or not at all, and to the disk before it counts: a new file, synced,
    renamed over the old one, and the folder synced (POSIX)."""
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if private else 0o644)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    for attempt in range(50):
        try:
            os.replace(tmp, path)
            break
        except PermissionError:  # Windows, while another process reads the old file
            if attempt == 49:
                raise
            time.sleep(0.02)
    if os.name == "posix":
        folder = os.open(os.path.dirname(os.path.abspath(path)), os.O_RDONLY)
        try:
            os.fsync(folder)
        finally:
            os.close(folder)


@contextlib.contextmanager
def file_lock(path: str, timeout: float = 10.0):
    """A lock file: the server and the user commands change users.json one at a time."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            break
        except FileExistsError:
            with contextlib.suppress(FileNotFoundError):
                if time.time() - os.stat(path).st_mtime > 30:  # left by a process that died
                    os.remove(path)
                    continue
            if time.monotonic() > deadline:
                raise RuntimeError(f"{path} stays locked (remove it if no lobby command runs)")
            time.sleep(0.02)
    try:
        yield
    finally:
        os.close(fd)
        os.remove(path)


def name_key(name: str) -> str:
    """Names that only look different are one name: capitals and runs of spaces don't count."""
    return " ".join(name.split()).casefold()


def new_name(name: str) -> bool:
    """A name someone may take: Azahar's rule for nicknames, without spaces at either end or two
    in a row (it would look like another name)."""
    return bool(NICKNAME.fullmatch(name)) and name == " ".join(name.split())


def invite_hash(token: bytes) -> str:
    return hashlib.sha256(token).hexdigest()  # invite tokens are random: no need for a slow hash


class Users:
    """The names this lobby vouches for, in users.json:
        {"users": {name key: {"name", "salt", "iterations", "hash", "moderator", "since"}},
         "invites": {SHA-256 of an unused invite token: since},
         "blocked": {name key: since}}          (names nobody may claim: remove-user)
    A token is kept only as a salted PBKDF2-SHA256 hash. The server and the user commands may
    run at once: a change re-reads the file under a lock file and replaces it whole, and the
    server reads it again whenever it has changed on disk."""

    PARTS = ("users", "invites", "blocked")

    def __init__(self, folder: str):
        self.path = os.path.join(folder, "users.json")
        self.mutex = threading.Lock()
        self.stamp = None
        self.data = {part: {} for part in self.PARTS}

    def _load(self):
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            self.data, self.stamp = {part: {} for part in self.PARTS}, None
            return
        stamp = (st.st_ino, st.st_mtime_ns, st.st_size)
        if stamp != self.stamp:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self.data = {part: data.get(part, {}) for part in self.PARTS}
            self.stamp = stamp

    def snapshot(self) -> dict:
        with self.mutex:
            self._load()
            return self.data  # replaced, never changed in place, once handed out

    def change(self, edit):
        """edit(data) on the newest users.json, then written back; returns what edit returns."""
        with self.mutex, file_lock(self.path + ".lock"):
            self.stamp = None
            self._load()  # a new dict, so that earlier snapshots stay as they were
            result = edit(self.data)
            write_file(self.path, json.dumps(self.data, indent=1, sort_keys=True) + "\n", True)
            self.stamp = None
            return result

    def get(self, name) -> dict | None:
        return self.snapshot()["users"].get(name_key(name)) if isinstance(name, str) else None

    @staticmethod
    def record(name: str, token: bytes, moderator: bool, now: float) -> dict:
        salt = secrets.token_bytes(16)
        return {"name": name, "salt": salt.hex(), "iterations": PBKDF2_ITERATIONS,
                "hash": hashlib.pbkdf2_hmac("sha256", token, salt, PBKDF2_ITERATIONS).hex(),
                "moderator": moderator, "since": int(now)}

    @staticmethod
    def matches(user: dict, name: str, token: bytes) -> bool:
        digest = hashlib.pbkdf2_hmac("sha256", token, bytes.fromhex(user["salt"]),
                                     user["iterations"]).hex()
        # the exact name: "LUIS" isn't "Luis", and can't be claimed by someone else either
        return user["name"] == name and hmac.compare_digest(digest, user["hash"])

    def claim(self, name: str, token: bytes, now: float, invite_only: bool) -> dict | None:
        """A name nobody has yet, for this token; in invite mode only with an unused invite."""
        snapshot = self.snapshot()
        if name_key(name) in snapshot["blocked"]:
            return None
        if invite_only and invite_hash(token) not in snapshot["invites"]:
            return None

        def edit(data):
            user = data["users"].get(name_key(name))
            if user is not None:  # someone else got there first
                return user if self.matches(user, name, token) else None
            if name_key(name) in data["blocked"]:
                return None
            if invite_only and data["invites"].pop(invite_hash(token), None) is None:
                return None
            user = data["users"][name_key(name)] = self.record(name, token, False, now)
            return user
        return self.change(edit)

    def add(self, name: str, token: bytes, moderator: bool, now: float) -> bool:
        """Adds a name, or gives it a new token; True if it was there already."""
        def edit(data):
            existed = name_key(name) in data["users"]
            data["blocked"].pop(name_key(name), None)
            data["users"][name_key(name)] = self.record(name, token, moderator, now)
            return existed
        return self.change(edit)

    def remove(self, name: str, now: float, free: bool = False) -> bool:
        """Removes a name and blocks it, so that nobody claims it (free: anyone may again);
        True if it had a user."""
        def edit(data):
            if free:
                data["blocked"].pop(name_key(name), None)
            else:
                data["blocked"][name_key(name)] = int(now)
            return data["users"].pop(name_key(name), None) is not None
        return self.change(edit)

    def add_invite(self, token: bytes, now: float):
        self.change(lambda data: data["invites"].__setitem__(invite_hash(token), int(now)))


# ── Requests ────────────────────────────────────────────────────────────────────────────────

class Refused(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


class Request:
    def __init__(self, method: str, path: str, headers, body: bytes, address: str,
                 peer: str = ""):
        self.method, self.path, self.headers, self.body = method, path, headers, body
        self.address = address  # the client's IP address (the proxy's client, with --trust-proxy)
        self.peer = peer or address  # the address the connection comes from
        self.user = None        # the name signed in, for the log
        self.note = ""          # what happened, for the log
        self.quiet = False      # no log line (health checks; refusals after the first)


def json_bytes(value) -> bytes:
    # UTF-8 as it is rather than \u escapes: a half to a third of the size outside ASCII
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def json_reply(value, status: int = 200):
    return status, JSON, json_bytes(value)


class RateLimit:
    """At most `count` events in each window of `seconds` for each key (a client's address, or
    a name). Fixed windows: a few numbers per key, however many events."""

    def __init__(self, count: int, seconds: float):
        self.count, self.seconds = count, seconds
        self.windows = {}  # key: [start, events]
        self.mutex = threading.Lock()

    def _window(self, key, now, make: bool):
        window = self.windows.get(key)
        if window is not None and now - window[0] >= self.seconds:
            window = None
        if window is None and make:
            if len(self.windows) >= 10000:  # forget the windows that have ended
                self.windows = {k: w for k, w in self.windows.items() if now - w[0] < self.seconds}
            window = self.windows[key] = [now, 0]
        return window

    def full(self, key, now) -> bool:
        with self.mutex:
            window = self._window(key, now, False)
            return (window[1] if window else 0) >= self.count

    def add(self, key, now) -> int:
        """Counts an event; returns how many its window has had."""
        with self.mutex:
            window = self._window(key, now, True)
            window[1] += 1
            return window[1]


def plain_ip(text: str) -> str | None:
    """An IP address in its usual form ('::ffff:1.2.3.4' is '1.2.3.4'), or None; an IPv6
    address with a zone (fe80::1%eth0) is refused: the zone can be any text."""
    try:
        ip = ipaddress.ip_address(text.strip())
    except ValueError:
        return None
    if ip.version == 6:
        if ip.scope_id is not None:
            return None
        if ip.ipv4_mapped:
            ip = ip.ipv4_mapped
    return str(ip)


def is_local(address: str) -> bool:
    """A loopback, private or link-local IP address."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


def printable(text: str, limit: int = 200) -> str:
    """For the log: one line, whatever a client sent."""
    return "".join(c if c.isprintable() else "?" for c in text[:limit])


# What a room sends (Azahar's to_json(Room) and to_json(Room::Member)), checked field by field:
# every client that lists the rooms parses all of them, and a wrong type there throws.

def clean_line(text: str) -> str:
    """One line of text as every Azahar client can show it: no control or format characters
    (bidi overrides, zero-width ones), no unpaired surrogates (Azahar's JSON parser refuses the
    whole list over one), no '|' (Azahar's Android app joins a room's fields with it and splits
    them again), and spaces collapsed."""
    kept = "".join(" " if c == "|" or unicodedata.category(c) == "Cc" else c
                   for c in text if unicodedata.category(c) not in ("Cf", "Cs"))
    return " ".join(kept.split())


def clean_text(value, field: str, limit: int, lines: int = 1) -> str:
    """value as clean text: one line, or up to `lines` lines; at most limit characters."""
    if not isinstance(value, str):
        raise Refused(400, f"{field} must be a string")
    if lines > 1:
        text = "\n".join([line for line in map(clean_line, value.splitlines()) if line][:lines])
    else:
        text = clean_line(value)
    return text[:limit].rstrip()


def whole_number(value, field: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise Refused(400, f"{field} must be a whole number from {low} to {high}")
    return value


def parse_json(body: bytes):
    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, RecursionError):
        raise Refused(400, "the body isn't JSON") from None


def avatar_url(value) -> str:
    """An avatar's address, if it is a plain http(s) one."""
    ok = (isinstance(value, str) and value.isascii() and len(value) <= 256 and
          re.fullmatch(r"https?://[^\s|]+", value))
    return value if ok else ""


def parse_players(value, limit: int) -> list:
    """The players a room reports, at most limit of them. Azahar's rooms take only nicknames by
    Azahar's rule, and checked usernames are this lobby's names: a player with another nickname
    is left out, another username is dropped (the player is shown unchecked)."""
    if not isinstance(value, list) or len(value) > MAX_PLAYERS:
        raise Refused(400, f"players must be a list of at most {MAX_PLAYERS}")
    players = []
    for item in value:
        if not isinstance(item, dict):
            raise Refused(400, "a player must be an object")
        try:
            nickname, game_name = item["nickname"], item["gameName"]
            game_id = whole_number(item["gameId"], "gameId", 0, U64)
        except KeyError as missing:
            raise Refused(400, f"a player has no {missing}") from None
        if not (isinstance(nickname, str) and NICKNAME.fullmatch(nickname)):
            continue
        player = {"nickname": nickname, "gameName": clean_text(game_name, "gameName", 128),
                  "gameId": game_id}
        username = item.get("username", "")
        if isinstance(username, str) and NICKNAME.fullmatch(username):
            # Azahar reads a player's username only together with an avatarUrl
            player["username"] = username
            player["avatarUrl"] = avatar_url(item.get("avatarUrl", ""))
        players.append(player)
    return players[:limit]


def parse_room(body: bytes) -> dict:
    data = parse_json(body)
    if not isinstance(data, dict):
        raise Refused(400, "a room must be a JSON object")
    try:
        game_name, game_id = data["preferredGameName"], data["preferredGameId"]
        room = {"name": clean_text(data["name"], "name", ROOM_NAME_LENGTH),
                "description": clean_text(data.get("description", ""), "description", 1000, 10),
                "port": whole_number(data["port"], "port", 1, 65535),
                "preferredGameName": clean_text(game_name, "preferredGameName", 128),
                "preferredGameId": whole_number(game_id, "preferredGameId", 0, U64),
                "maxPlayers": whole_number(data["maxPlayers"], "maxPlayers", 1, MAX_PLAYERS),
                "netVersion": whole_number(data["netVersion"], "netVersion", 0, U32),
                "hasPassword": data["hasPassword"]}
        room["players"] = parse_players(data.get("players", []), room["maxPlayers"])
    except KeyError as missing:
        raise Refused(400, f"the room has no {missing}") from None
    if type(room["hasPassword"]) is not bool:
        raise Refused(400, "hasPassword must be true or false")
    if not room["name"]:
        raise Refused(400, "the room has no name")
    return room


def public(room: dict) -> dict:
    """A room as the lobby lists it (Azahar's from_json(Room)): without the lobby's own notes."""
    shown = {key: value for key, value in room.items() if not key.startswith("_")}
    if not shown["description"]:
        del shown["description"]
    return shown


# ── The lobby ───────────────────────────────────────────────────────────────────────────────

class Lobby:
    """The API, apart from HTTP: rooms in memory, names in users.json, keys in the data folder.
    clock gives the time in seconds (tests pass their own); log gets one line per event."""

    def __init__(self, folder: str, mode: str = "open", room_ttl: float = ROOM_TTL,
                 trust_proxy: bool = False, games=(), public_address: str = "",
                 clock=time.time, log=None, key_bits: int = 2048):
        os.makedirs(folder, mode=0o700, exist_ok=True)
        self.folder, self.mode, self.room_ttl = folder, mode, room_ttl
        self.trust_proxy, self.games, self.public_address = trust_proxy, set(games), public_address
        self.clock, self.log = clock, log or log_to_stderr
        self.users = Users(folder)
        self.key = self._key(key_bits)
        self.public_pem = self.key.public_pem()
        self.secret = self._secret()
        self.rooms = {}
        self.listing = None  # GET /lobby's answer, kept until a room changes
        self.mutex = threading.Lock()
        # what one client address, or one name, may do in a while (docs/SERVER.md, Limits)
        self.requests = RateLimit(600, 60)        # requests per address
        self.failures = RateLimit(20, 600)        # failed sign-ins per address
        self.sign_ins = RateLimit(60, 600)        # sign-ins (a PBKDF2 each) per address,
        self.name_sign_ins = RateLimit(30, 600)   # ... and per name
        self.new_names = RateLimit(10, 3600)      # names claimed per address
        self.tokens = RateLimit(120, 600)         # room tokens (an RSA signature each) per address,
        self.name_tokens = RateLimit(60, 600)     # ... and per name
        self.notes = RateLimit(1, 60)             # a line about refusals, once a minute each

    def _key(self, bits: int) -> RSAKey:
        path = os.path.join(self.folder, "lobby_key.pem")
        if os.path.exists(path):
            with open(path, encoding="ascii") as f:
                return RSAKey.from_pem(f.read())
        self.log(f"making the lobby's RSA key ({bits} bits; once): {path}")
        key = RSAKey.generate(bits)
        write_file(path, key.private_pem(), private=True)
        return key

    def _secret(self) -> bytes:
        path = os.path.join(self.folder, "jwt_secret")
        if not os.path.exists(path):
            write_file(path, secrets.token_hex(32) + "\n", private=True)
        with open(path, encoding="ascii") as f:
            return bytes.fromhex(f.read().strip())

    def note_once(self, key, line: str):
        """A line for the log, at most once a minute for each key (refusals come in floods)."""
        if self.notes.add(key, self.clock()) == 1:
            self.log(line)

    def client_address(self, peer: str, headers) -> str:
        """The client's IP address. With --trust-proxy, a reverse proxy on this machine or its
        network (loopback or private address) gives it as the last X-Forwarded-For entry, over
        all such headers (HAProxy adds one of its own); it must be a global address."""
        address = plain_ip(peer)
        if address is None:
            return printable(peer)
        if self.trust_proxy and is_local(address):
            values = headers.get_all("X-Forwarded-For") if hasattr(headers, "get_all") else \
                [headers.get("X-Forwarded-For")]
            forwarded = plain_ip(",".join(v for v in values or () if v).split(",")[-1])
            if forwarded and ipaddress.ip_address(forwarded).is_global:
                address = forwarded
        return address

    def listed_address(self, address: str) -> str:
        """Where players reach a room that announces itself from this address."""
        if self.public_address and is_local(address):
            return self.public_address  # a room on the lobby's machine or network
        return address

    def expire(self, now: float):
        with self.mutex:
            for room in [r for r in self.rooms.values() if now - r["_updated"] > self.room_ttl]:
                del self.rooms[room["id"]]
                self.listing = None
                self.log(f"room {json.dumps(room['name'], ensure_ascii=False)} of {room['owner']}"
                         f" at {room['address']}:{room['port']} went quiet: no longer listed")

    ROUTES = [
        ("GET", r"/lobby", "list_rooms"),
        ("POST", r"/lobby", "register"),
        ("POST", r"/lobby/([A-Za-z0-9_-]{1,64})", "update"),
        ("DELETE", r"/lobby/([A-Za-z0-9_-]{1,64})", "close"),
        ("POST", r"/jwt/internal", "sign_in"),
        ("GET", r"/jwt/external/key\.pem", "public_key"),
        ("POST", r"/jwt/external/([A-Za-z0-9_-]{1,64})", "room_token"),
        ("GET", r"/profile", "profile"),
        ("GET", r"/health", "health"),
        ("GET", r"/ip", "your_address"),
        ("GET", r"/", "front_page"),
    ]

    def handle(self, req: Request):
        """One request: (status, content type, body)."""
        now = self.clock()
        count = self.requests.add(req.address, now)
        if count > self.requests.count:  # a flood: refused, and in the log once a minute
            req.quiet = count > self.requests.count + 1
            req.note = "too many requests from this address: refusing them for a minute"
            return json_reply({"error": "too many requests: wait a minute"}, 429)
        self.expire(now)
        path = req.path.split("?", 1)[0]
        found = False
        try:
            for method, pattern, name in self.ROUTES:
                match = re.fullmatch(pattern, path)
                if match:
                    found = True
                    if method == req.method:
                        return getattr(self, name)(req, now, *match.groups())
            raise Refused(405 if found else 404, "no such method here" if found else "no such page")
        except Refused as refused:
            req.note = req.note or refused.message
            return json_reply({"error": refused.message}, refused.status)

    def limit(self, req: Request, now: float, *limits):
        """(RateLimit, key) pairs: refuses the request if one is full, else counts it in all."""
        if any(rate.full(key, now) for rate, key in limits):
            raise Refused(429, "too many of these requests: wait a few minutes")
        for rate, key in limits:
            rate.add(key, now)

    def signed_in(self, req: Request, now: float) -> dict:
        scheme, _, token = (req.headers.get("Authorization") or "").partition(" ")
        claims = None
        if scheme.lower() == "bearer":
            claims = jwt_hs256_claims(token.strip(), self.secret, now)
        user = self.users.get(claims.get("sub")) if claims else None
        # a new token for the name (add-user again) changes its salt, and ends older sign-ins
        if user is None or user["salt"][:16] != claims.get("sid"):
            raise Refused(401, "sign in first")
        req.user = user["name"]
        return user

    @staticmethod
    def roles(user: dict) -> list:
        return ["user", "moderator"] if user.get("moderator") else ["user"]

    def sign_in(self, req: Request, now: float):
        name = req.headers.get("x-username") or ""
        token = (req.headers.get("x-token") or "").encode("latin-1", "replace")  # the raw bytes
        if self.failures.full(req.address, now):
            raise Refused(429, "too many failed sign-ins from this address: wait a few minutes")
        user = None
        if NICKNAME.fullmatch(name) and 0 < len(token) <= 256:
            req.user = name
            self.limit(req, now, (self.sign_ins, req.address), (self.name_sign_ins, name_key(name)))
            user = self.users.get(name)
            if user is None and new_name(name):
                if self.new_names.full(req.address, now):
                    raise Refused(429, "too many new names from this address: wait an hour")
                user = self.users.claim(name, token, now, invite_only=self.mode == "invite")
                if user is not None:
                    self.new_names.add(req.address, now)
                    req.note = "a new name"
            elif user is not None and not Users.matches(user, name, token):
                user = None
        if user is None:
            self.failures.add(req.address, now)
            raise Refused(401, "unknown name, or the wrong token for it")
        claims = {"iss": "mh4u-lobby", "sub": user["name"], "sid": user["salt"][:16],
                  "iat": int(now), "exp": int(now) + SIGN_IN_SECONDS}
        return 200, HTML, jwt_encode(claims, "HS256", hs256(self.secret)).encode()

    def room_token(self, req: Request, now: float, uid: str):
        """The statement a room checks when this player joins it (Azahar's VerifyUserJWT, with
        cpp-jwt: RS256, this issuer and audience, iat and jti present, exp not passed)."""
        user = self.signed_in(req, now)
        self.limit(req, now, (self.tokens, req.address), (self.name_tokens, name_key(user["name"])))
        claims = {"iss": "citra-core", "aud": f"external-{uid}", "iat": int(now),
                  "exp": int(now) + ROOM_TOKEN_SECONDS, "jti": str(uuid.uuid4()),
                  "username": user["name"], "displayName": user["name"], "avatarUrl": "",
                  "roles": self.roles(user)}
        return 200, HTML, jwt_encode(claims, "RS256", self.key.sign).encode()

    def public_key(self, req: Request, now: float):
        return 200, PLAIN, self.public_pem.encode()

    def profile(self, req: Request, now: float):
        user = self.signed_in(req, now)
        return json_reply({"username": user["name"], "displayName": user["name"],
                           "avatarUrl": "", "roles": self.roles(user)})

    def health(self, req: Request, now: float):
        req.quiet = ipaddress.ip_address(req.peer).is_loopback  # the container's own check
        return 200, PLAIN, b"ok\n"

    def your_address(self, req: Request, now: float):
        """{"ip": the caller's address, "public": where players would reach a room announced
        from it (the address itself when it's a global one, --public-address for one on the
        lobby's machine or network, else "": the lobby can't tell)}."""
        listed = self.listed_address(req.address)
        try:
            public = listed if listed != req.address or ipaddress.ip_address(listed).is_global else ""
        except ValueError:
            public = ""
        return json_reply({"ip": req.address, "public": public})

    def list_rooms(self, req: Request, now: float):
        with self.mutex:
            if self.listing is None:
                rooms = sorted(self.rooms.values(), key=lambda r: (name_key(r["name"]), r["id"]))
                self.listing = json_bytes({"rooms": [public(room) for room in rooms]})
            return 200, JSON, self.listing

    def unique_name(self, name: str) -> str:
        """Azahar's Android app keys the rooms by name, so one named like another listed room
        would hide it: the newer one gets a number. (With self.mutex held.)"""
        taken = {name_key(r["name"]) for r in self.rooms.values()}
        unique, n = name, 2
        while name_key(unique) in taken:
            suffix = f" ({n})"
            unique, n = name[:ROOM_NAME_LENGTH - len(suffix)].rstrip() + suffix, n + 1
        return unique

    def register(self, req: Request, now: float):
        user = self.signed_in(req, now)
        room = parse_room(req.body)
        if self.games and room["preferredGameId"] not in self.games:
            raise Refused(403, "this lobby lists rooms for other games")
        listed = self.listed_address(req.address)
        with self.mutex:
            # the same owner announcing a room at the same place again: the old one has closed
            place = (user["name"], listed, room["port"])
            for old in list(self.rooms.values()):
                if (old["owner"], old["address"], old["port"]) == place:
                    del self.rooms[old["id"]]
            if len(self.rooms) >= MAX_ROOMS:
                raise Refused(503, "the lobby is full")
            if sum(r["_from"] == req.address for r in self.rooms.values()) >= MAX_ROOMS_PER_ADDRESS:
                raise Refused(429, "too many rooms from this address")
            if sum(r["owner"] == user["name"] for r in self.rooms.values()) >= MAX_ROOMS_PER_NAME:
                raise Refused(429, "too many rooms for this name")
            asked = room["name"]
            room.update(name=self.unique_name(asked), id=secrets.token_urlsafe(12),
                        externalGuid=str(uuid.uuid4()), address=listed, owner=user["name"],
                        _from=req.address, _updated=now)
            self.rooms[room["id"]] = room
            self.listing = None
        req.note = (f"room {json.dumps(room['name'], ensure_ascii=False)} listed at "
                    f"{listed}:{room['port']}")
        if room["name"] != asked:
            req.note += " (another room has its name)"
        if listed != req.address:
            req.note += f" (announced from {req.address})"
        elif is_local(listed):
            where = ("this machine's own address" if ipaddress.ip_address(listed).is_loopback
                     else "a private address")
            req.note += f" ({where}: players elsewhere can't reach it; see --public-address)"
        return json_reply(public(room))

    def _own_room(self, user: dict, room_id: str) -> dict:
        room = self.rooms.get(room_id)
        if room is None:
            raise Refused(404, "no such room: announce it again")  # Azahar then registers again
        if room["owner"] != user["name"]:
            raise Refused(403, "not your room")
        return room

    def update(self, req: Request, now: float, room_id: str):
        user = self.signed_in(req, now)
        data = parse_json(req.body)
        listed = self.listed_address(req.address)
        with self.mutex:
            room = self._own_room(user, room_id)
            players = parse_players(data.get("players", []) if isinstance(data, dict) else None,
                                    room["maxPlayers"])
            if listed != room["address"]:  # the host's address changed: players follow
                req.note = f"room moved from {room['address']} to {listed}"
                room["address"], room["_from"] = listed, req.address
                self.listing = None
            if players != room["players"]:
                room["players"] = players
                self.listing = None
            room["_updated"] = now
            return json_reply(public(room))

    def close(self, req: Request, now: float, room_id: str):
        user = self.signed_in(req, now)
        with self.mutex:
            room = self._own_room(user, room_id)
            del self.rooms[room_id]
            self.listing = None
        req.note = f"room {json.dumps(room['name'], ensure_ascii=False)} closed"
        return json_reply({})

    def front_page(self, req: Request, now: float):
        with self.mutex:
            rooms = len(self.rooms)
            players = sum(len(r["players"]) for r in self.rooms.values())
        return 200, PLAIN, (
            f"MH4U lobby: {rooms} room(s), {players} player(s).\n"
            "To use it, in Azahar: Emulation > Configure > General > Network > Network Web API "
            "URL = this page's address; then Multiplayer > Browse Public Rooms.\n").encode()


# ── HTTP ────────────────────────────────────────────────────────────────────────────────────

class Deadline(io.RawIOBase):
    """A connection's input that ends at a deadline (Handler moves it), at whatever pace the
    client sends: a slow client can't hold a thread by sending a byte now and then."""

    def __init__(self, sock):
        self.sock, self.at = sock, time.monotonic() + HEAD_SECONDS

    def readable(self):
        return True

    def readinto(self, buffer):
        left = self.at - time.monotonic()
        if left <= 0:
            raise TimeoutError("the request took too long")
        self.sock.settimeout(left)
        return self.sock.recv_into(buffer)


class HeadLimit:
    """The request reader that http.server gets: the request line and headers together may
    take MAX_HEAD bytes (http.server alone would take 100 lines of 64 KiB); the body is read as
    it comes."""

    def __init__(self, reader):
        self.reader, self.left = reader, MAX_HEAD

    def readline(self, limit=-1):
        if self.left <= 0:
            raise http.client.LineTooLong("the request's head")  # 431, from http.server
        size = self.left if limit is None or limit < 0 else min(limit, self.left)
        line = self.reader.readline(size)
        self.left -= len(line)
        if self.left <= 0 and not line.endswith(b"\n"):
            raise http.client.LineTooLong("the request's head")
        return line

    def __getattr__(self, name):  # read() for the body, close()
        return getattr(self.reader, name)


class Handler(BaseHTTPRequestHandler):
    """One Lobby.handle() call and one log line per request. The log never shows a token: the
    path, the status, the name signed in and what happened."""

    protocol_version = "HTTP/1.1"
    server_version = "mh4u-lobby"
    sys_version = ""

    def setup(self):
        super().setup()
        self.rfile.close()  # the plain socket reader: replaced by one with a deadline and a limit
        self.input = Deadline(self.connection)
        self.rfile = HeadLimit(io.BufferedReader(self.input))

    def handle(self):
        try:
            super().handle()
        except http.client.HTTPException as error:  # a request line too long
            self.log_error("%s", error)
        except OSError:  # gone, or too slow
            pass

    def handle_one_request(self):
        self.input.at = time.monotonic() + HEAD_SECONDS  # a new request: its own time and room
        self.rfile.left = MAX_HEAD
        super().handle_one_request()

    def answer(self):
        lobby = self.server.lobby
        started = time.monotonic()
        peer = plain_ip(self.client_address[0]) or printable(self.client_address[0])
        req = Request(self.command, self.path, self.headers, b"",
                      lobby.client_address(self.client_address[0], self.headers), peer)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY:
            self.close_connection = True  # the body stays unread
            reply = json_reply({"error": "the body is too large, or of unknown length"}, 413)
        else:
            try:
                self.input.at = time.monotonic() + BODY_SECONDS
                req.body = self.rfile.read(length)
                if len(req.body) < length:
                    raise ConnectionError("the body ended early")
            except OSError:  # the client went silent, or away
                self.close_connection = True
                return
            try:
                reply = lobby.handle(req)
            except Exception:  # a bug: say so in the log and keep serving
                req.note = traceback.format_exc().strip().splitlines()[-1]
                reply = json_reply({"error": "internal error"}, 500)
        status, content_type, body = reply
        if status == 429:
            self.close_connection = True
        if not req.quiet:  # the log first: whoever reads it after the answer finds the line there
            line = f"{printable(req.address)} {req.method} {printable(req.path)} {status}"
            line += f" [{req.user}]" if req.user else ""
            line += f" {(time.monotonic() - started) * 1000:.0f}ms"
            lobby.log(line + (f" {printable(req.note, 300)}" if req.note else ""))
        self.connection.settimeout(SEND_SECONDS)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if self.command == "GET":  # public data: a web page may show it
            self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_DELETE = answer

    def log_request(self, code="-", size="-"):
        pass  # answer() logs every request it handles

    def log_error(self, format, *args):  # requests that never get to answer(): malformed, slow
        address = printable(self.client_address[0])
        self.server.lobby.note_once((address, format), f"{address} {printable(format % args)}")


def connection_limit() -> int:
    """MAX_CONNECTIONS, or fewer if the process may open fewer files."""
    try:
        import resource
        soft = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    except (ImportError, OSError, ValueError):
        return MAX_CONNECTIONS
    if soft == resource.RLIM_INFINITY:
        return MAX_CONNECTIONS
    return max(4, min(MAX_CONNECTIONS, soft - 32))  # room for the listener, logs and files


class LobbyServer(ThreadingHTTPServer):
    """One thread per connection, with a limit on connections in all and per client address
    (not for a reverse proxy's, with --trust-proxy: it limits its own clients)."""

    daemon_threads = True
    block_on_close = False  # a stop doesn't wait for slow clients

    def __init__(self, address, lobby: Lobby):
        self.lobby = lobby
        self.max_open, self.open, self.open_from = connection_limit(), 0, {}
        self.open_mutex = threading.Lock()
        super().__init__(address, Handler)

    def get_request(self):
        try:
            return super().get_request()
        except OSError as error:
            if error.errno in (errno.EMFILE, errno.ENFILE, errno.ENOBUFS, errno.ENOMEM):
                # out of file descriptors: accepting again at once would spin at full speed
                self.lobby.note_once("accept", f"can't take connections: {error.strerror}")
                time.sleep(ACCEPT_BACKOFF)
            raise

    def verify_request(self, request, client_address):
        peer = client_address[0]
        proxy = self.lobby.trust_proxy and is_local(plain_ip(peer) or "")
        with self.open_mutex:
            mine = self.open_from.get(peer, 0)
            if self.open < self.max_open and (proxy or mine < MAX_CONNECTIONS_PER_ADDRESS):
                self.open, self.open_from[peer] = self.open + 1, mine + 1
                return True
        address = printable(peer)
        self.lobby.note_once(("connections", address),
                             f"{address}: too many connections, refusing more for a while")
        with contextlib.suppress(OSError):
            request.setblocking(False)
            request.send(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\n"
                         b"Connection: close\r\n\r\n")
        return False

    def release(self, client_address):
        peer = client_address[0]
        with self.open_mutex:
            self.open -= 1
            self.open_from[peer] -= 1
            if not self.open_from[peer]:
                del self.open_from[peer]

    def process_request(self, request, client_address):
        try:
            super().process_request(request, client_address)
        except BaseException:  # no thread for it: the connection closes now
            self.release(client_address)
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.release(client_address)

    def handle_error(self, request, client_address):  # one line, not a traceback
        address = printable(client_address[0])
        self.lobby.note_once(("error", address),
                             f"{address}: {traceback.format_exc().strip().splitlines()[-1]}")


# ── Command line ────────────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    data = argparse.ArgumentParser(add_help=False)
    data.add_argument("--data", default=argparse.SUPPRESS, metavar="DIR",
                      help="the lobby's state: names, keys (default: $MH4U_LOBBY_DATA, "
                           "or lobby-data in the current folder)")
    parser = argparse.ArgumentParser(
        parents=[data], description="MH4U lobby server: the public room list and name checks "
        "for Azahar's network rooms (docs/SERVER.md). Without a command it runs the server.")
    parser.add_argument("--host", default="0.0.0.0", help="address to listen on (default: all)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"TCP port (default: {DEFAULT_PORT})")
    parser.add_argument("--mode", choices=("open", "invite"), default="open",
                        help="open: a name belongs to whoever signs in with it first (default); "
                             "invite: only names from add-user or an invite token")
    parser.add_argument("--room-ttl", type=float, default=ROOM_TTL, metavar="SECONDS",
                        help=f"a room leaves the list after this long without news ({ROOM_TTL})")
    parser.add_argument("--trust-proxy", action="store_true",
                        help="the client's address is the last X-Forwarded-For entry, when the "
                             "request comes from a loopback or private address (a reverse proxy)")
    parser.add_argument("--games", default="", metavar="IDS",
                        help="list only rooms for these games: program ids in hex, comma-separated "
                             "(MH4U USA: 0004000000126300)")
    parser.add_argument("--public-address", default="", metavar="HOST",
                        help="the address to list for rooms that announce themselves from a "
                             "loopback or private address, i.e. rooms on this machine or network "
                             "(a VPS running rooms next to the lobby); not for a LAN party")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    add = commands.add_parser("add-user", parents=[data],
                              help="add a name or give it a new token; prints the token")
    add.add_argument("name")
    add.add_argument("--token", help="the token (default: a new random one)")
    add.add_argument("--moderator", action="store_true",
                     help="moderator in every room that checks names with this lobby")
    remove = commands.add_parser("remove-user", parents=[data],
                                 help="remove a name, and block it so that nobody claims it")
    remove.add_argument("name")
    remove.add_argument("--free", action="store_true",
                        help="don't block it: anyone may claim it again (in open mode)")
    commands.add_parser("list-users", parents=[data], help="the names, invites and blocked names")
    commands.add_parser("invite", parents=[data],
                        help="prints a one-time token that claims a free name (for invite mode)")
    args = parser.parse_args(argv)
    folder = os.path.abspath(getattr(args, "data", None) or os.environ.get("MH4U_LOBBY_DATA")
                             or "lobby-data")
    os.makedirs(folder, mode=0o700, exist_ok=True)
    users = Users(folder)
    now = time.time()

    if args.command == "add-user":
        if not new_name(args.name):
            parser.error("a name is 4 to 20 letters, digits, spaces, '.', '_' or '-' (Azahar's "
                         "rule for usernames), with no space at either end or two in a row")
        token = args.token or secrets.token_urlsafe(18)
        existed = users.add(args.name, token.encode(), args.moderator, now)
        print(token)
        print(f"{'a new token for' if existed else 'added'} {args.name}"
              f"{' (moderator)' if args.moderator else ''}: in Azahar, Emulation > Configure > "
              "General > Network > Network Token (and this name as System > Username)",
              file=sys.stderr)
        return 0
    if args.command == "remove-user":
        had = users.remove(args.name, now, free=args.free)
        what = "removed" if had else "no user"
        rest = ("; anyone may claim it again" if args.free else
                "; the name is blocked: nobody can claim it (add-user gives it out again)")
        print(f"{what} {args.name}{rest}", file=sys.stderr)
        return 0
    if args.command == "list-users":
        snapshot = users.snapshot()
        for user in sorted(snapshot["users"].values(), key=lambda u: name_key(u["name"])):
            since = time.strftime("%Y-%m-%d", time.localtime(user["since"]))
            role = "moderator" if user["moderator"] else ""
            print(f"{user['name']:<20}  {role:<9}  since {since}")
        blocked = sorted(snapshot["blocked"])
        if blocked:
            print("blocked: " + ", ".join(blocked))
        print(f"{len(snapshot['users'])} name(s), {len(snapshot['invites'])} unused invite(s), "
              f"{len(blocked)} blocked")
        return 0
    if args.command == "invite":
        token = secrets.token_urlsafe(18)
        users.add_invite(token.encode(), now)
        print(token)
        print("one new player signs in with this token and a name nobody has (their Azahar "
              "username); it then stays that name's token", file=sys.stderr)
        return 0

    try:
        games = {int(game, 16) for game in args.games.replace(" ", "").split(",") if game}
    except ValueError:
        parser.error(f"--games: program ids in hex, comma-separated, not {args.games!r}")
    lobby = Lobby(folder, args.mode, args.room_ttl, args.trust_proxy, games, args.public_address)
    try:
        server = LobbyServer((args.host, args.port), lobby)
    except OSError as error:
        parser.error(f"can't listen on {args.host}:{args.port}: {error}")
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # docker stop
    mode = ("open: a name belongs to whoever signs in with it first" if args.mode == "open"
            else "invite: only names from add-user or an invite token")
    lobby.log(f"lobby on {args.host}:{server.server_address[1]} ({mode}); data in {folder}; "
              f"at most {server.max_open} connections")
    lobby.log("players set Emulation > Configure > General > Network > Network Web API URL to "
              f"http://<this machine's address>:{server.server_address[1]}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
