#if defined(__APPLE__) && !defined(_XOPEN_SOURCE)
// macOS keeps its ucontext routines (deprecated, still there) behind _XOPEN_SOURCE, which
// in turn hides BSD names such as MAP_ANONYMOUS unless _DARWIN_C_SOURCE is defined too
#define _XOPEN_SOURCE 600
#define _DARWIN_C_SOURCE
#endif
#include "fiber.h"

// Bionic (Android) has no getcontext/makecontext/swapcontext, so there the fibers switch
// stacks with a few lines of assembly (aarch64 and x86_64). MH4U_FIBER_ASM forces the same
// code on other hosts, which is how tests/test_port_android.py checks it.
#if !defined(_WIN32) && (defined(__ANDROID__) || defined(MH4U_FIBER_ASM))
#define MH4U_FIBER_USE_ASM 1
#endif

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#else
#include <sys/mman.h>
#include <unistd.h>
#ifndef MH4U_FIBER_USE_ASM
#include <ucontext.h>
#endif
#endif

namespace Port {

#ifdef _WIN32

struct Fiber::Impl {
    void* handle = nullptr;
    bool owns_thread = false; // converted the thread into a fiber
    std::function<void()> entry;
};

static void WINAPI FiberMain(void* param) {
    auto* impl = static_cast<Fiber::Impl*>(param);
    impl->entry();
    std::fprintf(stderr, "fiber entry returned\n");
    std::abort();
}

std::unique_ptr<Fiber> Fiber::FromCurrentThread() {
    auto impl = std::make_unique<Impl>();
    if (IsThreadAFiber()) {
        impl->handle = GetCurrentFiber();
    } else {
        impl->handle = ConvertThreadToFiber(nullptr);
        impl->owns_thread = true;
    }
    return std::unique_ptr<Fiber>(new Fiber(std::move(impl)));
}

std::unique_ptr<Fiber> Fiber::Create(std::function<void()> entry, std::size_t stack_size) {
    auto impl = std::make_unique<Impl>();
    impl->entry = std::move(entry);
    impl->handle = CreateFiberEx(64 * 1024, stack_size, FIBER_FLAG_FLOAT_SWITCH, FiberMain, impl.get());
    if (!impl->handle) {
        std::fprintf(stderr, "CreateFiberEx failed\n");
        std::abort();
    }
    return std::unique_ptr<Fiber>(new Fiber(std::move(impl)));
}

Fiber::~Fiber() {
    if (impl->owns_thread) {
        ConvertFiberToThread();
    } else if (impl->entry && impl->handle) {
        DeleteFiber(impl->handle);
    }
}

void Fiber::SwitchTo(Fiber& next) {
    SwitchToFiber(next.impl->handle);
}

#elif defined(MH4U_FIBER_USE_ASM)

// Context switch: push the callee-saved registers and the FP control state on the old stack,
// store the stack pointer, load the new one, pop, return. A new fiber's stack is made to look
// like a suspended one whose "return address" is a start stub that calls the entry function.
// The FP control state (rounding mode, flush-to-zero) is per fiber, as Win32's
// FIBER_FLAG_FLOAT_SWITCH does, because guest threads set it for themselves.
#if defined(__aarch64__)
__asm__(
    ".text\n"
    ".p2align 2\n"
    ".global mh4u_fiber_switch\n"
    ".hidden mh4u_fiber_switch\n"
    ".type mh4u_fiber_switch,%function\n"
    "mh4u_fiber_switch:\n"            // x0 = void** save sp, x1 = new sp
    "  sub sp, sp, #176\n"
    "  stp x19, x20, [sp, #0]\n"
    "  stp x21, x22, [sp, #16]\n"
    "  stp x23, x24, [sp, #32]\n"
    "  stp x25, x26, [sp, #48]\n"
    "  stp x27, x28, [sp, #64]\n"
    "  stp x29, x30, [sp, #80]\n"
    "  stp d8, d9, [sp, #96]\n"
    "  stp d10, d11, [sp, #112]\n"
    "  stp d12, d13, [sp, #128]\n"
    "  stp d14, d15, [sp, #144]\n"
    "  mrs x2, fpcr\n"
    "  mrs x3, fpsr\n"
    "  stp x2, x3, [sp, #160]\n"
    "  mov x2, sp\n"
    "  str x2, [x0]\n"
    "  mov sp, x1\n"
    "  ldp x2, x3, [sp, #160]\n"
    "  msr fpcr, x2\n"
    "  msr fpsr, x3\n"
    "  ldp x19, x20, [sp, #0]\n"
    "  ldp x21, x22, [sp, #16]\n"
    "  ldp x23, x24, [sp, #32]\n"
    "  ldp x25, x26, [sp, #48]\n"
    "  ldp x27, x28, [sp, #64]\n"
    "  ldp x29, x30, [sp, #80]\n"
    "  ldp d8, d9, [sp, #96]\n"
    "  ldp d10, d11, [sp, #112]\n"
    "  ldp d12, d13, [sp, #128]\n"
    "  ldp d14, d15, [sp, #144]\n"
    "  add sp, sp, #176\n"
    "  ret\n"
    ".size mh4u_fiber_switch, .-mh4u_fiber_switch\n"
    ".p2align 2\n"
    ".global mh4u_fiber_start\n"
    ".hidden mh4u_fiber_start\n"
    ".type mh4u_fiber_start,%function\n"
    "mh4u_fiber_start:\n"             // x19 = argument, x20 = entry function
    "  mov x0, x19\n"
    "  blr x20\n"
    "  brk #0\n"
    ".size mh4u_fiber_start, .-mh4u_fiber_start\n");
static constexpr std::size_t kFrameBytes = 176;
#elif defined(__x86_64__)
__asm__(
    ".text\n"
    ".p2align 4\n"
    ".global mh4u_fiber_switch\n"
    ".hidden mh4u_fiber_switch\n"
    ".type mh4u_fiber_switch,@function\n"
    "mh4u_fiber_switch:\n"            // rdi = void** save sp, rsi = new sp
    "  pushq %rbp\n"
    "  pushq %rbx\n"
    "  pushq %r12\n"
    "  pushq %r13\n"
    "  pushq %r14\n"
    "  pushq %r15\n"
    "  subq $16, %rsp\n"
    "  stmxcsr 0(%rsp)\n"
    "  fnstcw 4(%rsp)\n"
    "  movq %rsp, (%rdi)\n"
    "  movq %rsi, %rsp\n"
    "  ldmxcsr 0(%rsp)\n"
    "  fldcw 4(%rsp)\n"
    "  addq $16, %rsp\n"
    "  popq %r15\n"
    "  popq %r14\n"
    "  popq %r13\n"
    "  popq %r12\n"
    "  popq %rbx\n"
    "  popq %rbp\n"
    "  ret\n"
    ".size mh4u_fiber_switch, .-mh4u_fiber_switch\n"
    ".p2align 4\n"
    ".global mh4u_fiber_start\n"
    ".hidden mh4u_fiber_start\n"
    ".type mh4u_fiber_start,@function\n"
    "mh4u_fiber_start:\n"             // r13 = argument, r12 = entry function
    "  movq %r13, %rdi\n"
    "  callq *%r12\n"
    "  ud2\n"
    ".size mh4u_fiber_start, .-mh4u_fiber_start\n");
static constexpr std::size_t kFrameBytes = 16 + 6 * 8 + 8; // FP state, six registers, return address
#else
#error "MH4U_FIBER_ASM: this CPU has no fiber assembly (aarch64 and x86_64 do)"
#endif

extern "C" void mh4u_fiber_switch(void** save_sp, void* new_sp);
extern "C" void mh4u_fiber_start();

struct Fiber::Impl {
    void* sp = nullptr; // where the suspended fiber's registers are
    void* stack = nullptr;
    std::size_t stack_size = 0; // including the guard page
    std::function<void()> entry;
};

static void FiberMain(void* arg) {
    auto* impl = static_cast<Fiber::Impl*>(arg);
    impl->entry();
    std::fprintf(stderr, "fiber entry returned\n");
    std::abort();
}

std::unique_ptr<Fiber> Fiber::FromCurrentThread() {
    return std::unique_ptr<Fiber>(new Fiber(std::make_unique<Impl>()));
}

std::unique_ptr<Fiber> Fiber::Create(std::function<void()> entry, std::size_t stack_size) {
    auto impl = std::make_unique<Impl>();
    impl->entry = std::move(entry);
    const std::size_t page = static_cast<std::size_t>(sysconf(_SC_PAGESIZE));
    stack_size = (stack_size + page - 1) / page * page;
    impl->stack_size = stack_size + page;
    impl->stack = mmap(nullptr, impl->stack_size, PROT_READ | PROT_WRITE,
                       MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    if (impl->stack == MAP_FAILED) {
        std::perror("mmap fiber stack");
        std::abort();
    }
    mprotect(impl->stack, page, PROT_NONE); // the lowest page catches stack overflows
    const auto top = (reinterpret_cast<std::uintptr_t>(impl->stack) + impl->stack_size) &
                     ~static_cast<std::uintptr_t>(15);
    auto* frame = reinterpret_cast<std::uintptr_t*>(top - kFrameBytes);
    for (std::size_t i = 0; i < kFrameBytes / sizeof(std::uintptr_t); ++i) {
        frame[i] = 0;
    }
#if defined(__aarch64__)
    // x19, x20, ..., x30 at 0..88; d8-d15 at 96..152; fpcr, fpsr at 160, 168
    frame[0] = reinterpret_cast<std::uintptr_t>(impl.get());   // x19: argument
    frame[1] = reinterpret_cast<std::uintptr_t>(&FiberMain);   // x20: entry
    frame[11] = reinterpret_cast<std::uintptr_t>(&mh4u_fiber_start); // x30: where `ret` goes
    // fpcr/fpsr start as zero: round to nearest, nothing flushed; guest code sets its own
#else
    // [mxcsr|fpucw, 16 bytes] r15 r14 r13 r12 rbx rbp ret (words 2..8)
    std::uint32_t mxcsr = 0x1F80, fpucw = 0x037F;
    std::memcpy(&frame[0], &mxcsr, 4);
    std::memcpy(reinterpret_cast<char*>(&frame[0]) + 4, &fpucw, 4);
    frame[5] = reinterpret_cast<std::uintptr_t>(&FiberMain);   // r12: entry
    frame[4] = reinterpret_cast<std::uintptr_t>(impl.get());   // r13: argument
    frame[8] = reinterpret_cast<std::uintptr_t>(&mh4u_fiber_start); // return address
#endif
    impl->sp = frame;
    return std::unique_ptr<Fiber>(new Fiber(std::move(impl)));
}

Fiber::~Fiber() {
    if (impl->stack) {
        munmap(impl->stack, impl->stack_size);
    }
}

void Fiber::SwitchTo(Fiber& next) {
    mh4u_fiber_switch(&impl->sp, next.impl->sp);
}

#else

struct Fiber::Impl {
    ucontext_t context{};
    void* stack = nullptr;
    std::size_t stack_size = 0; // including the guard page
    std::function<void()> entry;
};

static void FiberMain(unsigned hi, unsigned lo) {
    auto* impl = reinterpret_cast<Fiber::Impl*>((static_cast<std::uintptr_t>(hi) << 32) | lo);
    impl->entry();
    std::fprintf(stderr, "fiber entry returned\n");
    std::abort();
}

std::unique_ptr<Fiber> Fiber::FromCurrentThread() {
    return std::unique_ptr<Fiber>(new Fiber(std::make_unique<Impl>()));
}

std::unique_ptr<Fiber> Fiber::Create(std::function<void()> entry, std::size_t stack_size) {
    auto impl = std::make_unique<Impl>();
    impl->entry = std::move(entry);
    const std::size_t page = static_cast<std::size_t>(sysconf(_SC_PAGESIZE));
    stack_size = (stack_size + page - 1) / page * page;
    impl->stack_size = stack_size + page;
    // Reserve lazily; the lowest page stays inaccessible to catch stack overflows.
    impl->stack = mmap(nullptr, impl->stack_size, PROT_READ | PROT_WRITE,
                       MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    if (impl->stack == MAP_FAILED) {
        std::perror("mmap fiber stack");
        std::abort();
    }
    mprotect(impl->stack, page, PROT_NONE);
    getcontext(&impl->context);
    impl->context.uc_stack.ss_sp = static_cast<char*>(impl->stack) + page;
    impl->context.uc_stack.ss_size = stack_size;
    impl->context.uc_link = nullptr;
    const auto p = reinterpret_cast<std::uintptr_t>(impl.get());
    makecontext(&impl->context, reinterpret_cast<void (*)()>(FiberMain), 2,
                static_cast<unsigned>(p >> 32), static_cast<unsigned>(p & 0xFFFFFFFFu));
    return std::unique_ptr<Fiber>(new Fiber(std::move(impl)));
}

Fiber::~Fiber() {
    if (impl->stack) {
        munmap(impl->stack, impl->stack_size);
    }
}

void Fiber::SwitchTo(Fiber& next) {
    swapcontext(&impl->context, &next.impl->context);
}

#endif

Fiber::Fiber(std::unique_ptr<Impl> impl_) : impl(std::move(impl_)) {}

} // namespace Port
