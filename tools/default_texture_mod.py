#!/usr/bin/env python3
"""Build a project that replaces every texture with Unreal's placeholder.

The game ships the engine content that holds ``DefaultTexture``, the grey
checkerboard Unreal falls back to when art is missing. This points every
texture in the game at it.

The art is rewritten rather than repointed. Repointing would be far smaller,
but a texture reference cannot be found reliably in every asset: a reference is
a package index, and a small negative number like -3 occurs throughout ordinary
payload data, so searching for one finds hundreds of places that are not
references at all. Rewriting the art instead touches only the textures
themselves, keeps every payload exactly its original length, and needs no
offset arithmetic, so it cannot corrupt an asset.

The cost is size. Every texture keeps its own dimensions and pixel format, so
the mod carries a re-encoded copy of each, which comes to a little over two
gigabytes.

    python3 tools/default_texture_mod.py
    python3 tools/default_texture_mod.py --limit 50 --build
"""
from __future__ import annotations
import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cqmod import config, texture
from cqmod.pak import PakReader
from cqmod.project import Project


def build_project(reader, limit=0, name="DefaultTextures", exclude="") -> Project:
    """Stage a replacement for every texture in the archive.

    Args:
        reader (cqmod.pak.PakReader): An open archive.
        limit (int): Stop after this many textures, for a quick trial. Zero
            means all of them.
        name (str): Mod name, which becomes the pak's file name.
        exclude (str): Regular expression; any path matching it is left alone.
            ``UI/`` keeps the menus readable, which matters more than it
            sounds: a checkerboard button is still a button, but a checkerboard
            icon on a checkerboard panel is not something you can play through.

    Returns:
        cqmod.project.Project: The staged project.
    """
    skip = re.compile(exclude) if exclude else None
    project = Project(name=name)
    for path in texture.all_textures(reader):
        if path == texture.DEFAULT_TEXTURE:
            continue
        if skip and skip.search(path):
            continue
        project.set_texture(path, source_texture=texture.DEFAULT_TEXTURE)
        if limit and len(project.textures) >= limit:
            break
    return project


def main():
    """Write the project, and optionally compile it.

    Returns:
        None
    """
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="projects/default_textures.json",
                    help="where to write the project")
    ap.add_argument("--limit", type=int, default=0,
                    help="only take this many textures, for a quick trial")
    ap.add_argument("--exclude", default="",
                    help="leave paths matching this regular expression alone, "
                         "e.g. 'UI/' to keep the menus readable")
    ap.add_argument("--build", action="store_true",
                    help="also compile it, into the staging folder")
    args = ap.parse_args()

    reader = PakReader(config.pak_path(), config.aes_key())
    started = time.time()
    project = build_project(reader, args.limit, exclude=args.exclude)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    project.save(out)
    print(f"{len(project.textures):,} textures staged -> {out} "
          f"({time.time() - started:.1f}s)")

    if args.build:
        started = time.time()
        raw = project.build(reader)
        dest = config.mods_dir()
        dest.mkdir(parents=True, exist_ok=True)
        pak = dest / f"ZZZ_{project.name}_P.pak"
        pak.write_bytes(raw)
        print(f"built {pak} ({len(raw) / 2**30:.2f} GB, "
              f"{time.time() - started:.0f}s)")
        print("It is in the staging folder, so it is not enabled yet.")
    else:
        print("Open it in the app and press Build & Install Mod.")
    reader.close()


if __name__ == "__main__":
    main()
