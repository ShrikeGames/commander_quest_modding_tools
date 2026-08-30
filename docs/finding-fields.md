# Finding the number you want to change

Property names need a `.usmap` we do not have, so gameplay values are addressed
by **byte offset** into an asset's `.uexp`. The problem is finding the right
offset among hundreds of meaningless ones.

## Use the variant diff

The game ships an upgraded `+` variant of most cards, and a variant differs from
its base almost exclusively in the values that matter. Diffing the two points
straight at them.

In the GUI: select a card, open the **Values** tab, press
**Find fields vs '+' variant**.

From Python:

```python
from cqmod import config, catalog, diff, uasset
from cqmod.pak import PakReader

pak = PakReader(config.pak_path(), config.aes_key())
by = {a.name: a for a in catalog.build(pak)}

a, b = by["DA_Card_Supply_Human_Insight"], by["DA_Card_Supply_Human_Insight+"]
found = diff.compare(
    a, pak.read(a.uexp),
    b, pak.read(b.uexp),
    uasset.parse(pak.read(a.uasset)).names,
    uasset.parse(pak.read(b.uasset)).names,
    only_plausible=True,
)
for f in found:
    print(f.offset_a, f.export_class, f.value_a, "->", f.value_b)
```

```
104 CMEffectData_MoveCard 1 -> 2
119 CMEffectData_MoveCard 1 -> 2
```

Insight reads *"Draw 1 card. Send 1 card from your hand to the draw pile."* and
its upgrade doubles both numbers. Two candidates, both correct.

## How it filters

A naive diff of those two cards reports 36 differences. Three things cut that to
two:

- **Text spans are excluded.** A card and its variant have different `FText`
  keys (`Insight_Title` vs `Insight+_Desc`), and the differing string bytes would
  otherwise dominate the output.
- **Implausible values are dropped.** Gameplay numbers are small and
  non-negative. A 4-byte window that straddles a string or pointer produces
  values like `1601464423`; `only_plausible=True` discards them.
- **Overlapping matches are collapsed.** Offsets are scanned one byte at a time,
  so a single changed byte appears at up to four consecutive offsets. Only the
  best-ranked representative of each run is kept.

Exports are matched by class and ordinal rather than position, so a base card
missing an export its variant has still lines up.

## How well it works

Across all **281** base/variant pairs in the game:

| candidates | pairs |
|---|---|
| 0 | 142 |
| 1 | 87 |
| 2 | 29 |
| 3 | 12 |
| 4 to 6 | 11 |

Median 0, mean 0.8, maximum 6. Some worked examples:

| card | text | found |
|---|---|---|
| `Infra_AlcoholicFurnace` | "Gain 5 Steel each time you use a grog card" | `5 -> 10` |
| `Infra_AltaroftheDead` | "Increase Attack Damage of Ethereal Body units by 2" | `2 -> 3` |
| `Infra_BladeMagicCircle` | "Deal 2 damage to 2 random enemy units" | `2 -> 3` |

## When it returns nothing

142 pairs yield no candidates. The upgrade changes something the diff cannot
see: text only, an added effect object, a float, an enum, or a boolean. Fall
back to reading the raw value list and reasoning from the export classes. An
export named `CMEffectData_AddResource` in a card that grants resources is a
strong hint about which numbers matter.

## Verify in the game

**Do not assume you know which field is which.** During the proof of concept,
Insight's two `MoveCard` effects were assumed to be draw-then-send, matching the
description text. They are serialized in the opposite order: offset 104 is
*send*, offset 119 is *draw*. Setting the wrong one produced a card that
discarded the player's hand instead of drawing.

Export order does not follow the description text. Change one value, run the
game, and confirm before building on it.
