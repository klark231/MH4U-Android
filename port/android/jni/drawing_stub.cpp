// pica_core.cpp (patched) asks SwRenderer::IsDrawingEnabled() before it runs the vertex
// shaders. That function lives in the software renderer, which Azahar doesn't build on
// Android (ENABLE_SOFTWARE_RENDERER is forced off there), so it's defined here: drawing is
// always on, the Vulkan renderer draws.
#include <atomic>

namespace SwRenderer {

namespace {
std::atomic<bool> drawing_enabled{true};
} // namespace

void SetDrawingEnabled(bool enabled) {
    drawing_enabled.store(enabled, std::memory_order_relaxed);
}

bool IsDrawingEnabled() {
    return drawing_enabled.load(std::memory_order_relaxed);
}

} // namespace SwRenderer
