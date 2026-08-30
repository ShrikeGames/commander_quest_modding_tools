"""FText values inside cooked export data.

Card titles and descriptions are string-table references, which serialize as:
    uint32 Flags | int8 HistoryType(=11) | FName TableId | FString Key
The FName is an index into the owning package's name table.
"""
from __future__ import annotations
import struct
from dataclasses import dataclass

HISTORY_STRING_TABLE = 11


@dataclass
class StringTableText:
    offset: int          # where the FText starts in the export data
    end: int
    table_name: str      # e.g. '/Game/.../ST_Card_Supply.ST_Card_Supply'
    key: str             # e.g. 'Insight_Title'


def find_all(data: bytes, names: list) -> list:
    """Scan an export payload for string-table FTexts.

    Scanning rather than walking properties by index: it needs no schema and is
    stable across the different card classes, which order their properties
    differently.
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
