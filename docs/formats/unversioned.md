# Unversioned properties

Implemented in [`cqmod/unversioned.py`](../../cqmod/unversioned.py).

This is the single fact that shapes what these tools can and cannot do.

## What it means

Normally a cooked Unreal property stream is self-describing: each value is
preceded by its property name and type, so a reader can decode an asset knowing
nothing about the class.

These packages set `PKG_UnversionedProperties` (flags `0x80002200`). Instead of
name/type tags, each export's payload opens with a run-length **bitmap of which
property indices carry a value** — indices being positions in the class's
declared property order. The values then follow in index order, with no types.

Decoding therefore requires knowing the class's property list. That is what a
`.usmap` file provides, and none ships with the game.

## Header format

A sequence of `uint16` fragments:

```
bits 0-6    SkipNum      properties skipped before this run
bit  7      bHasZeroes   run has entries in a trailing zero bitmap
bit  8      bIsLast      final fragment
bits 9-15   ValueNum     properties carrying a value in this run
```

If any fragment sets `bHasZeroes`, a bitmap of `ceil(bits/8)` bytes follows the
fragments, marking which of those values are zero and so not stored inline.

Worked example — `DA_Card_Supply_Human_Insight`, export +1:

```
bytes: 00 04  05 02  03 02  04 02  02 03

0x0400  skip 0, 2 values   -> indices 0, 1
0x0205  skip 5, 1 value    -> index 7
0x0203  skip 3, 1 value    -> index 11
0x0204  skip 4, 1 value    -> index 16
0x0302  skip 2, 1 value, last -> index 19

header = 10 bytes; values begin at export start + 10
```

So this card sets properties 0, 1, 7, 11, 16 and 19 of `CMCardData_Supply`. We
know *that*, and we know where each value's bytes begin — but not one property
name.

## What is still recoverable

Quite a lot, as it happens:

- **The class**, from the import table.
- **Text**, because string-table `FText` values have a recognisable byte
  signature and carry their own keys. See [`cqmod/ftext.py`](../../cqmod/ftext.py).
- **Art**, from the `Texture2D` import.
- **Which exports exist and what class each is** — an export named
  `CMEffectData_AddResource` is a strong hint about what its numbers mean.
- **Which property indices are set**, per export.

That is enough for a browser and for targeted edits. It is not enough to
*generate* a valid asset, which is why [creating new cards is out of
scope](../limitations.md).

## Getting a .usmap

The realistic route is to walk the live `UClass`/`FProperty` object graph in the
running game's memory: locate `GUObjectArray`, derive the `UObject`, `UStruct`
and `FField` layouts empirically without symbols, then resolve property types.
This is what tools like Dumper-7 do, and it is a substantial project.

An exploratory attempt got as far as decoding the game's `FName` pool from
process memory — the class family (`CMCardData` plus `_Curse`, `_Power`,
`_Summon`, `_Supply`, `_Tactics`) is plainly visible there. But the name pool
alone does not say which names are *properties*, of which class, in what order,
with what types. That needs the object graph.

Until then, values are addressed by byte offset and located by
[diffing against a variant](../finding-fields.md).
