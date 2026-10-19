// The in-game menu's Multiplayer tab: the room you're in and who is in it, joining another one
// (an invite, an address, a lobby's list, the last room), hosting one with an invite to share,
// and leaving, all while the game runs (multiplayer.h does the work).
#pragma once

namespace App::Ui {

class Layer;

/// In the menu's frame, on the Multiplayer tab.
void DrawMultiplayerTab(Layer& layer);

} // namespace App::Ui
