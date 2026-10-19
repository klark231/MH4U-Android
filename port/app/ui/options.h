// The game's options file (mh4u.ini) as text: read the way the port's hooks read it, and edited
// in place so that its comments and order stay.
#pragma once

#include <map>
#include <string>

namespace App::Ui {

/// "key = value" lines: a # starts a comment, spaces around keys and values don't count, and
/// of two lines with one key the last counts (port/src/hooks.cpp, LoadOptions).
std::map<std::string, std::string> ReadOptions(const std::string& text);

/// `text` with the keys of `values` set: each line that sets one of them rewritten in place
/// (its indentation and trailing comment kept), the keys it has no line for appended at the end,
/// everything else as it was (line endings too).
std::string WriteOptions(const std::string& text, const std::map<std::string, std::string>& values);

} // namespace App::Ui
