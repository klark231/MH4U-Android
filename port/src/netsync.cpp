// Network clock sync for local wireless play over Azahar's network rooms.
//
// MH4U drops a player whose console is silent for about a second. On a 3DS that is a console
// out of range; on a PC it is also one that stalls (shader compilation, a slow disk): its
// emulated clock stands still while the others' go on. Here each port console tells the
// room its emulated clock several times a second, in a Wi-Fi packet of a type that no 3DS
// frame uses (room servers pass Wi-Fi packets on without looking at them, and Azahar's
// local wireless service ignores types it doesn't know). A console that has run more than
// the slack (200 ms) ahead of a peer it plays with waits for that peer. So when one PC stalls, the
// others pause with it after a moment instead of dropping it; and a PC that can't keep full
// speed sets the pace. A peer that has said nothing for kPeerTimeoutS is left to the game.
// Each console counts its lead over a peer from the moment it saw the peer's game data, so two
// consoles can disagree a little. If each thinks it is ahead of the other, both would wait
// for ever: the reports say whom a console waits for, and two that wait for each other stop.
// Longer circles, if they ever happen, end after kMaxWaitS.
#include "netsync.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstring>
#include <map>
#include <mutex>
#include <thread>
#include <fmt/format.h>

#include "common/logging/log.h"
#include "core/core.h"
#include "core/core_timing.h"
#include "network/network.h"
#include "network/room_member.h"

namespace Port::NetSync {

namespace {

using Clock = std::chrono::steady_clock;
using Network::MacAddress;
using Network::WifiPacket;

constexpr auto kPacketType = static_cast<WifiPacket::PacketType>(0x4d); // no 3DS frame's type
constexpr u32 kMagic = 0x434e5953;     // "SYNC"
constexpr u32 kVersion = 2;         // 2: the end-skip vote in the flags byte (1: no flags)
constexpr u8 kFlagEndSkip = 0x01;   // flags: this console's player asks to end the wait after a quest
constexpr double kSendEveryS = 0.05;   // how often a console tells its clock
constexpr double kSessionMs = 3000.0;  // game data from a peer within this much of our emulated
                                       // time: we play together (emulated, not host time, so
                                       // that it doesn't run out while everyone waits)
constexpr double kPeerTimeoutS = 6.0;  // a peer silent this long is left to the game (one
                                       // paused in Azahar, say, or gone)
constexpr double kMaxWaitS = 8.0;      // then the leads are counted again from zero
constexpr double kTicksPerMs = 268111.856;
constexpr double kVoteLingerS = 2.0;   // a vote to end the wait is still told this long after it
                                       // is withdrawn: the wait ends (and the vote with it) the
                                       // moment the last console asks, and the others must still
                                       // hear that this one had asked

struct Peer {
    double clock_ms = 0.0;      // its last report
    MacAddress waiting_for{};   // ... and whom it was waiting for (zero: nobody)
    Clock::time_point heard{};  // when that arrived
    u64 data_packets = 0;       // game data packets from it so far
    u64 data_seen = 0;          // ... of which the emulation thread has noticed
    double data_at_ms = -1e18;  // our emulated clock when it noticed the last one
    bool reported = false;
    bool syncing = false;
    u32 version = 0;            // of its reports: 1 can't vote on the end skip
    bool end_skip = false;      // its player asks to end the wait after a quest
    Lead lead;
};

struct State {
    Mode mode = Mode::Off;
    double slack_ms = kDefaultSlackMs;
    std::mutex mutex;
    std::map<MacAddress, Peer> peers;
    Network::RoomMember::CallbackHandle<WifiPacket> callback;
    std::weak_ptr<Network::RoomMember> bound;
    MacAddress self{};  // this console's address in the room
    Clock::time_point last_send{};
    bool end_skip = false;  // this console's player asks to end the wait after a quest
    Clock::time_point end_skip_until{};  // ... or did, until then (kVoteLingerS)
};

State& GetState() {
    static State state;
    return state;
}

std::string MacText(const MacAddress& mac) {
    return fmt::format("{:02X}:{:02X}:{:02X}:{:02X}:{:02X}:{:02X}", mac[0], mac[1], mac[2], mac[3], mac[4],
                       mac[5]);
}

/// On the room's network thread: clocks from other port consoles, and which consoles send
/// game data to this one (to it alone: broadcasts reach every console in the room, also
/// those of other groups playing in it).
void OnWifiPacket(const WifiPacket& packet) {
    auto& state = GetState();
    const auto now = Clock::now();
    if (packet.type == kPacketType) {
        u32 magic = 0, version = 0;
        double clock_ms = 0.0;
        MacAddress waiting_for{};
        if (packet.data.size() < 24) {
            return;
        }
        std::memcpy(&magic, packet.data.data(), 4);
        std::memcpy(&version, packet.data.data() + 4, 4);
        std::memcpy(&clock_ms, packet.data.data() + 8, 8);
        std::memcpy(waiting_for.data(), packet.data.data() + 16, waiting_for.size());
        if (magic != kMagic || version < 1 || version > kVersion || !std::isfinite(clock_ms)) {
            return;
        }
        const u8 flags = version >= 2 ? packet.data[22] : 0;
        std::scoped_lock lock{state.mutex};
        auto& peer = state.peers[packet.transmitter_address];
        peer.clock_ms = clock_ms;
        peer.waiting_for = waiting_for;
        peer.heard = now;
        peer.reported = true;
        peer.version = version;
        peer.end_skip = (flags & kFlagEndSkip) != 0;
    } else if (packet.type == WifiPacket::PacketType::Data) {
        std::scoped_lock lock{state.mutex};
        if (packet.destination_address == state.self) {
            ++state.peers[packet.transmitter_address].data_packets;
        }
    }
}

/// Packet: magic, version (u32 each), the emulated clock in ms (f64), the MAC of the peer this
/// console is waiting for (zeros: none), the flags byte (kFlagEndSkip), a byte of padding.
void SendClock(Network::RoomMember& member, double own_ms, const MacAddress& waiting_for, u8 flags) {
    WifiPacket packet{};
    packet.type = kPacketType;
    packet.channel = 0;
    packet.transmitter_address = member.GetMacAddress();
    packet.destination_address = Network::BroadcastMac;
    packet.data.resize(24);
    std::memcpy(packet.data.data(), &kMagic, 4);
    std::memcpy(packet.data.data() + 4, &kVersion, 4);
    std::memcpy(packet.data.data() + 8, &own_ms, 8);
    std::memcpy(packet.data.data() + 16, waiting_for.data(), waiting_for.size());
    packet.data[22] = flags;
    member.SendWifiPacket(packet);
}

/// The peer we are furthest ahead of, and by how much (0 when none). A peer that waits for us
/// isn't waited for.
std::pair<MacAddress, double> Ahead(State& state, const MacAddress& self, double own_ms,
                                    Clock::time_point now, bool restart) {
    std::pair<MacAddress, double> worst{MacAddress{}, 0.0};
    std::scoped_lock lock{state.mutex};
    for (auto& [mac, peer] : state.peers) {
        if (peer.data_seen != peer.data_packets) {
            peer.data_seen = peer.data_packets;
            peer.data_at_ms = own_ms;
        }
        const bool alive = peer.reported && now - peer.heard < std::chrono::duration<double>(kPeerTimeoutS);
        const bool together = state.mode == Mode::All || own_ms - peer.data_at_ms < kSessionMs;
        if (!alive || !together) {
            if (peer.syncing) {
                LOG_INFO(Frontend, "net sync: no longer with {} ({}) at {:.0f} ms", MacText(mac),
                         alive ? "no game data" : "no clock", own_ms);
            }
            peer.syncing = false;
            continue;
        }
        if (!peer.syncing || restart) {
            if (!peer.syncing) {
                LOG_INFO(Frontend, "net sync: with {} from {:.0f} ms", MacText(mac), own_ms);
            }
            peer.lead.Start(own_ms, peer.clock_ms);
            peer.syncing = true;
            continue;
        }
        peer.lead.Update(own_ms, peer.clock_ms);
        if (peer.waiting_for == self) {
            peer.lead.Limit(state.slack_ms);
        }
        if (peer.lead.Value() > worst.second) {
            worst = {mac, peer.lead.Value()};
        }
    }
    return worst;
}

/// At the start of each RunLoop, on the emulation thread.
void BeforeRunLoop(Core::System& system) {
    auto& state = GetState();
    const auto member = Network::GetRoomMember().lock();
    if (!member || !member->IsConnected()) {
        return;
    }
    if (state.bound.lock() != member) {
        // the room member exists from Network::Init on; bind once it does
        if (auto old = state.bound.lock(); old && state.callback) {
            old->Unbind(state.callback);
        }
        state.callback = member->BindOnWifiPacketReceived(OnWifiPacket);
        state.bound = member;
    }
    const auto own_ms = [&system] {
        return static_cast<double>(system.CoreTiming().GetGlobalTicks()) / kTicksPerMs;
    };
    const MacAddress self = member->GetMacAddress();  // (a console that joins again may get another)
    {
        std::scoped_lock lock{state.mutex};
        state.self = self;
    }
    const auto send = [&](Clock::time_point now, const MacAddress& waiting_for) {
        if (now - state.last_send >= std::chrono::duration<double>(kSendEveryS)) {
            bool end_skip = false;
            {
                std::scoped_lock lock{state.mutex};
                end_skip = state.end_skip || now < state.end_skip_until;
            }
            SendClock(*member, own_ms(), waiting_for, end_skip ? kFlagEndSkip : 0);
            state.last_send = now;
        }
    };
    auto now = Clock::now();
    send(now, MacAddress{});
    auto [mac, lead] = Ahead(state, self, own_ms(), now, false);
    if (lead <= state.slack_ms) {
        return;
    }
    const auto waited_from = now;
    const MacAddress waited_for = mac;
    const double waited_at = own_ms();
    while (lead > state.slack_ms && member->IsConnected()) {
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
        now = Clock::now();
        send(now, mac);
        const bool restart = now - waited_from > std::chrono::duration<double>(kMaxWaitS);
        std::tie(mac, lead) = Ahead(state, self, own_ms(), now, restart);
    }
    const double waited = std::chrono::duration<double, std::milli>(Clock::now() - waited_from).count();
    if (waited >= 100.0) {
        LOG_INFO(Frontend, "net sync: waited {:.0f} ms at {:.0f} ms for {}", waited, waited_at,
                 MacText(waited_for));
    }
}

/// The port consoles this one plays with that haven't asked to end the wait (those that can
/// vote: version 2 reports). Under the lock.
std::vector<MacAddress> PendingPeers(State& state) {
    std::vector<MacAddress> pending;
    for (const auto& [mac, peer] : state.peers) {
        if (peer.syncing && peer.version >= 2 && !peer.end_skip) {
            pending.push_back(mac);
        }
    }
    return pending;
}

} // namespace

void SetEndSkipVote(bool vote) {
    auto& state = GetState();
    std::scoped_lock lock{state.mutex};
    if (state.end_skip != vote) {
        LOG_INFO(Frontend, "net sync: {} to end the wait after the quest", vote ? "asking" : "no longer asking");
        if (!vote) {
            state.end_skip_until = Clock::now() + std::chrono::duration_cast<Clock::duration>(
                                                      std::chrono::duration<double>(kVoteLingerS));
        }
    }
    state.end_skip = vote;
}

bool EndSkipAgreed() {
    auto& state = GetState();
    std::scoped_lock lock{state.mutex};
    return state.end_skip && PendingPeers(state).empty();
}

std::vector<std::string> EndSkipPending() {
    auto& state = GetState();
    std::vector<MacAddress> pending;
    {
        std::scoped_lock lock{state.mutex};
        pending = PendingPeers(state);
    }
    std::vector<std::string> names;
    const auto member = Network::GetRoomMember().lock();
    const auto members = member && member->IsConnected() ? member->GetMemberInformation()
                                                         : Network::RoomMember::MemberList{};
    for (const MacAddress& mac : pending) {
        std::string name = MacText(mac);
        for (const auto& info : members) {
            if (info.mac_address == mac && !info.nickname.empty()) {
                name = info.nickname;
                break;
            }
        }
        names.push_back(name);
    }
    return names;
}

Mode ParseMode(const std::string& value) {
    if (value == "0" || value == "off" || value == "false" || value == "no") {
        return Mode::Off;
    }
    if (value == "all") {
        return Mode::All;
    }
    return Mode::Session;
}

void Install(Core::System& system, Mode mode, double slack_ms, std::vector<std::string>& log) {
    auto& state = GetState();
    {
        std::scoped_lock lock{state.mutex};
        state.mode = mode;
        state.slack_ms = slack_ms > 0.0 ? slack_ms : kDefaultSlackMs;
        state.peers.clear();
        state.end_skip = false;
        state.end_skip_until = {};
    }
    if (mode == Mode::Off) {
        system.SetRunLoopHook(nullptr);
        log.push_back("net sync off");
        return;
    }
    system.SetRunLoopHook([&system] { BeforeRunLoop(system); });
    if (mode == Mode::All) {
        log.push_back("net sync with every port console in the room");
    }
    if (slack_ms > 0.0 && slack_ms != kDefaultSlackMs) {
        log.push_back(fmt::format("net sync slack {:.0f} ms", slack_ms));
    }
}

} // namespace Port::NetSync
