"""Unversioned property headers.

Cooked packages here use PKG_UnversionedProperties: instead of a self-describing
name/type stream, each export's data starts with a run-length bitmap saying
*which* property indices (in the class's declared order) are present. Values
then follow in index order.

Without a .usmap we know which indices are set and where their bytes are, but
not the property names or types. That is still enough to locate and edit values
by index, which is how the tools work today.

Fragment layout (uint16): SkipNum:7 | bHasZeroes:1 | bIsLast:1 | ValueNum:7
"""
from __future__ import annotations
import struct
from dataclasses import dataclass


@dataclass
class Fragment:
    skip: int
    has_zeroes: bool
    is_last: bool
    value_count: int

    @classmethod
    def unpack(cls, v: int) -> "Fragment":
        return cls(v & 0x7F, bool(v & 0x80), bool(v & 0x100), v >> 9)

    def pack(self) -> int:
        return ((self.value_count & 0x7F) << 9) | (0x100 if self.is_last else 0) \
               | (0x80 if self.has_zeroes else 0) | (self.skip & 0x7F)


@dataclass
class Header:
    fragments: list
    indices: list        # property indices carrying a value, in order
    size: int            # bytes consumed by the header (+ zero bitmap)
    zero_mask_bits: int


def parse(data: bytes, off: int = 0) -> Header:
    frags, indices = [], []
    idx = o = 0
    o = off
    while True:
        (v,) = struct.unpack_from("<H", data, o); o += 2
        f = Fragment.unpack(v)
        frags.append(f)
        idx += f.skip
        indices.extend(range(idx, idx + f.value_count))
        idx += f.value_count
        if f.is_last:
            break
    zero_bits = sum(f.value_count for f in frags if f.has_zeroes)
    if zero_bits:
        o += (zero_bits + 7) // 8
    return Header(frags, indices, o - off, zero_bits)
