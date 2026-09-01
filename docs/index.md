# Documentation

Tools for reading and modifying [Commander Quest](https://store.steampowered.com/app/2697930/)'s
game data.

## Start here

| | |
|---|---|
| [Getting started](getting-started.md) | Install, configure, recover the pak key, build a first mod |
| [GUI guide](gui-guide.md) | The editor, tab by tab |
| [Finding fields](finding-fields.md) | How to locate the number you actually want to change |
| [Randomizer](randomizer.md) | Generating a randomized run |
| [Packaging](packaging.md) | Building the downloadable releases |
| [Mod manager](mod-manager.md) | Enabling and disabling installed mods |
| [Starting decks](starting-decks.md) | Editing what cards a commander begins with |
| [Adding properties](adding-properties.md) | Giving an asset a stat it does not set |
| [What an effect targets](targeting.md) | Class branches, gameplay tags, and which you can change |
| [Property names](property-names.md) | Recovering the real schema from the running game |
| [Unit graphics](unit-graphics.md) | How units are drawn, and what replacing them would take |
| [Replacing textures](textures.md) | Block compression, mip chains, and art swapping |
| [Limitations](limitations.md) | What these tools cannot do, and why |

## Reference

| | |
|---|---|
| [Architecture](architecture.md) | Module map and data flow |
| [Property sizes](property-sizes.md) | Solving the serialized sizes the schema does not record |
| [Reverse engineering notes](reverse-engineering.md) | How the format was worked out |
| [Pak archives](formats/pak.md) | UE pak v11, encryption, entry encoding |
| [Packages](formats/package.md) | `.uasset` header, name/import/export tables |
| [Unversioned properties](formats/unversioned.md) | Why names are absent from the data, and how values are laid out |
| [Localization](formats/locres.md) | `.locres`, and the ANSI/UTF-16 trap |
| [Textures](formats/texture.md) | Texture2D on disk: mip chains, bulk data, block formats |

## The short version

Three facts make modding this game practical:

1. **Mod paks need neither the encryption key nor Oodle.** UE accepts an
   unencrypted index and uncompressed entries. Both are only needed to *read*
   the shipping archive.
2. **A length-preserving edit needs no export-table surgery.** Keep a patched
   `.uexp` the same byte length and its paired `.uasset` stays valid untouched.
   Edits that do change lengths, such as adding a property or an import, rewrite
   the export table and shift the offsets that moved.
3. **An asset can be pointed at something it never referenced.** Growing the
   import table means swapping art or a unit's model costs a rewritten header
   rather than a copy of the data.

Packages are cooked with `PKG_UnversionedProperties`, so names and types are
absent from the data itself. They are recovered from the running engine into
[`schema/usmap.json`](property-names.md) instead. What remains is that walking an
export stops at the first array or struct whose length cannot be measured, so
numbers past that point are still found by
[diffing a card against its upgrade variant](finding-fields.md).
