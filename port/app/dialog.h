// The few dialogs the app needs before its window is up: choosing the game's file, messages.
#pragma once

#include <string>
#include <vector>

namespace App::Dialog {

/// Asks for a file with one of `extensions` ("3ds", "cci", ...): the system's own file dialog
/// on Windows and macOS, zenity or kdialog on Linux. Empty if cancelled or there is none.
std::string OpenFile(const std::string& title, const std::vector<std::string>& extensions);

/// A message box (also in the log).
void Message(const std::string& title, const std::string& text, bool error = false);

} // namespace App::Dialog
