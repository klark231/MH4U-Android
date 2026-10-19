// The window for Azahar's OpenGL renderer: the renderer draws in a context of its own, shared
// with the window's, and Present() shows its finished frames (Frontend::TextureMailbox).
#include <cstring>
#include <SDL.h>
#include <glad/glad.h>

#include "common/logging/log.h"
#include "common/settings.h"
#include "config.h"
#include "core/core.h"
#include "ui/ui.h"
#include "video_core/gpu.h"
#include "video_core/renderer_base.h"
#include "window.h"

namespace App {

namespace {

/// A hidden window's context, shared with the one current when it is made, for the renderer's
/// threads. As Azahar expects (its Qt frontend's contexts behave so), making one doesn't change
/// the thread's current context, and DoneCurrent() only lets go of this one: the renderer makes
/// a context for its frame dumper and one per shader-compiling thread while its own is current,
/// and SDL_GL_CreateContext makes the new context current. Left so, the renderer went on in the
/// frame dumper's context, where the vertex arrays it had made don't exist (they aren't shared
/// between contexts), and the next context was shared with nothing: no picture at all.
class SharedContext final : public Frontend::GraphicsContext {
public:
    SharedContext() {
        SDL_Window* const previous_window = SDL_GL_GetCurrentWindow();
        const SDL_GLContext previous_context = SDL_GL_GetCurrentContext();
        window = SDL_CreateWindow(nullptr, SDL_WINDOWPOS_UNDEFINED, SDL_WINDOWPOS_UNDEFINED, 1, 1,
                                  SDL_WINDOW_HIDDEN | SDL_WINDOW_OPENGL);
        context = window ? SDL_GL_CreateContext(window) : nullptr;
        SDL_GL_MakeCurrent(previous_window, previous_context);
    }
    ~SharedContext() override {
        if (context) {
            SDL_GL_DeleteContext(context);
        }
        if (window) {
            SDL_DestroyWindow(window);
        }
    }
    bool Valid() const {
        return context != nullptr;
    }
    void MakeCurrent() override {
        SDL_GL_MakeCurrent(window, context);
    }
    void DoneCurrent() override {
        if (SDL_GL_GetCurrentContext() == context) {
            SDL_GL_MakeCurrent(window, nullptr);
        }
    }

private:
    SDL_Window* window = nullptr;
    SDL_GLContext context = nullptr;
};

class OpenGLWindow final : public Window {
public:
    OpenGLWindow(Core::System& system, const Config& config, bool is_secondary)
        : Window(system, config, is_secondary) {}

    bool Init(bool start_fullscreen) {
        SDL_GL_SetAttribute(SDL_GL_DOUBLEBUFFER, 1);
        SDL_GL_SetAttribute(SDL_GL_RED_SIZE, 8);
        SDL_GL_SetAttribute(SDL_GL_GREEN_SIZE, 8);
        SDL_GL_SetAttribute(SDL_GL_BLUE_SIZE, 8);
        SDL_GL_SetAttribute(SDL_GL_ALPHA_SIZE, 0);
        SDL_GL_SetAttribute(SDL_GL_SHARE_WITH_CURRENT_CONTEXT, 1);
        if (Settings::values.renderer_debug) {
            SDL_GL_SetAttribute(SDL_GL_CONTEXT_FLAGS, SDL_GL_CONTEXT_DEBUG_FLAG);
        }
        // OpenGL 4.3 core, else OpenGL ES 3.2
        for (const bool gles : {Settings::values.use_gles.GetValue(), !Settings::values.use_gles.GetValue()}) {
            SDL_GL_SetAttribute(SDL_GL_CONTEXT_MAJOR_VERSION, gles ? 3 : 4);
            SDL_GL_SetAttribute(SDL_GL_CONTEXT_MINOR_VERSION, gles ? 2 : 3);
            SDL_GL_SetAttribute(SDL_GL_CONTEXT_PROFILE_MASK,
                                gles ? SDL_GL_CONTEXT_PROFILE_ES : SDL_GL_CONTEXT_PROFILE_CORE);
            render_window = SDL_CreateWindow("MH4U PC port", SDL_WINDOWPOS_CENTERED,
                                             SDL_WINDOWPOS_CENTERED, 1280, 720,
                                             BaseFlags() | SDL_WINDOW_OPENGL);
            if (!render_window) {
                continue;
            }
            window_context = SDL_GL_CreateContext(render_window);
            if (window_context) {
                Settings::values.use_gles = gles;
                break;
            }
            SDL_DestroyWindow(render_window);
            render_window = nullptr;
        }
        if (!render_window || !window_context) {
            LOG_CRITICAL(Frontend, "no OpenGL 4.3 or OpenGL ES 3.2 context: {}", SDL_GetError());
            return false;
        }
        strict_context_required = std::strcmp(SDL_GetCurrentVideoDriver(), "wayland") == 0;
        auto shared = std::make_unique<SharedContext>();
        if (!shared->Valid()) {
            LOG_CRITICAL(Frontend, "no shared OpenGL context: {}", SDL_GetError());
            return false;
        }
        core_context = std::move(shared);

        int profile = 0;
        SDL_GL_GetAttribute(SDL_GL_CONTEXT_PROFILE_MASK, &profile);
        const auto load = profile == SDL_GL_CONTEXT_PROFILE_ES ? gladLoadGLES2Loader : gladLoadGLLoader;
        if (!load(static_cast<GLADloadproc>(SDL_GL_GetProcAddress))) {
            LOG_CRITICAL(Frontend, "couldn't load the OpenGL functions: {}", SDL_GetError());
            return false;
        }
        // the renderer's thread makes its own context current
        SDL_GL_MakeCurrent(render_window, nullptr);
        FinishSetup(start_fullscreen);
        return true;
    }

    ~OpenGLWindow() override {
        core_context.reset();
        if (window_context) {
            SDL_GL_DeleteContext(window_context);
        }
    }

    std::unique_ptr<Frontend::GraphicsContext> CreateSharedContext() const override {
        return std::make_unique<SharedContext>();
    }
    void MakeCurrent() override {
        core_context->MakeCurrent();
    }
    void DoneCurrent() override {
        core_context->DoneCurrent();
    }
    // Azahar saves the emulation thread's context around making the shader-compiling threads'
    // contexts; it goes back on its own (hidden) window, not on the one the present thread draws
    // into with the window's context
    void SaveContext() override {
        last_saved_window = SDL_GL_GetCurrentWindow();
        last_saved_context = SDL_GL_GetCurrentContext();
    }
    void RestoreContext() override {
        SDL_GL_MakeCurrent(last_saved_window ? last_saved_window : render_window, last_saved_context);
    }

    void Present() override {
        SDL_GL_MakeCurrent(render_window, window_context);
        if (!is_secondary) {
            Ui::InitOpenGL(); // the in-game menu, drawn in this context
        }
        bool vsync = !Settings::values.use_vsync.GetValue();
        while (IsOpen()) {
            // vsync as the settings say, also after the in-game menu changed it
            if (vsync != Settings::values.use_vsync.GetValue()) {
                vsync = Settings::values.use_vsync.GetValue();
                // adaptive vsync where the driver has it, so a late frame doesn't wait a whole refresh
                if (!vsync || SDL_GL_SetSwapInterval(-1) != 0) {
                    SDL_GL_SetSwapInterval(vsync ? 1 : 0);
                }
            }
            system.GPU().Renderer().TryPresent(100, is_secondary);
            if (!is_secondary) {
                const auto& layout = GetFramebufferLayout();
                Ui::DrawOpenGL(layout.width, layout.height);
            }
            SDL_GL_SwapWindow(render_window);
        }
        if (!is_secondary) {
            Ui::ShutdownOpenGL();
        }
        SDL_GL_MakeCurrent(render_window, nullptr);
    }

private:
    SDL_GLContext window_context = nullptr;
    SDL_Window* last_saved_window = nullptr;
    SDL_GLContext last_saved_context = nullptr;
    std::unique_ptr<Frontend::GraphicsContext> core_context;
};

} // namespace

std::unique_ptr<Window> MakeOpenGLWindow(Core::System& system, const Config& config, bool is_secondary) {
    auto window = std::make_unique<OpenGLWindow>(system, config, is_secondary);
    if (!window->Init(config.fullscreen && !is_secondary)) {
        return nullptr;
    }
    return window;
}

} // namespace App
