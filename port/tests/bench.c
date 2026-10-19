/* CPU benchmark for the port's backends: integer, memory, VFP and call-heavy loops with a
 * fixed amount of work. Run it with --cpu jit and --cpu recomp and compare wall times; the
 * printed checksums must match. No game code. */
#include "svc.h"

#define N 4096
static u32 buf[N];
static float mat[16], acc[16];

static u32 crc_pass(u32 crc) {
    const u8 *p = (const u8 *)buf;
    for (u32 i = 0; i < sizeof(buf); i++) {
        crc ^= p[i];
        for (int k = 0; k < 8; k++) crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1)));
    }
    return crc;
}

static void sort(u32 *a, int lo, int hi) {
    while (lo < hi) {
        u32 p = a[(lo + hi) / 2];
        int i = lo, j = hi;
        while (i <= j) {
            while (a[i] < p) i++;
            while (a[j] > p) j--;
            if (i <= j) { u32 t = a[i]; a[i] = a[j]; a[j] = t; i++; j--; }
        }
        if (j - lo < hi - i) { sort(a, lo, j); lo = i; } else { sort(a, i, hi); hi = j; }
    }
}

static void matmul(float *out, const float *a, const float *b) {
    for (int r = 0; r < 4; r++)
        for (int c = 0; c < 4; c++) {
            float s = 0;
            for (int k = 0; k < 4; k++) s += a[r * 4 + k] * b[k * 4 + c];
            out[r * 4 + c] = s;
        }
}

typedef u32 (*step_fn)(u32);
static u32 s0(u32 x) { return x * 1664525u + 1013904223u; }
static u32 s1(u32 x) { return x ^ (x << 13) ^ (x >> 17); }
static u32 s2(u32 x) { return (x >> 3) | (x << 29); }
static step_fn steps[3] = {s0, s1, s2};

void _start(void) __attribute__((section(".text.start")));
void _start(void) {
    print("bench: start");
    u32 seed = 1;
    for (u32 i = 0; i < N; i++) buf[i] = seed = seed * 1103515245u + 12345u;

    u32 crc = 0;
    for (int it = 0; it < 24; it++) crc = crc_pass(crc);
    print_hex("crc", crc);

    u32 sorted = 0;
    for (int it = 0; it < 40; it++) {
        for (u32 i = 0; i < N; i++) buf[i] = seed = seed * 1103515245u + 12345u;
        sort(buf, 0, N - 1);
        sorted += buf[N / 2];
    }
    print_hex("sort", sorted);

    for (int i = 0; i < 16; i++) { mat[i] = (float)(i % 5) * 0.25f - 0.5f; acc[i] = i == i / 4 * 5 ? 1.0f : 0.0f; }
    float tmp[16];
    for (int it = 0; it < 400000; it++) {
        matmul(tmp, acc, mat);
        for (int i = 0; i < 16; i++) acc[i] = tmp[i] * 0.5f + (float)(i & 1);
    }
    u32 fsum = 0;
    for (int i = 0; i < 16; i++) fsum = fsum * 31 + *(u32 *)&acc[i];
    print_hex("vfp", fsum);

    u32 x = 7;
    for (u32 i = 0; i < 6000000; i++) x = steps[(x ^ i) % 3](x);
    print_hex("calls", x);

    print("bench: done");
    svcExitProcess();
}
