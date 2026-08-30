"""Recovering property sizes so values can be addressed by index, not byte offset.

Unversioned property data carries no type tags, so a value's position depends on
the total size of every property before it. That is why a byte offset found on
one card is meaningless on another, and why two assets that set different
properties cannot be compared directly.

Sizes are recoverable without a .usmap. Every export records which property
indices it sets and how many bytes its values occupy, so across the whole game
each class yields many equations of the form::

    sum(size[i] for i in indices) == value_bytes

Two observations differing by exactly one index give that property's size
outright. Repeating this over every class solves most of them.

Two complications are handled explicitly. Text properties are variable length,
so their measured spans are subtracted before solving and the property is marked
variable. And a property whose value is zero is recorded in the header's zero
bitmap rather than inline, so it occupies no value bytes at all.

The result lets :func:`offsets` place every property of a given export, which is
what makes a finding on one card transfer to every card of its class.
"""
from __future__ import annotations
import collections, itertools, json
from dataclasses import dataclass, field, asdict
from pathlib import Path

from . import ftext

EXPORT_TRAILER = 4
"""Every export's value region ends with four zero bytes terminating the
property list. They belong to no property, so they are subtracted before any
size is derived. Subset differences cancel this constant, which is why omitting
it produced correct relative sizes but absolute sums that never balanced."""


@dataclass(frozen=True)
class Observation:
    """One export's contribution to a class's size equations.

    Attributes:
        indices (tuple): Property indices this export sets.
        value_bytes (int): Bytes its values occupy, excluding the header.
        text_bytes (int): How many of those bytes belong to text values.
        text_spans (tuple): ``(start, end)`` of each text value, relative to the
            start of the value region.
    """

    indices: tuple
    value_bytes: int
    text_bytes: int = 0
    text_spans: tuple = ()

    @property
    def fixed_bytes(self) -> int:
        """Value bytes attributable to fixed-size properties.

        Returns:
            int: The payload minus variable-length text and the export
            terminator, so this is exactly what the property sizes must sum to.
        """
        return max(0, self.value_bytes - self.text_bytes - EXPORT_TRAILER)


@dataclass
class PropertyLayout:
    """What is known about one property of a class.

    Attributes:
        index (int): Position in the class's declared property order.
        size (int | None): Fixed size in bytes, or None if variable length.
            Zero means the property is only ever stored in the zero bitmap.
        variable (bool): True if the size differs between assets, which in
            practice means text or an array.
        samples (int): How many observations contributed to this conclusion.
    """

    index: int
    size: int | None = None
    variable: bool = False
    samples: int = 0

    @property
    def solved(self) -> bool:
        """Whether this property's size is known.

        Returns:
            bool: True if a fixed size was determined.
        """
        return self.size is not None and not self.variable


@dataclass
class ClassLayout:
    """Solved property sizes for one class.

    Attributes:
        class_name (str): The class these properties belong to.
        properties (dict): Property index to :class:`PropertyLayout`.
        observations (int): Distinct observations used.
        validated (int): Observations whose sizes sum exactly to their payload.
        checked (int): Observations it was possible to check.
        ambiguous_sets (int): Index sets seen with more than one payload size,
            which proves a variable-length property is present.
    """

    class_name: str
    properties: dict = field(default_factory=dict)
    observations: int = 0
    validated: int = 0
    checked: int = 0
    ambiguous_sets: int = 0

    @property
    def confidence(self) -> float:
        """Fraction of observations the solved sizes fully explain.

        Returns:
            float: 1.0 when every checkable observation adds up, 0.0 if none do.
            Treat anything below 1.0 as a layout with unsolved properties.
        """
        return self.validated / self.checked if self.checked else 0.0

    @property
    def solved_count(self) -> int:
        """How many properties have a known fixed size.

        Returns:
            int: Count of solved properties.
        """
        return sum(1 for p in self.properties.values() if p.solved)


def observe(reader, assets) -> dict:
    """Collect size equations for every class in the catalog.

    Args:
        reader (cqmod.pak.PakReader): An open archive.
        assets (list[cqmod.catalog.Asset]): The catalog to walk.

    Returns:
        dict[str, list[Observation]]: Distinct observations per class name.
    """
    out = collections.defaultdict(set)
    for a in assets:
        try:
            payload = reader.read(a.uexp)
        except Exception:
            continue
        for e in a.exports:
            if e.end <= e.start:
                continue
            start = e.start + e.header_bytes
            if start > e.end:
                continue
            texts = _text_spans(payload, start, e.end)
            out[e.class_name].add(Observation(
                tuple(e.stored_indices), e.end - start,
                sum(t[1] - t[0] for t in texts),
                tuple((s - start, t - start) for s, t in texts),
            ))
    return {k: sorted(v, key=lambda o: (len(o.indices), o.indices)) for k, v in out.items()}


def _text_spans(payload: bytes, start: int, end: int):
    """Locate text values inside one export's value region.

    Args:
        payload (bytes): The whole ``.uexp``.
        start (int): First byte of the value region.
        end (int): One past its last byte.

    Returns:
        list[tuple[int, int]]: Absolute ``(start, end)`` of each text value.
    """
    region = payload[start:end]
    # Name indices are irrelevant here; only the span matters, so a permissive
    # table is passed and results are re-checked against the region bounds.
    out = []
    for t in ftext.find_all(region, [""] * 65536):
        out.append((start + t.offset, start + t.end))
    return out


def solve(class_name: str, obs: list) -> ClassLayout:
    """Determine property sizes for one class from its observations.

    Each observation gives an equation, ``sum(size[i] for i in indices) ==
    fixed_bytes``. Where one observation's property set is a subset of another's,
    subtracting them gives a further equation over just the difference, which is
    where most of the leverage comes from.

    Sizes are then derived by constraint propagation: an equation with exactly
    one unknown determines it, which may in turn reduce other equations to one
    unknown. Nothing is ever guessed, so a size that comes out of this is forced
    by the data. Properties that never become determined, or that produce
    contradictions, are reported as variable length rather than assigned a size.

    Args:
        class_name (str): The class being solved.
        obs (list[Observation]): Observations from :func:`observe`.

    Returns:
        ClassLayout: Solved sizes, validated by re-deriving every deterministic
        observation's payload size from them.
    """
    layout = ClassLayout(class_name, observations=len(obs))
    seen = collections.Counter(i for o in obs for i in o.indices)
    for idx, count in sorted(seen.items()):
        layout.properties[idx] = PropertyLayout(idx, samples=count)

    # An index set with more than one payload size hides a variable property.
    by_set = collections.defaultdict(set)
    for o in obs:
        by_set[o.indices].add(o.fixed_bytes)
    layout.ambiguous_sets = sum(1 for v in by_set.values() if len(v) > 1)
    eqs = {frozenset(k): next(iter(v)) for k, v in by_set.items() if len(v) == 1}
    if not eqs:
        for p in layout.properties.values():
            p.variable = True
        return layout

    # Subset differences: if B's properties are a subset of A's, the extra
    # properties in A must account for exactly the extra bytes.
    items = sorted(eqs.items(), key=lambda kv: len(kv[0]))
    derived = dict(eqs)
    for i, (sa, va) in enumerate(items):
        for sb, vb in items[:i]:
            if sb < sa:
                d = sa - sb
                delta = va - vb
                prev = derived.get(d)
                if prev is None:
                    derived[d] = delta
                elif prev != delta:
                    derived[d] = None          # contradictory, drop it

    known = {}
    contradictory = set()
    changed = True
    while changed:
        changed = False
        for props, total in derived.items():
            if total is None:
                continue
            unknown = [i for i in props if i not in known]
            rest = total - sum(known[i] for i in props if i in known)
            if not unknown:
                if rest != 0:
                    contradictory |= set(props)
            elif len(unknown) == 1 and rest >= 0:
                k = unknown[0]
                if known.get(k, rest) != rest:
                    contradictory.add(k)
                else:
                    known[k] = rest
                    changed = True

    for idx, p in layout.properties.items():
        if idx in known and idx not in contradictory:
            p.size = known[idx]
        else:
            p.variable = True

    # Propagation can still be fed by an equation that silently contained a
    # variable property, so discard any size the observations fail to reproduce
    # and repeat until what remains is self-consistent. A reported size is then
    # one that explains every observation it appears in.
    while True:
        bad = set()
        for o in obs:
            if len(by_set[o.indices]) != 1:
                continue
            if any(not layout.properties[i].solved for i in o.indices):
                continue
            if sum(layout.properties[i].size for i in o.indices) != o.fixed_bytes:
                bad |= set(o.indices)
        if not bad:
            break
        for i in bad:
            layout.properties[i].size = None
            layout.properties[i].variable = True

    for o in obs:
        if len(by_set[o.indices]) != 1:
            continue
        if any(not layout.properties[i].solved for i in o.indices):
            continue
        layout.checked += 1
        if sum(layout.properties[i].size for i in o.indices) == o.fixed_bytes:
            layout.validated += 1
    return layout


def build(reader, assets) -> dict:
    """Solve property layouts for every class in the game.

    Args:
        reader (cqmod.pak.PakReader): An open archive.
        assets (list[cqmod.catalog.Asset]): The catalog to walk.

    Returns:
        dict[str, ClassLayout]: One layout per class, keyed by class name.
    """
    return {cls: solve(cls, obs) for cls, obs in observe(reader, assets).items()}


def offsets(layout: ClassLayout, export, payload: bytes) -> dict:
    """Place every property of one export in its payload.

    Walks the export's properties in index order, accumulating sizes. Text
    properties have no fixed size, so their extent is measured from the payload
    itself.

    Args:
        layout (ClassLayout): The solved layout for the export's class.
        export (cqmod.catalog.ExportInfo): The export to place.
        payload (bytes): The whole ``.uexp``.

    Returns:
        dict[int, tuple[int, int]]: Property index to absolute
        ``(offset, size)``. Stops at the first property whose size is unknown,
        so a short result means the tail could not be placed.
    """
    start = export.start + export.header_bytes
    texts = dict(_text_spans(payload, start, export.end))
    out = {}
    cursor = start
    for idx in export.prop_indices:
        p = layout.properties.get(idx)
        if p is None:
            break
        if cursor in texts:
            size = texts[cursor] - cursor
        elif p.solved:
            size = p.size
        else:
            break
        out[idx] = (cursor, size)
        cursor += size
    return out


def save(layouts: dict, path) -> None:
    """Write solved layouts to JSON.

    Args:
        layouts (dict[str, ClassLayout]): Layouts from :func:`build`.
        path (str | Path): Destination file.
    """
    Path(path).write_text(json.dumps(
        {k: asdict(v) for k, v in sorted(layouts.items())}, indent=1))


def load(path) -> dict:
    """Read layouts previously written by :func:`save`.

    Args:
        path (str | Path): File to read.

    Returns:
        dict[str, ClassLayout]: The restored layouts.
    """
    raw = json.loads(Path(path).read_text())
    out = {}
    for cls, d in raw.items():
        props = {int(k): PropertyLayout(**v) for k, v in d.pop("properties").items()}
        out[cls] = ClassLayout(properties=props, **d)
    return out
