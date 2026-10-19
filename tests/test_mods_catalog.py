"""tools/mods.py's catalog and installs: downloading (resumed, checked), unpacking .zip, .7z
and .tar safely, texture packs into Azahar's texture folder and out again, mods into the
library, the catalog that comes with the tools and a newer one from the web. All with
synthetic files served from a temporary folder or a local HTTP server (never game data, never
the network)."""
import contextlib
import hashlib
import http.server
import io
import json
import os
import sys
import tarfile
import threading
import zipfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
sys.path.insert(0, HERE)
import mods  # noqa: E402
from sevenzip_writer import make_7z  # noqa: E402

TID = "0004000000126300"


def write(path, data=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    return path


def make_zip(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return path


def call(user, *args, code=0):
    """mods.py --json: (its result, its progress events)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mods.main(["--json", "--user-dir", str(user), *args])
    lines = buf.getvalue().splitlines(keepends=True)
    events = [json.loads(line[1:]) for line in lines if line.startswith("@")]
    out = json.loads("".join(line for line in lines if not line.startswith("@")))
    assert rc == code, out
    assert out["ok"] is (code == 0)
    return out, events


def run(user, *args, code=0):
    return call(user, *args, code=code)[0]


def textures(user):
    return user / "load" / "textures" / TID


def tree(top):
    return sorted(str(p.relative_to(top)).replace(os.sep, "/") for p in top.rglob("*") if p.is_file())


@pytest.fixture
def user(tmp_path):
    u = tmp_path / "azahar"
    u.mkdir()
    return u


def catalog(path, entries, updated="2026-01-01"):
    path.write_text(json.dumps({"format": 1, "updated": updated, "mods": entries}))
    return str(path)


def entry(cid, kind, downloads, **more):
    return dict({"id": cid, "kind": kind, "name": cid.replace("-", " ").title(), "version": "1",
                 "author": "someone", "description": "test", "page_url": f"https://example.org/{cid}",
                 "downloads": downloads}, **more)


def url(path):
    return path.as_uri()


class Server:
    """A local HTTP server for {path: bytes}, with Range requests (or without, to test that)."""

    def __init__(self, files, ranges=True):
        self.files, self.ranges, self.requests = files, ranges, []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                data = outer.files.get(self.path)
                rng = self.headers.get("Range")
                outer.requests.append((self.path, rng))
                if data is None:
                    self.send_error(404)
                    return
                start = 0
                if rng and outer.ranges:
                    start = int(rng.split("=")[1].split("-")[0])
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
                else:
                    self.send_response(200)
                body = data[start:]
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    servers = []

    def start(files, ranges=True):
        s = Server(files, ranges)
        servers.append(s)
        return s
    yield start
    for s in servers:
        s.close()


# ── the catalog that comes with the tools ──

def test_bundled_catalog_is_valid():
    with open(mods.CATALOG, encoding="utf-8") as f:
        raw = json.load(f)
    cat, warnings = mods.validate_catalog(raw, remote=True)    # as strict as one from the web
    assert warnings == []
    assert len(cat["mods"]) == len(raw["mods"]) >= 5
    ids = [e["id"] for e in cat["mods"]]
    assert len(set(ids)) == len(ids)
    recommended = [e for e in cat["mods"] if e["recommended"]]
    assert [e["kind"] for e in recommended] == ["textures"]           # the HD texture pack
    assert "custom_textures" in recommended[0]["needs"]
    for e in cat["mods"]:
        assert e["page_url"].startswith("https://") and e["author"] and e["description"] and e["license"]
        assert e["region"], e["id"]
        for c in e["conflicts"]:
            assert c in ids
        for d in e["downloads"]:
            assert d["url"].startswith("https://")


def test_catalog_lists_with_install_state(user, tmp_path):
    out = run(user, "catalog", "--catalog-url", "")
    assert out["catalog"]["source"] == "bundled"
    assert out["textures_folder"] == str(textures(user))
    hd = next(e for e in out["entries"] if e["recommended"])
    assert hd["installed"] is None and hd["direct"] and hd["size"] > 10 << 30
    # text output, too
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert mods.main(["--user-dir", str(user), "catalog", "--catalog-url", ""]) == 0
    assert "[recommended]" in buf.getvalue()


def test_bad_entries_are_left_out():
    raw = {"updated": "x", "mods": [
        {"id": "ok", "kind": "mod", "name": "Ok", "page_url": "https://e.org", "downloads": []},
        {"id": "ok", "kind": "mod", "name": "Again", "page_url": "https://e.org"},          # repeated id
        {"id": "Bad Id", "kind": "mod", "name": "x", "page_url": "https://e.org"},
        {"id": "http", "kind": "mod", "name": "x", "page_url": "https://e.org",
         "downloads": [{"url": "http://e.org/x.zip"}]},                                       # not https
        {"id": "escape", "kind": "textures", "name": "x", "page_url": "https://e.org",
         "downloads": [{"url": "https://e.org/x.zip", "into": "../../x"}]},
        {"id": "kind", "kind": "game", "name": "x", "page_url": "https://e.org"},
        {"id": "sum", "kind": "mod", "name": "x", "page_url": "https://e.org",
         "downloads": [{"url": "https://e.org/x.zip", "sha256": "abc"}]},
    ]}
    cat, warnings = mods.validate_catalog(raw, remote=True)
    assert [e["id"] for e in cat["mods"]] == ["ok"]
    assert len(warnings) == 6
    with pytest.raises(mods.Error):
        mods.validate_catalog({"mods": "no"}, remote=True)


def test_newer_catalog_from_the_web(user, tmp_path, server):
    with open(mods.CATALOG, encoding="utf-8") as f:
        bundled = json.load(f)
    newer = dict(bundled, updated="2999-01-01", mods=bundled["mods"][:1] + [
        {"id": "extra", "kind": "mod", "name": "Extra", "page_url": "https://example.org/extra"}])
    older = dict(bundled, updated="2000-01-01", mods=bundled["mods"][:1])
    s = server({"/new.json": json.dumps(newer).encode(), "/old.json": json.dumps(older).encode()})
    out = run(user, "catalog", "--refresh", "--catalog-url", s.base + "/new.json")
    assert out["catalog"]["source"] == "remote"
    assert [e["id"] for e in out["entries"]][-1] == "extra"
    # kept for next time, without fetching again
    out = run(user, "catalog")
    assert out["catalog"]["source"] == "cache" and out["entries"][-1]["id"] == "extra"
    # an older one doesn't replace the tools' own
    out = run(user, "catalog", "--refresh", "--catalog-url", s.base + "/old.json")
    assert out["catalog"]["source"] == "bundled"
    # nothing there: the copy here, with a warning
    out = run(user, "catalog", "--refresh", "--catalog-url", s.base + "/missing.json")
    assert out["catalog"]["source"] in ("bundled", "cache")
    assert any("can't be had" in w for w in out["warnings"])


def test_a_mod_download_without_sha256_is_refused():
    """A mod can carry a code patch (native code once recompiled): the catalog has to pin it."""
    base = {"name": "X", "page_url": "https://example.org/x"}
    raw = {"mods": [
        dict(base, id="pinned", kind="mod", downloads=[{"url": "https://example.org/a.ips", "file": "a.ips",
                                                         "archive": "file", "as": "code.ips", "sha256": "0" * 64}]),
        dict(base, id="unpinned", kind="mod", downloads=[{"url": "https://example.org/b.ips", "file": "b.ips",
                                                           "archive": "file", "as": "code.ips"}]),
        dict(base, id="pack", kind="textures", downloads=[{"url": "https://example.org/t.zip"}])]}
    cat, warnings = mods.validate_catalog(raw, remote=True)
    assert [e["id"] for e in cat["mods"]] == ["pinned", "pack"]
    assert len(warnings) == 1 and "unpinned" in warnings[0] and "sha256" in warnings[0]


def test_the_default_catalog_url_is_on_main():
    assert "/MH4UltimateDC/main/tools/mods_catalog.json" in mods.CATALOG_URL
    with open(os.path.join(HERE, "..", "port", "app", "config.h"), encoding="utf-8") as f:
        assert mods.CATALOG_URL in f.read()


# ── texture packs ──

def hd_like(tmp_path):
    """Like the HD pack: a .7z of folders, then a roll-up that replaces UI/ and adds pack.json."""
    base = make_7z(tmp_path / "srv" / "base.7z", [
        ("Armor/male/tex1_8x8_AAAA_13.png", b"armor"), ("Maps/Dunes/tex1_8x8_BBBB_13.png", b"map"),
        ("UI/old/tex1_8x8_CCCC_13.png", b"old ui"), ("UI/tex1_8x8_DDDD_13.png", b"ui v3.0"), ("Maps", None)],
        encode_header=True)
    rollup = make_7z(tmp_path / "srv" / "rollup.7z", [
        ("pack.json", b'{"options": {"skip_mipmap": false, "flip_png_files": true, "use_new_hash": true}}'),
        ("UI/tex1_8x8_DDDD_13.png", b"ui v3.03"), ("Maps/Dunes/tex1_8x8_BBBB_13.png", b"map v3.03")])
    return base, rollup


def test_texture_pack_in_steps(user, tmp_path):
    base, rollup = hd_like(tmp_path)
    cat = catalog(tmp_path / "cat.json", [entry("hd", "textures", [
        {"url": url(base), "size": base.stat().st_size, "archive": "7z"},
        {"url": url(rollup), "archive": "7z", "delete_first": ["ui"]}],
        needs=["custom_textures"], recommended=True)])
    write(user / "load" / "textures" / ".mh4u-staging-0ld" / "big.png")     # an install stopped half-way
    out, events = call(user, "install-from-catalog", "hd", "--catalog", cat)
    assert out["files"] == 4 and out["pack"] == "hd"
    assert "custom textures" in out["messages"][0]
    assert tree(textures(user)) == ["Armor/male/tex1_8x8_AAAA_13.png", "Maps/Dunes/tex1_8x8_BBBB_13.png",
                                    "UI/tex1_8x8_DDDD_13.png", "pack.json"]
    assert (textures(user) / "UI" / "tex1_8x8_DDDD_13.png").read_bytes() == b"ui v3.03"
    assert (textures(user) / "Maps" / "Dunes" / "tex1_8x8_BBBB_13.png").read_bytes() == b"map v3.03"
    kinds = {e["event"] for e in events}
    assert {"step", "download", "unpack", "install"} <= kinds
    assert not os.listdir(user / "mh4u-mods" / ".downloads")           # the downloads are gone
    assert not [p for p in os.listdir(user / "load" / "textures") if p.startswith(".")]   # and the staging
    listed = run(user, "catalog", "--catalog", cat)["entries"][0]
    assert listed["installed"]["as"] == "textures" and listed["installed"]["files"] == 4
    # files of the player's own stay when it is removed
    write(textures(user) / "Mine" / "tex1_1x1_EEEE_13.png", b"mine")
    out = run(user, "remove", "hd")
    assert "removed from the texture folder (4 files)" in out["messages"][0]
    assert tree(textures(user)) == ["Mine/tex1_1x1_EEEE_13.png"]
    assert run(user, "catalog", "--catalog", cat)["entries"][0]["installed"] is None


def test_texture_pack_root_into_and_wrappers(user, tmp_path):
    z = make_zip(tmp_path / "MH4HDUI.zip", {"MH4HDUI-main/README.md": "hi", "MH4HDUI-main/MH4u/UI/a.png": "a",
                                            "MH4HDUI-main/MH4u/UI/sub/b.png": "b",
                                            "MH4HDUI-main/MH4u/Other/c.png": "not this"})
    other = make_zip(tmp_path / "other.zip", {"0004000000126300/pack.json": "{}", "0004000000126300/x/d.png": "d",
                                              "readme.txt": "hi"})
    cat = catalog(tmp_path / "cat.json", [
        entry("ui", "textures", [{"url": url(z), "archive": "zip", "root": "mh4u/ui", "into": "UI"}],
              conflicts=["other"]),
        entry("other", "textures", [{"url": url(other)}], conflicts=["ui"])])
    run(user, "install-from-catalog", "ui", "--catalog", cat)
    assert tree(textures(user)) == ["UI/a.png", "UI/sub/b.png"]
    err = run(user, "install-from-catalog", "other", "--catalog", cat, code=1)
    assert err["error"]["code"] == "conflict" and err["error"]["entries"] == ["ui"]
    run(user, "install-from-catalog", "other", "--catalog", cat, "--force")
    assert tree(textures(user)) == ["UI/a.png", "UI/sub/b.png", "pack.json", "x/d.png"]   # the title folder looked through
    run(user, "remove", "ui")
    assert tree(textures(user)) == ["pack.json", "x/d.png"]


def test_texture_pack_files_of_another_pack_or_the_players(user, tmp_path):
    one = make_zip(tmp_path / "one.zip", {"same.png": "1", "a.png": "a"})
    two = make_zip(tmp_path / "two.zip", {"same.png": "2"})
    run(user, "install", "--textures", str(one), "--name", "One")
    err = run(user, "install", "--textures", str(two), "--name", "Two", code=1)
    assert err["error"]["code"] == "conflict" and err["error"]["packs"] == ["One"]
    out = run(user, "install", "--textures", str(two), "--name", "Two", "--force")
    assert (textures(user) / "same.png").read_text() == "2"
    run(user, "remove", "One")                  # same.png is Two's now: it stays
    assert tree(textures(user)) == ["same.png"]
    write(textures(user) / "mine.png", "mine")
    three = make_zip(tmp_path / "three.zip", {"mine.png": "3"})
    err = run(user, "install", "--textures", str(three), code=1)
    assert err["error"]["code"] == "foreign_files" and err["error"]["files"] == ["mine.png"]
    assert (textures(user) / "mine.png").read_text() == "mine"
    out = run(user, "install", "--textures", str(three), "--force")
    assert out["replaced"] == 1
    assert run(user, "install", "--textures", str(three), code=1)["error"]["code"] == "exists"
    run(user, "install", "--textures", str(three), "--replace")


def test_texture_pack_for_another_region(user, tmp_path):
    eu = make_zip(tmp_path / "eu.zip", {"0004000000126100/a.png": "a"})
    err = run(user, "install", "--textures", str(eu), code=1)
    assert err["error"]["code"] == "other_title"
    assert not textures(user).exists() or tree(textures(user)) == []


def test_texture_pack_names_one_file_on_windows(user, tmp_path):
    # a texture pack shouldn't fail for one such pair: the later one is used, with a warning
    pack = make_7z(tmp_path / "p.7z", [("UI/A.png", b"1"), ("ui/a.png", b"2"), ("b.png", b"b")])
    out = run(user, "install", "--textures", str(pack))
    assert any("one file on Windows" in w for w in out["warnings"])
    assert len(tree(textures(user))) == 2


# ── mods ──

def ips_patch(offset, data):
    return b"PATCH" + offset.to_bytes(3, "big") + len(data).to_bytes(2, "big") + data + b"EOF"


def test_mod_single_file_and_zip_layouts(user, tmp_path):
    code = write(tmp_path / "srv" / "code.ips", ips_patch(0xE2BCDE, b"\x64"))     # in the data: data only
    digest = hashlib.sha256(code.read_bytes()).hexdigest()
    life = make_zip(tmp_path / "srv" / "life.zip", {
        "Pkg/Azahar/load/mods/0004000000126300/exefs/code.ips": ips_patch(0x100, b"\0\0\0\0"),
        "Pkg/Luma3DS/luma/titles/0004000000126300/code.ips": ips_patch(0x100, b"\0\0\0\0"),
        "Pkg/README_en.md": "readme"})
    romfs = make_7z(tmp_path / "srv" / "romfs.7z", [("Some mod/romfs/data/x.arc", b"arc"),
                                                     ("Some mod/readme.txt", b"hi")])
    cat = catalog(tmp_path / "cat.json", [
        entry("crafting", "mod", [{"url": url(code), "size": code.stat().st_size, "sha256": digest, "archive": "file",
                                   "as": "exefs/code.ips"}]),
        entry("life", "mod", [{"url": url(life), "archive": "zip", "root": "Azahar"}]),
        entry("romfs", "mod", [{"url": url(romfs)}]),
        entry("page", "mod", [])])
    out = run(user, "install-from-catalog", "crafting", "--catalog", cat)
    mod = next(m for m in out["mods"] if m["id"] == out["mod"])
    assert mod["catalog"] == "crafting" and mod["name"] == "Crafting" and mod["enabled"]
    assert mod["code"]["changes_code"] is False and mod["code"]["adds_code"] is False
    lib = user / "mh4u-mods" / out["mod"]
    assert (lib / "exefs" / "code.ips").read_bytes() == code.read_bytes()
    assert json.loads((lib / "mod.json").read_text())["catalog"] == "crafting"
    out = run(user, "install-from-catalog", "life", "--catalog", cat)
    life_mod = next(m for m in out["mods"] if m["id"] == out["mod"])
    assert not life_mod["enabled"] and any("stays off" in m for m in out["messages"])   # one code mod at a time
    assert life_mod["layout"] == "title"
    out = run(user, "install-from-catalog", "romfs", "--catalog", cat)
    assert next(m for m in out["mods"] if m["id"] == out["mod"])["contains"] == ["romfs"]
    # installing again updates it in place
    out = run(user, "install-from-catalog", "crafting", "--catalog", cat)
    assert [m["id"] for m in out["mods"]].count("Crafting") == 1
    listed = {e["id"]: e for e in run(user, "catalog", "--catalog", cat)["entries"]}
    assert listed["crafting"]["installed"]["as"] == "mod" and listed["crafting"]["installed"]["enabled"]
    assert listed["page"]["direct"] is False
    assert run(user, "install-from-catalog", "page", "--catalog", cat, code=1)["error"]["code"] == "page_only"
    out = run(user, "remove", "crafting")                       # by its catalog id
    assert "Crafting" not in [m["id"] for m in out["mods"]]


def test_checksum_and_bad_archives_install_nothing(user, tmp_path):
    good = make_zip(tmp_path / "m.zip", {"romfs/data/x.arc": "x"})
    evil = make_zip(tmp_path / "evil.zip", {"../../outside.txt": "x", "romfs/data/y.arc": "y"})
    evil7 = make_7z(tmp_path / "evil.7z", [("romfs/data/a.arc", b"a"), ("../b.arc", b"b")])
    rar = write(tmp_path / "pack.rar", b"Rar!\x1a\x07\x01\x00 rest")
    cat = catalog(tmp_path / "cat.json", [
        entry("sum", "mod", [{"url": url(good), "sha256": "0" * 64}]),
        entry("evil", "mod", [{"url": url(evil)}]),
        entry("evil7", "textures", [{"url": url(evil7)}]),
        entry("rar", "textures", [{"url": url(rar)}])])
    assert run(user, "install-from-catalog", "sum", "--catalog", cat, code=1)["error"]["code"] == "checksum"
    assert run(user, "install-from-catalog", "evil", "--catalog", cat, code=1)["error"]["code"] == "archive"
    assert run(user, "install-from-catalog", "evil7", "--catalog", cat, code=1)["error"]["code"] == "archive"
    err = run(user, "install-from-catalog", "rar", "--catalog", cat, code=1)["error"]
    assert err["code"] == "manual_extract" and err["target"] == str(textures(user))
    assert os.path.isfile(err["download"])                     # kept, to unpack by hand
    assert not (tmp_path / "outside.txt").exists() and not (user / "outside.txt").exists()
    assert run(user, "list")["mods"] == []
    assert not textures(user).exists() or tree(textures(user)) == []
    assert not [p for p in os.listdir(user / "mh4u-mods") if p.startswith(".adding")]


def test_download_resumes_and_checks(user, tmp_path, server):
    data = os.urandom(3 << 20)
    blob = make_zip(tmp_path / "big.zip", {"romfs/data/big.arc": data})
    raw = blob.read_bytes()
    s = server({"/big.zip": raw})
    downloads = user / "mh4u-mods" / ".downloads"
    write(downloads / "big.zip.part", raw[:1000])               # a download that stopped
    cat = catalog(tmp_path / "cat.json", [entry("big", "mod", [
        {"url": s.base + "/big.zip", "file": "big.zip", "size": len(raw),
         "sha256": hashlib.sha256(raw).hexdigest()}])])
    out, events = call(user, "install-from-catalog", "big", "--catalog", cat, "--keep-download")
    assert s.requests == [("/big.zip", "bytes=1000-")]
    assert (downloads / "big.zip").read_bytes() == raw          # kept, as asked
    started = next(e for e in events if e["event"] == "download")
    assert started["resumed"] == 1000 and started["total"] == len(raw)
    assert (user / "mh4u-mods" / out["mod"] / "romfs" / "data" / "big.arc").read_bytes() == data
    # a server without ranges: from the start again
    s2 = server({"/big.zip": raw}, ranges=False)
    (downloads / "big.zip").unlink()
    write(downloads / "big.zip.part", raw[:1000])
    path = mods.download(s2.base + "/big.zip", str(downloads), "big.zip", len(raw))
    assert open(path, "rb").read() == raw
    # a missing file
    with pytest.raises(mods.Error) as e:
        mods.download(s2.base + "/nothing.zip", str(downloads), "nothing.zip")
    assert e.value.code == "download" and "404" in e.value.message


def test_install_from_url_and_archives(user, tmp_path, server):
    z7 = make_7z(tmp_path / "mod.7z", [("My Mod/romfs/data/a.arc", b"a")], method="bcj+lzma")
    s = server({"/files/My%20Mod.7z": z7.read_bytes()})
    out = run(user, "install", s.base + "/files/My%20Mod.7z")
    assert out["mod"] == "My Mod" and out["mods"][0]["contains"] == ["romfs"]
    assert not os.listdir(user / "mh4u-mods" / ".downloads")
    # add takes a .7z and a .tar.gz too now
    tar = tmp_path / "t.tar.gz"
    with tarfile.open(tar, "w:gz") as t:
        info = tarfile.TarInfo("romfs/data/b.arc")
        info.size = 1
        t.addfile(info, io.BytesIO(b"b"))
    out = run(user, "add", str(tar), "--name", "Tarred")
    assert out["mod"] == "Tarred"
    assert (user / "mh4u-mods" / "Tarred" / "romfs" / "data" / "b.arc").read_bytes() == b"b"


def test_code_mod_with_new_code_needs_a_library_built_with_it(user, tmp_path, monkeypatch):
    """A code mod that adds code after the game's own (a "code cave") runs on the port once the
    code library is built with it, which then recompiles the code's last page to its end
    (the build record's code_size); a library built without it, or by an older build_game.py
    that left the cave out, has to be built again."""
    text, end = mods.text_size(), mods.code_segment_end(mods.text_size())
    if text is None or end is None:
        pytest.skip("no config/versions.json")
    patch = ips_patch(text + 0x10, b"\x01\x02\x03\x04")
    cave = make_zip(tmp_path / "cave.zip", {"exefs/code.ips": patch})
    out = run(user, "add", str(cave), "--name", "Cave")
    assert out["mods"][0]["code"]["adds_code"] is True
    assert out["mods"][0]["code"]["changes_code"] is True          # the cave is part of the checked code
    run(user, "deploy")
    lib = write(user / "recomp" / (TID + (".dll" if sys.platform.startswith("win") else
                                         ".dylib" if sys.platform == "darwin" else ".so")), b"lib")
    status = run(user, "status")["code_library"]
    assert status["state"] == "unknown" and status["adds_code"] is True     # no record
    mods.write_build_record(str(lib), [], text, False)                      # built without the mod
    assert run(user, "status")["code_library"]["state"] == "rebuild"
    deployed = user / "load" / "mods" / TID / "exefs" / "code.ips"
    mods.write_build_record(str(lib), [str(deployed)], text, True)          # an older build_game.py
    status = run(user, "status")["code_library"]
    assert status["state"] == "rebuild" and "older" in status["reason"]
    mods.write_build_record(str(lib), [str(deployed)], text, True, code_size=end)
    status = run(user, "status")["code_library"]
    assert status["state"] == "ok" and status["adds_code"] is True
    assert lib.exists()
