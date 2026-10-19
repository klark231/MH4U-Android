/*
 * Native test harness for recompiled code (Linux/macOS).
 *
 * Guest memory is a 4 GiB PROT_NONE reservation; the Python side commits and fills the
 * regions it needs. Every way generated code can leave a call (system call, trap, bad
 * return, exhausted budget, fault on unmapped/protected memory, unknown indirect target)
 * is turned into a status code via siglongjmp, so the caller can compare with Unicorn.
 */
#define _GNU_SOURCE
#include <setjmp.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>

#include "recomp.h"

extern const RFuncEntry rc_func_table[];
extern const u32 rc_func_count;
extern const u32 rc_hook_addrs[], rc_hook_count;

enum { ST_RETURN = 0, ST_SVC = 1, ST_FAULT = 2, ST_BUDGET = 3, ST_TRAP = 4, ST_BADRET = 5, ST_NOFUNC = 6 };

#define GUEST_SPAN ((size_t)1 << 32)
#define GUARD ((size_t)1 << 16)

static u8 *g_mem;
static sigjmp_buf g_jmp;
static volatile sig_atomic_t g_in_call;
static volatile u32 g_status, g_info0, g_info1;
static volatile uintptr_t g_fault;
static int g_handlers;

int rh_init(void) {
    if (g_mem) return 0;
    void *p = mmap(NULL, GUEST_SPAN + GUARD, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    if (p == MAP_FAILED) return -1;
    g_mem = (u8 *)p;
    return 0;
}

/* prot: 0 none, 1 read, 3 read/write */
int rh_map(u32 addr, u32 size, int prot) {
    int p = prot == 0 ? PROT_NONE : prot == 1 ? PROT_READ : PROT_READ | PROT_WRITE;
    return mprotect(g_mem + addr, size, p);
}
void rh_write(u32 addr, const void *src, u32 n) { memcpy(g_mem + addr, src, n); }
void rh_read(u32 addr, void *dst, u32 n) { memcpy(dst, g_mem + addr, n); }
u32 rh_info0(void) { return g_info0; }
u32 rh_info1(void) { return g_info1; }
u32 rh_fault_addr(void) { return (u32)(g_fault - (uintptr_t)g_mem); }
u32 rh_func_count(void) { return rc_func_count; }
void rh_func_at(u32 i, u32 *addr, u32 *mode) { *addr = rc_func_table[i].addr; *mode = rc_func_table[i].mode; }
/* install (or with NULL remove) a hook on a hook point; 0 if addr isn't one */
int rh_set_hook(u32 addr, RHookFn hook) {
    for (u32 i = 0; i < rc_hook_count; i++) {
        if (rc_hook_addrs[i] == addr) {
            rc_hooks[i] = hook;
            return 1;
        }
    }
    return 0;
}

static const RFuncEntry *find(u32 addr) {
    u32 lo = 0, hi = rc_func_count;
    while (lo < hi) {
        u32 mid = (lo + hi) / 2;
        if (rc_func_table[mid].addr < addr) lo = mid + 1;
        else hi = mid;
    }
    if (lo < rc_func_count && rc_func_table[lo].addr == addr) return &rc_func_table[lo];
    return NULL;
}

static void leave(u32 status, u32 i0, u32 i1) {
    g_status = status;
    g_info0 = i0;
    g_info1 = i1;
    siglongjmp(g_jmp, 1);
}

void rc_call(RCtx *ctx, u32 target) {
    const RFuncEntry *e = find(target & ~1u);
    if (!e || e->mode != (target & 1)) leave(ST_NOFUNC, target, 0);
    e->fn(ctx);
}
void rc_call_ic(RCtx *ctx, u32 target, u32 *ic_target, void (**ic_fn)(RCtx *)) {
    const RFuncEntry *e = find(target & ~1u);
    if (!e || e->mode != (target & 1)) leave(ST_NOFUNC, target, 0);
    *ic_target = target;
    *ic_fn = e->fn;
    e->fn(ctx);
}
void rc_svc(RCtx *ctx, u32 imm, u32 next_pc) { (void)ctx; leave(ST_SVC, imm, next_pc & ~1u); }
void rc_trap(RCtx *ctx, u32 pc, u32 kind) { (void)ctx; leave(ST_TRAP, pc, kind); }
void rc_bad_return(RCtx *ctx, u32 got, u32 expected) { (void)ctx; leave(ST_BADRET, got, expected); }
void rc_budget(RCtx *ctx) { (void)ctx; leave(ST_BUDGET, 0, 0); }

static void on_signal(int sig, siginfo_t *si, void *uc) {
    (void)uc;
    if (!g_in_call) {
        signal(sig, SIG_DFL);
        raise(sig);
        return;
    }
    g_fault = (uintptr_t)si->si_addr;
    g_status = ST_FAULT;
    g_info0 = (u32)sig;
    siglongjmp(g_jmp, 1);
}

static void install(void) {
    if (g_handlers) return;
    static u8 altstack[1 << 16];
    stack_t ss = {.ss_sp = altstack, .ss_size = sizeof altstack, .ss_flags = 0};
    sigaltstack(&ss, NULL);
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_sigaction = on_signal;
    sa.sa_flags = SA_SIGINFO | SA_ONSTACK | SA_NODEFER;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGSEGV, &sa, NULL);
    sigaction(SIGBUS, &sa, NULL);
    sigaction(SIGILL, &sa, NULL);
    sigaction(SIGFPE, &sa, NULL);
    g_handlers = 1;
}

/* Call the function at guest address `addr` (bit 0 = Thumb). Returns a status code. */
static int g_check_return = 1;
void rh_set_check_return(int on) { g_check_return = on; }

int rh_call(u32 addr, RCtx *ctx) {
    const RFuncEntry *e = find(addr & ~1u);
    if (!e) return ST_NOFUNC;
    install();
    ctx->mem = g_mem;
    g_status = ST_RETURN;
    g_info0 = g_info1 = 0;
    const u32 expect = ctx->r[14];
    rc_apply_rounding(ctx->fpscr);          /* the host follows FPSCR.RMode in guest code */
    if (sigsetjmp(g_jmp, 1) == 0) {
        g_in_call = 1;
        e->fn(ctx);
        /* a tail-called function returns through its callee: check the final target */
        if (g_check_return && (ctx->r[15] & ~1u) != (expect & ~1u)) {
            g_status = ST_BADRET;
            g_info0 = ctx->r[15];
            g_info1 = expect;
        }
    }
    g_in_call = 0;
    rc_apply_rounding(0);
    return (int)g_status;
}
