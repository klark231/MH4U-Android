"""tools/mods.py: the mod library, layouts, zips, order, conflicts, deploy and status, on
synthetic mods (a few bytes each; never game data)."""
import contextlib
import io
import json
import os
import sys
import zipfile
import zlib

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import mods  # noqa: E402

FOLDER = ("load", "mods", "0004000000126300")


def write(path, data=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    return path


def make(root, files):
    """A folder with these files ({relative path: bytes or text})."""
    for rel, data in files.items():
        write(root / rel, data)
    return root


def make_zip(path, entries):
    """A zip of {name: bytes}; a name may be anything, even unsafe."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return path


def ips(*records):
    """(offset, data) records or (offset, count, byte) runs."""
    out = bytearray(b"PATCH")
    for r in records:
        out += r[0].to_bytes(3, "big")
        out += len(r[1]).to_bytes(2, "big") + r[1] if len(r) == 2 else b"\0\0" + r[1].to_bytes(2, "big") + bytes([r[2]])
    return bytes(out + b"EOF")


def number(n):
    out = bytearray()
    while True:
        x, n = n & 0x7F, n >> 7
        if n == 0:
            return bytes(out + bytes([0x80 | x]))
        out.append(x)
        n -= 1


def bps(source, target, commands):
    """commands: ("sr", n) | ("tr", bytes) | ("sc", n, delta) | ("tc", n, delta)."""
    body = bytearray(b"BPS1" + number(len(source)) + number(len(target)) + number(0))
    for c in commands:
        if c[0] == "sr":
            body += number((c[1] - 1) << 2)
        elif c[0] == "tr":
            body += number(((len(c[1]) - 1) << 2) | 1) + c[1]
        else:
            body += number(((c[1] - 1) << 2) | (2 if c[0] == "sc" else 3)) + number(abs(c[2]) << 1 | (c[2] < 0))
    body += zlib.crc32(source).to_bytes(4, "little") + zlib.crc32(target).to_bytes(4, "little")
    return bytes(body + zlib.crc32(bytes(body)).to_bytes(4, "little"))


def run(user, *args, code=0):
    """mods.py --json with Azahar's user folder `user`: its output, parsed."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mods.main(["--json", "--user-dir", str(user), *args])
    out = json.loads(buf.getvalue())
    assert rc == code, out
    assert out["ok"] is (code == 0)
    return out


def folder(user):
    return user.joinpath(*FOLDER)


def deployed(user):
    """The files in Azahar's mods folder, but the record, as {path: bytes}."""
    top = folder(user)
    return {p.relative_to(top).as_posix(): p.read_bytes() for p in sorted(top.rglob("*"))
            if p.is_file() and p.name != mods.DEPLOYED}


def names(out):
    return [(m["id"], m["enabled"]) for m in out["mods"]]


@pytest.fixture
def user(tmp_path):
    u = tmp_path / "azahar"
    u.mkdir()
    return u


def case_sensitive(path):
    write(path / "case" / "a")
    sensitive = not (path / "case" / "A").exists()
    return sensitive


# ── names and paths ─────────────────────────────────────────────────────────

def test_relative_parts_windows_style():
    assert mods.relative_parts("romfs\\data\\x.arc") == ["romfs", "data", "x.arc"]
    assert mods.relative_parts("./romfs//data/./x.arc") == ["romfs", "data", "x.arc"]
    for bad in ("/etc/passwd", "\\evil", "C:\\evil", "c:evil", "C:/x", "\\\\server\\share\\x", "a/../../b",
                "romfs\\..\\..\\b", "..", "x\0y"):
        with pytest.raises(mods.Error) as e:
            mods.relative_parts(bad)
        assert e.value.code == "bad_path", bad
    for bad in ("romfs/CON", "romfs/aux.arc", "romfs/Lpt1.txt", "x/a:b", "x?.arc", "data./x", "data /x", "a|b"):
        with pytest.raises(mods.Error) as e:
            mods.relative_parts(bad)
        assert e.value.code == "bad_name", bad


def test_mod_id():
    assert mods.mod_id('Steak: "swap"?') == "Steak_ _swap__"
    assert mods.mod_id(". .hidden. ") == "hidden"
    assert mods.mod_id("CON") == "_CON" and mods.mod_id("") == "mod" and mods.mod_id("a/b\\c") == "a_b_c"


def test_default_user_dir_matches_build_game():
    import build_game
    assert mods.default_user_dir() == build_game.default_user_dir()
    assert os.path.basename(mods.code_library("u")) == build_game.library_name(mods.PROGRAM_ID)


# ── layouts ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("files,layout,dests", [
    ({"romfs/data/x.arc": "1"}, "azahar", ["romfs/data/x.arc"]),
    ({"My Mod v1/readme.txt": "r", "My Mod v1/romfs/data/x.arc": "1"}, "azahar", ["romfs/data/x.arc"]),
    ({"0004000000126300/romfs/data/x.arc": "1"}, "title", ["romfs/data/x.arc"]),
    ({"0004000E00126300/code.ips": ips((0x10, b"A"))}, "title", ["exefs/code.ips"]),
    ({"luma/titles/0004000000126300/romfs/data/x.arc": "1", "luma/titles/0004000000126100/romfs/a": "2"},
     "title", ["romfs/data/x.arc"]),
    ({"data/x.arc": "1", "eng/data/y.arc": "2", "readme.txt": "r", "mod.json": "{}"}, "romfs",
     ["romfs/data/x.arc", "romfs/eng/data/y.arc"]),
    ({"Steak swap/How to.txt": "r", "Steak swap/eng/data/core_common.arc": "1"}, "romfs",
     ["romfs/eng/data/core_common.arc"]),
    ({"RomFS/Data/x.arc": "1", "__MACOSX/RomFS/._x.arc": "junk", ".DS_Store": "junk"}, "azahar",
     ["romfs/data/x.arc"]),
    ({"ENG/Data/x.arc": "1", "Data/Sub/y.arc": "2"}, "romfs", ["romfs/data/Sub/y.arc", "romfs/eng/data/x.arc"]),
])
def test_layouts(tmp_path, files, layout, dests):
    mod = mods.scan(str(make(tmp_path / "m", files)), "m")
    assert not mod.errors, mod.errors
    assert mod.layout == layout
    assert sorted(f.dest for f in mod.files) == dests


def test_layouts_refused(tmp_path):
    mod = mods.scan(str(make(tmp_path / "loose", {"core_common.arc": "1", "readme.txt": "r"})), "loose")
    assert mod.errors and "can't tell where" in mod.errors[0] and "core_common.arc" in mod.errors[0]
    mod = mods.scan(str(make(tmp_path / "eur", {"luma/titles/0004000000126100/romfs/data/x.arc": "1"})), "eur")
    assert mod.errors and "another game or region (title 0004000000126100)" in mod.errors[0]
    mod = mods.scan(str(make(tmp_path / "empty", {"exefs/readme.md": "r", "romfs/.DS_Store": "x"})), "empty")
    assert mod.errors == ["no files that Azahar uses (romfs/, romfs_ext/, exefs/, code.ips, ...)"]


def test_what_azahar_reads_from_a_mod(tmp_path):
    root = make(tmp_path / "m", {
        "exefs/code.ips": ips((0x10, b"A")), "code.bps": "b", "code.ips": "c",     # exefs/code.ips applies
        "banner.bnr": "top", "exefs/banner.bnr": "exefs", "icon.bin": "i",          # exefs/ first
        "exheader.bin": "e", "readme.txt": "r", "screens/1.png": "p", "exefs/notes.txt": "n",
        "romfs_ext/top.arc.stub": "", "romfs_ext/Patch.arc.IPS": ips((0, b"P")),
        "romfs_ext/data/deep.arc.ips": ips((0, b"D")), "romfs_ext/odd.txt": "?",
    })
    mod = mods.scan(str(root), "m")
    assert not mod.errors
    files = {f.dest: (f.target, os.path.relpath(f.src, root).replace(os.sep, "/")) for f in mod.files}
    assert files == {
        "exefs/code.ips": ("code", "exefs/code.ips"),
        "exefs/banner.bnr": ("exefs:banner.bnr", "exefs/banner.bnr"),
        "exefs/icon.bin": ("exefs:icon.bin", "icon.bin"),
        "exheader.bin": ("exheader", "exheader.bin"),
        "romfs_ext/top.arc.stub": ("romfs:top.arc", "romfs_ext/top.arc.stub"),
        "romfs_ext/Patch.arc.ips": ("romfs:patch.arc", "romfs_ext/Patch.arc.IPS"),
        # the port's Azahar reads romfs_ext/'s sub-folders too
        "romfs_ext/data/deep.arc.ips": ("romfs:data/deep.arc", "romfs_ext/data/deep.arc.ips"),
    }
    text = "\n".join(mod.warnings)
    assert "code.bps: not used, Azahar applies only exefs/code.ips" in text
    assert "code.ips: not used" in text and "banner.bnr: not used, Azahar takes exefs/banner.bnr first" in text
    assert "romfs_ext/odd.txt: not a .stub" in text and "deep.arc" not in text
    assert "exheader.bin replaces the game's exheader" in text
    assert set(mod.ignored) == {"readme.txt", "screens/", "exefs/notes.txt"}
    assert mod.contains == ["code", "exefs", "exheader", "romfs_ext"]


def test_manifest(tmp_path):
    root = make(tmp_path / "m", {"files/romfs/data/x.arc": "1", "mod.json": json.dumps({
        "name": "Steak Swap", "version": 1.2, "author": "someone", "description": "d", "url": "https://example.org",
        "contains": ["romfs", "code"], "root": "files", "unknown": [1]})})
    mod = mods.scan(str(root), "m")
    assert mod.name == "Steak Swap" and mod.manifest["version"] == "1.2" and mod.manifest["url"] == "https://example.org"
    assert mod.files[0].dest == "romfs/data/x.arc"
    assert any("says it contains code, romfs, but its files are romfs" in w for w in mod.warnings)
    write(root / "mod.json", '{"name": "x", "root": "../outside"}')
    assert "goes outside the mod's folder" in mods.scan(str(root), "m").errors[0]
    write(root / "mod.json", "{not json")
    mod = mods.scan(str(root), "m")
    assert mod.name == "m" and not mod.errors and "mod.json can't be read" in mod.warnings[0]
    # in a wrapper folder, next to the files
    mod = mods.scan(str(make(tmp_path / "w", {"Wrap/mod.json": '{"name": "Wrapped"}', "Wrap/romfs/a": "1"})), "w")
    assert mod.name == "Wrapped"


def test_names_that_are_one_file_on_windows(tmp_path):
    if not case_sensitive(tmp_path):
        pytest.skip("needs a case-sensitive file system")
    mod = mods.scan(str(make(tmp_path / "m", {"romfs/data/A.arc": "1", "romfs/data/a.arc": "2"})), "m")
    assert mod.errors and "the same file on Windows and macOS" in mod.errors[0]


# ── code patches ────────────────────────────────────────────────────────────

def test_patch_writes_and_changes_code(tmp_path):
    assert mods.patch_writes(ips((0x10, b"AB"), (0x400, 8, 0))) == [(0x10, 2), (0x400, 8)]
    source = bytes(range(32))
    target = source[:16] + b"NEW!" + source[20:]
    # SourceRead keeps 16 bytes in place, TargetRead writes 4 at 16, SourceRead the rest
    patch = bps(source, target, [("sr", 16), ("tr", b"NEW!"), ("sc", 12, 4)])
    assert mods.patch_writes(patch) == [(16, 4), (20, 12)]
    assert mods.patch_writes(b"garbage") is None
    data = write(tmp_path / "data.ips", ips((0x200, b"\5")))
    code = write(tmp_path / "code.ips", ips((0x200, b"\5"), (0x10, b"\1")))
    whole = write(tmp_path / "code.bin", b"\0" * 16)
    assert mods.changes_code([str(data)], 0x100) is False
    assert mods.changes_code([str(code)], 0x100) is True
    assert mods.changes_code([str(whole)], 0x100) is None and mods.changes_code([str(data)], None) is None
    assert mods.changes_code([], None) is False
    assert mods.text_size() == 0x00CEC734          # USA v1.3.0, config/versions.json


# ── the library ─────────────────────────────────────────────────────────────

def test_add_folder_and_zip(user, tmp_path):
    src = make(tmp_path / "src" / "Folder mod", {"romfs/data/a.arc": "a", "readme.txt": "r"})
    out = run(user, "add", str(src))
    assert out["mod"] == "Folder mod" and names(out) == [("Folder mod", True)]
    assert (src / "romfs" / "data" / "a.arc").read_bytes() == b"a"          # copied, not moved
    assert (user / "mh4u-mods" / "Folder mod" / "readme.txt").exists()
    z = make_zip(tmp_path / "Zipped v2.zip", {"Zipped/mod.json": '{"name": "Zipped mod", "version": "2"}',
                                               "Zipped/romfs/data/b.arc": "b"})
    out = run(user, "add", str(z))
    assert out["mod"] == "Zipped mod" and out["mods"][1]["version"] == "2"
    z2 = make_zip(tmp_path / "plain.zip", {"data/c.arc": "c"})
    out = run(user, "add", str(z2), "--disabled")
    assert names(out) == [("Folder mod", True), ("Zipped mod", True), ("plain", False)]
    # the same name again, whatever the capital letters
    err = run(user, "add", str(z2), "--name", "PLAIN", code=1)
    assert err["error"]["code"] == "exists" and err["error"]["mod"] == "plain"
    out = run(user, "add", str(z2), "--name", "plain 2")
    assert names(out)[-1] == ("plain 2", True)
    # --replace: a new version in the same place, on or off as before
    run(user, "order", "Zipped mod", "1")
    z3 = make_zip(tmp_path / "Zipped v3.zip", {"mod.json": '{"name": "zipped MOD", "version": "3"}',
                                               "romfs/data/b.arc": "B3"})
    out = run(user, "add", str(z3), "--replace")
    assert names(out)[0] == ("Zipped mod", True) and out["mods"][0]["version"] == "3"
    assert (user / "mh4u-mods" / "Zipped mod" / "romfs" / "data" / "b.arc").read_bytes() == b"B3"
    assert not [p for p in (user / "mh4u-mods").iterdir() if p.name.startswith(".")]
    # a folder put in the library by hand: listed last, new and off; add turns it on
    make(user / "mh4u-mods" / "By hand", {"romfs/data/d.arc": "d"})
    out = run(user, "list")
    assert out["mods"][-1]["id"] == "By hand" and out["mods"][-1]["new"] and not out["mods"][-1]["enabled"]
    out = run(user, "add", str(user / "mh4u-mods" / "By hand"))
    assert names(out)[-1] == ("By hand", True)


def test_add_refuses(user, tmp_path):
    loose = make(tmp_path / "loose", {"x.arc": "1"})
    err = run(user, "add", str(loose), code=1)
    assert err["error"]["code"] == "layout"
    assert os.listdir(user / "mh4u-mods") == []                           # nothing left behind
    assert run(user, "add", str(tmp_path / "missing"), code=1)["error"]["code"] == "not_found"
    write(tmp_path / "mod.7z", b"7z\xbc\xaf")
    assert run(user, "add", str(tmp_path / "mod.7z"), code=1)["error"]["code"] == "unsupported"
    write(tmp_path / "broken.zip", b"PK not really")
    assert run(user, "add", str(tmp_path / "broken.zip"), code=1)["error"]["code"] == "zip"
    assert run(user, "add", str(user), code=1)["error"]["code"] == "bad_path"   # holds the library
    err = run(user, "--library", str(folder(user) / "lib"), "list", code=1)
    assert err["error"]["code"] == "bad_path"


@pytest.mark.parametrize("entry,code", [
    ("../evil.arc", "bad_path"), ("/tmp/evil.arc", "bad_path"), ("C:\\evil.arc", "bad_path"),
    ("romfs\\..\\..\\evil.arc", "bad_path"), ("\\\\server\\share\\evil.arc", "bad_path"),
    ("romfs/data/aux.arc", "bad_name"), ("romfs/data/a:b.arc", "bad_name"),
])
def test_zip_paths_refused(user, tmp_path, entry, code):
    z = make_zip(tmp_path / "bad.zip", {"romfs/data/ok.arc": "ok", entry: "evil"})
    assert run(user, "add", str(z), code=1)["error"]["code"] == code
    assert os.listdir(user / "mh4u-mods") == []
    assert sorted(p.name for p in tmp_path.rglob("*evil*")) == []


def test_zip_with_windows_separators(user, tmp_path):
    z = make_zip(tmp_path / "win.zip", {"Win mod\\readme.txt": "r", "Win mod\\RomFS\\data\\x.arc": "x",
                                        "Win mod\\exefs\\Code.IPS": ips((0x10, b"A"))})
    out = run(user, "add", str(z))
    assert out["mods"][0]["id"] == "win" and out["mods"][0]["contains"] == ["code", "romfs"]
    run(user, "deploy")
    assert deployed(user) == {"romfs/data/x.arc": b"x", "exefs/code.ips": ips((0x10, b"A"))}
    record = json.loads((folder(user) / mods.DEPLOYED).read_text())
    assert record["files"]["romfs/data/x.arc"]["source"] == "win/Win mod/RomFS/data/x.arc"


def test_zip_symlink_skipped(user, tmp_path):
    z = tmp_path / "link.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("romfs/data/ok.arc", "ok")
        info = zipfile.ZipInfo("romfs/data/link.arc")
        info.create_system = 3
        info.external_attr = 0o120777 << 16          # a symbolic link to /etc/passwd
        zf.writestr(info, "/etc/passwd")
    out = run(user, "add", str(z))
    unpacked = user / "mh4u-mods" / "link"
    assert sorted(p.name for p in unpacked.rglob("*")) == ["data", "ok.arc", "romfs"]
    assert any("romfs/data/link.arc: a symbolic link, not unpacked" in w for w in out["mods"][0]["warnings"])


def test_zip_limits(user, tmp_path, monkeypatch):
    z = make_zip(tmp_path / "bomb.zip", {"romfs/data/zeros.arc": bytes(5000), "romfs/data/b.arc": bytes(5000)})
    monkeypatch.setattr(mods, "ZIP_MAX_FILE", 4000)
    assert "more than 4000" in run(user, "add", str(z), code=1)["error"]["message"]
    monkeypatch.setattr(mods, "ZIP_MAX_FILE", 1 << 20)
    monkeypatch.setattr(mods, "ZIP_MAX_TOTAL", 8000)
    assert "unpacks to more than 8000" in run(user, "add", str(z), code=1)["error"]["message"]
    monkeypatch.setattr(mods, "ZIP_MAX_TOTAL", 1 << 20)
    monkeypatch.setattr(mods, "ZIP_MAX_ENTRIES", 1)
    assert "more than 1" in run(user, "add", str(z), code=1)["error"]["message"]
    monkeypatch.setattr(mods, "ZIP_MAX_ENTRIES", 100)
    # a zip whose headers claim less than it unpacks to
    data = bytearray(z.read_bytes())
    for sig, at in ((b"PK\x03\x04", 22), (b"PK\x01\x02", 24)):
        pos = data.find(sig)
        data[pos + at:pos + at + 4] = (10).to_bytes(4, "little")
    write(tmp_path / "liar.zip", bytes(data))
    assert run(user, "add", str(tmp_path / "liar.zip"), code=1)["error"]["code"] == "zip"
    assert os.listdir(user / "mh4u-mods") == []


def test_zip_names_that_clash(user, tmp_path):
    z = make_zip(tmp_path / "case.zip", {"romfs/data/X.arc": "1", "romfs/data/x.arc": "2"})
    assert "same file on Windows and macOS" in run(user, "add", str(z), code=1)["error"]["message"]
    z = make_zip(tmp_path / "clash.zip", {"romfs/data": "1", "romfs/data/x.arc": "2"})
    assert "both a file and a folder" in run(user, "add", str(z), code=1)["error"]["message"]


def test_folder_symlinks_skipped(user, tmp_path):
    src = make(tmp_path / "linked", {"romfs/data/a.arc": "a"})
    try:
        os.symlink(tmp_path / "secret", src / "romfs" / "data" / "b.arc")
    except (OSError, NotImplementedError):
        pytest.skip("no symbolic links here")
    out = run(user, "add", str(src))
    assert sorted(p.name for p in (user / "mh4u-mods" / "linked").rglob("*")) == ["a.arc", "data", "romfs"]
    assert any("a symbolic link, not used" in w for w in out["mods"][0]["warnings"])


def test_order_enable_disable_remove(user, tmp_path):
    for name in ("One", "Two", "Three"):
        run(user, "add", str(make(tmp_path / name, {f"romfs/data/{name}.arc": name})))
    out = run(user, "order", "three", "first")
    assert [m["id"] for m in out["mods"]] == ["Three", "One", "Two"]
    out = run(user, "order", "One", "3")
    assert [m["id"] for m in out["mods"]] == ["Three", "Two", "One"] and out["messages"] == ["One is now 3 of 3"]
    assert run(user, "order", "One", "4", code=2)["error"]["code"] == "usage"
    assert run(user, "order", "One", "soon", code=2)["error"]["code"] == "usage"
    out = run(user, "disable", "TWO")
    assert names(out) == [("Three", True), ("Two", False), ("One", True)]
    assert run(user, "enable", "nope", code=1)["error"]["code"] == "not_found"
    out = run(user, "remove", "Two")
    assert names(out) == [("Three", True), ("One", True)] and not (user / "mh4u-mods" / "Two").exists()
    state = json.loads((user / "mh4u-mods" / "library.json").read_text())
    assert state["mods"] == [{"id": "Three", "enabled": True}, {"id": "One", "enabled": True}]
    # by the name in mod.json too, unless two mods share it
    write(user / "mh4u-mods" / "One" / "mod.json", '{"name": "Shared"}')
    out = run(user, "disable", "shared")
    assert names(out) == [("Three", True), ("One", False)]
    write(user / "mh4u-mods" / "Three" / "mod.json", '{"name": "Shared"}')
    err = run(user, "enable", "Shared", code=1)
    assert err["error"]["code"] == "ambiguous" and err["error"]["mods"] == ["Three", "One"]
    # a folder deleted by hand is dropped, with a warning
    mods._rmtree(str(user / "mh4u-mods" / "Three"))
    out = run(user, "list")
    assert names(out) == [("One", False)] and "Three: in library.json but its folder is gone" in out["warnings"]


def test_damaged_state(user, tmp_path):
    write(user / "mh4u-mods" / "library.json", "{oops")
    assert run(user, "list", code=1)["error"]["code"] == "state"


def test_code_mod_rule(user, tmp_path):
    run(user, "add", str(make(tmp_path / "Code A", {"code.ips": ips((0x10, b"A"))})))
    out = run(user, "add", str(make(tmp_path / "Code B", {"exefs/code.bps": "B"})))
    assert names(out) == [("Code A", True), ("Code B", False)]
    assert any(m.startswith("Code B stays off: Code B changes the game's code") for m in out["messages"])
    err = run(user, "enable", "Code B", code=1)
    assert err["error"]["code"] == "code_mod_conflict" and err["error"]["mods"] == ["Code A"]
    assert "turn Code A off first" in err["error"]["message"]
    run(user, "disable", "Code A")
    assert names(run(user, "enable", "Code B")) == [("Code A", False), ("Code B", True)]
    # two code mods on (library.json edited by hand): deploy refuses
    write(user / "mh4u-mods" / "library.json", json.dumps({"mods": [{"id": "Code A", "enabled": True},
                                                                     {"id": "Code B", "enabled": True}]}))
    assert run(user, "status")["deploy"]["state"] == "blocked"
    assert run(user, "deploy", code=1)["error"]["code"] == "code_mod_conflict"
    assert not folder(user).exists()


def test_conflicts_and_order(user, tmp_path):
    a = make(tmp_path / "A", {"romfs/data/x.arc": "A", "romfs/top.arc": "A", "romfs/data/only_a.arc": "A",
                              "exefs/banner.bnr": "A"})
    b = make(tmp_path / "B", {"romfs/data/X.arc": "B", "romfs_ext/top.arc.stub": "", "banner.bnr": "B"})
    run(user, "add", str(a))
    run(user, "add", str(b))
    out = run(user, "conflicts")
    assert [(c["path"], c["mods"], c["winner"]) for c in out["conflicts"]] == [
        ("data/X.arc", ["A", "B"], "B"), ("exefs/banner.bnr", ["A", "B"], "B"), ("top.arc", ["A", "B"], "B")]
    assert out["conflicts"][0]["files"] == {"A": ["romfs/data/x.arc"], "B": ["romfs/data/X.arc"]}
    run(user, "deploy")
    # one file per game file, the later mod's, as spelled by it (the same on every system)
    assert deployed(user) == {"romfs/data/X.arc": b"B", "romfs/data/only_a.arc": b"A", "romfs_ext/top.arc.stub": b"",
                              "exefs/banner.bnr": b"B"}
    run(user, "order", "B", "1")
    assert [c["winner"] for c in run(user, "conflicts")["conflicts"]] == ["A", "A", "A"]
    assert run(user, "status")["deploy"]["state"] == "out_of_date"
    run(user, "deploy")
    assert deployed(user) == {"romfs/data/x.arc": b"A", "romfs/data/only_a.arc": b"A", "romfs/top.arc": b"A",
                              "exefs/banner.bnr": b"A"}
    run(user, "disable", "A")
    assert run(user, "conflicts")["conflicts"] == []
    assert len(run(user, "conflicts", "--all")["conflicts"]) == 3


def test_deploy_links_records_and_is_idempotent(user, tmp_path, monkeypatch):
    run(user, "add", str(make(tmp_path / "A", {"romfs/data/a.arc": "aaa", "code.ips": ips((0x200000, b"\5"))})))
    out = run(user, "deploy")
    assert out["added"] == ["exefs/code.ips", "romfs/data/a.arc"] and out["changed"]
    src = user / "mh4u-mods" / "A" / "romfs" / "data" / "a.arc"
    dest = folder(user) / "romfs" / "data" / "a.arc"
    if out["linked"]:
        assert os.path.samefile(src, dest)
    record = json.loads((folder(user) / mods.DEPLOYED).read_text())
    assert set(record["files"]) == {"exefs/code.ips", "romfs/data/a.arc"}
    assert record["files"]["romfs/data/a.arc"]["source"] == "A/romfs/data/a.arc"
    before = (folder(user) / mods.DEPLOYED).stat().st_mtime_ns, dest.stat().st_mtime_ns
    out = run(user, "deploy")
    assert not out["changed"] and out["deploy"]["state"] == "up_to_date"
    assert ((folder(user) / mods.DEPLOYED).stat().st_mtime_ns, dest.stat().st_mtime_ns) == before
    # copies where links can't be made (another drive, FAT)
    run(user, "undeploy")

    def no_links(*a):
        raise OSError("no hard links here")
    monkeypatch.setattr(os, "link", no_links)
    out = run(user, "deploy")
    assert out["copied"] == 2 and out["linked"] == 0 and not os.path.samefile(src, dest)
    assert deployed(user)["romfs/data/a.arc"] == b"aaa"
    assert not run(user, "deploy")["changed"]
    # a changed file in the library is deployed again
    write(src, "AAAA")
    assert run(user, "status")["deploy"]["update"] == ["romfs/data/a.arc"]
    assert run(user, "deploy")["updated"] == ["romfs/data/a.arc"] and dest.read_bytes() == b"AAAA"


def test_deploy_keeps_files_it_did_not_put_there(user, tmp_path):
    make(folder(user), {"romfs/data/mine.arc": "mine", "cheats.txt": "c", "romfs/data/x.arc": "theirs"})
    run(user, "add", str(make(tmp_path / "A", {"romfs/data/x.arc": "A", "romfs/data/y.arc": "A",
                                               "code.ips": ips((0x10, b"A"))})))
    out = run(user, "status")
    assert out["deploy"]["state"] == "blocked" and out["deploy"]["in_the_way"] == ["romfs/data/x.arc"]
    err = run(user, "deploy", code=1)
    assert err["error"]["code"] == "foreign_files" and err["error"]["files"] == ["romfs/data/x.arc"]
    assert deployed(user) == {"cheats.txt": b"c", "romfs/data/mine.arc": b"mine", "romfs/data/x.arc": b"theirs"}
    out = run(user, "deploy", "--force")
    assert out["replaced"] == ["romfs/data/x.arc"]
    assert deployed(user) == {"cheats.txt": b"c", "romfs/data/mine.arc": b"mine", "romfs/data/x.arc": b"A",
                              "romfs/data/y.arc": b"A", "exefs/code.ips": ips((0x10, b"A"))}
    assert run(user, "status")["deploy"]["foreign"] == ["cheats.txt", "romfs/data/mine.arc"]
    out = run(user, "undeploy")
    assert sorted(out["removed"]) == ["exefs/code.ips", "romfs/data/x.arc", "romfs/data/y.arc"]
    assert deployed(user) == {"cheats.txt": b"c", "romfs/data/mine.arc": b"mine"}
    assert not (folder(user) / "exefs").exists() and not (folder(user) / mods.DEPLOYED).exists()
    assert not run(user, "undeploy")["changed"]


@pytest.mark.parametrize("theirs", ["romfs_ext/x.arc.stub", "exefs/code.bps", "code.bin", "romfs/data/X.ARC",
                                    "romfs"])
def test_deploy_competing_files(user, tmp_path, theirs):
    """A file not from the library that Azahar would use instead of, or with, the mod's."""
    run(user, "add", str(make(tmp_path / "A", {"romfs/x.arc": "A", "romfs/data/x.arc": "A",
                                               "code.ips": ips((0x10, b"A"))})))
    if theirs == "romfs/data/X.ARC" and not case_sensitive(tmp_path):
        pytest.skip("needs a case-sensitive file system")
    write(folder(user) / theirs, "theirs")
    assert run(user, "deploy", code=1)["error"]["files"] == [theirs]
    run(user, "deploy", "--force")
    assert theirs not in deployed(user) and "romfs/data/x.arc" in deployed(user)


def test_changed_files_are_left_alone(user, tmp_path):
    run(user, "add", str(make(tmp_path / "A", {"romfs/data/a.arc": "a", "romfs/data/b.arc": "b"})))
    run(user, "deploy")
    dest = folder(user) / "romfs" / "data" / "a.arc"
    write(folder(user) / "new.tmp", "edited")             # the player's own version, in place of ours
    os.replace(folder(user) / "new.tmp", dest)
    out = run(user, "status")
    assert out["deploy"]["changed"] == ["romfs/data/a.arc"] and out["deploy"]["state"] == "blocked"
    run(user, "disable", "A")
    out = run(user, "deploy")
    assert out["removed"] == ["romfs/data/b.arc"] and out["kept"] == ["romfs/data/a.arc"]
    assert deployed(user) == {"romfs/data/a.arc": b"edited"}
    assert run(user, "status")["deploy"]["foreign"] == ["romfs/data/a.arc"]


def test_library_file_edited_in_place(user, tmp_path):
    run(user, "add", str(make(tmp_path / "A", {"romfs/data/a.arc": "aaaa"})))
    out = run(user, "deploy")
    if not out["linked"]:
        pytest.skip("no hard links here")
    with open(user / "mh4u-mods" / "A" / "romfs" / "data" / "a.arc", "r+b") as f:
        f.write(b"bbbbbb")
    assert run(user, "status")["deploy"]["state"] == "up_to_date"     # the same file
    assert not run(user, "deploy")["changed"]
    assert deployed(user)["romfs/data/a.arc"] == b"bbbbbb"


def test_folder_spellings(user, tmp_path):
    run(user, "add", str(make(tmp_path / "A", {"romfs/data/sub/x.arc": "A"})))
    out = run(user, "add", str(make(tmp_path / "B", {"romfs/data/SUB/y.arc": "B"})))
    assert any("one folder spelled differently" in w for w in out["warnings"])
    run(user, "deploy")
    assert deployed(user) == {"romfs/data/SUB/x.arc": b"A", "romfs/data/SUB/y.arc": b"B"}
    run(user, "disable", "B")
    run(user, "deploy")
    assert deployed(user) == {"romfs/data/sub/x.arc": b"A"}
    assert os.listdir(folder(user) / "romfs" / "data") == ["sub"]
    # a folder spelled otherwise that holds the player's files is used as it is
    run(user, "undeploy")
    write(folder(user) / "romfs" / "Data" / "mine.arc", "mine")
    out = run(user, "deploy")
    assert out["added"] == ["romfs/Data/sub/x.arc"] and any("put in romfs/Data/" in w for w in out["warnings"])
    assert not run(user, "deploy")["changed"]


def test_file_in_one_mod_folder_in_another(user, tmp_path):
    run(user, "add", str(make(tmp_path / "A", {"romfs/data/sub": "a file"})))
    run(user, "add", str(make(tmp_path / "B", {"romfs/data/sub/x.arc": "B"})))
    out = run(user, "status")["deploy"]
    assert out["state"] == "blocked" and out["problems"] == [
        "romfs/data/sub is a file in A and a folder in another mod: turn one of them off"]
    assert run(user, "deploy", code=1)["error"]["code"] == "mod_problems"
    run(user, "disable", "A")
    run(user, "deploy")
    assert deployed(user) == {"romfs/data/sub/x.arc": b"B"}


def test_adding_azahar_mods_folder_as_a_mod(user, tmp_path):
    make(folder(user), {"romfs/data/old.arc": "old", "code.ips": ips((0x10, b"O"))})
    out = run(user, "add", str(folder(user)), "--name", "Old mods")
    assert out["mods"][0]["contains"] == ["code", "romfs"]
    assert run(user, "deploy", code=1)["error"]["code"] == "foreign_files"
    out = run(user, "deploy", "--force")
    assert deployed(user) == {"exefs/code.ips": ips((0x10, b"O")), "romfs/data/old.arc": b"old"}
    assert run(user, "add", str(folder(user)), code=1)["error"]["code"] == "bad_path"   # holds deployed files


def test_romfs_ext_in_folders_is_deployed(user, tmp_path):
    """The port's Azahar reads romfs_ext/'s sub-folders, where MH4U's archives are."""
    run(user, "add", str(make(tmp_path / "A", {"romfs_ext/data/x.arc.ips": ips((0, b"P")),
                                               "romfs_ext/root.arc.ips": ips((0, b"P"))})))
    run(user, "deploy")
    assert sorted(deployed(user)) == ["romfs_ext/data/x.arc.ips", "romfs_ext/root.arc.ips"]


def test_romfs_ext_in_folders_is_not_deployed_for_upstream_azahar(user, tmp_path, monkeypatch):
    monkeypatch.setattr(mods, "ROMFS_EXT_IN_FOLDERS", False)
    out = run(user, "add", str(make(tmp_path / "A", {"romfs_ext/data/x.arc.ips": ips((0, b"P")),
                                                     "romfs_ext/root.arc.ips": ips((0, b"P"))})))
    assert any("does nothing" in w for w in out["mods"][0]["warnings"])
    run(user, "deploy")
    assert list(deployed(user)) == ["romfs_ext/root.arc.ips"]
    err = run(user, "add", str(make(tmp_path / "B", {"romfs_ext/data/x.arc.stub": ""})), code=1)
    assert "no files that Azahar uses (romfs_ext/data/x.arc.stub: does nothing" in err["error"]["message"]


def test_no_user_folder(tmp_path):
    gone = tmp_path / "gone"
    assert run(gone, "undeploy")["changed"] is False          # nothing to take out
    assert run(gone, "deploy", code=1)["error"]["code"] == "not_found"
    assert not gone.exists()


# ── status and the code library ─────────────────────────────────────────────

def test_code_library_status(user, tmp_path):
    lib = user / "recomp" / os.path.basename(mods.code_library(str(user)))
    state = run(user, "status")["code_library"]
    assert state["state"] == "missing" and state["code_files"] == [] and state["changes_code"] is False
    write(lib, b"\x7fELF library")
    assert run(user, "status")["code_library"]["state"] == "unknown"
    # what tools/build_game.py records: built without code mods, from a code of 0x100 bytes
    mods.write_build_record(str(lib), [], 0x100, False)
    assert run(user, "status")["code_library"]["state"] == "ok"
    # a code mod that only patches data: no rebuild
    run(user, "add", str(make(tmp_path / "Data", {"code.ips": ips((0x180, b"\5"))})))
    run(user, "deploy")
    state = run(user, "status")["code_library"]
    assert state["state"] == "ok" and "only its data" in state["reason"] and state["changes_code"] is False
    assert state["code_files"] == ["load/mods/0004000000126300/exefs/code.ips"]
    # one that patches the code: build again
    run(user, "disable", "Data")
    run(user, "add", str(make(tmp_path / "Code", {"exefs/code.ips": ips((0x10, b"\1"))})))
    state = run(user, "deploy")["code_library"]
    assert state["state"] == "rebuild" and state["changes_code"] is True
    code_ips = folder(user) / "exefs" / "code.ips"
    mods.write_build_record(str(lib), [str(code_ips)], 0x100, True)
    assert run(user, "status")["code_library"]["state"] == "ok"
    # the same patch anywhere Azahar looks is the same code
    run(user, "undeploy")
    write(folder(user) / "code.ips", ips((0x10, b"\1")))
    assert run(user, "status")["code_library"]["state"] == "ok"
    # a library built again without a record
    write(lib, b"\x7fELF another library")
    assert "another build" in run(user, "status")["code_library"]["reason"]
    # Luma's patch: Azahar applies it, tools/build_game.py doesn't
    os.remove(folder(user) / "code.ips")
    write(user / "sdmc" / "luma" / "titles" / "0004000E00126300" / "code.ips", ips((0x10, b"\2")))
    state = run(user, "status")["code_library"]
    assert state["code_files"] == ["sdmc/luma/titles/0004000E00126300/code.ips"]
    assert "Luma's code patch" in state["warnings"][0]


def test_build_record(tmp_path):
    lib = write(tmp_path / "recomp" / "0004000000126300.so", b"lib")
    patch = write(tmp_path / "Code.IPS", ips((1, b"x")))
    path = mods.write_build_record(str(lib), [str(patch)], 0x1000, True)
    record = json.loads(open(path).read())
    assert path == str(tmp_path / "recomp" / "0004000000126300.build.json")
    assert record["code_mods"][0]["kind"] == "ips" and record["changes_code"] is True
    assert record["library_size"] == 3 and record["text_size"] == 0x1000
    assert record["fingerprint"] == mods.code_fingerprint([str(write(tmp_path / "elsewhere.ips", ips((1, b"x"))))])


# ── the command line ────────────────────────────────────────────────────────

def test_json_for_every_command(user, tmp_path):
    src = make(tmp_path / "A", {"romfs/data/a.arc": "a"})
    for args in (["list"], ["add", str(src)], ["disable", "A"], ["enable", "A"], ["order", "A", "1"],
                 ["conflicts"], ["deploy", "--dry-run"], ["deploy"], ["status"], ["undeploy", "--dry-run"],
                 ["undeploy"], ["remove", "A"]):
        out = run(user, *args)
        assert out["command"] == args[0], args
    out = run(user, "list", "--json")             # --json after the command too
    assert out["mods"] == [] and out["deploy_state"] == "up_to_date"
    err = run(user, "frobnicate", code=2)
    assert err["error"]["code"] == "usage" and err["command"] is None
    assert run(user, "order", "A", code=2)["error"]["code"] == "usage"
    assert run(user, "enable", "A", code=1)["error"] == {"code": "not_found", "message": "no mod 'A' in the library",
                                                         "name": "A"}


def test_text_output(user, tmp_path, capsys):
    def text(*args):
        assert mods.main(["--user-dir", str(user), *args]) == 0
        return capsys.readouterr().out

    write(tmp_path / "A" / "romfs" / "data" / "x.arc", "a")
    write(tmp_path / "B" / "mod.json", '{"name": "Bee", "version": "0.1"}')
    write(tmp_path / "B" / "romfs" / "data" / "x.arc", "b")
    text("add", str(tmp_path / "A"))
    out = text("add", str(tmp_path / "B"))
    assert "Bee is on" in out and "  2  on   Bee 0.1: game files, 1 file; wins 1, loses 0" in out
    assert "data/x.arc: A, Bee; Bee wins" in text("conflicts")
    assert "out of date; run deploy" in text("list")
    assert "1 added, 0 updated, 0 removed" in text("deploy")
    out = text("status")
    assert "(up to date)" in out and "code library: missing" in out
    assert mods.main(["--user-dir", str(user), "enable", "nothing"]) == 1
    assert "mods.py: no mod 'nothing' in the library" in capsys.readouterr().err


def test_romfs_ext_folders_without_the_ports_fix(tmp_path, monkeypatch):
    """Upstream Azahar reads only the top of romfs_ext/: a patch in a folder is left out."""
    monkeypatch.setattr(mods, "ROMFS_EXT_IN_FOLDERS", False)
    root = make(tmp_path / "m", {"romfs_ext/top.arc.stub": "", "romfs_ext/data/deep.arc.ips": ips((0, b"D"))})
    mod = mods.scan(str(root), "m")
    assert [f.dest for f in mod.files] == ["romfs_ext/top.arc.stub"]
    assert "romfs_ext/data/deep.arc.ips: does nothing" in "\n".join(mod.warnings)
