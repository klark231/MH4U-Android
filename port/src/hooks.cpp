#include "hooks.h"
#include "netsync.h"

#include <algorithm>
#include <atomic>
#include <cctype>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iterator>
#include <sstream>
#include <string_view>
#include <vector>
#include <fmt/format.h>

#include "common/common_types.h"
#include "common/file_util.h"
#include "common/logging/log.h"
#include "core/frontend/top_screen.h"
#include "common/settings.h"
#include "core/3ds.h"
#include "core/core.h"
#include "video_core/gpu.h"
#include "video_core/pica/pica_core.h"
#include "video_core/renderer_base.h"

// Hooks read and write guest memory through Azahar's page table, like recompiled code.
#define RC_PAGED
extern "C" {
#include "recomp.h"
}

namespace Port::Hooks {

namespace {

// USA v1.3.0 addresses and offsets (docs/research/system-services.md, widescreen.md).
constexpr u32 SMAIN_INSTANCE = 0x01057194;  // sMhMain*: the engine's main system (an sMain)
constexpr u32 SMAIN_RATE = 0x2c;            // f32: the unit of delta, in frames per second (60)
constexpr u32 SMAIN_CAP = 0x30;             // f32: frame-rate cap of the frame limiter
constexpr u32 SMAIN_FPS = 0x34;             // f32: measured frame rate, refreshed each second
constexpr u32 SMAIN_DELTA = 0x5c;           // f32: last frame's length in 1/rate s
constexpr u32 VBLANK_TOP = 0x0110d004;      // VBlank counters kept by the GX library
constexpr u32 VBLANK_BOTTOM = 0x0110d008;
constexpr u32 FRAMES_SUBMITTED = 0x010571d4;
constexpr u32 SLTRENDER_INSTANCE = 0x010572cc;
constexpr u32 SLTRENDER_DISPLAY_SKIP = 0x8aa8;  // u32[2] mDisplaySkip: top, bottom
// Render settings word: bits set by sLtRender's property setters (0x00bf685c registers them):
// 0x1 mVSyncEdit, 0x4 mTripleBufferRenderWaitEdit, 0x8 mUpsideToTopEdit, 0x10 mYFlipEdit,
// 0x40 mULCDEnableEdit, 0x100 mParallelRenderingEdit, 0x400 mFSAA_2D,
// 0x100000 mDitherDisplay0, 0x200000 mDitherDisplay1, 0x400000 mProfileRFPS
constexpr u32 SLTRENDER_RENDER_FLAGS = 0x6ba8;
constexpr u32 RENDER_DITHER = 0x300000;         // mDitherDisplay0 | mDitherDisplay1
constexpr u32 RENDER_STEREO = 0x40;             // mULCDEnableEdit: the top screen drawn once per eye
constexpr u32 RENDER_ANTIALIASING = 0x4400;     // mFSAA_2D (0x400) and 0x4000: the game's anti-aliasing
                                                // (the community's Trinity Disabler clears both; without
                                                // them the edges are jagged)
constexpr u32 SCAMERA_INSTANCE = 0x0105729c;    // sMhCamera*
constexpr u32 SCAMERA_VIEWPORTS = 0x18;         // 8 viewports of 0x1a8 bytes
constexpr u32 VIEWPORT_SIZE = 0x1a8;
constexpr u32 VIEWPORT_CAMERA = 0x04;           // uCamera*
constexpr u32 VIEWPORT_DISPLAY = 0x16;          // u8: 0 top screen, 1 bottom screen
constexpr u32 VIEWPORT_SCENE_TEXTURE = 0x0c;    // sub-scene texture: region from its size
constexpr u32 VIEWPORT_Y0 = 0x28, VIEWPORT_Y1 = 0x30;  // region rows (the screen's width)
constexpr u32 VIEWPORT_MODE = 0x15;             // u8 mMode: 0 the whole 400 rows, 2..9 halves or
                                                // quarters of them, 1 and >= 10 a region its
                                                // owner sets (0xa: the virtual-rect mode)
constexpr u32 SLTRENDER_SCENE = 0x6c28;         // nDraw::Scene*: the main render target
constexpr u32 SLTRENDER_TOP_TRANSFER_Y = 0x6e1c;   // u32: first row of the top screen's image in
                                                   // the composited target (512 - 400 = 112)
constexpr u32 SLTRENDER_BOTTOM_TRANSFER_Y = 0x6e24;  // u32: the same for the bottom screen's (112)
constexpr u32 SCENE_TARGET = 0x50, TARGET_TEXTURES = 0x34, TEXTURE_SIZE = 0x18;
constexpr u32 TOP_ROWS = 400, LCD_COLUMNS = 240;   // top screen in framebuffer orientation
constexpr float NATIVE_ASPECT = 400.0f / 240.0f;

float ReadF32(RCtx* ctx, u32 addr) {
    const u32 v = rd32(ctx, addr);
    float f;
    std::memcpy(&f, &v, sizeof(f));
    return f;
}

void WriteF32(RCtx* ctx, u32 addr, float f) {
    u32 v;
    std::memcpy(&v, &f, sizeof(v));
    wr32(ctx, addr, v);
}

/// Options from <Azahar user dir>/mh4u.ini, overridden by environment variables.
struct Options {
    float fps_cap = 0;      // fps / MH4U_FPS: frame-rate cap, 0 = the game's own
    float refresh_rate = 0; // refresh_rate / MH4U_REFRESH_RATE: emulated LCD Hz, 0 = the 3DS's
                            // (or, with an fps above 60, that fps: see RefreshRate)
    float widescreen = 0;   // widescreen / MH4U_WIDESCREEN: top-screen aspect ratio, 0 = native
    bool wide_render = false;  // wide_render / MH4U_WIDE_RENDER: render the wider image itself
    u32 trace_every = 0;    // trace_frames / MH4U_TRACE_FRAMES: log frame timing every n frames
    bool trace_panels = false;  // trace_panels / MH4U_TRACE_PANELS: log touch-panel activity
    u32 render_set = 0, render_clear = 0;  // render_flags_set/_clear: bits of the render word
    bool dither = true;     // dither / MH4U_DITHER: the game's dither pattern on both screens
    bool stereo_3d = false; // stereo_3d / MH4U_STEREO_3D: the game's stereoscopic 3D (a second pass
                            // of the top screen for the right eye, even with the 3D slider at 0)
    bool antialiasing = true;  // antialiasing / MH4U_ANTIALIASING: the game's own anti-aliasing (off
                               // saves its pass at a high internal resolution, where it matters less)
    u32 kick_button = 0;    // kick / MH4U_KICK: 3DS button that kicks (the game's pad bits)
    u32 target_button = 0;  // target / MH4U_TARGET: 3DS button for the target camera panel
    u32 skip_button = 0x800;  // skip / MH4U_SKIP: 3DS button that taps the touch screen's Skip
                              // once a quest is complete (ZR unless set: the kick does nothing then)
    u32 skip_end_buttons = 0x400 | 0x800;  // skip_end / MH4U_SKIP_END: buttons held together to end
                                           // the wait after a quest (ZL+ZR), with everyone's say-so online
    float skip_end_hold = 1.0f;  // skip_end_hold / MH4U_SKIP_END_HOLD: seconds they are held
    u32 skip_panel = 0xb;   // skip_panel: the touch-screen button whose tap the skip button makes
                            // (the panel manager's Skip, 0xb, as the stylus's tap on it shows)
    int skip_hint = 1;      // skip_hint / MH4U_SKIP_HINT: 0 off, 1 a hint on screen, 2 flashing
    Layout::ScreenOverlay touch_overlay;  // touch_overlay / MH4U_TOUCH_OVERLAY (custom layout)
    PanelView quest_overlay{true};   // quest_overlay / MH4U_QUEST_OVERLAY: in quests, the map
                                     // alone, at its size and place
    PanelView target_overlay{true};  // target_overlay / MH4U_TARGET_OVERLAY: with it, the
                                     // target camera panel in a view of its own
    int quest_overlay_test = -1;  // quest_overlay_test / MH4U_QUEST_OVERLAY_TEST: show the map's
                                  // part of slot n all the time (to check the drawing)
    bool hud_edges = true;  // hud_edges / MH4U_HUD_EDGES: with wide_render, HUD to the edges
    u32 trace_layout = 0;   // trace_layout / MH4U_TRACE_LAYOUT: log HUD objects every n frames
    NetSync::Mode net_sync = NetSync::Mode::Session;  // net_sync / MH4U_NET_SYNC
    float net_sync_slack = 0;  // net_sync_slack / MH4U_NET_SYNC_SLACK: ms, 0 = the default
    u32 filters_off = 0;    // bloom, motion_blur, depth_of_field, radial_blur, haze = off: the
                            // screen filters (FILTER_* bits) the port skips
    bool trace_filters = false;  // trace_filters / MH4U_TRACE_FILTERS: log the filters drawn
};

// The screen filters an option can turn off (see SCREEN_FILTERS below), by option name.
constexpr u32 FILTER_BLOOM = 1u << 0, FILTER_MOTION_BLUR = 1u << 1, FILTER_DEPTH_OF_FIELD = 1u << 2,
              FILTER_RADIAL_BLUR = 1u << 3, FILTER_HAZE = 1u << 4;
constexpr std::pair<const char*, u32> FILTER_OPTIONS[] = {{"bloom", FILTER_BLOOM},
                                                          {"motion_blur", FILTER_MOTION_BLUR},
                                                          {"depth_of_field", FILTER_DEPTH_OF_FIELD},
                                                          {"radial_blur", FILTER_RADIAL_BLUR},
                                                          {"haze", FILTER_HAZE}};

/// The 3DS buttons by name, and the bit sPad::processInput gives each in the held word.
constexpr std::pair<const char*, u32> PAD_BITS[] = {
    {"A", 0x2000}, {"B", 0x4000},  {"X", 0x1000},  {"Y", 0x8000},  {"L", 0x100},
    {"R", 0x200},  {"ZL", 0x400},  {"ZR", 0x800},  {"START", 0x8}, {"UP", 0x10},
    {"DOWN", 0x40}, {"LEFT", 0x80}, {"RIGHT", 0x20}};

/// A 3DS button name -> the bit sPad::processInput gives it in the held word.
u32 PadBit(std::string name) {
    std::transform(name.begin(), name.end(), name.begin(), [](unsigned char c) { return std::toupper(c); });
    for (const auto& [n, bit] : PAD_BITS) {
        if (name == n) {
            return bit;
        }
    }
    return 0;
}

/// The name of the 3DS button with that bit ("ZR"), or "".
std::string PadName(u32 bit) {
    for (const auto& [n, b] : PAD_BITS) {
        if (b == bit) {
            return n;
        }
    }
    return "";
}

/// "ZL+ZR" -> the bits of those buttons (0 for a name that isn't one, or "off").
u32 PadBits(const std::string& names) {
    u32 bits = 0;
    std::stringstream in(names);
    std::string name;
    while (std::getline(in, name, '+')) {
        name.erase(std::remove_if(name.begin(), name.end(), [](unsigned char c) { return std::isspace(c); }),
                   name.end());
        const u32 bit = PadBit(name);
        if (bit == 0) {
            return 0;
        }
        bits |= bit;
    }
    return bits;
}

/// The names of the buttons in `bits`, "ZL+ZR".
std::string PadNames(u32 bits) {
    std::string names;
    for (const auto& [n, b] : PAD_BITS) {
        if (bits & b) {
            names += (names.empty() ? "" : "+") + std::string(n);
        }
    }
    return names;
}

bool IsOn(const std::string& value) {
    return value == "1" || value == "true" || value == "on" || value == "yes";
}

bool IsOff(const std::string& value) {
    return value == "0" || value == "false" || value == "off" || value == "no";
}
Options options;
u64 frames = 0;
int missing_hook_points = 0;  // in the code library Install saw last
std::atomic<bool> quest_map_off{false};  // ToggleQuestMap: the whole touch screen in quests too

/// "16:9", "1.7778", "off"/"0" -> aspect ratio (0 = native).
float ParseAspect(const std::string& v) {
    const auto colon = v.find(':');
    if (colon != std::string::npos) {
        const float w = std::strtof(v.c_str(), nullptr);
        const float h = std::strtof(v.c_str() + colon + 1, nullptr);
        return h > 0 ? w / h : 0.0f;
    }
    return std::max(0.0f, std::strtof(v.c_str(), nullptr));
}

void Set(Options& o, const std::string& key, const std::string& value) {
    if (key == "fps") {
        o.fps_cap = std::max(0.0f, std::strtof(value.c_str(), nullptr));
    } else if (key == "refresh_rate") {
        o.refresh_rate = std::max(0.0f, std::strtof(value.c_str(), nullptr));
    } else if (key == "widescreen") {
        o.widescreen = ParseAspect(value);
    } else if (key == "wide_render") {
        o.wide_render = IsOn(value);
    } else if (key == "dither") {
        o.dither = IsOn(value);
    } else if (key == "stereo_3d") {
        o.stereo_3d = IsOn(value);
    } else if (key == "antialiasing") {
        o.antialiasing = IsOn(value);
    } else if (key == "render_flags_set") {
        o.render_set = static_cast<u32>(std::strtoul(value.c_str(), nullptr, 0));
    } else if (key == "render_flags_clear") {
        o.render_clear = static_cast<u32>(std::strtoul(value.c_str(), nullptr, 0));
    } else if (key == "trace_panels") {
        o.trace_panels = IsOn(value);
    } else if (key == "kick") {
        o.kick_button = PadBit(value);
    } else if (key == "target") {
        o.target_button = PadBit(value);
    } else if (key == "skip") {
        o.skip_button = PadBit(value);
    } else if (key == "skip_end") {
        o.skip_end_buttons = PadBits(value);
    } else if (key == "skip_end_hold") {
        o.skip_end_hold = std::clamp(std::strtof(value.c_str(), nullptr), 0.2f, 10.0f);
    } else if (key == "skip_panel") {
        o.skip_panel = static_cast<u32>(std::clamp(std::strtol(value.c_str(), nullptr, 0), 0L, 0xffL));
    } else if (key == "skip_hint") {
        o.skip_hint = IsOff(value) ? 0 : value == "flash" ? 2 : 1;
    } else if (key == "touch_overlay") {
        o.touch_overlay = ParseOverlay(value);
    } else if (key == "quest_overlay") {
        o.quest_overlay = ParseQuestOverlay(value);
    } else if (key == "target_overlay") {
        o.target_overlay = ParseTargetOverlay(value);
    } else if (key == "quest_overlay_test") {
        // a slot the map can start in: 0, 1, 3 or 4
        const long slot = std::strtol(value.c_str(), nullptr, 10);
        o.quest_overlay_test = slot >= 0 && slot <= 4 && slot != 2 ? static_cast<int>(slot) : -1;
    } else if (key == "net_sync") {
        o.net_sync = NetSync::ParseMode(value);
    } else if (key == "net_sync_slack") {
        o.net_sync_slack = std::max(0.0f, std::strtof(value.c_str(), nullptr));
    } else if (key == "hud_edges") {
        o.hud_edges = IsOn(value);
    } else if (key == "trace_layout") {
        o.trace_layout = static_cast<u32>(std::max(0L, std::strtol(value.c_str(), nullptr, 10)));
    } else if (key == "trace_frames") {
        o.trace_every = static_cast<u32>(std::max(0L, std::strtol(value.c_str(), nullptr, 10)));
    } else if (key == "trace_filters") {
        o.trace_filters = IsOn(value);
    } else {
        for (const auto& [name, bit] : FILTER_OPTIONS) {
            if (key == name) {
                // on (the game's own, as on a 3DS) unless turned off
                o.filters_off = IsOff(value) ? o.filters_off | bit : o.filters_off & ~bit;
            }
        }
    }
}

std::string Trim(const std::string& s) {
    const auto b = s.find_first_not_of(" \t\r\n");
    const auto e = s.find_last_not_of(" \t\r\n");
    return b == std::string::npos ? "" : s.substr(b, e - b + 1);
}

Options LoadOptions(std::vector<std::string>& log) {
    Options o;
    std::string path = FileUtil::GetUserPath(FileUtil::UserPath::UserDir) + "mh4u.ini";
#ifdef ANDROID
    path = AndroidUtils::TranslateFilePath(path);  // the user folder's virtual root, as a real path
#endif
    std::ifstream file(path);
    if (file) {
        std::string line;
        while (std::getline(file, line)) {
            line = Trim(line.substr(0, line.find('#')));
            const auto eq = line.find('=');
            if (eq != std::string::npos) {
                Set(o, Trim(line.substr(0, eq)), Trim(line.substr(eq + 1)));
            }
        }
        log.push_back("options from " + path);
    }
    const std::pair<const char*, const char*> env[] = {
        {"MH4U_FPS", "fps"},
        {"MH4U_REFRESH_RATE", "refresh_rate"},
        {"MH4U_WIDESCREEN", "widescreen"},
        {"MH4U_WIDE_RENDER", "wide_render"},
        {"MH4U_TRACE_FRAMES", "trace_frames"},
        {"MH4U_TRACE_PANELS", "trace_panels"},
        {"MH4U_KICK", "kick"},
        {"MH4U_TARGET", "target"},
        {"MH4U_SKIP", "skip"},
        {"MH4U_SKIP_END", "skip_end"},
        {"MH4U_SKIP_END_HOLD", "skip_end_hold"},
        {"MH4U_SKIP_HINT", "skip_hint"},
        {"MH4U_TOUCH_OVERLAY", "touch_overlay"},
        {"MH4U_QUEST_OVERLAY", "quest_overlay"},
        {"MH4U_TARGET_OVERLAY", "target_overlay"},
        {"MH4U_QUEST_OVERLAY_TEST", "quest_overlay_test"},
        {"MH4U_HUD_EDGES", "hud_edges"},
        {"MH4U_DITHER", "dither"},
        {"MH4U_STEREO_3D", "stereo_3d"},
        {"MH4U_ANTIALIASING", "antialiasing"},
        {"MH4U_RENDER_FLAGS_SET", "render_flags_set"},
        {"MH4U_RENDER_FLAGS_CLEAR", "render_flags_clear"},
        {"MH4U_TRACE_LAYOUT", "trace_layout"},
        {"MH4U_NET_SYNC", "net_sync"},
        {"MH4U_NET_SYNC_SLACK", "net_sync_slack"},
        {"MH4U_BLOOM", "bloom"},
        {"MH4U_MOTION_BLUR", "motion_blur"},
        {"MH4U_DEPTH_OF_FIELD", "depth_of_field"},
        {"MH4U_RADIAL_BLUR", "radial_blur"},
        {"MH4U_HAZE", "haze"},
        {"MH4U_TRACE_FILTERS", "trace_filters"}};
    for (const auto& [var, key] : env) {
        if (const char* value = std::getenv(var)) {
            Set(o, key, value);
        }
    }
    return o;
}

/// The emulated screen refresh rate to use: refresh_rate when set; otherwise an fps above 60
/// (the game presents at most once per VBlank, so more frames need more VBlanks), else 0 (the
/// 3DS's 59.83 Hz).
float RefreshRate() {
    if (options.refresh_rate > 0) {
        return options.refresh_rate;
    }
    return options.fps_cap > 60.0f ? options.fps_cap : 0.0f;
}

/// sMain::updateFrameTiming(this): the frame limiter. Delta time is counted in 1/60 s
/// whatever the cap, and gameplay scales with it, so the cap is what limits the frame rate.
void UpdateFrameTiming(RCtx* ctx, RFunc original) {
    if (options.fps_cap > 0) {
        WriteF32(ctx, ctx->r[0] + SMAIN_CAP, options.fps_cap);
    }
    original(ctx);
}

/// sMhMain::frame(this): one iteration of the main loop.
void WideTransfer(RCtx* ctx);
void UpdateQuestOverlay(RCtx* ctx);
void TraceFilters();

void Frame(RCtx* ctx, RFunc original) {
    if (frames == 0 && RefreshRate() > 0) {
        // the game presents at most once per VBlank: more VBlanks allow more than 60 fps
        Core::System::GetInstance().GPU().SetRefreshRate(RefreshRate());
    }
    const u32 clear = options.render_clear | (options.dither ? 0 : RENDER_DITHER) |
                      (options.stereo_3d ? 0 : RENDER_STEREO) | (options.antialiasing ? 0 : RENDER_ANTIALIASING);
    if (options.render_set || clear) {
        // the game reads these bits every frame (the community's quality cheats write the same
        // word); without the dither bits it draws no dither pattern, which an upscaled picture
        // shows as a screen-door grid; without the stereo bit it draws the top screen once (one
        // eye) instead of twice, as the community's "3D off" cheat does
        if (const u32 r = rd32(ctx, SLTRENDER_INSTANCE)) {
            const u32 flags = rd32(ctx, r + SLTRENDER_RENDER_FLAGS);
            wr32(ctx, r + SLTRENDER_RENDER_FLAGS, (flags & ~clear) | options.render_set);
        }
    }
    WideTransfer(ctx);
    original(ctx);
    ++frames;
    UpdateQuestOverlay(ctx);
    if (options.trace_filters) {
        TraceFilters();
    }
    if (options.trace_every == 0 || frames % options.trace_every != 0) {
        return;
    }
    const u32 main = rd32(ctx, SMAIN_INSTANCE);
    const u32 render = rd32(ctx, SLTRENDER_INSTANCE);
    if (main == 0 || render == 0) {
        return;
    }
    LOG_INFO(Frontend,
             "frame {}: delta {:.3f} fps {:.2f} rate {:.1f} cap {:.1f} vblanks {}/{} "
             "submitted {} display skip {}/{} render flags {:08X}",
             frames, ReadF32(ctx, main + SMAIN_DELTA), ReadF32(ctx, main + SMAIN_FPS),
             ReadF32(ctx, main + SMAIN_RATE), ReadF32(ctx, main + SMAIN_CAP), rd32(ctx, VBLANK_TOP),
             rd32(ctx, VBLANK_BOTTOM), rd32(ctx, FRAMES_SUBMITTED),
             rd32(ctx, render + SLTRENDER_DISPLAY_SKIP),
             rd32(ctx, render + SLTRENDER_DISPLAY_SKIP + 4),
             rd32(ctx, render + SLTRENDER_RENDER_FLAGS));
}

/// Whether `camera` is shown on the bottom screen (only top-screen views get widened).
bool OnBottomScreen(RCtx* ctx, u32 camera) {
    const u32 scamera = rd32(ctx, SCAMERA_INSTANCE);
    if (scamera == 0) {
        return false;
    }
    for (u32 i = 0; i < 8; ++i) {
        const u32 vp = scamera + SCAMERA_VIEWPORTS + i * VIEWPORT_SIZE;
        if (rd32(ctx, vp + VIEWPORT_CAMERA) == camera && rd8(ctx, vp + VIEWPORT_DISPLAY) == 1) {
            return true;
        }
    }
    return false;
}

/// The cameras' getProjMatrix(): returns the projection by value (result pointer in r0,
/// the camera in r1). Widescreen: divide m00 so the view gets wider by target/native
/// (Hor+); culling and stereo are derived from this matrix, so they follow. The frontend
/// shows the top screen stretched to the same aspect ratio (Azahar's aspect setting). An
/// aspect ratio narrower than the 3DS's (16:10, 4:3) narrows the view the same way, so that
/// the picture squeezed to that shape keeps its proportions.
u32 WideRows();

void GetProjMatrix(RCtx* ctx, RFunc original) {
    const u32 out = ctx->r[0];
    const u32 camera = ctx->r[1];
    original(ctx);
    // with wide_render the game's own aspect is already W/240; what is left (a target wider
    // than the widest render target, or narrower than the 3DS's) is done here and the frontend
    // stretches or squeezes the picture to it
    const float rendered = WideRows() ? WideRows() / static_cast<float>(LCD_COLUMNS) : NATIVE_ASPECT;
    if (options.widescreen > 0.0f && std::fabs(options.widescreen - rendered) > 0.001f &&
        !OnBottomScreen(ctx, camera)) {
        WriteF32(ctx, out, ReadF32(ctx, out) * rendered / options.widescreen);
    }
}

// ── True widescreen (wide_render, experimental): the top screen is rendered W pixels wide
// instead of 400 (docs/research/widescreen.md, "What has to grow"). The aspect ratio,
// projection, culling, GPU viewport and display transfer all follow from the sizes below.

/// W: the widescreen width rounded to whole 8-pixel tiles, or 0 when off. The scene target is
/// a texture with a power-of-two number of rows: 512 up to W = 512, then 1024 (twice the VRAM,
/// which the game has room for). PICA framebuffers have at most 1024 rows, so that is the limit;
/// a wider aspect ratio would get the rest from the projection (anamorphic).
constexpr u32 MAX_WIDE_ROWS = 1024;
u32 WideRows() {
    if (!options.wide_render || options.widescreen <= NATIVE_ASPECT) {
        return 0;
    }
    const u32 rows = static_cast<u32>(std::lround(LCD_COLUMNS * options.widescreen / 8.0f)) * 8;
    return std::min(rows, MAX_WIDE_ROWS);
}

/// nDraw::Scene::Scene(this, params): params {columns, rows, ...}; the main scene target.
void SceneCtor(RCtx* ctx, RFunc original) {
    const u32 rows = WideRows();
    const u32 params = ctx->r[1];
    if (rows && rd32(ctx, params) == LCD_COLUMNS && rd32(ctx, params + 4) == TOP_ROWS) {
        wr32(ctx, params + 4, rows);
        LOG_INFO(Frontend, "wide render: scene target {}x{}", rows, LCD_COLUMNS);
    }
    original(ctx);
}

/// nDraw::DisplayBuffer::init(this, columns, rows, format, ...): the top screen's buffers;
/// Azahar's LCD then has to show that many rows.
void DisplayBufferInit(RCtx* ctx, RFunc original) {
    const u32 rows = WideRows();
    if (rows && ctx->r[1] == LCD_COLUMNS && ctx->r[2] == TOP_ROWS) {
        ctx->r[2] = rows;
        Core::System::GetInstance().GPU().PicaCore().regs.framebuffer_config[0].height.Assign(rows);
    }
    original(ctx);
}

/// The rows each viewport with a region of its owner's was last given here, so that the region
/// is moved once and not again on every call.
struct OwnerRegion {
    u32 viewport, y0, y1;
};
std::vector<OwnerRegion> owner_regions;

/// sCamera::Viewport::updateRegion(this): top-screen viewports drawn into the scene target.
/// The regions updateRegion computes from the 400 rows (modes 0 and 2..9, every call) are
/// scaled to the wider target. A region its owner set (mode 1, >= 10), such as the weapon
/// preview at the smithy, stays as it is between calls; it frames a 3D view inside a HUD box,
/// and the HUD is centred in the wider picture, so it moves by half the extra rows, once (a
/// region over the whole 400 rows is widened instead).
void UpdateRegion(RCtx* ctx, RFunc original) {
    const u32 viewport = ctx->r[0];
    original(ctx);
    const u32 rows = WideRows();
    if (!rows || rd8(ctx, viewport + VIEWPORT_DISPLAY) != 0 ||
        rd32(ctx, viewport + VIEWPORT_SCENE_TEXTURE) != 0) {
        return;
    }
    const u32 render = rd32(ctx, SLTRENDER_INSTANCE);
    if (render == 0 || rd32(ctx, render + SLTRENDER_SCENE) == 0) {
        return;
    }
    const u8 mode = rd8(ctx, viewport + VIEWPORT_MODE);
    if (mode == 0 || (mode >= 2 && mode <= 9)) {
        for (const u32 field : {VIEWPORT_Y0, VIEWPORT_Y1}) {
            const u32 y = rd32(ctx, viewport + field);
            if (y <= TOP_ROWS) {
                wr32(ctx, viewport + field, y * rows / TOP_ROWS);
            }
        }
        return;
    }
    if (mode == 0xa) {
        return; // setupDraw puts its own rectangle in (a tunable makes it W rows)
    }
    const u32 y0 = rd32(ctx, viewport + VIEWPORT_Y0);
    const u32 y1 = rd32(ctx, viewport + VIEWPORT_Y1);
    auto it = std::find_if(owner_regions.begin(), owner_regions.end(),
                           [viewport](const OwnerRegion& r) { return r.viewport == viewport; });
    if (it != owner_regions.end() && it->y0 == y0 && it->y1 == y1) {
        return; // moved already
    }
    if (y0 > y1 || y1 > TOP_ROWS) {
        return; // not a region of the native 400 rows
    }
    const bool whole = y0 == 0 && y1 == TOP_ROWS;
    const u32 shift = (rows - TOP_ROWS) / 2;
    const u32 new_y0 = whole ? 0 : y0 + shift;
    const u32 new_y1 = whole ? rows : y1 + shift;
    wr32(ctx, viewport + VIEWPORT_Y0, new_y0);
    wr32(ctx, viewport + VIEWPORT_Y1, new_y1);
    if (it == owner_regions.end()) {
        owner_regions.push_back({viewport, new_y0, new_y1});
    } else {
        it->y0 = new_y0;
        it->y1 = new_y1;
    }
}

/// The display transfer copies the top screen from row `512 - 400` of the composited target
/// (the GPU draws from the bottom up, so the image ends at the last row). With W rows, the
/// image starts at `height - W`; the sLtRender constructor writes 112, so set it every frame.
/// The bottom screen is drawn into the same target afterwards, in the region (0, 80)-(240, 400)
/// (viewport 7), and copied from row `height - 400`: still 112 while the target has 512 rows,
/// but 624 once a W over 512 makes it 1024 rows tall.
void WideTransfer(RCtx* ctx) {
    const u32 rows = WideRows();
    const u32 render = rows ? rd32(ctx, SLTRENDER_INSTANCE) : 0;
    const u32 scene = render ? rd32(ctx, render + SLTRENDER_SCENE) : 0;
    const u32 target = scene ? rd32(ctx, scene + SCENE_TARGET) : 0;
    const u32 textures = target ? rd32(ctx, target + TARGET_TEXTURES) : 0;
    const u32 texture = textures ? rd32(ctx, textures) : 0;
    if (texture == 0) {
        return;
    }
    const u32 height = (rd32(ctx, texture + TEXTURE_SIZE) >> 16) & 0x7FFF;
    if (height >= rows && rd32(ctx, render + SLTRENDER_TOP_TRANSFER_Y) != height - rows) {
        wr32(ctx, render + SLTRENDER_TOP_TRANSFER_Y, height - rows);
        LOG_INFO(Frontend, "wide render: top screen transferred from row {} of {}", height - rows, height);
    }
    if (height >= TOP_ROWS && rd32(ctx, render + SLTRENDER_BOTTOM_TRANSFER_Y) != height - TOP_ROWS) {
        wr32(ctx, render + SLTRENDER_BOTTOM_TRANSFER_Y, height - TOP_ROWS);
        LOG_INFO(Frontend, "wide render: bottom screen transferred from row {} of {}", height - TOP_ROWS,
                 height);
    }
}

// ── The HUD in wide render (docs/research/widescreen.md, "2D HUD"). The rLayout HUD keeps
// its 400-pixel layout space, centred in the wider picture. Each layout object's translation
// goes to the GPU as a shader constant written by 0x00ae65a4; the objects that make up the
// sides of the hunting HUD get theirs moved by half the extra width, out to the edges. They
// are told apart by the hash at the start of each object (its name's, it seems: the same
// objects have the same hashes in every run). Layout x grows to the left: pixel = 200 - x.
constexpr u32 CDRAW_OBJECT_CONSTANTS = 0x83c;  // cDraw: where 0x00ae65a4 put (x, y, z, 1), or 0
constexpr u32 CDRAW_SCREEN = 0x1bc;             // cDraw: bits 1-5, the screen being drawn (0 top)
constexpr u32 LAYOUT_TOUCH_SCREEN = 7;          //   (7 the touch screen's: viewport 7)
constexpr u32 HUD_LEFT[] = {
    0x2E7FA61D,  // clock
    0x8293D551,  // health
    0xD07F4AA2,  // stamina
    0xBD56507D,  // weapon sharpness
    0xB2DA1296,  // next to the sharpness
    0x8AC1CCC4,  // party list: the hunter
    0xFDC6FC52,  // party list: the Palico
    0x64CFADE8,  // party list: a second hunter (local play)
    0x13C89D7E,  // party list: the second hunter's Palico
    0x4639C2C3,  // party list rows that were empty when traced (after the rows above, before the
    0x60497133,  //   item bar's objects): the other players' rows in a larger party
    0x174E41A5,
    0x892AD406,
};
constexpr u32 HUD_RIGHT[] = {
    0x1833E2C8,  // item selection
    0x3C598C15,  // item name
};

bool WatchingQuestMap();                       // the quest's touch panels (below)
void NoteTouchScreenObject(float x, float y);

/// 0x00ae65a4(object, cDraw): uploads a layout object's translation and colour.
void LayoutObjectConstants(RCtx* ctx, RFunc original) {
    const u32 object = ctx->r[0];
    const u32 draw = ctx->r[1];
    original(ctx);
    const u32 rows = options.hud_edges ? WideRows() : 0;
    const bool trace = options.trace_layout && frames % options.trace_layout == 0;
    if (!rows && !trace && !WatchingQuestMap()) {
        return;
    }
    const u32 constants = rd32(ctx, draw + CDRAW_OBJECT_CONSTANTS);
    if (constants == 0) {
        return;
    }
    const u32 screen = rd32(ctx, draw + CDRAW_SCREEN) >> 1 & 0x1f;
    const u32 hash = rd32(ctx, object);
    if (trace) {
        LOG_INFO(Frontend, "layout: frame {} object {:08X} hash {:08X} screen {} at {:.1f}, {:.1f}", frames, object,
                 hash, screen, ReadF32(ctx, constants), ReadF32(ctx, constants + 4));
    }
    if (screen == LAYOUT_TOUCH_SCREEN) {
        NoteTouchScreenObject(ReadF32(ctx, constants), ReadF32(ctx, constants + 4));
        return;
    }
    if (rows == 0 || screen != 0) {
        return;
    }
    const float shift = (static_cast<float>(rows) - TOP_ROWS) / 2.0f;
    if (std::find(std::begin(HUD_LEFT), std::end(HUD_LEFT), hash) != std::end(HUD_LEFT)) {
        WriteF32(ctx, constants, ReadF32(ctx, constants) + shift);
    } else if (std::find(std::begin(HUD_RIGHT), std::end(HUD_RIGHT), hash) != std::end(HUD_RIGHT)) {
        WriteF32(ctx, constants, ReadF32(ctx, constants) - shift);
    }
}

/// Sets every tunable with this name (config/tunables.txt); returns how many there were.
u32 SetTunables(const RCodeInfo& info, std::string_view name, u32 value) {
    u32 set = 0;
    for (u32 i = 0; i < info.tunable_count; ++i) {
        if (info.tunable_names[i] && name == info.tunable_names[i]) {
            info.tunables[i] = value;
            ++set;
        }
    }
    return set;
}

/// Shows the top screen at the widescreen aspect ratio: every screen layout gets a top
/// screen that wide, and the single-screen layout uses the matching aspect setting.
void SetScreenAspect(float aspect, std::vector<std::string>& log) {
    Layout::SetTopScreenWidth(static_cast<u32>(std::lround(LCD_COLUMNS * aspect)));
    using Settings::AspectRatio;
    const std::pair<float, AspectRatio> known[] = {{16.0f / 9.0f, AspectRatio::R16_9},
                                                   {16.0f / 10.0f, AspectRatio::R16_10},
                                                   {21.0f / 9.0f, AspectRatio::R21_9},
                                                   {4.0f / 3.0f, AspectRatio::R4_3}};
    AspectRatio choice = AspectRatio::Stretch;
    for (const auto& [value, ratio] : known) {
        if (std::fabs(aspect - value) < 0.01f) {
            choice = ratio;
        }
    }
    Settings::values.aspect_ratio.SetValue(choice);
    log.push_back(choice == AspectRatio::Stretch
                      ? "screen layouts show the top screen wider; single screen stretches it to the window"
                      : "screen layouts show the top screen at that aspect ratio");
}

// ── Touch panels (docs/research/second-screen.md): tracing, to map panel ids to actions
// before binding them to buttons.
constexpr u32 PANEL_MANAGER = 0x0109a350;
constexpr u32 PANEL_FLAGS = 0xcc;     // u16: event flags of the last touch (0x0200 tapped)
constexpr u32 PANEL_ID = 0xce;        // u16: id of the panel touched
constexpr u32 SMHPAD_INJECTED = 0x3f0;   // u32: gameplay action bits set by panels
constexpr u32 SMHPAD_INJECTED_MENU = 0x3f4;
constexpr u32 SPAD_HELD = 0x2c + 0x60;   // u32: buttons held, as sPad::processInput encodes them
constexpr u32 ACTION_KICK = 0x0001;      // the action no button produces: panel 7, the kick
u32 last_panel_touch = 0xFFFFFFFF;
// The target camera panel (panel type 13). Each frame the quest HUD's target driver
// (0x00b94804) runs the panel's taps through 0x00b8d024, which also takes a request byte
// as a tap: with one monster it toggles the target camera on it, with two it cycles
// first, second, off (a monster not yet seen gets the "no" sound). The HUD clears the byte
// at the end of the same update (0x00b9dfe0), and the game sets it itself from a menu
// action in one control mode only. sCamera+0xed5 is the result: 0 off, n monster n.
constexpr u32 QUEST_HUD = 0x0108260c;     // static (getQuestHud 0x0013f230)
constexpr u32 HUD_TARGET_REQUEST = 0x27e;  // u8: toggle the target camera as a tap would
// The quest HUD's update (0x00b9dfe0) switches on its state: in state 2 (hunting) it runs the
// panels' state machine (0x00b9ba38) unless the panel manager is busy (0x002a4da0, as it is
// while the area changes), and that runs the small panels in its states 0 and 1, a big panel
// in 2 and the way back in 3. START takes the HUD to states 3 and 4 while the quest menu is
// open (its touch screen has Customize Panels, Panel Schemes and the like); 0 and 1 set the
// HUD up again afterwards. While someone talks the HUD isn't updated at all.
constexpr u32 HUD_STATE = 0x2a8;        // u8
constexpr u8 HUD_HUNTING = 2, HUD_MENU_OPENING = 3, HUD_MENU = 4;
constexpr u32 HUD_PANEL_STATE = 0x2a9;  // u8
constexpr u8 PANELS_BIG = 2;
constexpr u32 PANEL_BUSY = 0xac;        // u8 (panel manager)
u32 target_held = 0;
// The skip after a quest (docs/research/second-screen.md, "Skipping the wait after a quest").
// Once the quest is complete the touch screen shows "Skip" (the panel manager's fixed
// button 0xb) while the scene of the monster's end plays, and a tap on it skips the scene;
// the game handles that tap inside PanelManager::processTouch (0x002951fc), in the state
// machine that follows the hit test (0x00292ea4: for 0xb the camera's skip), not through
// isTapped. So the skip button's tap is made where the hit test leaves its result:
// processTouch calls 0x0028f2f0 (whether touches are allowed) with the hit in r5, the index
// of the touched button plus event flags; the hook on it puts the Skip button's index there
// for three frames, "pressed", "held" and "released on it, activated", as the maintainer's
// log shows the stylus's tap (4001, 1001, 0A01), and processTouch goes on as for a real tap.
// UpdateSkip (below, once a frame) works out when the skip is offered.
constexpr u16 PANEL_TAP_FLAGS = 0x0a00;  // released on the button pressed (0x0800), activated
                                         // (0x0200)
constexpr u16 PANEL_PRESS_FLAGS = 0x4000;  // pressed on the button
constexpr u16 PANEL_HELD_FLAGS = 0x1000;   // still on it
constexpr u32 PANEL_MODE = 0xb1;         // u8: which buttons the touch screen has (the id table)
constexpr u32 PANEL_ID_TABLES = 0x00df5410;  // u32[4] per mode: the slot of each hit index
constexpr u32 PANEL_SLOT_IDS = 0xb4;     // u8[]: the id of the panel in each slot (processTouch 0x00295800)
constexpr u32 PANEL_TOUCH_ALLOWED = 0x0028f2f0;  // the call processTouch makes with the hit in r5
constexpr u32 PANEL_TOUCH_ALLOWED_RETURN = 0x002957a4;  // its return address in processTouch
// The wait itself (aQuest::playing 0x0065c18c, hooked as QuestPlaying below)
constexpr u32 AQUEST_WAIT_LEFT = 0x3c;    // s32: units of the wait left (loaded from the holder's
                                          // +0xad60: 20 s × sMain+0x2c)
constexpr u32 AQUEST_END_STAGE = 0x48;    // u32: the end sequence's stage, 0 while waiting
constexpr u32 QUEST_STATE_PHASE = 0x24;   // u8 in the state block: 0 playing, 1 waiting, then over
constexpr u8 QUEST_PHASE_WAITING = 1;
std::atomic<bool> skip_offered{false};   // SkipOffered()
bool skip_held = false;                  // the skip button, last frame
int skip_tap = 0;                        // frames of the tap left: 3 pressed, 2 held, 1 released
bool skip_tap_direct = false;            // the tap couldn't go through the hit test: write
                                         // the id and flags after processTouch instead
bool skip_tap_seen = false;              // isTapped has reported that tap
u32 skip_polls = 0;                      // isTapped asked about the button while offered
// The end of the wait (skip_end): the buttons held for skip_end_hold seconds ask for it; the
// wait is cut once every port console in the session asks (NetSync), by QuestPlaying
double end_hold_since = 0;               // emulated seconds when the hold began, 0 while not held
std::atomic<float> end_hold_fraction{0}; // EndSkipHold(): 0..1 of the hold, 1 once asked
std::atomic<bool> end_requested{false};  // EndSkipRequested(): this player asked
bool end_cut = false;                    // the wait was cut this quest
void UpdateSkip(RCtx* ctx);
double EmulatedSeconds();

/// The hit index that is the button `panel` on the touch screen now, or -1. processTouch
/// turns a hit index into a slot through the mode's table (0x00df5410) and the slot into
/// the panel's id through the manager's slot bytes (+0xb4 + slot); the maintainer's log
/// showed a hit on index 2 (slot 7 in mode 0) becoming panel 0, so both steps are taken here.
int PanelIndex(RCtx* ctx, u32 panel) {
    const u32 mode = rd8(ctx, PANEL_MANAGER + PANEL_MODE);
    for (u32 index = 0; index < 4; ++index) {
        const u32 slot = rd32(ctx, PANEL_ID_TABLES + mode * 16 + index * 4) & 0xFF;
        if (slot != 0 && rd8(ctx, PANEL_MANAGER + PANEL_SLOT_IDS + slot) == panel) {
            return static_cast<int>(index);
        }
    }
    return -1;
}

/// 0x0028f2f0 (whether touches are allowed), called by processTouch with the hit test's
/// result in r5 (kept in r5 across the call): while the skip button's tap is being made, the
/// Skip button's index goes there, pressed one frame and released the next, so processTouch
/// handles it as it would the stylus.
void PanelTouchAllowed(RCtx* ctx, RFunc original) {
    const bool from_process_touch = ctx->r[14] == PANEL_TOUCH_ALLOWED_RETURN;
    original(ctx);
    if (!from_process_touch || skip_tap == 0) {
        return;
    }
    const int index = PanelIndex(ctx, options.skip_panel);
    if (index < 0) {
        // no such button on this touch screen: the direct write after processTouch instead
        const u32 mode = rd8(ctx, PANEL_MANAGER + PANEL_MODE);
        LOG_INFO(Frontend,
                 "skip: no hit index is button {} (mode {}: slots {} {} {} {}, their panels {} {} {} {}); the tap "
                 "is written after processTouch instead",
                 options.skip_panel, mode, rd32(ctx, PANEL_ID_TABLES + mode * 16) & 0xFF,
                 rd32(ctx, PANEL_ID_TABLES + mode * 16 + 4) & 0xFF, rd32(ctx, PANEL_ID_TABLES + mode * 16 + 8) & 0xFF,
                 rd32(ctx, PANEL_ID_TABLES + mode * 16 + 12) & 0xFF,
                 rd8(ctx, PANEL_MANAGER + PANEL_SLOT_IDS + (rd32(ctx, PANEL_ID_TABLES + mode * 16) & 0xFF)),
                 rd8(ctx, PANEL_MANAGER + PANEL_SLOT_IDS + (rd32(ctx, PANEL_ID_TABLES + mode * 16 + 4) & 0xFF)),
                 rd8(ctx, PANEL_MANAGER + PANEL_SLOT_IDS + (rd32(ctx, PANEL_ID_TABLES + mode * 16 + 8) & 0xFF)),
                 rd8(ctx, PANEL_MANAGER + PANEL_SLOT_IDS + (rd32(ctx, PANEL_ID_TABLES + mode * 16 + 12) & 0xFF)));
        skip_tap_direct = true;
        return;
    }
    const u16 flags = skip_tap == 3 ? PANEL_PRESS_FLAGS : skip_tap == 2 ? PANEL_HELD_FLAGS : PANEL_TAP_FLAGS;
    const u32 hit = static_cast<u32>(index) | flags;
    ctx->r[5] = hit;
    LOG_INFO(Frontend, "skip: {} the touch screen's button {} (hit {:04X}, touches {})",
             skip_tap == 3 ? "pressing" : skip_tap == 2 ? "holding" : "releasing", options.skip_panel, hit,
             ctx->r[0] ? "allowed" : "not allowed");
    --skip_tap;
}

/// PanelManager::processTouch(): runs the touch groups, leaves the touched panel's id.
void PanelProcessTouch(RCtx* ctx, RFunc original) {
    original(ctx);
    if (skip_tap_direct) {
        skip_tap_direct = false;
        skip_tap = 0;
        skip_tap_seen = false;
        wr8(ctx, PANEL_MANAGER + PANEL_ID, options.skip_panel);
        wr16(ctx, PANEL_MANAGER + PANEL_FLAGS, PANEL_TAP_FLAGS);
        LOG_INFO(Frontend, "skip: a tap on the touch screen's button {} (written after processTouch)",
                 options.skip_panel);
        return;
    }
    const bool skipping = skip_offered.load(std::memory_order_relaxed);
    if (!options.trace_panels && !skipping) {
        return;
    }
    const u32 touch = rd16(ctx, PANEL_MANAGER + PANEL_ID) | rd16(ctx, PANEL_MANAGER + PANEL_FLAGS) << 16;
    if (touch != last_panel_touch && !(touch >> 16 & 0x8000)) {
        // while the skip is offered, taps are logged anyway: a tap on the touch screen's Skip
        // says which button it is (skip_panel)
        LOG_INFO(Frontend, "{}: touch on panel {} flags {:04X}", skipping ? "skip" : "panels",
                 touch & 0xFF, touch >> 16);
    }
    last_panel_touch = touch;
}

/// PanelManager::isTapped(id): polled by the code that reacts to each panel.
void PanelIsTapped(RCtx* ctx, RFunc original) {
    const u32 id = ctx->r[0];
    const u32 caller = ctx->r[14];
    original(ctx);
    if (id == options.skip_panel && skip_offered.load(std::memory_order_relaxed)) {
        ++skip_polls;
        if (ctx->r[0] != 0 && !skip_tap_seen) {
            skip_tap_seen = true;
            LOG_INFO(Frontend, "skip: button {} tapped, handled at {:08X}", id, caller);
            return;
        }
    }
    if (options.trace_panels && ctx->r[0] != 0) {
        LOG_INFO(Frontend, "panels: panel {} tapped, handled at {:08X}", id, caller);
    }
}

/// sMhPad::update(this): turns pad state and the panels' injected bits into actions.
/// The kick button injects the kick for the next update, like the touch panel does (the
/// button keeps its own action too, so pick one the game doesn't use).
void MhPadUpdate(RCtx* ctx, RFunc original) {
    const u32 pad = ctx->r[0];
    if (options.trace_panels) {
        const u32 gameplay = rd32(ctx, pad + SMHPAD_INJECTED);
        const u32 menu = rd32(ctx, pad + SMHPAD_INJECTED_MENU);
        if (gameplay || menu) {
            LOG_INFO(Frontend, "panels: injected actions {:04X}, menu actions {:04X}", gameplay, menu);
        }
    }
    original(ctx);
    const u32 held = rd32(ctx, pad + SPAD_HELD);
    if (options.kick_button && (held & options.kick_button)) {
        wr32(ctx, pad + SMHPAD_INJECTED, rd32(ctx, pad + SMHPAD_INJECTED) | ACTION_KICK);
    }
    if (options.target_button) {
        // a press, not a hold: one request per press, as one tap
        const u32 now = held & options.target_button;
        if (now && !target_held) {
            wr8(ctx, QUEST_HUD + HUD_TARGET_REQUEST, 1);
        }
        target_held = now;
    }
    UpdateSkip(ctx);
    const bool offered = skip_offered.load(std::memory_order_relaxed);
    // the end of the wait: the combo held for skip_end_hold seconds, once per quest
    const u32 combo = options.skip_end_buttons;
    const bool combo_held = combo && (held & combo) == combo;
    if (offered && combo_held && !end_requested.load(std::memory_order_relaxed)) {
        const double now = EmulatedSeconds();
        if (end_hold_since == 0) {
            end_hold_since = now;
        }
        const float fraction = std::min(1.0f, static_cast<float>((now - end_hold_since) / options.skip_end_hold));
        end_hold_fraction.store(fraction, std::memory_order_relaxed);
        if (fraction >= 1.0f) {
            end_requested.store(true, std::memory_order_relaxed);
            NetSync::SetEndSkipVote(true);
            LOG_INFO(Frontend, "skip: {} held {:.1f} s: asking to end the wait", PadNames(combo),
                     options.skip_end_hold);
        }
    } else {
        end_hold_since = 0;
        if (!end_requested.load(std::memory_order_relaxed)) {
            end_hold_fraction.store(0.0f, std::memory_order_relaxed);
        }
    }
    // the touch screen's Skip: a press while it is offered, one tap per press; not while the
    // rest of the combo is held (a combo that includes this button is the end, not the skip)
    const u32 rest = combo & ~options.skip_button;
    const bool rest_held = rest && (held & rest) == rest;
    const bool skip_now = options.skip_button && (held & options.skip_button);
    if (skip_now && !skip_held && offered && !rest_held) {
        skip_tap = 3;
        skip_tap_seen = false;
        LOG_INFO(Frontend, "skip: {} pressed", PadName(options.skip_button));
    }
    skip_held = skip_now;
}

/// aQuest's playing state (this, state block), once a frame during a quest. Once the quest is
/// complete, state +0x24 is 1 while the wait before the return runs (the carving time): +0x40
/// of the quest area accumulates the frame's delta (sMain+0x5c) and takes one off the counter
/// at +0x3c per whole unit; when the counter is 0 or less the game sets sGameControl+0x2f89
/// and its end sequence (0x0065a378, stage at +0x48, 0 while waiting) goes on to the "Quest
/// Complete" stamp. Once this player has asked to end the wait (skip_end held) and every port
/// console in the session has too, the counter is set to 0 here: what running out of time does.
void QuestPlaying(RCtx* ctx, RFunc original) {
    const u32 quest = ctx->r[0];
    const u32 state = ctx->r[1];
    if (end_requested.load(std::memory_order_relaxed) && !end_cut && NetSync::EndSkipAgreed()) {
        const s32 left = static_cast<s32>(rd32(ctx, quest + AQUEST_WAIT_LEFT));
        if (rd8(ctx, state + QUEST_STATE_PHASE) == QUEST_PHASE_WAITING &&
            rd32(ctx, quest + AQUEST_END_STAGE) == 0 && left > 0) {
            wr32(ctx, quest + AQUEST_WAIT_LEFT, 0);
            end_cut = true;
            LOG_INFO(Frontend, "skip: everyone asked; the wait ended with {} units left", left);
        }
    }
    original(ctx);
}

// ── Quest start (docs/research/system-services.md, "A loading race at the start of a
// quest"). Right before a quest starts, 0x0069b21c builds a list of quest-file resources by
// asking the resource manager for each id of a table (0x0069af14); 0x00b7c010 then has
// 0x0069b4d0 copy the quest's data out of that list and releases it (0x006bc838). A file
// that is still being set up at that moment is left out of the list. At 60 fps that is a
// quest nobody starts; at higher frame rates it is often the one being started, which
// then runs on an empty stage: the hunter falls into a void and the quest fails. Until its
// quest is in the list, startQuest is put off a frame and the list released, so that the
// game builds it again.
constexpr u32 QUEST_HOLDER = 0x00fbeffc;      // the object that holds the current quest
constexpr u32 QUEST_TABLE = 0xe604;           // holder: u16 quest ids, 0-terminated
constexpr u32 QUEST_LIST = 0xe608;            // holder: the resources found for them
constexpr u32 QUEST_LIST_COUNT = 0xe610;
constexpr u32 RESOURCE_BUFFER = 0x64;         // resource: its file contents
constexpr u32 QUEST_FLOW_PARAMS = 0x00edbe90; // [this]+0x9788: what 0x00b7c010 starts
constexpr u32 QUEST_LIST_RELEASE = 0x006bc838;
constexpr u32 QUEST_START = 0x00b7c010;
constexpr u32 QUEST_HOLD_MAX_FRAMES = 600;

/// The quest id 0x00b7c010 is about to set up (its three branches), or 0.
u32 NextQuestId(RCtx* ctx) {
    const u32 base = rd32(ctx, QUEST_FLOW_PARAMS);
    if (!base) {
        return 0;
    }
    const u32 p = base + 0x9788;
    if (rd8(ctx, p + 0x18)) {
        const u32 id = rd16(ctx, p + 0x1a);
        if (id != 0xffff) {
            return id;
        }
        const u32 which = rd8(ctx, p + 0x19);
        return which < 3 ? 0xafc8 + which : 0;
    }
    return rd8(ctx, p + 0x89) ? rd16(ctx, p + 0x8c) : rd16(ctx, p + 0x3c);
}

/// Whether the list was built from a table with quest `id` but doesn't have it: the quest's
/// file was not ready when 0x0069af14 asked for it (0x006ae870 reads an entry's quest id at
/// buffer + [buffer] + 0x20).
bool QuestLeftOut(RCtx* ctx, u32 holder, u32 id) {
    const u32 list = rd32(ctx, holder + QUEST_LIST);
    const u32 table = rd32(ctx, holder + QUEST_TABLE);
    const u32 count = list ? rd32(ctx, holder + QUEST_LIST_COUNT) : 0;
    if (!list || !table || count > 4096) {
        return false;
    }
    bool in_table = false;
    for (u32 i = 0; i < 4096; ++i) {
        const u32 t = rd16(ctx, table + 2 * i);
        if (t == 0) {
            break;
        }
        in_table = in_table || t == id;
    }
    if (!in_table) {
        return false;
    }
    for (u32 i = 0; i < count; ++i) {
        const u32 res = rd32(ctx, list + 4 * i);
        const u32 data = res ? rd32(ctx, res + RESOURCE_BUFFER) : 0;
        const u32 header = data ? rd32(ctx, data) : 0;
        if (header != 0 && header < 0x10000 && rd16(ctx, data + header + 0x20) == id) {
            return false;
        }
    }
    return true;
}

/// Calls a recompiled game function with one argument, leaving the caller's registers as
/// they were.
void CallGame(RCtx* ctx, u32 function, u32 arg) {
    u32 regs[16];
    std::copy(std::begin(ctx->r), std::end(ctx->r), regs);
    const u32 flags[4] = {ctx->fn, ctx->fz, ctx->fc, ctx->fv};
    const u32 expect_next = ctx->expect_next;
    ctx->expect_next = 0;
    ctx->r[0] = arg;
    ctx->r[14] = QUEST_START;
    rc_call(ctx, function);
    std::copy(std::begin(regs), std::end(regs), ctx->r);
    ctx->fn = flags[0];
    ctx->fz = flags[1];
    ctx->fc = flags[2];
    ctx->fv = flags[3];
    ctx->expect_next = expect_next;
}

u32 quest_start_held = 0;

/// QuestFlow::startQuest(this).
void StartQuest(RCtx* ctx, RFunc original) {
    const u32 holder = rd32(ctx, QUEST_HOLDER);
    const u32 id = NextQuestId(ctx);
    if (holder && id && quest_start_held < QUEST_HOLD_MAX_FRAMES && QuestLeftOut(ctx, holder, id)) {
        if (quest_start_held++ == 0) {
            LOG_INFO(Frontend, "quest {}: its file wasn't ready for the quest list; building the list again",
                     id);
        }
        CallGame(ctx, QUEST_LIST_RELEASE, holder);
        rc_hook_return(ctx);
        return;
    }
    if (quest_start_held) {
        LOG_INFO(Frontend, "quest {}: started after {} more frames{}", id, quest_start_held,
                 quest_start_held >= QUEST_HOLD_MAX_FRAMES ? " (gave up)" : "");
        quest_start_held = 0;
    }
    original(ctx);
}

// ── The wait after a quest is complete (docs/research/second-screen.md, "Skipping the wait
// after a quest"). The quest holder's status byte says how the quest stands: 3 and 7 complete
// (0x006a62c8 reads 3, 4 and 7 as complete, 5 and 6 as failed and abandoned). From the moment
// it is complete, the quest area (aQuest, 0x0065c18c) counts down the 20 seconds before the
// return (from the holder's +0xad60, set by 0x006b2de0); when they are over it sets
// sGameControl+0x2f89 and its end sequence (0x0065a378) plays the "Quest Complete" stamp and
// sets the status to 4. So the skip is offered while the status is 3 or 7, until 0x2f89 is
// set (if it wasn't at the start), and for 25 seconds at most: quests that end at once (the
// arena, training) leave the status as it was without a wait.
constexpr u32 QUEST_STATUS = 0xad24;             // u8 in the quest holder
constexpr u32 GAME_CONTROL = 0x00fb6b7c;         // sGameControl*
constexpr u32 GAME_CONTROL_RETURN_DUE = 0x2f89;  // u8: the wait is over
constexpr double SKIP_MOST_SECONDS = 25.0;
bool skip_complete = false;     // last frame: the quest complete
double skip_since = 0;          // when it was
u32 skip_due_at_start = 0;      // GAME_CONTROL_RETURN_DUE then

/// Once a frame (sMhPad::update): whether the game offers the skip now.
void UpdateSkip(RCtx* ctx) {
    const u32 holder = rd32(ctx, QUEST_HOLDER);
    const u32 status = holder ? rd8(ctx, holder + QUEST_STATUS) : 0;
    const bool complete = status == 3 || status == 7;
    const u32 control = rd32(ctx, GAME_CONTROL);
    const u32 due = control ? rd8(ctx, control + GAME_CONTROL_RETURN_DUE) : 0;
    const double now = EmulatedSeconds();
    if (complete && !skip_complete) {
        skip_since = now;
        skip_due_at_start = due;
        skip_polls = 0;
        LOG_INFO(Frontend, "skip: quest complete (status {}, return {})", status, due ? "due" : "not due");
    }
    skip_complete = complete;
    const bool offered =
        complete && now - skip_since < SKIP_MOST_SECONDS && !(skip_due_at_start == 0 && due != 0);
    if (offered != skip_offered.load(std::memory_order_relaxed)) {
        skip_offered.store(offered, std::memory_order_relaxed);
        if (offered) {
            LOG_INFO(Frontend, "skip: offered ({} taps the touch screen's button {})",
                     options.skip_button ? PadName(options.skip_button) : "no button", options.skip_panel);
        } else {
            LOG_INFO(Frontend,
                     "skip: no longer offered after {:.1f} s (status {}, return {}); the game asked about "
                     "button {} {} times",
                     now - skip_since, status, due ? "due" : "not due", options.skip_panel, skip_polls);
        }
        skip_tap = 0;
        skip_tap_direct = false;
        end_hold_since = 0;
        end_hold_fraction.store(0.0f, std::memory_order_relaxed);
        if (end_requested.exchange(false)) {
            NetSync::SetEndSkipVote(false);
        }
        end_cut = false;
    }
}

// ── The touch screen during a quest (docs/research/second-screen.md, "The quest's touch
// panels"). While the quest's small touch panels are up, the HUD's panel state machine
// (0x00b9ba38) runs them once a frame through 0x00b92e24; it doesn't while a big panel
// (the full map and the like) covers the touch screen. The panels sit in six slots, two
// columns of three under the touch screen's top bar; a panel takes one slot, or two one
// above the other (the slot below is then marked hidden), or two side by side. The map is
// one shape only, two slots tall. With quest_overlay, the touch screen in the corner of
// the custom layout shows the map alone while the panels are up, and with target_overlay the
// target camera panel in a view of its own beside it.
constexpr u32 QUEST_PANELS = 0x01082cec;  // (getter 0x001287a4)
constexpr u32 PANEL_TYPES = 0x7a;         // u16 per slot
constexpr u32 PANEL_HIDDEN = 0xb1;        // u8: a bit per slot covered by the panel before it
constexpr u32 PANEL_SLOTS = 6;
constexpr u32 PANEL_TYPE_MAP = 6;
constexpr u32 PANEL_TYPE_TARGET = 13;     // one slot only
constexpr int SLOT_WIDTH = PANEL_SLOT_WIDTH, SLOT_HEIGHT = PANEL_SLOT_HEIGHT;  // touch-screen pixels
constexpr int SLOTS_TOP = PANEL_SLOTS_TOP;
bool quest_panels_ran = false;  // this frame
int quest_map_slot = -1;        // the map's slot the last time the panels ran, -1 none
int quest_target_slot = -1;     // the target camera's then, -1 none
int map_slot_seen = -1;         // the last slot a quest had the map in (the layout editor's)
int target_slot_seen = -1;      // and the target camera
bool quest_map_drawn = false;   // this frame: the map panel drawn on the touch screen
bool layout_editing = false;    // SetLayoutEditing
QuestOverlayChoice quest_choice;
int applied_map_slot = -1, applied_target_slot = -1;  // what the touch overlay shows
u32 last_panel_states = 0xFFFFFFFF;  // trace_panels: the HUD's states last frame

/// Whether the touch screen's layout objects are looked at for the map panel's. While someone
/// talks the HUD isn't updated, so the panels don't run, but the touch screen still shows them.
bool WatchingQuestMap() {
    return quest_map_slot >= 0 && !quest_map_drawn && options.quest_overlay.on &&
           options.touch_overlay.height > 0;
}

/// A layout object drawn on the touch screen at (x, y) (layout units, which grow to the left
/// and up): every object of a panel sits at its slot's corner.
void NoteTouchScreenObject(float x, float y) {
    if (!WatchingQuestMap()) {
        return;
    }
    const int column = quest_map_slot / 3, row = quest_map_slot % 3;
    if (std::fabs(x + static_cast<float>(SLOT_WIDTH * column)) < 0.5f &&
        std::fabs(y + static_cast<float>(SLOTS_TOP + SLOT_HEIGHT * row)) < 0.5f) {
        quest_map_drawn = true;
    }
}

/// QuestHud::updatePanels(): the quest's small touch panels, once a frame while they are up.
void QuestPanels(RCtx* ctx, RFunc original) {
    original(ctx);
    quest_panels_ran = true;
    quest_map_slot = -1;
    quest_target_slot = -1;
    const u32 hidden = rd8(ctx, QUEST_PANELS + PANEL_HIDDEN);
    for (u32 slot = 0; slot < PANEL_SLOTS; ++slot) {
        if (hidden >> slot & 1) {
            continue;
        }
        const u32 type = rd16(ctx, QUEST_PANELS + PANEL_TYPES + 2 * slot);
        if (type == PANEL_TYPE_MAP && slot % 3 < 2 && quest_map_slot < 0) {
            quest_map_slot = static_cast<int>(slot);
        } else if (type == PANEL_TYPE_TARGET && quest_target_slot < 0) {
            quest_target_slot = static_cast<int>(slot);
        }
    }
    if (quest_map_slot >= 0) {
        map_slot_seen = quest_map_slot;
        target_slot_seen = quest_target_slot;
    }
}

/// Emulated time in seconds.
double EmulatedSeconds() {
    return static_cast<double>(Core::System::GetInstance().CoreTiming().GetGlobalTicks()) /
           static_cast<double>(BASE_CLOCK_RATE_ARM11);
}

/// The map's slot the touch overlay shows, -1 for the whole touch screen.
int ShownMapSlot() {
    if (options.touch_overlay.height <= 0 || !options.quest_overlay.on) {
        return -1;
    }
    if (layout_editing) {
        return map_slot_seen >= 0 ? map_slot_seen : 0;  // the default layout's (Type A)
    }
    return quest_choice.Shown();
}

/// The target camera panel's slot shown beside the map in `map_slot`, -1 none.
int ShownTargetSlot(int map_slot) {
    if (map_slot < 0 || !options.target_overlay.on) {
        return -1;
    }
    const bool stand_in = layout_editing || options.quest_overlay_test >= 0;
    const int seen = layout_editing ? target_slot_seen : quest_target_slot;
    if (seen >= 0 || !stand_in) {
        return seen;
    }
    // the slot of the map's column the map leaves (in the default layout, the target's)
    return map_slot / 3 * 3 + (map_slot % 3 == 0 ? 2 : 0);
}

/// The touch overlay as the options and the quest's panels now say.
void ApplyQuestOverlay(bool force) {
    const int map = ShownMapSlot();
    const int target = ShownTargetSlot(map);
    if (!force && map == applied_map_slot && target == applied_target_slot) {
        return;
    }
    const bool changed = map != applied_map_slot || target != applied_target_slot;
    applied_map_slot = map;
    applied_target_slot = target;
    Layout::SetScreenOverlay(map < 0 ? options.touch_overlay
                                     : QuestOverlay(options.touch_overlay, map, options.quest_overlay,
                                                    target, options.target_overlay));
    if (auto& system = Core::System::GetInstance(); frames > 0 && system.IsPoweredOn()) {
        // the screen layout is worked out when it changes, so work it out again
        system.GPU().Renderer().UpdateCurrentFramebufferLayout();
    }
    if (!changed || options.touch_overlay.height <= 0) {
        return;
    }
    if (map < 0) {
        LOG_INFO(Frontend, "touch overlay: the whole touch screen");
    } else if (target < 0) {
        LOG_INFO(Frontend, "touch overlay: the quest's map (panel slot {})", map);
    } else {
        LOG_INFO(Frontend, "touch overlay: the quest's map (panel slot {}) and target camera (slot {})", map,
                 target);
    }
}

/// Once a frame: the part of the touch screen the touch overlay shows in quests (see
/// QuestOverlayChoice).
void UpdateQuestOverlay(RCtx* ctx) {
    const bool panels = quest_panels_ran;
    const u8 hud = rd8(ctx, QUEST_HUD + HUD_STATE);
    const bool map_drawn = quest_map_drawn && hud == HUD_HUNTING;
    quest_panels_ran = false;
    quest_map_drawn = false;
    const u8 panel_state = rd8(ctx, QUEST_HUD + HUD_PANEL_STATE);
    if (options.trace_panels) {
        const u32 states = hud | panel_state << 8 | rd8(ctx, PANEL_MANAGER + PANEL_BUSY) << 16 |
                           (panels ? 1u : 0u) << 24 | (map_drawn ? 1u : 0u) << 25;
        if (states != last_panel_states) {
            LOG_INFO(Frontend,
                     "panels: at {:.2f} s HUD state {}, panel state {}, panel manager {}, small panels {}, "
                     "map {}, target camera {}",
                     EmulatedSeconds(), hud, panel_state, states >> 16 & 0xFF ? "busy" : "free",
                     panels ? "ran" : "not run", map_drawn ? "drawn" : "not drawn",
                     panels ? fmt::format("in slot {}", quest_target_slot) : std::string("-"));
            last_panel_states = states;
        }
    }
    if (!options.quest_overlay.on || options.touch_overlay.height <= 0) {
        return;
    }
    const bool touch_ui = hud == HUD_MENU_OPENING || hud == HUD_MENU ||
                          (hud == HUD_HUNTING && panel_state == PANELS_BIG);
    const int forced = quest_map_off                     ? -1
                       : options.quest_overlay_test >= 0 ? options.quest_overlay_test
                                                         : QuestOverlayChoice::FREE;
    quest_choice.Update(EmulatedSeconds(), panels, quest_map_slot, map_drawn, touch_ui, forced);
    ApplyQuestOverlay(false);
}

// ── Screen filters (docs/research/screen-filters.md). The engine's post-processing passes are
// units of uFilter's subclasses that register with sScene; each frame the scene calls their
// render(this, cDraw*) (virtual slot 12), which draws the pass into the scene's target: bloom
// adds a glow made from the bright parts of the picture at a quarter of its resolution, depth
// of field mixes in a blurred quarter-resolution copy by distance, motion blur blends in the
// frames before. A pass turned off returns at once, as if its filter weren't there (the
// community's "bloom off" cheat for MH4G writes a `bx lr` over the start of bloom's render).
// Classes are told apart by their vtable, since some share a render function.
struct ScreenFilter {
    u32 vtable;        // USA v1.3.0
    const char* name;  // in the log
    u32 option;        // the FILTER_* bit that turns it off, 0 none
};
constexpr ScreenFilter SCREEN_FILTERS[] = {
    {0x00e5e7f4, "bloom", FILTER_BLOOM},
    {0x00e624d0, "motion blur", FILTER_MOTION_BLUR},
    {0x00e5bafc, "depth of field", FILTER_DEPTH_OF_FIELD},
    {0x00e5e844, "bokeh", FILTER_DEPTH_OF_FIELD},
    {0x00e62520, "radial blur", FILTER_RADIAL_BLUR},
    {0x00e6313c, "radial blur (MH)", FILTER_RADIAL_BLUR},
    {0x00e61e08, "multi blur", FILTER_MOTION_BLUR},
    {0x00e5d56c, "haze", FILTER_HAZE},
    {0x00e5ff74, "haze (MH)", FILTER_HAZE},
    {0x00e62f28, "colour correction", 0},
    {0x00e5e970, "colour", 0},
    {0x00e5d474, "fade", 0},
    {0x00e61cbc, "cross-fade", 0},
    {0x00e62480, "image plane", 0},
    {0x00e63ac4, "image plane range", 0},
    {0x00e60ff8, "outline", 0},
    {0x00e611a4, "TV noise", 0},
};
static_assert(std::size(SCREEN_FILTERS) <= 32);
u32 filters_drawn = 0, filters_skipped = 0;  // bits: SCREEN_FILTERS index, since the last log line
u32 filters_logged = 0xFFFFFFFF, skipped_logged = 0xFFFFFFFF;
double filters_since = 0;

/// render(this, cDraw*) of a screen filter class.
void FilterRender(RCtx* ctx, RFunc original) {
    const u32 vtable = rd32(ctx, ctx->r[0]);
    for (u32 i = 0; i < std::size(SCREEN_FILTERS); ++i) {
        if (SCREEN_FILTERS[i].vtable != vtable) {
            continue;
        }
        if (SCREEN_FILTERS[i].option & options.filters_off) {
            filters_skipped |= 1u << i;
            rc_hook_return(ctx);
            return;
        }
        filters_drawn |= 1u << i;
        break;
    }
    original(ctx);
}

std::string FilterNames(u32 bits) {
    std::string names;
    for (u32 i = 0; i < std::size(SCREEN_FILTERS); ++i) {
        if (bits >> i & 1) {
            names += (names.empty() ? "" : ", ") + std::string(SCREEN_FILTERS[i].name);
        }
    }
    return names.empty() ? "none" : names;
}

/// Once a frame with trace_filters: the filters drawn and skipped over the last second, logged
/// when they change (a filter can draw every other frame, so a frame alone says too little).
void TraceFilters() {
    const double now = EmulatedSeconds();
    if (now - filters_since < 1.0) {
        return;
    }
    if (filters_drawn != filters_logged || filters_skipped != skipped_logged) {
        LOG_INFO(Frontend, "screen filters at {:.1f} s: {}{}", now, FilterNames(filters_drawn),
                 filters_skipped ? " (skipped: " + FilterNames(filters_skipped) + ")" : "");
        filters_logged = filters_drawn;
        skipped_logged = filters_skipped;
    }
    filters_drawn = filters_skipped = 0;
    filters_since = now;
}

struct Hook {
    u32 addr;
    const char* name;
    RHookFn fn;
};
constexpr Hook HOOKS[] = {
    {0x006442f8, "sMain::updateFrameTiming", UpdateFrameTiming},
    {0x00af5250, "sMhMain::frame", Frame},
    {0x00c46380, "uMhCamera::getProjMatrix", GetProjMatrix},
    {0x002535f8, "uFreeCamera::getProjMatrix", GetProjMatrix},
    {0x0032e338, "uMotionCamera::getProjMatrix", GetProjMatrix},
    {0x00633188, "nDraw::Scene::Scene", SceneCtor},
    {0x0062e184, "nDraw::DisplayBuffer::init", DisplayBufferInit},
    {0x00aefd44, "sCamera::Viewport::updateRegion", UpdateRegion},
    {0x002951fc, "PanelManager::processTouch", PanelProcessTouch},
    {0x0028f2f0, "PanelManager::touchAllowed", PanelTouchAllowed},
    {0x00288f4c, "PanelManager::isTapped", PanelIsTapped},
    {0x00694c98, "sMhPad::update", MhPadUpdate},
    {0x00b7c010, "QuestFlow::startQuest", StartQuest},
    {0x0065c18c, "aQuest::playing", QuestPlaying},
    {0x00ae65a4, "nLayout::objectConstants", LayoutObjectConstants},
    {0x00b92e24, "QuestHud::updatePanels", QuestPanels},
    {0x002bc238, "uBloomFilter::render", FilterRender},
    {0x003b0804, "uMotionBlurFilter::render", FilterRender},
    {0x001b0890, "uDOFFilter::render", FilterRender},
    {0x002bd53c, "uBokehFilter::render", FilterRender},
    {0x003b2938, "uRadialBlurFilter::render", FilterRender},
    {0x002d3168, "uMultiBlurFilter::render", FilterRender},
    {0x0025655c, "uHazeFilter::render", FilterRender},
    {0x003dd740, "uColorCorrectFilter::render", FilterRender},
    {0x002bffc0, "uColorFilter::render", FilterRender},
    {0x003a1a6c, "uCrossFadeFilter::render", FilterRender},
    {0x003aeccc, "uImagePlaneFilter::render", FilterRender},
    {0x0040a218, "uImagePlaneFilterRange::render", FilterRender},
    {0x003808ac, "uOutlineFilter::render", FilterRender},
    {0x00385c34, "uTVNoiseFilter::render", FilterRender},
};

} // namespace

std::vector<std::string> Install(const RCodeInfo& info) {
    std::vector<std::string> log;
    options = LoadOptions(log);
    // a new boot (Azahar can start the game again in the same process): nothing carries over
    // from the last one, whose emulated clock and game objects are gone
    frames = 0;
    owner_regions.clear();
    quest_start_held = 0;
    target_held = 0;
    skip_offered = false;
    skip_held = skip_tap_direct = skip_tap_seen = skip_complete = false;
    skip_tap = 0;
    skip_polls = 0;
    end_hold_since = 0;
    end_hold_fraction = 0.0f;
    end_requested = false;
    end_cut = false;
    last_panel_touch = 0xFFFFFFFF;
    quest_panels_ran = false;
    quest_map_slot = -1;
    quest_map_drawn = false;
    quest_choice = {};
    last_panel_states = 0xFFFFFFFF;
    filters_drawn = filters_skipped = 0;
    filters_logged = skipped_logged = 0xFFFFFFFF;
    filters_since = 0;
    // local wireless play over network rooms, for any program on recompiled code
    NetSync::Install(Core::System::GetInstance(), options.net_sync, options.net_sync_slack, log);
    if (!info.set_hook || info.hook_count == 0) {
        return log; // a test program, or a library from before hook points
    }
    if (options.fps_cap > 0) {
        log.push_back(fmt::format("frame-rate cap {}", options.fps_cap));
    }
    if (RefreshRate() > 0) {
        log.push_back(fmt::format("emulated screen refresh rate {} Hz{} (experimental)", RefreshRate(),
                                  options.refresh_rate > 0 ? "" : ", as fps"));
    }
    if (options.widescreen > 0) {
        log.push_back(WideRows() ? fmt::format("widescreen: top screen rendered {}x{} (experimental)",
                                               WideRows(), LCD_COLUMNS)
                                 : fmt::format("widescreen: 3D view {} to aspect ratio {:.4f}",
                                               options.widescreen > NATIVE_ASPECT ? "widened" : "narrowed",
                                               options.widescreen));
        SetScreenAspect(options.widescreen, log);
        if (WideRows()) {
            // the code's own 400s for the top screen (config/tunables.txt)
            const u32 set = SetTunables(info, "top_rows", WideRows());
            log.push_back(fmt::format("widescreen: {} of the code's top-screen sizes set to {}", set,
                                      WideRows()));
            if (options.hud_edges) {
                log.push_back(fmt::format("widescreen: the hunting HUD's sides moved {} pixels out to the edges",
                                          (WideRows() - TOP_ROWS) / 2));
            }
        }
    }
    if (options.trace_every) {
        log.push_back(fmt::format("tracing frame timing every {} frames", options.trace_every));
    }
    if (options.trace_panels) {
        log.push_back("tracing touch panels");
    }
    if (options.kick_button) {
        log.push_back(fmt::format("kick on pad button {:04X}", options.kick_button));
    }
    if (options.target_button) {
        log.push_back(fmt::format("target camera on pad button {:04X}", options.target_button));
    }
    if (options.skip_button) {
        log.push_back(fmt::format("skip after a quest on {} (a tap on the touch screen's button {})",
                                  PadName(options.skip_button), options.skip_panel));
    }
    if (options.skip_end_buttons) {
        log.push_back(fmt::format("the end of the wait after a quest on {} held {:.1f} s, with every port "
                                  "player's say-so",
                                  PadNames(options.skip_end_buttons), options.skip_end_hold));
    }
    Layout::SetScreenOverlay(options.touch_overlay);
    applied_map_slot = applied_target_slot = -1;
    if (const auto& overlay = options.touch_overlay; overlay.height > 0) {
        constexpr const char* corners[] = {"top left", "top right", "bottom left", "bottom right"};
        log.push_back(fmt::format("custom screen layout: the touch screen over the top screen, {}, "
                                  "{:.0f}% of the window's height, {:.0f}% opaque",
                                  corners[static_cast<int>(overlay.corner)], overlay.height * 100,
                                  overlay.opacity * 100));
        if (options.quest_overlay.on) {
            const Placement map = MapPlacement(overlay, options.quest_overlay);
            log.push_back(fmt::format("in quests the touch overlay shows the map alone, {:.1f}% of the "
                                      "window's height at {:.1f}% {:.1f}%",
                                      map.height * 100, map.x * 100, map.y * 100));
            if (options.target_overlay.on) {
                const Placement target = TargetPlacement(overlay, options.quest_overlay, options.target_overlay);
                log.push_back(fmt::format("and the target camera panel, {:.1f}% of the window's height at "
                                          "{:.1f}% {:.1f}%",
                                          target.height * 100, target.x * 100, target.y * 100));
            }
        }
    }
    if (options.trace_layout) {
        log.push_back(fmt::format("tracing HUD layout objects every {} frames", options.trace_layout));
    }
    if (!options.dither) {
        log.push_back("dithering off");
    }
    if (!options.stereo_3d) {
        log.push_back("stereoscopic 3D off: the top screen drawn once");
    }
    if (!options.antialiasing) {
        log.push_back("the game's anti-aliasing off");
    }
    if (options.filters_off) {
        std::string off;
        for (const auto& [name, bit] : FILTER_OPTIONS) {
            if (options.filters_off & bit) {
                off += (off.empty() ? "" : ", ") + std::string(name);
            }
        }
        log.push_back("screen filters off: " + off);
    }
    if (options.trace_filters) {
        log.push_back("tracing screen filters");
    }
    if (options.render_set || options.render_clear) {
        log.push_back(fmt::format("render flags: set {:08X}, clear {:08X} (experimental)",
                                  options.render_set, options.render_clear));
    }
    missing_hook_points = 0;
    for (const Hook& hook : HOOKS) {
        if (!info.set_hook(hook.addr, hook.fn)) {
            log.push_back(fmt::format("no hook point for {} at {:08X} (rebuild the code library)",
                                      hook.name, hook.addr));
            ++missing_hook_points;
        }
    }
    return log;
}

int MissingHookPoints() {
    return missing_hook_points;
}

void ToggleQuestMap() {
    const bool off = !quest_map_off.load();
    quest_map_off = off;
    LOG_INFO(Frontend, "touch overlay: {} in quests", off ? "the whole touch screen" : "the map");
}

bool SkipOffered() {
    return skip_offered.load(std::memory_order_relaxed);
}

std::string SkipEndButtons() {
    return PadNames(options.skip_end_buttons);
}

float EndSkipHold() {
    return end_hold_fraction.load(std::memory_order_relaxed);
}

bool EndSkipRequested() {
    return end_requested.load(std::memory_order_relaxed);
}

std::string SkipButton() {
    return PadName(options.skip_button);
}

int SkipHint() {
    return options.skip_hint;
}

bool SetOption(const std::string& key, const std::string& value) {
    // the options the hooks read each time they run; the others are applied once at start-up
    static constexpr std::string_view live[] = {
        "fps", "refresh_rate", "dither", "stereo_3d", "antialiasing", "render_flags_set", "render_flags_clear",
        "kick",
        "target", "skip", "skip_panel", "skip_hint", "skip_end", "skip_end_hold", "touch_overlay",
        "quest_overlay", "target_overlay",
        "hud_edges", "trace_frames", "trace_layout", "trace_panels", "bloom", "motion_blur",
        "depth_of_field", "radial_blur", "haze", "trace_filters"};
    if (std::find(std::begin(live), std::end(live), key) == std::end(live)) {
        return false;
    }
    Set(options, key, value);
    if ((key == "refresh_rate" || key == "fps") && frames > 0) {
        Core::System::GetInstance().GPU().SetRefreshRate(RefreshRate()); // 0: the 3DS's
    }
    if (key == "touch_overlay" || key == "quest_overlay" || key == "target_overlay") {
        // the corner as the options now say, with the quest's map in it if it shows the map
        ApplyQuestOverlay(true);
    }
    return true;
}

void SetLayoutEditing(bool on) {
    if (on != layout_editing) {
        layout_editing = on;
        LOG_INFO(Frontend, "touch overlay: {}", on ? "the layout editor is up" : "the layout editor is done");
    }
    ApplyQuestOverlay(true);
}

Layout::ScreenOverlay ParseOverlay(std::string v) {
    std::transform(v.begin(), v.end(), v.begin(), [](unsigned char c) { return std::tolower(c); });
    using Corner = Layout::ScreenOverlay::Corner;
    Layout::ScreenOverlay overlay{0.30f, 0.85f, Corner::TopRight};
    std::istringstream words(v);
    std::string corner;
    words >> corner;
    std::replace(corner.begin(), corner.end(), '_', '-');
    const std::pair<const char*, Corner> corners[] = {{"top-left", Corner::TopLeft},
                                                      {"top-right", Corner::TopRight},
                                                      {"bottom-left", Corner::BottomLeft},
                                                      {"bottom-right", Corner::BottomRight}};
    const auto it = std::find_if(std::begin(corners), std::end(corners),
                                 [&](const auto& c) { return corner == c.first; });
    if (it == std::end(corners)) {
        return IsOn(corner) ? overlay : Layout::ScreenOverlay{};
    }
    overlay.corner = it->second;
    std::string height, opacity;
    if (words >> height) {
        overlay.height = std::clamp(std::strtof(height.c_str(), nullptr) / 100.0f, 0.05f, 1.0f);
    }
    if (words >> opacity) {
        overlay.opacity = std::clamp(std::strtof(opacity.c_str(), nullptr) / 100.0f, 0.1f, 1.0f);
    }
    return overlay;
}

namespace {

/// The rest of a panel view's option after its first word: "[SIZE] [at X Y]", in %.
PanelView ParsePanelView(std::istringstream& words) {
    PanelView view{true};
    std::string word;
    while (words >> word) {
        if (word == "at") {
            std::string x, y;
            if (words >> x >> y) {
                view.placed = true;
                view.x = std::clamp(std::strtof(x.c_str(), nullptr) / 100.0f, 0.0f, 1.0f);
                view.y = std::clamp(std::strtof(y.c_str(), nullptr) / 100.0f, 0.0f, 1.0f);
            }
            break;
        }
        view.height = std::clamp(std::strtof(word.c_str(), nullptr) / 100.0f, 0.03f, 1.0f);
    }
    return view;
}

/// A fraction as a percentage to 0.1, without a needless ".0".
std::string Percent(float fraction) {
    return fmt::format("{:g}", std::round(fraction * 1000.0f) / 10.0f);
}

std::string FormatPanelView(const char* on, const PanelView& view) {
    if (!view.on) {
        return "off";
    }
    std::string text = on;
    if (view.height > 0) {
        text += " " + Percent(view.height);
    }
    if (view.placed) {
        text += " at " + Percent(view.x) + " " + Percent(view.y);
    }
    return text;
}

std::string LowerCase(std::string v) {
    std::transform(v.begin(), v.end(), v.begin(), [](unsigned char c) { return std::tolower(c); });
    return v;
}

/// The part of the touch screen in `slot` that is `rows` slots tall, in fractions.
void CropToSlots(float& left, float& top, float& right, float& bottom, int slot, int rows) {
    // 0-2 are the left column from the top, 3-5 the right one
    const int column = slot / 3, row = slot % 3;
    const float x0 = static_cast<float>(column * SLOT_WIDTH);
    const float y0 = static_cast<float>(SLOTS_TOP + row * SLOT_HEIGHT);
    left = x0 / Core::kScreenBottomWidth;
    right = (x0 + SLOT_WIDTH) / Core::kScreenBottomWidth;
    top = y0 / Core::kScreenBottomHeight;
    bottom = (y0 + static_cast<float>(rows * SLOT_HEIGHT)) / Core::kScreenBottomHeight;
}

} // namespace

PanelView ParseQuestOverlay(std::string v) {
    std::istringstream words(LowerCase(v));
    std::string what;
    words >> what;
    if (what != "map" && !IsOn(what)) {
        return {};
    }
    return ParsePanelView(words);
}

PanelView ParseTargetOverlay(std::string v) {
    std::istringstream words(LowerCase(v));
    std::string what;
    words >> what;
    if (what != "target" && !IsOn(what)) {
        return {};
    }
    return ParsePanelView(words);
}

std::string FormatQuestOverlay(const PanelView& map) {
    return FormatPanelView("map", map);
}

std::string FormatTargetOverlay(const PanelView& target) {
    return FormatPanelView("on", target);
}

Placement MapPlacement(const Layout::ScreenOverlay& touch, const PanelView& map) {
    using Corner = Layout::ScreenOverlay::Corner;
    Placement p;
    // at the touch overlay's own scale unless a height is given
    p.height = map.height > 0 ? map.height
                              : touch.height * static_cast<float>(2 * SLOT_HEIGHT) / Core::kScreenBottomHeight;
    if (map.placed) {
        p.x = map.x;
        p.y = map.y;
    } else {
        p.x = touch.corner == Corner::TopLeft || touch.corner == Corner::BottomLeft ? 0.0f : 1.0f;
        p.y = touch.corner == Corner::TopLeft || touch.corner == Corner::TopRight ? 0.0f : 1.0f;
    }
    return p;
}

Placement TargetPlacement(const Layout::ScreenOverlay& touch, const PanelView& map, const PanelView& target) {
    Placement p;
    p.height = target.height > 0 ? target.height
                                 : touch.height * static_cast<float>(SLOT_HEIGHT) / Core::kScreenBottomHeight;
    p.height = std::min(p.height, 1.0f);
    if (target.placed) {
        p.x = target.x;
        p.y = target.y;
        return p;
    }
    // right under the map (over it if there is no room), on the same side: positions are in
    // fractions of the window's height, so this holds at any window size
    const Placement m = MapPlacement(touch, map);
    const float map_top = m.y * (1.0f - std::min(m.height, 1.0f));
    const float under = map_top + m.height;
    const float top = under + p.height <= 1.0f ? under : std::max(0.0f, map_top - p.height);
    p.x = m.x;
    p.y = p.height < 1.0f ? std::clamp(top / (1.0f - p.height), 0.0f, 1.0f) : 0.0f;
    return p;
}

Layout::ScreenOverlay QuestOverlay(const Layout::ScreenOverlay& touch, int map_slot, const PanelView& map,
                                   int target_slot, const PanelView& target) {
    Layout::ScreenOverlay overlay = touch;
    // the map takes two slots, one above the other
    CropToSlots(overlay.crop_left, overlay.crop_top, overlay.crop_right, overlay.crop_bottom, map_slot, 2);
    const Placement m = MapPlacement(touch, map);
    overlay.height = m.height;
    overlay.x = m.x;
    overlay.y = m.y;
    overlay.views = {};
    if (target_slot >= 0 && target_slot < static_cast<int>(PANEL_SLOTS) && target.on) {
        auto& view = overlay.views[0];
        CropToSlots(view.crop_left, view.crop_top, view.crop_right, view.crop_bottom, target_slot, 1);
        const Placement t = TargetPlacement(touch, map, target);
        view.height = t.height;
        view.x = t.x;
        view.y = t.y;
    }
    return overlay;
}

bool QuestOverlayChoice::Update(double time, bool panels, int map_slot, bool map_drawn, bool touch_ui,
                                int forced) {
    if (panels || map_drawn) {
        panels_seen = true;
        panels_last = time;
    }
    const bool gone = !panels_seen || time - panels_last >= HOLD;
    const int want = forced != FREE     ? forced
                     : panels           ? map_slot
                     : touch_ui || gone ? -1
                                        : shown; // a short break: keep what is shown
    if (want != next) {
        next = want;
        next_since = time;
    }
    if (want == shown || time - next_since < SETTLE) {
        return false;
    }
    shown = want;
    return true;
}

} // namespace Port::Hooks
