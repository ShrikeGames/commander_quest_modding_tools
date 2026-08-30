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

Reaching `MaxHealth` needs one extra step. It sits at index 1, right after
`Tags`, a `StructProperty` whose size no type table can give. Seeding each
class's size equations with the sizes the types already provide leaves many
equations with a single unknown, which determines it: `CMUnitData::Tags` is 12
bytes. That resolves 206 further properties across the game.

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
