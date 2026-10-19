# Roadmap

The end goal is a native PC executable built from decompiled source, running with assets from the user's own dump. There are two well-trodden routes to a PC port; this project starts on shared groundwork and decides the main route at the end of Phase 1.

| Route | How it works | Pros | Cons |
|---|---|---|---|
| **A. Source decompilation** (functionally equivalent C/C++) | Rewrite each function in C/C++ that behaves like the original; compile for x86-64 | Real, editable source; best for mods and long-term maintenance | Slow: a game this size is many thousands of functions |
| **B. Static recompilation** (N64Recomp / XenonRecomp style) | Mechanically translate ARM machine code to C, then compile for PC; hand-write only the OS/GPU layer | Playable much sooner | Output is not human-readable; harder to modify |

A hybrid is common: recompile everything first (B) to get a running port, then replace recompiled functions with hand-decompiled ones (A) over time.

> **Byte-matching** decompilation (rebuilding an identical `code.bin`) would require the exact proprietary compiler the game was built with (most likely ARM's RVCT/armcc for 3DS titles). It is **not** a goal for now; *functional equivalence* is.

---

## Phase 0 — Bootstrapping ✅
- [x] Repository, legal guidelines, `.gitignore` for dumps
- [x] BLZ (backward LZ77) decompressor for `code.bin`
- [x] Exheader parser and `code.bin → ELF` converter for disassemblers
- [x] Extractor for decrypted .3ds / .cia / .cxi (`tools/extract_ncch.py`)
- [x] Verify against a real dump (USA v1.3.0 update); record title ID + hashes in `config/versions.json`
- [x] Target version: **USA v1.3.0** (code from the update title)

## Phase 1 — Mapping the binary
*How work is split across parallel agents: [`AGENT_WORKFLOW.md`](AGENT_WORKFLOW.md).*
- [x] Ghidra export script (`tools/ghidra/`): function list, call graph, strings, per-function pseudo-C → `build/export/` (git-ignored)
- [x] Second discovery pass for vtable-only code (`tools/find_missing_functions.py`): 25,872 → 31,409 functions, 80% → 89.7% of .text
- [x] `tools/difftest.py`: compile candidate C for armv6k, link against original addresses, compare with original in Unicorn; first verified function: `crc32_string`
- [x] Status table `config/functions.tsv` (`tools/build_function_table.py`, `tools/fnstatus.py`)
- [x] Worker prompt template `docs/prompts/worker.md`
- [x] Pilot batch of ~10 leaf functions; measure pass rate and cost
- [x] Ghidra project setup guide; shared function/symbol list in text form (`config/symbols.txt`)
- [ ] Identify 3DS SDK/system library code (svc wrappers, `nn::` services, libc, math) — these get replaced, not decompiled
- [x] Recover the MT Framework class registry: 992 classes with hierarchy, sizes, ids (`tools/mt_dti.py`, `config/mt_classes.tsv`)
- [ ] Identify engine layers (Capcom's MT Framework Mobile): memory, file/archive loading, rendering, sound, task/scheduling
- [ ] Document RomFS formats used by the game (`.arc` archives and their contents) — lots of public community knowledge exists from modding
- [x] **Decision point:** hybrid. Static recompilation first (route B) to get a running port, hand-decompiled functions (route A) where we need to change behaviour (widescreen, 60 fps, HUD). See [PORT_ARCHITECTURE.md](PORT_ARCHITECTURE.md).
- [x] Static recompiler for the whole game (`tools/recompile.py`, [RECOMPILER.md](RECOMPILER.md)): 39,697 functions, 3.2 M instructions, every jump table resolved; verified per instruction (fuzzing) and per function (random-state differential tests) against an ARM11 model

## Phase 2 — Running *something* on PC
- [x] Licence: GPL-3.0-or-later for the whole repository (the port reuses Azahar's GPL-3.0 core)
- [x] Recompiled-code CPU backend for Azahar's core (`port/`, `Core::ARM_Interface`): paged memory, SVCs into Azahar's kernel, cycle accounting, one host fiber per guest thread. A multi-threaded test program prints the same results as with Azahar's JIT
- [x] The game's own code boots like it does on Azahar's JIT (SDK start-up, services, static constructors) until it opens its RomFS
- [x] Frontend: Azahar's own Qt interface loads the code library for the game it opens (no separate SDL frontend needed); Windows build in CI, run under Wine with a code library built by llvm-mingw (into gameplay, OpenGL, widescreen); a Linux AppImage (tried on Ubuntu) and a macOS app for Apple silicon (not tried on a Mac yet) in CI too
- [x] `tools/build_game.py`: the user's dump to an installed code library in one command (fetches the compiler: llvm-mingw on Windows, Zig's C compiler on Linux and macOS when clang isn't installed)
- [x] Hook points: native code around chosen game functions (`config/hooks.txt`, `port/src/hooks.cpp`); headless runner with input scripts and screenshots for automated runs of the real game
- [x] Run the game with dynarmic and with the recompiled code side by side and diff them: same screens through first boot, intro movie (the hand-written video decoder needed new analysis patterns), title, character creation and the opening on the sand ship
- [x] Boot to title screen, and into gameplay, at 60 fps; in-game save and load work

## Phase 3 — Playable
- [x] Two-screen layout handling (side by side, swappable, touch screen via mouse): Azahar's layouts, plus the port's touch-screen overlay
- [x] Audio (DSP): Azahar's HLE DSP; sounds right in play on Windows
- [ ] Save data compatibility with real 3DS saves
- [x] Gameplay loop: village → quest → hunt (played on Windows)

## Phase 4 — PC niceties
- [x] Research: frame pacing (the engine is delta-time based; frame limiter in `sMain`), widescreen (projection, render targets, culling, HUD anchoring), second screen (viewport 7, layout views, touch panels) — [`research/`](research/README.md)
- [x] 60 fps: frame-rate cap hook, verified on the real game (frame delta 1/60 s, game speed unchanged); find what holds it at 30 on hardware
- [x] Above 60: 120 fps (delta 1/120 s, movies stay at their own 30; the quest-start loading race it exposes is worked around), played on Windows in hunts
- [ ] Widescreen: anamorphic (projection hook + Azahar single-screen aspect) and true wide rendering (render targets, transfer, tunable 400s; up to 1024x240: 16:9, 21:9, 32:9) work in gameplay; the hunting HUD's sides go to the screen's edges (`hud_edges`, by layout object); next, the quest HUD
- [ ] Second screen: Azahar layouts (available), the custom layout as a touch-screen overlay in a corner of a full-window game (`touch_overlay`), kick and target camera on buttons, and in quests the map alone in that corner (`quest_overlay`, by the player's panel layout) (done); next, the item shortcuts and the other panels on buttons
- [x] Screen filters: bloom, depth of field (it blurs whole cutscenes in emulation), motion and radial blur and heat haze can each be turned off (`bloom`, `depth_of_field`, ... in `mh4u.ini`; [research/screen-filters.md](research/screen-filters.md))
- [x] Resolution scaling, texture packs, rebindable controls and controllers: Azahar's
- [ ] Multiplayer: Azahar's network rooms carry MH4U's local play; runners (`--room-host`, `--room-join`, `--lockstep`) meet in a Gathering Hall and go on a quest together: two (also with one on Azahar's own CPU emulation), three and four (after fixing Azahar's node ids for consoles that join at the same moment); net sync keeps the port's consoles together through a PC's stalls; two players in an Arena quest with preset gear; next a hunt with the monster in sight

## Phase 5 — The port's own app *(current)*
The game's code is native already; what remains of the 3DS is Azahar's core (system services, PICA200 graphics, DSP audio, network rooms), used as libraries. This phase replaces Azahar's *interface* with the port's own and builds the PC features on it; parts of the core get replaced only where they are in the way.
- [x] `mh4u` app (`port/app/`): an SDL2 window on Azahar's core that opens MH4U directly, full screen, with the port's code library and options, Vulkan / OpenGL / software renderers (falling back in that order), the keyboard and a controller at once (the controller mapped on its own, remapped when plugged in), the mouse as the stylus, rooms joined or hosted from its settings (`mh4u-app.ini`); built for Windows, Linux and macOS in CI and shipped in the packages (`mh4u.exe`, `mh4u.AppImage`, `MH4U.app`)
- [x] Servers of our own ([SERVER.md](SERVER.md)): a lobby (`server/lobby/mh4u_lobby.py`) compatible with Azahar's lobby client, so *Browse Public Game Lobby* and public rooms work again, checked end to end against Azahar's dedicated room; host scripts and Docker for dedicated rooms; a room's host rights only for the host's checked name
- [x] Mods ([MODS.md](MODS.md)): a mod library with order, conflicts and deployment into Azahar's mods folder (`tools/mods.py`, JSON for the app), code mods tied to the code library; the port's Azahar reads `romfs_ext/` sub-folders, where MH4U's archives are
- [x] Metal: MoltenVK now, a native backend only on a measured trigger ([research/metal.md](research/metal.md))
- [x] In-game menu (Dear ImGui over the game, on Vulkan, OpenGL and software): the options of `mh4u.ini` and `mh4u-app.ini` (live where the hooks and Azahar allow), controls with rebinding, the room and its players, quitting; the 3DS software keyboard (names, chat) in the app
- [x] Launcher (`port/app/launcher/`, its own window with an SDL renderer): Home with *Play*; first-run Setup (the game file checked, the v1.3.0 update installed from its `.cia`, the code library built with `build_game.py` and its progress shown); Mods (`tools/mods.py`); Online (a lobby's room list, join by address, host); Settings; keyboard and controller. Checked in the cloud with the maintainer's files: setup all green, then the game on Vulkan and OpenGL with the menu over it
- [x] Rooms while the game runs: join, host and leave from the menu's *Multiplayer* tab without starting again, invite codes (`mh4u://HOST:PORT?password=...`) with Copy and Paste, a lobby's rooms in the menu, Rejoin last room; a late join checked with two instances in the cloud (room and local-wireless traffic both ways)
- [x] Quest HUD: the map anywhere at any size, the target camera panel in a view of its own (whom the camera targets; a click on it is a tap), *Edit HUD layout* to drag and resize both over the game; checked on an expedition with Vulkan and OpenGL
- [x] Mod catalog in the launcher (`tools/mods_catalog.json`): the HD texture pack and four more, installed with one click (downloads resumed and checked, `.zip`/`.7z`/`.tar`); code mods with a code cave recompiled into the library
- [x] A frame-rate counter (F3, off by default); the touch screen's *Skip* after a quest on a button with a hint on screen (`skip`; tried by the maintainer)
- [x] The game's second-eye pass off (`stereo_3d = off`), which doubled and softened the picture (v0.1.1); `skip_end`: ZL+ZR held ends the wait after a quest through `aQuest::playing`, online once every port player has held it, with the players still to ask on the screen
- [x] The touch screen's *Skip* (the scene of the monster's end) with the weapon drawn: the panel manager's button 0xb, tapped through the hit test inside processTouch as the stylus is (research/second-screen.md); tried by the maintainer with the weapon out (v0.1.2)
- [x] The app's own icon (`port/app/icon/mh4u.svg`, an original emblem: a hexagonal badge with three claw marks; `tools/icon.py` renders the .ico, .icns, .png and the window icon's pixels): the Windows executable's resources, the macOS bundle, the AppImage and the SDL windows
- [ ] The second screen gone: the touch screen only when the game needs it (`touch_screen = map/hidden`), the bottom pass skipped while hidden, item shortcuts on buttons, a controller cursor for touch menus
- [x] CPU clock speed (Azahar's) in the app's settings, live; the game's anti-aliasing switchable (`antialiasing`), for frame rate at high resolutions
- [ ] Measure on Apple silicon (checklist in research/metal.md); native Metal only if MoltenVK can't hold the frame rate

## Prior art to study
- N64Recomp + Zelda64Recomp, XenonRecomp + UnleashedRecomp — static recompilation to PC
- zeldaret's decomp projects and decomp.me — workflow, tooling, conventions
- 3DS emulators (Citra lineage: Azahar, etc.) — reference for OS service HLE and PICA200 behavior (mind their licenses if reusing code)
- 3dbrew wiki — file formats, system services, hardware docs
