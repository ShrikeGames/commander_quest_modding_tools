"""Texture2D payloads.

Commander Quest's card art is PF_B8G8R8A8 -- uncompressed 32-bit BGRA, a single
mip, no mip chain -- so importing custom art needs no BC7/DXT encoder: resize,
swap channel order, splice the pixels back in.

Keeping the image dimensions identical keeps the .uexp byte length identical,
which means the paired .uasset export table needs no edits at all.
"""
from __future__ import annotations
import struct
from dataclasses import dataclass

SUPPORTED_FORMAT = "PF_B8G8R8A8"
BULK_HEADER_GAP = 12     # between the pixel-format string and the pixel data
TRAILER = 28             # mip SizeX/SizeY/SizeZ + padding + package tag


class TextureError(RuntimeError):
    pass


@dataclass
class Texture:
    width: int
    height: int
    pixel_format: str
    data_offset: int
    raw: bytes

    @property
    def byte_count(self) -> int:
        return self.width * self.height * 4


def parse(uexp: bytes) -> Texture:
    marker = SUPPORTED_FORMAT.encode()
    i = uexp.find(marker)
    if i < 0:
        raise TextureError(
            f"unsupported texture: only {SUPPORTED_FORMAT} is handled "
            "(compressed formats would need a BC encoder)"
        )
    # FString length precedes the characters; SizeX/SizeY/PackedData precede that.
    len_at = i - 4
    w, h, _packed = struct.unpack_from("<iii", uexp, len_at - 12)
    (slen,) = struct.unpack_from("<i", uexp, len_at)
    start = len_at + 4 + slen + BULK_HEADER_GAP
    need = w * h * 4
    if start + need + TRAILER != len(uexp):
        raise TextureError(
            f"unexpected texture layout: {w}x{h} needs {need} bytes at offset "
            f"{start}, but the payload is {len(uexp)} bytes "
            "(mipmapped textures are not supported yet)"
        )
    return Texture(w, h, SUPPORTED_FORMAT, start, uexp)


def to_png_bytes(tex: Texture):
    from PIL import Image
    px = tex.raw[tex.data_offset:tex.data_offset + tex.byte_count]
    img = Image.frombytes("RGBA", (tex.width, tex.height), px)
    b, g, r, a = img.split()
    return Image.merge("RGBA", (r, g, b, a))


def replace(tex: Texture, image) -> bytes:
    """Return a new .uexp with `image` (a PIL Image) spliced in.

    The image is scaled to cover and centre-cropped, so aspect ratio is kept
    rather than squashed.
    """
    from PIL import Image
    im = image.convert("RGBA")
    sw, sh = im.size
    if (sw, sh) != (tex.width, tex.height):
        scale = max(tex.width / sw, tex.height / sh)
        im = im.resize((max(tex.width, round(sw * scale)),
                        max(tex.height, round(sh * scale))), Image.LANCZOS)
        left = (im.width - tex.width) // 2
        top = (im.height - tex.height) // 2
        im = im.crop((left, top, left + tex.width, top + tex.height))
    r, g, b, a = im.split()
    bgra = Image.merge("RGBA", (b, g, r, a)).tobytes()
    if len(bgra) != tex.byte_count:
        raise TextureError(f"converted image is {len(bgra)} bytes, expected {tex.byte_count}")
    out = bytearray(tex.raw)
    out[tex.data_offset:tex.data_offset + tex.byte_count] = bgra
    return bytes(out)
