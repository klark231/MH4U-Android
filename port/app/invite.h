// Invite codes: one line a host shares and a friend pastes to join their room,
//     mh4u://HOST:PORT              a room without a password
//     mh4u://HOST:PORT?password=PW  ... with one (percent-encoded)
// A plain address (HOST or HOST:PORT) is taken as well, and a code inside a longer text (a chat
// message: "join me: mh4u://...") is found. Standard library only (tests/test_app_invite.py).
#pragma once

#include <string>

namespace App::Invite {

inline constexpr int DEFAULT_PORT = 24872;

struct Room {
    bool ok = false;
    std::string error;     ///< why `text` isn't a room (when !ok), for the player
    std::string host;      ///< a host name or an IPv4 address
    int port = DEFAULT_PORT;
    std::string password;  ///< from the code; a plain address has none
    bool code = false;     ///< it came as an mh4u:// code (its password counts, even empty)
};

/// The room in `text`: an mh4u:// code (anywhere in it), or HOST[:PORT] (the whole text, spaces
/// around it aside).
Room Parse(const std::string& text);

/// The code for a room: mh4u://HOST:PORT, with ?password=... when it has one.
std::string Format(const std::string& host, int port, const std::string& password);

/// "HOST:PORT", as mh4u-app.ini's join keeps it.
std::string Address(const std::string& host, int port);

/// Which kind of network a PC's IPv4 address belongs to, for the host's list of addresses to
/// give out: "loopback", "lan" (192.168/16, 10/8, 172.16/12), "tailscale" (100.64/10, which is
/// also a provider's shared address, CGNAT), "radmin" (26/8, Radmin VPN), "link-local"
/// (169.254/16: no network), "public", or "" for text that isn't an IPv4 address.
std::string NetworkKind(const std::string& ipv4);

} // namespace App::Invite
