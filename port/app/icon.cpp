#include "icon.h"

#include <SDL.h>

#include "icon/icon_data.h"

namespace App::Icon {

void Apply(SDL_Window* window) {
    if (!window) {
        return;
    }
    // the pixels are RGBA, row by row, top to bottom
    SDL_Surface* surface = SDL_CreateRGBSurfaceWithFormatFrom(
        const_cast<unsigned char*>(kPixels), kSize, kSize, 32, kSize * 4, SDL_PIXELFORMAT_RGBA32);
    if (!surface) {
        return;
    }
    SDL_SetWindowIcon(window, surface);
    SDL_FreeSurface(surface);
}

} // namespace App::Icon
