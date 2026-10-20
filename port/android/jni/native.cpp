// The Android app's native side (org.mh4u.port.Native): runs Azahar's core with the game's
// recompiled code as the CPU, on a Surface from Kotlin. Everything the PC app (port/app) does
// around the core is the same code here where it doesn't depend on SDL: the app's settings
// (config.cpp), rooms (multiplayer.cpp, invite.cpp, launcher/lobby.cpp) and the port's hooks
// and options (src/hooks.cpp). What differs is this file: the window, input, storage, the
// keyboard and the thread the game runs on.
#include <atomic>
#include <condition_variable>
#include <functional>
#include <memory>
#include <mutex>
#include <queue>
#include <string>

#include <android/native_window_jni.h>
#include <jni.h>
#include <json.hpp>

#include "common/android_utils.h"
#include "common/file_util.h"
#include "common/logging/backend.h"
#include "common/logging/filter.h"
#include "common/logging/log.h"
#include "common/scope_exit.h"
#include "common/settings.h"
#include "config.h"
#include "core/core.h"
#include "core/frontend/applets/default_applets.h"
#include "core/hle/service/am/am.h"
#include "core/hle/service/cfg/cfg.h"
#include "core/loader/loader.h"
#include "emu_window_android.h"
#include "frontend.h"
#include "hooks.h"
#include "input_common/main.h"
#include "input_map.h"
#include "invite.h"
#include "jni_env.h"
#include "keyboard_android.h"
#include "launcher/lobby.h"
#include "multiplayer.h"
#include "network/network.h"
#include "pad_device.h"
#include "video_core/gpu.h"
#include "video_core/renderer_base.h"

namespace App::Input {
// config.cpp's Apply() asks for the input profile; here the pad device builds it (pad_device.cpp)
void BuildProfile(const Config&) {
    Mh4uAndroid::Pad::Init();
}
std::string ControllerDescription() {
    return "controls: the app's pad (on-screen controls and any device Android lists)";
}
} // namespace App::Input

namespace {

using nlohmann::json;
using Mh4uAndroid::EmuWindow;

// ── state shared between the UI thread and the emulation thread ──
std::recursive_mutex surface_mutex;
std::condition_variable_any surface_cv;
ANativeWindow* g_surface = nullptr;
std::unique_ptr<EmuWindow> g_window;

std::atomic<bool> g_stop{true};
std::atomic<bool> g_pause{false};
std::mutex g_run_mutex;  // one game at a time
std::mutex g_pause_mutex;
std::condition_variable g_pause_cv;

std::mutex g_task_mutex;
std::queue<std::function<void()>> g_tasks;  // for the emulation thread, once per loop

App::Config g_config;
std::string g_notice;  // the code library's note for the player (Port::BackendNotice)
std::mutex g_notice_mutex;
bool g_initialised = false;

void Post(std::function<void()> task) {
    std::scoped_lock lock(g_task_mutex);
    g_tasks.push(std::move(task));
}

void RunTasks() {
    std::queue<std::function<void()>> now;
    {
        std::scoped_lock lock(g_task_mutex);
        now.swap(g_tasks);
    }
    for (; !now.empty(); now.pop()) {
        now.front()();
    }
}

std::string Str(JNIEnv* env, jstring s) {
    if (!s) {
        return {};
    }
    const char* chars = env->GetStringUTFChars(s, nullptr);
    std::string out = chars ? chars : "";
    if (chars) {
        env->ReleaseStringUTFChars(s, chars);
    }
    return out;
}

jstring JStr(JNIEnv* env, const std::string& s) {
    return env->NewStringUTF(s.c_str());
}

/// What Kotlin's screen options ask for that the PC app has no name for: portrait layouts, the
/// screen gap, the large screen's share, swapped and upright screens. "key=value" lines.
void ApplyScreenExtras(const std::string& text) {
    auto& v = Settings::values;
    std::size_t at = 0;
    while (at < text.size()) {
        const std::size_t end = std::min(text.find('\n', at), text.size());
        const std::string line = text.substr(at, end - at);
        at = end + 1;
        const std::size_t eq = line.find('=');
        if (eq == std::string::npos) {
            continue;
        }
        const std::string key = line.substr(0, eq);
        const std::string value = line.substr(eq + 1);
        if (key == "swap") {
            v.swap_screen = value == "1";
        } else if (key == "upright") {
            v.upright_screen = value == "1";
        } else if (key == "gap") {
            v.screen_gap = std::atoi(value.c_str());
        } else if (key == "large_proportion") {
            v.large_screen_proportion = static_cast<float>(std::atof(value.c_str()));
        } else if (key == "portrait") {
            v.portrait_layout_option =
                value == "original" ? Settings::PortraitLayoutOption::PortraitOriginal
                : value == "custom" ? Settings::PortraitLayoutOption::PortraitCustomLayout
                                    : Settings::PortraitLayoutOption::PortraitTopFullWidth;
        }
    }
}

/// The app's settings (mh4u-app.ini) as Azahar's, then what is Android's own. PC's Apply() does
/// the common part (renderer, filter, clock, sound level, the input profile).
void ApplyConfig(const App::Config& config) {
    App::Apply(config);
    auto& v = Settings::values;
    v.graphics_api = Settings::GraphicsAPI::Vulkan;   // the one renderer of this app
    v.output_type = AudioCore::SinkType::Cubeb;       // Cubeb's Android backend (AAudio)
    v.use_vsync = config.vsync;
    // this Azahar counts the internal resolution in percent (100: native); the app's ini in
    // multiples of the 3DS's, 0 meaning from the screen
    v.resolution_factor = static_cast<u32>(config.resolution * 100);
    // layouts the PC app doesn't have (Azahar's own names)
    if (config.screens == "large") {
        v.layout_option = Settings::LayoutOption::LargeScreen;
    } else if (config.screens == "hybrid") {
        v.layout_option = Settings::LayoutOption::HybridScreen;
    } else if (config.screens == "windows") {
        v.layout_option = Settings::LayoutOption::CustomLayout;  // no second window on a phone
    }
}

// ── the game thread ──
Core::System::ResultStatus Run(const std::string& game, std::string& message) {
    std::scoped_lock run_lock(g_run_mutex);
    auto& system = Core::System::GetInstance();

    // A new run starts "not stopped": g_stop begins true, so without this the wait below
    // returns at once, before the surface exists, and the run ends silently.
    g_stop = false;

    // the surface the window draws on: wait for the activity to hand one over
    std::unique_lock<std::recursive_mutex> surface_lock(surface_mutex);
    surface_cv.wait(surface_lock, [] { return g_surface != nullptr || g_stop.load(); });
    if (!g_surface) {
        return Core::System::ResultStatus::ShutdownRequested;
    }
    g_window = std::make_unique<EmuWindow>(g_surface);

    // the core re-creates its renderer on a save-state load: hold the surface for that
    system.RegisterOnInitCallback([](bool init_start) {
        if (init_start) {
            surface_mutex.lock();
            if (!g_surface) {
                std::unique_lock<std::recursive_mutex> wait(surface_mutex, std::adopt_lock);
                surface_cv.wait(wait, [] { return g_surface != nullptr; });
                wait.release();
            }
        } else {
            surface_mutex.unlock();
        }
    });

    g_config = App::Load();
    ApplyConfig(g_config);
    Frontend::RegisterDefaultApplets(system);
    system.RegisterSoftwareKeyboard(std::make_shared<Mh4uAndroid::Keyboard>());
    system.ApplySettings();

    if (std::string error; !App::Multiplayer::StartAtBoot(error)) {
        message = error + " The game starts without the room.";
    }
    LOG_INFO(Frontend, "{}", Port::PrepareBackendFor(system, game));
    {
        std::scoped_lock lock(g_notice_mutex);
        g_notice = Port::BackendNotice();  // the activity shows it once the game is up
    }

    Settings::LogSettings();
    const auto status = system.Load(*g_window, game);
    surface_lock.unlock();
    if (status != Core::System::ResultStatus::Success) {
        message = system.GetStatusDetails();
        g_window.reset();
        return status;
    }

    g_pause = false;
    system.RegisterCoreLoopThreadId();
    SCOPE_EXIT({
        std::scoped_lock lock(surface_mutex);
        g_window->DoneCurrent();
        system.Shutdown();
        g_window.reset();
    });

    while (!g_stop) {
        RunTasks();
        App::Multiplayer::Tick();
        if (g_pause) {
            const float volume = Settings::values.volume.GetValue();
            SCOPE_EXIT({ Settings::values.volume = volume; });
            Settings::values.volume = 0;
            std::unique_lock lock(g_pause_mutex);
            g_pause_cv.wait(lock, [] { return !g_pause || g_stop; });
            continue;
        }
        const auto result = system.RunLoop();
        if (result == Core::System::ResultStatus::Success) {
            continue;
        }
        if (result == Core::System::ResultStatus::ShutdownRequested) {
            return result;
        }
        message = system.GetStatusDetails();
        return result;
    }
    return Core::System::ResultStatus::Success;
}

json MemberJson(const App::Multiplayer::Member& m) {
    return {{"nickname", m.nickname}, {"username", m.username}, {"game", m.game},
            {"you", m.you},           {"heard", m.heard}};
}

json StatusJson() {
    const auto s = App::Multiplayer::GetStatus();
    json j;
    j["phase"] = s.phase == App::Multiplayer::Phase::Off    ? "off"
                 : s.phase == App::Multiplayer::Phase::Busy ? "busy"
                                                            : "room";
    j["action"] = s.action;
    j["error"] = s.error;
    j["hint"] = s.hint;
    j["room_name"] = s.room_name;
    j["host"] = s.host;
    j["port"] = s.port;
    j["password"] = s.password;
    j["slots"] = s.slots;
    j["hosting"] = s.hosting;
    j["moderator"] = s.moderator;
    j["nickname"] = s.nickname;
    j["joined_in_game"] = s.joined_in_game;
    j["lobby_note"] = s.lobby_note;
    j["public_address"] = s.public_address;
    j["public_checked"] = s.public_checked;
    j["members"] = json::array();
    for (const auto& m : s.members) {
        j["members"].push_back(MemberJson(m));
    }
    j["addresses"] = json::array();
    for (const auto& a : s.addresses) {
        j["addresses"].push_back({{"ip", a.ip}, {"kind", a.kind}});
    }
    j["invite"] = s.phase == App::Multiplayer::Phase::InRoom && s.hosting && !s.addresses.empty()
                      ? App::Invite::Format(s.addresses.front().ip, s.port, s.password)
                      : std::string{};
    return j;
}

} // namespace

#define FN(ret, name, ...) extern "C" JNIEXPORT ret JNICALL Java_org_mh4u_port_Native_##name(JNIEnv* env, jclass, ##__VA_ARGS__)

extern "C" JNIEXPORT jint JNICALL JNI_OnLoad(JavaVM* vm, void*) {
    Mh4uAndroid::SetVm(vm);
    JNIEnv* env = nullptr;
    vm->GetEnv(reinterpret_cast<void**>(&env), JNI_VERSION_1_6);
    // the core's file access calls static methods of this class (storage root, raw paths)
    jclass storage = reinterpret_cast<jclass>(env->NewGlobalRef(env->FindClass("org/mh4u/port/Storage")));
    AndroidUtils::InitJNI(env, storage);
    Mh4uAndroid::InitKeyboardJni();
    return JNI_VERSION_1_6;
}

/// Once, before anything else. `privateDir` is where the code library is staged (app storage
/// that can execute; the user folder can't).
FN(void, init, jstring private_dir) {
    if (g_initialised) {
        return;
    }
    setenv("MH4U_PRIVATE_DIR", Str(env, private_dir).c_str(), 1);
    Common::Log::Initialize("mh4u_log.txt");
    Common::Log::Start();
    Common::Log::Filter filter;
    filter.ParseFilterString(Settings::values.log_filter.GetValue());
    Common::Log::SetGlobalFilter(filter);
    InputCommon::Init();
    Network::Init();
    g_config = App::Load();
    if (!FileUtil::Exists(Port::OptionsPath())) {
        FileUtil::WriteStringToFile(true, Port::OptionsPath(), Port::OptionsTemplate());
    }
    App::Multiplayer::Init(Core::System::GetInstance(), g_config);
    g_initialised = true;
}

FN(void, surfaceChanged, jobject surface) {
    std::scoped_lock lock(surface_mutex);
    if (g_surface) {
        ANativeWindow_release(g_surface);
        g_surface = nullptr;
    }
    g_surface = surface ? ANativeWindow_fromSurface(env, surface) : nullptr;
    if (!g_surface) {
        return;
    }
    surface_cv.notify_all();
    auto& system = Core::System::GetInstance();
    if (g_window && g_window->OnSurfaceChanged(g_surface) && system.IsPoweredOn()) {
        system.GPU().Renderer().NotifySurfaceChanged(false);
    }
}

FN(void, surfaceDestroyed) {
    std::scoped_lock lock(surface_mutex);
    if (g_surface) {
        ANativeWindow_release(g_surface);
        g_surface = nullptr;
        if (g_window) {
            g_window->OnSurfaceChanged(nullptr);
        }
    }
}

/// Runs the game on the calling thread until it stops. Returns "" on a normal end, else the
/// text for the player (why it didn't start, or what the port wants said). The code library's
/// notice comes back through `notice()` below.
FN(jstring, run, jstring game) {
    std::string message;
    const auto status = Run(Str(env, game), message);
    using Status = Core::System::ResultStatus;
    std::string out;
    switch (status) {
    case Status::Success:
    case Status::ShutdownRequested:
        out = "";
        break;
    case Status::ErrorGetLoader:
    case Status::ErrorLoader_ErrorInvalidFormat:
        out = "This file isn't a 3DS game the app can open. Choose your decrypted Monster Hunter "
              "4 Ultimate (.3ds, .cci or .cxi).";
        break;
    case Status::ErrorLoader_ErrorEncrypted:
        out = "The game file is encrypted. The port needs a decrypted dump of your own cartridge.";
        break;
    case Status::ErrorSystemFiles:
        out = "Some 3DS system files are missing.";
        break;
    default:
        out = message.empty() ? "The game stopped." : message;
    }
    return JStr(env, out);
}

FN(void, stop) {
    if (g_stop.exchange(true)) {
        return;
    }
    g_pause = false;
    g_pause_cv.notify_all();
    surface_cv.notify_all();
}

FN(void, setPaused, jboolean paused) {
    g_pause = paused;
    if (!paused) {
        g_pause_cv.notify_all();
    }
}

FN(jboolean, isRunning) {
    return !g_stop;
}

FN(void, touch, jint x, jint y, jboolean down) {
    if (g_window) {
        g_window->OnTouch(x, y, down);
    }
}

FN(void, touchMove, jint x, jint y) {
    if (g_window) {
        g_window->OnTouchMoved(x, y);
    }
}

/// The two screens' rectangles in window pixels (top, bottom): left, top, right, bottom each;
/// then the window's size. For the touch overlay editor and the on-screen controls.
FN(jfloatArray, layout) {
    jfloatArray out = env->NewFloatArray(10);
    if (!g_window) {
        return out;
    }
    const auto& l = g_window->GetFramebufferLayout();
    const float v[10] = {
        static_cast<float>(l.top_screen_enabled ? l.top_screen.left : 0),
        static_cast<float>(l.top_screen_enabled ? l.top_screen.top : 0),
        static_cast<float>(l.top_screen_enabled ? l.top_screen.right : 0),
        static_cast<float>(l.top_screen_enabled ? l.top_screen.bottom : 0),
        static_cast<float>(l.bottom_screen_enabled ? l.bottom_screen.left : 0),
        static_cast<float>(l.bottom_screen_enabled ? l.bottom_screen.top : 0),
        static_cast<float>(l.bottom_screen_enabled ? l.bottom_screen.right : 0),
        static_cast<float>(l.bottom_screen_enabled ? l.bottom_screen.bottom : 0),
        static_cast<float>(l.width), static_cast<float>(l.height)};
    env->SetFloatArrayRegion(out, 0, 10, v);
    return out;
}

/// Re-reads mh4u-app.ini and applies it to the running game. `extras`: see ApplyScreenExtras.
FN(void, applySettings, jstring extras) {
    const std::string extra_text = Str(env, extras);
    Post([extra_text] {
        g_config = App::Load();
        ApplyConfig(g_config);
        ApplyScreenExtras(extra_text);
        Core::System::GetInstance().ApplySettings();
        if (auto* gpu = &Core::System::GetInstance().GPU(); gpu) {
            // the layout follows the new option at once
            if (g_window) {
                g_window->OnSurfaceChanged(g_surface);
            }
        }
    });
    // before a game runs there is no emulation thread to take the task: do it here
    if (g_stop) {
        RunTasks();
    }
}

/// A game option of mh4u.ini changed while the game runs (the port's hooks read these live).
FN(jboolean, setGameOption, jstring key, jstring value) {
    const std::string k = Str(env, key), v = Str(env, value);
    Post([k, v] { Port::Hooks::SetOption(k, v); });
    return JNI_TRUE;
}

/// The code library's note for the player ("" when all is well): shown once the game runs.
FN(jstring, notice) {
    std::scoped_lock lock(g_notice_mutex);
    return JStr(env, g_notice);
}

FN(void, toggleQuestMap) {
    Port::ToggleQuestMap();
}

FN(jstring, optionsPath) {
    return JStr(env, Port::OptionsPath());
}

/// Frames per second the game draws, for the counter; speed in % of real time.
FN(jdoubleArray, perf) {
    jdoubleArray out = env->NewDoubleArray(2);
    auto& system = Core::System::GetInstance();
    if (system.IsPoweredOn()) {
        const auto r = system.GetAndResetPerfStats();
        const double v[2] = {r.game_fps, r.emulation_speed * 100.0};
        env->SetDoubleArrayRegion(out, 0, 2, v);
    }
    return out;
}

// ── installing the game's update (a .cia) ──
FN(jint, installCia, jstring path) {
    return static_cast<jint>(Service::AM::InstallCIA(Str(env, path)));
}

// ── multiplayer: App::Multiplayer, as the PC app's menu uses it ──
FN(jboolean, roomJoin, jstring host, jint port, jstring password) {
    App::Multiplayer::JoinRequest request;
    request.host = Str(env, host);
    request.port = port;
    request.password = Str(env, password);
    return App::Multiplayer::Join(request);
}

FN(jboolean, roomHost, jint port, jstring name, jint slots, jstring password, jboolean listed) {
    App::Multiplayer::HostRequest request;
    request.port = port;
    request.name = Str(env, name);
    request.slots = slots;
    request.password = Str(env, password);
    request.listed = listed;
    return App::Multiplayer::Host(request);
}

FN(jboolean, roomLeave, jboolean close) {
    return App::Multiplayer::Leave(close);
}

FN(jboolean, roomRejoin) {
    return App::Multiplayer::Rejoin();
}

FN(jstring, roomLast) {
    return JStr(env, App::Multiplayer::LastRoom());
}

FN(jstring, roomStatus) {
    return JStr(env, StatusJson().dump());
}

/// "ok|host|port|password|error" for an invite code or an address.
FN(jstring, parseInvite, jstring text) {
    const auto room = App::Invite::Parse(Str(env, text));
    json j = {{"ok", room.ok},     {"error", room.error}, {"host", room.host},
              {"port", room.port}, {"password", room.password}, {"code", room.code}};
    return JStr(env, j.dump());
}

FN(jstring, formatInvite, jstring host, jint port, jstring password) {
    return JStr(env, App::Invite::Format(Str(env, host), port, Str(env, password)));
}

FN(jboolean, validNickname, jstring name) {
    return App::Multiplayer::ValidNickname(Str(env, name));
}

/// A lobby's rooms as JSON (blocks: call off the main thread).
FN(jstring, lobbyRooms, jstring url) {
    const auto list = Launcher::Lobby::Fetch(Str(env, url));
    json j = {{"ok", list.ok}, {"error", list.error}, {"rooms", json::array()}};
    for (const auto& r : list.rooms) {
        j["rooms"].push_back({{"name", r.name},
                              {"description", r.description},
                              {"owner", r.owner},
                              {"address", r.address},
                              {"port", r.port},
                              {"max_players", r.max_players},
                              {"password", r.password},
                              {"players", r.players},
                              {"mh4u", r.mh4u}});
    }
    return JStr(env, j.dump());
}

// ── the combo engine's text form (input bindings), kept native so Kotlin stays thin ──
// (jni_input.cpp: Java_org_mh4u_port_MH4UInput_*)
