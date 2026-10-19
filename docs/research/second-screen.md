# The bottom (touch) screen: drawing, touch input and a PC design

How Monster Hunter 4 Ultimate draws its second screen, how a touch becomes a panel action, and what a PC port can hook to show the panels elsewhere or bind them to keys. Addresses apply to **USA v1.3.0** only.

Method: static reading of the code image (`build/code_v130_dec.bin`) with the Ghidra export, `tools/disasm.py` and one-off scripts (class vtables were found through each class's `getDTI` virtual, see [Method](#method)). Nothing here was run on the game, so every runtime claim is marked with a confidence and the open points are listed at the end. Names such as `PanelManager` or `LayoutObject` are ours.

Confidence: **high** = read directly in the code, several independent confirmations; **medium** = read in the code but the meaning is inferred; **low** = a lead.

## Summary

- **The bottom screen is camera viewport 7.** `sCamera` (instance `0x0105729c`) owns 8 viewports. Each has a `mDisplay` byte: 0 = top screen, 1 = bottom screen. The game sets viewport 7 to the bottom screen with its own camera and region (`sMhCamera::setupViewports` `0x00bfd9b0`, area setup `0x009abf2c`); viewport 0 is the main view on the top screen. (high)
- **The touch panels, the map and the bottom menus are ordinary MT layout objects** (`rLayout` files under `lyt\`, drawn by one `uLayout` unit owned by `sMhLayout`). The same unit holds the top-screen HUD. Each object carries a 4-bit *view* number; the layout draw draws an object only in the viewport with that number. `sMhLayout` gives top-screen layouts view 0 and bottom-screen layouts view 7, from per-file screen tables. (high)
- **One render pass per screen.** `sLtRender::buildFrame` (`0x00bf5a4c`) calls `sCamera::draw` for display 0 when the top-present flag is set and for display 1 when the bottom-present flag is set (or when the HOME/applet layer needs it), then copies each result to that screen's display buffer. The flags come from `mDisplaySkip`; both start at 1 and stay 1 while the skip is 0, so **the bottom screen is redrawn and presented every frame**. Nothing skips an unchanged bottom screen. (high)
- **Touch path:** HID shared memory → `sPad` (touch ring buffer, touch bit `0x01000000`) → `sMhPad::update` (`0x00694c98`) builds a touch block at `sMhPad+0x3e0` → panel code converts it to layout coordinates (`0x00af3cfc`), hit-tests layout rectangles (`0x00d03938`) inside small "touch groups", and turns hits into panel ids and events. (high)
- **Panels act through three channels**, which are also the hook points for key bindings: (1) gameplay action bits written into `sMhPad`'s action block or OR-ed into its injection word `sMhPad+0x3f0` (the virtual camera pad sets the D-pad bits; one action bit, `0x0001`, has no button at all and is injected when panel 7 is tapped); (2) menu action bits OR-ed into `sMhPad+0x3f4`; (3) direct calls into game code (item scrolling `0x00b31fa4`, player camera functions, map scrolling). A central query, `PanelManager::isTapped(id)` (`0x00288f4c`), is polled by about 40 call sites. (high for the mechanisms, medium for which panel is which)
- **Overlay is feasible inside the engine:** moving a bottom layout to view 0 makes the top pass draw it at native resolution, centred on the top screen, and a mouse click at top-screen (X, Y) then maps to touch (X − 40, Y). A frontend composite of the bottom framebuffer is cheaper and needs no hooks. Retargeting viewport 7 to the top display also works but crops instead of scaling unless the layout projection is hooked. (high for the mechanism, untested)
- **Not found statically:** the saved panel arrangement, and the names behind the panel hash ids. Both need either a run-time trace or the RomFS layout files (see [Open questions](#open-questions)).

## 1. What draws the bottom screen

### 1.1 Viewports and displays

`sCamera` (DTI `0x010e84d0`, size `0xe10`; the game instance is an `sMhCamera`, size `0x16d0`) has its constructor at `0x00af1f04`, which stores `this` in **`0x0105729c`**. It holds 8 `sCamera::Viewport` objects at `+0x18`, `0x1a8` bytes apart (viewport *i* at `sCamera + 0x18 + i*0x1a8`). Field offsets come from the viewport's property registration `0x00af0038`:

| Offset | Field | Notes |
|---|---|---|
| `+0x04` | `mpCamera` | camera unit used for this viewport |
| `+0x0c` | `mpSceneTexture` | |
| `+0x10` | `mVisible` (bool) | |
| `+0x12` | `mSubScene` (bool) | sub-scene viewports are skipped by the screen pass |
| `+0x13` | `mNo` (u8) | constructor sets *i* |
| `+0x14` | `mPriority` (u8) | constructor sets 7 − *i* |
| `+0x15` | `mMode` (u8) | |
| `+0x16` | **`mDisplay` (u8)** | 0 = top, 1 = bottom |
| `+0x17`, `+0x19`, `+0x1c`, `+0x20` | `mSubSceneViewport`, `mSubScenePass`, `mSubScenePriority`, `mSubSceneClearColor` | |
| `+0x24` | `mRegion` (rect, s32 ×4) | in the 240×400 rotated render-target space |

The `sCamera` constructor makes viewport 0 visible and sets viewport 1's `mDisplay` to 1 (an engine default). MH4U replaces that:

- **`sMhCamera::setupViewports` `0x00bfd9b0`** (called through `0x00bfced0` from five area/scene set-ups) puts the camera at `sMhCamera+0xe0c` into viewport 0 and the camera at **`sMhCamera+0xe10`** into **viewport 7**, then sets viewport 7 visible, `mDisplay = 1` and `mMode = 0`.
- **`0x009abf2c`** (slot 2 of a `cArea`-derived vtable, so an area's start-up) creates the cameras, registers them with `sUnit`, and sets viewport 7's region to **(0, 80)–(240, 400)**: 240×320 pixels, the bottom screen, inside the 240×400 target.

So viewport 0 = top screen (3D scene and top HUD), viewport 7 = bottom screen (confidence high).

### 1.2 Render passes

- `sLtRender`'s render virtual `0x00bfa670` ends in **`sLtRender::buildFrame` `0x00bf5a4c`**:
  - when the top flag `+0x8ab1` is set: `sCamera::draw(ctx, target, display 0)` (vtable `+0x28`), a second eye pass when stereo 3D is on, then a transfer into the top display buffer(s);
  - when the bottom flag `+0x8ab2` is set, **or** the `sApplet` state byte `[[0x0104dea0]+0x30]` is 4, 5 or 6 (HOME menu and other applet overlays): `sCamera::draw(ctx, target, display 1)`, the applet's own bottom-screen draw (`0x00ad3834` on `[0x0104dea0]+0x28`), then a transfer into the bottom display buffer.

  Both passes render into the same main colour target (`sLtRender+0x6c28`) one after the other; the bottom image is the 240×320 viewport-7 region of it, copied out (medium).
- **`sCamera::draw` `0x00aee5d4`** (vtable `+0x28`, args `this, ctx, target, display`) walks the 8 viewports and handles those that are visible, not sub-scenes, and have `mDisplay == display`. For each it writes the viewport index into bits 1–5 of the draw context word `ctx+0x1bc` (and the priority into `ctx+0x1b8`), computes the 3D projection from the region, sets up the 2D layout projection (`sLayout::beginViewport` `0x00af3d60`: an orthographic projection whose size is the viewport rectangle in pixels, only when the viewport has a camera), and draws the units through `sUnit` (vtable `+0x34`); the `uLayout` is one of those units.
- The present step (`0x00bf894c`) and the flags `+0x8ab1`/`+0x8ab2` are described in [system-services.md](system-services.md#present-and-buffer-swap-confidence-high-on-the-flow-medium-on-flag-meanings).

### 1.3 Layouts and the view number

The game UI is MT's **layout system**: `rLayout` resources (paths under `lyt\`), `uLayout` units, `sLayout`/`sMhLayout` manager. The newer `rGUI` system (§1.4) is not used for the game's HUD and menus.

- **`sMhLayout`** (DTI `0x010b514c`, size `0x248`, constructor `0x00c11930`, vtable `0x00e6cbdc`) is the instance behind **`0x01057534`** (the `sLayout` constructor `0x00af41b8` stores `this` there). Its constructor sets **`+0x245 = 0`** (view number of the upper screen) and **`+0x246 = 7`** (lower screen). `+0x100` points to the single `uLayout` that holds every loaded layout object; objects are addressed by a global index (`uLayout::getObject` `0x00b048a0`: array `+0x154`, count `+0x160`).
- **`sMhLayout::registerLayout` `0x00c0f424`** (`this, screen, firstIndex, rLayout*`, returns the object count) creates the objects of one `rLayout` at `firstIndex…`, hides each one (the panel code shows them when needed) and, if needed, rewrites its view field with `[sMhLayout + 0x245 + screen]` (flagging it dirty). Every loader calls it with a screen number from a per-file table or a constant.
- **`uLayout::draw` `0x00b04774`** (vtable `0x00e6bb40` slot 12, `+0x30`) takes the viewport index from `ctx+0x1bc` and draws only the objects whose view field matches and whose visible bit is set. That is the whole difference between the top HUD and the bottom panels.

Layout object fields used here (medium-high; offsets from the functions named):

| Offset | Meaning | Seen in |
|---|---|---|
| `+0x00` | name hash (`~crc32` of the pane name, see below) | `uLayout::findIndex` `0x00b04558` |
| `+0x28`, `+0x2c` | position (f32, layout units = pixels, origin at the screen centre) | hit test `0x00d03938` |
| `+0x34`, `+0x38` | offset (f32) | hit test |
| `+0x42` | dirty byte (3 after a change) | `0x00c0f424` |
| `+0x44` | bit 10 (`0x400`) visible; **bits 14–17 view number**; bits 18+ draw priority | `0x00ae538c`, `0x00b04774`, `0x00c0f424` |
| `+0x48` | next object in the priority-sorted draw list | `0x00b04774`, `0x00ae538c` |

`LayoutObject::setVisible` is `0x00ae538c` (about 2,000 call sites; it also re-sorts the draw list). Objects are found by name with `makeNameKey` `0x005ed628` (hash = `crc32_string(name, 0xFFFFFFFF)` from `0x005bf888`, i.e. the bitwise NOT of the standard CRC-32) and `sMhLayout::findObject` `0x00c0f598`.

**Which layouts go to which screen.** The loaders and their screen tables (screen 1 = bottom, view 7):

| Loader | Files | Screen |
|---|---|---|
| `0x00c1012c` quest HUD | `lyt\quest\` `ui200`–`ui208`, `ui211`, `ui212`, `ui220`–`ui222` | top (table `0x00efe1d0` = 0) |
| | **`ui601`, `ui602`, `ui603`, `ui604`, `ui610`, `ui250`** | **bottom** (table `0x00efe1d0` = 1) |
| | area map `lyt\quest\mNN\ui25N` (by map number, table `0x00efe220`; area 1 variants `0x00efe500`) | bottom (constant 1) |
| | alternative set when `[[0x00fb6b7c]+0xd30+0x42]` is set: `lyt\lobby\ui441` | per table `0x00efe21c` |
| | animation lists `ui202_*`, `ui204_gauge`, `ui205_select`, `ui206`, `ui207`, **`panel`, `panel_sc`** (`rLayoutAnimeList`, stored at `sMhLayout+0x188`) | — |
| `0x00c105a4` quest sub-layout by index | `lyt\quest\ui230`–`ui243` (table `0x00ef9180`), loaded into the last 10 index slots | bottom (constant 1) |
| `0x00c106f0` common | `lyt\common\ui0xx`, `ui490`, `panel_low`, `lyt\quest\ui223` (tables `0x00efdc60`, `0x00efdd0c`) | per table `0x00efdce8` |
| `0x00c0ff3c` village / gathering hall | `lyt\lobby\ui4xx`, `ui5xx`, … (tables `0x00efdf44`, `0x00efdd44`) | per table `0x00efe000` |
| `0x00c0faa8` cat / ending | `lyt\lobby\cat\ui7xx`, `lyt\end\…` (table `0x00eff240`) | per table `0x00eff2f8` |
| `0x00c10e20`, `0x00c11394` | both screens (constants 1 and 0) | |
| `0x00c0f5b8`, `0x00c0f8fc`, `0x00c0fd4c`, `0x00c10a88`, `0x00c115ec` | DLC, demo/title, select, `ui209`/`ui210`, ending | from a register |

The `_l_` in texture/pane names such as `ui251_l_map`, `ui281a00_l_area`, `ui270a00_l_icon` (string tables around `0x00ed16c0` and `0x00ee8188`) most likely means "lower screen" (medium).

### 1.4 The other GUI system (rGUI) is dual-screen aware but not used for the HUD

MT Framework Mobile's newer GUI (`rGUI`, `uGUI`, `sGUI`, `cGUI*`) has explicit dual-screen support: `cGUIInstance` registers **`DrawUpper`/`DrawLower`** (`0x002860c0`), `rGUI` and `uGUI` have `DualScreen`, and `sGUI` (instance `0x010572ac`, constructor `0x005b6454`, properties `0x005b5888`) has `UpperView` (`+0x70`, default 0), `LowerView` (`+0x71`, default 1) and `LowerViewOffset` (`+0x80`, default (−40, −360)). Only MT library code references the `rGUI` class (7 functions, all in `0x005b3334…0x005b4384` and DTI glue); the game loads its HUD and menus as `rLayout`. So the overlay work targets the layout system (confidence medium-high: the software keyboard and the cutscene subtitles, `uGUISubtitles`, may still use rGUI).

### 1.5 Frame rate of the bottom screen

- `sLtRender::updateDisplaySkip` `0x00bfa6f0` recomputes `+0x8ab1`/`+0x8ab2` only when `mDisplaySkip[0]`/`[1]` (`+0x8aa8`/`+0x8aac`) is non-zero; the constructor sets both flags to 1. With the default skip 0 the bottom screen is **drawn and presented every frame**, at whatever rate the top runs.
- `mDisplaySkip[1] = 1` would give a 30 Hz bottom screen (both the draw and the present). A port that sets it and later clears it must also write `+0x8ab2 = 1`, because a zero skip leaves the last computed flag in place.
- No change detection was found: the bottom pass re-renders everything visible in viewport 7. The applet states 4–6 force the bottom pass regardless of the flag.

## 2. Touch input and panel actions

### 2.1 From HID to `sPad`

- `sPad::update` (`0x005b825c`) calls **`sPad::readDevices` `0x005b70b0`**, which reads the pad, the extra pad (Circle Pad Pro) and the touch reader (`sPad+0x2d8`, `0x0048592c`) into a stack buffer, then **`sPad::processInput` `0x005b8438`**:
  - pad buttons are re-encoded (A `0x2000`, B `0x4000`, X `0x1000`, Y `0x8000`, L `0x100`, R `0x200`, ZL `0x400`, ZR `0x800`, Start `0x8`, D-pad up/down/left/right `0x10`/`0x40`/`0x80`/`0x20` plus circle-pad direction bits in the high half);
  - **touch sets bit `0x01000000`** in the button word; the latest sample goes to `Pad+0x8a`/`+0x8c` (x, y, adjusted by `mTouchAdjustOffset` = (160, 120) and `mTouchAdjustBias` when `sPad+0x300` bit 0 is set) and raw to `sPad+0x198`/`+0x19c`;
  - every sample is also appended to a 32-entry ring of {u16 x, u16 y, u8 down} at `sPad+0xc0` (the `sPad::TouchStatus` object at `sPad+0xbc`; write index `+0x180`).
- Button words of the pad (`sPad+0x2c` = `Pad`): `+0x60` held, `+0x64` previous, `+0x68` pressed, `+0x6c` released, `+0x70` changed, `+0x74` repeat; sticks at `+0x78…+0x84`.

### 2.2 `sMhPad`: actions, menu bits and the touch block

`sMhPad` (DTI `0x010b5280`, size `0x448`, constructor `0x0069522c`) is the instance at `0x010572e0`. **`sMhPad::update` `0x00694c98`** runs `sPad::update` and then fills these blocks (confidence high for layout, medium for names):

| Block | Offset | Contents |
|---|---|---|
| **gameplay actions** | `+0x30c` | u16 held, `+0x30e` previous, `+0x310` pressed, `+0x312` released, `+0x314` repeat, `+0x318` repeat timer; a second set at `+0x31c` from the right-hand stick/CPP; per-frame copies at `+0x3f8`. Built from the pad through the 16-entry table **`0x00fb8260`** (u32 pad masks, u16 action bits at `+0x40`; entries 9 and 11 depend on the control type) |
| **injected gameplay actions** | **`+0x3f0`** | u32 action bits OR-ed into the held word during the mapping, then cleared. Touch panels write here |
| **injected menu actions** | **`+0x3f4`** | consumed by `sMhPad::updateMenuInput` `0x006949a0`, which builds the menu block at `+0x340` (copied by the panel manager each frame); then cleared |
| **touch** | **`+0x3e0`** | u16 x, `+0x3e2` previous x, `+0x3e4` y, `+0x3e6` previous y, `+0x3e8` down, `+0x3e9` previous down, `+0x3ea` began, `+0x3eb` ended, `+0x3ec` moved. Filled from `sPad::TouchStatus::getSample` `0x00cf9340` (ring entry *n* frames back); **cleared while `[[0x00fb6b7c]+0x2fc0]` is non-zero** (`0x00fb6b7c` holds the `sGameControl` instance) |

Default action bits from `0x00fb8260`: D-pad up → `0x2000`, down → `0x1000`, left → `0x0800`, right → `0x0400` (the D-pad turns the camera), Y → `0x0200`, X → `0x0100`, R → `0x0080`, B → `0x0040`, A → `0x0020`, ZR → `0x0010`, L → `0x0008`, ZL → `0x0004`, Start → `0x8000`. **Action bit `0x0001` has pad mask 0: no button produces it; only `+0x3f0` can.** The local player copies the action block into its own input state once per frame (`0x00b1cda0`, called from `sPlayer`'s update, vtable `+0x1c` = `0x00af9138`), so an injected bit acts on the next player update.

### 2.3 Hit testing

- **`sLayout::screenToLayout` `0x00af3cfc`** (`out, sLayout*, x, y, screen`): layout point = (*cx* − x, 120 − y), with *cx* = 160 for screen 1 (bottom) and 200 otherwise. Every touch consumer passes screen 1.
- **`0x00d03938`** (`entry, point`): an entry is {object*, part*}; the point minus the object's position and offset is tested against the part's rectangle at `[[part+8]+0x20…+0x2c]` (min x, min y, max x, max y).
- Panels group their buttons in **touch groups**: N entries of 8 bytes, then a count, the id of the pressed entry, a state byte and flags. Nine routines of the same shape handle them, eight of them identical in size (`0x00d8e444`, `0x00d8e9d0`, `0x00d8ef5c`, `0x00d8f4e8`, `0x00d8f9d0`, `0x00d8ff5c`, `0x00d904e8`, `0x00d90aec`, `0x00d91078`; arguments `group, idTable, count`). They return the index of the entry in the id table plus event flags: `0x8000` no entry, `0x4000` pressed on an entry, `0x0800` released on the entry it was pressed on, `0x0200` activated (what `isTapped` tests; set on release or on press depending on the group's mode byte), `0x1000`/`0x2000` held or dragged, `0x0400` no change (medium: read in `0x00d8ff5c` and `0x002951fc`, not traced at run time).
- The id tables are 32-bit **name hashes** (for example `0x00defc00+0x21c`, `0x00df00b4`, `0x00ef3164`). None matched the ~19,600 strings in the code image or a dictionary of `uiNNN_<word>` names, so the pane names live in the layout files only.

### 2.4 The panel manager

A self-contained module at **`0x00287000–0x002a5900`** owns the bottom-screen panels; quests, the village and many menus call into it. Its data is a static block at **`0x0109a350`** (we call it `PanelManager`):

| Address | Meaning | Confidence |
|---|---|---|
| `0x0109a350 + 0x00` | copy of `sMhPad`'s menu block (`+0x340`, `0xa0` bytes), refreshed each frame | high |
| `+0xa0…` | manager state machine (state byte `+0xa0`, sub-state `+0xa1`) | medium |
| `+0xc2`, `+0xc4` | u16 masks of disabled/hidden panel ids (bit per id) | medium |
| **`+0xcc`** | event flags of the last panel touch (`0x0200` = tapped) | high |
| **`+0xce`** | id of the panel touched | high |
| `+0x1790 + k*0x18`, `+0x1808`, `+0x1828` | touch groups | high |
| `0x0109cdc8` | table of layout object pointers per panel id; byte pairs at `+0x1f8` (3 entries) look like a slot → panel id table | medium / low |
| `0x0109bd38` | quest big-panel controller (mode `+0x588`, zoom `+0x58a`, map scroll `+0x5b0`/`+0x5b2`, `+0x5b4`/`+0x5b8`) | medium |

Functions:

- **`PanelManager::update` `0x002a4468`**: once per frame from the areas' update: the quest game-system area (`aGameSys` vtable slot 3 `0x00b7e240` → `0x00b7f094`), another `aGameSys`-family area through `0x002b5b50`, and `0x00acccd0`. Copies the menu block, runs the state machine and the panel updates.
- **`PanelManager::processTouch` `0x002951fc`**: runs the manager's touch groups and writes `+0xce`/`+0xcc`.
- **`PanelManager::isTapped(id)` `0x00288f4c`**: true when `+0xce == id`, `+0xcc` has `0x0200`, and the id is not masked by `+0xc2`/`+0xc4`. Panel ids are small integers (masks are 16 bits; the slot code accepts ids below `0x14`). Call sites by id: **8** at about 35 sites (village and quest menus, HUD), **0xb** in 4, **9** and **7** in the quest HUD state machine, **6** in one menu. What each id is needs a run-time check.
- `0x0029e5bc(id, slot)` and `0x0029f4e4(slot)` manage the 3-entry table at `0x0109cdc8+0x1f8` (low).

### 2.5 The quest HUD and the known panel actions

The quest HUD controller is a static object at **`0x0108260c`** (getter `0x0013f230`, 113 callers; code at `0x00b7f800–0x00ba8000`, most of it called back from the panel manager).

| Panel / control | Path | Effect | Confidence |
|---|---|---|---|
| **Big-panel tabs** | touch group at HUD `+0x5a8` (ids `0x00df00b4`), read by `0x00ba156c`; dispatched by `0x00298ff4` | tabs 0–2 select mode 1, 2, 3 (`0x0109bd38+0x588`), tab 3 resets the view | medium |
| **Map (mode 1)** | `0x0029b658` | dragging scrolls the map (`+0x5b0`/`+0x5b2`); D-pad bits also scroll; redraw via `0x00ba0bf0` | medium |
| mode 2 | `0x0029b754` | dragging changes two floats (`+0x5b4`/`+0x5b8`) | low |
| **Virtual camera pad (mode 3)** | `0x00b862ac` → `0x00b9b05c` (touch → {code, bits}) → **`0x00b88efc`** | bits `0x2000`/`0x1000`/`0x0800`/`0x0400` = camera up/down/left/right. In `game_mode` 2 (`sGameControl+0x2b`) they are OR-ed into the action block (`+0x30c` held, `+0x310` pressed); otherwise the player functions `0x00b1c5ec`, `0x00b46b38` and, for some weapon types, `0x00b3a634` are called directly | medium |
| **Panel 7** | `isTapped(7)` in the HUD state machine `0x00b9dfe0` | sets bit `0x0001` in `sMhPad+0x3f0` (the action bit with no button): **the kick**. Writing it there from a hook makes the hunter kick (run-time, October 2026; see below) | high |
| **Panel 9** | `isTapped(9)` in `0x00b9dfe0` | sets bit `0x0400` in `sMhPad+0x3f4` (a menu action) | high / low |
| **Item bar swipe** | `0x00ba6b38` | horizontal drag calls **`0x00b31fa4(player, index, forward)`** (next/previous pouch slot) and stores the result at `[[player+0xa29c]+0x584]`; the player comes from `sPlayer::getLocalPlayer` `0x00af7954` on `[0x00fb83e8]` | medium |
| **Target camera** (panel type 13) | driver **`0x00b94804`** (each frame from the HUD update `0x00b8266c`) → taps in **`0x00b8d024`** (touch group at HUD `+0x460`, hash `0xd77e6342`; two-monster layout `+0x478`) | toggles HUD `+0x274`/`+0x275` (one per large monster) and sets **`sCamera+0xed5`** (`0` off, `n` = monster *n*); HUD byte **`+0x27e`** is a request that counts as a tap (one monster: on/off; two: first, second, off) and is cleared at the end of the same HUD update (`0x00b9dfe0`); a monster whose flags at HUD `+0x26e`/`+0x26f` have `0xe0` set (not seen yet) gets the "no" sound (id 3) and no change. The game sets `+0x27e` itself from menu action `0x8000` (`sMhPad+0x348`) only when `sGameControl+0x2b` is 1. The panel's drawing is `0x00b93738` (monster icon state from `0x00c03b2c(sCamera, i)`) | high (code), run time: a tap and the request both reach the "no" branch with the monster unseen |

The remaining panels (kick vs. special vs. skip, target camera selection, map zoom, shortcuts, hunter search, chat) are reachable through the same three channels; mapping each id is the first run-time task (§5, stage 0).

## 3. Panel layout data

- **Positions and sizes are layout data.** Each panel button is a layout object; its position is `obj+0x28`/`+0x2c` (+ offset `+0x34`/`+0x38`) in pixels from the screen centre, and its hit rectangle is in the part data. They come from the `rLayout` files; panel code changes them at run time in a few places (`0x002a0984`, `0x002a0b8c`, `0x00292900`, `0x002a3c14`).
- **Panel ids** are small integers handled by the panel manager; the buttons inside a panel are identified by name hashes (§2.3).
- **The saved arrangement was not found.** Leads: the slot table at `0x0109cdc8+0x1f8` (3 entries of {u8 id, u8}) with `0x0029e5bc`/`0x0029f4e4`; the panel-customisation screens are among the many external callers of the panel manager. The community found no description of the on-disk format either ([community-enhancements.md §3.4](community-enhancements.md#34-not-found)). Cheapest way to find it: diff two saves that differ only in panel layout, or watch writes to `0x0109cdc8+0x1f8` when a quest starts.

## 4. Overlay feasibility

**Same system for both screens.** The top HUD and the bottom panels are objects of the same `uLayout`, drawn by the same function, separated only by the view field (0 vs. 7). So the engine can draw bottom-screen panels on the top screen without new drawing code. Options:

| Option | How | Pros | Limits |
|---|---|---|---|
| **A. Frontend composite** | the PC frontend draws the bottom framebuffer onto the top (scaled, translucent, movable), like Azahar's layouts | no game hooks; all panels and menus work; mouse → touch is a fixed transform | the panel image is 320×240 scaled; the bottom pass still renders every frame |
| **B. Re-view layouts** | make `registerLayout` use screen 0 for chosen bottom files (or rewrite bits 14–17 of `obj+0x44` to 0) | native-resolution panels drawn with the top HUD; per-file choice (e.g. only the map) | layout coordinates are centred, so a bottom layout lands centred on the top screen over the HUD; moving it needs per-object position edits or a projection offset; full-screen background panes must be hidden; the map draws mask passes (`ui25x_sprite_mask_write`, `ui258_sprite_mask_blend`; probably stencil) that would now share the top pass's buffers |
| **C. Retarget viewport 7** | set viewport 7 `mDisplay = 0` and a smaller `mRegion` (picture in picture) | one byte and a rect | `sLayout::beginViewport` builds the projection from the region size in pixels, so a smaller region **crops** instead of scaling unless that function is hooked; viewport 7 probably clears its region (an opaque box; not checked); nothing is drawn on the bottom display any more |

Touch mapping for option B without moving objects: a click at top-screen (X, Y) is touch (X − 40, Y), because `screenToLayout` subtracts from 160 for the bottom and the top centre is 200. With objects moved by (dx, dy) in layout units the port subtracts that offset too.

The bottom pass costs a full extra scene pass per frame (§1.5). In overlay modes the port can drop it by writing `sLtRender+0x8ab2 = 0` after `updateDisplaySkip`, except while the applet state forces it.

## 5. Plan

Stages go from cheap to best; each lists the hook points (function entries, for `config/hooks.txt`) and the data it needs. All addresses are USA v1.3.0.

**Stage 0 – discovery hooks (no behaviour change).**
- `0x002951fc PanelManager::processTouch` (after): log changes of `0x0109a350+0xce`/`+0xcc` with the touch position → panel id map by tapping each panel in the emulator.
- `0x00c0f424 sMhLayout::registerLayout` (after): record (`rLayout*` and its path, first index, count from `r0`, screen) → per-file object ranges; then dump each object's hash, view, visible bit and position from the `uLayout` table (`[0x01057534]+0x100`, array `+0x154`, count `+0x160`).
- `0x00694c98 sMhPad::update` (before/after): log non-zero `sMhPad+0x3f0`/`+0x3f4` to see which panels inject which bits.
- Format-research task: parse `rLayout` from the maintainer's RomFS and print pane names with `~crc32(name)`, which labels every hash in the code (id tables in §2.3).

**Stage 1 – no game hooks.** Use Azahar's screen layouts (large screen, side by side, separate windows, custom) with the mouse as the stylus, plus a hotkey to swap or hide the bottom screen. Works for every menu. The bottom screen already updates every frame.

**Stage 2 – key bindings.**
- `0x00694c98 sMhPad::update`: OR bound gameplay bits into `sMhPad+0x3f0` (camera pad `0x2000`/`0x1000`/`0x0800`/`0x0400`; the kick `0x0001`, done: option `kick`) and menu bits into `+0x3f4`.
- `0x00288f4c PanelManager::isTapped` (replace when a bound key is down): return 1 for that id, so every poller of the id reacts as if the panel was tapped.
- Fallback for panels that only use touch groups: synthesise a tap at the button's centre (object position + part rectangle from stage 0, screen position = (160 − lx, 120 − ly)) through Azahar's touch input for two frames (down, then up).
- Direct calls, if wanted: item scroll `0x00b31fa4` on the player from `0x00af7954`; only from inside a hooked frame, never from another thread.

**Stage 3 – overlay on the main screen.**
- Option A (frontend composite) first: no hooks; with `0x00bfa6f0 sLtRender::updateDisplaySkip` (after) the port can still throttle the bottom pass.
- Option B for native resolution: `0x00c0f424` (before: pass screen 0 for the chosen files) or a post-load rewrite of the view bits; hide background panes by hash (stage 0 data) with `LayoutObject::setVisible` semantics (bit `0x400`); place the panels by editing `obj+0x28`/`+0x2c` after the game positions them, or by hooking `0x00af3d60 sLayout::beginViewport` to offset the projection for a chosen set; map clicks back with the inverse offset; suppress the bottom pass (`+0x8ab2 = 0`).
- Option C only with a hook on `0x00af3d60` that scales the projection, plus `0x00aee5d4 sCamera::draw` or `0x00bfd9b0 sMhCamera::setupViewports` to retarget viewport 7.

**Stage 4 – single-screen HUD (Capcom's Switch approach).** Minimap re-viewed into a top-screen corner (option B with an offset and stencil checks), target camera and kick on buttons (stage 2), item and shortcut panels behind a button-held radial or list, the bottom pass disabled except in applet states and in menus that need touch, where the port falls back to option A or a second window.

**Second window.** Azahar's "separate windows" layout already shows the bottom framebuffer in its own window with mouse input; the port only has to keep that layout available.

## Runtime check (October 2026)

First look on the full game (the opening on the sand ship, recompiled code, scripted taps on each panel of the Type A layout). Touch works: the virtual camera pad turns the camera. But the stage-0 logging (`trace_panels`) saw nothing:

- `0x002951fc` ("processTouch") is entered with `r0` = `0x0109a88c` once, then with `r0` = `0x8000` on every later call, so it is not a method of the object at `0x0109a350`; `+0xcc`/`+0xce` of that object stayed `0x8000`/`0` through every tap.
- `sMhPad::update` (`0x00694c98`, on a heap object) never had `+0x3f0`/`+0x3f4` set at entry, including during the camera-pad taps.

So the touch → panel path of §2 is not confirmed at run time, and the panel ids are still unmapped. Next: watch which fields of `sMhPad` and of the panel objects change while a panel is held (a memory diff per frame), starting from the camera pad, whose effect is visible.

**The kick, confirmed.** On the sand ship's deck, OR-ing action bit `0x0001` into `sMhPad+0x3f0` for a few frames (from the `sMhPad::update` hook) makes the hunter kick; the same run without it shows him standing still. The port's `kick` option does that while a chosen button is held. It reads the button from the word `sPad::processInput` builds (`this+0x2c+0x60`, `this` being sMhPad, the sPad singleton). The encoding was checked at run time: A `0x2000`, X `0x1000`, L `0x100`, R `0x200`, ZL `0x400`, ZR `0x800`, circle pad up `0x10010000`. The injected bit is consumed by the next update, a frame later. On the deck ZL and ZR showed no effect of their own. One caveat: a hook that reads `this` must take `r0` before calling the original, which returns with `r0` changed.

**Panel types and the target camera (October 2026, from the maintainer's save on an expedition).** The quest HUD keeps one panel type per slot as u16 at `0x01082cec+0x7a` (`0x01082cec` from `0x001287a4`); the Type A layout on an expedition gave `6, 6, 13, 1, 1, 16` with slots 1 and 4 masked (`+0xb1` = `0x12`). The per-frame HUD function `0x00b92e24` switches on the type: 13 is the target camera (`0x00b93738`), 14 the kick (`sMhPad+0x3f0 |= 1`, as found before), 3–5 and 11–12 open a big panel (HUD `+0x2a9..+0x2ab`). The way in was a diagnostic code library that counts calls (`-D RC_CALL_COUNTS`, `mh4u_runner --call-counts`): in one run, four 4-second windows, a tap on the target panel in the second and fourth; five functions ran once per tap and never in the quiet windows (the "no" sound `0x00312928`, the press animation `0x00ae1b7c`/`0x00ae275c`, and two sound/effect helpers), which led to `0x00b8d024` through the panel's touch-group hash. The port's `target` option writes the request byte `+0x27e` when the chosen button is pressed.

## The quest's touch panels: slots and shapes (October 2026)

Read in the code and checked on an expedition with the maintainer's save (layout object positions from `trace_layout`, a dump of the panel block, the bottom screen's picture):

- **Six slots in two columns of three.** Slots 0–2 are the left column from the top, 3–5 the right one (`0x00ba0074` tests `slot < 3` for the left column). Each slot is 160×68 pixels under the touch screen's 36-pixel top bar (*Hunter Search* / *Chat*): slot *s* is at x = 160·(*s* / 3), y = 36 + 68·(*s* mod 3). The panel layouts' root objects sit at exactly those offsets, in layout units: (0, −36) for the map in slot 0, (−160, −36) for the camera pad in slot 3, (0, −172) and (−160, −172) for slots 2 and 5.
- **The block at `0x01082cec`** (getter `0x001287a4`): a u16 panel type per slot at `+0x7a`, and at `+0xb1` a bit per slot that the panel before it covers. The default layout (Type A) is `6, 6, 13, 1, 1, 16` with `+0xb1` = `0x12`: the map in slots 0–1, the target camera in 2, the camera pad in 3–4 and the item shortcuts in 5.
- **Panel shapes.** `0x00b9f4c8` maps a type to a size class through the table at `0x00ed2440` (18 records of type, size class, group; types of one group are the shapes of one panel): class 0 is one slot, 1 two slots one above the other, 3 two side by side. The byte table at `0x00def804` turns the class into the index of a per-slot touch record (`0x00def804 + 0x14c + 48·slot + 16·shape`): one-slot records exist for every slot, tall ones for slots 0, 1, 3 and 4 and wide ones for 0–2 (the left column), which fits. Types by class: one slot 3, 5, 7, 9–16; tall 1 (the camera pad) and **6 (the map, which has no other shape)**; wide 4, 8 and 17 (the wide forms of 3, 7 and 16); type 2 has a class with no records.
- **When the small panels are up.** `0x00b9ba38` is the panels' state machine (HUD `+0x2a9`): states 0 and 1 run the small panels through **`0x00b92e24`** every frame, state 2 runs the big panel whose type is at `+0x2ab` (the panels' tap code puts 2 there), 3 is the way back. `0x00b92e24` loops over the slots and switches on the type (see *Panel types and the target camera* above); a tap on the map (type 6) toggles HUD `+0x129` with sound `0x1a`, except in quest types 6, 0xe and 0x15.

- **When they stop.** The quest HUD's update `0x00b9dfe0` switches on HUD `+0x2a8` (13 states). In state 2, hunting, it calls `0x00b9ba38` only while the panel manager's byte `+0xac` is clear (`0x002a4da0`). Traced on an expedition with `trace_panels`: the byte is set for the 1.7 s of a change of area (both screens black, then *Now Loading*); START takes the HUD to state 3 and then 4 for as long as the quest menu is open (its touch screen has *Customize Panels*, *Panel Schemes*, *Hunter Search Position*, a camera pad and *Confirm*/*Back*), then through 0 and 1 back to 2; while the Caravaneer talks the HUD isn't updated at all (the bytes stay 2 and 1, and the touch screen keeps showing the panels). Not seen yet: states 7–8, which last while the player's `+0x54c` is neither 0 nor 7 (`0x00296a28`), and 9–10 and 11–12, which follow HUD flags `+0x1be` and `+0x1c5` and two of the player's, each with a touch-screen setup of its own.

The port's `quest_overlay` uses this: a hook on `0x00b92e24` notes each frame that the small panels ran and which slot holds the map, and the touch overlay of the custom layout then shows that 160×136 part of the touch screen alone. The quest menu (HUD state 3 or 4) and a big panel (panel state 2) bring the whole touch screen back at once, and so do 5 s without the panels on the touch screen (the quest is over); shorter breaks keep the map. While the HUD isn't updated the panels are still drawn: the hook on `0x00ae65a4` sees the map panel's layout objects at its slot's corner on the touch screen (cDraw screen 7; every object of a panel sits at its slot's corner, and nothing outside quests was drawn at the slots' corners), so the map also stays while someone talks. The same hook notes the slot of the target camera panel (type 13, one slot); with `target_overlay` the custom layout draws that 160×68 part of the touch screen a second time, as a view of its own (Azahar's layout gained a short list of such views of the bottom screen, drawn by all three renderers, and a touch that starts on one maps to its part). Checked on an expedition (October 2026, Vulkan and OpenGL): the views show the map and the "Unseen" monster icon, and a click on the target view lights the panel up as a tap on the touch screen does. The first version switched to the whole touch screen whenever the panels stopped for three frames: in a 76-minute session of the maintainer's at 180 fps that was about 25 changes of area of 1.3–1.5 s each and a few runs of the panels stopping and starting every 0.2–0.5 s, most likely the flicker he reported at changes of area and in cutscenes.

## Screens that use the touch screen (player's catalogue, October 2026)

The maintainer played the port from a new save to the first village quests and recorded every bottom screen he reached. Screenshots aren't kept in the repository (game artwork). Not covered yet: minigames and the full-size map.

| Screen | Where | What the touch screen holds | For the port |
|---|---|---|---|
| Town main screen | Val Habar, Dundorma | Town map with the facilities; buttons Guild Cards, Gestures, Friends; Hunters for Hire, World Map; Hunter Search bar; Menu | Mouse for now; a single-screen version needs the map and the three buttons |
| Panel menu | Menu on the main screen | Customize Panels, Panel Schemes, Hunter Search Position | Only arranges panels: not needed if the port replaces them |
| Guild Cards | Main screen | Check (7 pages, L/R: stats, weapon usage, journal, Palico, awards, monster log, arena log), Edit, Send, Card List | Menus, mostly readable with buttons; awards need a tap per category |
| Gestures, Friends, Hunter Search, Hunters for Hire | Main screen | Button grids and lists | Menus |
| World map | Solo/Multiplayer | Val Habar, Dundorma (Commons, Your House, Assembly), Gathering Hall (Gathering Hall, Change Online Status) | Menus |
| Shop and smithy lists | Any shop | The top screen shows the list; the bottom shows controls help, a camera pad and Confirm/Back | Buttons already work |
| Help | Help button | Topic grid | Menu |
| Quest board | Guild/village counters | Rank tabs, Guild Quests, Event Quests, Arena (Arena Quests, Challenge Quests, Leaderboards), quest lists with Confirm/Back | Menus |
| Quest panels | During a quest | Map, camera pad, target camera ("Unseen" monster icon), item shortcuts (e.g. First-aid Med, Ration) | **Target camera and kick are the only actions with no button.** MH3U on Wii U puts them on ZR when playing with a Pro Controller; the port should do the same (stage 2) |
| Gallery, save select, character creation | Title menus | Lists and grids | Menus |
| Expeditions | World map (Solo/Multiplayer) once unlocked | The map with the expedition destinations and a message box with Confirm; then the expedition card (rank, monsters seen there, environment, treasure area, online status) with Depart | Menus: Confirm and Depart take A |

So stage 2 starts with two bindings, target camera and kick. The kick is done (option `kick`, checked on the sand ship's deck); the target camera is the option `target` (see above; checked up to the "not seen yet" branch, which needs a monster in sight to go further).

## Skipping the wait after a quest (October 2026, static)

The maintainer saw it in the game: once a quest's requirements are met, a short animation plays and the touch screen shows **Skip** where the kick button is, for the countdown before the return. Read in the code and in the maintainer's RomFS (layouts and message files, parsed in place, nothing copied); not traced at run time.

- **The touch screen's kick button is a button of the panel manager's own**, not a slot panel: the HUD's hunting state polls `isTapped(7)` and injects the kick (§2.5), and an option swaps it with the menu: the English `msg\common\Menu` has *"Set whether START is used to kick or open the menu."* (0x29e) with *"Press START to open the Game Menu. Use the Touch Screen to kick."* / *"Press START to kick. Touch the Touch Screen for the Game Menu."* (0x2cd/0x2ce); `isTapped(9)` is the menu (action `0x400`). `0x00292ea4` handles the panel manager's fixed buttons by id (7, 8, 9, 0xb) and turns 7 and 9 off together in one game mode. The slot panel of type 14 is a different one: *"Special Attacks: Perform kicks, etc."* (0x313), shown as the kick or another form by `0x00b9e808` (kind table `0x00defa70`, its second form chosen by `0x00293688`). (medium)
- **"Kick" and "Skip" are messages 0x31b and 0x31c of the same file**, side by side, so most likely the two labels of that button. The code that switches them wasn't found: neither id is an immediate or a literal in the HUD or the panel manager, and no pane of the quest layouts is named for a skip (`ui610_kick00`/`kick01` are the two forms of the special-attack panel). (low)
- **The quest's status** is the byte **`[[0x00fbeffc]+0xad24]`** (the quest holder): `0x006a62c8` reads 3, 4 and 7 as complete and 5, 6 as failed and abandoned. (high)
- **The countdown.** The end set-up `0x006b2de0` writes the holder's `+0xad60` = 20.0 × the frame rate unit (`sMain+0x2c`), and a network update `0x006b3e04` does the same for the other players. The quest area (class `aQuest`: update `0x0065af10` at vtable `0x00e655e8`, its playing state `0x0065c18c`, state block at `+0x60`) sees the result in sub-state `+0x84` = 0, sets `+0x84` = 1 and loads its counter **`aQuest+0x3c`** from `+0xad60` (5 s when that is 0; quest 100 and `0x006b9160` end at once instead). Each frame it adds the frame's delta (`sMain+0x5c`) to `+0x40` and takes one off `+0x3c` per whole unit; at 0 it sets **`sGameControl+0x2f89`** (`[0x00fb6b7c]`) and the end sequence `0x0065a378` runs: the `ui207` animations `questclear`/`questfailed`/`questretire` (table `0x00dee374`), the `ui207_hanko` stamp, and the status set to 4. A failure skips the countdown. Only `aQuest`'s own code writes `+0x3c`. (high for the code, medium for the names)
- **What a tap on Skip does** wasn't found statically: nothing reads input in the countdown, nothing else writes the counter, and the only poller of panel 7 is the HUD's kick. The general way out of a quest is `sGameControl::requestExit` `0x002b16ac` (`+0xcc0 |= 1`, read by `aQuest` at `0x0065c4ec`); its callers are quest-specific (`0x006aece4`), the quest holder and player code. (open)
- **The wait, read in `aQuest::playing` `0x0065c18c` (this, state block) (October 2026, static):** state `+0x24` is the phase. In phase 0 the function asks the holder's outcome (`0x006a62c8(holder, 1)`: -1 failed or abandoned, 0 running, 1 complete). On 1 it goes to phase 1, zeroes the accumulator `aQuest+0x40` and loads `aQuest+0x3c` from `0x006a025c(holder)` (the holder's `+0xad60`; when that is 0, 5 s × `sMain+0x2c`), or, for a quest that ends at once (`0x006b3c84` or `0x006b9160` say so), sets the end stage `aQuest+0x48` = 3 with `+0x3c` = 0. In phase 1, each frame: `+0x40 += sMain+0x5c`; for each whole unit in it, `+0x3c -= 1` (not below -1); if `+0x3c <= 0`, `sGameControl+0x2f89 = 1`; then the end sequence `0x0065a378(this, state)` runs, whose stage 0 (`+0x48` = 0) does nothing while `+0x3c > 0` and otherwise moves to stage 1 (the stamp, the status set to 4). On -1 (failed) the wait is skipped: `+0x3c` = 0, `+0x48` = 0, and a message byte is set. So **the wait is `aQuest+0x3c` and nothing else**; setting it to 0 ends it the way running out of time does. (high: read in the disassembly, exercised by the port's skip fallback)
- **Skip with the weapon drawn (October 2026, seen by the maintainer):** the port's tap on panel 7 does nothing then, because the tap is the kick's action (`sMhPad+0x3f0 |= 1`; `0x00b9dfe0` does nothing else with the tap, read again in the disassembly) and the hunter can't kick with the weapon out. Whether the console's own *Skip* works with the weapon drawn isn't known yet (to try: F12 for the whole touch screen, a mouse tap on *Skip* with the weapon out). What the kick's action does during the end scene, and where, isn't found: the local player's input copy `0x00b1cda0` zeroes the whole input block while `0x0019fbf0` says a demo plays or `[player+0xa29c]+0x13c > 0`, so the skip isn't an ordinary player action either. (open)
- **Where the stylus's tap is handled (October 2026, from the maintainer's log and the disassembly).** With the port's first taps (the id and flags written after `processTouch`), the log showed the HUD asking about button 7 some 1,460 times during a 16 s wait and never consuming 0xb, and the taps skipping nothing with the weapon drawn, while the stylus's own tap on *Skip* did. The difference is where the tap is seen: `processTouch` (`0x002951fc`) clears `+0xce`/`+0xcc` at its start, runs the hit test (mode 0: `0x00d8ff5c` on the fixed buttons' group; mode 1: the slot panels, `sLayout::screenToLayout` and `LayoutHit::contains`), calls `0x0028f2f0` (touches allowed? the manager's `+0xb3` clear) with the hit in **r5** (the index into the mode's id table `0x00df5410` — mode 0 `[5, 2, 7, 7]`, mode 1 `[0, 1, 2, 7]` — plus event flags), turns the index into the id, checks the masks `+0xc2`/`+0xc4`, stores the id and flags, and **then runs its own state machine** (`switch` on `+0xb4` at `0x00295844`), which reads `+0xce`/`+0xcc` (`0x00295b88`, `0x00295db4`) and handles the fixed buttons through `0x00292ea4` (by id: 7 the kick, 8, 9 the menu, 0xb; for 7 it refuses the tap with the "no" sound when `[player+0xe30]+0x31 == 2`). A tap written after `processTouch` misses all of that and is only seen by the HUD's `isTapped(7)` a moment later, which injects the kick's action: the kick when sheathed (and, apparently, the skip through the player then), nothing with the weapon out. The port now hooks `0x0028f2f0` and, while the skip button's tap is being made, puts the Skip button's index in r5 after the original returns (processTouch reloads r5 from the context): "pressed" (`0x4000`) one frame, "held" (`0x1000`) the next, "released on it, activated" (`0x0a00`) the third, so processTouch does the rest as for the stylus. The index comes from the mode's table and the manager's slot bytes: processTouch maps the hit index to a slot (`0x00df5410`) and the slot to the panel's id (`+0xb4 + slot`, `0x00295800`); the first try took the table's value for the id and a hit on index 2 became panel 0 (the maintainer's log). (high for the flow)
- **The fixed button 0xb (October 2026, static).** Two scene routines, `0x009dc278` and `0x00a4717c` (the latter ends by setting `sCamera+0xe24 = 0x16`, a camera mode), each frame take `0x009ac36c(obj, 0x400)` (menu action `0x400` in `sMhPad+0x348`, unless `obj+0x97d6` is set) **or `isTapped(0xb)`** as "skip", and otherwise wait for their timer (`[obj+0xc]+0x34` against `obj+0x10`); `0x0029cec4` does the same with menu action `0x40` or `isTapped(0xb)` for a 20-state sequence. The HUD state machine `0x00b9dfe0` leaves its hunting state 2 for state 9 (then 0xa) while `[player+0xa29c]+0x9dc` is set, or for 0xb (then 0xc) when `questHud+0x1c5 == 1` or `+0x9de` is set: both show the fixed button through `0x2a4260(1, 0xb)` and label it with `0x298560(2, 0, 0x6ad280/0x6ae82c(holder))` every frame. **Confirmed by the maintainer's log (October 2026):** a stylus tap on *Skip* with the weapon drawn, which skipped the scene, was `touch on panel 11 flags 4001`, then `1001`, then `0A01`: the Skip is button 0xb at hit index 1, pressed, held and released over three frames, while the HUD kept asking about button 7 (1,528 polls). So the port's skip button taps 0xb through the hit test (`skip_panel`'s default), with those three frames; tried by the maintainer with the weapon drawn: the scene skips. (high)
- **Two skips (October 2026):** the port's first fix cut the wait itself 0.3 s after the press, which ended the quest and the carving time with it; the maintainer wanted the scene skipped, not the quest. Now `skip` only taps the panel, and a second option, `skip_end` (ZL+ZR held for `skip_end_hold` s), ends the wait through `aQuest::playing` (`+0x3c = 0` while the phase is 1 and the end stage 0), and online only once every port console in the session has asked (the vote travels in the net sync packets, netsync.cpp, version 2: the flags byte at offset 22; a withdrawn vote is still told for 2 s so that the last console to ask, which ends its wait at once, is heard by the others).

**What the port does** (option `skip`, default ZR): while the status is 3 or 7, for at most 25 s and until `sGameControl+0x2f89` is set, the skip is offered (`Port::Hooks::SkipOffered`, logged as `skip: quest complete ... offered` and `skip: no longer offered ...`). A press of the button then makes a tap on the panel manager's button 7 the way the game sees one: right after `processTouch` (`0x002951fc`) the last touch becomes id 7 (`+0xce`) with the flags `0x0a00` (released on the button pressed, activated), so `isTapped(7)` and any code reading the touch directly see the tap, once. While the skip is offered every tap the panel manager sees is logged (`skip: touch on panel N`), so a mouse tap on *Skip* names its id if it isn't 7; `skip_panel = N` then retargets the button. The app shows *Press C / RT to skip* at the bottom of the screen meanwhile (`skip_hint`).

## Open questions

1. Which panel ids are which (7, 8, 9, 0xb, 6 and the rest), and what action bit `0x0001` does: stage 0 logging, one tap per panel (the first attempt found the logging points don't see the taps; see the runtime check above).
2. The pane names behind the hash ids: `rLayout` parsing of the maintainer's RomFS.
3. Where the panel arrangement is saved and how it is applied at quest start (§3).
4. Whether the software keyboard, the cutscene subtitles or any other screen uses `rGUI` (`DrawLower`).
5. How the map's mask passes (`*_sprite_mask_write`) behave when drawn in the top pass, and whether viewport 7 clears its region.

## Method

- Class vtables were located without RTTI: every MT class has a two-instruction `getDTI` virtual that loads its DTI address, found by searching literal pools for the DTI addresses of `config/mt_classes.tsv` (988 of 992 classes). The data word pointing to that function is slot 5 of the class vtable; slot 4 is `createProperty`, whose property names and field offsets document the class. Unused virtuals are zero in the vtables (the linker removed them).
- Singletons were tied to classes through the constructors that store the class vtable and then store `this` into a global.
- `screenToLayout`, the hit test, the view filter and the per-display viewport loop were each read in full; the panel meanings in §2.5 come from reading their callers and are marked medium or low accordingly.
- The analysis scripts are one-off and live outside the repository.
