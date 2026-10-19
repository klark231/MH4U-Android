#include "ui/hint.h"

#include <cmath>
#include <sstream>
#include <imgui.h>

#include "common/settings.h"
#include "config.h"
#include "hooks.h"
#include "netsync.h"
#include "ui/menu.h"

namespace App::Ui {

namespace {

namespace NativeButton = Settings::NativeButton;

/// mh4u.ini's 3DS button names (port/src/hooks.cpp, PAD_BITS) -> the app's buttons.
int ButtonIndex(const std::string& name) {
    static constexpr std::pair<const char*, int> buttons[] = {
        {"A", NativeButton::A},         {"B", NativeButton::B},       {"X", NativeButton::X},
        {"Y", NativeButton::Y},         {"L", NativeButton::L},       {"R", NativeButton::R},
        {"ZL", NativeButton::ZL},       {"ZR", NativeButton::ZR},     {"START", NativeButton::Start},
        {"UP", NativeButton::Up},       {"DOWN", NativeButton::Down}, {"LEFT", NativeButton::Left},
        {"RIGHT", NativeButton::Right}};
    for (const auto& [n, index] : buttons) {
        if (name == n) {
            return index;
        }
    }
    return -1;
}

/// The app's key and controller button for one 3DS button ("C / RT"), each part "" when
/// unbound: `key`, `pad`.
std::pair<std::string, std::string> Names(const Config& config, const std::string& button) {
    const int index = ButtonIndex(button);
    if (index < 0) {
        return {};
    }
    // a button given full input parameters has no key of the app's
    const std::string key = config.button_params[index].empty() ? config.keys[index] : "";
    const std::string pad = ControllerInputName(Settings::values.current_input_profile.buttons[index]);
    return {key, pad};
}

/// "C / RT" for one button, "Q+C / LT+RT" for "ZL+ZR": the keys, then the controller's.
std::string ButtonsText(const Config& config, const std::string& buttons) {
    std::string keys, pads;
    std::stringstream in(buttons);
    std::string button;
    bool all_keys = true, all_pads = true;
    while (std::getline(in, button, '+')) {
        auto [key, pad] = Names(config, button);
        all_keys = all_keys && !key.empty();
        all_pads = all_pads && !pad.empty();
        keys += (keys.empty() ? "" : "+") + (key.empty() ? "?" : key);
        pads += (pads.empty() ? "" : "+") + (pad.empty() ? "?" : pad);
    }
    if (all_keys && all_pads) {
        return keys + " / " + pads;
    }
    if (all_keys) {
        return keys;
    }
    if (all_pads) {
        return pads;
    }
    return "the 3DS's " + buttons;
}

} // namespace

Hint CurrentHint(const Config& config) {
    Hint hint;
    if (!Port::Hooks::SkipOffered() || Port::Hooks::SkipHint() == 0) {
        return hint;
    }
    const std::string skip = Port::Hooks::SkipButton();
    if (!skip.empty()) {
        hint.skip = ButtonsText(config, skip);
    }
    const std::string end = Port::Hooks::SkipEndButtons();
    if (!end.empty()) {
        hint.end = ButtonsText(config, end);
        hint.hold = Port::Hooks::EndSkipHold();
        hint.requested = Port::Hooks::EndSkipRequested();
        if (hint.requested) {
            hint.waiting_for = Port::NetSync::EndSkipPending();
        }
    }
    return hint;
}

void DrawHint(const Hint& hint, bool flash) {
    const ImGuiIO& io = ImGui::GetIO();
    const ImVec4 amber(0.92f, 0.63f, 0.20f, 1.0f);
    const ImVec4 dim(0.75f, 0.75f, 0.75f, 1.0f);
    // a slow pulse of the frame (and a little of the background) when it flashes
    const float pulse = flash ? 0.5f + 0.5f * std::sin(static_cast<float>(ImGui::GetTime()) * 5.0f) : 0.0f;
    ImGui::SetNextWindowPos(ImVec2(io.DisplaySize.x * 0.5f, io.DisplaySize.y * 0.88f), ImGuiCond_Always,
                            ImVec2(0.5f, 0.5f));
    ImGui::SetNextWindowBgAlpha(0.55f + 0.2f * pulse);
    ImGui::PushStyleColor(ImGuiCol_Border, ImVec4(amber.x, amber.y, amber.z, flash ? 0.25f + 0.75f * pulse : 0.45f));
    ImGui::PushStyleVar(ImGuiStyleVar_WindowBorderSize, flash ? 2.0f : 1.0f);
    constexpr ImGuiWindowFlags flags = ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_NoInputs |
                                       ImGuiWindowFlags_NoNav | ImGuiWindowFlags_NoFocusOnAppearing |
                                       ImGuiWindowFlags_NoSavedSettings | ImGuiWindowFlags_AlwaysAutoResize;
    if (ImGui::Begin("##hint", nullptr, flags)) {
        if (!hint.skip.empty()) {
            ImGui::TextUnformatted("Skip: ");
            ImGui::SameLine(0.0f, 0.0f);
            ImGui::TextColored(amber, "%s", hint.skip.c_str());
        }
        if (!hint.end.empty()) {
            if (hint.requested) {
                if (hint.waiting_for.empty()) {
                    ImGui::TextColored(amber, "Ending the quest...");
                } else {
                    std::string names;
                    for (const std::string& name : hint.waiting_for) {
                        names += (names.empty() ? "" : ", ") + name;
                    }
                    ImGui::TextUnformatted("Waiting for ");
                    ImGui::SameLine(0.0f, 0.0f);
                    ImGui::TextColored(amber, "%s", names.c_str());
                    ImGui::SameLine(0.0f, 0.0f);
                    ImGui::TextUnformatted(" to end the quest");
                }
            } else {
                ImGui::TextColored(dim, "End the quest: hold ");
                ImGui::SameLine(0.0f, 0.0f);
                ImGui::TextColored(amber, "%s", hint.end.c_str());
                if (hint.hold > 0.0f) {
                    ImGui::PushStyleColor(ImGuiCol_PlotHistogram, amber);
                    ImGui::ProgressBar(hint.hold, ImVec2(-1.0f, 6.0f), "");
                    ImGui::PopStyleColor();
                }
            }
        }
    }
    ImGui::End();
    ImGui::PopStyleVar();
    ImGui::PopStyleColor();
}

} // namespace App::Ui
