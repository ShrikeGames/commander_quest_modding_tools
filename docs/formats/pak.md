# Pak archives (version 11)

Implemented in [`cqmod/pak.py`](../../cqmod/pak.py).

## Commander Quest's archive

| | |
|---|---|
| File | `Commander/Content/Paks/Commander-Windows.pak` |
| Size | 3,533,079,776 bytes |
| Version | 11 |
| Files | 27,932 |
| Mount point | `../../../` |
| Index | AES-256-ECB encrypted, `EncryptionKeyGuid` all zero |
| Compression | Oodle **Kraken**, no checksums |
| Entry data | not encrypted; only 71 of 27,932 entries set the per-entry flag |
| Signing | none, no `.sig` present |

Note that the *index* is encrypted but the *data* is not. That distinction
matters: it means only the file listing is protected, and once the index is
decrypted the assets read normally.

## Layout

```
[ entry header + data ] x N
[ primary index ]
[ full directory index ]
[ 221-byte footer ]
```

The footer is fixed-size, so it is read from the end of the file and the magic
`0x5A6F12E1` located by search:

```
Guid    EncryptionKeyGuid   16   zero means the default key
uint8   bEncryptedIndex      1
uint32  Magic                4   0x5A6F12E1
int32   Version              4   11
int64   IndexOffset          8
int64   IndexSize            8
uint8   IndexHash[20]       20   SHA-1 of the decrypted primary index
char    CompressionNames[5][32]  160
```

## Primary index

```
FString MountPoint
int32   NumEntries
uint64  PathHashSeed
int32   bHasPathHashIndex   (+ offset, size, hash if set)
int32   bHasFullDirectoryIndex (+ offset, size, hash if set)
int32   EncodedEntriesSize  followed by that many bytes
int32   NumFilesWithNonEncodableEntries
```

UE accepts either index kind. `build_pak` writes only the full directory index,
which keeps the writer simple. The path hash index would require reproducing
UE's `FCrc::StrCrc32` seeded hash.

## Full directory index

```
int32 NumDirectories
  FString DirectoryName      e.g. "Commander/Content/Data/Cards/SupplyCards/"
  int32   NumFiles
    FString FileName         e.g. "DA_Card_Supply_Human_Insight.uexp"
    uint32  EncodedEntryOffset
```

Directory names are mount-relative, carry a trailing slash and no leading slash.

## Encoded entries

A compact bitfield per entry:

```
bit  31     offset fits in 32 bits
bit  30     uncompressed size fits in 32 bits
bit  29     size fits in 32 bits  (only present when compressed)
bits 23-28  compression method index, 0 = stored
bit  22     entry is encrypted
bits 6-21   compression block count
bits 0-5    block size >> 11, or 0x3F meaning "read an explicit uint32"
```

`build_pak` always emits `0xC0000000`, meaning stored with a 32-bit offset and
size and no blocks, which is 12 bytes per entry.

## Inline entry header

Immediately before each entry's data, and authoritative for block layout:

```
int64 Offset            always 0 here; the real offset lives in the index
int64 Size
int64 UncompressedSize
int32 CompressionMethodIndex
uint8 Hash[20]          SHA-1 of the stored (still compressed) bytes
[ int32 BlockCount + BlockCount x (int64 start, int64 end) ]   if compressed
uint8  Flags
uint32 CompressionBlockSize
```

Uncompressed entries therefore have a 53-byte header. Block offsets are relative
to the entry's own start.

Note the hash covers the **compressed** bytes, so it validates storage integrity
but cannot be used to check that a decompressor produced correct output.

## Writing mod paks

`build_pak` emits an unencrypted index and uncompressed entries. UE accepts both,
so **building a mod requires neither the AES key nor an Oodle compressor.**

Name the output `ZZZ_<name>_P.pak`. The `_P` suffix marks it as a patch pak so UE
mounts it above the base archive; the prefix keeps it sorting last among
patches.
