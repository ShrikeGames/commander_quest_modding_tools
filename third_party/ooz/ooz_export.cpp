/* A stable C entry point for the Kraken decoder.
 *
 * Upstream declares Kraken_Decompress as a plain C++ function, so the symbol
 * that lands in the library is a mangled name. That name is not portable: it
 * encodes the size_t width, which differs between 64-bit Linux and 64-bit
 * MinGW, and it differs again under MSVC. Looking it up by hand therefore
 * breaks the moment the library is built anywhere other than where it started.
 *
 * This wrapper exports one unmangled name that every platform can dlopen the
 * same way, which is what lets a packaged build ship a prebuilt decoder.
 */
#include <stddef.h>

int Kraken_Decompress(const unsigned char *src, size_t src_len,
                      unsigned char *dst, size_t dst_len);

extern "C"
#ifdef _WIN32
__declspec(dllexport)
#endif
int ooz_kraken_decompress(const unsigned char *src, size_t src_len,
                          unsigned char *dst, size_t dst_len) {
    return Kraken_Decompress(src, src_len, dst, dst_len);
}
