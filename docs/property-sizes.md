# Property sizes

Implemented in [`cqmod/schema.py`](../cqmod/schema.py).

Unversioned property data has no type tags, so a value's position depends on the
total size of everything before it. That is why a byte offset found on one card
is meaningless on another, and why two assets setting different properties
cannot be compared. Recovering per-property *sizes* is what would fix both.

## The method

Every export records which property indices it sets and how many bytes its
values occupy, so each one is an equation:

```
sum(size[i] for i in indices) == value_bytes
```

Across the whole game a class yields hundreds of these. Where one export's
property set is a subset of another's, subtracting them gives an equation over
just the difference, which is where most of the leverage comes from. Sizes are
then derived by constraint propagation: an equation with exactly one unknown
determines it, which may reduce others to one unknown in turn.

Nothing is guessed. A size that comes out of this is forced by the data, and a
final pass discards any size the observations fail to reproduce.

## Three corrections that mattered

Getting this to balance required fixing three things, each of which silently
produced plausible-looking nonsense:

**Zero-valued properties occupy no bytes.** A property whose value is zero is
recorded as a bit in the header's zero bitmap and omitted from the value region
entirely. So the same property set legitimately produces different payload
sizes. The bitmap is now decoded, and those indices are excluded from the
equations. Its length was also being computed wrongly: UE stores it as a
`uint8`, a `uint16`, or a run of `uint32`s, not as `ceil(bits/8)`.

**Text is variable length.** Its spans are located and subtracted before
solving, and the property is marked variable rather than assigned a size.

**Every export ends with a four-byte terminator.** Exports with no properties at
all still have four bytes of payload, and all 7,585 export value regions end
with `00 00 00 00`. Subset differences cancel this constant, so omitting it gave
correct *relative* sizes while absolute sums never balanced. Accounting for it
took the solver from 118 of 189 classes self-consistent to 191 of 191.

## What it achieves

377 of 1,299 properties solved, across 191 classes that reproduce every one of
their own observations exactly.

```
CMEffectData_MoveCard   1:1  4:1  5:1  11:12  13:4   (rest variable)
```

## Where it stops

The classes that matter most to a modder are not solved:
`CMCardData_Summon`, `CMCardData_Tactics`, `CMCardData_Supply` and `CMUnitData`
come out entirely variable. They are dominated by variable-length properties,
so no equation ever reduces to a single unknown.

Correlating against known values from the game UI does not rescue this either.
Given ten cards with costs read off the collection screen, the cost value does
not appear as a byte, `int16` or `int32` anywhere in the card export for
Assassin (2), Ambush Cavalry (2), Cataphract (3) or Altar of the Fallen (2). The
displayed cost is not stored plainly in the card asset.

Naming the fields on those classes needs real type information, which means a
`.usmap` generated from the running game. See
[unversioned properties](formats/unversioned.md).
