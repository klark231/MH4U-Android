#include "launcher/launcher.h"
#include "icon.h"

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <utility>
#include <SDL.h>
#include <fmt/format.h>
#include <imgui.h>
#include <imgui_impl_sdl2.h>
#include <imgui_impl_sdlrenderer2.h>
#include <imgui_internal.h>

#include "common/file_util.h"
#include "common/logging/log.h"
#include "common/scm_rev.h"
#include "config.h"
#include "core/core.h"
#include "frontend.h"
#include "launcher/pick.h"
#include "launcher/state.h"
#include "launcher/ui.h"

#ifndef MH4U_PORT_COMMIT
#define MH4U_PORT_COMMIT ""
#endif

namespace Launcher {

namespace {

constexpr Uint32 IDLE_AFTER_MS = 2000;  // without input: draw only every so often
constexpr int WORKING_WAIT_MS = 66;     // a job, a check or a list going on: ~15 frames a second
constexpr int IDLE_WAIT_MS = 100;       // nothing going on: ~10 (the Play button's glow)
constexpr int HIDDEN_WAIT_MS = 200;     // minimised: no frames, the jobs still followed

SDL_Window* g_window = nullptr;
bool g_fullscreen = false;
bool g_code_notice_seen = false;  ///< CodeNoticeSeen()

void SetFullscreen(bool on) {
    if (g_window && SDL_SetWindowFullscreen(g_window, on ? SDL_WINDOW_FULLSCREEN_DESKTOP : 0) == 0) {
        g_fullscreen = on;
    }
}

/// What the picker gave: the path to `then`, or a line to type the path into where the system
/// has no picker.
void Picked(State& s, bool folder, const std::string& title, const Pick::Result& picked,
            std::function<void(const std::string&)> then) {
    // keys held when the dialog opened never saw their release
    ImGui::GetIO().ClearInputKeys();
    if (!picked.available) {
        Dialog d;
        d.title = title;
        d.text = "This system has no file dialog (zenity or kdialog): type or paste the path.";
        d.input = true;
        d.input_hint = folder ? "/path/to/the/folder" : "/path/to/the/file";
        d.on_input = std::move(then);
        d.buttons = {{"OK", nullptr, true}, {"Cancel", nullptr, false}};
        s.Show(std::move(d));
        return;
    }
    if (!picked.path.empty()) {
        then(picked.path);
    }
}

/// A system file or folder picker. Windows' is a modal dialog that keeps the launcher's window
/// answering; elsewhere it is another program (zenity, kdialog, osascript), waited for on a
/// thread while the launcher draws a window saying so, out of full screen (a desktop's dialog
/// can open behind a full-screen window).
void PickPath(State& s, bool folder, const std::string& title, const std::string& kind,
              const std::vector<std::string>& extensions, std::function<void(const std::string&)> then) {
#ifdef _WIN32
    const Pick::Result picked = folder ? Pick::Folder(title) : Pick::File(title, kind, extensions);
    Picked(s, folder, title, picked, std::move(then));
#else
    if (s.picking) {
        return;
    }
    State::Picking p;
    p.folder = folder;
    p.title = title;
    p.then = std::move(then);
    p.was_fullscreen = g_fullscreen;
    if (p.was_fullscreen) {
        SetFullscreen(false);
    }
    p.result = std::async(std::launch::async, [folder, title, kind, extensions] {
        return folder ? Pick::Folder(title) : Pick::File(title, kind, extensions);
    });
    s.picking = std::move(p);
#endif
}

void PumpPick(State& s) {
    if (!s.picking || s.picking->result.wait_for(std::chrono::seconds(0)) != std::future_status::ready) {
        return;
    }
    State::Picking p = std::move(*s.picking);
    s.picking.reset();
    if (p.was_fullscreen) {
        SetFullscreen(true);
    }
    Picked(s, p.folder, p.title, p.result.get(), std::move(p.then));
}

/// While the picker is up: a window saying so, which keeps the launcher's own items out of
/// reach; the frame after, it closes.
void DrawPicking(State& s) {
    const bool up = s.picking.has_value();
    if (up && !ImGui::IsPopupOpen("##picking")) {
        ImGui::OpenPopup("##picking");
    }
    const ImVec2 display = ImGui::GetIO().DisplaySize;
    ImGui::SetNextWindowPos(ImVec2(display.x * 0.5f, display.y * 0.5f), ImGuiCond_Always, ImVec2(0.5f, 0.5f));
    ImGui::SetNextWindowSize(ImVec2(std::min(display.x * 0.9f, 720.0f * Ui::Scale()), 0.0f), ImGuiCond_Always);
    if (!ImGui::BeginPopupModal("##picking", nullptr,
                                ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_NoMove |
                                    ImGuiWindowFlags_NoSavedSettings)) {
        return;
    }
    if (up) {
        Ui::Heading(s.picking->title);
        ImGui::Spacing();
        Ui::Spinner();
        ImGui::SameLine();
        Ui::TextDim(s.picking->folder ? "Choose the folder in the system's window (it may be behind this one)."
                                      : "Choose the file in the system's window (it may be behind this one).");
    } else {
        ImGui::CloseCurrentPopup();
    }
    ImGui::EndPopup();
}

void PumpJob(State& s) {
    Job& job = s.job;
    if (!job.Active() || job.finished) {
        return;
    }
    int code = 0;
    std::string output;
    if (job.process) {
        if (job.process->Running()) {
            return;
        }
        code = job.process->ExitCode();
        output = job.process->Output();
        job.lines = job.process->Lines();
    } else if (job.install) {
        if (job.install->Running()) {
            return;
        }
        code = job.install->Error().empty() ? 0 : 1;
        output = job.install->Error();
    }
    job.finished = true;
    job.ok = code == 0;
    job.focus_set = false;
    if (job.quit_after) {
        s.result = Result::Quit;
        s.done = true;
        return;
    }
    if (job.process && job.process->WasStopped()) {
        job.summary = "Stopped.";
    } else if (!job.ok && job.summary.empty()) {
        job.summary = job.process ? Tools::ErrorSummary(job.lines) : output;
    }
    if (job.on_done) {
        auto done = std::move(job.on_done);
        done(code, output);
    }
}

void PumpProbe(State& s) {
    if (!s.probing || s.probe.wait_for(std::chrono::seconds(0)) != std::future_status::ready) {
        return;
    }
    auto [tools, python] = s.probe.get();
    s.probing = false;
    s.tools_dir = tools;
    s.python = python;
    LOG_INFO(Frontend, "launcher: tools {}, Python {}", tools.empty() ? "not found" : tools,
             python.command.empty() ? "not found" : python.version);
    if (s.ToolsReady()) {
        RefreshMods(s);
    }
}

void PumpRooms(State& s) {
    if (!s.rooms_loading ||
        s.rooms_future.wait_for(std::chrono::seconds(0)) != std::future_status::ready) {
        return;
    }
    s.rooms = s.rooms_future.get();
    s.rooms_loading = false;
    s.rooms_fetched = true;
}

void TopBar(State& s) {
    const float scale = Ui::Scale();
    ImGui::BeginGroup();
    Ui::PushFont(1.7f, true);
    ImGui::TextColored(ImGui::ColorConvertU32ToFloat4(Ui::Color::Accent), "MH4U");
    Ui::PopFont();
    ImGui::SameLine(0, 10.0f * scale);
    ImGui::BeginGroup();
    ImGui::Dummy(ImVec2(0, 4.0f * scale));
    Ui::PushFont(0.8f, true);
    ImGui::TextColored(ImGui::ColorConvertU32ToFloat4(Ui::Color::Text), "PC PORT");
    Ui::PopFont();
    Ui::PushFont(0.7f);
    ImGui::TextColored(ImGui::ColorConvertU32ToFloat4(Ui::Color::Dim), "Monster Hunter 4 Ultimate");
    Ui::PopFont();
    ImGui::EndGroup();
    ImGui::EndGroup();

    // the tabs, on the right, with the controller's shoulder buttons around them
    const bool pad = s.pad;
    float width = 0.0f;
    Ui::PushFont(1.1f, true);
    for (int i = 0; i < SCREEN_COUNT; ++i) {
        width += ImGui::CalcTextSize(ScreenName(static_cast<Screen>(i))).x +
                 ImGui::GetStyle().FramePadding.x * 2.4f + ImGui::GetStyle().ItemSpacing.x * 0.5f;
    }
    Ui::PopFont();
    if (pad) {
        width += 80.0f * scale;
    }
    Ui::SameLineRight(width);
    // (the keycaps a little lower than the tabs: the line's own y set again after each)
    const float line_y = ImGui::GetCursorPosY();
    if (pad) {
        ImGui::SetCursorPosY(line_y + 8.0f * scale);
        Ui::KeyHint("LB", "");
        ImGui::SameLine();
        ImGui::SetCursorPosY(line_y);
    }
    for (int i = 0; i < SCREEN_COUNT; ++i) {
        const auto screen = static_cast<Screen>(i);
        if (Ui::Tab(ScreenName(screen), s.screen == screen)) {
            s.GoTo(screen);
        }
        ImGui::SameLine(0, ImGui::GetStyle().ItemSpacing.x * 0.5f);
    }
    if (pad) {
        ImGui::SetCursorPosY(line_y + 8.0f * scale);
        Ui::KeyHint("RB", "");
    } else {
        ImGui::NewLine();
    }
    // a hairline under the bar
    const ImVec2 at = ImGui::GetCursorScreenPos();
    ImGui::GetWindowDrawList()->AddLine(ImVec2(at.x, at.y), ImVec2(at.x + ImGui::GetContentRegionAvail().x, at.y),
                                        Ui::Color::CardBorder, 1.0f);
    ImGui::Dummy(ImVec2(0, 6.0f * scale));
}

void Footer(State& s) {
    const bool pad = s.pad;
    const float gap = 24.0f * Ui::Scale();
    Ui::KeyHint(pad ? "A" : "Enter", "Select");
    if (s.screen != Screen::Home) {
        ImGui::SameLine(0, gap);
        Ui::KeyHint(pad ? "B" : "Esc", "Back");
    }
    ImGui::SameLine(0, gap);
    Ui::KeyHint(pad ? "LB/RB" : "Ctrl+Tab", "Screens");
    if (!pad) {
        ImGui::SameLine(0, gap);
        Ui::KeyHint("F11", "Full screen");
    }
    // work in the background, on the right
    std::string busy;
    if (s.probing) {
        busy = "Looking for Python";
    } else if (s.runner.Busy()) {
        busy = s.runner.What().empty() ? "Working" : s.runner.What();
    } else if (s.rooms_loading) {
        busy = "Asking the lobby";
    }
    if (!busy.empty()) {
        Ui::PushFont(0.85f);
        const float w = ImGui::CalcTextSize(busy.c_str()).x + ImGui::GetTextLineHeight() * 1.6f;
        Ui::SameLineRight(w);
        Ui::Spinner();
        ImGui::SameLine();
        ImGui::TextColored(ImGui::ColorConvertU32ToFloat4(Ui::Color::Dim), "%s", busy.c_str());
        Ui::PopFont();
    }
}

void DrawJob(State& s) {
    Job& job = s.job;
    if (!ImGui::IsPopupOpen("##job")) {
        ImGui::OpenPopup("##job");
    }
    const ImVec2 display = ImGui::GetIO().DisplaySize;
    const bool big = job.kind == Job::Kind::Build;
    ImGui::SetNextWindowPos(ImVec2(display.x * 0.5f, display.y * 0.5f), ImGuiCond_Always, ImVec2(0.5f, 0.5f));
    ImGui::SetNextWindowSize(big ? ImVec2(std::min(display.x * 0.9f, 1100.0f * Ui::Scale()), display.y * 0.8f)
                                 : ImVec2(std::min(display.x * 0.9f, 720.0f * Ui::Scale()), 0.0f),
                             ImGuiCond_Always);
    if (!ImGui::BeginPopupModal("##job", nullptr,
                                ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_NoMove |
                                    ImGuiWindowFlags_NoSavedSettings)) {
        return;
    }
    Ui::Heading(job.title);
    ImGui::Spacing();
    if (job.process && !job.finished) {
        // the output, read again only when there is more of it
        const std::string output = job.process->Output();
        if (output.size() != job.output_size) {
            job.output_size = output.size();
            job.lines = job.process->Lines();
        }
    }
    bool close = false;
    if (job.kind == Job::Kind::Build) {
        const auto progress = Tools::BuildProgress(job.lines);
        const float fraction = job.finished && job.ok ? 1.0f : progress.fraction;
        ImGui::ProgressBar(fraction, ImVec2(-FLT_MIN, ImGui::GetFrameHeight() * 0.8f), "");
        if (!job.finished) {
            Ui::Spinner();
            ImGui::SameLine();
            ImGui::TextUnformatted(progress.stage.c_str());
        } else if (job.process && job.process->WasStopped()) {
            Ui::StatusLine(Ui::Status::Warn, "Stopped", "Start it again whenever you like.");
        } else {
            Ui::StatusLine(job.ok ? Ui::Status::Ok : Ui::Status::Error,
                           job.ok ? "Done" : "It didn't work", job.summary);
        }
        ImGui::Spacing();
        const float buttons = ImGui::GetFrameHeightWithSpacing() * 1.4f;
        ImGui::PushStyleColor(ImGuiCol_ChildBg, IM_COL32(8, 10, 14, 255));
        if (ImGui::BeginChild("##output", ImVec2(0, -buttons), ImGuiChildFlags_Borders,
                              ImGuiWindowFlags_HorizontalScrollbar)) {
            Ui::PushFont(0.8f);
            ImGuiListClipper clipper;
            clipper.Begin(static_cast<int>(job.lines.size()));
            while (clipper.Step()) {
                for (int i = clipper.DisplayStart; i < clipper.DisplayEnd; ++i) {
                    ImGui::TextUnformatted(job.lines[static_cast<std::size_t>(i)].c_str());
                }
            }
            Ui::PopFont();
            // follow the output unless the player scrolled up to read
            if (ImGui::GetScrollY() < ImGui::GetScrollMaxY() - 2.0f && ImGui::IsWindowHovered() &&
                ImGui::GetIO().MouseWheel > 0.0f) {
                job.follow = false;
            } else if (ImGui::GetScrollY() >= ImGui::GetScrollMaxY() - 2.0f) {
                job.follow = true;
            }
            if (job.follow) {
                ImGui::SetScrollHereY(1.0f);
            }
        }
        ImGui::EndChild();
        ImGui::PopStyleColor();
    } else if (job.kind == Job::Kind::Download) {
        // mods.py's progress lines: a bar for the stage it is in (a download, unpacking)
        const auto progress = Tools::ModsProgress(job.lines);
        const float fraction = job.finished && job.ok ? 1.0f : progress.fraction;
        if (fraction >= 0.0f) {
            ImGui::ProgressBar(fraction, ImVec2(-FLT_MIN, ImGui::GetFrameHeight() * 0.8f), "");
        } else {
            ImGui::ProgressBar(-0.3f * static_cast<float>(ImGui::GetTime()),
                               ImVec2(-FLT_MIN, ImGui::GetFrameHeight() * 0.8f), "");
        }
        if (!job.finished) {
            Ui::Spinner();
            ImGui::SameLine();
            ImGui::TextUnformatted(progress.stage.c_str());
        } else if (job.process && job.process->WasStopped()) {
            Ui::StatusLine(Ui::Status::Warn, "Stopped", "It goes on from where it stopped next time.");
        } else {
            Ui::StatusLine(job.ok ? Ui::Status::Ok : Ui::Status::Error, job.ok ? "Done" : "It didn't work",
                           job.summary);
        }
    } else if (job.kind == Job::Kind::Install) {
        const float progress = job.install ? job.install->Progress() : 1.0f;
        ImGui::ProgressBar(progress, ImVec2(-FLT_MIN, ImGui::GetFrameHeight() * 0.8f), "");
        if (!job.finished) {
            ImGui::Text("%d%%", static_cast<int>(progress * 100.0f));
        } else {
            Ui::StatusLine(job.ok ? Ui::Status::Ok : Ui::Status::Error,
                           job.ok ? "Installed" : "It didn't work", job.summary);
        }
    } else {
        if (!job.finished) {
            Ui::Spinner();
            ImGui::SameLine();
            ImGui::TextUnformatted(job.lines.empty() ? "Working..." : job.lines.back().c_str());
        } else {
            Ui::StatusLine(job.ok ? Ui::Status::Ok : Ui::Status::Error, job.ok ? "Done" : "It didn't work",
                           job.summary);
        }
    }
    ImGui::Spacing();
    if (!job.finished) {
        if (job.quit_asked || job.cancel_asked) {
            // (asked first: an Enter or an A pressed twice on the Build button would stop it)
            ImGui::AlignTextToFramePadding();
            const std::string question =
                job.stop_question.empty() ? std::string("Stop the build? You can start it again later.")
                                          : job.stop_question;
            ImGui::TextUnformatted(job.quit_asked ? (question.substr(0, question.find('?')) + " and quit?" +
                                                     question.substr(question.find('?') + 1))
                                                        .c_str()
                                                  : question.c_str());
            ImGui::SameLine();
            if (!job.focus_set) {
                ImGui::SetKeyboardFocusHere();
                job.focus_set = true;
            }
            if (ImGui::Button(job.keep_label.empty() ? "Keep building" : job.keep_label.c_str())) {
                job.quit_asked = false;
                job.cancel_asked = false;
                job.focus_set = false;
            }
            ImGui::SameLine();
            if (Ui::AccentButton(job.quit_asked ? "Stop and quit"
                                 : job.stop_label.empty() ? "Stop it"
                                                          : job.stop_label.c_str())) {
                job.quit_after = job.quit_asked;
                job.process->Stop();
            }
        } else if (job.cancellable) {
            if (!job.focus_set) {
                // (again after "Keep building", whose button is gone)
                ImGui::SetKeyboardFocusHere();
                job.focus_set = true;
            }
            if (ImGui::Button("Cancel")) {
                job.cancel_asked = true;
                job.focus_set = false;
            }
        } else {
            ImGui::Dummy(ImVec2(0, ImGui::GetFrameHeight()));
        }
    } else if (job.close_when_done) {
        close = true;
    } else {
        if (!job.focus_set) {
            // the Close button wasn't there a moment ago: give it the focus
            ImGui::SetKeyboardFocusHere();
            job.focus_set = true;
        }
        if (Ui::AccentButton("Close")) {
            close = true;
        }
        if (ImGui::IsKeyPressed(ImGuiKey_Escape, false) || ImGui::IsKeyPressed(ImGuiKey_GamepadFaceRight, false)) {
            close = true;
        }
    }
    if (close) {
        ImGui::CloseCurrentPopup();
    }
    ImGui::EndPopup();
    if (close) {
        s.job = Job{};
        // the button that started it may be another now (Build → Build it again): the focus
        // goes to the screen's next step
        s.focus_first = true;
    }
}

void DrawDialog(State& s) {
    if (!s.dialog_open) {
        ImGui::OpenPopup("##dialog");
        s.dialog_open = true;
    }
    Dialog& d = *s.dialog;
    const ImVec2 display = ImGui::GetIO().DisplaySize;
    ImGui::SetNextWindowPos(ImVec2(display.x * 0.5f, display.y * 0.5f), ImGuiCond_Always, ImVec2(0.5f, 0.5f));
    ImGui::SetNextWindowSize(ImVec2(std::min(display.x * 0.9f, 780.0f * Ui::Scale()), 0.0f), ImGuiCond_Always);
    if (!ImGui::BeginPopupModal("##dialog", nullptr,
                                ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_NoMove |
                                    ImGuiWindowFlags_NoSavedSettings)) {
        // closed from elsewhere
        s.dialog.reset();
        s.dialog_open = false;
        return;
    }
    Ui::Heading(d.title);
    ImGui::Spacing();
    if (!d.text.empty()) {
        Ui::TextColored(Ui::Color::Text, d.text);
    }
    if (!d.details.empty()) {
        ImGui::Spacing();
        const float line = ImGui::GetTextLineHeightWithSpacing();
        const float height = std::min(display.y * 0.35f, line * static_cast<float>(d.details.size()) +
                                                             ImGui::GetStyle().WindowPadding.y * 2);
        ImGui::PushStyleColor(ImGuiCol_ChildBg, IM_COL32(8, 10, 14, 255));
        if (ImGui::BeginChild("##details", ImVec2(0, height), ImGuiChildFlags_Borders)) {
            for (const auto& item : d.details) {
                Ui::TextDim(item);
            }
        }
        ImGui::EndChild();
        ImGui::PopStyleColor();
    }
    bool submit = false;
    if (d.input) {
        ImGui::Spacing();
        ImGui::SetNextItemWidth(-FLT_MIN);
        // typing right away with a keyboard; with a controller the field only has the focus (A
        // opens it, B still closes the dialog)
        if (ImGui::IsWindowAppearing() && !s.pad) {
            ImGui::SetKeyboardFocusHere();
        }
        ImGuiInputTextFlags flags = ImGuiInputTextFlags_EnterReturnsTrue;
        if (d.password) {
            flags |= ImGuiInputTextFlags_Password;
        }
        submit = Ui::InputText("##value", &d.value, flags, d.input_hint.c_str());
        if (ImGui::IsWindowAppearing() && s.pad) {
            ImGui::SetItemDefaultFocus();
        }
    }
    ImGui::Spacing();
    ImGui::Spacing();
    std::function<void()> action;
    bool close = false;
    for (std::size_t i = 0; i < d.buttons.size(); ++i) {
        const auto& button = d.buttons[i];
        if (i > 0) {
            ImGui::SameLine();
        }
        const bool pressed = button.accent ? Ui::AccentButton(button.label.c_str()) : ImGui::Button(button.label.c_str());
        if (button.accent && !d.input && ImGui::IsWindowAppearing()) {
            ImGui::SetItemDefaultFocus();
        }
        if (pressed) {
            close = true;
            action = button.action;
            submit = submit || (button.accent && d.input);
        }
    }
    if (submit && !close) {
        // Enter in the text field: the accent button
        close = true;
        for (const auto& button : d.buttons) {
            if (button.accent) {
                action = button.action;
            }
        }
    }
    if (!ImGui::GetIO().WantTextInput &&
        (ImGui::IsKeyPressed(ImGuiKey_Escape, false) || ImGui::IsKeyPressed(ImGuiKey_GamepadFaceRight, false))) {
        close = true;
        submit = false;
        action = nullptr;
    }
    if (close) {
        ImGui::CloseCurrentPopup();
    }
    ImGui::EndPopup();
    if (close) {
        auto on_input = submit && d.input ? std::move(d.on_input) : nullptr;
        const std::string value = d.value;
        s.dialog.reset();
        s.dialog_open = false;
        // after the reset: these may show another dialog
        if (on_input) {
            on_input(value);
        }
        if (action) {
            action();
        }
    }
}

/// The launcher's own keys (seen as SDL events): another screen, back to Home, and Play from
/// Home when no item shows the keyboard focus (after the mouse). Not while a window or a text
/// field has them.
void GlobalKeys(State& s) {
    const int step = std::exchange(s.screen_step, 0);
    const bool back = std::exchange(s.back_pressed, false);
    const bool accept = std::exchange(s.accept_pressed, false);
    if (s.modal_was_open || s.text_was_active || ImGui::IsPopupOpen("", ImGuiPopupFlags_AnyPopupId) ||
        ImGui::GetIO().WantTextInput) {
        return;
    }
    if (step != 0) {
        s.GoTo(static_cast<Screen>((static_cast<int>(s.screen) + step + SCREEN_COUNT) % SCREEN_COUNT));
    } else if (back && s.screen != Screen::Home) {
        s.GoTo(Screen::Home);
    } else if (accept && s.screen == Screen::Home && s.cursor_was_hidden) {
        s.Play();
    }
}

/// The SDL events the launcher looks at itself; false when Dear ImGui shouldn't see it.
bool OwnKeys(State& s, const SDL_Event& event) {
    // which hints to show: the controller's or the keyboard's
    if (event.type == SDL_CONTROLLERBUTTONDOWN) {
        s.pad = true;
    } else if (event.type == SDL_KEYDOWN || event.type == SDL_MOUSEBUTTONDOWN || event.type == SDL_TEXTINPUT) {
        s.pad = false;
    }
    if (event.type == SDL_KEYDOWN && event.key.repeat == 0) {
        const auto key = event.key.keysym.scancode;
        const auto mod = event.key.keysym.mod;
        if (key == SDL_SCANCODE_F11 ||
            ((key == SDL_SCANCODE_RETURN || key == SDL_SCANCODE_KP_ENTER) && (mod & KMOD_ALT))) {
            SetFullscreen(!g_fullscreen);
            return false;  // not an Enter for the focused item
        }
        if (key == SDL_SCANCODE_TAB && (mod & KMOD_CTRL)) {
            s.screen_step = (mod & KMOD_SHIFT) ? -1 : 1;
            return false;  // not Dear ImGui's tabbing
        }
        if (key == SDL_SCANCODE_ESCAPE) {
            s.back_pressed = true;
        } else if (key == SDL_SCANCODE_RETURN || key == SDL_SCANCODE_KP_ENTER) {
            s.accept_pressed = true;
        }
    } else if (event.type == SDL_CONTROLLERBUTTONDOWN) {
        switch (event.cbutton.button) {
        case SDL_CONTROLLER_BUTTON_LEFTSHOULDER:
            s.screen_step = -1;
            break;
        case SDL_CONTROLLER_BUTTON_RIGHTSHOULDER:
            s.screen_step = 1;
            break;
        case SDL_CONTROLLER_BUTTON_B:
            s.back_pressed = true;
            break;
        case SDL_CONTROLLER_BUTTON_A:
            s.accept_pressed = true;
            break;
        default:
            break;
        }
    }
    return true;
}

void Frame(State& s) {
    ImGuiIO& io = ImGui::GetIO();
    Ui::Background(ImGui::GetBackgroundDrawList(), io.DisplaySize);
    ImGui::SetNextWindowPos(ImVec2(0, 0));
    ImGui::SetNextWindowSize(io.DisplaySize);
    ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(32.0f * Ui::Scale(), 18.0f * Ui::Scale()));
    ImGui::Begin("##launcher", nullptr,
                 ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_NoMove | ImGuiWindowFlags_NoSavedSettings |
                     ImGuiWindowFlags_NoBringToFrontOnFocus | ImGuiWindowFlags_NoBackground);
    ImGui::PopStyleVar();
    TopBar(s);
    const float footer = ImGui::GetFrameHeightWithSpacing() + 6.0f * Ui::Scale();
    if (ImGui::BeginChild("##content", ImVec2(0, -footer), ImGuiChildFlags_NavFlattened)) {
        switch (s.screen) {
        case Screen::Home:
            DrawHome(s);
            break;
        case Screen::Setup:
            DrawSetup(s);
            break;
        case Screen::Mods:
            DrawMods(s);
            break;
        case Screen::Online:
            DrawOnline(s);
            break;
        case Screen::Settings:
            DrawSettings(s);
            break;
        }
    }
    ImGui::EndChild();
    Footer(s);
    if (s.picking || ImGui::IsPopupOpen("##picking")) {
        DrawPicking(s);
    } else if (s.job.Active()) {
        DrawJob(s);
    } else if (s.dialog) {
        DrawDialog(s);
    }
    GlobalKeys(s);
    s.modal_was_open = ImGui::IsPopupOpen("", ImGuiPopupFlags_AnyPopupId);
    ImGui::End();
}

/// The player closes the launcher: during a build the job's window asks first, an install
/// finishes first.
void RequestQuit(State& s) {
    if (s.picking) {
        return;  // the system's picker first
    }
    if (s.job.Active() && !s.job.finished) {
        if (s.job.cancellable && !s.job.quit_asked) {
            s.job.quit_asked = true;
            s.job.focus_set = false;
        }
        return;
    }
    s.result = Result::Quit;
    s.done = true;
}

} // namespace

// ── State ────────────────────────────────────────────────────────────────────────────────────

const char* ScreenName(Screen screen) {
    switch (screen) {
    case Screen::Home:
        return "Home";
    case Screen::Setup:
        return "Setup";
    case Screen::Mods:
        return "Mods";
    case Screen::Online:
        return "Online";
    case Screen::Settings:
        return "Settings";
    }
    return "";
}

void Runner::Queue(std::vector<std::string> args, std::string what, Done done) {
    queue.push_back({std::move(args), std::move(what), std::move(done)});
    Pump();
}

void Runner::Pump() {
    if (process && !process->Running()) {
        const int code = process->ExitCode();
        const std::string output = process->Output();
        auto done = std::move(current_done);
        process.reset();
        current_what.clear();
        if (done) {
            done(code, output);
        }
    }
    while (!process && !queue.empty()) {
        Item item = std::move(queue.front());
        queue.pop_front();
        auto next = std::make_unique<Process>();
        std::string error;
        if (!next->Start(item.args, error)) {
            LOG_ERROR(Frontend, "launcher: {} didn't start: {}", item.args.front(), error);
            if (item.done) {
                item.done(-1, error);
            }
            continue;
        }
        process = std::move(next);
        current_done = std::move(item.done);
        current_what = std::move(item.what);
    }
}

void State::Change(const std::function<void(App::Config&)>& edit) {
    edit(config);
    edit(saved);
    App::Save(saved);
}

void State::GoTo(Screen next) {
    if (screen != next) {
        screen = next;
        focus_first = true;
    }
}

void State::Show(Dialog d) {
    dialog = std::move(d);
    dialog_open = false;
}

void State::Message(const std::string& title, const std::string& text) {
    Dialog d;
    d.title = title;
    d.text = text;
    d.buttons = {{"OK", nullptr, true}};
    Show(std::move(d));
}

void State::Recheck() {
    game = Checks::CheckGame(config.game);
    update = Checks::CheckUpdate();
    library_exists = FileUtil::Exists(Checks::LibraryPath());
    if (!library_exists) {
        code = {Checks::Code::State::Missing, ""};
    } else if (code.state == Checks::Code::State::Missing) {
        code = {};
    }
}

void State::CheckCode() {
    if (!library_exists) {
        code = {Checks::Code::State::Missing, ""};
        return;
    }
    if (game.state != Checks::Game::State::Ok) {
        code = {};
        return;
    }
    code.state = Checks::Code::State::Checking;
    runner.Queue({SelfPath(), "--user", Tools::UserDir(), "--check-code", config.game},
                 "Checking the code library", [this](int exit_code, const std::string& output) {
                     code = Checks::CodeFromCheck(exit_code, output);
                     LOG_INFO(Frontend, "launcher: code library check {}: {}", exit_code,
                              code.notice.empty() ? "fine" : code.notice);
                 });
}

void State::ProbeTools() {
    if (probing) {
        return;
    }
    probing = true;
    probe = std::async(std::launch::async, [] {
        return std::make_pair(Tools::FindToolsDir(), Tools::FindPython());
    });
}

bool State::SetupComplete() const {
    return game.state == Checks::Game::State::Ok && update.state == Checks::Update::State::Ok &&
           library_exists;
}

void State::StartBuild() {
    if (job.Active()) {
        return;
    }
    if (update.state != Checks::Update::State::Ok) {
        Message("The update first",
                "The code library is built from the v1.3.0 update installed in Azahar's virtual SD "
                "card: install the update first (Setup, step 2).");
        return;
    }
    if (probing) {
        Message("One moment", "The launcher is still looking for Python. Try again in a moment.");
        return;
    }
    if (tools_dir.empty()) {
        Dialog d;
        d.title = "The port's tools aren't here";
        d.text = "Building the code library needs the port's tools, the mh4u-tools folder of the "
                 "port's package. Keep it next to the app (or set MH4U_TOOLS to its tools folder). "
                 "Looked in:";
        d.details = Tools::ToolsCandidates();
        d.buttons = {{"OK", nullptr, true}};
        Show(std::move(d));
        return;
    }
    if (python.command.empty()) {
        Dialog d;
        d.title = "Python is needed";
        d.text = "Building the code library runs the port's tools, which need Python 3.10 or newer. " +
                 Tools::PythonHint(python);
        d.buttons = {{"Check again", [this] { ProbeTools(); }, true}, {"Close", nullptr, false}};
        Show(std::move(d));
        return;
    }
    auto process = std::make_unique<Process>();
    const auto args = Tools::BuildCommand(python, tools_dir, update.content);
    std::string error;
    if (!process->Start(args, error, /*low_priority=*/true)) {
        Message("The build didn't start", error);
        return;
    }
    LOG_INFO(Frontend, "launcher: building the code library from {}", update.content);
    job = Job{};
    job.kind = Job::Kind::Build;
    job.title = "Building the code library";
    job.cancellable = true;
    job.process = std::move(process);
    job.on_done = [this](int exit_code, const std::string&) {
        if (exit_code == 0) {
            job.summary = "The code library is built from your update and installed. The game runs "
                          "on it from now on.";
        }
        LOG_INFO(Frontend, "launcher: build finished with status {}", exit_code);
        Recheck();
        CheckCode();
        if (ToolsReady()) {
            RefreshMods(*this);
        }
    };
}

void State::ChooseAndInstallUpdate() {
    if (job.Active()) {
        return;
    }
    PickPath(*this, false, "Your decrypted MH4U v1.3.0 update (.cia)", "CIA files", {"cia"},
             [this](const std::string& path) {
                 if (const auto problem = Checks::CheckUpdateCia(path); !problem.empty()) {
                     Message("This isn't the update", problem);
                     return;
                 }
                 LOG_INFO(Frontend, "launcher: installing {}", path);
                 job = Job{};
                 job.kind = Job::Kind::Install;
                 job.title = "Installing the v1.3.0 update";
                 job.install = std::make_unique<Checks::Install>();
                 job.install->Start(path);
                 job.on_done = [this](int exit_code, const std::string&) {
                     if (exit_code == 0) {
                         job.summary = "The update is in Azahar's virtual SD card.";
                     }
                     Recheck();
                     CheckCode();
                 };
             });
}

void State::ChooseGame() {
    PickPath(*this, false, "Your decrypted Monster Hunter 4 Ultimate (.3ds, .cci or .cxi)",
             "Monster Hunter 4 Ultimate", {"3ds", "cci", "cxi", "app"}, [this](const std::string& path) {
                 const auto checked = Checks::CheckGame(path);
                 if (checked.state != Checks::Game::State::Ok) {
                     Message("This file won't do", Checks::Describe(checked));
                     return;
                 }
                 Change([&](App::Config& c) { c.game = path; });
                 LOG_INFO(Frontend, "launcher: game {}", path);
                 Recheck();
                 CheckCode();
             });
}

void State::Pick(bool folder, const std::string& title, const std::string& kind,
                 const std::vector<std::string>& extensions, std::function<void(const std::string&)> then) {
    PickPath(*this, folder, title, kind, extensions, std::move(then));
}

void State::Play(int stage) {
    // (a finished job's on_done may go on to play)
    if ((job.Active() && !job.finished) || dialog) {
        return;
    }
    if (stage <= 0) {
        if (game.state != Checks::Game::State::Ok) {
            GoTo(Screen::Setup);
            Message("The game first", Checks::Describe(game));
            return;
        }
        stage = 1;
    }
    if (stage == 1) {
        // the tools and the mod library first, when Play comes right at the start
        if (probing || (ToolsReady() && !mods.loaded && mods.error.empty())) {
            play_waiting = 1;
            return;
        }
        if (ToolsReady() && mods.loaded && !mods.deploy_state.empty() && mods.deploy_state != "up_to_date") {
            // (a "play anyway" to the rebuild question needn't be asked again as stage 2)
            DeployMods(*this, false, [this](bool without_code) { Play(without_code ? 3 : 2); });
            return;
        }
        stage = 2;
    }
    if (stage == 2) {
        // the code library's check first (it runs again after the mods were put in place)
        if (code.state == Checks::Code::State::Checking) {
            play_waiting = 2;
            return;
        }
        constexpr const char* WITHOUT =
            " The game then runs on Azahar's own CPU emulation, without the port's options (frame rate, "
            "widescreen, the touch screen overlay, net sync).";
        std::string problem;
        if (update.state != Checks::Update::State::Ok) {
            problem = std::string("The v1.3.0 update isn't installed in Azahar's virtual SD card.") + WITHOUT;
        } else if (!library_exists) {
            problem = std::string("The code library isn't built yet.") + WITHOUT;
        } else if (code.state == Checks::Code::State::NotUsed) {
            problem = code.notice;  // (it says what happens)
        }
        if (!problem.empty()) {
            Dialog d;
            d.title = "Play without the port's code?";
            d.text = problem;
            d.buttons = {{"Set it up", [this] { GoTo(Screen::Setup); }, true},
                         {"Play anyway", [this] { Play(3); }, false},
                         {"Back", nullptr, false}};
            Show(std::move(d));
            return;
        }
        if (code.state == Checks::Code::State::Partial) {
            Dialog d;
            d.title = "Some of the port's options won't work";
            d.text = code.notice + " Building it again takes a few minutes.";
            d.buttons = {{"Build it now", [this] { StartBuild(); }, true},
                         {"Play anyway", [this] { Play(3); }, false},
                         {"Back", nullptr, false}};
            Show(std::move(d));
            return;
        }
    }
    // (stage 3: the player was told about the code library and plays anyway)
    g_code_notice_seen = stage == 3;
    LOG_INFO(Frontend, "launcher: play {}", config.game);
    result = Result::Play;
    done = true;
}

std::string AppVersion() {
    std::string version = "MH4U PC port";
    if (std::string(MH4U_PORT_COMMIT).size() > 0) {
        version += fmt::format(" {}", MH4U_PORT_COMMIT);
    }
    return version + fmt::format(" \xC2\xB7 Azahar {}", Common::g_build_fullname);
}

std::string FileName(const std::string& path) {
    const auto slash = path.find_last_of("/\\");
    return slash == std::string::npos ? path : path.substr(slash + 1);
}

void FocusNext(State& state, bool enabled) {
    // a disabled item can't take it: the next frame it is enabled does (e.g. after the tools
    // probe), and neither can one behind a window on top (a build's: once it closes). The
    // request reaches items scrolled out of view too, and scrolls to them.
    if (state.focus_first && enabled &&
        !ImGui::IsPopupOpen("", ImGuiPopupFlags_AnyPopupId | ImGuiPopupFlags_AnyPopupLevel)) {
        state.focus_first = false;
        ImGui::SetKeyboardFocusHere();
        if (state.pad || GImGui->NavInputSource == ImGuiInputSource_Gamepad || GImGui->NavCursorVisible) {
            ImGui::SetNavCursorVisible(true);
        }
    }
}

std::string RoomSummary(const State& state) {
    const auto& c = state.config;
    if (c.host > 0) {
        const bool listed = c.room_public && !c.lobby.empty() && !c.token.empty();
        return fmt::format("Hosting \"{}\" on port {}, {}{}", c.room_name, c.host,
                           listed ? "listed in the lobby" : "unlisted",
                           c.password.empty() ? "" : ", with a password");
    }
    if (!c.join.empty()) {
        return fmt::format("Joining {}{}", state.room_label.empty() ? c.join : "\"" + state.room_label + "\" (" + c.join + ")",
                           c.password.empty() ? "" : ", with a password");
    }
    return "Solo: no room";
}

// ── the launcher's window ────────────────────────────────────────────────────────────────────

bool CodeNoticeSeen() {
    return g_code_notice_seen;
}

Result Run(App::Config& config, bool show) {
    g_code_notice_seen = false;
    State s(config);
    s.saved = App::Load();
    s.Recheck();
    if (!show && s.SetupComplete()) {
        LOG_INFO(Frontend, "launcher off, setup complete: starting the game");
        return Result::Play;
    }

    SDL_Rect usable{0, 0, 1280, 720};
    SDL_GetDisplayUsableBounds(0, &usable);
    const int width = std::min(1280, usable.w * 9 / 10);
    const int height = std::min(720, usable.h * 9 / 10);
    SDL_Window* window = SDL_CreateWindow("MH4U PC port", SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED,
                                          width, height,
                                          SDL_WINDOW_RESIZABLE | SDL_WINDOW_ALLOW_HIGHDPI | SDL_WINDOW_HIDDEN);
    if (!window) {
        LOG_ERROR(Frontend, "launcher: no window: {}", SDL_GetError());
        return Result::Unavailable;
    }
    App::Icon::Apply(window);
    const std::string old_quality = SDL_GetHint(SDL_HINT_RENDER_SCALE_QUALITY) ? SDL_GetHint(SDL_HINT_RENDER_SCALE_QUALITY) : "";
    SDL_SetHint(SDL_HINT_RENDER_SCALE_QUALITY, "linear");
    SDL_Renderer* renderer = SDL_CreateRenderer(window, -1, SDL_RENDERER_ACCELERATED | SDL_RENDERER_PRESENTVSYNC);
    if (!renderer) {
        LOG_WARNING(Frontend, "launcher: no accelerated renderer ({}), using SDL's software one", SDL_GetError());
        renderer = SDL_CreateRenderer(window, -1, SDL_RENDERER_SOFTWARE);
    }
    if (!renderer) {
        LOG_ERROR(Frontend, "launcher: no renderer: {}", SDL_GetError());
        SDL_DestroyWindow(window);
        return Result::Unavailable;
    }
    SDL_RendererInfo info{};
    SDL_GetRendererInfo(renderer, &info);
    LOG_INFO(Frontend, "launcher: SDL renderer {}", info.name ? info.name : "?");
    const bool vsync = (info.flags & SDL_RENDERER_PRESENTVSYNC) != 0;
    SDL_SetWindowMinimumSize(window, 640, 360);
    g_window = window;
    Pick::SetOwner(window);
    if (config.fullscreen) {
        SetFullscreen(true);
    }
    SDL_ShowWindow(window);
    SDL_RaiseWindow(window);

    IMGUI_CHECKVERSION();
    ImGuiContext* context = ImGui::CreateContext();
    ImGuiIO& io = ImGui::GetIO();
    io.IniFilename = nullptr;
    io.LogFilename = nullptr;
    io.ConfigFlags |= ImGuiConfigFlags_NavEnableKeyboard | ImGuiConfigFlags_NavEnableGamepad;
    // Ctrl+Tab switches screens here, and Alt is for Alt+Enter
    GImGui->ConfigNavWindowingKeyNext = 0;
    GImGui->ConfigNavWindowingKeyPrev = 0;
    GImGui->NavWindowingToggleKey = ImGuiKey_None;
    Ui::LoadFonts();
    ImGui_ImplSDL2_InitForSDLRenderer(window, renderer);
    ImGui_ImplSDLRenderer2_Init(renderer);

    s.wizard = !s.SetupComplete();
    s.screen = s.wizard ? Screen::Setup : Screen::Home;
    s.focus_first = true;
    s.ProbeTools();
    s.CheckCode();
    LOG_INFO(Frontend, "launcher: {}", s.wizard ? "the setup isn't complete" : "ready to play");

    Uint32 last_input = SDL_GetTicks();
    bool first_frame = true;
    while (!s.done) {
        const Uint32 frame_start = SDL_GetTicks();
        // Frames at the full rate only for a while after the player's input: a build's progress,
        // the spinners and the Play button's glow need few, and the launcher shouldn't take the
        // processor from the build (or draw at all while minimised).
        const bool working = (s.job.Active() && !s.job.finished) || s.runner.Busy() || s.probing ||
                             s.rooms_loading || s.play_waiting > 0 || s.picking.has_value();
        const bool hidden = (SDL_GetWindowFlags(window) & (SDL_WINDOW_MINIMIZED | SDL_WINDOW_HIDDEN)) != 0;
        int wait_ms = 0;
        if (hidden) {
            wait_ms = HIDDEN_WAIT_MS;
        } else if (frame_start - last_input > IDLE_AFTER_MS) {
            wait_ms = working ? WORKING_WAIT_MS : IDLE_WAIT_MS;
        }
        SDL_Event event;
        bool have_event = false;
        if (wait_ms > 0) {
            have_event = SDL_WaitEventTimeout(&event, wait_ms) != 0;
        } else {
            have_event = SDL_PollEvent(&event) != 0;
        }
        // (the state of the frame these events come after)
        s.cursor_was_hidden = !GImGui->NavCursorVisible;
        s.text_was_active = io.WantTextInput;
        while (have_event) {
            last_input = SDL_GetTicks();
            if (event.type == SDL_QUIT ||
                (event.type == SDL_WINDOWEVENT && event.window.event == SDL_WINDOWEVENT_CLOSE &&
                 event.window.windowID == SDL_GetWindowID(window))) {
                RequestQuit(s);
            }
            if (OwnKeys(s, event)) {
                ImGui_ImplSDL2_ProcessEvent(&event);
            }
            have_event = SDL_PollEvent(&event) != 0;
        }

        s.runner.Pump();
        PumpJob(s);
        PumpProbe(s);
        PumpRooms(s);
        PumpPick(s);
        if (s.play_waiting > 0) {
            // Play goes on once what it waits for is there (it waits again, or stops at a dialog)
            const int stage = s.play_waiting;
            s.play_waiting = 0;
            s.Play(stage);
        }
        if (hidden) {
            continue;
        }

        int w = 0, h = 0;
        SDL_GetWindowSize(window, &w, &h);
        Ui::UpdateScale(static_cast<float>(h));
        ImGui_ImplSDLRenderer2_NewFrame();
        ImGui_ImplSDL2_NewFrame();
        ImGui::NewFrame();
        if (first_frame) {
            // keyboard and controller players see where they are from the start
            ImGui::SetNavCursorVisible(true);
            first_frame = false;
        }
        Frame(s);
        ImGui::Render();
        SDL_SetRenderDrawColor(renderer, 12, 15, 21, 255);
        SDL_RenderClear(renderer);
        ImGui_ImplSDLRenderer2_RenderDrawData(ImGui::GetDrawData(), renderer);
        SDL_RenderPresent(renderer);
        if (!vsync) {
            const Uint32 spent = SDL_GetTicks() - frame_start;
            if (spent < 16) {
                SDL_Delay(16 - spent);
            }
        }
    }

    // a command still running (a check, mods.py) ends with the launcher
    s.runner = Runner{};
    ImGui_ImplSDLRenderer2_Shutdown();
    ImGui_ImplSDL2_Shutdown();
    ImGui::DestroyContext(context);
    SDL_StopTextInput();
    SDL_DestroyRenderer(renderer);
    SDL_DestroyWindow(window);
    g_window = nullptr;
    Pick::SetOwner(nullptr);
    SDL_SetHint(SDL_HINT_RENDER_SCALE_QUALITY, old_quality.empty() ? nullptr : old_quality.c_str());
    // the game's own window sets up OpenGL from SDL's defaults, as without the launcher
    SDL_GL_ResetAttributes();
    if (s.result == Result::Play) {
        // what the game should start with: the launcher's full-screen choice of the moment
        // doesn't change the setting
        LOG_INFO(Frontend, "launcher: room: {}", RoomSummary(s));
    }
    return s.result;
}

int CheckCode(const std::string& game) {
    auto& system = Core::System::GetInstance();
    const std::string line = Port::PrepareBackendFor(system, game);
    const std::string notice = Port::BackendNotice();
    std::printf("backend: %s\n", line.c_str());
    if (!notice.empty()) {
        std::printf("notice:\n%s\n", notice.c_str());
    }
    std::fflush(stdout);
    const bool recompiled = line.rfind("running recompiled code", 0) == 0;
    if (!recompiled) {
        return 2;
    }
    return notice.empty() ? 0 : 3;
}

} // namespace Launcher
