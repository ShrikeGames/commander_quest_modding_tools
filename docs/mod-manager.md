# Mod manager

Implemented in [`cqmod/mods.py`](../cqmod/mods.py), shown on the **Mods** tab.

## How enabling works

The game loads every `.pak` it finds in `Commander/Content/Paks`, and there is no
built-in way to mark one as inactive. The manager adds that by keeping a second
directory:

```
Commander/Content/Paks/     live      an enabled mod sits here
<repo>/mods/                staging   a disabled mod sits here
```

Toggling a mod moves the file between the two. Nothing is copied, rewritten or
regenerated, so disabling and re-enabling returns the exact same bytes.

Staging lives outside the game directory on purpose. Steam's *verify integrity of
game files* may remove files it does not recognise from the pak folder, and a mod
parked in staging is out of its reach.

The staging location defaults to `mods/` beside the repository root. Override it
with `mods_dir` in `cqmod_config.local.json`, or the `CQMOD_MODS_DIR` environment
variable.

## The Mods tab

A table of every mod in both directories:

| column | meaning |
|---|---|
| Enabled | Tick to move into the live folder, untick to move back to staging |
| Mod | Display name, with the `ZZZ_` prefix and `_P.pak` suffix stripped |
| Assets | How many files the pak contains, read from its index |
| Size | File size in bytes |
| Built | Last modification time |
| File | The actual filename |

Actions beneath it:

- **Refresh** rescans both directories.
- **Import pak...** copies an external pak into staging. It is opened and
  validated first, so a corrupt or encrypted file is rejected before it can reach
  the game's folder. Imported mods arrive disabled.
- **Delete** permanently removes the selected pak, after confirmation.
- **Open staging folder** opens staging in your file manager.

**Restart the game after any change.** Paks are mounted at startup, so toggling a
mod while the game is running has no effect until it is relaunched.

## Building

*Build & Install Mod* on the Edit assets tab now writes into staging and then
enables the result, so a freshly built mod appears in this table straight away.
Rebuilding a project of the same name replaces any previous copy in either
directory, so repeated builds do not accumulate duplicates.

## Safety

The base game archive is identified by filename and protected. It is excluded
from the listing entirely, and both `disable` and `delete` refuse to touch it:

```python
>>> mgr.disable(base_archive)
ModError: refusing to move the base game archive
```

Two other guards worth knowing:

- A move that would overwrite an existing file at the destination is refused
  rather than clobbering it.
- A mod whose name does not end in `_P.pak` is listed with a warning, because UE
  mounts it at base priority and it will not reliably override game assets.

## Using it from Python

```python
from cqmod import config
from cqmod.mods import ModManager

mgr = ModManager(config.paks_dir(), config.mods_dir(), config.pak_path())

for m in mgr.list():
    print(m.name, "enabled" if m.enabled else "disabled", m.file_count, "assets")

mgr.disable(mgr.list()[0])     # move to staging
mgr.enable(mgr.list()[0])      # move back
```
