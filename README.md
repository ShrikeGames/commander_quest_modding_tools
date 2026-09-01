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
| **Randomize** | Generate a seeded randomized run from a checklist of 20 categories, including unit models, quests and events |
| **Manage mods** | List installed mods and enable or disable each one |

Asset classes indexed include `CMCardData_*` (666 cards), `CMUnitData` (325 units),
`CMGearDefinition` (151 gear), `CMCommanderData` (5 commanders), quests, events and more.

## Install

Grab the build for your platform from
[Releases](https://github.com/ShrikeGames/commander_quest_modding_tools/releases),
unpack it anywhere, and run **CommanderQuestModTool**. No Python, no compiler,
nothing installed.

On first launch it asks where the game is, which it can usually work out, and
for the archive key. The game assembles that key while it runs, so start the
game, wait for the main menu, and press **Recover key from the running game**.
The key is the same for everyone on a given version, so this is a one-time step
unless a patch changes it. Reading another process's memory needs permission:
run the tool as administrator on Windows, or once on Linux
`sudo sysctl -w kernel.yama.ptrace_scope=0`.

The key is deliberately not shipped. It belongs to the game, so you take it
from your own copy, and recovering it locally still works after a patch that
rotates it.

## Setup from source

```bash
pip install -r requirements.txt          # cryptography, Pillow, PySide6, numpy
python3 tools/build_native.py            # the Oodle decoder and key scanner
```

`tools/build_native.py` needs only a C and C++ compiler, and works on Linux and
Windows. See [Packaging](docs/packaging.md) to produce a release build.

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
present in any shipped binary**. It is baked into a build rather than tied to a
player, so one key covers every copy of a given version. Recover it from your
own installed copy: launch the game, then

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
  resources.py    finding bundled files, from a checkout or a packaged build
  keyfinder.py    recovering the pak key from the running game
  oodle.py        ctypes binding to the vendored Kraken decoder
  pak.py          PakReader (AES + Oodle) and build_pak (uncompressed, unencrypted)
  uasset.py       package summary, name/import/export tables, and growing them
  unversioned.py  unversioned property headers
  usmap.py        property names and types recovered from the engine
  schema.py       solving serialized property sizes
  payload.py      adding a property an export does not set
  ftext.py        string-table FText values
  locres.py       localization read/write
  texture.py      Texture2D read/replace
  dxt.py          DXT1 and DXT5 block compression
  artchain.py     finding the texture that depicts a unit
  decks.py        commander starting decks
  catalog.py      the asset index
  diff.py         variant comparison / field finder
  randomizer.py   generating a randomized mod from the game's own data
  project.py      staged edits -> a mod pak
  mods.py         enable / disable installed mods
third_party/ooz/  vendored Oodle Kraken decompressor (GPL-3.0)
```

Three facts make this tractable:

1. **Mod paks need neither the AES key nor Oodle.** UE accepts an unencrypted index
   and uncompressed entries, so `build_pak` writes plain data. Decryption and Oodle
   are only needed to *read* the shipping pak.
2. **Most edits preserve length, and the ones that do not are handled.** A patched
   `.uexp` that keeps its exact byte length leaves the paired `.uasset` valid
   untouched, which covers ordinary value and text edits. Adding a property,
   a name or an import does change lengths, so those rewrite the export table and
   shift every offset that moved.
3. **An asset can be pointed at art it never referenced.** Growing the import
   table means swapping a card's illustration costs a rewritten header rather
   than a copy of the image, which is what makes randomizing art and unit models
   cheap enough to be practical.

## Limitations

See [docs/limitations.md](docs/limitations.md) for the full picture.

**Creating an asset from nothing is not supported.** Everything here edits assets
the game already ships. Most of the pieces a new asset needs now exist, including
the class schema, name and import insertion and export resizing, but not the step
that ties them into a new package plus its `DT_Cards` row. Repurposing a card you
do not mind losing is the practical route.

**Placement stops at the first unmeasurable property.** Names and types are
recovered, so the editor shows `Count (IntProperty)` rather than an offset, but
walking an export stops at the first array or struct whose length is unknown,
leaving the tail unaddressed. Object references get around this by being found
through the import they point at. See [property names](docs/property-names.md).

**Not every type has an editor.** Integers, enums, bytes and booleans are
editable. Object references are shown read-only on purpose, since a hand-typed
number would repoint them rather than change a value; the randomizer repoints
them where that is meaningful. Floats have no editor yet, though the randomizer
writes them.

**Replacement art keeps the original dimensions.** `PF_B8G8R8A8`, `PF_DXT1` and
`PF_DXT5` are handled, mip chains included. Swapping art between existing assets
has no such limit, because it repoints a reference instead of copying pixels.

## Licence

GPL-3.0, matching the vendored `ooz` decoder it links against.
