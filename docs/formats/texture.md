# Textures

Implemented in [`cqmod/texture.py`](../../cqmod/texture.py).

Card art is the easiest thing in this codebase to modify, thanks to one lucky
property of how the game cooks it.

## The format

Card images use **`PF_B8G8R8A8`**, meaning uncompressed 32-bit BGRA with a single
mip and no mip chain. There is no BC7/DXT block compression to encode, so
importing custom art is a resize and a channel swap.

A representative card:

```
T_Image_Card_Supply_Human_Insight.uexp   4,160,153 bytes
  1040 x 1000 x 4 = 4,160,000 bytes of pixels
  starting at offset 125, with a 28-byte trailer
```

## Locating the pixels

Rather than walking the property stream, which would need a `.usmap`, the parser
anchors on the pixel-format string:

```
int32   SizeX               1040
int32   SizeY               1000
int32   PackedData             1
FString PixelFormatName     "PF_B8G8R8A8"
... 12 bytes of bulk-data header ...
[ SizeX * SizeY * 4 bytes of BGRA pixels ]
int32   MipSizeX, MipSizeY, MipSizeZ
... 12 bytes padding ...
uint32  PackageTag          0x9E2A83C1
```

The computed span is then checked against the payload's actual length. A
mismatch, which is what a mipmapped texture looks like, raises `TextureError`
rather than corrupting the file.

## Replacing art

```python
from PIL import Image
from cqmod import texture

tex = texture.parse(pak.read(path + ".uexp"))
new_uexp = texture.replace(tex, Image.open("my_art.png"))
```

Images that are not already the right size are scaled to **cover** and then
centre-cropped, preserving aspect ratio rather than squashing.

The result is always exactly as long as the original payload. That is the whole
point: the paired `.uasset` records the export's `SerialSize`, so an identical
length means it needs no edits at all.

## Extracting art

`texture.to_png_bytes(tex)` returns a PIL image with channels swapped back to
RGBA. Despite the name it returns an image object, not encoded bytes, so call
`.save(path)` on it. The GUI exposes this as *Export PNG…*, which is the easy way
to get a base to paint over.

## Limitations

Only `PF_B8G8R8A8` is supported. Block-compressed formats would need a BC
encoder, and mipmapped textures would need the whole chain regenerated and the
payload length recomputed, which would in turn mean rewriting the `.uasset`.
