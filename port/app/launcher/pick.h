// The system's own file and folder pickers for the launcher (a game file, the update's .cia, a
// mod's folder or .zip): Windows' and macOS's dialogs, zenity or kdialog on Linux.
#pragma once

#include <string>
#include <vector>

struct SDL_Window;

namespace Launcher::Pick {

struct Result {
    std::string path;        ///< empty if cancelled
    bool available = true;   ///< false: this system has no dialog (type the path instead)
};

/// The launcher's window, which the dialogs belong to (Windows).
void SetOwner(SDL_Window* window);

/// A file with one of `extensions` ("cia", "3ds", ...), shown as `kind` ("CIA files").
Result File(const std::string& title, const std::string& kind,
            const std::vector<std::string>& extensions);

/// A folder.
Result Folder(const std::string& title);

} // namespace Launcher::Pick
