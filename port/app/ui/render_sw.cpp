// The in-game menu in the software window: Dear ImGui's triangles drawn by the CPU straight into
// the window's surface, after the screens are copied there and before the surface is shown, on
// the window's present thread. (SDL's own renderers can't do it: Azahar builds its SDL2 without
// them.) Rectangles, which are most of a menu (text, fills), take a quicker way than triangles.

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <utility>
#include <vector>
#include <SDL.h>
#include <imgui.h>

#include "common/logging/log.h"
#include "ui/layer.h"
#include "ui/ui.h"

namespace App::Ui {

namespace {

/// A texture of the menu's (the font atlas): RGBA as Dear ImGui keeps it (red in the low byte).
struct Texture {
    int width = 0;
    int height = 0;
    std::vector<ImU32> pixels;
};

/// The window's surface: 32 bits a pixel, 8 for each of red, green and blue.
struct Target {
    Uint8* pixels;
    int pitch;
    int width;
    int height;
    int r_shift, g_shift, b_shift;
    Uint32 keep; ///< the bits that aren't colour (left as they are)
};

struct Clip {
    int x0, y0, x1, y1;
};

/// A vertex in the surface's pixels, its colour and texture position.
struct Vertex {
    float x, y, u, v;
    float r, g, b, a; // 0..255
};

// the present thread's
bool initialized = false;
bool unsupported_logged = false;

void UpdateTexture(ImTextureData* tex) {
    if (tex->Status == ImTextureStatus_WantCreate || tex->Status == ImTextureStatus_WantUpdates) {
        auto* texture = static_cast<Texture*>(tex->BackendUserData);
        if (!texture) {
            texture = new Texture;
            tex->BackendUserData = texture;
            tex->SetTexID(static_cast<ImTextureID>(reinterpret_cast<std::uintptr_t>(texture)));
        }
        texture->width = tex->Width;
        texture->height = tex->Height;
        texture->pixels.resize(static_cast<std::size_t>(tex->Width) * tex->Height);
        const auto* src = static_cast<const unsigned char*>(tex->GetPixels());
        if (tex->Format == ImTextureFormat_Alpha8) {
            for (std::size_t i = 0; i < texture->pixels.size(); ++i) {
                texture->pixels[i] = IM_COL32(255, 255, 255, src[i]);
            }
        } else {
            std::memcpy(texture->pixels.data(), src, texture->pixels.size() * sizeof(ImU32));
        }
        tex->SetStatus(ImTextureStatus_OK);
    } else if (tex->Status == ImTextureStatus_WantDestroy) {
        delete static_cast<Texture*>(tex->BackendUserData);
        tex->BackendUserData = nullptr;
        tex->SetTexID(ImTextureID_Invalid);
        tex->SetStatus(ImTextureStatus_Destroyed);
    }
}

void DestroyTextures() {
    for (ImTextureData* tex : ImGui::GetPlatformIO().Textures) {
        if (tex->RefCount == 1 && tex->BackendUserData) {
            tex->SetStatus(ImTextureStatus_WantDestroy);
            UpdateTexture(tex);
        }
    }
}

/// The texel at (u, v), nearest; white without a texture.
inline ImU32 Sample(const Texture* texture, float u, float v) {
    if (!texture || texture->pixels.empty()) {
        return 0xFFFFFFFF;
    }
    const int x = std::clamp(static_cast<int>(u * texture->width), 0, texture->width - 1);
    const int y = std::clamp(static_cast<int>(v * texture->height), 0, texture->height - 1);
    return texture->pixels[static_cast<std::size_t>(y) * texture->width + x];
}

/// x / 255, rounded (x up to 255 * 255).
inline unsigned Div255(unsigned x) {
    x += 128;
    return (x + (x >> 8)) >> 8;
}

/// What goes over a pixel: a colour and its opacity, 0..255 each.
struct Paint {
    unsigned r, g, b, a;
};

/// A vertex colour (0..255 each) times a texel.
inline Paint Modulate(unsigned r, unsigned g, unsigned b, unsigned a, ImU32 texel) {
    return {Div255(r * (texel & 0xFF)), Div255(g * ((texel >> 8) & 0xFF)), Div255(b * ((texel >> 16) & 0xFF)),
            Div255(a * (texel >> 24))};
}

inline unsigned Channel(float c) {
    return static_cast<unsigned>(c + 0.5f);
}

/// `p` over the pixel at `dst`.
inline void Blend(const Target& t, Uint32* dst, const Paint& p) {
    if (p.a == 0) {
        return;
    }
    const Uint32 d = *dst;
    unsigned r = p.r, g = p.g, b = p.b;
    if (p.a < 255) {
        const unsigned ia = 255 - p.a;
        r = Div255(r * p.a + ((d >> t.r_shift) & 0xFF) * ia);
        g = Div255(g * p.a + ((d >> t.g_shift) & 0xFF) * ia);
        b = Div255(b * p.a + ((d >> t.b_shift) & 0xFF) * ia);
    }
    *dst = (d & t.keep) | (r << t.r_shift) | (g << t.g_shift) | (b << t.b_shift);
}

/// `count` pixels from `dst` with one `p` over them all.
void FillSpan(const Target& t, Uint32* dst, int count, const Paint& p) {
    if (p.a == 0 || count <= 0) {
        return;
    }
    if (p.a == 255) {
        const Uint32 colour = (p.r << t.r_shift) | (p.g << t.g_shift) | (p.b << t.b_shift);
        for (int i = 0; i < count; ++i) {
            dst[i] = (dst[i] & t.keep) | colour;
        }
        return;
    }
    // Div255(c * a + d * (255 - a)), with what doesn't change worked out once
    const unsigned ia = 255 - p.a, r = p.r * p.a + 128, g = p.g * p.a + 128, b = p.b * p.a + 128;
    for (int i = 0; i < count; ++i) {
        const Uint32 d = dst[i];
        unsigned nr = ((d >> t.r_shift) & 0xFF) * ia + r;
        unsigned ng = ((d >> t.g_shift) & 0xFF) * ia + g;
        unsigned nb = ((d >> t.b_shift) & 0xFF) * ia + b;
        nr = (nr + (nr >> 8)) >> 8;
        ng = (ng + (ng >> 8)) >> 8;
        nb = (nb + (nb >> 8)) >> 8;
        dst[i] = (d & t.keep) | (nr << t.r_shift) | (ng << t.g_shift) | (nb << t.b_shift);
    }
}

inline Uint32* Row(const Target& t, int y) {
    return reinterpret_cast<Uint32*>(t.pixels + static_cast<std::ptrdiff_t>(y) * t.pitch);
}

/// An axis-aligned rectangle, its texture position going linearly from the top left's to the
/// bottom right's: the pixels whose centres are inside.
void DrawRect(const Target& t, const Clip& clip, const Vertex& tl, const Vertex& br, const Texture* texture) {
    const int x0 = std::max(clip.x0, static_cast<int>(std::ceil(tl.x - 0.5f)));
    const int x1 = std::min(clip.x1, static_cast<int>(std::ceil(br.x - 0.5f)));
    const int y0 = std::max(clip.y0, static_cast<int>(std::ceil(tl.y - 0.5f)));
    const int y1 = std::min(clip.y1, static_cast<int>(std::ceil(br.y - 0.5f)));
    if (x0 >= x1 || y0 >= y1) {
        return;
    }
    const unsigned r = Channel(tl.r), g = Channel(tl.g), b = Channel(tl.b), a = Channel(tl.a);
    if (!texture || (tl.u == br.u && tl.v == br.v)) {
        // one colour all over (fills, the menu's dimming)
        const Paint p = Modulate(r, g, b, a, Sample(texture, tl.u, tl.v));
        for (int y = y0; y < y1; ++y) {
            FillSpan(t, Row(t, y) + x0, x1 - x0, p);
        }
        return;
    }
    // a texture's part on it (text)
    const float du = (br.u - tl.u) / (br.x - tl.x), dv = (br.v - tl.v) / (br.y - tl.y);
    for (int y = y0; y < y1; ++y) {
        Uint32* row = Row(t, y);
        const float v = tl.v + (static_cast<float>(y) + 0.5f - tl.y) * dv;
        for (int x = x0; x < x1; ++x) {
            const float u = tl.u + (static_cast<float>(x) + 0.5f - tl.x) * du;
            Blend(t, row + x, Modulate(r, g, b, a, Sample(texture, u, v)));
        }
    }
}

/// Whether edge a->b is a top or a left one (for the triangles' orientation below): a pixel
/// centre right on an edge two triangles share is drawn by one of them only.
inline bool TopLeft(const Vertex& a, const Vertex& b) {
    return (a.y == b.y && b.x > a.x) || b.y < a.y;
}

inline float Edge(const Vertex& a, const Vertex& b, float x, float y) {
    return (b.x - a.x) * (y - a.y) - (b.y - a.y) * (x - a.x);
}

/// A triangle, its colours and texture positions interpolated across it.
void DrawTriangle(const Target& t, const Clip& clip, Vertex p0, Vertex p1, Vertex p2, const Texture* texture) {
    float area = Edge(p0, p1, p2.x, p2.y);
    if (area == 0.0f) {
        return;
    }
    if (area < 0.0f) {
        std::swap(p1, p2);
        area = -area;
    }
    const int x0 = std::max(clip.x0, static_cast<int>(std::floor(std::min({p0.x, p1.x, p2.x}))));
    const int x1 = std::min(clip.x1, static_cast<int>(std::ceil(std::max({p0.x, p1.x, p2.x}))));
    const int y0 = std::max(clip.y0, static_cast<int>(std::floor(std::min({p0.y, p1.y, p2.y}))));
    const int y1 = std::min(clip.y1, static_cast<int>(std::ceil(std::max({p0.y, p1.y, p2.y}))));
    if (x0 >= x1 || y0 >= y1) {
        return;
    }
    const bool tl0 = TopLeft(p1, p2), tl1 = TopLeft(p2, p0), tl2 = TopLeft(p0, p1);
    // the edge functions step by these from one pixel to the next on a row
    const float s0 = -(p2.y - p1.y), s1 = -(p0.y - p2.y), s2 = -(p1.y - p0.y);
    const float inv = 1.0f / area;
    const bool flat_colour = p0.r == p1.r && p1.r == p2.r && p0.g == p1.g && p1.g == p2.g && p0.b == p1.b &&
                             p1.b == p2.b && p0.a == p1.a && p1.a == p2.a;
    const bool flat_texel = !texture || (p0.u == p1.u && p1.u == p2.u && p0.v == p1.v && p1.v == p2.v);
    const ImU32 flat = Sample(texture, p0.u, p0.v);
    const Paint flat_paint = Modulate(Channel(p0.r), Channel(p0.g), Channel(p0.b), Channel(p0.a), flat);
    if (flat_colour && flat_texel && flat_paint.a == 0) {
        return;
    }
    const float last = static_cast<float>(x1 - x0 - 1);
    for (int y = y0; y < y1; ++y) {
        const float cy = static_cast<float>(y) + 0.5f, cx = static_cast<float>(x0) + 0.5f;
        const float e0 = Edge(p1, p2, cx, cy), e1 = Edge(p2, p0, cx, cy), e2 = Edge(p0, p1, cx, cy);
        // the row's pixels each edge can let in, give or take one (the exact test is below)
        float lo = 0.0f, hi = last;
        bool empty = false;
        for (const auto& [e, s] : {std::pair{e0, s0}, std::pair{e1, s1}, std::pair{e2, s2}}) {
            if (s > 0.0f) {
                lo = std::max(lo, -e / s - 1.0f);
            } else if (s < 0.0f) {
                hi = std::min(hi, -e / s + 1.0f);
            } else if (e < 0.0f) {
                empty = true;
            }
        }
        if (empty || lo > hi) {
            continue;
        }
        const int k0 = static_cast<int>(std::floor(lo)), k1 = static_cast<int>(std::ceil(hi));
        const auto inside = [&](int k) {
            const float w0 = e0 + s0 * static_cast<float>(k), w1 = e1 + s1 * static_cast<float>(k);
            const float w2 = e2 + s2 * static_cast<float>(k);
            return !((w0 < 0.0f || (w0 == 0.0f && !tl0)) || (w1 < 0.0f || (w1 == 0.0f && !tl1)) ||
                     (w2 < 0.0f || (w2 == 0.0f && !tl2)));
        };
        Uint32* row = Row(t, y) + x0;
        if (flat_colour && flat_texel) {
            // the row's pixels inside are one run: its two ends, and one fill
            int first = k0, end = k1 + 1;
            while (first < end && !inside(first)) {
                ++first;
            }
            while (end > first && !inside(end - 1)) {
                --end;
            }
            FillSpan(t, row + first, end - first, flat_paint);
            continue;
        }
        for (int k = k0; k <= k1; ++k) {
            if (!inside(k)) {
                continue;
            }
            const float w0 = e0 + s0 * static_cast<float>(k), w1 = e1 + s1 * static_cast<float>(k);
            const float w2 = e2 + s2 * static_cast<float>(k);
            const float b0 = w0 * inv, b1 = w1 * inv, b2 = w2 * inv;
            const ImU32 texel =
                flat_texel ? flat
                           : Sample(texture, p0.u * b0 + p1.u * b1 + p2.u * b2, p0.v * b0 + p1.v * b1 + p2.v * b2);
            if (flat_colour) {
                Blend(t, row + k, Modulate(Channel(p0.r), Channel(p0.g), Channel(p0.b), Channel(p0.a), texel));
            } else {
                Blend(t, row + k,
                      Modulate(Channel(p0.r * b0 + p1.r * b1 + p2.r * b2), Channel(p0.g * b0 + p1.g * b1 + p2.g * b2),
                               Channel(p0.b * b0 + p1.b * b1 + p2.b * b2), Channel(p0.a * b0 + p1.a * b1 + p2.a * b2),
                               texel));
            }
        }
    }
}

/// Whether the six indices at `idx` are one of Dear ImGui's rectangles: corners a b c d
/// clockwise from the top left, as (a b c) (a c d), one colour, the texture square to them.
bool IsRect(const ImDrawIdx* idx, const ImDrawVert* vtx) {
    if (idx[3] != idx[0] || idx[4] != idx[2]) {
        return false;
    }
    const ImDrawVert &a = vtx[idx[0]], &b = vtx[idx[1]], &c = vtx[idx[2]], &d = vtx[idx[5]];
    return a.pos.y == b.pos.y && b.pos.x == c.pos.x && c.pos.y == d.pos.y && d.pos.x == a.pos.x &&
           a.pos.x < b.pos.x && a.pos.y < d.pos.y && a.col == b.col && b.col == c.col && c.col == d.col &&
           a.uv.y == b.uv.y && b.uv.x == c.uv.x && c.uv.y == d.uv.y && d.uv.x == a.uv.x;
}

void Render(ImDrawData* data, SDL_Surface* surface) {
    if (data->Textures) {
        for (ImTextureData* tex : *data->Textures) {
            if (tex->Status != ImTextureStatus_OK) {
                UpdateTexture(tex);
            }
        }
    }
    const SDL_PixelFormat* f = surface->format;
    if (f->BytesPerPixel != 4 || f->Rloss || f->Gloss || f->Bloss) {
        if (!unsupported_logged) {
            unsupported_logged = true;
            LOG_ERROR(Frontend, "menu: can't draw on a {} surface", SDL_GetPixelFormatName(f->format));
        }
        return;
    }
    if (SDL_MUSTLOCK(surface) && SDL_LockSurface(surface) != 0) {
        return;
    }
    const Target t{static_cast<Uint8*>(surface->pixels),
                   surface->pitch,
                   surface->w,
                   surface->h,
                   f->Rshift,
                   f->Gshift,
                   f->Bshift,
                   ~(f->Rmask | f->Gmask | f->Bmask)};
    const ImVec2 off = data->DisplayPos, scale = data->FramebufferScale;
    const auto vertex = [&](const ImDrawVert& v) {
        return Vertex{(v.pos.x - off.x) * scale.x,
                      (v.pos.y - off.y) * scale.y,
                      v.uv.x,
                      v.uv.y,
                      static_cast<float>(v.col & 0xFF),
                      static_cast<float>((v.col >> 8) & 0xFF),
                      static_cast<float>((v.col >> 16) & 0xFF),
                      static_cast<float>(v.col >> 24)};
    };
    for (const ImDrawList* list : data->CmdLists) {
        for (const ImDrawCmd& cmd : list->CmdBuffer) {
            if (cmd.UserCallback) {
                continue; // the menu adds none
            }
            const Clip clip{std::max(0, static_cast<int>(std::floor((cmd.ClipRect.x - off.x) * scale.x))),
                            std::max(0, static_cast<int>(std::floor((cmd.ClipRect.y - off.y) * scale.y))),
                            std::min(t.width, static_cast<int>(std::ceil((cmd.ClipRect.z - off.x) * scale.x))),
                            std::min(t.height, static_cast<int>(std::ceil((cmd.ClipRect.w - off.y) * scale.y)))};
            if (clip.x0 >= clip.x1 || clip.y0 >= clip.y1) {
                continue;
            }
            const auto* texture = reinterpret_cast<const Texture*>(static_cast<std::uintptr_t>(cmd.GetTexID()));
            const ImDrawVert* vtx = list->VtxBuffer.Data + cmd.VtxOffset;
            const ImDrawIdx* idx = list->IdxBuffer.Data + cmd.IdxOffset;
            for (unsigned i = 0; i + 2 < cmd.ElemCount;) {
                if (i + 5 < cmd.ElemCount && IsRect(idx + i, vtx)) {
                    DrawRect(t, clip, vertex(vtx[idx[i]]), vertex(vtx[idx[i + 2]]), texture);
                    i += 6;
                    continue;
                }
                DrawTriangle(t, clip, vertex(vtx[idx[i]]), vertex(vtx[idx[i + 1]]), vertex(vtx[idx[i + 2]]),
                             texture);
                i += 3;
            }
        }
    }
    if (SDL_MUSTLOCK(surface)) {
        SDL_UnlockSurface(surface);
    }
}

} // namespace

void DrawSoftware(SDL_Surface* surface) {
    Layer* layer = Get();
    if (!layer || !surface) {
        return;
    }
    auto lock = layer->Lock();
    layer->MakeCurrent();
    if (!initialized) {
        ImGuiIO& io = ImGui::GetIO();
        io.BackendRendererName = "mh4u_software";
        io.BackendFlags |= ImGuiBackendFlags_RendererHasVtxOffset | ImGuiBackendFlags_RendererHasTextures;
        layer->SetTexturedLines(false); // the texels here are the nearest ones, not filtered
        initialized = true;
        layer->SetRendererReady(true);
    }
    if (!layer->Visible()) {
        return;
    }
    if (ImDrawData* data = layer->DrawData(static_cast<u32>(surface->w), static_cast<u32>(surface->h))) {
        Render(data, surface);
    }
}

void ShutdownSoftware() {
    Layer* layer = Get();
    if (!layer || !initialized) {
        return;
    }
    auto lock = layer->Lock();
    layer->MakeCurrent();
    layer->SetRendererReady(false);
    DestroyTextures();
    ImGuiIO& io = ImGui::GetIO();
    io.BackendRendererName = nullptr;
    io.BackendFlags &= ~(ImGuiBackendFlags_RendererHasVtxOffset | ImGuiBackendFlags_RendererHasTextures);
    initialized = false;
}

} // namespace App::Ui
