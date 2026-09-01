/* Recover a UE pak's index AES key from a live process.
 *
 * The key is assembled at runtime, so it is not present in the shipped
 * binaries and has to be read out of memory while the game runs. Every 32-byte
 * window is tried as a key against the index's first ciphertext block; a
 * correct key decrypts it to an FString mount point whose leading int32 is a
 * small length, which narrows billions of windows to a handful of candidates.
 *
 * Candidates are printed as they are found and confirmed by the caller, which
 * decrypts the whole index and compares SHA-1 with the hash in the pak footer.
 * Splitting it that way keeps this program free of any crypto library: the hot
 * filter needs one AES block decrypt, which is small enough to carry inline,
 * and the confirmation runs once per candidate where a library is no burden.
 *
 * Being dependency-free is what makes it portable. It builds with any C
 * compiler on Linux and Windows, and packaged builds ship it prebuilt so a user
 * never needs a toolchain.
 */
#ifdef _WIN32
/* MinGW defaults to the MSVC C runtime's printf, which does not understand
 * %llx. Asking for the ANSI implementation makes the format strings below mean
 * the same thing on both platforms. */
#define __USE_MINGW_ANSI_STDIO 1
#else
/* Must precede every system header, or pread is not declared. */
#define _GNU_SOURCE
#endif

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#ifdef _WIN32
#include <windows.h>
#include <psapi.h>
#else
#include <fcntl.h>
#include <unistd.h>
#endif

/* ------------------------------------------------------------------ AES-256 */

static const unsigned char SBOX[256] = {
0x63,0x7c,0x77,0x7b,0xf2,0x6b,0x6f,0xc5,0x30,0x01,0x67,0x2b,0xfe,0xd7,0xab,0x76,
0xca,0x82,0xc9,0x7d,0xfa,0x59,0x47,0xf0,0xad,0xd4,0xa2,0xaf,0x9c,0xa4,0x72,0xc0,
0xb7,0xfd,0x93,0x26,0x36,0x3f,0xf7,0xcc,0x34,0xa5,0xe5,0xf1,0x71,0xd8,0x31,0x15,
0x04,0xc7,0x23,0xc3,0x18,0x96,0x05,0x9a,0x07,0x12,0x80,0xe2,0xeb,0x27,0xb2,0x75,
0x09,0x83,0x2c,0x1a,0x1b,0x6e,0x5a,0xa0,0x52,0x3b,0xd6,0xb3,0x29,0xe3,0x2f,0x84,
0x53,0xd1,0x00,0xed,0x20,0xfc,0xb1,0x5b,0x6a,0xcb,0xbe,0x39,0x4a,0x4c,0x58,0xcf,
0xd0,0xef,0xaa,0xfb,0x43,0x4d,0x33,0x85,0x45,0xf9,0x02,0x7f,0x50,0x3c,0x9f,0xa8,
0x51,0xa3,0x40,0x8f,0x92,0x9d,0x38,0xf5,0xbc,0xb6,0xda,0x21,0x10,0xff,0xf3,0xd2,
0xcd,0x0c,0x13,0xec,0x5f,0x97,0x44,0x17,0xc4,0xa7,0x7e,0x3d,0x64,0x5d,0x19,0x73,
0x60,0x81,0x4f,0xdc,0x22,0x2a,0x90,0x88,0x46,0xee,0xb8,0x14,0xde,0x5e,0x0b,0xdb,
0xe0,0x32,0x3a,0x0a,0x49,0x06,0x24,0x5c,0xc2,0xd3,0xac,0x62,0x91,0x95,0xe4,0x79,
0xe7,0xc8,0x37,0x6d,0x8d,0xd5,0x4e,0xa9,0x6c,0x56,0xf4,0xea,0x65,0x7a,0xae,0x08,
0xba,0x78,0x25,0x2e,0x1c,0xa6,0xb4,0xc6,0xe8,0xdd,0x74,0x1f,0x4b,0xbd,0x8b,0x8a,
0x70,0x3e,0xb5,0x66,0x48,0x03,0xf6,0x0e,0x61,0x35,0x57,0xb9,0x86,0xc1,0x1d,0x9e,
0xe1,0xf8,0x98,0x11,0x69,0xd9,0x8e,0x94,0x9b,0x1e,0x87,0xe9,0xce,0x55,0x28,0xdf,
0x8c,0xa1,0x89,0x0d,0xbf,0xe6,0x42,0x68,0x41,0x99,0x2d,0x0f,0xb0,0x54,0xbb,0x16};

static unsigned char RSBOX[256];
static uint32_t TD0[256], TD1[256], TD2[256], TD3[256];
static uint32_t TE[256];

static unsigned char xtime(unsigned char x) {
    return (unsigned char)((x << 1) ^ ((x & 0x80) ? 0x1b : 0));
}

static unsigned char gmul(unsigned char x, unsigned char y) {
    unsigned char r = 0;
    while (y) {
        if (y & 1) r ^= x;
        x = xtime(x);
        y >>= 1;
    }
    return r;
}

/* Build the tables for the equivalent inverse cipher.
 *
 * A scan expands a fresh key for every candidate window and then decrypts a
 * single block, so the usual balance is inverted: key setup costs as much as
 * the cipher and neither can be amortised. The equivalent inverse cipher folds
 * InvSubBytes, InvShiftRows and InvMixColumns into one table lookup per state
 * byte, which is the cheapest way to run fourteen rounds once.
 */
static void build_tables(void) {
    for (int i = 0; i < 256; i++) RSBOX[SBOX[i]] = (unsigned char)i;
    for (int i = 0; i < 256; i++) {
        unsigned char x = RSBOX[i];
        uint32_t w = ((uint32_t)gmul(x, 14) << 24) | ((uint32_t)gmul(x, 9) << 16)
                   | ((uint32_t)gmul(x, 13) << 8) | (uint32_t)gmul(x, 11);
        TD0[i] = w;
        TD1[i] = (w >> 8) | (w << 24);
        TD2[i] = (w >> 16) | (w << 16);
        TD3[i] = (w >> 24) | (w << 8);
        TE[i] = (uint32_t)SBOX[i];
    }
}

#define GETU32(p) (((uint32_t)(p)[0] << 24) | ((uint32_t)(p)[1] << 16) | \
                   ((uint32_t)(p)[2] << 8) | (uint32_t)(p)[3])

/* InvMixColumns on one round-key word, used to build the decryption schedule. */
static uint32_t inv_mix(uint32_t w) {
    unsigned char a0 = (unsigned char)(w >> 24), a1 = (unsigned char)(w >> 16),
                  a2 = (unsigned char)(w >> 8),  a3 = (unsigned char)w;
    return ((uint32_t)(gmul(a0,14) ^ gmul(a1,11) ^ gmul(a2,13) ^ gmul(a3,9)) << 24)
         | ((uint32_t)(gmul(a0,9)  ^ gmul(a1,14) ^ gmul(a2,11) ^ gmul(a3,13)) << 16)
         | ((uint32_t)(gmul(a0,13) ^ gmul(a1,9)  ^ gmul(a2,14) ^ gmul(a3,11)) << 8)
         | ((uint32_t)(gmul(a0,11) ^ gmul(a1,13) ^ gmul(a2,9)  ^ gmul(a3,14)));
}

static uint32_t INVMIX[256][4];

/* Precompute InvMixColumns per byte position so the key schedule transform is
 * four table lookups a word instead of sixteen field multiplications. */
static void build_invmix(void) {
    for (int i = 0; i < 256; i++) {
        INVMIX[i][0] = inv_mix((uint32_t)i << 24);
        INVMIX[i][1] = inv_mix((uint32_t)i << 16);
        INVMIX[i][2] = inv_mix((uint32_t)i << 8);
        INVMIX[i][3] = inv_mix((uint32_t)i);
    }
}

static uint32_t inv_mix_fast(uint32_t w) {
    return INVMIX[(unsigned char)(w >> 24)][0] ^ INVMIX[(unsigned char)(w >> 16)][1]
         ^ INVMIX[(unsigned char)(w >> 8)][2]  ^ INVMIX[(unsigned char)w][3];
}

/* Build the AES-256 decryption round keys for one candidate window. */
static void expand_decrypt_key(const unsigned char *key, uint32_t rk[60]) {
    uint32_t w[60];
    for (int i = 0; i < 8; i++) w[i] = GETU32(key + 4 * i);
    unsigned char rcon = 1;
    for (int i = 8; i < 60; i++) {
        uint32_t t = w[i - 1];
        if (i % 8 == 0) {
            t = (t << 8) | (t >> 24);
            t = (TE[(unsigned char)(t >> 24)] << 24) | (TE[(unsigned char)(t >> 16)] << 16)
              | (TE[(unsigned char)(t >> 8)] << 8) | TE[(unsigned char)t];
            t ^= (uint32_t)rcon << 24;
            rcon = (unsigned char)((rcon << 1) ^ ((rcon & 0x80) ? 0x1b : 0));
        } else if (i % 8 == 4) {
            t = (TE[(unsigned char)(t >> 24)] << 24) | (TE[(unsigned char)(t >> 16)] << 16)
              | (TE[(unsigned char)(t >> 8)] << 8) | TE[(unsigned char)t];
        }
        w[i] = w[i - 8] ^ t;
    }
    /* Reverse into decryption order, then fold InvMixColumns into the middle
     * rounds so each round is a plain table lookup and XOR. */
    for (int r = 0; r < 15; r++)
        for (int c = 0; c < 4; c++) rk[4 * r + c] = w[4 * (14 - r) + c];
    for (int i = 4; i < 56; i++) rk[i] = inv_mix_fast(rk[i]);
}

/* Decrypt one 16-byte block with AES-256 in ECB mode. */
static void aes256_decrypt_block(const uint32_t *rk, const unsigned char *in,
                                 unsigned char *out) {
    uint32_t s0 = GETU32(in)      ^ rk[0], s1 = GETU32(in + 4)  ^ rk[1],
             s2 = GETU32(in + 8)  ^ rk[2], s3 = GETU32(in + 12) ^ rk[3];
    uint32_t t0, t1, t2, t3;
    const uint32_t *k = rk + 4;
    for (int r = 0; r < 13; r++) {
        t0 = TD0[(unsigned char)(s0 >> 24)] ^ TD1[(unsigned char)(s3 >> 16)]
           ^ TD2[(unsigned char)(s2 >> 8)]  ^ TD3[(unsigned char)s1] ^ k[0];
        t1 = TD0[(unsigned char)(s1 >> 24)] ^ TD1[(unsigned char)(s0 >> 16)]
           ^ TD2[(unsigned char)(s3 >> 8)]  ^ TD3[(unsigned char)s2] ^ k[1];
        t2 = TD0[(unsigned char)(s2 >> 24)] ^ TD1[(unsigned char)(s1 >> 16)]
           ^ TD2[(unsigned char)(s0 >> 8)]  ^ TD3[(unsigned char)s3] ^ k[2];
        t3 = TD0[(unsigned char)(s3 >> 24)] ^ TD1[(unsigned char)(s2 >> 16)]
           ^ TD2[(unsigned char)(s1 >> 8)]  ^ TD3[(unsigned char)s0] ^ k[3];
        s0 = t0; s1 = t1; s2 = t2; s3 = t3; k += 4;
    }
    /* Final round substitutes without mixing. */
    uint32_t r0 = ((uint32_t)RSBOX[(unsigned char)(s0 >> 24)] << 24)
                | ((uint32_t)RSBOX[(unsigned char)(s3 >> 16)] << 16)
                | ((uint32_t)RSBOX[(unsigned char)(s2 >> 8)] << 8)
                | (uint32_t)RSBOX[(unsigned char)s1];
    r0 ^= k[0];
    out[0] = (unsigned char)(r0 >> 24); out[1] = (unsigned char)(r0 >> 16);
    out[2] = (unsigned char)(r0 >> 8);  out[3] = (unsigned char)r0;
    /* Only the first four plaintext bytes are inspected by the filter, so the
     * remaining three columns of the last round are not computed. */
}

/* ------------------------------------------------------------------ scanning */

static unsigned char g_ct[16];
static long long g_candidates;

/* Report every window of a buffer that decrypts the index head plausibly.
 *
 * A UE pak index begins with an FString mount point, so the first four
 * plaintext bytes are a length: small and positive for ANSI, small and
 * negative for UTF-16. Anything else cannot be the key.
 */
static void scan(const unsigned char *buf, long n, unsigned long long base,
                 int step) {
    uint32_t rk[60];
    unsigned char pt[4];
    for (long o = 0; o + 32 <= n; o += step) {
        expand_decrypt_key(buf + o, rk);
        aes256_decrypt_block(rk, g_ct, pt);
        int32_t len = (int32_t)((uint32_t)pt[0] | ((uint32_t)pt[1] << 8) |
                                ((uint32_t)pt[2] << 16) | ((uint32_t)pt[3] << 24));
        if (!((len >= 1 && len <= 4096) || (len <= -1 && len >= -4096))) continue;
        g_candidates++;
        printf("CANDIDATE=");
        for (int i = 0; i < 32; i++) printf("%02X", buf[o + i]);
        printf(" ADDR=0x%llx\n", base + (unsigned long long)o);
        fflush(stdout);
    }
}

#define CHUNK (64u << 20)

#ifdef _WIN32
/* Walk a process's committed, readable, private memory.
 *
 * Only private read-write pages are worth reading. Mapped images and file
 * views are the shipped binaries, which by definition do not hold a key that
 * is assembled at runtime, and skipping them removes most of the address
 * space.
 */
static int scan_process(unsigned long pid, int step) {
    HANDLE h = OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ,
                           FALSE, (DWORD)pid);
    if (!h) {
        fprintf(stderr, "cannot open process %lu (run as administrator?)\n", pid);
        return 3;
    }
    unsigned char *buf = (unsigned char *)malloc(CHUNK);
    if (!buf) { CloseHandle(h); return 2; }
    MEMORY_BASIC_INFORMATION mbi;
    unsigned char *addr = 0;
    long long scanned = 0;
    while (VirtualQueryEx(h, addr, &mbi, sizeof mbi) == sizeof mbi) {
        unsigned char *next = (unsigned char *)mbi.BaseAddress + mbi.RegionSize;
        int readable = mbi.State == MEM_COMMIT
            && mbi.Type == MEM_PRIVATE
            && !(mbi.Protect & (PAGE_NOACCESS | PAGE_GUARD))
            && (mbi.Protect & (PAGE_READWRITE | PAGE_READONLY
                               | PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE));
        if (readable && mbi.RegionSize >= 32) {
            size_t size = mbi.RegionSize;
            for (size_t o = 0; o < size;) {
                size_t chunk = size - o < CHUNK ? size - o : CHUNK;
                SIZE_T got = 0;
                if (!ReadProcessMemory(h, (unsigned char *)mbi.BaseAddress + o,
                                       buf, chunk, &got) || got < 32) break;
                scanned += (long long)got;
                scan(buf, (long)got, (unsigned long long)(size_t)mbi.BaseAddress + o,
                     step);
                if (got < chunk) break;
                o += chunk > 31 ? chunk - 31 : chunk;
            }
        }
        if (next <= addr) break;
        addr = next;
    }
    free(buf);
    CloseHandle(h);
    fprintf(stderr, "scanned %lld MB, %lld candidates\n",
            scanned >> 20, g_candidates);
    return 0;
}
#else
static int scan_process(unsigned long pid, int step) {
    char p[64];
    snprintf(p, sizeof p, "/proc/%lu/maps", pid);
    FILE *m = fopen(p, "r");
    if (!m) { perror("maps"); return 2; }
    snprintf(p, sizeof p, "/proc/%lu/mem", pid);
    int fd = open(p, O_RDONLY);
    if (fd < 0) { perror("mem"); fclose(m); return 3; }

    char line[1024];
    unsigned char *buf = (unsigned char *)malloc(CHUNK);
    long long scanned = 0;
    while (fgets(line, sizeof line, m)) {
        unsigned long lo, hi; char perms[8], path[512]; path[0] = 0;
        if (sscanf(line, "%lx-%lx %7s %*s %*s %*s %511[^\n]",
                   &lo, &hi, perms, path) < 3) continue;
        if (perms[0] != 'r' || strstr(path, ".pak") || strstr(path, "/dev/")) continue;
        unsigned long size = hi - lo;
        if (size < 32) continue;
        for (unsigned long o = 0; o < size;) {
            unsigned long chunk = size - o < CHUNK ? size - o : CHUNK;
            long got = (long)pread(fd, buf, chunk, (off_t)(lo + o));
            if (got <= 0) break;
            scanned += got;
            scan(buf, got, lo + o, step);
            if ((unsigned long)got < chunk) break;
            o += chunk > 31 ? chunk - 31 : chunk;
        }
    }
    free(buf);
    close(fd); fclose(m);
    fprintf(stderr, "scanned %lld MB, %lld candidates\n",
            scanned >> 20, g_candidates);
    return 0;
}
#endif

/* Scan a file instead of a process, which is how the scanner is tested.
 *
 * A live game is not available in a test, so the self-test writes a buffer of
 * random bytes with a known key planted in it and checks that the key comes
 * back. That exercises the same filter and the same AES the real scan uses.
 */
static int scan_file(const char *path, int step) {
    FILE *f = fopen(path, "rb");
    if (!f) { perror("file"); return 2; }
    unsigned char *buf = (unsigned char *)malloc(CHUNK);
    unsigned long long base = 0;
    for (;;) {
        size_t got = fread(buf, 1, CHUNK, f);
        if (got < 32) break;
        scan(buf, (long)got, base, step);
        if (got < CHUNK) break;
        if (fseek(f, -31, SEEK_CUR)) break;
        base += got - 31;
    }
    free(buf); fclose(f);
    fprintf(stderr, "scanned file, %lld candidates\n", g_candidates);
    return 0;
}

static void usage(void) {
    fprintf(stderr,
        "usage: aes_finder (--pid PID | --file PATH) --block HEX32 [--step N]\n"
        "  --block  the index's first 16 ciphertext bytes, as 32 hex digits\n"
        "  --step   window stride; 1 scans every offset, 8 only aligned ones\n");
}

int main(int argc, char **argv) {
    const char *file = NULL, *block = NULL;
    unsigned long pid = 0;
    int step = 1;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--pid") && i + 1 < argc) pid = strtoul(argv[++i], 0, 0);
        else if (!strcmp(argv[i], "--file") && i + 1 < argc) file = argv[++i];
        else if (!strcmp(argv[i], "--block") && i + 1 < argc) block = argv[++i];
        else if (!strcmp(argv[i], "--step") && i + 1 < argc) step = atoi(argv[++i]);
        else { usage(); return 2; }
    }
    if (!block || (!pid && !file) || step < 1) { usage(); return 2; }
    if (strlen(block) != 32) {
        fprintf(stderr, "--block must be 32 hex digits (16 bytes)\n");
        return 2;
    }
    for (int i = 0; i < 16; i++) {
        unsigned v;
        if (sscanf(block + 2 * i, "%2x", &v) != 1) {
            fprintf(stderr, "--block is not hex\n");
            return 2;
        }
        g_ct[i] = (unsigned char)v;
    }
    build_tables();
    build_invmix();
    return file ? scan_file(file, step) : scan_process(pid, step);
}
