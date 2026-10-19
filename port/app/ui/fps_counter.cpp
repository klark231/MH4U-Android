#include "ui/fps_counter.h"

#include <atomic>
#include <cmath>
#include <imgui.h>

namespace App::Ui {

namespace {
// written by the window's event loop and read by the menu's frame, both on the emulation thread
// today; atomics so that neither has to be
std::atomic<double> measured_fps{0.0};
std::atomic<double> measured_frame_ms{0.0};
std::atomic<double> measured_speed{0.0};
} // namespace

bool FpsCounterOn(const std::string& place) {
    for (const char* p : FPS_COUNTER_PLACES) {
        if (place == p) {
            return place != "off";
        }
    }
    return false;
}

void SetFpsMeasure(double fps, double frame_ms, double speed) {
    measured_fps.store(fps, std::memory_order_relaxed);
    measured_frame_ms.store(frame_ms, std::memory_order_relaxed);
    measured_speed.store(speed, std::memory_order_relaxed);
}

void DrawFpsCounter(const std::string& place) {
    const ImGuiIO& io = ImGui::GetIO();
    const bool right = place == "top-right" || place == "bottom-right";
    const bool bottom = place == "bottom-left" || place == "bottom-right";
    const float margin = std::round(ImGui::GetFontSize() * 0.5f);
    ImGui::SetNextWindowPos(ImVec2(right ? io.DisplaySize.x - margin : margin,
                                   bottom ? io.DisplaySize.y - margin : margin),
                            ImGuiCond_Always, ImVec2(right ? 1.0f : 0.0f, bottom ? 1.0f : 0.0f));
    ImGui::SetNextWindowBgAlpha(0.45f);
    ImGui::PushStyleVar(ImGuiStyleVar_WindowBorderSize, 0.0f);
    ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(margin * 0.8f, margin * 0.4f));
    constexpr ImGuiWindowFlags flags = ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_NoInputs |
                                       ImGuiWindowFlags_NoNav | ImGuiWindowFlags_NoFocusOnAppearing |
                                       ImGuiWindowFlags_NoSavedSettings | ImGuiWindowFlags_AlwaysAutoResize;
    if (ImGui::Begin("##fps_counter", nullptr, flags)) {
        const double fps = measured_fps.load(std::memory_order_relaxed);
        const double frame_ms = measured_frame_ms.load(std::memory_order_relaxed);
        const double speed = measured_speed.load(std::memory_order_relaxed);
        // green at full speed, amber when the emulation falls behind
        const ImVec4 colour = speed <= 0.0 || speed >= 0.97 ? ImVec4(0.55f, 0.90f, 0.55f, 1.0f)
                                                            : ImVec4(0.95f, 0.70f, 0.30f, 1.0f);
        ImGui::TextColored(colour, "%.0f FPS", fps);
        ImGui::SameLine();
        ImGui::TextDisabled("%.1f ms", frame_ms);
        if (speed > 0.0 && speed < 0.97) {
            ImGui::SameLine();
            ImGui::TextDisabled("%.0f%%", speed * 100.0);
        }
    }
    ImGui::End();
    ImGui::PopStyleVar(2);
}

} // namespace App::Ui
