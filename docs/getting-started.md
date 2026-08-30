# Getting started

## Requirements

- Linux (the Oodle decoder builds with GCC; the key finder reads `/proc`)
- Python 3.10+
- A build toolchain for the vendored decoder and the key scanner

```bash
pip install -r requirements.txt            # cryptography, Pillow, PySide6
sudo apt install build-essential libssl-dev
```

Native code is built automatically on first use, but you can do it up front:

```bash
make -C third_party/ooz      # libooz.so, the Oodle Kraken decoder
make -C tools/aes_finder     # the AES key scanner
```

## Configuration

Settings live in `cqmod_config.local.json` beside the repository root. It is
gitignored, because it holds a key specific to your copy of the game.

```json
{
  "game_dir": "/path/to/steamapps/common/Commander Quest",
  "aes_key": "…64 hex characters…",
  "mods_dir": "/optional/path/for/disabled/mods"
}
```

`game_dir` defaults to the repository's parent, so if you cloned into the game
folder you can leave it out. `mods_dir` defaults to `mods/` beside the
repository root. All three can also come from the environment, as
`CQMOD_GAME_DIR`, `CQMOD_AES_KEY` and `CQMOD_MODS_DIR`.

## Recovering the pak key

The archive's index is AES-256 encrypted. **The key is assembled at runtime and
is not present in any shipped binary.** Exhaustive byte-aligned scans of the
150 MB executable and every bundled DLL find nothing. It has to be read out of
live process memory.

1. Launch Commander Quest and get to the main menu.
2. Run:

```bash
python3 tools/find_aes_key.py --save
```

Every candidate is confirmed by decrypting the entire index and comparing SHA-1
against the hash stored in the pak footer, so a reported key is proven rather
than guessed.

If it reports a permission error, Linux is restricting `ptrace`:

```bash
sudo sysctl -w kernel.yama.ptrace_scope=0    # revert with =1
```

That setting is a hardening measure, so turn it back on when you are done.

## Checking the setup

```bash
python3 tools/selftest.py
```

Sixteen checks run against the real archive: index decryption, Oodle decoding of
400 assets, package parsing, localization round-tripping, texture
round-tripping, pak writing, the field finder, and a complete mod build. All
should pass before you rely on anything else.

## Your first mod

```bash
python3 ui/app.py
```

1. Search for a card. `Insight` is a good first target.
2. On the **Text** tab, change the title and press *Stage text change*.
3. On the **Values** tab, press *Find fields vs '+' variant*. For Insight this
   narrows 100 integer slots to exactly two: offsets 104 and 119.
4. Type a new value into the **New value** column.
5. Press **Build & Install Mod** and restart the game.

The mod is written to the staging folder and enabled. Use the **Mods** tab to
turn it off again without deleting it, or to remove it entirely. See
[mod manager](mod-manager.md). Nothing else on disk is ever modified, and the
base archive is only ever read.

## A note on Steam

The mod pak is a file Steam does not know about. *Verify integrity of game
files* should leave it alone or remove it, rather than re-downloading the 3.5 GB
archive, but delete the mod first if you want a guaranteed-clean check.
