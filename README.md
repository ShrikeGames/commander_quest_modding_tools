# Commander Quest Modding Tools

Browse and edit the data assets of [Commander Quest](https://store.steampowered.com/app/2697930/):
cards, units, gear and commanders. Compile the changes into a mod pak the game
loads at startup.

![status](https://img.shields.io/badge/status-working%20proof%20of%20concept-B03A0B)

## What works today

| | |
|---|---|
| **Browse** | All 1,492 data assets, filtered by class, searchable by name and text |
| **View** | Card art, title/description, class, export layout, raw property values |
| **Edit text** | Titles and descriptions, via the localization resource |
| **Edit art** | Replace card or unit art with any PNG, or copy art between assets |
| **Edit values** | Overwrite integers in export payloads, or add a stat an asset does not set |
| **Named properties** | Real property names and types, recovered from the running game |
| **Follow links** | Jump from a summon card to the unit holding its stats |
| **Edit decks** | Change the cards a commander starts with |
| **Find fields** | Diff a card against its `+` upgrade variant to locate gameplay numbers |
| **Build** | Compile staged edits into `ZZZ_<name>_P.pak` and install it |
| **Randomize** | Generate a seeded randomized run from a checklist of categories |
| **Manage mods** | List installed mods and enable or disable each one |

Asset classes indexed include `CMCardData_*` (666 cards), `CMUnitData` (325 units),
`CMGearDefinition` (151 gear), `CMCommanderData` (5 commanders), quests, events and more.

## Setup

```bash
pip install -r requirements.txt          # cryptography, Pillow, PySide6
sudo apt install build-essential libssl-dev   # to build the Oodle decoder
```

Create `cqmod_config.local.json`, which is gitignored because it holds a key
specific to your copy:

```json
{
  "game_dir": "/path/to/steamapps/common/Commander Quest",
  "aes_key": "…64 hex chars…",
  "mods_dir": "/optional/path/for/disabled/mods"
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

Pick an asset, edit it, and press **Build & Install Mod**. Restart the game to
load it. The **Mods** tab lists every installed mod with a checkbox to enable or
disable it: enabled mods live in the game's pak folder, disabled ones are held in
a staging folder, and toggling moves the file between the two.

### Finding the number you want to change

Property *names* are not recoverable without a `.usmap` (see below), so values are
addressed by byte offset. The **Find fields vs '+' variant** button does the work:
it diffs a card against its upgraded variant, filters out text and misaligned reads,
and usually leaves one or two offsets, which are the gameplay numbers. Across
all 281 variant pairs in the game the median result is 0 candidates and the
maximum is 6.

## Documentation

Full docs are in [`docs/`](docs/index.md):

| | |
|---|---|
| [Getting started](docs/getting-started.md) | Install, configure, recover the key, build a first mod |
| [GUI guide](docs/gui-guide.md) | The editor, tab by tab |
| [Finding fields](docs/finding-fields.md) | Locating the number you want to change |
| [Randomizer](docs/randomizer.md) | Generating a randomized run |
| [Mod manager](docs/mod-manager.md) | Enabling and disabling installed mods |
| [Starting decks](docs/starting-decks.md) | Editing what cards a commander begins with |
| [Adding properties](docs/adding-properties.md) | Giving an asset a stat it does not set |
| [What an effect targets](docs/targeting.md) | Class branches, gameplay tags, and which you can change |
| [Property names](docs/property-names.md) | Recovering the real schema from the running game |
| [Unit graphics](docs/unit-graphics.md) | How units are drawn, and what replacing them would take |
| [Replacing textures](docs/textures.md) | Block compression, mip chains, and art swapping |
| [Architecture](docs/architecture.md) | Module map and data flow |
| [Limitations](docs/limitations.md) | What these tools cannot do, and why |
| [Reverse engineering notes](docs/reverse-engineering.md) | How the format was worked out |
| Formats | [pak](docs/formats/pak.md) · [package](docs/formats/package.md) · [unversioned properties](docs/formats/unversioned.md) · [locres](docs/formats/locres.md) · [textures](docs/formats/texture.md) |

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
  mods.py         enable / disable installed mods
third_party/ooz/  vendored Oodle Kraken decompressor (GPL-3.0)
```

Three facts make this tractable:

1. **Mod paks need neither the AES key nor Oodle.** UE accepts an unencrypted index
   and uncompressed entries, so `build_pak` writes plain data. Decryption and Oodle
   are only needed to *read* the shipping pak.
2. **Length-preserving edits need no export-table surgery.** If a patched `.uexp`
   keeps its exact byte length, the paired `.uasset` stays valid untouched. Every
   edit type here preserves length.
3. **Card art is `PF_B8G8R8A8`**, uncompressed 32-bit BGRA with a single mip.
   Custom art needs no BC7/DXT encoder.

## Limitations

See [docs/limitations.md](docs/limitations.md) for the full picture.

**Adding genuinely new assets is not supported.** A new card needs a new `DT_Cards`
row and a new asset, which changes byte lengths and therefore requires rewriting
`.uasset` export tables with correct property serialization, which needs the class
schema. Everything here edits existing assets instead.

**Property names are recovered, but placement stops at variable-length data.**
`schema/usmap.json` restores real names and types from the running game, so the
editor shows `Count (IntProperty)` rather than an offset. Placement still stops at
the first array or struct it cannot measure, leaving the tail of a card unresolved.
See [property names](docs/property-names.md).

**Only int32 values are editable.** Floats, enums, booleans and object references are
visible in the raw view but have no dedicated editor yet.

**Linux only** so far. The Oodle decoder builds with GCC, and the key finder reads
`/proc/<pid>/mem`.

## Licence

GPL-3.0, matching the vendored `ooz` decoder it links against.
