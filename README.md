# Commander Quest Modding Tools

Browse and edit the data assets of [Commander Quest](https://store.steampowered.com/app/2697930/)
— cards, units, gear and commanders — and compile changes into a mod pak the
game loads at startup.

![status](https://img.shields.io/badge/status-working%20proof%20of%20concept-B03A0B)

## What works today

| | |
|---|---|
| **Browse** | All 1,492 data assets, filtered by class, searchable by name and text |
| **View** | Card art, title/description, class, export layout, raw property values |
| **Edit text** | Titles and descriptions, via the localization resource |
| **Edit art** | Replace card art with any PNG (auto-scaled and centre-cropped) |
| **Edit values** | Overwrite integers in export payloads by offset |
| **Find fields** | Diff a card against its `+` upgrade variant to locate gameplay numbers |
| **Build** | Compile staged edits into `ZZZ_<name>_P.pak` and install it |

Asset classes indexed include `CMCardData_*` (666 cards), `CMUnitData` (325 units),
`CMGearDefinition` (151 gear), `CMCommanderData` (5 commanders), quests, events and more.

## Setup

```bash
pip install -r requirements.txt          # cryptography, Pillow, PySide6
sudo apt install build-essential libssl-dev   # to build the Oodle decoder
```

Create `cqmod_config.local.json` (gitignored — it holds a key specific to your copy):

```json
{
  "game_dir": "/path/to/steamapps/common/Commander Quest",
  "aes_key": "…64 hex chars…"
}
```

The pak's index is AES-encrypted and **the key is assembled at runtime, so it is not
present in any shipped binary**. Recover it from your own installed copy: launch the
game, then

```bash
python3 tools/find_aes_key.py --save
```

Each candidate is confirmed by decrypting the entire index and comparing SHA-1 against
the hash in the pak footer, so a reported key is proven rather than guessed. If it
reports a permission error, `sudo sysctl -w kernel.yama.ptrace_scope=0` (revert with `=1`).

## Usage

```bash
python3 ui/app.py          # the GUI
python3 tools/selftest.py  # 16 end-to-end checks against the real pak
```

Pick an asset, edit it, and press **Build & Install Mod**. Restart the game to load it.
To uninstall, delete the `ZZZ_*_P.pak` from `Commander/Content/Paks/`.

### Finding the number you want to change

Property *names* are not recoverable without a `.usmap` (see below), so values are
addressed by byte offset. The **Find fields vs '+' variant** button does the work:
it diffs a card against its upgraded variant, filters out text and misaligned reads,
and usually leaves one or two offsets — the gameplay numbers. Across all 281 variant
pairs in the game the median result is 0 candidates and the maximum is 6.

## How it works

```
cqmod/
  config.py       game paths + AES key (never committed)
  oodle.py        ctypes binding to the vendored Kraken decoder
  pak.py          PakReader (AES + Oodle) and build_pak (uncompressed, unencrypted)
  uasset.py       package summary, name/import/export tables
  unversioned.py  unversioned property headers
  ftext.py        string-table FText values
  locres.py       localization read/write
  texture.py      PF_B8G8R8A8 art import/export
  catalog.py      the asset index
  diff.py         variant comparison / field finder
  project.py      staged edits -> a mod pak
third_party/ooz/  vendored Oodle Kraken decompressor (GPL-3.0)
```

Three facts make this tractable:

1. **Mod paks need neither the AES key nor Oodle.** UE accepts an unencrypted index
   and uncompressed entries, so `build_pak` writes plain data. Decryption and Oodle
   are only needed to *read* the shipping pak.
2. **Length-preserving edits need no export-table surgery.** If a patched `.uexp`
   keeps its exact byte length, the paired `.uasset` stays valid untouched. Every
   edit type here preserves length.
3. **Card art is `PF_B8G8R8A8`** — uncompressed 32-bit BGRA, single mip. Custom art
   needs no BC7/DXT encoder.

## Limitations

**Adding genuinely new assets is not supported.** A new card needs a new `DT_Cards`
row and a new asset, which changes byte lengths and therefore requires rewriting
`.uasset` export tables with correct property serialization — which needs the class
schema. Everything here edits existing assets instead.

**Property names and types are unknown.** These packages are cooked with
`PKG_UnversionedProperties`: property data is a presence bitmask against each class's
declared property order rather than a self-describing stream. A `.usmap` would fix
this, but generating one means walking the live `UClass`/`FProperty` graph in process
memory. Until then, values are addressed by offset and identified by diffing.

**Only int32 values are editable.** Floats, enums, booleans and object references are
visible in the raw view but have no dedicated editor yet.

**Linux only** so far — the Oodle decoder builds with GCC, and the key finder reads
`/proc/<pid>/mem`.

## Licence

GPL-3.0, matching the vendored `ooz` decoder it links against.
