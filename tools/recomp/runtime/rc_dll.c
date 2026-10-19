/* Glue for building recompiled code as a shared library (.so/.dll/.dylib).
 *
 * Compile together with the generated sources, with RC_PAGED and hidden visibility. The
 * host loads the library, looks up rc_code_init and passes its callbacks; the generated
 * code's calls to rc_call(), rc_svc(), ... land here and go through that table. */
#include "recomp.h"

#if defined(_WIN32)
#define RC_EXPORT __declspec(dllexport)
#else
#define RC_EXPORT __attribute__((visibility("default")))
#endif

extern const RFuncEntry rc_func_table[];
extern const u32 rc_func_count, rc_text_base, rc_text_size, rc_text_crc32;
extern const u64 rc_title_id;
extern const u32 rc_hook_addrs[], rc_hook_count;
extern const u32 rc_tunable_addrs[], rc_tunable_count;
extern const char *const rc_tunable_names[];

static RHost host;

/* Function lookup inside the library (open addressing, keys are addr | thumb bit); only
 * targets that aren't recompiled go to the host (which interprets them). */
typedef struct { u32 key; RFunc fn; } Slot;
static Slot *slots;
static u32 slot_mask;
static Slot slot_storage[1u << 17];   /* 131072 slots: plenty for ~40,000 functions */

static u32 slot_of(u32 key) { return (key * 0x9E3779B1u) >> 15 & slot_mask; }

static void build_slots(void) {
    slots = slot_storage;
    slot_mask = (1u << 17) - 1;
    for (u32 i = 0; i < rc_func_count; i++) {
        u32 key = rc_func_table[i].addr | rc_func_table[i].mode, h = slot_of(key);
        while (slots[h].fn) h = (h + 1) & slot_mask;
        slots[h].key = key;
        slots[h].fn = rc_func_table[i].fn;
    }
}

static RFunc lookup(u32 key) {
    for (u32 h = slot_of(key);; h = (h + 1) & slot_mask) {
        if (!slots[h].fn) return 0;
        if (slots[h].key == key) return slots[h].fn;
    }
}

void rc_call(RCtx *ctx, u32 target) {
    RFunc fn = lookup(target);
    if (fn) fn(ctx);
    else host.call(ctx, target);
}

void rc_call_ic(RCtx *ctx, u32 target, u32 *ic_target, RFunc *ic_fn) {
    RFunc fn = lookup(target);
    if (!fn) {
        host.call(ctx, target);
        return;
    }
    *ic_target = target;
    *ic_fn = fn;
    fn(ctx);
}
void rc_svc(RCtx *ctx, u32 imm, u32 next_pc) { host.svc(ctx, imm, next_pc); }
void rc_trap(RCtx *ctx, u32 pc, u32 kind) { host.trap(ctx, pc, kind); }
void rc_bad_return(RCtx *ctx, u32 got, u32 expected) { host.bad_return(ctx, got, expected); }
void rc_budget(RCtx *ctx) { host.budget(ctx); }
u32 rc_slow_read(RCtx *ctx, u32 a, u32 size) { return host.slow_read(ctx, a, size); }
void rc_slow_write(RCtx *ctx, u32 a, u32 v, u32 size) { host.slow_write(ctx, a, v, size); }

static int set_hook(u32 addr, RHookFn hook) {
    for (u32 i = 0; i < rc_hook_count; i++) {
        if (rc_hook_addrs[i] == addr) {
            rc_hooks[i] = hook;
            return 1;
        }
    }
    return 0;
}

#ifdef RC_CALL_COUNTS
static RCallCount *count_list;
void rc_count_first(RCallCount *c) { c->next = count_list; count_list = c; }

/* Diagnostic builds only: the functions that have run so far, as (address, calls) pairs in
 * out[0..2*max); returns how many there are (call with max 0 to size the buffer). */
RC_EXPORT u32 rc_call_counts(u32 *out, u32 max) {
    u32 n = 0;
    for (const RCallCount *c = count_list; c; c = c->next, n++) {
        if (n < max) {
            out[2 * n] = c->addr;
            out[2 * n + 1] = c->count;
        }
    }
    return n;
}
#endif

RC_EXPORT int rc_code_init(const RHost *h, RCodeInfo *info) {
    if (!h || h->version != RC_HOST_VERSION || h->ctx_size != sizeof(RCtx)) {
        return 0;
    }
    host = *h;
    if (rc_func_count > (1u << 16)) return 0;   /* hash table too small */
    build_slots();
    info->table = rc_func_table;
    info->count = rc_func_count;
    info->text_base = rc_text_base;
    info->text_size = rc_text_size;
    info->text_crc32 = rc_text_crc32;
    info->title_id = rc_title_id;
    info->hook_addrs = rc_hook_addrs;
    info->hook_count = rc_hook_count;
    info->set_hook = set_hook;
    info->tunables = rc_tunables;
    info->tunable_addrs = rc_tunable_addrs;
    info->tunable_names = rc_tunable_names;
    info->tunable_count = rc_tunable_count;
    return 1;
}
