# Notes for AI-assisted sessions

Project: decompilation of Monster Hunter 4 Ultimate (3DS, ARM11 / ARMv6K) aiming for a native PC port. Read `docs/ROADMAP.md` for the current phase.

Hard rules:
- Never add game data (ROM, code.bin, exheader, RomFS files, keys) to the repo, and never help obtain unowned copies. Work only from the maintainer's own dump in `baserom/` (git-ignored).
- Tools are Python 3.10+, standard library first; every tool change needs a test in `tests/` using synthetic data.
- Run `python3 -m pytest` before committing.
- Multi-agent work follows `docs/AGENT_WORKFLOW.md`: only the orchestrator edits shared files (`config/*`, `include/*`); workers write their own new files and return results. A function counts as decompiled only when `difftest` passes; report `blocked` rather than weakening a test.
- Ghidra exports (`build/export/`) are derived from game code: read them, never commit them.
- The 100% native port (no Azahar) lives on branch `native`. On any other branch, for anything about it, read its runbook first, `git fetch origin native && git show FETCH_HEAD:.claude/skills/native-orchestrate/SKILL.md`, and follow it (it sets the branch up). Never commit native-port work to `main`.

Key facts:
- `code.bin` from ExeFS is usually BLZ (backward LZ77) compressed; exheader flag bit 0 at offset 0x0D says so. `tools/blz.py` handles it.
- Segment addresses come from the exheader SCI (text/ro/data/bss). `tools/codebin2elf.py` turns them into an ELF for Ghidra (`ARM:LE:32:v6`).
- Engine is Capcom's MT Framework Mobile (verify details against the binary before relying on them).
