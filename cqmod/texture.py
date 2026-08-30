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
"""Uncompressed BGRA, used by card art."""

BLOCK_FORMATS = ("PF_DXT1", "PF_DXT5")
"""Block-compressed formats, used by unit and world textures."""

ALL_FORMATS = (SUPPORTED_FORMAT,) + BLOCK_FORMATS

BULK_HEADER_GAP = 12
"""Bytes between the pixel-format string and the start of pixel data."""

TRAILER = 28
"""Bytes after the pixels: mip SizeX/SizeY/SizeZ, padding, and the package tag."""


class TextureError(RuntimeError):
    """Raised for unsupported pixel formats or unexpected texture layouts."""


@dataclass
class MipLevel:
    """One level of a texture's mip chain.

    Attributes:
        level (int): Mip index, 0 being full size.
        width (int): Pixel width.
        height (int): Pixel height.
        size (int): Encoded byte size.
        where (str): ``uexp`` for data stored inline, ``ubulk`` for data held in
            the separate bulk file.
        offset (int): Byte offset within whichever file holds it.
    """

    level: int
    width: int
    height: int
    size: int
    where: str
    offset: int


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
    mips: list = None
    ubulk: bytes = b""

    @property
    def is_block(self) -> bool:
        """Whether the texture is block compressed.

        Returns:
            bool: True for DXT formats, which need an encoder rather than a
            straight pixel copy.
        """
        return self.pixel_format in BLOCK_FORMATS

    @property
    def byte_count(self) -> int:
        """Size of the pixel data.

        Returns:
            int: ``width * height * 4``, since every pixel is 4 bytes of BGRA.
        """
        return self.width * self.height * 4


def _walk_mips(uexp: bytes, cursor: int, count: int, width: int, height: int,
               fmt: str, first_level: int = 0):
    """Locate every mip level in a block-compressed texture.

    Each level is recorded as an index, optionally its data, then its width,
    height and depth. The largest levels are usually held in a separate bulk
    file and contribute no inline bytes, so each level is identified by checking
    which arrangement makes the following dimensions land where they should.

    Args:
        uexp (bytes): The texture payload.
        cursor (int): Offset just past the mip count.
        count (int): Number of mip levels.
        width (int): Full texture width.
        height (int): Full texture height.
        fmt (str): Pixel format.
        first_level (int): Level number of the first serialized mip, which is
            not zero when a texture's top levels have been cooked out.

    Returns:
        tuple[list[MipLevel], int]: The levels and the offset after the last.

    Raises:
        TextureError: If a level cannot be located, which means the layout is
            not the one this understands.
    """
    from . import dxt

    out = []
    bulk_at = 0
    w, h = width, height
    for n in range(count):
        level = first_level + n
        cursor += 4                                  # the level's own index
        want = struct.pack("<iii", w, h, 1)
        size = dxt.block_size(w, h, fmt)
        if uexp[cursor:cursor + 12] == want:         # held in the bulk file
            out.append(MipLevel(level, w, h, size, "ubulk", bulk_at))
            bulk_at += size
            cursor += 12
        elif uexp[cursor + size:cursor + size + 12] == want:
            out.append(MipLevel(level, w, h, size, "uexp", cursor))
            cursor += size + 12
        else:
            raise TextureError(
                f"mip {level} ({w}x{h}) is not where the layout predicts")
        w, h = max(1, w // 2), max(1, h // 2)
    return out, cursor


def parse(uexp: bytes, ubulk: bytes = b"") -> Texture:
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
    fmt = next((f for f in ALL_FORMATS if f.encode() in uexp), None)
    if fmt is None:
        raise TextureError(
            "unsupported texture: handled formats are " + ", ".join(ALL_FORMATS))
    i = uexp.find(fmt.encode())
    len_at = i - 4
    w, h, _packed = struct.unpack_from("<iii", uexp, len_at - 12)
    (slen,) = struct.unpack_from("<i", uexp, len_at)
    after = len_at + 4 + slen

    if fmt == SUPPORTED_FORMAT:
        start = after + BULK_HEADER_GAP
        need = w * h * 4
        if start + need + TRAILER != len(uexp):
            raise TextureError(
                f"unexpected texture layout: {w}x{h} needs {need} bytes at offset "
                f"{start}, but the payload is {len(uexp)} bytes "
                "(mipmapped uncompressed textures are not supported)")
        return Texture(w, h, fmt, start, uexp, [], b"")

    # Large textures have their top levels cooked out, so the chain starts at
    # FirstMipToSerialize rather than at the full size.
    (first_mip,) = struct.unpack_from("<i", uexp, after)
    (count,) = struct.unpack_from("<i", uexp, after + 4)
    mw, mh = max(1, w >> first_mip), max(1, h >> first_mip)
    mips, end = _walk_mips(uexp, after + 8, count, mw, mh, fmt, first_mip)
    bulk_needed = sum(m.size for m in mips if m.where == "ubulk")
    if ubulk and len(ubulk) != bulk_needed:
        raise TextureError(
            f"bulk file is {len(ubulk)} bytes but the mip chain needs {bulk_needed}")
    return Texture(w, h, fmt, mips[0].offset if mips else after, uexp, mips, ubulk)


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


def replace(tex: Texture, image):
    """Build new texture data with different art spliced in.

    Images that are not already the right size are scaled to *cover* and then
    centre-cropped, so the aspect ratio is preserved rather than squashed. Block
    formats have every mip level re-encoded at its own size, which keeps each
    one exactly as long as the data it replaces, so no offset in either file
    moves and the paired ``.uasset`` stays valid.

    Args:
        tex (Texture): The texture being replaced, from :func:`parse`.
        image (PIL.Image.Image): Replacement art, any size or mode.

    Returns:
        tuple[bytes, bytes]: The new ``.uexp`` and ``.ubulk`` contents. The
        second is empty for textures that keep everything inline.

    Raises:
        TextureError: If conversion produced the wrong number of bytes, which
            indicates an encoding bug rather than bad input.
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

    if not tex.is_block:
        r, g, b, a = im.split()
        bgra = Image.merge("RGBA", (b, g, r, a)).tobytes()
        if len(bgra) != tex.byte_count:
            raise TextureError(
                f"converted image is {len(bgra)} bytes, expected {tex.byte_count}")
        out = bytearray(tex.raw)
        out[tex.data_offset:tex.data_offset + tex.byte_count] = bgra
        return bytes(out), b""

    from . import dxt

    uexp = bytearray(tex.raw)
    ubulk = bytearray(tex.ubulk)
    for m in tex.mips:
        data = dxt.encode(im, m.width, m.height, tex.pixel_format)
        if len(data) != m.size:
            raise TextureError(
                f"mip {m.level} encoded to {len(data)} bytes, expected {m.size}")
        target = uexp if m.where == "uexp" else ubulk
        target[m.offset:m.offset + m.size] = data
    if len(uexp) != len(tex.raw) or len(ubulk) != len(tex.ubulk):
        raise TextureError("replacement changed a file length")
    return bytes(uexp), bytes(ubulk)


def to_image(tex: Texture, ubulk: bytes = b""):
    """Decode a texture's largest mip into an image.

    Args:
        tex (Texture): A texture from :func:`parse`.
        ubulk (bytes): Bulk data, if not already attached to ``tex``.

    Returns:
        PIL.Image.Image: An RGBA image.

    Raises:
        TextureError: If the format cannot be decoded here.
    """
    from PIL import Image
    import io

    if not tex.is_block:
        return to_png_bytes(tex)
    data = ubulk or tex.ubulk
    top = tex.mips[0]
    if top.where == "ubulk":
        payload = data[top.offset:top.offset + top.size]
    else:
        payload = tex.raw[top.offset:top.offset + top.size]
    # PIL reads DDS, so wrap the block data in a minimal header rather than
    # writing a decoder.
    four_cc = b"DXT1" if tex.pixel_format == "PF_DXT1" else b"DXT5"
    hdr = (b"DDS " + (124).to_bytes(4, "little") + (0x1007).to_bytes(4, "little")
           + top.height.to_bytes(4, "little") + top.width.to_bytes(4, "little")
           + len(payload).to_bytes(4, "little") + b"\0" * 4
           + (0).to_bytes(4, "little") + b"\0" * 44
           + (32).to_bytes(4, "little") + (4).to_bytes(4, "little") + four_cc
           + b"\0" * 20 + (0x1000).to_bytes(4, "little") + b"\0" * 16)
    return Image.open(io.BytesIO(hdr + payload)).convert("RGBA")
