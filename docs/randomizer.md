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
| Relic icons | shuffle | cosmetic, and cheap at about 34 MB |
| Commander starting decks | random | ten cards drawn from the whole pool |
| Unit tags | shuffle | gameplay tags redistributed between units that have them |
| Card art | shuffle | cosmetic, but see the size warning below |

Relics are `CMGearDefinition` in the data. Their effects are separate exports
just as cards' are, so the same numeric knobs are reachable: 151 relics carry
`ResourceAmount`, `TriggerCount`, `BuffValue`, `HpAmmount` and similar.

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

## Image categories cost space

Swapping an image copies it, because pointing a card at another card's
illustration would need a reference change that is not supported yet. The two
image categories are therefore very different in cost:

| category | added size |
|---|---|
| Relic icons | about 34 MB |
| Card art | about 1 GB |

The tab estimates this live as you tick categories, so the cost is visible
before you generate rather than after the build. Card art is off by default.

Everything else together is around 1,200 edits and builds in a second or two.
