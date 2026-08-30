"""Reading and writing UE localization resources (``.locres``, version 3).

The file is a magic and version, an ``int64`` offset to the string array, an
entry count, then a namespace/key table whose leaves index into that array. The
string array sits last, so retargeting strings only requires rewriting the tail
while the recorded array offset stays valid -- which is exactly what :func:`save`
does, leaving the namespace table byte-identical.

.. warning::
   UE deserializes a positive-length ``FString`` as **ANSI**, not UTF-8. Writing
   multi-byte UTF-8 with a positive length turns every non-ASCII string into
   mojibake. :func:`_wr_string` encodes non-ASCII as UTF-16 for this reason; see
   ``docs/formats/locres.md``.
"""
from __future__ import annotations
import struct
from dataclasses import dataclass, field

MAGIC = bytes.fromhex("0e147475674a03fc4a15909dc3377f1b")
"""The 16-byte GUID every ``.locres`` file starts with."""

_HEADER_SIZE = 25
"""Bytes before the entry count: 16 magic + 1 version + 8 array offset."""


class LocresError(RuntimeError):
    """Raised for a malformed, truncated, or too-old localization resource."""


@dataclass
class Locres:
    """A parsed localization resource.

    Attributes:
        version (int): Format version; 3 or higher is required.
        raw (bytes): The original file, retained so :func:`save` can reuse the
            namespace table verbatim.
        array_offset (int): Byte offset of the string array.
        strings (list[list]): ``[text, refcount]`` pairs. Mutable so callers can
            edit text in place.
        entries (dict): ``(namespace, key) -> index`` into :attr:`strings`.
            Several keys may share an index, since identical text is deduplicated.
    """

    version: int
    raw: bytes
    array_offset: int
    strings: list = field(default_factory=list)
    entries: dict = field(default_factory=dict)

    def get(self, namespace: str, key: str):
        """Look up a localized string.

        Args:
            namespace (str): Namespace, e.g. ``ST_Card_Supply``. For a card this
                is the part of its string-table path after the final dot.
            key (str): Key within the namespace, e.g. ``Insight_Title``.

        Returns:
            str | None: The text, or None if the pair is not present.
        """
        i = self.entries.get((namespace, key))
        return self.strings[i][0] if i is not None else None

    def set(self, namespace: str, key: str, value: str) -> None:
        """Change the text a key resolves to.

        Identical strings are deduplicated in the file, so if this key shares its
        entry with others the string is *split*: a new array entry is appended
        and only this key is repointed at it. Editing in place would silently
        change every other key sharing the text.

        Args:
            namespace (str): Namespace containing the key.
            key (str): Key to retarget.
            value (str): Replacement text. Any Unicode is fine; :func:`save`
                picks the right encoding.

        Raises:
            KeyError: If the pair is not already present. New keys need the
                namespace table rebuilt, which :func:`save` does not do.
        """
        i = self.entries.get((namespace, key))
        if i is None:
            raise KeyError(f"{namespace}/{key} not present in this locres")
        shared = sum(1 for j in self.entries.values() if j == i)
        if shared > 1:
            self.strings.append([value, 1])
            self.entries[(namespace, key)] = len(self.strings) - 1
            self.strings[i][1] -= 1
        else:
            self.strings[i][0] = value


def _rd_string(buf, o):
    """Read an ``FString``.

    Args:
        buf (bytes): Buffer to read from.
        o (int): Offset of the length prefix.

    Returns:
        tuple[str, int]: The string, and the offset just past it. A negative
        length means UTF-16LE; positive means single-byte.
    """
    (n,) = struct.unpack_from("<i", buf, o); o += 4
    if n < 0:
        return buf[o:o - n * 2].decode("utf-16-le").rstrip("\0"), o - n * 2
    return buf[o:o + n - 1].decode("latin-1"), o + n


def _wr_string(s: str) -> bytes:
    """Serialize an ``FString`` the way UE expects to read it back.

    ASCII goes out single-byte with a positive length. Anything else *must* go
    out as UTF-16 with a negative length, because UE reads a positive length as
    ANSI and would otherwise mangle multi-byte sequences.

    Args:
        s (str): Text to encode.

    Returns:
        bytes: Length prefix followed by null-terminated character data.
    """
    if all(ord(c) < 128 for c in s):
        b = s.encode("ascii") + b"\0"
        return struct.pack("<i", len(b)) + b
    b = s.encode("utf-16-le") + b"\0\0"
    return struct.pack("<i", -(len(b) // 2)) + b


def load(raw: bytes) -> Locres:
    """Parse a localization resource.

    Args:
        raw (bytes): Complete file contents.

    Returns:
        Locres: The parsed resource.

    Raises:
        LocresError: If the magic is wrong, the version predates 3, or the
            string array does not end exactly at the end of the file.
    """
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

    o = _HEADER_SIZE + 4
    (ns_count,) = struct.unpack_from("<I", raw, o); o += 4
    entries = {}
    for _ in range(ns_count):
        o += 4
        ns, o = _rd_string(raw, o)
        (kcount,) = struct.unpack_from("<I", raw, o); o += 4
        for _ in range(kcount):
            o += 4
            key, o = _rd_string(raw, o)
            o += 4
            (idx,) = struct.unpack_from("<i", raw, o); o += 4
            entries[(ns, key)] = idx
    return Locres(version, raw, array_offset, strings, entries)


def save(loc: Locres) -> bytes:
    """Serialize a resource back to bytes.

    Only the trailing string array is rebuilt; everything before it is copied
    verbatim from the original. That keeps the recorded array offset correct and
    guarantees an unedited resource round-trips byte-identically.

    Args:
        loc (Locres): The resource to write.

    Returns:
        bytes: A complete ``.locres`` file.

    Note:
        This cannot *add* ``(namespace, key)`` pairs, only retarget existing
        ones, because the namespace table is reused unchanged.
    """
    out = bytearray(loc.raw[:loc.array_offset])
    out += struct.pack("<i", len(loc.strings))
    for s, rc in loc.strings:
        out += _wr_string(s) + struct.pack("<i", rc)
    return bytes(out)
