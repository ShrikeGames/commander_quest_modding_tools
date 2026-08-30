# Documentation

Tools for reading and modifying [Commander Quest](https://store.steampowered.com/app/2697930/)'s
game data.

## Start here

| | |
|---|---|
| [Getting started](getting-started.md) | Install, configure, recover the pak key, build a first mod |
| [GUI guide](gui-guide.md) | The editor, tab by tab |
| [Finding fields](finding-fields.md) | How to locate the number you actually want to change |
| [Mod manager](mod-manager.md) | Enabling and disabling installed mods |
| [Property names](property-names.md) | Recovering the real schema from the running game |
| [Unit graphics](unit-graphics.md) | How units are drawn, and what replacing them would take |
| [Replacing textures](textures.md) | Block compression, mip chains, and art swapping |
| [Limitations](limitations.md) | What these tools cannot do, and why |

## Reference

| | |
|---|---|
| [Architecture](architecture.md) | Module map and data flow |
| [Property sizes](property-sizes.md) | Solving property layouts without a .usmap |
| [Reverse engineering notes](reverse-engineering.md) | How the format was worked out |
| [Pak archives](formats/pak.md) | UE pak v11, encryption, entry encoding |
| [Packages](formats/package.md) | `.uasset` header, name/import/export tables |
| [Unversioned properties](formats/unversioned.md) | Why property names are missing |
| [Localization](formats/locres.md) | `.locres`, and the ANSI/UTF-16 trap |
| [Textures](formats/texture.md) | `PF_B8G8R8A8` card art |

## The short version

Three facts make modding this game practical:

1. **Mod paks need neither the encryption key nor Oodle.** UE accepts an
   unencrypted index and uncompressed entries. Both are only needed to *read*
   the shipping archive.
2. **Length-preserving edits need no export-table surgery.** Keep a patched
   `.uexp` the same byte length and its paired `.uasset` stays valid untouched.
3. **Card art is uncompressed BGRA.** No BC7/DXT encoder required.

And one fact that limits them: packages are cooked with
`PKG_UnversionedProperties`, so property *names and types* are not recoverable
without a `.usmap`. Values are addressed by byte offset instead, and found by
[diffing a card against its upgrade variant](finding-fields.md).
