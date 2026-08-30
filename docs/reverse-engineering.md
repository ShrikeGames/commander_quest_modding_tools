# Reverse engineering notes

How the format was worked out, and why some things are the way they are. Useful
if the game updates and something stops working.

## Recovering the pak key

The archive footer reports `bEncryptedIndex = 1` with an all-zero
`EncryptionKeyGuid`, meaning the default key baked into the build. The index's
first bytes are high-entropy, confirming encryption; `IndexSize` is a multiple of
16, consistent with AES.

The index decrypts to an `FString` mount point, so a correct key produces a small
`int32` length in the first four plaintext bytes. That gives a cheap filter, and
the footer's SHA-1 of the decrypted index gives an exact confirmation. Together
they allow an exhaustive search with no false positives.

Searching the shipped binaries found **nothing**:

| scanned | candidates confirmed | result |
|---|---|---|
| `CommanderGame-Win64-Shipping.exe`, 150,916,096 bytes, every byte offset | 224 | none |
| base64-encoded forms (how `Crypto.json` stores it) | 44 | none |
| every bundled DLL (D3D12, DirectML, Aftermath, MsQuic) | 63 | none |

So **the key is assembled at runtime rather than stored**. Reading the live
process found it immediately, at `0x34ab110` in anonymous memory, within the
first 51 MB. That is what `tools/find_aes_key.py` automates.

Practical consequence: if the key ever needs re-deriving after a patch, go
straight to the live-process scan. Do not repeat the static passes.

## Why the vendored decoder is trustworthy

`powzix/ooz` had not been updated in seven years, which is a fair thing to be
suspicious of. Three lines of evidence settled it.

**The codec is Kraken, exclusively.** Sampling 4,000 compressed entries: 3,993
Kraken with no checksums, and 7 header reads that failed for unrelated reasons.
Not Leviathan, not Hydra, nothing recent. Kraken's bitstream dates to 2016 and is
frozen. It has to be, since every game shipped with it must stay decodable. A
decoder frozen for seven years is targeting a format frozen for nine.

**The bundled reference vectors decode.** `dickens.kraken` produces 10,192,446
bytes of clean Project Gutenberg text; `mozilla.kraken` produces 51,220,480
bytes. Both exact.

**Every compressed asset in the game decodes.** All **7,133** compressed
`.uasset` files decode to their exact recorded byte count with a valid package
magic. Zero failures.

## Building ooz on Linux

Upstream is a Visual Studio project. Two changes are needed, both expressed as
Makefile steps so the vendored sources stay pristine:

1. Cut `kraken.cpp` before `typedef int WINAPI OodLZ_CompressFunc(`. Everything
   after it is the Windows CLI, DLL loading and `main`.
2. Replace `stdafx.h` with `ooz_linux.h`, which supplies the typedefs and shims
   `_BitScanReverse`, `_BitScanForward` and the `_byteswap_*` builtins.

**Do not define `_rotl`.** GCC's `ia32intrin.h` already provides it, and
redefining it produces a cascade of errors that look like the source is broken.
All the `_mm_*` SSE intrinsics compile natively.

## Measuring table strides

Import and export strides were derived from the summary's own offsets rather
than assumed from struct layouts, which vary between engine versions:

```
imports: (ExportOffset  - ImportOffset) / ImportCount = (1605 - 1189) / 13 = 32
exports: (DependsOffset - ExportOffset) / ExportCount = (1893 - 1605) /  3 = 96
```

An earlier guess of 100 bytes for exports parsed the first export correctly and
turned the rest into nonsense, which is a failure mode worth recognising.

## Two mistakes worth remembering

**Effect order does not follow description text.** Insight reads *"Draw 1 card.
Send 1 card…"*, so its two `CMEffectData_MoveCard` exports were assumed to be
draw-then-send. They are the reverse: offset 104 is *send*, offset 119 is *draw*.
Acting on the assumption produced a card that discarded the player's hand. Verify
in-game before building on an inference.

**`FString` positive lengths are ANSI, not UTF-8.** Writing UTF-8 with a positive
length would have corrupted 52 unrelated strings. The symptom was the resource
coming out 15 KB smaller than the original. See
[localization](formats/locres.md).

## What the name pool showed

An exploratory memory scan decoded the game's `FName` pool, where the card class
family is plainly visible: `CMCardData` with `_Curse`, `_Power`, `_Summon`,
`_Supply` and `_Tactics`, alongside function names like `GetDisplayUseCost` and
`GetCardRarity`.

Tempting, but not sufficient: the pool is a flat list of strings. It does not say
which names are *properties*, belonging to which class, in what order, with what
types. That needs the `UClass`/`FProperty` object graph, which is the
[`.usmap` problem](formats/unversioned.md).
