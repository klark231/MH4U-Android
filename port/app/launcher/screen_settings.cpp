// Settings: the app's main settings (renderer, full screen, resolution, the screens, custom
// textures, vsync, volume, the launcher itself), saved in mh4u-app.ini as they change (App::Save).
#include <fmt/format.h>
#include <imgui.h>

#include "common/file_util.h"
#include "frontend.h"
#include "launcher/state.h"
#include "launcher/ui.h"

namespace Launcher {

namespace {

struct Choice {
    const char* value;
    const char* label;
    const char* about;
};

constexpr Choice RENDERERS[] = {
#ifdef ENABLE_VULKAN
    {"vulkan", "Vulkan", "The fastest on most PCs (on a Mac, Vulkan runs on Metal)."},
#endif
#ifdef ENABLE_OPENGL
    {"opengl", "OpenGL", "For graphics drivers without Vulkan."},
#endif
#ifdef ENABLE_SOFTWARE_RENDERER
    {"software", "Software", "Without the graphics card: slow, for when nothing else works."},
#endif
};

constexpr Choice SCREENS[] = {
    {"overlay", "The game, the touch screen in a corner",
     "The game fills the window; the touch screen sits over it in a corner (mh4u.ini's touch_overlay), "
     "in quests the map alone. F12 or the right stick's click switches it."},
    {"single", "The game alone", "Only the top screen."},
    {"stacked", "Both screens, stacked", "As on a 3DS."},
    {"side", "Both screens, side by side", "The touch screen to the right of the game."},
    {"windows", "The touch screen in its own window", "Two windows (not with the software renderer)."},
};

/// A label in the first column, the control in the second.
void Label(const char* text, const char* about = nullptr) {
    ImGui::TableNextRow();
    ImGui::TableNextColumn();
    ImGui::AlignTextToFramePadding();
    ImGui::TextUnformatted(text);
    if (about) {
        Ui::PushFont(0.85f);
        Ui::TextDim(about);
        Ui::PopFont();
    }
    ImGui::TableNextColumn();
}

} // namespace

void DrawSettings(State& s) {
    const float scale = Ui::Scale();
    const auto& c = s.config;
    ImGui::BeginChild("##settings", ImVec2(0, 0), ImGuiChildFlags_NavFlattened);
    Ui::Heading("Settings");
    Ui::TextDim(fmt::format("Saved as they change, in {}. The game's own options (frame rate, widescreen, "
                            "the touch screen's corner, the screen filters) are in {}.",
                            App::ConfigPath(), Port::OptionsPath()));
    ImGui::Spacing();
    if (!ImGui::BeginTable("##settings_table", 2, ImGuiTableFlags_SizingStretchProp | ImGuiTableFlags_PadOuterX)) {
        ImGui::EndChild();
        return;
    }
    ImGui::TableSetupColumn("label", ImGuiTableColumnFlags_WidthStretch, 0.42f);
    ImGui::TableSetupColumn("value", ImGuiTableColumnFlags_WidthStretch, 0.58f);

    // the renderer
    Label("Renderer", "How the game is drawn.");
    const Choice* renderer = nullptr;
    for (const auto& r : RENDERERS) {
        if (c.renderer == r.value) {
            renderer = &r;
        }
    }
    ImGui::SetNextItemWidth(-FLT_MIN);
    FocusNext(s);
    if (ImGui::BeginCombo("##renderer", renderer ? renderer->label : c.renderer.c_str())) {
        for (const auto& r : RENDERERS) {
            if (ImGui::Selectable(r.label, renderer == &r)) {
                const std::string value = r.value;
                s.Change([&](App::Config& cfg) { cfg.renderer = value; });
            }
            if (renderer == &r) {
                ImGui::SetItemDefaultFocus();
            }
        }
        ImGui::EndCombo();
    }
    if (renderer) {
        Ui::TextDim(renderer->about);
    }

    Label("Full screen", "F11 or Alt+Enter switches it while you play.");
    bool fullscreen = c.fullscreen;
    if (Ui::Toggle(fullscreen ? "On##fullscreen" : "Off##fullscreen", &fullscreen)) {
        s.Change([&](App::Config& cfg) { cfg.fullscreen = fullscreen; });
    }

    Label("Internal resolution", "The 3DS's 400x240 drawn this many times larger.");
    const std::string current = c.resolution == 0 ? "As the window needs" : fmt::format("{}x ({}x{})", c.resolution,
                                                                                       400 * c.resolution,
                                                                                       240 * c.resolution);
    ImGui::SetNextItemWidth(-FLT_MIN);
    if (ImGui::BeginCombo("##resolution", current.c_str())) {
        for (int r = 0; r <= 10; ++r) {
            const std::string label = r == 0 ? "As the window needs" : fmt::format("{}x ({}x{})", r, 400 * r, 240 * r);
            if (ImGui::Selectable(label.c_str(), c.resolution == r)) {
                s.Change([&](App::Config& cfg) { cfg.resolution = r; });
            }
            if (c.resolution == r) {
                ImGui::SetItemDefaultFocus();
            }
        }
        ImGui::EndCombo();
    }

    Label("Screens", "Where the 3DS's two screens go.");
    for (const auto& screen : SCREENS) {
        if (ImGui::RadioButton(screen.label, c.screens == screen.value)) {
            const std::string value = screen.value;
            s.Change([&](App::Config& cfg) { cfg.screens = value; });
        }
        if (c.screens == screen.value) {
            ImGui::Indent(ImGui::GetFrameHeight() + ImGui::GetStyle().ItemInnerSpacing.x);
            Ui::PushFont(0.85f);
            Ui::TextDim(screen.about);
            Ui::PopFont();
            ImGui::Unindent(ImGui::GetFrameHeight() + ImGui::GetStyle().ItemInnerSpacing.x);
        }
    }

    Label("Custom textures", "Texture packs in load/textures/0004000000126300/ (the Mods screen's catalog "
                             "installs them there).");
    bool textures = c.custom_textures;
    if (Ui::Toggle(textures ? "On##custom_textures" : "Off##custom_textures", &textures)) {
        s.Change([&](App::Config& cfg) { cfg.custom_textures = textures; });
    }

    Label("Vsync", "Frames in step with the monitor: no tearing.");
    bool vsync = c.vsync;
    if (Ui::Toggle(vsync ? "On##vsync" : "Off##vsync", &vsync)) {
        s.Change([&](App::Config& cfg) { cfg.vsync = vsync; });
    }

    Label("Volume");
    int volume = c.volume;
    ImGui::SetNextItemWidth(std::min(ImGui::GetContentRegionAvail().x, 420.0f * scale));
    ImGui::SliderInt("##volume", &volume, 0, 100, "%d%%");
    if (volume != c.volume) {
        s.config.volume = volume;  // while it moves; saved when let go
    }
    if (ImGui::IsItemDeactivatedAfterEdit()) {
        s.Change([&](App::Config& cfg) { cfg.volume = volume; });
    }

    Label("This launcher", "Off: the app starts the game straight away (the launcher still opens while "
                           "something in the setup is missing). --launcher shows it once.");
    bool launcher = c.launcher;
    if (Ui::Toggle(launcher ? "Shown when the app starts##launcher" : "Not shown##launcher", &launcher)) {
        s.Change([&](App::Config& cfg) { cfg.launcher = launcher; });
    }
    ImGui::EndTable();
    ImGui::EndChild();
}

} // namespace Launcher
