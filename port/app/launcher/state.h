// The launcher's state, shared by its screens: the settings being edited, what the setup checks
// found, the jobs and commands running, the mod library, the lobby's rooms and the dialogs.
#pragma once

#include <deque>
#include <functional>
#include <future>
#include <memory>
#include <optional>
#include <string>
#include <vector>

#include "config.h"
#include "launcher/checks.h"
#include "launcher/launcher.h"
#include "launcher/lobby.h"
#include "launcher/pick.h"
#include "launcher/process.h"
#include "launcher/tools.h"
#include "launcher/ui.h"

namespace Launcher {

enum class Screen { Home, Setup, Mods, Online, Settings };
inline constexpr int SCREEN_COUNT = 5;
const char* ScreenName(Screen screen);

/// Short commands in child processes (mods.py, the code check), one at a time, their results
/// handed to the UI thread.
class Runner {
public:
    using Done = std::function<void(int exit_code, const std::string& output)>;
    void Queue(std::vector<std::string> args, std::string what, Done done);
    /// Each frame: finishes the current command (its callback runs here) and starts the next.
    void Pump();
    bool Busy() const {
        return process != nullptr || !queue.empty();
    }
    /// What runs now, for a status line ("" when idle).
    const std::string& What() const {
        return current_what;
    }

private:
    struct Item {
        std::vector<std::string> args;
        std::string what;
        Done done;
    };
    std::deque<Item> queue;
    std::unique_ptr<Process> process;
    Done current_done;
    std::string current_what;
};

/// A long job the player waits for in a window of its own: building the code library (a
/// child process, with its output and a Cancel button), installing the update, deploying mods,
/// downloading and installing a mod or texture pack (mods.py's progress lines).
struct Job {
    enum class Kind { None, Build, Install, Command, Download };
    Kind kind = Kind::None;
    std::string title;
    std::unique_ptr<Process> process;
    std::unique_ptr<Checks::Install> install;
    bool finished = false;
    bool ok = false;
    bool cancellable = false;
    bool close_when_done = false;    ///< no result to read: the window closes by itself
    std::string summary;             ///< what happened, when finished
    std::vector<std::string> lines;  ///< the output so far
    std::size_t output_size = 0;
    bool follow = true;              ///< the output view scrolls with new lines
    bool focus_set = false;          ///< a button that just appeared got the focus
    bool quit_asked = false;         ///< the player closed the window during the job
    bool cancel_asked = false;       ///< the player pressed Cancel: asked whether to stop it
    bool quit_after = false;         ///< ... and chose to stop it and quit
    /// What Cancel asks, and its two answers (a build's, when empty)
    std::string stop_question, keep_label, stop_label;
    std::function<void(int exit_code, const std::string& output)> on_done;
    bool Active() const {
        return kind != Kind::None;
    }
};

/// A message or question in a modal window.
struct Dialog {
    struct Button {
        std::string label;
        std::function<void()> action;  ///< null: just closes
        bool accent = false;
    };
    std::string title;
    std::string text;
    std::vector<std::string> details;  ///< a list under the text (files, problems)
    std::vector<Button> buttons;
    bool input = false;                ///< a line to type into (a path, a password)
    bool password = false;
    std::string input_hint;
    std::string value;
    std::function<void(const std::string&)> on_input;  ///< with the button marked accent
};

struct ModInfo {
    std::string id, name, version, author, description, url;
    bool enabled = false;
    bool is_new = false;
    int position = 0;
    int files = 0;
    int wins = 0;
    int loses = 0;
    bool code = false;
    int changes_code = -1;  ///< a code mod: 1 changes the game's code, 0 only data, -1 unknown
    bool adds_code = false; ///< a code mod that adds code after the game's own (a "code cave")
    std::vector<std::string> warnings, errors;
};

/// A mod or texture pack of the catalog (tools/mods.py catalog --json).
struct CatalogEntry {
    std::string id, kind, name, author, version, description, details, category, region, region_note,
        license, page_url, note;
    bool recommended = false;
    bool direct = false;     ///< installed from here (else from its page)
    bool installed = false;
    bool update = false;     ///< installed, in another version than the catalog's
    bool enabled = false;    ///< a mod: on in the library
    std::string installed_as, installed_id, installed_version;
    std::vector<std::string> needs, conflicts;
    long long size = -1, installed_size = -1;  ///< -1: not known
    long long downloaded = 0;                  ///< already in the downloads folder (to go on with)
};

/// A texture pack installed by the mod manager (from the catalog or not).
struct TexturePack {
    std::string id, name, version, catalog;
    int files = 0;
    long long size = -1;
};

struct ModCatalog {
    bool loaded = false;
    bool fetched = false;    ///< asked the web for a newer one this session
    std::string error;
    std::string source, updated;
    std::string textures_folder, downloads_folder;
    std::vector<std::string> warnings;
    std::vector<CatalogEntry> entries;
    std::vector<TexturePack> textures;
    std::string selected;    ///< the entry whose details show
};

struct Conflict {
    std::string path;
    std::string winner;
    std::vector<std::string> mods;
};

struct ModLibrary {
    bool loaded = false;
    std::string error;                  ///< the last command's error
    std::vector<std::string> messages;  ///< what the last command did
    std::vector<ModInfo> mods;
    std::vector<Conflict> conflicts;
    std::string deploy_state;           ///< up_to_date, out_of_date, blocked
    std::vector<std::string> problems, warnings;
    std::vector<std::string> in_the_way;  ///< the player's own files where mods go
    std::string code_state;             ///< the code library: ok, rebuild, unknown, missing
    std::string code_reason;
    std::string library_path;
    std::string folder;                 ///< Azahar's mods folder for the game
    std::string selected;               ///< the mod whose details show
    bool show_catalog = false;          ///< the Mods screen shows the catalog, not the library
    ModCatalog catalog;
};

struct State {
    explicit State(App::Config& config_) : config(config_) {}

    App::Config& config;  ///< what the game will start with (command-line choices included)
    App::Config saved;    ///< mh4u-app.ini as it is: the player's changes go to both

    Screen screen = Screen::Home;
    bool focus_first = true;  ///< give the first item of the screen the focus next frame
    bool wizard = false;      ///< the setup was shown on its own: something was missing
    int setup_step = -1;      ///< the setup's step to do next (0: none), as last drawn
    bool done = false;
    Result result = Result::Quit;
    /// Play was pressed while something it needs was still being read or checked (the mod
    /// library, the code library): the stage of Play to go on with once it is there
    int play_waiting = 0;

    // the launcher's own keys and buttons, seen as SDL events (Dear ImGui's navigation would
    // take some of them first), applied between frames
    int screen_step = 0;          ///< Ctrl+Tab, Ctrl+Shift+Tab, a controller's shoulder buttons
    bool back_pressed = false;    ///< Esc, a controller's B
    bool accept_pressed = false;  ///< Enter, a controller's A
    bool cursor_was_hidden = false;
    bool text_was_active = false;
    bool modal_was_open = false;
    bool pad = false;  ///< the last button pressed was a controller's: its hints, not the keyboard's

    // the setup
    Checks::Game game;
    Checks::Update update;
    Checks::Code code;
    bool library_exists = false;

    // the tools
    bool probing = false;
    std::future<std::pair<std::string, Tools::Python>> probe;
    std::string tools_dir;
    Tools::Python python;
    bool ToolsReady() const {
        return !probing && !tools_dir.empty() && !python.command.empty();
    }

    Runner runner;
    Job job;
    std::optional<Dialog> dialog;
    bool dialog_open = false;  ///< the popup for `dialog` is up

    /// The system's file picker while it is up (Linux, macOS: another program, waited for on a
    /// thread of its own so that the launcher keeps drawing and answering the desktop)
    struct Picking {
        std::future<Pick::Result> result;
        std::function<void(const std::string&)> then;
        bool folder = false;
        std::string title;
        bool was_fullscreen = false;
    };
    std::optional<Picking> picking;

    ModLibrary mods;

    // the room
    std::future<Lobby::List> rooms_future;
    bool rooms_loading = false;
    Lobby::List rooms;
    bool rooms_fetched = false;
    std::string room_label;  ///< the joined room's name, when it came from the lobby's list
    /// The Online screen's fields while they are edited (the settings change on Join / Host,
    /// or when a field of your name and lobby is left)
    struct OnlineForm {
        bool loaded = false;
        int tab = 0;  ///< join, host, your name and lobby
        std::string address, address_password;  ///< address: an invite (mh4u://...) or HOST:PORT
        std::string paste_note;                  ///< why the clipboard had no invite
        int port = 24872;
        std::string name, password;
        int members = 8;
        bool listed = true;
        std::string nickname, lobby, token;
    } online;
    void FetchRooms();

    /// Applies a change to the settings the game starts with and to mh4u-app.ini.
    void Change(const std::function<void(App::Config&)>& edit);

    void GoTo(Screen next);
    void Show(Dialog d);
    void Message(const std::string& title, const std::string& text);

    /// Every setup check again (the game file, the update, the code library).
    void Recheck();
    /// The code library's check (a child process), after a change that could affect it.
    void CheckCode();
    /// Looks for the tools and Python (on a worker thread).
    void ProbeTools();

    /// The game file, the update and the code library are all there (quick checks).
    bool SetupComplete() const;

    /// Builds the code library from the installed update.
    void StartBuild();
    /// Asks for the update's .cia and installs it.
    void ChooseAndInstallUpdate();
    /// Asks for the game file.
    void ChooseGame();
    /// The system's file or folder picker (or a line to type the path into), then `then`.
    void Pick(bool folder, const std::string& title, const std::string& kind,
              const std::vector<std::string>& extensions, std::function<void(const std::string&)> then);

    /// Play: deploys the mods, warns about the setup, then hands over to the game.
    void Play(int stage = 0);
};

// the screens (screen_*.cpp)
void DrawHome(State& state);
void DrawSetup(State& state);
void DrawMods(State& state);
void DrawOnline(State& state);
void DrawSettings(State& state);

// the mod library (screen_mods.cpp)
void RefreshMods(State& state);
/// The catalog and which of it is installed; `fetch`: also a newer catalog from the web
/// (mh4u-app.ini's catalog_url), once a session.
void RefreshCatalog(State& state, bool fetch);
/// then (if any) runs when the player goes on to play; without_code says they already agreed to
/// play without the port's code (asked when the code mods need the library built again).
void DeployMods(State& state, bool force, std::function<void(bool without_code)> then);

// the setup's three parts as status lines (screen_setup.cpp)
struct SetupRow {
    Ui::Status status;
    std::string label;
    std::string detail;
};
SetupRow GameRow(const State& state);
SetupRow UpdateRow(const State& state);
SetupRow CodeRow(const State& state);

// small helpers shared by the screens (launcher.cpp)
std::string AppVersion();
std::string FileName(const std::string& path);
/// Gives the next item the focus when the screen was just entered (call before the screen's
/// first item; `enabled` false when it will be disabled). Not for a text field: Dear ImGui
/// would start editing it.
void FocusNext(State& state, bool enabled = true);
/// The room the game will join or host, for the Home screen.
std::string RoomSummary(const State& state);

} // namespace Launcher
