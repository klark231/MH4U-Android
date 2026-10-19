"""Tests for tools/check_no_game_data.py (synthetic data, temporary git repos)."""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "check_no_game_data.py"
sys.path.insert(0, str(ROOT / "tools"))
import check_no_game_data as guard  # noqa: E402


def git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    (tmp_path / "README.md").write_text("synthetic\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path


def put(repo: Path, rel: str, data: bytes | str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data if isinstance(data, bytes) else data.encode())


def run(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOL), "--repo", str(repo), *args],
        capture_output=True, text=True,
    )


def commit_range(repo: Path, files: dict[str, bytes | str]) -> str:
    base = git(repo, "rev-parse", "HEAD")
    for rel, data in files.items():
        put(repo, rel, data)
    git(repo, "add", "-f", "-A")
    git(repo, "commit", "-q", "-m", "change")
    return f"{base}..HEAD"


def test_clean_staged(repo: Path) -> None:
    put(repo, "docs/a.md", "hello\n")
    put(repo, "tools/x.py", "print(1)\n")
    git(repo, "add", "-A")
    r = run(repo, "--staged")
    assert r.returncode == 0, r.stdout + r.stderr


def test_clean_range(repo: Path) -> None:
    rng = commit_range(repo, {"src/a.c": "int x;\n"})
    assert run(repo, "--range", rng).returncode == 0


def test_nothing_staged_is_ok(repo: Path) -> None:
    assert run(repo, "--staged").returncode == 0


@pytest.mark.parametrize(
    "path",
    ["game.3ds", "a/b.CXI", "x.cia", "x.cfa", "x.cci", "x.arc", "slot.sav",
     "code.bin", "sub/exheader.bin", "romfs.bin", "exefs.bin", "baserom/anything.txt"],
)
def test_ci_patterns(repo: Path, path: str) -> None:
    put(repo, path, "x")
    git(repo, "add", "-f", path)
    r = run(repo, "--staged")
    assert r.returncode == 1
    assert path in r.stdout


@pytest.mark.parametrize("path", ["build/log.txt", "export/callgraph.tsv", "baserom/x.txt", "a/build/y.txt"])
def test_forbidden_dirs(repo: Path, path: str) -> None:
    put(repo, path, "x")
    git(repo, "add", "-f", path)
    r = run(repo, "--staged")
    assert r.returncode == 1
    assert path in r.stdout and "forbidden-dir" in r.stdout


@pytest.mark.parametrize("path", ["shot.ppm", "a.bin", "sound.WAV"])
def test_extensions(repo: Path, path: str) -> None:
    put(repo, path, "x")
    git(repo, "add", path)
    r = run(repo, "--staged")
    assert r.returncode == 1 and "extension" in r.stdout


@pytest.mark.parametrize("magic", ["MGPU", "MDSP", "MHID"])
def test_capture_magics(repo: Path, magic: str) -> None:
    put(repo, "cap/frame.dat", magic.encode() + b"\x01\x00\x00\x00rest")
    git(repo, "add", "cap/frame.dat")
    r = run(repo, "--staged")
    assert r.returncode == 1 and "magic" in r.stdout and magic in r.stdout


def check_bytes(path: str, data: bytes, allowed: dict[str, str] | None = None) -> list[tuple[str, str]]:
    """Run the one checking path (check_entries) on a single in-memory file."""
    return guard.check_entries([("100644", "s", path)], lambda sha: data, allowed or {})


def test_magic_not_at_start_is_fine() -> None:
    assert check_bytes("a.dat", b"xMGPU") == []


def test_size_rule_and_exemption(repo: Path) -> None:
    big = b"A" * (2 * 1024 * 1024 + 1)
    put(repo, "third_party/archive.dat", big)
    git(repo, "add", "third_party/archive.dat")
    r = run(repo, "--staged")
    assert r.returncode == 1 and "size" in r.stdout

    digest = hashlib.sha256(big).hexdigest()
    put(repo, "config/large_files.txt", f"# vendored\n{digest}  third_party/archive.dat\n")
    assert run(repo, "--staged").returncode == 0

    # Same path, different content: the exemption no longer applies.
    put(repo, "third_party/archive.dat", big + b"B")
    git(repo, "add", "third_party/archive.dat")
    assert run(repo, "--staged").returncode == 1

    # Same content under another path: not exempt either.
    put(repo, "third_party/archive.dat", big)
    put(repo, "other/archive.dat", big)
    git(repo, "add", "-A")
    r = run(repo, "--staged")
    assert r.returncode == 1 and "other/archive.dat" in r.stdout
    assert "third_party/archive.dat" not in r.stdout


def test_exactly_2mb_is_allowed() -> None:
    # Fixture adjusted in N0-19 follow-up 2: short lines, since one 2 MB line is a dense blob now.
    line = b"A" * 79 + b"\n"
    data = (line * (guard.MAX_BYTES // 80 + 1))[: guard.MAX_BYTES]
    assert check_bytes("a.dat", data) == []


def svc_line(i: int) -> str:
    return f"{i} 32 pc=0010ABCD r0=00000001\n"


@pytest.mark.parametrize(
    "make",
    [
        svc_line,
        lambda i: f"# thread {i} entry=0x00100000\n",
        lambda i: f"seq={i} session=3 name=x\n",
        lambda i: f"f={i} t=5 pc=0x00100000\n",
        lambda i: f"op svc=0x{i:02x} r=0x00000000\n",
        lambda i: f"op cmd=0x{i:04x} r=0x00000000\n",
    ],
    ids=["svc", "thread", "ipc", "state", "svc-field", "cmd-field"],
)
def test_trace_rule(repo: Path, make) -> None:
    put(repo, "notes/t.txt", "".join(make(i) for i in range(51)))
    git(repo, "add", "notes/t.txt")
    r = run(repo, "--staged")
    assert r.returncode == 1 and "trace" in r.stdout


def test_trace_rule_limit_and_binary(repo: Path) -> None:
    put(repo, "notes/ok.txt", "".join(svc_line(i) for i in range(50)))
    assert check_bytes("n.txt", "".join(svc_line(i) for i in range(50)).encode()) == []
    assert check_bytes("n.txt", "".join(svc_line(i) for i in range(51)).encode())
    # Binary data is not scanned as text.
    # (N0-19 follow-up 2: such a file is refused as binary content now, but never as a trace.)
    found = check_bytes("n.dat", b"\0" + "".join(svc_line(i) for i in range(60)).encode())
    assert found and all(r.startswith("binary-content") for _, r in found)


def test_range_only_checks_the_range(repo: Path) -> None:
    put(repo, "old.ppm", "x")
    git(repo, "add", "old.ppm")
    git(repo, "commit", "-q", "-m", "old dirt")
    rng = commit_range(repo, {"docs/new.md": "fine\n"})
    assert run(repo, "--range", rng).returncode == 0


def test_range_dirty_commit(repo: Path) -> None:
    rng = commit_range(repo, {"docs/new.md": "fine\n", "x/y.sav": "s"})
    r = run(repo, "--range", rng)
    assert r.returncode == 1
    assert "x/y.sav" in r.stdout and "docs/new.md" not in r.stdout


def test_range_dirty_added_and_removed_within_range_is_reported(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    put(repo, "a.ppm", "x")
    git(repo, "add", "a.ppm")
    git(repo, "commit", "-q", "-m", "add")
    git(repo, "rm", "-q", "a.ppm")
    git(repo, "commit", "-q", "-m", "remove")
    r = run(repo, "--range", f"{base}..HEAD")
    assert r.returncode == 1 and "a.ppm" in r.stdout


def test_range_dirty_in_merged_branch_is_reported(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "side")
    put(repo, "side/t.wav", "x")
    git(repo, "add", "side/t.wav")
    git(repo, "commit", "-q", "-m", "side")
    git(repo, "rm", "-q", "side/t.wav")
    git(repo, "commit", "-q", "-m", "side rm")
    git(repo, "checkout", "-q", "-")
    put(repo, "m.md", "m\n")
    git(repo, "add", "m.md")
    git(repo, "commit", "-q", "-m", "main")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge", "side")
    r = run(repo, "--range", f"{base}..HEAD")
    assert r.returncode == 1 and "side/t.wav" in r.stdout


def test_range_dirty_commit_before_range_is_accepted(repo: Path) -> None:
    put(repo, "old.ppm", "x")
    git(repo, "add", "old.ppm")
    git(repo, "commit", "-q", "-m", "old")
    base = git(repo, "rev-parse", "HEAD")
    put(repo, "ok.md", "fine\n")
    git(repo, "add", "ok.md")
    git(repo, "commit", "-q", "-m", "ok")
    r = run(repo, "--range", f"{base}..HEAD")
    assert r.returncode == 0, r.stdout


def test_unreadable_regular_file_is_an_error() -> None:
    def read(sha: str) -> bytes:
        raise RuntimeError("gone")

    with pytest.raises(RuntimeError, match="a.md"):
        guard.check_entries([("100644", "s", "a.md")], read, {})
    # gitlinks keep the name rules only
    assert guard.check_entries([("160000", "s", "sub/x.sav")], read, {})


def test_main_exit_2_when_cat_file_fails(repo: Path) -> None:
    rng = commit_range(repo, {"docs/lost.md": "unique lost content\n"})
    sha = git(repo, "rev-parse", "HEAD:docs/lost.md")
    obj = repo / ".git" / "objects" / sha[:2] / sha[2:]
    obj.chmod(0o644)
    obj.unlink()
    r = run(repo, "--range", rng)
    assert r.returncode == 2
    assert "cannot read docs/lost.md" in r.stderr


def test_staged_submodule_gitlink_is_not_an_error(repo: Path) -> None:
    sha = git(repo, "rev-parse", "HEAD")
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{sha},sub")
    assert run(repo, "--staged").returncode == 0


def test_range_three_dots(repo: Path) -> None:
    git(repo, "checkout", "-q", "-b", "side")
    put(repo, "s.wav", "x")
    git(repo, "add", "s.wav")
    git(repo, "commit", "-q", "-m", "side")
    git(repo, "checkout", "-q", "-")
    put(repo, "main.md", "m\n")
    git(repo, "add", "main.md")
    git(repo, "commit", "-q", "-m", "main")
    r = run(repo, "--range", "HEAD...side")
    assert r.returncode == 1 and "s.wav" in r.stdout and "main.md" not in r.stdout


def test_output_never_prints_content(repo: Path) -> None:
    put(repo, "x.ppm", "SECRET-CONTENT-123")
    git(repo, "add", "x.ppm")
    r = run(repo, "--staged")
    assert "SECRET-CONTENT-123" not in r.stdout + r.stderr


def test_bad_range_is_usage_error(repo: Path) -> None:
    assert run(repo, "--range", "nonexistent..HEAD").returncode == 2


def test_allowlist_parsing() -> None:
    h = "a" * 64
    parsed = guard.load_allowlist(f"# c\n\n{h}  dir/f.dat\nbadline\n{h[:10]} short\n")
    assert parsed == {"dir/f.dat": h}


def test_shipped_allowlist_lists_only_the_icons() -> None:
    text = (ROOT / "config" / "large_files.txt").read_text()
    assert text.startswith("#")
    # N0-19 follow-up 2: the tracked icons (binary files and the C byte-array form) are listed.
    assert set(guard.load_allowlist(text)) == {
        "port/app/icon/icon_data.h", "port/app/icon/mh4u.icns",
        "port/app/icon/mh4u.ico", "port/app/icon/mh4u.png",
    }


# --- every path of every commit in --range (hardening round) ---


def commit_all(repo: Path, msg: str = "c") -> None:
    git(repo, "add", "-f", "-A")
    git(repo, "commit", "-q", "-m", msg)


def test_range_duplicate_content_under_forbidden_second_name(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    put(repo, "a/clean.txt", "same bytes\n")
    put(repo, "export/b.txt", "same bytes\n")
    put(repo, "z/dump.ppm", "same bytes\n")
    commit_all(repo)
    r = run(repo, "--range", f"{base}..HEAD")
    assert r.returncode == 1
    assert "export/b.txt" in r.stdout and "z/dump.ppm" in r.stdout
    assert "a/clean.txt" not in r.stdout


def test_range_duplicate_content_across_commits(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    put(repo, "clean.txt", "dup\n")
    commit_all(repo, "one")
    put(repo, "build/out.txt", "dup\n")
    commit_all(repo, "two")
    r = run(repo, "--range", f"{base}..HEAD")
    assert r.returncode == 1 and "build/out.txt" in r.stdout


@pytest.mark.parametrize("content", ["", "README content\n"])
def test_range_forbidden_path_with_content_already_in_base(repo: Path, content: str) -> None:
    # README.md holds "synthetic\n"; the empty blob is added to the base here.
    put(repo, "empty.txt", "")
    commit_all(repo, "empty in base")
    base = git(repo, "rev-parse", "HEAD")
    data = "" if content == "" else "synthetic\n"
    put(repo, "baserom/x.txt", data)
    put(repo, "snap.wav", data)
    commit_all(repo)
    r = run(repo, "--range", f"{base}..HEAD")
    assert r.returncode == 1
    assert "baserom/x.txt" in r.stdout and "snap.wav" in r.stdout


def test_range_evil_merge_adds_forbidden_path(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "side")
    put(repo, "side.md", "side\n")
    commit_all(repo, "side")
    git(repo, "checkout", "-q", "-")
    put(repo, "main.md", "main\n")
    commit_all(repo, "main")
    git(repo, "merge", "-q", "--no-ff", "--no-commit", "side")
    put(repo, "export/evil.txt", "only in the merge\n")
    git(repo, "add", "-f", "export/evil.txt")
    git(repo, "commit", "-q", "-m", "evil merge")
    r = run(repo, "--range", f"{base}..HEAD")
    assert r.returncode == 1 and "export/evil.txt" in r.stdout


def test_range_added_then_deleted_forbidden_name_with_clean_twin(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    put(repo, "keep.txt", "twin\n")
    put(repo, "gone.ppm", "twin\n")
    commit_all(repo, "add")
    git(repo, "rm", "-q", "gone.ppm")
    git(repo, "commit", "-q", "-m", "rm")
    r = run(repo, "--range", f"{base}..HEAD")
    assert r.returncode == 1 and "gone.ppm" in r.stdout


def test_range_deleting_a_forbidden_path_is_not_reported(repo: Path) -> None:
    put(repo, "old.ppm", "x")
    commit_all(repo, "old")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "rm", "-q", "old.ppm")
    git(repo, "commit", "-q", "-m", "rm")
    assert run(repo, "--range", f"{base}..HEAD").returncode == 0


def test_range_content_rule_still_applies_to_duplicate_blob(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    put(repo, "a.txt", b"MGPU" + b"\0" * 8)
    put(repo, "b.txt", b"MGPU" + b"\0" * 8)
    commit_all(repo)
    r = run(repo, "--range", f"{base}..HEAD")
    assert r.returncode == 1 and "a.txt" in r.stdout and "b.txt" in r.stdout


def test_range_not_fooled_by_show_signature_config(repo: Path) -> None:
    git(repo, "config", "log.showSignature", "true")
    rng = commit_range(repo, {"export/q.txt": "x\n"})
    r = run(repo, "--range", rng)
    assert r.returncode == 1 and "export/q.txt" in r.stdout


def test_range_not_fooled_by_ignore_submodules_config(repo: Path) -> None:
    git(repo, "config", "diff.ignoreSubmodules", "all")
    base = git(repo, "rev-parse", "HEAD")
    sha = git(repo, "rev-parse", "HEAD")
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{sha},export/sub")
    git(repo, "commit", "-q", "-m", "gitlink")
    r = run(repo, "--range", f"{base}..HEAD")
    assert r.returncode == 1 and "export/sub" in r.stdout
    git(repo, "reset", "-q", "--soft", base)
    r = run(repo, "--staged")
    assert r.returncode == 1 and "export/sub" in r.stdout


def test_parse_raw_rejects_unknown_tokens() -> None:
    with pytest.raises(RuntimeError):
        guard.parse_raw(b"No signature\0:000000 100644 " + b"0" * 40 + b" " + b"a" * 40 + b" A\0f\0")


def test_staged_type_change_symlink_to_magic_file(repo: Path) -> None:
    (repo / "lnk").symlink_to("README.md")
    git(repo, "add", "lnk")
    git(repo, "commit", "-q", "-m", "link")
    (repo / "lnk").unlink()
    put(repo, "lnk", b"MGPU" + b"\0" * 8)
    git(repo, "add", "lnk")
    r = run(repo, "--staged")
    assert r.returncode == 1 and "lnk" in r.stdout


def test_staged_glob_named_gitlink_does_not_hide_file(repo: Path) -> None:
    put(repo, "a.dat", b"MGPU" + b"\0" * 8)
    git(repo, "add", "a.dat")
    sha = git(repo, "rev-parse", "HEAD")
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{sha},)x.dat")
    r = run(repo, "--staged")
    assert r.returncode == 1 and "a.dat: magic" in r.stdout


@pytest.mark.parametrize("path", [".bin", "a/.PPM", ".wav", "dir/.BIN"])
def test_extension_rule_for_dotfile_names(repo: Path, path: str) -> None:
    put(repo, path, "x")
    git(repo, "add", "-f", path)
    r = run(repo, "--staged")
    assert r.returncode == 1 and "extension" in r.stdout
    git(repo, "commit", "-q", "-m", "c")
    assert run(repo, "--range", "HEAD~1..HEAD").returncode == 1


@pytest.mark.parametrize("path", ["Export/x.txt", "BUILD/x.txt", "Export/a/b.tsv"])
def test_forbidden_dir_is_case_insensitive(repo: Path, path: str) -> None:
    put(repo, path, "x")
    git(repo, "add", "-f", path)
    r = run(repo, "--staged")
    assert r.returncode == 1 and "forbidden-dir" in r.stdout


# --- polish round: replace refs, Windows-equivalent names, dedupe, range endpoints ---


def test_range_ignores_git_replace(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    put(repo, "shot.ppm", "x")
    commit_all(repo, "dirty")
    real = git(repo, "rev-parse", "HEAD")
    clean = git(repo, "commit-tree", f"{base}^{{tree}}", "-p", base, "-m", "clean")
    git(repo, "replace", real, clean)
    assert git(repo, "diff", "--name-only", f"{base}..HEAD") == ""  # the substitute is what git shows
    r = run(repo, "--range", f"{base}..HEAD")
    assert r.returncode == 1 and "shot.ppm" in r.stdout


def test_range_ignores_replaced_blob(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    put(repo, "cap.dat", b"MGPU" + b"\0" * 8)
    commit_all(repo, "magic")
    real = git(repo, "rev-parse", "HEAD:cap.dat")
    clean = git(repo, "rev-parse", f"{base}:README.md")
    git(repo, "replace", real, clean)
    r = run(repo, "--range", f"{base}..HEAD")
    assert r.returncode == 1 and "cap.dat: magic" in r.stdout


WINDOWS_EQUIVALENT = [
    ("a.ppm ", "extension"),
    ("b.wav.", "extension"),
    ("c.wav . .", "extension"),
    ("export /x.txt", "forbidden-dir"),
    ("Export./x.txt", "forbidden-dir"),
    ("a/build . /x.txt", "forbidden-dir"),
    ("x.ppm\n", "extension"),
    ("dir/x.bin\n", "extension"),
    ("game.3ds.", "ci-pattern"),
    ("sub/code.bin ", "ci-pattern"),
]


@pytest.mark.parametrize("path,rule", WINDOWS_EQUIVALENT)
def test_windows_equivalent_names_staged(repo: Path, path: str, rule: str) -> None:
    put(repo, path, "x")
    git(repo, "add", "-f", "--", path)
    r = run(repo, "--staged")
    assert r.returncode == 1 and rule in r.stdout


@pytest.mark.parametrize("path,rule", WINDOWS_EQUIVALENT)
def test_windows_equivalent_names_range(repo: Path, path: str, rule: str) -> None:
    rng = commit_range(repo, {path: "x"})
    r = run(repo, "--range", rng)
    assert r.returncode == 1 and rule in r.stdout


def test_trailing_dots_in_clean_names_are_fine(repo: Path) -> None:
    rng = commit_range(repo, {"notes. /readme.md.": "fine\n", "exports/a.txt": "fine\n"})
    assert run(repo, "--range", rng).returncode == 0


def test_merge_reports_each_path_and_rule_once(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "side")
    put(repo, "both.ppm", "side\n")
    commit_all(repo, "side")
    git(repo, "checkout", "-q", "-")
    put(repo, "both.ppm", "main\n")
    commit_all(repo, "main")
    subprocess.run(["git", "merge", "-q", "--no-ff", "--no-commit", "side"], cwd=repo, capture_output=True)
    put(repo, "both.ppm", "resolved\n")
    git(repo, "add", "both.ppm")
    git(repo, "commit", "-q", "-m", "merge")
    r = run(repo, "--range", f"{base}..HEAD")
    assert r.returncode == 1
    assert r.stdout.count("both.ppm: extension") == 1
    assert "1 problem(s)" in r.stderr


def test_check_entries_dedupes_pairs() -> None:
    entries = [("100644", "s", "a.ppm"), ("100644", "s", "a.ppm")]
    assert len(guard.check_entries(entries, lambda sha: b"x", {})) == 1


@pytest.mark.parametrize("rng", ["..HEAD", "HEAD..", "..", "...", "HEAD...", "...HEAD", " ..HEAD"])
def test_range_with_empty_endpoint_is_an_error(repo: Path, rng: str) -> None:
    r = run(repo, "--range", rng)
    assert r.returncode == 2
    assert "endpoint" in r.stderr and "ok" not in r.stdout


def test_range_two_and_three_dots_still_work(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    commit_range(repo, {"s.wav": "x"})
    assert run(repo, "--range", f"{base}..HEAD").returncode == 1
    assert run(repo, "--range", f"{base}...HEAD").returncode == 1


def test_valid_empty_range_is_ok(repo: Path) -> None:
    r = run(repo, "--range", "HEAD..HEAD")
    assert r.returncode == 0 and "0 file(s) checked" in r.stdout


def _hidden_history(repo: Path) -> tuple[str, str]:
    """BASE; D adds cap.ppm; E deletes it; F adds a clean file. Returns (base, E)."""
    base = git(repo, "rev-parse", "HEAD")
    put(repo, "cap.ppm", "x")
    commit_all(repo, "D")
    git(repo, "rm", "-q", "cap.ppm")
    git(repo, "commit", "-q", "-m", "E")
    e = git(repo, "rev-parse", "HEAD")
    put(repo, "clean.txt", "ok")
    commit_all(repo, "F")
    return base, e


def test_range_ignores_shallow_file(repo: Path) -> None:
    base, e = _hidden_history(repo)
    r = run(repo, "--range", f"{base}..HEAD")
    assert r.returncode == 1 and "cap.ppm" in r.stdout
    (repo / ".git" / "shallow").write_text(e + "\n")
    r = run(repo, "--range", f"{base}..HEAD")
    assert r.returncode == 1 and "cap.ppm" in r.stdout


def test_range_ignores_info_grafts(repo: Path) -> None:
    base, e = _hidden_history(repo)
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "grafts").write_text(e + "\n")
    r = run(repo, "--range", f"{base}..HEAD")
    assert r.returncode == 1 and "cap.ppm" in r.stdout


def test_range_endpoint_starting_with_dash_is_exit_2(repo: Path) -> None:
    r = run(repo, "--range=--since=2100..HEAD")
    assert r.returncode == 2


# ---- unmapped report, images, ELF files and archives (N0-19 follow-up) ----

def unmapped_line(i: int) -> str:
    return f"count={1000 - i} addr=0x{0x08000000 + i * 4:08x} size=4 rw={'read' if i % 2 else 'write'} lr=0x{0x00100000 + i:08x}\n"


def unmapped_report(n: int) -> str:
    return "# unmapped v1 synthetic\n" + "".join(unmapped_line(i) for i in range(n))


@pytest.mark.parametrize("lines,refused", [(49, False), (50, False), (51, True), (200, True)])
def test_unmapped_report_lines(repo: Path, lines: int, refused: bool) -> None:
    put(repo, "notes/u.txt", unmapped_report(lines))
    git(repo, "add", "notes/u.txt")
    r = run(repo, "--staged")
    assert (r.returncode == 1) == refused, r.stdout + r.stderr
    if refused:
        assert "trace" in r.stdout


def test_unmapped_report_without_0x_and_with_crlf() -> None:
    text = "".join(f"count=3 addr={0x1000 + i:x} size=1 rw=read lr={i:x}\r\n" for i in range(51))
    assert check_bytes("u.txt", text.encode())


@pytest.mark.parametrize("variant", ["trailing", "leading", "tabs"])
def test_unmapped_report_with_extra_field_is_refused(variant: str) -> None:
    def line(i: int) -> str:
        base = unmapped_line(i).rstrip("\n")
        if variant == "trailing":
            return base + " x=1\n"
        if variant == "leading":
            return " " + base + "\n"
        return base.replace(" ", "\t") + "\n"
    text = "".join(line(i) for i in range(51))
    assert check_bytes("u.txt", text.encode())
    assert check_bytes("u.txt", "".join(line(i) for i in range(50)).encode()) == []


def test_unmapped_report_near_misses_are_not_trace() -> None:
    text = "".join(f"count={i} addr=0x10 size=4 rw=exec lr=0x10\n" for i in range(60))
    assert check_bytes("u.txt", text.encode()) == []


BIN_EXTS = [".png", ".jpg", ".jpeg", ".bmp", ".tga", ".gif", ".elf", ".so", ".dll", ".exe",
            ".zip", ".7z", ".gz", ".tar", ".xz", ".zst", ".dmp", ".mgpu", ".gcap"]
BIN_MAGICS = {
    "ELF": b"\x7fELF", "PNG": b"\x89PNG\r\n\x1a\n", "JPEG": b"\xff\xd8\xff", "GIF87": b"GIF87a",
    "GIF89": b"GIF89a", "ZIP": b"PK\x03\x04", "gzip": b"\x1f\x8b", "7z": b"7z\xbc\xaf\x27\x1c",
    "xz": b"\xfd7zXZ\x00", "zstd": b"\x28\xb5\x2f\xfd",
}


@pytest.mark.parametrize("ext", BIN_EXTS + [e.upper() for e in BIN_EXTS[:3]])
def test_binary_extensions_staged_and_range(repo: Path, ext: str) -> None:
    put(repo, f"docs/file{ext}", "plain text\n")
    git(repo, "add", f"docs/file{ext}")
    r = run(repo, "--staged")
    assert r.returncode == 1 and "binary-ext" in r.stdout
    git(repo, "commit", "-q", "-m", "x")
    r = run(repo, "--range", "HEAD~1..HEAD")
    assert r.returncode == 1 and "binary-ext" in r.stdout


@pytest.mark.parametrize("trail", ["file.png ", "file.png.", "file.png\n"])
def test_binary_extension_windows_normalisation(trail: str) -> None:
    assert any(r.startswith("binary-ext") for r in guard.name_rules("a/" + trail))


@pytest.mark.parametrize("name", sorted(BIN_MAGICS))
def test_binary_magics_under_clean_name(repo: Path, name: str) -> None:
    put(repo, "notes.txt", BIN_MAGICS[name] + b"synthetic body")
    git(repo, "add", "notes.txt")
    r = run(repo, "--staged")
    assert r.returncode == 1 and "binary-magic" in r.stdout
    git(repo, "commit", "-q", "-m", "x")
    assert run(repo, "--range", "HEAD~1..HEAD").returncode == 1


def test_binary_magic_only_at_start() -> None:
    assert check_bytes("a.dat", b"x\x7fELF") == []
    assert check_bytes("a.dat", b"\x1f") == []


@pytest.mark.parametrize("name", ["PNG", "ELF", "ZIP"])
def test_binary_allowlist_exempts_only_matching_sha(repo: Path, name: str) -> None:
    data = BIN_MAGICS[name] + b"synthetic body"
    ext = {"PNG": ".png", "ELF": ".elf", "ZIP": ".zip"}[name]
    path = f"third_party/vendored{ext}"
    put(repo, path, data)
    git(repo, "add", path)
    assert run(repo, "--staged").returncode == 1

    digest = hashlib.sha256(data).hexdigest()
    put(repo, "config/large_files.txt", f"{digest}  {path}\n")
    assert run(repo, "--staged").returncode == 0
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "x")
    assert run(repo, "--range", "HEAD~1..HEAD").returncode == 0

    # Wrong sha, or the same bytes under another path: refused.
    put(repo, "config/large_files.txt", f"{'0' * 64}  {path}\n")
    put(repo, "other/copy" + ext, data)
    git(repo, "add", "-A")
    r = run(repo, "--staged")
    assert r.returncode == 1 and "other/copy" + ext in r.stdout
    git(repo, "commit", "-q", "-m", "y")
    put(repo, path, data + b"!")
    git(repo, "add", path)
    assert run(repo, "--staged").returncode == 1


def test_binary_exemption_does_not_cover_other_rules(repo: Path) -> None:
    # Listed, but under build/ (forbidden-dir), with a .bin name, or with trace content.
    png = BIN_MAGICS["PNG"] + b"body"
    lines = []
    for path, data in (("build/x.png", png), ("vendored/a.bin", png),
                       ("vendored/t.zip", BIN_MAGICS["ZIP"][:4] + b"\n" + b"".join(svc_line(i).encode() for i in range(60)))):
        put(repo, path, data)
        lines.append(f"{hashlib.sha256(data).hexdigest()}  {path}")
    put(repo, "config/large_files.txt", "\n".join(lines) + "\n")
    git(repo, "add", "-f", "-A")
    r = run(repo, "--staged")
    assert r.returncode == 1
    assert "build/x.png: forbidden-dir" in r.stdout
    assert "vendored/a.bin: extension" in r.stdout
    assert "vendored/t.zip: trace" in r.stdout
    assert "binary-" not in r.stdout


def test_binary_exemption_does_not_cover_capture_magic() -> None:
    # A capture magic and an image magic cannot both start one file, so the capture rule is
    # checked on an allowlisted file with an image name.
    data = b"MGPU" + b"\x00" * 8
    path = "third_party/x.png"
    found = check_bytes(path, data, {path: hashlib.sha256(data).hexdigest()})
    assert [p for p, _ in found] == [path]
    assert found[0][1].startswith("magic") and all(not r.startswith("binary-") for _, r in found)


NEW_RULES = ("binary-", "listing", "dense-blob")


def _tripped_in_tracked_tree(allowed: dict[str, str]) -> set[str]:
    files = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    tripped: set[str] = set()
    for rel in files.decode("utf-8", "surrogateescape").split("\0"):
        path = ROOT / rel
        if not rel or not path.is_file() or path.is_symlink():
            continue
        data = path.read_bytes()
        found = guard.check_entries([("100644", "s", rel)], lambda sha: data, allowed)
        if any(r.startswith(NEW_RULES) for _, r in found):
            tripped.add(rel)
    return tripped


def test_tracked_tree_binary_rules_are_a_known_set() -> None:
    # Intended change of the expected value (N0-19 follow-up 2): the icons are now listed in
    # config/large_files.txt, so nothing tracked trips these rules; a new file must be noticed.
    assert _tripped_in_tracked_tree(guard.read_allowlist(ROOT)) == set()


def test_tracked_tree_without_the_list_trips_only_the_icons() -> None:
    assert _tripped_in_tracked_tree({}) == {
        "port/app/icon/icon_data.h", "port/app/icon/mh4u.icns",
        "port/app/icon/mh4u.ico", "port/app/icon/mh4u.png",
    }


# ---- N0-19 follow-up 2: generic binary rule, text listings, dense blobs ----

RANDOM = bytes((i * 73 + 41) % 256 for i in range(100)) + b"\x00\xff"


@pytest.mark.parametrize(
    "name",
    [".dat", ".raw", ".rom", ".pcap", ".dds", ".ico", ".lz4", ".cab", ".o", ".a", ".dylib", ".pdf",
     ".nds", ".3dsx", ".cro", ".crr", ".img", ".iso", ".dump", ".mem", ".mp3", ".mp4", ".ogg", ".tif"],
)
@pytest.mark.parametrize("mode", ["staged", "range"])
def test_generic_binary_by_any_name(repo: Path, name: str, mode: str) -> None:
    path = "assets/blob" + name
    if mode == "staged":
        put(repo, path, RANDOM)
        git(repo, "add", "-f", path)
        r = run(repo, "--staged")
    else:
        r = run(repo, "--range", commit_range(repo, {path: RANDOM}))
    assert r.returncode == 1 and path in r.stdout and "binary-content" in r.stdout


def test_generic_binary_invalid_utf8_without_nul(repo: Path) -> None:
    put(repo, "a.txt", b"hello \xff\xfe world\n")
    git(repo, "add", "-f", "a.txt")
    r = run(repo, "--staged")
    assert r.returncode == 1 and "binary-content" in r.stdout


def _pad(n: int) -> bytes:
    """n bytes of short text lines (one long run of letters would be a dense blob)."""
    return (b"a" * 63 + b"\n") * (n // 64) + b"a" * (n % 64)


def test_generic_binary_nul_window_edges(repo: Path) -> None:
    put(repo, "in.txt", _pad(8191) + b"\x00\n")
    git(repo, "add", "-f", "in.txt")
    r = run(repo, "--staged")
    assert r.returncode == 1 and "binary-content" in r.stdout
    git(repo, "reset", "-q")
    put(repo, "out.txt", _pad(8192) + b"\x00\n")
    git(repo, "add", "-f", "out.txt")
    assert run(repo, "--staged").returncode == 0


def test_xxd_three_groups_not_refused_four_refused(repo: Path) -> None:
    def lines(g: int) -> str:
        return "".join(f"{i * 8:08x}: " + " ".join(f"{(i + k) % 256:02x}{k:02x}" for k in range(g)) + "\n"
                       for i in range(51))
    put(repo, "three.txt", lines(3))
    git(repo, "add", "three.txt")
    assert run(repo, "--staged").returncode == 0
    git(repo, "reset", "-q")
    put(repo, "four.txt", lines(4))
    git(repo, "add", "four.txt")
    r = run(repo, "--staged")
    assert r.returncode == 1 and "listing" in r.stdout


def test_generic_binary_nul_only_after_8k_and_valid_utf8_passes(repo: Path) -> None:
    put(repo, "a.txt", _pad(8192) + b"\x00\n")
    put(repo, "b.txt", b"\xef\xbb\xbfhello \xc3\xa9\n")  # BOM + e-acute
    git(repo, "add", "-f", "a.txt", "b.txt")
    assert run(repo, "--staged").returncode == 0


def test_generic_binary_exemption_and_its_limits(repo: Path) -> None:
    put(repo, "assets/ours.dat", RANDOM)
    put(repo, "config/large_files.txt", f"{hashlib.sha256(RANDOM).hexdigest()} assets/ours.dat\n")
    git(repo, "add", "-f", "-A")
    assert run(repo, "--staged").returncode == 0
    # same path, other content: refused again
    put(repo, "assets/ours.dat", RANDOM + b"x")
    git(repo, "add", "-f", "assets/ours.dat")
    assert run(repo, "--staged").returncode == 1


@pytest.mark.parametrize("path", ["build/x.dat", "a.wav", "b.ppm", "c.bin", "game.3ds"])
def test_generic_exemption_does_not_cover_other_rules(repo: Path, path: str) -> None:
    put(repo, path, RANDOM)
    put(repo, "config/large_files.txt", f"{hashlib.sha256(RANDOM).hexdigest()} {path}\n")
    git(repo, "add", "-f", "-A")
    assert run(repo, "--staged").returncode == 1


def test_capture_magic_not_exempt_with_listing(repo: Path) -> None:
    data = b"MGPU" + RANDOM
    put(repo, "x.dat", data)
    put(repo, "config/large_files.txt", f"{hashlib.sha256(data).hexdigest()} x.dat\n")
    git(repo, "add", "-f", "-A")
    r = run(repo, "--staged")
    assert r.returncode == 1 and "magic (capture" in r.stdout


def _hexdump_c(i: int) -> str:
    return f"{i * 16:08x}  " + " ".join(f"{(i + k) % 256:02x}" for k in range(8)) + "  " + \
        " ".join(f"{(i + k) % 256:02x}" for k in range(8, 16)) + "  |................|"


def _xxd(i: int) -> str:
    return f"{i * 16:08x}: " + " ".join(f"{(i + k) % 256:02x}{(i + k + 1) % 256:02x}" for k in range(0, 16, 2)) + "  ................"


def _xxd_short(i: int) -> str:
    return f"{i * 16:04x}: " + " ".join(f"{(i + k) % 256:02x}" for k in range(8))


def _objdump(i: int) -> str:
    return f"   {0x8000 + i * 4:x}:\te1a00000 \tmov\tr0, r0"


def _thumb(i: int) -> str:
    return f"{0x8000 + i * 2:8x}:\t4770      \tbx\tlr"


def _carray(i: int) -> str:
    return "    " + ", ".join(f"0x{(i + k) % 256:02X}" for k in range(16)) + ","


def _ghidra(i: int) -> str:
    return f"        {0x8000 + i * 4:08x} 00 00 a0 e1     mov        r0,r0"


def _ghidra_seg(i: int) -> str:
    return f"ram:{0x8000 + i * 4:08x} 00 00 a0 e1 mov r0, r0"


def _ida(i: int) -> str:
    return f"ROM:{0x8000 + i * 4:08X} 00 00 A0 E1  MOV R0, R0"


def _gdb(i: int) -> str:
    return f"0x{0x8000 + i * 4:08x}: e1a00000 mov r0, r0"


def _prefix_addr(i: int) -> str:
    return f"{0x8000 + i * 4:08x} <foo+0x{i * 4:x}>:\te1a00000\tmov\tr0, r0"


def _byte_groups(i: int) -> str:
    return f"{0x8000 + i * 4:x}:\t00 00 a0 e1 \tnop"


def _no_colon(i: int) -> str:
    return f"{0x8000 + i * 4:08x}  e1a00000  mov r0, r0"


def _xxd_four_groups(i: int) -> str:
    return f"{i * 8:08x}: " + " ".join(f"{(i + k) % 256:02x}{(i + k + 1) % 256:02x}" for k in range(4))


LISTINGS = {
    "ghidra": _ghidra, "ghidra-seg": _ghidra_seg, "ida": _ida, "gdb": _gdb,
    "objdump-prefix": _prefix_addr, "objdump-bytes": _byte_groups, "no-colon": _no_colon,
    "xxd-4-groups": _xxd_four_groups,
    "hexdump-c": _hexdump_c, "xxd": _xxd, "xxd-short": _xxd_short,
    "objdump": _objdump, "objdump-thumb": _thumb, "c-array": _carray,
}


@pytest.mark.parametrize("kind", sorted(LISTINGS))
@pytest.mark.parametrize("n,refused", [(49, False), (50, False), (51, True)])
@pytest.mark.parametrize("mode", ["staged", "range"])
def test_listing_boundary(repo: Path, kind: str, n: int, refused: bool, mode: str) -> None:
    text = "\n".join(LISTINGS[kind](i) for i in range(n)) + "\n"
    if mode == "staged":
        put(repo, "notes.txt", text)
        git(repo, "add", "-f", "notes.txt")
        r = run(repo, "--staged")
    else:
        r = run(repo, "--range", commit_range(repo, {"notes.txt": text}))
    assert r.returncode == (1 if refused else 0), r.stdout + r.stderr
    if refused:
        assert "listing" in r.stdout


def test_listing_c_array_in_header_and_mixed_lines_count_together(repo: Path) -> None:
    text = "\n".join([_carray(i) for i in range(26)] + [_objdump(i) for i in range(25)]) + "\n"
    put(repo, "gen.h", text)
    git(repo, "add", "gen.h")
    assert run(repo, "--staged").returncode == 1


def test_listing_c_array_15_entries_is_not_a_line(repo: Path) -> None:
    line = "    " + ", ".join(f"0x{k:02X}" for k in range(15)) + ",\n"
    put(repo, "t.h", line * 80)
    git(repo, "add", "t.h")
    assert run(repo, "--staged").returncode == 0


def test_listing_hex_needs_eight_byte_groups(repo: Path) -> None:
    line = "00000000: " + " ".join(f"{k:02x}" for k in range(7)) + "\n"
    put(repo, "t.txt", line * 80)
    git(repo, "add", "t.txt")
    assert run(repo, "--staged").returncode == 0


def test_listing_exemption_via_list(repo: Path) -> None:
    text = "\n".join(_carray(i) for i in range(60)) + "\n"
    put(repo, "icon.h", text)
    put(repo, "config/large_files.txt", f"{hashlib.sha256(text.encode()).hexdigest()} icon.h\n")
    git(repo, "add", "-A")
    assert run(repo, "--staged").returncode == 0


@pytest.mark.parametrize("alphabet,ok", [
    ("QUJD" * 256, False),            # 1024 base64 characters
    ("QUJD" * 255 + "QUJ", True),    # 1023
    ("0123456789abcdef" * 64, False),  # hex
    ("QUJD" * 255 + "QUJ ", True),   # space inside the budget: not only base64 characters
    ("QUJD" * 256 + "=" , False),
])
@pytest.mark.parametrize("mode", ["staged", "range"])
def test_dense_blob(repo: Path, alphabet: str, ok: bool, mode: str) -> None:
    text = "header\n" + alphabet + "\nfooter\n"
    if mode == "staged":
        put(repo, "blob.txt", text)
        git(repo, "add", "blob.txt")
        r = run(repo, "--staged")
    else:
        r = run(repo, "--range", commit_range(repo, {"blob.txt": text}))
    assert r.returncode == (0 if ok else 1), r.stdout + r.stderr
    if not ok:
        assert "dense-blob" in r.stdout


@pytest.mark.parametrize("line,ok", [
    ("QUJD" * 256 + " ", False),
    ('"' + "QUJD" * 256 + '"', False),
    ("data = " + "QUJD" * 256, False),
    ('{"k": "' + "QUJD" * 256 + '"},', False),
    ("QUJD" * 255 + "QUJ" + " more text here", True),
    ("x = " + "QUJD" * 255 + "QUJ", True),
])
def test_dense_blob_embedded_in_a_line(repo: Path, line: str, ok: bool) -> None:
    put(repo, "blob.h", "header\n" + line + "\n")
    git(repo, "add", "blob.h")
    r = run(repo, "--staged")
    assert r.returncode == (0 if ok else 1), r.stdout
    if not ok:
        assert "dense-blob" in r.stdout


def test_dense_blob_crlf_line(repo: Path) -> None:
    put(repo, "blob.txt", ("QUJD" * 300 + "\r\n").encode())
    git(repo, "add", "blob.txt")
    assert run(repo, "--staged").returncode == 1


def test_legit_sources_mentioning_hex_pass(repo: Path) -> None:
    doc = "# Format\n\nHeader bytes are `0x7F 0x45` then 00000000: 7f 45 4c 46.\n" * 10
    py = "MASK = 0xFF\nTABLE = [0x00, 0x01, 0x02]\n# 8000: e1a00000 mov r0, r0\n" * 10
    cpp = "static const u8 k[] = {0x00, 0x01, 0x02, 0x03};\n" * 100
    put(repo, "docs/a.md", doc)
    put(repo, "tools/a.py", py)
    put(repo, "src/a.cpp", cpp)
    git(repo, "add", "-A")
    r = run(repo, "--staged")
    assert r.returncode == 0, r.stdout + r.stderr


# --- N0-19 round 2 fixes -----------------------------------------------------------------

def _rules(found: list[tuple[str, str]]) -> str:
    return " ".join(r for _, r in found)


def test_allowlisted_invalid_utf8_file_still_gets_trace_rule() -> None:
    data = "".join(svc_line(i) for i in range(60)).encode() + b"\xff"
    digest = hashlib.sha256(data).hexdigest()
    found = check_bytes("x.dat", data, {"x.dat": digest})
    assert found and all(r.startswith("trace") for _, r in found)
    # Not allowlisted: refused as binary content as well.
    assert "binary-content" in _rules(check_bytes("x.dat", data))
    # Allowlisted with 50 trace lines only: fine.
    ok = "".join(svc_line(i) for i in range(50)).encode() + b"\xff"
    assert check_bytes("x.dat", ok, {"x.dat": hashlib.sha256(ok).hexdigest()}) == []


def test_dense_blob_reached_only_with_padding() -> None:
    assert check_bytes("a.txt", b"A" * 1020 + b"====\n")
    assert check_bytes("a.txt", b"A" * 1020 + b"===\n") == []


def test_disassembly_with_eight_byte_groups() -> None:
    line = "00008000: " + " ".join(["e1"] * 8) + " mov r0, r0\n"
    assert check_bytes("d.txt", (line * 51).encode())
    assert check_bytes("d.txt", (line * 50).encode()) == []


def test_lone_byte_is_not_a_mnemonic() -> None:
    assert check_bytes("d.txt", ("00008000 e1 mov r0,r0\n" * 51).encode())
    assert check_bytes("d.txt", ("00008000 e1\n" * 51).encode()) == []
