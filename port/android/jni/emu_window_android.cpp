#include "emu_window_android.h"

#include <algorithm>

#include <android/native_window.h>

#include "common/logging/log.h"

namespace Mh4uAndroid {

namespace {
// Azahar's Vulkan renderer asks its window for a context to find the driver in; none is needed
// when the system's libvulkan is the driver.
class NoContext final : public Frontend::GraphicsContext {};
} // namespace

EmuWindow::EmuWindow(ANativeWindow* surface) : Frontend::EmuWindow(false) {
    window_info.type = Frontend::WindowSystemType::Android;
    window_info.render_surface = surface;
    window_info.render_surface_scale = 1.0f;
    if (surface) {
        width = ANativeWindow_getWidth(surface);
        height = ANativeWindow_getHeight(surface);
        Resize();
    }
}

bool EmuWindow::OnSurfaceChanged(ANativeWindow* surface) {
    const int w = surface ? ANativeWindow_getWidth(surface) : 0;
    const int h = surface ? ANativeWindow_getHeight(surface) : 0;
    if (window_info.render_surface == surface && w == width && h == height) {
        return false;
    }
    width = w;
    height = h;
    window_info.type = Frontend::WindowSystemType::Android;
    window_info.render_surface = surface;
    Resize();
    return true;
}

void EmuWindow::Resize() {
    if (width > 0 && height > 0) {
        UpdateCurrentFramebufferLayout(static_cast<unsigned>(width), static_cast<unsigned>(height),
                                       height > width);
    }
}

void EmuWindow::OnTouch(int x, int y, bool down) {
    if (down) {
        TouchPressed(static_cast<unsigned>(std::max(x, 0)), static_cast<unsigned>(std::max(y, 0)));
    } else {
        TouchReleased();
    }
}

void EmuWindow::OnTouchMoved(int x, int y) {
    TouchMoved(static_cast<unsigned>(std::max(x, 0)), static_cast<unsigned>(std::max(y, 0)));
}

std::unique_ptr<Frontend::GraphicsContext> EmuWindow::CreateSharedContext() const {
    return std::make_unique<NoContext>();
}

} // namespace Mh4uAndroid
