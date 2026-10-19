// The app's window (SDL2) as Azahar's renderers see it (Frontend::EmuWindow): the keyboard and
// controllers go to the emulated console, the mouse is the stylus, and a few keys are the app's
// own (full screen, the touch screen). One subclass per renderer: OpenGL, Vulkan, software.
#pragma once

#include <atomic>
#include <memory>
#include <string>

#include "common/common_types.h"
#include "core/frontend/emu_window.h"

union SDL_Event;
struct SDL_Window;

namespace Core {
class System;
}

namespace App {

struct Config;

class Window : public Frontend::EmuWindow {
public:
    enum class Api { OpenGL, Vulkan, Software };

    /// A window for `api`, or null if it can't be made (the log says why).
    static std::unique_ptr<Window> Create(Core::System& system, Api api, const Config& config,
                                          bool is_secondary = false);

    /// SDL with video and controllers, before InputCommon::Init() so that Azahar's controller
    /// engine reads the events this window pumps instead of starting a thread of its own.
    static bool InitSDL();

    ~Window() override;

    /// Handles the window's events: input, its own keys, resizing, closing. Azahar's renderers
    /// call it once a frame on the emulation thread.
    void PollEvents() override;

    /// For renderers that hand finished frames to the window (OpenGL, software): shows them
    /// until the window closes. Runs on a thread of its own.
    virtual void Present() {}

    bool IsOpen() const {
        return is_open;
    }
    void RequestClose() {
        is_open = false;
    }

    void SetFullscreen(bool on);
    bool IsFullscreen() const {
        return fullscreen;
    }

    /// Set when a controller came or went since the last call (the input profile has to be
    /// built again, between frames).
    bool TakeControllersChanged() {
        return controllers_changed.exchange(false);
    }
    /// The input profile built again as if a controller had come (the in-game menu changed the
    /// controls).
    void RequestInputReload() {
        controllers_changed = true;
    }

    SDL_Window* GetSDLWindow() const {
        return render_window;
    }

protected:
    Window(Core::System& system, const Config& config, bool is_secondary);

    /// The SDL window's flags common to every renderer.
    static u32 BaseFlags();

    /// After the subclass made `render_window`: size, full screen, minimum size.
    void FinishSetup(bool start_fullscreen);

    /// The layout for the window's drawable size (pixels, which differ from SDL's window
    /// coordinates on high-DPI screens).
    void OnResize();

    void OnMinimalClientAreaChangeRequest(std::pair<u32, u32> minimal_size) override;

    /// Window coordinates to drawable pixels.
    std::pair<unsigned, unsigned> ToPixels(s32 x, s32 y) const;

    SDL_Window* render_window = nullptr;
    u32 window_id = 0;
    Core::System& system;

private:
    void OnKey(int scancode, bool down, u16 modifiers);
    void ShowCursorFor(u32 ms);

    std::atomic<bool> is_open{true};
    bool fullscreen = false;
    int touch_key = 0, fullscreen_key = 0, fps_counter_key = 0;
    std::atomic<bool> controllers_changed{false};
    u32 cursor_hide_at = 0;  ///< SDL ticks after which an idle mouse cursor hides (full screen)
    u32 last_title = 0;
    u32 last_measure = 0;
    double fps = 0.0;  ///< the game's frame rate at the last measure
};

} // namespace App
