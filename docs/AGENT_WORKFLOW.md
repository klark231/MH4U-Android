# Multi-agent workflow

How this project uses several Claude agents in parallel without drowning in plausible-looking but wrong code.

## The one rule

**Agents only get work that has an automatic pass/fail check.** A decompiled function that "looks right" is worthless; one that behaves identically to the original on thousands of inputs is progress. Every role below is defined by its *oracle*: the thing that says "done" without a human reading every line.

| Oracle | What it proves | Who runs it |
|---|---|---|
| `difftest` | Candidate C behaves like the original ARM code (same return values, same memory writes) on randomized + recorded inputs, executed in an ARM emulator (Unicorn) | Worker agents, in a loop, before returning |
| Build + unit tests | Code compiles, tools work | Workers locally, CI on every PR |
| Independent review | Names, types and structure make sense; tests aren't gamed | A reviewer agent that did **not** write the code, then the maintainer per batch |

### Why difftest works without the original compiler
Byte-matching needs ARM's proprietary compiler, so we aim for *functional equivalence* instead. The candidate is compiled with clang for `armv6k` and **linked against the original binary**: every callee and runtime helper (e.g. `__aeabi_idiv`, which ARMv6 needs for division) resolves to its real address in the game's `code.bin`. Both versions then run in Unicorn from the same CPU/memory state and their results are compared.

A proof of concept (Oct 2026) confirmed the toolchain works in a cloud session: a correct reimplementation matched 2000/2000 random cases; a version with a subtle integer-rounding change matched only 32/2000 and was rejected.

## Data flow

```
 Maintainer's PC                         Cloud session (orchestrator + agents)           GitHub (public)
 ────────────────                        ─────────────────────────────────────           ───────────────
 cartridge ─► GodMode9 dump               baserom/  (staged from PC, never committed)
 code.bin + exheader ─► codebin2elf ─►    build/export/  (Ghidra text export, ignored)
 Ghidra (GUI + headless, export script)   ──► agents read pseudo-C, xrefs, strings
                                          src/, include/, config/symbols.txt  ─────────►  PRs (one per batch)
                                          difftest results ─► PR description             CI: build + unit tests
```

- **Ghidra runs on the maintainer's machine**, not in the cloud (the cloud sandbox can't download Ghidra, and analysis wants lots of RAM). A script in `tools/ghidra/` exports text the agents consume: function list (address, size, name), call graph, string references, and per-function pseudo-C.
- **Exports and dumps stay git-ignored.** Ghidra pseudo-C is a mechanical derivative of the game's code; only hand-written, difftest-verified C goes into `src/`.
- **Public CI never sees the game**, so it can't run difftests. Workers run them in the session and paste the summary into the PR.

## Roles

| Role | Input | Output | Oracle | Parallel? |
|---|---|---|---|---|
| **Orchestrator** (main session, with the maintainer) | Status table, call graph | Batches of tasks, merged results, updated `config/symbols.txt` | — | 1 only: the **single writer** of shared files |
| **Triage** | Address range + strings + instruction patterns | Category per function: `sdk` / `runtime` / `engine` / `game` / `unknown` | Spot-check by orchestrator | Yes, split by address range |
| **Namer** | One function's pseudo-C, callers, callees, strings | Proposed name + signature + confidence | Reviewer agreement | Yes (read-only, cheap) |
| **Decompiler worker** | One function (or tight cluster) + known types | `src/…` file that passes `difftest` | `difftest` | Yes: 5–10 at a time, each in its own git worktree |
| **Reviewer** | A finished batch (diff + difftest logs) | Approve / list of problems | Fresh eyes; never reviews its own work | Yes, one per batch |
| **Format researcher** | RomFS files from the maintainer's dump + public community docs | `docs/research/<format>.md`, parsers in `tools/` with synthetic tests | Parser round-trips real files locally | Yes, independent of the code track |
| **Port engineer** (Phase 2+) | List of OS services / GPU commands the game uses | `port/` code | Unit tests, later boots-to-title | A few, by subsystem |

`sdk` and `runtime` functions are **not decompiled**: during testing they're linked to the originals, and in the PC port they're replaced by the platform layer. Finding them early is the biggest single time saver.

## Work units and order

- **Unit = one function**, or a small cluster that only makes sense together. Small units give a clear pass/fail and cheap retries.
- **Bottom-up through the call graph.** Leaves first: once callees have names and types, their callers become much easier.
- **Pilot before scaling.** The first batch is ~10 leaf functions. Measure pass rate, retries per function, time and token cost, then tune the worker prompt before running bigger batches.
- **One batch = one branch = one PR.** The maintainer reviews per batch, not per function.

## Shared state (single-writer rule)

Workers never edit shared files. They return their result (source file + proposed names/types + difftest summary) and the orchestrator merges it. This avoids merge conflicts between parallel worktrees.

| File | Contents | Writer |
|---|---|---|
| `config/functions.tsv` | `address  size  category  name  status  batch` for every function | Orchestrator (generated from the Ghidra export, then updated) |
| `config/symbols.txt` | Accepted names for functions and globals; imported back into Ghidra by script | Orchestrator |
| `include/*.h` | Recovered structs/enums | Orchestrator, after review |
| `src/<subsystem>/*.c` | Decompiled code | Workers (their own new files only) |

Status lifecycle: `unknown → triaged → named → decompiled (difftest pass) → reviewed → merged`, plus `blocked` with a reason.

## Worker contract

Each decompiler worker gets the same prompt template (to be kept in `docs/prompts/worker.md`):

1. Inputs: address, Ghidra pseudo-C, disassembly, known callee signatures, relevant headers.
2. Write the function in clean C (C++ only where vtables/classes demand it) in a new file under `src/`.
3. Run `tools/difftest.py <address> <file>` and iterate until it passes, **up to a fixed attempt limit**.
4. If it can't pass, stop and return `blocked` with the reason (system call, indirect call it can't resolve, input generation too weak, …). **Never** weaken the test to make it pass.
5. Return: file path, proposed names/types, difftest summary, open questions.

## Where agents run

- **Interactive:** the orchestrator session spawns workers with worktree isolation; the maintainer watches and decides.
- **Overnight:** a scheduled task can run a batch and open a PR for morning review. It gets the dump by staging it from the maintainer's linked computer, so the dump never leaves that machine except into the short-lived session. **Don't put the dump in any repository, public or private.**

## Plan

| Step | Work | Who | Parallel? |
|---|---|---|---|
| 0 | Dump the cartridge, run `verify_dump.py`, build the ELF, load in Ghidra and run auto-analysis | Maintainer | — |
| 1 | **Critical path:** Ghidra export script, `difftest` tool (from the PoC), status/symbol tooling, worker prompt template | Orchestrator (one agent) | No |
| 2a | Triage the whole binary; find SDK/runtime code | Triage agents | Yes |
| 2b | RomFS format research (`.arc` and contents) | Format researchers | Yes, can start as soon as the RomFS is dumped |
| 3 | Pilot batch (~10 leaf functions) → measure → tune | Workers + reviewer | Yes |
| 4 | Scale batches; decide route A / B / hybrid ([ROADMAP](ROADMAP.md)) using pilot data | Everyone | Yes |

## Pilot results

**pilot-1** (Oct 2026): 10 leaf functions (0x28–0x70 bytes) chosen by `tools/select_batch.py` from 1,841 candidates. 5 workers ran in parallel, 2 functions each.

| Metric | Result |
|---|---|
| Passed difftest | **10 / 10** (re-verified by the orchestrator with a new seed, 1,000 cases each) |
| Attempts | 9 functions passed on the first run, 1 needed 3 runs (only extra checks) |
| Wall time | 75–183 s per worker; the whole batch took about 3 minutes |
| Cost | ~450k subagent tokens in total, **~45k tokens per function** |
| Code quality | Readable C with structs and named fields. No embedded machine code, no special-cased inputs |

Findings that changed the tools:
- `disasm.py` stopped decoding after jump-table data. Fixed: it now resumes after undecodable words.
- Tables in `.bss` are all zero under difftest, so match paths were barely exercised. Added `difftest --random-region`, with a test proving it catches bugs the zeroed version misses.
- Ghidra underestimates the size of functions that end in jump tables. Noted per function; the worker prompt explains how to spot it.
- Workers recognised a compiler runtime helper (`__aeabi_lasr`) and recovered a real method name from an error string (`PacketAnalysisData::CreateIndex`).

What this means for the route decision:
- For small leaf functions the pipeline works and is fast. Bigger functions with calls, C++ classes and floating point will cost more per function.
- At ~45k tokens per function, hand-decompiling all ~31k functions is not realistic as a first goal. This argues for the **hybrid route**: static recompilation for the bulk, and agent decompilation focused on what the port needs to understand or change (file loading, rendering, input, save data), plus whatever blocks the recompiler.

## Known risks

- **Weak test inputs.** Random integers don't exercise pointer-heavy code well. Mitigation: per-type input generators, and later real inputs recorded from an emulator (Azahar) trace.
- **C++ and indirect calls** (vtables, function pointers) need type recovery before workers can handle them, so they go after leaves.
- **Floating point.** The ARM11 VFP must be emulated faithfully; compare floats bit-exactly and flag functions that depend on rounding modes.
- **Cost.** Each worker uses tokens; the pilot sets the batch size we can afford.
- **Agent overconfidence.** Difftest + an independent reviewer, and the rule that workers report `blocked` instead of forcing a pass.
