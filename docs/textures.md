# Replacing textures

Implemented in [`cqmod/texture.py`](../cqmod/texture.py) and
[`cqmod/dxt.py`](../cqmod/dxt.py).

Two kinds of texture exist in this game and they need different handling.

| | format | where |
|---|---|---|
| Card art | `PF_B8G8R8A8`, single mip, no bulk file | card illustrations |
| Unit and world art | `PF_DXT1` (753) or `PF_DXT5` (122), full mip chain | models, tiles, effects |

892 of the 900 textures in the game parse. The eight that do not are
uncompressed textures that carry a mip chain, a combination the single-mip path
does not handle.

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
