"""Property names and types recovered from the engine.

Cooked packages here omit property metadata, so on its own the toolchain can
only address values by byte offset. ``schema/usmap.json`` restores the missing
half: it is dumped from the running game's reflection data by
``tools/usmap/dump.py`` and maps each class to its properties, in the order the
unversioned serializer numbers them.

With that, a property index becomes a name and a type, and a type becomes a
serialized size, which is what lets :func:`place` say exactly which bytes hold
``CMEffectData_MoveCard::Count`` in a given asset.

Serialized sizes are not the same as the in-memory sizes reflection reports: an
``ObjectProperty`` is an 8-byte pointer in memory but a 4-byte package index on
disk. The table below was solved from the game's own data and reproduces all 416
usable observations exactly.
"""
from __future__ import annotations
import json, struct
from dataclasses import dataclass
from pathlib import Path

from . import ftext

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "schema" / "usmap.json"

SERIALIZED_SIZE = {
    "BoolProperty": 1,
    "EnumProperty": 1,
    "ByteProperty": 1,
    "IntProperty": 4,
    "FloatProperty": 4,
    "ObjectProperty": 4,
    "ClassProperty": 4,
    "SoftObjectProperty": 4,
    "NameProperty": 8,
    "Int64Property": 8,
    "DoubleProperty": 8,
}
"""Bytes each property type occupies in a cooked payload."""

VARIABLE_TYPES = {
    "TextProperty", "StrProperty", "ArrayProperty",
    "StructProperty", "MapProperty", "SetProperty",
}
"""Types whose serialized length depends on their contents."""


class UsmapError(RuntimeError):
    """Raised when the schema file is missing or unreadable."""


@dataclass
class Field:
    """One property placed inside an asset's payload.

    Attributes:
        index (int): Property index as the unversioned header refers to it.
        name (str): Property name, e.g. ``Count``.
        type (str): Property type, e.g. ``IntProperty``.
        owner (str): Class declaring it, which may be a parent.
        offset (int): Byte offset in the ``.uexp``, or -1 when the value is
            zero and therefore stored in the header bitmap rather than inline.
        size (int): Bytes occupied, 0 for a zero-valued property.
        value (int | None): Decoded value for integer-like types.
    """

    index: int
    name: str
    type: str
    owner: str
    offset: int
    size: int
    value: object = None

    @property
    def editable(self) -> bool:
        """Whether this field can be changed with a value edit.

        Returns:
            bool: True for inline 4-byte integers, which is what
            :class:`cqmod.project.ValueEdit` can write.
        """
        return self.size == 4 and self.type in ("IntProperty", "FloatProperty",
                                                "ObjectProperty", "ClassProperty")


class Usmap:
    """The recovered property schema.

    Args:
        data (dict): Parsed schema, class name to its property list.
    """

    def __init__(self, data: dict):
        """Wrap a parsed schema document."""
        self.data = data

    @classmethod
    def load(cls, path=None) -> "Usmap":
        """Read the schema from disk.

        Args:
            path (str | Path | None): Schema file; defaults to
                ``schema/usmap.json``.

        Returns:
            Usmap: The loaded schema.

        Raises:
            UsmapError: If the file is missing or not valid JSON. Regenerate it
                with ``tools/usmap/dump.py`` while the game is running.
        """
        p = Path(path or DEFAULT_PATH)
        if not p.is_file():
            raise UsmapError(
                f"{p} not found. Launch the game and run tools/usmap/dump.py")
        try:
            return cls(json.loads(p.read_text()))
        except json.JSONDecodeError as e:
            raise UsmapError(f"{p} is not valid JSON: {e}") from e

    def __contains__(self, class_name):
        """Test whether a class is present.

        Args:
            class_name (str): Class to look for.

        Returns:
            bool: True if the schema describes it.
        """
        return class_name in self.data

    def properties(self, class_name: str) -> list:
        """List a class's properties in serialization order.

        Args:
            class_name (str): Class to look up.

        Returns:
            list[dict]: Property records, empty if the class is unknown.
        """
        e = self.data.get(class_name)
        return e["properties"] if e else []

    def place(self, export, payload: bytes) -> list:
        """Work out which bytes of a payload hold which property.

        Walks the export's properties in index order, advancing by each one's
        serialized size. Zero-valued properties are recorded in the header
        bitmap and occupy nothing. Text is variable length, so its extent is
        measured from the payload.

        Args:
            export (cqmod.catalog.ExportInfo): The export to place.
            payload (bytes): The whole ``.uexp``.

        Returns:
            list[Field]: One entry per property the export sets, in order.
            Placement stops at the first property whose length cannot be
            determined, so a short list means the tail is unresolved.
        """
        props = {p["index"]: p for p in self.properties(export.class_name)}
        if not props:
            return []
        start = export.start + export.header_bytes
        texts = {t.offset + start: t.end + start
                 for t in ftext.find_all(payload[start:export.end], [""] * 65536)}
        zero = set(export.zero_indices)
        out = []
        cursor = start
        for idx in export.prop_indices:
            p = props.get(idx)
            if p is None:
                break
            if idx in zero:
                out.append(Field(idx, p["name"], p["type"], p["owner"], -1, 0, 0))
                continue
            if cursor in texts:
                size = texts[cursor] - cursor
            elif p["type"] in SERIALIZED_SIZE:
                size = SERIALIZED_SIZE[p["type"]]
            else:
                break
            value = None
            if size == 4 and p["type"] in ("IntProperty", "ObjectProperty", "ClassProperty"):
                value = struct.unpack_from("<i", payload, cursor)[0]
            elif size == 1:
                value = payload[cursor]
            out.append(Field(idx, p["name"], p["type"], p["owner"], cursor, size, value))
            cursor += size
        return out
