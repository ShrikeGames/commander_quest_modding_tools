#!/usr/bin/env python3
"""Build a project that replaces every texture with a placeholder.

The game ships Unreal's engine content, so several placeholders come with it.
The default is ``DefaultWhiteGrid``, the white and grey checkerboard people
picture when they think of a missing texture. ``DefaultTexture`` is available
too but, despite the name, looks like pale rock rather than a grid.

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


def tiled_source(reader, source, tile, out_dir) -> str:
    """Render a placeholder tiled into an image, and return where it was saved.

    Art is scaled to cover its target, so a 2x2 grid stretched across a card
    becomes four big squares rather than anything anyone would read as a
    missing texture. Repeating the pattern first keeps the squares small enough
    to look like the grid it is.

    Args:
        reader (cqmod.pak.PakReader): An open archive.
        source (str): Pak path of the texture to tile.
        tile (int): How many times to repeat it in each direction.
        out_dir (pathlib.Path): Where to write the image.

    Returns:
        str: Path of the written image.
    """
    from PIL import Image

    files = set(reader.files())
    bulk_path = source + ".ubulk"
    bulk = reader.read(bulk_path) if bulk_path in files else b""
    one = texture.to_image(texture.parse(reader.read(source + ".uexp"), bulk))
    sheet = Image.new("RGBA", (one.width * tile, one.height * tile))
    for y in range(tile):
        for x in range(tile):
            sheet.paste(one, (x * one.width, y * one.height))
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{Path(source).name}_x{tile}.png"
    sheet.save(out)
    return str(out)


def build_project(reader, limit=0, name="DefaultTextures", exclude="",
                  source=texture.PLACEHOLDER_GRID, tile=8) -> Project:
    """Stage a replacement for every texture in the archive.

    Args:
        reader (cqmod.pak.PakReader): An open archive.
        limit (int): Stop after this many textures, for a quick trial. Zero
            means all of them.
        name (str): Mod name, which becomes the pak's file name.
        source (str): Pak path of the texture to use, without extension.
        tile (int): Repeat the source this many times in each direction before
            using it. One uses the texture as it is.
        exclude (str): Regular expression; any path matching it is left alone.
            ``UI/`` keeps the menus readable, which matters more than it
            sounds: a checkerboard button is still a button, but a checkerboard
            icon on a checkerboard panel is not something you can play through.

    Returns:
        cqmod.project.Project: The staged project.
    """
    skip = re.compile(exclude) if exclude else None
    image = ""
    if tile > 1:
        image = tiled_source(reader, source, tile,
                             Path(__file__).resolve().parent.parent
                             / "mod_assets" / "images")
    project = Project(name=name)
    for path in texture.all_textures(reader):
        if path == source:
            continue
        if skip and skip.search(path):
            continue
        if image:
            project.set_texture(path, image_path=image)
        else:
            project.set_texture(path, source_texture=source)
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
    ap.add_argument("--source", default=texture.PLACEHOLDER_GRID,
                    help="texture to use, as a pak path without extension "
                         "(default: Unreal's white grid)")
    ap.add_argument("--tile", type=int, default=8,
                    help="repeat the source this many times each way, so the "
                         "grid stays fine rather than being stretched to a few "
                         "huge squares (1 disables)")
    ap.add_argument("--exclude", default="",
                    help="leave paths matching this regular expression alone, "
                         "e.g. 'UI/' to keep the menus readable")
    ap.add_argument("--build", action="store_true",
                    help="also compile it, into the staging folder")
    args = ap.parse_args()

    reader = PakReader(config.pak_path(), config.aes_key())
    started = time.time()
    project = build_project(reader, args.limit, exclude=args.exclude,
                            source=args.source, tile=args.tile)
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
