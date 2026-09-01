# Recovering property names

Implemented in [`tools/usmap/`](../tools/usmap/) and [`cqmod/usmap.py`](../cqmod/usmap.py).

Cooked packages omit property names and types entirely, so for a long time the
tools could only address values by byte offset. That information does still
exist in one place: the engine's own reflection data, in the running game.

`schema/usmap.json` is dumped from there. It is committed, so you only need to
regenerate it when the game updates.

## Regenerating it

```bash
# 1. launch Commander Quest and reach the main menu
sudo sysctl -w kernel.yama.ptrace_scope=0     # revert with =1 afterwards
python3 tools/usmap/dump.py
```

Roughly two seconds. Pass `--all` to dump every class in the engine rather than
only those the game's data assets use.

## How it is found

No symbols and no hardcoded addresses. Everything bootstraps from one known
name:

1. **The name pool.** Find a known name's `FNameEntry` in memory, then any
   pointer into the 128 KB block containing it, then walk back to the
   `FNameEntryAllocator` header. That header records how many blocks are live,
   which is verified against the array actually turning null just past that
   point. Every name ID is then resolvable.
2. **The class registry.** Find the object named `Class` whose own class pointer
   is itself. Every `UClass` is then just an object pointing at it, which one
   scan finds: 5,566 of them, in about a second.
3. **The properties.** Walk each class's `ChildProperties` chain for names and
   types, following `SuperStruct` for inherited ones.

Structure offsets were measured against this build by dumping known objects and
matching them to values already verified from the pak, not copied from headers:

```
UObject   +0x10 Class   +0x18 Name   +0x20 Outer
UStruct   +0x40 Super   +0x48 Children   +0x50 ChildProperties   +0x58 PropertiesSize
FField    +0x08 Class   +0x18 Next   +0x20 Name   +0x34 ElementSize
```

**Property order is derived-class-first.** A class's own properties are numbered
before its parent's, which is the opposite of the obvious guess and produced an
off-by-one that only showed up when the result was checked against known assets.

## Serialized sizes

Reflection reports in-memory sizes, which are not what a cooked payload uses: an
`ObjectProperty` is an 8-byte pointer in memory but a 4-byte package index on
disk. Knowing every property's *type* reduces the size problem to about a dozen
unknowns against thousands of equations, which solves cleanly:

| type | bytes | type | bytes |
|---|---|---|---|
| BoolProperty | 1 | IntProperty | 4 |
| EnumProperty | 1 | FloatProperty | 4 |
| ObjectProperty | 4 | NameProperty | 8 |
| ClassProperty | 4 | | |

All 416 usable observations are reproduced exactly. Text, strings, arrays,
structs, maps and sets are variable length; text spans are measured from the
payload, and placement stops at the first other variable property.

## The result

```
Insight (DA_Card_Supply_Human_Insight)
  +1 CMCardData_Supply
     [ 0] cardName            TextProperty     off=10
     [ 1] CardDesc            TextProperty     off=41
     [ 7] CardIllustration    ObjectProperty   off=71   value=-13
     [11] UsingSound          ObjectProperty   off=75   value=-6
  +2 CMEffectData_MoveCard
     [ 0] FromPileType        EnumProperty     off=103  value=5
     [ 3] Count               IntProperty      off=104  value=1
  +3 CMEffectData_MoveCard
     [ 0] FromPileType        EnumProperty     off=117  value=4
     [ 1] ToPileType          EnumProperty     off=118  value=5
     [ 2] PileLocation        EnumProperty     zero     value=0
     [ 3] Count               IntProperty      off=119  value=1
     [ 5] bIsManualSelection  BoolProperty     off=123  value=1
```

Offsets 104 and 119, originally found by diffing hex against the `+` variant,
are `CMEffectData_MoveCard::Count`. The self-test asserts this, so a schema that
stops lining up with the pak fails loudly.

`PileLocation` showing as `zero` is the header bitmap in action: a zero-valued
property is a bit in the header and occupies no bytes.

## Object references are read only

An object or class property serializes as a package index, so typing a different
number into one does not change a value: it repoints the reference at whatever
else sits at that index. That fails quietly. Setting a unit's `AttackType` to 1
aims it at a VFX export instead of its attack type, leaving the unit with no
attack, so any `AttackDamage` on it does nothing.

References are therefore shown resolved and read only:

```
AttackType          ObjectProperty   -> CMUnitAttackType_MeleeTarget
BehaviorTreeAsset   ObjectProperty   -> BT_MinionAIBehaviorTree
```

Only plain integers can be typed. Repointing a reference safely needs the same
import table work that swapping a unit's mesh does.

## Where the stats actually live

Unit attack and health are **not on the card**. A summon card points at a
`DA_Unit_*` asset through `CMCardData_Summon::UnitData`, and that asset carries
them:

```
DA_Unit_Human_Cataphract       AttackDamage 4    MaxHealth 13
DA_Unit_Human_GrowingSquire    AttackDamage 3    MaxHealth 7
```

Both match the collection screen exactly, and the self-test asserts it. The
editor lists a linked asset's properties alongside the card's own, prefixed with
the asset they belong to, so a summon card's health and attack can be edited
without going to find the unit.

Base, enhanced and enemy units are **separate assets** with independent stats:
`DA_Unit_Human_GrowingSquire` is 7 health and 3 attack while
`DA_Unit_Human_GrowingSquire+` is 8 and 4. Editing one does not affect the
others, which is a common reason a change appears to do nothing in game.

Reaching `MaxHealth` needs one extra step, because it sits at index 1 right
after `Tags`, a `StructProperty` whose size no type table can give.

Solving a fixed size for it would be wrong: `Tags` is a gameplay tag container
and its length depends on how many tags a unit has, 12 bytes for one and 20 for
two. An early version derived 12, applied it everywhere, and silently shifted
every later property, which is how `DA_Unit_Human_Archer` came to report a
`MaxHealth` of 0. It was reading the tail of `Tags`.

Placement is therefore **validated against the payload length**. Container
properties serialize as a count followed by elements, so their count is read and
the plausible element widths tried; a layout is only accepted if it consumes the
value region exactly, to the byte. Anything that does not add up falls back to
placing only the prefix that is certain, rather than reporting offsets that
might be wrong.

All 325 unit assets now place completely, and the recovered stats match the
collection screen for every card checked: Militia 2/4, Ambush Cavalry 3/8,
Assassin 6/2, Cataphract 4/13, Budding Squire 3/7.

Capturing struct and array element types made the guessing unnecessary in most
cases: a `GameplayTagContainer` is `4 + 8n`, an array is `4 + n` times its
element width. Across the whole game 82% of exports now place completely.

## Gameplay tags

`StructProperty` now records which struct it holds, so a `GameplayTagContainer`
is parsed rather than guessed: a count followed by that many `FName` values.
Those resolve to real names, and the editor lists them as their own rows.

```
DA_Unit_Human_Archer   Tags -> Minion.AttackType.Projectile, Card.SummonType.Militia
DA_Unit_Human_Cataphract  Tags -> Minion.Type.Cavalry
```

A tag is an `FName`, which is an index into the owning package's name table, so
changing one is a four-byte write and the editor offers a dropdown of the names
that package carries.

Any of the 42 tags used anywhere in the game can be chosen, not just the ones a
package happens to reference. If the asset has never used a tag, its name is
appended to the package name table at build time.

Growing that table shifts everything after it, so the insertion rewrites
`TotalHeaderSize`, every offset field in the summary, the generation record's
name count, and each export's `SerialOffset`. Export payloads are untouched, and
because the header size and the serial offsets move together, every export still
resolves to exactly the same bytes of the `.uexp`.

This was only attempted after parsing the summary end to end and confirming the
walk lands precisely on `NameOffset`, which is what proves no field was missed.
All **1,790** data assets survive an insertion with their names, imports, exports
and payload slices intact, and the self-test checks that on every one of them.

One asset caught a real bug: `DA_Gear_Priest'sBreastplate` has a Unicode
apostrophe in its name, so its package name string is UTF-16 with a negative
length. Stepping over it as though the length were positive walked backwards
through the file.

## Linked assets

Object and class properties serialize as an `FPackageIndex`, and a negative one
indexes the import table, which names what it points at. The editor resolves
those and offers a button per reference, so a summon card takes one click to
reach the `DA_Unit_*` asset holding its attack and health rather than a search.

```
DA_Card_Summon_Human_Assassin
   summonUnit -> BP_Unit_Assassin_C
   UnitData   -> DA_Unit_Human_Assassin      (MaxHealth 2, AttackDamage 6)
   ShapeClass -> BP_CardUseShape_Point_C
```

334 of 406 summon cards resolve their unit this way. The rest reference it
through a property that placement cannot reach yet.

## Not every unit has tags

155 of 325 units carry a `Tags` property and 170 do not, which is correct rather
than a gap in placement. Unversioned serialization omits any property left at
its default, so a unit with no tags has no entry at all.

The consequence is that a tag cannot currently be *added* to a unit that has
none: that means inserting an index into the export's unversioned header and
widening its payload, which changes the export's `SerialSize`. The name table
work shows that kind of surgery is tractable, but it is not implemented.

## What is still not named

Placement stops at the first variable-length property it cannot measure, so on a
card the tail after `UseEffects` (an `ArrayProperty`) is unresolved. Parsing
array and struct payloads would extend this further.

Placement also halts at any variable-size struct it cannot measure. On a summon
card that is `UseRuleData`, which hides the six properties after it.

And the schema settles one earlier puzzle: **mana cost is not a property at
all.** Searching all 309 classes and 5,146 script structs finds no cost field on
any card type. `CMCardDataRow::price` is the shop price, `CMCommanderData::BaseMana`
is the player's pool, and the tag containers hold card types rather than costs.
`GetDisplayUseCost` exists only as a function, so the number on the collection
screen is computed at runtime. That is why correlating known costs against card
payloads found nothing, and it is not something a value edit can change.

## A layout that fits is not always the right one

Placement searches for an arrangement of properties that consumes an export's
value region exactly. That check is strong but not a proof: a container whose
element width has to be guessed can be too big while a later one is too small,
and the total still lands on the right byte.

Where the element width is known there is nothing to guess, so it is not
guessed. A gameplay tag is an `FName`, so a container of them is a count plus
eight bytes per tag; an array of a fixed-size type has a fixed stride. Offering
alternatives for those invented ambiguity that did not exist, and it was that
invented ambiguity which mis-placed `MaxHealth` on seventeen units: the search
sized their `Tags` container wrongly, balanced the error against a later array,
and put the health value inside the tag data.

What remains genuinely ambiguous is left unplaced. Across the catalog that is
two exports out of 7,585, against 5,790 placed uniquely, and pinning the known
widths raised the number of fields reachable overall from 18,742 to 19,740.
Being stricter cost nothing and gained coverage, because the guesses were
buying wrong answers rather than extra ones.
