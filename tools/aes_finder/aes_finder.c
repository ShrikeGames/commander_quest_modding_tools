/* Recover a UE pak's index AES key from a live process.
 *
 * The key is assembled at runtime, so it is not present in the shipped
 * binaries. Every 32-byte window of readable memory is tried as a key; a cheap
 * filter (a correct key decrypts the index's first block to an FString mount
 * point, whose leading int32 is a small length) narrows to a handful of
 * candidates, each then confirmed by decrypting the entire index and comparing
 * SHA-1 with the hash stored in the pak footer. A reported key is proven.
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <openssl/aes.h>
#include <openssl/evp.h>
#include <openssl/sha.h>

static unsigned char *g_index, g_want[20], *g_scratch, g_ct[16];
static long g_index_len;

static int confirm(const unsigned char *key) {
    EVP_CIPHER_CTX *c = EVP_CIPHER_CTX_new();
    EVP_DecryptInit_ex(c, EVP_aes_256_ecb(), NULL, key, NULL);
    EVP_CIPHER_CTX_set_padding(c, 0);
    int l1 = 0, l2 = 0;
    EVP_DecryptUpdate(c, g_scratch, &l1, g_index, (int)g_index_len);
    EVP_DecryptFinal_ex(c, g_scratch + l1, &l2);
    EVP_CIPHER_CTX_free(c);
    unsigned char h[20];
    SHA1(g_scratch, l1 + l2, h);
    return memcmp(h, g_want, 20) == 0;
}

static int scan(unsigned char *buf, long n, unsigned long base, long *checked) {
    AES_KEY k; unsigned char pt[16];
    for (long o = 0; o + 32 <= n; o++) {
        AES_set_decrypt_key(buf + o, 256, &k);
        AES_decrypt(g_ct, pt, &k);
        int len = (int)((unsigned)pt[0] | ((unsigned)pt[1] << 8) |
                        ((unsigned)pt[2] << 16) | ((unsigned)pt[3] << 24));
        if (!((len >= 1 && len <= 4096) || (len <= -1 && len >= -4096))) continue;
        (*checked)++;
        if (confirm(buf + o)) {
            printf("KEY=");
            for (int i = 0; i < 32; i++) printf("%02X", buf[o + i]);
            printf("\nADDR=0x%lx\n", base + o);
            return 1;
        }
    }
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 5) {
        fprintf(stderr, "usage: aes_finder PID PAK INDEX_OFFSET INDEX_SIZE INDEX_SHA1\n");
        return 2;
    }
    int pid = atoi(argv[1]);
    long long ioff = strtoll(argv[3], 0, 0);
    g_index_len = atol(argv[4]);
    for (int i = 0; i < 20; i++) { unsigned v; sscanf(argv[5] + 2 * i, "%2x", &v); g_want[i] = v; }

    FILE *pf = fopen(argv[2], "rb");
    if (!pf) { perror("pak"); return 2; }
    g_index = malloc(g_index_len); g_scratch = malloc(g_index_len + 64);
    if (fseek(pf, ioff, SEEK_SET) || fread(g_index, 1, g_index_len, pf) != (size_t)g_index_len) {
        perror("read index"); return 2;
    }
    fclose(pf);
    memcpy(g_ct, g_index, 16);

    char p[64]; snprintf(p, sizeof p, "/proc/%d/maps", pid);
    FILE *m = fopen(p, "r");
    if (!m) { perror("maps"); return 2; }
    snprintf(p, sizeof p, "/proc/%d/mem", pid);
    int fd = open(p, O_RDONLY);
    if (fd < 0) { perror("mem"); return 3; }

    char line[1024];
    unsigned char *buf = malloc(64u << 20);
    long long scanned = 0, checked = 0;
    while (fgets(line, sizeof line, m)) {
        unsigned long lo, hi; char perms[8], path[512]; path[0] = 0;
        if (sscanf(line, "%lx-%lx %7s %*s %*s %*s %511[^\n]", &lo, &hi, perms, path) < 3) continue;
        if (perms[0] != 'r' || strstr(path, ".pak") || strstr(path, "/dev/")) continue;
        unsigned long size = hi - lo;
        if (size < 32) continue;
        for (unsigned long o = 0; o < size;) {
            unsigned long chunk = size - o; if (chunk > (64ul << 20)) chunk = 64ul << 20;
            ssize_t got = pread(fd, buf, chunk, lo + o);
            if (got <= 0) break;
            scanned += got;
            if (scan(buf, got, lo + o, &checked)) {
                fprintf(stderr, "scanned %lld MB, %lld candidates confirmed\n", scanned >> 20, checked);
                return 0;
            }
            if ((unsigned long)got < chunk) break;
            o += chunk > 31 ? chunk - 31 : chunk;
        }
    }
    fprintf(stderr, "no key found (scanned %lld MB, %lld candidates)\n", scanned >> 20, checked);
    return 1;
}
