"""Adding a property to an export that does not set one.

Unversioned serialization omits any property left at its default, so a unit with
no attack has no ``AttackDamage`` entry and there is nothing to edit. Giving it
one means inserting the index into the export's header and its value into the
payload, which changes the payload's length and therefore the export's recorded
size.

That makes this the one edit that is not length-preserving. The header fix is
the same shifting problem as adding a name, and is handled by
:func:`cqmod.uasset.resize_export`.
"""
from __future__ import annotations
import struct

from . import uasset, unversioned

EXPORT_TRAILER = 4
"""Four zero bytes terminate every export's value region."""


class PayloadError(RuntimeError):
    """Raised when a property cannot be inserted."""


def add_property(header: bytes, payload: bytes, asset, export_index: int,
                 prop_index: int, value: bytes, um) -> tuple:
    """Insert a property into an export that does not currently set it.

    The value goes after the last property with a lower index, which is where
    the serializer expects it. Everything before that point is untouched, so
    existing offsets below the insertion stay valid.

    Args:
        header (bytes): The package's ``.uasset``.
        payload (bytes): The package's ``.uexp``.
        asset (cqmod.catalog.Asset): The asset being edited.
        export_index (int): Zero-based index of the export to change.
        prop_index (int): Property index to add.
        value (bytes): Its serialized value.
        um (cqmod.usmap.Usmap): Schema, used to place existing properties.

    Returns:
        tuple[bytes, bytes]: The new ``.uasset`` and ``.uexp``.

    Raises:
        PayloadError: If the export already sets the property, or its existing
            properties cannot all be placed, which would make the insertion
            point a guess.
    """
    export = asset.exports[export_index]
    if prop_index in export.prop_indices:
        raise PayloadError(f"export already sets property {prop_index}")

    placed = um.place(export, payload)
    if len(placed) != len(export.prop_indices):
        raise PayloadError(
            "cannot place every existing property of this export, so the "
            "insertion point is not known")

    # Values are written in index order, so the new one follows the last
    # property below it.
    before = [f for f in placed if f.index < prop_index]
    if before:
        last = max(before, key=lambda f: f.index)
        at = (last.offset + last.size) if last.offset >= 0 else \
            export.start + export.header_bytes
    else:
        at = export.start + export.header_bytes

    new_indices = sorted(list(export.prop_indices) + [prop_index])
    new_header = unversioned.build(new_indices, export.zero_indices)
    grow = len(new_header) - export.header_bytes + len(value)

    body = payload[export.start + export.header_bytes:at]
    rest = payload[at:export.end]
    new_payload = (payload[:export.start] + new_header + body + value + rest
                   + payload[export.end:])
    if len(new_payload) != len(payload) + grow:
        raise PayloadError("payload length did not change as expected")

    return uasset.resize_export(header, export_index, grow), new_payload
