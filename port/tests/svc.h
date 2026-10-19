/* Freestanding helpers for the port's 3DS test programs: types, system calls (register
 * usage as in libctru's svc.s) and debug output. No game code. */
#ifndef PORT_TESTS_SVC_H
#define PORT_TESTS_SVC_H

typedef unsigned char u8;
typedef unsigned int u32;
typedef int s32;
typedef unsigned long long u64;
typedef long long s64;
typedef u32 Handle;

/* ── system calls (register usage as in libctru's svc.s) ───────────────────────── */
#define NAKED static __attribute__((naked, noinline, used))
NAKED s32 svcOutputDebugString(const char *s, s32 len) { __asm__ volatile("svc 0x3D\n bx lr"); }
NAKED void svcExitProcess(void) { __asm__ volatile("svc 0x03\n bx lr"); }
NAKED void svcExitThread(void) { __asm__ volatile("svc 0x09\n bx lr"); }
NAKED void svcSleepThread(s64 ns) { __asm__ volatile("svc 0x0A\n bx lr"); }
NAKED s32 svcCloseHandle(Handle h) { __asm__ volatile("svc 0x23\n bx lr"); }
NAKED s32 svcSignalEvent(Handle h) { __asm__ volatile("svc 0x18\n bx lr"); }
NAKED s32 svcWaitSynchronization(Handle h, s64 ns) { __asm__ volatile("svc 0x24\n bx lr"); }
NAKED s32 svcReleaseMutex(Handle h) { __asm__ volatile("svc 0x14\n bx lr"); }
NAKED s32 svcCreateEvent(Handle *out, u32 reset_type) {
    __asm__ volatile("str r0, [sp, #-4]!\n svc 0x17\n ldr r2, [sp], #4\n str r1, [r2]\n bx lr");
}
NAKED s32 svcCreateMutex(Handle *out, u32 initially_locked) {
    __asm__ volatile("str r0, [sp, #-4]!\n svc 0x13\n ldr r3, [sp], #4\n str r1, [r3]\n bx lr");
}
NAKED s32 svcCreateThread(Handle *out, void (*entry)(u32), u32 arg, u32 *stack_top, s32 prio, s32 core) {
    __asm__ volatile("push {r0, r4}\n ldr r0, [sp, #8]\n ldr r4, [sp, #12]\n svc 0x08\n"
                     "pop {r2, r4}\n str r1, [r2]\n bx lr");
}

/* ── output ─────────────────────────────────────────────────────────────────────── */
static inline u32 str_len(const char *s) { u32 n = 0; while (s[n]) n++; return n; }

static inline void print(const char *s) { svcOutputDebugString(s, (s32)str_len(s)); }

static inline void print_hex(const char *label, u32 v) {
    char buf[64];
    u32 n = 0;
    while (label[n] && n < 40) { buf[n] = label[n]; n++; }
    buf[n++] = ' ';
    for (int i = 7; i >= 0; i--) buf[n++] = "0123456789abcdef"[(v >> (4 * i)) & 0xF];
    buf[n] = 0;
    print(buf);
}

#endif
