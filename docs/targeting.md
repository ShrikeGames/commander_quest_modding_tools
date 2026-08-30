# What an effect targets

Some effects pick their targets by a **unit class branch**, others by a
**gameplay tag**, and the two are edited very differently.

## Class branch, the common case

Three Lions Flag reads *"Increase the number of units summoned by 3 when
summoning cavalry units"*, and stores that as:

```
CMEffectData_CardSummonCount
   BuffValue              3
   TargetUnitClassBranch  2      <- cavalry
```

`TargetUnitClassBranch` is an enum matching `CMUnitData::UnitClassBranch`, so
changing the number changes who the effect applies to. It is one byte, and the
editor writes it at that width.

`ECMUnitClassBranch` has the values All, Beast, Cavalry, Commander, Elemental,
Environment, Ghost, Infantry, Machine, Mechanic and None. The value-to-name
order is not recorded in the cooked data, but grouping every unit by the value
it uses makes the important ones unambiguous:

| value | units that use it | so it is |
|---|---|---|
| 2 | Cataphract, Ambush Cavalry, Griffin Rider, Elite Knight | Cavalry |
| 4 | Sheep, Dragon, Hatchling, Large Spider, Ghost Bat | Beast |
| 6 | Ash Spirit, Bomb Spirit, Letter Spirit | Ghost |
| 7 | every Commander unit | Commander |
| 8 | Chest, Mana Vein, Metal Chest | Environment |
| 9 | Ballista, Bibi Beep, Gyrocopter | Machine |

So pointing Three Lions Flag at beasts is a single change,
`TargetUnitClassBranch` from 2 to 4, and sheep are already beasts.

## Gameplay tags, the harder case

The same effect also has `TargetCardTag`, a `GameplayTagContainer`, so tag
targeting is supported by the game. Three Lions Flag simply does not use it, and
the container is absent from its payload.

Using it would need two things that are not implemented:

- **Adding a container property.** Only fixed-size types can be added, and a tag
  container's length depends on how many tags it holds. See
  [adding properties](adding-properties.md).
- **Adding a tag to a unit that has none.** Same problem: growing an existing
  container rather than repointing an entry in it.

Introducing a brand new tag is at least possible in principle.
`Commander/Config/DefaultGameplayTags.ini` registers all 254 of the game's tags
and lives in the pak, so a mod can override it with an extra `Tag="..."` line.
Until containers can be resized there is nothing to attach that tag to.

## Reading config files

Those ini files are the only per-entry encrypted entries in the pak, all 71 of
them, and they are encrypted *and* Oodle compressed. Encrypted data is stored
padded to the AES block size, so the padded span is decrypted and then only the
real compressed length handed to the decoder; passing the padding makes Oodle
fail outright rather than return partial output.
