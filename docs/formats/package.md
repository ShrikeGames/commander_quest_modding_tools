# Packages (`.uasset` / `.uexp`)

Implemented in [`cqmod/uasset.py`](../../cqmod/uasset.py).

A cooked package is split across two files. The `.uasset` holds the header: the
name table, the imports, the exports and their offsets. The `.uexp` holds the
serialized property data those exports point at.

## Summary header

Starting at byte 0:

```
uint32  Tag                     0x9E2A83C1
int32   LegacyFileVersion       -8
int32   LegacyUE3Version        0   \
int32   FileVersionUE4          0    | all zero: the package is unversioned
int32   FileVersionUE5          0    |
int32   FileVersionLicenseeUE4  0   /
int32   CustomVersionCount      followed by 20 bytes each
int32   TotalHeaderSize         equals the .uasset length
FString PackageName             e.g. /Game/Data/Cards/.../DA_Card_...
uint32  PackageFlags
int32   NameCount, NameOffset
int32   SoftObjectPathsCount, SoftObjectPathsOffset
int32   GatherableTextCount, GatherableTextOffset
int32   ExportCount, ExportOffset
int32   ImportCount, ImportOffset
int32   DependsOffset
```

Package flags on these assets are `0x80002200`:

| | |
|---|---|
| `0x80000000` | `PKG_FilterEditorOnly` |
| `0x00002000` | `PKG_UnversionedProperties`, see [unversioned properties](unversioned.md) |
| `0x00000200` | `PKG_Cooked` |

## Name table

Each entry is an `FString` followed by four bytes of name hashes. A positive
length means single-byte characters; negative means UTF-16LE.

The name table is informative on its own. It lists every class, asset and string
key the package references, which is how the class of a card can be identified
without decoding any property data.

## Import and export tables

Imports come **before** exports in the file. Strides were measured rather than
assumed:

| | stride | derivation |
|---|---|---|
| `FObjectImport` | 32 bytes | `(ExportOffset - ImportOffset) / ImportCount` |
| `FObjectExport` | 96 bytes | `(DependsOffset - ExportOffset) / ExportCount` |

Getting the export stride wrong is a trap worth knowing about: the first export
still parses correctly and every later one becomes garbage. An early attempt
here used 100 bytes and produced exactly that.

Fields used from each export:

```
offset  0   int32 ClassIndex
offset 12   int32 OuterIndex
offset 16   FName ObjectName
offset 28   int64 SerialSize
offset 36   int64 SerialOffset
```

`FPackageIndex` is one-based and signed: positive indexes exports, negative
indexes imports, zero is null.

## Locating export payloads

`SerialOffset` is absolute from the start of the *logical* package, so it
includes the header. Subtract `TotalHeaderSize` to get a `.uexp` offset;
`Export.uexp_slice()` does this.

A good integrity check, used by the self-test: export payload sizes must tile the
`.uexp` exactly, leaving only the trailing 4-byte package tag.

```
DA_Card_Supply_Human_Insight   uexp = 132 bytes
  +1 DA_Card_Supply_Human_Insight  CMCardData_Supply       uexp[0:99]    99
  +2 CMEffectData_MoveCard         CMEffectData_MoveCard   uexp[99:112]  13
  +3 CMEffectData_MoveCard         CMEffectData_MoveCard   uexp[112:128] 16
                                                    99+13+16 = 128 = 132 - 4
```

Note that the main object is **not** reliably `exports[0]`; `catalog.build`
matches the export named after the asset file instead.
