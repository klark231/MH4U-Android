// The in-game menu's Dear ImGui context (inside ui/): its input, the frames built on the emulation
// thread, and the copy of the last frame's draw data that the renderer's thread draws.
//
// Locking: `Lock()` guards the context and the copy. The emulation thread holds it while it feeds
// events and builds a frame; a renderer's thread holds it while it uses Dear ImGui's renderer
// backend (texture uploads, drawing the copy), which reads and writes the context's textures and
// allocates through Dear ImGui. Work that touches the core or waits for anything (applying a
// setting, leaving a room, quitting) is posted and runs after the frame, without the lock.
#pragma once

#include <atomic>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <vector>
#include <imgui.h>

#include "common/common_types.h"

union SDL_Event;

namespace Core {
class System;
}

namespace App {

struct Config;
class Window;

namespace Ui {

class Keyboard;
class Menu;

/// A frame's draw data, copied (the context rebuilds its own draw lists every frame). The lists
/// are made, grown and freed with the layer's lock held.
class DrawCopy {
public:
    ~DrawCopy();
    void CopyFrom(const ImDrawData& source);
    void Clear();

    ImDrawData data;

private:
    ImVector<ImDrawList*> lists;
};

class Layer {
public:
    Layer(Core::System& system, Window& window, Config& config);
    ~Layer();

    Layer(const Layer&) = delete;
    Layer& operator=(const Layer&) = delete;

    // ── The emulation thread (ui.h) ──
    bool OnEvent(const SDL_Event& event);
    void Frame();
    /// Something of the menu's is on the screen (the renderers draw the copy).
    bool Visible() const {
        return visible.load(std::memory_order_acquire);
    }
    /// The menu or the software keyboard is up and has the input (not only a hint or the
    /// frame-rate counter over the game).
    bool Capturing() const {
        return capturing;
    }
    void ToggleFpsCounter();

    // ── The renderers' threads (render_*.cpp) ──
    std::unique_lock<std::mutex> Lock() {
        return std::unique_lock{mutex};
    }
    /// Makes the menu's context Dear ImGui's current one (with the lock held).
    void MakeCurrent();
    /// With the lock: the last frame for a target `width` x `height` pixels, or null when there
    /// is nothing to draw.
    ImDrawData* DrawData(u32 width, u32 height);
    /// With the lock: a renderer backend came up (frames can be built: Dear ImGui needs one that
    /// takes its textures before its first frame) or went.
    void SetRendererReady(bool ready) {
        renderer_ready.store(ready, std::memory_order_release);
    }
    /// With the lock: anti-aliased lines from the font atlas's baked ones (they need a renderer
    /// that filters textures) or drawn as triangles.
    void SetTexturedLines(bool on) {
        base_style.AntiAliasedLinesUseTex = on;
        ImGui::GetStyle().AntiAliasedLinesUseTex = on;
    }

    // ── The menu and the keyboard, in a frame ──
    Core::System& system;
    Window& window;
    Config& config;
    /// Runs `action` after the frame, on the emulation thread, without the lock.
    void Post(std::function<void()> action);
    void CloseMenu() {
        close_requested = true;
    }
    /// The description of the control under the mouse or the controller's cursor, for the
    /// menu's footer.
    void Help(const char* text);
    const char* HelpText() const {
        return help;
    }
    /// Keys go to the menu's key capture (Controls) instead of Dear ImGui until it is over.
    void SetKeyCapture(bool on) {
        key_capture = on;
    }
    /// At the end of the last frame a list or a question was open, or an item being edited: Esc
    /// or a controller's B in this frame is theirs, not the menu's.
    bool WasBusy() const {
        return was_busy;
    }

private:
    void Open();
    void Close();
    /// The game's input is cut off (the menu or the keyboard is up) or given back.
    void SetCapture(bool on);
    void UpdateScale(float display_height);

    std::mutex mutex;
    ImGuiContext* context = nullptr;
    ImGuiStyle base_style;
    float font_size = 0.0f;

    std::atomic<bool> visible{false};
    std::atomic<bool> renderer_ready{false};
    bool menu_open = false;
    bool capturing = false;      ///< the game's input cut off
    std::string last_fps_corner = "top-left";  ///< where ToggleFpsCounter puts the counter back
    bool fresh_input = false;    ///< the next frame starts from a clean input state
    bool close_requested = false;
    bool escape_pressed = false; ///< Esc while open: closes the menu unless something takes it
    bool was_busy = false;       ///< last frame: a list or popup open, or an item being edited
    bool key_capture = false;
    bool draw_ready = false;     ///< the copy has a frame to draw
    u32 controller_buttons = 0;  ///< Back and Start held (bits), for Back+Start
    const char* help = nullptr;

    DrawCopy copy;
    std::unique_ptr<Menu> menu;
    std::shared_ptr<Keyboard> keyboard;
    std::vector<std::function<void()>> posted;
};

/// The menu's layer, or null when it isn't running.
Layer* Get();

#ifdef ENABLE_VULKAN
/// Puts the menu's drawing into Azahar's Vulkan renderer (render_vk.cpp).
void RegisterVulkanOverlay(Layer& layer);
void UnregisterVulkanOverlay();
#endif

} // namespace Ui
} // namespace App
