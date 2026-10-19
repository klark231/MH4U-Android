// The launcher: the app's home screen before the game, in a window of its own (an SDL_Renderer
// with Dear ImGui, so it needs none of Azahar's renderers and looks the same everywhere). Play,
// the setup (the game file, the v1.3.0 update, the code library), the mod library, the room to
// play in and the app's settings, with the mouse, the keyboard or a controller. It hands a
// ready App::Config back to main.cpp and closes before the game's window opens.
#pragma once

#include <string>

namespace App {
struct Config;
}

namespace Launcher {

enum class Result {
    Play,         ///< start the game with the config as the launcher left it
    Quit,         ///< the player closed the launcher
    Unavailable,  ///< no window could be opened (the log says why): carry on without it
};

/// Shows the launcher; with `show` false only when the setup is incomplete (no usable game
/// file, the update or the code library missing). `config` is what main.cpp starts the game
/// with (command-line choices included); what the player changes is saved in mh4u-app.ini
/// too. SDL must be initialised (video, controllers, events).
Result Run(App::Config& config, bool show);

/// After Run returned Play: the player was told in the launcher what the code library's state
/// means for the game and chose to play anyway, so the start needn't say it again.
bool CodeNoticeSeen();

/// mh4u --check-code GAME, in a process of its own: what the port's start-up would say about
/// the code library for GAME (Port::PrepareBackendFor, Port::BackendNotice), printed for the
/// launcher, which doesn't load the library itself (it couldn't be built again while loaded).
/// Exit status: 0 the game runs on it, 2 it doesn't, 3 it does with some of the port's
/// options missing.
int CheckCode(const std::string& game);

} // namespace Launcher
