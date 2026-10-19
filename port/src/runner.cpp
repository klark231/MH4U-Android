// Headless runner: boots a 3DS executable with Azahar's core and no window, with either
// the recompiled code or one of Azahar's own CPU backends, until the program exits or an
// emulated-time limit is reached. Debug output (svcOutputDebugString) goes to stdout.
// For testing the real game it can also install a CIA (the update) first, press buttons
// and touch the screen on an emulated-time script, save both screens as images, and join
// a network room (Azahar's multiplayer) so that two runners can play locally together.
#include <algorithm>
#include <cctype>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <memory>
#include <sstream>
#include <string>
#include <thread>
#include <vector>
#include <fmt/format.h>

#include "arm_recomp.h"
#include "hooks.h"
#include "common/file_util.h"
#include "common/logging/backend.h"
#include "common/logging/filter.h"
#include "common/param_package.h"
#include "common/settings.h"
#include "core/core.h"
#include "core/core_timing.h"
#include "core/frontend/applets/default_applets.h"
#include "core/frontend/emu_window.h"
#include "core/frontend/framebuffer_layout.h"
#include "core/frontend/image_interface.h"
#include "core/frontend/input.h"
#include "core/hle/kernel/kernel.h"
#include "core/hle/kernel/process.h"
#include "core/hle/kernel/svc.h"
#include "core/hle/kernel/thread.h"
#include "core/hle/service/am/am.h"
#include "core/hle/service/cfg/cfg.h"
#include "core/hle/service/service.h"
#include "network/network.h"
#include "network/verify_user.h"
#include "video_core/gpu.h"
#include "video_core/renderer_software/renderer_software.h"

namespace {

constexpr double TICKS_PER_MS = 268111.856; // ARM11 clock

const char* const USAGE = R"(usage: mh4u_runner <file> [options]
  --code LIB        recompiled code library (with --cpu recomp)
  --cpu KIND        recomp (default), jit or interp
  --ms N            stop after N ms of emulated time (default 10000)
  --log FILTER      log filter (default "*:Warning Debug.Emulated:Debug Kernel:Info")
  --user DIR        Azahar user directory (NAND, SD card, saves) to use
  --install CIA     install a CIA, e.g. the game's update, before booting (repeatable)
  --shots DIR       save the top and bottom screens as PPM images in DIR
  --shot-every MS   how often, in emulated time (default 1000)
  --shots-from MS   first screenshot at this emulated time (default: one interval in)
  --draw-window MS  draw only during the MS of emulated time before each screenshot (and
                    nothing without --shots; the GPU's vertex shaders are skipped too): much
                    faster with the software renderer, but a screen built up over several
                    frames can come out incomplete
  --speed PERCENT   emulation speed limit (default 0 = as fast as possible; 100 = real time)
  --cpu-clock PERCENT  emulated CPU clock (default 100; Azahar's CPU Clock Speed setting)
  --async-io        let file reads finish on host threads and start the clock at the
                    host's time, as Azahar does by default; the runner otherwise does reads in
                    place and starts at a fixed date, so that a run repeats exactly (an
                    asynchronous read wakes the game at an emulated time that depends on how
                    fast the host was, which moves loading screens by seconds, and the game
                    seeds its random numbers from the clock)
  --dump MS:ADDR:SIZE  at emulated time MS, write SIZE bytes of guest memory from ADDR to
                    mem_<ms>_<addr>.bin in the --shots directory (or the current one);
                    repeatable, e.g. to diff the game's state with and without an input
  --replace MS:FROM:TO  at emulated time MS, replace every FROM in the game's writable
                    memory with TO (hex bytes, same length) and print how many it found;
                    repeatable, e.g. to give the second of two runners from copies of one
                    user folder another hunter
  --call-counts MS  at emulated time MS (or at the end, if the run ends first), write how
                    often each recompiled function has run so far to calls_<ms>.txt in the
                    --shots directory (repeatable; needs a code library built with
                    -D RC_CALL_COUNTS): diff two to see what ran in between
  --trace-svc FILE  write every system call (thread, number, pc, lr, r0-r3, ticks) to FILE,
                    to compare CPU backends thread by thread
  --input SCRIPT    comma-separated presses at emulated times, e.g.
                    "3000:A,3500:START:200,5000:TOUCH:160:120,6000:CIRCLE:0:1:500"
                    (ms:BUTTON[:hold ms], ms:TOUCH:x:y[:hold], ms:CIRCLE:x:y[:hold];
                    hold defaults to 100 ms; buttons A B X Y UP DOWN LEFT RIGHT L R
                    START SELECT ZL ZR HOME)
  --room-host PORT  host a network room (Azahar's multiplayer) on this UDP port and join it
  --room-join HOST:PORT  join a network room; the game's local wireless play then reaches
                    the other members (use --speed 100 on every runner so they keep time)
  --nick NAME       nickname in the room, 4 to 20 letters, digits, spaces, '.', '_' or '-'
                    (default "runner")
  --new-console-id  give the console a new random ID before the game starts (a room turns
                    away a second member with the same ID, e.g. two copies of a user folder)
  --lockstep FILE:COUNT:INDEX  keep the emulated clocks of COUNT runners on this machine
                    together (this one is INDEX, 0..COUNT-1; they share FILE): a runner waits
                    while it is more than a few ms ahead of the slowest. Without it, a runner
                    that stops to draw a screenshot (the clock stands still while the GPU
                    works) falls silent for the others, and the game's local play drops it
  --stalls MS       print each step of the emulation that took longer than MS of host time
  --freeze MS:HOST_MS  at emulated time MS, stop emulating for HOST_MS of host time, as a PC
                    that stalls would (shader compilation): the port's net sync makes the other
                    consoles in a network room wait instead of dropping this one
  --set MS:KEY=VALUE  at emulated time MS, change one of the port's options (mh4u.ini keys
                    the hooks read as the game runs: fps, refresh_rate, dither, kick, target,
                    quest_overlay, hud_edges, bloom, depth_of_field, ...), e.g. one player of a
                    session at 120 fps once everyone is in (repeatable)
or:    mh4u_runner --overlay-layout SPEC WIDTHxHEIGHT[:TOP_WIDTH] [QUEST SLOT [TARGET TSLOT]]
                    print the custom screen layout the port's touch_overlay option SPEC
                    gives a window that size (top screen TOP_WIDTH x 240, default 400);
                    with the quest_overlay option QUEST and the map's touch-panel slot,
                    the layout while a quest's panels are up, the part of the touch screen
                    shown, and where on the touch screen a click in the overlay's middle lands;
                    with the target_overlay option TARGET and the target camera's slot, the
                    same for the target camera's view
or:    mh4u_runner --panel-view quest_overlay|target_overlay VALUE
                    print the option's value as the port reads it and writes it back
or:    mh4u_runner --quest-overlay-frames FPS KIND:SECONDS[,KIND:SECONDS...]
                    run a made-up quest's frames through the choice of what the touch overlay
                    shows and print each change; KIND: map0..map5 (the small panels up, the map
                    in that slot), nomap, drawn (on the touch screen but not running: someone
                    talking), none (no panels: a change of area), menu (the quest menu or a big
                    panel), off (F12)
or:    mh4u_runner --install CIA [--install CIA...] [--user DIR]
                    install CIAs (the game's update, say) into an Azahar user folder and
                    exit, as Azahar's File > Install CIA... does
)";

class HeadlessWindow final : public Frontend::EmuWindow {
public:
    void PollEvents() override {}
};

int Usage() {
    std::fputs(USAGE, stderr);
    return 1;
}

s64 g_start_ticks = 0; // the emulated clock doesn't start at 0

/// Emulated time since the program was loaded.
double EmulatedMs() {
    return (Core::System::GetInstance().CoreTiming().GetGlobalTicks() - g_start_ticks) /
           TICKS_PER_MS;
}

// ── scripted input ──────────────────────────────────────────────────────────────────

struct Press {
    double start_ms, end_ms;
    int button;      // Settings::NativeButton, or TOUCH / CIRCLE
    float x, y;      // touch: pixels on the bottom screen; circle pad: -1..1
};
constexpr int TOUCH = -1, CIRCLE = -2;
std::vector<Press> g_presses;

struct Dump {
    double ms;
    u32 addr, size;
    bool done = false;
};
std::vector<Dump> g_dumps;

struct Replace {
    double ms;
    std::vector<u8> from, to;
    bool done = false;
};
std::vector<Replace> g_replaces;

bool ParseHexBytes(const std::string& hex, std::vector<u8>& out) {
    if (hex.empty() || hex.size() % 2) {
        return false;
    }
    out.clear();
    for (std::size_t i = 0; i < hex.size(); i += 2) {
        if (!std::isxdigit(static_cast<unsigned char>(hex[i])) ||
            !std::isxdigit(static_cast<unsigned char>(hex[i + 1]))) {
            return false;
        }
        out.push_back(static_cast<u8>(std::stoul(hex.substr(i, 2), nullptr, 16)));
    }
    return true;
}

bool ParseReplace(const std::string& arg) {
    const auto first = arg.find(':');
    const auto second = first == std::string::npos ? first : arg.find(':', first + 1);
    if (second == std::string::npos) {
        return false;
    }
    Replace r{};
    char* end = nullptr;
    r.ms = std::strtod(arg.c_str(), &end);
    if (end != arg.c_str() + first || !ParseHexBytes(arg.substr(first + 1, second - first - 1), r.from) ||
        !ParseHexBytes(arg.substr(second + 1), r.to) || r.from.size() != r.to.size()) {
        return false;
    }
    g_replaces.push_back(std::move(r));
    return true;
}

/// Replaces byte strings in every writable region of the game's address space whose time has come.
void DoReplaces(Core::System& system) {
    const double now = EmulatedMs();
    for (Replace& r : g_replaces) {
        if (r.done || now < r.ms) {
            continue;
        }
        r.done = true;
        const auto process = system.Kernel().GetCurrentProcess();
        if (!process) {
            continue;
        }
        auto& memory = system.Memory();
        unsigned found = 0;
        std::vector<u8> bytes;
        for (const auto& [base, vma] : process->vm_manager.vma_map) {
            if (vma.type != Kernel::VMAType::BackingMemory ||
                (static_cast<u32>(vma.permissions) & static_cast<u32>(Kernel::VMAPermission::Write)) == 0) {
                continue;
            }
            bytes.resize(vma.size);
            memory.ReadBlock(*process, vma.base, bytes.data(), vma.size);
            auto at = bytes.begin();
            while ((at = std::search(at, bytes.end(), r.from.begin(), r.from.end())) != bytes.end()) {
                memory.WriteBlock(*process, vma.base + static_cast<u32>(at - bytes.begin()), r.to.data(),
                                  r.to.size());
                ++found;
                at += static_cast<std::ptrdiff_t>(r.from.size());
            }
        }
        std::printf("replace at %.0f ms: %u found\n", now, found);
        std::fflush(stdout);
    }
}

// ── lockstep ────────────────────────────────────────────────────────────────────────

/// Runners in lockstep share a file with one slot per runner: its emulated time in ms (a
/// double; +infinity once it has finished). Each runner writes its own slot and waits while
/// it is ahead of the slowest of the others by more than kLockstepSlackMs.
struct Lockstep {
    std::FILE* file = nullptr;
    int count = 0, index = 0;
    double written = -1.0;  // last time written to our slot
    double others = 0.0;    // the slowest other runner, as last read
};
Lockstep g_lockstep;
constexpr double kLockstepSlackMs = 4.0;
constexpr double kLockstepGiveUpS = 120.0; // a runner that hasn't moved for this long has died

bool ParseLockstep(const std::string& arg) {
    const auto second = arg.rfind(':');
    const auto first = second == std::string::npos || second == 0 ? std::string::npos : arg.rfind(':', second - 1);
    if (first == std::string::npos || first == 0) {
        return false;
    }
    const int count = std::atoi(arg.c_str() + first + 1);
    const int index = std::atoi(arg.c_str() + second + 1);
    if (count < 2 || count > 16 || index < 0 || index >= count) {
        return false;
    }
    const std::string path = arg.substr(0, first);
    std::FILE* f = std::fopen(path.c_str(), "r+b");
    if (!f) {
        f = std::fopen(path.c_str(), "w+b");
    }
    if (!f) {
        return false;
    }
    std::setvbuf(f, nullptr, _IONBF, 0); // every read and write goes to the shared file
    std::fseek(f, 0, SEEK_END);
    for (long size = std::ftell(f); size < count * static_cast<long>(sizeof(double)); size += sizeof(double)) {
        const double zero = 0.0;
        std::fwrite(&zero, sizeof zero, 1, f);
    }
    g_lockstep = {f, count, index};
    return true;
}

void LockstepWrite(double ms) {
    std::fseek(g_lockstep.file, g_lockstep.index * static_cast<long>(sizeof(double)), SEEK_SET);
    std::fwrite(&ms, sizeof ms, 1, g_lockstep.file);
    g_lockstep.written = ms;
}

double LockstepReadOthers() {
    std::vector<double> slots(g_lockstep.count, 0.0);
    std::fseek(g_lockstep.file, 0, SEEK_SET);
    if (std::fread(slots.data(), sizeof(double), slots.size(), g_lockstep.file) != slots.size()) {
        return g_lockstep.others;
    }
    double slowest = std::numeric_limits<double>::infinity();
    for (int i = 0; i < g_lockstep.count; i++) {
        if (i != g_lockstep.index) {
            slowest = std::min(slowest, slots[i]);
        }
    }
    return slowest;
}

/// Publishes this runner's emulated time and waits until it is no more than a few ms ahead of
/// every other runner (or one of them has stopped moving for a long time).
void LockstepWait(double now) {
    if (!g_lockstep.file) {
        return;
    }
    if (now - g_lockstep.written >= 1.0) {
        LockstepWrite(now);
    }
    if (now <= g_lockstep.others + kLockstepSlackMs) {
        return;
    }
    auto moved = std::chrono::steady_clock::now();
    double last = g_lockstep.others;
    while ((g_lockstep.others = LockstepReadOthers()) + kLockstepSlackMs < now) {
        const auto host = std::chrono::steady_clock::now();
        if (g_lockstep.others != last) {
            last = g_lockstep.others;
            moved = host;
        } else if (std::chrono::duration<double>(host - moved).count() > kLockstepGiveUpS) {
            std::printf("lockstep: the other runners stopped at %.0f ms; going on alone\n", last);
            std::fflush(stdout);
            std::fclose(g_lockstep.file);
            g_lockstep.file = nullptr;
            return;
        }
        if (now - g_lockstep.written > 0.0) {
            LockstepWrite(now);
        }
        std::this_thread::sleep_for(std::chrono::microseconds(200));
    }
}

void LockstepFinish() {
    if (g_lockstep.file) {
        LockstepWrite(std::numeric_limits<double>::infinity());
        std::fclose(g_lockstep.file);
        g_lockstep.file = nullptr;
    }
}

bool ParseDump(const std::string& arg) {
    Dump d{};
    char extra = 0;
    if (std::sscanf(arg.c_str(), "%lf:%x:%x%c", &d.ms, &d.addr, &d.size, &extra) != 3 || d.size == 0) {
        return false;
    }
    g_dumps.push_back(d);
    return true;
}

/// --set MS:KEY=VALUE: one of the port's options changed at an emulated time.
struct OptionChange {
    double ms;
    std::string key, value;
    bool done = false;
};
std::vector<OptionChange> g_option_changes;

bool ParseOptionChange(const std::string& arg) {
    const auto colon = arg.find(':');
    const auto eq = arg.find('=', colon == std::string::npos ? 0 : colon);
    if (colon == std::string::npos || eq == std::string::npos || eq == colon + 1) {
        return false;
    }
    char* end = nullptr;
    const double ms = std::strtod(arg.c_str(), &end);
    if (end != arg.c_str() + colon || ms < 0.0) {
        return false;
    }
    g_option_changes.push_back({ms, arg.substr(colon + 1, eq - colon - 1), arg.substr(eq + 1)});
    return true;
}

/// Applies the option changes that are due; false if one names an option that can't change.
bool ApplyOptionChanges() {
    for (auto& c : g_option_changes) {
        if (c.done || EmulatedMs() < c.ms) {
            continue;
        }
        c.done = true;
        if (!Port::Hooks::SetOption(c.key, c.value)) {
            std::fprintf(stderr, "--set: %s is not an option that can change while the game runs\n",
                         c.key.c_str());
            return false;
        }
        std::printf("set %s = %s at %.0f ms\n", c.key.c_str(), c.value.c_str(), EmulatedMs());
        std::fflush(stdout);
    }
    return true;
}

/// Writes the dumps that are due; unmapped bytes come out as zero.
std::vector<std::pair<double, bool>> g_call_snapshots; // emulated ms, written

/// --call-counts: each function's calls so far, sorted by address (at the end of the run,
/// `all` writes the snapshots whose time hasn't come).
void WriteCallCounts(const std::string& dir, bool all = false) {
    for (auto& [ms, done] : g_call_snapshots) {
        if (done || (EmulatedMs() < ms && !all)) {
            continue;
        }
        done = true;
        const auto path = fmt::format("{}/calls_{:07.0f}.txt", dir.empty() ? "." : dir, ms);
        if (FILE* f = std::fopen(path.c_str(), "w")) {
            for (const auto& [addr, calls] : Port::ARM_Recomp::CallCounts()) {
                std::fprintf(f, "%08x %u\n", addr, calls);
            }
            std::fclose(f);
        }
    }
}

void WriteDumps(Core::System& system, const std::string& dir) {
    const double now = EmulatedMs();
    for (Dump& d : g_dumps) {
        if (d.done || now < d.ms) {
            continue;
        }
        d.done = true;
        std::vector<u8> bytes(d.size);
        auto& memory = system.Memory();
        auto process = system.Kernel().GetCurrentProcess();
        for (u32 off = 0; off < d.size;) {
            const VAddr a = d.addr + off;
            const u32 chunk = std::min<u32>(d.size - off, 0x1000 - (a & 0xfff)); // page by page
            if (process && memory.IsValidVirtualAddress(*process, a)) {
                memory.ReadBlock(*process, a, bytes.data() + off, chunk);
            }
            off += chunk;
        }
        const std::string path = fmt::format("{}/mem_{:07.0f}_{:08x}.bin", dir.empty() ? "." : dir, d.ms, d.addr);
        if (std::FILE* f = std::fopen(path.c_str(), "wb")) {
            std::fwrite(bytes.data(), 1, bytes.size(), f);
            std::fclose(f);
        }
    }
}

const Press* Active(int button) {
    const double now = EmulatedMs();
    for (const Press& p : g_presses) {
        if (p.button == button && now >= p.start_ms && now < p.end_ms) {
            return &p;
        }
    }
    return nullptr;
}

bool ParseScript(const std::string& script) {
    static const std::pair<const char*, int> names[] = {
        {"A", Settings::NativeButton::A},         {"B", Settings::NativeButton::B},
        {"X", Settings::NativeButton::X},         {"Y", Settings::NativeButton::Y},
        {"UP", Settings::NativeButton::Up},       {"DOWN", Settings::NativeButton::Down},
        {"LEFT", Settings::NativeButton::Left},   {"RIGHT", Settings::NativeButton::Right},
        {"L", Settings::NativeButton::L},         {"R", Settings::NativeButton::R},
        {"START", Settings::NativeButton::Start}, {"SELECT", Settings::NativeButton::Select},
        {"ZL", Settings::NativeButton::ZL},       {"ZR", Settings::NativeButton::ZR},
        {"HOME", Settings::NativeButton::Home},
    };
    std::stringstream items(script);
    std::string item;
    while (std::getline(items, item, ',')) {
        std::vector<std::string> f;
        std::stringstream fields(item);
        std::string field;
        while (std::getline(fields, field, ':')) {
            f.push_back(field);
        }
        if (f.size() < 2) {
            return false;
        }
        Press p{std::atof(f[0].c_str()), 0, 0, 0, 0};
        std::size_t hold_at = 2;
        if (f[1] == "TOUCH" || f[1] == "CIRCLE") {
            if (f.size() < 4) {
                return false;
            }
            p.button = f[1] == "TOUCH" ? TOUCH : CIRCLE;
            p.x = static_cast<float>(std::atof(f[2].c_str()));
            p.y = static_cast<float>(std::atof(f[3].c_str()));
            hold_at = 4;
        } else {
            const auto it = std::find_if(std::begin(names), std::end(names),
                                         [&](const auto& n) { return f[1] == n.first; });
            if (it == std::end(names)) {
                return false;
            }
            p.button = it->second;
        }
        p.end_ms = p.start_ms + (f.size() > hold_at ? std::atof(f[hold_at].c_str()) : 100.0);
        g_presses.push_back(p);
    }
    return true;
}

class ScriptButton final : public Input::ButtonDevice {
public:
    explicit ScriptButton(int button) : button(button) {}
    bool GetStatus() const override {
        return Active(button) != nullptr;
    }

private:
    int button;
};

class ScriptCirclePad final : public Input::AnalogDevice {
public:
    std::tuple<float, float> GetStatus() const override {
        const Press* p = Active(CIRCLE);
        return p ? std::make_tuple(p->x, p->y) : std::make_tuple(0.0f, 0.0f);
    }
};

class ScriptTouch final : public Input::TouchDevice {
public:
    std::tuple<float, float, bool> GetStatus() const override {
        const Press* p = Active(TOUCH);
        return p ? std::make_tuple(p->x / 320.0f, p->y / 240.0f, true)
                 : std::make_tuple(0.0f, 0.0f, false);
    }
};

template <typename Device, typename Make>
class ScriptFactory final : public Input::Factory<Device> {
public:
    explicit ScriptFactory(Make make) : make(make) {}
    std::unique_ptr<Device> Create(const Common::ParamPackage& params) override {
        return make(params);
    }

private:
    Make make;
};

template <typename Device, typename Make>
void Register(Make make) {
    Input::RegisterFactory<Device>("script", std::make_shared<ScriptFactory<Device, Make>>(make));
}

void UseScriptedInput() {
    Register<Input::ButtonDevice>([](const Common::ParamPackage& params) {
        return std::make_unique<ScriptButton>(params.Get("button", 0));
    });
    Register<Input::AnalogDevice>(
        [](const Common::ParamPackage&) { return std::make_unique<ScriptCirclePad>(); });
    Register<Input::TouchDevice>(
        [](const Common::ParamPackage&) { return std::make_unique<ScriptTouch>(); });
    auto& profile = Settings::values.current_input_profile;
    for (std::size_t i = 0; i < profile.buttons.size(); ++i) {
        profile.buttons[i] = "engine:script,button:" + std::to_string(i);
    }
    profile.analogs[Settings::NativeAnalog::CirclePad] = "engine:script";
    profile.touch_device = "engine:script";
    profile.use_touchpad = false;
    profile.use_touch_from_button = false;
}

// ── system call trace ───────────────────────────────────────────────────────────────

FILE* g_svc_trace = nullptr;

void TraceSvc(Core::System& system, u32 imm) {
    auto& cpu = system.GetRunningCore();
    const auto* thread = system.Kernel().GetCurrentThreadManager().GetCurrentThread();
    std::fprintf(g_svc_trace, "%u %02X pc=%08X lr=%08X %08X %08X %08X %08X t=%lld\n",
                 thread ? thread->GetThreadId() : 0u, imm, cpu.GetReg(15), cpu.GetReg(14),
                 cpu.GetReg(0), cpu.GetReg(1), cpu.GetReg(2), cpu.GetReg(3),
                 static_cast<long long>(system.CoreTiming().GetGlobalTicks() - g_start_ticks));
}

// ── screenshots ─────────────────────────────────────────────────────────────────────

/// Writes one screen of the software renderer as a binary PPM (landscape, as displayed).
bool SaveScreen(Core::System& system, VideoCore::ScreenId id, const std::string& path) {
    auto* renderer = dynamic_cast<SwRenderer::RendererSoftware*>(&system.GPU().Renderer());
    if (!renderer) {
        return false;
    }
    const auto& info = renderer->Screen(id);
    if (info.pixels.empty()) {
        return false;
    }
    // The renderer stores the screen rotated: info.height pixels per output row.
    const u32 width = info.height, height = info.width;
    std::vector<u8> rgb(static_cast<std::size_t>(width) * height * 3);
    for (std::size_t i = 0; i < static_cast<std::size_t>(width) * height; ++i) {
        std::memcpy(&rgb[i * 3], &info.pixels[i * 4], 3);
    }
    FILE* f = std::fopen(path.c_str(), "wb");
    if (!f) {
        return false;
    }
    std::fprintf(f, "P6\n%u %u\n255\n", width, height);
    std::fwrite(rgb.data(), 1, rgb.size(), f);
    std::fclose(f);
    return true;
}

// ── network room ────────────────────────────────────────────────────────────────────

/// A new random console ID, saved in the user folder's system settings.
void NewConsoleId(Core::System& system) {
    const auto cfg = Service::CFG::GetModule(system);
    const auto [random_number, console_id] = cfg->GenerateConsoleUniqueId();
    cfg->SetConsoleUniqueId(random_number, console_id);
    cfg->UpdateConfigNANDSavegame();
    std::printf("console id %016llx\n", static_cast<unsigned long long>(console_id));
}

/// Hosts a room on `host_port` (when it isn't 0) and joins it, or joins the room at `join`
/// (HOST:PORT), the way Azahar's Multiplayer menu does. Waits until the room has accepted
/// or refused this member.
bool JoinRoom(Core::System& system, u16 host_port, const std::string& join,
              const std::string& nickname) {
    if (!Network::Init()) {
        std::fprintf(stderr, "room: the network library didn't start\n");
        return false;
    }
    std::string host = "127.0.0.1";
    u16 port = host_port;
    if (host_port) {
        const auto room = Network::GetRoom().lock();
        if (!room || !room->Create("mh4u_runner", "", "", host_port, "", 16, "", "", 0,
                                   std::make_unique<Network::VerifyUser::NullBackend>())) {
            std::fprintf(stderr, "room: could not host on port %u\n", host_port);
            return false;
        }
        std::printf("room: hosting on port %u\n", host_port);
        std::fflush(stdout);
    } else {
        const auto colon = join.rfind(':');
        host = join.substr(0, colon);
        port = static_cast<u16>(std::atoi(join.c_str() + colon + 1));
    }
    const auto member = Network::GetRoomMember().lock();
    if (!member) {
        return false;
    }
    member->BindOnError([](const Network::RoomMember::Error& error) {
        std::printf("room: %s\n", Network::GetErrorStr(error));
        std::fflush(stdout);
    });
    member->Join(nickname, Service::CFG::GetConsoleIdHash(system), host.c_str(), port);
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);
    while (member->GetState() == Network::RoomMember::State::Joining &&
           std::chrono::steady_clock::now() < deadline) {
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    if (!member->IsConnected()) {
        std::fprintf(stderr, "room: could not join %s:%u (%s)\n", host.c_str(), port,
                     Network::GetStateStr(member->GetState()));
        return false;
    }
    std::printf("room: joined %s:%u as %s, MAC %s\n", host.c_str(), port, nickname.c_str(),
                Service::CFG::MacToString(member->GetMacAddress()).c_str());
    std::fflush(stdout);
    return true;
}

/// The room's members as this runner last heard of them, sorted, for the log.
void PrintRoomMembers() {
    const auto member = Network::GetRoomMember().lock();
    if (!member || !member->IsConnected()) {
        return;
    }
    std::vector<std::string> names;
    for (const auto& info : member->GetMemberInformation()) {
        names.push_back(info.nickname);
    }
    std::sort(names.begin(), names.end());
    std::string list;
    for (const auto& name : names) {
        list += (list.empty() ? "" : ", ") + name;
    }
    std::printf("room: members: %s\n", list.c_str());
}

} // namespace

/// --overlay-layout: the screens' rectangles for a touch_overlay option and window size, and
/// with a quest_overlay option and the map's panel slot, what a quest shows: the part of the
/// touch screen in the corner and where a click in its middle touches.
int PrintOverlayLayout(const std::string& spec, const std::string& size, const std::string& quest,
                       const std::string& slot, const std::string& target, const std::string& target_slot) {
    unsigned width = 0, height = 0, top_width = Core::kScreenTopWidth;
    if (std::sscanf(size.c_str(), "%ux%u:%u", &width, &height, &top_width) < 2 || !width ||
        !height || !top_width) {
        return Usage();
    }
    Layout::SetTopScreenWidth(top_width);
    auto overlay = Port::Hooks::ParseOverlay(spec);
    if (!quest.empty()) {
        const auto map = Port::Hooks::ParseQuestOverlay(quest);
        if (map.on) {
            overlay = Port::Hooks::QuestOverlay(overlay, std::atoi(slot.c_str()), map,
                                                target.empty() ? -1 : std::atoi(target_slot.c_str()),
                                                Port::Hooks::ParseTargetOverlay(target));
        }
    }
    Layout::SetScreenOverlay(overlay);
    const auto layout = Layout::CustomFrameLayout(width, height, false, false);
    const auto& t = layout.top_screen;
    const auto& b = layout.bottom_screen;
    std::printf("top %u,%u %u,%u opacity %.2f\nbottom %u,%u %u,%u opacity %.2f\n", t.left, t.top,
                t.right, t.bottom, layout.top_opacity, b.left, b.top, b.right, b.bottom,
                layout.bottom_opacity);
    if (quest.empty()) {
        return 0;
    }
    const auto& crop = layout.bottom_crop;
    std::printf("shows %.0f,%.0f %.0f,%.0f of the touch screen\n", crop.left * Core::kScreenBottomWidth,
                crop.top * Core::kScreenBottomHeight, crop.right * Core::kScreenBottomWidth,
                crop.bottom * Core::kScreenBottomHeight);
    // a click through Azahar's own touch handling, in a window with the custom layout
    Settings::values.layout_option.SetValue(Settings::LayoutOption::CustomLayout);
    HeadlessWindow window;
    window.UpdateCurrentFramebufferLayout(width, height);
    const auto& shown = window.GetFramebufferLayout().bottom_screen;
    const unsigned x = (shown.left + shown.right) / 2, y = (shown.top + shown.bottom) / 2;
    window.TouchPressed(x, y);
    const auto touch = Input::CreateDevice<Input::TouchDevice>(
        Common::ParamPackage{{"engine", "emu_window"}}.Serialize());
    const auto [touch_x, touch_y, pressed] = touch->GetStatus();
    std::printf("a click at %u,%u touches %.0f,%.0f%s\n", x, y, touch_x * Core::kScreenBottomWidth,
                touch_y * Core::kScreenBottomHeight, pressed ? "" : " (not pressed)");
    window.TouchReleased();
    // the further views (the target camera): where, which part, and a click on each, then
    // dragged to the window's bottom-left corner (the touch stays on the view's part)
    const auto& framebuffer = window.GetFramebufferLayout();
    for (u32 i = 0; i < framebuffer.bottom_view_count; ++i) {
        const auto& [r, part] = framebuffer.bottom_views[i];
        std::printf("view %u,%u %u,%u shows %.0f,%.0f %.0f,%.0f of the touch screen\n", r.left, r.top,
                    r.right, r.bottom, part.left * Core::kScreenBottomWidth, part.top * Core::kScreenBottomHeight,
                    part.right * Core::kScreenBottomWidth, part.bottom * Core::kScreenBottomHeight);
        const unsigned vx = (r.left + r.right) / 2, vy = (r.top + r.bottom) / 2;
        window.TouchPressed(vx, vy);
        const auto [view_x, view_y, view_pressed] = touch->GetStatus();
        std::printf("a click at %u,%u touches %.0f,%.0f%s\n", vx, vy, view_x * Core::kScreenBottomWidth,
                    view_y * Core::kScreenBottomHeight, view_pressed ? "" : " (not pressed)");
        window.TouchMoved(0, height - 1);
        const auto [drag_x, drag_y, drag_pressed] = touch->GetStatus();
        std::printf("dragged to 0,%u it touches %.0f,%.0f%s\n", height - 1, drag_x * Core::kScreenBottomWidth,
                    drag_y * Core::kScreenBottomHeight, drag_pressed ? "" : " (not pressed)");
        window.TouchReleased();
    }
    return 0;
}

/// --quest-overlay-frames: runs the frames of a made-up quest at FPS frames a second through
/// the choice of what the touch overlay shows (Port::Hooks::QuestOverlayChoice) and prints each
/// change. SPANS: KIND:SECONDS,... with KIND map0..map5 (the small panels up, the map in that
/// slot), nomap (up, with no map), drawn (not running but on the touch screen: someone
/// talking), none (not there: a change of area), menu (the quest menu or a big panel) or off
/// (F12: the whole touch screen, the panels up).
int PrintQuestOverlayFrames(const std::string& fps_text, const std::string& spans) {
    using Choice = Port::Hooks::QuestOverlayChoice;
    const double fps = std::atof(fps_text.c_str());
    if (fps <= 0) {
        return Usage();
    }
    Choice choice;
    long long frame = 0;
    double span_end = 0;
    std::stringstream list(spans);
    std::string span;
    while (std::getline(list, span, ',')) {
        const auto colon = span.find(':');
        if (colon == std::string::npos) {
            return Usage();
        }
        const std::string kind = span.substr(0, colon);
        bool panels = false, map_drawn = false, touch_ui = false;
        int slot = -1, forced = Choice::FREE;
        if (kind.size() == 4 && kind.compare(0, 3, "map") == 0 && kind[3] >= '0' && kind[3] <= '5') {
            panels = true;
            slot = kind[3] - '0';
        } else if (kind == "nomap") {
            panels = true;
        } else if (kind == "drawn") {
            map_drawn = true;
        } else if (kind == "menu") {
            touch_ui = true;
        } else if (kind == "off") {
            panels = true;
            slot = 0;
            forced = -1;
        } else if (kind != "none") {
            return Usage();
        }
        span_end += std::atof(span.c_str() + colon + 1);
        for (; static_cast<double>(frame) / fps < span_end - 1e-9; ++frame) {
            const double time = static_cast<double>(frame) / fps;
            if (choice.Update(time, panels, slot, map_drawn, touch_ui, forced)) {
                if (choice.Shown() < 0) {
                    std::printf("%.3f s: the whole touch screen\n", time);
                } else {
                    std::printf("%.3f s: the map in slot %d\n", time, choice.Shown());
                }
            }
        }
    }
    return 0;
}

int main(int argc, char** argv) {
    if (argc == 4 && std::string(argv[1]) == "--panel-view") {
        // a quest_overlay or target_overlay value as the hooks read it and the menu writes it
        const std::string key = argv[2];
        if (key != "quest_overlay" && key != "target_overlay") {
            return Usage();
        }
        const bool map = key == "quest_overlay";
        const auto view = map ? Port::Hooks::ParseQuestOverlay(argv[3]) : Port::Hooks::ParseTargetOverlay(argv[3]);
        std::printf("%s\n", (map ? Port::Hooks::FormatQuestOverlay(view) : Port::Hooks::FormatTargetOverlay(view)).c_str());
        return 0;
    }
    if ((argc == 4 || argc == 6 || argc == 8) && std::string(argv[1]) == "--overlay-layout") {
        return PrintOverlayLayout(argv[2], argv[3], argc >= 6 ? argv[4] : "", argc >= 6 ? argv[5] : "",
                                  argc == 8 ? argv[6] : "", argc == 8 ? argv[7] : "");
    }
    if (argc == 4 && std::string(argv[1]) == "--quest-overlay-frames") {
        return PrintQuestOverlayFrames(argv[2], argv[3]);
    }
    std::string path;
    std::string code_library;
    std::string cpu = "recomp";
    std::string log_filter = "*:Warning Debug.Emulated:Debug Kernel:Info";
    double emulated_ms = 10000.0;
    std::string user_dir, shots_dir, input_script, svc_trace;
    std::vector<std::string> install;
    double shot_every = 1000.0;
    double shots_from = -1.0;
    double speed = 0.0;
    double draw_window = -1.0; // < 0: draw every frame
    double stall_ms = 0.0;     // > 0: report emulation steps that take longer
    double freeze_at = -1.0, freeze_ms = 0.0;  // --freeze: stop emulating once, for a while
    int cpu_clock = 100;
    bool async_io = false;
    int room_host = 0;
    std::string room_join;
    std::string nickname = "runner";
    bool new_console_id = false;
    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        if (arg == "--code" && i + 1 < argc) {
            code_library = argv[++i];
        } else if (arg == "--cpu" && i + 1 < argc) {
            cpu = argv[++i];
        } else if (arg == "--ms" && i + 1 < argc) {
            emulated_ms = std::atof(argv[++i]);
        } else if (arg == "--log" && i + 1 < argc) {
            log_filter = argv[++i];
        } else if (arg == "--user" && i + 1 < argc) {
            user_dir = argv[++i];
        } else if (arg == "--install" && i + 1 < argc) {
            install.push_back(argv[++i]);
        } else if (arg == "--shots" && i + 1 < argc) {
            shots_dir = argv[++i];
        } else if (arg == "--lockstep" && i + 1 < argc) {
            if (!ParseLockstep(argv[++i])) {
                std::fprintf(stderr, "bad --lockstep: %s (want FILE:COUNT:INDEX, 2 <= COUNT <= 16)\n", argv[i]);
                return 1;
            }
        } else if (arg == "--stalls" && i + 1 < argc) {
            stall_ms = std::max(0.0, std::atof(argv[++i]));
        } else if (arg == "--freeze" && i + 1 < argc) {
            if (std::sscanf(argv[++i], "%lf:%lf", &freeze_at, &freeze_ms) != 2 || freeze_ms <= 0.0) {
                std::fprintf(stderr, "bad --freeze %s (MS:HOST_MS)\n", argv[i]);
                return 1;
            }
        } else if (arg == "--set" && i + 1 < argc) {
            if (!ParseOptionChange(argv[++i])) {
                std::fprintf(stderr, "bad --set %s (MS:KEY=VALUE)\n", argv[i]);
                return 1;
            }
        } else if (arg == "--replace" && i + 1 < argc) {
            if (!ParseReplace(argv[++i])) {
                std::fprintf(stderr, "bad --replace: %s (want MS:FROM:TO, hex bytes of equal length)\n", argv[i]);
                return 1;
            }
        } else if (arg == "--dump" && i + 1 < argc) {
            if (!ParseDump(argv[++i])) {
                std::fprintf(stderr, "bad --dump: %s (want MS:ADDR:SIZE, hex address and size)\n", argv[i]);
                return 1;
            }
        } else if (arg == "--call-counts" && i + 1 < argc) {
            g_call_snapshots.emplace_back(std::atof(argv[++i]), false);
        } else if (arg == "--trace-svc" && i + 1 < argc) {
            svc_trace = argv[++i];
        } else if (arg == "--speed" && i + 1 < argc) {
            speed = std::max(0.0, std::atof(argv[++i]));
        } else if (arg == "--shots-from" && i + 1 < argc) {
            shots_from = std::max(0.0, std::atof(argv[++i]));
        } else if (arg == "--shot-every" && i + 1 < argc) {
            shot_every = std::max(1.0, std::atof(argv[++i]));
        } else if (arg == "--cpu-clock" && i + 1 < argc) {
            cpu_clock = std::clamp(std::atoi(argv[++i]), 5, 400);
        } else if (arg == "--async-io") {
            async_io = true;
        } else if (arg == "--draw-window" && i + 1 < argc) {
            draw_window = std::max(0.0, std::atof(argv[++i]));
        } else if (arg == "--room-host" && i + 1 < argc) {
            room_host = std::atoi(argv[++i]);
            if (room_host <= 0 || room_host > 65535) {
                return Usage();
            }
        } else if (arg == "--room-join" && i + 1 < argc) {
            room_join = argv[++i];
            const auto colon = room_join.rfind(':');
            if (colon == std::string::npos || colon == 0 || std::atoi(room_join.c_str() + colon + 1) <= 0) {
                return Usage();
            }
        } else if (arg == "--nick" && i + 1 < argc) {
            nickname = argv[++i];
            // what rooms accept; anything else comes back as a name collision
            if (nickname.size() < 4 || nickname.size() > 20 ||
                !std::all_of(nickname.begin(), nickname.end(), [](char c) {
                    return std::isalnum(static_cast<unsigned char>(c)) || std::strchr(" ._-", c);
                })) {
                std::fprintf(stderr, "bad --nick: %s\n", nickname.c_str());
                return 1;
            }
        } else if (arg == "--new-console-id") {
            new_console_id = true;
        } else if (arg == "--input" && i + 1 < argc) {
            input_script = argv[++i];
            if (!ParseScript(input_script)) {
                std::fprintf(stderr, "bad --input script: %s\n", input_script.c_str());
                return 1;
            }
        } else if (!arg.empty() && arg[0] != '-' && path.empty()) {
            path = arg;
        } else {
            return Usage();
        }
    }
    const bool install_only = path.empty() && !install.empty();
    if ((path.empty() && !install_only) || (cpu != "recomp" && cpu != "jit" && cpu != "interp") ||
        (room_host && !room_join.empty())) {
        return Usage();
    }

    if (!user_dir.empty()) {
        // before anything asks for a path: the other directories are derived from it once
        FileUtil::SetUserPath(user_dir.back() == '/' ? user_dir : user_dir + '/');
    }
    Common::Log::Initialize("mh4u_runner_log.txt"); // not over Azahar's own log in a shared folder
    Common::Log::SetColorConsoleBackendEnabled(true);
    Common::Log::Start();
    Common::Log::Filter filter;
    filter.ParseFilterString(log_filter);
    Common::Log::SetGlobalFilter(filter);

    for (const auto& cia : install) {
        const auto result = Service::AM::InstallCIA(cia);
        if (result != Service::AM::InstallStatus::Success) {
            std::fprintf(stderr, "installing %s failed (status %u)\n", cia.c_str(),
                         static_cast<unsigned>(result));
            return 1;
        }
        std::printf("installed %s\n", cia.c_str());
    }
    if (install_only) {
        Common::Log::Stop();
        return 0;
    }
    UseScriptedInput();
    Settings::values.graphics_api.SetValue(Settings::GraphicsAPI::Software);
    Settings::values.frame_limit.SetValue(speed); // 0: don't wait for real time
    Settings::values.cpu_clock_percentage.SetValue(cpu_clock);
    // (the port's Azahar patch also keeps host time out of file delays in this mode)
    Settings::values.deterministic_async_operations.SetValue(!async_io);
    if (!async_io) {
        // the game seeds its random numbers from the clock: a fixed start date makes two runs
        // the same down to the monster's moves (2026-01-01 12:00 UTC)
        Settings::values.init_clock.SetValue(Settings::InitClock::FixedTime);
        Settings::values.init_time.SetValue(1767268800ULL);
    }
    // no sound card needed (and on Windows, cubeb's WASAPI backend asserts on a thread
    // without COM, which a runner without Qt never initialises)
    Settings::values.output_type.SetValue(AudioCore::SinkType::Null);
    Settings::values.input_type.SetValue(AudioCore::InputType::Null);
    Settings::values.use_cpu_jit = cpu == "jit";
    Settings::values.use_fastinterp = false;

    for (const auto& service_module : Service::service_module_map) {
        Settings::values.lle_modules.emplace(service_module.name, false); // all services HLE
    }

    Core::System& system = Core::System::GetInstance();
    Frontend::RegisterDefaultApplets(system);
    system.RegisterImageInterface(std::make_shared<Frontend::ImageInterface>());
    std::vector<Port::ARM_Recomp*> recomp_cores;
    if (cpu == "recomp") {
        std::string error;
        if (code_library.empty() || !Port::ARM_Recomp::LoadCode(code_library, error)) {
            std::fprintf(stderr, "%s\n", code_library.empty() ? "--cpu recomp needs --code LIB" : error.c_str());
            return 1;
        }
        if (!g_call_snapshots.empty() && !Port::ARM_Recomp::HasCallCounts()) {
            std::fprintf(stderr, "--call-counts needs a code library built with -D RC_CALL_COUNTS\n");
            return 1;
        }
        system.SetCPUFactory([&recomp_cores](Core::System& sys, Memory::MemorySystem& memory,
                                             u32 core_id, std::shared_ptr<Core::Timing::Timer> timer) {
            auto core = std::make_shared<Port::ARM_Recomp>(sys, memory, core_id, std::move(timer));
            recomp_cores.push_back(core.get());
            return std::shared_ptr<Core::ARM_Interface>(core);
        });
    }

    // Before loading, as Azahar's Multiplayer menu does: the system settings are read when the
    // services start, and the local wireless service only talks to a room that exists when
    // it starts (it also takes the room's MAC address for the console's).
    if (new_console_id) {
        NewConsoleId(system);
    }
    const bool in_room = room_host || !room_join.empty();
    if (in_room && !JoinRoom(system, static_cast<u16>(room_host), room_join, nickname)) {
        Network::Shutdown();
        Common::Log::Stop();
        return 5;
    }

    HeadlessWindow window;
    const auto status = system.Load(window, path);
    if (status != Core::System::ResultStatus::Success) {
        std::fprintf(stderr, "failed to load %s (status %u)\n", path.c_str(), static_cast<unsigned>(status));
        return 2;
    }
    std::weak_ptr<Kernel::Process> process = system.Kernel().GetCurrentProcess();
    // ARM11 clock: 268,111,856 Hz
    const s64 tick_limit = static_cast<s64>(emulated_ms * 268111.856);
    const s64 start_ticks = system.CoreTiming().GetGlobalTicks();
    g_start_ticks = start_ticks;
    if (!svc_trace.empty()) {
        g_svc_trace = std::fopen(svc_trace.c_str(), "w");
        if (g_svc_trace) {
            Kernel::SetSVCObserver(TraceSvc);
        }
    }

    if (!shots_dir.empty()) {
        FileUtil::CreateFullPath(shots_dir.back() == '/' ? shots_dir : shots_dir + '/');
    }
    double next_shot = shots_from >= 0.0 ? shots_from : shot_every;
    int rc = 0;
    while (true) {
        if (draw_window >= 0.0) {
            SwRenderer::SetDrawingEnabled(!shots_dir.empty() &&
                                          EmulatedMs() >= next_shot - draw_window);
        }
        LockstepWait(EmulatedMs());
        if (!ApplyOptionChanges()) {
            rc = 1;
            break;
        }
        if (freeze_at >= 0.0 && EmulatedMs() >= freeze_at) {
            std::printf("freeze at %.0f ms for %.0f ms of host time\n", EmulatedMs(), freeze_ms);
            std::fflush(stdout);
            std::this_thread::sleep_for(std::chrono::duration<double, std::milli>(freeze_ms));
            freeze_at = -1.0;
        }
        const auto step_start = std::chrono::steady_clock::now();
        const double step_emulated = EmulatedMs();
        const auto result = system.RunLoop();
        if (stall_ms > 0.0) {
            const double host =
                std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - step_start).count();
            if (host > stall_ms) {
                std::printf("stall: %.0f ms of host time at %.1f ms, the emulated clock moved %.1f ms\n", host,
                            step_emulated, EmulatedMs() - step_emulated);
                std::fflush(stdout);
            }
        }
        WriteDumps(system, shots_dir);
        DoReplaces(system);
        WriteCallCounts(shots_dir);
        if (!shots_dir.empty() && EmulatedMs() >= next_shot) {
            const auto stem = fmt::format("{}/{:07.0f}", shots_dir, next_shot);
            SaveScreen(system, VideoCore::ScreenId::TopLeft, stem + "_top.ppm");
            SaveScreen(system, VideoCore::ScreenId::Bottom, stem + "_bottom.ppm");
            next_shot += shot_every;
        }
        if (result == Core::System::ResultStatus::ShutdownRequested) {
            break;
        }
        if (result != Core::System::ResultStatus::Success) {
            std::fprintf(stderr, "emulation error (status %u)\n", static_cast<unsigned>(result));
            rc = 3;
            break;
        }
        bool failed = false;
        for (const auto* core : recomp_cores) {
            if (core->HasFailed()) {
                std::fprintf(stderr, "recompiled code stopped: %s\n", core->Failure().c_str());
                failed = true;
            }
        }
        if (failed) {
            rc = 4;
            break;
        }
        const auto p = process.lock();
        if (!p || p->status == Kernel::ProcessStatus::Exited) {
            std::printf("process exited\n");
            break;
        }
        if (system.CoreTiming().GetGlobalTicks() - start_ticks > tick_limit) {
            std::printf("time limit reached\n");
            break;
        }
    }
    LockstepFinish();
    WriteCallCounts(shots_dir, true);
    std::printf("emulated time: %.1f ms\n",
                (system.CoreTiming().GetGlobalTicks() - start_ticks) / 268111.856);
    PrintRoomMembers();
    std::fflush(stdout);
    if (g_svc_trace) {
        Kernel::SetSVCObserver(nullptr);
        std::fclose(g_svc_trace);
    }
    system.Shutdown();
    if (in_room) {
        Network::Shutdown();
    }
    Common::Log::Stop();
    return rc;
}
