/* Minimal runtime linked into difftest candidates.
 *
 * clang may emit calls to these helpers (ARMv6 has no divide instruction, struct
 * copies become memcpy, ...). They are implemented here without '/' or '%' so they
 * never call themselves. Behaviour matches the ARM run-time ABI.
 */
typedef unsigned int u32;
typedef unsigned long long u64;
typedef unsigned long size_t;

static u32 udiv32(u32 n, u32 d, u32 *rem) {
    u32 q = 0, r = 0;
    if (d == 0) { if (rem) *rem = n; return 0; }
    for (int i = 31; i >= 0; i--) {
        r = (r << 1) | ((n >> i) & 1);
        if (r >= d) { r -= d; q |= 1u << i; }
    }
    if (rem) *rem = r;
    return q;
}

static u64 udiv64(u64 n, u64 d, u64 *rem) {
    u64 q = 0, r = 0;
    if (d == 0) { if (rem) *rem = n; return 0; }
    for (int i = 63; i >= 0; i--) {
        r = (r << 1) | ((n >> i) & 1);
        if (r >= d) { r -= d; q |= 1ull << i; }
    }
    if (rem) *rem = r;
    return q;
}

u32 __aeabi_uidiv(u32 n, u32 d) { return udiv32(n, d, 0); }

int __aeabi_idiv(int n, int d) {
    u32 un = n < 0 ? 0u - (u32)n : (u32)n, ud = d < 0 ? 0u - (u32)d : (u32)d;
    u32 q = udiv32(un, ud, 0);
    return ((n < 0) != (d < 0)) ? -(int)q : (int)q;
}

/* {quotient, remainder} returned in r0, r1 */
u64 __aeabi_uidivmod(u32 n, u32 d) {
    u32 r, q = udiv32(n, d, &r);
    return ((u64)r << 32) | q;
}

u64 __aeabi_idivmod(int n, int d) {
    u32 un = n < 0 ? 0u - (u32)n : (u32)n, ud = d < 0 ? 0u - (u32)d : (u32)d, ur;
    u32 uq = udiv32(un, ud, &ur);
    int q = ((n < 0) != (d < 0)) ? -(int)uq : (int)uq;
    int r = n < 0 ? -(int)ur : (int)ur;
    return ((u64)(u32)r << 32) | (u32)q;
}

/* 64-bit versions return {quotient in r0:r1, remainder in r2:r3}. A C function can't
 * return four registers, so a naked wrapper passes a remainder slot on the stack and
 * loads it into r2:r3 afterwards. */
__attribute__((used)) u64 dt_uldivmod(u64 n, u64 d, u64 *rem) { return udiv64(n, d, rem); }

__attribute__((used)) long long dt_ldivmod(long long n, long long d, long long *rem) {
    u64 un = n < 0 ? 0ull - (u64)n : (u64)n, ud = d < 0 ? 0ull - (u64)d : (u64)d, ur;
    u64 uq = udiv64(un, ud, &ur);
    *rem = n < 0 ? -(long long)ur : (long long)ur;
    return ((n < 0) != (d < 0)) ? -(long long)uq : (long long)uq;
}

#define DIVMOD64_WRAPPER(name, helper)          \
    __attribute__((naked)) void name(void) {    \
        __asm__ volatile(                       \
            "push {r4, lr}\n"                   \
            "sub sp, sp, #16\n"                 \
            "add r4, sp, #8\n"                  \
            "str r4, [sp]\n"                    \
            "bl " #helper "\n"                  \
            "ldr r2, [sp, #8]\n"                \
            "ldr r3, [sp, #12]\n"               \
            "add sp, sp, #16\n"                 \
            "pop {r4, pc}\n");                  \
    }

DIVMOD64_WRAPPER(__aeabi_uldivmod, dt_uldivmod)
DIVMOD64_WRAPPER(__aeabi_ldivmod, dt_ldivmod)

void *memcpy(void *dst, const void *src, size_t n) {
    unsigned char *d = dst; const unsigned char *s = src;
    while (n--) *d++ = *s++;
    return dst;
}

void *memmove(void *dst, const void *src, size_t n) {
    unsigned char *d = dst; const unsigned char *s = src;
    if (d < s) { while (n--) *d++ = *s++; }
    else { d += n; s += n; while (n--) *--d = *--s; }
    return dst;
}

void *memset(void *dst, int c, size_t n) {
    unsigned char *d = dst;
    while (n--) *d++ = (unsigned char)c;
    return dst;
}

void __aeabi_memcpy(void *d, const void *s, size_t n) { memcpy(d, s, n); }
void __aeabi_memcpy4(void *d, const void *s, size_t n) { memcpy(d, s, n); }
void __aeabi_memcpy8(void *d, const void *s, size_t n) { memcpy(d, s, n); }
void __aeabi_memmove(void *d, const void *s, size_t n) { memmove(d, s, n); }
void __aeabi_memmove4(void *d, const void *s, size_t n) { memmove(d, s, n); }
void __aeabi_memset(void *d, size_t n, int c) { memset(d, c, n); }
void __aeabi_memset4(void *d, size_t n, int c) { memset(d, c, n); }
void __aeabi_memclr(void *d, size_t n) { memset(d, 0, n); }
void __aeabi_memclr4(void *d, size_t n) { memset(d, 0, n); }
void __aeabi_memclr8(void *d, size_t n) { memset(d, 0, n); }
