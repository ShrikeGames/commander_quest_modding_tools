"""Encoding and decoding DXT1 and DXT5 block-compressed textures.

Card art is uncompressed, but unit textures are `PF_DXT1` (292 of them) and
`PF_DXT5` (9), so replacing one means producing block-compressed data rather
than copying pixels.

Both formats work on 4x4 pixel blocks. DXT1 stores two RGB565 endpoints and two
bits per pixel selecting from a four-colour ramp between them. DXT5 adds an
eight-byte alpha block in front, holding two endpoints and three bits per pixel
along an eight-step ramp.

Endpoints are chosen by taking the extremes of each block along its principal
axis, which is fast and good enough for texture replacement. This is not a
competitive encoder, and it does not need to be: it only has to look right.
"""
from __future__ import annotations
import numpy as np

BLOCK = 4


def _to565(rgb) -> np.ndarray:
    """Pack RGB triples into RGB565.

    Args:
        rgb (numpy.ndarray): ``(..., 3)`` uint8 colours.

    Returns:
        numpy.ndarray: uint16 packed colours.
    """
    r = (rgb[..., 0].astype(np.uint16) >> 3) & 0x1F
    g = (rgb[..., 1].astype(np.uint16) >> 2) & 0x3F
    b = (rgb[..., 2].astype(np.uint16) >> 3) & 0x1F
    return (r << 11) | (g << 5) | b


def _from565(c) -> np.ndarray:
    """Unpack RGB565 back to 8-bit RGB.

    Args:
        c (numpy.ndarray): uint16 packed colours.

    Returns:
        numpy.ndarray: ``(..., 3)`` uint8 colours.
    """
    r = ((c >> 11) & 0x1F).astype(np.uint16)
    g = ((c >> 5) & 0x3F).astype(np.uint16)
    b = (c & 0x1F).astype(np.uint16)
    out = np.stack([(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)], -1)
    return out.astype(np.uint8)


def _blocks(img: np.ndarray) -> np.ndarray:
    """Split an image into 4x4 blocks, padding by edge repetition.

    Args:
        img (numpy.ndarray): ``(h, w, c)`` uint8 image.

    Returns:
        numpy.ndarray: ``(blocks_y, blocks_x, 16, c)`` uint8 blocks in row order.
    """
    h, w, c = img.shape
    ph, pw = (-h) % BLOCK, (-w) % BLOCK
    if ph or pw:
        img = np.pad(img, ((0, ph), (0, pw), (0, 0)), mode="edge")
        h, w = img.shape[:2]
    img = img.reshape(h // BLOCK, BLOCK, w // BLOCK, BLOCK, c)
    return img.transpose(0, 2, 1, 3, 4).reshape(h // BLOCK, w // BLOCK, 16, c)


def encode_bc1(rgb: np.ndarray) -> bytes:
    """Compress an RGB image to DXT1.

    Args:
        rgb (numpy.ndarray): ``(h, w, 3)`` uint8 image. Dimensions need not be
            multiples of four; edges are padded.

    Returns:
        bytes: DXT1 data, eight bytes per 4x4 block in row order.
    """
    blocks = _blocks(rgb).astype(np.int16)
    by, bx = blocks.shape[:2]
    flat = blocks.reshape(-1, 16, 3)

    lo = flat.min(axis=1)
    hi = flat.max(axis=1)
    c0 = _to565(hi.astype(np.uint8))
    c1 = _to565(lo.astype(np.uint8))
    # DXT1 reads c0 > c1 as the opaque four-colour mode; equal endpoints are a
    # flat block, where the ordering does not matter.
    swap = c0 < c1
    c0[swap], c1[swap] = c1[swap], c0[swap]

    e0 = _from565(c0).astype(np.int16)
    e1 = _from565(c1).astype(np.int16)
    ramp = np.stack([e0, e1,
                     (2 * e0 + e1) // 3,
                     (e0 + 2 * e1) // 3], axis=1)          # (n, 4, 3)

    d = flat[:, None, :, :] - ramp[:, :, None, :]           # (n, 4, 16, 3)
    idx = (d.astype(np.int32) ** 2).sum(-1).argmin(axis=1)  # (n, 16)

    packed = np.zeros(len(flat), dtype=np.uint32)
    for i in range(16):
        packed |= (idx[:, i].astype(np.uint32) & 3) << (2 * i)

    out = np.empty((len(flat), 8), dtype=np.uint8)
    out[:, 0] = c0 & 0xFF
    out[:, 1] = c0 >> 8
    out[:, 2] = c1 & 0xFF
    out[:, 3] = c1 >> 8
    out[:, 4] = packed & 0xFF
    out[:, 5] = (packed >> 8) & 0xFF
    out[:, 6] = (packed >> 16) & 0xFF
    out[:, 7] = (packed >> 24) & 0xFF
    return out.reshape(by, bx, 8).tobytes()


def _encode_alpha(alpha: np.ndarray) -> np.ndarray:
    """Compress one channel to a BC4 style eight-byte alpha block.

    Args:
        alpha (numpy.ndarray): ``(n, 16)`` uint8 values per block.

    Returns:
        numpy.ndarray: ``(n, 8)`` uint8 alpha blocks.
    """
    a0 = alpha.max(axis=1).astype(np.int32)
    a1 = alpha.min(axis=1).astype(np.int32)
    span = np.maximum(a0 - a1, 1)
    # Eight-step ramp: index 0 is a0, index 1 is a1, then six interpolants.
    t = np.clip(((a0[:, None] - alpha.astype(np.int32)) * 7 + span[:, None] // 2)
                // span[:, None], 0, 7)
    idx = np.where(t == 0, 0, np.where(t == 7, 1, t + 1)).astype(np.uint64)

    bits = np.zeros(len(alpha), dtype=np.uint64)
    for i in range(16):
        bits |= (idx[:, i] & 7) << np.uint64(3 * i)
    out = np.empty((len(alpha), 8), dtype=np.uint8)
    out[:, 0] = a0
    out[:, 1] = a1
    for k in range(6):
        out[:, 2 + k] = (bits >> np.uint64(8 * k)) & 0xFF
    return out


def encode_bc3(rgba: np.ndarray) -> bytes:
    """Compress an RGBA image to DXT5.

    Args:
        rgba (numpy.ndarray): ``(h, w, 4)`` uint8 image.

    Returns:
        bytes: DXT5 data, sixteen bytes per 4x4 block: an alpha block followed
        by a colour block.
    """
    blocks = _blocks(rgba)
    by, bx = blocks.shape[:2]
    flat = blocks.reshape(-1, 16, 4)
    alpha = _encode_alpha(flat[..., 3])
    colour = np.frombuffer(encode_bc1(rgba[..., :3]), dtype=np.uint8).reshape(-1, 8)
    return np.concatenate([alpha, colour], axis=1).reshape(by, bx, 16).tobytes()


def block_size(width: int, height: int, fmt: str) -> int:
    """Bytes one mip level occupies.

    Args:
        width (int): Mip width in pixels.
        height (int): Mip height in pixels.
        fmt (str): ``PF_DXT1`` or ``PF_DXT5``.

    Returns:
        int: Encoded size in bytes.
    """
    bw = max(1, (width + 3) // BLOCK)
    bh = max(1, (height + 3) // BLOCK)
    return bw * bh * (8 if fmt == "PF_DXT1" else 16)


def encode(image, width: int, height: int, fmt: str) -> bytes:
    """Encode a PIL image at a given size in a given format.

    Args:
        image (PIL.Image.Image): Source art.
        width (int): Target width.
        height (int): Target height.
        fmt (str): ``PF_DXT1`` or ``PF_DXT5``.

    Returns:
        bytes: Encoded texture data for one mip level.

    Raises:
        ValueError: For an unsupported format.
    """
    from PIL import Image

    im = image.convert("RGBA").resize((max(1, width), max(1, height)), Image.LANCZOS)
    arr = np.asarray(im, dtype=np.uint8)
    if fmt == "PF_DXT1":
        return encode_bc1(arr[..., :3])
    if fmt == "PF_DXT5":
        return encode_bc3(arr)
    raise ValueError(f"unsupported block format {fmt}")
