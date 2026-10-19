#include "ui/menu.h"

#include <algorithm>
#include <cfloat>
#include <cmath>
#include <cstdlib>
#include <iterator>
#include <mutex>
#include <tuple>
#include <vector>
#include <SDL.h>
#include <fmt/format.h>
#include <imgui.h>

#include "common/file_util.h"
#include "common/logging/log.h"
#include "common/param_package.h"
#include "common/settings.h"
#include "common/string_util.h"
#include "config.h"
#include "core/core.h"
#include "core/core_timing.h"
#include "frontend.h"
#include "hooks.h"
#include "input.h"
#include "ui/fps_counter.h"
#include "ui/layer.h"
#include "ui/multiplayer_tab.h"
#include "ui/options.h"
#include "video_core/gpu.h"
#include "video_core/renderer_base.h"
#include "window.h"

namespace App::Ui {

namespace {

namespace NativeAnalog = Settings::NativeAnalog;
namespace NativeButton = Settings::NativeButton;

constexpr const char* TABS[] = {"Game", "Display", "Audio", "Controls", "Multiplayer", "Quit"};
constexpr int TAB_COUNT = static_cast<int>(std::size(TABS));

// mh4u.ini: what the hooks take when a key has no line (port/src/hooks.cpp, Options), and
// the keys they take while the game runs (Port::Hooks::SetOption)
const std::map<std::string, std::string> GAME_DEFAULTS = {
    {"fps", "0"},           {"refresh_rate", "0"}, {"widescreen", "off"}, {"wide_render", "0"},
    {"hud_edges", "1"},     {"touch_overlay", "off"}, {"quest_overlay", "map"}, {"kick", "off"},
    {"target", "off"},      {"dither", "on"},      {"stereo_3d", "off"},  {"antialiasing", "on"}, {"bloom", "on"},
    {"depth_of_field", "on"},
    {"motion_blur", "on"},  {"radial_blur", "on"}, {"haze", "on"},        {"net_sync", "on"},
    {"target_overlay", "on"}, {"skip", "ZR"}, {"skip_hint", "on"}, {"skip_end", "ZL+ZR"},
    {"skip_end_hold", "1.0"}};
const std::set<std::string> GAME_LIVE = {"fps",           "refresh_rate", "hud_edges", "touch_overlay",
                                         "quest_overlay", "target_overlay", "kick",    "target",
                                         "skip",          "skip_hint",    "skip_end", "skip_end_hold",
                                         "dither",
                                         "stereo_3d",     "antialiasing", "bloom",         "depth_of_field", "motion_blur",
                                         "radial_blur",   "haze"};
constexpr const char* PANEL_VIEW_KEYS[] = {"quest_overlay", "target_overlay"};

bool IsOn(const std::string& v) {
    return v == "1" || v == "on" || v == "yes" || v == "true";
}

bool IsOff(const std::string& v) {
    return v == "0" || v == "off" || v == "no" || v == "false";
}

std::string Lower(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return std::tolower(c); });
    return s;
}

std::string Upper(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return std::toupper(c); });
    return s;
}

// ── Layout helpers: a two-column table, the setting's name on the left ──

bool BeginSettings(const char* id) {
    if (!ImGui::BeginTable(id, 2, ImGuiTableFlags_SizingStretchProp | ImGuiTableFlags_RowBg)) {
        return false;
    }
    ImGui::TableSetupColumn("name", ImGuiTableColumnFlags_WidthStretch, 0.46f);
    ImGui::TableSetupColumn("value", ImGuiTableColumnFlags_WidthStretch, 0.54f);
    return true;
}

/// A setting's row, ready for its control (full width); `live` false: it applies from the next
/// start, and says so.
void Row(const char* name, bool live = true) {
    ImGui::TableNextRow();
    ImGui::TableSetColumnIndex(0);
    ImGui::AlignTextToFramePadding();
    ImGui::TextUnformatted(name);
    if (!live) {
        ImGui::SameLine();
        ImGui::TextDisabled("(next start)");
    }
    ImGui::TableSetColumnIndex(1);
    ImGui::SetNextItemWidth(-FLT_MIN);
}

/// A table's header row as plain text: ImGui's own header cells are buttons, which the keyboard
/// and controller would stop at on their way to the table's controls.
void HeaderRow() {
    ImGui::TableNextRow(ImGuiTableRowFlags_Headers);
    ImGui::TableSetBgColor(ImGuiTableBgTarget_RowBg0, ImGui::GetColorU32(ImGuiCol_TableHeaderBg));
    for (int column = 0; column < ImGui::TableGetColumnCount(); ++column) {
        ImGui::TableSetColumnIndex(column);
        ImGui::TextUnformatted(ImGui::TableGetColumnName(column));
    }
}

void Section(const char* title) {
    ImGui::Spacing();
    ImGui::SeparatorText(title);
}

/// Esc or a controller's B in a question (Dear ImGui's B doesn't close those itself).
bool CancelPressed() {
    return ImGui::IsKeyPressed(ImGuiKey_Escape, false) || ImGui::IsKeyPressed(ImGuiKey_GamepadFaceRight, false);
}

/// A list to pick one of `labels` from; true when the pick changed `current`.
bool Choose(const char* id, int& current, const std::vector<std::string>& labels) {
    bool changed = false;
    const char* preview = current >= 0 && current < static_cast<int>(labels.size()) ? labels[current].c_str() : "";
    if (ImGui::BeginCombo(id, preview)) {
        for (int i = 0; i < static_cast<int>(labels.size()); ++i) {
            const bool selected = i == current;
            if (ImGui::Selectable(labels[i].c_str(), selected) && !selected) {
                current = i;
                changed = true;
            }
            if (selected) {
                ImGui::SetItemDefaultFocus();
            }
        }
        ImGui::EndCombo();
    }
    return changed;
}

/// Picks one of `values` (shown as `labels`), the current value added if it isn't one of them.
bool ChooseValue(const char* id, std::string& value, std::vector<std::string> values,
                 std::vector<std::string> labels) {
    auto it = std::find(values.begin(), values.end(), value);
    if (it == values.end()) {
        values.push_back(value);
        labels.push_back(value);
        it = values.end() - 1;
    }
    int current = static_cast<int>(it - values.begin());
    if (!Choose(id, current, labels)) {
        return false;
    }
    value = values[current];
    return true;
}

// ── Controls ──

struct Binding {
    const char* name;
    int button; ///< Settings::NativeButton, or -1 for a stick's direction
    int stick;  ///< Settings::NativeAnalog
    int part;   ///< of the stick's keys: up, down, left, right, the half-way key
};

constexpr Binding BINDINGS[] = {
    {"A", NativeButton::A, -1, 0},
    {"B", NativeButton::B, -1, 0},
    {"X", NativeButton::X, -1, 0},
    {"Y", NativeButton::Y, -1, 0},
    {"L", NativeButton::L, -1, 0},
    {"R", NativeButton::R, -1, 0},
    {"ZL", NativeButton::ZL, -1, 0},
    {"ZR", NativeButton::ZR, -1, 0},
    {"START", NativeButton::Start, -1, 0},
    {"SELECT", NativeButton::Select, -1, 0},
    {"D-pad up", NativeButton::Up, -1, 0},
    {"D-pad down", NativeButton::Down, -1, 0},
    {"D-pad left", NativeButton::Left, -1, 0},
    {"D-pad right", NativeButton::Right, -1, 0},
    {"Circle pad up", -1, NativeAnalog::CirclePad, 0},
    {"Circle pad down", -1, NativeAnalog::CirclePad, 1},
    {"Circle pad left", -1, NativeAnalog::CirclePad, 2},
    {"Circle pad right", -1, NativeAnalog::CirclePad, 3},
    {"Circle pad half way (walk)", -1, NativeAnalog::CirclePad, 4},
    {"C-stick up (camera)", -1, NativeAnalog::CStick, 0},
    {"C-stick down", -1, NativeAnalog::CStick, 1},
    {"C-stick left", -1, NativeAnalog::CStick, 2},
    {"C-stick right", -1, NativeAnalog::CStick, 3},
};
constexpr int BINDING_COUNT = static_cast<int>(std::size(BINDINGS));

/// A stick's keys, "up,down,left,right[,half way]", as five names (empty: none).
std::vector<std::string> StickKeys(const std::string& spec) {
    std::vector<std::string> keys;
    for (const std::string& part : Common::SplitString(spec, ',')) {
        keys.push_back(Common::StripSpaces(part));
    }
    keys.resize(5);
    return keys;
}

std::string JoinStickKeys(const std::vector<std::string>& keys) {
    std::string spec = fmt::format("{},{},{},{}", keys[0], keys[1], keys[2], keys[3]);
    if (keys.size() > 4 && !keys[4].empty()) {
        spec += "," + keys[4];
    }
    return spec;
}

std::string KeyOf(const Config& config, const Binding& b) {
    return b.button >= 0 ? config.keys[b.button] : StickKeys(config.stick_keys[b.stick])[b.part];
}

void SetKey(Config& config, const Binding& b, const std::string& key) {
    if (b.button >= 0) {
        config.keys[b.button] = key;
    } else {
        auto keys = StickKeys(config.stick_keys[b.stick]);
        keys[b.part] = key;
        config.stick_keys[b.stick] = JoinStickKeys(keys);
    }
}

std::string ControllerButtonName(int button) {
    switch (button) {
    case SDL_CONTROLLER_BUTTON_A:
        return "A (bottom)";
    case SDL_CONTROLLER_BUTTON_B:
        return "B (right)";
    case SDL_CONTROLLER_BUTTON_X:
        return "X (left)";
    case SDL_CONTROLLER_BUTTON_Y:
        return "Y (top)";
    case SDL_CONTROLLER_BUTTON_BACK:
        return "Back";
    case SDL_CONTROLLER_BUTTON_GUIDE:
        return "Guide";
    case SDL_CONTROLLER_BUTTON_START:
        return "Start";
    case SDL_CONTROLLER_BUTTON_LEFTSTICK:
        return "Left stick click";
    case SDL_CONTROLLER_BUTTON_RIGHTSTICK:
        return "Right stick click";
    case SDL_CONTROLLER_BUTTON_LEFTSHOULDER:
        return "LB";
    case SDL_CONTROLLER_BUTTON_RIGHTSHOULDER:
        return "RB";
    case SDL_CONTROLLER_BUTTON_DPAD_UP:
        return "D-pad up";
    case SDL_CONTROLLER_BUTTON_DPAD_DOWN:
        return "D-pad down";
    case SDL_CONTROLLER_BUTTON_DPAD_LEFT:
        return "D-pad left";
    case SDL_CONTROLLER_BUTTON_DPAD_RIGHT:
        return "D-pad right";
    default:
        return fmt::format("button {}", button);
    }
}

std::string ControllerAxisName(int axis) {
    switch (axis) {
    case SDL_CONTROLLER_AXIS_LEFTX:
    case SDL_CONTROLLER_AXIS_LEFTY:
        return "Left stick";
    case SDL_CONTROLLER_AXIS_RIGHTX:
    case SDL_CONTROLLER_AXIS_RIGHTY:
        return "Right stick";
    case SDL_CONTROLLER_AXIS_TRIGGERLEFT:
        return "LT";
    case SDL_CONTROLLER_AXIS_TRIGGERRIGHT:
        return "RT";
    default:
        return fmt::format("axis {}", axis);
    }
}

/// What a controller does for an input in the profile (Settings' parameters), or empty.
std::string ControllerName(const std::string& params) {
    const Common::ParamPackage p{params};
    const std::string engine = p.Get("engine", "");
    if (engine == "mh4u_any") { // the app's keyboard + controller pair (input.cpp)
        for (const char* side : {"b", "a"}) {
            if (std::string name = ControllerName(p.Get(side, "")); !name.empty()) {
                return name;
            }
        }
        return "";
    }
    if (engine == "analog_from_button") {
        return "";
    }
    if (engine == "keyboard" || engine.empty()) {
        return "";
    }
    if (engine != "sdl" && engine != "gcpad") {
        return engine;
    }
    if (p.Has("button")) {
        return ControllerButtonName(p.Get("button", -1));
    }
    if (p.Has("axis_x")) {
        return ControllerAxisName(p.Get("axis_x", -1));
    }
    if (p.Has("axis")) {
        return ControllerAxisName(p.Get("axis", -1));
    }
    if (p.Has("hat")) {
        return "D-pad (" + p.Get("direction", "") + ")";
    }
    return "controller";
}

const char* NameOf(Settings::GraphicsAPI api) {
    switch (api) {
    case Settings::GraphicsAPI::OpenGL:
        return "OpenGL";
    case Settings::GraphicsAPI::Software:
        return "software";
    default:
        return "Vulkan";
    }
}

Settings::LayoutOption LayoutFor(const std::string& screens) {
    if (screens == "single") {
        return Settings::LayoutOption::SingleScreen;
    }
    if (screens == "stacked") {
        return Settings::LayoutOption::Default;
    }
    if (screens == "side") {
        return Settings::LayoutOption::SideScreen;
    }
    if (screens == "windows") {
        return Settings::LayoutOption::SeparateWindows;
    }
    return Settings::LayoutOption::CustomLayout; // the overlay (mh4u.ini's touch_overlay)
}

} // namespace

std::string ControllerInputName(const std::string& params) {
    return ControllerName(params);
}

Menu::Menu(Layer& layer_) : layer{layer_} {}

Menu::~Menu() = default;

void Menu::Opened() {
    just_opened = true;
    capture = -1;
    capture_note.clear();
    std::string text;
    FileUtil::ReadFileToString(true, Port::OptionsPath(), text);
    game_file = ReadOptions(text);
    game_changes.clear();
}

void Menu::Closed() {
    capture = -1;
    if (layout_edit) {
        EndLayoutEdit(); // closed from the editor (Done, or the controller's Guide): kept
    }
    if (!game_changes.empty()) {
        const std::string path = Port::OptionsPath();
        std::string text;
        FileUtil::ReadFileToString(true, path, text);
        if (FileUtil::WriteStringToFile(true, path, WriteOptions(text, game_changes))) {
            std::string what;
            for (const auto& [key, value] : game_changes) {
                what += fmt::format("{}{} = {}", what.empty() ? "" : ", ", key, value);
            }
            LOG_INFO(Frontend, "menu: {} in {}", what, path);
        } else {
            LOG_ERROR(Frontend, "menu: couldn't write {}", path);
        }
        game_changes.clear();
    }
    if (!app_changes.empty()) {
        // the file's settings with the menu's changes: not the command line's (--windowed, ...)
        Config file = App::Load();
        const Config& now = layer.config;
        for (const AppSetting setting : app_changes) {
            switch (setting) {
            case AppSetting::Fullscreen:
                file.fullscreen = now.fullscreen;
                break;
            case AppSetting::Screens:
                file.screens = now.screens;
                break;
            case AppSetting::Resolution:
                file.resolution = now.resolution;
                break;
            case AppSetting::LinearFilter:
                file.linear_filter = now.linear_filter;
                break;
            case AppSetting::Vsync:
                file.vsync = now.vsync;
                break;
            case AppSetting::CpuClock:
                file.cpu_clock = now.cpu_clock;
                break;
            case AppSetting::Renderer:
                file.renderer = now.renderer;
                break;
            case AppSetting::Volume:
                file.volume = now.volume;
                break;
            case AppSetting::Output:
                file.audio_output = now.audio_output;
                break;
            case AppSetting::FpsCounter:
                file.fps_counter = now.fps_counter;
                break;
            case AppSetting::Controls:
                file.keys = now.keys;
                file.stick_keys = now.stick_keys;
                file.controller_auto = now.controller_auto;
                file.button_params = now.button_params;
                file.stick_params = now.stick_params;
                break;
            }
        }
        App::Save(file);
        LOG_INFO(Frontend, "menu: settings saved in {}", App::ConfigPath());
        app_changes.clear();
    }
}

std::string Menu::Game(const std::string& key) const {
    if (const auto it = game_changes.find(key); it != game_changes.end()) {
        return it->second;
    }
    if (const auto it = game_file.find(key); it != game_file.end()) {
        return it->second;
    }
    const auto it = GAME_DEFAULTS.find(key);
    return it == GAME_DEFAULTS.end() ? "" : it->second;
}

void Menu::SetGame(const std::string& key, const std::string& value) {
    game_changes[key] = value;
    if (GAME_LIVE.count(key)) {
        layer.Post([key, value] {
            if (!Port::Hooks::SetOption(key, value)) {
                LOG_WARNING(Frontend, "menu: {} applies from the next start", key);
            }
        });
    }
}

void Menu::Changed(AppSetting setting) {
    app_changes.insert(setting);
}

void Menu::ControlsChanged() {
    Changed(AppSetting::Controls);
    // the main loop builds the input profile again from the app's settings (as when a
    // controller comes) and gives the console's input services the new one
    layer.window.RequestInputReload();
}

void Menu::OnCapturedKey(int scancode) {
    if (capture < 0 || capture >= BINDING_COUNT) {
        layer.SetKeyCapture(false);
        return;
    }
    const Binding& binding = BINDINGS[capture];
    Config& config = layer.config;
    capture_note.clear();
    if (scancode == SDL_SCANCODE_ESCAPE) {
        // kept as it was
    } else if (scancode == SDL_SCANCODE_DELETE) {
        SetKey(config, binding, "");
        ControlsChanged();
    } else if (scancode == Input::KeyCode(config.fullscreen_key) ||
               scancode == Input::KeyCode(config.touch_screen_key) ||
               scancode == Input::KeyCode(config.fps_counter_key)) {
        capture_note = fmt::format("{} is the app's own key.", SDL_GetScancodeName(static_cast<SDL_Scancode>(scancode)));
    } else if (const std::string key = SDL_GetScancodeName(static_cast<SDL_Scancode>(scancode)); key.empty()) {
        capture_note = "That key has no name to keep in mh4u-app.ini.";
    } else {
        // one key, one input: it leaves the one it had
        for (const Binding& other : BINDINGS) {
            if (&other != &binding && KeyOf(config, other) == key) {
                SetKey(config, other, "");
            }
        }
        SetKey(config, binding, key);
        ControlsChanged();
    }
    capture = -1;
    layer.SetKeyCapture(false);
}

void Menu::Draw() {
    if (layout_edit) {
        DrawLayoutEditor();
        just_opened = false;
        return;
    }
    const ImGuiIO& io = ImGui::GetIO();
    const float em = ImGui::GetFontSize();
    // the game, dimmed behind the menu
    ImGui::GetBackgroundDrawList()->AddRectFilled(ImVec2(0, 0), io.DisplaySize, IM_COL32(0, 0, 0, 110));
    const ImVec2 size(std::min(io.DisplaySize.x * 0.94f, em * 46.0f), std::min(io.DisplaySize.y * 0.92f, em * 29.0f));
    ImGui::SetNextWindowPos(ImVec2(io.DisplaySize.x * 0.5f, io.DisplaySize.y * 0.5f), ImGuiCond_Always,
                            ImVec2(0.5f, 0.5f));
    ImGui::SetNextWindowSize(size, ImGuiCond_Always);
    if (just_opened) {
        ImGui::SetNextWindowFocus();
    }
    ImGui::Begin("##menu", nullptr,
                 ImGuiWindowFlags_NoTitleBar | ImGuiWindowFlags_NoResize | ImGuiWindowFlags_NoMove |
                     ImGuiWindowFlags_NoCollapse | ImGuiWindowFlags_NoSavedSettings |
                     ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoScrollWithMouse);

    // the title, and the way back
    ImGui::PushFont(nullptr, ImGui::GetStyle().FontSizeBase * 1.25f);
    ImGui::AlignTextToFramePadding();
    ImGui::TextUnformatted("MH4U PC port");
    ImGui::PopFont();
    const char* back = "Back to the game";
    const float back_width = ImGui::CalcTextSize(back).x + ImGui::GetStyle().FramePadding.x * 2;
    ImGui::SameLine();
    ImGui::SetCursorPosX(ImGui::GetCursorPosX() + std::max(0.0f, ImGui::GetContentRegionAvail().x - back_width));
    if (ImGui::Button(back)) {
        layer.CloseMenu();
    }
    layer.Help("Closes the menu (Esc, the controller's Guide button, or Back and Start together).");

    const float footer = ImGui::GetTextLineHeightWithSpacing() * 3.2f;
    if (ImGui::BeginTabBar("##tabs")) {
        for (int i = 0; i < TAB_COUNT; ++i) {
            const ImGuiTabItemFlags flags = select_tab == i ? ImGuiTabItemFlags_SetSelected : 0;
            if (ImGui::BeginTabItem(TABS[i], nullptr, flags)) {
                tab = i;
                ImGui::BeginChild("##page", ImVec2(0.0f, -footer), ImGuiChildFlags_NavFlattened);
                switch (i) {
                case 0:
                    DrawGame();
                    break;
                case 1:
                    DrawDisplay();
                    break;
                case 2:
                    DrawAudio();
                    break;
                case 3:
                    DrawControls();
                    break;
                case 4:
                    DrawMultiplayer();
                    break;
                default:
                    DrawQuit();
                    break;
                }
                ImGui::EndChild();
                ImGui::EndTabItem();
            }
        }
        ImGui::EndTabBar();
    }
    select_tab = -1;

    // what the control under the cursor does, and the keys
    ImGui::Separator();
    ImGui::PushTextWrapPos(0.0f);
    const char* help = layer.HelpText();
    ImGui::TextUnformatted(help ? help : "");
    ImGui::PopTextWrapPos();
    ImGui::SetCursorPosY(ImGui::GetWindowHeight() - ImGui::GetStyle().WindowPadding.y -
                         ImGui::GetTextLineHeight());
    ImGui::TextDisabled("Esc, Guide or Back+Start: back to the game    LB / RB: tabs    "
                        "A: choose    B: back");

    // a controller: LB and RB change tabs, B at the top closes the menu
    const bool idle = !layer.WasBusy() && capture < 0 &&
                      ImGui::IsWindowFocused(ImGuiFocusedFlags_RootAndChildWindows);
    if (idle && ImGui::IsKeyPressed(ImGuiKey_GamepadL1, false)) {
        select_tab = (tab + TAB_COUNT - 1) % TAB_COUNT;
    }
    if (idle && ImGui::IsKeyPressed(ImGuiKey_GamepadR1, false)) {
        select_tab = (tab + 1) % TAB_COUNT;
    }
    if (idle && ImGui::IsKeyPressed(ImGuiKey_GamepadFaceRight, false)) {
        layer.CloseMenu();
    }
    if (capture >= 0 && ImGui::IsKeyPressed(ImGuiKey_GamepadFaceRight, false)) {
        capture = -1;
        layer.SetKeyCapture(false);
    }
    ImGui::End();
    just_opened = false;
}

// ── Game: mh4u.ini ──

void Menu::DrawGame() {
    Config& config = layer.config;
    if (const std::string notice = Port::BackendNotice(); !notice.empty()) {
        ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(1.0f, 0.75f, 0.45f, 1.0f));
        ImGui::TextWrapped("%s", notice.c_str());
        ImGui::PopStyleColor();
    }
    ImGui::TextDisabled("mh4u.ini (saved when the menu closes)");

    Section("Frame rate");
    if (BeginSettings("##rate")) {
        Row("Frame rate");
        std::string fps = Game("fps");
        if (std::atoi(fps.c_str()) <= 0) {
            fps = "30";
        }
        if (ChooseValue("##fps", fps, {"30", "60", "90", "120", "144", "165", "180", "240"},
                        {"30 (as on a 3DS)", "60", "90", "120", "144", "165", "180", "240"})) {
            SetGame("fps", fps);
        }
        layer.Help("The most frames per second the game draws. Its speed stays right at any rate; above 60 a "
                   "busy scene may need a higher CPU clock speed.");
        Row("CPU clock speed");
        int clock = std::clamp(config.cpu_clock, 5, 400);
        if (ImGui::SliderInt("##cpu_clock", &clock, 50, 400, "%d%%")) {
            config.cpu_clock = clock;
            Changed(AppSetting::CpuClock);
            layer.Post([&system = layer.system, clock] {
                Settings::values.cpu_clock_percentage = static_cast<u32>(clock);
                system.CoreTiming().UpdateClockSpeed(static_cast<u32>(clock));
            });
        }
        layer.Help("The emulated CPU's speed, in % of a 3DS's (Azahar's CPU Clock Speed). More keeps a high "
                   "frame rate in busy scenes, if your PC can take it; too much makes some games misbehave.");
        Row("Screen refresh rate");
        std::string hz = Game("refresh_rate");
        if (std::atof(hz.c_str()) <= 0.0) {
            hz = "0";
        }
        if (ChooseValue("##hz", hz, {"0", "60", "120", "144", "165", "240"},
                        {"Auto (the frame rate above 60)", "60", "120", "144", "165", "240"})) {
            SetGame("refresh_rate", hz);
        }
        layer.Help("How often the emulated screen refreshes. The game shows at most one frame per "
                   "refresh: Auto follows the frame rate when it is above 60.");
        ImGui::EndTable();
    }

    Section("Widescreen");
    if (BeginSettings("##wide")) {
        Row("Shape of the top screen", false);
        std::string shape = Lower(Game("widescreen"));
        if (shape.empty() || IsOff(shape)) {
            shape = "off";
        }
        if (ChooseValue("##widescreen", shape, {"off", "16:9", "16:10", "21:9", "32:9", "4:3"},
                        {"5:3, as on a 3DS", "16:9", "16:10", "21:9", "32:9", "4:3"})) {
            SetGame("widescreen", shape);
        }
        layer.Help("The 3D view widened (or narrowed) to this shape.");
        Row("Draw the wider picture", false);
        bool wide = IsOn(Game("wide_render"));
        if (ImGui::Checkbox("##wide_render", &wide)) {
            SetGame("wide_render", wide ? "1" : "0");
        }
        layer.Help("On: the game draws the wider picture itself. Off: a 400x240 picture is stretched.");
        Row("HUD at the screen's edges");
        bool edges = IsOn(Game("hud_edges"));
        if (ImGui::Checkbox("##hud_edges", &edges)) {
            SetGame("hud_edges", edges ? "1" : "0");
        }
        layer.Help("With the wider picture: health, stamina and the party list on the left edge, the "
                   "items on the right one, instead of in the middle.");
        ImGui::EndTable();
    }

    Section("Touch screen");
    if (BeginSettings("##touch")) {
        Layout::ScreenOverlay overlay = Port::Hooks::ParseOverlay(Game("touch_overlay"));
        bool on = overlay.height > 0.0f;
        const auto write = [&] {
            static constexpr const char* corners[] = {"top-left", "top-right", "bottom-left", "bottom-right"};
            SetGame("touch_overlay",
                    on ? fmt::format("{} {} {}", corners[static_cast<int>(overlay.corner)],
                                     std::lround(overlay.height * 100), std::lround(overlay.opacity * 100))
                       : "off");
        };
        Row("Over the game, in a corner");
        if (ImGui::Checkbox("##touch_overlay", &on)) {
            if (on) {
                overlay = Port::Hooks::ParseOverlay("on");
            }
            write();
        }
        layer.Help("With the screens set to the game with the touch screen in a corner (Display): the "
                   "touch screen drawn over the game. Off: Azahar's own custom layout.");
        if (on) {
            Row("Corner");
            int corner = static_cast<int>(overlay.corner);
            if (Choose("##corner", corner, {"Top left", "Top right", "Bottom left", "Bottom right"})) {
                overlay.corner = static_cast<Layout::ScreenOverlay::Corner>(corner);
                write();
            }
            layer.Help("The item bar is at the bottom right of the game's screen.");
            Row("Size");
            int height = static_cast<int>(std::lround(overlay.height * 100));
            if (ImGui::SliderInt("##size", &height, 5, 100, "%d%% of the window's height")) {
                overlay.height = static_cast<float>(height) / 100.0f;
                write();
            }
            Row("Opacity");
            int opacity = static_cast<int>(std::lround(overlay.opacity * 100));
            if (ImGui::SliderInt("##opacity", &opacity, 10, 100, "%d%%")) {
                overlay.opacity = static_cast<float>(opacity) / 100.0f;
                write();
            }
        }
        Row("In quests, only the map");
        auto map = Port::Hooks::ParseQuestOverlay(Game("quest_overlay"));
        if (ImGui::Checkbox("##quest_overlay", &map.on)) {
            SetGame("quest_overlay", map.on ? "map" : "off");
        }
        layer.Help("In a quest the corner shows the map alone, and the whole touch screen again for the "
                   "quest menu (F12 or the right stick's click switch it while you play).");
        if (map.on) {
            DrawPanelViewRows("quest_overlay", "Map");
            Row("Target camera beside the map");
            auto target = Port::Hooks::ParseTargetOverlay(Game("target_overlay"));
            if (ImGui::Checkbox("##target_overlay", &target.on)) {
                SetGame("target_overlay", target.on ? "on" : "off");
            }
            layer.Help("With the map, the target camera panel (the large monster the camera follows; a tap on "
                       "it changes the target) in a small view of its own, if your panel layout has it.");
            if (target.on) {
                DrawPanelViewRows("target_overlay", "Target camera");
            }
        }
        if (on) {
            Row("Move them with the mouse");
            const bool overlay_layout =
                Settings::values.layout_option.GetValue() == Settings::LayoutOption::CustomLayout;
            ImGui::BeginDisabled(!overlay_layout);
            if (ImGui::Button("Edit HUD layout", ImVec2(-FLT_MIN, 0.0f))) {
                StartLayoutEdit();
            }
            ImGui::EndDisabled();
            layer.Help(overlay_layout
                           ? "Shows the map and the target camera over the game as in a quest (outside one, "
                             "whatever the touch screen has there): drag them, and drag a corner to resize."
                           : "Needs the screens set to the game with the touch screen in a corner (Display).");
        }
        ImGui::EndTable();
    }

    Section("Buttons for touch panels");
    if (BeginSettings("##buttons")) {
        const std::vector<std::string> buttons = {"OFF", "A",  "B",     "X",  "Y",    "L",    "R",
                                                  "ZL",  "ZR", "START", "UP", "DOWN", "LEFT", "RIGHT"};
        const std::vector<std::string> labels = {"None", "A",  "B",     "X",       "Y",         "L",         "R",
                                                 "ZL",   "ZR", "START", "D-pad up", "D-pad down", "D-pad left",
                                                 "D-pad right"};
        for (const auto& [key, name, help] :
             {std::tuple{"kick", "Kick", "The kick, which on a 3DS only the touch screen's kick panel does."},
              std::tuple{"target", "Target camera",
                         "What a tap on the target camera panel does: the camera's target on the large "
                         "monster (two: the first, the second, off)."},
              std::tuple{"skip", "Skip the scene",
                         "Once a quest is complete, the touch screen's kick button reads Skip: this button "
                         "taps it, which skips the scene of the monster's end. It can be the kick's: the "
                         "kick does nothing then."}}) {
            Row(name);
            std::string value = Upper(Game(key));
            if (value.empty() || IsOff(Lower(value)) || value == "NONE") {
                value = "OFF";
            }
            if (ChooseValue((std::string("##") + key).c_str(), value, buttons, labels)) {
                SetGame(key, Lower(value) == "off" ? "off" : value);
            }
            layer.Help(help);
        }
        Row("End the quest");
        std::string combo = Upper(Game("skip_end"));
        if (combo.empty() || IsOff(Lower(combo)) || combo == "NONE") {
            combo = "OFF";
        }
        if (ChooseValue("##skip_end", combo, {"OFF", "ZL+ZR", "L+R", "ZL+R", "L+ZR", "ZL+ZR+START"},
                        {"None", "ZL + ZR", "L + R", "ZL + R", "L + ZR", "ZL + ZR + START"})) {
            SetGame("skip_end", combo == "OFF" ? "off" : combo);
        }
        layer.Help("Held together once a quest is complete, these end the wait before the return (the "
                   "carving time) as if it had run out. Online, the wait ends once every player with the "
                   "port has held them; the screen says who hasn't.");
        Row("Hold for");
        float hold = std::strtof(Game("skip_end_hold").c_str(), nullptr);
        hold = hold > 0.0f ? hold : 1.0f;
        if (ImGui::SliderFloat("##skip_end_hold", &hold, 0.2f, 5.0f, "%.1f s")) {
            SetGame("skip_end_hold", fmt::format("{:.1f}", hold));
        }
        layer.Help("How long the buttons are held before the quest ends, so that it doesn't happen by accident.");
        Row("Skip hint");
        std::string hint = Lower(Game("skip_hint"));
        hint = IsOff(hint) ? "off" : hint == "flash" ? "flash" : "on";
        if (ChooseValue("##skip_hint", hint, {"on", "flash", "off"}, {"On", "Flashing", "Off"})) {
            SetGame("skip_hint", hint);
        }
        layer.Help("Once a quest is complete, lines at the bottom of the screen say which keys skip the "
                   "scene and which end the quest, show the hold, and online who hasn't asked to end it.");
        ImGui::EndTable();
    }

    Section("Picture");
    if (BeginSettings("##picture")) {
        Row("Dither pattern");
        bool dither = IsOn(Game("dither"));
        if (ImGui::Checkbox("##dither", &dither)) {
            SetGame("dither", dither ? "on" : "off");
        }
        layer.Help("The game's dither pattern, as on a 3DS: at a higher internal resolution it shows as a "
                   "fine grid over everything, so turn it off then.");
        Row("Stereoscopic 3D");
        bool stereo = IsOn(Game("stereo_3d"));
        if (ImGui::Checkbox("##stereo_3d", &stereo)) {
            SetGame("stereo_3d", stereo ? "on" : "off");
        }
        layer.Help("The game draws the top screen twice, once per eye, even with the 3D slider at 0: "
                   "a full scene pass more, and a doubled, soft picture. Off draws it once.");
        Row("Anti-aliasing");
        bool aa = !IsOff(Lower(Game("antialiasing")));
        if (ImGui::Checkbox("##antialiasing", &aa)) {
            SetGame("antialiasing", aa ? "on" : "off");
        }
        layer.Help("The game's own anti-aliasing, as on a 3DS. Off saves its pass: at a high internal "
                   "resolution the edges stay fine enough, and the frame rate gains.");
        for (const auto& [key, name, help] : {
                 std::tuple{"bloom", "Glow over bright parts", "Bloom: softens the picture with a haze."},
                 std::tuple{"depth_of_field", "Focus blur of cutscenes",
                            "Depth of field: emulation gets it wrong and can blur the whole picture."},
                 std::tuple{"motion_blur", "Motion blur", "The blur some effects blend in."},
                 std::tuple{"radial_blur", "Zoom blur of roars", "Radial blur, around a roaring monster."},
                 std::tuple{"haze", "Heat shimmer", "Haze."}}) {
            Row(name);
            bool filter = !IsOff(Lower(Game(key)));
            if (ImGui::Checkbox((std::string("##") + key).c_str(), &filter)) {
                SetGame(key, filter ? "on" : "off");
            }
            layer.Help(help);
        }
        ImGui::EndTable();
    }

    Section("Multiplayer");
    if (BeginSettings("##net")) {
        Row("Net sync", false);
        std::string sync = Lower(Game("net_sync"));
        sync = IsOff(sync) ? "off" : sync == "all" ? "all" : "on";
        if (ChooseValue("##net_sync", sync, {"on", "off", "all"},
                        {"On", "Off", "With every port player in the room"})) {
            SetGame("net_sync", sync);
        }
        layer.Help("In a room, the port's players wait a moment for one whose PC stalls, instead of the "
                   "game dropping them.");
        ImGui::EndTable();
    }
}

// ── Touch screen: the quest's map and target camera views, and their editor ──

namespace {

using Port::Hooks::PanelView;
using Port::Hooks::Placement;

bool IsMapKey(const std::string& key) {
    return key == "quest_overlay";
}

PanelView ParseView(const std::string& key, const std::string& value) {
    return IsMapKey(key) ? Port::Hooks::ParseQuestOverlay(value) : Port::Hooks::ParseTargetOverlay(value);
}

std::string FormatView(const std::string& key, const PanelView& view) {
    return IsMapKey(key) ? Port::Hooks::FormatQuestOverlay(view) : Port::Hooks::FormatTargetOverlay(view);
}

/// A view's width over its height: the map is two touch-panel slots tall, the target camera one.
float ViewAspect(const std::string& key) {
    return static_cast<float>(Port::Hooks::PANEL_SLOT_WIDTH) /
           static_cast<float>(Port::Hooks::PANEL_SLOT_HEIGHT * (IsMapKey(key) ? 2 : 1));
}

/// Where a view is in a `width` x `height` window, as Azahar's overlay layout puts it.
ImVec4 ViewRect(const Placement& p, float aspect, float width, float height) {
    const float h = std::clamp(std::round(height * p.height), 1.0f, height);
    const float w = std::min(width, std::floor(h * aspect));
    return ImVec4(std::round(p.x * (width - w)), std::round(p.y * (height - h)), w, h);
}

} // namespace

void Menu::DrawPanelViewRows(const char* key, const char* name) {
    auto view = ParseView(key, Game(key));
    const auto touch = Port::Hooks::ParseOverlay(Game("touch_overlay"));
    const auto map = Port::Hooks::ParseQuestOverlay(Game("quest_overlay"));
    const Placement now = IsMapKey(key) ? Port::Hooks::MapPlacement(touch, view)
                                        : Port::Hooks::TargetPlacement(touch, map, view);
    const std::string id = std::string("##") + key;
    const auto set = [&] { SetGame(key, FormatView(key, view)); };

    Row(fmt::format("{} size", name).c_str());
    float percent = view.height * 100.0f;
    if (ImGui::SliderFloat((id + "_size").c_str(), &percent, 0.0f, 100.0f,
                           percent < 1.0f ? "the touch screen's scale" : "%.0f%% of the window's height")) {
        view.height = percent < 1.0f ? 0.0f : std::max(percent, 3.0f) / 100.0f;
        set();
    }
    layer.Help("0: as large as it is in the touch screen in the corner.");

    Row(fmt::format("{} position", name).c_str());
    int where = view.placed ? 1 : 0;
    if (Choose((id + "_where").c_str(), where,
               {IsMapKey(key) ? "In the touch screen's corner" : "Under the map (over it at the bottom)",
                "Where you put it"})) {
        view.placed = where == 1;
        view.x = now.x;
        view.y = now.y;
        set();
    }
    layer.Help("Edit HUD layout below places it with the mouse; or set it here.");
    if (view.placed) {
        Row("    Across");
        float x = view.x * 100.0f;
        if (ImGui::SliderFloat((id + "_x").c_str(), &x, 0.0f, 100.0f, "%.0f%% (0 left, 100 right)")) {
            view.x = x / 100.0f;
            set();
        }
        layer.Help("0: at the window's left edge, 100: at its right edge, 50: in the middle.");
        Row("    Down");
        float y = view.y * 100.0f;
        if (ImGui::SliderFloat((id + "_y").c_str(), &y, 0.0f, 100.0f, "%.0f%% (0 top, 100 bottom)")) {
            view.y = y / 100.0f;
            set();
        }
        layer.Help("0: at the window's top edge, 100: at its bottom edge.");
    }
}

void Menu::StartLayoutEdit() {
    layout_edit = true;
    drag = -1;
    layout_before.clear();
    layout_before_missing.clear();
    for (const char* key : PANEL_VIEW_KEYS) {
        if (const auto it = game_changes.find(key); it != game_changes.end()) {
            layout_before[key] = it->second;
        } else {
            layout_before_missing.insert(key);
        }
    }
    layer.Post([] { Port::Hooks::SetLayoutEditing(true); });
    LOG_INFO(Frontend, "menu: the HUD layout editor");
}

void Menu::EndLayoutEdit() {
    layout_edit = false;
    drag = -1;
    layer.Post([] { Port::Hooks::SetLayoutEditing(false); });
}

void Menu::CancelLayoutEdit() {
    if (!layout_edit) {
        return;
    }
    for (const char* key : PANEL_VIEW_KEYS) {
        if (layout_before_missing.count(key)) {
            game_changes.erase(key);
        } else {
            game_changes[key] = layout_before[key];
        }
        layer.Post([key = std::string(key), value = Game(key)] { Port::Hooks::SetOption(key, value); });
    }
    EndLayoutEdit();
    select_tab = 0; // back to Game > Touch screen
}

void Menu::DrawLayoutEditor() {
    const ImGuiIO& io = ImGui::GetIO();
    const float em = ImGui::GetFontSize();
    const float width = io.DisplaySize.x, height = io.DisplaySize.y;
    const auto touch = Port::Hooks::ParseOverlay(Game("touch_overlay"));
    PanelView views[2] = {Port::Hooks::ParseQuestOverlay(Game("quest_overlay")),
                          Port::Hooks::ParseTargetOverlay(Game("target_overlay"))};
    const char* names[2] = {"Map", "Target camera"};
    ImVec4 rects[2];
    bool shown[2];
    for (int i = 0; i < 2; ++i) {
        const Placement p = i == 0 ? Port::Hooks::MapPlacement(touch, views[0])
                                   : Port::Hooks::TargetPlacement(touch, views[0], views[1]);
        rects[i] = ViewRect(p, ViewAspect(PANEL_VIEW_KEYS[i]), width, height);
        shown[i] = views[0].on && views[i].on; // the target camera only with the map
    }

    // the toolbar, at the top in the middle
    ImGui::SetNextWindowPos(ImVec2(width * 0.5f, em * 0.5f), ImGuiCond_Always, ImVec2(0.5f, 0.0f));
    // a fixed width, so that its buttons stay put whatever the help line says
    ImGui::SetNextWindowSize(ImVec2(std::min(width * 0.9f, em * 26.0f), 0.0f), ImGuiCond_Always);
    ImGui::SetNextWindowBgAlpha(0.92f);
    if (just_opened) {
        ImGui::SetNextWindowFocus();
    }
    ImGui::Begin("##layout_editor", nullptr,
                 ImGuiWindowFlags_NoTitleBar | ImGuiWindowFlags_NoResize | ImGuiWindowFlags_NoMove |
                     ImGuiWindowFlags_NoCollapse | ImGuiWindowFlags_NoSavedSettings |
                     ImGuiWindowFlags_NoScrollbar);
    ImGui::TextUnformatted("Edit HUD layout");
    ImGui::PushStyleColor(ImGuiCol_Text, ImGui::GetStyleColorVec4(ImGuiCol_TextDisabled));
    ImGui::TextWrapped("Drag a view to move it, its corner to resize it.");
    ImGui::PopStyleColor();
    for (int i = 0; i < 2; ++i) {
        if (i == 1) {
            ImGui::SameLine();
        }
        bool on = views[i].on;
        ImGui::BeginDisabled(i == 1 && !views[0].on);
        if (ImGui::Checkbox(names[i], &on)) {
            views[i].on = on;
            SetGame(PANEL_VIEW_KEYS[i], FormatView(PANEL_VIEW_KEYS[i], views[i]));
        }
        ImGui::EndDisabled();
    }
    if (ImGui::Button("Done")) {
        EndLayoutEdit();
        layer.CloseMenu();
    }
    layer.Help("Keeps the views where they are (saved in mh4u.ini) and goes back to the game.");
    ImGui::SameLine();
    if (ImGui::Button("Reset")) {
        // the touch screen's corner at its scale, the target camera under the map
        for (int i = 0; i < 2; ++i) {
            SetGame(PANEL_VIEW_KEYS[i], views[i].on ? (i == 0 ? "map" : "on") : "off");
        }
    }
    layer.Help("The map in the touch screen's corner at its scale, the target camera under it.");
    ImGui::SameLine();
    if (ImGui::Button("Cancel")) {
        CancelLayoutEdit();
    }
    layer.Help("Puts them back as they were (Esc, or a controller's B).");
    const char* help = layer.HelpText();
    ImGui::PushStyleColor(ImGuiCol_Text, ImGui::GetStyleColorVec4(ImGuiCol_TextDisabled));
    ImGui::TextWrapped("%s", help ? help : "Esc or a controller's B: cancel. Sliders for both: Game > Touch screen.");
    ImGui::PopStyleColor();
    ImGui::End();
    if (!layout_edit) {
        return;
    }
    if (ImGui::IsKeyPressed(ImGuiKey_GamepadFaceRight, false)) {
        CancelLayoutEdit();
        return;
    }

    // the mouse on the game: pick a view (the target camera is drawn over the map) or its corner
    const ImVec2 mouse = io.MousePos;
    const float handle = em * 1.1f;
    if (drag < 0 && ImGui::IsMouseClicked(ImGuiMouseButton_Left) &&
        !ImGui::IsWindowHovered(ImGuiHoveredFlags_AnyWindow)) {
        for (int i = 1; i >= 0; --i) {
            const ImVec4& r = rects[i];
            if (!shown[i] || mouse.x < r.x || mouse.y < r.y || mouse.x >= r.x + r.z || mouse.y >= r.y + r.w) {
                continue;
            }
            drag = i;
            drag_resize = mouse.x >= r.x + r.z - handle && mouse.y >= r.y + r.w - handle;
            drag_mouse_x = mouse.x;
            drag_mouse_y = mouse.y;
            drag_left = r.x;
            drag_top = r.y;
            drag_height = r.w;
            break;
        }
    }
    if (drag >= 0 && !ImGui::IsMouseDown(ImGuiMouseButton_Left)) {
        drag = -1;
    }
    if (drag >= 0 && width > 0 && height > 0) {
        const std::string key = PANEL_VIEW_KEYS[drag];
        const float aspect = ViewAspect(key);
        float left = drag_left, top = drag_top, h = drag_height;
        if (drag_resize) {
            // the top-left corner stays; the size follows the mouse, inside the window
            h = std::max((mouse.x - left) / aspect, mouse.y - top);
            h = std::clamp(h, std::max(height * 0.03f, 1.0f),
                           std::max(1.0f, std::min(height - top, (width - left) / aspect)));
        } else {
            left += mouse.x - drag_mouse_x;
            top += mouse.y - drag_mouse_y;
        }
        const float w = std::min(width, h * aspect);
        left = std::clamp(left, 0.0f, width - w);
        top = std::clamp(top, 0.0f, height - h);
        // the window's edges hold it a little, so that it sits right against them
        const float snap = em * 0.6f;
        left = left < snap ? 0.0f : width - w - left < snap ? width - w : left;
        top = top < snap ? 0.0f : height - h - top < snap ? height - h : top;
        PanelView view = views[drag];
        view.on = true;
        view.placed = true;
        view.height = h / height;
        view.x = width - w > 0.5f ? left / (width - w) : 0.0f;
        view.y = height - h > 0.5f ? top / (height - h) : 0.0f;
        if (const std::string text = FormatView(key, view); text != Game(key)) {
            SetGame(key, text);
        }
    }

    // the frames, under the toolbar
    ImDrawList* draw = ImGui::GetBackgroundDrawList();
    const ImU32 accent = ImGui::GetColorU32(ImGuiCol_CheckMark);
    for (int i = 0; i < 2; ++i) {
        if (!shown[i]) {
            continue;
        }
        const ImVec4& r = rects[i];
        const ImVec2 a(r.x, r.y), b(r.x + r.z, r.y + r.w);
        const bool hot = drag == i || (drag < 0 && mouse.x >= a.x && mouse.y >= a.y && mouse.x < b.x && mouse.y < b.y);
        draw->AddRectFilled(a, b, IM_COL32(255, 200, 120, hot ? 48 : 24));
        draw->AddRect(a, b, hot ? accent : IM_COL32(255, 255, 255, 200), 0.0f, std::max(2.0f, em * 0.12f));
        const ImVec2 text = ImGui::CalcTextSize(names[i]);
        const ImVec2 at(a.x + em * 0.3f, a.y + em * 0.2f);
        draw->PushClipRect(a, b, true); // a small view cuts its name short
        draw->AddRectFilled(ImVec2(at.x - em * 0.2f, at.y - em * 0.1f),
                            ImVec2(at.x + text.x + em * 0.2f, at.y + text.y + em * 0.1f), IM_COL32(0, 0, 0, 170),
                            em * 0.2f);
        draw->AddText(at, IM_COL32(255, 255, 255, 255), names[i]);
        draw->PopClipRect();
        draw->AddTriangleFilled(ImVec2(b.x, b.y - handle), b, ImVec2(b.x - handle, b.y), accent);
    }
    if (!shown[0]) {
        const char* text = "Tick Map in the box above to place the map.";
        const ImVec2 size = ImGui::CalcTextSize(text);
        draw->AddText(ImVec2((width - size.x) * 0.5f, (height - size.y) * 0.5f), IM_COL32(255, 255, 255, 230), text);
    }
}

// ── Display, Audio: mh4u-app.ini ──

void Menu::DrawDisplay() {
    Config& config = layer.config;
    const bool software = Settings::values.graphics_api.GetValue() == Settings::GraphicsAPI::Software;
    if (BeginSettings("##display")) {
        Row("Full screen");
        bool full = layer.window.IsFullscreen();
        if (ImGui::Checkbox("##fullscreen", &full)) {
            config.fullscreen = full;
            Changed(AppSetting::Fullscreen);
            layer.Post([&window = layer.window, full] { window.SetFullscreen(full); });
        }
        layer.Help("F11 or Alt+Enter switch it too.");

        Row("Frame-rate counter");
        std::string counter = FpsCounterOn(config.fps_counter) ? config.fps_counter : "off";
        if (ChooseValue("##fps_counter", counter, {std::begin(FPS_COUNTER_PLACES), std::end(FPS_COUNTER_PLACES)},
                        {"Off", "Top left", "Top right", "Bottom left", "Bottom right"})) {
            config.fps_counter = counter;
            Changed(AppSetting::FpsCounter);
        }
        // (the help line is drawn after this frame's widgets: the text has to outlive them)
        static std::string counter_help;
        counter_help = fmt::format("The game's frames per second over the picture. {} shows and hides it while "
                                   "you play.", config.fps_counter_key.empty() ? "A key (fps_counter_key)"
                                                                               : config.fps_counter_key);
        layer.Help(counter_help.c_str());

        // a window of its own for the touch screen exists only from the start
        const bool windows_now = Settings::values.layout_option.GetValue() == Settings::LayoutOption::SeparateWindows;
        Row("Screens", !windows_now);
        std::string screens = config.screens;
        if (ChooseValue("##screens", screens, {"overlay", "single", "stacked", "side", "windows"},
                        {"The game, the touch screen in a corner", "The game alone", "Stacked, as on a 3DS",
                         "Side by side", "The touch screen in a window of its own (next start)"})) {
            config.screens = screens;
            Changed(AppSetting::Screens);
            if (!windows_now && screens != "windows") {
                layer.Post([&system = layer.system, screens] {
                    Settings::values.layout_option = LayoutFor(screens);
                    Settings::values.swap_screen = false;
                    Settings::values.upright_screen = false;
                    system.GPU().Renderer().UpdateCurrentFramebufferLayout();
                });
            }
        }
        layer.Help("Where the two screens go. The corner is set in Game > Touch screen.");

        Row("Internal resolution");
        int resolution = std::clamp(config.resolution, 0, 10);
        std::vector<std::string> factors = {"As large as the window needs"};
        for (int i = 1; i <= 10; ++i) {
            factors.push_back(fmt::format("{}x ({}x{})", i, 400 * i, 240 * i));
        }
        ImGui::BeginDisabled(software);
        if (Choose("##resolution", resolution, factors)) {
            config.resolution = resolution;
            Changed(AppSetting::Resolution);
            Settings::values.resolution_factor = static_cast<u32>(resolution);
        }
        ImGui::EndDisabled();
        layer.Help(software ? "The software renderer draws at the 3DS's own resolution."
                            : "The 3D view drawn at a multiple of the 3DS's resolution.");

        Row("Smooth scaling");
        bool linear = config.linear_filter;
        if (ImGui::Checkbox("##linear", &linear)) {
            config.linear_filter = linear;
            Changed(AppSetting::LinearFilter);
            Settings::values.filter_mode = linear;
        }
        layer.Help("Smooths the picture when it is scaled to the window.");

        Row("Vsync");
        bool vsync = config.vsync;
        ImGui::BeginDisabled(software);
        if (ImGui::Checkbox("##vsync", &vsync)) {
            config.vsync = vsync;
            Changed(AppSetting::Vsync);
            Settings::values.use_vsync = vsync;
        }
        ImGui::EndDisabled();
        layer.Help("Frames shown in step with the monitor, without tearing.");

        Row("Renderer", false);
        std::string renderer = Lower(config.renderer);
        if (ChooseValue("##renderer", renderer, {"vulkan", "opengl", "software"},
                        {"Vulkan", "OpenGL", "Software (no graphics card)"})) {
            config.renderer = renderer;
            Changed(AppSetting::Renderer);
        }
        layer.Help("Vulkan runs on Metal through MoltenVK on a Mac. If the one chosen doesn't start, the "
                   "next one does.");
        ImGui::EndTable();
    }
    ImGui::TextDisabled("In use: %s.", NameOf(Settings::values.graphics_api.GetValue()));
}

void Menu::DrawAudio() {
    Config& config = layer.config;
    if (BeginSettings("##audio")) {
        Row("Volume");
        int volume = std::clamp(config.volume, 0, 100);
        if (ImGui::SliderInt("##volume", &volume, 0, 100, "%d%%")) {
            config.volume = volume;
            Changed(AppSetting::Volume);
            Settings::values.volume = static_cast<float>(volume) / 100.0f;
        }
        layer.Help("The game's sound.");
        Row("Output", false);
        std::string output = Lower(config.audio_output);
        if (ChooseValue("##output", output, {"auto", "cubeb", "sdl2", "openal", "null"},
                        {"Automatic", "cubeb", "SDL2", "OpenAL", "None"})) {
            config.audio_output = output;
            Changed(AppSetting::Output);
        }
        layer.Help("The system the sound goes out through.");
        ImGui::EndTable();
    }
}

// ── Controls ──

void Menu::DrawControls() {
    Config& config = layer.config;
    bool controller = config.controller_auto;
    if (ImGui::Checkbox("Map a controller on its own", &controller)) {
        config.controller_auto = controller;
        ControlsChanged();
    }
    layer.Help("The first controller found is mapped like MH3U on a Wii U: A, B, X and Y by position, ZL "
               "and ZR on the triggers, the C-stick (camera) on the right stick.");
    ImGui::SameLine();
    ImGui::TextDisabled("(%s)", Input::ControllerDescription().c_str());
    if (!capture_note.empty()) {
        ImGui::TextColored(ImVec4(1.0f, 0.6f, 0.4f, 1.0f), "%s", capture_note.c_str());
    }

    const auto& profile = Settings::values.current_input_profile;
    if (ImGui::BeginTable("##controls", 3, ImGuiTableFlags_SizingStretchProp | ImGuiTableFlags_RowBg)) {
        ImGui::TableSetupColumn("3DS", ImGuiTableColumnFlags_WidthStretch, 0.38f);
        ImGui::TableSetupColumn("Keyboard", ImGuiTableColumnFlags_WidthStretch, 0.32f);
        ImGui::TableSetupColumn("Controller", ImGuiTableColumnFlags_WidthStretch, 0.30f);
        HeaderRow();
        for (int i = 0; i < BINDING_COUNT; ++i) {
            const Binding& b = BINDINGS[i];
            ImGui::TableNextRow();
            ImGui::TableSetColumnIndex(0);
            ImGui::AlignTextToFramePadding();
            ImGui::TextUnformatted(b.name);
            ImGui::TableSetColumnIndex(1);
            const bool custom = b.button >= 0 ? !config.button_params[b.button].empty()
                                              : !config.stick_params[b.stick].empty();
            const std::string key = KeyOf(config, b);
            const std::string label = capture == i ? std::string("Press a key...")
                                      : custom     ? std::string("(Azahar parameters)")
                                      : key.empty() ? std::string("-")
                                                    : key;
            ImGui::BeginDisabled(custom);
            if (ImGui::Button(fmt::format("{}##key{}", label, i).c_str(), ImVec2(-FLT_MIN, 0.0f))) {
                capture = i;
                capture_note.clear();
                layer.SetKeyCapture(true);
            }
            ImGui::EndDisabled();
            layer.Help(custom ? "Set with Azahar's input parameters in mh4u-app.ini (param_...): Reset "
                                "to the defaults to use keys again."
                              : "Choose, then press the key for it (Esc: keep the one it has, Delete: "
                                "none). A key taken from another input leaves that one without.");
            ImGui::TableSetColumnIndex(2);
            ImGui::AlignTextToFramePadding();
            std::string pad = b.button >= 0 ? ControllerName(profile.buttons[b.button])
                                            : ControllerName(profile.analogs[b.stick]);
            if (b.button < 0 && !pad.empty()) {
                static constexpr const char* directions[] = {"up", "down", "left", "right", ""};
                pad = b.part < 4 ? pad + " " + directions[b.part] : std::string("by how far it is pushed");
            }
            ImGui::TextDisabled("%s", pad.empty() ? "-" : pad.c_str());
        }
        ImGui::EndTable();
    }
    ImGui::Spacing();
    if (ImGui::Button("Reset to the defaults")) {
        const Config defaults = App::Defaults();
        config.keys = defaults.keys;
        config.stick_keys = defaults.stick_keys;
        config.button_params = defaults.button_params;
        config.stick_params = defaults.stick_params;
        config.controller_auto = defaults.controller_auto;
        capture = -1;
        layer.SetKeyCapture(false);
        ControlsChanged();
    }
    layer.Help("The keys as they come (W A S D, E, Space, ...) and the controller mapped on its own.");
    ImGui::TextDisabled("The app's own keys: Esc menu, F11 full screen, F12 touch screen in quests.");
}

// ── Multiplayer ──

void Menu::DrawMultiplayer() {
    DrawMultiplayerTab(layer); // ui/multiplayer_tab.cpp
}

// ── Quit ──

void Menu::DrawQuit() {
    ImGui::TextWrapped("Back to the desktop. Save in the game first: what you did since your last save "
                       "is lost.");
    ImGui::Spacing();
    if (ImGui::Button("Quit the game")) {
        ImGui::OpenPopup("Quit?");
    }
    layer.Help("The app closes as when its window is closed.");
    ImGui::SetNextWindowPos(ImGui::GetMainViewport()->GetCenter(), ImGuiCond_Appearing, ImVec2(0.5f, 0.5f));
    if (ImGui::BeginPopupModal("Quit?", nullptr, ImGuiWindowFlags_AlwaysAutoResize | ImGuiWindowFlags_NoSavedSettings)) {
        ImGui::TextUnformatted("Quit Monster Hunter 4 Ultimate?");
        ImGui::Spacing();
        if (ImGui::Button("Quit")) {
            ImGui::CloseCurrentPopup();
            layer.CloseMenu(); // its changes are saved first
            layer.Post([&window = layer.window] {
                LOG_INFO(Frontend, "quit from the menu");
                window.RequestClose();
            });
        }
        ImGui::SameLine();
        if (ImGui::Button("Cancel") || CancelPressed()) {
            ImGui::CloseCurrentPopup();
        }
        ImGui::SetItemDefaultFocus();
        ImGui::EndPopup();
    }
}

} // namespace App::Ui
