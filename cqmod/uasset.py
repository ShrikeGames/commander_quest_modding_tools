"""Parsing cooked Unreal ``.uasset`` package headers.

Only what the tools need is decoded: the name table, the import and export
tables, and enough of the summary to locate each export's payload inside the
paired ``.uexp``.

A cooked package is split in two. The ``.uasset`` holds the header, meaning the
name table, the imports, the exports and their offsets. The ``.uexp`` holds the
serialized property data those exports point at. Export offsets are absolute from the start
of the *logical* package, so subtracting :attr:`Package.header_size` converts
them to ``.uexp`` offsets; :meth:`Export.uexp_slice` does that for you.

These packages set ``PKG_UnversionedProperties``, so property values in the
``.uexp`` are a presence bitmask against each class's declared property order
rather than a self-describing stream. See :mod:`cqmod.unversioned`.
"""
from __future__ import annotations
import struct
from dataclasses import dataclass, field

PACKAGE_MAGIC = 0x9E2A83C1
"""Leading ``uint32`` of every Unreal package."""

PKG_UNVERSIONED_PROPERTIES = 0x00002000
PKG_FILTER_EDITOR_ONLY = 0x80000000
PKG_COOKED = 0x00000200

IMPORT_STRIDE = 32
"""Bytes per ``FObjectImport``, measured as ``(exportOffset - importOffset) / importCount``."""

EXPORT_STRIDE = 96
"""Bytes per ``FObjectExport``, measured as ``(dependsOffset - exportOffset) / exportCount``.

Worth stating explicitly because it is easy to get wrong: an incorrect stride
still parses the first export correctly and turns every later one into garbage.
"""


class AssetError(RuntimeError):
    """Raised when data is not a parseable Unreal package."""


@dataclass
class Import:
    """A reference to an object defined in another package.

    Attributes:
        class_package (str): Package declaring the class, e.g. ``/Script/Engine``.
        class_name (str): Class of the referenced object, e.g. ``Texture2D``.
        outer_index (int): ``FPackageIndex`` of the containing object. For an
            asset reference this points at the package import, which is how
            :func:`cqmod.catalog._texture_package` recovers art paths.
        object_name (str): Name of the referenced object.
    """

    class_package: str
    class_name: str
    outer_index: int
    object_name: str


@dataclass
class Export:
    """An object serialized into this package's ``.uexp``.

    Attributes:
        class_index (int): ``FPackageIndex`` of the object's class.
        outer_index (int): ``FPackageIndex`` of its containing object.
        object_name (str): The object's name.
        serial_size (int): Length of its payload in bytes.
        serial_offset (int): Absolute offset of its payload from the start of the
            logical package, i.e. including the ``.uasset`` header.
        class_name (str): Class name resolved from :attr:`class_index`, filled in
            by :func:`parse`.
    """

    class_index: int
    outer_index: int
    object_name: str
    serial_size: int
    serial_offset: int
    class_name: str = ""

    def uexp_slice(self, header_size: int):
        """Convert this export's absolute range into ``.uexp`` offsets.

        Args:
            header_size (int): :attr:`Package.header_size`, the ``.uasset`` length.

        Returns:
            tuple[int, int]: ``(start, end)`` offsets into the ``.uexp``.
        """
        start = self.serial_offset - header_size
        return start, start + self.serial_size


@dataclass
class Package:
    """A parsed package header.

    Attributes:
        name (str): Package path, e.g. ``/Game/Data/Cards/.../DA_Card_...``.
        flags (int): ``EPackageFlags`` bitmask.
        header_size (int): Total header size, equal to the ``.uasset`` length and
            therefore the base for converting export offsets.
        names (list[str]): The name table.
        imports (list[Import]): The import table.
        exports (list[Export]): The export table.
    """

    name: str
    flags: int
    header_size: int
    names: list = field(default_factory=list)
    imports: list = field(default_factory=list)
    exports: list = field(default_factory=list)

    @property
    def unversioned(self) -> bool:
        """Whether property data omits type information.

        Returns:
            bool: True if ``PKG_UnversionedProperties`` is set, meaning property
            names and types need a ``.usmap`` to recover.
        """
        return bool(self.flags & PKG_UNVERSIONED_PROPERTIES)

    def resolve(self, package_index: int) -> str:
        """Resolve an ``FPackageIndex`` to an object name.

        The encoding is one-based and signed: positive indexes exports, negative
        indexes imports, and zero means null.

        Args:
            package_index (int): The index to resolve.

        Returns:
            str: The object's name, an empty string for null, or a placeholder
            like ``<import -14>`` if the index is out of range.
        """
        if package_index > 0:
            i = package_index - 1
            return self.exports[i].object_name if i < len(self.exports) else f"<export {package_index}>"
        if package_index < 0:
            i = -package_index - 1
            return self.imports[i].object_name if i < len(self.imports) else f"<import {package_index}>"
        return ""


def _fstring(buf, off):
    """Read an ``FString``.

    Args:
        buf (bytes): Buffer to read from.
        off (int): Offset of the length prefix.

    Returns:
        tuple[str, int]: The string, and the offset just past it. Negative
        lengths denote UTF-16LE.
    """
    (n,) = struct.unpack_from("<i", buf, off); off += 4
    if n == 0:
        return "", off
    if n < 0:
        return buf[off:off - n * 2].decode("utf-16-le").rstrip("\0"), off - n * 2
    return buf[off:off + n - 1].decode("latin-1"), off + n


def parse(data: bytes) -> Package:
    """Parse a cooked ``.uasset`` header.

    Args:
        data (bytes): Complete ``.uasset`` contents.

    Returns:
        Package: The parsed header, with export class names resolved.

    Raises:
        AssetError: If the leading magic is not :data:`PACKAGE_MAGIC`.
        struct.error: If the data is truncated.
    """
    (magic,) = struct.unpack_from("<I", data, 0)
    if magic != PACKAGE_MAGIC:
        raise AssetError(f"not a UE package (magic {magic:#x})")

    o = 8 + 16
    (ncv,) = struct.unpack_from("<i", data, o); o += 4 + 20 * ncv
    (header_size,) = struct.unpack_from("<i", data, o); o += 4
    pkg_name, o = _fstring(data, o)
    (flags,) = struct.unpack_from("<I", data, o); o += 4
    name_count, name_off = struct.unpack_from("<ii", data, o); o += 8
    o += 8
    o += 8
    export_count, export_off = struct.unpack_from("<ii", data, o); o += 8
    import_count, import_off = struct.unpack_from("<ii", data, o); o += 8

    pkg = Package(pkg_name, flags, header_size)

    p = name_off
    for _ in range(name_count):
        s, p = _fstring(data, p)
        p += 4
        pkg.names.append(s)

    def nm(i):
        """Resolve a name-table index, tolerating out-of-range values.

        Args:
            i (int): Index into the name table.

        Returns:
            str: The name, or a ``<name N>`` placeholder.
        """
        return pkg.names[i] if 0 <= i < len(pkg.names) else f"<name {i}>"

    for k in range(import_count):
        b = import_off + k * IMPORT_STRIDE
        cp, = struct.unpack_from("<I", data, b)
        cn, = struct.unpack_from("<I", data, b + 8)
        outer, = struct.unpack_from("<i", data, b + 16)
        on, = struct.unpack_from("<I", data, b + 20)
        pkg.imports.append(Import(nm(cp), nm(cn), outer, nm(on)))

    for k in range(export_count):
        b = export_off + k * EXPORT_STRIDE
        cls, = struct.unpack_from("<i", data, b)
        outer, = struct.unpack_from("<i", data, b + 12)
        on, = struct.unpack_from("<I", data, b + 16)
        size, off = struct.unpack_from("<qq", data, b + 28)
        pkg.exports.append(Export(cls, outer, nm(on), size, off))

    for e in pkg.exports:
        e.class_name = pkg.resolve(e.class_index)
    return pkg


# Offsets in the summary that point past the name table, as
# (byte offset from the end of NameOffset, width in bytes). Anything positive
# in these fields shifts when the name table grows. Fields holding 0 or -1 are
# absent rather than located, so they are left alone.
_SHIFTING_FIELDS = [
    (0, 4),    # SoftObjectPathsOffset is at +4 of its count
]


def _skip_fstring(data: bytes, o: int) -> int:
    """Step over an ``FString`` without decoding it.

    A negative length means UTF-16, so the character data is twice as long.
    Assuming positive lengths walks backwards through the file on any package
    whose name contains a non-ASCII character.

    Args:
        data (bytes): Buffer being walked.
        o (int): Offset of the length prefix.

    Returns:
        int: Offset just past the string.
    """
    (n,) = struct.unpack_from("<i", data, o)
    return o + 4 + (n if n >= 0 else -n * 2)


def _summary_end(data: bytes):
    """Parse the summary far enough to locate every field that can shift.

    The summary runs from the file start to ``NameOffset``, and every layout
    field is read here so that insertion can adjust all of them. The caller
    checks that parsing lands exactly on ``NameOffset``, which is what proves
    the layout is fully understood for this build.

    Args:
        data (bytes): A complete ``.uasset``.

    Returns:
        dict: Field positions and values, including ``end`` where the summary
        finishes and ``shift_positions`` listing every offset field to adjust.

    Raises:
        AssetError: If the parse does not land on ``NameOffset``.
    """
    o = 8 + 16
    (ncv,) = struct.unpack_from("<i", data, o); o += 4 + 20 * ncv
    total_header_pos = o; o += 4
    o = _skip_fstring(data, o)                      # PackageName
    o += 4                                          # PackageFlags
    name_count_pos = o
    (name_count, name_offset) = struct.unpack_from("<ii", data, o); o += 8

    # Count/offset pairs and bare offsets, in file order.
    pairs = []
    for _ in range(2):                              # soft object paths, gatherable text
        pairs.append(o + 4); o += 8
    export_count_pos = o
    (export_count, _) = struct.unpack_from("<ii", data, o)
    pairs.append(o + 4); o += 8                     # export count/offset
    (import_count, _) = struct.unpack_from("<ii", data, o)
    pairs.append(o + 4); o += 8                     # import count/offset
    pairs.append(o); o += 4                         # DependsOffset
    pairs.append(o + 4); o += 8                     # soft package refs count/offset
    pairs.append(o); o += 4                         # SearchableNamesOffset
    pairs.append(o); o += 4                         # ThumbnailTableOffset
    o += 16                                         # Guid

    (gen_count,) = struct.unpack_from("<i", data, o); o += 4
    # FGenerationInfo is two int32s: ExportCount then NameCount.
    gen_name_positions = [o + 8 * i + 4 for i in range(gen_count)]
    o += 8 * gen_count
    for _ in range(2):                              # saved-by / compatible-with versions
        o = _skip_fstring(data, o + 10)             # version fields then Branch
    o += 4                                          # CompressionFlags
    (chunks,) = struct.unpack_from("<i", data, o); o += 4
    if chunks:
        raise AssetError("compressed chunks are not supported")
    o += 4                                          # PackageSource
    (extra,) = struct.unpack_from("<i", data, o); o += 4
    for _ in range(extra):
        o = _skip_fstring(data, o)
    pairs.append(o); o += 4                         # AssetRegistryDataOffset
    bulk_pos = o; o += 8                            # BulkDataStartOffset (int64)
    pairs.append(o); o += 4                         # WorldTileInfoDataOffset
    (chunk_ids,) = struct.unpack_from("<i", data, o); o += 4 + 4 * chunk_ids
    o += 4                                          # PreloadDependencyCount
    pairs.append(o); o += 4                         # PreloadDependencyOffset
    o += 4                                          # NamesReferencedFromExportDataCount
    o += 8                                          # PayloadTocOffset (int64, often -1)
    pairs.append(o); o += 4                         # DataResourceOffset

    if o != name_offset:
        raise AssetError(
            f"summary parse ended at {o} but the name table starts at {name_offset}")
    return {
        "end": o,
        "total_header_pos": total_header_pos,
        "name_count_pos": name_count_pos,
        "name_count": name_count,
        "name_offset": name_offset,
        "export_count": export_count,
        "export_count_pos": export_count_pos,
        "shift32": pairs,
        "shift64": [bulk_pos],
        "gen_name_positions": gen_name_positions,
    }


def add_name(data: bytes, new_name: str) -> bytes:
    """Append an entry to a package's name table.

    Every gameplay tag is an ``FName``, an index into this table, so a tag the
    asset has never referenced cannot be set until its name exists here. Growing
    the table shifts everything after it, so each summary offset, the total
    header size and every export's ``SerialOffset`` are adjusted by the same
    delta. Export payloads themselves are untouched, and because both the header
    size and the serial offsets move together, each export still resolves to the
    same bytes of the ``.uexp``.

    Args:
        data (bytes): The original ``.uasset``.
        new_name (str): Name to append, e.g. ``Card.SummonType.Cavalry``.

    Returns:
        bytes: A rebuilt ``.uasset`` whose last name is ``new_name``.

    Raises:
        AssetError: If the name is already present, the summary cannot be fully
            parsed, or the package uses features this does not handle.
    """
    pkg = parse(data)
    if new_name in pkg.names:
        raise AssetError(f"{new_name!r} is already in the name table")
    if any(ord(c) > 127 for c in new_name):
        raise AssetError("only ASCII names are supported")

    s = _summary_end(data)
    entry = struct.pack("<i", len(new_name) + 1) + new_name.encode("ascii") + b"\0"
    entry += b"\0" * 4                              # the two name hashes
    delta = len(entry)

    # The name table ends where the section after it begins, which is the
    # smallest positive offset in the summary.
    following = [struct.unpack_from("<i", data, p)[0] for p in s["shift32"]]
    table_end = min([v for v in following if v > s["name_offset"]] or [len(data)])

    out = bytearray(data[:table_end]) + entry + data[table_end:]

    struct.pack_into("<i", out, s["name_count_pos"], s["name_count"] + 1)
    struct.pack_into("<i", out, s["total_header_pos"],
                     struct.unpack_from("<i", data, s["total_header_pos"])[0] + delta)
    for p in s["shift32"]:
        v = struct.unpack_from("<i", data, p)[0]
        if v > s["name_offset"]:
            struct.pack_into("<i", out, p, v + delta)
    for p in s["shift64"]:
        v = struct.unpack_from("<q", data, p)[0]
        if v > s["name_offset"]:
            struct.pack_into("<q", out, p, v + delta)
    for p in s["gen_name_positions"]:
        struct.pack_into("<i", out, p, struct.unpack_from("<i", data, p)[0] + 1)

    # Export payloads live in the .uexp, but their offsets are measured from the
    # start of the logical package, so they move with the header.
    export_offset = struct.unpack_from("<i", out, s["export_count_pos"] + 4)[0]
    for k in range(s["export_count"]):
        p = export_offset + k * EXPORT_STRIDE + 36
        struct.pack_into("<q", out, p, struct.unpack_from("<q", out, p)[0] + delta)
    return bytes(out)
