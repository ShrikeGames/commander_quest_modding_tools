"""``FText`` values inside cooked export data.

Card titles and descriptions are string-table references rather than inline
strings. They serialize as::

    uint32  Flags
    int8    HistoryType   (11 = StringTableEntry)
    FName   TableId       index into the owning package's name table
    FString Key           e.g. 'Insight_Title'

The key is looked up in the string table at runtime, and localized through
``Game.locres``, so editing card text means editing the locres, not the asset.
See :mod:`cqmod.locres`.
"""
from __future__ import annotations
import struct
from dataclasses import dataclass

HISTORY_STRING_TABLE = 11
"""``ETextHistoryType::StringTableEntry``."""


@dataclass
class StringTableText:
    """A string-table ``FText`` located inside an export payload.

    Attributes:
        offset (int): Where the ``FText`` starts in the ``.uexp``.
        end (int): One past its last byte, so ``[offset:end]`` is the whole value.
        table_name (str): Package path of the string table, e.g.
            ``/Game/Data/Cards/SupplyCards/ST_Card_Supply.ST_Card_Supply``. The
            part after the final dot is the locres namespace.
        key (str): Lookup key within that table, e.g. ``Insight_Title``.
    """

    offset: int
    end: int
    table_name: str
    key: str


def find_all(data: bytes, names: list) -> list:
    """Scan an export payload for string-table ``FText`` values.

    This scans for the byte signature rather than walking properties by index,
    deliberately: property order differs between card classes and would need a
    ``.usmap`` to follow, whereas the signature is unambiguous enough to match
    directly. A candidate is only accepted if its name index is in range and its
    key is printable, which rejects coincidental byte patterns.

    Args:
        data (bytes): An export payload, or a whole ``.uexp``.
        names (list[str]): The owning package's name table, used to resolve the
            ``FName`` table reference.

    Returns:
        list[StringTableText]: Matches in ascending offset order. For cards this
        is typically the title followed by the description.
    """
    out = []
    o = 0
    n = len(data)
    while o + 17 <= n:
        flags, hist = struct.unpack_from("<IB", data, o)
        if hist == HISTORY_STRING_TABLE and flags == 0:
            p = o + 5
            (nidx, nnum) = struct.unpack_from("<II", data, p); p += 8
            if 0 <= nidx < len(names):
                (klen,) = struct.unpack_from("<i", data, p)
                if 0 < klen < 512 and p + 4 + klen <= n:
                    key = data[p + 4:p + 4 + klen - 1].decode("latin-1", "replace")
                    if key.isprintable():
                        out.append(StringTableText(o, p + 4 + klen, names[nidx], key))
                        o = p + 4 + klen
                        continue
        o += 1
    return out
