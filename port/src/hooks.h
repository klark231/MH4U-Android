// Native code around game functions: the port's hooks on the hook points that the code
// library was recompiled with (config/hooks.txt).
#pragma once

#include <string>
#include <vector>

#include "core/frontend/top_screen.h"

struct RCodeInfo;

namespace Port::Hooks {

/// Installs the port's hooks on a freshly loaded code library. Options come from
/// "mh4u.ini" in Azahar's user directory (`key = value` lines: fps, widescreen,
/// trace_frames), and environment variables override them (MH4U_FPS, MH4U_WIDESCREEN,
/// MH4U_TRACE_FRAMES). Returns lines for the log: options in effect and hooks the library
/// has no hook point for (it was built with an older hooks.txt).
std::vector<std::string> Install(const RCodeInfo& info);

/// Changes one of the options above while the game runs (the runner's --set, for tests: one
/// player of a session at another frame rate once everyone is in, say; the app's in-game
/// menu). Only options that the hooks read as the game runs can change: fps, refresh_rate,
/// dither, render_flags_set, render_flags_clear, kick, target, skip, skip_panel, skip_hint,
/// touch_overlay, quest_overlay, target_overlay (these redo the screen layout at once),
/// hud_edges, the screen filters (bloom, motion_blur, depth_of_field, radial_blur, haze) and
/// the trace_* options. Call it on the emulation thread. Returns false for any other key.
bool SetOption(const std::string& key, const std::string& value);

/// Whether the game offers to skip the wait after a quest is complete (the 20 seconds before
/// the return, while the touch screen's kick button reads "Skip"): worked out once a frame on
/// the emulation thread, safe to read from any thread.
bool SkipOffered();

/// The 3DS button that skips (mh4u.ini's skip: "ZR", ...), or "" when none.
std::string SkipButton();

/// The 3DS buttons held together to end the wait after a quest (mh4u.ini's skip_end:
/// "ZL+ZR", ...), or "" when none.
std::string SkipEndButtons();

/// How much of the hold of those buttons is done, 0..1 (1 once the player has asked).
float EndSkipHold();

/// Whether this player has asked to end the wait (the hold done); the wait ends once every
/// port console in the session has (NetSync::EndSkipPending names those that haven't).
bool EndSkipRequested();

/// mh4u.ini's skip_hint: 0 no hint on the screen while the skip is offered, 1 a hint, 2 a
/// flashing one.
int SkipHint();

/// In quests, switches the touch overlay between the map alone (quest_overlay) and the whole
/// touch screen (for the chat or the item shortcuts), until switched back. Safe to call from
/// any thread; it takes effect a moment later.
void ToggleQuestMap();

/// How many of the port's hook points the code library loaded last didn't have (it was built
/// with an older config/hooks.txt, so the options behind them don't work until it is rebuilt).
int MissingHookPoints();

/// The touch_overlay option: "top-right 30 85" (corner, height in % of the window's, opacity
/// in %), "on" (top right, 30%, 85%) or "off".
Layout::ScreenOverlay ParseOverlay(std::string value);

/// The quest's touch panels: six slots of 160x68 touch-screen pixels under a 36-pixel bar, in
/// two columns of three. The map takes two slots one above the other, the target camera one.
constexpr int PANEL_SLOT_WIDTH = 160, PANEL_SLOT_HEIGHT = 68, PANEL_SLOTS_TOP = 36;

/// A touch panel shown on its own over the game in a quest (the map, the target camera):
/// whether it is, its height as a fraction of the window's (0: at the touch overlay's scale)
/// and, when `placed`, where: x and y from 0 (its left or top edge at the window's) to 1 (its
/// right or bottom edge at the window's), fractions of the room the window leaves beside and
/// above it, so that it stays in the window at any size. Not placed, the map goes in the touch
/// overlay's corner and the target camera right under the map (over it if there is no room).
struct PanelView {
    bool on = false;
    float height = 0.0f;
    bool placed = false;
    float x = 1.0f, y = 0.0f;
};

/// The quest_overlay option: "map" (while a quest's touch panels are up, the touch overlay
/// shows the map panel alone, at its own scale), "map 40" (the map 40% of the window's
/// height), "map 40 at 100 0" (and at x = 100%, y = 0%: the top-right corner) or "off".
PanelView ParseQuestOverlay(std::string value);
/// The target_overlay option: "on" (with the map, the target camera panel in a view of its
/// own beside it), "on 12" (12% of the window's height), "on 12 at 100 20" or "off".
PanelView ParseTargetOverlay(std::string value);
/// The options' text for these ("map 40 at 100 0", "on", "off"), numbers to 0.1%.
std::string FormatQuestOverlay(const PanelView& map);
std::string FormatTargetOverlay(const PanelView& target);

/// Where a panel's view goes in the end: its height (a fraction of the window's) and its place
/// (as PanelView's x and y).
struct Placement {
    float height = 0.0f, x = 1.0f, y = 0.0f;
};
Placement MapPlacement(const Layout::ScreenOverlay& touch, const PanelView& map);
Placement TargetPlacement(const Layout::ScreenOverlay& touch, const PanelView& map,
                          const PanelView& target);

/// The touch overlay `touch` cut down to the quest's map in touch-panel slot `map_slot` (0-2
/// the left column from the top, 3-5 the right one), placed as `map` says, and with
/// `target_slot` >= 0 the target camera panel in that slot as a further view placed as
/// `target` says.
Layout::ScreenOverlay QuestOverlay(const Layout::ScreenOverlay& touch, int map_slot, const PanelView& map,
                                   int target_slot = -1, const PanelView& target = {});

/// The app's layout editor: while on, the touch overlay shows the map and the target camera
/// as in a quest (in the slots a quest last had them, else the default panel layout's), even
/// outside quests. Call it on the emulation thread.
void SetLayoutEditing(bool on);

/// What the touch overlay shows in a quest (quest_overlay), worked out once a frame. The map
/// alone while the quest's small touch panels are up; the whole touch screen while the quest
/// menu or a big panel is on it (both take taps), when the panels have no map, and once the
/// panels have been gone from the touch screen for a while (the quest is over). Through
/// shorter breaks, a change of area or a cutscene, and while someone talks (the panels stay
/// on the touch screen but don't run), it keeps what it shows, so that the corner doesn't jump
/// between the two. A change shows once it has lasted a moment. Times are emulated seconds,
/// so the frame rate makes no difference.
class QuestOverlayChoice {
public:
    static constexpr double SETTLE = 0.1;  // a change shows once it has lasted this long
    static constexpr double HOLD = 5.0;    // without the panels this long: out of the quest
    static constexpr int FREE = -2;        // `forced`: no slot forced

    /// One frame at `time`: `panels` whether the small panels ran, with the map in `map_slot`
    /// (-1 none); `map_drawn` whether the map panel was drawn on the touch screen all the same;
    /// `touch_ui` whether the quest menu or a big panel was up. `forced` overrides the choice
    /// (-1: the whole touch screen). Returns true when Shown() changes.
    bool Update(double time, bool panels, int map_slot, bool map_drawn, bool touch_ui, int forced = FREE);

    /// The map's slot the overlay shows, or -1 for the whole touch screen.
    int Shown() const {
        return shown;
    }

private:
    int shown = -1;
    int next = -1;           // what the frames since `next_since` asked for
    double next_since = 0;
    bool panels_seen = false;
    double panels_last = 0;  // when the small panels last ran or were drawn
};

} // namespace Port::Hooks
