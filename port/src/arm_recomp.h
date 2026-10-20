// Azahar CPU backend that runs the game's recompiled code (tools/recompile.py) natively.
//
// Each guest thread runs on its own host fiber: the C call stack of the recompiled code
// *is* the guest thread's execution state, so when Azahar's kernel switches threads
// (SaveContext/LoadContext) the backend switches fibers. Guest code gives control back
// to Azahar at system calls (rc_svc), when its cycle budget runs out (rc_budget), and on
// errors. Guest memory is Azahar's page table (recomp.h's RC_PAGED mode).
#pragma once

#include <memory>
#include <utility>
#include <vector>
#include <string>
#include <unordered_map>
#include <unordered_set>

#include "core/arm/arm_interface.h"
#include "core/hle/kernel/svc.h"

struct RCtx;
struct RCodeInfo;

namespace Core {
class System;
}
namespace Memory {
class MemorySystem;
struct PageTable;
} // namespace Memory

namespace Port {

class Fiber;

class ARM_Recomp final : public Core::ARM_Interface {
public:
    ARM_Recomp(Core::System& system, Memory::MemorySystem& memory, u32 core_id,
               std::shared_ptr<Core::Timing::Timer> timer);
    ~ARM_Recomp() override;

    void Run() override;
    void Step() override;
    void ClearInstructionCache() override {}
    void InvalidateCacheRange(u32 start_address, std::size_t length) override;
    void ClearExclusiveState() override;
    void SetPageTable(const std::shared_ptr<Memory::PageTable>& page_table) override;
    void SetPC(u32 addr) override;
    u32 GetPC() const override;
    u32 GetReg(int index) const override;
    void SetReg(int index, u32 value) override;
    u32 GetVFPReg(int index) const override;
    void SetVFPReg(int index, u32 value) override;
    u32 GetVFPSystemReg(VFPSystemRegister reg) const override;
    void SetVFPSystemReg(VFPSystemRegister reg, u32 value) override;
    u32 GetCPSR() const override;
    void SetCPSR(u32 cpsr) override;
    u32 GetCP15Register(CP15Register reg) const override;
    void SetCP15Register(CP15Register reg, u32 value) override;
    void SaveContext(ThreadContext& ctx) override;
    void LoadContext(const ThreadContext& ctx) override;
    void PrepareReschedule() override;
    bool HasSingleInstructionBreakAccuracy() override {
        return false;
    }

    /// The emulated program did something the recompiled code can't continue from (a jump
    /// to code that wasn't recompiled, an undefined instruction, ...). Emulation stops.
    bool HasFailed() const {
        return !failure.empty();
    }
    const std::string& Failure() const {
        return failure;
    }

    /// The recompiled program to run: a code library built by port/CMakeLists.txt or by
    /// tools/recomp/build.py --shared.
    static bool LoadCode(const std::string& path, std::string& error);
    static void SetCode(const RCodeInfo& info);
    /// Number of recompiled functions available, and the program id they're for (0: any).
    static std::size_t FunctionCount();
    static u64 CodeTitleId();
    /// Whether `code` (a program's code segment onwards, as the loader puts it in memory) is
    /// the code the loaded library was recompiled from.
    static bool IsRecompiledCode(const std::vector<u8>& code);
    /// A code library built with -DRC_CALL_COUNTS counts every function's calls: whether
    /// the loaded one does, and (address, calls) for each function that has run so far.
    static bool HasCallCounts();
    static std::vector<std::pair<u32, u32>> CallCounts();

    // Called by the runtime callbacks of recomp.h, on a guest fiber.
    void Call(u32 target);
    void Svc(u32 imm, u32 next_pc);
    void Budget();
    [[noreturn]] void Fail(std::string message);
    u32 SlowRead(u32 addr, u32 size);
    void SlowWrite(u32 addr, u32 value, u32 size);

protected:
    std::shared_ptr<Memory::PageTable> GetPageTable() const override;

private:
    struct GuestThread;

    void Execute(s64 budget);
    void Interpret(u32 target);
    bool ExitsThread() const;
    void ToInterpreter();
    void FromInterpreter();
    void GuestMain();
    void YieldToHost();
    void SyncTicks();

    Core::System& system;
    Memory::MemorySystem& memory;
    Kernel::SVCContext svc_context;
    std::unique_ptr<RCtx> ctx;
    u32 fpexc = 0;
    bool thumb = false; // CPSR.T of the loaded context
    std::shared_ptr<Memory::PageTable> page_table;
    std::unique_ptr<Fiber> host;
    std::unordered_map<const ThreadContext*, std::unique_ptr<GuestThread>> threads;
    const ThreadContext* loaded = nullptr; // context whose registers are in `ctx`
    GuestThread* running = nullptr;        // thread executing right now (on its fiber)
    const ThreadContext* running_context = nullptr;
    s64 slice_start = 0;
    bool reschedule = false;
    // Android: a system call waiting for the emulation thread's own stack (see Svc())
    bool svc_pending = false;
    u32 svc_imm = 0;
    std::string failure;
    // fallback for code that wasn't recompiled
    std::unique_ptr<Core::ARM_Interface> interp;
    bool interpreting = false;
    std::unordered_set<u32> missing_logged;
};

} // namespace Port
