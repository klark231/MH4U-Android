// mh4u: the MH4U PC port's own app. It opens Monster Hunter 4 Ultimate directly in a window of
// its own (SDL2), full screen, on Azahar's 3DS core with the game's recompiled code as the CPU
// (port/src/frontend.h), with the port's screen layouts, the keyboard and a controller mapped
// on their own, and multiplayer rooms joined from its settings. It contains no game code: the
// game is the player's own dump, and the code library is built from it on their machine.
//
// usage: mh4u [GAME] [--windowed | --fullscreen] [--renderer vulkan|opengl|software]
//             [--join HOST[:PORT] | --join mh4u://INVITE] [--host PORT] [--nickname NAME]
//             [--password PASSWORD] [--join-after SECONDS] [--new-console-id]
//             [--user DIR] [--launcher | --no-launcher] [--log FILTER]
// Settings: mh4u-app.ini (the app) and mh4u.ini (the game's options) in Azahar's user folder.
// Before the game, the launcher (launcher/) shows the home screen, setup, mods, room, settings.

#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <memory>
#include <string>
#include <thread>
#include <vector>
#include <SDL.h>
#include <fmt/format.h>

#include "common/common_types.h"
#include "common/detached_tasks.h"
#include "common/file_util.h"
#include "common/logging/backend.h"
#include "common/logging/filter.h"
#include "common/logging/log.h"
#include "common/scm_rev.h"
#include "common/settings.h"
#include "common/string_util.h"
#include "config.h"
#include "core/core.h"
#include "core/frontend/applets/default_applets.h"
#include "core/hle/service/apt/apt.h"
#include "core/hle/service/cfg/cfg.h"
#include "core/hle/service/hid/hid.h"
#include "core/hle/service/ir/ir_rst.h"
#include "core/hle/service/ir/ir_user.h"
#include "core/hle/service/sm/sm.h"
#include "core/loader/loader.h"
#include "dialog.h"
#include "frontend.h"
#include "input.h"
#include "input_common/main.h"
#include "invite.h"
#include "launcher/launcher.h"
#include "multiplayer.h"
#include "network/network.h"
#include "ui/ui.h"
#include "video_core/gpu.h"
#include "video_core/rasterizer_interface.h"
#include "video_core/renderer_base.h"
#include "window.h"

#ifdef _WIN32
#include <windows.h>
#include <shellapi.h>
extern "C" {
// laptops with two GPUs: run on the dedicated one
__declspec(dllexport) unsigned long NvOptimusEnablement = 0x00000001;
__declspec(dllexport) int AmdPowerXpressRequestHighPerformance = 1;
}
#endif

namespace {

constexpr const char* TITLE = "MH4U PC port";
constexpr const char* USAGE =
    "usage: mh4u [GAME] [--windowed | --fullscreen] [--renderer vulkan|opengl|software]\n"
    "            [--join HOST[:PORT] | --join mh4u://INVITE] [--host PORT] [--nickname NAME]\n"
    "            [--password PASSWORD] [--join-after SECONDS] [--new-console-id]\n"
    "            [--user DIR] [--launcher | --no-launcher] [--log FILTER]\n"
    "GAME: your decrypted Monster Hunter 4 Ultimate (.3ds, .cci, .cxi); without it, the one in\n"
    "mh4u-app.ini, or the app asks. Settings: mh4u-app.ini and mh4u.ini in Azahar's user folder\n"
    "(--user: another one, e.g. for a portable setup). --no-launcher: straight into the game\n"
    "(launcher = off in mh4u-app.ini), unless the setup is incomplete.\n"
    "--join: a room's address or an invite code; the room is joined before the game starts, or\n"
    "with --join-after that many seconds after it started (as the menu's Join does).\n"
    "--new-console-id: give this console a new ID first (a room lets in one player per console\n"
    "ID, and copies of one Azahar user folder have the same one).\n"
    "Rooms can also be joined, hosted and left in the game: Esc > Multiplayer.\n";

struct Args {
    std::string game, renderer, join, nickname, password, user, log;
    int fullscreen = -1;  // -1: as the settings say
    int host = -1;
    int launcher = -1;       // -1: as the settings say
    double join_after = -1;  // seconds after the game started (the menu's way); -1: before
    bool new_console_id = false;
    std::string check_code;  // the launcher's code check, in a process of its own
    bool help = false, bad = false;
};

Args Parse(const std::vector<std::string>& argv) {
    Args a;
    for (std::size_t i = 1; i < argv.size(); ++i) {
        const std::string& arg = argv[i];
        const auto value = [&]() -> std::string {
            if (i + 1 < argv.size()) {
                return argv[++i];
            }
            a.bad = true;
            return "";
        };
        if (arg == "-h" || arg == "--help") {
            a.help = true;
        } else if (arg == "--windowed") {
            a.fullscreen = 0;
        } else if (arg == "--fullscreen" || arg == "-f") {
            a.fullscreen = 1;
        } else if (arg == "--renderer") {
            a.renderer = value();
        } else if (arg == "--join") {
            a.join = value();
        } else if (arg == "--join-after") {
            a.join_after = std::atof(value().c_str());
            if (a.join_after < 0) {
                a.bad = true;
            }
        } else if (arg == "--new-console-id") {
            a.new_console_id = true;
        } else if (arg == "--host") {
            a.host = std::atoi(value().c_str());
        } else if (arg == "--nickname") {
            a.nickname = value();
        } else if (arg == "--password") {
            a.password = value();
        } else if (arg == "--user") {
            a.user = value();
        } else if (arg == "--log") {
            a.log = value();  // Azahar's log filter, e.g. "*:Info Render.OpenGL:Debug"
        } else if (arg == "--launcher" || arg == "--no-launcher") {
            a.launcher = arg == "--launcher" ? 1 : 0;
        } else if (arg == "--check-code") {
            a.check_code = value();
        } else if (!arg.empty() && arg[0] == '-') {
            a.bad = true;
        } else {
            a.game = arg;
        }
    }
    return a;
}

App::Window::Api ApiFor(const std::string& name) {
    if (name == "opengl") {
        return App::Window::Api::OpenGL;
    }
    if (name == "software") {
        return App::Window::Api::Software;
    }
    return App::Window::Api::Vulkan;
}

Settings::GraphicsAPI SettingFor(App::Window::Api api) {
    switch (api) {
    case App::Window::Api::OpenGL:
        return Settings::GraphicsAPI::OpenGL;
    case App::Window::Api::Software:
        return Settings::GraphicsAPI::Software;
    default:
        return Settings::GraphicsAPI::Vulkan;
    }
}

const char* NameOf(App::Window::Api api) {
    switch (api) {
    case App::Window::Api::OpenGL:
        return "OpenGL";
    case App::Window::Api::Software:
        return "software";
    default:
        return "Vulkan";
    }
}

/// The window for the chosen renderer, or for the next one that works (Vulkan, OpenGL,
/// software); Settings::values.graphics_api follows.
std::unique_ptr<App::Window> MakeWindow(Core::System& system, App::Window::Api wanted,
                                        const App::Config& config, App::Window::Api& used) {
    std::vector<App::Window::Api> order{wanted};
    for (const auto api : {App::Window::Api::Vulkan, App::Window::Api::OpenGL,
                           App::Window::Api::Software}) {
        if (api != wanted) {
            order.push_back(api);
        }
    }
    for (const auto api : order) {
        Settings::values.graphics_api = SettingFor(api);
        if (auto window = App::Window::Create(system, api, config)) {
            if (api != wanted) {
                LOG_WARNING(Frontend, "{} didn't start; using {}", NameOf(wanted), NameOf(api));
            }
            used = api;
            return window;
        }
    }
    return nullptr;
}

/// The input devices again, after the profile changed (a controller came or went), without
/// the rest of System::ApplySettings (which also restarts the sound).
void ReloadInput(Core::System& system) {
    if (const auto hid = Service::HID::GetModule(system)) {
        hid->ReloadInputDevices();
    }
    if (const auto apt = Service::APT::GetModule(system)) {
        apt->GetAppletManager()->ReloadInputDevices();
    }
    auto& sm = system.ServiceManager();
    if (const auto ir_user = sm.GetService<Service::IR::IR_USER>("ir:USER")) {
        ir_user->ReloadInputDevices();
    }
    if (const auto ir_rst = sm.GetService<Service::IR::IR_RST>("ir:rst")) {
        ir_rst->ReloadInputDevices();
    }
}

/// A new random console ID, kept in the user folder's system settings: two copies of one Azahar
/// user folder are one console to a room, which lets in only one of them.
void NewConsoleId(Core::System& system) {
    const auto cfg = Service::CFG::GetModule(system);
    const auto [random_number, console_id] = cfg->GenerateConsoleUniqueId();
    cfg->SetConsoleUniqueId(random_number, console_id);
    cfg->UpdateConfigNANDSavegame();
    LOG_INFO(Frontend, "the console has a new ID: {:016x}", console_id);
}

std::string LoadError(Core::System::ResultStatus status, Core::System& system) {
    using Status = Core::System::ResultStatus;
    switch (status) {
    case Status::ErrorGetLoader:
    case Status::ErrorLoader_ErrorInvalidFormat:
        return "This file isn't a 3DS game the app can open. Choose your decrypted Monster Hunter "
               "4 Ultimate (.3ds, .cci or .cxi) in mh4u-app.ini (game = ...) or remove that line to "
               "be asked again.";
    case Status::ErrorLoader_ErrorEncrypted:
        return "The game file is encrypted. The port needs a decrypted dump of your own cartridge "
               "(GodMode9 can make one; docs/SETUP.md in the port's repository).";
    case Status::ErrorSystemFiles:
        return "Some 3DS system files are missing (Azahar's System settings can set them up).";
    default:
        return fmt::format("The game didn't start: {}", system.GetStatusDetails());
    }
}

int Run(const std::vector<std::string>& argv) {
    Common::DetachedTasks detached_tasks;
    const Args args = Parse(argv);
    if (args.help || args.bad) {
        std::fputs(USAGE, args.bad ? stderr : stdout);
        return args.bad ? 1 : 0;
    }
    if (!args.user.empty()) {
        // before anything looks at the user folder (the log is in it)
        const char last = args.user.back();
        FileUtil::SetUserPath(last == '/' || last == '\\' ? args.user : args.user + '/');
    }
    // (the launcher's code check logs to a file of its own: the launcher's process has this one)
    Common::Log::Initialize(args.check_code.empty() ? "mh4u_log.txt" : "mh4u_check_log.txt");
    Common::Log::SetColorConsoleBackendEnabled(true);
    Common::Log::Start();
    Common::Log::Filter filter;
    filter.ParseFilterString(args.log.empty() ? Settings::values.log_filter.GetValue() : args.log);
    Common::Log::SetGlobalFilter(filter);
    if (!args.check_code.empty()) {
        const int status = Launcher::CheckCode(args.check_code);
        Common::Log::Stop();
        return status;
    }

    LOG_INFO(Frontend, "MH4U PC port app, Azahar {} | {}-{}", Common::g_build_fullname,
             Common::g_scm_branch, Common::g_scm_desc);

    App::Config config = App::Load();
    if (args.fullscreen >= 0) {
        config.fullscreen = args.fullscreen == 1;
    }
    if (!args.renderer.empty()) {
        config.renderer = args.renderer;
    }
    if (!args.join.empty()) {
        config.join = args.join;
        config.host = 0;
    }
    if (args.host >= 0) {
        config.host = args.host;
    }
    if (!args.nickname.empty()) {
        config.nickname = args.nickname;
    }
    if (!args.password.empty()) {
        config.password = args.password;
    }
    // the game's options, with the recommended settings when there are none yet (as the Qt
    // frontend's Tools > MH4U Port Options... does)
    if (!FileUtil::Exists(Port::OptionsPath())) {
        FileUtil::WriteStringToFile(true, Port::OptionsPath(), Port::OptionsTemplate());
    }

    if (!App::Window::InitSDL()) {
        return 1;
    }
    InputCommon::Init();
    App::Input::Register();
    Network::Init();
    auto& system = Core::System::GetInstance();
    App::Multiplayer::Init(system, config);

    // ── the launcher (launcher/): the home screen, setup, mods, the room and settings; it
    // leaves the game file and the room in `config`. Without a window of its own, as before.
    if (!args.game.empty()) {
        config.game = args.game;
    }
    const bool show_launcher = args.launcher >= 0 ? args.launcher == 1 : config.launcher;
    if (Launcher::Run(config, show_launcher) == Launcher::Result::Quit) {
        Network::Shutdown();
        InputCommon::Shutdown();
        Common::Log::Stop();
        SDL_Quit();
        return 0;
    }
    // ──

    std::string game = config.game;
    if (game.empty() || !FileUtil::Exists(game)) {
        if (!game.empty()) {
            LOG_WARNING(Frontend, "{} isn't there any more", game);
        }
        game = App::Dialog::OpenFile(
            "Choose your Monster Hunter 4 Ultimate (decrypted .3ds, .cci or .cxi)",
            {"3ds", "cci", "cxi", "app"});
        if (game.empty()) {
            App::Dialog::Message(TITLE,
                                 "No game chosen. Put the path of your decrypted Monster Hunter 4 "
                                 "Ultimate in " + App::ConfigPath() + " (game = ...), or start "
                                 "the app with the file as its argument.",
                                 true);
            return 1;
        }
        config.game = game;
        App::Save(config);
    }

    App::Apply(config);
    LOG_INFO(Frontend, "{}", App::Input::ControllerDescription());

    Frontend::RegisterDefaultApplets(system);
    system.ApplySettings();

    if (args.new_console_id) {
        NewConsoleId(system);
    }
    if (args.join_after >= 0) {
        // --join-after: the room is joined from the game, by the menu's own Join
        if (const auto room = App::Invite::Parse(config.join); room.ok) {
            App::Multiplayer::JoinRequest request;
            request.host = room.host;
            request.port = room.port;
            request.password = room.code ? room.password : config.password;
            App::Multiplayer::JoinLater(request, args.join_after);
        } else {
            LOG_ERROR(Frontend, "--join-after: no room to join ({})", room.error);
        }
        config.join.clear();
    }
    if (std::string error; !App::Multiplayer::StartAtBoot(error)) {
        App::Dialog::Message(TITLE, error + " The game starts without the room (Esc > Multiplayer joins "
                             "one while it runs).", true);
    }

    LOG_INFO(Frontend, "{}", Port::PrepareBackendFor(system, game));
    // (not again when the launcher said it and the player chose to play anyway)
    if (const std::string notice = Port::BackendNotice(); !notice.empty() && !Launcher::CodeNoticeSeen()) {
        App::Dialog::Message(TITLE, notice);
    }

    App::Window::Api api = ApiFor(config.renderer);
    auto window = MakeWindow(system, api, config, api);
    if (!window) {
        App::Dialog::Message(TITLE, "No renderer could open a window (see mh4u_log.txt).", true);
        App::Multiplayer::Shutdown();
        return 1;
    }
    std::unique_ptr<App::Window> secondary;
    if (Settings::values.layout_option.GetValue() == Settings::LayoutOption::SeparateWindows &&
        api != App::Window::Api::Software) {
        secondary = App::Window::Create(system, api, config, true);
        if (!secondary) {
            Settings::values.layout_option = Settings::LayoutOption::CustomLayout;
        }
    }
    LOG_INFO(Frontend, "renderer: {}", NameOf(api));

    // the in-game menu (Esc, a controller's Guide or Back+Start), and the 3DS software keyboard
    // drawn in it instead of Azahar's default one (ui/)
    App::Ui::Start(system, *window, config);

    const auto scope = window->Acquire();
    Settings::LogSettings();
    const auto status = system.Load(*window, game, secondary.get());
    if (status != Core::System::ResultStatus::Success) {
        App::Dialog::Message(TITLE, LoadError(status, system), true);
        App::Multiplayer::Shutdown();
        Network::Shutdown();
        return 1;
    }

    std::thread present([&window] { window->Present(); });
    std::thread present_secondary([&secondary] {
        if (secondary) {
            secondary->Present();
        }
    });

    u64 program_id = 0;
    system.GetAppLoader().ReadProgramId(program_id);
    system.GPU().ApplyPerProgramSettings(program_id);
    std::atomic_bool stop_loading{false};
    system.GPU().Renderer().Rasterizer()->LoadDefaultDiskResources(
        stop_loading, [](VideoCore::LoadCallbackStage, std::size_t value, std::size_t total,
                         const std::string&) {
            if (total && (value == total || value % 256 == 0)) {
                LOG_DEBUG(Frontend, "shader cache {}/{}", value, total);
            }
        });

    while (window->IsOpen() && (!secondary || secondary->IsOpen())) {
        const auto result = system.RunLoop();
        if (result == Core::System::ResultStatus::ShutdownRequested) {
            break;
        }
        if (result != Core::System::ResultStatus::Success) {
            LOG_ERROR(Frontend, "emulation stopped: {}", system.GetStatusDetails());
            App::Dialog::Message(TITLE, "The game stopped: " + system.GetStatusDetails(), true);
            break;
        }
        App::Multiplayer::Tick();
        if (window->TakeControllersChanged()) {
            App::Input::BuildProfile(config);
            ReloadInput(system);
            LOG_INFO(Frontend, "{}", App::Input::ControllerDescription());
        }
    }
    window->RequestClose();
    if (secondary) {
        secondary->RequestClose();
    }
    present.join();
    present_secondary.join();

    system.Shutdown();
    App::Ui::Stop(); // after the renderer, before SDL and the room go
    App::Multiplayer::Shutdown();
    Network::Shutdown();
    InputCommon::Shutdown();
    detached_tasks.WaitForAllTasks();
    Common::Log::Stop();
    SDL_Quit();
    return 0;
}

} // namespace

int main(int argc, char** argv) {
#ifdef _WIN32
    // the arguments as UTF-16, so that paths with any characters arrive whole (MinGW's runtime
    // calls main even in a windowed program, with the arguments in the ANSI code page)
    int wargc = 0;
    LPWSTR* wargv = CommandLineToArgvW(GetCommandLineW(), &wargc);
    std::vector<std::string> args;
    for (int i = 0; i < wargc; ++i) {
        args.push_back(Common::UTF16ToUTF8(std::wstring(wargv[i])));
    }
    LocalFree(wargv);
    return Run(args);
#else
    return Run(std::vector<std::string>(argv, argv + argc));
#endif
}
