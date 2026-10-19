// Keeps the emulated clocks of the port's consoles in a network room together, so that one
// whose PC stalls isn't dropped by the others (see netsync.cpp).
#pragma once

#include <string>
#include <vector>

#include "common/common_types.h"

namespace Core {
class System;
}

namespace Port::NetSync {

enum class Mode {
    Off,
    Session, ///< with the port consoles this one plays local wireless with (game data in the
             ///< last few seconds): the default
    All,     ///< with every port console in the room (for tests without local wireless)
};

/// "on"/"session", "all" or "off" (anything else: the default).
Mode ParseMode(const std::string& value);

/// Starts (or, for Off, stops) keeping the clocks together: a hook at the start of each
/// Core::System::RunLoop. `slack_ms`: how far ahead of a peer this console may get before it
/// waits for it, in emulated ms. Lines for the log go to `log`.
void Install(Core::System& system, Mode mode, double slack_ms, std::vector<std::string>& log);

constexpr double kDefaultSlackMs = 200.0;

// ── The skip of the wait after a quest, agreed by everyone (hooks.cpp, the skip_end option).
// Each port console tells the room whether its player asks to end the wait, in the same
// packets as its clock; the wait is cut only once every port console this one plays with
// asks too. Consoles on official Azahar, and ports from before this, can't ask and aren't
// waited for.

/// Whether this console's player asks to end the wait (set by the hooks; false once the
/// wait is over).
void SetEndSkipVote(bool vote);

/// Whether every port console this one plays with (and this one) asks to end the wait.
/// True with no room, no peers, or peers that can't vote.
bool EndSkipAgreed();

/// The nicknames (the room's; a MAC when unknown) of the port consoles this one plays with
/// that haven't asked yet, for the screen.
std::vector<std::string> EndSkipPending();

/// How far this console's emulated clock has run ahead of a peer's since they began to play
/// together, from our clock and the peer's last report (emulated ms; the clocks' origins
/// don't matter, only how they advance).
class Lead {
public:
    void Start(double own_ms, double peer_ms) {
        lead = 0.0;
        last_own = own_ms;
        last_peer = peer_ms;
    }
    void Update(double own_ms, double peer_ms) {
        lead += (own_ms - last_own) - (peer_ms - last_peer);
        last_own = own_ms;
        last_peer = peer_ms;
    }
    /// Caps the lead (two consoles that each think they are ahead would wait for each other).
    void Limit(double max_ms) {
        lead = lead > max_ms ? max_ms : lead;
    }
    double Value() const {
        return lead;
    }

private:
    double lead = 0.0;
    double last_own = 0.0, last_peer = 0.0;
};

} // namespace Port::NetSync
