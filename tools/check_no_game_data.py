#!/usr/bin/env python3
"""Refuse commits that contain game data or files derived from it.

Usage:
    check_no_game_data.py --staged          check what is staged in the index
    check_no_game_data.py --range A..B      check every (blob, path) of every commit in A..B
    check_no_game_data.py --range A...B     same, from the merge base of A and B

--range inspects every added or modified path of every commit of the range
(git log --raw, merges against each parent), not the net diff and not a
deduplicated blob list: a file added in one commit and removed in a later one is
still reported, and so is a forbidden name whose content also exists under a
clean name or already in the base.

Rules (each offending path is printed with the rule it broke; exit status 1):
    ci-pattern    the patterns of the guard in .github/workflows/ci.yml
    forbidden-dir anything under build/, baserom/ or export/ (at any depth)
    extension     .ppm, .bin and .wav files
    magic         files that start with the capture magics MGPU, MDSP or MHID
    size          files over 2 MB, unless config/large_files.txt lists
                  their path and SHA-256
    trace         text files with more than 50 lines that look like trace lines
    binary-ext    screenshots, ELF files (code.bin converted for Ghidra), archives, dumps and
                  GPU captures by name: .png .jpg .jpeg .bmp .tga .gif .elf .so .dll .exe
                  .zip .7z .gz .tar .xz .zst .dmp .mgpu .gcap (case-insensitive)
    binary-magic  the same kinds by content, so a renamed file is still caught: ELF, PNG,
                  JPEG, GIF, ZIP, gzip, 7z, xz and zstd headers
    binary-content any file with a NUL byte in its first 8 KiB or that is not valid UTF-8
                  (a UTF-8 BOM is fine), whatever its name
    listing       text files with more than 50 lines of hex dump (xxd, hexdump -C), objdump-style
                  disassembly or C byte arrays (16+ 0xNN entries on a line)
    dense-blob    a text file with a line of 1024+ characters made only of base64 or hex characters

The trace rule also covers the unmapped-access report of the runner (lines
'count=<n> addr=0x<hex> size=<n> rw=<read|write> lr=0x<hex>' under a '# unmapped v1' header).

Exemption: a file listed in config/large_files.txt (path and SHA-256 both matching) is
exempt from the size rule as before; the only newly exempted rules are binary-ext,
binary-magic, binary-content, listing and dense-blob (our own or vendored files, such as an icon
and its C byte-array form). The other rules are unchanged by the listing.

Trailing spaces, dots and newlines of a path component are ignored by the name rules (Windows
does the same); git replace refs are ignored; each (path, rule) is printed once.
Only the names and the rule are printed, never the content of a file.
Exit status: 0 clean, 1 offending files, 2 usage or git error.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

MAX_BYTES = 2 * 1024 * 1024
TRACE_LINE_LIMIT = 50
MAGICS = (b"MGPU", b"MDSP", b"MHID")
ALLOWLIST = "config/large_files.txt"

# Same expression as the grep in .github/workflows/ci.yml (case-insensitive).
CI_PATTERN = re.compile(
    r"\.(3ds|cci|cia|cxi|cfa|arc|sav)$|(^|/)(code|exheader|romfs|exefs)\.bin$|^baserom/",
    re.IGNORECASE,
)
FORBIDDEN_DIRS = frozenset({"build", "baserom", "export"})
FORBIDDEN_EXTENSIONS = frozenset({".ppm", ".bin", ".wav"})

BINARY_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".bmp", ".tga", ".gif", ".elf", ".so", ".dll", ".exe",
    ".zip", ".7z", ".gz", ".tar", ".xz", ".zst", ".dmp", ".mgpu", ".gcap",
)
BINARY_MAGICS = (
    (b"\x7fELF", "ELF"),
    (b"\x89PNG\r\n\x1a\n", "PNG"),
    (b"\xff\xd8\xff", "JPEG"),
    (b"GIF87a", "GIF"),
    (b"GIF89a", "GIF"),
    (b"PK\x03\x04", "ZIP"),
    (b"\x1f\x8b", "gzip"),
    (b"7z\xbc\xaf\x27\x1c", "7z"),
    (b"\xfd7zXZ\x00", "xz"),
    (b"\x28\xb5\x2f\xfd", "zstd"),
)
BINARY_RULES = ("binary-ext", "binary-magic", "binary-content", "listing", "dense-blob")

# Text listings of binary data (hex dumps, disassembly, C byte arrays): same limit as the traces.
_H = "[0-9A-Fa-f]"
LISTING_PATTERNS = tuple(
    re.compile(p)
    for p in (
        # hexdump -C / xxd: offset (4 to 8 digits), optional colon, 8+ byte groups or 4+ word groups
        rf"^\s*{_H}{{4,8}}:?(?:\s+{_H}{{2}}(?!{_H})){{8,}}",
        rf"^\s*{_H}{{4,8}}:?(?:\s+{_H}{{4}}(?!{_H})){{4,}}",
        # disassembly (objdump, gdb, Ghidra, IDA): optional section label and 0x prefix, address,
        # optional <symbol>, optional colon, one 4 or 8 digit word or 1 to 8 byte groups, mnemonic
        # (a lone two-digit hex token is a byte group, not a mnemonic)
        rf"^\s*(?:[A-Za-z_.][\w.]*:)?(?:0[xX])?{_H}+\s*(?:<[^>\n]*>)?:?"
        rf"(?:\s+{_H}{{4}}(?:{_H}{{4}})?(?!{_H})|(?:\s+{_H}{{2}}(?!{_H})){{1,8}})"
        rf"\s+(?!{_H}{{2}}(?:\s|$))[A-Za-z][A-Za-z0-9._]*(?:\s|$)",
        # C byte array: 16 or more 0xNN entries on one line
        rf"(?:0[xX]{_H}{{2}}[\s,]+){{15}}0[xX]{_H}{{2}}(?!{_H})",
    )
)
DENSE_LINE_MIN = 1024
DENSE_LINE = re.compile(r"[A-Za-z0-9+/=]{%d,}" % DENSE_LINE_MIN)

TRACE_PATTERNS = tuple(
    re.compile(p)
    for p in (
        r"^\d+ [0-9A-F]{2} pc=[0-9A-F]{8}",  # SVC trace
        r"^# thread ",  # thread lines
        r"seq=\d+ session=",  # IPC trace
        r"^f=\d+ t=\d+",  # state trace
        r"^\s*count=\d+\s+addr=(0x)?[0-9A-Fa-f]+\s+size=\d+\s+rw=(read|write)\b",  # unmapped report (prefix match: new fields keep matching)
        r"svc=0x",
        r"cmd=0x",
    )
)

Reader = Callable[[str], bytes]  # blob sha -> content


def git(*args: str, cwd: Optional[Path] = None, text: bool = False):
    # --no-replace-objects: a local refs/replace entry must not show the guard a substitute
    # commit or blob. GIT_SHALLOW_FILE / GIT_GRAFT_FILE=/dev/null: a local .git/shallow or
    # .git/info/grafts must not cut the history the guard walks.
    env = {**os.environ, "GIT_SHALLOW_FILE": os.devnull, "GIT_GRAFT_FILE": os.devnull}
    proc = subprocess.run(
        ["git", "--no-replace-objects", *args], cwd=cwd, capture_output=True, text=text,
        check=False, env=env,
    )
    if proc.returncode != 0:
        err = proc.stderr if text else proc.stderr.decode("utf-8", "replace")
        raise RuntimeError(f"git {' '.join(args)} failed: {err.strip()}")
    return proc.stdout


def split_z(data: bytes) -> list[str]:
    return [p.decode("utf-8", "surrogateescape") for p in data.split(b"\0") if p]


def load_allowlist(text: str) -> dict[str, str]:
    """Parse '<sha256> <path>' lines into {path: sha256}."""
    allowed: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) == 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            allowed[parts[1].strip()] = parts[0].lower()
    return allowed


def is_text(data: bytes) -> bool:
    return b"\0" not in data[:8192]


def count_trace_lines(data: bytes) -> int:
    count = 0
    for raw in data.decode("utf-8", "replace").splitlines():
        if any(p.search(raw) for p in TRACE_PATTERNS):
            count += 1
    return count


def count_listing_lines(data: bytes) -> int:
    count = 0
    for raw in data.decode("utf-8", "replace").splitlines():
        if any(p.search(raw) for p in LISTING_PATTERNS):
            count += 1
    return count


def has_dense_line(data: bytes) -> bool:
    return any(
        len(raw) >= DENSE_LINE_MIN and DENSE_LINE.search(raw)
        for raw in data.decode("utf-8", "replace").split("\n")
    )


def is_utf8(data: bytes) -> bool:
    try:
        data.decode("utf-8")  # a BOM decodes to U+FEFF and is fine
    except UnicodeDecodeError:
        return False
    return True


def normalize_path(path: str) -> str:
    """Strip what Windows ignores at the end of a path component (spaces, dots, newlines),
    so 'a.ppm ', 'b.wav.', 'export ' and 'x.ppm\\n' are judged like 'a.ppm', 'b.wav', 'export'."""
    return "/".join(p.rstrip(" .\r\n") for p in path.split("/"))


def name_rules(path: str) -> list[str]:
    path = normalize_path(path)
    broken: list[str] = []
    if CI_PATTERN.search(path):
        broken.append("ci-pattern (game data file name)")
    parts = path.split("/")
    if any(p.lower() in FORBIDDEN_DIRS for p in parts[:-1]):
        broken.append("forbidden-dir (build/, baserom/ or export/)")
    lowered = path.lower()
    for ext in sorted(FORBIDDEN_EXTENSIONS):
        # endswith, not Path.suffix: a file named just '.bin' has no suffix.
        if lowered.endswith(ext):
            broken.append(f"extension ({ext} files are not allowed)")
    for ext in BINARY_EXTENSIONS:
        if lowered.endswith(ext):
            broken.append(f"binary-ext ({ext} files are not allowed)")
    return broken


def content_rules(data: bytes) -> list[str]:
    """Rules that depend only on the content (the size rule is exempted per path by the caller)."""
    broken: list[str] = []
    if data[:4] in MAGICS:
        broken.append(f"magic (capture file starting with {data[:4].decode()})")
    for magic, kind in BINARY_MAGICS:
        if data.startswith(magic):
            broken.append(f"binary-magic ({kind} file by content)")
            break
    if len(data) > MAX_BYTES:
        broken.append(
            f"size ({len(data)} bytes > {MAX_BYTES}; list path and sha256 in {ALLOWLIST})"
        )
    text_like = is_text(data)
    if not text_like:
        broken.append("binary-content (NUL byte in the first 8 KiB; list path and sha256 in "
                      f"{ALLOWLIST} if it is ours)")
    elif not is_utf8(data):
        broken.append(f"binary-content (not valid UTF-8; list path and sha256 in {ALLOWLIST})")
    if text_like:
        # The trace rule never depends on UTF-8 validity (an allowlisted file is still checked).
        n = count_trace_lines(data)
        if n > TRACE_LINE_LIMIT:
            broken.append(f"trace ({n} lines look like trace lines, limit {TRACE_LINE_LIMIT})")
    if text_like and is_utf8(data):
        n = count_listing_lines(data)
        if n > TRACE_LINE_LIMIT:
            broken.append(
                f"listing ({n} lines look like hex dump, disassembly or byte-array lines, "
                f"limit {TRACE_LINE_LIMIT})"
            )
        if has_dense_line(data):
            broken.append(f"dense-blob (a line of {DENSE_LINE_MIN}+ base64 or hex characters)")
    return broken


def range_entries(root: Path, rng: str) -> list[tuple[str, str, str]]:
    """(mode, blob sha, path) for every added or modified entry of every commit in the range.

    Merge commits report their diff against each parent (-m), so a file that only a
    merge resolution introduces is covered. Deleted entries are skipped."""
    sep = "..." if "..." in rng else ".."
    a, _, b = rng.partition(sep)
    a, b = a.strip(), b.strip()
    if a.startswith("-") or b.startswith("-"):
        raise RuntimeError(f"--range {rng!r}: an endpoint must not start with '-'")
    if not a or not b:
        raise RuntimeError(f"--range {rng!r}: both endpoints are required (A..B or A...B)")
    if sep == "...":
        base = git("merge-base", a, b, cwd=root, text=True).strip()
        spec = f"{base}..{b}"
    else:
        spec = f"{a}..{b}"
    raw = git(
        "log", "--format=", "--no-show-signature", "--raw", "-z", "--no-abbrev",
        "--no-renames", "--ignore-submodules=none", "-m", spec,
        cwd=root,
    )
    return parse_raw(raw)


def parse_raw(raw: bytes) -> list[tuple[str, str, str]]:
    """Parse `git ... --raw -z --no-renames` output strictly: every non-empty token must be
    a ':' meta line followed by its path; anything else is an error, never skipped."""
    toks = [t.decode("utf-8", "surrogateescape") for t in raw.split(b"\0")]
    entries: list[tuple[str, str, str]] = []
    i = 0
    while i < len(toks):
        tok = toks[i]
        if not tok:
            i += 1
            continue
        if not tok.startswith(":"):
            raise RuntimeError(f"unexpected git --raw output near {tok[:40]!r}")
        fields = tok[1:].split()
        if len(fields) < 5 or i + 1 >= len(toks):
            raise RuntimeError(f"unexpected git --raw output near {tok[:40]!r}")
        new_mode, new_sha, status, path = fields[1], fields[3], fields[4], toks[i + 1]
        i += 2
        if status.startswith("D") or set(new_sha) == {"0"}:
            continue
        entries.append((new_mode, new_sha, path))
    return entries


def check_entries(
    entries: list[tuple[str, str, str]], read_blob: Reader, allowed: dict[str, str]
) -> list[tuple[str, str]]:
    """The one checking path (main() and the tests): name rules for every (sha, path),
    content rules once per blob sha, each (path, rule) pair reported once.

    read_blob(sha) returns the content; a regular file whose blob cannot be read is an
    error (RuntimeError, exit 2). Gitlinks (mode 160000) get the name rules only."""
    offences: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    cache: dict[str, tuple[list[str], list[str], str]] = {}
    for mode, sha, path in entries:
        rules = name_rules(path)
        if mode != "160000":
            if sha not in cache:
                try:
                    data = read_blob(sha)
                except RuntimeError as e:
                    raise RuntimeError(f"cannot read {path}: {e}") from e
                found = content_rules(data)
                cache[sha] = (
                    [r for r in found if not r.startswith("size")],
                    [r for r in found if r.startswith("size")],
                    hashlib.sha256(data).hexdigest(),
                )
            plain, size, digest = cache[sha]
            exempt = allowed.get(path) == digest
            rules += plain
            if size and not exempt:
                rules += size
            if exempt:
                rules = [r for r in rules if not r.startswith(BINARY_RULES)]
        for r in rules:
            if (path, r) not in seen:
                seen.add((path, r))
                offences.append((path, r))
    return offences


def read_allowlist(root: Path) -> dict[str, str]:
    f = root / ALLOWLIST
    if f.is_file():
        return load_allowlist(f.read_text(encoding="utf-8", errors="replace"))
    return {}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged", action="store_true", help="check the staged changes")
    mode.add_argument("--range", metavar="A..B", help="check the files changed in a commit range")
    ap.add_argument("--repo", default=".", help="repository to check (default: current directory)")
    args = ap.parse_args(argv)

    try:
        root = Path(git("rev-parse", "--show-toplevel", cwd=Path(args.repo), text=True).strip())
        allowed = read_allowlist(root)
        if args.staged:
            # One raw listing with exact paths, modes and blob ids (no pathspec globbing).
            entries = parse_raw(
                git(
                    "diff", "--cached", "--raw", "-z", "--no-abbrev", "--no-renames",
                    "--ignore-submodules=none", "--diff-filter=ACMRT", cwd=root,
                )
            )
        else:
            if ".." not in args.range:
                ap.error("--range needs A..B")
            entries = range_entries(root, args.range)
        names = sorted({path for _, _, path in entries})
        offences = check_entries(
            entries, lambda sha: git("cat-file", "blob", sha, cwd=root), allowed
        )
    except RuntimeError as e:
        print(f"check_no_game_data: {e}", file=sys.stderr)
        return 2

    if offences:
        for path, rule in offences:
            print(f"{path}: {rule}")
        print(
            f"check_no_game_data: {len(offences)} problem(s) in {len({p for p, _ in offences})} file(s); "
            "game data and files derived from it never go in the repository.",
            file=sys.stderr,
        )
        return 1
    print(f"check_no_game_data: ok ({len(names)} file(s) checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
