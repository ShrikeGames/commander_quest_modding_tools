# Architecture

## Layers

```
ui/app.py            PySide6 editor
      |
cqmod/randomizer.py  a randomized mod from the game's own data
cqmod/project.py     staged edits  ->  a mod pak
cqmod/mods.py        enable / disable installed mods
      |
cqmod/catalog.py     the asset index      cqmod/decks.py     commander decks
cqmod/diff.py        field finder         cqmod/artchain.py  unit -> its texture
cqmod/schema.py      solved property sizes per class
cqmod/usmap.py       recovered property names and types
      |
cqmod/uasset.py      package headers      cqmod/locres.py    localization
cqmod/unversioned.py property headers     cqmod/texture.py   Texture2D
cqmod/payload.py     adding a property    cqmod/dxt.py       BC1 / BC3
cqmod/ftext.py       string-table text
      |
cqmod/pak.py         archive read + write
cqmod/oodle.py       Kraken decompression  ->  native/libooz.so or .dll
cqmod/keyfinder.py   pak key recovery      ->  native/aes_finder
cqmod/config.py      game paths, AES key
cqmod/resources.py   bundled files, from a checkout or a packaged build
```

Each layer only depends on those below it. `pak.py` knows nothing about cards;
`catalog.py` knows nothing about the GUI.

## Reading

```
Commander-Windows.pak
  -> PakReader: locate footer, decrypt index (AES-256-ECB), verify SHA-1
  -> per entry: read inline header, Oodle-decode blocks, verify size
  -> uasset.parse:  names, imports, exports
  -> unversioned.parse: which property indices carry values, and where
  -> ftext.find_all: string-table keys
  -> locres.load: resolve those keys to display text
  -> catalog.Asset
```

Every step is self-validating. The archive stores a SHA-1 for the index, the
directory index, and each entry; packages carry a magic and a header size; and
export payload sizes must tile the `.uexp` exactly. A wrong AES key fails at the
first hash rather than producing plausible garbage.

## Writing

```
Project (TextEdit / TextureEdit / ValueEdit)
  -> group edits by asset and locale
  -> apply: patch int32s, splice pixels, retarget strings
  -> build_pak: uncompressed entries, unencrypted index
  -> ZZZ_<name>_P.pak in Content/Paks
```

The writer deliberately does *not* encrypt or compress. UE accepts both plain
forms, so nothing about building a mod requires the key or an Oodle compressor.
Those exist purely to read the original.

## Why edits preserve byte length

A cooked package is split in two. The `.uasset` holds the export table, which
records each export's `SerialOffset` and `SerialSize` into the `.uexp`. Change a
payload's length and every subsequent offset shifts, so the `.uasset` must be
rewritten too. Doing that correctly means understanding the property
serialization, which needs a `.usmap`.

All three edit kinds sidestep this:

- **Value edits** overwrite an `int32` in place.
- **Texture edits** keep the image dimensions, so the pixel span is unchanged.
- **Text edits** touch the localization resource, not the asset at all.

This is the single constraint that shapes the whole design, and it is also why
[creating new assets is out of reach](limitations.md).

## Native components

| | |
|---|---|
| `third_party/ooz` | Oodle Kraken decompressor, GPL-3.0, vendored pristine with the Linux patch expressed as Makefile steps |
| `tools/aes_finder` | Memory scanner for the pak key; Python would take hours for what C does in seconds |

Both build on demand. Generated artefacts are gitignored.
