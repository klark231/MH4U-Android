// The window for Azahar's Vulkan renderer, which makes its own surface from the native window
// handle and presents by itself. On macOS the handle is a Metal layer and Vulkan runs on
// MoltenVK (Vulkan over Metal).
#include "common/logging/log.h"
#include "config.h"
#include "window.h"

// last: the X11 headers it pulls in define macros (Bool, None, ...) that break later headers
#include <SDL.h>
#include <SDL_syswm.h>

namespace App {

namespace {

class NoContext final : public Frontend::GraphicsContext {};

class VulkanWindow final : public Window {
public:
    VulkanWindow(Core::System& system_, const Config& config, bool is_secondary_)
        : Window(system_, config, is_secondary_) {}

    bool Init(bool start_fullscreen) {
        render_window = SDL_CreateWindow("MH4U PC port", SDL_WINDOWPOS_CENTERED,
                                         SDL_WINDOWPOS_CENTERED, 1280, 720, BaseFlags());
        if (!render_window) {
            LOG_CRITICAL(Frontend, "no window: {}", SDL_GetError());
            return false;
        }
        SDL_SysWMinfo wm;
        SDL_VERSION(&wm.version);
        if (SDL_GetWindowWMInfo(render_window, &wm) == SDL_FALSE) {
            LOG_CRITICAL(Frontend, "no native window handle: {}", SDL_GetError());
            return false;
        }
        switch (wm.subsystem) {
#ifdef SDL_VIDEO_DRIVER_WINDOWS
        case SDL_SYSWM_WINDOWS:
            window_info.type = Frontend::WindowSystemType::Windows;
            window_info.render_surface = reinterpret_cast<void*>(wm.info.win.window);
            break;
#endif
#ifdef SDL_VIDEO_DRIVER_X11
        case SDL_SYSWM_X11:
            window_info.type = Frontend::WindowSystemType::X11;
            window_info.display_connection = wm.info.x11.display;
            window_info.render_surface = reinterpret_cast<void*>(wm.info.x11.window);
            break;
#endif
#ifdef SDL_VIDEO_DRIVER_WAYLAND
        case SDL_SYSWM_WAYLAND:
            window_info.type = Frontend::WindowSystemType::Wayland;
            window_info.display_connection = wm.info.wl.display;
            window_info.render_surface = wm.info.wl.surface;
            break;
#endif
#ifdef SDL_VIDEO_DRIVER_COCOA
        case SDL_SYSWM_COCOA:
            window_info.type = Frontend::WindowSystemType::MacOS;
            window_info.render_surface = SDL_Metal_GetLayer(SDL_Metal_CreateView(render_window));
            break;
#endif
        default:
            LOG_CRITICAL(Frontend, "Vulkan isn't supported on this window system ({})",
                         static_cast<int>(wm.subsystem));
            return false;
        }
        int w = 0, h = 0, pw = 0;
        SDL_GetWindowSize(render_window, &w, &h);
        SDL_GetWindowSizeInPixels(render_window, &pw, nullptr);
        window_info.render_surface_scale = w > 0 ? static_cast<float>(pw) / w : 1.0f;
        FinishSetup(start_fullscreen);
        return true;
    }

    std::unique_ptr<Frontend::GraphicsContext> CreateSharedContext() const override {
        return std::make_unique<NoContext>();
    }
};

} // namespace

std::unique_ptr<Window> MakeVulkanWindow(Core::System& system, const Config& config, bool is_secondary) {
    auto window = std::make_unique<VulkanWindow>(system, config, is_secondary);
    if (!window->Init(config.fullscreen && !is_secondary)) {
        return nullptr;
    }
    return window;
}

} // namespace App
