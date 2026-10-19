// Fiber test (built with -DMH4U_FIBER_ASM on hosts that have ucontext, always on Android):
// ping-pong between fibers, deep stacks, floating-point state kept per fiber.
#include "fiber.h"

#include <cfenv>
#include <cmath>
#include <cstdio>
#include <vector>

using Port::Fiber;

static int fails = 0;
#define CHECK(c) do { if (!(c)) { std::printf("FAIL line %d: %s\n", __LINE__, #c); ++fails; } } while (0)

static std::unique_ptr<Fiber> host, a, b;
static std::vector<int> order;

static int Recurse(int n) { volatile char pad[256]; pad[0] = static_cast<char>(n); return n == 0 ? pad[0] : 1 + Recurse(n - 1); }

int main() {
    host = Fiber::FromCurrentThread();
    a = Fiber::Create([] {
        for (int i = 0; i < 3; ++i) { order.push_back(10 + i); a->SwitchTo(*host); }
        // a rounding mode set here must not leak into the others
        std::fesetround(FE_UPWARD);
        order.push_back(Recurse(5000));            // ~1.3 MB of stack
        a->SwitchTo(*host);
        for (;;) a->SwitchTo(*host);
    });
    b = Fiber::Create([] {
        double x = 1.0, y = 3.0;
        for (int i = 0; i < 3; ++i) {
            volatile double z = x / y;             // callee-saved FP registers and FPCR survive
            order.push_back(20 + i + (z > 0.33 && z < 0.34 ? 0 : 100));
            b->SwitchTo(*host);
        }
        order.push_back(std::fegetround() == FE_TONEAREST ? 30 : 130);
        for (;;) b->SwitchTo(*host);
    });
    for (int i = 0; i < 3; ++i) { host->SwitchTo(*a); host->SwitchTo(*b); }
    host->SwitchTo(*a);                            // a: sets FE_UPWARD, recurses
    host->SwitchTo(*b);                            // b: must still see round-to-nearest
    CHECK((order == std::vector<int>{10, 20, 11, 21, 12, 22, 5000, 30}));
    CHECK(std::fegetround() == FE_TONEAREST);      // the host's own mode is back
    a.reset(); b.reset(); host.reset();
    if (!fails) std::printf("fiber ok\n");
    return fails;
}
