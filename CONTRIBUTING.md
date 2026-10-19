# Contributing

Thanks for helping! Read [`docs/LEGAL.md`](docs/LEGAL.md) first — the one non-negotiable rule is **no copyrighted game data in the repo**. Discussions in English or Spanish are both fine.

## Where help is most useful right now
- **Playing and reporting bugs**, with your log (see [Reporting a bug](#reporting-a-bug)).
- **Testing on hardware we haven't tried:** Steam Deck, other Linux distributions, Apple silicon Macs.
- **Hosting rooms** for other players ([`docs/SERVER.md`](docs/SERVER.md)).
- **Reverse engineering:** function and struct names, MT Framework Mobile classes and resource formats, file-format notes → `docs/research/`. Notes are as valuable as code.
- **The native runtime** (branch `native`, no Azahar inside): 3DS services (HLE), DSP audio, the PICA200 GPU. Read [`docs/native/README.md`](https://github.com/CompaLarios18/MH4UltimateDC/blob/native/docs/native/README.md) on that branch first and open an issue before starting, so work isn't done twice.
- **Tooling** (with tests) and the port's app (`port/app/`).
- **Mods for the catalog:** open an issue with the mod's page, its licence and the author's permission to be listed (see below).

## What may never be committed, pasted in an issue, or posted in a PR
- The ROM, `code.bin`, the exheader, RomFS files (`.arc`, models, textures, audio, text), keys or firmware.
- Anything derived from them: Ghidra/IDA exports and pseudo-C, disassembly listings, hex dumps, traces, GPU/DSP captures, screenshots of decompiler output, the recompiler's generated C (`build/recomp/`).
- Code from leaked SDKs or other proprietary sources.
- Links to, or hints about, where to get the game. Everyone works from their own dump (GodMode9).

Short excerpts in a research note (a few instructions to explain a function) are fine; whole functions or listings are not. When in doubt, ask in an issue first. CI checks every commit of every PR (`tools/check_no_game_data.py`), but that is a safety net, not a licence.

## Mod catalog entries
`tools/mods_catalog.json` is fetched live by every player's app, so it is reviewed with extra care:
- the download must be the author's own link, and the entry must say what licence the author gives;
- every download of a `mod` needs its `sha256` (the tools refuse it otherwise), and a texture pack should have one too;
- URLs point at a fixed version (a release or a commit), never at a branch.

## Reporting a bug
- Close the game first (the log is written in a buffer), then attach `mh4u_log.txt` from Azahar's log folder: `%APPDATA%\Azahar\log` on Windows, the `log` folder inside Azahar's user folder elsewhere.
- **Remove personal details before posting:** your user name in folder paths, and any room invite (`mh4u://…`), which contains your IP address and the room's password.
- Say which package and version you use, your OS, CPU and GPU, and what you did just before the problem.

## Security problems
Don't open a public issue for a security problem (in the app, the lobby or room server, or the mod downloads): see [`SECURITY.md`](SECURITY.md).

## Conventions
- Python tools: 3.10+, standard library preferred, tests in `tests/` with synthetic data only (`python3 -m pytest`).
- Addresses are written as `0x00123456` (8 hex digits, lowercase `0x`).
- Commit messages: imperative mood, short subject line (`Add exheader parser`, not `added stuff`).
- By contributing you agree your work is licensed under the project's licence (GPL-3.0-or-later).

## Before opening a PR
- `python3 -m pytest` passes.
- `python3 tools/check_no_game_data.py --staged` (or `--range origin/main..HEAD`) prints `ok`.
- `git status` shows no files from `baserom/`, `build/`, or any game data.
