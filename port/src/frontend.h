// Hook for frontends (Azahar's Qt UI, mh4u_runner): pick the CPU backend for a game.
#pragma once

#include <string>

namespace Core {
class System;
}

namespace Port {

/// Call before Core::System::Load(). If a recompiled code library matches the program in
/// `rom_path`, installs the recompiled CPU backend; otherwise leaves the emulator's own
/// backend in place. Code libraries are looked up in this order:
///   1. the MH4U_CODE environment variable (path to the library),
///   2. "<program id>.<so|dll|dylib>" in Azahar's user directory, under "recomp/".
/// Returns a line for the log describing what was chosen.
std::string PrepareBackendFor(Core::System& system, const std::string& rom_path);

/// After PrepareBackendFor: a note for the player when the game the port is made for (MH4U,
/// USA) runs without the port's code library, or with one that is missing some of the port's
/// hook points: what happened and how to build the library. Empty when all is well, and for
/// every other game.
std::string BackendNotice();

/// In quests, switches the touch overlay between the map alone and the whole touch screen
/// (a hotkey in Azahar's Qt frontend).
void ToggleQuestMap();

/// The port's options file: "mh4u.ini" in Azahar's user directory.
std::string OptionsPath();

/// What a new options file says: the recommended settings, each with what it does.
std::string OptionsTemplate();

} // namespace Port
