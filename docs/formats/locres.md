# Localization resources (`.locres`)

Implemented in [`cqmod/locres.py`](../../cqmod/locres.py).

Card titles and descriptions are not stored in the card asset. The asset holds a
string-table reference, meaning a namespace and a key, and the text itself lives in
`Commander/Content/Localization/Game/<locale>/Game.locres`.

So **editing card text means editing the locres**, not the asset. That is
convenient: it is length-unconstrained, and other locales are untouched.

## The game's resources

Version 3. The English resource holds 2,571 strings across 2,692 keys in 17
namespaces:

| namespace | keys |
|---|---|
| `ST_Events` | 699 |
| `ST_Card_Summon` | 375 |
| `ST_Card_Summon_Enemy` | 348 |
| `ST_Gears` | 323 |
| `ST_Tooltips` | 240 |
| `ST_Card_Tactics` | 149 |
| `ST_Card_Supply` | 129 |
| `ST_Commander` | 87 |

Plus `ST_Card_Infra`, `ST_Card_Curse`, `ST_Quest`, `ST_Races`, `ST_Consumables`
and others. Text carries rich-text markup, e.g.
`Deal <Desc_Bold>4</> damage to all units within range.`

## Layout

```
byte  0   Guid    Magic     0E147475674A03FC4A15909DC3377F1B
byte 16   uint8   Version   3
byte 17   int64   StringArrayOffset
byte 25   uint32  EntryCount
byte 29   uint32  NamespaceCount
            uint32  NamespaceHash
            FString Namespace
            uint32  KeyCount
              uint32  KeyHash
              FString Key
              uint32  SourceStringHash
              int32   StringIndex
...
at StringArrayOffset:
          int32   Count
            FString Text
            int32   RefCount
```

The string array sits **last**, which is what makes in-place editing safe:
rewriting only the tail leaves `StringArrayOffset` valid and the namespace table
byte-identical. `locres.save` does exactly that, so an unedited resource
round-trips byte-for-byte.

Identical text is deduplicated, so several keys may share one array index.
`Locres.set` detects this and *splits* the entry, appending a new string and
repointing only the requested key, rather than silently changing every key that
shared it.

## The ANSI trap

> UE deserializes a **positive-length `FString` as ANSI, not UTF-8.**

Write multi-byte UTF-8 with a positive length and every non-ASCII string becomes
mojibake. Non-ASCII text must be written as UTF-16LE with a *negative* length.

This is not hypothetical. The first version of the "Pot of Greed" build wrote
everything as UTF-8; the resource came out 15 KB smaller than the original,
which was the clue. Fifty-two strings elsewhere in the game, none of them anything
to do with the card being edited, would have been corrupted.

The correct rule, in `_wr_string`:

```python
if all(ord(c) < 128 for c in s):
    b = s.encode("ascii") + b"\0"
    return struct.pack("<i", len(b)) + b
b = s.encode("utf-16-le") + b"\0\0"
return struct.pack("<i", -(len(b) // 2)) + b
```

With that fix, editing two strings changes the file size by 40 bytes and leaves
all 52 UTF-16 strings intact. The self-test checks both properties.

## Limitation

Only existing `(namespace, key)` pairs can be retargeted. Adding a new key would
mean rebuilding the namespace table, which `save` deliberately does not do.
