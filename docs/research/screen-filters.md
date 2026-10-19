# Screen filters: bloom, depth of field, blur

USA v1.3.0. How the engine's post-processing passes work, which ones MH4U uses where, and how the port turns them off (`mh4u.ini`: `bloom`, `motion_blur`, `depth_of_field`, `radial_blur`, `haze`; `trace_filters` logs them). Found by reading the code and the game's data, then checked in the cloud with Luis's save (title, village, cutscene, expedition).

## Summary

- Every post-processing pass is a unit of a `uFilter` subclass. Its constructor registers it with `sScene` (`0x006c0d08`, an array at `sScene+0x20`), and each frame the scene calls the filter's `render(this, cDraw*)`, virtual slot 12 (`+0x30`), which records the pass's draws into the scene's target.
- A pass whose `render` returns at once leaves the picture as the passes before it left it: no glow, no blur. The community's "Turn off bloom" cheat for MH4G JPN v1.2 does exactly this to bloom (`002B7498 E12FFF1E`, a `bx lr` over the first instruction; see [community-enhancements.md](community-enhancements.md)); bloom's `render` is at `0x002bc238` in USA v1.3.0. The port does the same per class, through hook points on the `render` functions.
- What MH4U uses (from the game's archives, and traced in play):

| Filter | Where | What it looks like |
|---|---|---|
| `uBloomFilter` | the scheduler of nearly every stage (192 stage archives) and 121 cutscenes; drawn in the title menus, the village, the field | bright parts glow: they are extracted, blurred in quarter, eighth and sixteenth resolution targets (`BloomTemp0`-`5`) and added back, which lays a soft haze over everything bright |
| `uDOFFilter` | 193 cutscenes, nowhere else | depth of field: a blurred quarter-resolution copy of the scene (`mpQuarterScene`) mixed in by distance; with Azahar's software renderer the mix covers the whole frame, so cutscenes come out blocky and blurred from front to back (below) |
| `uRadialBlurFilter`, `uMhRadialBlurFilter` | 37 cutscenes; in hunts, `0x00c0814c` makes a `uMhRadialBlurFilter` around a point when the player it is given is the local one, called from about 40 functions in the monsters' code (`0x006ddaa8`-`0x00914190`; roars, it seems: not checked) | zoom blur from a centre |
| `uMultiBlurFilter` | made by code only: `0x00af3400` keeps up to eight, one per bit of a mask it is given, and puts them in a unit line it gets from `sEffect` | blends several frames (`mDrawNum`, `mDrawMax`, `mEntry`): the game's motion blur |
| `uMotionBlurFilter` | not in any archive and not made by code | the engine's velocity-based blur (`mVelocityScale`, `mSampleCount`), unused by MH4U |
| `uHazeFilter`, `uMhHazeFilter` | 4 cutscenes (`uMhHazeFilter` in no archive) | heat haze (distortion) |
| `uBokehFilter` | 1 cutscene | bokeh |
| `uColorCorrectFilter` | stages and cutscenes | gamma and tone curves: part of the intended colours, never turned off |
| `uMhFilter` (a `cUnit`, not a `uFilter`) | stages and cutscenes | sun position, lens flare, sky-to-ground colour gradient |

## Classes (USA v1.3.0)

| Class | Size | vtable | `render` (slot 12) | Properties (from slot 4) |
|---|---|---|---|---|
| `uFilter` | 0x38 | `0x00e6baa0` | `0x00627bd8` (`bx lr`) | |
| `uBloomFilter` | 0x288 | `0x00e5e7f4` | `0x002bc238` | `mThrethold`, `mBlurCount`, `mBloomColor`, `mpQScene[0..2][0]` |
| `uDOFFilter` | 0x580 | `0x00e5bafc` | `0x001b0890` | `mDOFColor`, `mNear`, `mNearLimite`, `mFar`, `mFarLimite`, `mFocal`, `mpDensity`, `mpQuarterScene` |
| `uBokehFilter` | 0x78 | `0x00e5e844` | `0x002bd53c` | `mBokehMode`, `mBlendMode`, `mColorBias`, `mColor`, `mpAlphaMap` |
| `uMotionBlurFilter` | 0x58 | `0x00e624d0` | `0x003b0804` | `mVelocityLimite`, `mVelocityScale`, `mSampleCount` |
| `uMultiBlurFilter` | 0x60 | `0x00e61e08` | `0x002d3168` | `mDrawNum`, `mDrawMax`, `mEntry` |
| `uRadialBlurFilter` | 0xc0 | `0x00e62520` | `0x003b2938` | `mBlendColor`, `mBlendRate`, `mSampleCount`, `mBias`, `mCenterPos`, `mMode3D`, `mpAlphaMap` |
| `uMhRadialBlurFilter` | 0xc8 | `0x00e6313c` | `0x003b2938` (inherited) | as above |
| `uHazeFilter`, `uMhHazeFilter` | 0x70 | `0x00e5d56c`, `0x00e5ff74` | `0x0025655c` | `mHazeMapScale`, `mHazeMapOffset`, `mHazeSpeed`, `mHazeScale`, `mHazeBias`, `mHazeColor` |
| `uColorCorrectFilter` | 0x690 | `0x00e62f28` | `0x003dd740` | `mGamma`, `mToneCurveR/G/B`, `mCorrector` |
| `uColorFilter`, `uFadeFilter` | 0x50, 0x60 | `0x00e5e970`, `0x00e5d474` | `0x002bffc0` | (fades to and from black use these) |
| `uCrossFadeFilter` | 0x68 | `0x00e61cbc` | `0x003a1a6c` | |
| `uImagePlaneFilter`, `uImagePlaneFilterRange` | 0x1a0, 0x1d0 | `0x00e62480`, `0x00e63ac4` | `0x003aeccc`, `0x0040a218` | `mpTexture`, `mBlendType`, `mPos`, `mScale`, `mAngle` |
| `uOutlineFilter` | 0x48 | `0x00e60ff8` | `0x003808ac` | |
| `uTVNoiseFilter` | 0x80 | `0x00e611a4` | `0x00385c34` | (`system\filter\TVNoise_LIN`) |

How they were found: each class's DTI initializer gives the DTI vtable, whose third entry is `newInstance` (it allocates the instance size and calls the constructor, which writes the class vtable); slot 5 of a class vtable is its `getDTI` (`ldr r0, =DTI; bx lr`), so a vtable can also be found by scanning `.rodata` for that function. `uFilter`'s vtable has 20 slots (`uMultiFilter` adds more).

The `render` functions start the same way: they return when the `cDraw` has no scene target (`cDraw+0x1e4`, then `+0x108`), then call `0x00b00730` (the filter's common set-up) and record their passes (`cDraw::setViewport` `0x005e294c` and the like). `sScene` itself is drawn from `0x00bf5a4c` through its own slot 12.

## Where the filters come from

Filters are made from data: the archives' `rScheduler` resources (type `0x4c0db839`), the time-lines that stages and cutscenes run, create units by class and set their properties. A scan of every `.arc` in the RomFS for the classes' ids (`~crc32(name) & 0x7FFFFFFF`, see [mt-classes.md](mt-classes.md)) finds them only in schedulers, as in the table above. `uMhRadialBlurFilter` and `uMultiBlurFilter` are made by code instead.

## Seen in play (cloud runs with Luis's save, software renderer at 1x)

- **Title and menus:** bloom and colour correction.
- **Village (Val Habar):** bloom and colour correction. Without bloom the bright cloth and the tents keep their edges and contrast; with it they have a soft glow, at 1x already. At a higher internal resolution the rest of the picture gets sharper while the glow, a blur by design, stays as soft (my reading; not compared at higher resolutions, which the software renderer doesn't do).
- **A cutscene in the village:** bloom, depth of field, colour correction, an image plane. With depth of field the whole frame is blurred and blocky, the characters in front as much as the background; without it the same frame is sharp. On the console the blurred copy is presumably mixed in by distance (the filter has an `mpDensity` texture besides the quarter-resolution scene); in emulation it covers everything. This is most likely Azahar's issue #765 (cutscenes blurred, related to the PICA "gas" rendering the game uses), whose suggested workarounds were 1% stereoscopy depth or the community's render cheats. Azahar emulates no gas mode: its software renderer draws such fragments as ordinary ones (`sw_rasterizer.cpp` only handles the fog mode), and since #1525 (OpenGL) and #2165 (Vulkan), both in the port's Azahar, the hardware renderers write a transparent black fragment instead, "(just discarding the pixel causes graphical issues in some MH games)". The runs here use the software renderer; how depth of field looks with OpenGL or Vulkan wasn't tried.
- **Val Habar outdoors, before an expedition:** bloom and colour correction, then a fade. Here bloom matters most: the sun and the bright stone and cloth spread a glow over a third of the picture, and without it the same view has clear edges and sky (mean brightness 134 with bloom, 120 without).
- **Expedition (a forest with ruins, in the field with the HUD up):** bloom and colour correction only, no depth of field. Bloom is subtle in the shade: a light haze on the sky and a little less contrast (mean brightness 69.7 with, 68.6 without).
- Not seen in these runs: `uMultiBlurFilter` and the radial blurs (they come with effects and roars, which the runs didn't reach), haze, bokeh. Skipping them goes through the same hook as bloom and depth of field.

## The port's options

`mh4u.ini` (or `MH4U_BLOOM`, `MH4U_MOTION_BLUR`, `MH4U_DEPTH_OF_FIELD`, `MH4U_RADIAL_BLUR`, `MH4U_HAZE`): each is on by default (the game's own look) and `off` skips:

| Option | Skips |
|---|---|
| `bloom` | `uBloomFilter` |
| `motion_blur` | `uMultiBlurFilter`, `uMotionBlurFilter` |
| `depth_of_field` | `uDOFFilter`, `uBokehFilter` |
| `radial_blur` | `uRadialBlurFilter`, `uMhRadialBlurFilter` |
| `haze` | `uHazeFilter`, `uMhHazeFilter` |

They change only what is drawn, never the game's state, so players in a multiplayer session can use different values. `trace_filters = 1` logs the filters drawn over each second when they change, e.g. `screen filters at 32.7 s: colour correction, image plane (skipped: bloom, depth of field)`. All of them can change while the game runs (`mh4u_runner --set MS:bloom=off`).

The hook (`FilterRender` in `port/src/hooks.cpp`) sits on the 14 distinct `render` functions above and tells the classes apart by vtable, since some share one.
