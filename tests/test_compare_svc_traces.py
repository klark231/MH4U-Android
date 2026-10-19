"""tools/compare_svc_traces.py on synthetic traces."""
import os
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "tools"))

LINES = [
    "1 21 pc=00107328 lr=00106790 0FFFFFE0 00000000 00000000 00000000 t=10",
    "2 24 pc=00200000 lr=00200100 00000001 00000000 00000000 00000000 t=15",
    "1 2B pc=001091E0 lr=001078BC 0FFFFFE8 FFFF8001 00000014 00000001 t=20",
    "1 32 pc=00106B6C lr=00106A00 00000005 00000000 00000000 00000000 t=30",
]


def write(tmp_path, name, lines):
    p = tmp_path / name
    p.write_text("\n".join(lines) + "\n")
    return str(p)


def test_interleaving_and_ticks_dont_matter(tmp_path):
    import compare_svc_traces as c
    ref = write(tmp_path, "ref.txt", LINES)
    # other thread order and other tick counts
    cand_lines = [LINES[0].replace("t=10", "t=11"), LINES[2], LINES[1], LINES[3].replace("t=30", "t=99")]
    cand = write(tmp_path, "cand.txt", cand_lines)
    assert c.compare(c.parse(ref), c.parse(cand)) == []
    assert c.main([ref, cand]) == 0


def test_first_difference_per_thread(tmp_path):
    import compare_svc_traces as c
    ref = write(tmp_path, "ref.txt", LINES)
    bad = list(LINES)
    bad[2] = bad[2].replace("00000014", "00000015")             # thread 1, call 1: other argument
    cand = write(tmp_path, "cand.txt", bad[:3])                  # and thread 1 stops early
    diffs = c.compare(c.parse(ref), c.parse(cand))
    assert [(d["thread"], d["index"]) for d in diffs] == [("1", 1)]
    assert diffs[0]["ref"].args[2] == "00000014" and diffs[0]["cand"].args[2] == "00000015"
    assert c.compare(c.parse(ref), c.parse(cand), with_args=False) != []          # still shorter
    assert c.compare(c.parse(ref), c.parse(cand), with_args=False, prefix=True) == []
    assert c.main([ref, cand]) == 1
