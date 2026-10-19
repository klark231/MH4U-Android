# Community enhancements for MH4U and sibling games

Researched 2026-10-12 from public web pages only. No game files were downloaded, no piracy sites were used, and nothing here comes from the local dump. Target build: USA v1.3.0 (`0004000000126300`). The port recompiles the ARM code and runs it on Azahar's core, so Azahar behaviour (releases up to 2126.1.1) matters as much as game behaviour.

## How to read this

- **Published** means the linked page says it. **My reading** marks my own decoding or inference; check it before building on it.
- Every address and value applies only to the build named. **None of them is for USA v1.3.0.** Cheat lines are in Gateway/Action Replay notation as published.
- Cheat-file contents were read through a page-summarising fetch tool, not as raw text. Re-open the linked raw file before copying a value into code.
- Third-party cheat files carry no stated licence, so only short address/value lines are quoted, for orientation.
- Web search for "MH4U 60 fps cheat" is flooded with generated pages that contain no codes (see [Pages to ignore and pages not read](#pages-to-ignore-and-pages-not-read)). Nothing below relies on them.
- Where nothing was found, the section says so.

## Summary

1. **Frame rate.** The community unlocked 60 fps on MH4U (USA, EUR), MH4G (JPN), the Korean release, MH Generations, MH3U, RE Revelations and the Switch ports by writing one float (30.0 to 60.0; MH Generations swaps one instruction instead). I found no IPS/BPS/3GX patch for this; all of it is cheats. In the JPN/KOR cheats the cap is read at `+0x30` of an object behind a fixed global pointer (so probably a singleton; my reading), and the USA/EUR cheats put a "game speed" float 4 bytes below the cap. The USA/EUR cheats use heap addresses of v1.0/v1.1; v1.3.0 is unmapped.
2. **Logic time unit.** Game speed "Normal" is 60.0 in the MH4U cheats, and a Switch MHGU modder quotes frame data "at 60fps" for a game that renders at 30. My reading: gameplay runs on a 60 Hz tick scaled by frame time, not a 30 Hz tick. That would make `docs/PORT_ARCHITECTURE.md`'s "logic stays at 30 Hz, interpolate the rest" wrong. It needs a check in the binary.
3. **Timing trouble.** Azahar users see periodic jitter with 60 fps codes in MH games, blamed (second-hand, from Discord) on a hard-coded sleep in MT Framework (an "engine brake"). Unresolved upstream.
4. **Widescreen.** No prior art for any 3DS game. One MH4G (JPN) cheat claims 16:9 by overwriting two instructions; nobody documents what it does on screen.
5. **Bottom screen.** No mod found. Capcom's own Switch redesign and the official manual's panel list are the only references.
6. **Multiplayer.** Azahar tunnels the 3DS local-wireless service (UDS) through rooms; both ends need the same Azahar build. Real online play ended on 8 Apr 2024. Only the DLC server has been revived (MH Oldschool, through a DNS redirect); Azahar failed to reach it in the reports I found.
7. **Graphics.** Azahar offers up to 18x internal resolution and custom textures. No MH4U HD pack was found. Several MH4U renderer bugs are known; the release notes list two MH4U crash fixes (2126.1 alpha 1 and 2126.1.1), and a 2126.0 change to Vulkan gas rendering probably addresses the cutscene blur (my reading).
8. **Formats.** Two community toolchains (dasding, svanheulen) cover ARC v17, textures, models, text, quests, saves and DLC crypto. The two ARC type tags I could check (model, texture) equal our DTI class ids.
9. **Code-patch cheats.** Some of the community codes (MH Generations 60 fps, MH4G "bloom off" and 16:9) rewrite instructions in `.text`. They cannot work on statically recompiled code unless the port handles them (section 8).

---

## 1. Frame rate

### 1.1 What exists

All fixes found are cheats (Gateway/Action Replay on 3DS and in Azahar, Atmosphere on Switch) that write a 32-bit float, except one that swaps a single instruction (MH Generations, below). No IPS/BPS patch or 3GX plugin for a 60 fps MH was found; for the Switch MHXX the cheat's author says an IPS patch of the cheat "can't be made" ([gbatemp, page 3][mhgu60-p3], as relayed by the fetch tool), and a later poster says the patch is "cheat only" because the ROM location could not be found ([page 5][mhgu60-p5]). The MH FPS-counter plugin that users mention ([Azahar #2231][az-2231]) is not named or linked anywhere I could read.

Floats: 30.0 = `41F00000`, 60.0 = `42700000`, 120.0 = `42F00000`.

| Game / build | Applied how | Frame-rate float | Notes |
|---|---|---|---|
| MH4U USA, "v1.1" (`0004000000126300`) | Gateway, heap offset `D3000000 08000000` | cap `00E44330` (heap `0x08E44330`); game speed `00E4432C` | Speed options: Faster `42F00000`, Normal `42700000`, Slower `41F00000`. Render word `0018B648`. Hazerou. [file][rs-usa] |
| MH4U EUR v1.0 and v1.1 (`0004000000126100`) | same | cap `00E43D80` / `00E447A0`; speed `00E43D7C` / `00E4479C` | Render word `0018B098` / `0018BAB8`. Hazerou. [file][rs-eur] |
| MH4G JPN v1.2 (`000400000011D700`) | Gateway, pointer form | global `0x01037BBC`, then `+0x30` | mikewii7, Kronous. [file][rs-jpn], [thread][kronous] |
| MH4U KOR (`0004000000153200`) | Gateway, pointer form | global `0x0101BF7C`, then `+0x30` | Render: global `0x0101C0B4`, `+0x6BA8`. mikewii7. [file][rs-kor] |
| MH Generations EUR (`0004000000185B00`) | code patch: one instruction in `.text` (no data write) | `00892F5C`: `EDD61A0C` (original) to `EEF01A49` | mikewii7. [file][rs-gen] |
| MH3U USA (`00040000000AE400`) | absolute heap address | `086261C0`, default `453B8000` (the file calls it "Default 3000FPS") | Hazerou. [file][rs-3u] |
| RE Revelations USA (`0004000000060200`) | absolute heap address | cap `0932B2A0`; speed `0932B29C` | Both default to 30.0. [Hazerou's guide][hazerou-guide] |
| MHGU West v1.4.0 / MHXX JPN v1.5.0 (Switch) | Atmosphere cheat VM | `[main+0x018A6210]+0x243C` / `[main+0x0214B898]+0x484C` | masagrator. [post][mhgu60-cheats] |

Excerpts (published, shortened):

```
; MH4U USA v1.1 (Hazerou): every address is added to the offset set by D3
D3000000 08000000
00E44330 42700000        cap = 60.0
D2000000 00000000

; MH4G JPN v1.2 (mikewii7, Kronous): follow a pointer instead of a fixed address
61037BBC 00000000        if [0x01037BBC] != 0
B1037BBC 00000000        offset = [0x01037BBC]
00000030 42700000        [offset + 0x30] = 60.0
D2000000 00000000

; "Hold R for 30 FPS" form used in several files
DD000000 00000100        while R is held
00E44330 41F00000
```

The code types follow Citra's `gateway_cheat.h` (see section 7). The Atmosphere lines are `58000000 018A6210` (load a value from the main module), `78000000 0000243C` (add) and `64000000 00000000 42700000` (store 32 bits). My reading of the opcodes, from [CheatASM's examples][cheatasm] (`58000000 00001234` is `mov.q R0,[MAIN+0x1234]`).

Other MH sets exist but were only seen in search results, not opened: [MH3U JPN][rs-3u-jpn] and [EUR][rs-3u-eur], and [JourneyOver's MH Generations USA file][jo-gen-usa]. [GameBrew][gb-ctrpf] lists MH4U (EUR/USA) in the CTRPF cheat collection; the file was not opened.

### 1.2 What the cheats say about the engine

- **Two adjacent floats.** Game speed sits at X-4 and the cap at X. [Hazerou's guide][hazerou-guide] states the rule ("subtract 4 from the FPS address") and the MH4U files follow it.
- **An object at `+0x30`.** In the JPN and KOR builds the cap is `object+0x30`, with the object found through a fixed global pointer (published: the `B1037BBC` / `6101BF7C` lines). That global is why I guess a singleton. The speed would then be at `+0x2C` (my reading; those files have no speed cheat).
- **Code reads it with a VFP load.** The MH Generations 60 fps patch swaps `vldr s3,[r6,#0x30]` for `vmov.f32 s3,s18` (my decoding of `EDD61A0C` and `EEF01A49`, checked with Capstone). So the original code reads the cap from `object+0x30`, and the patch substitutes a register that presumably already holds 60.0.
- **Defaults.** The MH3U file lists `453B8000` (3000.0) as "Default 3000FPS" ([file][rs-3u]; Hazerou first read it as "3000 FPS???" in [his guide thread][hazerou-guide]). My reading: that means no effective cap, and the 60/30 cheats then act as a limiter. RE Revelations has cap 30.0 and speed 30.0, where speed 60 gives 2x and 15 gives 0.5x ([guide][hazerou-guide]). MH4U USA's "Normal" speed is 60.0 ([file][rs-usa]); the file does not say what the unmodified game's cap is.
- **The cap is not an exact rate.** NotACat's MH3U caps labelled 55/50/45/40/35 FPS ([page 37][fps-thread-37]) are the floats 54.5, 50.25, 45.25, 42.0 and 36.5 (my decoding). The labels are approximate, and the values look tuned until the measured rate matched; my reading is that this fits a timing-based limiter more than an integer divider of the display rate.
- **Time base.** A MHGU modder writes frame data at 60 fps ("Valor HBG dodge I-frames 20 > 12 (at 60fps)", [README][potobc]) although the game renders at 30 on Switch. Together with "Normal = 60.0" in the MH4U cheats, my reading is: the logic tick is 60 per second, each loop advances it by a number of ticks that depends on the measured frame time (scaled by the speed float), and the cap only limits the loop. Evidence is indirect. Test it before relying on it (section 8).
- **Cheats rewrite continuously.** In Citra's original engine cheats run once per 1/60 s of emulated time ([source][citra-cheats-cpp]).

### 1.3 Side effects, and what real hardware does

Published side effects:

- **RE Revelations at 60 FPS** (same engine family): bullets sometimes pass through enemies and boxes, thrown projectiles are doubled, "anything else seems faster" ([Hazerou's guide][hazerou-guide]).
- **3DS 60 fps codes in general**: per the fetch tool's summary of the collection thread's first post, many codes also make the game run faster (not only smoother); the post also warns that on Citra a game running below 30 fps gains nothing from a 60 fps code ([first post][fps-thread]).
- **MH in Azahar: periodic jitter with 60 fps codes**, in both Vulkan and OpenGL ([#1737][az-1737]). The issue relays a Discord user (erikk194) who "found the internal engine brake": "a hard-coded sleep routine within the MT Framework that's simply too aggressive for high-performance emulation". That is second-hand and unverified. Disabling vsync does not remove the jitter, and locking the game to 55 fps avoids it (reporter). Raising "delay render thread" made it subtler at a cost in performance (Evilmass); PabloMK7 suggested trying 1-3 ms. Old unofficial builds had an "FMV Tick Hack"; value 77 reportedly removed the jitter ([build][citra-rasp-legacy]). Evilmass found that PR #2095's new option did not fix it. Azahar's maintainer wants the root cause, not a hack. Status: open.
- **Switch MHGU/MHXX** ([thread][mhgu60]): cheat only, needs overclocking to hold 60 (frame drops on fast camera moves). A yuzu user lost the quick reticle with the cheat on ([page 5][mhgu60-p5]); an Eden user reports freezes near gathering spots ([Eden #620][eden-620]). Someone asked on page 5 (Jun 2026) whether any logic is tied to frame rate; no answer is on that page.
- I found no page reporting that the MH4U, MH4G or MH Generations cheat makes the game run fast. I also found none that tests for it.

Real-hardware frame rate (the baseline is unclear):

- A New 3DS in MH4U's Val Harbor "sits at a solid 35-36 FPS" according to a Luma plugin that shows FPS (unnamed), and Azahar "is mostly solid at 35 but dips to 33 every few seconds" (DonKatsu in [#2231][az-2231]; the Azahar settings and counter are not stated). That is above a 30 lock.
- A MHXX user's CTRPF counter shows 30-50 fps in play, 50-60 with dithering and AA off, and about 170 on loading screens ([gbatemp][mhxx-q]).
- Press says both the 3DS original and the Switch version of MHXX run at 30 fps ([Siliconera][siliconera-xx]); the Switch version is "a locked 30fps" ([Digital Foundry, via Nintendo Wire][nw-df]). A French test says MH4U runs noticeably faster and more stably on a New 3DS, with the remaining slowdowns in online play ([Gameblog][gameblog], summarised by the fetch tool).
- My reading: on 3DS the cap is not a hard 30. The game runs at whatever the GPU allows, typically 30-40, under a ceiling of at least 50-60. "30 fps" in the press is typical performance. The Switch ports are locked at 30.
- Azahar's 2126.0 notes say games with dynamic fps that do not reach 60 on hardware will not reach it with "Simulate 3DS GPU timings" on ([release][az-2126.0]). In [#2231][az-2231] (reporter tested MH3U, MH4U and MHXX) PabloMK7 said "If you want full 60fps you need to turn it off" and that the option is "kinda working as intended".

### 1.4 Not found

- Any 60 fps cheat or patch for USA v1.2/v1.3.0, or a statement of which update the USA file targets beyond "v1.1".
- The default cap and default speed of v1.3.0; whether the logic is dt-based; any analysis of the main loop.
- Any interpolation, frame-generation or "above 60" work on a 3DS MH game.
- An MH4U frame-rate measurement from Digital Foundry or similar (only the MHXX Switch summary was readable).

---

## 2. Widescreen and aspect ratio

### 2.1 What exists

- **No 3DS widescreen hack was found for any game.** The Widescreen Hack page of GameTechWiki says of the 3DS: "Currently there is no ultrawide hack for 3DS system" ([page][gtw-wide]). Searches for Citra, Azahar or Lime3DS widescreen patches returned only DS-Homebrew's page about DS backwards-compatibility titles ([page][ds-wide]) and Dolphin material.
- **One MH-specific code.** Kronous's MH4G JPN v1.2 cheat list has "Aspect Ratio 16:9" ([thread][kronous]):

  ```
  [Aspect Ratio 16:9 v1.2]
  00AD7E88 E3A00010
  00AD7E90 E3A01009
  ```

  My decoding: `mov r0,#16` and `mov r1,#9`, 8 bytes apart (one instruction between them). The author gives no explanation and does not say whether it widens the view or only changes a ratio. It is for the JPN build, it is not marked `*citra_enabled`, and I found no report of its effect on screen.
- **The same author's "Turn off bloom"** is `002B7498 E12FFF1E`, which is `bx lr` (return immediately) written over a function's first instruction (my decoding). Like the 16:9 code, it patches `.text`, so it needs the code-patch handling discussed in section 8.

### 2.2 Emulator-side options

I found no emulator feature that widens the rendered view; Azahar's layouts only arrange and scale the two screens (section 7). This is my reading from the absence of any hack in section 2.1, not a statement from a source.

### 2.3 Switch editions

MHXX/MHGU on Switch render natively at 16:9 (1080p docked, 720p handheld) with a redone HUD ([Dualshockers][dualshockers-xx]). No ultrawide mod for MHGU was found; a search returned only generic emulator pages. In the MHGU thread, masagrator looked for a draw-distance setting and found the string `mVisibleDistance` at `main+0x158016f` with candidate pointers, without success ([post][mhgu60-p1]). The name may exist in the 3DS binary too (a string to search).

### 2.4 Not found

- Any projection, FOV or culling patch for a 3DS game.
- Any statement on how the MH engine handles the 3D slider's second eye in the projection code.
- Any ultrawide patch for the Switch MH ports.

---

## 3. Bottom screen and HUD

### 3.1 What exists

- **No mod was found that moves the touch panels onto the main screen** for any 3DS MH game (several searches).
- **Emulator layouts** are the only tool; see section 7.

### 3.2 What the bottom screen does (the specification)

MH4U official manual, [Touch Screen during Quests][man-touch]: Camera Controls, Map (hunter position; monsters appear when conditions are met), Use Item / Item Shortcut (assignable), Kick / Special Attack / Skip (cutscene), Target Cam (icon appears when a monster has spotted you; "Unseen" and "Discovered" states; L focuses, L twice resets behind you), Hunter Search (offline only) and Chat (online only). The contents page lists "Panel Customization", so the panels are user-arranged ([contents][man-contents]).

[Top Screen during Quests][man-top]: Time Limit, Health Gauge, Stamina Gauge, Weapon Status (sharpness, ammo, coating, special gauges), Hunter Names and Icons (with the monster-spotted eye), Item Bar. [Top Screen in Town][man-town] adds the target name at upper left, speech bubbles and delivery icons. I did not open MH4U's "Touch Screen in Town" page; the 3DS Generations manual lists seven town touch functions: Hunter Search, Shortcut Buttons, Menu, Help, World Map, Hunters for Hire and Chat ([page][gen-town]).

A US patent ([9,452,357][patent]) describes a dual-screen game with touch "panel images" in a six-cell (3x2) arrangement area, panels dragged into cells, and camera panels spanning two or three cells. The readable part of the page does not name the assignee or the game, so treat it as indicative of the panel grid, not proof.

### 3.3 Capcom's single-screen redesign (Switch MHXX / MHGU)

| Element | Switch placement or control | Source |
|---|---|---|
| Map (a touch-screen panel on 3DS) | upper right corner of the screen (Dualshockers notes the 3DS original used two screens); in MHGU the mini-map shows on the right of the screen once you collect the Map from the Supply Box | [Dualshockers][dualshockers-xx], [Nintendo Life][nl-guide] |
| Target Camera (a touch-screen icon on 3DS) | ZL toggles it; an icon in the upper right shows its state; L turns the camera to the monster; the right stick moves the camera | [Gematsu][gematsu-xx] (it attributes this to a 4Gamer preview, not to Capcom) |
| Hunter Arts gauges | beside the item selection window, lower right | [Gematsu][gematsu-xx], [Dualshockers][dualshockers-xx] (calls it the hunting gauge) |
| Button layouts | three presets that move Target Camera and Hunter Arts to different buttons (for example Target Camera on ZL, on Down, or on a stick press) | [Perfectly Nintendo][pn-xx] |
| Overall | UI "optimised for a single-screen display" (Perfectly Nintendo); Game Informer calls the Switch screen space "generous real estate" and says the 3DS UI was cluttered | [Perfectly Nintendo][pn-xx], [Game Informer][gi-mhgu] |

How the other touch functions (Hunter Search, Chat, shortcuts, camera pad) were handled is not described on any page I could read.

### 3.4 Not found

- Any patch, plugin or layout mod that merges the two screens' HUD.
- Any description of the Switch menus for Hunter Search, Chat or shortcuts.
- Any data on the on-disk format of the panel layout (it is saved user data).

---

## 4. Multiplayer

### 4.1 Local wireless over Azahar rooms

- **Mechanism.** Citra emulates the 3DS local-wireless stack (UDS) and forwards each game's wireless traffic through a server/client setup. It needs no Nintendo account ([announcement, 2017][citra-net]). Rooms hold up to 16 players, can be password-protected, and have a preferred-game setting ([2018 report][citra-pr-2018]).
- **MH status over time.** The 2017 post lists MH3U/MH4U as "functional but flawed" (two players work, more cause disconnects) and Generations/X/XX as incompatible. The 2018 Q1 report lists MH3U, MH4U, MHX and MHXX as working. Both pages are archived and may be stale.
- **Hosting.** The archived help page ([multiplayer][citra-mp-help]) covers a public room browser, direct connect, a dedicated-room Docker image on `5000/udp`, and port forwarding. It gives no latency figures and no version-matching rule. Large rooms "may result in slowdowns or even disconnections". MH4U allows 4 players per hall ([manual][man-local]).
- **Same build on both ends** ([#2051][az-2051]). An MH4G player on Citra Canary 2764 could not join an MH4U hall hosted on Azahar 2125.1 ("The connection is failed"); the reverse worked; two Azahar 2125.1 peers worked both ways. Maintainers answered that multiplayer changed in Azahar, old builds are "actually inaccurate", and they will not add compatibility. Workaround: the same Azahar version.
- **Regions.** NA and EU versions play together locally and online; Japan is separate ([Nintendo Life, 2014][nl-regions]). The #2051 report suggests emulated MH4U and MH4G halls can still interoperate in one direction.
- **Fixes.** A UDS beacon memory-access fix landed in 2126.1 alpha 1 (#2403, "may resolve undiscovered issues in some multiplayer games"). Android gained rooms in 2126.0, and the desktop got a "No Preference" preferred game ([notes][az-2126.0], [article][aa-rooms]).
- **Not supported:** Download Play and SpotPass titles were out of scope in 2017-2018 ([announcement][citra-net], [report][citra-pr-2018]). The archived help page says some games need Download Play through low-level emulation, which needs system files dumped by the user ([help][citra-mp-help]). MH4U's Local Play needs one copy of the game per player ([manual][man-local]), so it should not depend on Download Play (my reading).
- **Latency:** no measurement for MH was found.

### 4.2 Online play and DLC after the Nintendo Network shutdown

- **Shutdown.** Online features for MH3U, MH4U, Generations and Stories on 3DS ended on 8 Apr 2024; DLC could no longer be re-downloaded; downloaded DLC and Local Play remain ([Nintendo Wire][nw-shutdown], [Nintendo Life][nl-shutdown]).
- **MH Oldschool** is a community download server for DLC and event quests, "not affiliated with Pretendo". It covers MH3G, MH3U, MH4, MH4G and MH4U. Generations, X, XX and Stories are unsupported "until 3rd party service tokens are worked out on Pretendo's end". It is used through a custom DNS server on the console ([forum post][mhos]). Pretendo members say Pretendo already supports the DNS redirection for MH3U/MH4U "DLC", which "is hosted by another service" ([thread][pretendo-stories]). The post does not say whether MH4U's online gathering halls work.
- **Pretendo overall.** A console-mods page says 79% of progress has been made across 3DS games and services and does not list individual games (it points to Pretendo's own progress page, which I could not read); it does not mention Monster Hunter ([page][consolemods-pretendo]). It also says a 3DS needs custom firmware to connect.
- **Azahar failed to reach the DLC server in the reports I found.** [#875][az-875] (MH3G, MH3U, MH4G, MH4U, latest versions): infinite loading or error 090-2441; the reporter says the same DNS redirect (to 34.75.107.68) works on PabloMK7's Citra and on Lime3DS. The issue does not name MH Oldschool, but 34.75.107.68 is the DNS the MH Oldschool post gives, and that post's commenter reports Azahar "receives error code 090-2411 (error code is typically for mismatched game version)" and that Azahar "lacks the ability to connect to the DLC server", adding that they filed a bug report ([forum post][mhos]; the number is not given, probably this issue). The two pages quote different codes (2441 in the issue, 2411 in the forum post); I read both through a summarising fetch tool and did not check which digits are right, but both are described as a game-version mismatch. No maintainer reply is shown; one commenter says it "looks to be working with the latest version" (screenshot, unverified).
- **Conflicting reports on online halls.** A compatibility-list tester says event quests work and online play works "using Pretendo" ([#14][az-compat-14]). Another user on a Steam Deck with Azahar 2125.1.2, also on Pretendo, gets an immediate crash when creating an online gathering hall ([#2226][az-2226]); no diagnosis is shown. The Capcom manual's Internet Play page does not mention Nintendo Network IDs or server details ([manual page][man-internet]).
- **DLC is stored as Extra Data.** Commenters in the MH Oldschool thread say downloaded DLC is saved as Extra Data on the SD card, so it may transfer to another console ([forum post][mhos], second-hand).
- **DLC quests on PC.** Episodic quests do not appear after moving a save to Azahar on PC; the quests live in ExtData ([#2160][az-2160], open).
- **Older tooling.** svanheulen's `mhqs` is a proxy server for hosting DLC quests (MH4U script `mh4u_proxy.py`, needs Twisted) and `mhef` decrypts and encrypts DLC and saves ([thread][mhef-thread]).

### 4.3 Not found

- Any project that revives MH4U's Nintendo Network matchmaking apart from the Pretendo claims above.
- The requirements of MH4U's online mode (which NEX or NASC tokens, which Capcom endpoints).
- Latency measurements or desync reports for emulated MH4U at 4 players.

---

## 5. Graphics

### 5.1 Resolution and renderer

- **Works well in practice.** A compatibility tester rated MH4U "Great" on Lime3DS 2117 at 9x internal resolution with a texture pack (not named), OpenGL, and the "3D Off" and "Bloom Off" cheats, and recommends updating the game to version 1.1; a maintainer confirmed the rating after a second tester agreed ([#14][az-compat-14]). Stutters returned in 2122 for one tester, and another says the stutters are gone on Vulkan.
- **Resolution cap.** The libretro page lists up to 18x ([docs][libretro-azahar]). 2126.1 alpha 1 raised the maximum from 10x ([notes][az-2126.1a1], #1906).
- **Known MH-related renderer issues:**

  | Issue | Symptom and cause | Status |
  |---|---|---|
  | [#765][az-765] | Cutscenes with smoke or steam blur the whole frame; the game uses PICA "gas" rendering, which has only preliminary support (commenter: wwylele's `gas-hw` branch). Reported fixed on OpenGL in 2124 but not on Vulkan. Workarounds: stereoscopy depth 1%, or the 3D-off, FSAA type-2, no-dithering and bloom-off cheats | Open when I read it. 2126.0 changed the Vulkan gas stub "fixing a known graphical issue in Monster Hunter games" (#2165), probably this one (my reading; the issue does not link it) |
  | [#1852][az-1852] | Vulkan crash at 6x internal resolution on 2125.0 alpha 5 (reported with an EUR title ID; Vulkan 5x and OpenGL 5x/6x were stable on the same machine, per the reporter): the surface garbage collector destroys textures still in use (Wunkolo's diagnosis) | The 2126.1 alpha 1 notes describe the same mechanism: texture surfaces deleted while in use, "most notably when starting missions in Monster Hunter 4 Ultimate" (#2407). The issue page I read does not link the PR, so the match is my reading |
  | [#2531][az-2531] | Assert when opening the equipment set screen on Vulkan; a shadow-texture slot receives an RGBA4 surface | The 2126.1.1 notes list a fix for "a crash in Monster Hunter 4 Ultimate when opening the equipment menu" (#2533); the issue page I read had no PR linked |
  | [#769][az-769] | Creating a new game and saving in MH4U trips an assert in `IPC::RequestHelperBase::ValidateHeader` ("Operations do not match the header"), bisected by the reporter to one commit | Status not stated; the reporter built a debug build (the check is a debug assert). My reading: an Azahar service handler pops or pushes a different number of words than its command header declares, for a command MH4U issues while saving. Worth checking against the service map the other agent is producing |
  | [#1632][az-1632] | MHXX Vulkan rendering error from the character select screen, none on OpenGL (2124) | Open at time of reading |
  | [#1567][az-1567] | Android: black screen after importing MH4U save/ExtData | Open |
  | [#726][az-726] | MH4U (reporter blames the US version) fails to launch from the HOME Menu (RC3) | No diagnosis |
  | [#97][az-compat-97] | Low-end laptop: MH4U/MHXX about 45 fps on Lime3DS, about 8 fps in the offline lobby on Azahar 2123 | Below minimum specs |

- **Quality toggles are cheats.** Everyone uses the engine's settings word to turn off 3D, FSAA, dithering and bloom (section 1.1 and 2.1). Hazerou's cheats write the packed word `001F076E` (default) or `000F432E` ("Trinity Disabler": AA, dithering and 2nd-screen AA off) at heap `0x0818B648` in USA v1.1. The MH Generations EUR comment names the fields `FSAA_2D`, `mDitherDisplay0` and `NoAADisplay1`.
- NotACat's MH3U USA set also has a filter toggle (`002A894C 1A00027D`) that removes bloom and blur, and a no-dithering option ([page 37][fps-thread-37]).

### 5.2 Texture packs

- **Azahar feature:** dump to a folder, replace by hash-named `.png`, `.dds` or `.ktx` files, with a `pack.json` that maps arbitrary names; the new hash method is the default; only power-of-two textures are dumped; "Preload Custom Textures" trades RAM for speed ([help, archived][citra-textures]).
- **MH3U:** the "MH Classic HD Project" pack (distributed through its Discord) drops a good machine from 60 fps to 30-47 fps until textures are preloaded ([#1443][az-1443]).
- **MH4U, MH Generations, MHXX:** no pack found. GameTechWiki's texture pack page lists none for MH ([page][gtw-textures]). One MHXX poster planned a 4K pack with Dasding's tools; the outcome is unknown ([gbatemp][mhxx-q]).

### 5.3 Shader presets and post-processing

None found for any 3DS MH game. Azahar offers texture filters (Anime4K, Bicubic, ScaleForce, xBRZ, MMPX) in the libretro core ([docs][libretro-azahar]).

---

## 6. Modding knowledge

### 6.1 Tools and documents

| Resource | What it covers | Licence / notes |
|---|---|---|
| dasding's MH Mod Tools (rev07 attached to the [ROM Hacking Guide thread][guide]): `arc.py`, `arctool.py`, `textool`, `tex2png`, `wav2mca`, [`mca2wav`][mca2wav] | ARC unpack/repack, TEX to PNG, MCA audio | Licence not stated. The guide's first post was not opened; pages 2, 5, 7 and 8 were read |
| svanheulen's `mhff` wiki ([MH4U models][mhff-models], [MHXX rQuestData][mhff-quest]) and [Blender importer][mhff-blender] | MOD structure, quest format | Pages not opened; the links come from gbatemp posts ([models thread][extract-thread], [guide page 5][guide-p5]). The GBAtemp user moghedien, whose posts link these repos, says "All my code it's licensed under GPLv3" |
| `mhef`, `mhqs`, `mhxqe` (github.com/svanheulen, posted by moghedien) | DLC/save crypto, DLC proxy, browser quest editor for MHX/MHGen | GPLv3 per the author ([thread][mhef-thread], [editor][mhxqe]). No MH4U quest editor found |
| [Rokumaehn/mh4edit][mh4edit] (also on [GameBrew][gb-mh4edit]) | MH4U save editor (C#, WPF): item box, equipment, guild quests, palicos | MIT. Handles encryption itself (Blowfish source files); no offsets documented in the README; supported regions not stated |
| [APM save editor][apm], [MH Talisman Editor][talisman] | MHX/MHGen/MHXX saves; MH4G/MH4U support partial or unverified | |
| [eesandoval/MH4U-Tool][mh4u-tool] | damage calculator with a bundled database | LGPL-3.0; not a format reference |
| Kuriimu / Karameru | open `.arc` and `.lmd` text; v1.0 advised for LMD ([page 8][guide-p8]) | |
| FluffyQuack's [ARCtool][arctool], BoubouV12's [ARC Unpacker][arcunpacker] | MT Framework PC games (RE5, Dragon's Dogma, Revelations PC) | 3DS not mentioned |
| [Vermil's MHXX monster-size and food plugin][gb-vermil] | CTRPF-style cheat plugins for MHXX 1.2.0 (last updated 2018): precedent for plugin-based MH cheats | No addresses or source on the page |
| [anco/mh4u_qol][qol] (Codeberg) | MH4U quality-of-life patches as BPS/IPS for `code.bin`, plus cheats; Python patcher (`patch_binary.py`, patch definitions under `patches/`) | LGPL-3.0. Release 0.0.1 has one patch, `100_success_rate`: 136 one-byte writes at `0x00F2BCDE`..`0x00F2C20E` (one every 8 bytes, setting each combination's success rate to 5). All 136 original bytes match the **USA v1.3.0** update, and they lie in `.data`, so the patch changes no code: through Azahar's mods folder it works with the port's recompiled code as it is (tried in the cloud: the bytes read 5 at run time, and the code library still matched the running code) |
| Cheat databases: [Reshiban/60FPS-AR-CHEATS-3DS][rs-repo], [JourneyOver's][jo-repo] and [iSharingan's][is-repo] CTRPF-AR-CHEAT-CODES | per-title `.txt` files named by title ID | Only Reshiban's MH files were opened |

### 6.2 Format facts

- **ARC.** `arc.py` carries a version: 17 for MH4U, 16 for MH3U. One user changed it from 17 to 16 and got different file sizes ([page 2][guide-p2]).
- **Type tags are class ids.** In a 2015 thread, ARCTool output is described with model files carrying a `*.58A15856` extension (per moghedien) and a user guessing that `*.241F5DEB` is a texture ([thread][extract-thread]). Both equal our DTI ids for `rModel` and `rTexture` in `config/mt_classes.tsv` (`0x58a15856`, `0x241f5deb`; I checked the file). So the ARC entry type is the DTI class id, as `docs/research/mt-classes.md` assumes, and the same tag-to-class mapping should hold for the other resource classes (`rMaterial`, `rMotion`, `rGUI`, `rLayout`, `rQuestData`...) - my reading, untested.
- **Textures** are `.tex`; format 12 is discussed; `textool -ra` writes uncompressed RGBA and avoids artifacts ([page 2][guide-p2]).
- **Materials** are `.mrl`; dasding says they define texture parameters and probably hold compiled shader code, without certainty ([page 5][guide-p5]). That matches `rMaterial`.
- **Text** is `.lmd`; Kuriimu-edited files froze the game ([page 8][guide-p8]). **Audio** is `.mca`, readable with `mca2wav` and vgmstream.
- **Quests** are `rQuestData` and can be edited in a hex editor using svanheulen's format page ([page 5][guide-p5]).
- **DLC and saves** are encrypted; `mhef` handles both ([thread][mhef-thread]). No keys are quoted here and none should be added to the repo.
- Mixing files between MH4U and MHXX causes crashes; a modder advises using MH4G files when moving weapons into MHXX ([page 8][guide-p8]).

### 6.3 Engine and memory documentation

- MT Framework Mobile is based on MT Framework 2.0, was designed for the 3DS, and is listed for RE Revelations, Mercenaries 3D, MH3U, MH4, MH4U, Generations, MHXX and MH Stories ([Wikipedia][wiki-mtf]; the page has nothing on language, DTI or file formats). A tech report relayed by Siliconera lists HDR lighting, colour correction, self-shadowing, normal mapping, depth of field and motion blur on the 3DS build ([article][siliconera-engine]).
- **No public RAM map, struct documentation or decompilation project for MH4U or any MT Framework Mobile game was found.** The only memory knowledge is in cheat files (section 1). The MH4U "Invincibility Cheat Help" thread ([post][invincibility]) shows no replies in the pages I read.

### 6.4 Not found

- Documentation of MT Framework Mobile internals (DTI, resource loading, scheduler).
- A published quest, monster or weapon data table specification for MH4U beyond the pages above (the 2016 guide mentions weapon file `w13`).

---

## 7. Azahar specifics

- **Cheats.** One file per title, named `<TitleID as 16 hex digits>.txt`, in the cheats directory. `[Name]` starts an entry, hex pairs follow, `*` starts a comment line, and `*citra_enabled` marks it enabled ([`cheats.cpp`][citra-cheats-cpp], [`gateway_cheat.h`][citra-gateway]). Types in the header (`enum class CheatType`): `00/01/02` writes (32/16/8 bit), `03-06` 32-bit conditionals (greater, less, equal, not equal), `07-0A` masked 16-bit conditionals, `0B` load offset, `0C` loop, `D0` terminator (end of an if), `D2` full terminator, `D3` set offset, `DC` add offset, `D4`/`D5` add/set the data register, `D6-D8` incremental writes, `D9-DB` loads, `DD` key conditional ("Joker") and `E` patch. In the UI: game Properties, Cheats tab ([guide][joey]). The engine runs the enabled cheats from core timing every `BASE_CLOCK_RATE_ARM11 / 60` ticks ([`cheats.cpp`][citra-cheats-cpp]). This is Citra's original code from a mirror; I could not read Azahar's current tree.
- **Mods.** `load/mods/<TitleID>/` with optional `exefs/` (`code.bin`, `code.ips` or `code.bps`), `romfs/`, `romfs_ext/` (`.stub` deletes a file, `.ips`/`.bps` patch one) and `exheader.bin`. Luma layouts work as they are. 3GX plugins go in `sdmc/luma/plugins/<TITLEID>/<name>.3gx` with "Enable 3GX plugin loader" on ([docs, archived][citra-mods]). In 2126.1-rc4 a new "debug exception handler" setting that had been wrongly enabled by default was switched off by default, because having it on can crash game mods that target emulators ([rc4][az-2126.1rc4]).
- **Textures.** `load/textures/<TitleID>` (RetroDECK maps it the same way, [guide][retrodeck-az]); options and caveats in section 5.2.
- **Layouts (desktop).** Single screen, Large Screen, Side by Side, Separate Windows, Hybrid Screen and Custom Layout ([guide][joey]). Custom layout uses `custom_top_x/y/width/height` and the bottom equivalents; Citra's edge-based tags are ignored, and `layout_option=6` with `custom_layout=true` selects the custom layout at start-up ([discussion #860][az-860], marked unanswered; one user reports a working 3840x1200 side-by-side setup). The libretro core has Top-Bottom, Single Screen Only, Large Screen/Small Screen (1x to 6x, default 4x) and Side by Side, with no custom layout listed, and marks RetroArch cheats unsupported ([docs][libretro-azahar]). Android secondary-screen layouts are in the 2126.0 notes ([notes][az-2126.0]). A 3D slider range up to 255% arrived in 2121-rc1 ([notes][az-2121rc1]).
- **Rooms.** The standalone room server moved into the main executable as `--room` in 2121-rc1 (#873), with a Docker image ([notes][az-2121rc1]). See section 4.1 for the rest.
- **Timing options.** "Simulate 3DS GPU timings" (experimental, off by default; #2095, #2145) and "Skip presenting duplicate frames" (added in 2126.0 for frame-generation tools, #1867; turned off by default in 2126.1.1 because of regressions, #2530/#2536, [notes][az-2126.1.1]). GPU command-list processing was optimised by about 0.5-1 ms per frame in 2126.1 alpha 1 (#2318, [notes][az-2126.1a1]).
- **Per-game hacks.** None for MH is documented. The only MH-specific changes in release notes are renderer fixes (#2165, #2407, #2533). The "FMV Tick Hack" is requested but absent ([#1737][az-1737]).
- **Policy.** Not re-checked here; `docs/PORT_ARCHITECTURE.md` already records Azahar's licence and AI-contribution rules.

---

## 8. What this means for the port

Ordered by value for effort. The first three decide the architecture, so do them before the features.

**P0 - settle the time model (blocks the 60 fps / unlocked plan).**

1. **Find the frame-pacing object in v1.3.0.** What the sources show: in MH4G JPN and MH4U KOR the cap is a float at `+0x30` of an object reached through a global pointer; in MH Generations the code reads it with `vldr s3,[r6,#0x30]`; in MH4U USA/EUR the cap sits at a fixed heap address with the game-speed float 4 bytes below it (so probably `+0x2C` of the same object; my reading). Things to look for in the v1.3.0 Ghidra export, none confirmed: `vldr sN,[rM,#0x30]` near `vldr sK,[rM,#0x2c]`; the literals `42700000` (60.0), `41F00000` (30.0) and `453B8000` (3000.0, MH3U's default); and the constructors of `sMain` (size `0x288`) and `sMhMain` (`0x358`) in `config/mt_classes.tsv`, since a main-loop system is the natural owner (a hypothesis, not a finding). Record the result in a new research note.
2. **Settle the tick unit.** Cheapest test, no running game needed: in the code that reads the speed float, see whether the per-frame step is derived from 60.0 or from 30.0. Second test once the port runs a frame loop: count logic updates per presented frame with the cap at 30.0 and at 60.0. One update per frame at 60 and two at 30 would confirm a 60 Hz tick; then 60 fps needs no interpolation and only rates above 60 do. This tests the "logic stays at 30 Hz" assumption in `docs/PORT_ARCHITECTURE.md`; if the check shows a 60 Hz tick, that document needs updating (`docs/ROADMAP.md` already says only that logic is "likely frame-locked").
3. **Find the engine's sleep.** The "brake" in [#1737][az-1737] is claimed (second-hand) to be a sleep inside MT Framework. Look for callers of SVC `0x0A` (SleepThread) in the main loop and for the vsync wait (GSP event waits). Because the port runs the game under Azahar's kernel, the port decides how those waits are timed; knowing which call is the frame limiter tells us what to bypass for an unlocked frame rate and what to keep for correct animation timing. Check also what the "FMV Tick Hack" corrected (my guess from the name: movie timing).

**P1 - reuse what exists.**

4. **Cheats and mods, and the code-patch problem.** Keep Azahar's formats working: `<TitleID>.txt` Gateway files (including `D3`, `B`, `DD`) and `load/mods/<TitleID>/` (`romfs`, `romfs_ext`, `exefs`). Data-only cheats, such as the USA/EUR 60 fps and render-word codes, write guest memory and should keep working. Anything that rewrites code will not change statically recompiled functions: the MH Generations 60 fps swap, the MH4G bloom and 16:9 codes, and any `code.ips`/`code.bps`/`code.bin` mod such as `anco/mh4u_qol`. This is my reading of the architecture in `docs/PORT_ARCHITECTURE.md`, not something a source says. Options: apply `exefs` patches to `code.bin` before recompiling (one generated build per patch set); detect writes into `.text` and run that code through Azahar's own CPU backend, which the architecture note already wants for diffing; or offer the same effects as hooks and settings, which suits bloom, aspect ratio and frame cap best. **Done (October 2026):** the first option. `tools/build_game.py` applies the code mods in Azahar's mods folder the way Azahar does (`tools/code_mods.py`: a replacement `code.bin`, then the first of `exefs/code.ips`, `exefs/code.bps`, `code.ips`, `code.bps`) before recompiling, and reports whether a mod touches `.text`. Data-only mods such as `anco/mh4u_qol`'s need no rebuild at all, because the port checks only `.text` against what it recompiled. RomFS mods (textures, text, models) are unaffected and are the safest route for community content. Existing community cheat files apply only after someone finds the v1.3.0 addresses.
5. **Multiplayer.** Inherit Azahar's UDS rooms and keep the UDS service untouched. Add an explicit protocol/build check, because [#2051][az-2051] shows that peers on different builds fail with only "The connection is failed". Any setting that changes simulation (frame cap, game speed) must be identical across peers or locked; purely visual settings (resolution, widescreen) are local. The 2017 Citra post says MH3U/MH4U worked with two players but dropped with more ([post][citra-net]); the 2018 report lists them as working. Four-player MH4U on current Azahar is untested in anything I found, so test it early.
6. **DLC.** The DLC server is reached by a DNS redirect, and Azahar failed to connect in the reports I found (#875, the MH Oldschool post); PabloMK7's Citra and Lime3DS reportedly work. Either work out what Azahar's network services lack by comparing with those builds, or let the player supply DLC as ExtData from their own console and read it locally. DLC is game data: per `CLAUDE.md` it never goes into the repo, and the port should only read what the user supplies. Online gathering halls on Nintendo Network are out of scope: I found no working revival, and the two reports on online halls on Pretendo conflict.
7. **Graphics.** Use Azahar's renderer, texture hashing and filters unchanged. Before shipping, re-test the MH4U-specific issues in section 5.1 on our build: cutscene blur (gas), Vulkan texture lifetime at high resolution, the shadow-texture assert, and the save-creation IPC assert (#769). Offer OpenGL as the safe default until those are confirmed on our core: for correctness, the MH reports favour OpenGL in #765, #1632 and #1852 (one compatibility report prefers Vulkan for smoothness, [#14][az-compat-14]). Replace the "disable 3D/FSAA/dither/bloom" cheats by named settings that write the engine's settings word through a hook (**done** for dithering, `dither`; bloom is a filter, not a bit of that word: the port skips its render pass, `bloom = off`, see [screen-filters.md](screen-filters.md)). Strings to search: `FSAA_2D`, `mDitherDisplay0`, `NoAADisplay1`; the USA v1.1 default word is `001F076E`. The word sits at `+0x6BA8` (MH4G) and `+0x6B20` (MH Generations) from an object, which could fit inside `sLtRender` (size `0x8AB8`) - a candidate owner, not confirmed.

**P2 - no prior art; this is our work.**

8. **Widescreen.** Start from the MH4G cheat. If its name is accurate, the engine keeps an aspect ratio as two small integers (16 and 9), set by two `mov` instructions with one instruction between them (`00AD7E88` and `00AD7E90` in MH4G JPN). In v1.3.0, search camera code (`sCamera`, `sMhCamera`, `uCamera`) for the unpatched equivalent: probably `mov r0,#5` and `mov r1,#3` (400:240; my guess), or the float literals `43C80000` (400.0), `43700000` (240.0) and `3FD55555` (5/3). Nobody has published whether that edit widens the view or only changes a stored ratio, so test it before building on it. Widening the projection is the small part. The render target is the other: the game draws a 400x240 colour buffer through GPU command lists, and I found nothing on how to make Azahar's renderer present a wider one (my reading; the 3DS top screen also has an 800x240 "wide mode" with 3D off, which I know from general knowledge, not from a page I read, and I did not find whether MH4U uses it). Re-anchoring HUD layouts (`rLayout`, `rGUI`, authored for 400x240) is the larger job.
9. **Second screen, in stages.** First, Azahar's layouts and mouse as touch. Second, an on-screen replica of the panel set (camera, map, item shortcut, kick/special, Target Cam, Hunter Search, Chat), honouring the player's saved panel arrangement. Third, merge the panels into the main HUD following Capcom's MHXX Switch placement (section 3.3), working from the manual's panel list (section 3.2).
10. **Graphics mods.** No MH4U texture pack exists. The texture format is documented (`.tex`, `textool`) and Azahar's dump/replace flow already works, so a pack project is feasible; start with UI atlases once the HUD work is under way.

**P3 - keep in view.**

11. **Formats.** Use dasding's and svanheulen's pages as a starting reference for `rArchive`, `rTexture`, `rModel`, `rMaterial`, `rGUI` and `rQuestData`, and verify each against the maintainer's dump. svanheulen's code is GPLv3 and mh4edit is MIT, both compatible with the port's GPL-3.0-or-later; dasding's tools and the cheat files have no stated licence, so use them as documentation, cite addresses, and do not copy code or files.
12. **Open questions to answer from the dump** (none can be answered from the web): the default cap and speed in v1.3.0; whether `sMain` or another class owns them; the tick unit; whether the logic loop uses a fixed or variable step; where the engine sleeps; how the render word is laid out in v1.3.0; and which update the "v1.1" cheats actually target, which would show how far addresses moved between updates (the `anco/mh4u_qol` patch targets v1.3.0: see the table in section 6).

---

## Pages to ignore and pages not read

**Generated or unreliable (no codes found).** One was opened and checked: `discourse.weareopen.coop/news/mh4u-unlock-60-fps-with` has no code lines and reads as auto-generated filler. Pages with the same titles ("MH4U: Unlock 60 FPS With This Cheat", "Boost MH4U FPS On Citra", "MH4U 120 FPS") appear on `erp.blackboxoperations.com` and `discourse.weareopen.coop`; a family of "citra 60 fps cheat" PDF sites (`ns1.mudfish.net`, `curvymonthly.curvyyoga.com`, `blog.durstus.com`), `edx.ipeindia.org`, `grandavehousing.calpoly.edu`, `autry.cs.grinnell.edu` and `archive.aeon.org` show the same pattern in search results. They were not opened and are not linked here.

**Not opened or not readable.**

- svanheulen's `mhff` wiki pages and JourneyOver's repository: the fetch tool asked for permission because the URLs came from page text rather than search results, and the request went unanswered. The first post of the [ROM Hacking Guide][guide] and the root of the [model extraction thread][extract-thread] were not opened either (only individual posts were read).
- iSharingan's cheat repository and the JourneyOver MH Generations USA file (seen in search results only), Pretendo's own 3DS progress page, Digital Foundry's video, and the `patches/` folder of `anco/mh4u_qol` (the site blocks the fetch tool).
- Azahar's source tree: I did not read it. The one command I tried in order to check GitHub access was denied by the session's policy and I did not retry or work around it, so the cheat-engine and UDS notes come from a Citra source mirror, release notes and issues.
- Blocked or errored: GameTechWiki's aspect-ratio and 30 FPS code pages (its 60 FPS page lists no 3DS codes), Nintenderos, RPG Site's Switch 2 article, and the Lime3DS-hosted copy of compatibility issue #14 (the Azahar-hosted copy was read).
- Reddit threads could not be searched (the search proxy rejected the domain filter), and Discord content is only known second-hand through issue comments.

<!-- Reference-style links -->

[rs-repo]: https://github.com/Reshiban/60FPS-AR-CHEATS-3DS
[rs-usa]: https://github.com/Reshiban/60FPS-AR-CHEATS-3DS/blob/master/Cheats/Monster%20Hunter%204%20Ultimate%20%28USA%29/0004000000126300.txt
[rs-eur]: https://github.com/Reshiban/60FPS-AR-CHEATS-3DS/blob/master/Cheats/Monster%20Hunter%204%20Ultimate%20%28EUR%29/0004000000126100.txt
[rs-jpn]: https://github.com/Reshiban/60FPS-AR-CHEATS-3DS/blob/master/Cheats/Monster%20Hunter%204%20Ultimate%20%28JPN%29/000400000011D700.txt
[rs-kor]: https://github.com/Reshiban/60FPS-AR-CHEATS-3DS/blob/master/Cheats/Monster%20Hunter%204%20Ultimate%20%28KOR%29/0004000000153200.txt
[rs-gen]: https://github.com/Reshiban/60FPS-AR-CHEATS-3DS/blob/master/Cheats/Monster%20Hunter%20Generations%20%28EUR%29/0004000000185B00.txt
[rs-3u]: https://github.com/Reshiban/60FPS-AR-CHEATS-3DS/blob/master/Cheats/Monster%20Hunter%203%20Ultimate%20%28USA%29/00040000000AE400.txt
[rs-3u-jpn]: https://github.com/Reshiban/60FPS-AR-CHEATS-3DS/blob/master/Cheats/Monster%20Hunter%203%20Ultimate%20%28JPN%29/0004000000048100.txt
[rs-3u-eur]: https://github.com/Reshiban/60FPS-AR-CHEATS-3DS/blob/master/Cheats/Monster%20Hunter%203%20Ultimate%20%28EUR%29/00040000000B1D00.txt
[jo-repo]: https://github.com/JourneyOver/CTRPF-AR-CHEAT-CODES
[is-repo]: https://github.com/iSharingan/CTRPF-AR-CHEAT-CODES
[jo-gen-usa]: https://github.com/JourneyOver/CTRPF-AR-CHEAT-CODES/blob/master/Cheats/Monster%20Hunter%20Generations%20%28USA%29/0004000000187000.txt
[gb-ctrpf]: https://www.gamebrew.org/wiki/CTRPF_AR_Cheat_Collections_3DS
[kronous]: https://gbatemp.net/threads/cheat-monster-hunter-4g-v1-2-jp.655972/
[hazerou-guide]: https://gbatemp.net/threads/finding-fps-and-rendering-codes-on-most-capcom-games.549440/
[fps-thread]: https://gbatemp.net/threads/60-fps-patches-cheat-codes-releases-and-discussion.550527/
[fps-thread-37]: https://gbatemp.net/threads/60-fps-patches-cheat-codes-releases-and-discussion.550527/page-37
[mhgu60]: https://gbatemp.net/threads/monster-hunter-generations-ultimate-60-fps-hack.553053/
[mhgu60-cheats]: https://gbatemp.net/threads/monster-hunter-generations-ultimate-60-fps-hack.553053/post-8875029
[mhgu60-p1]: https://gbatemp.net/threads/monster-hunter-generations-ultimate-60-fps-hack.553053/post-8877414
[mhgu60-p3]: https://gbatemp.net/threads/monster-hunter-generations-ultimate-60-fps-hack.553053/page-3
[mhgu60-p5]: https://gbatemp.net/threads/monster-hunter-generations-ultimate-60-fps-hack.553053/page-5
[mhxx-q]: https://gbatemp.net/threads/mhxx-hacking-question.565003/
[eden-620]: https://github.com/eden-emulator/Issue-Reports/issues/620
[potobc]: https://github.com/Potobc/MHGU-Rebalanced-Mod
[cheatasm]: https://github.com/adrianmgg/CheatASM
[citra-cheats-cpp]: https://git.openpunk.com/OpenPunk/citra/src/commit/2f3142e1920dac6bad750934f842b3c1a7b08f00/src/core/cheats/cheats.cpp
[citra-gateway]: https://git.openpunk.com/OpenPunk/citra/src/commit/32b88d47192a0b980d0d9cabd40f7636a08b522a/src/core/cheats/gateway_cheat.h
[citra-rasp-legacy]: https://github.com/fadillzzz/citra-rasp-legacy
[siliconera-xx]: https://siliconera.com/?p=651435
[nw-df]: https://nintendowire.com/news/2017/08/18/digital-foundry-compares-monster-hunter-xx-switch-3ds/
[gameblog]: https://www.gameblog.fr/jeu-video/ed/news/les-nouveaux-jeux-3ds-moins-fluides-sur-le-modele-standard-notre-verdict-44849
[gtw-wide]: https://emulation.gametechwiki.com/index.php/Widescreen_Hack
[ds-wide]: https://wiki.ds-homebrew.com/twilightmenu/playing-in-widescreen
[gtw-textures]: https://emulation.gametechwiki.com/index.php/Texture_Packs
[dualshockers-xx]: https://www.dualshockers.com/monster-hunter-xx-switch/
[gematsu-xx]: https://www.gematsu.com/2017/06/monster-hunter-xx-nintendo-switch-ver-details-target-camera-hunter-arts-vibration-changes
[pn-xx]: https://www.perfectly-nintendo.com/?p=219918
[gi-mhgu]: https://gameinformer.com/review/monster-hunter-generations-ultimate/touching-up-the-past
[nl-guide]: https://nintendolife.com/news/2018/08/guide_getting_started_with_monster_hunter_generations_ultimate_on_nintendo_switch
[man-touch]: https://game.capcom.com/manual/MH4U/en/page-26.html
[man-top]: https://game.capcom.com/manual/MH4U/en/page-24.html
[man-town]: https://game.capcom.com/manual/MH4U/en/page-22.html
[man-contents]: https://game.capcom.com/manual/MH4U/en/contents2.html
[man-local]: https://game.capcom.com/manual/MH4U/en/page-152.html
[man-internet]: https://game.capcom.com/manual/MH4U/en/page-153.html
[gen-town]: https://game.capcom.com/manual/MH_Gen/en/page-22.html
[patent]: https://patents.justia.com/patent/9452357
[citra-net]: https://citra.azahar-emu.org/entry/announcing-networking-support/
[citra-pr-2018]: https://citra.azahar-emu.org/entry/citra-progress-report-2018-q1/
[citra-mp-help]: https://citra.azahar-emu.org/help/feature/multiplayer/
[citra-textures]: https://citra.azahar-emu.org/help/feature/custom-textures/
[citra-mods]: https://citra.azahar-emu.org/help/feature/game-modding/
[nl-regions]: https://www.nintendolife.com/news/2014/07/monster_hunter_4_ultimate_multiplayer_will_work_across_na_and_european_versions
[nw-shutdown]: https://nintendowire.com/news/2024/04/06/capcom-urges-monster-hunter-fans-to-re-download-3ds-and-wii-u-dlc-before-its-too-late/
[nl-shutdown]: https://www.nintendolife.com/news/2024/04/capcom-reminds-monster-hunter-fans-about-3ds-and-wii-u-online-shut-down
[mhos]: https://forum.pretendo.network/t/how-to-access-mh-oldschool-monster-hunter-dlc-servers-for-3ds-and-wiiu/10126
[pretendo-stories]: https://forum.pretendo.network/t/monster-hunter-stories-in-pretendo/25176
[consolemods-pretendo]: https://consolemods.org/wiki/3DS:Pretendo
[aa-rooms]: https://androidauthority.com/azahar-3ds-emulator-multiplayer-android-3686723
[libretro-azahar]: https://docs.libretro.com/library/azahar/
[joey]: https://joeysretrohandhelds.com/guides/azahar-pc-setup-guide/
[retrodeck-az]: https://retrodeck.readthedocs.io/en/latest/wiki_emulator_guides/azahar/azahar-guide/
[az-860]: https://github.com/azahar-emu/azahar/discussions/860
[az-2126.0]: https://newreleases.io/project/github/azahar-emu/azahar/release/2126.0
[az-2126.1a1]: https://newreleases.io/project/github/azahar-emu/azahar/release/2126.1-alpha1
[az-2126.1rc4]: https://newreleases.io/project/github/azahar-emu/azahar/release/2126.1-rc4
[az-2126.1.1]: https://github.com/azahar-emu/azahar/releases/tag/2126.1.1
[az-2121rc1]: https://newreleases.io/project/github/azahar-emu/azahar/release/2121-rc1
[az-compat-14]: https://github.com/azahar-emu/compatibility-list/issues/14
[az-compat-97]: https://github.com/azahar-emu/compatibility-list/issues/97
[az-726]: https://github.com/azahar-emu/azahar/issues/726
[az-769]: https://github.com/azahar-emu/azahar/issues/769
[az-765]: https://github.com/azahar-emu/azahar/issues/765
[az-875]: https://github.com/azahar-emu/azahar/issues/875
[az-1443]: https://github.com/azahar-emu/azahar/issues/1443
[az-1567]: https://github.com/azahar-emu/azahar/issues/1567
[az-1632]: https://github.com/azahar-emu/azahar/issues/1632
[az-1737]: https://github.com/azahar-emu/azahar/issues/1737
[az-1852]: https://github.com/azahar-emu/azahar/issues/1852
[az-2051]: https://github.com/azahar-emu/azahar/issues/2051
[az-2160]: https://github.com/azahar-emu/azahar/issues/2160
[az-2226]: https://github.com/azahar-emu/azahar/issues/2226
[az-2231]: https://github.com/azahar-emu/azahar/issues/2231
[az-2531]: https://github.com/azahar-emu/azahar/issues/2531
[guide]: https://gbatemp.net/threads/mh4u-mhx-rom-hacking-guide.425059/
[guide-p2]: https://gbatemp.net/threads/mh4u-mhx-rom-hacking-guide.425059/post-6923805
[guide-p5]: https://gbatemp.net/threads/mh4u-mhx-rom-hacking-guide.425059/post-7678624
[guide-p8]: https://gbatemp.net/threads/mh4u-mhx-rom-hacking-guide.425059/post-9121504
[extract-thread]: https://gbatemp.net/threads/how-to-extract-mh4u-models.383040/post-5587739
[mca2wav]: https://github.com/mhdasding/mca2wav
[mhff-models]: https://github.com/svanheulen/mhff/wiki/Monster-Hunter-4-Ultimate-Models
[mhff-quest]: https://github.com/svanheulen/mhff/wiki/MHXX-rQuestData-Format
[mhff-blender]: https://github.com/svanheulen/mhff/blob/master/io_import_scene_mod.py
[mhef-thread]: https://gbatemp.net/threads/mh4u-dlc-and-savefile-encryption.392183/post-5561911
[mhxqe]: https://gbatemp.net/threads/release-mhx-quest-editor.409420/
[mh4edit]: https://github.com/Rokumaehn/mh4edit
[gb-mh4edit]: https://www.gamebrew.org/wiki/MH4U_Save_Editor_3DS
[gb-vermil]: https://www.gamebrew.org/wiki/MHXX_1st_%26_2nd_Monster_Size_Plugin_3DS
[apm]: https://gbatemp.net/threads/apm-mhx-mhgen-save-editor.434421/
[talisman]: https://gbatemp.net/threads/release-mh-talisman-editor-for-mhxx-mhx-mhgen-mh4g-mh4u.411182/
[mh4u-tool]: https://github.com/eesandoval/MH4U-Tool
[arctool]: https://www.patreon.com/posts/36746106
[arcunpacker]: https://www.moddb.com/games/resident-evil-revelations/downloads/arc-unpacker-repacker-v09428
[qol]: https://codeberg.org/anco/mh4u_qol
[invincibility]: https://gbatemp.net/threads/monster-hunter-4-ultimate-invincibility-cheat-help.668575/post-10613015
[wiki-mtf]: https://www.wikipedia.com/wiki/MT_Framework
[siliconera-engine]: https://siliconera.com/?p=86890
