# Randomizer

Implemented in [`cqmod/randomizer.py`](../cqmod/randomizer.py), shown on the
**Randomizer** tab.

Pick what to randomize, press generate, and the result is staged as ordinary
project edits. Nothing is written until you build, so a run can be reviewed on
the Pending edits tab, adjusted by hand, and saved as a project like any other
mod.

## Reproducibility

A run is fully determined by its seed and options. Each category draws from its
own generator seeded from the run seed and the category name, so turning one
category on does not change what another rolls, and sharing a seed shares the
exact mod.

## Modes

**Shuffle** redistributes the values that already exist. Totals and spread are
preserved, so the game stays about as balanced as it started while nothing is
where you expect it.

**Random** rolls a fresh value inside the range the game itself uses. The
**variance** setting widens that range, as a fraction of it, for runs that
should be less reasonable.

## Categories

| category | modes | notes |
|---|---|---|
| Unit health | shuffle, random | `MaxHealth` across every unit |
| Unit attack | shuffle, random | `AttackDamage`; units without an attack keep none |
| Unit class branch | shuffle | cavalry, beast, machine and so on, which changes what class-targeted effects hit |
| Unit move speed | shuffle, random | `MoveSpeedStatus` |
| Unit attack range | shuffle, random | `AttackRangeStatus`, banded (see below) |
| Unit attack speed | shuffle, random | `AttackCooltimeStatus`, banded |
| Card effect numbers | shuffle, random | integers inside card effects, such as cards drawn or damage dealt |
| Relic effect numbers | shuffle, random | the same for relics: resource amounts, trigger counts, buff values |
| Relic rarity | shuffle | changes what turns up in shops and rewards |
| Relic icons | shuffle | cosmetic, and cheap: the reference moves, not the image |
| Commander starting decks | random | ten cards drawn from the whole pool |
| Unit tags | shuffle | gameplay tags redistributed between units that have them |
| Card art | shuffle | cosmetic, and likewise repointed rather than copied |
| Quest rarity | shuffle | `QuestRarity`, which changes how often a quest is offered |
| Quest requirements | shuffle, random | how much a quest asks of you, pooled by kind |
| Quest rewards | shuffle | which card, relic or consumable a quest hands out |
| Event numbers | shuffle, random | amounts events give and take: gold, cards, relics, consumables |
| Event health effects | shuffle, random | the health ratios events apply to your commander |
| Event relic rarity | shuffle | what rarity of relic an event awards |

Relics are `CMGearDefinition` in the data. Their effects are separate exports
just as cards' are, so the same numeric knobs are reachable: 151 relics carry
`ResourceAmount`, `TriggerCount`, `BuffValue`, `HpAmmount` and similar.

## Quests and events

Quests are `CMQuestDefinition` and map events are `CMInteractionEventDefinition`.
Both keep their moving parts in sub-objects of the same asset: a quest holds
`QuestCompleteCondition_*` and `QuestCompleteReward_*` exports, and an event
holds a `CMEventActionParameter_*` export per thing it does.

### Amounts are pooled by what they count

The same property name means different things on different classes. `TakeAmount`
is two or three consumables on `TakeConsumable` and up to three hundred gold on
`TakeGold`, so pooling by name alone would have an event hand out three gold or
three hundred potions. Occurrences are therefore grouped by class **and** name,
and only integers between 1 and 999 are taken. One reward slot stores 65536,
which is not a count of anything and would otherwise become a demand for 65536
cards.

### Dialogue is left alone

Events carry a `CMTalkBoxActionParameter_DefaultOneIntParameter` per line of
dialogue, whose single `IntValue` is the page the line belongs to: 229 of the
302 are `1`, and the rest run 2, 3, 4 upward. It reads like a quantity and is
not one, so rerolling it would send a conversation to the wrong line. Only
`CMEventActionParameter_*` exports are collected, which excludes it by
construction, and the self-test asserts none of those 302 fields is ever
written.

### Health effects are floats, and some are negative

`MaxHealthIncreaseRatio` is `0.2` on an event that grants maximum health and
`-0.2` on one that takes it. These are the only float fields the randomizer
writes, which is why `Project.set_value` grew an `is_float` flag: packing `0.2`
as an integer would store a number near zero instead. Rolling is bounded by
what the pool contains rather than clamped at zero, so a shrine that healed you
can end up costing you, but never by an amount the game has never used.

### Rewards are found by their table

A reward stores an `FDataTableRowHandle`: two flag bytes, a reference to the
table, then the row's `FName`, the same 14-byte shape a starting deck slot
uses. Property placement lands a couple of bytes off on this struct, so the row
is located by its table instead. A `DataTable` import inside a reward export is
unambiguous, and the row name sits four bytes past it. That finds all 47
rewards, and the one export it finds nothing in is the gold reward, which has
no row handle.

The table is also what the reward *is*, so grouping by it keeps a card reward a
card and a relic reward a relic. That matters more than it sounds: consumable
rows are keyed in Korean and match no asset name, so there is no list of valid
rows to check against. It also means a shuffled consumable reward usually needs
a row name its quest has never carried, which is what `add_name`'s wide
`FString` form is for.

## Range and attack speed are banded

These are tier enums whose meaning depends on the attack type, and the game
gives melee and projectile types **disjoint** sets:

```
AttackRangeStatus   melee types      6, 7
                    projectile types 9, 10, 11, 12, 13
```

Randomizing across the whole set would give archers a melee reach and swordsmen
a bowshot, so each attack type class is treated as its own pool. Melee units
stay short ranged and ranged units stay long ranged, which keeps the game's own
caps without needing to know what each tier means in tiles. The self-test
asserts the two bands never overlap after a run.

## Commanders are protected

Commander units are excluded unless **Include commanders** is ticked. Rolling a
commander's health down to a few points makes a run unwinnable rather than
interesting. Commander *cards* are ordinary cards and are randomized normally.

## Image categories repoint rather than copy

Swapping an image changes which texture the card points at instead of copying
pixels into the mod. A card holds its illustration as an object reference, and
`cqmod/uasset.py` can grow a package's import table, so the reference can be
aimed at a texture the card never mentioned. The mod then carries a rewritten
header of a couple of kilobytes in place of a multi-megabyte image.

| category | swaps | added size |
|---|---|---|
| Relic icons | 151 | about 0.3 MB |
| Card art | 663 | about 2.7 MB |

The tab estimates this live as you tick categories. Both image categories are
now cheap enough to leave on, and everything else together is around 1,200
edits, so a full run builds in a second or two.

## Finding the reference

Property placement walks an export's properties in order and stops at the first
one whose length it cannot measure, which on a summon card comes before the
illustration. When placement cannot reach the property, the reference is found
by what it points at instead: the texture's entry in the import table has a
known package index, and the payload is searched for that value. A match counts
only when it is unique, so a repeated value is left alone rather than guessed
at. The two methods together reach 663 of the game's cards where placement
alone reached 61.
