"""Reading a commander's starting deck.

A commander does not list its cards as a plain property. They sit inside
``CMCommanderData::CommanderDataSetContainer``, a map keyed by race, and each
entry names a row of ``DT_Cards`` rather than pointing at a card asset:

    Jeanne d'Arc
      Human   Summon_Militia x5, Summon_Archer x2, Tactics_AttackEnhancement,
              Summon_Barricade, Infra_HealFountain
      Dwarf   Summon_Dwarf_Miner x5, Summon_Dwarf_BoltSpitter x2, ...

Placement stops at the map, so the entries are found by scanning instead: each
is a fixed-size record ending in an ``FName``, and the run of them is
recognisable because the stride is regular and every name resolves to a card
row. That makes a deck slot an ``FName`` index at a known offset, so changing a
card is the same four-byte write as changing a gameplay tag.
"""
from __future__ import annotations
import struct
from dataclasses import dataclass, field

from . import uasset

ENTRY_STRIDE = 14
"""Bytes per deck entry: two flag bytes, a table reference, then the FName."""

ROW_PREFIXES = ("Summon_", "Infra_", "Tactics_", "Supply_", "Power_", "Curse_")
"""How DT_Cards names its rows, used to tell deck entries from other names."""

CARD_TABLE = "Commander/Content/Data/Cards/DT_Cards"


@dataclass
class DeckCard:
    """One slot in a starting deck.

    Attributes:
        offset (int): Byte offset of the slot's ``FName`` index in the ``.uexp``.
        row (str): The ``DT_Cards`` row it currently names.
    """

    offset: int
    row: str


@dataclass
class Deck:
    """One commander's deck for one race.

    Attributes:
        start (int): Offset of the first entry.
        cards (list[DeckCard]): Slots in order.
    """

    start: int
    cards: list = field(default_factory=list)

    def __len__(self):
        """Count the cards in the deck.

        Returns:
            int: Number of slots.
        """
        return len(self.cards)


def card_rows(reader) -> list:
    """List every row name in the card table.

    These are the values a deck slot can hold. They are read from the table's
    name table, which is where the row names live.

    Args:
        reader (cqmod.pak.PakReader): An open archive.

    Returns:
        list[str]: Row names, sorted. Empty if the table cannot be read.
    """
    try:
        pkg = uasset.parse(reader.read(CARD_TABLE + ".uasset"))
    except Exception:
        return []
    return sorted(n for n in pkg.names if n.startswith(ROW_PREFIXES))


def parse(reader, asset) -> list:
    """Find the decks stored in a commander asset.

    Args:
        reader (cqmod.pak.PakReader): An open archive.
        asset (cqmod.catalog.Asset): A ``CMCommanderData`` asset.

    Returns:
        list[Deck]: One deck per race, in file order. Empty if none are found,
        which is the case for anything that is not a commander.
    """
    try:
        payload = reader.read(asset.uexp)
        names = uasset.parse(reader.read(asset.uasset)).names
    except Exception:
        return []

    rows = {i for i, n in enumerate(names) if n.startswith(ROW_PREFIXES)}
    if not rows:
        return []

    hits = []
    for off in range(len(payload) - 8):
        idx, number = struct.unpack_from("<II", payload, off)
        if idx in rows and number == 0:
            hits.append((off, names[idx]))

    decks, current = [], []
    for prev, nxt in zip(hits, hits[1:] + [(None, None)]):
        current.append(prev)
        if nxt[0] is None or nxt[0] - prev[0] != ENTRY_STRIDE:
            if len(current) > 1:          # a lone match is a coincidence
                decks.append(Deck(current[0][0],
                                  [DeckCard(o, n) for o, n in current]))
            current = []
    return decks
