# PC port architecture

Goal: play MH4U on any PC, with online multiplayer, enhanced graphics (including community mods), unlocked frame rate, any aspect ratio, and a proper replacement for the second (touch) screen. This document describes how the pieces fit, and which part of the work delivers which feature.

## The three layers

```
 ┌─────────────────────────────── MH4U-PC executable ───────────────────────────────┐
 │                                                                                  │
 │  Game code: ARM → C, generated at build time from the user's own dump            │
 │  (tools/recompile.py). Every function verified against an ARM11 model.           │
 │      │ calls/overrides                                                           │
 │      ├──────────► Hooks: hand-written C replacing specific game functions        │
 │      │            (widescreen camera, frame interpolation, HUD layout, ...)      │
 │      │ svc / memory / threads                                                    │
 │  ────▼─────────────────────────────────────────────────────────────────────────  │
 │  3DS platform (HLE): kernel, services (fs, hid, gsp, dsp, apt, nwm::UDS, ir, …),  │
 │  PICA200 GPU renderer, audio DSP — reused from Azahar's core libraries            │
 │  ────────────────────────────────────────────────────────────────────────────── │
 │  Frontend: window, screen layouts, input mapping, multiplayer lobby, settings    │
 └──────────────────────────────────────────────────────────────────────────────────┘
```

### 1. Game code: static recompilation (done, being verified)
`tools/recomp/` translates all ARM and Thumb code of `code.bin` into C (see [RECOMPILER.md](RECOMPILER.md)). The C is generated on the user's machine from their own dump and never committed. Output is exact at instruction level: each function's register, flag, VFP and memory effects match an ARM11 MPCore model.

### 2. Platform: Azahar's core, with our code as the CPU
Writing a 3DS operating system, GPU and audio implementation from scratch would take years. [Azahar](https://github.com/azahar-emu/azahar) (the continuation of Citra) already has them, with the features we need, as separate libraries (`core`, `video_core`, `audio_core`, `network`):

- CPU backends sit behind `Core::ARM_Interface` (`Run`, `Step`, `SaveContext`/`LoadContext`, `SetPageTable`, …). Today the backends are dynarmic (JIT), dyncom and fastinterp. **We add a fourth one that runs the recompiled functions.**
- Guest memory is a page table of host pointers (`PageTable::pointers.raw`, 4 KiB pages; NULL for pages the GPU cache watches). `recomp.h` has a matching `RC_PAGED` mode: fast path through the table, slow path through Azahar's memory system.
- System calls: generated code calls `rc_svc(ctx, n, next_pc)`; the backend forwards it to Azahar's SVC handler, as the dynarmic backend does with `CallSVC`.
- Timing: generated code charges cycles per basic block (`RC_TICK`); the backend maps that onto Azahar's `Timer` (`AddTicks`, `GetTicksRemaining`).
- Threads: every guest thread gets its own host fiber. When Azahar's scheduler switches threads, the backend switches fibers, so native C stacks are never mixed.

**Licence consequence:** Azahar is GPL-3.0-or-later, so the port must be GPL-3.0-or-later too. (Azahar's AI policy forbids AI-written contributions *to Azahar*; it doesn't restrict using its GPL code here. Any patch we'd want to send upstream must be written by a person.)

**Bonus:** the same executable can run the game with dynarmic and with the recompiled code, side by side. Comparing the two frame by frame pinpoints any remaining recompilation issue in the real game.

### 3. Frontend and enhancements
| Goal | How | Needs from decompilation |
|---|---|---|
| Runs on any PC | Native code + Azahar renderer (OpenGL/Vulkan). Windows, Linux, macOS builds via CI | — |
| Online multiplayer | MH4U's local multiplayer uses `nwm::UDS`; Azahar tunnels it over its room servers, so 4-player hunts work over the internet. Later: our own lobby UI | — |
| Enhanced graphics | Internal resolution scaling, texture filtering, **custom texture packs in Citra's format**, so existing community packs work | — |
| Unlocked frame rate | The engine is delta-time based: its frame limiter (`sMain`) measures frames in 1/60 s and gameplay scales with that ([system-services.md](research/system-services.md#frame-pacing)), so 60 fps is a higher frame cap (hook on the limiter, `MH4U_FPS`) plus enough emulated CPU clock for a frame to fit in one VBlank. Above 60 Hz the present path waits for VBlanks, so the VBlank rate or those waits have to change; interpolation only if some logic turns out to be frame-counted | Present/VBlank path (`sLtRender`), anything that counts frames |
| Any aspect ratio | Widen the projection and culling in the camera code; re-anchor HUD elements | Camera projection setup; GUI layout (`uLayout`, `nGUI`) |
| Second screen | First: configurable layouts (side by side, separate window, overlay) plus mouse as touch. Then: move the map and item bar into the main HUD | Bottom-screen GUI (`cQuestBoard`, layout resources) |
| Controls | Gamepad + keyboard/mouse; the Circle Pad Pro's right stick for camera (`ir:USER`) | — |

Hooks wrap or replace a recompiled function with native code ([hook points](RECOMPILER.md#hook-points), `config/hooks.txt`, `port/src/hooks.cpp`). They're the only place game behaviour changes, so a hook that replaces a function gets the same difftest discipline: first decompile the function, verify it, then modify the verified version.

## Milestones
1. **Recompiler verified on the whole game** (done for what random states can reach; the rest is checked by running the game).
2. **Boot to title on PC:** Azahar core + recompiled CPU backend, inside Azahar's own Qt frontend (it loads the code library for the game it opens); user's dump for code and assets. *The backend is done (`port/`): it runs a multi-threaded 3DS test program under Azahar's kernel with the same output as Azahar's JIT, and it boots the game's own code (SDK start-up, service connections, all 954 static constructors) exactly like the JIT does, up to the point where the game opens its RomFS. Going further needs the user's full dump.*
3. **Playable**, with dynarmic as a reference to diff against.
4. **Multiplayer and graphics** (mostly inherited from the core).
5. **Widescreen, 60 fps, second-screen HUD:** targeted decompilation with the agent workflow ([AGENT_WORKFLOW.md](AGENT_WORKFLOW.md)), plus hooks.
