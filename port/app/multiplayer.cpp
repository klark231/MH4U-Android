// (httplib first: on Windows it needs winsock2.h before windows.h)
#include <httplib.h>

#include "multiplayer.h"

#include <algorithm>
#include <atomic>
#include <cctype>
#include <chrono>
#include <condition_variable>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <thread>
#include <fmt/format.h>
#include <json.hpp>

#ifdef _WIN32
#include <ws2tcpip.h>
#else
#include <arpa/inet.h>
#include <ifaddrs.h>
#include <net/if.h>
#include <netinet/in.h>
#include <sys/socket.h>
#endif

#include "common/file_util.h"
#include "common/logging/log.h"
#include "common/settings.h"
#include "config.h"
#include "core/core.h"
#include "core/hle/kernel/kernel.h"
#include "core/hle/kernel/shared_page.h"
#include "core/hle/service/cfg/cfg.h"
#include "invite.h"
#include "launcher/lobby.h"
#include "network/network.h"
#include "network/room.h"
#include "network/room_member.h"
#include "network/verify_user.h"

#ifdef ENABLE_WEB_SERVICE
#include "network/announce_multiplayer_session.h"
#include "web_service/verify_user_jwt.h"
#endif

namespace App::Multiplayer {

namespace {

using Clock = std::chrono::steady_clock;
using Network::MacAddress;
using Network::RoomMember;

constexpr u64 MH4U_USA = 0x0004000000126300;
constexpr const char* MH4U_NAME = "Monster Hunter 4 Ultimate";

/// Who this console is in a room, read from its settings on the emulation thread.
struct Identity {
    std::string nickname;
    std::string console_id;
    MacAddress mac{};
};

struct Traffic {
    Clock::time_point last{};  ///< the last local-play frame (not a net sync clock)
    u64 beacons = 0, data = 0, other = 0, clocks = 0;
};

/// The port's net sync clock reports (port/src/netsync.cpp): a Wi-Fi packet type no 3DS frame
/// uses. They show that a port player is in the room, not that their game plays locally.
constexpr auto NET_SYNC_PACKET = static_cast<Network::WifiPacket::PacketType>(0x4d);

struct State {
    Core::System* system = nullptr;
    Config* config = nullptr;

    std::mutex mutex;  // everything below
    Status status;
    std::thread job;
    std::thread lookup;  // the host's addresses after a start in a hosted room
    bool job_running = false;
    std::vector<std::function<void()>> for_main;  // run by Tick
    std::map<MacAddress, Traffic> traffic;          // local-play packets per sender
    Clock::time_point traffic_logged{};
    std::map<MacAddress, Traffic> traffic_at_log;
    RoomMember::MemberList members;
    RoomMember::Error last_error{};
    u64 error_count = 0;
    bool was_in_room = false;
    // --join-after
    bool join_later = false;
    JoinRequest later;
    double later_seconds = 0;
    Clock::time_point started{};
    bool tick_seen = false;

#ifdef ENABLE_WEB_SERVICE
    std::shared_ptr<Network::AnnounceMultiplayerSession> announce;
#endif
    RoomMember::CallbackHandle<RoomMember::Error> on_error;
    RoomMember::CallbackHandle<RoomMember::State> on_state;
    RoomMember::CallbackHandle<Network::RoomInformation> on_info;
    RoomMember::CallbackHandle<Network::WifiPacket> on_wifi;
    RoomMember::CallbackHandle<Network::StatusMessageEntry> on_status;

    ~State() {
        // (an exit that didn't go through Shutdown)
        for (std::thread* t : {&job, &lookup}) {
            if (t->joinable()) {
                t->join();
            }
        }
    }
};

State& S() {
    static State state;
    return state;
}

std::string MacText(const MacAddress& mac) {
    return fmt::format("{:02X}:{:02X}:{:02X}:{:02X}:{:02X}:{:02X}", mac[0], mac[1], mac[2], mac[3],
                       mac[4], mac[5]);
}

/// What a room's refusal (or its end) means, and what to do about it.
void Explain(RoomMember::Error error, const std::string& where, const std::string& nickname,
             std::string& message, std::string& hint) {
    using E = RoomMember::Error;
    switch (error) {
    case E::CouldNotConnect:
        message = fmt::format("No room answered at {}.", where);
        hint = "Check the address and port. The host must forward UDP port " +
               where.substr(where.rfind(':') + 1) +
               " on their router to their PC and allow the app in their firewall (or you all "
               "play in one virtual network: Tailscale, ZeroTier, Radmin VPN, with the host's "
               "address there). Behind a provider's shared address (CGNAT) forwarding doesn't "
               "work: use a virtual network or a room on a server.";
        break;
    case E::WrongPassword:
        message = "The room's password is wrong.";
        hint = "Ask the host for it (an invite code carries it).";
        break;
    case E::NameCollision:
        message = fmt::format("The room turned the name \"{}\" away: someone in it has that name.",
                              nickname);
        hint = "Choose another name below (4 to 20 letters, digits, spaces, '.', '_' or '-').";
        break;
    case E::ConsoleIdCollision:
        message = "Someone in the room has the same console ID as this one: the two of you play "
                  "from copies of one Azahar user folder.";
        hint = "One of you starts the app once with --new-console-id (it gives this console a "
               "new ID and keeps it), then joins again.";
        break;
    case E::MacCollision:
        message = "Someone in the room has this console's MAC address.";
        hint = "Try again: the room then gives this console another one.";
        break;
    case E::WrongVersion:
        message = "The room speaks another version of Azahar's room protocol.";
        hint = "The host's Azahar or room server is much older or newer: use a recent one (2125 or "
               "later) on both sides.";
        break;
    case E::RoomIsFull:
        message = "The room is full.";
        hint = "Wait for someone to leave, or ask the host for a room with more places.";
        break;
    case E::HostBanned:
        message = "The room's host has banned you.";
        hint = "";
        break;
    case E::HostKicked:
        message = "The room's host removed you from the room.";
        hint = "";
        break;
    case E::LostConnection:
        message = "The connection to the room was lost.";
        hint = "The host's PC or connection went away, or the network dropped. Rejoin when it's back.";
        break;
    default:
        message = fmt::format("Couldn't join the room at {} ({}).", where, Network::GetErrorStr(error));
        hint = "Is the network up? Try again.";
        break;
    }
}

void ForMain(std::function<void()> action) {
    auto& s = S();
    std::scoped_lock lock{s.mutex};
    s.for_main.push_back(std::move(action));
}

/// The console's identity (the emulation thread, or before the game starts).
Identity Me() {
    auto& s = S();
    Identity me;
    me.nickname = s.config->nickname;
    if (me.nickname.empty()) {
        me.nickname = Service::CFG::GetUsername(*s.system);
    }
    me.console_id = Service::CFG::GetConsoleIdHash(*s.system);
    me.mac = Service::CFG::GetConsoleMacAddress(*s.system);
    return me;
}

/// Gives the running console this MAC address (the shared page the game reads it from), as
/// Azahar's local-wireless service does when it starts in a room. The emulation thread.
void SetConsoleMac(const MacAddress& mac) {
    auto& system = *S().system;
    if (!system.IsPoweredOn()) {
        return;
    }
    auto& page = system.Kernel().GetSharedPageHandler();
    if (page.GetMacAddress() != mac) {
        page.SetMacAddress(mac);
        LOG_INFO(Network, "the console's MAC address is now {}", MacText(mac));
    }
}

void StopAnnounce() {
#ifdef ENABLE_WEB_SERVICE
    std::shared_ptr<Network::AnnounceMultiplayerSession> announce;
    {
        auto& s = S();
        std::scoped_lock lock{s.mutex};
        announce = std::move(s.announce);
    }
    if (announce) {
        announce->Stop();
    }
#endif
}

/// Leaves (and closes a hosted room when `close`): blocks for a moment.
void LeaveNow(bool close) {
    const auto member = Network::GetRoomMember().lock();
    if (member && member->IsConnected()) {
        member->Leave();
        LOG_INFO(Network, "left the room");
    }
    const auto room = Network::GetRoom().lock();
    if (close && room && room->GetState() == Network::Room::State::Open) {
        StopAnnounce();
        room->Destroy();
        LOG_INFO(Network, "closed the room");
    }
}

bool Hosting() {
    const auto room = Network::GetRoom().lock();
    return room && room->GetState() == Network::Room::State::Open;
}

/// Joins and waits for the room's answer. The console's own MAC address first (the one the
/// game started with); a room where it's taken gets another one.
bool JoinNow(const Identity& me, const std::string& host, int port, const std::string& password,
             std::string& error, std::string& hint) {
    auto& s = S();
    const auto member = Network::GetRoomMember().lock();
    if (!member) {
        error = "The network library didn't start.";
        return false;
    }
    const std::string where = Invite::Address(host, port);
    for (const bool own_mac : {true, false}) {
        u64 errors_before;
        {
            std::scoped_lock lock{s.mutex};
            errors_before = s.error_count;
        }
        LOG_INFO(Network, "joining the room at {} as {}{}", where, me.nickname,
                 own_mac ? fmt::format(" (MAC {})", MacText(me.mac)) : "");
        member->Join(me.nickname, me.console_id, host.c_str(), static_cast<u16>(port), 0,
                     own_mac ? me.mac : Network::NoPreferredMac, password);
        const auto deadline = Clock::now() + std::chrono::seconds(10);
        while (member->GetState() == RoomMember::State::Joining && Clock::now() < deadline) {
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
        if (member->GetState() == RoomMember::State::Joined ||
            member->GetState() == RoomMember::State::Moderator) {
            LOG_INFO(Network, "in the room at {} as {}, MAC {}", where, me.nickname,
                     MacText(member->GetMacAddress()));
            return true;
        }
        // (the member gives its state, then the reason)
        RoomMember::Error why = RoomMember::Error::CouldNotConnect;
        bool known = false;
        const auto reason_deadline = Clock::now() + std::chrono::milliseconds(300);
        while (!known && member->GetState() != RoomMember::State::Joining) {
            {
                std::scoped_lock lock{s.mutex};
                if (s.error_count != errors_before) {
                    why = s.last_error;
                    known = true;
                }
            }
            if (known || Clock::now() >= reason_deadline) {
                break;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(5));
        }
        if (member->GetState() == RoomMember::State::Joining) {
            member->Leave();  // no answer to the join request in 10 s
        }
        if (known && why == RoomMember::Error::MacCollision && own_mac) {
            LOG_INFO(Network, "the room has another console with MAC {}: joining with one it gives",
                     MacText(me.mac));
            continue;
        }
        Explain(why, where, me.nickname, error, hint);
        LOG_WARNING(Network, "couldn't join the room at {}: {}", where, error);
        return false;
    }
    return false;
}

bool HostNow(const Identity& me, const HostRequest& request, std::string& error, std::string& hint,
             std::string& lobby_note) {
    auto& s = S();
    const auto room = Network::GetRoom().lock();
    if (!room) {
        error = "The network library didn't start.";
        return false;
    }
    std::string lobby, token;
    {
        std::scoped_lock lock{s.mutex};
        lobby = s.config->lobby;
        token = s.config->token;
    }
    const bool listed = request.listed && !lobby.empty() && !token.empty();
    std::unique_ptr<Network::VerifyUser::Backend> verify =
        std::make_unique<Network::VerifyUser::NullBackend>();
#ifdef ENABLE_WEB_SERVICE
    if (listed) {
        Settings::values.web_api_url = Launcher::Lobby::NormalizeUrl(lobby);
        Settings::values.network_token = token;
        verify = std::make_unique<WebService::VerifyUserJWT>(Settings::values.web_api_url.GetValue());
    }
#endif
    const std::string name = request.name.empty() ? "MH4U" : request.name;
    if (!room->Create(name, "", "", static_cast<u16>(request.port), request.password,
                      static_cast<u32>(std::clamp(request.slots, 2, 254)), me.nickname, MH4U_NAME,
                      MH4U_USA, std::move(verify))) {
        error = fmt::format("Couldn't open a room on UDP port {}.", request.port);
        hint = "Another program (another room, or a second copy of the app) uses that port: close it "
               "or choose another port.";
        return false;
    }
    LOG_INFO(Network, "hosting room \"{}\" on port {}", name, request.port);
    lobby_note = listed ? "" : (request.listed ? "Not listed: listing needs a lobby and your token there."
                                               : "Unlisted: friends join by address or invite.");
#ifdef ENABLE_WEB_SERVICE
    if (listed) {
        auto announce = std::make_shared<Network::AnnounceMultiplayerSession>(me.nickname);
        const auto result = announce->Register();
        if (result.result_code != Common::WebResult::Code::Success) {
            lobby_note = fmt::format("The lobby didn't list the room: {}. Friends can still join "
                                     "by address or invite.",
                                     result.result_string);
            LOG_ERROR(Network, "the lobby at {} didn't list the room: {}", lobby, result.result_string);
        } else {
            announce->Start();
            lobby_note = "Listed in the lobby at " + Launcher::Lobby::NormalizeUrl(lobby) + ".";
            LOG_INFO(Network, "room listed in the lobby at {}", lobby);
            std::scoped_lock lock{s.mutex};
            s.announce = std::move(announce);
        }
    }
#endif
    if (!JoinNow(me, "127.0.0.1", request.port, request.password, error, hint)) {
        StopAnnounce();
        room->Destroy();
        return false;
    }
    return true;
}

/// This PC's IPv4 addresses, loopback and link-local left out, LAN ones first.
std::vector<Address> LocalAddresses() {
    std::vector<Address> list;
    const auto add = [&list](const std::string& ip) {
        const std::string kind = Invite::NetworkKind(ip);
        if (kind.empty() || kind == "loopback" || kind == "link-local") {
            return;
        }
        if (std::none_of(list.begin(), list.end(), [&](const Address& a) { return a.ip == ip; })) {
            list.push_back({ip, kind});
        }
    };
#ifdef _WIN32
    char name[256] = {};
    if (gethostname(name, sizeof(name)) == 0) {
        addrinfo hints{};
        hints.ai_family = AF_INET;
        addrinfo* found = nullptr;
        if (getaddrinfo(name, nullptr, &hints, &found) == 0) {
            for (addrinfo* a = found; a; a = a->ai_next) {
                char text[INET_ADDRSTRLEN] = {};
                const auto* in = reinterpret_cast<const sockaddr_in*>(a->ai_addr);
                if (inet_ntop(AF_INET, &in->sin_addr, text, sizeof(text))) {
                    add(text);
                }
            }
            freeaddrinfo(found);
        }
    }
#else
    ifaddrs* interfaces = nullptr;
    if (getifaddrs(&interfaces) == 0) {
        for (ifaddrs* i = interfaces; i; i = i->ifa_next) {
            if (!i->ifa_addr || i->ifa_addr->sa_family != AF_INET || !(i->ifa_flags & IFF_UP)) {
                continue;
            }
            char text[INET_ADDRSTRLEN] = {};
            const auto* in = reinterpret_cast<const sockaddr_in*>(i->ifa_addr);
            if (inet_ntop(AF_INET, &in->sin_addr, text, sizeof(text))) {
                add(text);
            }
        }
        freeifaddrs(interfaces);
    }
#endif
    const auto rank = [](const Address& a) {
        return a.kind == "lan" ? 0 : a.kind == "public" ? 1 : 2;
    };
    std::stable_sort(list.begin(), list.end(),
                     [&](const Address& a, const Address& b) { return rank(a) < rank(b); });
    return list;
}

/// The public address as the lobby sees this PC (GET <lobby>/ip, server/lobby), "" when it
/// can't tell (no lobby, an older one, the lobby on this PC's network).
std::string PublicAddress(const std::string& lobby_url) {
    const std::string url = Launcher::Lobby::NormalizeUrl(lobby_url);
    if (url.empty()) {
        return "";
    }
    const auto host_start = url.find("://") + 3;
    const auto path_start = url.find('/', host_start);
    httplib::Client client(url.substr(0, path_start));
    if (!client.is_valid()) {
        return "";
    }
    client.set_connection_timeout(5, 0);
    client.set_read_timeout(5, 0);
    const std::string prefix = path_start == std::string::npos ? "" : url.substr(path_start);
    const auto result = client.Get(prefix + "/ip", {{"Accept", "application/json"}});
    if (!result || result->status != 200) {
        return "";
    }
    const auto json = nlohmann::json::parse(result->body, nullptr, false);
    if (!json.is_object() || !json.contains("public") || !json["public"].is_string()) {
        return "";
    }
    return json["public"].get<std::string>();
}

/// Where friends reach this PC, for the host's invite (the lobby's answer can take seconds).
void FindAddresses(const std::string& lobby) {
    auto& s = S();
    auto addresses = LocalAddresses();
    {
        std::scoped_lock lock{s.mutex};
        s.status.addresses = std::move(addresses);
    }
    const std::string address = PublicAddress(lobby);
    std::scoped_lock lock{s.mutex};
    s.status.public_address = address;
    s.status.public_checked = true;
    if (!address.empty()) {
        LOG_INFO(Network, "the lobby sees this PC at {}", address);
    }
}

/// Runs `work` on the job thread (one at a time).
bool StartJob(const std::string& action, std::function<void()> work) {
    auto& s = S();
    std::thread old;
    {
        std::scoped_lock lock{s.mutex};
        if (s.job_running) {
            return false;
        }
        s.job_running = true;
        old = std::move(s.job);
        s.status.phase = Phase::Busy;
        s.status.action = action;
        s.status.error.clear();
        s.status.hint.clear();
    }
    if (old.joinable()) {
        old.join();  // (finished: job_running was false)
    }
    std::thread job([work = std::move(work)] {
        work();
        auto& s = S();
        std::scoped_lock lock{s.mutex};
        s.job_running = false;
        s.status.action.clear();
    });
    std::scoped_lock lock{s.mutex};
    s.job = std::move(job);
    return true;
}

/// After a job: the status as the room member is now.
void Refresh(const std::string& error, const std::string& hint) {
    auto& s = S();
    const auto member = Network::GetRoomMember().lock();
    const bool in_room = member && (member->GetState() == RoomMember::State::Joined ||
                                    member->GetState() == RoomMember::State::Moderator);
    std::scoped_lock lock{s.mutex};
    s.status.phase = in_room ? Phase::InRoom : Phase::Off;
    s.status.error = error;
    s.status.hint = hint;
    s.status.hosting = Hosting();
    s.was_in_room = in_room;
    if (in_room) {
        const auto info = member->GetRoomInformation();
        s.status.room_name = info.name;
        s.status.slots = info.member_slots;
        s.status.nickname = member->GetNickname();
        s.status.moderator = member->GetState() == RoomMember::State::Moderator;
        s.members = member->GetMemberInformation();
    }
}

/// The settings for the next start and Rejoin, saved (the emulation thread).
void Remember(std::function<void(Config&)> change) {
    ForMain([change = std::move(change)] {
        auto& s = S();
        change(*s.config);
        Config file = App::Load();
        change(file);
        App::Save(file);
    });
}

} // namespace

bool ValidNickname(const std::string& name) {
    return name.size() >= 4 && name.size() <= 20 && std::all_of(name.begin(), name.end(), [](char c) {
               return std::isalnum(static_cast<unsigned char>(c)) || c == ' ' || c == '.' ||
                      c == '_' || c == '-';
           });
}

std::string Nickname() {
    auto& s = S();
    if (!s.config->nickname.empty()) {
        return s.config->nickname;
    }
    return Service::CFG::GetUsername(*s.system);
}

void Init(Core::System& system, Config& config) {
    auto& s = S();
    s.system = &system;
    s.config = &config;
    // the console's MAC address is kept in sysdata/mac.txt; without the folder it is a new random
    // one each time it's read before the game starts, and the room's differs from the game's
    FileUtil::CreateFullPath(FileUtil::GetUserPath(FileUtil::UserPath::SysDataDir));
    const auto member = Network::GetRoomMember().lock();
    if (!member) {
        return;
    }
    s.on_error = member->BindOnError([](const RoomMember::Error& error) {
        LOG_WARNING(Network, "room: {}", Network::GetErrorStr(error));
        auto& s = S();
        std::scoped_lock lock{s.mutex};
        s.last_error = error;
        ++s.error_count;
        using E = RoomMember::Error;
        if (!s.job_running && (error == E::LostConnection || error == E::HostKicked || error == E::HostBanned)) {
            // out of the room without asking (the state came first, see on_state)
            Explain(error, Invite::Address(s.status.host, s.status.port), s.status.nickname,
                    s.status.error, s.status.hint);
        }
    });
    s.on_status = member->BindOnStatusMessageReceived([](const Network::StatusMessageEntry& msg) {
        if (msg.type == Network::IdMemberJoin) {
            LOG_INFO(Network, "{} joined the room", msg.nickname);
        } else if (msg.type == Network::IdMemberLeave) {
            LOG_INFO(Network, "{} left the room", msg.nickname);
        }
    });
    // the list is copied on the network's thread, right after it changed it
    s.on_info = member->BindOnRoomInformationChanged([](const Network::RoomInformation& info) {
        const auto member = Network::GetRoomMember().lock();
        auto& s = S();
        std::scoped_lock lock{s.mutex};
        s.status.room_name = info.name;
        s.status.slots = info.member_slots;
        if (member) {
            s.members = member->GetMemberInformation();
        }
    });
    s.on_state = member->BindOnStateChanged([](const RoomMember::State& state) {
        auto& s = S();
        const bool in_room = state == RoomMember::State::Joined || state == RoomMember::State::Moderator;
        std::scoped_lock lock{s.mutex};
        if (s.was_in_room && !in_room && !s.job_running) {
            // dropped, kicked, or the room closed (on_error then says why): the console gets
            // its own address back
            s.status.phase = Phase::Off;
            s.status.error = "Out of the room.";
            s.status.hint.clear();
            s.status.joined_in_game = false;
            s.status.hosting = Hosting();
            s.for_main.push_back([] { SetConsoleMac(Service::CFG::GetConsoleMacAddress(*S().system)); });
        }
        s.was_in_room = in_room;
        if (in_room) {
            s.status.moderator = state == RoomMember::State::Moderator;
        }
    });
    s.on_wifi = member->BindOnWifiPacketReceived([](const Network::WifiPacket& packet) {
        auto& s = S();
        std::scoped_lock lock{s.mutex};
        auto& t = s.traffic[packet.transmitter_address];
        if (packet.type == NET_SYNC_PACKET) {
            ++t.clocks;
            return;
        }
        t.last = Clock::now();
        switch (packet.type) {
        case Network::WifiPacket::PacketType::Beacon:
            ++t.beacons;
            break;
        case Network::WifiPacket::PacketType::Data:
            ++t.data;
            break;
        default:
            ++t.other;
            break;
        }
    });
}

void Shutdown() {
    auto& s = S();
    if (!s.system) {
        return;
    }
    std::thread job, lookup;
    {
        std::scoped_lock lock{s.mutex};
        job = std::move(s.job);
        lookup = std::move(s.lookup);
    }
    if (job.joinable()) {
        job.join();
    }
    if (lookup.joinable()) {
        lookup.join();
    }
    StopAnnounce();
    LeaveNow(true);
    if (const auto member = Network::GetRoomMember().lock()) {
        member->Unbind(s.on_error);
        member->Unbind(s.on_status);
        member->Unbind(s.on_info);
        member->Unbind(s.on_state);
        member->Unbind(s.on_wifi);
    }
    s.system = nullptr;
}

bool StartAtBoot(std::string& error) {
    auto& s = S();
    const Config& c = *s.config;
    if (c.join.empty() && c.host <= 0) {
        return true;
    }
    const Identity me = Me();
    std::string hint, host = "127.0.0.1", password = c.password;
    int port = c.host;
    bool ok;
    if (c.host > 0) {
        HostRequest request;
        request.port = c.host;
        request.name = c.room_name;
        request.slots = c.room_members;
        request.password = c.password;
        request.listed = c.room_public;
        std::string note;
        ok = HostNow(me, request, error, hint, note);
        std::scoped_lock lock{s.mutex};
        s.status.lobby_note = note;
    } else {
        const auto room = Invite::Parse(c.join);
        if (!room.ok) {
            error = fmt::format("join = {} in mh4u-app.ini: {}", c.join, room.error);
            return false;
        }
        host = room.host;
        port = room.port;
        password = room.code ? room.password : c.password;
        ok = JoinNow(me, host, port, password, error, hint);
    }
    {
        std::scoped_lock lock{s.mutex};
        s.status.host = host;
        s.status.port = port;
        s.status.password = password;
    }
    Refresh(ok ? "" : error, ok ? "" : hint);
    if (!ok && !hint.empty()) {
        error += " " + hint;
    }
    if (ok) {
        const bool hosted = c.host > 0;
        const std::string last = hosted ? "host" : Invite::Format(host, port, password);
        // the last room, for Rejoin (the file's: --join and --host don't change join and host)
        Config file = App::Load();
        file.last_room = last;
        if (hosted) {
            file.room_port = c.host;
        }
        App::Save(file);
        s.config->last_room = last;
        if (hosted) {
            std::scoped_lock lock{s.mutex};
            s.lookup = std::thread([lobby = c.lobby] { FindAddresses(lobby); });
        }
    }
    return ok;
}

bool Join(const JoinRequest& request) {
    auto& s = S();
    if (!s.system) {
        return false;
    }
    const Identity me = Me();
    const bool in_game = s.system->IsPoweredOn();
    return StartJob(fmt::format("Joining {}...", request.label.empty()
                                                     ? Invite::Address(request.host, request.port)
                                                     : request.label),
                    [me, request, in_game] {
                        auto& s = S();
                        if (Hosting()) {
                            LeaveNow(true);  // one room at a time
                        }
                        std::string error, hint;
                        const bool ok = JoinNow(me, request.host, request.port, request.password, error, hint);
                        if (ok) {
                            {
                                std::scoped_lock lock{s.mutex};
                                s.status.host = request.host;
                                s.status.port = request.port;
                                s.status.password = request.password;
                                s.status.joined_in_game = in_game;
                                s.status.lobby_note.clear();
                                s.status.addresses.clear();
                                s.status.public_address.clear();
                                s.status.public_checked = false;
                                s.traffic.clear();
                            }
                            if (const auto member = Network::GetRoomMember().lock(); member && member->IsConnected()) {
                                const MacAddress mac = member->GetMacAddress();
                                ForMain([mac] { SetConsoleMac(mac); });
                            }
                            const std::string address = Invite::Address(request.host, request.port);
                            const std::string code = Invite::Format(request.host, request.port, request.password);
                            const std::string password = request.password;
                            Remember([address, password, code](Config& c) {
                                c.join = address;
                                c.password = password;
                                c.host = 0;
                                c.last_room = code;
                            });
                        } else {
                            ForMain([] { SetConsoleMac(Service::CFG::GetConsoleMacAddress(*S().system)); });
                        }
                        Refresh(error, hint);
                    });
}

bool Host(const HostRequest& request) {
    auto& s = S();
    if (!s.system) {
        return false;
    }
    const Identity me = Me();
    const bool in_game = s.system->IsPoweredOn();
    std::string lobby;
    {
        std::scoped_lock lock{s.mutex};
        lobby = s.config->lobby;
    }
    return StartJob(fmt::format("Opening a room on port {}...", request.port), [me, request, in_game, lobby] {
        auto& s = S();
        LeaveNow(true);
        std::string error, hint, note;
        const bool ok = HostNow(me, request, error, hint, note);
        if (ok) {
            {
                std::scoped_lock lock{s.mutex};
                s.status.host = "127.0.0.1";
                s.status.port = request.port;
                s.status.password = request.password;
                s.status.joined_in_game = in_game;
                s.status.lobby_note = note;
                s.status.addresses.clear();
                s.status.public_address.clear();
                s.status.public_checked = false;
                s.traffic.clear();
            }
            if (const auto member = Network::GetRoomMember().lock(); member && member->IsConnected()) {
                const MacAddress mac = member->GetMacAddress();
                ForMain([mac] { SetConsoleMac(mac); });
            }
            Remember([request](Config& c) {
                c.host = request.port;
                c.room_port = request.port;
                c.room_name = request.name;
                c.room_members = request.slots;
                c.password = request.password;
                c.room_public = request.listed;
                c.join.clear();
                c.last_room = "host";
            });
        } else {
            ForMain([] { SetConsoleMac(Service::CFG::GetConsoleMacAddress(*S().system)); });
        }
        Refresh(error, hint);
        if (ok) {
            FindAddresses(lobby);
        }
    });
}

bool Leave(bool close) {
    auto& s = S();
    if (!s.system) {
        return false;
    }
    return StartJob(close ? "Closing the room..." : "Leaving the room...", [close] {
        auto& s = S();
        {
            std::scoped_lock lock{s.mutex};
            s.was_in_room = false;  // not an error
        }
        LeaveNow(close);
        ForMain([] { SetConsoleMac(Service::CFG::GetConsoleMacAddress(*S().system)); });
        Remember([](Config& c) {
            c.join.clear();
            c.host = 0;
        });
        Refresh("", "");
        std::scoped_lock lock{s.mutex};
        s.status.joined_in_game = false;
    });
}

std::string LastRoom() {
    auto& s = S();
    if (!s.config) {
        return "";
    }
    const std::string& last = s.config->last_room;
    if (last == "host") {
        return fmt::format("host \"{}\" on port {}", s.config->room_name, s.config->room_port);
    }
    const auto room = Invite::Parse(last);
    return room.ok ? "join " + Invite::Address(room.host, room.port) : "";
}

bool Rejoin() {
    auto& s = S();
    if (!s.config) {
        return false;
    }
    const Config& c = *s.config;
    if (c.last_room == "host") {
        HostRequest request;
        request.port = c.room_port;
        request.name = c.room_name;
        request.slots = c.room_members;
        request.password = c.password;
        request.listed = c.room_public;
        return Host(request);
    }
    const auto room = Invite::Parse(c.last_room);
    if (!room.ok) {
        return false;
    }
    JoinRequest request;
    request.host = room.host;
    request.port = room.port;
    request.password = room.password;
    return Join(request);
}

void JoinLater(const JoinRequest& request, double seconds) {
    auto& s = S();
    std::scoped_lock lock{s.mutex};
    s.join_later = true;
    s.later = request;
    s.later_seconds = seconds;
}

void Tick() {
    auto& s = S();
    std::vector<std::function<void()>> actions;
    bool join_now = false;
    JoinRequest later;
    std::string traffic_line;
    {
        std::scoped_lock lock{s.mutex};
        actions.swap(s.for_main);
        const auto now = Clock::now();
        if (!s.tick_seen) {
            s.tick_seen = true;
            s.started = now;
            s.traffic_logged = now;
        }
        if (s.join_later && now - s.started >= std::chrono::duration<double>(s.later_seconds)) {
            s.join_later = false;
            join_now = true;
            later = s.later;
        }
        // local play's traffic from the others, in the log every 10 s while there is some
        if (now - s.traffic_logged >= std::chrono::seconds(10)) {
            s.traffic_logged = now;
            for (const auto& [mac, t] : s.traffic) {
                const Traffic& before = s.traffic_at_log[mac];
                if (t.beacons == before.beacons && t.data == before.data && t.other == before.other &&
                    t.clocks == before.clocks) {
                    continue;
                }
                std::string who = MacText(mac);
                for (const auto& m : s.members) {
                    if (m.mac_address == mac) {
                        who = m.nickname + " " + who;
                    }
                }
                traffic_line += fmt::format("{}{}: {} beacons, {} data, {} other frames, {} net sync",
                                            traffic_line.empty() ? "" : "; ", who, t.beacons - before.beacons,
                                            t.data - before.data, t.other - before.other,
                                            t.clocks - before.clocks);
            }
            s.traffic_at_log = s.traffic;
        }
    }
    for (auto& action : actions) {
        action();
    }
    if (!traffic_line.empty()) {
        LOG_INFO(Network, "room traffic in the last 10 s from {}", traffic_line);
    }
    if (join_now) {
        LOG_INFO(Network, "joining {}:{} from the game (--join-after)", later.host, later.port);
        if (!Join(later)) {
            LOG_WARNING(Network, "--join-after: another room job runs");
        }
    }
}

Status GetStatus() {
    auto& s = S();
    std::scoped_lock lock{s.mutex};
    Status status = s.status;
    if (status.phase == Phase::InRoom) {
        const auto now = Clock::now();
        for (const auto& m : s.members) {
            Member member;
            member.nickname = m.nickname;
            member.username = m.username;
            member.game = m.game_info.name;
            member.you = m.nickname == status.nickname;
            if (const auto it = s.traffic.find(m.mac_address);
                it != s.traffic.end() && it->second.last != Clock::time_point{}) {
                member.heard = std::chrono::duration<double>(now - it->second.last).count();
            }
            status.members.push_back(std::move(member));
        }
    }
    return status;
}

} // namespace App::Multiplayer
