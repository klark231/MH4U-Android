#include "window.h"

#include <algorithm>
#include <cmath>
#include <SDL.h>
#include <fmt/format.h>

#include "common/logging/log.h"
#include "config.h"
#include "core/core.h"
#include "core/perf_stats.h"
#include "frontend.h"
#include "icon.h"
#include "input.h"
#include "input_common/keyboard.h"
#include "input_common/main.h"
#include "ui/fps_counter.h"
#include "ui/ui.h"

namespace App {

#ifdef ENABLE_OPENGL
std::unique_ptr<Window> MakeOpenGLWindow(Core::System& system, const Config& config, bool is_secondary);
#endif
#ifdef ENABLE_VULKAN
std::unique_ptr<Window> MakeVulkanWindow(Core::System& system, const Config& config, bool is_secondary);
#endif
#ifdef ENABLE_SOFTWARE_RENDERER
std::unique_ptr<Window> MakeSoftwareWindow(Core::System& system, const Config& config, bool is_secondary);
#endif

namespace {
constexpr u32 CURSOR_IDLE_MS = 2000;  // full screen: the mouse cursor hides after this long
constexpr u32 TITLE_EVERY_MS = 2000;
constexpr u32 MEASURE_EVERY_MS = 500;  // the frame rate, for the title and the counter
} // namespace

std::unique_ptr<Window> Window::Create(Core::System& system, Api api, const Config& config,
                                       bool is_secondary) {
    switch (api) {
#ifdef ENABLE_OPENGL
    case Api::OpenGL:
        return MakeOpenGLWindow(system, config, is_secondary);
#endif
#ifdef ENABLE_VULKAN
    case Api::Vulkan:
        return MakeVulkanWindow(system, config, is_secondary);
#endif
#ifdef ENABLE_SOFTWARE_RENDERER
    case Api::Software:
        return MakeSoftwareWindow(system, config, is_secondary);
#endif
    default:
        LOG_ERROR(Frontend, "this build has no such renderer");
        return nullptr;
    }
}

bool Window::InitSDL() {
    SDL_SetHint(SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, "1");
    // Windows: draw at the screen's own pixels on a scaled (high-DPI) display instead of being
    // stretched blurry by the system (the window's coordinates are pixels then, as ToPixels expects)
    SDL_SetHint(SDL_HINT_WINDOWS_DPI_AWARENESS, "permonitorv2");
    // the game is played with the mouse as a stylus: no touch-to-mouse doubling
    SDL_SetHint(SDL_HINT_TOUCH_MOUSE_EVENTS, "0");
    SDL_SetHint(SDL_HINT_MOUSE_FOCUS_CLICKTHROUGH, "1");
    if (SDL_Init(SDL_INIT_VIDEO | SDL_INIT_GAMECONTROLLER | SDL_INIT_EVENTS) < 0) {
        LOG_CRITICAL(Frontend, "SDL didn't start: {}", SDL_GetError());
        return false;
    }
    SDL_SetMainReady();
    return true;
}

Window::Window(Core::System& system_, const Config& config, bool is_secondary_)
    : EmuWindow(is_secondary_), system(system_) {
    touch_key = Input::KeyCode(config.touch_screen_key);
    fullscreen_key = Input::KeyCode(config.fullscreen_key);
    fps_counter_key = Input::KeyCode(config.fps_counter_key);
}

Window::~Window() {
    if (render_window) {
        SDL_DestroyWindow(render_window);
    }
}

u32 Window::BaseFlags() {
    return SDL_WINDOW_RESIZABLE | SDL_WINDOW_ALLOW_HIGHDPI;
}

void Window::FinishSetup(bool start_fullscreen) {
    window_id = SDL_GetWindowID(render_window);
    Icon::Apply(render_window);
    // a window the size of a 1280x720 picture, or the screen's if that's smaller
    SDL_Rect usable{};
    if (SDL_GetDisplayUsableBounds(SDL_GetWindowDisplayIndex(render_window), &usable) == 0) {
        const int w = std::min(1280, usable.w * 9 / 10);
        const int h = std::min(720, usable.h * 9 / 10);
        SDL_SetWindowSize(render_window, w, h);
        SDL_SetWindowPosition(render_window, SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED);
    }
    if (start_fullscreen) {
        SetFullscreen(true);
    }
    OnResize();
    OnMinimalClientAreaChangeRequest(GetActiveConfig().min_client_area_size);
    SDL_PumpEvents();
}

void Window::SetFullscreen(bool on) {
    // desktop-sized full screen: no mode switch, quick to toggle, the game picks its own size
    if (SDL_SetWindowFullscreen(render_window, on ? SDL_WINDOW_FULLSCREEN_DESKTOP : 0) != 0) {
        LOG_ERROR(Frontend, "full screen {}: {}", on ? "on" : "off", SDL_GetError());
        return;
    }
    fullscreen = on;
    ShowCursorFor(CURSOR_IDLE_MS);
    OnResize();
}

void Window::OnResize() {
    int width = 0, height = 0;
    SDL_GetWindowSizeInPixels(render_window, &width, &height);
    if (width > 0 && height > 0) {
        UpdateCurrentFramebufferLayout(static_cast<unsigned>(width), static_cast<unsigned>(height));
    }
}

void Window::OnMinimalClientAreaChangeRequest(std::pair<u32, u32> minimal_size) {
    // the core asks for a 3DS-sized minimum; a smaller window is fine for the port's layouts
    SDL_SetWindowMinimumSize(render_window, static_cast<int>(minimal_size.first / 2),
                             static_cast<int>(minimal_size.second / 2));
}

std::pair<unsigned, unsigned> Window::ToPixels(s32 x, s32 y) const {
    int w = 1, h = 1, pw = 1, ph = 1;
    SDL_GetWindowSize(render_window, &w, &h);
    SDL_GetWindowSizeInPixels(render_window, &pw, &ph);
    const auto scale = [](s32 v, int from, int to) {
        return static_cast<unsigned>(std::max(0.0, std::round(static_cast<double>(v) * to / std::max(from, 1))));
    };
    return {scale(x, w, pw), scale(y, h, ph)};
}

void Window::ShowCursorFor(u32 ms) {
    SDL_ShowCursor(SDL_ENABLE);
    cursor_hide_at = fullscreen ? SDL_GetTicks() + ms : 0;
}

void Window::OnKey(int scancode, bool down, u16 modifiers) {
    const bool alt_enter = scancode == SDL_SCANCODE_RETURN && (modifiers & KMOD_ALT);
    if (scancode == fullscreen_key || alt_enter) {
        if (down) {
            SetFullscreen(!fullscreen);
        }
        return;
    }
    if (scancode == fps_counter_key && fps_counter_key != 0) {
        if (down && !is_secondary) {
            Ui::ToggleFpsCounter();
        }
        return;
    }
    if (scancode == touch_key) {
        if (down) {
            Port::ToggleQuestMap();
        }
        return;
    }
    if (down) {
        InputCommon::GetKeyboard()->PressKey(scancode);
    } else {
        InputCommon::GetKeyboard()->ReleaseKey(scancode);
    }
}

void Window::PollEvents() {
    SDL_Event event;
    while (SDL_PollEvent(&event)) {
        // the in-game menu's (ui/): its toggle, and while it is up the keys, mouse and controller
        if (Ui::OnEvent(event)) {
            continue;
        }
        switch (event.type) {
        case SDL_WINDOWEVENT:
            if (event.window.windowID != window_id) {
                break;
            }
            switch (event.window.event) {
            case SDL_WINDOWEVENT_SIZE_CHANGED:
            case SDL_WINDOWEVENT_RESIZED:
            case SDL_WINDOWEVENT_MAXIMIZED:
            case SDL_WINDOWEVENT_RESTORED:
            case SDL_WINDOWEVENT_DISPLAY_CHANGED:
                OnResize();
                break;
            case SDL_WINDOWEVENT_CLOSE:
                RequestClose();
                break;
            case SDL_WINDOWEVENT_FOCUS_LOST:
                // keys held while switching away would stay down
                InputCommon::GetKeyboard()->ReleaseAllKeys();
                break;
            }
            break;
        case SDL_KEYDOWN:
        case SDL_KEYUP:
            if (event.key.repeat == 0) {
                OnKey(static_cast<int>(event.key.keysym.scancode), event.type == SDL_KEYDOWN,
                      event.key.keysym.mod);
            }
            break;
        case SDL_MOUSEMOTION:
            if (event.motion.which != SDL_TOUCH_MOUSEID && event.motion.windowID == window_id) {
                ShowCursorFor(CURSOR_IDLE_MS);
                if (event.motion.state & SDL_BUTTON_LMASK) {
                    const auto [x, y] = ToPixels(event.motion.x, event.motion.y);
                    TouchMoved(x, y);
                }
            }
            break;
        case SDL_MOUSEBUTTONDOWN:
        case SDL_MOUSEBUTTONUP:
            if (event.button.which != SDL_TOUCH_MOUSEID && event.button.windowID == window_id &&
                event.button.button == SDL_BUTTON_LEFT) {
                ShowCursorFor(CURSOR_IDLE_MS);
                if (event.type == SDL_MOUSEBUTTONDOWN) {
                    const auto [x, y] = ToPixels(event.button.x, event.button.y);
                    TouchPressed(x, y);
                } else {
                    TouchReleased();
                }
            }
            break;
        case SDL_FINGERDOWN:
        case SDL_FINGERMOTION: {
            int pw = 1, ph = 1;
            SDL_GetWindowSizeInPixels(render_window, &pw, &ph);
            const auto x = static_cast<unsigned>(std::max(0.0f, event.tfinger.x * pw));
            const auto y = static_cast<unsigned>(std::max(0.0f, event.tfinger.y * ph));
            if (event.type == SDL_FINGERDOWN) {
                TouchPressed(x, y);
            } else {
                TouchMoved(x, y);
            }
            break;
        }
        case SDL_FINGERUP:
            TouchReleased();
            break;
        case SDL_CONTROLLERBUTTONDOWN:
            // the 3DS has no stick clicks: the right one is the port's touch-screen switch
            if (event.cbutton.button == SDL_CONTROLLER_BUTTON_RIGHTSTICK) {
                Port::ToggleQuestMap();
            }
            break;
        case SDL_CONTROLLERDEVICEADDED:
        case SDL_CONTROLLERDEVICEREMOVED:
            controllers_changed = true;
            break;
        case SDL_QUIT:
            RequestClose();
            break;
        default:
            break;
        }
    }
    if (cursor_hide_at && SDL_GetTicks() >= cursor_hide_at && !Ui::Visible()) {
        SDL_ShowCursor(SDL_DISABLE);
        cursor_hide_at = 0;
    }
    if (!is_secondary) {
        const u32 now = SDL_GetTicks();
        if (now - last_measure >= MEASURE_EVERY_MS) {
            last_measure = now;
            const auto stats = system.GetAndResetPerfStats();
            fps = stats.game_fps;
            Ui::SetFpsMeasure(fps, fps > 0.0 ? 1000.0 / fps : 0.0, stats.emulation_speed);
        }
        if (now - last_title >= TITLE_EVERY_MS) {
            last_title = now;
            SDL_SetWindowTitle(render_window, fmt::format("MH4U PC port | {:.0f} fps", fps).c_str());
        }
        Ui::Frame(); // the in-game menu's frame, when it is up
    }
}

} // namespace App
