// Linux/GCC replacement for upstream ooz's stdafx.h.
#pragma once
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include <stdint.h>
#include <x86intrin.h>

typedef unsigned char byte;
typedef unsigned char uint8;
typedef unsigned int uint32;
typedef uint64_t uint64;
typedef int64_t int64;
typedef signed int int32;
typedef unsigned short uint16;
typedef signed short int16;
typedef unsigned int uint;
#define __int64 int64_t
#define __forceinline inline __attribute__((always_inline))

// NOTE: do not define _rotl here - GCC's ia32intrin.h already provides it,
// and redefining it breaks the build with a confusing cascade of errors.
static inline unsigned char _BitScanReverse(unsigned long *idx, unsigned int mask){
  if(!mask) return 0; *idx = 31 - __builtin_clz(mask); return 1;
}
static inline unsigned char _BitScanForward(unsigned long *idx, unsigned int mask){
  if(!mask) return 0; *idx = __builtin_ctz(mask); return 1;
}
static inline unsigned int   _byteswap_ulong (unsigned int v){ return __builtin_bswap32(v); }
static inline unsigned short _byteswap_ushort(unsigned short v){ return __builtin_bswap16(v); }
static inline uint64_t       _byteswap_uint64(uint64_t v){ return __builtin_bswap64(v); }
