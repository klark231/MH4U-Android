#!/usr/bin/env python3
"""Recompile the game's ARM code into C (the generated code is never committed).

usage: recompile.py [--exheader ...] [--code ...] [--functions config/functions.tsv]
                    [--extra build/export/missing2.txt] -o build/recomp
Writes <out>/src/funcs.h, <out>/src/fNNN.c, <out>/src/table.c and <out>/report.txt.
"""
from __future__ import annotations

import argparse
import bisect
import collections
import os
import struct
import sys
import time
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import build_function_table as bft  # noqa: E402
import codebin2elf  # noqa: E402
import exheader as exh  # noqa: E402
from recomp.analysis import Analyzer, Image  # noqa: E402
from recomp.emit import LOCALS_PREAMBLE, FunctionEmitter, fname  # noqa: E402

FILE_SHIFT = 16


def load_entries(functions_tsv: str, extra: list[str]) -> dict[int, str]:
    entries = {}
    for r in bft.read_tsv(functions_tsv):
        entries[int(r["address"], 16)] = r["mode"]
    for path in extra:
        with open(path) as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if line:
                    parts = line.split()
                    a = int(parts[0], 16)
                    mode = "thumb" if (a & 1 or (len(parts) > 1 and parts[1] == "thumb")) else "arm"
                    entries.setdefault(a & ~1, mode)
    return entries


def load_hooks(path: str | None) -> dict[int, str]:
    """Hook points (config/hooks.txt): `<address> <name>  # why` per line."""
    hooks: dict[int, str] = {}
    if not path or not os.path.exists(path):
        return hooks
    with open(path) as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line:
                parts = line.split()
                hooks[int(parts[0], 16)] = parts[1] if len(parts) > 1 else ""
    return hooks


def load_tunables(path: str | None) -> dict[int, tuple[int, str]]:
    """Tunables (config/tunables.txt): `<address> <original immediate> <name>  # why` per
    line: instructions whose immediate operand the host can change at run time."""
    tunables: dict[int, tuple[int, str]] = {}
    if not path or not os.path.exists(path):
        return tunables
    with open(path) as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line:
                parts = line.split()
                tunables[int(parts[0], 16)] = (int(parts[1], 0), parts[2] if len(parts) > 2 else "")
    return tunables


def apply_tunables(funcs: dict, tunables: dict[int, tuple[int, str]]) -> tuple[list[int], list[str]]:
    """Make each tunable instruction read its immediate from rc_tunables[i] (i in address
    order of the tunables that apply). Returns those addresses and the problems."""
    from recomp.insn import hexc
    found = sorted(a for a in tunables if any(a in f.insns for f in funcs.values()))
    index = {a: i for i, a in enumerate(found)}
    problems, applied = [], set()
    for f in funcs.values():
        for a, ins in f.insns.items():
            if a not in index:
                continue
            literal, var = hexc(tunables[a][0]), f"rc_tunables[{index[a]}]"
            if any(var in line for line in ins.body):
                applied.add(a)              # an instruction object shared by two functions
                continue
            if sum(line.count(literal) for line in ins.body) != 1:
                continue
            ins.body = [line.replace(literal, var) for line in ins.body]
            applied.add(a)
    for a in sorted(tunables):
        if a not in applied:
            problems.append(f"tunable 0x{a:08x} ({tunables[a][1]}): no instruction with immediate {tunables[a][0]}")
    return [a for a in found if a in applied], problems


def code_cover(funcs: dict) -> tuple[dict[str, set[int]], dict[str, set[int]]]:
    """Halfword addresses covered by decoded instructions, and addresses that some
    instruction falls through into, per instruction set."""
    em = FunctionEmitter({})
    cover = {"arm": set(), "thumb": set()}
    fall_into = {"arm": set(), "thumb": set()}
    for f in funcs.values():
        cv, fi = cover[f.mode], fall_into[f.mode]
        for a, ins in f.insns.items():
            cv.update(range(a, a + ins.size, 2))
            # calls may not return (assert handlers), so code after a call can be a function
            if em.falls_through(f, ins) and ins.kind not in ("bl", "icall") and a not in f.link_calls:
                fi.add(ins.next)
    return cover, fall_into


def near(sorted_addrs: list[int], a: int, distance: int) -> bool:
    i = bisect.bisect_left(sorted_addrs, a)
    return any(0 <= j < len(sorted_addrs) and abs(sorted_addrs[j] - a) <= distance for j in (i - 1, i))


def looks_like_arm(img: Image, a: int) -> bool:
    """Most ARM instructions are unconditional (condition field 0xE); in Thumb code a
    word with that top nibble is the exception."""
    words = [img.read32((a & ~3) + 4 * k) for k in range(-8, 8)]
    return sum(1 for w in words if w is not None and w >> 28 == 0xE) >= 10


NOPS = (0xE1A00000, 0xE320F000)     # mov r0, r0; nop


def tail_falls_into(img: Image, t: int) -> bool:
    """RVCT ends a function whose last act is to call the function right after it with
    `pop {..., lr}` (or `ldr lr, [sp], #4`) and an alignment `mov r0, r0`, and falls into it.
    That next function is still a function of its own, which vtables point to."""
    prev, before = img.read32(t - 4), img.read32(t - 8)
    if prev not in NOPS or before is None:
        return False
    pops_lr = (before & 0x0FFF0000) == 0x08BD0000 and before & 0x4000 and not before & 0x8000
    return bool(pops_lr) or (before & 0x0FFFFFFF) == 0x049DE004


def data_pointer_entries(img: Image, an: Analyzer, segments: list[tuple[int, int]],
                         known: dict[int, str], cover: dict[str, set[int]],
                         fall_into: dict[str, set[int]], literals: set[int] = frozenset()) -> dict[int, str]:
    """Code addresses stored in data (vtables, callback tables): words in .rodata/.data
    that point into .text at an instruction that starts clean, trap-free code. Words that
    are really numbers are weeded out: the target must not be reached by falling through
    from the instruction before it (unless that is the end of a function handing over to
    it, see tail_falls_into), and the code must not overlap code of the other instruction
    set."""
    found: dict[int, str] = {}
    thumb_entries = sorted(a for a, m in known.items() if m == "thumb")
    def values():
        for start, size in segments:
            for a in range(start, start + size - 3, 4):
                yield img.read32(a)
        yield from sorted(literals)     # constants loaded by code (function pointer arguments)

    for v in values():
        if v is None or not img.in_text(v & ~1):
            continue
        t, m = (v & ~1, "thumb") if v & 1 else (v, "arm")
        if (m == "arm" and t & 3) or t in known or t in found:
            continue
        if t in fall_into[m] and not (m == "arm" and tail_falls_into(img, t)):
            continue
        if m == "thumb" and (looks_like_arm(img, t) or not near(thumb_entries, t, 0x4000)):
            continue    # odd pointers into data; real Thumb code sits next to other Thumb code
        ins = an.decode(t, m)
        if ins.kind == "trap" or (m == "arm" and ins.word >> 28 != 0xE):
            continue
        f = an.analyze(t, m, known)
        if f.problems or any(i.kind == "trap" for i in f.insns.values()):
            continue
        other = cover["thumb" if m == "arm" else "arm"]
        if any(h in other for x, i in f.insns.items() for h in range(x, x + i.size, 2)):
            continue
        found[t] = m
    return found


INIT_LOOP = struct.pack("<5H", 0x6820, 0x1900, 0x4780, 0x1D24, 0x42AC)  # ldr r0,[r4]; adds r0,r4; blx r0; adds r4,#4; cmp r4,r5


def init_array_entries(img: Image) -> dict[int, str]:
    """Static constructors. RVCT's start-up code (`__cpp_initialize__aeabi_`, Thumb) walks a
    table of offsets relative to each entry, between two PC-relative bounds:
        ldr r4, =table-.; add r4, pc; ldr r5, =end-.; add r5, pc; b test
        loop: ldr r0, [r4]; adds r0, r0, r4; blx r0; adds r4, #4; test: cmp r4, r5; bne loop
    The data-pointer scan can't see these targets, so read the table here."""
    found: dict[int, str] = {}
    text = img.code[: img.text_size]
    pos = text.find(INIT_LOOP)
    while pos != -1:
        loop = img.base + pos
        bounds = {}
        for k in range(1, 8):                       # ldr rN, [pc, #i] / add rN, pc before the loop
            a = loop - 2 * k
            hw, nxt = img.read16(a), img.read16(a + 2)
            if hw is None or nxt is None:
                break
            if hw >> 11 == 0b01001 and (nxt & 0xFF78) == 0x4478 and (hw >> 8) & 7 == (nxt & 7) | ((nxt >> 4) & 8):
                reg = (hw >> 8) & 7
                lit = ((a + 4) & ~3) + (hw & 0xFF) * 4
                val = img.read32(lit)
                if val is not None and reg not in bounds:
                    bounds[reg] = (val + a + 2 + 4) & 0xFFFFFFFF   # add rN, pc: pc = add's address + 4
        if 4 in bounds and 5 in bounds and 0 < bounds[5] - bounds[4] < 0x100000:
            for e in range(bounds[4], bounds[5], 4):
                off = img.read32(e)
                if off is None:
                    break
                t = (e + off) & 0xFFFFFFFF
                if img.in_text(t & ~1):
                    found[t & ~1] = "thumb" if t & 1 else "arm"
        pos = text.find(INIT_LOOP, pos + 2)
    return found


def code_size(header: exh.ExHeader, code: bytes) -> int:
    """How much of the code segment to recompile, from its start: the code (.text), or, when
    something was put in the padding after it, up to the end of its last page (where the
    read-only data starts). Code mods put new code there (a "code cave") and branch to it
    from the code they patch, so it has to be recompiled with the rest. The game's own
    padding is zeros: for the game without such a mod the range, and so the generated code,
    stays exactly the same."""
    size = header.text.size
    end = min(header.text.padded_size, len(code))
    if header.ro.address > header.text.address:
        end = min(end, header.ro.address - header.text.address)
    return end if end > size and any(code[size:end]) else size


def recompile(img: Image, entries: dict[int, str], out: str, log=print,
              data_segments: list[tuple[int, int]] | None = None, title_id: int = 0,
              hooks: dict[int, str] | None = None,
              tunables: dict[int, tuple[int, str]] | None = None) -> dict:
    """`hooks`: function entries native code may wrap or replace (see RHookFn in recomp.h);
    each must also be an entry point. `tunables`: instructions whose immediate (the given
    original value) the host can change at run time (rc_tunables in recomp.h)."""
    t0 = time.time()
    hooks = hooks or {}
    an = Analyzer(img)
    inits = {a: m for a, m in init_array_entries(img).items() if a not in entries}
    if inits:
        entries = {**entries, **inits}
        log(f"  {len(inits)} static constructors from the init table")
    outside = [a for a in entries if not img.in_text(a)]
    if outside:
        entries = {a: m for a, m in entries.items() if img.in_text(a)}
        log(f"  ignoring {len(outside)} entry points outside .text")
    extra = {}
    if data_segments:
        first = an.analyze_all(entries)
        cover, fall_into = code_cover(first)
        known = {a: f.mode for a, f in first.items()}
        literals = {i.literal for f in first.values() for i in f.insns.values() if i.literal is not None}
        extra = data_pointer_entries(img, an, data_segments, known, cover, fall_into, literals)
        entries = {**entries, **extra}
        log(f"  {len(extra)} entry points found through data pointers")
    funcs = an.analyze_all(entries, progress=lambda n, m: log(f"  analysed {n}/{m}"))
    t1 = time.time()
    known = {a: f.mode for a, f in funcs.items()}
    tunable_addrs, tunable_problems = apply_tunables(funcs, tunables or {})
    hook_addrs = sorted(a for a in hooks if a in funcs)
    em = FunctionEmitter(known, {a: i for i, a in enumerate(hook_addrs)})
    src = os.path.join(out, "src")
    os.makedirs(src, exist_ok=True)

    def write_if_changed(path: str, text: str) -> None:
        if os.path.exists(path):
            with open(path) as fh:
                if fh.read() == text:
                    return
        with open(path, "w") as fh:
            fh.write(text)

    order = sorted(funcs)
    write_if_changed(os.path.join(src, "funcs.h"),
                     '#include "recomp.h"\n' + "".join(f"void {fname(a)}(RCtx *ctx);\n" for a in order))
    stats = collections.Counter()
    problems = []
    files = []
    # one file per 64 KiB of code, so adding or changing functions only rebuilds their file
    buckets = collections.defaultdict(list)
    for a in order:
        buckets[a >> FILE_SHIFT].append(a)
    for bucket, addrs in sorted(buckets.items()):
        parts = ['#include "funcs.h"\n' + LOCALS_PREAMBLE]
        for a in addrs:
            f = funcs[a]
            parts.append("\n".join(em.function(f)))
            stats["insns"] += len(f.insns)
            stats["traps"] += sum(1 for i in f.insns.values() if i.kind == "trap")
            stats["jump_tables"] += len(f.jtabs)
            problems += [f"{fname(a)}: {p}" for p in f.problems]
        path = os.path.join(src, f"f{bucket << FILE_SHIFT:08x}.c")
        write_if_changed(path, "\n".join(parts) + "\n")
        files.append(path)
    problems += [f"hook 0x{a:08x} ({hooks[a]}): not a function" for a in sorted(set(hooks) - set(hook_addrs))]
    problems += tunable_problems
    keep = {os.path.basename(p) for p in files} | {"funcs.h", "table.c"}
    for old in os.listdir(src):
        if old not in keep:
            os.remove(os.path.join(src, old))
    table = ['#include "funcs.h"', "", "const RFuncEntry rc_func_table[] = {"]
    table += [f"    {{0x{a:08x}u, {1 if funcs[a].mode == 'thumb' else 0}, {fname(a)}}}," for a in order]
    table += ["};", f"const u32 rc_func_count = {len(funcs)};",
              "/* the code this was recompiled from; runtimes check it before running */",
              f"const u32 rc_text_base = 0x{img.base:08x}u;",
              f"const u32 rc_text_size = 0x{img.text_size:08x}u;",
              f"const u32 rc_text_crc32 = 0x{zlib.crc32(img.code[:img.text_size]):08x}u;",
              f"const u64 rc_title_id = 0x{title_id:016x}ull;",
              "/* hook points (config/hooks.txt): set by the host through RCodeInfo.set_hook */",
              "const u32 rc_hook_addrs[] = {" + ", ".join(f"0x{a:08x}u" for a in hook_addrs or [0]) + "};",
              f"const u32 rc_hook_count = {len(hook_addrs)};",
              f"RHookFn rc_hooks[{max(1, len(hook_addrs))}];",
              "/* tunables (config/tunables.txt): immediates the host may change, RCodeInfo.tunables */",
              "u32 rc_tunables[] = {" + (", ".join(f"0x{tunables[a][0] & 0xFFFFFFFF:08x}u" for a in tunable_addrs) or "0") + "};",
              "const u32 rc_tunable_addrs[] = {" + (", ".join(f"0x{a:08x}u" for a in tunable_addrs) or "0") + "};",
              "const char *const rc_tunable_names[] = {" + (", ".join(f'"{tunables[a][1]}"' for a in tunable_addrs) or "0") + "};",
              f"const u32 rc_tunable_count = {len(tunable_addrs)};", ""]
    write_if_changed(os.path.join(src, "table.c"), "\n".join(table))
    t2 = time.time()
    report = {
        "functions": len(funcs), "discovered": an.discovered, "from_data_pointers": len(extra), "instructions": stats["insns"],
        "trap_instructions": stats["traps"], "jump_tables": stats["jump_tables"],
        "problems": len(problems), "c_files": len(files) + 1, "hook_points": len(hook_addrs),
        "tunables": len(tunable_addrs),
        "analysis_s": round(t1 - t0, 1), "emit_s": round(t2 - t1, 1),
    }
    with open(os.path.join(out, "report.txt"), "w") as fh:
        for k, v in report.items():
            fh.write(f"{k}: {v}\n")
        fh.write("\n".join(problems) + "\n")
    return {"report": report, "funcs": funcs, "files": files, "problems": problems}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--exheader", default=os.path.join(ROOT, "baserom", "update_v130", "exheader.bin"))
    ap.add_argument("--code", default=os.path.join(ROOT, "baserom", "update_v130", "code.bin"))
    ap.add_argument("--functions", default=os.path.join(ROOT, "config", "functions.tsv"))
    ap.add_argument("--extra", action="append", default=[os.path.join(ROOT, "config", "extra_entries.txt")],
                    help="more entry point lists (address [arm|thumb] per line)")
    ap.add_argument("--hooks", default=os.path.join(ROOT, "config", "hooks.txt"),
                    help="hook points: functions native code may wrap or replace")
    ap.add_argument("--tunables", default=os.path.join(ROOT, "config", "tunables.txt"),
                    help="instructions whose immediate the host can change at run time")
    ap.add_argument("--no-data-pointers", action="store_true", help="don't scan .rodata/.data for code pointers")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "build", "recomp"))
    args = ap.parse_args(argv)
    with open(args.exheader, "rb") as f:
        header = exh.parse(f.read())
    with open(args.code, "rb") as f:
        code = codebin2elf.load_code(header, f.read())
    img = Image(code, header.text.address, code_size(header, code))
    segs = None if args.no_data_pointers else [(header.ro.address, header.ro.size), (header.data.address, header.data.size)]
    title_id = struct.unpack_from("<Q", open(args.exheader, "rb").read(), 0x200)[0]   # ARM11 local caps: program id
    res = recompile(img, load_entries(args.functions, args.extra), args.out, data_segments=segs, title_id=title_id,
                    hooks=load_hooks(args.hooks), tunables=load_tunables(args.tunables))
    for k, v in res["report"].items():
        print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
