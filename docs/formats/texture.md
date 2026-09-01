# Textures

Implemented in [`cqmod/texture.py`](../../cqmod/texture.py) and
[`cqmod/dxt.py`](../../cqmod/dxt.py). For the practical side, replacing and
swapping art, see [Replacing textures](../textures.md).

## The format

A cooked `Texture2D` stores `FTexturePlatformData`: the dimensions, a pixel
format name, then a mip chain. Three formats appear in this game.

| format | meaning | where |
|---|---|---|
| `PF_B8G8R8A8` | uncompressed 32-bit BGRA | card illustrations |
| `PF_DXT1` | BC1, 8 bytes per 4x4 block, no alpha | opaque model and world art |
| `PF_DXT5` | BC3, 16 bytes per block, alpha included | model and world art with transparency |

Card art is almost always uncompressed and one level deep, which is what made it
the easy first target. It is not a rule: of 330 card illustrations, one is
`PF_DXT1` and sixteen carry an eleven-level chain in a `.ubulk`. Assuming
otherwise is what broke the special sheep cards.

## Locating the pixels

Rather than walking the property stream, the parser anchors on the pixel-format
string, which is stable and unambiguous:

```
int32   SizeX               1040
int32   SizeY               1000
int32   PackedData             1
FString PixelFormatName     "PF_B8G8R8A8"
... bulk-data header ...
[ mip 0 ]
[ mip 1 ... ]
uint32  PackageTag          0x9E2A83C1
```

Each mip records its own size and a flag saying whether its bytes live inline in
the `.uexp` or in the sibling `.ubulk`. `FirstMipToSerialize` decides where the
chain starts, and large mips are the ones pushed out to bulk storage.

## Replacing art

```python
from PIL import Image
from cqmod import texture

tex = texture.parse(pak.read(path + ".uexp"), pak.read(path + ".ubulk"))
new_uexp, new_ubulk = texture.replace(tex, Image.open("my_art.png"))
```

Every mip is regenerated at its recorded size and re-encoded in the texture's
own format, so a block-compressed texture stays block-compressed and a chain
stays a chain. Images that are not already the right size are scaled to **cover**
and centre-cropped, preserving aspect ratio rather than squashing.

The result is exactly as long as the original payload. That is the point: the
paired `.uasset` records the export's `SerialSize`, so an identical length means
it needs no edits at all. Keeping dimensions fixed is what buys that.

## Extracting art

`texture.to_image(tex)` returns a PIL image. Uncompressed data is a channel swap;
block-compressed data is wrapped in a minimal DDS header and handed to PIL, which
already reads DDS, rather than decoded by hand. The GUI exposes this as
*Export PNG...*, which is the easy way to get a base to paint over.

## Limits

Dimensions cannot change, since that would change the payload length and the mip
chain with it. Formats outside the three above, such as cube maps and volume
textures, raise `TextureError` rather than being silently corrupted.
