# Replacing textures

Implemented in [`cqmod/texture.py`](../cqmod/texture.py) and
[`cqmod/dxt.py`](../cqmod/dxt.py).

Two kinds of texture exist in this game and they need different handling.

| | format | where |
|---|---|---|
| Card art | `PF_B8G8R8A8`, single mip, no bulk file | card illustrations |
| Unit and world art | `PF_DXT1` (753) or `PF_DXT5` (122), full mip chain | models, tiles, effects |

Both use the same mip chain layout, so both take the same path through the
parser. Card art usually has a single level, but not always: several
illustrations, including the special sheep cards, are 1024x1024 with eleven
levels split across a `.ubulk`. Treating uncompressed art as necessarily
single-mip broke exactly those.

All 638 card illustrations parse, and 2,311 textures across the game. The 155
that do not are engine assets rather than art: cube maps, volume textures, BRDF
lookup tables, and level lightmap data whose bulk files are shared between
textures.

## Where the pixels live

A block-compressed texture splits its mip chain across two files. The largest
levels sit in a `.ubulk` laid out back to back from offset zero, and the rest
are inline in the `.uexp`, each written as its level index, then its data, then
its width, height and depth.

```
T_Human_Assasin_D   256x256 PF_DXT1, 9 mips
   mip0  256x256   32768 bytes  ubulk @0
   mip1  128x128    8192 bytes  ubulk @32768
   mip2   64x64     2048 bytes  uexp  @145
   mip3   32x32      512 bytes  uexp  @2209
   ...
   mip8    1x1         8 bytes  uexp  @2977
```

Large textures have their top levels cooked out, so the chain starts at
`FirstMipToSerialize` rather than at the full size: `T_Cactus_D` is nominally
2048x2048 but its first stored level is 128x128. Missing that accounted for all
101 initial parse failures.

## Encoding

DXT1 and DXT5 are block formats, so replacing one means encoding 4x4 pixel
blocks rather than copying bytes. `cqmod/dxt.py` implements both: DXT1 stores
two RGB565 endpoints and two bits per pixel along a four colour ramp, and DXT5
prefixes an eight byte alpha block holding two endpoints and three bits per
pixel.

Endpoints come from each block's extremes, which is fast and good enough here.
Round-tripping a real game texture through encode and decode gives a mean
channel error of **1.97 / 255**, and a 256x256 texture encodes in about 0.02
seconds.

Every mip is re-encoded at its own size, so each replacement is exactly as long
as the data it overwrites. No offset in either file moves, which is what keeps
the paired `.uasset` valid without any header surgery.

## Using it

**Replace art** takes any image file. It is scaled to cover and centre-cropped
to the target's dimensions, then encoded to the target's format.

**Copy from game art** uses another texture already in the game. The source is
decoded and re-encoded to the target's size and format, so the two need not
match: a 1024x1024 `PF_DXT5` source can be written into a 256x256 `PF_DXT1`
target. This is the quick way to make one unit wear another's skin.

**Export PNG** writes the current art out, including block-compressed textures,
which is the easy way to get a base to paint over.

## What this does not do

Copying art changes the pixels a material samples. It does not repoint a unit at
a different mesh: that is an object reference in the Blueprint, and pointing it
somewhere new means adding an entry to the import table rather than the name
table. The mechanics are the same shifting problem that name insertion already
solves, but it is not implemented.

Authoring a new skeletal mesh remains out of scope. See
[unit graphics](unit-graphics.md).

## Finding a unit's texture

A card names its illustration directly. A unit does not: its appearance hangs
off the Blueprint the summon card points at, and only 22 of 325 unit assets
reference any texture at all. Those that do usually reference something
incidental, which is a trap: the Gyrocopter imports `T_Unit_Notify_NoSteel`, the
"out of steel" status icon, so taking the first texture an asset mentions showed
a unit wearing a warning symbol.

`cqmod/artchain.py` follows the real chain instead:

```
DA_Card_Summon_Human_Assassin
  summonUnit -> BP_Unit_Assassin
                  -> MI_Human_Assasin -> T_Human_Assasin_D
  UnitData   -> DA_Unit_Human_Assassin
```

The unit does not name its own Blueprint, but the summon card names both, so the
pairing is recovered from the cards. From there the walk follows materials and
mesh material slots, because some Blueprints reach their art only through the
mesh: the Gyrocopter's Blueprint imports shared placeholder materials from
`Character_OLD`, and its real texture hangs off the `Dwarf_Gyrocopter` mesh.

A Blueprint reaches several textures, so all of them are offered and ranked. A
clear name match wins, otherwise the nearest one does. Both signals are needed:
name alone picks a blood decal for the Assassin, whose texture is spelled
`T_Human_Assasin_D` with one fewer `s` and shares no token with its Blueprint;
depth alone picks an elephant for the Gyrocopter.

322 of 325 units resolve at least one texture, and the editor lists them all so
the guess can be overridden.

## Repointing instead of replacing

Replacing a texture rewrites its pixels. Pointing an asset at a *different*
texture is cheaper and often what is actually wanted, but it needs an entry in
the referring package's import table, and an asset only imports the textures it
already uses.

`uasset.add_import` appends an `FObjectImport`, a 32-byte record of class
package, class name, outer index and object name. `uasset.add_asset_reference`
adds the pair that a reference needs, one entry for the package that holds the
object and one for the object itself, reusing either if it is already present.
Both return the new `FPackageIndex`, which is negative and counts from -1.

Growing the table shifts everything after it, so the summary offsets, the total
header size and every export's `SerialOffset` move by 32 bytes each time. The
export payloads themselves are untouched, and because the header size and the
serial offsets move together each export still resolves to the same bytes of
the `.uexp`. The self-test grows all 1,790 data assets and checks that the
exports still slice identically afterwards.

`Project.set_reference` records one of these as a pending edit. It runs before
value edits during a build, since the added imports change nothing in the
payload but the header edits have to be applied in a fixed order.
