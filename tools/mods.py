#!/usr/bin/env python3
"""A mod manager for the port: a library of mods, in an order, put into Azahar's mods folder.

Azahar loads a game's mods from one folder, <Azahar user dir>/load/mods/0004000000126300/ (the
v1.3.0 update uses the base game's folder; src/core/file_sys/ncch_container.cpp):
romfs/ replaces or adds RomFS files and romfs_ext/ deletes (<file>.stub) or patches
(<file>.ips, <file>.bps) them (layered_fs.cpp); exefs/<name> or <name> replaces an ExeFS file
(code.bin, icon.bin, banner.bnr, logo.bcma.lz); the first of exefs/code.ips, exefs/code.bps,
code.ips and code.bps patches the code; exheader.bin replaces the exheader.

Players want many mods at once. This keeps a mod library (<Azahar user dir>/mh4u-mods/, one
folder per mod, each with an optional mod.json) and an order in which later mods win, shows
the files that two enabled mods change, and puts the enabled mods' files into Azahar's folder
(hard links where it can, copies otherwise). It remembers what it put there, so it only ever
removes its own files, and it won't replace files it didn't put there unless told to.

    python3 tools/mods.py list                    # the mods, on or off, in order
    python3 tools/mods.py add "Some mod.zip"      # or a folder; unpacked into the library
    python3 tools/mods.py enable|disable|remove NAME
    python3 tools/mods.py order NAME POSITION     # 1 = first; later mods win
    python3 tools/mods.py conflicts               # files more than one enabled mod changes
    python3 tools/mods.py deploy [--force]        # the enabled mods into Azahar's mods folder
    python3 tools/mods.py undeploy                # and out again
    python3 tools/mods.py status                  # what deploy would change; rebuild needed?
    python3 tools/mods.py catalog [--refresh]     # mods and texture packs recommended for the port
    python3 tools/mods.py install-from-catalog ID # downloaded, checked and installed
    python3 tools/mods.py install URL|ARCHIVE [--textures]   # a mod or texture pack from elsewhere

Texture packs go into Azahar's custom texture folder for the game (load/textures/<program
id>/), and the files each one put there are remembered, so remove takes them out again.
Archives: .zip, .7z (tools/sevenzip.py) and .tar.

--json on any command prints one JSON object instead, for the port's launcher. Code mods
(code.bin, code.ips, code.bps) change the code the port recompiled: only one can be on at a
time, and the code library has to be built again for one that changes the game's code
(tools/build_game.py); status says when. docs/MODS.md has the details.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
import zlib
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import code_mods  # noqa: E402
import sevenzip  # noqa: E402

PROGRAM_ID = 0x0004000000126300      # MH4U USA (config/versions.json): names Azahar's mods folder
UPDATE_ID = 0x0004000E00126300       # its v1.3.0 update, whose code the port runs
LIBRARY = "mh4u-mods"                # the mod library, in Azahar's user folder
STATE = "library.json"               # in the library: the order, and which mods are on
MANIFEST = "mod.json"                # in a mod: what it is
DEPLOYED = "mh4u-mods-deployed.json"  # in Azahar's mods folder: what deploy put there
TEMP = ".mh4u-mods-tmp"              # suffix of a file being put in place
FORMAT = 1

# What Azahar reads from a mods folder (ncch_container.cpp: LoadOverrideExeFSSection,
# ApplyCodePatch, the exheader override; layered_fs.cpp)
SECTIONS = ("code.bin", "icon.bin", "banner.bnr", "logo.bcma.lz")       # exefs/<name>, then <name>
PATCHES = ("exefs/code.ips", "exefs/code.bps", "code.ips", "code.bps")  # the first found applies
TOP_DIRS = ("romfs", "romfs_ext", "exefs")
TOP_FILES = SECTIONS + ("code.ips", "code.bps", "exheader.bin")
EXT_KINDS = (".stub", ".ips", ".bps")
# Upstream Azahar's LayeredFS::LoadExtRelocations looks only at the files at the top of
# romfs_ext/; the port's Azahar (port/azahar/azahar.patch) reads its sub-folders too, which MH4U's
# archives need (data/, eng/data/). False would leave such files out with a warning.
ROMFS_EXT_IN_FOLDERS = True

# The top-level folders of MH4U's RomFS, as far as they are known without reading game files:
# data/ holds the archives (the code builds archive paths such as data\m%02d and
# data\gc_bg%02d, docs/research/system-services.md; community guides repack
# romfs\data\pl_swo010.arc) and localized archives sit in a language folder (the v1.3.0
# update's eng\data\core_common.arc, from a community mod's instructions; the other
# languages by analogy with MT Framework's language codes). They tell a bare tree of the
# game's files from a folder that only wraps one, and they are lower case in the game.
ROMFS_TOP = ("data", "eng", "fre", "ger", "ita", "spa", "jpn")

# A zip is unpacked only within these limits (bytes actually unpacked are counted too)
ZIP_MAX_ENTRIES = 200_000
ZIP_MAX_FILE = 4 << 30
ZIP_MAX_TOTAL = 8 << 30

KINDS = ("romfs", "romfs_ext", "code", "exefs", "exheader")  # what a mod can contain
TEXT_FIELDS = ("name", "version", "author", "description", "url", "catalog")

_TITLE = re.compile(r"[0-9A-Fa-f]{16}")
_WINDOWS_RESERVED = {"con", "prn", "aux", "nul"} | {f"{p}{i}" for p in ("com", "lpt") for i in range(1, 10)}
_WINDOWS_BAD = set('<>:"|?*') | {chr(i) for i in range(32)}
_DOC_EXTS = (".txt", ".md", ".pdf", ".htm", ".html", ".url", ".nfo", ".rtf", ".json",
             ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
_DOC_NAMES = ("readme", "license", "licence", "copying", "changelog", "changes", "credits", "authors")


class Error(Exception):
    """Something refused or failed: a code for the launcher, a message for the player."""

    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details


# ── paths and names ─────────────────────────────────────────────────────────

def default_user_dir() -> str:
    """Azahar's user folder on this platform (as tools/build_game.py has it)."""
    if sys.platform.startswith("win"):
        return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Azahar")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/Azahar")
    data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(data, "azahar-emu")


def mods_folder(user_dir: str) -> str:
    return code_mods.mod_dir(user_dir, PROGRAM_ID)


def code_library(user_dir: str) -> str:
    """Where tools/build_game.py --install puts the code library on this platform."""
    ext = "dll" if sys.platform.startswith("win") else "dylib" if sys.platform == "darwin" else "so"
    return os.path.join(user_dir, "recomp", f"{PROGRAM_ID:016X}.{ext}")


def build_record_path(library: str, program_id: int = PROGRAM_ID) -> str:
    return os.path.join(os.path.dirname(library), f"{program_id:016X}.build.json")


def ignored(name: str) -> bool:
    """Files no mod means: hidden ones (.DS_Store, .git, ._ forks), zip and OS leftovers."""
    return name.startswith(".") or name.lower() in ("__macosx", "thumbs.db", "desktop.ini")


def doc_file(name: str) -> bool:
    """A file that goes with a mod rather than into the game: a readme, a licence, a picture."""
    low = name.lower()
    return low.endswith(_DOC_EXTS) or low.split(".")[0] in _DOC_NAMES


def check_name(part: str, where: str) -> None:
    """Refuse a file name Windows can't have (the mod wouldn't work there)."""
    if set(part) & _WINDOWS_BAD or part.endswith((" ", ".")) or \
            part.split(".")[0].rstrip(" ").lower() in _WINDOWS_RESERVED:
        raise Error("bad_name", f"{where}: {part!r} isn't a valid file name on Windows")


def relative_parts(name: str, where: str = "") -> list[str]:
    """A relative path from a zip or a mod.json, as its names. Either slash separates; absolute
    paths, drive letters, .. and names Windows can't have are refused."""
    where = where or name
    if "\0" in name:
        raise Error("bad_path", f"{where}: a NUL character in the path")
    path = name.replace("\\", "/")
    if path.startswith("/") or re.match(r"[A-Za-z]:", path):
        raise Error("bad_path", f"{where}: an absolute path")
    parts = [p for p in path.split("/") if p not in ("", ".")]
    for p in parts:
        if p == "..":
            raise Error("bad_path", f"{where}: goes outside the mod's folder (..)")
        check_name(p, where)
    return parts


def mod_id(name: str) -> str:
    """A folder name for a mod: what Windows allows, not hidden, not too long."""
    out = "".join("_" if c in _WINDOWS_BAD or c in "/\\" else c for c in name).strip(" .") or "mod"
    if out.split(".")[0].lower() in _WINDOWS_RESERVED:
        out = "_" + out
    return out[:80].rstrip(" .")


def _rel(path: str, top: str) -> str:
    rel = os.path.relpath(path, top)
    return "" if rel == "." else rel.replace(os.sep, "/")


def _abs(top: str, rel: str) -> str:
    return os.path.join(top, *rel.split("/"))


def _join(a: str, b: str) -> str:
    return f"{a}/{b}" if a else b


def _within(path: str, top: str) -> bool:
    path, top = os.path.normcase(os.path.realpath(path)), os.path.normcase(os.path.realpath(top))
    return path == top or path.startswith(top.rstrip(os.sep) + os.sep)


def _write_json(path: str, data) -> None:
    tmp = path + TEMP
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def _remove_file(path: str) -> None:
    try:
        os.remove(path)
    except PermissionError:          # a read-only file on Windows
        os.chmod(path, stat.S_IWRITE)
        os.remove(path)


def _rmtree(path: str) -> None:
    def retry(func, p, *_):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:
        shutil.rmtree(path, onerror=retry)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


# ── a mod: its layout and its files ─────────────────────────────────────────

@dataclass
class ModFile:
    dest: str     # where it goes in Azahar's mods folder: "romfs/data/x.arc", "exefs/code.ips"
    src: str      # the file in the library
    target: str   # what it changes in the game: "romfs:data/x.arc", "code", "exefs:banner.bnr"


@dataclass
class Mod:
    id: str                 # its folder in the library
    path: str
    enabled: bool = False
    new: bool = False       # a folder put in the library by hand, not in its order yet
    manifest: dict = field(default_factory=dict)
    layout: str | None = None     # "azahar", "title" (a title id or luma folder), "romfs"
    root: str | None = None       # the folder with that layout
    files: list[ModFile] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)   # it can't be enabled while there are any
    ignored: list[str] = field(default_factory=list)  # what Azahar wouldn't read

    @property
    def name(self) -> str:
        return self.manifest.get("name") or self.id

    @property
    def code_files(self) -> list[ModFile]:
        return [f for f in self.files if f.target == "code"]

    @property
    def contains(self) -> list[str]:
        return sorted({kind_of(f) for f in self.files})


def kind_of(f: ModFile) -> str:
    if f.target == "code":
        return "code"
    if f.dest.startswith("romfs/"):
        return "romfs"
    if f.dest.startswith("romfs_ext/"):
        return "romfs_ext"
    return "exheader" if f.dest == "exheader.bin" else "exefs"


def _listing(path: str) -> tuple[list[str], list[str]]:
    """(folders, files) in a folder, without ignored names and symbolic links."""
    dirs, files = [], []
    try:
        entries = list(os.scandir(path))
    except OSError:
        return dirs, files
    for e in entries:
        if ignored(e.name) or e.is_symlink():
            continue
        (dirs if e.is_dir() else files).append(e.name)
    return sorted(dirs), sorted(files)


def _files_under(top: str, label: str, warnings: list[str]) -> list[tuple[str, str]]:
    """(path from top with '/', absolute path) of every file under top. Symbolic links are
    skipped with a warning: a mod has no use for them, and they could point anywhere."""
    out = []
    for root, dirs, files in os.walk(top):
        rel_root = _rel(root, top)
        keep = []
        for d in sorted(dirs):
            if ignored(d):
                continue
            if os.path.islink(os.path.join(root, d)):
                warnings.append(f"{_join(label, _join(rel_root, d))}: a symbolic link, not used")
                continue
            keep.append(d)
        dirs[:] = keep
        for name in files:
            if ignored(name):
                continue
            if os.path.islink(os.path.join(root, name)):
                warnings.append(f"{_join(label, _join(rel_root, name))}: a symbolic link, not used")
                continue
            out.append((_join(rel_root, name), os.path.join(root, name)))
    return sorted(out)


def detect(base: str) -> tuple[str, str, list[str], list[str]]:
    """How a mod folder is laid out: ("azahar" | "title" | "romfs", the folder with that layout,
    the folders passed on the way there, notes). A folder that holds only one folder (and
    perhaps a readme) is looked through, as zips often wrap their contents in one."""
    level, chain, notes = base, [], []
    for _ in range(8):
        chain.append(level)
        dirs, files = _listing(level)
        low_dirs = {d.lower(): d for d in dirs}
        if any(d in low_dirs for d in TOP_DIRS) or {f.lower() for f in files} & set(TOP_FILES):
            return "azahar", level, chain, notes
        titles = [(int(d, 16), os.path.join(level, d)) for d in dirs if _TITLE.fullmatch(d)]
        if "luma" in low_dirs:              # luma/titles/<title id>/, as for a 3DS
            luma = os.path.join(level, low_dirs["luma"])
            sub = {d.lower(): d for d in _listing(luma)[0]}
            if "titles" in sub:
                tdir = os.path.join(luma, sub["titles"])
                titles += [(int(d, 16), os.path.join(tdir, d)) for d in _listing(tdir)[0] if _TITLE.fullmatch(d)]
        if titles:
            mine = sorted((tid != PROGRAM_ID, path) for tid, path in titles if tid in (PROGRAM_ID, UPDATE_ID))
            others = sorted({f"{tid:016X}" for tid, _ in titles if tid not in (PROGRAM_ID, UPDATE_ID)})
            if not mine:
                raise Error("other_title", f"made for another game or region (title {', '.join(others)}); "
                                           f"the port plays MH4U USA ({PROGRAM_ID:016X})")
            if others:
                notes.append(f"folders for other titles not used: {', '.join(others)}")
            return "title", mine[0][1], chain, notes
        if any(d.lower() in ROMFS_TOP for d in dirs):
            return "romfs", level, chain, notes
        if len(dirs) == 1 and all(doc_file(f) for f in files):
            level = os.path.join(level, dirs[0])
            continue
        break
    dirs, files = _listing(base)
    found = ", ".join([d + "/" for d in dirs] + files)
    raise Error("layout", "can't tell where this mod's files go. Accepted: Azahar's mod folder layout (romfs/, "
                          "romfs_ext/, exefs/, code.ips, ...), a 0004000000126300 or luma/titles/0004000000126300 "
                          f"folder holding one, or the game's own folders ({', '.join(f + '/' for f in ROMFS_TOP[:2])}"
                          f"...). Found: {found or 'nothing'}")


def _canon_top(rel: str, mod: Mod, label: str) -> str:
    """The game's own spelling of its known folders: Data/ is data/, ENG/Data/ is eng/data/."""
    parts = rel.split("/")
    known = 2 if len(parts) > 2 and parts[0].lower() in ROMFS_TOP[1:] and parts[1].lower() == "data" else 1
    if len(parts) > 1 and parts[0].lower() in ROMFS_TOP:
        for i in range(known):
            if parts[i] != parts[i].lower():
                note = f"{label}{'/'.join(parts[:i + 1])}/: the game's folder is {parts[i].lower()}/, used instead"
                if note not in mod.warnings:
                    mod.warnings.append(note)
                parts[i] = parts[i].lower()
    return "/".join(parts)


def _collect_azahar(mod: Mod, root: str) -> None:
    dirs, files = _listing(root)
    top = {f.lower(): os.path.join(root, f) for f in reversed(files)}
    exefs: dict[str, str] = {}
    for d in dirs:
        low = d.lower()
        if low == "romfs":
            for rel, src in _files_under(os.path.join(root, d), d, mod.warnings):
                rel = _canon_top(rel, mod, d + "/")
                mod.files.append(ModFile(f"romfs/{rel}", src, f"romfs:{rel.casefold()}"))
        elif low == "romfs_ext":
            for rel, src in _files_under(os.path.join(root, d), d, mod.warnings):
                ext = next((e for e in EXT_KINDS if rel.lower().endswith(e) and len(rel) > len(e)), None)
                if ext is None:
                    mod.warnings.append(f"{d}/{rel}: not a .stub, .ips or .bps file, not used")
                    continue
                if "/" in rel and not ROMFS_EXT_IN_FOLDERS:
                    mod.warnings.append(f"{d}/{rel}: does nothing, as Azahar reads only the files at the top of "
                                        "romfs_ext/ (put the whole changed file in romfs/ instead)")
                    continue
                game = rel[:-len(ext)]
                mod.files.append(ModFile(f"romfs_ext/{game}{ext}", src, f"romfs:{game.casefold()}"))
        elif low == "exefs":
            sub_dirs, sub_files = _listing(os.path.join(root, d))
            for name in sub_files:
                if name.lower() in SECTIONS + ("code.ips", "code.bps"):
                    exefs.setdefault(name.lower(), os.path.join(root, d, name))
                else:
                    mod.ignored.append(f"{d}/{name}")
            mod.ignored += [f"{d}/{s}/" for s in sub_dirs]
        else:
            mod.ignored.append(d + "/")
    mod.ignored += [f for f in files if f.lower() not in TOP_FILES]

    def present(rel: str) -> str | None:
        return exefs.get(rel[6:]) if rel.startswith("exefs/") else top.get(rel)

    # in each group Azahar takes the first file it finds and never looks at the others
    for name in SECTIONS:
        found = [(rel, p) for rel in (f"exefs/{name}", name) if (p := present(rel))]
        if found:
            mod.files.append(ModFile(f"exefs/{name}", found[0][1], "code" if name == "code.bin" else f"exefs:{name}"))
            mod.warnings += [f"{rel}: not used, Azahar takes {found[0][0]} first" for rel, _ in found[1:]]
    found = [(rel, p) for rel in PATCHES if (p := present(rel))]
    if found:
        mod.files.append(ModFile("exefs/" + found[0][0].rsplit("/", 1)[-1], found[0][1], "code"))
        mod.warnings += [f"{rel}: not used, Azahar applies only {found[0][0]}" for rel, _ in found[1:]]
    if "exheader.bin" in top:
        mod.files.append(ModFile("exheader.bin", top["exheader.bin"], "exheader"))
        mod.warnings.append("exheader.bin replaces the game's exheader, which says how its code is loaded; "
                            "the code library is built with the dump's own")


def _collect_romfs(mod: Mod, root: str) -> None:
    for rel, src in _files_under(root, "", mod.warnings):
        if "/" not in rel and doc_file(rel):
            mod.ignored.append(rel)
            continue
        rel = _canon_top(rel, mod, "")
        mod.files.append(ModFile(f"romfs/{rel}", src, f"romfs:{rel.casefold()}"))


def read_manifest(path: str) -> tuple[dict, list[str]]:
    """mod.json: name, version, author, description, url (text), contains (a list of KINDS),
    root (the folder inside the mod that holds its files). Anything else is ignored."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        return {}, [f"{MANIFEST} can't be read ({e}); ignored"]
    if not isinstance(data, dict):
        return {}, [f"{MANIFEST} isn't a JSON object; ignored"]
    out, warnings = {}, []
    for key in TEXT_FIELDS:
        value = data.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and key == "version":
            value = str(value)
        if value is None or value == "":
            continue
        if not isinstance(value, str):
            warnings.append(f"{MANIFEST}: {key} should be text; ignored")
            continue
        out[key] = value.strip()
    contains = data.get("contains")
    if isinstance(contains, str):
        contains = [contains]
    if contains is not None:
        if isinstance(contains, list) and all(isinstance(c, str) and c in KINDS for c in contains):
            out["contains"] = sorted(set(contains))
        else:
            warnings.append(f"{MANIFEST}: contains should list some of {', '.join(KINDS)}; ignored")
    root = data.get("root")
    if root is not None:
        if isinstance(root, str):
            out["root"] = root
        else:
            warnings.append(f"{MANIFEST}: root should be a folder's path; ignored")
    return out, warnings


def scan(path: str, mid: str) -> Mod:
    """Everything about one mod folder: its manifest, its layout, the files Azahar would use."""
    mod = Mod(mid, path)
    base = path
    if os.path.isfile(os.path.join(path, MANIFEST)):
        mod.manifest, notes = read_manifest(os.path.join(path, MANIFEST))
        mod.warnings += notes
        if "root" in mod.manifest:
            try:
                base = os.path.join(path, *relative_parts(mod.manifest["root"], f"{MANIFEST}: root"))
            except Error as e:
                mod.errors.append(e.message)
                return mod
            if not os.path.isdir(base):
                mod.errors.append(f"{MANIFEST}: root {mod.manifest['root']!r} isn't a folder in the mod")
                return mod
    try:
        mod.layout, mod.root, chain, notes = detect(base)
    except Error as e:
        mod.errors.append(e.message)
        return mod
    mod.warnings += notes
    if not os.path.isfile(os.path.join(path, MANIFEST)):     # a zip's wrapper folder may hold it
        for level in chain[1:] + [mod.root]:
            if os.path.isfile(os.path.join(level, MANIFEST)):
                mod.manifest, notes = read_manifest(os.path.join(level, MANIFEST))
                mod.warnings += notes
                break
    if mod.layout == "romfs":
        _collect_romfs(mod, mod.root)
    else:
        _collect_azahar(mod, mod.root)
    seen: dict[str, str] = {}
    for f in mod.files:
        key = f.dest.casefold()
        if key in seen:
            mod.errors.append(f"{seen[key]} and {f.dest} are the same file on Windows and macOS "
                              "(the names differ only in capital letters)")
        seen.setdefault(key, f.dest)
    if not mod.files and not mod.errors:
        mod.errors.append("no files that Azahar uses " + (f"({'; '.join(mod.warnings)})" if mod.warnings else
                                                          "(romfs/, romfs_ext/, exefs/, code.ips, ...)"))
    declared = mod.manifest.get("contains")
    if declared is not None and declared != mod.contains:
        mod.warnings.append(f"{MANIFEST} says it contains {', '.join(declared) or 'nothing'}, "
                            f"but its files are {', '.join(mod.contains) or 'nothing'}")
    return mod


# ── code patches, without the game's code ───────────────────────────────────

def patch_writes(data: bytes) -> list[tuple[int, int]] | None:
    """The (offset, length) ranges an IPS or BPS patch may change in the code, read from the
    patch alone (for BPS: everything it doesn't copy from the same place); None if unreadable."""
    ranges = []
    if data[:5] == b"PATCH":
        cursor, end = 5, len(data) - 3
        while cursor < end and data[cursor:cursor + 3] != b"EOF":
            offset = int.from_bytes(data[cursor:cursor + 3], "big")
            length = int.from_bytes(data[cursor + 3:cursor + 5], "big")
            if length == 0:              # a run: 2-byte count, 1 byte
                ranges.append((offset, int.from_bytes(data[cursor + 5:cursor + 7], "big")))
                cursor += 8
            else:
                ranges.append((offset, length))
                cursor += 5 + length
        return ranges
    if data[:4] == b"BPS1" and len(data) >= 16:
        pos = 4
        for _ in range(2):               # source and target sizes
            _, pos = code_mods._bps_number(data, pos)
        metadata, pos = code_mods._bps_number(data, pos)
        pos += metadata
        out, end = 0, len(data) - 12
        while pos < end:
            word, pos = code_mods._bps_number(data, pos)
            command, length = word & 3, (word >> 2) + 1
            if command == 1:             # TargetRead: the bytes follow
                pos += length
            elif command >= 2:           # SourceCopy, TargetCopy: an offset follows
                _, pos = code_mods._bps_number(data, pos)
            if command != 0:             # SourceRead keeps the bytes where they are
                ranges.append((out, length))
            out += length
        return ranges
    return None


def _code_kind(path: str) -> str:
    low = path.lower()
    return "code.bin" if low.endswith("code.bin") else "bps" if low.endswith(".bps") else "ips"


def changes_code(paths: list[str], text_size: int | None) -> bool | None:
    """Whether these code files (a code.bin, a patch) change the game's code segment, the part
    the port recompiles and checks (it compares the CRC32 of the first text_size bytes, and of
    the rest of the segment's last page when something was put there, see adds_code); a
    patch within the data changes nothing there. None when that can't be told: a whole
    code.bin, an unreadable patch, an unknown code size."""
    result = False
    end = code_segment_end(text_size) or text_size
    for path in paths:
        if _code_kind(path) == "code.bin" or text_size is None:
            return None
        with open(path, "rb") as f:
            ranges = patch_writes(f.read())
        if ranges is None:
            return None
        result = result or any(length and offset < end for offset, length in ranges)
    return result


def adds_code(paths: list[str], text_size: int | None) -> bool:
    """Whether a code patch writes past the end of the code segment but within its last page:
    new code put in the padding before the read-only data (a "code cave"), which hooks in the
    code branch to. tools/build_game.py then recompiles that page to its end too (the build
    record's code_size); a code library built before it did can't run such code."""
    end = code_segment_end(text_size)
    if end is None:
        return False
    for path in paths:
        if _code_kind(path) == "code.bin":
            continue
        try:
            with open(path, "rb") as f:
                ranges = patch_writes(f.read())
        except OSError:
            continue
        if any(length and offset < end and offset + length > text_size for offset, length in ranges or []):
            return True
    return False


def code_segment_end(text: int | None) -> int | None:
    """Where the read-only data starts, from the code's start (the code segment's last page
    ends there), for the version the port targets; None for code of another size."""
    try:
        with open(os.path.join(ROOT, "config", "versions.json")) as f:
            versions = json.load(f)["versions"]
        seg = next(v for v in versions if v.get("target"))["segments"]
        if int(seg["text"][1], 16) != text:
            return None
        return int(seg["rodata"][0], 16) - int(seg["text"][0], 16)
    except (OSError, ValueError, KeyError, StopIteration, TypeError, IndexError):
        return None


def text_size() -> int | None:
    """The size of the code segment of the version the port targets (config/versions.json)."""
    try:
        with open(os.path.join(ROOT, "config", "versions.json")) as f:
            versions = json.load(f)["versions"]
        target = next(v for v in versions if v.get("target"))
        return int(target["segments"]["text"][1], 16)
    except (OSError, ValueError, KeyError, StopIteration, TypeError, IndexError):
        return None


def code_inputs(user_dir: str) -> list[str]:
    """The files Azahar makes the game's code from: a code.bin from the mods folder, then the
    first code patch (tools/code_mods.py), or else Luma's IPS, which Azahar looks for under the
    program id of the update the code comes from (ApplyCodePatch)."""
    replacement, patch = code_mods.find_mods(user_dir, PROGRAM_ID)
    luma = os.path.join(user_dir, "sdmc", "luma", "titles", f"{UPDATE_ID:016X}", "code.ips")
    if patch is None and os.path.isfile(luma):
        patch = luma
    return [p for p in (replacement, patch) if p]


def code_fingerprint(paths: list[str]) -> str:
    """What makes the code: each file's kind and SHA-256, wherever it is."""
    h = hashlib.sha256()
    for path in paths:
        h.update(f"{_code_kind(path)} {_sha256(path)}\n".encode())
    return h.hexdigest()


def write_build_record(library: str, used: list[str], text: int, changed: bool,
                       program_id: int = PROGRAM_ID, code_size: int | None = None) -> str:
    """Next to an installed code library, what it was built from (for status): called by
    tools/build_game.py with the code mods it applied, whether they change the code, and how
    much of the code segment it recompiled (code_size: text, or up to the end of its last
    page when a code mod put new code there)."""
    st = os.stat(library)
    record = {"format": FORMAT, "program_id": f"{program_id:016X}", "library": os.path.basename(library),
              "library_size": st.st_size, "library_mtime_ns": st.st_mtime_ns, "text_size": text,
              "code_size": text if code_size is None else code_size,
              "code_mods": [{"kind": _code_kind(p), "sha256": _sha256(p)} for p in used],
              "fingerprint": code_fingerprint(used), "changes_code": bool(changed), "built": _now()}
    path = build_record_path(library, program_id)
    _write_json(path, record)
    return path


def code_library_state(user_dir: str, library: str | None = None) -> dict:
    """Whether the installed code library was built from the code Azahar will now run."""
    library = library or code_library(user_dir)
    inputs = code_inputs(user_dir)
    state = {"library": library, "code_files": [_rel(p, user_dir) for p in inputs]}
    record_path = build_record_path(library)
    record = None
    if os.path.isfile(record_path):
        try:
            with open(record_path, encoding="utf-8") as f:
                record = json.load(f)
            if not isinstance(record, dict):
                record = None
        except (OSError, ValueError):
            record = None
    size = record.get("text_size") if record and isinstance(record.get("text_size"), int) else text_size()
    now = changes_code(inputs, size)
    state["changes_code"] = now
    notes = []
    if any(os.sep + "luma" + os.sep in p for p in inputs):
        notes.append("Azahar applies Luma's code patch (sdmc/luma/titles/...), which tools/build_game.py doesn't "
                     "apply: a code library can't be built for it; move it into a mod")

    def result(name: str, reason: str) -> dict:
        state.update(state=name, reason=reason)
        if notes:
            state["warnings"] = notes
        return state

    if not os.path.isfile(library):
        return result("missing", "no code library: build it (docs/PLAYING.md, step 1)")
    cave = adds_code(inputs, size)
    if cave:
        state["adds_code"] = True
    if record is None:
        return result("unknown", "the code library has no record of the code mods it was built with (built by an "
                                 "older tools/build_game.py)" + (": the code mods in Azahar's folder change the "
                                                                 "game's code, so build it again" if now else ""))
    st = os.stat(library)
    if (st.st_size, st.st_mtime_ns) != (record.get("library_size"), record.get("library_mtime_ns")):
        return result("unknown", "the record next to the code library is from another build of it")
    if code_fingerprint(inputs) == record.get("fingerprint"):
        built = record.get("code_size")
        if cave and not (isinstance(built, int) and isinstance(size, int) and built > size):
            return result("rebuild", "the code mod adds new code after the game's own, and the code library was "
                                     "built with it by an older tools/build_game.py, which left that code out: "
                                     "build it again (tools/build_game.py --install, docs/PLAYING.md step 1)")
        return result("ok", "built with the code mods now in Azahar's folder")
    if now is False and record.get("changes_code") is False:
        return result("ok", "the code mods aren't the ones it was built with, but neither change the game's code, "
                            "only its data")
    return result("rebuild", "the code mods changed since the code library was built: build it again "
                             "(tools/build_game.py --install, docs/PLAYING.md step 1)")


# ── the library ─────────────────────────────────────────────────────────────

class Library:
    """A folder of mod folders, and library.json with their order and which are on."""

    def __init__(self, path: str):
        self.path = path
        self.state_path = os.path.join(path, STATE)

    def _state(self) -> list[dict]:
        if not os.path.isfile(self.state_path):
            return []
        try:
            with open(self.state_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            raise Error("state", f"{self.state_path} can't be read ({e}): mend or delete it (deleting it "
                                 "forgets the order and which mods are on)")
        entries = data.get("mods") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            raise Error("state", f"{self.state_path} has no list of mods: mend or delete it")
        return [{"id": e["id"], "enabled": e.get("enabled") is True} for e in entries
                if isinstance(e, dict) and isinstance(e.get("id"), str)]

    def folders(self) -> list[str]:
        if not os.path.isdir(self.path):
            return []
        return sorted((e.name for e in os.scandir(self.path) if e.is_dir() and not ignored(e.name)),
                      key=str.casefold)

    def mods(self) -> tuple[list[Mod], list[str]]:
        """The mods in order (folders not in the order yet come last, off), and warnings."""
        folders = self.folders()
        exact, folded = set(folders), {}
        for name in folders:
            folded.setdefault(name.casefold(), name)
        out, seen, warnings = [], set(), []
        for entry in self._state():
            name = entry["id"] if entry["id"] in exact else folded.get(entry["id"].casefold())
            if name is None:
                warnings.append(f"{entry['id']}: in {STATE} but its folder is gone")
                continue
            if name in seen:
                continue
            seen.add(name)
            mod = scan(os.path.join(self.path, name), name)
            mod.enabled = entry["enabled"]
            out.append(mod)
        for name in folders:
            if name not in seen:
                mod = scan(os.path.join(self.path, name), name)
                mod.new = True
                out.append(mod)
        return out, warnings

    def save(self, mods: list[Mod]) -> None:
        os.makedirs(self.path, exist_ok=True)
        _write_json(self.state_path, {"format": FORMAT, "mods": [{"id": m.id, "enabled": m.enabled} for m in mods]})


def find(mods: list[Mod], name: str) -> Mod:
    """A mod by its folder name, or its name in mod.json; capital letters don't matter."""
    tests = (lambda m: m.id == name, lambda m: m.id.casefold() == name.casefold(),
             lambda m: m.name.casefold() == name.casefold())
    for test in tests:
        hits = [m for m in mods if test(m)]
        if len(hits) == 1:
            return hits[0]
        if hits:
            raise Error("ambiguous", f"{name!r} could be any of {', '.join(m.id for m in hits)}: give the folder name",
                        mods=[m.id for m in hits])
    raise Error("not_found", f"no mod {name!r} in the library", name=name)


def check_code_rule(mods: list[Mod], mod: Mod) -> None:
    """Azahar applies one code patch, onto one code.bin: two code mods can't be on together."""
    if not mod.code_files:
        return
    others = [m for m in mods if m.enabled and m is not mod and m.code_files]
    if others:
        other = others[0]
        raise Error("code_mod_conflict",
                    f"{mod.id} changes the game's code ({', '.join(f.dest for f in mod.code_files)}), and so does "
                    f"{other.id}, which is on. Azahar applies only one code patch, so two code mods can't be on "
                    f"together: turn {other.id} off first (mods.py disable \"{other.id}\")", mods=[m.id for m in others])


# ── what goes into Azahar's mods folder ─────────────────────────────────────

def _display(target: str, dest: str) -> str:
    if target.startswith("romfs:"):
        path = dest.split("/", 1)[1]
        if dest.startswith("romfs_ext/"):
            path = path[:-len(next(e for e in EXT_KINDS if path.lower().endswith(e)))]
        return path
    return {"code": "the game's code"}.get(target, dest)


def make_plan(mods: list[Mod]) -> tuple[dict[str, tuple[ModFile, str]], list[dict], list[str]]:
    """For the enabled mods in order: {path in Azahar's folder: (file, mod id)}, the conflicts
    (later mods win), and warnings. Names that differ only in capital letters are one file, as
    on Windows and macOS; a folder is spelled as the last mod that has it spells it."""
    by_target: dict[str, list[tuple[Mod, list[ModFile]]]] = {}
    for mod in mods:
        per: dict[str, list[ModFile]] = {}
        for f in mod.files:
            per.setdefault(f.target, []).append(f)
        for target, files in per.items():
            by_target.setdefault(target, []).append((mod, files))
    spell: dict[str, str] = {}
    spellings: dict[str, set[str]] = {}
    for mod in mods:
        for f in mod.files:
            parts = f.dest.split("/")[:-1]
            for i in range(1, len(parts) + 1):
                key = "/".join(parts[:i]).casefold()
                spell[key] = parts[i - 1]
                spellings.setdefault(key, set()).add("/".join(parts[:i]))
    plan, conflicts, warnings = {}, [], []
    for target, entries in by_target.items():
        winner, files = entries[-1]
        for f in files:
            parts = f.dest.split("/")
            dirs = [spell["/".join(parts[:i]).casefold()] for i in range(1, len(parts))]
            dest = "/".join(dirs + parts[-1:])
            plan[dest] = (ModFile(dest, f.src, f.target), winner.id)
        if len(entries) > 1:
            conflicts.append({"target": target, "path": _display(target, files[0].dest),
                              "mods": [m.id for m, _ in entries], "winner": winner.id,
                              "files": {m.id: [f.dest for f in fs] for m, fs in entries}})
    for key, names in sorted(spellings.items()):
        if len({n.rsplit("/", 1)[-1] for n in names}) > 1:
            chosen = "/".join(spell["/".join(key.split("/")[:i])] for i in range(1, key.count("/") + 2))
            warnings.append(f"{', '.join(sorted(names))}: one folder spelled differently by the mods; used as {chosen} "
                            "(the game's names are case-sensitive, so only its own spelling works)")
    conflicts.sort(key=lambda c: c["path"].casefold())
    return plan, conflicts, warnings


def target_of(rel: str) -> str | None:
    """What a file in Azahar's mods folder changes in the game, as ModFile.target."""
    low = rel.lower()
    if low.startswith("romfs/"):
        return "romfs:" + rel[6:].casefold()
    if low.startswith("romfs_ext/"):
        game = rel[10:]
        ext = next((e for e in EXT_KINDS if game.lower().endswith(e)), None)
        if ext and ("/" not in game or ROMFS_EXT_IN_FOLDERS):
            return "romfs:" + game[:-len(ext)].casefold()
        return None
    name = low[6:] if low.startswith("exefs/") and low.count("/") == 1 else low
    if name in ("code.bin", "code.ips", "code.bps"):
        return "code"
    if name in SECTIONS:
        return "exefs:" + name
    return "exheader" if low == "exheader.bin" else None


def inventory(folder: str) -> tuple[dict[str, os.stat_result], set[str]]:
    """The files (and symbolic links, never followed) in Azahar's mods folder by path, and its folders."""
    files: dict[str, os.stat_result] = {}
    dirs: set[str] = set()
    if not os.path.isdir(folder):
        return files, dirs
    for root, dnames, fnames in os.walk(folder):
        rel_root = _rel(root, folder)
        for d in list(dnames):
            if os.path.islink(os.path.join(root, d)):
                files[_join(rel_root, d)] = os.lstat(os.path.join(root, d))
                dnames.remove(d)
            else:
                dirs.add(_join(rel_root, d))
        for name in fnames:
            if not rel_root and name in (DEPLOYED, DEPLOYED + TEMP):
                continue
            files[_join(rel_root, name)] = os.lstat(os.path.join(root, name))
    return files, dirs


def read_deployed(folder: str) -> tuple[dict, list[str]]:
    path = os.path.join(folder, DEPLOYED)
    if not os.path.isfile(path):
        return {}, []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        files = data["files"]
        if not isinstance(files, dict):
            raise ValueError("no files")
    except (OSError, ValueError, KeyError, TypeError) as e:
        return {}, [f"{DEPLOYED} can't be read ({e}): the files in Azahar's mods folder count as not put there "
                    "by the mod manager (deploy --force replaces them)"]
    library = data.get("library") if isinstance(data.get("library"), str) else ""
    return {"library": library, "mods": data.get("mods"),
            "files": {k: v for k, v in files.items() if isinstance(k, str) and isinstance(v, dict)}}, []


def _owned(folder: str, rel: str, st: os.stat_result, rec: dict, library: str) -> bool:
    """A file deploy put there and nobody changed since (or a hard link to its library file)."""
    if not stat.S_ISREG(st.st_mode):
        return False
    if (st.st_size, st.st_mtime_ns) == (rec.get("size"), rec.get("mtime_ns")):
        return True
    if isinstance(rec.get("source"), str) and library:
        try:
            return os.path.samefile(_abs(folder, rel), _abs(library, rec["source"]))
        except OSError:
            return False
    return False


def _current(folder: str, dest: str, rec: dict, f: ModFile, owner: str, library: str) -> bool:
    """Whether the deployed file at dest is already this library file."""
    if rec.get("mod") != owner or rec.get("source") != _rel(f.src, library):
        return False
    try:
        s = os.stat(f.src)
        if rec.get("link") and os.path.samefile(_abs(folder, dest), f.src):
            return True
    except OSError:
        return False
    return (s.st_size, s.st_mtime_ns) == (rec.get("source_size"), rec.get("source_mtime_ns"))


@dataclass
class Analysis:
    """What deploy would do: Azahar's mods folder against the enabled mods."""
    folder: str
    plan: dict
    conflicts: list
    problems: list            # why it can't be deployed (a mod with errors, two code mods)
    warnings: list
    ours: dict                # files deploy put there and nobody changed: path -> record
    add: list
    update: list
    remove: list
    same: list
    in_the_way: list          # files not put there by deploy, which it would replace or which compete
    changed: list             # files deploy put there that were changed since: now the player's
    foreign: list             # every file in the folder not put there by deploy
    problem_code: str = "mod_problems"

    @property
    def state(self) -> str:
        if self.problems or self.in_the_way:
            return "blocked"
        return "out_of_date" if self.add or self.update or self.remove else "up_to_date"

    def summary(self) -> dict:
        return {"state": self.state, "folder": self.folder, "add": self.add, "update": self.update,
                "remove": self.remove, "unchanged": len(self.same), "in_the_way": self.in_the_way,
                "changed": self.changed, "foreign": self.foreign, "problems": self.problems}


def analyze(lib: Library, folder: str, mods: list[Mod], undeploy: bool = False) -> Analysis:
    enabled = [] if undeploy else [m for m in mods if m.enabled]
    problems = [f"{m.id} can't be used: {'; '.join(m.errors)}" for m in enabled if m.errors]
    code = [m.id for m in enabled if m.code_files]
    if len(code) > 1:
        problems.insert(0, f"{', '.join(code)} all change the game's code, and only one code mod can be on: "
                           "turn the others off")
    plan, conflicts, warnings = make_plan([m for m in enabled if not m.errors])
    record, notes = read_deployed(folder)
    warnings += notes
    rec_files, rec_library = record.get("files", {}), record.get("library", "")
    disk, dirs = inventory(folder)
    ours, changed, foreign = {}, [], []
    for rel, st in sorted(disk.items()):
        rec = rec_files.get(rel)
        if rec is not None and _owned(folder, rel, st, rec, rec_library):
            ours[rel] = rec
        else:
            (changed if rec is not None else foreign).append(rel)
    others = changed + foreign
    held = {"/".join(rel.split("/")[:i]) for rel in others for i in range(1, rel.count("/") + 1)}
    held_cf: dict[str, str] = {}
    for d in sorted(held):
        held_cf.setdefault(d.casefold(), d)
    # where a folder of the plan already exists, spelled otherwise, with the player's files in
    # it, the files go into it as it is (on Windows and macOS they would anyway)
    files = {}
    for dest, (f, owner) in plan.items():
        parts, out = dest.split("/"), []
        for comp in parts[:-1]:
            cand = "/".join(out + [comp])
            alt = held_cf.get(cand.casefold())
            if cand not in held and alt is not None:
                warnings.append(f"{cand}: put in {alt}/, which is spelled differently and holds files not "
                                "from the mod library")
                out = alt.split("/")
            else:
                out.append(comp)
        dest = "/".join(out + parts[-1:])
        files[dest] = (ModFile(dest, f.src, f.target), owner)
    warnings[:] = list(dict.fromkeys(warnings))
    planned = {d.casefold() for d in files}
    planned_dirs = {"/".join(d.split("/")[:i]).casefold() for d in files for i in range(1, d.count("/") + 1)}
    problems += [f"{d} is a file in {files[d][1]} and a folder in another mod: turn one of them off"
                 for d in sorted(files) if d.casefold() in planned_dirs]
    targets = {f.target for f, _ in files.values()}
    in_the_way = [rel for rel in others
                  if rel.casefold() in planned or rel.casefold() in planned_dirs or target_of(rel) in targets]
    in_the_way += sorted(d + "/" for d in held if d.casefold() in planned)
    add, update, same = [], [], []
    for dest, (f, owner) in sorted(files.items()):
        rec = ours.get(dest)
        if rec is None:
            add.append(dest)
        elif _current(folder, dest, rec, f, owner, lib.path):
            same.append(dest)
        else:
            update.append(dest)
    remove = sorted(rel for rel in ours if rel not in files)
    return Analysis(folder, files, conflicts, problems, warnings, ours, add, update, remove, same,
                    in_the_way, changed, foreign, "code_mod_conflict" if len(code) > 1 else "mod_problems")


def _record(folder: str, dest: str, f: ModFile, owner: str, library: str, linked: bool) -> dict:
    s, d = os.stat(f.src), os.stat(_abs(folder, dest))
    return {"mod": owner, "source": _rel(f.src, library), "source_size": s.st_size,
            "source_mtime_ns": s.st_mtime_ns, "size": d.st_size, "mtime_ns": d.st_mtime_ns, "link": linked}


def _place(src: str, dest: str, copy: bool) -> bool:
    """Put src at dest (a hard link, or a copy); True when linked."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + TEMP
    if os.path.lexists(tmp):
        _remove_file(tmp)
    linked = False
    if not copy:
        try:
            os.link(src, tmp)
            linked = True
        except OSError:              # another drive, FAT/exFAT, not allowed: copy
            pass
    try:
        if not linked:
            shutil.copy2(src, tmp)
        os.replace(tmp, dest)
    except BaseException:
        if os.path.lexists(tmp):
            _remove_file(tmp)
        raise
    return linked


def _prune(folder: str, rel: str) -> None:
    """Remove the folders above rel that are now empty."""
    parts = rel.split("/")[:-1]
    while parts:
        try:
            os.rmdir(_abs(folder, "/".join(parts)))
        except OSError:
            return
        parts.pop()


def apply(an: Analysis, lib: Library, mods: list[Mod], force: bool = False, copy: bool = False) -> dict:
    """Make Azahar's mods folder what the analysis planned; returns what was done."""
    if an.problems:
        raise Error(an.problem_code, "can't deploy: " + "; ".join(an.problems), problems=an.problems)
    if an.in_the_way and not force:
        raise Error("foreign_files", "files that the mod manager didn't put in Azahar's mods folder are in the way: "
                                     f"{', '.join(an.in_the_way)}. Move them into a mod in the library, or deploy "
                                     "--force to replace them", files=an.in_the_way)
    folder = an.folder
    done = {"added": [], "updated": [], "removed": [], "replaced": [], "linked": 0, "copied": 0}
    records = dict(an.ours)        # as they are, should something fail half-way
    old, _ = read_deployed(folder)
    try:
        for rel in an.remove:
            _remove_file(_abs(folder, rel))
            _prune(folder, rel)
            del records[rel]
            done["removed"].append(rel)
        if force:
            for rel in an.in_the_way:
                path = _abs(folder, rel.rstrip("/"))
                if os.path.isdir(path) and not os.path.islink(path):
                    _rmtree(path)
                elif os.path.lexists(path):
                    _remove_file(path)
                _prune(folder, rel.rstrip("/"))
                done["replaced"].append(rel)
        for rel in an.same:          # a hard-linked file edited in place: remember it as it is now
            f, owner = an.plan[rel]
            records[rel] = _record(folder, rel, f, owner, lib.path, bool(an.ours[rel].get("link")))
        for key, dests in (("added", an.add), ("updated", an.update)):
            for dest in dests:
                f, owner = an.plan[dest]
                linked = _place(f.src, _abs(folder, dest), copy)
                records[dest] = _record(folder, dest, f, owner, lib.path, linked)
                done[key].append(dest)
                done["linked" if linked else "copied"] += 1
    except OSError as e:
        raise Error("io", f"{e} (if Azahar is running the game, close it and deploy again)") from e
    finally:
        path = os.path.join(folder, DEPLOYED)
        if records:
            enabled = [m for m in mods if m.enabled]
            data = {"format": FORMAT, "tool": "tools/mods.py", "library": lib.path,
                    "mods": [{"id": m.id, "version": m.manifest.get("version", "")} for m in enabled],
                    "files": dict(sorted(records.items()))}
            if any(old.get(k) != data[k] for k in ("files", "library", "mods")):
                os.makedirs(folder, exist_ok=True)
                data["deployed"] = _now()
                _write_json(path, data)
        elif os.path.isfile(path):
            _remove_file(path)
    done["changed"] = bool(done["added"] or done["updated"] or done["removed"] or done["replaced"])
    done["kept"] = [rel for rel in an.changed if not (force and rel in an.in_the_way)]
    return done


# ── the commands ────────────────────────────────────────────────────────────

@dataclass
class Context:
    user_dir: str
    library: Library
    folder: str
    code_library: str | None = None
    progress: "Progress | None" = None


def mod_info(mod: Mod, position: int, conflicts: list[dict], size: int | None) -> dict:
    code = None
    if mod.code_files:
        srcs = [f.src for f in mod.code_files]
        code = {"files": [f.dest for f in mod.code_files], "changes_code": changes_code(srcs, size),
                "adds_code": adds_code(srcs, size)}
    return {"id": mod.id, "name": mod.name, "version": mod.manifest.get("version", ""),
            "author": mod.manifest.get("author", ""), "description": mod.manifest.get("description", ""),
            "url": mod.manifest.get("url", ""), "catalog": mod.manifest.get("catalog", ""), "position": position,
            "enabled": mod.enabled, "new": mod.new,
            "layout": mod.layout, "contains": mod.contains, "files": len(mod.files), "code": code,
            "wins": sum(1 for c in conflicts if c["winner"] == mod.id),
            "loses": sum(1 for c in conflicts if mod.id in c["mods"] and c["winner"] != mod.id),
            "path": mod.path, "warnings": mod.warnings, "errors": mod.errors, "ignored": mod.ignored}


def overview(ctx: Context, mods: list[Mod], warnings: list[str]) -> dict:
    """The library as list shows it (and as the commands that change it return it)."""
    an = analyze(ctx.library, ctx.folder, mods)
    size = text_size()
    return {"library": ctx.library.path, "mods_folder": ctx.folder,
            "mods": [mod_info(m, i, an.conflicts, size) for i, m in enumerate(mods, 1)],
            "conflict_count": len(an.conflicts), "deploy_state": an.state, "warnings": warnings + an.warnings,
            "problems": an.problems}


def cmd_list(ctx: Context, args) -> dict:
    mods, warnings = ctx.library.mods()
    return overview(ctx, mods, warnings)


def _unpack_zip(path: str, dest: str) -> list[str]:
    """Unpack a mod's zip into dest (a new folder), refusing paths that leave it, absolute paths,
    names Windows can't have and two names that are one file on Windows; symbolic links are
    skipped, and the sizes are limited (also as actually unpacked, whatever the zip says)."""
    warnings = []
    try:
        z = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as e:
        raise Error("zip", f"{os.path.basename(path)} can't be opened as a zip ({e})")
    with z:
        infos = z.infolist()
        if len(infos) > ZIP_MAX_ENTRIES:
            raise Error("zip", f"{os.path.basename(path)} has {len(infos)} entries, more than {ZIP_MAX_ENTRIES}")
        todo, seen, total = [], {}, 0
        for info in infos:
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                warnings.append(f"{info.filename}: a symbolic link, not unpacked")
                continue
            parts = relative_parts(info.filename)
            if not parts or any(ignored(p) for p in parts) or info.is_dir():
                continue
            if info.flag_bits & 1:
                raise Error("zip", f"{os.path.basename(path)} is password-protected: unpack it yourself and add "
                                   "the folder")
            if info.file_size > ZIP_MAX_FILE:
                raise Error("zip", f"{info.filename}: {info.file_size} bytes, more than {ZIP_MAX_FILE}")
            total += info.file_size
            if total > ZIP_MAX_TOTAL:
                raise Error("zip", f"{os.path.basename(path)} unpacks to more than {ZIP_MAX_TOTAL} bytes")
            key = "/".join(parts).casefold()
            if key in seen:
                raise Error("zip", f"{seen[key]} and {info.filename} are the same file on Windows and macOS")
            seen[key] = info.filename
            todo.append((info, parts))
        for key, name in seen.items():
            parts = key.split("/")
            for i in range(1, len(parts)):
                if "/".join(parts[:i]) in seen:
                    raise Error("zip", f"{seen['/'.join(parts[:i])]} is both a file and a folder in the zip")
        written = 0
        for info, parts in todo:
            target = os.path.join(dest, *parts)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            count = 0
            try:
                with z.open(info) as src, open(target, "xb") as out:
                    while chunk := src.read(1 << 20):
                        count += len(chunk)
                        written += len(chunk)
                        if count > info.file_size or count > ZIP_MAX_FILE or written > ZIP_MAX_TOTAL:
                            raise Error("zip", f"{info.filename} unpacks to more than its size in the zip says "
                                               "(or past the limits): not unpacked")
                        out.write(chunk)
            except (zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, RuntimeError, EOFError,
                    zlib.error) as e:
                raise Error("zip", f"{info.filename} can't be unpacked ({e}); unpack the zip yourself and add the "
                                   "folder")
    return warnings


def _copy_folder(src: str, dest: str) -> list[str]:
    warnings = []
    os.makedirs(dest)
    for rel, path in _files_under(src, "", warnings):
        target = _abs(dest, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(path, target)
    return warnings


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _delete(path: str) -> None:
    """Delete a mod's folder (only the link, for one linked into the library)."""
    if os.path.islink(path):
        os.unlink(path)
    else:
        _rmtree(path)


def _turn_on(mods: list[Mod], mod: Mod, messages: list[str]) -> None:
    """Turn a mod on, unless another code mod is on: then it stays off, and says why."""
    try:
        check_code_rule(mods, mod)
    except Error as e:
        mod.enabled = False
        messages.append(f"{mod.id} stays off: {e.message}")
        return
    mod.enabled = True
    messages.append(f"{mod.id} is on; deploy puts it in Azahar's mods folder")


def cmd_add(ctx: Context, args) -> dict:
    lib = ctx.library
    source = os.path.abspath(args.path)
    if not os.path.exists(source):
        raise Error("not_found", f"{args.path} doesn't exist")
    mods, warnings = lib.mods()
    messages: list[str] = []
    if os.path.isdir(source) and _same_path(os.path.dirname(source), lib.path):
        mod = find(mods, os.path.basename(source))      # a folder already in the library
        if mod.errors:
            raise Error("layout", f"{mod.id}: {'; '.join(mod.errors)}", errors=mod.errors)
        if not mod.enabled:
            _turn_on(mods, mod, messages)
        lib.save(mods)
        return dict(overview(ctx, mods, warnings), mod=mod.id, messages=messages or [f"{mod.id} is on"])
    if _within(lib.path, source) or (_within(ctx.folder, source) and not _same_path(ctx.folder, source)):
        raise Error("bad_path", f"{args.path} holds the mod library or Azahar's mods folder: add a mod's own folder")
    if _same_path(ctx.folder, source) and os.path.isfile(os.path.join(source, DEPLOYED)):
        raise Error("bad_path", "Azahar's mods folder holds files the mod manager put there: undeploy first, then add "
                                "the folder to keep what is left in it as a mod")
    kind = "folder" if os.path.isdir(source) else archive_kind(source)
    if kind == "unknown" and source.lower().endswith(".zip"):
        kind = "zip"                    # (a damaged one: says so)
    if kind not in ("folder", "zip", "7z", "tar"):
        raise Error("unsupported", f"{args.path}: " + (_MANUAL.get(kind) or "only folders and .zip, .7z and .tar "
                                                       "archives can be added (unpack it yourself and add the folder)"))
    os.makedirs(lib.path, exist_ok=True)
    tmp = os.path.join(lib.path, f".adding-{uuid.uuid4().hex[:8]}")
    try:
        if kind == "zip":
            notes = _unpack_zip(source, tmp)
        elif kind == "folder":
            notes = _copy_folder(source, tmp)
        else:
            notes = extract_archive(source, tmp, kind=kind, progress=ctx.progress)
        os.makedirs(tmp, exist_ok=True)
        stem = os.path.basename(source.rstrip("/\\"))
        return _add_unpacked(ctx, mods, warnings, tmp, stem if kind == "folder" else _stem(stem),
                             os.path.basename(source), args, notes)
    finally:
        if os.path.isdir(tmp):
            _rmtree(tmp)


def _add_unpacked(ctx: Context, mods: list[Mod], warnings: list[str], tmp: str, stem: str, label: str, args,
                  notes: list[str]) -> dict:
    """Put a mod unpacked into tmp (a new folder in the library) in the library: under its name,
    or in place of the mod of that name with args.replace. tmp is gone afterwards."""
    lib = ctx.library
    messages: list[str] = []
    try:
        probe = scan(tmp, "new")
        if probe.errors:
            raise Error("layout", f"{label}: {'; '.join(probe.errors)}", errors=probe.errors)
        mid = mod_id(args.name or probe.manifest.get("name") or stem)
        existing = next((m for m in mods if m.id.casefold() == mid.casefold()), None)
        if existing and not args.replace:
            raise Error("exists", f"a mod {existing.id!r} is already in the library: add with --replace to update it, "
                                  "or with --name to keep both", mod=existing.id)
        if existing:
            old = os.path.join(lib.path, f".old-{uuid.uuid4().hex[:8]}")
            os.rename(existing.path, old)
            try:
                os.rename(tmp, existing.path)
            except OSError:
                os.rename(old, existing.path)
                raise
            _delete(old)
            mod = scan(existing.path, existing.id)
            mods[mods.index(existing)] = mod
            version = mod.manifest.get("version")
            messages.append(f"{mod.id} updated" + (f" to {version}" if version else "") + ", in the same place")
            turn_on = existing.enabled
        else:
            os.rename(tmp, os.path.join(lib.path, mid))
            mod = scan(os.path.join(lib.path, mid), mid)
            mods.append(mod)
            messages.append(f"{mod.id} added to the library, last in the order")
            turn_on = not args.disabled
    finally:
        if os.path.isdir(tmp):
            _rmtree(tmp)
    mod.warnings[:0] = notes
    if turn_on:
        _turn_on(mods, mod, messages)
    lib.save(mods)
    return dict(overview(ctx, mods, warnings), mod=mod.id, messages=messages)


def cmd_remove(ctx: Context, args) -> dict:
    """A mod from the library, or an installed texture pack; by its name, folder or catalog id."""
    mods, warnings = ctx.library.mods()
    try:
        mod = find(mods, args.name)
    except Error as e:
        if e.code != "not_found":
            raise
        mod = next((m for m in mods if m.manifest.get("catalog", "").casefold() == args.name.casefold()), None)
        if mod is None:
            pid = find_pack(read_textures(ctx), args.name) if os.path.isfile(
                os.path.join(ctx.library.path, TEXTURES_RECORD)) else None
            if pid is None:
                raise Error("not_found", f"no mod or texture pack {args.name!r} installed", name=args.name)
            done = remove_textures(ctx, pid)
            return dict(overview(ctx, mods, warnings), textures=textures_info(ctx), removed_pack=pid,
                        messages=[f"{done['name']} removed from the texture folder ({done['removed']} files)"])
    _delete(mod.path)
    mods.remove(mod)
    ctx.library.save(mods)
    return dict(overview(ctx, mods, warnings), messages=[f"{mod.id} removed from the library; deploy takes its "
                                                         "files out of Azahar's mods folder"])


def cmd_enable(ctx: Context, args) -> dict:
    mods, warnings = ctx.library.mods()
    mod = find(mods, args.name)
    if mod.errors:
        raise Error("mod_errors", f"{mod.id} can't be used: {'; '.join(mod.errors)}", errors=mod.errors)
    check_code_rule(mods, mod)
    mod.enabled = True
    ctx.library.save(mods)
    return dict(overview(ctx, mods, warnings), messages=[f"{mod.id} is on; deploy puts it in Azahar's mods folder"])


def cmd_disable(ctx: Context, args) -> dict:
    mods, warnings = ctx.library.mods()
    mod = find(mods, args.name)
    mod.enabled = False
    ctx.library.save(mods)
    return dict(overview(ctx, mods, warnings), messages=[f"{mod.id} is off; deploy takes it out of Azahar's mods "
                                                         "folder"])


def cmd_order(ctx: Context, args) -> dict:
    mods, warnings = ctx.library.mods()
    mod = find(mods, args.name)
    word = args.position.lower()
    if word in ("first", "last"):
        position = 1 if word == "first" else len(mods)
    else:
        try:
            position = int(word)
        except ValueError:
            raise Error("usage", f"position {args.position!r}: a number from 1 to {len(mods)}, first or last")
        if not 1 <= position <= len(mods):
            raise Error("usage", f"position {position}: from 1 to {len(mods)} (later mods win)")
    mods.remove(mod)
    mods.insert(position - 1, mod)
    ctx.library.save(mods)
    return dict(overview(ctx, mods, warnings), messages=[f"{mod.id} is now {position} of {len(mods)}"])


def cmd_conflicts(ctx: Context, args) -> dict:
    mods, warnings = ctx.library.mods()
    chosen = [m for m in mods if (m.enabled or args.all) and not m.errors]
    _, conflicts, notes = make_plan(chosen)
    return {"library": ctx.library.path, "all": bool(args.all), "conflicts": conflicts,
            "warnings": warnings + notes}


def cmd_deploy(ctx: Context, args) -> dict:
    mods, warnings = ctx.library.mods()
    undeploy = getattr(args, "undeploy", False)
    an = analyze(ctx.library, ctx.folder, mods, undeploy=undeploy)
    result = {"library": ctx.library.path, "mods_folder": ctx.folder, "dry_run": bool(args.dry_run),
              "deploy": an.summary(), "warnings": warnings + an.warnings}
    if args.dry_run:
        return result
    if not undeploy and not os.path.isdir(ctx.user_dir):
        raise Error("not_found", f"Azahar's user folder {ctx.user_dir} doesn't exist: start Azahar once, or give "
                                 "--user-dir")
    done = apply(an, ctx.library, [] if undeploy else mods, force=getattr(args, "force", False),
                 copy=getattr(args, "copy", False))
    result.update(done)
    result["deploy"] = analyze(ctx.library, ctx.folder, mods, undeploy=undeploy).summary()
    result["code_library"] = code_library_state(ctx.user_dir, ctx.code_library)
    return result


def cmd_status(ctx: Context, args) -> dict:
    mods, warnings = ctx.library.mods()
    an = analyze(ctx.library, ctx.folder, mods)
    return {"library": ctx.library.path, "mods_folder": ctx.folder, "deploy": an.summary(),
            "conflict_count": len(an.conflicts), "code_library": code_library_state(ctx.user_dir, ctx.code_library),
            "warnings": warnings + an.warnings}


# ── progress, for the launcher ──────────────────────────────────────────────

class Progress:
    """Progress of a long command (a download, unpacking). With --json each update is one line
    "@{...}" before the result (the launcher shows them and leaves them out of the result);
    otherwise a line on standard error that a carriage return starts over."""

    def __init__(self, as_json: bool, stream=None, interval: float = 0.25):
        self.as_json, self.interval = as_json, interval
        self.stream = stream
        self.last, self.open_line = 0.0, False

    def _out(self):
        return self.stream or (sys.stdout if self.as_json else sys.stderr)

    def event(self, event: str, **data) -> None:
        """A stage starts or ends: always shown."""
        self._emit(dict(data, event=event), force=True)

    def update(self, event: str, done: int, total: int | None, **data) -> None:
        """How far a stage is: shown a few times a second."""
        now = time.monotonic()
        if now - self.last < self.interval and (total is None or done < total):
            return
        self.last = now
        self._emit(dict(data, event=event, done=done, total=total), force=False)

    def _emit(self, data: dict, force: bool) -> None:
        out = self._out()
        if self.as_json:
            out.write("@" + json.dumps(data, ensure_ascii=False) + "\n")
            out.flush()
            return
        text = data.get("message") or data.get("event", "")
        if "done" in data:
            done, total = data["done"], data.get("total")
            text = f"{data.get('what', data['event'])}: {_mb(done)}" + (f" of {_mb(total)}" if total else "")
        if force:
            if self.open_line:
                out.write("\n")
            out.write(text + "\n")
            self.open_line = False
        else:
            out.write("\r" + text)
            self.open_line = True
        out.flush()

    def end_line(self) -> None:
        if self.open_line and not self.as_json:
            self._out().write("\n")
            self.open_line = False


def _mb(n: int | None) -> str:
    if n is None:
        return "?"
    if n < 1 << 20:
        return f"{max(1, round(n / 1024))} KB" if n else "0 KB"
    return f"{n / (1 << 30):.2f} GB" if n >= 1 << 30 else f"{n / (1 << 20):.1f} MB"


# ── archives: .zip, .7z, .tar, as the catalog's downloads come ───────────────

ARCHIVE_MAX_FILE = 4 << 30
ARCHIVE_MAX_TOTAL = 64 << 30       # a whole HD texture pack is about 12 GB
_MANUAL = {
    "rar": "a .rar archive, which Python can't unpack: unpack it yourself (7-Zip, WinRAR, The Unarchiver) and add "
           "the folder",
    "unknown": "not an archive this can read (.zip, .7z or .tar): unpack it yourself and add the folder",
}


def archive_kind(path: str) -> str:
    """"zip", "7z", "tar", "rar" or "unknown", from the file's first bytes."""
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError as e:
        raise Error("io", f"{path}: {e}")
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        return "zip"
    if head.startswith(sevenzip.SIGNATURE):
        return "7z"
    if head.startswith(b"Rar!\x1a\x07"):
        return "rar"
    try:
        if tarfile.is_tarfile(path):
            return "tar"
    except OSError:
        pass
    return "unknown"


def _stem(name: str) -> str:
    """A download's name without its archive extension(s)."""
    low = name.lower()
    for ext in (".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".zip", ".7z", ".tar", ".rar"):
        if low.endswith(ext) and len(name) > len(ext):
            return name[:-len(ext)]
    return os.path.splitext(name)[0] or name


@dataclass
class Member:
    """A file in an archive."""
    parts: list[str]
    size: int
    ref: object             # what the archive's reader needs to read it


def _members(path: str, kind: str, warnings: list[str]):
    """(an opened archive, its files as Members). Folders, links, devices, the "anti" items of a
    .7z and names Windows can't have are left out (with a warning); a path that would leave the
    folder it is unpacked into refuses the whole archive."""
    name = os.path.basename(path)
    out: list[Member] = []

    def keep(raw: str, size: int, ref) -> None:
        try:
            parts = relative_parts(raw)
        except Error as e:
            if e.code == "bad_name":
                warnings.append(f"{raw}: a name Windows can't have, not unpacked")
                return
            raise Error("archive", f"{name}: {e.message}")
        if parts and not any(ignored(p) for p in parts):
            out.append(Member(parts, size, ref))

    if kind == "zip":
        try:
            arc = zipfile.ZipFile(path)
        except (OSError, zipfile.BadZipFile) as e:
            raise Error("zip", f"{name} can't be opened as a zip ({e})")
        for info in arc.infolist():
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                warnings.append(f"{info.filename}: a symbolic link, not unpacked")
            elif not info.is_dir():
                if info.flag_bits & 1:
                    arc.close()
                    raise Error("zip", f"{name} is password-protected: unpack it yourself and add the folder")
                keep(info.filename, info.file_size, info)
    elif kind == "7z":
        try:
            f = open(path, "rb")
            arc = sevenzip.Archive(f)
            arc.check_supported()
        except sevenzip.Error as e:
            f.close()
            raise Error("manual_extract" if e.code == "unsupported" else "archive", f"{name}: {e.message}" + (
                ": unpack it with 7-Zip yourself" if e.code == "unsupported" else ""))
        arc.close = f.close
        for e in arc.entries:
            if e.is_link:
                warnings.append(f"{e.name}: a symbolic link, not unpacked")
            elif not e.is_dir and not e.is_anti:
                keep(e.name, e.size, e)
    elif kind == "tar":
        try:
            arc = tarfile.open(path)
        except (OSError, tarfile.TarError) as e:
            raise Error("archive", f"{name} can't be opened as a tar archive ({e})")
        for info in arc:
            if info.isfile():
                keep(info.name, info.size, info)
            elif not info.isdir():
                warnings.append(f"{info.name}: not a plain file, not unpacked")
    else:
        raise Error("manual_extract" if kind == "rar" else "unsupported", f"{name}: {_MANUAL[kind]}")
    if len(out) > ZIP_MAX_ENTRIES:
        arc.close()
        raise Error("archive", f"{name} has {len(out)} files, more than {ZIP_MAX_ENTRIES}")
    return arc, out


def _match_prefix(members: list[Member], want: list[str]) -> list[str] | None:
    """The members' own spelling of the folder `want` (capitals don't count), if they have it."""
    n = len(want)
    target = [w.casefold() for w in want]
    for m in members:
        if len(m.parts) > n and [p.casefold() for p in m.parts[:n]] == target:
            return m.parts[:n]
    return None


def _select(members: list[Member], root: str | None, name: str) -> tuple[list[Member], list[str]]:
    """The members under `root` (a folder in the archive, perhaps inside the one folder that
    wraps everything), with paths from there on."""
    if not root:
        return members, []
    want = relative_parts(root, f"{name}: root")
    prefix = _match_prefix(members, want)
    if prefix is None:
        tops = {m.parts[0] for m in members if len(m.parts) > 1}
        if len(tops) == 1:
            prefix = _match_prefix(members, [next(iter(tops))] + want)
    if prefix is None:
        raise Error("layout", f"{name}: no folder {root!r} in it (the download changed? see the mod's page)")
    n = len(prefix)
    keep = [Member(m.parts[n:], m.size, m.ref) for m in members
            if len(m.parts) > n and [p.casefold() for p in m.parts[:n]] == [p.casefold() for p in prefix]]
    return keep, prefix


def extract_archive(path: str, dest: str, kind: str | None = None, root: str | None = None, into: str | None = None,
                    overwrite: bool = False, progress: Progress | None = None,
                    max_total: int = ARCHIVE_MAX_TOTAL, duplicates: str = "refuse") -> list[str]:
    """Unpack an archive (.zip, .7z, .tar) into dest: only what is under `root` if given, into
    the folder `into` of dest. Paths are checked as for a mod's zip (nothing outside dest), sizes
    are limited, and the free space is checked first. Two names that are one file on Windows
    and macOS refuse it, or with duplicates="last" the later one is used (a texture pack's
    thousands of files shouldn't fail for one). overwrite: files already in dest are replaced
    (else that refuses). Returns warnings."""
    kind = kind or archive_kind(path)
    name = os.path.basename(path)
    warnings: list[str] = []
    arc, members = _members(path, kind, warnings)
    try:
        members, _ = _select(members, root, name)
        base = os.path.join(dest, *relative_parts(into, f"{name}: into")) if into else dest
        seen: dict[str, str] = {}
        chosen: dict[str, Member] = {}
        for m in members:
            key = "/".join(m.parts).casefold()
            if key in seen and seen[key] != "/".join(m.parts):
                if duplicates != "last":
                    raise Error("archive", f"{name}: {seen[key]} and {'/'.join(m.parts)} are the same file on "
                                           "Windows and macOS")
                warnings.append(f"{seen[key]} and {'/'.join(m.parts)} are one file on Windows and macOS: "
                                f"{'/'.join(m.parts)} used")
            seen[key] = "/".join(m.parts)
            chosen.pop(key, None)       # (a tar may hold a file twice: the later one counts)
            chosen[key] = m
        members = list(chosen.values())
        total = 0
        for m in members:
            if m.size > ARCHIVE_MAX_FILE:
                raise Error("archive", f"{name}: {'/'.join(m.parts)} is {_mb(m.size)}, more than "
                                       f"{_mb(ARCHIVE_MAX_FILE)}")
            total += m.size
        if total > max_total:
            raise Error("archive", f"{name} unpacks to {_mb(total)}, more than {_mb(max_total)}")
        for key in seen:
            parts = key.split("/")
            for i in range(1, len(parts)):
                if "/".join(parts[:i]) in seen:
                    raise Error("archive", f"{name}: {seen['/'.join(parts[:i])]} is both a file and a folder")
        check_space(dest, total, f"to unpack {name}")
        os.makedirs(base, exist_ok=True)
        state = {"written": 0}
        what = f"Unpacking {name}"
        if progress:
            progress.event("unpack", file=name, total=total, message=what)

        def target_of_member(m: Member) -> str:
            t = os.path.join(base, *m.parts)
            if os.path.lexists(t):
                if not overwrite or os.path.isdir(t) or os.path.islink(t):
                    raise Error("archive", f"{name}: {'/'.join(m.parts)} is there already")
                _remove_file(t)
            os.makedirs(os.path.dirname(t), exist_ok=True)
            return t

        def copy_stream(src, m: Member) -> None:
            count = 0
            with open(target_of_member(m), "xb") as out:
                while chunk := src.read(1 << 20):
                    count += len(chunk)
                    if count > m.size:
                        raise Error("archive", f"{name}: {'/'.join(m.parts)} unpacks to more than its size in the "
                                               "archive says: not unpacked")
                    out.write(chunk)
                    state["written"] += len(chunk)
                    if progress:
                        progress.update("unpack", state["written"], total, what=what)

        if kind == "zip":
            for m in members:
                try:
                    with arc.open(m.ref) as src:
                        copy_stream(src, m)
                except (zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, RuntimeError, EOFError,
                        zlib.error) as e:
                    raise Error("zip", f"{'/'.join(m.parts)} can't be unpacked ({e}); unpack {name} yourself")
        elif kind == "tar":
            for m in members:
                try:
                    src = arc.extractfile(m.ref)
                    if src is None:
                        continue
                    with src:
                        copy_stream(src, m)
                except (tarfile.TarError, EOFError, zlib.error, OSError) as e:
                    raise Error("archive", f"{'/'.join(m.parts)} can't be unpacked ({e}); unpack {name} yourself")
        else:   # 7z: folder by folder, each file's pieces in order
            by_entry = {id(m.ref): m for m in members}
            open_files: dict[int, object] = {}

            def sink(entry, chunk: bytes) -> None:
                m = by_entry[id(entry)]
                out = open_files.get(id(entry))
                if out is None:
                    out = open_files[id(entry)] = open(target_of_member(m), "xb")
                if chunk:
                    out.write(chunk)
                    state["written"] += len(chunk)
                    if progress:
                        progress.update("unpack", state["written"], total, what=what)
                else:
                    out.close()
                    del open_files[id(entry)]

            try:
                arc.extract(sink, [m.ref for m in members if m.ref.folder is not None])
            except sevenzip.Error as e:
                raise Error("archive", f"{name}: {e.message}")
            finally:
                for out in open_files.values():
                    out.close()
            for m in members:               # empty files have no data to unpack
                if m.ref.folder is None:
                    open(target_of_member(m), "xb").close()
        if progress:
            progress.end_line()
    finally:
        arc.close()
    return warnings


def check_space(path: str, need: int, what: str) -> None:
    """Refuse when the drive of `path` (or the nearest folder above it) hasn't `need` bytes free."""
    probe = os.path.abspath(path)
    while not os.path.exists(probe) and os.path.dirname(probe) != probe:
        probe = os.path.dirname(probe)
    try:
        free = shutil.disk_usage(probe).free
    except OSError:
        return
    if need + (64 << 20) > free:
        raise Error("space", f"not enough free space {what}: it needs about {_mb(need)}, and {_mb(free)} are free "
                             f"on that drive", need=need, free=free)


# ── downloads ────────────────────────────────────────────────────────────────

USER_AGENT = "mh4u-port-mods/1 (https://github.com/CompaLarios18/MH4UltimateDC)"
DOWNLOADS = ".downloads"            # in the library (a hidden folder: not a mod)
DOWNLOAD_TIMEOUT = 60


def downloads_folder(ctx: Context) -> str:
    return os.path.join(ctx.library.path, DOWNLOADS)


def _is_url(text: str) -> bool:
    return re.match(r"(?i)(https?|file)://", text) is not None


def _url_name(url: str) -> str:
    path = urllib.parse.urlparse(url).path
    base = urllib.parse.unquote(path.rstrip("/").rsplit("/", 1)[-1]) or "download"
    return mod_id(base)


def download(url: str, folder: str, name: str, size: int | None = None, sha256: str | None = None,
             progress: Progress | None = None, label: str = "") -> str:
    """Download url into folder/name (http, https, file). A download that stopped goes on from
    where it stopped when the server allows (a .part file next to it); one already there is used
    again when it is complete (its size or SHA-256 as the catalog says). Returns its path."""
    os.makedirs(folder, exist_ok=True)
    final = os.path.join(folder, name)
    part = final + ".part"
    if os.path.isfile(final):
        if sha256 and _sha256(final) == sha256.lower():
            return final
        if not sha256 and (size is None or os.path.getsize(final) == size):
            return final
        _remove_file(final)
    start = os.path.getsize(part) if os.path.isfile(part) else 0
    if size is not None and start > size:
        _remove_file(part)
        start = 0
    what = label or f"Downloading {name}"
    if size is not None:
        check_space(folder, size - start, f"to download {name}")
    headers = {"User-Agent": USER_AGENT}
    if start:
        headers["Range"] = f"bytes={start}-"
    try:
        response = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=DOWNLOAD_TIMEOUT)
    except urllib.error.HTTPError as e:
        if e.code == 416 and start and size is not None and start == size:   # it was complete
            response = None
        else:
            raise Error("download", f"{url}: the server answered {e.code} {e.reason}", url=url, status=e.code)
    except (urllib.error.URLError, OSError, ValueError) as e:
        reason = getattr(e, "reason", e)
        raise Error("download", f"{url} can't be downloaded ({reason})", url=url)
    if response is not None:
        with response:
            status = getattr(response, "status", None) or response.getcode()
            if start and status != 206:
                start = 0                   # the server sends it all again
            length = response.headers.get("Content-Length")
            sent = start + int(length) if length and length.isdigit() else None
            total = sent if sent is not None else size
            if progress:
                progress.event("download", file=name, url=url, total=total, resumed=start, message=what)
            done = start
            try:
                with open(part, "ab" if start else "wb") as out:
                    while True:
                        chunk = response.read(1 << 20)
                        if not chunk:
                            break
                        out.write(chunk)
                        done += len(chunk)
                        if size is not None and done > size * 2 + (1 << 20):
                            raise Error("download", f"{url}: much bigger than the catalog says ({_mb(size)})")
                        if progress:
                            progress.update("download", done, total, what=what)
            except (OSError, ValueError) as e:
                if isinstance(e, Error):
                    raise
                raise Error("download", f"{url}: the download stopped ({e}); try again to go on from there", url=url)
            if progress:
                progress.end_line()
            if sent is not None and done < sent:
                raise Error("download", f"{url}: the download stopped at {_mb(done)} of {_mb(sent)}; try again to "
                                        "go on from there", url=url)
    got = os.path.getsize(part)
    if sha256:
        digest = _sha256(part)
        if digest != sha256.lower():
            _remove_file(part)
            raise Error("checksum", f"{name}: its SHA-256 is {digest}, not {sha256.lower()} as the catalog says (a "
                                    "damaged download, or the file changed on its site): not used", url=url)
    elif size is not None and got != size:
        raise Error("download", f"{name}: {got} bytes, not {size} as the catalog says (a damaged download, or the file "
                                "changed on its site; delete it from the downloads folder to start over)", url=url,
                    file=part)
    os.replace(part, final)
    return final


# ── texture packs: load/textures/<program id>/ ───────────────────────────────

TEXTURES_RECORD = "textures.json"   # in the library: the texture packs installed, and their files


def textures_folder(user_dir: str) -> str:
    """Where Azahar loads custom textures for the game from (its whole tree, pack.json at its top)."""
    return os.path.join(user_dir, "load", "textures", f"{PROGRAM_ID:016X}")


def read_textures(ctx: Context) -> dict:
    path = os.path.join(ctx.library.path, TEXTURES_RECORD)
    if not os.path.isfile(path):
        return {"format": FORMAT, "packs": {}}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        packs = data["packs"]
        if not isinstance(packs, dict):
            raise ValueError("no packs")
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise Error("state", f"{path} can't be read ({e}): mend or delete it (deleting it forgets which texture "
                             "files the mod manager installed)")
    return {"format": FORMAT, "packs": {k: v for k, v in packs.items() if isinstance(v, dict)
                                        and isinstance(v.get("files"), list)}}


def write_textures(ctx: Context, record: dict) -> None:
    os.makedirs(ctx.library.path, exist_ok=True)
    _write_json(os.path.join(ctx.library.path, TEXTURES_RECORD), record)


def _texture_root(staging: str) -> str:
    """The folder of an unpacked texture pack whose contents go into the game's texture folder:
    through a title id folder (load/textures/<id>/, or just <id>/), or one folder that wraps a
    pack.json; anything else stays as it is (Azahar reads the texture folder's whole tree)."""
    level = staging
    for _ in range(8):
        dirs, files = _listing(level)
        low = {d.lower(): d for d in dirs}
        if any(f.lower() == "pack.json" for f in files):
            return level
        titles = [d for d in dirs if _TITLE.fullmatch(d)]
        if titles and not [f for f in files if not doc_file(f)]:
            mine = [d for d in titles if int(d, 16) in (PROGRAM_ID, UPDATE_ID)]
            if not mine:
                raise Error("other_title", f"a texture pack for another game or region (title {', '.join(titles)}); "
                                           f"the port plays MH4U USA ({PROGRAM_ID:016X})")
            level = os.path.join(level, mine[0])
            continue
        if len(dirs) == 1 and all(doc_file(f) for f in files):
            inner_dirs, inner_files = _listing(os.path.join(level, dirs[0]))
            if dirs[0].lower() in ("load", "textures") or any(f.lower() == "pack.json" for f in inner_files) or \
                    any(_TITLE.fullmatch(d) for d in inner_dirs):
                level = os.path.join(level, dirs[0])
                continue
        if "load" in low or "textures" in low:
            sub = os.path.join(level, low.get("load") or low["textures"])
            if any(_TITLE.fullmatch(d) or d.lower() == "textures" for d in _listing(sub)[0]):
                level = sub
                continue
        return level
    return level


def find_pack(record: dict, name: str) -> str | None:
    for test in (lambda pid, p: pid == name, lambda pid, p: pid.casefold() == name.casefold(),
                 lambda pid, p: str(p.get("name", "")).casefold() == name.casefold(),
                 lambda pid, p: str(p.get("catalog", "")).casefold() == name.casefold()):
        hits = [pid for pid, p in record["packs"].items() if test(pid, p)]
        if hits:
            return hits[0]
    return None


def install_textures(ctx: Context, pack_id: str, root: str, meta: dict, force: bool = False) -> dict:
    """Move an unpacked texture pack's files (under root) into the game's texture folder and
    remember them, so remove can take them out again. Files of another pack the mod manager
    installed, or files put there by hand, are not replaced unless forced."""
    folder = textures_folder(ctx.user_dir)
    record = read_textures(ctx)
    old = record["packs"].get(pack_id, {})
    old_files = {f.casefold() for f in old.get("files", [])}
    files = [rel for rel, _ in _files_under(root, "", [])]
    owners: dict[str, str] = {}
    for pid, pack in record["packs"].items():
        if pid != pack_id:
            for f in pack.get("files", []):
                owners[f.casefold()] = pid
    clash = sorted({owners[f.casefold()] for f in files if f.casefold() in owners})
    if clash and not force:
        names = ", ".join(str(record["packs"][p].get("name", p)) for p in clash)
        raise Error("conflict", f"it has texture files that {names} installed too: remove "
                                f"{'it' if len(clash) == 1 else 'them'} first", packs=clash)
    foreign = [f for f in files if f.casefold() not in old_files and f.casefold() not in owners
               and os.path.lexists(_abs(folder, f))]
    if foreign and not force:
        shown = ", ".join(foreign[:3]) + (", ..." if len(foreign) > 3 else "")
        raise Error("foreign_files", f"{len(foreign)} of its files are in the texture folder already, put there by "
                                     f"hand or by another program ({shown}): install with --force to replace them",
                    files=foreign[:50], folder=folder)
    progress = ctx.progress
    if progress:
        progress.event("install", total=len(files), message=f"Putting {meta.get('name', pack_id)} in place")
    moved: list[str] = []
    kept: list[str] = []
    try:
        for rel in files:
            dest = _abs(folder, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if os.path.isdir(dest) and not os.path.islink(dest):
                raise Error("conflict", f"{rel} is a folder in the texture folder")
            os.replace(_abs(root, rel), dest)
            moved.append(rel)
            if progress:
                progress.update("install", len(moved), len(files), what="Putting the files in place")
        new = {f.casefold() for f in files}
        for f in old.get("files", []):      # the old version's files that the new one hasn't
            if f.casefold() not in new and os.path.isfile(_abs(folder, f)):
                _remove_file(_abs(folder, f))
                _prune(folder, f)
    except OSError as e:
        raise Error("io", f"{e}")
    finally:
        # (also when it failed half-way: what is in place now, so that remove takes it out)
        moved_cf = {f.casefold() for f in moved}
        if len(moved) < len(files):
            kept = [f for f in old.get("files", []) if f.casefold() not in moved_cf]
        for pid in clash:                   # forced: those files are this pack's now
            pack = record["packs"][pid]
            pack["files"] = [f for f in pack["files"] if f.casefold() not in moved_cf]
        listed = sorted(set(moved) | set(kept))
        record["packs"][pack_id] = dict({k: v for k, v in meta.items() if v not in (None, "")},
                                        files=listed, size=sum(os.path.getsize(_abs(folder, f)) for f in listed
                                                               if os.path.isfile(_abs(folder, f))),
                                        installed=_now())
        write_textures(ctx, record)
        if progress:
            progress.end_line()
    return {"pack": pack_id, "textures_folder": folder, "files": len(files), "replaced": len(foreign)}


def remove_textures(ctx: Context, pack_id: str) -> dict:
    folder = textures_folder(ctx.user_dir)
    record = read_textures(ctx)
    pack = record["packs"].pop(pack_id)
    removed = 0
    for rel in pack.get("files", []):
        try:
            parts = relative_parts(rel)
        except Error:
            continue
        path = _abs(folder, "/".join(parts))
        if os.path.isfile(path) and not os.path.islink(path):
            _remove_file(path)
            _prune(folder, "/".join(parts))
            removed += 1
    write_textures(ctx, record)
    return {"pack": pack_id, "name": pack.get("name", pack_id), "removed": removed}


def textures_info(ctx: Context) -> list[dict]:
    record = read_textures(ctx)
    return [{"id": pid, "name": p.get("name", pid), "version": p.get("version", ""), "catalog": p.get("catalog", ""),
             "files": len(p.get("files", [])), "size": p.get("size"), "installed": p.get("installed", "")}
            for pid, p in sorted(record["packs"].items())]


# ── the catalog: mods recommended for the port, installed with one command ────

CATALOG = os.path.join(HERE, "mods_catalog.json")       # comes with the tools
CATALOG_URL = ("https://raw.githubusercontent.com/CompaLarios18/MH4UltimateDC/main/tools/"
               "mods_catalog.json")                      # a newer one, when there is (the app's default too)
CATALOG_CACHE = "catalog-cache.json"                    # in the library: the last one fetched
CATALOG_MAX = 1 << 20
_CATALOG_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_SHA256 = re.compile(r"[0-9a-fA-F]{64}")
ENTRY_KINDS = ("textures", "mod")
ARCHIVES = ("auto", "zip", "7z", "tar", "file")


def _text(e: dict, key: str, limit: int = 2000) -> str:
    v = e.get(key)
    return v.strip()[:limit] if isinstance(v, str) else ""


def _url_ok(url: str, remote: bool) -> bool:
    if not isinstance(url, str) or len(url) > 2000:
        return False
    scheme = urllib.parse.urlparse(url).scheme.lower()
    return scheme == "https" if remote else scheme in ("https", "http", "file")


def validate_catalog(data, remote: bool, pinned: bool | None = None) -> tuple[dict, list[str]]:
    """The catalog's entries as the commands use them; entries that aren't right are left out
    with a warning (a catalog fetched from the web is data, checked like a zip's paths).
    pinned (by default: remote): every download of a mod needs its sha256. True for the web's,
    the cached one and the one that comes with the tools; a catalog file of the player's own
    (--catalog) is theirs to trust."""
    if pinned is None:
        pinned = remote
    if not isinstance(data, dict) or not isinstance(data.get("mods"), list):
        raise Error("catalog", "the catalog has no list of mods")
    warnings, entries, ids = [], [], set()
    for i, e in enumerate(data["mods"]):
        where = f"catalog entry {i + 1}"
        try:
            if not isinstance(e, dict):
                raise Error("catalog", f"{where} isn't an object")
            cid = e.get("id")
            if not isinstance(cid, str) or not _CATALOG_ID.fullmatch(cid) or cid in ids:
                raise Error("catalog", f"{where}: a missing, repeated or malformed id")
            where = f"catalog entry {cid}"
            kind = e.get("kind")
            if kind not in ENTRY_KINDS:
                raise Error("catalog", f"{where}: kind should be one of {', '.join(ENTRY_KINDS)}")
            name = _text(e, "name", 120)
            page = e.get("page_url", "")
            if not name or not _url_ok(page, True):
                raise Error("catalog", f"{where}: a name and an https page_url are needed")
            steps = []
            for d in e.get("downloads") or []:
                if not isinstance(d, dict) or not _url_ok(d.get("url"), remote):
                    raise Error("catalog", f"{where}: a download without a usable url")
                archive = d.get("archive", "auto")
                if archive not in ARCHIVES:
                    raise Error("catalog", f"{where}: archive should be one of {', '.join(ARCHIVES)}")
                step = {"url": d["url"], "archive": archive,
                        "file": mod_id(d["file"]) if isinstance(d.get("file"), str) else _url_name(d["url"])}
                for key in ("size",):
                    if d.get(key) is not None:
                        if not isinstance(d[key], int) or isinstance(d[key], bool) or d[key] < 0:
                            raise Error("catalog", f"{where}: {key} should be a number of bytes")
                        step[key] = d[key]
                if d.get("sha256"):
                    if not isinstance(d["sha256"], str) or not _SHA256.fullmatch(d["sha256"]):
                        raise Error("catalog", f"{where}: sha256 should be 64 hexadecimal digits")
                    step["sha256"] = d["sha256"].lower()
                elif kind == "mod" and pinned:
                    # a mod can carry a code patch, which becomes native code on the player's PC:
                    # what the catalog points at must be exactly the file that was reviewed
                    raise Error("catalog", f"{where}: a mod's download needs its sha256")
                for key in ("root", "into", "as"):
                    if d.get(key):
                        if not isinstance(d[key], str):
                            raise Error("catalog", f"{where}: {key} should be a path")
                        relative_parts(d[key], f"{where}: {key}")
                        step[key] = d[key]
                if archive == "file" and not step.get("as"):
                    raise Error("catalog", f"{where}: a single file needs 'as' (where it goes in the mod)")
                delete_first = d.get("delete_first") or []
                if not isinstance(delete_first, list) or not all(isinstance(p, str) for p in delete_first):
                    raise Error("catalog", f"{where}: delete_first should list paths")
                for p in delete_first:
                    relative_parts(p, f"{where}: delete_first")
                step["delete_first"] = delete_first
                steps.append(step)
            def strings(key):
                v = e.get(key) or []
                return [x for x in v if isinstance(x, str)][:20] if isinstance(v, list) else []
            entry = {"id": cid, "kind": kind, "name": name, "author": _text(e, "author", 200),
                     "version": _text(e, "version", 40), "description": _text(e, "description"),
                     "details": _text(e, "details", 4000), "category": _text(e, "category", 60),
                     "region": _text(e, "region", 40), "region_note": _text(e, "region_note", 400),
                     "license": _text(e, "license", 400), "page_url": page, "recommended": e.get("recommended") is True,
                     "needs": strings("needs"), "conflicts": strings("conflicts"), "downloads": steps,
                     "note": _text(e, "note", 1000)}
            for key in ("size", "installed_size"):
                v = e.get(key)
                entry[key] = v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None
            if entry["size"] is None and steps and all("size" in s for s in steps):
                entry["size"] = sum(s["size"] for s in steps)
            ids.add(cid)
            entries.append(entry)
        except Error as err:
            warnings.append(err.message)
    updated = data.get("updated") if isinstance(data.get("updated"), str) else ""
    return {"format": data.get("format"), "updated": updated, "mods": entries}, warnings


def _read_catalog_file(path: str, remote: bool, pinned: bool | None = None) -> tuple[dict, list[str]]:
    try:
        with open(path, encoding="utf-8") as f:
            return validate_catalog(json.load(f), remote, pinned)
    except (OSError, ValueError) as e:
        raise Error("catalog", f"{path} can't be read ({e})")


def fetch_catalog(url: str) -> dict:
    """The catalog from the web (a small JSON file), not yet checked."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read(CATALOG_MAX + 1)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise Error("download", f"{url} can't be fetched ({getattr(e, 'reason', e)})")
    if len(raw) > CATALOG_MAX:
        raise Error("catalog", f"{url}: bigger than a catalog can be")
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except ValueError as e:
        raise Error("catalog", f"{url} isn't a catalog ({e})")


def load_catalog(ctx: Context, path: str | None = None, url: str | None = None,
                 refresh: bool = False) -> tuple[dict, dict]:
    """(the catalog, where it came from). An explicit file is used as it is. Otherwise the newest
    (by its "updated" date) of the one that comes with the tools, the one fetched last time
    and, with refresh, the one at url (fetched now, kept for next time)."""
    if path:
        cat, warnings = _read_catalog_file(path, remote=False)
        return cat, {"source": "file", "path": os.path.abspath(path), "updated": cat["updated"], "warnings": warnings}
    cat, warnings = _read_catalog_file(CATALOG, remote=False, pinned=True)
    info = {"source": "bundled", "path": CATALOG, "updated": cat["updated"], "warnings": warnings}
    cache = os.path.join(ctx.library.path, CATALOG_CACHE)
    notes: list[str] = []
    other = None
    if refresh and url:
        try:
            raw = fetch_catalog(url)
            fetched, more = validate_catalog(raw, remote=True)
            os.makedirs(ctx.library.path, exist_ok=True)
            _write_json(cache, raw)
            other = (fetched, {"source": "remote", "url": url, "updated": fetched["updated"], "warnings": more})
        except Error as e:
            notes.append(f"the catalog on the web can't be had ({e.message}); using the copy here")
    if other is None and os.path.isfile(cache):
        try:
            cached, more = _read_catalog_file(cache, remote=True)
            other = (cached, {"source": "cache", "path": cache, "updated": cached["updated"], "warnings": more})
        except Error as e:
            notes.append(f"the catalog fetched last time can't be read ({e.message})")
    # the newer one (the tools' own after an update of the app; else the one from the web)
    if other is not None and other[0]["mods"] and other[0]["updated"] >= cat["updated"]:
        cat, info = other
    info["warnings"] = notes + info["warnings"]
    return cat, info


def catalog_entry(cat: dict, cid: str) -> dict:
    for e in cat["mods"]:
        if e["id"] == cid or e["id"] == cid.casefold():
            return e
    raise Error("not_found", f"no mod {cid!r} in the catalog", name=cid)


def _installed(ctx: Context, mods: list[Mod], record: dict, entry: dict) -> dict | None:
    """How a catalog entry is installed: a texture pack, or a mod in the library (by the catalog
    id its mod.json keeps)."""
    if entry["kind"] == "textures":
        pid = next((p for p, v in record["packs"].items() if v.get("catalog") == entry["id"]), None)
        if pid is None:
            return None
        pack = record["packs"][pid]
        return {"as": "textures", "id": pid, "version": pack.get("version", ""), "files": len(pack.get("files", []))}
    mod = next((m for m in mods if m.manifest.get("catalog") == entry["id"]), None)
    if mod is None:
        return None
    return {"as": "mod", "id": mod.id, "version": mod.manifest.get("version", ""), "enabled": mod.enabled,
            "files": len(mod.files)}


def cmd_catalog(ctx: Context, args) -> dict:
    cat, info = load_catalog(ctx, args.catalog, args.catalog_url, args.refresh)
    mods, _ = ctx.library.mods()
    record = read_textures(ctx)
    downloads = downloads_folder(ctx)
    entries = []
    for e in cat["mods"]:
        partial = 0
        for step in e["downloads"]:
            for p in (os.path.join(downloads, step["file"]), os.path.join(downloads, step["file"] + ".part")):
                if os.path.isfile(p):
                    partial += os.path.getsize(p)
        installed = _installed(ctx, mods, record, e)
        out = {k: v for k, v in e.items() if k != "downloads"}
        out.update(direct=bool(e["downloads"]), archives=sorted({s["archive"] for s in e["downloads"]}),
                   downloads=[{"url": s["url"], "file": s["file"], "size": s.get("size")} for s in e["downloads"]],
                   installed=installed, downloaded=partial,
                   update=bool(installed and e["version"] and installed["version"] and
                               installed["version"] != e["version"]))
        entries.append(out)
    return {"catalog": info, "entries": entries, "textures_folder": textures_folder(ctx.user_dir),
            "mods_folder": ctx.folder, "downloads_folder": downloads, "textures": textures_info(ctx),
            "warnings": info["warnings"]}


def _clear(staging: str, rel: str) -> None:
    """delete_first: a folder or file in what was unpacked so far (capitals don't count)."""
    level = staging
    for part in relative_parts(rel):
        dirs, files = _listing(level)
        match = next((n for n in dirs + files if n.casefold() == part.casefold()), None)
        if match is None:
            return
        level = os.path.join(level, match)
    if os.path.isdir(level) and not os.path.islink(level):
        _rmtree(level)
    elif os.path.lexists(level):
        _remove_file(level)


def _unpack_step(path: str, kind: str, staging: str, step: dict, overwrite: bool, progress: Progress | None,
                 max_total: int, duplicates: str) -> list[str]:
    for rel in step.get("delete_first", []):
        _clear(staging, rel)
    if kind == "file":
        dest = os.path.join(staging, *relative_parts(step["as"]))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.lexists(dest):
            _remove_file(dest)
        shutil.copyfile(path, dest)
        return []
    return extract_archive(path, staging, kind=kind, root=step.get("root"), into=step.get("into"),
                           overwrite=overwrite, progress=progress, max_total=max_total, duplicates=duplicates)


def _staging(ctx: Context, textures: bool) -> str:
    """A new folder to unpack into, on the drive where the files end up (moving them is then quick)."""
    base = os.path.dirname(textures_folder(ctx.user_dir)) if textures else ctx.library.path
    os.makedirs(base, exist_ok=True)
    if textures:            # what an install that was stopped half-way left (gigabytes, perhaps)
        for name in os.listdir(base):
            old = os.path.join(base, name)
            if name.startswith(".mh4u-staging-") and os.path.isdir(old) and not os.path.islink(old):
                _rmtree(old)
    path = os.path.join(base, f".mh4u-staging-{uuid.uuid4().hex[:8]}" if textures else
                        f".adding-{uuid.uuid4().hex[:8]}")
    os.makedirs(path)
    return path


def _mod_json(path: str, meta: dict) -> None:
    """The catalog's description of a mod as its mod.json (which the library shows)."""
    _write_json(os.path.join(path, MANIFEST), {k: v for k, v in meta.items() if v})


def cmd_install_catalog(ctx: Context, args) -> dict:
    cat, info = load_catalog(ctx, args.catalog, args.catalog_url, False)
    entry = catalog_entry(cat, args.id)
    progress = ctx.progress
    if not entry["downloads"]:
        raise Error("page_only", f"{entry['name']} is installed from its page: {entry['page_url']}",
                    page_url=entry["page_url"])
    mods, warnings = ctx.library.mods()
    record = read_textures(ctx)
    others = [c for c in cat["mods"] if c["id"] in entry["conflicts"] and _installed(ctx, mods, record, c)]
    if others and not args.force:
        names = ", ".join(c["name"] for c in others)
        raise Error("conflict", f"{entry['name']} doesn't go with {names}, which is installed: remove it first",
                    entries=[c["id"] for c in others])
    textures = entry["kind"] == "textures"
    downloads = downloads_folder(ctx)
    need = sum(s.get("size") or 0 for s in entry["downloads"]) + (entry.get("installed_size") or 0)
    if need:
        check_space(ctx.user_dir, need, f"to download and install {entry['name']}")
    paths = []
    steps = len(entry["downloads"])
    for i, step in enumerate(entry["downloads"], 1):
        label = f"Downloading {entry['name']}" + (f" ({i} of {steps})" if steps > 1 else "")
        if progress:
            progress.event("step", step=i, steps=steps, message=label)
        paths.append(download(step["url"], downloads, step["file"], step.get("size"), step.get("sha256"), progress,
                              label))
    staging = _staging(ctx, textures)
    notes: list[str] = []
    try:
        for i, (step, path) in enumerate(zip(entry["downloads"], paths), 1):
            kind = "file" if step["archive"] == "file" else archive_kind(path)
            if step["archive"] not in ("auto", "file") and kind != step["archive"]:
                raise Error("archive", f"{os.path.basename(path)} isn't a {step['archive']} archive as the catalog "
                                       "says (the file changed on its site?)")
            if kind in ("rar", "unknown"):
                raise Error("manual_extract", f"{os.path.basename(path)}: {_MANUAL[kind]}", download=path,
                            target=textures_folder(ctx.user_dir) if textures else ctx.library.path,
                            page_url=entry["page_url"])
            if progress:
                progress.event("step", step=i, steps=steps, message=f"Unpacking {entry['name']}" +
                               (f" ({i} of {steps})" if steps > 1 else ""))
            try:
                notes += _unpack_step(path, kind, staging, step, i > 1, progress,
                                      ARCHIVE_MAX_TOTAL if textures else ZIP_MAX_TOTAL,
                                      "last" if textures else "refuse")
            except Error as e:
                if e.code == "manual_extract":
                    e.details.update(download=path, page_url=entry["page_url"],
                                     target=textures_folder(ctx.user_dir) if textures else ctx.library.path)
                raise
        meta = {"name": entry["name"], "version": entry["version"], "author": entry["author"],
                "description": entry["description"], "url": entry["page_url"], "catalog": entry["id"]}
        if textures:
            pid = next((p for p, v in record["packs"].items() if v.get("catalog") == entry["id"]), None) or \
                mod_id(entry["id"])
            done = install_textures(ctx, pid, _texture_root(staging), meta, force=args.force)
            result = dict(done, messages=[f"{entry['name']} installed in {done['textures_folder']}"
                                          + ("; turn on custom textures (the launcher's Settings, or custom_textures = "
                                             "on in mh4u-app.ini) to see it" if "custom_textures" in entry["needs"]
                                             else "")])
        else:
            _mod_json(staging, meta)
            existing = next((m for m in mods if m.manifest.get("catalog") == entry["id"]), None)
            ns = argparse.Namespace(name=existing.id if existing else entry["name"], replace=existing is not None,
                                    disabled=False)
            result = _add_unpacked(ctx, mods, warnings, staging, entry["id"], entry["name"], ns, notes)
    finally:
        if os.path.isdir(staging):
            _rmtree(staging)
    if not args.keep_download:
        for path in paths:
            if os.path.isfile(path):
                _remove_file(path)
    return dict(result, id=entry["id"], kind=entry["kind"], needs=entry["needs"], catalog=info,
                warnings=list(result.get("warnings", [])) + notes)


def cmd_install(ctx: Context, args) -> dict:
    """A mod or texture pack from a URL, an archive or a folder."""
    source = args.source
    if _is_url(source):
        name = mod_id(args.file) if args.file else _url_name(source)
        path = download(source, downloads_folder(ctx), name, None, args.sha256, ctx.progress)
    else:
        path = os.path.abspath(source)
        if not os.path.exists(path):
            raise Error("not_found", f"{source} doesn't exist")
        if args.sha256 and os.path.isfile(path) and _sha256(path) != args.sha256.lower():
            raise Error("checksum", f"{source}: its SHA-256 isn't {args.sha256}")
    if not args.textures:
        ns = argparse.Namespace(path=path, name=args.name, replace=args.replace, disabled=args.disabled)
        result = cmd_add(ctx, ns)
    else:
        name = args.name or _stem(os.path.basename(path.rstrip("/\\")))
        pid = mod_id(name)
        staging = _staging(ctx, True)
        try:
            if os.path.isdir(path):
                notes = []
                for rel, src in _files_under(path, "", notes):
                    dest = _abs(staging, rel)
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    shutil.copy2(src, dest)
            else:
                kind = archive_kind(path)
                if kind in ("rar", "unknown"):
                    raise Error("manual_extract", f"{os.path.basename(path)}: {_MANUAL[kind]}", download=path,
                                target=textures_folder(ctx.user_dir))
                notes = extract_archive(path, staging, kind=kind, progress=ctx.progress, duplicates="last")
            record = read_textures(ctx)
            if pid in record["packs"] and not args.replace:
                raise Error("exists", f"a texture pack {pid!r} is installed already: install with --replace to update "
                                      "it, or with --name to keep both", pack=pid)
            result = install_textures(ctx, pid, _texture_root(staging), {"name": name}, force=args.force)
            result["warnings"] = notes
            result["messages"] = [f"{name} installed in {result['textures_folder']}; turn on custom textures to see it"]
        finally:
            if os.path.isdir(staging):
                _rmtree(staging)
    if _is_url(source) and not args.keep_download and os.path.isfile(path):
        _remove_file(path)
    return result


# ── text output ─────────────────────────────────────────────────────────────

def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _describe(m: dict) -> str:
    what = []
    for kind, label in (("romfs", "game files"), ("romfs_ext", "file patches"), ("exefs", "ExeFS files"),
                        ("exheader", "exheader")):
        if kind in m["contains"]:
            what.append(label)
    if m["code"]:
        what.append("code patch (adds code after the game's own)" if m["code"].get("adds_code") else
                    {True: "code patch (changes the code)", False: "code patch (changes data only)",
                     None: "code"}[m["code"]["changes_code"]])
    text = (", ".join(what) or "nothing") + f", {_plural(m['files'], 'file')}"
    if m["wins"] or m["loses"]:
        text += f"; wins {m['wins']}, loses {m['loses']} of its conflicts"
    if m["new"]:
        text += "; new"
    if m["errors"]:
        text = "can't be used: " + "; ".join(m["errors"])
    return text


def print_text(command: str, r: dict) -> None:
    for line in r.get("messages", []):
        print(line)
    if command == "catalog":
        c = r["catalog"]
        print(f"catalog: {c.get('url') or c.get('path')} ({c['source']}, {c['updated'] or 'undated'})")
        for e in r["entries"]:
            state = "installed" if e["installed"] else "not installed"
            if e["update"]:
                state = f"installed ({e['installed']['version']}; {e['version']} in the catalog)"
            badge = " [recommended]" if e["recommended"] else ""
            print(f"  {e['id']}: {e['name']}{' ' + e['version'] if e['version'] else ''}{badge}, by "
                  f"{e['author'] or '?'}; {e['category'] or e['kind']}, {_mb(e['size']) if e['size'] else 'size ?'}"
                  f"; {state}")
            if e["region_note"]:
                print(f"      {e['region_note']}")
            print(f"      {e['page_url']}" + ("" if e["direct"] else " (installed from its page)"))
        print(f"install one with  mods.py install-from-catalog ID; texture packs go in {r['textures_folder']}")
    if "mods" in r and command not in ("install", "install-from-catalog") or command in ("list", "add", "remove"):
        print(f"mod library: {r['library']}")
        print(f"Azahar's mods folder: {r['mods_folder']} ({r['deploy_state'].replace('_', ' ')}"
              + ("; run deploy)" if r["deploy_state"] == "out_of_date" else ")"))
        for m in r["mods"]:
            label = m["name"] + (f" {m['version']}" if m["version"] else "") + ("" if m["name"] == m["id"] else
                                                                                  f" [{m['id']}]")
            print(f"{m['position']:>3}  {'on ' if m['enabled'] else 'off'}  {label}: {_describe(m)}")
            for w in m["warnings"]:
                print(f"       warning: {w}")
        if not r["mods"]:
            print("no mods yet: add one with  mods.py add PATH|ZIP")
        elif r["conflict_count"]:
            print(f"{_plural(r['conflict_count'], 'file')} changed by more than one enabled mod: the later mod "
                  "wins (mods.py conflicts)")
        for p in r.get("problems", []):
            print(f"problem: {p}")
    if command == "conflicts":
        for c in r["conflicts"]:
            print(f"{c['path']}: {', '.join(c['mods'])}; {c['winner']} wins")
        if not r["conflicts"]:
            print("no file is changed by more than one " + ("mod" if r["all"] else "enabled mod"))
    if "deploy" in r:
        d = r["deploy"]
        state = d["state"].replace("_", " ")
        if r.get("dry_run"):
            print(f"Azahar's mods folder: {d['folder']} ({state})")
            for key in ("add", "update", "remove"):
                for rel in d[key]:
                    print(f"  would {key} {rel}")
        elif command in ("deploy", "undeploy"):
            if r.get("changed"):
                print(f"{d['folder']}: {len(r['added'])} added, {len(r['updated'])} updated, {len(r['removed'])} "
                      f"removed ({r['linked']} hard links, {r['copied']} copies)")
                for rel in r["replaced"]:
                    print(f"  replaced (not from the mod library): {rel}")
            else:
                print(f"{d['folder']}: already up to date")
        else:
            print(f"Azahar's mods folder: {d['folder']} ({state})")
            for key, label in (("add", "to add"), ("update", "to update"), ("remove", "to remove")):
                if d[key]:
                    print(f"  {_plural(len(d[key]), 'file')} {label}")
        for rel in d["in_the_way"]:
            print(f"  in the way (not from the mod library): {rel}")
        for rel in d["changed"]:
            print(f"  changed since it was deployed, left alone: {rel}")
        others = [rel for rel in d["foreign"] if rel not in d["in_the_way"] and rel not in d["changed"]]
        if others:
            print(f"  {_plural(len(others), 'other file')} there, not from the mod library: {', '.join(others[:5])}"
                  + (" ..." if len(others) > 5 else ""))
        for p in d["problems"]:
            print(f"problem: {p}")
    if "code_library" in r:
        c = r["code_library"]
        print(f"code library: {c['state']}: {c['reason']}")
        for w in c.get("warnings", []):
            print(f"  warning: {w}")
    for w in r.get("warnings", []):
        print(f"warning: {w}")


# ── command line ────────────────────────────────────────────────────────────

COMMANDS = {"list": cmd_list, "add": cmd_add, "remove": cmd_remove, "enable": cmd_enable, "disable": cmd_disable,
            "order": cmd_order, "conflicts": cmd_conflicts, "deploy": cmd_deploy, "undeploy": cmd_deploy,
            "status": cmd_status, "catalog": cmd_catalog, "install-from-catalog": cmd_install_catalog,
            "install": cmd_install}
LONG = ("install", "install-from-catalog", "add")   # with progress lines


def build_parser(json_errors: bool) -> argparse.ArgumentParser:
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            if json_errors:
                raise Error("usage", message)
            super().error(message)

    def common(p: argparse.ArgumentParser, default) -> None:
        p.add_argument("--json", action="store_true", default=default if default is not None else False,
                       help="print one JSON object (for the port's launcher)")
        p.add_argument("--user-dir", default=default, help=f"Azahar's user folder (default: {default_user_dir()})")
        p.add_argument("--library", default=default, help=f"the mod library (default: <user dir>/{LIBRARY})")
        p.add_argument("--code-library", default=default, help=argparse.SUPPRESS)

    ap = Parser(prog="mods.py", description=__doc__.splitlines()[0])
    common(ap, None)
    sub = ap.add_subparsers(dest="command", metavar="COMMAND", parser_class=Parser)
    sub.required = True
    s = argparse.SUPPRESS
    p = sub.add_parser("list", help="the mods, on or off, in order")
    common(p, s)
    p = sub.add_parser("add", help="a mod's folder or .zip into the library (on, last in the order)")
    p.add_argument("path")
    p.add_argument("--name", help="its name in the library (default: mod.json's name, or the folder's or zip's)")
    p.add_argument("--replace", action="store_true", help="update the mod of that name, keeping its place")
    p.add_argument("--disabled", action="store_true", help="add it turned off")
    common(p, s)
    for name, text in (("remove", "delete a mod from the library"), ("enable", "turn a mod on"),
                       ("disable", "turn a mod off")):
        p = sub.add_parser(name, help=text)
        p.add_argument("name")
        common(p, s)
    p = sub.add_parser("order", help="move a mod to a place in the order (1 = first; later mods win)")
    p.add_argument("name")
    p.add_argument("position", help="1 to the number of mods, first or last")
    common(p, s)
    p = sub.add_parser("conflicts", help="files that more than one enabled mod changes, and which wins")
    p.add_argument("--all", action="store_true", help="among all the mods, on or off")
    common(p, s)
    p = sub.add_parser("deploy", help="put the enabled mods in Azahar's mods folder")
    p.add_argument("--force", action="store_true", help="replace files there that the mod manager didn't put there")
    p.add_argument("--copy", action="store_true", help="copy files instead of hard-linking them")
    p.add_argument("--dry-run", action="store_true", help="only say what would change")
    common(p, s)
    p = sub.add_parser("undeploy", help="take the mod manager's files out of Azahar's mods folder")
    p.add_argument("--dry-run", action="store_true", help="only say what would change")
    common(p, s)
    p = sub.add_parser("status", help="whether Azahar's mods folder and the code library are up to date")
    common(p, s)

    def catalog_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--catalog", metavar="FILE", default=None, help="this catalog file instead of the tools' own "
                                                                       "and the one from the web")
        p.add_argument("--catalog-url", metavar="URL", default=CATALOG_URL,
                       help=f"where a newer catalog is fetched from with --refresh ('' for nowhere; default: "
                            f"{CATALOG_URL})")

    p = sub.add_parser("catalog", help="the mods and texture packs recommended for the port, and which are installed")
    p.add_argument("--refresh", action="store_true", help="fetch the newest catalog from the web first")
    catalog_args(p)
    common(p, s)
    p = sub.add_parser("install-from-catalog", help="download and install a mod or texture pack of the catalog")
    p.add_argument("id", help="its id in the catalog (mods.py catalog lists them)")
    p.add_argument("--force", action="store_true", help="also when it conflicts with something installed, or files "
                                                        "of its are there already: replace them")
    p.add_argument("--keep-download", action="store_true", help="keep the downloaded archive (in the library's "
                                                                ".downloads folder)")
    catalog_args(p)
    common(p, s)
    p = sub.add_parser("install", help="a mod or texture pack from a URL, an archive (.zip, .7z, .tar) or a folder")
    p.add_argument("source", help="an http(s) URL, an archive or a folder")
    p.add_argument("--textures", action="store_true", help="a texture pack: into Azahar's custom texture folder for "
                                                           "the game (load/textures/0004000000126300/)")
    p.add_argument("--name", help="its name (default: mod.json's name, or the archive's or folder's)")
    p.add_argument("--file", help="the downloaded file's name (default: from the URL)")
    p.add_argument("--sha256", help="refuse it unless its SHA-256 is this")
    p.add_argument("--replace", action="store_true", help="update the mod or texture pack of that name")
    p.add_argument("--disabled", action="store_true", help="add a mod turned off")
    p.add_argument("--force", action="store_true", help="a texture pack: replace files there that it didn't put there")
    p.add_argument("--keep-download", action="store_true", help="keep the downloaded file")
    common(p, s)
    return ap


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    as_json = "--json" in args
    command = next((a for a in args if a in COMMANDS), None)
    if not as_json and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(errors="replace")    # a mod's name the console can't show
        except (ValueError, OSError):
            pass
    try:
        ns = build_parser(as_json).parse_args(args)
        command = ns.command
        if command == "undeploy":
            ns.undeploy = True
        user_dir = os.path.abspath(ns.user_dir or default_user_dir())
        library = Library(os.path.abspath(ns.library or os.path.join(user_dir, LIBRARY)))
        ctx = Context(user_dir, library, mods_folder(user_dir),
                      os.path.abspath(ns.code_library) if ns.code_library else None,
                      Progress(as_json) if command in LONG else None)
        if _within(ctx.folder, library.path) or _within(library.path, ctx.folder):
            raise Error("bad_path", "the mod library and Azahar's mods folder must be separate folders")
        try:
            result = COMMANDS[command](ctx, ns)
        except OSError as e:
            raise Error("io", str(e)) from e
        finally:
            if ctx.progress:
                ctx.progress.end_line()
    except Error as e:
        if as_json:
            print(json.dumps({"ok": False, "command": command, "error": dict(e.details, code=e.code,
                                                                              message=e.message)}, indent=1))
        else:
            print(f"mods.py: {e.message}", file=sys.stderr)
        return 2 if e.code == "usage" else 1
    if as_json:
        print(json.dumps(dict(result, ok=True, command=command), indent=1))
    else:
        print_text(command, result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
