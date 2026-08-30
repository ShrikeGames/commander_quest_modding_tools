# Limitations

## Adding new assets is not supported

These tools **edit existing assets**. They cannot create a new card.

A new card needs a new row in `DT_Cards` and a new `DA_Card_*` asset. Both change
byte lengths, which means rewriting `.uasset` export tables with correct
`SerialOffset`/`SerialSize` values *and* emitting valid unversioned property data
for the target class, which requires knowing that class's property order and
types. That is precisely what a `.usmap` provides and we do not have one.

The practical workaround is to repurpose a card you do not mind losing: change
its art, text and numbers into the card you want. That is how the "Pot of Greed"
proof of concept works.

## Property names and types are unknown

Packages are cooked with `PKG_UnversionedProperties`. Property data is a presence
bitmask against each class's declared property order rather than a
self-describing name/type stream, so the tools can see *which* property indices
carry values and where the bytes are, but not what they are called or what type
they hold. See [unversioned properties](formats/unversioned.md).

Consequences:

- Values are addressed by byte offset, not by name.
- You find the offset you want by [diffing against a `+` variant](finding-fields.md).
- An offset is only valid for one asset, and only until the game is patched.

Generating a `.usmap` means walking the live `UClass`/`FProperty` object graph in
process memory, deriving `UObject`/`UStruct`/`FField` layouts empirically without
symbols. That is a substantial project of its own.

## Only int32 values can be edited

Floats, enums, booleans, object references and arrays are visible in the raw
value list but have no dedicated editor. Since types are unknown, the list shows
every position interpreted as `int32`; that is a deliberately crude view, and
most rows in it are meaningless.

## Textures must keep their dimensions and format

Only `PF_B8G8R8A8` is handled, meaning uncompressed BGRA with a single mip, which
is what the card art happens to use. Block-compressed or mipmapped textures raise a
clear error rather than being silently corrupted. Replacement art is scaled and
centre-cropped to the original dimensions, because changing them would change the
payload length.

## New localization keys cannot be added

`locres.save` rewrites only the trailing string array and reuses the namespace
table verbatim. That guarantees an unedited resource round-trips byte-identically
and cannot corrupt the other strings, but it means you can only retarget keys
that already exist.

## Linux only

The Oodle decoder builds with GCC and the key finder reads `/proc/<pid>/mem`.
The pak and asset code is portable; the native pieces are not, yet.

## Game patches invalidate byte offsets

Every `ValueEdit` is an offset into a specific build's asset. After a game update
those offsets may point somewhere else entirely. Project files are JSON and
readable, so re-deriving them with the field finder is quick, but do check a mod
still does what you meant after an update.
