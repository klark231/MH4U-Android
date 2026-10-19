#include "launcher/ui.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <vector>
#include <imgui_internal.h>

#include "common/file_util.h"
#include "common/logging/log.h"

namespace Launcher::Ui {

namespace {

constexpr float BASE_FONT = 20.0f;      // points of body text at 720 points of window height
constexpr float REFERENCE_HEIGHT = 720.0f;

ImFont* regular = nullptr;
ImFont* bold = nullptr;
float scale = 1.0f;
float applied = 0.0f;

std::vector<std::string> FontFiles(bool want_bold) {
    std::vector<std::string> files;
#if defined(_WIN32)
    const char* windir = std::getenv("WINDIR");
    const std::string fonts = std::string(windir ? windir : "C:\\Windows") + "\\Fonts\\";
    if (want_bold) {
        files = {fonts + "segoeuib.ttf", fonts + "arialbd.ttf"};
    } else {
        files = {fonts + "segoeui.ttf", fonts + "arial.ttf"};
    }
#elif defined(__APPLE__)
    if (want_bold) {
        files = {"/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/Library/Fonts/Arial Bold.ttf"};
    } else {
        files = {"/System/Library/Fonts/Supplemental/Arial.ttf", "/Library/Fonts/Arial.ttf",
                 "/System/Library/Fonts/SFNS.ttf"};
    }
#else
    // fontconfig knows the desktop's sans-serif face; then the usual places of common ones
    const std::string query = std::string("fc-match -f '%{file}' 'sans-serif:style=") +
                              (want_bold ? "Bold" : "Regular") + "' 2>/dev/null";
    if (FILE* pipe = popen(query.c_str(), "r")) {
        char buffer[1024];
        std::string out;
        while (std::fgets(buffer, sizeof(buffer), pipe)) {
            out += buffer;
        }
        pclose(pipe);
        if (!out.empty()) {
            files.push_back(out);
        }
    }
    const char* names[] = {want_bold ? "DejaVuSans-Bold.ttf" : "DejaVuSans.ttf",
                           want_bold ? "NotoSans-Bold.ttf" : "NotoSans-Regular.ttf",
                           want_bold ? "LiberationSans-Bold.ttf" : "LiberationSans-Regular.ttf"};
    for (const char* dir : {"/usr/share/fonts/truetype/dejavu/", "/usr/share/fonts/TTF/",
                            "/usr/share/fonts/dejavu/", "/usr/share/fonts/truetype/noto/",
                            "/usr/share/fonts/noto/", "/usr/share/fonts/google-noto/",
                            "/usr/share/fonts/truetype/liberation/", "/usr/share/fonts/liberation/"}) {
        for (const char* name : names) {
            files.push_back(std::string(dir) + name);
        }
    }
#endif
    return files;
}

ImFont* LoadFont(bool want_bold) {
    ImFontConfig config;
    config.Flags |= ImFontFlags_NoLoadError;
    config.OversampleH = 2;
    for (const auto& file : FontFiles(want_bold)) {
        const std::string lower = [&] {
            std::string s = file;
            std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return std::tolower(c); });
            return s;
        }();
        const bool usable = lower.ends_with(".ttf") || lower.ends_with(".otf") || lower.ends_with(".ttc");
        if (!usable || !FileUtil::Exists(file)) {
            continue;
        }
        if (ImFont* font = ImGui::GetIO().Fonts->AddFontFromFileTTF(file.c_str(), BASE_FONT, &config)) {
            LOG_INFO(Frontend, "launcher font: {}", file);
            return font;
        }
    }
    return nullptr;
}

ImGuiStyle BaseStyle() {
    ImGuiStyle s;
    ImGui::StyleColorsDark(&s);
    s.WindowPadding = ImVec2(24, 20);
    s.FramePadding = ImVec2(14, 8);
    s.ItemSpacing = ImVec2(12, 10);
    s.ItemInnerSpacing = ImVec2(8, 6);
    s.CellPadding = ImVec2(10, 6);
    s.IndentSpacing = 22;
    s.ScrollbarSize = 14;
    s.GrabMinSize = 18;
    s.WindowRounding = 12;
    s.ChildRounding = 12;
    s.FrameRounding = 8;
    s.PopupRounding = 12;
    s.ScrollbarRounding = 8;
    s.GrabRounding = 8;
    s.TabRounding = 8;
    s.WindowBorderSize = 0;
    s.ChildBorderSize = 1;
    s.PopupBorderSize = 1;
    s.FrameBorderSize = 0;
    s.WindowTitleAlign = ImVec2(0.5f, 0.5f);
    s.SelectableTextAlign = ImVec2(0.0f, 0.5f);
    s.DisplaySafeAreaPadding = ImVec2(0, 0);

    ImVec4* c = s.Colors;
    const auto rgba = [](int r, int g, int b, int a = 255) {
        return ImVec4(r / 255.0f, g / 255.0f, b / 255.0f, a / 255.0f);
    };
    c[ImGuiCol_Text] = rgba(232, 234, 237);
    c[ImGuiCol_TextDisabled] = rgba(110, 118, 130);
    c[ImGuiCol_WindowBg] = rgba(14, 17, 22);
    c[ImGuiCol_ChildBg] = rgba(0, 0, 0, 0);
    c[ImGuiCol_PopupBg] = rgba(22, 27, 35, 250);
    c[ImGuiCol_Border] = rgba(52, 62, 78);
    c[ImGuiCol_FrameBg] = rgba(34, 41, 52);
    c[ImGuiCol_FrameBgHovered] = rgba(44, 53, 67);
    c[ImGuiCol_FrameBgActive] = rgba(52, 62, 78);
    c[ImGuiCol_TitleBg] = rgba(22, 27, 35);
    c[ImGuiCol_TitleBgActive] = rgba(22, 27, 35);
    c[ImGuiCol_ScrollbarBg] = rgba(0, 0, 0, 0);
    c[ImGuiCol_ScrollbarGrab] = rgba(60, 70, 86);
    c[ImGuiCol_ScrollbarGrabHovered] = rgba(76, 88, 106);
    c[ImGuiCol_ScrollbarGrabActive] = rgba(92, 105, 125);
    c[ImGuiCol_CheckMark] = rgba(232, 163, 61);
    c[ImGuiCol_SliderGrab] = rgba(232, 163, 61);
    c[ImGuiCol_SliderGrabActive] = rgba(245, 185, 90);
    c[ImGuiCol_Button] = rgba(40, 48, 61);
    c[ImGuiCol_ButtonHovered] = rgba(54, 64, 80);
    c[ImGuiCol_ButtonActive] = rgba(66, 78, 96);
    c[ImGuiCol_Header] = rgba(40, 48, 61);
    c[ImGuiCol_HeaderHovered] = rgba(50, 60, 76);
    c[ImGuiCol_HeaderActive] = rgba(60, 72, 90);
    c[ImGuiCol_Separator] = rgba(52, 62, 78);
    c[ImGuiCol_TableHeaderBg] = rgba(30, 36, 46);
    c[ImGuiCol_TableBorderStrong] = rgba(52, 62, 78);
    c[ImGuiCol_TableBorderLight] = rgba(40, 48, 61);
    c[ImGuiCol_TableRowBg] = rgba(0, 0, 0, 0);
    c[ImGuiCol_TableRowBgAlt] = rgba(255, 255, 255, 8);
    c[ImGuiCol_TextSelectedBg] = rgba(232, 163, 61, 90);
    c[ImGuiCol_NavCursor] = rgba(245, 185, 90);
    c[ImGuiCol_ModalWindowDimBg] = rgba(5, 7, 10, 170);
    c[ImGuiCol_PlotHistogram] = rgba(232, 163, 61);
    return s;
}

/// A rectangle for an inline icon: as tall as the text, on the current line.
ImRect IconBox(float factor = 0.85f) {
    const float h = ImGui::GetTextLineHeight();
    const float d = h * factor;
    const ImVec2 at = ImGui::GetCursorScreenPos();
    ImGui::Dummy(ImVec2(d, h));
    const float top = at.y + (h - d) * 0.5f;
    return ImRect(ImVec2(at.x, top), ImVec2(at.x + d, top + d));
}

} // namespace

void LoadFonts() {
    ImGuiIO& io = ImGui::GetIO();
    regular = LoadFont(false);
    if (!regular) {
        ImFontConfig config;
        config.SizePixels = BASE_FONT;
        regular = io.Fonts->AddFontDefaultVector(&config);
        LOG_INFO(Frontend, "launcher font: Dear ImGui's own");
    }
    bold = LoadFont(true);
    if (!bold) {
        bold = regular;
    }
    io.FontDefault = regular;
}

void UpdateScale(float window_height) {
    scale = std::clamp(window_height / REFERENCE_HEIGHT, 0.6f, 4.0f);
    if (std::fabs(scale - applied) < 0.005f) {
        return;
    }
    applied = scale;
    ImGuiStyle& style = ImGui::GetStyle();
    style = BaseStyle();
    style.ScaleAllSizes(scale);
    style.FontSizeBase = std::round(BASE_FONT * scale);
}

float Scale() {
    return scale;
}

float FontSize() {
    return ImGui::GetStyle().FontSizeBase;
}

ImFont* Bold() {
    return bold;
}

void PushFont(float factor, bool want_bold) {
    ImGui::PushFont(want_bold ? bold : regular, std::round(FontSize() * factor));
}

void PopFont() {
    ImGui::PopFont();
}

void TextColored(ImU32 color, const std::string& text) {
    ImGui::PushStyleColor(ImGuiCol_Text, color);
    // wrapped at the content's edge, unless the caller wraps it earlier (beside a button)
    const bool wrap = GImGui->CurrentWindow->DC.TextWrapPos < 0.0f;
    if (wrap) {
        ImGui::PushTextWrapPos(0.0f);
    }
    ImGui::TextUnformatted(text.c_str());
    if (wrap) {
        ImGui::PopTextWrapPos();
    }
    ImGui::PopStyleColor();
}

void TextDim(const std::string& text) {
    TextColored(Color::Dim, text);
}

void Heading(const std::string& text) {
    PushFont(1.45f, true);
    ImGui::TextUnformatted(text.c_str());
    PopFont();
}

void Icon(Status status) {
    const ImRect box = IconBox();
    ImDrawList* dl = ImGui::GetWindowDrawList();
    const ImVec2 c = box.GetCenter();
    const float r = box.GetWidth() * 0.5f;
    const float t = std::max(1.5f, r * 0.22f);
    switch (status) {
    case Status::Ok:
        dl->AddCircleFilled(c, r, Color::Ok);
        dl->PathLineTo(ImVec2(c.x - r * 0.45f, c.y + r * 0.02f));
        dl->PathLineTo(ImVec2(c.x - r * 0.12f, c.y + r * 0.35f));
        dl->PathLineTo(ImVec2(c.x + r * 0.48f, c.y - r * 0.33f));
        dl->PathStroke(IM_COL32(12, 30, 20, 255), t);
        break;
    case Status::Warn:
        dl->AddCircleFilled(c, r, Color::Warn);
        dl->AddLine(ImVec2(c.x, c.y - r * 0.5f), ImVec2(c.x, c.y + r * 0.12f), Color::OnAccent, t);
        dl->AddCircleFilled(ImVec2(c.x, c.y + r * 0.45f), t * 0.6f, Color::OnAccent);
        break;
    case Status::Error:
        dl->AddCircleFilled(c, r, Color::Error);
        dl->AddLine(ImVec2(c.x - r * 0.38f, c.y - r * 0.38f), ImVec2(c.x + r * 0.38f, c.y + r * 0.38f),
                    IM_COL32(40, 8, 8, 255), t);
        dl->AddLine(ImVec2(c.x + r * 0.38f, c.y - r * 0.38f), ImVec2(c.x - r * 0.38f, c.y + r * 0.38f),
                    IM_COL32(40, 8, 8, 255), t);
        break;
    case Status::Busy: {
        const float a = static_cast<float>(ImGui::GetTime()) * 6.0f;
        dl->PathArcTo(c, r * 0.8f, a, a + 4.2f, 24);
        dl->PathStroke(Color::Info, t);
        break;
    }
    case Status::Off:
        dl->AddCircle(c, r * 0.9f, Color::Dim, 24, t * 0.8f);
        break;
    }
}

void StatusLine(Status status, const std::string& label, const std::string& detail) {
    Icon(status);
    ImGui::SameLine();
    ImGui::BeginGroup();
    ImGui::PushFont(bold, 0.0f);
    ImGui::TextUnformatted(label.c_str());
    ImGui::PopFont();
    if (!detail.empty()) {
        TextDim(detail);
    }
    ImGui::EndGroup();
}

void Lock(ImU32 color) {
    const ImRect box = IconBox(0.8f);
    ImDrawList* dl = ImGui::GetWindowDrawList();
    const float w = box.GetWidth();
    const float t = std::max(1.5f, w * 0.12f);
    const ImVec2 body_min(box.Min.x + w * 0.12f, box.Min.y + w * 0.45f);
    const ImVec2 body_max(box.Max.x - w * 0.12f, box.Max.y);
    dl->PathArcTo(ImVec2(box.GetCenter().x, box.Min.y + w * 0.38f), w * 0.24f, IM_PI, 2.0f * IM_PI, 12);
    dl->PathStroke(color, t);
    dl->AddLine(ImVec2(box.GetCenter().x - w * 0.24f, box.Min.y + w * 0.38f),
                ImVec2(box.GetCenter().x - w * 0.24f, body_min.y), color, t);
    dl->AddLine(ImVec2(box.GetCenter().x + w * 0.24f, box.Min.y + w * 0.38f),
                ImVec2(box.GetCenter().x + w * 0.24f, body_min.y), color, t);
    dl->AddRectFilled(body_min, body_max, color, w * 0.08f);
}

bool PlayButton(const char* label, const ImVec2& size_arg, bool ready) {
    const bool pressed = ImGui::InvisibleButton(label, size_arg, ImGuiButtonFlags_EnableNav);
    const bool hovered = ImGui::IsItemHovered();
    const bool held = ImGui::IsItemActive();
    const bool focused = ImGui::IsItemFocused() && GImGui->NavCursorVisible;
    ImDrawList* dl = ImGui::GetWindowDrawList();
    // (the size asked for may be relative: -FLT_MIN is the whole width)
    const ImVec2 at = ImGui::GetItemRectMin();
    const ImVec2 end = ImGui::GetItemRectMax();
    const ImVec2 size(end.x - at.x, end.y - at.y);
    const float rounding = ImGui::GetStyle().FrameRounding * 1.6f;
    ImU32 fill = ready ? Color::Accent : IM_COL32(64, 72, 86, 255);
    if (ready && (hovered || focused)) {
        fill = held ? Color::AccentDark : Color::AccentHover;
    }
    if (ready) {
        // a soft glow around it
        const float pulse = 0.5f + 0.5f * std::sin(static_cast<float>(ImGui::GetTime()) * 2.5f);
        for (int i = 3; i >= 1; --i) {
            const float grow = i * 4.0f * Scale();
            dl->AddRect(ImVec2(at.x - grow, at.y - grow), ImVec2(end.x + grow, end.y + grow),
                        IM_COL32(232, 163, 61, static_cast<int>((focused || hovered ? 34 : 18) * (0.6f + 0.4f * pulse) / i)),
                        rounding + grow, 3.0f * Scale());
        }
    }
    dl->AddRectFilled(at, end, fill, rounding);
    // a lighter band at the top for some depth
    dl->AddRectFilled(at, ImVec2(end.x, at.y + size.y * 0.45f), IM_COL32(255, 255, 255, ready ? 22 : 10),
                      rounding, ImDrawFlags_RoundCornersTop);
    if (focused) {
        dl->AddRect(ImVec2(at.x - 3, at.y - 3), ImVec2(end.x + 3, end.y + 3), IM_COL32(255, 240, 210, 255),
                    rounding + 3, 3.0f * Scale());
    }
    // the label, large and bold, without its ## part
    const char* label_end = ImGui::FindRenderedTextEnd(label);
    ImGui::PushFont(bold, std::round(FontSize() * 2.2f));
    const ImVec2 text = ImGui::CalcTextSize(label, label_end);
    dl->AddText(ImVec2(at.x + (size.x - text.x) * 0.5f, at.y + (size.y - text.y) * 0.5f),
                ready ? Color::OnAccent : IM_COL32(170, 178, 190, 255), label, label_end);
    ImGui::PopFont();
    return pressed;
}

bool AccentButton(const char* label, const ImVec2& size) {
    ImGui::PushStyleColor(ImGuiCol_Button, Color::Accent);
    ImGui::PushStyleColor(ImGuiCol_ButtonHovered, Color::AccentHover);
    ImGui::PushStyleColor(ImGuiCol_ButtonActive, Color::AccentDark);
    ImGui::PushStyleColor(ImGuiCol_Text, Color::OnAccent);
    ImGui::PushFont(bold, 0.0f);
    const bool pressed = ImGui::Button(label, size);
    ImGui::PopFont();
    ImGui::PopStyleColor(4);
    return pressed;
}

bool Tab(const char* label, bool selected) {
    const char* label_end = ImGui::FindRenderedTextEnd(label);
    ImGui::PushFont(bold, std::round(FontSize() * 1.1f));
    const ImVec2 text = ImGui::CalcTextSize(label, label_end);
    const ImVec2 pad(ImGui::GetStyle().FramePadding.x * 1.2f, ImGui::GetStyle().FramePadding.y);
    const ImVec2 size(text.x + pad.x * 2, text.y + pad.y * 2 + 4.0f * Scale());
    const ImVec2 at = ImGui::GetCursorScreenPos();
    const bool pressed = ImGui::InvisibleButton(label, size, ImGuiButtonFlags_EnableNav);
    const bool hovered = ImGui::IsItemHovered();
    ImDrawList* dl = ImGui::GetWindowDrawList();
    if (hovered && !selected) {
        dl->AddRectFilled(at, ImVec2(at.x + size.x, at.y + size.y), IM_COL32(255, 255, 255, 14),
                          ImGui::GetStyle().FrameRounding);
    }
    dl->AddText(ImVec2(at.x + pad.x, at.y + pad.y),
                selected ? Color::Accent : (hovered ? Color::Text : Color::Dim), label, label_end);
    if (selected) {
        const float h = 3.0f * Scale();
        dl->AddRectFilled(ImVec2(at.x + pad.x, at.y + size.y - h), ImVec2(at.x + size.x - pad.x, at.y + size.y),
                          Color::Accent, h);
    }
    ImGui::PopFont();
    return pressed;
}

namespace {
/// A card is a group in its window, not a child window: a child scrolled out of view skips its
/// items, which a focus request (and the controller's navigation) then can't reach.
struct CardFrame {
    ImVec2 start;
    float width;
    bool highlight;
    float work_max_x, content_max_x;
};
std::vector<CardFrame> cards;

ImVec2 CardPadding() {
    const ImGuiStyle& style = ImGui::GetStyle();
    return ImVec2(style.WindowPadding.x * 0.85f, style.WindowPadding.y * 0.85f);
}
} // namespace

bool IconButton(const char* id, Glyph glyph) {
    const float d = ImGui::GetFrameHeight();
    const bool pressed = ImGui::Button(id, ImVec2(d, d));
    const ImVec2 min = ImGui::GetItemRectMin();
    const ImVec2 c(min.x + d * 0.5f, min.y + d * 0.5f);
    const float r = d * 0.22f;
    // (in a disabled block the style's alpha dims it like the button)
    const ImU32 color = ImGui::GetColorU32(ImGuiCol_Text);
    ImDrawList* dl = ImGui::GetWindowDrawList();
    switch (glyph) {
    case Glyph::Up:
        dl->AddTriangleFilled(ImVec2(c.x, c.y - r), ImVec2(c.x + r * 1.1f, c.y + r * 0.7f),
                              ImVec2(c.x - r * 1.1f, c.y + r * 0.7f), color);
        break;
    case Glyph::Down:
        dl->AddTriangleFilled(ImVec2(c.x - r * 1.1f, c.y - r * 0.7f), ImVec2(c.x + r * 1.1f, c.y - r * 0.7f),
                              ImVec2(c.x, c.y + r), color);
        break;
    case Glyph::Remove: {
        const float t = std::max(1.5f, d * 0.07f);
        dl->AddLine(ImVec2(c.x - r, c.y - r), ImVec2(c.x + r, c.y + r), color, t);
        dl->AddLine(ImVec2(c.x + r, c.y - r), ImVec2(c.x - r, c.y + r), color, t);
        break;
    }
    }
    return pressed;
}

bool BeginCard(const char* id, const ImVec2& size, bool highlight) {
    ImGuiWindow* window = ImGui::GetCurrentWindow();
    const ImVec2 pad = CardPadding();
    CardFrame card;
    card.start = window->DC.CursorPos;
    const float avail = ImGui::GetContentRegionAvail().x;
    card.width = size.x > 0.0f ? size.x : std::max(pad.x * 4.0f, avail + size.x);
    card.highlight = highlight;
    card.work_max_x = window->WorkRect.Max.x;
    card.content_max_x = window->ContentRegionRect.Max.x;
    // the content in front, the card's background drawn behind it once its height is known
    window->DrawList->ChannelsSplit(2);
    window->DrawList->ChannelsSetCurrent(1);
    ImGui::PushID(id);
    ImGui::SetCursorScreenPos(ImVec2(card.start.x + pad.x, card.start.y + pad.y));
    ImGui::BeginGroup();
    // items that fill the width, wrapped text and right-aligned buttons end at the card's edge
    const float inner_max = card.start.x + card.width - pad.x;
    window->WorkRect.Max.x = std::min(window->WorkRect.Max.x, inner_max);
    window->ContentRegionRect.Max.x = std::min(window->ContentRegionRect.Max.x, inner_max);
    cards.push_back(card);
    return true;
}

void EndCard() {
    const CardFrame card = cards.back();
    cards.pop_back();
    ImGuiWindow* window = ImGui::GetCurrentWindow();
    ImGui::EndGroup();
    window->WorkRect.Max.x = card.work_max_x;
    window->ContentRegionRect.Max.x = card.content_max_x;
    ImGui::PopID();
    const ImVec2 pad = CardPadding();
    const ImVec2 end(card.start.x + card.width, ImGui::GetItemRectMax().y + pad.y);
    const float rounding = ImGui::GetStyle().ChildRounding;
    window->DrawList->ChannelsSetCurrent(0);
    window->DrawList->AddRectFilled(card.start, end, Color::Card, rounding);
    window->DrawList->AddRect(card.start, end, card.highlight ? Color::Accent : Color::CardBorder, rounding,
                              card.highlight ? 2.0f * Scale() : 1.0f);
    window->DrawList->ChannelsMerge();
    ImGui::SetCursorScreenPos(card.start);
    ImGui::Dummy(ImVec2(card.width, end.y - card.start.y));
}

bool Toggle(const char* label, bool* value) {
    const char* label_end = ImGui::FindRenderedTextEnd(label);
    const float h = ImGui::GetFrameHeight();
    const float w = h * 1.8f;
    const ImVec2 text = ImGui::CalcTextSize(label, label_end);
    const ImVec2 at = ImGui::GetCursorScreenPos();
    const float spacing = ImGui::GetStyle().ItemInnerSpacing.x * 1.5f;
    const bool pressed = ImGui::InvisibleButton(label, ImVec2(w + (text.x > 0 ? spacing + text.x : 0), h),
                                                ImGuiButtonFlags_EnableNav);
    if (pressed) {
        *value = !*value;
    }
    const bool hovered = ImGui::IsItemHovered();
    ImDrawList* dl = ImGui::GetWindowDrawList();
    const ImU32 track = *value ? (hovered ? Color::AccentHover : Color::Accent)
                               : (hovered ? IM_COL32(70, 80, 96, 255) : IM_COL32(52, 62, 78, 255));
    dl->AddRectFilled(at, ImVec2(at.x + w, at.y + h), track, h * 0.5f);
    const float r = h * 0.5f - 3.0f * Scale();
    const float x = *value ? at.x + w - h * 0.5f : at.x + h * 0.5f;
    dl->AddCircleFilled(ImVec2(x, at.y + h * 0.5f), r, *value ? Color::OnAccent : IM_COL32(200, 206, 214, 255));
    if (text.x > 0) {
        dl->AddText(ImVec2(at.x + w + spacing, at.y + (h - text.y) * 0.5f), Color::Text, label, label_end);
    }
    return pressed;
}

void Spinner() {
    const float h = ImGui::GetTextLineHeight();
    const ImVec2 at = ImGui::GetCursorScreenPos();
    ImGui::Dummy(ImVec2(h, h));
    const float a = static_cast<float>(ImGui::GetTime()) * 6.0f;
    ImDrawList* dl = ImGui::GetWindowDrawList();
    dl->PathArcTo(ImVec2(at.x + h * 0.5f, at.y + h * 0.5f), h * 0.38f, a, a + 4.2f, 24);
    dl->PathStroke(Color::Accent, std::max(2.0f, h * 0.12f));
}

namespace {
int ResizeCallback(ImGuiInputTextCallbackData* data) {
    if (data->EventFlag == ImGuiInputTextFlags_CallbackResize) {
        auto* text = static_cast<std::string*>(data->UserData);
        text->resize(static_cast<std::size_t>(data->BufTextLen));
        data->Buf = text->data();
    }
    return 0;
}
} // namespace

bool InputText(const char* label, std::string* text, ImGuiInputTextFlags flags, const char* hint) {
    flags |= ImGuiInputTextFlags_CallbackResize;
    if (hint) {
        return ImGui::InputTextWithHint(label, hint, text->data(), text->capacity() + 1, flags,
                                        ResizeCallback, text);
    }
    return ImGui::InputText(label, text->data(), text->capacity() + 1, flags, ResizeCallback, text);
}

void KeyHint(const char* key, const char* action) {
    PushFont(0.8f, true);
    const ImVec2 size = ImGui::CalcTextSize(key);
    const ImVec2 pad(6.0f * Scale(), 2.0f * Scale());
    const ImVec2 at = ImGui::GetCursorScreenPos();
    ImGui::Dummy(ImVec2(size.x + pad.x * 2, size.y + pad.y * 2));
    ImDrawList* dl = ImGui::GetWindowDrawList();
    dl->AddRectFilled(at, ImVec2(at.x + size.x + pad.x * 2, at.y + size.y + pad.y * 2),
                      IM_COL32(60, 70, 86, 255), 5.0f * Scale());
    dl->AddText(ImVec2(at.x + pad.x, at.y + pad.y), Color::Text, key);
    PopFont();
    ImGui::SameLine(0, 6.0f * Scale());
    PushFont(0.85f);
    ImGui::TextColored(ImGui::ColorConvertU32ToFloat4(Color::Dim), "%s", action);
    PopFont();
}

void TableHeaders(std::initializer_list<const char*> names) {
    ImGui::TableNextRow(ImGuiTableRowFlags_Headers);
    int column = 0;
    for (const char* name : names) {
        ImGui::TableSetColumnIndex(column++);
        ImGui::TableSetBgColor(ImGuiTableBgTarget_CellBg, ImGui::GetColorU32(ImGuiCol_TableHeaderBg));
        PushFont(0.85f, true);
        ImGui::TextColored(ImGui::ColorConvertU32ToFloat4(Color::Dim), "%s", name);
        PopFont();
    }
}

void SameLineRight(float width) {
    ImGui::SameLine();
    const float avail = ImGui::GetContentRegionAvail().x;
    if (avail > width) {
        ImGui::SetCursorPosX(ImGui::GetCursorPosX() + avail - width);
    }
}

void Background(ImDrawList* list, const ImVec2& size) {
    list->AddRectFilledMultiColor(ImVec2(0, 0), size, IM_COL32(12, 15, 21, 255), IM_COL32(12, 15, 21, 255),
                                  IM_COL32(21, 27, 37, 255), IM_COL32(21, 27, 37, 255));
    // two soft glows, warm at the top left and cool at the bottom right: radial gradients (a fan
    // of triangles from an opaque centre to a transparent rim)
    const auto glow = [&](ImVec2 c, float radius, ImU32 color) {
        constexpr int SEGMENTS = 64;
        const ImVec2 uv = list->_Data->TexUvWhitePixel;
        list->PrimReserve(SEGMENTS * 3, SEGMENTS + 1);
        const auto center = static_cast<ImDrawIdx>(list->_VtxCurrentIdx);
        list->PrimWriteVtx(c, uv, color);
        for (int i = 0; i < SEGMENTS; ++i) {
            const float a = static_cast<float>(i) / SEGMENTS * 2.0f * IM_PI;
            list->PrimWriteVtx(ImVec2(c.x + std::cos(a) * radius, c.y + std::sin(a) * radius), uv,
                               color & ~IM_COL32_A_MASK);
        }
        for (int i = 0; i < SEGMENTS; ++i) {
            list->PrimWriteIdx(center);
            list->PrimWriteIdx(static_cast<ImDrawIdx>(center + 1 + i));
            list->PrimWriteIdx(static_cast<ImDrawIdx>(center + 1 + (i + 1) % SEGMENTS));
        }
    };
    glow(ImVec2(size.x * 0.1f, size.y * 0.0f), size.y * 1.0f, IM_COL32(232, 163, 61, 30));
    glow(ImVec2(size.x * 0.95f, size.y * 1.05f), size.y * 0.9f, IM_COL32(70, 120, 200, 34));
    // a faint diagonal hatching for texture
    const float step = 28.0f * Scale();
    for (float x = -size.y; x < size.x; x += step) {
        list->AddLine(ImVec2(x, size.y), ImVec2(x + size.y, 0), IM_COL32(255, 255, 255, 4), 1.0f);
    }
}

} // namespace Launcher::Ui
