#include "arm_recomp.h"

#include <algorithm>
#include <array>
#include <cstdlib>
#include <vector>
#include <fmt/format.h>

#include "common/assert.h"
#include "common/logging/log.h"
#include "core/arm/dyncom/arm_dyncom.h"
#include "core/arm/skyeye_common/armstate.h"
#include "core/core.h"
#include "core/core_timing.h"
#include "core/memory.h"
#include "fiber.h"
#include "hooks.h"

#include "common/dynamic_library.h"

// The C runtime contract; included last because it defines short macros (R, C_EQ, ...).
extern "C" {
#include "recomp.h"
void rc_slow_write(RCtx* ctx, u32 a, u32 v, u32 size);
u32 rc_slow_read(RCtx* ctx, u32 a, u32 size);
}

namespace Port {

namespace {

constexpr u32 SVC_EXIT_PROCESS = 0x03;
constexpr u32 SVC_EXIT_THREAD = 0x09;
constexpr u32 CPSR_T = 1u << 5;
// Bits that recomp.h keeps outside of the separate flag words (mode, masks, E, ...).
constexpr u32 CPSR_MISC_MASK = 0x07F0FFFFu & ~CPSR_T;

/// The recompiled program: its function table and the code it was made from.
RCodeInfo g_code{};
/// Recompiled functions by entry address; Thumb entries have bit 0 set.
std::unordered_map<u32, RFunc> g_functions;
/// Keeps a loaded code library mapped.
std::unique_ptr<Common::DynamicLibrary> g_library;
/// rc_call_counts() of a diagnostic code library (-DRC_CALL_COUNTS), or null.
using RCallCounts = u32 (*)(u32* out, u32 max);
RCallCounts g_call_counts = nullptr;

const std::unordered_map<u32, RFunc>& Functions() {
    return g_functions;
}

ARM_Recomp& Self(RCtx* ctx) {
    return *static_cast<ARM_Recomp*>(ctx->user);
}

u32 Crc32(const u8* p, std::size_t n) {
    static const auto table = [] {
        std::array<u32, 256> t{};
        for (u32 i = 0; i < 256; ++i) {
            u32 c = i;
            for (int k = 0; k < 8; ++k) {
                c = (c >> 1) ^ (0xEDB88320u & (0u - (c & 1)));
            }
            t[i] = c;
        }
        return t;
    }();
    u32 crc = 0xFFFFFFFFu;
    for (std::size_t i = 0; i < n; ++i) {
        crc = table[(crc ^ p[i]) & 0xFF] ^ (crc >> 8);
    }
    return ~crc;
}

} // namespace

struct ARM_Recomp::GuestThread {
    std::unique_ptr<Fiber> fiber;
    bool finished = false;
};

ARM_Recomp::ARM_Recomp(Core::System& system_, Memory::MemorySystem& memory_, u32 core_id,
                       std::shared_ptr<Core::Timing::Timer> timer_)
    : ARM_Interface(core_id, std::move(timer_)), system(system_), memory(memory_),
      svc_context(system_), ctx(std::make_unique<RCtx>()) {
    *ctx = RCtx{};
    ctx->user = this;
    ctx->cpsr_misc = 0x10; // user mode
    SetPageTable(memory.GetCurrentPageTable());
}

ARM_Recomp::~ARM_Recomp() = default;

std::size_t ARM_Recomp::FunctionCount() {
    return g_code.count;
}

u64 ARM_Recomp::CodeTitleId() {
    return g_code.title_id;
}

bool ARM_Recomp::IsRecompiledCode(const std::vector<u8>& code) {
    return code.size() >= g_code.text_size && Crc32(code.data(), g_code.text_size) == g_code.text_crc32;
}

void ARM_Recomp::SetCode(const RCodeInfo& info) {
    g_code = info;
    g_functions.clear();
    g_functions.reserve(info.count);
    for (u32 i = 0; i < info.count; ++i) {
        g_functions.emplace(info.table[i].addr | info.table[i].mode, info.table[i].fn);
    }
}

bool ARM_Recomp::HasCallCounts() {
    return g_call_counts != nullptr;
}

std::vector<std::pair<u32, u32>> ARM_Recomp::CallCounts() {
    std::vector<std::pair<u32, u32>> counts;
    if (!g_call_counts) {
        return counts;
    }
    std::vector<u32> pairs;
    u32 n = g_call_counts(nullptr, 0);
    do { // functions may run for the first time between the two calls: size again
        pairs.resize(2 * std::size_t{n} + 64);
        n = g_call_counts(pairs.data(), static_cast<u32>(pairs.size() / 2));
    } while (2 * std::size_t{n} > pairs.size());
    counts.reserve(n);
    for (u32 i = 0; i < n; ++i) {
        counts.emplace_back(pairs[2 * i], pairs[2 * i + 1]);
    }
    std::sort(counts.begin(), counts.end());
    return counts;
}

bool ARM_Recomp::LoadCode(const std::string& path, std::string& error) {
    auto library = std::make_unique<Common::DynamicLibrary>();
    if (!library->Load(path)) {
        error = fmt::format("can't load {}: {}", path, library->GetLoadError());
        return false;
    }
    const auto init = library->GetSymbol<RCodeInit>(RC_CODE_INIT_SYMBOL);
    if (!init) {
        error = fmt::format("{} is not a recompiled code library", path);
        return false;
    }
    static const RHost host{RC_HOST_VERSION, sizeof(RCtx), rc_call, rc_svc, rc_trap,
                            rc_bad_return, rc_budget, rc_slow_read, rc_slow_write};
    RCodeInfo info{};
    if (!init(&host, &info)) {
        error = fmt::format("{} was built against a different runtime (recomp.h)", path);
        return false;
    }
    g_call_counts = library->GetSymbol<RCallCounts>("rc_call_counts");
    g_library = std::move(library);
    SetCode(info);
    for (const auto& line : Hooks::Install(info)) {
        LOG_INFO(Core_ARM11, "hooks: {}", line);
    }
    return true;
}

// ── execution ───────────────────────────────────────────────────────────────────────

void ARM_Recomp::Run() {
    Execute(GetTimer().GetDowncount());
}

void ARM_Recomp::Step() {
    Execute(1); // runs to the end of the current basic block
}

void ARM_Recomp::Execute(s64 budget) {
    if (break_flag || HasFailed() || !loaded) {
        return;
    }
    if (!host) {
        host = Fiber::FromCurrentThread(); // the emulation thread, created lazily
        // The recompiled code is only valid for exactly the code it came from.
        std::vector<u8> text(g_code.text_size);
        memory.ReadBlock(g_code.text_base, text.data(), text.size());
        const u32 crc = Crc32(text.data(), text.size());
        if (crc != g_code.text_crc32) {
            // Wrong version (e.g. the update isn't installed): keep going, slowly, with
            // everything in the interpreter.
            LOG_CRITICAL(Core_ARM11,
                         "the running program's code ({:08X}+{:X}, crc32 {:08X}) is not the code "
                         "that was recompiled (crc32 {:08X}): interpreting everything",
                         g_code.text_base, g_code.text_size, crc, g_code.text_crc32);
            g_functions.clear();
        }
    }
    auto& slot = threads[loaded];
    if (!slot) {
        // first time this thread runs
        slot = std::make_unique<GuestThread>();
        slot->fiber = Fiber::Create([this] { GuestMain(); });
    }
    // Other cores may have written memory since this core last ran: a reservation taken
    // before then must not let a STREX succeed.
    ctx->excl_valid = 0;
    reschedule = false;
    slice_start = budget;
    ctx->budget = budget;
    running = slot.get();
    running_context = loaded;
    host->SwitchTo(*running->fiber);
#ifdef __ANDROID__
    // System calls run here, on the emulation thread's own stack (Svc() hands them over).
    while (svc_pending) {
        svc_pending = false;
        const u32 imm = svc_imm;
        svc_context.CallSVC(imm);
        if (imm == SVC_EXIT_THREAD || imm == SVC_EXIT_PROCESS) {
            running->finished = true;
            break;
        }
        if (reschedule || loaded != running_context) {
            break; // the kernel wants another thread: this one resumes when it is loaded again
        }
        host->SwitchTo(*running->fiber);
    }
#endif
    if (running->finished) {
        // The thread exited; its fiber is parked for good and nothing on its stack needs
        // unwinding, so the stack can go.
        threads.erase(running_context);
    }
    running = nullptr;
    running_context = nullptr;
    SyncTicks();
}

void ARM_Recomp::GuestMain() {
    const u32 entry = ctx->r[15] | (thumb ? 1u : 0u);
    // Guest code runs with the host FPU rounding like FPSCR.RMode says (each fiber keeps
    // its own FPU control state across switches).
    rc_apply_rounding(ctx->fpscr);
    Call(entry);
    Fail(fmt::format("thread entry {:08X} returned to {:08X}", entry, ctx->r[15]));
}

void ARM_Recomp::YieldToHost() {
    running->fiber->SwitchTo(*host);
}

void ARM_Recomp::SyncTicks() {
    const s64 used = slice_start - ctx->budget;
    if (used > 0) {
        GetTimer().AddTicks(static_cast<u64>(used));
    }
    slice_start = ctx->budget;
}

void ARM_Recomp::Call(u32 target) {
    const auto& functions = Functions();
    const auto it = functions.find(target);
    if (it == functions.end()) {
        Interpret(target);
        return;
    }
    it->second(ctx.get());
}

// Code that wasn't recompiled (an entry point the analysis missed, or code created at run
// time) runs in Azahar's interpreter until it returns to where the recompiled code expects
// it to (LR, or the caller's return address for a tail call), with the stack pointer back
// where it was or above. Slow, but the game keeps going; the address is logged so it can be
// added to the next recompilation (--extra).
void ARM_Recomp::Interpret(u32 target) {
    if (missing_logged.insert(target).second) {
        LOG_WARNING(Core_ARM11, "interpreting {:08X}, which is not recompiled code (lr {:08X})",
                    target, ctx->r[14]);
    }
    if (!interp) {
        interp = std::make_unique<Core::ARM_DynCom>(system, memory, USER32MODE, GetID(), timer);
    }
    // A tail call says where the code should come back to (the caller's own return
    // address, which LR may no longer hold); a callee that also pops its caller's frame
    // comes back with the stack pointer above where it was.
    const u32 return_to = ctx->expect_next ? ctx->expect_next : ctx->r[14];
    ctx->expect_next = 0;
    const u32 return_sp = ctx->r[13];
    SyncTicks();
    ctx->r[15] = target & ~1u;
    thumb = (target & 1) != 0;
    ToInterpreter();
    while (true) {
        const bool exits = ExitsThread();
        interp->Step();
        if (exits) {
            interpreting = false;
            running->finished = true;
            YieldToHost();
            UNREACHABLE_MSG("exited guest thread resumed");
        }
        if (loaded != running_context) {
            // The kernel switched threads inside a system call (it saved our state from the
            // interpreter); `ctx` belongs to the other thread now.
            interpreting = false;
            YieldToHost();
            ToInterpreter();
            continue;
        }
        const u32 pc = interp->GetPC() | ((interp->GetCPSR() & CPSR_T) ? 1u : 0u);
        if (pc == return_to && interp->GetReg(13) >= return_sp) {
            break;
        }
        if (HasFailed()) {
            YieldToHost(); // the interpreter hit something it couldn't handle
        }
        if (reschedule || GetTimer().GetDowncount() <= 0) {
            // A system call blocked or the time slice is over: park the thread with its
            // state in ctx (where SaveContext looks), then pick it up again.
            FromInterpreter();
            YieldToHost();
            ToInterpreter();
        }
    }
    FromInterpreter();
    slice_start = ctx->budget = GetTimer().GetDowncount();
    rc_apply_rounding(ctx->fpscr);
}

bool ARM_Recomp::ExitsThread() const {
    const u32 pc = interp->GetPC();
    if (interp->GetCPSR() & CPSR_T) {
        const u16 hw = memory.Read16(pc);
        return hw == 0xDF00 + SVC_EXIT_THREAD || hw == 0xDF00 + SVC_EXIT_PROCESS;
    }
    const u32 w = memory.Read32(pc);
    return (w & 0x0FFFFFFF) == 0x0F000000 + SVC_EXIT_THREAD || (w & 0x0FFFFFFF) == 0x0F000000 + SVC_EXIT_PROCESS;
}

void ARM_Recomp::ToInterpreter() {
    rc_apply_rounding(0); // the interpreter's VFP is software and honours FPSCR itself
    ThreadContext tc;
    SaveContext(tc);
    interp->LoadContext(tc);
    interp->SetCP15Register(CP15_THREAD_UPRW, ctx->tpidrurw);
    interp->SetCP15Register(CP15_THREAD_URO, ctx->tpidruro);
    interpreting = true;
}

void ARM_Recomp::FromInterpreter() {
    interpreting = false;
    ThreadContext tc;
    interp->SaveContext(tc);
    const auto* keep = loaded;
    LoadContext(tc);
    loaded = keep;
    ctx->tpidrurw = interp->GetCP15Register(CP15_THREAD_UPRW);
}

void ARM_Recomp::Svc(u32 imm, u32 next_pc) {
    SyncTicks();
    ctx->r[15] = next_pc & ~1u;
    thumb = (next_pc & 1) != 0;
    rc_apply_rounding(0); // Azahar's own code runs with the default rounding
#ifdef __ANDROID__
    // The system call goes through Azahar, which calls into Java (file access). Android's
    // runtime throws a StackOverflowError for Java code that runs on a stack other than the
    // thread's own, and a fiber's stack is one. So hand the call to Execute(), which runs it
    // on the emulation thread's stack, and wait to be resumed.
    svc_imm = imm;
    svc_pending = true;
    YieldToHost();
    if (imm == SVC_EXIT_THREAD || imm == SVC_EXIT_PROCESS) {
        UNREACHABLE_MSG("exited guest thread resumed");
    }
#else
    svc_context.CallSVC(imm);
    if (imm == SVC_EXIT_THREAD || imm == SVC_EXIT_PROCESS) {
        running->finished = true;
        YieldToHost();
        UNREACHABLE_MSG("exited guest thread resumed");
    }
    if (reschedule || loaded != running_context) {
        // The kernel wants another thread (or another core) to run, or already switched
        // threads during the call: park this one until its context is loaded again.
        YieldToHost();
    }
#endif
    slice_start = ctx->budget = GetTimer().GetDowncount();
    rc_apply_rounding(ctx->fpscr);
}

void ARM_Recomp::Budget() {
    SyncTicks();
    YieldToHost();
}

void ARM_Recomp::Fail(std::string message) {
    LOG_CRITICAL(Core_ARM11, "recompiled code: {}", message);
    failure = std::move(message);
    SetBreakFlag();
    running->finished = true;
    YieldToHost();
    std::abort(); // never resumed
}

void ARM_Recomp::PrepareReschedule() {
    reschedule = true;
}

// ── memory ──────────────────────────────────────────────────────────────────────────

void ARM_Recomp::SetPageTable(const std::shared_ptr<Memory::PageTable>& page_table_) {
    page_table = page_table_;
    ctx->pages = page_table ? page_table->GetPointerArray().data() : nullptr;
}

std::shared_ptr<Memory::PageTable> ARM_Recomp::GetPageTable() const {
    return page_table;
}

u32 ARM_Recomp::SlowRead(u32 addr, u32 size) {
    const bool crosses = (addr & Memory::CITRA_PAGE_MASK) > Memory::CITRA_PAGE_SIZE - size;
    if (size == 1) {
        return memory.Read8(addr);
    }
    if (crosses) {
        u32 v = 0;
        for (u32 i = 0; i < size; ++i) {
            v |= static_cast<u32>(memory.Read8(addr + i)) << (8 * i);
        }
        return v;
    }
    return size == 2 ? memory.Read16(addr) : memory.Read32(addr);
}

void ARM_Recomp::SlowWrite(u32 addr, u32 value, u32 size) {
    const bool crosses = (addr & Memory::CITRA_PAGE_MASK) > Memory::CITRA_PAGE_SIZE - size;
    if (size == 1) {
        memory.Write8(addr, static_cast<u8>(value));
    } else if (crosses) {
        for (u32 i = 0; i < size; ++i) {
            memory.Write8(addr + i, static_cast<u8>(value >> (8 * i)));
        }
    } else if (size == 2) {
        memory.Write16(addr, static_cast<u16>(value));
    } else {
        memory.Write32(addr, value);
    }
}

void ARM_Recomp::InvalidateCacheRange(u32 start_address, std::size_t length) {
    // Code is compiled ahead of time; nothing to invalidate. Code written at run time
    // (CROs, self-modifying code) would show up as jumps to non-recompiled addresses.
    LOG_TRACE(Core_ARM11, "ignoring code invalidation {:08X}+{:X}", start_address, length);
}

void ARM_Recomp::ClearExclusiveState() {
    ctx->excl_valid = 0;
}

// ── registers ───────────────────────────────────────────────────────────────────────

void ARM_Recomp::SetPC(u32 addr) {
    ctx->r[15] = addr;
}

u32 ARM_Recomp::GetPC() const {
    if (interpreting) {
        return interp->GetPC();
    }
    return ctx->r[15]; // exact at system calls, which is when the kernel asks
}

u32 ARM_Recomp::GetReg(int index) const {
    if (interpreting) {
        return interp->GetReg(index);
    }
    return ctx->r[index];
}

void ARM_Recomp::SetReg(int index, u32 value) {
    if (interpreting) {
        interp->SetReg(index, value);
        return;
    }
    ctx->r[index] = value;
}

u32 ARM_Recomp::GetVFPReg(int index) const {
    if (interpreting) {
        return interp->GetVFPReg(index);
    }
    return index < 32 ? ctx->f.sw[index] : 0;
}

void ARM_Recomp::SetVFPReg(int index, u32 value) {
    if (interpreting) {
        interp->SetVFPReg(index, value);
        return;
    }
    if (index < 32) {
        ctx->f.sw[index] = value;
    }
}

u32 ARM_Recomp::GetVFPSystemReg(VFPSystemRegister reg) const {
    switch (reg) {
    case VFP_FPSCR:
        return ctx->fpscr;
    case VFP_FPEXC:
        return fpexc;
    default:
        return 0;
    }
}

void ARM_Recomp::SetVFPSystemReg(VFPSystemRegister reg, u32 value) {
    switch (reg) {
    case VFP_FPSCR:
        ctx->fpscr = value;
        break;
    case VFP_FPEXC:
        fpexc = value;
        break;
    default:
        break;
    }
}

u32 ARM_Recomp::GetCPSR() const {
    if (interpreting) {
        return interp->GetCPSR();
    }
    return rc_get_cpsr(ctx.get()) | (thumb ? CPSR_T : 0);
}

void ARM_Recomp::SetCPSR(u32 cpsr) {
    rc_set_cpsr(ctx.get(), cpsr, 8 | 4);
    ctx->cpsr_misc = cpsr & CPSR_MISC_MASK;
    thumb = (cpsr & CPSR_T) != 0;
}

u32 ARM_Recomp::GetCP15Register(CP15Register reg) const {
    switch (reg) {
    case CP15_THREAD_UPRW:
        return ctx->tpidrurw;
    case CP15_THREAD_URO:
        return ctx->tpidruro;
    default:
        return 0;
    }
}

void ARM_Recomp::SetCP15Register(CP15Register reg, u32 value) {
    switch (reg) {
    case CP15_THREAD_UPRW:
        ctx->tpidrurw = value;
        break;
    case CP15_THREAD_URO:
        ctx->tpidruro = value;
        break;
    default:
        break;
    }
}

void ARM_Recomp::SaveContext(ThreadContext& tc) {
    if (interpreting) {
        interp->SaveContext(tc);
        return;
    }
    for (int i = 0; i < 16; ++i) {
        tc.cpu_registers[i] = ctx->r[i];
    }
    tc.cpsr = GetCPSR();
    for (int i = 0; i < 64; ++i) {
        tc.fpu_registers[i] = i < 32 ? ctx->f.sw[i] : 0;
    }
    tc.fpscr = ctx->fpscr;
    tc.fpexc = fpexc;
}

void ARM_Recomp::LoadContext(const ThreadContext& tc) {
    for (int i = 0; i < 16; ++i) {
        ctx->r[i] = tc.cpu_registers[i];
    }
    SetCPSR(tc.cpsr);
    for (int i = 0; i < 32; ++i) {
        ctx->f.sw[i] = tc.fpu_registers[i];
    }
    ctx->fpscr = tc.fpscr;
    fpexc = tc.fpexc;
    loaded = &tc;
}

} // namespace Port

// ── runtime callbacks for the generated code ────────────────────────────────────────

extern "C" {

void rc_call(RCtx* ctx, u32 target) {
    Port::Self(ctx).Call(target);
}

void rc_svc(RCtx* ctx, u32 imm, u32 next_pc) {
    Port::Self(ctx).Svc(imm, next_pc);
}

void rc_trap(RCtx* ctx, u32 pc, u32 kind) {
    static const char* const kinds[] = {"?", "undefined instruction", "unsupported instruction",
                                        "breakpoint", "bad jump"};
    Port::Self(ctx).Fail(fmt::format("{} at {:08X}", kinds[kind < 5 ? kind : 0], pc));
}

void rc_bad_return(RCtx* ctx, u32 got, u32 expected) {
    Port::Self(ctx).Fail(fmt::format("returned to {:08X}, expected {:08X}", got, expected));
}

void rc_budget(RCtx* ctx) {
    Port::Self(ctx).Budget();
}

u32 rc_slow_read(RCtx* ctx, u32 a, u32 size) {
    return Port::Self(ctx).SlowRead(a, size);
}

void rc_slow_write(RCtx* ctx, u32 a, u32 v, u32 size) {
    Port::Self(ctx).SlowWrite(a, v, size);
}

} // extern "C"
