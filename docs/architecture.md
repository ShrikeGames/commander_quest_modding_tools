# Architecture

## Layers

```
ui/app.py            PySide6 editor
      |
cqmod/project.py     staged edits  ->  a mod pak
cqmod/catalog.py     the asset index
cqmod/diff.py        variant comparison / field finder
      |
cqmod/uasset.py      package headers      cqmod/locres.py    localization
cqmod/unversioned.py property headers     cqmod/texture.py   card art
cqmod/ftext.py       string-table text
      |
cqmod/pak.py         archive read + write
cqmod/oodle.py       Kraken decompression  ->  third_party/ooz/libooz.so
cqmod/config.py      game paths, AES key
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
