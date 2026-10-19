/* Basic fixed-width types used across the decompilation. ARM11 (ARMv6K), little endian. */
#ifndef MH4U_TYPES_H
#define MH4U_TYPES_H

typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned long long u64;
typedef signed char s8;
typedef signed short s16;
typedef signed int s32;
typedef signed long long s64;
typedef float f32;
typedef double f64;

#ifndef NULL
#define NULL ((void *)0)
#endif

#endif
