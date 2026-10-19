#include "frontend.h"

#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <memory>
#include <vector>
#include <fmt/format.h>

#include "arm_recomp.h"
#include "hooks.h"
#include "common/file_util.h"
#include "common/logging/log.h"
#include "core/core.h"
#include "core/file_sys/ncch_container.h"
#include "core/hle/service/am/am.h"
#include "core/loader/loader.h"

namespace Port {

namespace {

constexpr u64 MH4U_USA = 0x0004000000126300;
std::string notice;

constexpr const char* BUILD_HINT =
    "Build it from your own copy of the v1.3.0 update with \"Build code library\" next to "
    "azahar.exe on Windows, or tools/build_game.py (docs/PLAYING.md in the port's repository).";

/// The code Azahar's loader will put in memory for the game at `rom_path`: the installed
/// update's when there is one, else the game's own, with the code mods in Azahar's mods folder
/// applied (as Loader::AppLoader_NCCH does). Empty if it can't be read.
std::vector<u8> CodeToBeLoaded(const std::string& rom_path, u64 program_id) {
    constexpr u64 UPDATE_TID_HIGH = 0x0004000e00000000;
    FileSys::NCCHContainer update(Service::AM::GetTitleContentPath(
        Service::FS::MediaType::SDMC, (program_id & 0xFFFFFFFF) | UPDATE_TID_HIGH));
    FileSys::NCCHContainer base(rom_path);
    FileSys::NCCHContainer* ncch = update.Load() == Loader::ResultStatus::Success ? &update : &base;
    std::vector<u8> code;
    if (ncch->Load() != Loader::ResultStatus::Success ||
        ncch->LoadSectionExeFS(".code", code) != Loader::ResultStatus::Success) {
        return {};
    }
    const auto patched = ncch->ApplyCodePatch(code);
    if (patched != Loader::ResultStatus::Success && patched != Loader::ResultStatus::ErrorNotUsed) {
        return {};
    }
    return code;
}


#ifdef __ANDROID__
// Shared storage (where the user's Azahar folder is) is mounted noexec, so dlopen() refuses a
// library there. A copy in the app's own storage loads. The copy is replaced when the original
// changes size or date.
std::string StageForAndroid(const std::string& virtual_library, std::string& error) {
    namespace fs = std::filesystem;
    // Azahar's Android core names files from the user folder's virtual root ("/recomp/x.so");
    // std::filesystem needs the real path
    const std::string library = AndroidUtils::TranslateFilePath(virtual_library);
    std::string dir;
    if (const char* env = std::getenv("MH4U_PRIVATE_DIR"); env && *env) {
        dir = env; // set by the app: its code cache directory
    } else if (std::ifstream cmdline("/proc/self/cmdline"); cmdline) {
        std::string package; // the app's process is named after its package
        std::getline(cmdline, package, '\0');
        dir = "/data/data/" + package.substr(0, package.find(':')) + "/code_cache";
    }
    std::error_code ec;
    if (dir.empty() || (fs::create_directories(dir, ec), ec)) {
        error = "no private directory to load the library from";
        return library;
    }
    const fs::path target = fs::path(dir) / "mh4u_code.so";
    const auto same = fs::exists(target, ec) && fs::file_size(target, ec) == fs::file_size(library, ec) &&
                      fs::last_write_time(target, ec) >= fs::last_write_time(library, ec);
    if (!same) {
        fs::remove(target, ec);
        fs::copy_file(library, target, fs::copy_options::overwrite_existing, ec);
        if (ec) {
            error = "couldn't copy the library into the app's storage: " + ec.message();
            return library;
        }
        fs::permissions(target, fs::perms::owner_read | fs::perms::owner_exec, ec); // read-only
    }
    return target.string();
}
#endif

} // namespace

std::string BackendNotice() {
    return notice;
}

void ToggleQuestMap() {
    Hooks::ToggleQuestMap();
}

std::string OptionsPath() {
    return FileUtil::GetUserPath(FileUtil::UserPath::UserDir) + "mh4u.ini";
}

std::string OptionsTemplate() {
    return R"(# MH4U PC port options. Changes apply the next time the game starts.
# One "key = value" per line; a line starting with # is ignored. All of them, and what was
# tried: docs/PLAYING.md in the port's repository.

# frame-rate cap (the game runs at 30 on a 3DS; its speed stays right at any rate), e.g. your
# monitor's refresh rate; above 60 a busy scene may need Emulation > Configure > Debug > CPU
# Clock Speed raised
fps = 60

# the top screen's shape: 16:9, 16:10, 21:9, 32:9, 4:3 or off. wide_render = 1 draws the
# wider picture itself (0 stretches a 400x240 one); hud_edges = 1 moves the hunting HUD's
# sides (health, stamina, party list; the item selection) out to the screen's edges
widescreen = 16:9
wide_render = 1
hud_edges = 1

# View > Screen Layout > Custom Layout: the game fills the window and the touch screen sits
# in a corner (top-left, top-right, bottom-left, bottom-right), its height in % of the
# window's, its opacity in %. off = Azahar's own custom layout
touch_overlay = top-right 30 85
# in quests that corner shows only the map (off = the whole touch screen); F12 switches
# between the two while you play. map 40 = 40% of the window's height (without a size: the
# touch screen's scale); map 40 at 100 0 also places it: across and down in % of the room the
# window leaves (0 0 = top left, 100 0 = top right, 50 100 = bottom middle), so it stays in the
# window at any size. The in-game menu (Esc) > Game > Edit HUD layout places it with the mouse
quest_overlay = map
# with the map, the target camera panel (whose monster the camera follows; a tap on it changes
# the target) in a small view of its own, if your panel layout has it: on = under the map at
# the touch screen's scale, on 12 at 100 30 = 12% of the window's height, placed as above; off
target_overlay = on

# buttons for the kick and the target camera, touch-screen panels on a 3DS
# (A B X Y L R ZL ZR START UP DOWN LEFT RIGHT; ZL and ZR are a controller's triggers)
kick = ZR
target = ZL
# a button for the touch screen's Skip, which replaces the kick button once a quest is
# complete and skips the scene of the monster's end (the kick does nothing then, so ZR suits
# both); buttons held together for skip_end_hold seconds to end the wait before the return,
# the carving time, as if it had run out (online, once every player with the port has held
# them; the screen says who hasn't); skip_hint: lines on the screen say which keys do what
# (on, flash or off)
skip = ZR
skip_end = ZL+ZR
skip_end_hold = 1.0
skip_hint = on

# the game's dither pattern: off for a clean picture at an Internal Resolution above 1x
dither = off
# the game's stereoscopic 3D: it draws the top screen twice (one pass per eye) even with the 3D
# slider at 0, which costs a full scene pass and can show as a doubled, soft picture; off draws
# it once, as the community's "3D off" cheat does. on = as on a 3DS
stereo_3d = off
# the game's own anti-aliasing (on, as on a 3DS): off saves its pass, which at a high Internal
# Resolution costs frames for little; the edges get a little jagged
antialiasing = on

# the game's screen filters (on = as on a 3DS, off = skipped): the glow over bright parts, which
# softens the picture; the focus blur of cutscenes, which emulation gets wrong (it can blur the
# whole picture); the blur some effects blend in; the zoom blur of roars; heat shimmer
bloom = off
depth_of_field = off
motion_blur = on
radial_blur = on
haze = on

# multiplayer: wait a moment for a player with the port whose PC stalls, instead of the game
# dropping them (on, off; all = with every port player in the room)
net_sync = on
)";
}

std::string PrepareBackendFor(Core::System& system, const std::string& rom_path) {
    system.SetCPUFactory(nullptr);
    notice.clear();

    u64 program_id = 0;
    if (const auto loader = Loader::GetLoader(rom_path)) {
        if (loader->ReadProgramId(program_id) != Loader::ResultStatus::Success) {
            program_id = 0;
        }
    }

    std::string library;
    if (const char* env = std::getenv("MH4U_CODE"); env && *env) {
        library = env;
    } else if (program_id != 0) {
#if defined(_WIN32)
        constexpr const char* ext = "dll";
#elif defined(__APPLE__)
        constexpr const char* ext = "dylib";
#else
        constexpr const char* ext = "so";
#endif
        library = fmt::format("{}recomp/{:016X}.{}", FileUtil::GetUserPath(FileUtil::UserPath::UserDir),
                              program_id, ext);
        if (!FileUtil::Exists(library)) {
            if (program_id == MH4U_USA) {
                notice = fmt::format("The MH4U PC port's code library isn't installed ({}), so the game "
                                     "runs on Azahar's own CPU emulation, without the port's options "
                                     "(frame rate, widescreen, touch screen overlay, net sync).\n\n{}",
                                     library, BUILD_HINT);
            }
            return fmt::format("no recompiled code for {:016X}, using the emulated CPU", program_id);
        }
    } else {
        return "no recompiled code, using the emulated CPU";
    }

    std::string error;
#ifdef __ANDROID__
    library = StageForAndroid(library, error);
    if (!error.empty()) {
        return fmt::format("{}; using the emulated CPU", error);
    }
#endif
    if (!ARM_Recomp::LoadCode(library, error)) {
        if (program_id == MH4U_USA) {
            notice = fmt::format("The MH4U PC port's code library couldn't be used ({}), so the game "
                                 "runs on Azahar's own CPU emulation, without the port's options. A "
                                 "library made for an older version of this Azahar has to be built "
                                 "again.\n\n{}",
                                 error, BUILD_HINT);
        }
        return fmt::format("{}; using the emulated CPU", error);
    }
    const u64 code_title = ARM_Recomp::CodeTitleId();
    if (code_title != 0 && program_id != 0 && code_title != program_id) {
        return fmt::format("{} is for {:016X}, not {:016X}; using the emulated CPU", library,
                           code_title, program_id);
    }
    // The recompiled code only runs the code it came from; any other (the game without its
    // update, a code mod added since) would run entirely in the interpreter, far too slowly.
    // Azahar's own CPU emulation plays it at full speed instead.
    if (const auto code = program_id != 0 ? CodeToBeLoaded(rom_path, program_id) : std::vector<u8>{};
        !code.empty() && !ARM_Recomp::IsRecompiledCode(code)) {
        if (program_id == MH4U_USA) {
            notice = fmt::format(
                "The game's code isn't the code the MH4U PC port's library was built from, so the "
                "game runs on Azahar's own CPU emulation, without the port's options. Either the "
                "v1.3.0 update isn't installed in Azahar (File > Install CIA... with your update), or "
                "a code mod was added, changed or removed since the library was built.\n\n{}",
                BUILD_HINT);
        }
        return "the program's code isn't the code that was recompiled; using the emulated CPU";
    }
    if (const int missing = Hooks::MissingHookPoints(); missing > 0 && program_id == MH4U_USA) {
        notice = fmt::format("The MH4U PC port's code library is older than this Azahar: {} of the "
                             "port's hook points are missing, so some of its options won't work "
                             "until the library is built again.\n\n{}",
                             missing, BUILD_HINT);
    }
    system.SetCPUFactory([](Core::System& sys, Memory::MemorySystem& memory, u32 core_id,
                            std::shared_ptr<Core::Timing::Timer> timer)
                             -> std::shared_ptr<Core::ARM_Interface> {
        return std::make_shared<ARM_Recomp>(sys, memory, core_id, std::move(timer));
    });
    return fmt::format("running recompiled code from {} ({} functions)", library,
                       ARM_Recomp::FunctionCount());
}

} // namespace Port
