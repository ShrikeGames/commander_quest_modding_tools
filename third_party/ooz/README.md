# ooz (vendored)

Oodle Kraken decompressor from [powzix/ooz](https://github.com/powzix/ooz), **GPL-3.0**.
Commander Quest's pak is Kraken-compressed, and Oodle is statically linked into the
shipping binary, so a standalone decoder is required to read game assets.

Adapted for Linux:
- `kraken_lib.cpp` / `lzna_lib.cpp` / `bitknit_lib.cpp` — upstream sources with the
  Windows-only CLI tail (DLL loading, `main`, `QueryPerformanceCounter`) removed.
- `ooz_linux.h` — replaces upstream `stdafx.h`; shims `_BitScanReverse`,
  `_BitScanForward` and the `_byteswap_*` builtins. Do **not** define `_rotl`;
  GCC's `ia32intrin.h` already provides it.

Build with `make`. Only decompression is used — writing mod paks stores entries
uncompressed, so no Oodle *compressor* is ever needed.

Verified against all 7,133 compressed `.uasset` files in the game pak: every one
decodes to its exact recorded size with a valid UE package magic.
