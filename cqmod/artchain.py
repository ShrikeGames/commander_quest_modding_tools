"""Finding the texture that actually depicts a unit.

A card carries its illustration directly, but a unit's appearance is several
hops away: the card names a Blueprint, the Blueprint names a material instance,
and the material names a texture.

    DA_Card_Summon_Human_Assassin
      summonUnit -> BP_Unit_Assassin
                      -> MI_Human_Assasin
                           -> T_Human_Assasin_D

Following that matters because a unit data asset usually references no texture
at all, and where it does reference one it is often incidental. The Gyrocopter,
for instance, imports ``T_Unit_Notify_NoSteel``, a status icon rather than its
appearance, so taking the first texture an asset mentions shows the wrong image.
"""
from __future__ import annotations
from pathlib import Path

from . import uasset

GAME_PREFIX = "/Game/"
CONTENT_ROOT = "Commander/Content/"
CARD_ART_ROOT = "Commander/Content/UI/ArtResources/"
"""Where card illustrations live; textures elsewhere are icons or effects."""

MATERIAL_CLASSES = ("MaterialInstanceConstant", "Material", "MaterialInstance")
MESH_CLASSES = ("SkeletalMesh", "StaticMesh")


def to_pak_path(game_path: str) -> str:
    """Convert a ``/Game/...`` package path to its path inside the archive.

    Args:
        game_path (str): Engine-style package path.

    Returns:
        str: Archive path without extension, or an empty string if the input is
        not a game package path.
    """
    if not game_path.startswith(GAME_PREFIX):
        return ""
    return CONTENT_ROOT + game_path[len(GAME_PREFIX):]


def _imported_packages(pkg, classes) -> list:
    """List packages an asset imports objects of certain classes from.

    Args:
        pkg (cqmod.uasset.Package): A parsed header.
        classes (tuple[str, ...]): Import class names to accept.

    Returns:
        list[str]: Archive paths of the referenced packages, without extension.
    """
    out = []
    for imp in pkg.imports:
        if imp.class_name not in classes:
            continue
        outer = imp.outer_index
        if outer >= 0:
            continue
        i = -outer - 1
        if i >= len(pkg.imports):
            continue
        path = to_pak_path(pkg.imports[i].object_name)
        if path and path not in out:
            out.append(path)
    return out


def unit_blueprints(reader, assets, usmap) -> dict:
    """Map each unit asset to the Blueprint that draws it.

    A unit data asset does not name its Blueprint; the summon card names both,
    so the pairing is recovered from the cards.

    Args:
        reader (cqmod.pak.PakReader): An open archive.
        assets (list[cqmod.catalog.Asset]): The catalog.
        usmap (cqmod.usmap.Usmap): Schema used to read the card's references.

    Returns:
        dict[str, str]: Unit asset name to Blueprint archive path.
    """
    out = {}
    for a in assets:
        if a.class_name != "CMCardData_Summon":
            continue
        try:
            pkg = uasset.parse(reader.read(a.uasset))
            links = dict(usmap.links(a, pkg, reader.read(a.uexp)))
        except Exception:
            continue
        unit, bp = links.get("UnitData"), links.get("summonUnit")
        if not unit or not bp:
            continue
        target = bp.removesuffix("_C")
        for imp in pkg.imports:
            path = to_pak_path(imp.object_name)
            if path and Path(path).name == target:
                out.setdefault(unit, path)
                break
    return out


def _tokens(name: str) -> set:
    """Split an asset name into comparable lowercase tokens.

    Args:
        name (str): An asset or texture name.

    Returns:
        set[str]: Tokens of three characters or more.
    """
    out, cur = set(), ""
    for ch in name:
        if ch.isalnum():
            cur += ch.lower()
        else:
            if len(cur) >= 3:
                out.add(cur)
            cur = ""
    if len(cur) >= 3:
        out.add(cur)
    return out - {"unit", "texture", "material"}


def model_textures(reader, blueprint_path: str, depth: int = 2) -> list:
    """Collect the textures a Blueprint's materials sample, best match first.

    A Blueprint reaches several textures: a body, a mount, a weapon, and shared
    effects such as blood decals. Returning the first found picks arbitrarily
    and can be badly wrong, so all are returned, ordered by how directly they
    are reached and then by name similarity.

    Depth is the stronger signal. A unit's own material is imported by its
    Blueprint directly, while shared effect textures are reached through further
    hops. Names help but cannot be relied on alone: the Assassin's texture is
    spelled ``T_Human_Assasin_D``, with one fewer ``s`` than its Blueprint.

    Args:
        reader (cqmod.pak.PakReader): An open archive.
        blueprint_path (str): Archive path of the Blueprint, without extension.
        depth (int): How many material hops to follow.

    Returns:
        list[str]: Texture archive paths without extension, best first.
    """
    want = _tokens(Path(blueprint_path).name)
    at_depth = {}
    seen = set()
    frontier = [blueprint_path]
    for level in range(depth + 1):
        nxt = []
        for path in frontier:
            if path in seen or (path + ".uasset") not in reader:
                continue
            seen.add(path)
            try:
                pkg = uasset.parse(reader.read(path + ".uasset"))
            except Exception:
                continue
            for tex in _imported_packages(pkg, ("Texture2D",)):
                at_depth.setdefault(tex, level)
            # Meshes carry their own material slots, and some Blueprints only
            # reach their real art that way: the Gyrocopter's Blueprint imports
            # shared placeholder materials, with its actual texture hanging off
            # the SkeletalMesh instead.
            nxt += _imported_packages(pkg, MATERIAL_CLASSES + MESH_CLASSES)
        frontier = nxt
        if not frontier:
            break

    def score(tex):
        """Rank a texture, nearest first then by name overlap.

        Args:
            tex (str): Texture archive path.

        Returns:
            tuple: Sort key, best first.
        """
        t = _tokens(Path(tex).name)
        shared = sum(len(x) for x in t & want)
        partial = sum(min(len(a), len(b)) for a in t for b in want
                      if a in b or b in a)
        # A clear name match beats proximity, because a Blueprint may reach a
        # shared placeholder material more directly than its own art. Below
        # that threshold names are unreliable, so depth decides: the Assassin's
        # texture is spelled with one fewer "s" and shares no token at all.
        strong = 0 if max(shared, partial) >= 6 else 1
        return (strong, at_depth[tex], -shared, -partial, Path(tex).name)

    return sorted(at_depth, key=score)


def model_texture(reader, blueprint_path: str, depth: int = 2) -> str:
    """The single best texture for a Blueprint.

    Args:
        reader (cqmod.pak.PakReader): An open archive.
        blueprint_path (str): Archive path of the Blueprint, without extension.
        depth (int): How many material hops to follow.

    Returns:
        str: Best matching texture path, or an empty string if none.
    """
    hits = model_textures(reader, blueprint_path, depth)
    return hits[0] if hits else ""
