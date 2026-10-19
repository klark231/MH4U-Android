// The game's window on Android: a Surface the Kotlin side hands over, drawn on by Azahar's
// Vulkan renderer. Touches on the touch screen come in as window pixels. (Modelled on Azahar's
// own Android window; one window, Vulkan only, the system's Vulkan driver.)
#pragma once

#include <memory>

#include "core/frontend/emu_window.h"

struct ANativeWindow;

namespace Mh4uAndroid {

class EmuWindow final : public Frontend::EmuWindow {
public:
    explicit EmuWindow(ANativeWindow* surface);
    ~EmuWindow() override = default;

    /// The surface changed (rotation, the app came back). False if nothing differs.
    bool OnSurfaceChanged(ANativeWindow* surface);

    void OnTouch(int x, int y, bool down);
    void OnTouchMoved(int x, int y);

    void PollEvents() override {}
    void MakeCurrent() override {}
    void DoneCurrent() override {}
    std::unique_ptr<GraphicsContext> CreateSharedContext() const override;

    int Width() const { return width; }
    int Height() const { return height; }

private:
    void Resize();

    int width = 0;
    int height = 0;
};

} // namespace Mh4uAndroid
