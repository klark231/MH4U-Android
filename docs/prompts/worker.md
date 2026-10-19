# Worker prompt: decompile one function

The orchestrator fills in `{…}` and sends this to a decompiler agent. Keep it in sync with `docs/AGENT_WORKFLOW.md`.

---

You are decompiling functions of Monster Hunter 4 Ultimate (Nintendo 3DS, ARM11/ARMv6K, compiled by ARM's compiler) into C that behaves identically to the original. Repository: `{repo}`. The maintainer's own game dump is already in `{repo}/baserom/` (never copy, print in bulk, or commit it).

**Your functions:** {functions}  (address, size, pointer-arg hint from the selector)

For each function:

1. Look at it: `python3 tools/disasm.py <address>`. Literal loads are resolved and known names (`config/symbols.txt`, MT classes) are annotated.
2. Work out the arguments (registers r0–r3 read before written; floats in s0–s15), the return type (r0, r0:r1, s0/d0 or void), and what it does.
3. Write clean, idiomatic C in a **new** file `src/unsorted/func_<address8>.c`:
   - `#include "types.h"`; name the function `func_<address8>` (e.g. `func_0010ec6c`).
   - Globals the code touches: declare them `extern` with a placeholder name `g_<address8>` (or `data_<address8>` for tables), and **report** them so the orchestrator can add them to `config/symbols.txt`. Until then, test with `--sym g_<address8>=0x<address>` (see below).
   - Prefer readable code (loops, structs, named fields) over transliterated assembly. Struct layouts you infer go in a comment at the top of your file; do not edit shared headers.
   - A top comment: address, one-line description, difftest result.
4. Verify: `python3 tools/difftest.py <address> src/unsorted/func_<address8>.c --func func_<address8> --args <n> --ret <int|i64|void|float|double> [--fargs <n>] [--sym name=0xADDR ...]`
   Iterate until it prints `PASS` with at least 300 compared cases. Maximum **8 attempts** per function.
5. If you can't get a PASS: stop and report `blocked` with the reason (indirect call, input generator can't drive it, needs a callee name, …). **Never** weaken the test, special-case inputs, or embed the original's machine code.

Tips (from pilot-1):
- The pointer-arg hint can be wrong. If an argument is clearly a scalar, pass `--ptr-args` yourself (e.g. `--ptr-args 0`).
- Tables in `.bss` (0x01081b84–0x0111d128) are filled at runtime and are all zero under difftest. Add `--random-region <addr>:<size>` so the interesting paths really get tested.
- Function sizes from Ghidra are too short for functions ending in a jump table (`ldr pc, [pc, rN, lsl #2]`). If the disassembly stops mid-table, rerun `disasm.py` with a larger `--size` and report the real size.
- Integer compares against bytes/halfwords loaded from memory are often against the full 32-bit argument; type such args as `u32`/`s32` so values above 255 behave like the original.

Rules:
- Only create your own new files. Don't edit `config/`, `include/`, docs, tools or other workers' files; don't commit or push.
- Don't paste large disassembly or binary data into files.

**Reply format** (one block per function, nothing else):

```
function: 0x________
status: done | blocked
file: src/unsorted/func_________.c
difftest: PASS n/n (skipped k), args=…, ret=…, ptr_args=[…]
proposed_name: <name or ->
globals: <address name type> … or -
summary: <one sentence on what it does>
notes: <blocker reason, struct guesses, anything odd>
```
