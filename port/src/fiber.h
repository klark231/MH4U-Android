// Host fibers: execution contexts with their own stack, switched cooperatively on one
// thread. The recompiled-code CPU backend runs each guest thread on its own fiber, so a
// guest thread can be suspended in the middle of its (native) call stack when the 3DS
// kernel switches threads, and resumed later exactly where it stopped.
#pragma once

#include <cstddef>
#include <functional>
#include <memory>

namespace Port {

class Fiber {
public:
    /// Wraps the calling thread so other fibers can switch back to it.
    static std::unique_ptr<Fiber> FromCurrentThread();

    /// A new fiber that runs `entry` the first time it is switched to. `entry` must never
    /// return; switch to another fiber instead.
    static std::unique_ptr<Fiber> Create(std::function<void()> entry,
                                         std::size_t stack_size = 8u << 20);

    ~Fiber();
    Fiber(const Fiber&) = delete;
    Fiber& operator=(const Fiber&) = delete;

    /// Suspends `*this` (which must be the running fiber) and resumes `next`.
    void SwitchTo(Fiber& next);

    struct Impl;

private:
    explicit Fiber(std::unique_ptr<Impl> impl);
    std::unique_ptr<Impl> impl;
};

} // namespace Port
