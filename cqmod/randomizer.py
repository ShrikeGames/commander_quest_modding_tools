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

from . import decks, uasset

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
    Category("unit_models", "Unit models",
             "Swaps what units look like on the battlefield. A unit's mesh, "
             "its animation and its materials move together, so a swapped "
             "unit still animates and is textured as the creature it now "
             "looks like.",
             (SHUFFLE,)),
    Category("quest_rarity", "Quest rarity",
             "Shuffles how rare each quest is, which changes how often it is "
             "offered.",
             (SHUFFLE,)),
    Category("quest_conditions", "Quest requirements",
             "Changes how much a quest asks of you, such as how many cards to "
             "play or how much of a resource to spend. Each requirement stays "
             "in the pool of its own kind, so a count that was small stays "
             "small and one measured in hundreds stays large."),
    Category("quest_rewards", "Quest rewards",
             "Shuffles which card, relic or consumable each quest hands out. "
             "Rewards stay within their kind, so a quest that gave a relic "
             "still gives a relic.",
             (SHUFFLE,)),
    Category("event_values", "Event numbers",
             "Rerolls the amounts events give and take: gold, cards, relics "
             "and consumables. Grouped by what is being counted, so a gold "
             "payout is not swapped with a card count."),
    Category("event_health", "Event health effects",
             "Changes what events do to your commander's health. These are "
             "ratios rather than counts, and some are negative, so a shrine "
             "that healed you may end up costing you instead."),
    Category("event_gear_rarity", "Event relic rarity",
             "Shuffles the rarity of relic an event awards.",
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

    mode = settings.mode("unit_models")
    if mode != OFF:
        summary["unit_models"] = _shuffle_models(
            reader, _rng(settings, "unit_models"), project)

    quests = sorted((a for a in assets if a.class_name == QUEST_CLASS),
                    key=lambda a: a.name)
    events = sorted((a for a in assets if a.class_name == EVENT_CLASS),
                    key=lambda a: a.name)

    mode = settings.mode("quest_rarity")
    if mode != OFF:
        summary["quest_rarity"] = _apply(
            project, _rng(settings, "quest_rarity"), SHUFFLE,
            _collect(reader, um, quests, "QuestRarity"), settings)

    mode = settings.mode("quest_conditions")
    if mode != OFF:
        summary["quest_conditions"] = _apply_groups(
            project, _rng(settings, "quest_conditions"), mode,
            _quantities(reader, um, quests, "QuestCompleteCondition"), settings)

    mode = settings.mode("quest_rewards")
    if mode != OFF:
        summary["quest_rewards"] = _shuffle_rewards(
            reader, _rng(settings, "quest_rewards"), quests, project)

    mode = settings.mode("event_values")
    if mode != OFF:
        summary["event_values"] = _apply_groups(
            project, _rng(settings, "event_values"), mode,
            _quantities(reader, um, events, "CMEventActionParameter"), settings)

    mode = settings.mode("event_health")
    if mode != OFF:
        summary["event_health"] = _apply_floats(
            project, _rng(settings, "event_health"), mode,
            _floats(reader, um, events, "CMEventActionParameter"), settings)

    mode = settings.mode("event_gear_rarity")
    if mode != OFF:
        summary["event_gear_rarity"] = _apply(
            project, _rng(settings, "event_gear_rarity"), SHUFFLE,
            _collect(reader, um, events, "GearRarity"), settings)

    return summary


UNIT_BLUEPRINT_DIR = "Commander/Content/Data/Units/"
"""Where the unit actor blueprints live, one per summonable unit."""

VISUAL_CLASSES = ("SkeletalMesh", "AnimBlueprintGeneratedClass",
                  "MaterialInstanceConstant")
"""The imports that together make up a unit's appearance.

``CMUnitData`` holds a unit's rules but nothing about how it looks. The model
sits on the ``BP_Unit_*`` actor instead, as a skeletal mesh, the animation
blueprint that drives it and the material overrides its component carries.
"""


QUEST_CLASS = "CMQuestDefinition"
"""Asset class holding a quest, its completion conditions and its rewards."""

EVENT_CLASS = "CMInteractionEventDefinition"
"""Asset class holding a map event, its pages and the actions they run."""

QUANTITY_RANGE = (1, 999)
"""Bounds an integer must fall in to be treated as a quantity.

Quest and event exports also carry integers that are identifiers or packed
flags rather than amounts. One reward slot stores 65536, which is a count of
nothing and would become a demand for 65536 cards if it were rerolled. Real
amounts in this data run from a single card to three hundred gold, so anything
outside that band is left alone.
"""


def _quantities(reader, um, owners, class_prefix):
    """Collect the amounts inside quest or event sub-objects.

    Occurrences are grouped by the class that holds them together with the
    property name, because the same name means different things in different
    places: ``TakeAmount`` is two or three consumables on one class and up to
    three hundred gold on another, and pooling those would have an event hand
    out three gold or three hundred potions.

    Exports are matched by class prefix, which also keeps dialogue out. Talk
    box actions carry a bare ``IntValue`` that is a page number rather than an
    amount, and rerolling it would send a conversation to the wrong line.

    Args:
        reader (cqmod.pak.PakReader): An open archive.
        um (cqmod.usmap.Usmap): Property schema.
        owners (list): Quest or event assets to search.
        class_prefix (str): Only exports whose class starts with this are read.

    Returns:
        dict[tuple, list]: ``(class name, property name)`` to a list of
        ``(asset, offset, value, size)``.
    """
    low, high = QUANTITY_RANGE
    groups = {}
    for a in owners:
        try:
            body = reader.read(a.uexp)
        except Exception:
            continue
        for e in a.exports:
            if not e.class_name.startswith(class_prefix):
                continue
            for f in um.place(e, body):
                if (f.type == "IntProperty" and f.offset >= 0
                        and f.value is not None and low <= f.value <= high):
                    groups.setdefault((e.class_name, f.name), []).append(
                        (a, f.offset, f.value, f.size))
    return groups


def _apply_groups(project, rng, mode, groups, settings):
    """Stage edits for each pool of a grouped collection separately.

    Args:
        project (cqmod.project.Project): Project to add edits to.
        rng (random.Random): Generator for this category.
        mode (str): ``shuffle`` or ``random``.
        groups (dict): Output of :func:`_quantities`.
        settings (Settings): Run settings, for variance.

    Returns:
        int: How many edits were staged across all pools.
    """
    return sum(_apply(project, rng, mode, groups[k], settings, low=1)
               for k in sorted(groups))


def _floats(reader, um, owners, class_prefix):
    """Collect float knobs, grouped the same way as :func:`_quantities`.

    Args:
        reader (cqmod.pak.PakReader): An open archive.
        um (cqmod.usmap.Usmap): Property schema.
        owners (list): Assets to search.
        class_prefix (str): Only exports whose class starts with this are read.

    Returns:
        dict[tuple, list]: ``(class name, property name)`` to a list of
        ``(asset, offset, value)``.
    """
    groups = {}
    for a in owners:
        try:
            body = reader.read(a.uexp)
        except Exception:
            continue
        for e in a.exports:
            if not e.class_name.startswith(class_prefix):
                continue
            for f in um.place(e, body):
                if f.type == "FloatProperty" and f.offset >= 0:
                    value = struct.unpack_from("<f", body, f.offset)[0]
                    groups.setdefault((e.class_name, f.name), []).append(
                        (a, f.offset, value))
    return groups


def _apply_floats(project, rng, mode, groups, settings):
    """Stage new values for float fields, one pool at a time.

    Health effects are ratios, and some of them are negative: an event that
    takes maximum health away stores -0.2 where one that grants it stores 0.2.
    Rolling is therefore bounded by what the pool actually contains rather than
    clamped at zero, so a reward can turn into a penalty but not into a number
    the game has never seen.

    Args:
        project (cqmod.project.Project): Project to add edits to.
        rng (random.Random): Generator for this category.
        mode (str): ``shuffle`` or ``random``.
        groups (dict): Output of :func:`_floats`.
        settings (Settings): Run settings, for variance.

    Returns:
        int: How many edits were staged.
    """
    n = 0
    for key in sorted(groups):
        found = groups[key]
        values = [v for _, _, v in found]
        if mode == SHUFFLE:
            new_values = list(values)
            rng.shuffle(new_values)
        else:
            lo, hi = min(values), max(values)
            if settings.variance:
                span = max(abs(hi - lo), abs(hi)) or 1.0
                lo -= span * settings.variance
                hi += span * settings.variance
            new_values = [rng.uniform(lo, hi) for _ in found]
        for (a, off, old), value in zip(found, new_values):
            value = round(value, 4)
            if value == round(old, 4):
                continue
            project.set_value(a.path, off, value, f"{key[1]} {value:.3g}",
                              is_float=True)
            n += 1
    return n


def _reward_rows(reader, quests):
    """Find the data table row each quest reward hands out.

    A reward stores an ``FDataTableRowHandle``: two flag bytes, a reference to
    the table, then the row's ``FName``. Property placement lands a couple of
    bytes off on this struct, so the row is found by its table instead. An
    import naming a ``DataTable`` is unambiguous inside a reward export, and
    the row name sits four bytes past it.

    The table is also what the reward is. Grouping by it keeps a card reward a
    card and a relic reward a relic, without needing a list of valid rows,
    which matters because consumable rows are keyed in Korean and do not match
    any asset name.

    Args:
        reader (cqmod.pak.PakReader): An open archive.
        quests (list): Quest assets.

    Returns:
        dict[str, list]: Table name to a list of ``(asset, offset, row name)``.
    """
    groups = {}
    for a in quests:
        try:
            body = reader.read(a.uexp)
            pkg = uasset.parse(reader.read(a.uasset))
        except Exception:
            continue
        for e in a.exports:
            if not e.class_name.startswith("QuestCompleteReward"):
                continue
            hits = []
            for o in range(e.start, max(e.start, e.end - 12)):
                index = struct.unpack_from("<i", body, o)[0]
                if index >= 0 or -index - 1 >= len(pkg.imports):
                    continue
                imp = pkg.imports[-index - 1]
                if imp.class_name != "DataTable":
                    continue
                name_index, number = struct.unpack_from("<II", body, o + 4)
                if number == 0 and name_index < len(pkg.names):
                    hits.append((imp.object_name, o + 4,
                                 pkg.names[name_index]))
            # More than one candidate would make the choice a guess, and a
            # reward pointing at the wrong row is worse than an unchanged one.
            if len(hits) == 1:
                table, off, row = hits[0]
                groups.setdefault(table, []).append((a, off, row))
    return groups


def _shuffle_rewards(reader, rng, quests, project):
    """Redistribute quest rewards within each kind.

    Args:
        reader (cqmod.pak.PakReader): An open archive.
        rng (random.Random): Generator for this category.
        quests (list): Quest assets.
        project (cqmod.project.Project): Project to add edits to.

    Returns:
        int: How many rewards were changed.
    """
    n = 0
    groups = _reward_rows(reader, quests)
    for table in sorted(groups):
        found = groups[table]
        pool = [row for _, _, row in found]
        rng.shuffle(pool)
        for (a, off, old), row in zip(found, pool):
            if row == old:
                continue
            project.set_name_ref(a.path, off, row)
            n += 1
    return n


@dataclass
class UnitVisual:
    """Everything that makes one unit look the way it does.

    Attributes:
        path (str): Pak path of the ``BP_Unit_*`` blueprint, without extension.
        name (str): The blueprint's file name.
        mesh (tuple): ``(package path, object name, [offsets])`` of its
            skeletal mesh.
        anim (tuple | None): The same for its animation blueprint class, or
            None for a unit that does not animate.
        materials (list): One such tuple per material override, in slot order.
    """

    path: str
    name: str
    mesh: tuple
    anim: tuple
    materials: list


def _import_refs(pkg, body, class_name):
    """Find each import of one class and where the payload points at it.

    Args:
        pkg (cqmod.uasset.Package): The parsed header.
        body (bytes): Its ``.uexp``.
        class_name (str): Import class to look for.

    Returns:
        list[tuple]: ``(package path, object name, [offsets])`` per import that
        the payload actually references, ordered by first use. A mesh is
        referenced twice, once as the component's deprecated ``SkeletalMesh``
        and once as the ``SkinnedAsset`` that replaced it, and both have to
        move together.
    """
    out = []
    for i, imp in enumerate(pkg.imports):
        if imp.class_name != class_name:
            continue
        index = -(i + 1)
        offsets = [o for o in range(len(body) - 4)
                   if struct.unpack_from("<i", body, o)[0] == index]
        if not offsets:
            continue
        package = pkg.resolve(imp.outer_index)
        if isinstance(package, str) and package.startswith("/Game/"):
            out.append((package, imp.object_name, offsets))
    return sorted(out, key=lambda x: x[2][0])


def unit_visuals(reader):
    """Read the appearance of every unit blueprint in the game.

    Args:
        reader (cqmod.pak.PakReader): An open archive.

    Returns:
        list[UnitVisual]: One entry per blueprint whose model can be swapped.
        Blueprints with no skeletal mesh, such as a plain barricade, are left
        out because there is nothing to exchange.
    """
    out = []
    for path in sorted(f for f in reader.files()
                       if f.startswith(UNIT_BLUEPRINT_DIR)
                       and f.endswith(".uasset")):
        base = path[:-len(".uasset")]
        try:
            pkg = uasset.parse(reader.read(path))
            body = reader.read(base + ".uexp")
        except Exception:
            continue
        mesh = _import_refs(pkg, body, "SkeletalMesh")
        # Two meshes on one actor means a mount or a crew, and there is no way
        # to tell which is the body, so those are skipped rather than guessed.
        if len(mesh) != 1:
            continue
        anim = _import_refs(pkg, body, "AnimBlueprintGeneratedClass")
        out.append(UnitVisual(base, Path(base).name, mesh[0],
                              anim[0] if len(anim) == 1 else None,
                              _import_refs(pkg, body,
                                           "MaterialInstanceConstant")))
    return out


def _shuffle_models(reader, rng, project):
    """Swap unit models between units, keeping each one coherent.

    A model is not swappable on its own. The animation blueprint is built
    against a particular skeleton, and the material overrides on the component
    are written for a particular mesh's slots, so moving the mesh alone would
    leave a unit animating against the wrong skeleton and wearing another
    creature's textures. The mesh, its animation and its materials therefore
    move together as one package, which also means the two do not have to share
    a skeleton: the donor's animation comes with the donor's body.

    Units are pooled by how many material overrides they carry so that every
    slot receives a material. A unit with three slots cannot take the
    appearance of one with two without leaving a slot pointing at its old
    material, which is the wrong-textures case again.

    Args:
        reader (cqmod.pak.PakReader): An open archive.
        rng (random.Random): Generator for this category.
        project (cqmod.project.Project): Project to add edits to.

    Returns:
        int: How many units were given a new appearance.
    """
    # Commanders take part whatever the include-commanders setting says. That
    # guard is there to stop a commander being rolled down to a few health and
    # making a run unwinnable, and an appearance changes no numbers. Card art
    # treats commanders the same way.
    visuals = unit_visuals(reader)

    pools = {}
    for v in visuals:
        pools.setdefault((len(v.materials), v.anim is not None), []).append(v)

    n = 0
    for key in sorted(pools):
        pool = pools[key]
        if len(pool) < 2:
            continue
        donors = list(pool)
        rng.shuffle(donors)
        for target, donor in zip(pool, donors):
            if donor.path == target.path:
                continue
            moves = [(target.mesh, donor.mesh, "SkeletalMesh")]
            if target.anim and donor.anim:
                moves.append((target.anim, donor.anim,
                              "AnimBlueprintGeneratedClass"))
            moves += [(a, b, "MaterialInstanceConstant")
                      for a, b in zip(target.materials, donor.materials)]
            for (_, _, offsets), (package, obj, _), class_name in moves:
                for off in offsets:
                    project.set_reference(target.path, off, package,
                                          class_name, class_name, obj)
            n += 1
    return n


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
