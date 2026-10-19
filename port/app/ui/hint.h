// Lines over the game while the in-game menu is closed, once a quest is complete
// (port/src/hooks.h): which buttons tap the touch screen's Skip and which, held, end the
// wait; the hold's progress; and, online, who hasn't asked to end it yet.
#pragma once

#include <string>
#include <vector>

namespace App {

struct Config;

namespace Ui {

struct Hint {
    std::string skip;      ///< the keys / controller buttons that skip ("C / RT"), or ""
    std::string end;       ///< those held to end the wait ("Q+C / LT+RT"), or ""
    float hold = 0.0f;     ///< 0..1 of the hold, 1 once asked
    bool requested = false;          ///< this player asked to end the wait
    std::vector<std::string> waiting_for;  ///< online: port players who haven't asked yet
    bool Empty() const {
        return skip.empty() && end.empty();
    }
};

/// What to show now: nothing (Empty()) when the skip isn't offered, no button is bound, or
/// mh4u.ini's skip_hint is off. The key names come from `config` (mh4u-app.ini) and the
/// controller mapping. Cheap when there is nothing.
Hint CurrentHint(const Config& config);

/// Draws the hint at the bottom middle of the screen, in a Dear ImGui frame (the menu's
/// context): small and translucent, it takes no input. `flash`: its frame pulses.
void DrawHint(const Hint& hint, bool flash);

} // namespace Ui
} // namespace App
