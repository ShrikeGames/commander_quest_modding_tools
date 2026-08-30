# Starting decks

Implemented in [`cqmod/decks.py`](../cqmod/decks.py), shown on the **Deck** tab
when a commander is selected.

Every commander starts with two decks, one per playable race, of ten cards each.

```
Jeanne d'Arc
  deck 1  Summon_Militia x5, Summon_Archer x2, Tactics_AttackEnhancement,
          Summon_Barricade, Infra_HealFountain
  deck 2  Summon_Dwarf_Miner x5, Summon_Dwarf_BoltSpitter x2,
          Summon_Dwarf_ShieldBiter, Summon_Dwarf_SteelBallista,
          Infra_Dwarf_MiningContract
```

All five share a core of five basic units, two archers and one tactics card,
differing only in their last two slots. Those two are the commander's signature:
Khan gets a horse archer and Insight, Zhuge Liang gets Spark Catalyst and
Movement Resistance.

## Where they live

Not in a property of their own. Decks sit inside
`CMCommanderData::CommanderDataSetContainer`, a map keyed by race, and each slot
names a **row of `DT_Cards`** rather than pointing at a card asset.

Property placement stops at the map, so the slots are found by scanning instead.
Each is a fixed 14-byte record ending in an `FName`, and a run of them is
recognisable because the stride is regular and every name resolves to a card
table row. A lone match is ignored as coincidence.

That makes a deck slot an `FName` index at a known offset, which is the same
thing a gameplay tag is, so both are staged and built the same way.

## Editing

The Deck tab lists every slot with a dropdown of all 255 card table rows. Any
row can go in any slot, including cards the commander has never referenced: the
build appends the name to the commander's package first, using the same name
table insertion that tags rely on.

```
name   DA_Commander_Jeanne += 'Curse_Ascension_Sheep' (index 78)
name   DA_Commander_Jeanne @241 = Curse_Ascension_Sheep
```

The payload length never changes, since only a four-byte index is rewritten.

## Limits

The deck size is fixed. Slots can be repointed but not added or removed, because
that would change the array's length and therefore the export's `SerialSize`.
Giving a commander an eleventh card needs the same payload-resizing work that
adding a tag to a unit without one does.

Only the two starting decks are here. Cards acquired during a run come from
shops and events, which are separate systems.
