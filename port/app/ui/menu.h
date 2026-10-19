// The in-game menu's window and tabs: Game (mh4u.ini, the port's hooks), Display, Audio and
// Controls (mh4u-app.ini), Multiplayer (the network room) and Quit. A change applies at once
// where the game or Azahar can take it while running, otherwise from the next start (each such
// setting says so); the files are written when the menu closes.
#pragma once

#include <map>
#include <memory>
#include <set>
#include <string>

namespace App::Ui {

class Layer;

/// What a controller does for an input of the input profile (Settings' parameters: "RT",
/// "A (bottom)", ...), or "" when no controller does.
std::string ControllerInputName(const std::string& params);

class Menu {
public:
    explicit Menu(Layer& layer);
    ~Menu();

    Menu(const Menu&) = delete;
    Menu& operator=(const Menu&) = delete;

    /// The menu came up: the settings files are read again.
    void Opened();
    /// It went away: what changed is written.
    void Closed();
    /// In a frame.
    void Draw();
    /// Controls: the key pressed while a binding waits for one (Esc keeps the old one, Delete
    /// clears it).
    void OnCapturedKey(int scancode);

    enum class AppSetting { Fullscreen, Screens, Resolution, LinearFilter, Vsync, Renderer, Volume, Output, Controls, FpsCounter, CpuClock };

    /// The layout editor is up in place of the menu's window: frames over the game for the
    /// quest's map and target camera views, moved and resized with the mouse.
    bool EditingLayout() const {
        return layout_edit;
    }
    /// Esc or a controller's B in the editor: what it changed is undone and the menu comes back.
    void CancelLayoutEdit();

private:
    void DrawLayoutEditor();
    void StartLayoutEdit();
    /// The editor goes; the menu comes back unless it is closing.
    void EndLayoutEdit();
    /// Touch screen: a quest panel's view (quest_overlay, target_overlay) as sliders and lists.
    void DrawPanelViewRows(const char* key, const char* name);
    void DrawGame();
    void DrawDisplay();
    void DrawAudio();
    void DrawControls();
    void DrawMultiplayer();
    void DrawQuit();

    /// mh4u.ini: a key's value in effect (the menu's change, the file's, or the hooks' default).
    std::string Game(const std::string& key) const;
    /// Changes it: at once where the hooks take it while the game runs, and in the file later.
    void SetGame(const std::string& key, const std::string& value);
    void Changed(AppSetting setting);
    /// The controls changed: the input profile is built again from them.
    void ControlsChanged();

    Layer& layer;
    int tab = 0;
    int select_tab = -1;
    bool just_opened = false;
    std::map<std::string, std::string> game_file;    ///< mh4u.ini when the menu came up
    std::map<std::string, std::string> game_changes; ///< written when it goes
    std::set<AppSetting> app_changes;
    int capture = -1;          ///< Controls: the row waiting for a key
    std::string capture_note;  ///< why the last key wasn't taken

    // the layout editor
    bool layout_edit = false;
    /// quest_overlay's and target_overlay's entries in game_changes before it (empty: none)
    std::map<std::string, std::string> layout_before;
    std::set<std::string> layout_before_missing;
    int drag = -1;             ///< the view being dragged: 0 the map, 1 the target camera
    bool drag_resize = false;  ///< by its corner
    float drag_mouse_x = 0, drag_mouse_y = 0, drag_left = 0, drag_top = 0, drag_height = 0;
};

} // namespace App::Ui
