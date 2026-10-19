"""tools/recomp/build.py: incremental builds keep objects of different settings apart."""
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "tools"))

pytestmark = pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")


def objects(tmp_path):
    return sorted(o for o in os.listdir(tmp_path / "obj") if o.endswith(".o"))


def test_objects_per_build_variant(tmp_path):
    from recomp import build as rbuild
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.c").write_text("#ifdef VARIANT\nint variant = 1;\n#endif\nint a(void) { return 1; }\n")
    rbuild.build(str(tmp_path), 1, "O0", [], "one.so", [])
    rbuild.build(str(tmp_path), 1, "O0", [], "two.so", [], extra_flags=["-DVARIANT"])
    objs = objects(tmp_path)
    assert len(objs) == 2 and all(o.startswith("a-O0-") for o in objs)
    stamp = {o: os.path.getmtime(tmp_path / "obj" / o) for o in objs}
    rbuild.build(str(tmp_path), 1, "O0", [], "one.so", [])        # nothing to recompile
    assert {o: os.path.getmtime(tmp_path / "obj" / o) for o in objs} == stamp


def test_header_date_does_not_matter_its_contents_do(tmp_path):
    """Unpacking a new package gives recomp.h a new date: with the same contents nothing is
    recompiled; changed contents recompile everything, under the same object names."""
    from recomp import build as rbuild
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.c").write_text("int a(void) { return 1; }\n")
    header = tmp_path / "recomp.h"
    header.write_text("/* v1 */\n")
    rbuild.build(str(tmp_path), 1, "O0", [], "one.so", [], runtime_header=str(header))
    objs = objects(tmp_path)
    assert len(objs) == 1
    stamp = os.path.getmtime(tmp_path / "obj" / objs[0])
    future = stamp + 3600
    os.utime(header, (future, future))                                   # a copy: new date, same text
    rbuild.build(str(tmp_path), 1, "O0", [], "one.so", [], runtime_header=str(header))
    assert objects(tmp_path) == objs
    assert os.path.getmtime(tmp_path / "obj" / objs[0]) == stamp          # nothing recompiled
    os.remove(tmp_path / "obj" / "recomp.h.sha1")                        # objects from before the stamp
    rbuild.build(str(tmp_path), 1, "O0", [], "one.so", [], runtime_header=str(header))
    assert os.path.getmtime(tmp_path / "obj" / objs[0]) == stamp          # kept, and stamped now
    assert (tmp_path / "obj" / "recomp.h.sha1").exists()
    header.write_text("/* v2 */\n")                                        # changed contents
    os.utime(header, (stamp - 3600, stamp - 3600))                       # even with an old date
    rbuild.build(str(tmp_path), 1, "O0", [], "one.so", [], runtime_header=str(header))
    assert objects(tmp_path) == objs
    assert os.path.getmtime(tmp_path / "obj" / objs[0]) > stamp           # recompiled
