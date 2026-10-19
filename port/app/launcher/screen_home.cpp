// Home: Play, the state of the setup with a button to fix each part, the room, the mods,
// the version, Quit.
#include <fmt/format.h>
#include <imgui.h>

#include "launcher/state.h"
#include "launcher/ui.h"

namespace Launcher {

namespace {

/// A setup line with its fix-it button on the right; true when the button was pressed.
bool SetupLine(const SetupRow& row, const char* button, bool enabled = true) {
    // (the label's width without its ##id)
    const float button_width = ImGui::CalcTextSize(button, nullptr, true).x + ImGui::GetStyle().FramePadding.x * 2;
    const float right = ImGui::GetContentRegionAvail().x;
    ImGui::BeginGroup();
    ImGui::PushTextWrapPos(ImGui::GetCursorPosX() + right - button_width - ImGui::GetStyle().ItemSpacing.x * 2);
    Ui::StatusLine(row.status, row.label, row.detail);
    ImGui::PopTextWrapPos();
    ImGui::EndGroup();
    Ui::SameLineRight(button_width);
    ImGui::BeginDisabled(!enabled);
    const bool pressed = ImGui::Button(button);
    ImGui::EndDisabled();
    return pressed;
}

int EnabledMods(const State& s) {
    int n = 0;
    for (const auto& mod : s.mods.mods) {
        n += mod.enabled ? 1 : 0;
    }
    return n;
}

} // namespace

void DrawHome(State& s) {
    const float scale = Ui::Scale();
    const ImVec2 avail = ImGui::GetContentRegionAvail();
    const float gap = 28.0f * scale;
    const float left_width = std::max(avail.x * 0.4f, 300.0f * scale);

    // ── left: Play and the room
    ImGui::BeginChild("##home_left", ImVec2(left_width, 0), ImGuiChildFlags_NavFlattened);
    ImGui::Dummy(ImVec2(0, avail.y * 0.06f));
    const bool ready = s.game.state == Checks::Game::State::Ok;
    FocusNext(s);
    if (Ui::PlayButton("PLAY##play", ImVec2(-FLT_MIN, std::max(110.0f * scale, avail.y * 0.3f)), ready)) {
        s.Play();
    }
    ImGui::Spacing();
    if (s.play_waiting > 0) {
        Ui::Spinner();
        ImGui::SameLine();
        Ui::TextDim(s.play_waiting == 1 ? "Reading the mod library first..." : "Checking the code library first...");
    } else if (!ready) {
        Ui::TextColored(Ui::Color::Warn, "Choose your game in Setup first.");
    } else if (!s.SetupComplete()) {
        Ui::TextDim("The setup isn't complete: the game would run without the port's code library.");
    } else {
        Ui::TextDim(fmt::format("Monster Hunter 4 Ultimate, {}", FileName(s.config.game)));
    }
    ImGui::Spacing();
    ImGui::Spacing();
    Ui::BeginCard("##room", ImVec2(0, 0));
    Ui::PushFont(0.85f, true);
    ImGui::TextColored(ImGui::ColorConvertU32ToFloat4(Ui::Color::Dim), "ROOM");
    Ui::PopFont();
    Ui::TextColored(Ui::Color::Text, RoomSummary(s));
    if (ImGui::Button("Change room")) {
        s.GoTo(Screen::Online);
    }
    if (s.config.host > 0 || !s.config.join.empty()) {
        ImGui::SameLine();
        if (ImGui::Button("Play solo")) {
            s.Change([](App::Config& c) {
                c.join.clear();
                c.host = 0;
            });
            s.room_label.clear();
        }
    }
    Ui::EndCard();
    ImGui::Spacing();
    if (ImGui::Button("Quit")) {
        s.result = Result::Quit;
        s.done = true;
    }
    ImGui::EndChild();

    ImGui::SameLine(0, gap);

    // ── right: the setup, the mods
    ImGui::BeginChild("##home_right", ImVec2(0, 0), ImGuiChildFlags_NavFlattened);
    ImGui::Dummy(ImVec2(0, avail.y * 0.06f));
    Ui::BeginCard("##setup", ImVec2(0, 0), !s.SetupComplete());
    Ui::PushFont(0.85f, true);
    ImGui::TextColored(ImGui::ColorConvertU32ToFloat4(Ui::Color::Dim), "SETUP");
    Ui::PopFont();
    if (SetupLine(GameRow(s), s.game.state == Checks::Game::State::Ok ? "Change##game" : "Choose##game")) {
        s.ChooseGame();
    }
    ImGui::Spacing();
    if (SetupLine(UpdateRow(s), s.update.state == Checks::Update::State::Ok ? "Reinstall##update" : "Install##update")) {
        s.ChooseAndInstallUpdate();
    }
    ImGui::Spacing();
    const bool code_ok = s.code.state == Checks::Code::State::Ok;
    if (SetupLine(CodeRow(s), code_ok ? "Build again##code" : "Build##code",
                  s.update.state == Checks::Update::State::Ok && s.code.state != Checks::Code::State::Checking)) {
        s.StartBuild();
    }
    Ui::EndCard();
    ImGui::Spacing();

    Ui::BeginCard("##mods", ImVec2(0, 0));
    Ui::PushFont(0.85f, true);
    ImGui::TextColored(ImGui::ColorConvertU32ToFloat4(Ui::Color::Dim), "MODS");
    Ui::PopFont();
    std::string mods_text;
    if (s.probing) {
        mods_text = "Looking for the port's tools...";
    } else if (!s.ToolsReady()) {
        mods_text = s.tools_dir.empty() ? "The mod library needs the port's tools (mh4u-tools)."
                                        : "The mod library needs Python 3.10 or newer.";
    } else if (!s.mods.loaded) {
        mods_text = s.mods.error.empty() ? "Reading the mod library..." : s.mods.error;
    } else if (s.mods.mods.empty()) {
        mods_text = "No mods in the library.";
    } else {
        mods_text = fmt::format("{} of {} on", EnabledMods(s), s.mods.mods.size());
        if (s.mods.deploy_state == "out_of_date") {
            mods_text += ", put in place when you play";
        } else if (s.mods.deploy_state == "blocked") {
            mods_text += ", but they can't be put in place: see Mods";
        }
        if (s.mods.code_state == "rebuild") {
            mods_text += ". The code library needs building again for the code mods.";
        }
    }
    const float button_width = ImGui::CalcTextSize("Manage").x + ImGui::GetStyle().FramePadding.x * 2;
    ImGui::PushTextWrapPos(ImGui::GetCursorPosX() + ImGui::GetContentRegionAvail().x - button_width -
                           ImGui::GetStyle().ItemSpacing.x * 2);
    ImGui::AlignTextToFramePadding();
    ImGui::TextUnformatted(mods_text.c_str());
    ImGui::PopTextWrapPos();
    Ui::SameLineRight(button_width);
    if (ImGui::Button("Manage")) {
        s.GoTo(Screen::Mods);
    }
    Ui::EndCard();

    // the version, at the bottom
    Ui::PushFont(0.8f);
    const float line = ImGui::GetTextLineHeightWithSpacing();
    if (ImGui::GetContentRegionAvail().y > line) {
        ImGui::SetCursorPosY(ImGui::GetCursorPosY() + ImGui::GetContentRegionAvail().y - line);
    }
    ImGui::TextColored(ImGui::ColorConvertU32ToFloat4(Ui::Color::Dim), "%s", AppVersion().c_str());
    Ui::PopFont();
    ImGui::EndChild();
}

} // namespace Launcher
