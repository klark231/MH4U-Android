// The app's in-game menu, drawn with Dear ImGui over the game: Esc, a controller's Guide button
// or Back+Start opens and closes it. Its tabs change the game's options (mh4u.ini) and the app's
// (mh4u-app.ini) while the game runs, rebind the keys, show the network room and quit; the 3DS
// software keyboard the game asks for (a hunter's name, chat) is drawn in it too, and while it is
// closed a hint over the game when there is one (ui/hint.h: the skip after a quest).
//
// Threads: the menu is built on the emulation thread, in the main window's PollEvents (where SDL's
// events are pumped), and a copy of each frame's draw data goes to the thread that draws: the
// OpenGL window's present thread, Azahar's Vulkan renderer (through its present overlay,
// video_core/renderer_vulkan/vk_overlay.h in azahar.patch) or the software window's thread.
#pragma once

#include "common/common_types.h"

union SDL_Event;
struct SDL_Surface;

namespace Core {
class System;
}

namespace App {

struct Config;
class Window;

namespace Ui {

/// Starts the menu for the main `window` (after it is made, before the game loads) and puts the
/// menu's software keyboard in place of Azahar's default one. `config` is the app's settings as
/// the app runs with them; the menu changes them in place (input profile and display settings
/// follow) and saves its changes to mh4u-app.ini without the command line's.
void Start(Core::System& system, Window& window, Config& config);

/// After the renderer has gone (System::Shutdown) and before SDL quits.
void Stop();

/// The windows' events, in PollEvents: true when the event is the menu's (its toggle, or input
/// while it is up) and the game mustn't have it.
bool OnEvent(const SDL_Event& event);

/// The main window's PollEvents, after the events: builds the menu's frame while it is up.
void Frame();

/// Shows or hides the frame-rate counter (mh4u-app.ini's fps_counter: back to its last corner,
/// top left at first) and saves the choice. The main window's fps_counter_key.
void ToggleFpsCounter();

/// Whether the menu (or the software keyboard) is on the screen and has the input.
bool Visible();

/// OpenGL, on the window's present thread with its context current: the menu's renderer before
/// the first frame, the menu over each presented frame (`width` x `height` pixels, before the
/// swap) and the renderer again before the thread ends.
void InitOpenGL();
void DrawOpenGL(u32 width, u32 height);
void ShutdownOpenGL();

/// The software window, on its present thread: the menu drawn into the window's surface, and
/// the renderer it used released before the thread ends.
void DrawSoftware(SDL_Surface* surface);
void ShutdownSoftware();

} // namespace Ui
} // namespace App
