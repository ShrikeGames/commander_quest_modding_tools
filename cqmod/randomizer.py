"""Generating a randomized mod from the game's own data.

Each category collects the values it can reach, then either redistributes them
between assets or rolls new ones, and stages the result as ordinary project
edits. Nothing here writes files: the output is the same edit list the editor
produces by hand, so a randomized run can be inspected, adjusted and rebuilt
like any other mod.

Two modes are offered wherever both make sense:

``shuffle``
    Redistribute the values that already exist. Totals and spread are preserved,
    so the game stays roughly as balanced as it started while nothing is where
    you expect it.
``random``
    Roll a fresh value inside the range the game itself uses, which is wilder
    and can produce combinations that do not occur naturally.

Runs are reproducible. Every category draws from its own generator seeded from
the run seed and the category name, so changing one category does not disturb
the others.
"""
from __future__ import annotations
import random
import struct
from dataclasses import dataclass, field
from pathlib import Path

from . import decks

SHUFFLE = "shuffle"
RANDOM = "random"
OFF = "off"


@dataclass
class Category:
    """One thing that can be randomized.

    Attributes:
        key (str): Stable identifier used in settings.
        label (str): Short name for the interface.
        description (str): What it changes, and what to expect.
        modes (tuple): Modes this category supports.
    """

    key: str
    label: str
    description: str
    modes: tuple = (SHUFFLE, RANDOM)


CATEGORIES = [
    Category("unit_health", "Unit health",
             "Redistributes or rerolls MaxHealth across every unit. Shuffle keeps "
             "the game's spread of tough and fragile units, just not where you "
             "expect them."),
    Category("unit_attack", "Unit attack",
             "The same for AttackDamage. A unit with no attack stays without one, "
             "since giving it a value would change what the unit is."),
    Category("unit_class", "Unit class branch",
             "Shuffles whether a unit counts as cavalry, beast, machine and so on. "
             "This changes what class-targeted effects apply to.",
             (SHUFFLE,)),
    Category("card_art", "Card art",
             "Swaps card illustrations between cards. Cosmetic, and the quickest "
             "way to tell a randomized run apart.",
             (SHUFFLE,)),
    Category("unit_move_speed", "Unit move speed",
             "Redistributes or rerolls how fast units move."),
    Category("unit_range", "Unit attack range",
             "Changes how far units can attack, kept within the band their attack "
             "type uses: melee units stay short ranged and archers stay long "
             "ranged, because the game gives the two disjoint sets of values."),
    Category("unit_attack_speed", "Unit attack speed",
             "Changes how often units attack, again kept within the band their "
             "attack type uses."),
    Category("effect_values", "Card effect numbers",
             "Rerolls the numbers inside card effects, such as how many cards are "
             "drawn or how much damage is dealt."),
    Category("gear_values", "Relic effect numbers",
             "Rerolls the numbers inside relic effects, such as how much of a "
             "resource is granted or how many times an effect triggers."),
    Category("gear_rarity", "Relic rarity",
             "Shuffles how rare each relic is, which changes what turns up in "
             "shops and rewards.",
             (SHUFFLE,)),
    Category("gear_icons", "Relic icons",
             "Swaps relic icons between relics. Cosmetic, and far cheaper than "
             "shuffling card art because the icons are small.",
             (SHUFFLE,)),
    Category("starting_decks", "Commander starting decks",
             "Replaces each commander's ten starting cards with cards drawn from "
             "the whole pool.",
             (RANDOM,)),
    Category("unit_tags", "Unit tags",
             "Shuffles gameplay tags between the units that carry them.",
             (SHUFFLE,)),
]

CATEGORY_BY_KEY = {c.key: c for c in CATEGORIES}


@dataclass
class Settings:
    """How a randomized run should be generated.

    Attributes:
        seed (int): Run seed. The same seed and choices always produce the same
            mod.
        choices (dict): Category key to mode, or ``off``.
        variance (float): For ``random`` modes, how far a rolled value may sit
            outside the range the game uses, as a fraction. 0.0 keeps rolls
            inside the observed range.
        include_enemies (bool): Whether enemy-only units are randomized too.
        include_commanders (bool): Whether the commanders' own units are
            randomized. Off by default: rolling a commander's health down to a
            few points makes a run unwinnable rather than interesting.
    """

    seed: int = 0
    choices: dict = field(default_factory=dict)
    variance: float = 0.0
    include_enemies: bool = True
    include_commanders: bool = False

    def mode(self, key: str) -> str:
        """Mode chosen for a category.

        Args:
            key (str): Category key.

        Returns:
            str: The chosen mode, or ``off``.
        """
        return self.choices.get(key, OFF)


def estimate_bytes(reader, assets, settings: Settings) -> int:
    """Roughly how large a mod these settings would produce.

    Image categories repoint a reference rather than copying an image, so their
    cost is the rewritten header of each asset touched. Everything else edits a
    few bytes of assets that are themselves small, so the estimate counts the
    assets whose header has to be rebuilt.

    Args:
        reader (cqmod.pak.PakReader): An open archive.
        assets (list): The catalog.
        settings (Settings): The settings to estimate.

    Returns:
        int: Approximate uncompressed size in bytes.
    """
    # Images are repointed rather than copied, so an image category now costs
    # only the assets whose reference changes, not the pictures themselves.
    total = 0
    for a in assets:
        edits_it = (
            (settings.mode("card_art") != OFF
             and a.class_name.startswith("CMCardData"))
            or (settings.mode("gear_icons") != OFF
                and a.class_name == "CMGearDefinition"))
        if not edits_it or not a.texture:
            continue
        for ext in (".uasset", ".uexp"):
            entry = reader.entries.get(a.path + ext)
            if entry:
                total += entry.uncompressed_size
    return total


def _rng(settings: Settings, key: str) -> random.Random:
    """Generator for one category.

    Deriving each category's stream from the seed and its own name keeps
    categories independent: turning one on does not change what another rolls.

    Args:
        settings (Settings): The run settings.
        key (str): Category key.

    Returns:
        random.Random: A seeded generator.
    """
    return random.Random(f"{settings.seed}:{key}")


def _units(assets, settings):
    """Units eligible for randomizing.

    Args:
        assets (list): The catalog.
        settings (Settings): Run settings, which may exclude enemy units.

    Returns:
        list: Matching ``CMUnitData`` assets, in a stable order.
    """
    out = [a for a in assets if a.class_name == "CMUnitData"]
    if not settings.include_enemies:
        out = [a for a in out if "_Enemy" not in a.name and not a.name.startswith(
            "DA_Unit_Enemy")]
    if not settings.include_commanders:
        out = [a for a in out if "Commander" not in a.name]
    return sorted(out, key=lambda a: a.name)


def _collect(reader, um, assets, prop_name, predicate=None):
    """Find every occurrence of a named property across assets.

    Args:
        reader (cqmod.pak.PakReader): An open archive.
        um (cqmod.usmap.Usmap): Property schema.
        assets (list): Assets to search.
        prop_name (str): Property to look for.
        predicate (Callable | None): Extra filter on the placed field.

    Returns:
        list[tuple]: ``(asset, offset, value, size)`` per occurrence.
    """
    found = []
    for a in assets:
        try:
            body = reader.read(a.uexp)
        except Exception:
            continue
        for e in a.exports:
            for f in um.place(e, body):
                if f.name != prop_name or f.offset < 0 or f.value is None:
                    continue
                if predicate and not predicate(f):
                    continue
                found.append((a, f.offset, f.value, f.size))
    return found


def _apply(project, rng, mode, found, settings, low=None, high=None):
    """Stage new values for a set of collected fields.

    Args:
        project (cqmod.project.Project): Project to add edits to.
        rng (random.Random): Generator for this category.
        mode (str): ``shuffle`` or ``random``.
        found (list): Output of :func:`_collect`.
        settings (Settings): Run settings, for variance.
        low (int | None): Lower bound for rolls, defaulting to the observed
            minimum.
        high (int | None): Upper bound, defaulting to the observed maximum.

    Returns:
        int: How many edits were staged.
    """
    if not found:
        return 0
    values = [v for _, _, v, _ in found]
    lo = low if low is not None else min(values)
    hi = high if high is not None else max(values)
    if mode == RANDOM and settings.variance:
        span = max(1, hi - lo)
        lo = max(0, int(lo - span * settings.variance))
        hi = int(hi + span * settings.variance)

    if mode == SHUFFLE:
        pool = list(values)
        rng.shuffle(pool)
        new_values = pool
    else:
        new_values = [rng.randint(lo, hi) for _ in found]

    n = 0
    for (a, off, old, size), value in zip(found, new_values):
        value = max(0, min(value, (1 << (8 * size - 1)) - 1))
        if value == old:
            continue
        project.set_value(a.path, off, value, a.name)
        n += 1
    return n


def run(reader, assets, um, settings: Settings, project) -> dict:
    """Generate a randomized mod into a project.

    Args:
        reader (cqmod.pak.PakReader): An open archive.
        assets (list): The catalog.
        um (cqmod.usmap.Usmap): Property schema, with sizes solved.
        settings (Settings): What to randomize and how.
        project (cqmod.project.Project): Project to stage edits into.

    Returns:
        dict: Category key to the number of edits it produced, for reporting.
    """
    summary = {}
    units = _units(assets, settings)

    mode = settings.mode("unit_health")
    if mode != OFF:
        found = _collect(reader, um, units, "MaxHealth")
        summary["unit_health"] = _apply(project, _rng(settings, "unit_health"),
                                        mode, found, settings, low=1)

    mode = settings.mode("unit_attack")
    if mode != OFF:
        found = _collect(reader, um, units, "AttackDamage")
        summary["unit_attack"] = _apply(project, _rng(settings, "unit_attack"),
                                        mode, found, settings, low=0)

    mode = settings.mode("unit_class")
    if mode != OFF:
        found = _collect(reader, um, units, "UnitClassBranch")
        summary["unit_class"] = _apply(project, _rng(settings, "unit_class"),
                                       SHUFFLE, found, settings)

    mode = settings.mode("unit_move_speed")
    if mode != OFF:
        found = _collect(reader, um, units, "MoveSpeedStatus")
        summary["unit_move_speed"] = _apply(
            project, _rng(settings, "unit_move_speed"), mode, found, settings, low=1)

    for key, prop in (("unit_range", "AttackRangeStatus"),
                      ("unit_attack_speed", "AttackCooltimeStatus")):
        mode = settings.mode(key)
        if mode == OFF:
            continue
        rng = _rng(settings, key)
        groups = _collect_grouped(reader, um, units, prop)
        total = 0
        for cls in sorted(groups):
            total += _apply(project, rng, mode, groups[cls], settings)
        summary[key] = total

    mode = settings.mode("effect_values")
    if mode != OFF:
        cards = sorted((a for a in assets if a.class_name.startswith("CMCardData")),
                       key=lambda a: a.name)
        found = _effect_numbers(reader, um, cards)
        summary["effect_values"] = _apply(project, _rng(settings, "effect_values"),
                                          mode, found, settings, low=1)

    gears = sorted((a for a in assets if a.class_name == "CMGearDefinition"),
                   key=lambda a: a.name)

    mode = settings.mode("gear_values")
    if mode != OFF:
        summary["gear_values"] = _apply(project, _rng(settings, "gear_values"),
                                        mode, _effect_numbers(reader, um, gears),
                                        settings, low=1)

    mode = settings.mode("gear_rarity")
    if mode != OFF:
        found = _collect(reader, um, gears, "Rarity")
        summary["gear_rarity"] = _apply(project, _rng(settings, "gear_rarity"),
                                        SHUFFLE, found, settings)

    mode = settings.mode("gear_icons")
    if mode != OFF:
        summary["gear_icons"] = _shuffle_textures(
            reader, um, _rng(settings, "gear_icons"), gears, project, "Icon")

    mode = settings.mode("card_art")
    if mode != OFF:
        cards_for_art = [a for a in assets if a.class_name.startswith("CMCardData")]
        summary["card_art"] = _shuffle_textures(
            reader, um, _rng(settings, "card_art"), cards_for_art, project,
            "CardIllustration")

    mode = settings.mode("starting_decks")
    if mode != OFF:
        summary["starting_decks"] = _random_decks(
            reader, _rng(settings, "starting_decks"), assets, project)

    mode = settings.mode("unit_tags")
    if mode != OFF:
        summary["unit_tags"] = _shuffle_tags(
            reader, um, _rng(settings, "unit_tags"), units, project)

    return summary


def _collect_grouped(reader, um, assets, prop_name):
    """Find a property, grouped by the class of the export holding it.

    Range and attack speed are tiers whose meaning depends on the attack type:
    melee attack types only ever use the low values and projectile types only
    the high ones. Randomizing across the whole set would give archers a melee
    reach and swordsmen a bowshot, so each class is treated as its own pool.

    Args:
        reader (cqmod.pak.PakReader): An open archive.
        um (cqmod.usmap.Usmap): Property schema.
        assets (list): Assets to search.
        prop_name (str): Property to look for.

    Returns:
        dict[str, list]: Export class name to its occurrences.
    """
    groups = {}
    for a in assets:
        try:
            body = reader.read(a.uexp)
        except Exception:
            continue
        for e in a.exports:
            for f in um.place(e, body):
                if f.name == prop_name and f.offset >= 0 and f.value is not None:
                    groups.setdefault(e.class_name, []).append((a, f.offset, f.value, f.size))
    return groups


def _effect_numbers(reader, um, owners):
    """Collect the numeric knobs inside effect objects.

    Only integer properties on effect exports are taken, and only small positive
    ones. Large values are identifiers or bitmasks rather than quantities, and
    rerolling those breaks a card rather than changing it.

    Args:
        reader (cqmod.pak.PakReader): An open archive.
        um (cqmod.usmap.Usmap): Property schema.
        owners (list): Assets whose effect exports should be searched.

    Returns:
        list[tuple]: ``(asset, offset, value, size)`` per occurrence.
    """
    found = []
    for a in owners:
        try:
            body = reader.read(a.uexp)
        except Exception:
            continue
        for e in a.exports:
            if not e.class_name.startswith(("CMEffectData", "CMBuffEffectData")):
                continue
            for f in um.place(e, body):
                if f.type == "IntProperty" and f.offset >= 0 \
                        and f.value is not None and 1 <= f.value <= 99:
                    found.append((a, f.offset, f.value, f.size))
    return found


def _find_reference(body: bytes, pkg, texture_path: str):
    """Locate an object reference by the import it points at.

    Property placement stops at the first variable-length property it cannot
    measure, which on a summon card happens before the illustration. The
    reference can still be found directly: an object property stores the
    target's package index, so the payload is searched for that value. A match
    is only accepted when it is unique, since a repeated value would make the
    choice a guess.

    Args:
        body (bytes): The asset's ``.uexp``.
        pkg (cqmod.uasset.Package): Its parsed header.
        texture_path (str): Pak path of the texture it currently uses.

    Returns:
        int | None: Byte offset of the reference, or None if it is not uniquely
        identifiable.
    """
    name = Path(texture_path).name
    index = None
    for i, imp in enumerate(pkg.imports):
        if imp.object_name == name and imp.class_name == "Texture2D":
            index = -(i + 1)
            break
    if index is None:
        return None
    hits = [o for o in range(len(body) - 4)
            if struct.unpack_from("<i", body, o)[0] == index]
    return hits[0] if len(hits) == 1 else None


def _shuffle_textures(reader, um, rng, owners, project, prop_name):
    """Point a set of assets at each other's images.

    This repoints references rather than copying pixels, so shuffling art costs
    a few bytes per asset instead of duplicating every image. The target must be
    added to each package's import table, which the build handles.

    Args:
        reader (cqmod.pak.PakReader): An open archive.
        um (cqmod.usmap.Usmap): Property schema.
        rng (random.Random): Generator for this category.
        owners (list): Assets whose images should be exchanged.
        project (cqmod.project.Project): Project to stage edits into.
        prop_name (str): The property holding the reference.

    Returns:
        int: How many assets were repointed.
    """
    from . import uasset

    slots = []
    for a in owners:
        if not a.texture:
            continue
        try:
            body = reader.read(a.uexp)
            pkg = uasset.parse(reader.read(a.uasset))
        except Exception:
            continue
        off = None
        for e in a.exports:
            for f in um.place(e, body):
                if f.name == prop_name and f.offset >= 0:
                    off = f.offset
                    break
            if off is not None:
                break
        if off is None:
            off = _find_reference(body, pkg, a.texture)
        if off is not None:
            slots.append((a, off, a.texture))
    if len(slots) < 2:
        return 0
    pool = [t for _, _, t in slots]
    rng.shuffle(pool)
    n = 0
    for (a, off, old), target in zip(slots, pool):
        if target == old:
            continue
        game_path = "/Game/" + target[len("Commander/Content/"):]
        project.set_reference(a.path, off, game_path, "Texture2D", prop_name)
        n += 1
    return n


def _random_decks(reader, rng, assets, project):
    """Replace commander starting decks with cards drawn from the pool.

    Args:
        reader (cqmod.pak.PakReader): An open archive.
        rng (random.Random): Generator for this category.
        assets (list): The catalog.
        project (cqmod.project.Project): Project to stage edits into.

    Returns:
        int: How many deck slots were changed.
    """
    pool = decks.card_rows(reader)
    if not pool:
        return 0
    n = 0
    for a in sorted((x for x in assets if x.class_name == "CMCommanderData"),
                    key=lambda x: x.name):
        for deck in decks.parse(reader, a):
            for slot in deck.cards:
                pick = rng.choice(pool)
                if pick == slot.row:
                    continue
                project.set_name_ref(a.path, slot.offset, pick)
                n += 1
    return n


def _shuffle_tags(reader, um, rng, units, project):
    """Redistribute gameplay tags between the units that carry them.

    Args:
        reader (cqmod.pak.PakReader): An open archive.
        um (cqmod.usmap.Usmap): Property schema.
        rng (random.Random): Generator for this category.
        units (list): Unit assets.
        project (cqmod.project.Project): Project to stage edits into.

    Returns:
        int: How many tag slots were changed.
    """
    from . import uasset

    slots = []
    for a in units:
        try:
            body = reader.read(a.uexp)
            names = uasset.parse(reader.read(a.uasset)).names
        except Exception:
            continue
        for e in a.exports:
            for f in um.place(e, body):
                if not um.is_tag_container(e, f.index):
                    continue
                for off, idx in um.tags(f, body):
                    if idx < len(names):
                        slots.append((a, off, names[idx]))
    if len(slots) < 2:
        return 0
    pool = [t for _, _, t in slots]
    rng.shuffle(pool)
    n = 0
    for (a, off, old), tag in zip(slots, pool):
        if tag == old:
            continue
        project.set_name_ref(a.path, off, tag)
        n += 1
    return n
