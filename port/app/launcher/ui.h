// The launcher's look: a dark theme, fonts that scale with the window's height (read well from
// 720p to 4K), and a few widgets of its own (the big Play button, status lines, cards, icons).
#pragma once

#include <initializer_list>
#include <string>
#include <imgui.h>

namespace Launcher::Ui {

namespace Color {
inline constexpr ImU32 Text = IM_COL32(232, 234, 237, 255);
inline constexpr ImU32 Dim = IM_COL32(150, 160, 175, 255);
inline constexpr ImU32 Accent = IM_COL32(232, 163, 61, 255);       // amber
inline constexpr ImU32 AccentHover = IM_COL32(245, 185, 90, 255);
inline constexpr ImU32 AccentDark = IM_COL32(200, 135, 40, 255);
inline constexpr ImU32 OnAccent = IM_COL32(30, 20, 8, 255);
inline constexpr ImU32 Ok = IM_COL32(76, 195, 138, 255);
inline constexpr ImU32 Warn = IM_COL32(232, 163, 61, 255);
inline constexpr ImU32 Error = IM_COL32(229, 83, 75, 255);
inline constexpr ImU32 Info = IM_COL32(88, 166, 255, 255);
inline constexpr ImU32 Card = IM_COL32(26, 32, 41, 235);
inline constexpr ImU32 CardBorder = IM_COL32(52, 62, 78, 255);
} // namespace Color

enum class Status { Ok, Warn, Error, Busy, Off };

/// Loads the fonts (a system sans-serif face when there is one, else Dear ImGui's own) into
/// the current context.
void LoadFonts();

/// The style for this frame's window height (call before ImGui::NewFrame()).
void UpdateScale(float window_height);

/// The current scale: 1 at 720 points of window height.
float Scale();

/// Base font size for this frame, before any PushFont.
float FontSize();

/// The bold face (the regular one if there is none).
ImFont* Bold();

/// Text in another size (a factor of the body text) and face; pop with PopFont().
void PushFont(float factor, bool bold = false);
void PopFont();

/// Text in a color, wrapped at the content's edge.
void TextColored(ImU32 color, const std::string& text);
void TextDim(const std::string& text);
void Heading(const std::string& text);

/// A status icon on the current line, as tall as the text.
void Icon(Status status);
/// An icon, then a label in bold and a detail after it, wrapped.
void StatusLine(Status status, const std::string& label, const std::string& detail);
/// A small padlock drawn at the cursor, as tall as the text.
void Lock(ImU32 color);

/// The big Play button; true when pressed. `ready` false draws it subdued.
bool PlayButton(const char* label, const ImVec2& size, bool ready);

/// A button in the accent color.
bool AccentButton(const char* label, const ImVec2& size = ImVec2(0, 0));

/// A tab of the top bar.
bool Tab(const char* label, bool selected);

/// A small square button with a drawn icon (a list row's actions).
enum class Glyph { Up, Down, Remove };
bool IconButton(const char* id, Glyph glyph);

/// A framed panel with padding; End with EndCard().
bool BeginCard(const char* id, const ImVec2& size, bool highlight = false);
void EndCard();

/// An on/off switch with a label; true when changed.
bool Toggle(const char* label, bool* value);

/// A turning circle for work in progress.
void Spinner();

/// Dear ImGui's InputText for a std::string (as misc/cpp/imgui_stdlib does).
bool InputText(const char* label, std::string* text, ImGuiInputTextFlags flags = 0,
               const char* hint = nullptr);

/// Keyboard or controller hints: a key cap with a label after it.
void KeyHint(const char* key, const char* action);

/// On the same line, the next `width` of items at the right edge of the content.
void SameLineRight(float width);

/// A table's header row as plain text (TableHeadersRow's headers would be stops for a
/// controller on its way to the rows).
void TableHeaders(std::initializer_list<const char*> names);

/// The window's background (a gradient with a few soft shapes).
void Background(ImDrawList* list, const ImVec2& size);

} // namespace Launcher::Ui
