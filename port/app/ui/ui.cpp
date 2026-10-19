#include "ui/ui.h"

#include <algorithm>
#include <cmath>
#include <SDL.h>
#include <imgui.h>
#include <imgui_impl_sdl2.h>

#include "common/logging/log.h"
#include "config.h"
#include "core/core.h"
#include "hooks.h"
#include "input.h"
#include "input_common/keyboard.h"
#include "input_common/main.h"
#include "ui/fps_counter.h"
#include "ui/hint.h"
#include "ui/keyboard.h"
#include "ui/layer.h"
#include "ui/menu.h"
#include "window.h"

namespace App::Ui {

namespace {

std::atomic<Layer*> instance{nullptr};

constexpr u32 BACK_HELD = 1, START_HELD = 2;

/// The menu's look: dark, translucent, an amber accent; sizes for a 13-pixel font, scaled with
/// the font (UpdateScale).
void SetStyle(ImGuiStyle& s) {
    ImGui::StyleColorsDark(&s);
    s.WindowPadding = ImVec2(14, 12);
    s.FramePadding = ImVec2(8, 5);
    s.CellPadding = ImVec2(6, 4);
    s.ItemSpacing = ImVec2(10, 7);
    s.ItemInnerSpacing = ImVec2(7, 5);
    s.ScrollbarSize = 12;
    s.GrabMinSize = 12;
    s.WindowRounding = 8;
    s.ChildRounding = 6;
    s.FrameRounding = 5;
    s.PopupRounding = 6;
    s.GrabRounding = 4;
    s.TabRounding = 5;
    s.ScrollbarRounding = 6;
    s.WindowBorderSize = 1;
    s.PopupBorderSize = 1;
    s.WindowTitleAlign = ImVec2(0.5f, 0.5f);

    const ImVec4 amber(0.92f, 0.63f, 0.20f, 1.00f);
    const auto mix = [](float r, float g, float b, float a = 1.0f) { return ImVec4(r, g, b, a); };
    ImVec4* c = s.Colors;
    c[ImGuiCol_Text] = mix(0.93f, 0.92f, 0.89f);
    c[ImGuiCol_TextDisabled] = mix(0.58f, 0.56f, 0.52f);
    c[ImGuiCol_WindowBg] = mix(0.07f, 0.07f, 0.08f, 0.95f);
    c[ImGuiCol_ChildBg] = mix(0.0f, 0.0f, 0.0f, 0.0f);
    c[ImGuiCol_PopupBg] = mix(0.09f, 0.09f, 0.10f, 0.98f);
    c[ImGuiCol_Border] = mix(0.92f, 0.63f, 0.20f, 0.30f);
    c[ImGuiCol_FrameBg] = mix(0.16f, 0.16f, 0.17f);
    c[ImGuiCol_FrameBgHovered] = mix(0.27f, 0.22f, 0.15f);
    c[ImGuiCol_FrameBgActive] = mix(0.36f, 0.28f, 0.15f);
    c[ImGuiCol_TitleBg] = mix(0.10f, 0.09f, 0.08f);
    c[ImGuiCol_TitleBgActive] = mix(0.20f, 0.15f, 0.09f);
    c[ImGuiCol_CheckMark] = amber;
    c[ImGuiCol_SliderGrab] = amber;
    c[ImGuiCol_SliderGrabActive] = mix(1.00f, 0.76f, 0.36f);
    c[ImGuiCol_Button] = mix(0.21f, 0.19f, 0.17f);
    c[ImGuiCol_ButtonHovered] = mix(0.46f, 0.33f, 0.15f);
    c[ImGuiCol_ButtonActive] = mix(0.62f, 0.43f, 0.15f);
    c[ImGuiCol_Header] = mix(0.31f, 0.24f, 0.14f);
    c[ImGuiCol_HeaderHovered] = mix(0.46f, 0.33f, 0.15f);
    c[ImGuiCol_HeaderActive] = mix(0.62f, 0.43f, 0.15f);
    c[ImGuiCol_Separator] = mix(0.92f, 0.63f, 0.20f, 0.25f);
    c[ImGuiCol_Tab] = mix(0.15f, 0.14f, 0.13f);
    c[ImGuiCol_TabHovered] = mix(0.52f, 0.37f, 0.15f);
    c[ImGuiCol_TabSelected] = mix(0.40f, 0.29f, 0.13f);
    c[ImGuiCol_TabSelectedOverline] = amber;
    c[ImGuiCol_TableRowBgAlt] = mix(1.0f, 1.0f, 1.0f, 0.03f);
    c[ImGuiCol_TextSelectedBg] = mix(0.92f, 0.63f, 0.20f, 0.35f);
    c[ImGuiCol_NavCursor] = amber;
    c[ImGuiCol_ModalWindowDimBg] = mix(0.0f, 0.0f, 0.0f, 0.45f);
    c[ImGuiCol_SeparatorHovered] = mix(0.92f, 0.63f, 0.20f, 0.78f);
    c[ImGuiCol_SeparatorActive] = amber;
    c[ImGuiCol_ResizeGrip] = mix(0.92f, 0.63f, 0.20f, 0.20f);
    c[ImGuiCol_ResizeGripHovered] = mix(0.92f, 0.63f, 0.20f, 0.67f);
    c[ImGuiCol_ResizeGripActive] = mix(0.92f, 0.63f, 0.20f, 0.95f);
    c[ImGuiCol_TextLink] = amber;
    // the ones the dark style works out from its own (blue) colours, from these instead
    const auto lerp = [](const ImVec4& a, const ImVec4& b, float t) {
        return ImVec4(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t,
                      a.w + (b.w - a.w) * t);
    };
    c[ImGuiCol_CheckboxSelectedBg] = lerp(c[ImGuiCol_FrameBg], c[ImGuiCol_FrameBgHovered], 0.65f);
    c[ImGuiCol_TabDimmed] = lerp(c[ImGuiCol_Tab], c[ImGuiCol_TitleBg], 0.80f);
    c[ImGuiCol_TabDimmedSelected] = lerp(c[ImGuiCol_TabSelected], c[ImGuiCol_TitleBg], 0.40f);
}

} // namespace

// ── The draw-data copy ──

DrawCopy::~DrawCopy() {
    for (ImDrawList* list : lists) {
        IM_DELETE(list);
    }
}

void DrawCopy::CopyFrom(const ImDrawData& source) {
    // the lists are kept from frame to frame, so a frame like the last one allocates nothing
    while (lists.Size < source.CmdLists.Size) {
        lists.push_back(IM_NEW(ImDrawList)(ImGui::GetDrawListSharedData()));
    }
    data.Clear();
    for (int i = 0; i < source.CmdLists.Size; ++i) {
        const ImDrawList* from = source.CmdLists[i];
        ImDrawList* to = lists[i];
        to->CmdBuffer = from->CmdBuffer;
        to->IdxBuffer = from->IdxBuffer;
        to->VtxBuffer = from->VtxBuffer;
        to->Flags = from->Flags;
        data.CmdLists.push_back(to);
    }
    data.Valid = true;
    data.FrameCount = source.FrameCount;
    data.TotalIdxCount = source.TotalIdxCount;
    data.TotalVtxCount = source.TotalVtxCount;
    data.DisplayPos = source.DisplayPos;
    data.DisplaySize = source.DisplaySize;
    data.FramebufferScale = source.FramebufferScale;
    data.Textures = source.Textures; // the context's: the renderer uploads what changed
}

void DrawCopy::Clear() {
    data.Clear();
    for (ImDrawList* list : lists) {
        IM_DELETE(list);
    }
    lists.clear();
}

// ── The layer ──

Layer::Layer(Core::System& system_, Window& window_, Config& config_)
    : system{system_}, window{window_}, config{config_} {
    IMGUI_CHECKVERSION();
    context = ImGui::CreateContext();
    ImGui::SetCurrentContext(context);
    ImGuiIO& io = ImGui::GetIO();
    io.IniFilename = nullptr; // nothing of the menu's is kept between runs but the settings
    io.LogFilename = nullptr;
    io.ConfigFlags |= ImGuiConfigFlags_NavEnableKeyboard | ImGuiConfigFlags_NavEnableGamepad;
    // the window shows and hides the cursor itself (hidden after a while in full screen)
    io.ConfigFlags |= ImGuiConfigFlags_NoMouseCursorChange;
    // a font that scales: drawn at the size the window's height asks for (UpdateScale)
    io.Fonts->AddFontDefaultVector();
    SetStyle(ImGui::GetStyle());
    base_style = ImGui::GetStyle();
    ImGui_ImplSDL2_InitForOther(window.GetSDLWindow());
    ImGui_ImplSDL2_SetGamepadMode(ImGui_ImplSDL2_GamepadMode_AutoAll); // any controller, not the first

    menu = std::make_unique<Menu>(*this);
    keyboard = std::make_shared<Keyboard>();
    system.RegisterSoftwareKeyboard(keyboard);
#ifdef ENABLE_VULKAN
    RegisterVulkanOverlay(*this);
#endif
    LOG_INFO(Frontend, "in-game menu: Esc, or a controller's Guide or Back+Start");
}

Layer::~Layer() {
#ifdef ENABLE_VULKAN
    UnregisterVulkanOverlay();
#endif
    if (menu_open) {
        menu->Closed(); // the window closed with the menu up: its changes are kept all the same
    }
    SetCapture(false);
    menu.reset();
    std::scoped_lock lock{mutex};
    MakeCurrent();
    copy.Clear();
    ImGui_ImplSDL2_Shutdown();
    ImGui::DestroyContext(context);
}

void Layer::MakeCurrent() {
    ImGui::SetCurrentContext(context);
}

void Layer::Post(std::function<void()> action) {
    posted.push_back(std::move(action));
}

void Layer::Help(const char* text) {
    if (ImGui::IsItemHovered(ImGuiHoveredFlags_AllowWhenDisabled) || ImGui::IsItemFocused()) {
        help = text;
    }
}

void Layer::Open() {
    if (menu_open) {
        return;
    }
    menu_open = true;
    menu->Opened();
    SetCapture(true);
}

void Layer::Close() {
    if (!menu_open) {
        return;
    }
    menu_open = false;
    key_capture = false;
    menu->Closed();
    keyboard->Refocus();
    SetCapture(keyboard->Active());
}

void Layer::SetCapture(bool on) {
    if (on == capturing) {
        return;
    }
    capturing = on;
    Input::SetMuted(on);
    if (on) {
        // what the game had held is let go: the menu has the keys and the mouse now
        InputCommon::GetKeyboard()->ReleaseAllKeys();
        window.TouchReleased();
        fresh_input = true;
        SDL_ShowCursor(SDL_ENABLE);
    }
}

bool Layer::OnEvent(const SDL_Event& event) {
    // the menu's toggle: Esc, a controller's Guide, or its Back and Start together (Guide is
    // often Steam's or the Game Bar's)
    switch (event.type) {
    case SDL_KEYDOWN:
        if (event.key.repeat == 0 && event.key.keysym.scancode == SDL_SCANCODE_ESCAPE &&
            !key_capture) {
            if (!menu_open) {
                Open();
                return true;
            }
            escape_pressed = true; // the frame decides: a list or a text field takes it first
        }
        break;
    case SDL_CONTROLLERBUTTONDOWN:
    case SDL_CONTROLLERBUTTONUP: {
        const bool down = event.type == SDL_CONTROLLERBUTTONDOWN;
        const u8 button = event.cbutton.button;
        if (button == SDL_CONTROLLER_BUTTON_GUIDE) {
            if (down) {
                menu_open ? Close() : Open();
            }
            return true;
        }
        const u32 bit = button == SDL_CONTROLLER_BUTTON_BACK    ? BACK_HELD
                        : button == SDL_CONTROLLER_BUTTON_START ? START_HELD
                                                                : 0;
        if (bit) {
            const u32 before = controller_buttons;
            controller_buttons = down ? controller_buttons | bit : controller_buttons & ~bit;
            if (down && before != controller_buttons &&
                controller_buttons == (BACK_HELD | START_HELD)) {
                menu_open ? Close() : Open();
                return true;
            }
        }
        break;
    }
    case SDL_CONTROLLERDEVICEADDED:
    case SDL_CONTROLLERDEVICEREMOVED: {
        // the menu's list of controllers, kept whether it is up or not (and the window's)
        if (event.type == SDL_CONTROLLERDEVICEREMOVED) {
            controller_buttons = 0;
        }
        std::scoped_lock lock{mutex};
        MakeCurrent();
        ImGui_ImplSDL2_ProcessEvent(&event);
        return false;
    }
    default:
        break;
    }

    if (!capturing) {
        return false;
    }
    // Controls: the next key pressed is the one to bind (Dear ImGui still sees keys let go, the
    // one that started the capture among them)
    if (key_capture && event.type == SDL_KEYDOWN) {
        if (event.key.repeat == 0) {
            menu->OnCapturedKey(static_cast<int>(event.key.keysym.scancode));
        }
        return true;
    }
    {
        std::scoped_lock lock{mutex};
        MakeCurrent();
        ImGui_ImplSDL2_ProcessEvent(&event);
    }
    switch (event.type) {
    case SDL_KEYDOWN:
    case SDL_KEYUP: {
        // full screen stays the window's (F11, Alt+Enter)
        const int code = static_cast<int>(event.key.keysym.scancode);
        const bool alt_enter = code == SDL_SCANCODE_RETURN && (event.key.keysym.mod & KMOD_ALT);
        return !(alt_enter || code == Input::KeyCode(config.fullscreen_key));
    }
    case SDL_TEXTINPUT:
    case SDL_TEXTEDITING:
    case SDL_MOUSEMOTION:
    case SDL_MOUSEBUTTONDOWN:
    case SDL_MOUSEBUTTONUP:
    case SDL_MOUSEWHEEL:
    case SDL_FINGERDOWN:
    case SDL_FINGERUP:
    case SDL_FINGERMOTION:
    case SDL_CONTROLLERBUTTONDOWN:
    case SDL_CONTROLLERBUTTONUP:
    case SDL_CONTROLLERAXISMOTION:
        return true;
    default:
        return false; // the window's too: resizing, focus, controllers coming and going, quitting
    }
}

void Layer::UpdateScale(float display_height) {
    // a font about 1/34 of the window's height: 21 pixels at 720p, 32 at 1080p, 64 at 4K
    const float size = std::clamp(std::round(display_height / 34.0f), 14.0f, 96.0f);
    if (size == font_size) {
        return;
    }
    font_size = size;
    ImGuiStyle& style = ImGui::GetStyle();
    style = base_style;
    style.ScaleAllSizes(size / 13.0f);
    style.FontSizeBase = size;
}

void Layer::ToggleFpsCounter() {
    if (FpsCounterOn(config.fps_counter)) {
        last_fps_corner = config.fps_counter;
        config.fps_counter = "off";
    } else {
        config.fps_counter = last_fps_corner;
    }
    // the file's settings with this one changed: not the command line's
    Config file = App::Load();
    file.fps_counter = config.fps_counter;
    App::Save(file);
    LOG_INFO(Frontend, "frame-rate counter: {}", config.fps_counter);
}

void Layer::Frame() {
    if (escape_pressed) {
        escape_pressed = false;
        if (menu_open && !was_busy && !key_capture) {
            if (menu->EditingLayout()) {
                menu->CancelLayoutEdit(); // the layout editor's Esc: back to the menu as it was
            } else {
                Close();
            }
        }
    }
    if (close_requested) {
        close_requested = false;
        Close();
    }
    const bool keyboard_up = keyboard->Active();
    SetCapture(menu_open || keyboard_up);
    // a hint over the game (the skip after a quest) while nothing else of the menu's is up
    const Hint hint = capturing ? Hint{} : CurrentHint(config);
    const bool fps_counter = FpsCounterOn(config.fps_counter);
    const bool draw = capturing || keyboard->HasNotice() || !hint.Empty() || fps_counter;
    if (!draw || !renderer_ready.load(std::memory_order_acquire)) {
        if (visible.load(std::memory_order_relaxed)) {
            std::scoped_lock lock{mutex};
            draw_ready = false;
            visible.store(false, std::memory_order_release);
        }
        return;
    }

    std::unique_lock lock{mutex};
    MakeCurrent();
    ImGuiIO& io = ImGui::GetIO();
    if (fresh_input) {
        // whatever the menu had pressed when it closed is over
        io.ClearEventsQueue();
        io.ClearInputKeys();
        io.ClearInputMouse();
        int x = 0, y = 0;
        SDL_GetMouseState(&x, &y);
        io.AddMousePosEvent(static_cast<float>(x), static_cast<float>(y));
        // and a controller's buttons held now (the backend reads them each frame) count as held,
        // not as pressed: a frame that takes them in and shows nothing (B held as the menu
        // opened would close it at once, A would choose its first button)
        ImGui_ImplSDL2_NewFrame();
        ImGui::NewFrame();
        ImGui::EndFrame();
        fresh_input = false;
    }
    ImGui_ImplSDL2_NewFrame();
    // pixels per window unit as the window counts them (the backend asks OpenGL, which a
    // Vulkan or software window doesn't have)
    int w = 0, h = 0, pw = 0, ph = 0;
    SDL_GetWindowSize(window.GetSDLWindow(), &w, &h);
    SDL_GetWindowSizeInPixels(window.GetSDLWindow(), &pw, &ph);
    if (w > 0 && h > 0 && pw > 0 && ph > 0) {
        io.DisplayFramebufferScale = ImVec2(static_cast<float>(pw) / w, static_cast<float>(ph) / h);
    }
    UpdateScale(io.DisplaySize.y);
    if (capturing) {
        SDL_ShowCursor(SDL_ENABLE);
    }

    ImGui::NewFrame();
    help = nullptr;
    if (menu_open) {
        menu->Draw();
    } else if (keyboard_up) {
        keyboard->Draw();
    }
    if (!keyboard_up && keyboard->HasNotice()) {
        keyboard->DrawNotice();
    }
    if (!hint.Empty()) {
        DrawHint(hint, Port::Hooks::SkipHint() == 2);
    }
    if (fps_counter) {
        DrawFpsCounter(config.fps_counter);
    }
    was_busy = ImGui::IsPopupOpen(nullptr, ImGuiPopupFlags_AnyPopup) || ImGui::IsAnyItemActive();
    ImGui::Render();
    copy.CopyFrom(*ImGui::GetDrawData());
    draw_ready = true;
    visible.store(true, std::memory_order_release);
    lock.unlock();

    // what the frame asked for, without the lock
    auto actions = std::move(posted);
    posted.clear();
    for (auto& action : actions) {
        action();
    }
}

ImDrawData* Layer::DrawData(u32 width, u32 height) {
    ImDrawData& data = copy.data;
    if (!draw_ready || width == 0 || height == 0 || data.DisplaySize.x <= 0.0f ||
        data.DisplaySize.y <= 0.0f || data.CmdLists.Size == 0) {
        return nullptr;
    }
    // drawn at the size of the target, which can differ from the window's for a frame while it
    // is resized
    data.FramebufferScale = ImVec2(static_cast<float>(width) / data.DisplaySize.x,
                                   static_cast<float>(height) / data.DisplaySize.y);
    return &data;
}

Layer* Get() {
    return instance.load(std::memory_order_acquire);
}

// ── ui.h ──

void Start(Core::System& system, Window& window, Config& config) {
    if (!Get()) {
        instance.store(new Layer(system, window, config), std::memory_order_release);
    }
}

void Stop() {
    delete instance.exchange(nullptr);
}

bool OnEvent(const SDL_Event& event) {
    Layer* layer = Get();
    return layer && layer->OnEvent(event);
}

void Frame() {
    if (Layer* layer = Get()) {
        layer->Frame();
    }
}

bool Visible() {
    const Layer* layer = Get();
    return layer && layer->Capturing();
}

void ToggleFpsCounter() {
    if (Layer* layer = Get()) {
        layer->ToggleFpsCounter();
    }
}

} // namespace App::Ui
