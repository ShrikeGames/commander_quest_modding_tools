"""Reading and writing UE localization resources (.locres, version 3).

Layout: magic, version, int64 offset of the string array, entry count, then a
namespace/key table whose leaves index into that array. The string array sits
last in the file, so retargeting strings only requires rewriting the tail while
keeping the recorded array offset valid.
"""
from __future__ import annotations
import struct
from dataclasses import dataclass, field

MAGIC = bytes.fromhex("0e147475674a03fc4a15909dc3377f1b")


class LocresError(RuntimeError):
    pass


@dataclass
class Locres:
    version: int
    raw: bytes
    array_offset: int
    strings: list = field(default_factory=list)      # [[text, refcount]]
    entries: dict = field(default_factory=dict)      # (namespace, key) -> string index

    def get(self, namespace: str, key: str):
        i = self.entries.get((namespace, key))
        return self.strings[i][0] if i is not None else None

    def set(self, namespace: str, key: str, value: str) -> None:
        i = self.entries.get((namespace, key))
        if i is None:
            raise KeyError(f"{namespace}/{key} not present in this locres")
        shared = sum(1 for j in self.entries.values() if j == i)
        if shared > 1:
            # Splitting a deduplicated string would silently change other keys.
            self.strings.append([value, 1])
            self.entries[(namespace, key)] = len(self.strings) - 1
            self.strings[i][1] -= 1
        else:
            self.strings[i][0] = value


def _rd_string(buf, o):
    (n,) = struct.unpack_from("<i", buf, o); o += 4
    if n < 0:
        return buf[o:o - n * 2].decode("utf-16-le").rstrip("\0"), o - n * 2
    return buf[o:o + n - 1].decode("latin-1"), o + n


def _wr_string(s: str) -> bytes:
    # UE reads a positive length as ANSI, so non-ASCII MUST go out as UTF-16.
    if all(ord(c) < 128 for c in s):
        b = s.encode("ascii") + b"\0"
        return struct.pack("<i", len(b)) + b
    b = s.encode("utf-16-le") + b"\0\0"
    return struct.pack("<i", -(len(b) // 2)) + b


def load(raw: bytes) -> Locres:
    if raw[:16] != MAGIC:
        raise LocresError("not a .locres (bad magic)")
    version = raw[16]
    if version < 3:
        raise LocresError(f"locres version {version} not supported (need >= 3)")
    (array_offset,) = struct.unpack_from("<q", raw, 17)
    o = array_offset
    (count,) = struct.unpack_from("<i", raw, o); o += 4
    strings = []
    for _ in range(count):
        s, o = _rd_string(raw, o)
        (rc,) = struct.unpack_from("<i", raw, o); o += 4
        strings.append([s, rc])
    if o != len(raw):
        raise LocresError("trailing bytes after locres string array")

    o = 25  # magic + version + array offset
    o += 4  # total entry count
    (ns_count,) = struct.unpack_from("<I", raw, o); o += 4
    entries = {}
    for _ in range(ns_count):
        o += 4                              # namespace hash
        ns, o = _rd_string(raw, o)
        (kcount,) = struct.unpack_from("<I", raw, o); o += 4
        for _ in range(kcount):
            o += 4                          # key hash
            key, o = _rd_string(raw, o)
            o += 4                          # source string hash
            (idx,) = struct.unpack_from("<i", raw, o); o += 4
            entries[(ns, key)] = idx
    return Locres(version, raw, array_offset, strings, entries)


def save(loc: Locres) -> bytes:
    """Rewrite only the trailing string array; the namespace table is untouched.

    Note this cannot add new (namespace, key) pairs -- only retarget existing
    ones. New keys need the namespace table rebuilt too.
    """
    out = bytearray(loc.raw[:loc.array_offset])
    out += struct.pack("<i", len(loc.strings))
    for s, rc in loc.strings:
        out += _wr_string(s) + struct.pack("<i", rc)
    return bytes(out)
