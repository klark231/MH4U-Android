// (httplib first: on Windows it needs winsock2.h before windows.h)
#include <httplib.h>

#include "launcher/lobby.h"

#include <algorithm>
#include <cctype>
#include <fmt/format.h>
#include <json.hpp>

#include "common/file_util.h"
#include "common/logging/log.h"

namespace Launcher::Lobby {

namespace {

constexpr u64 MH4U_USA = 0x0004000000126300;

std::string Text(const nlohmann::json& object, const char* key) {
    const auto it = object.find(key);
    return it != object.end() && it->is_string() ? it->get<std::string>() : std::string();
}

u64 Number(const nlohmann::json& object, const char* key) {
    const auto it = object.find(key);
    return it != object.end() && it->is_number_unsigned() ? it->get<u64>() : 0;
}

std::string Folded(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return std::tolower(c); });
    return s;
}

} // namespace

std::string NormalizeUrl(const std::string& typed) {
    std::string url = typed;
    const auto first = url.find_first_not_of(" \t");
    const auto last = url.find_last_not_of(" \t/");
    url = first == std::string::npos ? std::string() : url.substr(first, last - first + 1);
    if (!url.empty() && url.find("://") == std::string::npos) {
        url = "http://" + url;
    }
    return url;
}

List Parse(const std::string& body) {
    List list;
    const auto json = nlohmann::json::parse(body, nullptr, false);
    if (json.is_discarded() || !json.is_object() || !json.contains("rooms") ||
        !json["rooms"].is_array()) {
        list.error = "The lobby's answer isn't a list of rooms.";
        return list;
    }
    for (const auto& item : json["rooms"]) {
        if (!item.is_object()) {
            continue;
        }
        Room room;
        room.name = Text(item, "name");
        room.description = Text(item, "description");
        room.owner = Text(item, "owner");
        room.address = Text(item, "address");
        room.game = Text(item, "preferredGameName");
        room.port = static_cast<u16>(std::min<u64>(Number(item, "port"), 65535));
        room.game_id = Number(item, "preferredGameId");
        room.max_players = static_cast<u32>(std::min<u64>(Number(item, "maxPlayers"), 65535));
        const auto password = item.find("hasPassword");
        room.password = password != item.end() && password->is_boolean() && password->get<bool>();
        if (const auto players = item.find("players"); players != item.end() && players->is_array()) {
            for (const auto& player : *players) {
                if (player.is_object()) {
                    room.players.push_back(Text(player, "nickname"));
                }
            }
        }
        room.mh4u = room.game_id == MH4U_USA;
        if (room.address.empty() || room.port == 0) {
            continue;  // nowhere to join
        }
        list.rooms.push_back(std::move(room));
    }
    std::stable_sort(list.rooms.begin(), list.rooms.end(), [](const Room& a, const Room& b) {
        if (a.mh4u != b.mh4u) {
            return a.mh4u;
        }
        return Folded(a.name) < Folded(b.name);
    });
    list.ok = true;
    return list;
}

List Fetch(const std::string& lobby_url) {
    List list;
    const std::string url = NormalizeUrl(lobby_url);
    if (url.empty()) {
        list.error = "No lobby: put its address under Your name and lobby.";
        return list;
    }
    // scheme://host[:port] for the client, and any path in front of /lobby (a lobby behind a
    // reverse proxy's path; Azahar itself ignores the path)
    const auto host_start = url.find("://") + 3;
    const auto path_start = url.find('/', host_start);
    const std::string base = url.substr(0, path_start);
    const std::string prefix = path_start == std::string::npos ? "" : url.substr(path_start);
    httplib::Client client(base);
    if (!client.is_valid()) {
        list.error = fmt::format("\"{}\" isn't a lobby address the app can use (http://HOST:PORT).",
                                 lobby_url);
        return list;
    }
    client.set_connection_timeout(5, 0);
    client.set_read_timeout(10, 0);
    client.set_write_timeout(5, 0);
    client.set_follow_location(true);
#if !defined(_WIN32) && !defined(__APPLE__)
    // the bundled TLS library looks for /etc/ssl/cert.pem, which not every distribution has
    for (const char* bundle : {"/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt",
                               "/etc/ssl/ca-bundle.pem", "/etc/ssl/cert.pem"}) {
        if (FileUtil::Exists(bundle)) {
            client.set_ca_cert_path(bundle);
            break;
        }
    }
#endif
    const httplib::Headers headers{{"api-version", "1"}, {"Accept", "application/json"}};
    const auto result = client.Get(prefix + "/lobby", headers);
    if (!result) {
        list.error = fmt::format("The lobby at {} didn't answer ({}).", url,
                                 httplib::to_string(result.error()));
        LOG_WARNING(Frontend, "{}", list.error);
        return list;
    }
    if (result->status != 200) {
        list.error = fmt::format("The lobby at {} answered with error {}.", url, result->status);
        LOG_WARNING(Frontend, "{}", list.error);
        return list;
    }
    list = Parse(result->body);
    LOG_INFO(Frontend, "{} rooms in the lobby at {}", list.rooms.size(), url);
    return list;
}

} // namespace Launcher::Lobby
