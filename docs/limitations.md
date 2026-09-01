# Limitations

## Creating an asset from nothing is not supported

These tools **edit assets the game already ships**. They cannot conjure a new card
out of thin air.

Most of the machinery a new asset would need does now exist. `schema/usmap.json`
supplies the property order and types for 309 classes, `uasset.add_name` and
`uasset.add_import` grow the name and import tables, `uasset.resize_export`
rewrites the export table when a payload changes length, and `payload.py` emits
valid unversioned property data. What is missing is the last step of tying those
together into a brand new package plus the `DT_Cards` row that indexes it.

The practical approach is to repurpose a card you do not mind losing: change its
art, text, numbers and targeting into the card you want. That is how the
"Pot of Greed" proof of concept works, and it is what the randomizer does 2,800
times over.

## Placement stops at the first unmeasurable property

Property names and types *are* recovered, from the live engine rather than from a
shipped `.usmap`. See [property names](property-names.md).

Placement is the remaining gap. Walking an export's properties in order requires
knowing how long each one is, and the walk stops at the first array or struct
whose length cannot be determined, leaving the tail of that export unaddressed.
On a summon card that boundary falls before the illustration.

It also stops when more than one arrangement of properties accounts for the
value region exactly. Two containers can be mis-sized in opposite directions and
still add up, so a layout that fits is not necessarily the right one. Reporting
the first arrangement found was worse than reporting nothing: a wrong offset
does not fail, it writes into whatever really lives there, and a number landing
in a gameplay tag container leaves a package the game refuses to load. Ambiguity
is therefore treated as failure, the same as finding no layout at all.

Where the target is an object reference there is a way around it: the reference
holds a known package index, so the payload can be searched for that value
directly, accepting a match only when it is unique. That is how art and model
swapping reach assets placement cannot. It does not generalise to plain numbers,
where one 4-byte value looks like any other.

## Not every type has an editor

| type | in the app |
|---|---|
| `IntProperty`, `EnumProperty`, `ByteProperty`, `BoolProperty` | editable |
| `ObjectProperty`, `ClassProperty`, `SoftObjectProperty` | shown, deliberately read-only |
| `FloatProperty` | shown, no editor; the randomizer writes them |
| arrays, maps, structs | shown as a group, not individually editable |

Object references are read-only **on purpose**. They serialize as a package
index, so a hand-typed number does not change a value, it silently repoints the
reference at whatever else happens to sit at that index. Getting one wrong breaks
an asset quietly: aiming a unit's `AttackType` at a VFX export leaves it with no
attack at all. Repointing is available where it is meaningful and checked, which
is the randomizer's art, icon and model categories.

## Replacement art keeps the original dimensions

Uncompressed `PF_B8G8R8A8` and block-compressed `PF_DXT1` and `PF_DXT5` are all
handled, mip chains included, whether the mips live in the `.uexp` or a `.ubulk`.

Dimensions are fixed. Replacement art is scaled and centre-cropped to whatever
the original was, because changing the size would change the payload length and
the mip chain along with it. Formats outside those three raise a clear error
rather than being silently corrupted.

Swapping art *between* existing assets has no such limit, since it repoints a
reference and copies nothing.

## Some descriptions cannot follow their numbers

Randomizing an effect rewrites the text that quotes it, but only where the
answer is certain: the old number must appear exactly once in the asset's text,
only one changed property may have held it, and no other asset may share the
string. Roughly one text in six is left saying the old number rather than being
guessed at. See [the randomizer](randomizer.md).

Event wording is the weakest case, because an amount often appears across
several pages at once.

## New localization keys cannot be added

`locres.save` rebuilds only the trailing string array and reuses the namespace
table verbatim. That guarantees an unedited resource round-trips byte-identically
and cannot corrupt the other strings, but it means existing keys can be retargeted
and new ones cannot be introduced.

## Game patches invalidate offsets, and may rotate the key

Every value edit is an offset into a specific build's asset. After a game update
those offsets may point somewhere else entirely. Project files are JSON and
readable, so re-deriving them with the field finder is quick, but do check that a
mod still does what you meant after an update.

A patch can also change the pak's encryption key. Recovering it again takes one
press of **Recover key from the running game**.
