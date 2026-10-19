/* Input and display test for the port's runner (no game code): reads buttons, the circle
 * pad and the touch screen from HID shared memory, and draws a known pattern into the top
 * screen's framebuffer, so the runner's --input scripts and --shots images can be checked.
 *
 * Top screen as displayed (400x240): a green band across rows 0-19, then the left half
 * (columns 0-199) red and the right half blue. Built by port/tests/build_svc_test.py. */
#include "svc.h"

NAKED s32 svcConnectToPort(Handle *out, const char *name) {
    __asm__ volatile("str r0, [sp, #-4]!\n svc 0x2D\n ldr r2, [sp], #4\n str r1, [r2]\n bx lr");
}
NAKED s32 svcSendSyncRequest(Handle h) { __asm__ volatile("svc 0x32\n bx lr"); }
NAKED s32 svcMapMemoryBlock(Handle mem, u32 addr, u32 perm, u32 other_perm) { __asm__ volatile("svc 0x1F\n bx lr"); }

static u32 *cmdbuf(void) {
    u32 *tls;
    __asm__ volatile("mrc p15, 0, %0, c13, c0, 3" : "=r"(tls));
    return tls + 0x80 / 4;
}

static Handle srv;

static s32 srv_init(void) {
    s32 r = svcConnectToPort(&srv, "srv:");
    if (r) return r;
    u32 *cmd = cmdbuf();
    cmd[0] = 0x00010002;   /* RegisterClient */
    cmd[1] = 0x20;         /* current process id */
    r = svcSendSyncRequest(srv);
    return r ? r : (s32)cmd[1];
}

static s32 get_service(Handle *out, const char *name) {
    u32 *cmd = cmdbuf();
    u32 n = str_len(name);
    char *dst = (char *)&cmd[1];
    cmd[0] = 0x00050100;   /* GetServiceHandle */
    for (u32 i = 0; i < 8; i++) dst[i] = i < n ? name[i] : 0;
    cmd[3] = n;
    cmd[4] = 0;
    s32 r = svcSendSyncRequest(srv);
    if (r) return r;
    *out = cmd[3];
    return (s32)cmd[1];
}

#define HID_SHM 0x10000000u
#define TOP_FB 0x1F1E6000u      /* VRAM 0x181E6000: the top screen's default framebuffer */

static void draw_top(void) {
    /* stored 240 pixels per line, 400 lines, BGR: line = display column, the pixel at
     * offset (240 - row) % 240 = display row */
    volatile u8 *fb = (volatile u8 *)TOP_FB;
    for (u32 col = 0; col < 400; col++) {
        for (u32 row = 0; row < 240; row++) {
            volatile u8 *p = fb + (col * 240 + (240 - row) % 240) * 3;
            u8 r = 0, g = 0, b = 0;
            if (row < 20) g = 255;
            else if (col < 200) r = 255;
            else b = 255;
            p[0] = b; p[1] = g; p[2] = r;
        }
    }
}

/* The runner's --replace test changes this in memory while the program runs. */
static volatile u32 marker = 0x4d483455u;

void _start(void) __attribute__((section(".text.start")));
void _start(void) {
    print("io_test: start");
    Handle hid;
    s32 r = srv_init();
    if (r == 0) r = get_service(&hid, "hid:USER");
    if (r != 0) print_hex("service error", (u32)r);
    u32 *cmd = cmdbuf();
    cmd[0] = 0x000A0000;   /* GetIPCHandles */
    svcSendSyncRequest(hid);
    Handle shm = cmd[3];
    r = svcMapMemoryBlock(shm, HID_SHM, 1, 0x10000000);
    if (r != 0) print_hex("map error", (u32)r);
    draw_top();
    print("io_test: drawn");

    volatile u8 *mem = (volatile u8 *)HID_SHM;
    u32 last_buttons = 0, touched = 0, moved = 0, replaced = 0;
    for (u32 frame = 0; frame < 150; frame++) {
        u32 pad_index = *(volatile u32 *)(mem + 0x10) & 7;
        volatile u8 *pad = mem + 0x28 + pad_index * 16;
        u32 buttons = *(volatile u32 *)pad;
        if ((buttons & ~last_buttons) & 1) print_hex("A pressed, frame", frame);
        if ((buttons & ~last_buttons) & 8) print_hex("START pressed, frame", frame);
        last_buttons = buttons;
        s32 cx = *(volatile short *)(pad + 12), cy = *(volatile short *)(pad + 14);
        if (!moved && (cx > 40 || cx < -40 || cy > 40 || cy < -40)) {
            moved = 1;
            print_hex(cx > 0 ? "circle right" : "circle left", (u32)(cx < 0 ? -cx : cx));
            print_hex(cy > 0 ? "circle up" : "circle down", (u32)(cy < 0 ? -cy : cy));
        }
        u32 touch_index = *(volatile u32 *)(mem + 0xB8) & 7;
        volatile u8 *touch = mem + 0xC8 + touch_index * 8;
        if (!touched && (*(volatile u32 *)(touch + 4) & 0x7F)) {
            touched = 1;
            print_hex("touch x", *(volatile unsigned short *)touch);
            print_hex("touch y", *(volatile unsigned short *)(touch + 2));
        }
        if (!replaced && marker != 0x4d483455u) {
            replaced = 1;
            print_hex("marker", marker);
        }
        svcSleepThread(16666667);
    }
    print("io_test: done");
    svcExitProcess();
}
