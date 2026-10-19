#!/usr/bin/env python3
"""Compile recompiled C (from tools/recompile.py) into a shared library.

usage: python3 -m recomp.build build/recomp [--harness] [-j N] [--opt O1] [-o game.so]
       python3 -m recomp.build build/recomp --code-library [--cc CC] [-o mh4u_code.so]

--harness links the differential-test harness (gametest). --code-library builds the
library the PC port loads with `mh4u_runner --code` (paged memory, callbacks from the
host); --cc picks another compiler, e.g. llvm-mingw's x86_64-w64-mingw32-clang for a
Windows .dll. Incremental: an object is reused while its source is older than it and
recomp.h has the contents it was compiled with (obj/recomp.h.sha1 remembers them, so a
copy of the tools with new dates, as unpacking a new package makes, recompiles nothing).
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME = os.path.join(HERE, "runtime")
HARNESS = os.path.join(HERE, "harness", "harness.c")
DLL_GLUE = os.path.join(RUNTIME, "rc_dll.c")
BASE_FLAGS = ["-c", "-fPIC", "-ffp-contract=off", "-frounding-math", "-fno-strict-aliasing", "-fwrapv", "-w",
              f"-I{RUNTIME}"]


def compile_one(cc: str, src: str, obj: str, flags: list[str]) -> tuple[str, float, str]:
    t = time.time()
    r = subprocess.run([cc, *flags, src, "-o", obj], capture_output=True, text=True)
    return src, time.time() - t, (r.stderr if r.returncode else "")


def build(outdir: str, jobs: int, opt: str, defines: list[str], lib_name: str, extra_sources: list[str],
          cc: str = "clang", extra_flags: list[str] | None = None, runtime_header: str | None = None) -> str:
    src_dir = os.path.join(outdir, "src")
    obj_dir = os.path.join(outdir, "obj")
    os.makedirs(obj_dir, exist_ok=True)
    # funcs.h only gains or loses declarations; a file whose calls change is rewritten itself.
    # recomp.h is in every object: when its contents differ from those the objects were
    # compiled with (obj/recomp.h.sha1), everything is recompiled; its date is not looked at,
    # since a copy of the tools (a new package unpacked) renews it without changing a thing
    with open(runtime_header or os.path.join(RUNTIME, "recomp.h"), "rb") as fh:
        header_hash = hashlib.sha1(fh.read()).hexdigest()
    stamp = os.path.join(obj_dir, "recomp.h.sha1")
    # no stamp yet (objects from before it was kept): they were built against the header
    # of their day, which the old date check kept current, so they stand and get the stamp
    header_changed = os.path.exists(stamp) and open(stamp).read().strip() != header_hash
    flags = [f"-{opt}", *BASE_FLAGS, f"-I{src_dir}", *[f"-D{d}" for d in defines], *(extra_flags or [])]
    sources = sorted(os.path.join(src_dir, f) for f in os.listdir(src_dir) if f.endswith(".c")) + extra_sources
    # objects are named after everything that changes the code, so builds with other
    # settings (harness, code library, another compiler) never reuse each other's objects
    variant = hashlib.sha1(" ".join([os.path.basename(cc), opt, *defines, *(extra_flags or [])]).encode()).hexdigest()[:8]
    todo, objs = [], []
    for s in sources:
        o = os.path.join(obj_dir, f"{os.path.basename(s)[:-2]}-{opt}-{variant}.o")
        objs.append(o)
        if header_changed or not os.path.exists(o) or os.path.getmtime(o) < os.path.getmtime(s):
            todo.append((s, o))
    if header_changed:
        # the other variants' objects were compiled against the old header too
        for name in os.listdir(obj_dir):
            if name.endswith(".o") and os.path.join(obj_dir, name) not in objs:
                os.remove(os.path.join(obj_dir, name))
    t0 = time.time()
    errors = []
    with cf.ThreadPoolExecutor(max_workers=jobs) as ex:
        futs = [ex.submit(compile_one, cc, s, o, flags) for s, o in todo]
        for i, fut in enumerate(cf.as_completed(futs), 1):
            src, dt, err = fut.result()
            if err:
                errors.append((src, err))
            if i % 20 == 0 or i == len(futs):
                print(f"  compiled {i}/{len(futs)} ({time.time() - t0:.0f}s)", flush=True)
    if errors:
        for src, err in errors[:5]:
            print(f"error in {src}:\n{err[-2000:]}", file=sys.stderr)
        raise SystemExit(1)
    with open(stamp, "w") as fh:
        fh.write(header_hash + "\n")
    lib = os.path.join(outdir, lib_name)
    # the object list goes in a response file: Windows limits a command line to 32 KiB
    rsp = os.path.join(outdir, "objects.rsp")
    with open(rsp, "w") as f:
        f.write("\n".join('"' + o.replace("\\", "/") + '"' for o in objs) + "\n")
    r = subprocess.run([cc, "-shared", f"@{rsp}", "-o", lib, *(["-lm"] if "mingw" not in cc else [])],
                       capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(r.stderr[-4000:])
    print(f"linked {lib} ({len(objs)} objects, {len(todo)} recompiled in {time.time() - t0:.0f}s)")
    return lib


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("outdir")
    ap.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 2)
    ap.add_argument("--opt", default="O1")
    ap.add_argument("--harness", action="store_true", help="link the test harness (Linux/macOS)")
    ap.add_argument("--code-library", action="store_true", help="build the PC port's code library")
    ap.add_argument("--cc", default="clang")
    ap.add_argument("-D", dest="defines", action="append", default=[])
    ap.add_argument("-o", "--lib", default="")
    args = ap.parse_args(argv)
    if args.code_library:
        lib = args.lib or ("mh4u_code.dll" if "mingw" in args.cc else "mh4u_code.so")
        build(args.outdir, args.jobs, args.opt, ["RC_PAGED", *args.defines], lib, [DLL_GLUE],
              cc=args.cc, extra_flags=["-fvisibility=hidden"])
    else:
        build(args.outdir, args.jobs, args.opt, args.defines, args.lib or "game.so",
              [HARNESS] if args.harness else [], cc=args.cc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
