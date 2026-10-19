# Widescreen on the top screen (USA v1.3.0)

What has to change to render the top screen at an arbitrary aspect ratio: where the projection is built, which buffers and rectangles are sized 400x240, how culling follows, how the 2D HUD is placed, and a ranked list of hook points for a first prototype. Addresses apply to the USA v1.3.0 code only. Names are ours unless a property string or a DTI gives them.

Found by reading the code with `tools/disasm.py`, the Ghidra export and `tools/find_constants.py` (new, see [the end](#tool)), starting from `config/mt_classes.tsv`. Nothing here was run on a game image yet; the [checks](#open-questions-and-runtime-checks) list what to verify in the port.

## Results in the port (October 2026)

Run on the full game with the recompiled code (the cloud runner's software renderer, screenshots compared with the native picture):

- **Anamorphic** (`widescreen = 16:9`): the projection hook widens the view in gameplay; the top screen is meant to be stretched to 16:9 by the frontend.
- **True widescreen** (`wide_render = 1`): at 16:9 the top screen is rendered 424x240, the 3D view fills the whole width with the right proportions, and the bottom screen is unchanged. Besides the five places below, two more sizes had to change:
  1. **The display transfer's first row.** The top screen is copied from row 112 (`512 - 400`) of the 256x512 composited target: the GPU draws from the bottom up, so the picture ends at the last row. The sLtRender constructor writes 112 at `sLtRender+0x6e1c` (`0x00bfbd24`; the bottom screen's 112 is at `+0x6e24`). With W rows it must be `512 - W`, or the transfer reads past the end of the target. The port sets it every frame. The bottom screen is drawn into the same target after the top one (viewport 7, region (0, 80)-(240, 400)) and copied from row `height - 400` (`+0x6e24`): 112 for the 512-row target, 624 for a 1024-row one (left at 112, the touch screen comes out black).
  2. **A bordered full-screen pass** (`0x005dde98`) grows its rectangle by 8 pixels on each side and clamps the far edge to 400 (`cmp`/`movgt` at `0x005de49c`/`0x005de4a0`); left alone it leaves a 24-pixel strip of the previous frame in gameplay. Those two instructions, and the mode-`0xa` override below (`0x00af0c84`), are **tunables**: the recompiled code reads their immediate from a table the port fills with W ([RECOMPILER.md](../RECOMPILER.md#tunables)).
- **The HUD** (rLayout, ±200 x ±120) stays 400 pixels wide and is drawn centred in the wider picture, at its own proportions. The hunting HUD's sides are moved out to the edges (`hud_edges`) by shifting their layout objects' translations: see [the HUD section](#4-2d-hud).
- **W goes up to 1024**: the scene target's texture has a power-of-two number of rows, 512 up to W = 512 and 1024 beyond (`nDraw::Scene::Scene` rounds up; twice the VRAM, which the game had room for). 1024 rows is the most a PICA framebuffer has. Checked at 560 (21:9) and 856 (32:9) in Val Habar and on an expedition (hunting HUD at the edges): the 3D view fills the width, the HUD is centred (a dialog box sits (W - 400)/2 to the right of its native place) and, with the bottom transfer row moved, the touch screen is unchanged. Not known: what the 256x512 texture at `sLtRender+0x6bfc` is for (only the constructor refers to that offset), which stays 512 rows tall.

## Summary

- **The aspect ratio is not a constant.** Every frame, `sCamera::update` (`0x00aeee2c`) recomputes each viewport's region and writes `region height / region width` into the camera's `mAspect` (`uCamera+0x38`). The camera's virtual `getProjMatrix` then calls **`MtMatrix::perspective` (`0x00b7171c`)** with `mNearPlane`, `mFarPlane`, `mFov` (degrees, converted to radians) and `mAspect`. The default `mFov` is vertical, so a wider aspect widens the view horizontally (Hor+).
- **The MH4G 16:9 cheat** overwrites the two subtractions that produce that region height and width, so the aspect becomes 16/9. In v1.3.0 the same instruction pair is at **`0x00aeef04` / `0x00aeef0c`** (normal play) and `0x00aeef38` / `0x00aeef40` (the virtual-rect mode). The cheat changes only the projection, not the 400x240 buffers, so it gives an *anamorphic* picture: correct only when the top screen is stretched to 16:9.
- **Culling follows the projection.** Both frustums the engine uses are rebuilt every frame from the projection matrix: the viewport's 6 planes (`Viewport+0x38`, from `m00`/`m11` and the near plane) and the draw context's planes (`[cDraw+0x428]`, extracted from view x projection in `cDraw::setScene` `0x005e040c`). Widening the projection widens culling; no separate culling hook is needed.
- **400x240 is hard-coded in five places for a true wide image**, all in rotated framebuffer orientation (240 columns by 400 rows): the main scene render target (sLtRender constructor), the top display buffers (`sLtRender::initGraphics`), the viewport region (`sCamera::Viewport::updateRegion`), a mode-`0xa` override in `sCamera::Viewport::setupDraw`, and Azahar's LCD configuration plus screen layout. Everything else (half/quarter post-processing buffers, display-transfer sizes, GPU viewport/scissor, aspect, culling) is derived from those.
- **The 2D HUD**: the rLayout system (the game HUD; 708 functions use `g_sLayout`) works in centre-origin coordinates of ±200 x ±120 on the top screen (`sLayout::screenToLayout` `0x00af3cfc`), and uploads one translation per layout object (`0x00ae65a4`), which is the natural place for edge anchoring. The rGUI system (`nGUI::Draw`) maps pixels with the current viewport size (`0x005ad9e0`). Where the layout system's own projection comes from was not found statically; it needs a runtime look.
- **First prototype (anamorphic):** a post-hook on the three camera `getProjMatrix` virtuals that divides `m00` by `k = target_aspect / (5/3)`, plus a top screen stretched to the target aspect in the port's frontend. **Second step (true widescreen):** three pre/post hooks that size the scene target, the display buffers and the viewport region to W x 240, and Azahar's top framebuffer height set to W.

## How a frame reaches the screen

1. **Camera (main thread).** `sMhCamera::update` (`0x00c05ce4`, vtable `+0x1c`) runs `sCamera::update` (`0x00aeee2c`). For each of the 8 viewports that is visible and has a camera, it refreshes the region (`0x00aefd44`), sets the camera's aspect, fetches the view matrix (camera vtable `+0x48`, or the `sVibration` object at `[0x01057c34]` when it overrides that viewport, probably camera shake) into `Viewport+0x98` and the projection (vtable `+0x4c`) into `Viewport+0xd8`, and builds the frustum planes at `Viewport+0x38`.
2. **Draw setup.** `sCamera::draw` (`0x00aee5d4`, vtable `+0x28`, called per screen) runs `sCamera::Viewport::setupDraw` (`0x00af084c`) for each viewport of that screen. That function recomputes the region, applies stereo (the SDK's ULCD stereo camera, `0x0056c3c4`/`0x0056c414`/`0x0056c444`) and, when drawing into the rotated scene target, a 90° rotation built with sin/cos(π/2); it hands view, projection and region to `cDraw::setScene` (`0x005e040c`), which stores matrices, extracts the culling planes and calls `cDraw::setViewport` (`0x005e294c`). `sCamera::draw` then binds the scene colour target (`sLtRender+0x8a8c`, size read from the texture).
3. **Render thread.** The executor `0x00bfa7ec` replays the recorded packets (14 types, jump table at `0x00bfa93c`). Binding a target goes through `0x00bf83b0`, which writes the PICA registers for framebuffer flush (`0x111`), colour and depth buffers, the viewport (`0x41`-`0x44` as one sequential write, `0x68`) and the scissor (`0x66`, `0x67`). It doubles the viewport for anti-aliased targets and clamps it to the target's size (packed at `+0x2c` of the descriptor), so **a viewport can never be wider than its render target**.
4. **Copy to the display buffer.** Packet type 10 (handler at `0x00bfb0a8` inside the executor) calls `gx_AddB2LTransferCommand` (`0x00d809bc` → `0x00d89448`, GX request 3 = display transfer). Source size comes from the source texture (`+0x18`, width in the low half, height in the high half; `+0x1c` bits `0x10000`/`0x20000` select 2x1/2x2 anti-alias downsampling). Destination address and size come from the `nDraw::DisplayBuffer` (`+0x18` address, `+0x24` width, `+0x28` height). No size is a constant here.
5. **Present.** `sLtRender::present` (`0x00bf894c`) hands the display buffer to `0x00d82804` (address, width 240, format), which fills the GSP framebuffer info. Azahar's `GPU::SetBufferSwap` copies address, stride and format into `framebuffer_config[0]`; the LCD **height of 400 is Azahar's own default** (`pica_core.cpp`) and the game never sets it.

## 1. Projection and aspect ratio

### Classes and fields (confidence: high; names from the property strings)

| Class (DTI) | Field | Offset | Notes |
|---|---|---|---|
| `uCamera` (`0x010e7890`, 0xd0 bytes) | `mFarPlane` | `+0x30` | default 4,000,000.0 (constructor `0x00afa244`) |
| | `mNearPlane` | `+0x34` | default 16.0 |
| | `mAspect` | `+0x38` | default 1.0; **overwritten every frame by `sCamera::update`** |
| | `mFov` | `+0x3c` | degrees; default 40.0 |
| | `mCameraPos`, `mCameraUp`, `mTargetPos` | `+0x40`, `+0x50`, `+0x60` | defaults (0, 200, -700), (0, 1, 0), (0, 200, 0) |
| `uMhCamera` (`0x010b9430`, 0xd8) | `mRoll`, `mFovType` | `+0xd0`, `+0xd4` | properties at `0x00c463dc` |
| `uMotionCamera` (`0x010e7e10`, 0x170) | `mZoom`, `mRoll`, `mFovType` | `+0x100`, `+0x104`, `+0x114` | cutscene camera; `mZoom` is added to `mFov` |
| `sCamera` (`0x010e84d0`, 0xe10), instance `0x0105729c` | `mViewport[8]` | `+0x18`, 0x1a8 each | `sMhCamera` (0x16d0) derives from it |
| | default region | `+0xd58`..`+0xd64` | `{0, 0, sLtRender+0x6c20, sLtRender+0x6c24}` |
| | `mVirtualRect` | `+0xd68` | constructor sets `{0, 0, 400, 572}` (probably both screens as one surface: 240 + 92 + 240) |
| | `mBlankColor`, `mBGColor`, `mRelativeOffset`, `mPause` | `+0xd78`, `+0xd7c`, `+0xd80`, `+0xd91` | |
| `sCamera::Viewport` (`0x010e84f0`, 0x1a8) | `mpCamera` | `+0x04` | `uCamera*` |
| | `mpSceneTexture` | `+0x0c` | when set, the region comes from that texture's size |
| | `mVisible`, `mRealSwitch`, `mSubScene`, `mNo`, `mPriority`, `mMode`, `mDisplay` | `+0x10`..`+0x16` (bytes) | `mDisplay` 0 = top, 1 = bottom |
| | `mRegion` | `+0x24` | four ints `{x0, y0, x1, y1}` in framebuffer orientation: x across the 240 columns, y across the 400 rows |
| | frustum planes | `+0x38` | 6 x vec4, written by `sCamera::update` |
| | view, projection | `+0x98`, `+0xd8` | 4x4 each |
| | previous view, projection | `+0x118`, `+0x158` | copied each frame before the new ones |
| | ULCD stereo parameters | `+0x198`..`+0x1a4` | `mDepthRange`, `mDepthLevel`, `mDepthBias` (order not mapped) |

Property registrations: `uCamera::createProperty` `0x00af9c2c`, `sCamera::createProperty` `0x00aee170`, `sCamera::Viewport::createProperty` `0x00af0038`.

### Where the aspect comes from (confidence: high)

`sCamera::update` (`0x00aeee2c`) computes the aspect right after `updateRegion`, in one of three ways, and stores it with the instruction at `0x00aeef80` into `mpCamera->mAspect`:

| Path | Taken when | Aspect | Code |
|---|---|---|---|
| rotated scene | the main scene target exists (`sLtRender+0x6c28` non-null, i.e. always in play) and `mMode != 0xa` | `(y1 - y0) / (x1 - x0)` of `mRegion` = 400/240 on the top screen, 320/240 for a bottom-screen viewport | `0x00aeeef4`..`0x00aeef1c` |
| virtual rect | scene target exists and `mMode == 0xa` | from `sCamera.mVirtualRect` | `0x00aeef24`..`0x00aeef50` |
| no scene target | fallback | `(x1 - x0) / (y1 - y0)` | `0x00aeef58`..`0x00aeef7c` |

The region itself is set every frame by **`sCamera::Viewport::updateRegion` (`0x00aefd44`)**, also called by `setupDraw`:
- With the scene target present it uses the **hard-coded size 240 x 400** (immediates at `0x00aefdc0` and `0x00aefdb0`), with `y0 = 80` for `mDisplay != 0` (`0x00aefdbc`), so a bottom-screen viewport occupies rows 80..400 of the same target.
- With `mpSceneTexture` set, the size comes from that texture.
- `mMode` then picks the result (jump table at `0x00aefe18`): 0 uses the whole size, 2..9 select halves or quarters of it, and 1 or ≥ 10 leave `mRegion` as its owner set it (a manual region; `0xa` is the virtual-rect mode). Without the scene target every mode except 1 is treated as 0.

Without the scene target the default region is `sLtRender+0x6c20`/`+0x6c24`, which the sLtRender constructor sets to 256 x 512 (at `0x00bfbce4`); that branch is not used in normal play.

### Projection matrix (confidence: high)

- The camera's vtable slot `+0x4c` returns the projection, slot `+0x48` the view matrix:

  | Class | `+0x48` view | `+0x4c` projection |
  |---|---|---|
  | `uCamera` | abort stub (`0x00103a1e`, Thumb; pure virtual) | abort stub |
  | `uMhCamera` (vtable `0x00e6d0d4`) | `0x00b70fe0` (look-at from pos, target, up; `0x00b70ff0`) | **`0x00c46380`** |
  | `uFreeCamera` (vtable `0x00e5d4c4`) | `0x00253618` | **`0x002535f8`** (tail-calls the builder) |
  | `uMotionCamera` (vtable `0x00e600c0`) | `0x0032e3d4` | **`0x0032e338`** (adds `mZoom`, clamps to 0.01..179.99°) |

- All three call **`MtMatrix::perspective` (`0x00b7171c`)**: output pointer in `r0`, then `s0` near, `s1` far, `s2` vertical FOV in radians, `s3` aspect. It uses `tanf` (`0x00d14760`) and writes an OpenGL-style matrix (`m00 = cot(fov/2)/aspect`, `m11 = cot(fov/2)`, `-1` in the w row). Other callers (cube-map/shadow renderers such as `0x002d3efc` with fov π/2 and aspect 1, `0x00625228`, `0x00956bc4`, `0x0025fcb8`, `0x00260368`) are not the main view and must not be widened.
- **`mFovType`.** When `uMhCamera+0xd4` (or `uMotionCamera+0x114`) is 1, `getProjMatrix` multiplies `m00` and `m11` by `mAspect` after the call. `mFov` is then the *horizontal* field of view, and a wider aspect narrows the vertical view instead of widening the horizontal one. Any widescreen hook should widen `m00` directly, which works for both types (below). Which cameras set type 1 is not known yet.
- **Stereo.** `setupDraw` builds the ULCD base frustum from `m00`/`m11` of `Viewport+0xd8` (around `0x00af0e88`), so per-eye matrices inherit any widening.
- **Matrix blending.** `setupDraw` also blends the previous and current view/projection with the factor at `sCamera+0xd9c` when it is above 0 (start of `0x00af084c`). Probably a camera effect (it is 0 after construction); it may matter for frame interpolation later.

### The MH4G 16:9 cheat in v1.3.0 (confidence: high on the location, medium-high on which pair it hits)

The published MH4G JPN v1.2 code (`00AD7E88 E3A00010`, `00AD7E90 E3A01009`: `mov r0,#16` and, one instruction later, `mov r1,#9`) has exactly the shape of the aspect computation above. In both scene-target paths the region height is computed by a subtraction into `r0`, the next instruction moves it to a VFP register, and the one after that computes the region width by a subtraction into `r1`; the aspect is then `r0 / r1`. Replacing the two subtractions with the constants 16 and 9 forces the aspect to 16/9.

- v1.3.0 equivalent: **`0x00aeef04` and `0x00aeef0c`** (rotated-scene path, used in normal play). The virtual-rect path has the same pattern at `0x00aeef38` / `0x00aeef40`. The JPN addresses are 0x1707C lower; the first pair is the likelier target.
- **What it controls:** only `uCamera.mAspect` for every camera viewport, and through it the projection and both culling frustums. The scene target, display buffers, GPU viewport and HUD stay 400x240. The game thus renders a 16:9 field of view into 400x240 pixels (squeezed horizontally by 15/16), which looks right only when the emulator stretches the top screen to 16:9. That is the classic anamorphic widescreen hack, and it fits the cheat's name.
- **Effect per camera type:** with `mFovType` 0, more of the scene is visible left and right (Hor+). With `mFovType` 1 the horizontal view is unchanged and the vertical view shrinks (Vert-).
- **For the port:** code bytes can't be patched at run time in recompiled code (see [the note on literals](#how-the-recompiler-treats-constants)). The same effect, done better, is the getProjMatrix hook in [the plan](#5-plan-hook-points-for-a-first-prototype).

## 2. Render targets, viewport and display transfer

### Buffers (confidence: high)

All sizes are in framebuffer orientation (columns x rows; the top LCD is 240 columns by 400 rows).

| Buffer | Size | Created by | Where the size comes from |
|---|---|---|---|
| Main scene target (`nDraw::Scene`, 0x130 bytes) at **`sLtRender+0x6c28`** | 240 x 400 colour (`+0xec`) and depth (`+0xd4`/`+0xd8`); 120 x 200 (`+0x104`) and 60 x 100 (`+0x108`) for post-processing; optional extra view (`+0x118`, flag `0x20000`) | `nDraw::Scene::Scene` **`0x00633188`**, called from the sLtRender constructor `0x00bfbabc` with a parameter block on the stack | **immediates 240 at `0x00bfc094` and 400 at `0x00bfc0a4`**; parameter block `{width, height, flag, AA mode 0/1/2, flags, float}`. AA modes 1/2 add flag `0x100`/`0x200` to the colour buffer |
| Scene colour texture handle | | sLtRender constructor | `sLtRender+0x8a8c`, taken from the scene's colour target view (`scene+0x50`) |
| Top display buffers, left eye, 2 or 3 of them (`nDraw::DisplayBuffer`, 0x34 bytes) at `sLtRender+0x6c2c` + i*0x34 | 240 x 400 | `nDraw::DisplayBuffer::init` **`0x0062e184`** (only caller: `sLtRender::initGraphics` `0x00bfa42c`) | **immediates 400/240 at `0x00bfa5ac`/`0x00bfa5b0`** |
| Top display buffers, right eye, at `+0x6cc8` + i*0x34 | 240 x 400 | same | immediates at `0x00bfa5cc`/`0x00bfa5d0` |
| Bottom display buffers at `+0x6d64` + i*0x34 | 240 x 320 | same | `0x00bfa5f0` |
| 256 x 512 texture at `sLtRender+0x6bfc` | | sLtRender constructor | purpose unknown |

The display buffer stores width `+0x24`, height `+0x28`, format `+0x2c` (RGB8 when 0x11, else RGB565) and its address `+0x18`. The display transfer and the present path read these fields, so **the transfer follows the buffer sizes automatically**.

### Viewport and scissor (confidence: high)

- Engine side: the region becomes the GPU viewport through `setupDraw` → `cDraw::setScene` → **`cDraw::setViewport` (`0x005e294c`)**, which stores four ints at `cDraw+0x12c`..`+0x138` and sets dirty bit 3 in `+0x128`. `cDraw` is MT's draw context (DTI size 0x6af0). Other callers of `setViewport` include `sCamera::draw` for sub-scenes and the bottom-screen copy at `0x00ad3834`.
- Render thread: **`0x00bf83b0`** emits the register writes listed [above](#how-a-frame-reaches-the-screen). It is the only function in the engine (MT) code that writes the viewport registers. The SDK's GL-style layer has its own viewport writers (`0x00d6c908`, `0x00d8bc94`) and scissor writers (`0x00d15650`, `0x00d16008`, `0x00d19a44`); their use was not traced.
- `setupDraw` overrides the region with the hard-coded rectangles `{0, 80, 240, 400}` and `{0, 0, 240, 400}` (immediates at `0x00af08dc` and `0x00af0c84`) when `mMode == 0xa` and the scene target exists. The same `{0, 80, 240, 400}` appears in `0x00ad3834` (bottom-screen copy while `sApplet`'s state at `[[0x0104dea0]+0x30]` is 4, 5 or 6) and in `0x009abf2c`, which gives viewport 7 that region with `mMode` 1 (manual).
- `sCamera::draw` maps sub-scene rectangles authored in a virtual 400 x 240 space onto the bound texture with the float constants 1/400 and 1/240 (around `0x00aeeb58`).

### What has to grow for a wider image

Take W as the new top-screen width (rows of the rotated framebuffer); for 16:9 that is 427, rounded to a multiple of 8 (424 or 432).

| Item | Change | Kind |
|---|---|---|
| Scene target height (sLtRender constructor) | 400 → W | hard-coded immediate; hook `0x00633188` |
| Top display buffers (`initGraphics`) | 400 → W, both eyes | hard-coded immediates; hook `0x0062e184` |
| Viewport region (`updateRegion`) | y1 400 → W (and split modes in proportion) | hard-coded immediates; hook `0x00aefd44` |
| Mode `0xa` override in `setupDraw` | 400 → W | hard-coded; rare, can wait |
| Azahar LCD config | `framebuffer_config[0].height` 400 → W | port (Azahar) change |
| Azahar screen layout | top width 400 → W (`Core::kScreenTopWidth`, 20 uses in `framebuffer_layout.cpp`, plus `TOP_SCREEN_ASPECT_RATIO`) | frontend change |
| Half/quarter post-processing buffers | follow the scene size | automatic |
| Display transfer source/destination sizes | read from the texture and display-buffer objects | automatic |
| GPU viewport/scissor | from the region via `cDraw` | automatic |
| Aspect, projection, culling | from the region | automatic |

Constraints and risks:
- **Multiples of 8.** PICA render targets and display transfers work on 8x8 tiles. Choose W divisible by 8, better by 16 so the half-size buffer stays tiled (432 → 216).
- **Anti-aliasing.** For an AA target the viewport and buffer double in the row direction too (2x2 mode, flag `0x200`), so the buffer becomes 480 x 2W. PICA framebuffers top out at 1024 rows, so with 2x2 AA keep W ≤ 512 (about 2.13:1). Which AA mode the game uses is chosen by sLtRender constructor flags (`0x800` → 2x1, `0x1000` → 2x2); the community's "Trinity Disabler" cheat turns it off.
- **VRAM.** The scene target is the largest VRAM user (240 x 400 x 4 bytes for colour and again for depth, x2 or x4 with AA). The game's `nDraw::VramAllocator` pool size is not known; log allocation failures in the first wide build.
- **Bottom screen.** Bottom-screen 3D (`mDisplay` 1) renders into rows 80..400 of the same scene target. Keep that region 320 rows tall at the end of the wider target (`y0 = W - 320`) or leave such viewports alone; this path was not traced to its transfer.
- Azahar re-creates its screen texture when the framebuffer width/height change (`RendererOpenGL::PrepareRendertarget`), so only the default height and the layout code need changing on the emulator side.

## 3. Culling (confidence: high on the mechanism, medium on completeness)

- **Viewport frustum.** At the end of each viewport's update (`0x00aef860` onward), `sCamera::update` takes the camera position, near and far planes and the view axes, sizes the near plane as `near / m00` by `near / m11`, and builds 6 planes at `Viewport+0x38`. The width is also multiplied by a stereo factor stored at `Viewport+0x1a4` (1 + depth parameters / 76.8 when 3D is on).
- **Draw-context frustum.** `cDraw::setScene` (`0x005e040c`) extracts 6 planes from view x projection into per-frame storage at `[cDraw+0x428]` (`0x005e0fac`). The engine's culling tests read those planes: `cDraw::cullAABB` `0x005e3708` and `cDraw::cullSphere` `0x005e4558` (−1 = outside, otherwise a mask of intersected planes), and the masked variants `0x005e380c` / `0x005e45ec`. Their callers (`0x00b0173c`, `0x001a5500`, `0x001a6820`, `0x00cd8404`, `0x00ce36a4`) were not identified further.
- Both sets come from the projection, so **widening `m00` (or the region) widens culling with it**; objects at the new edges will not pop. Not checked: game code that culls with its own screen-space or angle tests (HUD markers, effect LOD by distance `mHMLDist`… in sEffect, grass). Effects have a screen-size setting, `sEffect.mDefaultScreenSize`: `sEffect::sEffect` (`0x00af38e4`) sets it to 400 x 240 at `g_sEffect+0x30`/`+0x34` (`g_sEffect` = `0x010572a4`), for effects whose size adjusts to the screen.

## 4. 2D HUD

There are two 2D systems.

**rLayout (the game HUD).** `sLayout`/`sMhLayout` (instance **`0x01057534`**, referenced by 708 functions), `uLayout` and its schedulers. Resources are `lyt\...` files.
- **Coordinates are centre-origin.** `sLayout::screenToLayout` (`0x00af3cfc`, 65 callers; the ones read convert touch positions) converts a pixel position to layout coordinates as (200 − x, 120 − y) on the top screen and (160 − x, 120 − y) on the bottom screen; the 200/160/120 are float literals at `0x00af3d24`/`0x00af3d20`/`0x00af3d1c`. Layout space is therefore ±200 x ±120 on the top screen.
- **Draw path:** `uLayout::draw` (`0x00b04774`, vtable `+0x30`) → `0x00b048b8` (updates the instance, and copies the current `cDraw` viewport into a second rectangle at `cDraw+0x13c`, probably the scissor) → per object `0x00ae6cd0`, which records or replays a command cache.
- **Per-object translation.** For each layout object `0x00ae65a4` uploads a shader constant `(x + dx, y + dy, z, 1)` from the object's `+0x28`/`+0x2c`/`+0x30` plus `+0x34`/`+0x38`, followed by its colour (RGBA floats at +0x10 of the constant). It writes them where `cDraw+0x83c` then points. One function, one object at a time: **this is the hook point for anchoring**.
- **What a layout object is (runtime, an expedition in USA v1.3.0).** `uLayout::draw` (`0x00b04774`) walks the unit's object list (`+0x120`, next at `+0x48`) and draws the objects whose screen (bits 14-17 of `+0x44`) is the one being drawn (bits 1-5 of `cDraw+0x1bc`: 0 top, 7 bottom). Each object is one HUD part: the clock, the health bar, the stamina bar, the sharpness, each party row, the item selection and the item name are separate objects. Their translations are all 0 on the top screen; the parts are placed by their panes (group nodes, whose 2D transforms `0x00ae8db0` uploads, x at +0xc of each 64-byte slot) or by their vertices. **Layout x grows to the left**: pixel = 200 − x, as in `screenToLayout`.
- **Which object is which** was found by tinting: the hook overwrote the colour of the *i*th top-screen object in red when bit *k* of *i* was set, and four screenshots gave each part's index. The word at `+0x0` of an object is a hash, the same in every run (probably of the object's name), so the port keeps two lists of hashes: left (clock `2E7FA61D`, health `8293D551`, stamina `D07F4AA2`, sharpness `BD56507D`, next to it `B2DA1296`, party rows `8AC1CCC4` and `FDC6FC52`) and right (item selection `1833E2C8`, item name `3C598C15`). It shifts those objects' x by +(W − 400)/2 and −(W − 400)/2. In a two-player quest the party list has two more rows, the other hunter (`64CFADE8`) and their Palico (`13C89D7E`); moving test objects from the hook by a large offset told them apart. Four more objects come right after them in the list and draw nothing with two players (`4639C2C3`, `60497133`, `174E41A5`, `892AD406`): the port treats them as the rows of a larger party. Unknown: `8D5C9685` (draws nothing in these scenes), `4273292C` (expedition only, panes all over the screen), and the 3D name tags (`F24BC1DA`, which follow the wider projection by themselves). `trace_layout` logs every object's hash for more.
- **Open:** the matrix that maps layout space to the screen was not found in code. It may be a constant of the layout vertex shader or part of the cached command lists. If it maps ±200 to the viewport edges, a wider viewport stretches the HUD; if it uses pixel units, the HUD stays centred at its original size. See the checks.

**rGUI (`uGUI`/`sGUI`/`nGUI::Draw`)**: its unit classes are `uGUI`, `uGUIScheduler` and `uGUISubtitles`; where else the game uses it was not mapped (the software keyboard's `uSwkbdLayout` is an rLayout class).
- `0x005ad9e0` builds the 2D pixel-to-clip transform from the current `cDraw` viewport: scale (2/width, −2/height) with a top-left origin and a half-pixel offset, and a different offset when the scene is rotated (`sLtRender+0x6ba4` bit 3). GUI content therefore keeps its pixel size and stays where it is relative to one corner of the screen when the viewport grows. Hooking this function to add (W − 400)/2 keeps it centred.
- `uGUI::draw` (`0x005bc350`) re-lays out its object tree when its "screen rect" changes. It takes that rect from `sLtRender+0x6c20`/`+0x6c24`, which hold 256 x 512, not the screen size; what the rect means for rGUI objects is unclear.
- When rendering into the rotated scene target, `uGUI::draw` passes `sGUI`'s 90° rotation matrix as the root transform. `sGUI` is at `0x010572ac`; its constructor `0x005b6454` builds that matrix at `+0x7c` with sin/cos(π/2), and also sets `-40`/`-360` at `+0x80`/`+0x84`, probably the bottom screen's offset in the dual-screen layout (−40 = −(400 − 320)/2 centres it under the top screen; a wider top screen changes that).

What a global HUD fix needs:
1. **Keep the HUD's shape.** If the layout projection turns out to be relative to the viewport, it must keep a fixed scale of 1 pixel per layout unit (hook where it is set, once found).
2. **Re-anchor.** Shift layout objects near the left and right edges outward in `0x00ae65a4`. A threshold on |x| doesn't work (the translations are 0; see above), so the port uses a table of object hashes.
3. **Centre rGUI** content with an x offset of (W − 400)/2 in `0x005ad9e0`.

Touch input is on the bottom screen and is not affected.

## 5. Plan: hook points for a first prototype

Hook points are function entries; adding them to `config/hooks.txt` is the orchestrator's job. Ranked by value for effort:

| # | Address | Function | Hook | What it does |
|---|---|---|---|---|
| 1 | `0x00c46380` | `uMhCamera::getProjMatrix` | post | Save `r0` (output matrix) before running the original; afterwards divide `m00` (offset 0 of the matrix `MtMatrix::perspective` wrote) by `k = target_aspect / (5/3)` (16/15 for 16:9). Widens the horizontal FOV by `k` for both `mFovType` values (Hor+); culling and stereo follow. Not needed once hook 6 widens the region |
| 2 | `0x002535f8` | `uFreeCamera::getProjMatrix` | post | Same |
| 3 | `0x0032e338` | `uMotionCamera::getProjMatrix` | post | Same (cutscenes) |
| — | frontend | top screen layout | — | Anamorphic mode: draw the 400x240 top screen stretched to the target aspect. Together with 1-3 this is the MH4G cheat done properly, and is the whole first prototype |
| 4 | `0x00633188` | `nDraw::Scene::Scene` | pre | If the parameter block in `r1` holds width 240 and height 400, set the height to W. Sizes colour, depth and post-processing buffers. The other caller (`0x00cfb0ac`, the DTI create path) can make scenes too, so also check that `lr` is inside the sLtRender constructor |
| 5 | `0x0062e184` | `nDraw::DisplayBuffer::init` | pre | If `r1` == 240 and `r2` == 400, set `r2` = W (top-left and top-right eyes; bottom is 320) |
| 6 | `0x00aefd44` | `sCamera::Viewport::updateRegion` | post | Viewport in `r0`, saved before the call. When the scene target exists, `mDisplay` is 0, `mpSceneTexture` is null and `mMode` is 0 or 2..9 (regions computed from the hard-coded 400), scale the y coordinates of `mRegion` (`+0x28`, `+0x30`) by W/400. The aspect then becomes W/240 by itself (drop hooks 1-3) |
| — | port/Azahar | GPU defaults + layout | — | `framebuffer_config[0].height = W`; top-screen width W in the frontend layout. With 4-6 this is true widescreen |
| 7 | `0x00ae65a4` | layout object constants | pre or replace | Anchor HUD objects: shift x by ±(W − 400)/2 for objects beyond a threshold. Needs the runtime check of the layout projection first |
| 8 | `0x005ad9e0` | `nGUI::Draw` screen transform | post | Add (W − 400)/2 pixels to the GUI offset to centre rGUI content |
| 9 | data | `g_sEffect` (`[0x010572a4]`) `+0x30` | data write | Set `mDefaultScreenSize` width to W if screen-sized effects look off |

Sketch of hook 1 in `port/src/hooks.cpp` style (port code, not game code):

```cpp
void WidenProjection(RCtx* ctx, RFunc original) {
    const u32 out = ctx->r[0];          // MtMatrix* result
    original(ctx);
    WriteF32(ctx, out, ReadF32(ctx, out) / options.widescreen_k);   // m00
}
```

Proposed `config/hooks.txt` lines:

```
0x00c46380 uMhCamera::getProjMatrix        # widescreen: widen m00 (anamorphic / Hor+)
0x002535f8 uFreeCamera::getProjMatrix      # widescreen
0x0032e338 uMotionCamera::getProjMatrix    # widescreen (cutscene cameras)
0x00633188 nDraw::Scene::Scene             # widescreen: scene target 240x400 -> 240xW
0x0062e184 nDraw::DisplayBuffer::init      # widescreen: top display buffers 240x400 -> 240xW
0x00aefd44 sCamera::Viewport::updateRegion # widescreen: viewport region 400 -> W rows
```

Hooks 7 and 8 can wait for the HUD check.

### How the recompiler treats constants

From `tools/recomp/arm.py` and `thumb.py`:
- **Immediates** (`mov rX,#400`) are compiled into the generated C.
- **`LDR` literal-pool loads** are folded into constants at recompile time, so writing a new value to the pool word in guest memory changes nothing.
- **`VLDR` literal loads** are not folded: they read guest memory at run time, so a data write to a float pool word does take effect.

Therefore every hard-coded 400/240 above (all immediates) needs a hook or a patch of `code.bin` before recompiling. Float constants such as the 200/160/120 in `sLayout::screenToLayout` or 1/400 and 1/240 in `sCamera::draw` can be changed with data writes. `tools/find_constants.py` prints which case applies for each hit.

## Open questions and runtime checks

1. **Layout projection.** Run hooks 4-6 with W = 432 and look at the HUD: stretched (the projection follows the viewport) or centred with gaps (fixed pixel scale). Azahar's GPU debugger can also show the vertex-shader float uniforms of a layout draw. Then find where that uniform is written.
2. **VRAM and AA at run time.** Log the sLtRender constructor's flag words (AA mode), the sizes passed to `0x00633188`/`0x0062e184`, and any allocation failure.
3. **Which cameras use `mFovType` 1.** Log `uMhCamera+0xd4`/`uMotionCamera+0x114` in hook 1/3.
4. **Screen-space HUD markers** (target arrows, damage numbers, item pop-ups) that project 3D positions with 400/240 or 200/120 constants: compare positions at W = 400 and W = 432.
5. **Bottom-screen 3D via the scene target** (`mDisplay` 1, mode `0xa`, `0x00ad3834`, `0x009abf2c`): when the game uses it, and how its transfer finds rows 80..400.
6. **Matrix blending** at `sCamera+0xd9c`: who sets it (camera effect, or something the 60 fps work should know about).

## Tool

`tools/find_constants.py` (tests: `tests/test_find_constants.py`, synthetic code only) finds where a constant lives:
- `--value`/`--float`: LDR/VLDR literal loads (ARM) and Thumb LDR literals of a 32-bit word, with whether the recompiler folds the load;
- `--mov`, `--mov-pair a,b --gap n`: MOV/MVN immediates and pairs of them;
- `--pica-reg`: literal loads of PICA200 register-write headers for given register ids.

It reads the dump through `--exheader/--code` (as `tools/disasm.py` does) or a decompressed image (`--image build/code_v130_dec.bin --text-end 0xDEC734`), and names hits with `config/functions.tsv` and `config/symbols.txt`. A whole-image query takes about 4 s. Examples that reproduce this page:

```
python3 tools/find_constants.py --image build/code_v130_dec.bin --text-end 0xDEC734 --mov-pair 400,240 --gap 4
python3 tools/find_constants.py --image build/code_v130_dec.bin --text-end 0xDEC734 --pica-reg 0x41 --pica-reg 0x68
python3 tools/find_constants.py --image build/code_v130_dec.bin --text-end 0xDEC734 --float 200.0
```
