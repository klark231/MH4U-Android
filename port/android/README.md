# MH4U for Android

The PC port's app, on a phone: the same recompiled-code backend and hooks on Azahar's core, with
its own launcher, in-game menu and controls in the PC app's amber theme. **Azahar's Android app
is not used**: only its core libraries are built (the app here is its own Kotlin/Compose
project). Azahar's Android sources were the reference for the surface, storage and input glue.

## What is here

| | |
|---|---|
| `app/` | the Android app (Kotlin, Jetpack Compose) |
| `jni/` | `libmh4u-android.so`: window, input, storage, keyboard, JNI; shares the PC app's `config.cpp`, `multiplayer.cpp`, `invite.cpp`, `lobby.cpp` |
| `build-code-library.sh` | builds the game's code library (arm64) on a PC from your own dump |
| `../azahar/azahar-android.patch` | the port's changes to Azahar's core, rebased onto the AzaharPlus tree (Android-relevant files only: no Qt, no ImGui overlay) |

## Feature map (PC app → Android)

| PC | Android |
|---|---|
| Launcher: Home, Setup, Online, Settings | `MainActivity`: same four screens. Setup imports the game, the code library and the update `.cia` |
| In-game menu (Esc): Game, Display, Audio, Controls, Multiplayer | `EmulationActivity`'s menu (Back or a pad's Guide button): same tabs, written to the same `mh4u-app.ini` / `mh4u.ini` |
| Every `mh4u.ini` option (fps, widescreen, HUD, touch overlay, quest map/target panels, kick/target/skip buttons, filters, net sync) | Game tab; changes reach the running game through `Hooks::SetOption` |
| Screen layouts (overlay, stacked, single, side) | Display tab: overlay, stacked, single, side, large+small, hybrid; portrait layouts; swap, upright, gap |
| Keyboard + controller bindings | Controls tab: any gamepad/keyboard/mouse Android lists, per-device bindings, learn-by-pressing, chords and combos (`port/src/input_map.*`) |
| (touch screen) | On-screen controls: buttons, d-pad, circle pad, C-stick, **combo buttons** (several 3DS buttons per touch, optional latch), drag/pinch editor, opacity, vibration; the 3DS touch screen is the stylus |
| Multiplayer tab, invite codes, lobby | Multiplayer tab (in game) and Online screen (room the game starts with): join by `mh4u://` code or address, host, member list, share invite, lobby list |
| 3DS software keyboard | Android dialog (`Keyboard.kt`) |
| Colours | `ui/Theme.kt`: the PC theme's values converted from `ui/ui.cpp` |

Not ported: the **Mods** screen (it runs the port's Python tools, which need a PC: build code mods
into the library there with `build-code-library.sh --mods`), OpenGL and software renderers
(Vulkan only), the second window, mouse-drawn HUD editor (sliders instead).

## Build

1. Azahar tree (your AzaharPlus checkout) with the core patch:
   `cd <azahar> && git apply <repo>/port/azahar/azahar-android.patch`
2. Android Studio (or Gradle 8.9+), NDK 27, CMake 3.25+. Open `port/android/`.
   Set `azahar.dir` in `gradle.properties` (or `-Pazahar.dir=...`) to the tree from step 1.
3. Build `assembleDebug`. The native build compiles Azahar's core: expect a long first build.
4. On a PC, build the code library from your own dump: `ANDROID_NDK_HOME=... port/android/build-code-library.sh "update.cia"`.
5. On the phone: Setup > choose your decrypted game, the `.so`, the update.

## Status

Written against the AzaharPlus tree and the PC port's sources, **not yet compiled or run**: no
Android SDK/NDK was available where this was written. The combo/binding engine
(`port/src/input_map.*`) passes its existing host test; everything else needs its first build,
and the first fixes will be compile errors and API drift. Things to look at first are listed in
`NOTES.md`.
