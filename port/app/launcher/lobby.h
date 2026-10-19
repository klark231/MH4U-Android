// A lobby's list of rooms (GET <lobby>/lobby: server/lobby/mh4u_lobby.py, or any lobby that
// speaks Azahar's web API), for the launcher's Online screen.
#pragma once

#include <string>
#include <vector>

#include "common/common_types.h"

namespace Launcher::Lobby {

struct Room {
    std::string name, description, owner, address, game;
    u16 port = 0;
    u64 game_id = 0;
    u32 max_players = 0;
    bool password = false;
    std::vector<std::string> players;  ///< nicknames
    bool mh4u = false;                 ///< set up for Monster Hunter 4 Ultimate (USA)
};

struct List {
    bool ok = false;
    std::string error;
    std::vector<Room> rooms;  ///< MH4U's first, then by name
};

/// The rooms a lobby lists. Blocks for a few seconds at most: run it on a worker thread.
List Fetch(const std::string& lobby_url);

/// The JSON a lobby answers with, as rooms (for tests and Fetch).
List Parse(const std::string& json);

/// A lobby address as typed ("lobby.example.org:24872") with its scheme ("http://...").
std::string NormalizeUrl(const std::string& url);

} // namespace Launcher::Lobby
