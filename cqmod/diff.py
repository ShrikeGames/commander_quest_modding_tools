"""Locating meaningful fields by comparing two assets.

Without a .usmap the tools cannot name properties, but the game ships upgraded
'+' variants of most cards that differ from their base only in the values that
matter. Diffing a card against its variant points straight at the interesting
offsets -- this is how the draw/send counts on Insight were found.
"""
from __future__ import annotations
import struct
from dataclasses import dataclass


@dataclass
class FieldDiff:
    export_index: int
    export_class: str
    offset_a: int          # absolute offset in asset A's .uexp
    offset_b: int
    value_a: int
    value_b: int

    @property
    def plausible(self) -> bool:
        """Gameplay numbers are small and non-negative; huge or negative values
        are almost always misaligned reads straddling strings or pointers."""
        return 0 <= self.value_a <= 9999 and 0 <= self.value_b <= 9999

    @property
    def rank(self):
        return (not self.plausible, abs(self.value_a - self.value_b),
                self.value_a, self.offset_a)


def variant_name(name: str) -> str:
    """The upgraded counterpart of a card, by the game's naming convention."""
    return name if name.endswith("+") else name + "+"


def _text_spans(payload: bytes, names: list):
    """Byte ranges occupied by FText values, so string differences (a card and
    its '+' variant have different text keys) do not drown out real fields."""
    from . import ftext
    return [(t.offset, t.end) for t in ftext.find_all(payload, names)]


def compare(asset_a, payload_a: bytes, asset_b, payload_b: bytes,
            names_a=None, names_b=None, only_plausible=False) -> list:
    """Diff int32 slots of matching exports, aligned by offset within each
    export's value region. Exports are matched by class and ordinal so that a
    base card missing an export (e.g. an inherited title) still lines up."""
    out = []
    skip_a = _text_spans(payload_a, names_a) if names_a else []
    skip_b = _text_spans(payload_b, names_b) if names_b else []

    def in_span(o, spans):
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
    """A single changed byte shows up at up to four consecutive int32 offsets.
    Keep the best-ranked representative of each overlapping run."""
    kept = []
    for f in diffs:
        if any(g.export_index == f.export_index and abs(g.offset_a - f.offset_a) < 4
               for g in kept):
            continue
        kept.append(f)
    return kept
