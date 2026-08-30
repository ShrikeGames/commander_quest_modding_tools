"""Reading and replacing ``Texture2D`` payloads.

Commander Quest's card art uses ``PF_B8G8R8A8``, meaning uncompressed 32-bit
BGRA with a single mip and no mip chain. That is unusually convenient: importing
custom art needs no BC7/DXT encoder, just a resize and a channel swap.

Keeping the image dimensions identical keeps the ``.uexp`` byte length
identical, which in turn means the paired ``.uasset`` export table stays valid
without any edits. See ``docs/formats/texture.md``.
"""
from __future__ import annotations
import struct
from dataclasses import dataclass

SUPPORTED_FORMAT = "PF_B8G8R8A8"
"""The only pixel format handled; anything else needs a block-compression encoder."""

BULK_HEADER_GAP = 12
"""Bytes between the pixel-format string and the start of pixel data."""

TRAILER = 28
"""Bytes after the pixels: mip SizeX/SizeY/SizeZ, padding, and the package tag."""


class TextureError(RuntimeError):
    """Raised for unsupported pixel formats or unexpected texture layouts."""


@dataclass
class Texture:
    """A parsed texture payload.

    Attributes:
        width (int): Pixel width.
        height (int): Pixel height.
        pixel_format (str): Always :data:`SUPPORTED_FORMAT` for now.
        data_offset (int): Where pixel data begins in :attr:`raw`.
        raw (bytes): The complete original ``.uexp``, kept so that
            :func:`replace` can splice pixels while preserving every other byte.
    """

    width: int
    height: int
    pixel_format: str
    data_offset: int
    raw: bytes

    @property
    def byte_count(self) -> int:
        """Size of the pixel data.

        Returns:
            int: ``width * height * 4``, since every pixel is 4 bytes of BGRA.
        """
        return self.width * self.height * 4


def parse(uexp: bytes) -> Texture:
    """Locate the pixel data inside a ``Texture2D`` payload.

    Rather than walking the property stream (which would need a ``.usmap``),
    this anchors on the pixel-format string: ``SizeX``, ``SizeY`` and
    ``PackedData`` immediately precede its length prefix, and the pixels follow
    a fixed-size bulk-data header. The computed layout is then checked against
    the payload's actual length, so a mismatch fails loudly rather than
    corrupting the texture.

    Args:
        uexp (bytes): The texture's ``.uexp`` contents.

    Returns:
        Texture: The parsed texture.

    Raises:
        TextureError: If the format is not :data:`SUPPORTED_FORMAT`, or if the
            computed pixel span does not account for the whole payload, which
            is what a mipmapped texture looks like here.
    """
    marker = SUPPORTED_FORMAT.encode()
    i = uexp.find(marker)
    if i < 0:
        raise TextureError(
            f"unsupported texture: only {SUPPORTED_FORMAT} is handled "
            "(compressed formats would need a BC encoder)"
        )
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
    """Decode a texture's pixels into an image.

    Args:
        tex (Texture): A texture from :func:`parse`.

    Returns:
        PIL.Image.Image: An RGBA image, channel-swapped from the stored BGRA.
        Despite the name this returns an image object, not encoded PNG bytes;
        call ``.save(path)`` on it to write a file.
    """
    from PIL import Image
    px = tex.raw[tex.data_offset:tex.data_offset + tex.byte_count]
    img = Image.frombytes("RGBA", (tex.width, tex.height), px)
    b, g, r, a = img.split()
    return Image.merge("RGBA", (r, g, b, a))


def replace(tex: Texture, image) -> bytes:
    """Build a new ``.uexp`` with different art spliced in.

    Images that are not already the right size are scaled to *cover* and then
    centre-cropped, so the aspect ratio is preserved rather than squashed. The
    result is always exactly as long as the original payload.

    Args:
        tex (Texture): The texture being replaced, from :func:`parse`.
        image (PIL.Image.Image): Replacement art, any size or mode.

    Returns:
        bytes: A complete ``.uexp``, byte-length identical to ``tex.raw``, so
        the paired ``.uasset`` needs no changes.

    Raises:
        TextureError: If conversion produced the wrong number of bytes, which
            would indicate a resize bug rather than bad input.
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
