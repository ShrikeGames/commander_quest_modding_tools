/* Fast scanning primitives over another process's memory.
 *
 * Walking Unreal's reflection data needs a handful of whole-address-space
 * searches. Those are far too slow in Python (minutes over ~3 GB), while the
 * structure walking that follows touches only a few bytes at a time and is
 * better expressed there. So this tool provides just the scans, and prints
 * matching addresses one per line.
 *
 *   scan bytes  PID HEX        addresses whose bytes match HEX
 *   scan ptr    PID LO HI      addresses of 8-byte values inside [LO,HI)
 *   scan dword  PID VALUE      addresses of 4-byte values equal to VALUE
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <stdint.h>

#define CHUNK (64u << 20)
#define MAX_HITS 200000

static int hexbyte(const char *s) {
    int hi = -1, lo = -1;
    if (s[0] >= '0' && s[0] <= '9') hi = s[0] - '0';
    else if ((s[0] | 32) >= 'a' && (s[0] | 32) <= 'f') hi = (s[0] | 32) - 'a' + 10;
    if (s[1] >= '0' && s[1] <= '9') lo = s[1] - '0';
    else if ((s[1] | 32) >= 'a' && (s[1] | 32) <= 'f') lo = (s[1] | 32) - 'a' + 10;
    return (hi < 0 || lo < 0) ? -1 : (hi << 4) | lo;
}

int main(int argc, char **argv) {
    if (argc < 4) { fprintf(stderr, "usage: scan {bytes|ptr|dword} PID ...\n"); return 2; }
    const char *mode = argv[1];
    int pid = atoi(argv[2]);

    unsigned char pat[256]; int patlen = 0;
    uint64_t lo = 0, hi = 0; uint32_t want = 0;
    if (!strcmp(mode, "bytes")) {
        const char *h = argv[3];
        for (int i = 0; h[i] && h[i + 1]; i += 2) {
            int b = hexbyte(h + i);
            if (b < 0) { fprintf(stderr, "bad hex\n"); return 2; }
            pat[patlen++] = (unsigned char)b;
        }
    } else if (!strcmp(mode, "ptr")) {
        if (argc < 5) return 2;
        lo = strtoull(argv[3], 0, 0); hi = strtoull(argv[4], 0, 0);
    } else if (!strcmp(mode, "dword")) {
        want = (uint32_t)strtoul(argv[3], 0, 0);
    } else { fprintf(stderr, "unknown mode\n"); return 2; }

    char p[64];
    snprintf(p, sizeof p, "/proc/%d/maps", pid);
    FILE *m = fopen(p, "r");
    if (!m) { perror("maps"); return 3; }
    snprintf(p, sizeof p, "/proc/%d/mem", pid);
    int fd = open(p, O_RDONLY);
    if (fd < 0) { perror("mem"); return 3; }

    unsigned char *buf = malloc(CHUNK);
    char line[1024];
    long hits = 0;
    while (fgets(line, sizeof line, m)) {
        unsigned long a, b; char perms[8], path[512]; path[0] = 0;
        if (sscanf(line, "%lx-%lx %7s %*s %*s %*s %511[^\n]", &a, &b, perms, path) < 3) continue;
        if (perms[0] != 'r' || strstr(path, ".pak") || strstr(path, "/dev/")) continue;
        unsigned long size = b - a;
        for (unsigned long off = 0; off < size;) {
            unsigned long want_n = size - off; if (want_n > CHUNK) want_n = CHUNK;
            ssize_t got = pread(fd, buf, want_n, a + off);
            if (got <= 0) break;
            if (!strcmp(mode, "bytes")) {
                unsigned char *q = buf, *end = buf + got;
                while (patlen && (q = memmem(q, end - q, pat, patlen))) {
                    printf("%lx\n", a + off + (unsigned long)(q - buf));
                    if (++hits >= MAX_HITS) goto done;
                    q++;
                }
            } else if (!strcmp(mode, "ptr")) {
                for (ssize_t i = 0; i + 8 <= got; i += 8) {
                    uint64_t v; memcpy(&v, buf + i, 8);
                    if (v >= lo && v < hi) {
                        printf("%lx\n", a + off + (unsigned long)i);
                        if (++hits >= MAX_HITS) goto done;
                    }
                }
            } else {
                for (ssize_t i = 0; i + 4 <= got; i += 4) {
                    uint32_t v; memcpy(&v, buf + i, 4);
                    if (v == want) {
                        printf("%lx\n", a + off + (unsigned long)i);
                        if (++hits >= MAX_HITS) goto done;
                    }
                }
            }
            if ((unsigned long)got < want_n) break;
            off += want_n > 16 ? want_n - 16 : want_n;
        }
    }
done:
    fflush(stdout);
    fprintf(stderr, "hits: %ld\n", hits);
    return hits ? 0 : 1;
}
