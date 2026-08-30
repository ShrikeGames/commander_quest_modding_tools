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

import collections

from . import ftext

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "schema" / "usmap.json"

EXPORT_TRAILER = 4
"""Every export payload ends with four bytes terminating the property list."""

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

STRUCT_SIZE = {
    "GameplayTag": 8,
    "Color": 4,
    "LinearColor": 16,
    "Vector2D": 16,
    "IntPoint": 8,
    "DataTableRowHandle": 12,
}
"""Structs with a fixed serialized size. Others are measured or solved."""

TAG_CONTAINER = "GameplayTagContainer"
"""A count followed by that many FNames, each an index into the package's names."""

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
        self.sizes = {}
        """Empirically solved sizes for properties whose type is variable,
        keyed by ``(class name, property index)``. See :meth:`solve_sizes`."""

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

    def tags(self, field, payload: bytes) -> list:
        """Read the gameplay tags held by a tag container property.

        A container is a count followed by that many ``FName`` values, each an
        index into the owning package's name table plus an instance number. The
        index is what a tag edit changes.

        Args:
            field (Field): A placed property whose struct is a tag container.
            payload (bytes): The whole ``.uexp``.

        Returns:
            list[tuple[int, int]]: ``(offset, name index)`` per tag, in order.
            Empty if the field is not a tag container.
        """
        if field.offset < 0 or field.size < 4:
            return []
        n = struct.unpack_from("<i", payload, field.offset)[0]
        if not 0 <= n <= 4096 or 4 + 8 * n != field.size:
            return []
        return [(field.offset + 4 + 8 * i,
                 struct.unpack_from("<I", payload, field.offset + 4 + 8 * i)[0])
                for i in range(n)]

    def is_tag_container(self, export, index: int) -> bool:
        """Whether a property holds gameplay tags.

        Args:
            export (cqmod.catalog.ExportInfo): The owning export.
            index (int): Property index.

        Returns:
            bool: True if the property is a ``GameplayTagContainer``.
        """
        for p in self.properties(export.class_name):
            if p["index"] == index:
                return p.get("struct") == TAG_CONTAINER
        return False

    def solve_sizes(self, reader, assets) -> int:
        """Measure the serialized size of variable-typed properties.

        Types alone do not size a struct or an array, but many of them are the
        same length in every asset that uses them. Seeding each class's size
        equations with the sizes the types already give, any equation left with
        one unknown determines it. ``CMUnitData::Tags`` resolves to 12 bytes
        this way, which is what lets placement continue to ``MaxHealth``.

        Sizes that genuinely vary, such as text and most arrays, are left
        unknown rather than averaged into something wrong.

        Args:
            reader (cqmod.pak.PakReader): An open archive.
            assets (list[cqmod.catalog.Asset]): The catalog to learn from.

        Returns:
            int: How many variable-typed properties were resolved.
        """
        from . import schema

        obs = schema.observe(reader, assets)
        known = {}
        for cls in obs:
            for p in self.properties(cls):
                if p["type"] in SERIALIZED_SIZE:
                    known[(cls, p["index"])] = SERIALIZED_SIZE[p["type"]]
        seeded_keys = set(known)
        seeded = len(known)
        poisoned = set()
        """Properties proven to vary between assets; never given a fixed size."""

        changed = True
        while changed:
            changed = False
            for cls, olist in obs.items():
                by = collections.defaultdict(set)
                for o in olist:
                    by[o.indices].add(o.fixed_bytes)
                for idxs, sizes in by.items():
                    if len(sizes) != 1:
                        continue          # a variable property is in play
                    total = next(iter(sizes))
                    unknown = [i for i in idxs if (cls, i) not in known]
                    rest = total - sum(known[(cls, i)] for i in idxs if (cls, i) in known)
                    if len(unknown) == 1 and rest >= 0:
                        k = (cls, unknown[0])
                        if k not in poisoned:
                            known[k] = rest
                            changed = True
                    elif not unknown and rest != 0:
                        # These sizes cannot all be right, so something in this
                        # set varies between assets. Drop the derived ones and
                        # poison them, otherwise propagation re-derives the same
                        # wrong value forever.
                        for i in idxs:
                            k = (cls, i)
                            if k in known and k not in seeded_keys:
                                del known[k]
                                poisoned.add(k)
                                changed = True
        self.sizes = known
        return len(known) - seeded

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
        vstart = export.start + export.header_bytes
        vend = export.end - EXPORT_TRAILER
        spans = ftext.find_all(payload[vstart:export.end], [""] * 65536)
        text_at = {t.offset + vstart: t.end + vstart for t in spans}
        zero = set(export.zero_indices)
        order = [i for i in export.prop_indices if i in props]

        def candidates(idx, cursor):
            """Possible byte sizes for a property at a given position.

            A known type or a solved size gives exactly one answer. Otherwise
            the property is a container, which serializes as a count followed by
            its elements, so the count is read and the plausible element widths
            offered as alternatives. The search below keeps only whichever
            choice makes the whole payload add up.

            Args:
                idx (int): Property index.
                cursor (int): Where the property starts.

            Returns:
                list[int]: Candidate sizes, most likely first.
            """
            if cursor in text_at:
                return [text_at[cursor] - cursor]
            p = props[idx]
            t = p["type"]
            if t in SERIALIZED_SIZE:
                return [SERIALIZED_SIZE[t]]
            if t == "StructProperty" and p.get("struct") in STRUCT_SIZE:
                return [STRUCT_SIZE[p["struct"]]]
            if cursor + 4 > len(payload):
                return []
            n = struct.unpack_from("<i", payload, cursor)[0]
            # Containers serialize as a count followed by their elements, so the
            # element width gives the size outright once the type is known.
            if 0 <= n <= 4096:
                if t == "StructProperty" and p.get("struct") == TAG_CONTAINER:
                    return [4 + 8 * n]
                if t in ("ArrayProperty", "SetProperty"):
                    w = SERIALIZED_SIZE.get(p.get("inner", ""))
                    if w:
                        return [4 + w * n]
            fixed = self.sizes.get((export.class_name, idx))
            if fixed is not None:
                return [fixed]
            if not 0 <= n <= 4096:
                return []
            return [4 + n * w for w in (8, 4, 16, 1, 12, 32)]

        def search(i, cursor, acc):
            """Find a layout that consumes the value region exactly.

            Args:
                i (int): Position in ``order``.
                cursor (int): Current byte offset.
                acc (list): Placements chosen so far.

            Returns:
                list | None: A complete layout, or None if this branch fails.
            """
            if i == len(order):
                return acc if cursor == vend else None
            idx = order[i]
            if idx in zero:
                return search(i + 1, cursor, acc + [(idx, -1, 0)])
            for size in candidates(idx, cursor):
                if size < 0 or cursor + size > vend:
                    continue
                got = search(i + 1, cursor + size, acc + [(idx, cursor, size)])
                if got is not None:
                    return got
            return None

        layout = search(0, vstart, [])
        if layout is None:
            # No arrangement accounts for every byte, so place only the prefix
            # that is certain rather than reporting positions that may be wrong.
            layout = []
            cursor = vstart
            for idx in order:
                if idx in zero:
                    layout.append((idx, -1, 0))
                    continue
                c = candidates(idx, cursor)
                if len(c) != 1 or cursor + c[0] > vend:
                    break
                layout.append((idx, cursor, c[0]))
                cursor += c[0]

        out = []
        for idx, off, size in layout:
            p = props[idx]
            value = None
            if off >= 0 and size == 4 and p["type"] in (
                    "IntProperty", "ObjectProperty", "ClassProperty"):
                value = struct.unpack_from("<i", payload, off)[0]
            elif off >= 0 and size == 1:
                value = payload[off]
            elif off < 0:
                value = 0
            out.append(Field(idx, p["name"], p["type"], p["owner"], off, size, value))
        return out
