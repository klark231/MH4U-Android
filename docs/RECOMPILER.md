# Static recompiler

`tools/recompile.py` turns all ARM and Thumb code in the game's `code.bin` into C that runs natively on PC. The C is generated on the user's machine from their own dump. It's derived from game code, so it is **never committed** (`build/` is git-ignored).

```
python3 tools/recompile.py --extra build/export/missing2.txt -o build/recomp     # ~80 s
cd tools && python3 -m recomp.build ../build/recomp --harness --opt O0 -D RC_CHECK_RETURNS -o game.so
python3 -m recomp.gametest --sample 2000 --cases 3 --report ../build/recomp/sample.tsv
```

## Pipeline

1. **Entry points.** `config/functions.tsv` (Ghidra plus the second discovery pass), extra lists (`--extra`), the static constructors in RVCT's relative init table, and code addresses found in `.rodata`/`.data` (vtables, callback tables) or loaded from literal pools (function pointers passed as arguments). Numbers that merely look like code addresses are weeded out: the target must decode into clean code, must not be reached by falling through from the instruction before it, must not overlap code of the other instruction set, and Thumb targets must sit near known Thumb code. Entries outside `.text` are dropped.
2. **Decoding** (`recomp/arm.py`, `recomp/thumb.py`). Each instruction becomes an `Insn`: a kind (plain op, branch, call, return, indirect jump, jump table, SVC, trap) and a list of C statements over the register file. Covered: ARMv6K (data processing, multiplies, saturating and parallel arithmetic, media instructions, LDREX/STREX family, CP15 TLS registers), VFPv2, Thumb-1. UNPREDICTABLE encodings become traps.
3. **Function analysis** (`recomp/analysis.py`). Follows the control flow from each entry:
   - branches to another entry are tail calls, others are local labels;
   - jump tables: `add pc, pc, rI, lsl #2`, `ldr pc, [pc, rI, lsl #2]`, `adr` + `ldr pc, [rB, rI, lsl #2]`, with the bound found from the guarding `cmp` (or `and` + `subs` + `bhs`);
   - `mov lr, pc` / `add lr, pc, #n` followed by a jump are calls that return to that constant, and a conditional jump after it is a conditional call (the game's video decoder sets LR once, then picks a routine with `beq`/`bne` or a table jump);
   - offset tables (`ldr rX, [rB, rI, lsl #2]`, or `[rB, rI]` with the index already scaled, + `add pc, rX, rB`), with signed offsets (tables placed after the routines they reach) and unused zero slots;
   - `add rX, pc, #a` (+ `add rX, rX, #b`) then `mov pc, rX`/`bx rX`: the constant target becomes a function too (the jump itself is still dispatched at run time);
   - FPSCR vector length and stride are tracked through `vmsr`, so VFP short-vector code (LEN=3 in two audio mixers) is expanded per element;
   - RVCT floating-point helpers that read a descriptor word after their call site and continue at one of several places in the caller are handled as multi-return calls;
   - code that falls into a function of the other instruction set stops there (the call before it doesn't return);
   - every function knows its return address (LR at entry, or the caller's own when code runs into the next function without a call or tail-calls it), so a computed jump to it is a return (Thumb's `pop {r3}; bx r3`). Tail calls pass it on (`expect_next`), as LR can hold something else by then: the decoder's block routines are entered right after a `bl` and return by popping their caller's frame.
   New call targets are analysed too, until the set is closed.
4. **Emission** (`recomp/emit.py`). One C function per guest function, `goto` for local control flow, `switch` for jump tables, a cycle charge per basic block (`RC_TICK`). Direct calls become direct C calls; anything else goes through `rc_call`.
5. **Build** (`recomp/build.py`). Parallel, incremental clang build into a shared library (or object files for the port).

## Runtime contract (`tools/recomp/runtime/recomp.h`)

Generated code only touches `RCtx` (core registers, split NZCVQ/GE flags, VFP registers and FPSCR, TLS registers, exclusive monitor, cycle budget) and guest memory. Memory is either one flat mapping (`ctx->mem + addr`, used by the tests) or, with `RC_PAGED`, a table of host pointers per 4 KiB page, as used by Azahar (`PageTable::pointers`), with `rc_slow_read`/`rc_slow_write` for pages that need special handling.

The host provides:

| Callback | Called for |
|---|---|
| `rc_call(ctx, target)` | indirect calls and jumps (function pointers, virtual calls, unknown targets) |
| `rc_svc(ctx, imm, next_pc)` | `svc` (system calls) |
| `rc_trap(ctx, addr, kind)` | undefined/unsupported instructions, bad jump-table indices, jumps into the middle of nowhere |
| `rc_bad_return(ctx, got, expected)` | with `RC_CHECK_RETURNS`: a function returned somewhere other than its caller |
| `rc_budget(ctx)` | the cycle budget ran out (yield point for the scheduler) |

### Hook points

Functions listed in `config/hooks.txt` are hook points: the generated `f_<addr>` checks `rc_hooks[i]` and, if a hook is installed, calls it with the guest context and the original body (`fb_<addr>`). A hook can run the original, change registers or memory before or after it, or skip it (results in `r0`-`r3`, then `rc_hook_return`). Direct calls, indirect calls and the function table all go through the check, so a hook sees every call. The host installs hooks through `RCodeInfo.set_hook` when it loads a code library (`port/src/hooks.cpp`); the test harness has `rh_set_hook`. Other functions are generated exactly as before, so hook points cost nothing elsewhere.

### Tunables

Instructions listed in `config/tunables.txt` (address, original immediate, name) read their immediate operand from `rc_tunables[i]` instead of a constant; the array starts with the original values and the host reaches it through `RCodeInfo.tunables` (with the addresses and names). It is for numbers baked into the code that the port wants to change at run time, like the top screen's 400 rows for widescreen. A tunable whose instruction doesn't contain that immediate exactly once is reported as a problem.

## Performance

Generated functions keep guest registers and flags in local variables (so the C compiler can put them in host registers) and write them back to the `RCtx` only around calls, system calls, returns and yields. VFP arithmetic runs on the host FPU set up like FPSCR (rounding mode, flush-to-zero) and only NaN results take a slow path to get ARM's NaN. Indirect calls use a per-site inline cache. Each basic block charges its instruction count to the cycle budget (Azahar's JIT uses a per-instruction cycle table; the totals come out close).

On `port/tests/bench.c` under Azahar (same machine, same output), the recompiled code at `-O2` runs the integer, sorting, VFP and indirect-call phases 2-6x faster than Azahar's JIT, about 1.5x faster overall including the emulator's own per-frame work.

## Semantics worth knowing

- **Flags** are kept as separate 0/1 words; `rc_adds` implements ADC/SBC/CMP/CMN carry and overflow exactly.
- **VFP** follows ARM rules where C's differ: in RunFast mode (FPSCR.FZ and FPSCR.DN, which the game sets at startup) denormal inputs and outputs are flushed to signed zero and every NaN result is the default NaN; without DN, NaN propagation follows ARM operand priority. The rounding mode is FPSCR.RMode, applied to the host FPU (new 3DS threads start in round-towards-zero). Cumulative exception flags are not modelled.
- **`vmsr fpscr`** keeps the bits QEMU and dynarmic keep (`0xFFF7009F`).
- **Short vectors**: destination in bank 0 means scalar; second operand in bank 0 means a scalar broadcast; element registers wrap within their bank; elements are computed in order. Functions are assumed to start with LEN=0, as the procedure call standard requires; the analysis reports any call, return or fallthrough made in vector mode (there are none).

## Verification

There are three levels, all against Unicorn's ARM11 MPCore model:

1. **Instruction fuzzing** (`python3 -m recomp.fuzz --only {vfp,vfpvec,media,mul} | --mode thumb`): random encodings the translator accepts, each run from random states (special float values, denormals, all FZ/DN combinations, random vector lengths), comparing all registers, flags, VFP state and memory. Zero mismatches. Known reference bug: Unicorn gets double-precision VMOV/VABS/VNEG/VSQRT short vectors wrong, so those are fuzzed as scalars only.
2. **Synthetic programs** (`tests/test_recomp.py`): a C program built with clang (recursion, switch tables, function pointers, VFP, 64-bit division, ARM↔Thumb), plus hand-written assembly for short-vector code, a multi-return helper and the video decoder's computed calls and frame-ending tail jumps, recompiled whole and compared function by function.
3. **Whole-game differential test** (`python3 -m recomp.gametest`): every recompiled function, with everything it calls, from random states (registers, VFP, flags, pointer-rich scratch memory) on both sides. A case passes when both exit the same way (return, same system call, or fault at the same address) with identical registers, flags, VFP state and identical writable memory (`.data`, `.bss`, stack, buffers, TLS). Indirect targets that the native side couldn't find are written to `missing_targets.txt` and can be fed back with `--extra`.

Latest full run (October 2026, every function, 2 random states each): 13,401 pass, 26,361 fault at the same address on both sides, 798 inconclusive (instruction budget), 2 multi-return helpers tested through their callers, and 4 that fault at different addresses only after other tests have run (a different 4 each run; alone they agree, so harness state, not code). No function returns a different state.

Random states can't reach everything (most game functions fault early on random pointers), so the final check is running the game itself with the dynarmic JIT as reference (see [PORT_ARCHITECTURE.md](PORT_ARCHITECTURE.md)): from first boot to gameplay on the sand ship, screen for screen.

## Current numbers (USA v1.3.0)

| | |
|---|---|
| Functions | 40,566 (8,124 found through data pointers) |
| Instructions | 3,256,600 |
| Jump tables | 3,475, all resolved |
| Traps | 1 (data after a call that never returns) |
| Analysis problems | 0 |
| Generate / build | ~100 s / ~10 min at `-O0` on 2 cores (rebuilds only touch changed 64 KiB buckets) |
