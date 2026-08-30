# Adding a property an asset does not set

Implemented in [`cqmod/payload.py`](../cqmod/payload.py) and
[`cqmod/unversioned.py`](../cqmod/unversioned.py).

Unversioned serialization omits any property left at its default, so a property
an asset does not set is absent from the payload entirely and there is nothing
to edit. `DA_Unit_Sheep` has an attack export but no `AttackDamage`, so the
sheep cannot be given an attack by changing a value.

The editor lists those properties anyway, greyed out and marked **not set**.
Entering a value adds them.

## Why this is the awkward edit

Every other edit preserves byte lengths, which is what keeps the paired
`.uasset` valid without touching it. This one cannot: adding a property inserts
its index into the export's header and its value into the payload, so the
payload grows.

That means the header has to be corrected. `uasset.resize_export` updates the
changed export's `SerialSize`, every later export's `SerialOffset`, and
`BulkDataStartOffset`, which sits past the end of the payload region. The
`.uasset` itself does not change length.

## Rebuilding the header

`unversioned.build` serializes a property header from a list of indices,
grouping consecutive ones into fragments and emitting the zero bitmap. Its
correctness is checked the strict way: rebuilding the header of every export in
the game from its parsed indices reproduces the original bytes exactly, for all
**6,947** exports that set at least one property.

The 597 exports that set none are the sole exception. UE writes a trailing skip
covering the class's whole property list there, rather than an empty fragment.
That case never arises when adding a property, since the result always has one.

## Ordering with other edits

An insertion moves every byte after it, which would silently invalidate any
value or tag edit staged at a later offset. Additions are therefore applied
first, and the remaining edits have their offsets shifted to match.

The self-test covers exactly that: it adds `AttackDamage` to the sheep while
also staging a `MaxHealth` change at an offset past the insertion, and checks
both land correctly.

```
add    DA_Unit_Sheep export +3 AttackDamage = 9  (+4 bytes)
value  DA_Unit_Sheep @62 = 25  (MaxHealth)
```

## Limits

Only properties with a fixed serialized size can be added, which covers
integers, floats, booleans, enums and object references. Text, arrays, structs
and maps are not offered, because their length depends on contents the editor
has no way to author.

Removing a property is not implemented, though it is the same problem in
reverse.
