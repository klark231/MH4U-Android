// The app's icon on its windows (icon/mh4u.svg). Windows and macOS take the executable's or
// the bundle's icon for the taskbar and the Dock; the window's own icon, which Linux desktops
// and Windows' title bar show, is set here from the pixels in icon/icon_data.h.
#pragma once

struct SDL_Window;

namespace App::Icon {

/// Gives `window` the app's icon (a no-op when SDL can't make the surface).
void Apply(SDL_Window* window);

} // namespace App::Icon
