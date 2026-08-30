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

## Where the stats actually live

Unit attack and health are **not on the card**. A summon card points at a
`DA_Unit_*` asset through `CMCardData_Summon::UnitData`, and that asset carries
them:

```
DA_Unit_Human_Cataphract       AttackDamage 4    MaxHealth 13
DA_Unit_Human_GrowingSquire    AttackDamage 3    MaxHealth 7
```

Both match the collection screen exactly, and the self-test asserts it. Select
the unit asset rather than the card to edit them.

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

**You can only pick a name the asset already references.** Archer's package has
29 names of which two are tags, so those are the only choices. Introducing a tag
the asset has never used would mean appending to the package name table, which
shifts every offset in the header and each export's `SerialOffset`. That is not
implemented.

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
