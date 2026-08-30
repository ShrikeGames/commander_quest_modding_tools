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
