#include "ui/keyboard.h"

#include <algorithm>
#include <cctype>
#include <cstring>
#include <SDL.h>
#include <fmt/format.h>
#include <imgui.h>

#include "common/logging/log.h"
#include "common/string_util.h"

namespace App::Ui {

namespace {

using Frontend::ButtonConfig;
using Frontend::ValidationError;

constexpr u32 NOTICE_MS = 5000;
// keys on the screen, for a controller (Shift gives the capitals)
constexpr const char* KEY_ROWS[] = {"1234567890-", "qwertyuiop'", "asdfghjkl.,", "zxcvbnm!?&/"};
constexpr int KEYS_PER_ROW = 11;

std::size_t Utf16Length(const std::string& s) {
    return Common::UTF8ToUTF16(s).size();
}

int Digits(const std::string& s) {
    return static_cast<int>(
        std::count_if(s.begin(), s.end(), [](unsigned char c) { return c >= '0' && c <= '9'; }));
}

/// `s` cut, a whole character at a time, to `max` UTF-16 units (the 3DS's measure).
void Truncate(std::string& s, std::size_t max) {
    while (!s.empty() && Utf16Length(s) > max) {
        std::size_t i = s.size() - 1;
        while (i > 0 && (static_cast<unsigned char>(s[i]) & 0xC0) == 0x80) {
            --i;
        }
        s.erase(i);
    }
}

std::string ErrorText(ValidationError error, const Frontend::KeyboardConfig& config) {
    switch (error) {
    case ValidationError::FixedLengthRequired:
        return fmt::format("The text must be {} characters long.", config.max_text_length);
    case ValidationError::MaxLengthExceeded:
        return fmt::format("At most {} characters.", config.max_text_length);
    case ValidationError::BlankInputNotAllowed:
        return "The text can't be only spaces.";
    case ValidationError::EmptyInputNotAllowed:
        return "The text can't be empty.";
    case ValidationError::MaxDigitsExceeded:
        return fmt::format("At most {} digits.", config.max_digits);
    case ValidationError::AtSignNotAllowed:
        return "@ isn't allowed here.";
    case ValidationError::PercentNotAllowed:
        return "% isn't allowed here.";
    case ValidationError::BackslashNotAllowed:
        return "\\ isn't allowed here.";
    case ValidationError::ProfanityNotAllowed:
        return "That word isn't allowed.";
    default:
        return "The game didn't take the text.";
    }
}

const char* ButtonText(const Frontend::KeyboardConfig& config, std::size_t i, const char* fallback) {
    return i < config.button_text.size() && !config.button_text[i].empty()
               ? config.button_text[i].c_str()
               : fallback;
}

} // namespace

void Keyboard::Execute(const Frontend::KeyboardConfig& config_) {
    SoftwareKeyboard::Execute(config_);
    std::scoped_lock lock{mutex};
    // asked again after the game turned the text down: it stays, to be put right
    if (error.empty()) {
        text.clear();
    }
    Truncate(text, config.max_text_length);
    active = true;
    appeared = true;
    shift = false;
    start_armed = false;
    notice.clear();
    LOG_INFO(Frontend, "software keyboard: \"{}\", up to {} characters", config.hint_text,
             config.max_text_length);
}

void Keyboard::ShowError(const std::string& message) {
    std::scoped_lock lock{mutex};
    LOG_WARNING(Frontend, "software keyboard: the game says \"{}\"", message);
    error = message.empty() ? "The game didn't take the text." : message;
    // shown on its own unless the game asks for the text again (Execute)
    notice = error;
    notice_until = SDL_GetTicks() + NOTICE_MS;
}

bool Keyboard::Active() const {
    std::scoped_lock lock{mutex};
    return active;
}

void Keyboard::Refocus() {
    std::scoped_lock lock{mutex};
    appeared = active;
    start_armed = false;
}

bool Keyboard::HasNotice() const {
    std::scoped_lock lock{mutex};
    return !notice.empty() && !SDL_TICKS_PASSED(SDL_GetTicks(), notice_until);
}

bool Keyboard::Accepts(unsigned c) const {
    if (c == '\n' || c == '\r') {
        return config.multiline_mode;
    }
    if (c < 0x20) {
        return false;
    }
    const auto& f = config.filters;
    if ((f.prevent_at && c == '@') || (f.prevent_percent && c == '%') ||
        (f.prevent_backslash && c == '\\')) {
        return false;
    }
    if (f.prevent_digit && c >= '0' && c <= '9' && Digits(text) >= config.max_digits) {
        return false;
    }
    return Utf16Length(text) + (c > 0xFFFF ? 2 : 1) <= config.max_text_length;
}

int Keyboard::TextCallback(ImGuiInputTextCallbackData* data) {
    const auto* self = static_cast<const Keyboard*>(data->UserData);
    if (data->EventFlag == ImGuiInputTextFlags_CallbackCharFilter) {
        return self->Accepts(data->EventChar) ? 0 : 1;
    }
    return 0;
}

void Keyboard::Insert(const std::string& characters) {
    for (const char16_t c : Common::UTF8ToUTF16(characters)) {
        if (Accepts(c)) {
            text += Common::UTF16ToUTF8(std::u16string(1, c));
        }
    }
    error.clear();
}

void Keyboard::Submit(u8 button) {
    const ValidationError result = Finalize(text, button);
    if (result != ValidationError::None) {
        error = ErrorText(result, config);
        return;
    }
    LOG_INFO(Frontend, "software keyboard: done ({} characters, button {})", Utf16Length(text),
             button);
    active = false;
    error.clear();
}

void Keyboard::Draw() {
    std::scoped_lock lock{mutex};
    if (!active) {
        return;
    }
    const ImGuiIO& io = ImGui::GetIO();
    const float em = ImGui::GetFontSize();
    ImGui::GetBackgroundDrawList()->AddRectFilled(ImVec2(0, 0), io.DisplaySize, IM_COL32(0, 0, 0, 100));
    const float width = std::min(io.DisplaySize.x * 0.94f, em * 30.0f);
    ImGui::SetNextWindowPos(ImVec2(io.DisplaySize.x * 0.5f, io.DisplaySize.y * 0.5f), ImGuiCond_Always,
                            ImVec2(0.5f, 0.5f));
    ImGui::SetNextWindowSize(ImVec2(width, 0.0f), ImGuiCond_Always);
    if (appeared) {
        ImGui::SetNextWindowFocus();
    }
    const std::string title = (config.hint_text.empty() ? std::string("Text") : config.hint_text) + "###swkbd";
    ImGui::Begin(title.c_str(), nullptr,
                 ImGuiWindowFlags_NoResize | ImGuiWindowFlags_NoMove | ImGuiWindowFlags_NoCollapse |
                     ImGuiWindowFlags_NoSavedSettings);

    // the text, with the game's limits on what can be typed (and pasted)
    const std::size_t capacity = std::min<std::size_t>(config.max_text_length * 4u + 1u, 16384u);
    buffer.assign(std::max<std::size_t>(capacity, 1), '\0');
    std::memcpy(buffer.data(), text.data(), std::min(text.size(), buffer.size() - 1));
    if (appeared) {
        ImGui::SetKeyboardFocusHere();
        appeared = false;
    }
    const ImGuiInputTextFlags flags =
        ImGuiInputTextFlags_CallbackCharFilter | ImGuiInputTextFlags_EnterReturnsTrue;
    bool enter = false;
    ImGui::SetNextItemWidth(-FLT_MIN);
    if (config.multiline_mode) {
        enter = ImGui::InputTextMultiline("##text", buffer.data(), buffer.size(), ImVec2(-FLT_MIN, em * 4.0f),
                                          flags | ImGuiInputTextFlags_CtrlEnterForNewLine, TextCallback, this);
    } else {
        enter = ImGui::InputTextWithHint("##text", config.hint_text.c_str(), buffer.data(), buffer.size(),
                                         flags, TextCallback, this);
    }
    if (std::string typed = buffer.data(); typed != text) {
        Truncate(typed, config.max_text_length);
        text = std::move(typed);
        error.clear();
    }
    ImGui::TextDisabled("%zu / %u", Utf16Length(text), static_cast<unsigned>(config.max_text_length));
    if (!error.empty()) {
        ImGui::SameLine();
        ImGui::TextColored(ImVec4(1.0f, 0.45f, 0.35f, 1.0f), "%s", error.c_str());
    }

    // keys on the screen
    ImGui::Spacing();
    const ImGuiStyle& style = ImGui::GetStyle();
    const float key = (ImGui::GetContentRegionAvail().x - style.ItemSpacing.x * (KEYS_PER_ROW - 1)) / KEYS_PER_ROW;
    for (const char* row : KEY_ROWS) {
        for (int i = 0; row[i]; ++i) {
            char label[2] = {shift ? static_cast<char>(std::toupper(static_cast<unsigned char>(row[i]))) : row[i], 0};
            if (i > 0) {
                ImGui::SameLine();
            }
            ImGui::PushID(row + i);
            if (ImGui::Button(label, ImVec2(key, 0.0f))) {
                Insert(label);
            }
            ImGui::PopID();
        }
    }
    if (ImGui::Button(shift ? "abc" : "ABC", ImVec2(key * 2 + style.ItemSpacing.x, 0.0f))) {
        shift = !shift;
    }
    ImGui::SameLine();
    if (ImGui::Button("Space", ImVec2(key * 6 + style.ItemSpacing.x * 5, 0.0f))) {
        Insert(" ");
    }
    ImGui::SameLine();
    const auto backspace = [this] {
        Truncate(text, Utf16Length(text) > 0 ? Utf16Length(text) - 1 : 0);
        error.clear();
    };
    if (ImGui::Button("Delete", ImVec2(ImGui::GetContentRegionAvail().x, 0.0f))) {
        backspace();
    }

    // a controller's shortcuts: LB deletes, RB is a space (not while the text field takes the
    // keys: it has its own copy of the text then), Start is OK (below)
    const bool focused = ImGui::IsWindowFocused(ImGuiFocusedFlags_RootAndChildWindows);
    const bool editing = ImGui::IsAnyItemActive();
    if (focused && !editing && ImGui::IsKeyPressed(ImGuiKey_GamepadL1)) {
        backspace();
    }
    if (focused && !editing && ImGui::IsKeyPressed(ImGuiKey_GamepadR1)) {
        Insert(" ");
    }

    // the game's buttons, OK on the right
    ImGui::Spacing();
    ImGui::Separator();
    const u8 ok_id = static_cast<u8>(config.button_config);
    struct Choice {
        const char* label;
        u8 id;
    };
    std::vector<Choice> choices;
    switch (config.button_config) {
    case ButtonConfig::Triple:
        choices = {{ButtonText(config, 0, Frontend::SWKBD_BUTTON_CANCEL), 0},
                   {ButtonText(config, 1, Frontend::SWKBD_BUTTON_FORGOT), 1},
                   {ButtonText(config, 2, Frontend::SWKBD_BUTTON_OKAY), ok_id}};
        break;
    case ButtonConfig::Dual:
        choices = {{ButtonText(config, 0, Frontend::SWKBD_BUTTON_CANCEL), 0},
                   {ButtonText(config, 2, Frontend::SWKBD_BUTTON_OKAY), ok_id}};
        break;
    case ButtonConfig::Single:
        choices = {{ButtonText(config, 2, Frontend::SWKBD_BUTTON_OKAY), ok_id}};
        break;
    default: // none on a 3DS; something to finish with all the same
        choices = {{"Done", ok_id}};
        break;
    }
    const float button = std::max(em * 6.0f, (ImGui::GetContentRegionAvail().x - style.ItemSpacing.x * 2) / 3);
    ImGui::SetCursorPosX(ImGui::GetCursorPosX() + ImGui::GetContentRegionAvail().x -
                         button * choices.size() - style.ItemSpacing.x * (choices.size() - 1));
    for (std::size_t i = 0; i < choices.size(); ++i) {
        if (i > 0) {
            ImGui::SameLine();
        }
        if (ImGui::Button(fmt::format("{}##choice{}", choices[i].label, i).c_str(), ImVec2(button, 0.0f))) {
            Submit(choices[i].id);
        }
    }
    ImGui::PushTextWrapPos(0.0f);
    ImGui::TextDisabled("Enter or Start: %s   LB: delete   RB: space", choices.back().label);
    if (config.multiline_mode) {
        ImGui::TextDisabled("Ctrl+Enter: a new line");
    }
    ImGui::PopTextWrapPos();
    // Start is OK when it is let go, unless Back joined it (Back and Start together open the menu)
    if (ImGui::IsKeyPressed(ImGuiKey_GamepadStart, false)) {
        start_armed = focused;
    }
    if (ImGui::IsKeyDown(ImGuiKey_GamepadBack)) {
        start_armed = false;
    }
    const bool start = start_armed && ImGui::IsKeyReleased(ImGuiKey_GamepadStart);
    if (start) {
        start_armed = false;
    }
    if (enter || start) {
        Submit(ok_id);
    }
    ImGui::End();
}

void Keyboard::DrawNotice() {
    std::scoped_lock lock{mutex};
    const ImGuiIO& io = ImGui::GetIO();
    ImGui::SetNextWindowPos(ImVec2(io.DisplaySize.x * 0.5f, io.DisplaySize.y * 0.08f), ImGuiCond_Always,
                            ImVec2(0.5f, 0.0f));
    ImGui::SetNextWindowBgAlpha(0.85f);
    ImGui::Begin("##swkbd_notice", nullptr,
                 ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_AlwaysAutoResize | ImGuiWindowFlags_NoInputs |
                     ImGuiWindowFlags_NoSavedSettings | ImGuiWindowFlags_NoFocusOnAppearing |
                     ImGuiWindowFlags_NoNav);
    ImGui::TextUnformatted(notice.c_str());
    ImGui::End();
}

} // namespace App::Ui
