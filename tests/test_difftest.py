"""difftest end-to-end on a synthetic "game" compiled with clang (no game data).

Skipped when clang / ld.lld / llvm tools are not installed.
"""
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "tools"))

needed = ["clang", "ld.lld", "llvm-nm", "llvm-objcopy"]
pytestmark = pytest.mark.skipif(any(shutil.which(t) is None for t in needed),
                                reason="clang/lld toolchain not installed")

import difftest as dt  # noqa: E402

BASE = 0x00100000

ORIGINAL = r"""
typedef struct { int hp, max_hp, def; } Monster;
int counter;
const unsigned char weights[4] = {1, 2, 4, 8};

int clamp(int v, int lo, int hi) { return v < lo ? lo : v > hi ? hi : v; }

int apply_damage(Monster *m, int raw) {
    int dmg = raw * 100 / (100 + m->def);
    m->hp = clamp(m->hp - dmg, 0, m->max_hp);
    counter++;
    return dmg;
}

unsigned weigh(unsigned x) { return weights[x & 3] * x; }

int table[8];
int find(int k) { for (int i = 0; i < 8; i++) if (table[i] == k) return i; return -1; }
"""

GOOD = r"""
typedef struct { int hp, max_hp, def; } Monster;
extern int counter;
int clamp(int v, int lo, int hi);
int apply_damage(Monster *m, int raw) {
    int dmg = (raw * 100) / (m->def + 100);
    m->hp = clamp(m->hp - dmg, 0, m->max_hp);
    counter += 1;
    return dmg;
}
"""

FORGETS_COUNTER = GOOD.replace("counter += 1;", "")
WRONG_ROUNDING = GOOD.replace("(raw * 100) / (m->def + 100)", "raw / (m->def + 100) * 100")

WEIGH_GOOD = r"""
extern const unsigned char weights[4];
unsigned weigh(unsigned x) { return x * weights[x % 4]; }
"""


@pytest.fixture(scope="module")
def game(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("game")
    src, obj, rt, elf, binp = (tmp / n for n in ("game.c", "game.o", "rt.o", "game.elf", "game.bin"))
    src.write_text(ORIGINAL)
    subprocess.run(["clang", *dt.CFLAGS, "-c", str(src), "-o", str(obj)], check=True)
    subprocess.run(["clang", *dt.CFLAGS, "-c", dt.RT_SOURCE, "-o", str(rt)], check=True)
    script = tmp / "link.ld"
    script.write_text(dt.LINKER_SCRIPT.replace(f"0x{dt.CAND_BASE:08x}", f"0x{BASE:08x}"))
    subprocess.run(["ld.lld", "-T", str(script), "-e", "clamp", str(obj), str(rt), "-o", str(elf)], check=True)
    subprocess.run(["llvm-objcopy", "-O", "binary", str(elf), str(binp)], check=True)
    syms = {}
    for line in subprocess.run(["llvm-nm", "--defined-only", str(elf)], capture_output=True, text=True,
                               check=True).stdout.splitlines():
        addr, _, name = line.split()
        syms[name] = int(addr, 16)
    code = binp.read_bytes()
    return code, syms, tmp


def check(game, source, func, nargs, cases=300):
    code, syms, tmp = game
    path = tmp / f"cand_{abs(hash(source))}.c"
    path.write_text(source)
    link_syms = {n: a for n, a in syms.items() if n != func}
    cand = dt.build_candidate(str(path), func, link_syms, [])
    return dt.compare(code, BASE, BASE + len(code) + 0x1000, syms[func], False, cand,
                      nargs, 0, "int", cases, seed=7)


def test_equivalent_candidate_passes_and_pointer_arg_is_detected(game):
    rep = check(game, GOOD, "apply_damage", 2)
    assert rep.ok, rep.mismatches
    assert rep.ptr_slots == (0,)
    assert rep.compared == 300


def test_missing_global_write_is_caught(game):
    rep = check(game, FORGETS_COUNTER, "apply_damage", 2)
    assert not rep.ok
    assert any("memory differs" in p for m in rep.mismatches for p in m["problems"])


def test_wrong_rounding_is_caught(game):
    rep = check(game, WRONG_ROUNDING, "apply_damage", 2)
    assert not rep.ok


def test_reads_original_const_data(game):
    rep = check(game, WEIGH_GOOD, "weigh", 1)
    assert rep.ok, rep.mismatches
    assert rep.ptr_slots == ()


def test_unknown_callee_is_reported(game):
    code, syms, tmp = game
    path = tmp / "unknown.c"
    path.write_text("int mystery(int); int f(int x) { return mystery(x) + 1; }")
    with pytest.raises(dt.DifftestError, match="mystery"):
        dt.build_candidate(str(path), "f", {}, [])


def test_runtime_division_helpers(game, tmp_path):
    """The bundled runtime must agree with C semantics for / and %, 32 and 64 bit."""
    code, syms, _ = game
    src = tmp_path / "div.c"
    src.write_text(r"""
int f(int a, int b) {
    if (b == 0) return 0;
    long long x = (long long)a * 1000003LL, y = (long long)b;
    return (a / b) ^ (a % b) ^ (int)(x / y) ^ (int)(x % y) ^ (int)((unsigned)a / (unsigned)b);
}""")
    cand = dt.build_candidate(str(src), "f", {}, [])
    m = dt.Machine(code, BASE, BASE + len(code) + 0x1000, cand)
    import random
    rng = random.Random(3)
    for _ in range(200):
        a, b = rng.randint(-2**31, 2**31 - 1), rng.choice([rng.randint(-50, 50), rng.randint(-2**31, 2**31 - 1)])
        r = m.run(cand.entry, False, [a & 0xFFFFFFFF, b & 0xFFFFFFFF], [], b"\0" * (dt.BUF_SLOT * dt.BUF_COUNT))
        assert r.fault is None

        def cdiv(n, d):
            q = abs(n) // abs(d)
            return q if (n < 0) == (d < 0) else -q

        if b == 0:
            expected = 0
        else:
            x, y = a * 1000003, b
            q64, q32 = cdiv(x, y), cdiv(a, b)
            expected = q32 ^ (a - q32 * b) ^ (q64 & 0xFFFFFFFF) ^ ((x - q64 * y) & 0xFFFFFFFF) \
                ^ ((a & 0xFFFFFFFF) // (b & 0xFFFFFFFF))
        assert r.regs["r0"] == expected & 0xFFFFFFFF, (a, b)


FIND_BUG_ONLY_FOR_NONZERO = r"""
extern int table[8];
int find(int k) { for (int i = 0; i < 8; i++) if (table[i] == k) return k ? i + 1 : i; return -1; }
"""


def test_random_region_exposes_bugs_hidden_by_zeroed_bss(game):
    code, syms, tmp = game
    path = tmp / "find_bug.c"
    path.write_text(FIND_BUG_ONLY_FOR_NONZERO)
    cand = dt.build_candidate(str(path), "find", {"table": syms["table"]}, [])
    end = BASE + len(code) + 0x1000
    zeroed = dt.compare(code, BASE, end, syms["find"], False, cand, 1, 0, "int", 300, seed=5)
    assert zeroed.ok                       # all-zero table: the bug is invisible
    randomized = dt.compare(code, BASE, end, syms["find"], False, cand, 1, 0, "int", 300, seed=5,
                            random_regions=[(syms["table"], 32)])
    assert not randomized.ok
