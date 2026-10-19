// The window for Azahar's software renderer (for machines without a usable GPU driver, and for
// tests): Present() scales the two screens into the window as the screen layout says, with the
// touch overlay's part of the touch screen and its opacity.
#include <algorithm>
#include <chrono>
#include <thread>
#include <SDL.h>

#include "common/logging/log.h"
#include "common/settings.h"
#include "config.h"
#include "core/core.h"
#include "ui/ui.h"
#include "video_core/gpu.h"
#include "video_core/renderer_software/renderer_software.h"
#include "window.h"

namespace App {

namespace {

class NoContext final : public Frontend::GraphicsContext {};

class SoftwareWindow final : public Window {
public:
    SoftwareWindow(Core::System& system_, const Config& config, bool is_secondary_)
        : Window(system_, config, is_secondary_) {}

    bool Init(bool start_fullscreen) {
        render_window = SDL_CreateWindow("MH4U PC port", SDL_WINDOWPOS_CENTERED,
                                         SDL_WINDOWPOS_CENTERED, 1280, 720, BaseFlags());
        if (!render_window) {
            LOG_CRITICAL(Frontend, "no window: {}", SDL_GetError());
            return false;
        }
        FinishSetup(start_fullscreen);
        return true;
    }

    std::unique_ptr<Frontend::GraphicsContext> CreateSharedContext() const override {
        return std::make_unique<NoContext>();
    }
    void MakeCurrent() override {}
    void DoneCurrent() override {}

    void Present() override {
        using namespace std::chrono_literals;
        while (IsOpen()) {
            const auto start = std::chrono::steady_clock::now();
            DrawOnce();
            std::this_thread::sleep_until(start + 16ms);
        }
        if (!is_secondary) {
            Ui::ShutdownSoftware();
        }
    }

private:
    /// One screen of the renderer's (stored turned a quarter: `width` is the 3DS's rows) into
    /// `dst` of the window, `crop` of it (fractions), `opacity` 0..1.
    void DrawScreen(SDL_Surface* window_surface, VideoCore::ScreenId id,
                    const Common::Rectangle<u32>& dst, const Common::Rectangle<float>& crop,
                    float opacity) {
        const auto* renderer = dynamic_cast<SwRenderer::RendererSoftware*>(&system.GPU().Renderer());
        if (!renderer) {
            return;
        }
        const auto& info = renderer->Screen(id);
        if (info.pixels.empty() || dst.GetWidth() == 0 || dst.GetHeight() == 0) {
            return;
        }
        const int w = static_cast<int>(info.height), h = static_cast<int>(info.width);
        SDL_Surface* screen = SDL_CreateRGBSurfaceWithFormatFrom(
            const_cast<u8*>(info.pixels.data()), w, h, 32, w * 4, SDL_PIXELFORMAT_RGBA32);
        if (!screen) {
            return;
        }
        SDL_SetSurfaceBlendMode(screen, opacity < 1.0f ? SDL_BLENDMODE_BLEND : SDL_BLENDMODE_NONE);
        SDL_SetSurfaceAlphaMod(screen, static_cast<u8>(std::clamp(opacity, 0.0f, 1.0f) * 255));
        SDL_Rect src{static_cast<int>(crop.left * w), static_cast<int>(crop.top * h),
                     std::max(1, static_cast<int>((crop.right - crop.left) * w)),
                     std::max(1, static_cast<int>((crop.bottom - crop.top) * h))};
        SDL_Rect out{static_cast<int>(dst.left), static_cast<int>(dst.top),
                     static_cast<int>(dst.GetWidth()), static_cast<int>(dst.GetHeight())};
        SDL_BlitScaled(screen, &src, window_surface, &out);
        SDL_FreeSurface(screen);
    }

    void DrawOnce() {
        SDL_Surface* surface = SDL_GetWindowSurface(render_window);
        if (!surface) {
            return;
        }
        const auto& layout = GetFramebufferLayout();
        SDL_FillRect(surface, nullptr,
                     SDL_MapRGB(surface->format,
                                static_cast<u8>(Settings::values.bg_red.GetValue() * 255),
                                static_cast<u8>(Settings::values.bg_green.GetValue() * 255),
                                static_cast<u8>(Settings::values.bg_blue.GetValue() * 255)));
        // the surface is in window coordinates, the layout in drawable pixels
        int pw = 1, ph = 1;
        SDL_GetWindowSizeInPixels(render_window, &pw, &ph);
        const auto scaled = [&](const Common::Rectangle<u32>& r) {
            const auto sx = [&](u32 v) { return static_cast<u32>(static_cast<u64>(v) * surface->w / std::max(pw, 1)); };
            const auto sy = [&](u32 v) { return static_cast<u32>(static_cast<u64>(v) * surface->h / std::max(ph, 1)); };
            return Common::Rectangle<u32>{sx(r.left), sy(r.top), sx(r.right), sy(r.bottom)};
        };
        const Common::Rectangle<float> whole{0.0f, 0.0f, 1.0f, 1.0f};
        const auto top = [&] {
            if (layout.top_screen_enabled) {
                DrawScreen(surface, VideoCore::ScreenId::TopLeft, scaled(layout.top_screen), whole,
                           layout.top_opacity);
            }
        };
        const auto bottom = [&] {
            if (layout.bottom_screen_enabled) {
                DrawScreen(surface, VideoCore::ScreenId::Bottom, scaled(layout.bottom_screen),
                           layout.bottom_crop, layout.bottom_opacity);
            }
        };
        // the screen drawn second is the one on top (the overlay's corner)
        if (Settings::values.swap_screen.GetValue()) {
            bottom();
            top();
        } else {
            top();
            bottom();
            // and further parts of the touch screen, each where the layout puts it (the quest's
            // target camera beside the map)
            for (u32 i = 0; i < layout.bottom_view_count && layout.bottom_screen_enabled; ++i) {
                DrawScreen(surface, VideoCore::ScreenId::Bottom, scaled(layout.bottom_views[i].screen),
                           layout.bottom_views[i].crop, layout.bottom_opacity);
            }
        }
        if (!is_secondary) {
            Ui::DrawSoftware(surface); // the in-game menu over them
        }
        SDL_UpdateWindowSurface(render_window);
    }
};

} // namespace

std::unique_ptr<Window> MakeSoftwareWindow(Core::System& system, const Config& config, bool is_secondary) {
    auto window = std::make_unique<SoftwareWindow>(system, config, is_secondary);
    if (!window->Init(config.fullscreen && !is_secondary)) {
        return nullptr;
    }
    return window;
}

} // namespace App
