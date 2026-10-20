#include "config.h"

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <fmt/format.h>
#include <INIReader.h>

#include "common/file_util.h"
#include "common/logging/log.h"
#include "core/hle/service/service.h"
#include "input.h"

namespace App {

namespace {
namespace NativeAnalog = Settings::NativeAnalog;
namespace NativeButton = Settings::NativeButton;
} // namespace

/// The keyboard as it comes: the left hand on the circle pad and the hunter's actions (R, the
/// dash, on Shift), the arrows on the C-stick (the camera).
Config Defaults() {
    Config c;
    c.keys[NativeButton::A] = "E";
    c.keys[NativeButton::B] = "Space";
    c.keys[NativeButton::X] = "R";
    c.keys[NativeButton::Y] = "F";
    c.keys[NativeButton::Up] = "1";
    c.keys[NativeButton::Down] = "2";
    c.keys[NativeButton::Left] = "3";
    c.keys[NativeButton::Right] = "4";
    c.keys[NativeButton::L] = "Q";
    c.keys[NativeButton::R] = "Left Shift";
    c.keys[NativeButton::Start] = "Return";
    c.keys[NativeButton::Select] = "Backspace";
    c.keys[NativeButton::ZL] = "Tab";
    c.keys[NativeButton::ZR] = "C";
    c.stick_keys[NativeAnalog::CirclePad] = "W,S,A,D,Left Ctrl";
    c.stick_keys[NativeAnalog::CStick] = "Up,Down,Left,Right";
    return c;
}

namespace {

bool IsOn(const std::string& v) {
    return v == "1" || v == "on" || v == "yes" || v == "true";
}

std::string OnOff(bool v) {
    return v ? "on" : "off";
}

/// A value without its key: "button_a" -> "a" for the template's key list.
std::string ButtonKeyName(int button) {
    return NativeButton::mapping[button];
}

} // namespace

std::string ConfigPath() {
    return FileUtil::GetUserPath(FileUtil::UserPath::UserDir) + "mh4u-app.ini";
}

Config Load() {
    Config c = Defaults();
    const std::string path = ConfigPath();
    if (!FileUtil::Exists(path)) {
        FileUtil::CreateFullPath(path);
        FileUtil::WriteStringToFile(true, path, Template(c));
        LOG_INFO(Frontend, "wrote {} with the defaults", path);
        return c;
    }
#ifdef __ANDROID__
    // On Android the user folder is a virtual path that only the core's file code understands,
    // and INIReader opens real files. So read it the core's way and parse a copy kept in the
    // app's private folder.
    std::string real = path;
    if (const char* priv = std::getenv("MH4U_PRIVATE_DIR")) {
        std::string text;
        FileUtil::ReadFileToString(true, path, text);
        real = std::string(priv) + "/mh4u-app.ini";
        if (std::FILE* f = std::fopen(real.c_str(), "wb")) {
            std::fwrite(text.data(), 1, text.size(), f);
            std::fclose(f);
        }
    }
    INIReader ini(real);
#else
    INIReader ini(path);
#endif
    if (ini.ParseError() < 0) {
        LOG_ERROR(Frontend, "couldn't read {}, using the defaults", path);
        return c;
    }
    if (ini.ParseError() > 0) {
        LOG_WARNING(Frontend, "{}: line {} isn't understood", path, ini.ParseError());
    }
    const auto str = [&](const char* section, const char* key, const std::string& def) {
        return ini.Get(section, key, def);
    };
    const auto flag = [&](const char* section, const char* key, bool def) {
        return IsOn(ini.Get(section, key, def ? "on" : "off"));
    };
    const auto num = [&](const char* section, const char* key, int def, int lo, int hi) {
        return std::clamp(static_cast<int>(ini.GetInteger(section, key, def)), lo, hi);
    };

    c.game = str("Game", "game", c.game);
    c.launcher = flag("Game", "launcher", c.launcher);
    c.new_3ds = str("Game", "new_3ds", c.new_3ds);
    c.cpu_clock = num("Game", "cpu_clock", c.cpu_clock, 5, 400);

    c.renderer = str("Display", "renderer", c.renderer);
    c.fullscreen = flag("Display", "fullscreen", c.fullscreen);
    c.resolution = num("Display", "resolution", c.resolution, 0, 10);
    c.vsync = flag("Display", "vsync", c.vsync);
    c.accurate_mul = flag("Display", "accurate_mul", c.accurate_mul);
    c.screens = str("Display", "screens", c.screens);
    c.linear_filter = flag("Display", "linear_filter", c.linear_filter);
    c.custom_textures = flag("Display", "custom_textures", c.custom_textures);
    c.async_shaders = flag("Display", "async_shaders", c.async_shaders);
    c.disk_shader_cache = flag("Display", "disk_shader_cache", c.disk_shader_cache);
    c.skip_duplicate_frames = flag("Display", "skip_duplicate_frames", c.skip_duplicate_frames);
    c.texture_filter = str("Display", "texture_filter", c.texture_filter);
    c.texture_sampling = str("Display", "texture_sampling", c.texture_sampling);
    c.shader = str("Display", "shader", c.shader);
    c.fps_counter = str("Display", "fps_counter", c.fps_counter);

    c.volume = num("Audio", "volume", c.volume, 0, 100);
    c.audio_output = str("Audio", "output", c.audio_output);

    for (int i = 0; i < NativeButton::NumButtons; ++i) {
        c.keys[i] = str("Controls", NativeButton::mapping[i], c.keys[i]);
        c.button_params[i] = str("Controls", ("param_" + ButtonKeyName(i)).c_str(), "");
    }
    for (int i = 0; i < NativeAnalog::NumAnalogs; ++i) {
        c.stick_keys[i] = str("Controls", NativeAnalog::mapping[i], c.stick_keys[i]);
        c.stick_params[i] =
            str("Controls", (std::string("param_") + NativeAnalog::mapping[i]).c_str(), "");
    }
    c.controller_auto = str("Controls", "controller", "auto") == "auto";
    c.touch_screen_key = str("Controls", "touch_screen_key", c.touch_screen_key);
    c.fullscreen_key = str("Controls", "fullscreen_key", c.fullscreen_key);
    c.fps_counter_key = str("Controls", "fps_counter_key", c.fps_counter_key);

    c.nickname = str("Multiplayer", "nickname", c.nickname);
    c.join = str("Multiplayer", "join", c.join);
    c.password = str("Multiplayer", "password", c.password);
    c.host = num("Multiplayer", "host", c.host, 0, 65535);
    c.room_port = num("Multiplayer", "room_port", c.room_port, 1, 65535);
    c.room_name = str("Multiplayer", "room_name", c.room_name);
    c.room_members = num("Multiplayer", "room_members", c.room_members, 2, 254);
    c.lobby = str("Multiplayer", "lobby", c.lobby);
    c.token = str("Multiplayer", "token", c.token);
    c.room_public = flag("Multiplayer", "room_public", c.room_public);
    c.last_room = str("Multiplayer", "last_room", c.last_room);

    c.catalog_url = str("Mods", "catalog_url", c.catalog_url);
    LOG_INFO(Frontend, "settings from {}", path);
    return c;
}

void Save(const Config& config) {
    const std::string path = ConfigPath();
    FileUtil::CreateFullPath(path);
    if (!FileUtil::WriteStringToFile(true, path, Template(config))) {
        LOG_ERROR(Frontend, "couldn't write {}", path);
    }
}

std::string Template(const Config& c) {
    std::string keys;
    for (int i = 0; i < NativeButton::NumButtons; ++i) {
        keys += fmt::format("{} = {}\n", NativeButton::mapping[i], c.keys[i]);
    }
    for (int i = 0; i < NativeAnalog::NumAnalogs; ++i) {
        keys += fmt::format("{} = {}\n", NativeAnalog::mapping[i], c.stick_keys[i]);
    }
    std::string params;
    for (int i = 0; i < NativeButton::NumButtons; ++i) {
        if (!c.button_params[i].empty()) {
            params += fmt::format("param_{} = {}\n", NativeButton::mapping[i], c.button_params[i]);
        }
    }
    for (int i = 0; i < NativeAnalog::NumAnalogs; ++i) {
        if (!c.stick_params[i].empty()) {
            params += fmt::format("param_{} = {}\n", NativeAnalog::mapping[i], c.stick_params[i]);
        }
    }
    return fmt::format(R"(# MH4U PC port: the app's settings. The game's own options (frame rate, widescreen, the touch
# screen's corner, the kick and target camera buttons, the screen filters) are in mh4u.ini next
# to this file. A line starting with # is ignored; on/off settings also take 1/0.

[Game]
# your decrypted Monster Hunter 4 Ultimate (USA): .3ds, .cci or .cxi. Empty: the app asks.
# The v1.3.0 update must be installed (Build code library does that) for the port's code.
game = {}
# the launcher before the game: Play, the setup, mods, the room, settings. off: straight into
# the game (the launcher still shows while the game, the update or the code library is missing)
launcher = {}
# Azahar's New 3DS mode: on, off or auto (off while custom textures are on: the HD texture pack's
# monsters show only then; on otherwise)
new_3ds = {}
# the CPU's clock speed in % of the 3DS's (5-400): above 100 a busy scene keeps a high frame
# rate (fps in mh4u.ini) at the cost of more work for your PC; the same as Azahar's CPU Clock Speed
cpu_clock = {}

[Display]
# vulkan, opengl or software (opengl isn't available on macOS; vulkan runs on Metal there)
renderer = {}
fullscreen = {}
# internal resolution in multiples of the 3DS's (1-10); 0 = as many as the window needs
resolution = {}
vsync = {}
# overlay: the game fills the window and the touch screen sits over it in a corner (mh4u.ini's
#          touch_overlay and touch_screen say where and when; F12 or the right stick's click
#          switch it)
# single: the game alone; stacked: the two screens as on a 3DS; side: side by side;
# windows: the touch screen in a window of its own
screens = {}
# smooth the picture when it is scaled up
linear_filter = {}
# texture packs from load/textures/0004000000126300/ in this folder
custom_textures = {}
# a post-processing shader from the shaders folder (empty: none)
shader = {}
# the frame-rate counter over the game: off, top-left, top-right, bottom-left or bottom-right
# (fps_counter_key, F3, shows and hides it while you play)
fps_counter = {}

[Audio]
# 0-100
volume = {}
# auto, or cubeb, sdl2, openal, null
output = {}

[Controls]
# controller = auto maps the first controller found like MH3U on a Wii U: the 3DS's A, B, X, Y
# on the right face buttons by position, L/R on the bumpers, ZL/ZR on the triggers (target
# camera and kick, see mh4u.ini), the circle pad and C-stick (camera) on the sticks, START and
# SELECT on Start and Back; the right stick's click switches the touch screen. off: keys only.
controller = {}
# keyboard keys by their SDL names (https://wiki.libsdl.org/SDL2/SDL_Scancode): a 3DS button
# each; a stick takes up,down,left,right and optionally a key that makes it move half way
{}# full Azahar input parameters replace a binding altogether, e.g.
# param_button_a = engine:sdl,guid:...,port:0,api:controller,button:1
{}touch_screen_key = {}
fullscreen_key = {}
fps_counter_key = {}

[Multiplayer]
# MH4U's local play over the internet, through a room (Azahar's network rooms; docs/SERVER.md).
# The in-game menu's Multiplayer tab (Esc) joins, hosts and leaves rooms while the game runs and
# keeps these lines up to date. Your name in the room (empty: the console's user name, set in
# Azahar's System settings)
nickname = {}
# join this room when the game starts: HOST, HOST:PORT (default port 24872) or an invite code
# (mh4u://...), and its password if it has one
join = {}
password = {}
# or host a room on this UDP port when the game starts (24872; forward it on your router), and
# join it (0: don't)
host = {}
# the port the menu hosts on
room_port = {}
room_name = {}
room_members = {}
# a lobby server lists the hosted room publicly (http://HOST:PORT); the token is your password
# there (any you like on an open lobby)
lobby = {}
token = {}
# room_public = off keeps a hosted room out of the lobby's list (players join it by address)
room_public = {}
# the last room joined (its invite code) or hosted ("host"), for the menu's Rejoin last room
last_room = {}

[Mods]
# the launcher's mod catalog (Mods, Catalog) comes with the app's tools; a newer one is fetched
# from here when the launcher starts (empty: never; the copy that came with the app is used)
catalog_url = {}
)",
                       c.game, OnOff(c.launcher), c.new_3ds, c.cpu_clock, c.renderer, OnOff(c.fullscreen), c.resolution,
                       OnOff(c.vsync), c.screens, OnOff(c.linear_filter), OnOff(c.custom_textures),
                       c.shader, c.fps_counter, c.volume, c.audio_output, c.controller_auto ? "auto" : "off", keys,
                       params, c.touch_screen_key, c.fullscreen_key, c.fps_counter_key, c.nickname, c.join, c.password,
                       c.host, c.room_port, c.room_name, c.room_members, c.lobby, c.token,
                       OnOff(c.room_public), c.last_room, c.catalog_url);
}

void Apply(const Config& c) {
    auto& v = Settings::values;
    if (c.renderer == "opengl") {
        v.graphics_api = Settings::GraphicsAPI::OpenGL;
    } else if (c.renderer == "software") {
        v.graphics_api = Settings::GraphicsAPI::Software;
    } else {
        v.graphics_api = Settings::GraphicsAPI::Vulkan;
    }
    v.resolution_factor = static_cast<u32>(c.resolution);
    v.use_vsync = c.vsync;
    v.shaders_accurate_mul = c.accurate_mul;
    v.filter_mode = c.linear_filter;
    v.custom_textures = c.custom_textures;
    v.async_shader_compilation = c.async_shaders;
    v.use_disk_shader_cache = c.disk_shader_cache;
    v.use_skip_duplicate_frames = c.skip_duplicate_frames;
    {
        using TF = Settings::TextureFilter;
        const std::string& f = c.texture_filter;
        v.texture_filter = f == "anime4k"      ? TF::Anime4K
                           : f == "bicubic"    ? TF::Bicubic
                           : f == "scaleforce" ? TF::ScaleForce
                           : f == "xbrz"       ? TF::xBRZ
                           : f == "mmpx"       ? TF::MMPX
                                               : TF::NoFilter;
        using TS = Settings::TextureSampling;
        v.texture_sampling = c.texture_sampling == "nearest"  ? TS::NearestNeighbor
                             : c.texture_sampling == "linear" ? TS::Linear
                                                              : TS::GameControlled;
    }
    v.is_new_3ds = c.new_3ds == "auto" ? !c.custom_textures : IsOn(c.new_3ds);
    v.cpu_clock_percentage = static_cast<u32>(std::clamp(c.cpu_clock, 5, 400));
    v.async_custom_loading = true;
    if (!c.shader.empty()) {
        v.pp_shader_name = c.shader;
    }
    v.swap_screen = false;
    v.upright_screen = false;
    if (c.screens == "single") {
        v.layout_option = Settings::LayoutOption::SingleScreen;
    } else if (c.screens == "stacked") {
        v.layout_option = Settings::LayoutOption::Default;
    } else if (c.screens == "side") {
        v.layout_option = Settings::LayoutOption::SideScreen;
#ifndef ANDROID
    } else if (c.screens == "windows") {
        v.layout_option = Settings::LayoutOption::SeparateWindows;
#endif
    } else {
        // the custom layout is the port's overlay when mh4u.ini's touch_overlay is on
        v.layout_option = Settings::LayoutOption::CustomLayout;
    }
    v.volume = static_cast<float>(c.volume) / 100.0f;
    if (c.audio_output == "cubeb") {
        v.output_type = AudioCore::SinkType::Cubeb;
    } else if (c.audio_output == "sdl2") {
        v.output_type = AudioCore::SinkType::SDL2;
    } else if (c.audio_output == "openal") {
        v.output_type = AudioCore::SinkType::OpenAL;
    } else if (c.audio_output == "null") {
        v.output_type = AudioCore::SinkType::Null;
    } else {
        v.output_type = AudioCore::SinkType::Auto;
    }
    v.frame_limit = 100;
    // every system module high-level emulated, as Azahar's other frontends set it (Service::Init
    // looks each one up and throws on one that isn't there)
    for (const auto& module : Service::service_module_map) {
        v.lle_modules.emplace(module.name, false);
    }
    Input::BuildProfile(c);
}

} // namespace App
