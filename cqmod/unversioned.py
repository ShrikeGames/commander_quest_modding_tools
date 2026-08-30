"""Unversioned property headers.

Cooked packages here set ``PKG_UnversionedProperties``. Instead of a
self-describing name/type stream, each export's payload starts with a run-length
bitmap saying *which* property indices carry a value, where an index is a
position in the class's declared property order. The values themselves then
follow in index order, with no type tags.

Without a ``.usmap`` we can therefore see which indices are set and where the
value bytes begin, but not property names or types. That is still enough to
locate and edit values by byte offset, which is how these tools work. See
``docs/formats/unversioned.md``.

Fragment layout, one ``uint16`` each::

    bits 0-6    SkipNum      properties skipped before this run
    bit  7      bHasZeroes   run has a trailing zero bitmap
    bit  8      bIsLast      final fragment
    bits 9-15   ValueNum     properties carrying a value in this run
"""
from __future__ import annotations
import struct
from dataclasses import dataclass


@dataclass
class Fragment:
    """One run in an unversioned property header.

    Attributes:
        skip (int): Property indices skipped before this run begins.
        has_zeroes (bool): Whether any value in the run is zero-valued and
            recorded in the header's trailing zero bitmap rather than inline.
        is_last (bool): Whether this is the final fragment of the header.
        value_count (int): Consecutive properties carrying a value in this run.
    """

    skip: int
    has_zeroes: bool
    is_last: bool
    value_count: int

    @classmethod
    def unpack(cls, v: int) -> "Fragment":
        """Decode a fragment from its packed representation.

        Args:
            v (int): The raw ``uint16`` read from the header.

        Returns:
            Fragment: The decoded fragment.
        """
        return cls(v & 0x7F, bool(v & 0x80), bool(v & 0x100), v >> 9)

    def pack(self) -> int:
        """Encode this fragment back to its packed representation.

        Returns:
            int: A ``uint16`` suitable for writing into a header.
        """
        return ((self.value_count & 0x7F) << 9) | (0x100 if self.is_last else 0) \
               | (0x80 if self.has_zeroes else 0) | (self.skip & 0x7F)


@dataclass
class Header:
    """A parsed unversioned property header.

    Attributes:
        fragments (list[Fragment]): Runs in the order they were read.
        indices (list[int]): Property indices carrying a value, ascending. These
            are positions in the class's declared property order, not names.
        size (int): Bytes the header occupies, including any zero bitmap. Value
            data begins this many bytes after the header's start.
        zero_mask_bits (int): Number of bits in the trailing zero bitmap.
        zero_indices (set): Property indices whose value is zero. These are
            recorded in the bitmap and occupy **no bytes** in the value region,
            which is why the same property set can produce different payload
            sizes.
    """

    fragments: list
    indices: list
    size: int
    zero_mask_bits: int
    zero_indices: set = None

    def __post_init__(self):
        """Default the zero set without sharing one instance across headers."""
        if self.zero_indices is None:
            self.zero_indices = set()

    @property
    def stored_indices(self) -> list:
        """Property indices that actually occupy bytes in the value region.

        Returns:
            list[int]: :attr:`indices` minus the zero-valued ones, in order.
        """
        return [i for i in self.indices if i not in self.zero_indices]


def parse(data: bytes, off: int = 0) -> Header:
    """Parse the unversioned property header at the start of an export payload.

    Args:
        data (bytes): The full ``.uexp`` payload.
        off (int): Byte offset where the export's data begins. Use
            :meth:`cqmod.uasset.Export.uexp_slice` to compute it.

    Returns:
        Header: The decoded header, whose :attr:`Header.size` tells you where
        the export's value bytes start.

    Raises:
        struct.error: If the data ends before a terminating fragment is found,
            which normally means ``off`` did not point at an export boundary.
    """
    frags, indices = [], []
    masked = []          # indices covered by the zero bitmap, in bit order
    idx = 0
    o = off
    while True:
        (v,) = struct.unpack_from("<H", data, o); o += 2
        f = Fragment.unpack(v)
        frags.append(f)
        idx += f.skip
        run = list(range(idx, idx + f.value_count))
        indices.extend(run)
        if f.has_zeroes:
            masked.extend(run)
        idx += f.value_count
        if f.is_last:
            break

    zero_bits = len(masked)
    zeros = set()
    if zero_bits:
        # UE stores the mask as a uint8, a uint16, or a run of uint32s.
        if zero_bits <= 8:
            width = 1
        elif zero_bits <= 16:
            width = 2
        else:
            width = ((zero_bits + 31) // 32) * 4
        mask = int.from_bytes(data[o:o + width], "little")
        o += width
        zeros = {p for n, p in enumerate(masked) if mask >> n & 1}
    return Header(frags, indices, o - off, zero_bits, zeros)


def build(indices, zero_indices=()) -> bytes:
    """Serialize an unversioned property header.

    Consecutive present indices become one fragment; a gap starts a new one.
    Runs longer than a fragment can express are split. Fragments containing a
    zero-valued property set the zero flag, and a bitmap follows listing which
    of their values are zero.

    Args:
        indices (Iterable[int]): Property indices carrying a value, ascending.
        zero_indices (Iterable[int]): Of those, the ones whose value is zero.

    Returns:
        bytes: A header that :func:`parse` reads back to the same indices.

    Raises:
        ValueError: If an index gap exceeds what a fragment can skip and cannot
            be bridged, which does not occur in this game's data.
    """
    idx = sorted(set(indices))
    zeros = set(zero_indices)
    runs = []
    for i in idx:
        if runs and i == runs[-1][-1] + 1 and len(runs[-1]) < 127:
            runs[-1].append(i)
        else:
            runs.append([i])

    frags, prev_end = [], 0
    for run in runs:
        skip = run[0] - prev_end
        while skip > 127:
            # Bridge an oversized gap with an empty fragment.
            frags.append(Fragment(127, False, False, 0))
            skip -= 127
        frags.append(Fragment(skip, any(i in zeros for i in run), False, len(run)))
        prev_end = run[-1] + 1
    if not frags:
        frags = [Fragment(0, False, True, 0)]
    frags[-1].is_last = True

    out = bytearray()
    for f in frags:
        out += struct.pack("<H", f.pack())

    masked = [i for f, run in zip([f for f in frags if f.value_count], runs)
              if f.has_zeroes for i in run]
    if masked:
        bits = 0
        for n, i in enumerate(masked):
            if i in zeros:
                bits |= 1 << n
        width = 1 if len(masked) <= 8 else 2 if len(masked) <= 16 \
            else ((len(masked) + 31) // 32) * 4
        out += bits.to_bytes(width, "little")
    return bytes(out)
