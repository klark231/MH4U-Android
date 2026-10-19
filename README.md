# MH4UltimateDC

A work-in-progress decompilation of **Monster Hunter 4 Ultimate** (Nintendo 3DS), with the long-term goal of a **native PC port**.

> 🇲🇽 *Proyecto de decompilación de Monster Hunter 4 Ultimate (3DS) con el objetivo de llegar a un port nativo para PC. La documentación está en inglés para abrirlo a la comunidad; las discusiones en español son bienvenidas.*

## Status

🟩 **Playable; Phase 5 — the port's own app.** Target version is **USA v1.3.0**.

- A **static recompiler** translates the whole game (~40,000 functions, 3.2 M ARM/Thumb instructions) into C, generated on your machine from your own dump and verified against an ARM11 model ([`docs/RECOMPILER.md`](docs/RECOMPILER.md)).
- That code runs as the CPU of **Azahar's 3DS core** (kernel, services, GPU, audio, network rooms), inside Azahar's normal interface, on Windows, Linux and macOS ([`docs/PORT_ARCHITECTURE.md`](docs/PORT_ARCHITECTURE.md), [`port/`](port/)). The village, quests and hunts are played on Windows at 60 and 120 fps, with sound.
- **Hooks** wrap game functions with native code, based on research into the engine's frame pacing, cameras and second screen ([`docs/research/`](docs/research/README.md)):
  - 60 and 120 fps, and a fix for a loading race that higher frame rates bring out;
  - true widescreen (16:9, 21:9, 32:9) with the hunting HUD at the screen's edges, and 16:10 or 4:3 without distortion;
  - no dither grid when upscaling, the game's second-eye pass (stereoscopic 3D) off for a sharp picture, and the game's screen filters (bloom, the focus blur of cutscenes, motion and radial blur) each switchable;
  - the kick, the target camera and the *Skip* after a quest on buttons, and the touch screen as an overlay in a corner of a full-window game (in quests, just the map, movable and resizable, with the target camera panel in a view of its own).
- **Local multiplayer** goes over Azahar's network rooms, also with players on official Azahar: two to four players meet in a Gathering Hall and go on quests together, at different frame rates if they like, and the port keeps them together when a PC stalls.
- **The port's own app**, `mh4u`: opens the game directly in a window of its own (no emulator interface), full screen, Vulkan, OpenGL or software, keyboard and controller at once, the touch screen as an overlay, rooms joined and hosted from its menu while the game runs (with invite codes), a mod catalog with the HD texture pack, a frame-rate counter; in the Windows, Linux and macOS packages next to the port's Azahar ([`port/app/`](port/app/), [`docs/ROADMAP.md`](docs/ROADMAP.md) Phase 5).
- **Servers of our own** for online play: a lobby server compatible with Azahar's lobby client (public room lists work again) and scripts/Docker for dedicated rooms ([`docs/SERVER.md`](docs/SERVER.md)).
- **Community mods**: a mod manager with load order and conflicts ([`docs/MODS.md`](docs/MODS.md)); code mods (`code.ips`, `code.bps`) are applied before recompiling; data and RomFS mods work as on Azahar.
- Where the port needs to change behaviour, functions also get hand-decompiled into readable C, each one verified with `tools/difftest.py`.

How to play it: [`docs/PLAYING.md`](docs/PLAYING.md). Common questions: [`FAQ.md`](FAQ.md). The plan: [`docs/ROADMAP.md`](docs/ROADMAP.md).

## ⚠️ This repository contains no game data

This project **does not** and **will not** include the ROM, `code.bin`, RomFS assets, encryption keys, or any other copyrighted material from Capcom or Nintendo. To build or work on it you must dump **your own** copy of the game. See [`docs/LEGAL.md`](docs/LEGAL.md) and [`docs/SETUP.md`](docs/SETUP.md).

Pull requests that add copyrighted binaries or assets will be rejected and purged from history.

## Quick start

```bash
# 1. Python tooling (3.10+)
pip install -r requirements.txt

# 2. Place your own decrypted dump's ExeFS code + exheader in baserom/  (see docs/SETUP.md)
#    baserom/exheader.bin
#    baserom/code.bin      (compressed or decompressed, both work)

# 3. Produce an ELF for your disassembler
python3 tools/codebin2elf.py --exheader baserom/exheader.bin --code baserom/code.bin -o build/mh4u.elf

# 4. Run the tool tests
python3 -m pytest

# 5. Recompile the game into C (build/recomp/, never committed) and build it
python3 tools/recompile.py --exheader baserom/exheader.bin --code baserom/code.bin -o build/recomp
(cd tools && python3 -m recomp.build ../build/recomp --harness --opt O0 -D RC_CHECK_RETURNS)

# Or, to play: the update .cia straight to an installed code library (see docs/PLAYING.md)
python3 tools/build_game.py --dump "MH4U v1.3.0 update.cia" --install --fetch-compiler
```

## Layout

| Path | Purpose |
|---|---|
| `tools/` | Python tooling (BLZ decompression, exheader parsing, code.bin → ELF, difftest, static recompiler in `tools/recomp/`) |
| `config/` | Per-region version info (hashes, title IDs) — filled in as dumps are verified |
| `src/`, `include/` | Hand-decompiled C, each function verified with `tools/difftest.py` |
| `port/` | The PC port: a recompiled-code CPU backend for Azahar's 3DS core, the hooks on game functions, a headless runner for tests, and the app (`port/app/`) |
| `server/` | Online play: a lobby server and dedicated-room scripts ([`docs/SERVER.md`](docs/SERVER.md)) |
| `docs/` | Roadmap, setup, legal, research notes |
| `baserom/`, `build/` | **Git-ignored.** Your dump and build output |

## Licence

[GPL-3.0-or-later](LICENSE), for everything in this repository: the tools, the port and the docs. The port builds on [Azahar](https://github.com/azahar-emu/azahar), which is GPL-3.0, and the same licence lets code move freely between the two. It covers only what this project writes: the game itself stays Capcom's and is never part of the repository or of any build we distribute.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). Research notes (file formats, struct layouts, function names) are as valuable as code at this stage.
