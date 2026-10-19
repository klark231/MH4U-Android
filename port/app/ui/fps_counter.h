// The frame-rate counter over the game (mh4u-app.ini's [Display] fps_counter, off by default):
// the game's frames per second and the time a frame takes, in a corner of the window, also while
// the in-game menu is closed. The menu's Display tab and the fps_counter_key (F3) switch it.
#pragma once

#include <string>

namespace App::Ui {

/// The corners fps_counter takes, "off" first.
inline constexpr const char* FPS_COUNTER_PLACES[] = {"off", "top-left", "top-right", "bottom-left",
                                                     "bottom-right"};

/// Whether `place` (fps_counter's value) shows the counter.
bool FpsCounterOn(const std::string& place);

/// The window's last measure (Window::PollEvents, a few times a second): the game's frames per
/// second, a frame's time in milliseconds and the emulation's speed (1 = full speed).
void SetFpsMeasure(double fps, double frame_ms, double speed);

/// Draws the counter in the corner `place`, in a Dear ImGui frame (the menu's context); it takes
/// no input.
void DrawFpsCounter(const std::string& place);

} // namespace App::Ui
