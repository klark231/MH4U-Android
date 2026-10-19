// The port's Python tools as the launcher runs them: where they are (the package's mh4u-tools
// folder, or the repository's tools/ for a build from source), which Python runs them, and
// what build_game.py's output says about its progress.
#pragma once

#include <string>
#include <vector>

namespace Launcher::Tools {

/// The folder with build_game.py and mods.py, empty if there is none. Looked for in
/// $MH4U_TOOLS, mh4u-tools/tools next to the executable (on Linux next to the AppImage, on a
/// Mac next to the app or in its Resources), then the repository the app was built from.
std::string FindToolsDir();

/// The folders FindToolsDir() looks at, in order (for a message when none has the tools).
std::vector<std::string> ToolsCandidates();

struct Python {
    std::vector<std::string> command;  ///< e.g. {"py", "-3"}; empty: none found
    std::string version;               ///< "3.12.4"
    std::string too_old;               ///< a Python that was found but is older than 3.10
};

/// Python 3.10 or newer: py -3, python, python3 on Windows (and where python.org installs it),
/// python3 elsewhere (on a Mac also Homebrew's and python.org's, which an app's PATH lacks).
/// Runs each candidate: call it on a worker thread.
Python FindPython();

/// How to get Python on this system, for the player.
std::string PythonHint(const Python& found);

/// Azahar's user folder for the tools' --install / --user-dir (no separator at the end).
std::string UserDir();

/// build_game.py --dump DUMP --install <user folder> --fetch-compiler
std::vector<std::string> BuildCommand(const Python& python, const std::string& tools,
                                      const std::string& dump);

/// mods.py --json --user-dir <user folder> ARGS...
std::vector<std::string> ModsCommand(const Python& python, const std::string& tools,
                                     const std::vector<std::string>& args);

struct Progress {
    float fraction = 0.0f;  ///< 0..1, roughly
    std::string stage;      ///< what it is doing, for the player
};

/// build_game.py's progress from its output so far (its lines as Process::Lines() gives them).
Progress BuildProgress(const std::vector<std::string>& lines);

/// A download's or an install's progress from mods.py's progress lines ("@{...}", --json): the
/// current stage's message and how far it is (fraction < 0: not known).
Progress ModsProgress(const std::vector<std::string>& lines);

/// mods.py --json's output without its progress lines: the one JSON object.
std::string WithoutProgress(const std::string& output);

/// The line that says what went wrong, from a failed tool's output (its last one).
std::string ErrorSummary(const std::vector<std::string>& lines);

} // namespace Launcher::Tools
