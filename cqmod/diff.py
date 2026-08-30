"""Locating meaningful fields by comparing two assets.

Without a ``.usmap`` the tools cannot name properties -- but the game ships
upgraded ``+`` variants of most cards, and a variant differs from its base
almost exclusively in the values that matter. Diffing the two therefore points
straight at the gameplay numbers.

Two filters make the result usable. Text spans are excluded, because a card and
its variant have different ``FText`` keys and the differing string bytes would
otherwise dominate. And overlapping matches are collapsed, because reading
``int32`` at every byte offset means one changed byte shows up at up to four
consecutive offsets.

Across all 281 base/variant pairs in the game this yields a median of 0 and a
maximum of 6 candidates. See ``docs/finding-fields.md``.
"""
from __future__ import annotations
import struct
from dataclasses import dataclass


@dataclass
class FieldDiff:
    """One integer slot whose value differs between two assets.

    Attributes:
        export_index (int): 1-based index of the export the slot lives in.
        export_class (str): Class of that export, e.g. ``CMEffectData_MoveCard``.
            Usually the strongest hint about what the number means.
        offset_a (int): Byte offset in asset A's ``.uexp``. This is the offset to
            use when staging a :class:`cqmod.project.ValueEdit`.
        offset_b (int): The corresponding offset in asset B's ``.uexp``.
        value_a (int): Value read from asset A.
        value_b (int): Value read from asset B.
    """

    export_index: int
    export_class: str
    offset_a: int
    offset_b: int
    value_a: int
    value_b: int

    @property
    def plausible(self) -> bool:
        """Whether this looks like a gameplay number rather than a stray read.

        Returns:
            bool: True if both values are small and non-negative. Damage, costs
            and counts are; huge or negative values almost always mean the
            4-byte window straddled a string, pointer or float.
        """
        return 0 <= self.value_a <= 9999 and 0 <= self.value_b <= 9999

    @property
    def rank(self):
        """Sort key placing the most likely real fields first.

        Returns:
            tuple: Orders plausible values before implausible ones, then prefers
            small deltas (an upgrade nudging 1 to 2 beats an unrelated jump),
            then small values, then position.
        """
        return (not self.plausible, abs(self.value_a - self.value_b),
                self.value_a, self.offset_a)


def variant_name(name: str) -> str:
    """Return the upgraded counterpart of an asset name.

    Args:
        name (str): Base asset name, e.g. ``DA_Card_Supply_Human_Insight``.

    Returns:
        str: The variant name with ``+`` appended, or ``name`` unchanged if it
        is already a variant. Callers should treat an unchanged return as
        "no variant to compare against".
    """
    return name if name.endswith("+") else name + "+"


def _text_spans(payload: bytes, names: list):
    """Byte ranges occupied by ``FText`` values, for exclusion from the diff.

    Args:
        payload (bytes): The ``.uexp`` payload.
        names (list[str]): The package's name table.

    Returns:
        list[tuple[int, int]]: ``(start, end)`` ranges to ignore.
    """
    from . import ftext
    return [(t.offset, t.end) for t in ftext.find_all(payload, names)]


def compare(asset_a, payload_a: bytes, asset_b, payload_b: bytes,
            names_a=None, names_b=None, only_plausible=False) -> list:
    """Diff the integer slots of two assets and report what differs.

    Exports are matched by class and ordinal rather than by position, so a base
    card missing an export its variant has (an inherited title, say) still lines
    up correctly. Within each matched pair, offsets are compared relative to the
    start of the export's value region, since the headers may differ in length.

    Args:
        asset_a (cqmod.catalog.Asset): The asset being edited.
        payload_a (bytes): Its ``.uexp`` contents.
        asset_b (cqmod.catalog.Asset): The asset to compare against, typically
            the ``+`` variant.
        payload_b (bytes): Its ``.uexp`` contents.
        names_a (list[str] | None): Asset A's name table. Supply it to exclude
            text spans; omitting it makes the result much noisier.
        names_b (list[str] | None): Asset B's name table, likewise.
        only_plausible (bool): Drop candidates failing :attr:`FieldDiff.plausible`.
            Recommended for interactive use.

    Returns:
        list[FieldDiff]: Candidates, best first, with overlapping matches
        collapsed to one representative each.
    """
    out = []
    skip_a = _text_spans(payload_a, names_a) if names_a else []
    skip_b = _text_spans(payload_b, names_b) if names_b else []

    def in_span(o, spans):
        """Whether a 4-byte read at ``o`` touches any excluded range.

        Args:
            o (int): Offset of the read.
            spans (list[tuple[int, int]]): Ranges to test against.

        Returns:
            bool: True if the read overlaps a span.
        """
        return any(s <= o < e or s < o + 4 <= e for s, e in spans)

    by_class_b: dict[str, list] = {}
    for e in asset_b.exports:
        by_class_b.setdefault(e.class_name, []).append(e)
    seen: dict[str, int] = {}

    for ea in asset_a.exports:
        n = seen.get(ea.class_name, 0)
        seen[ea.class_name] = n + 1
        cands = by_class_b.get(ea.class_name, [])
        if n >= len(cands):
            continue
        eb = cands[n]
        sa, sb = ea.start + ea.header_bytes, eb.start + eb.header_bytes
        span = min(ea.end - sa, eb.end - sb)
        for d in range(max(0, span - 3)):
            oa, ob = sa + d, sb + d
            if oa + 4 > len(payload_a) or ob + 4 > len(payload_b):
                break
            (va,) = struct.unpack_from("<i", payload_a, oa)
            (vb,) = struct.unpack_from("<i", payload_b, ob)
            if va != vb and not in_span(oa, skip_a) and not in_span(ob, skip_b):
                out.append(FieldDiff(ea.index, ea.class_name, oa, ob, va, vb))

    out.sort(key=lambda f: f.rank)
    if only_plausible:
        out = [f for f in out if f.plausible]
    return _dedupe(out)


def _dedupe(diffs: list) -> list:
    """Collapse overlapping matches caused by the one-byte scan stride.

    A single changed byte is visible at up to four consecutive ``int32`` offsets.
    Since ``diffs`` arrives sorted best-first, keeping the first of each
    overlapping run keeps the best-ranked representative.

    Args:
        diffs (list[FieldDiff]): Candidates, already ranked.

    Returns:
        list[FieldDiff]: One entry per distinct changed location.
    """
    kept = []
    for f in diffs:
        if any(g.export_index == f.export_index and abs(g.offset_a - f.offset_a) < 4
               for g in kept):
            continue
        kept.append(f)
    return kept
