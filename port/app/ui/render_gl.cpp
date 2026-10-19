// The in-game menu over OpenGL: Dear ImGui's OpenGL 3 backend on the window's present thread, in
// the window's own context, after the game's frame is presented and before the swap.

// the backend on the OpenGL functions the window loaded (glad), instead of its own loader
#include <glad/glad.h>
#define IMGUI_IMPL_OPENGL_LOADER_CUSTOM
#include "imgui_impl_opengl3.cpp"

#include <cstring>

#include "common/logging/log.h"
#include "ui/layer.h"
#include "ui/ui.h"

namespace App::Ui {

namespace {
bool gl_ready = false; // the present thread's
} // namespace

void InitOpenGL() {
    Layer* layer = Get();
    if (!layer || gl_ready) {
        return;
    }
    auto lock = layer->Lock();
    layer->MakeCurrent();
    const char* version = reinterpret_cast<const char*>(glGetString(GL_VERSION));
    const bool es = version && std::strncmp(version, "OpenGL ES", 9) == 0;
    // default: "#version 410" on OpenGL 4.1 and newer (the window asks for 4.3 core)
    if (!ImGui_ImplOpenGL3_Init(es ? "#version 300 es" : nullptr)) {
        LOG_ERROR(Frontend, "menu: no OpenGL renderer ({})", version ? version : "no version");
        return;
    }
    ImGui_ImplOpenGL3_NewFrame(); // its shaders and buffers, made here, in this context
    gl_ready = true;
    layer->SetRendererReady(true);
}

void DrawOpenGL(u32 width, u32 height) {
    Layer* layer = Get();
    if (!layer || !gl_ready || !layer->Visible()) {
        return;
    }
    auto lock = layer->Lock();
    if (ImDrawData* data = layer->DrawData(width, height)) {
        layer->MakeCurrent();
        ImGui_ImplOpenGL3_RenderDrawData(data);
    }
}

void ShutdownOpenGL() {
    Layer* layer = Get();
    if (!layer || !gl_ready) {
        return;
    }
    auto lock = layer->Lock();
    layer->MakeCurrent();
    layer->SetRendererReady(false);
    ImGui_ImplOpenGL3_Shutdown();
    gl_ready = false;
}

} // namespace App::Ui
