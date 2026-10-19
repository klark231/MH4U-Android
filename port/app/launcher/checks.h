// What the launcher's setup checks: the game file (Monster Hunter 4 Ultimate USA, decrypted),
// the v1.3.0 update in Azahar's virtual SD card (and installing it from the player's .cia),
// and the code library (installed, and built from the code the game will run).
#pragma once

#include <atomic>
#include <string>
#include <thread>

#include "common/common_types.h"

namespace Launcher::Checks {

constexpr u64 MH4U_USA = 0x0004000000126300;
constexpr u64 MH4U_UPDATE = 0x0004000E00126300;
constexpr u16 UPDATE_VERSION = 1072;  ///< v1.3.0 (config/versions.json)

struct Game {
    enum class State { Unset, Missing, NotA3ds, Encrypted, OtherGame, Ok };
    State state = State::Unset;
    u64 program_id = 0;
};

/// Reads the game file's NCCH header (Azahar's NCCHContainer): which program, decrypted or not.
Game CheckGame(const std::string& path);

/// A sentence for the player about the game file.
std::string Describe(const Game& game);

struct Update {
    enum class State { Missing, OtherVersion, Ok };
    State state = State::Missing;
    u16 version = 0;
    std::string content;  ///< the installed update's program (an NCCH that build_game.py reads)
};

/// The update in Azahar's virtual SD card (Service::AM's title paths).
Update CheckUpdate();

/// "1.3.0" for a title version.
std::string VersionText(u16 version);

/// Whether a .cia is MH4U's v1.3.0 update, decrypted: empty if so, else why not.
std::string CheckUpdateCia(const std::string& path);

/// Where the port looks for the code library (recomp/<program id>.<dll|so|dylib>).
std::string LibraryPath();

/// The code library as the game's start would find it, from the app's code check (CheckCode,
/// run as a process of its own so that the library isn't loaded here).
struct Code {
    enum class State {
        Unknown,   ///< not checked (no game yet)
        Missing,   ///< no library
        Checking,
        Ok,        ///< the game runs on it
        Partial,   ///< it runs, but the library is older than the app (some options missing)
        NotUsed,   ///< it won't run: notice says why (the update, a code mod, an old library)
        Failed,    ///< the check itself didn't work (notice says why)
    };
    State state = State::Unknown;
    std::string notice;
};

/// The verdict from the code check's exit status and output.
Code CodeFromCheck(int exit_code, const std::string& output);

/// Installs a CIA (the update) on a thread of its own, with its progress.
class Install {
public:
    ~Install();
    void Start(const std::string& path);
    bool Running() const {
        return running;
    }
    float Progress() const {
        return progress;
    }
    /// After the end: empty if it worked, else what went wrong.
    const std::string& Error() const {
        return error;
    }

private:
    std::thread thread;
    std::atomic<bool> running{false};
    std::atomic<float> progress{0.0f};
    std::string error;
};

} // namespace Launcher::Checks
