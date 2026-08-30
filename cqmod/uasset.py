"""Parsing cooked Unreal .uasset package headers.

Only what the tools need: the name table, import/export tables, and enough of
the summary to locate each export's payload inside the paired .uexp file.

These packages are cooked with PKG_UnversionedProperties, so property *values*
in the .uexp are a presence bitmask against each class's declared property
order rather than a self-describing name/type stream. See `unversioned.py`.
"""
from __future__ import annotations
import struct
from dataclasses import dataclass, field

PACKAGE_MAGIC = 0x9E2A83C1
PKG_UNVERSIONED_PROPERTIES = 0x00002000
PKG_FILTER_EDITOR_ONLY = 0x80000000
PKG_COOKED = 0x00000200

IMPORT_STRIDE = 32
EXPORT_STRIDE = 96   # measured as (dependsOffset - exportOffset) / exportCount


class AssetError(RuntimeError):
    pass


@dataclass
class Import:
    class_package: str
    class_name: str
    outer_index: int
    object_name: str


@dataclass
class Export:
    class_index: int
    outer_index: int
    object_name: str
    serial_size: int
    serial_offset: int      # absolute from package start
    class_name: str = ""    # resolved via class_index

    def uexp_slice(self, header_size: int):
        start = self.serial_offset - header_size
        return start, start + self.serial_size


@dataclass
class Package:
    name: str
    flags: int
    header_size: int
    names: list = field(default_factory=list)
    imports: list = field(default_factory=list)
    exports: list = field(default_factory=list)

    @property
    def unversioned(self) -> bool:
        return bool(self.flags & PKG_UNVERSIONED_PROPERTIES)

    def resolve(self, package_index: int) -> str:
        """FPackageIndex: >0 export (1-based), <0 import (-1-based), 0 = null."""
        if package_index > 0:
            i = package_index - 1
            return self.exports[i].object_name if i < len(self.exports) else f"<export {package_index}>"
        if package_index < 0:
            i = -package_index - 1
            return self.imports[i].object_name if i < len(self.imports) else f"<import {package_index}>"
        return ""


def _fstring(buf, off):
    (n,) = struct.unpack_from("<i", buf, off); off += 4
    if n == 0:
        return "", off
    if n < 0:
        return buf[off:off - n * 2].decode("utf-16-le").rstrip("\0"), off - n * 2
    return buf[off:off + n - 1].decode("latin-1"), off + n


def parse(data: bytes) -> Package:
    (magic,) = struct.unpack_from("<I", data, 0)
    if magic != PACKAGE_MAGIC:
        raise AssetError(f"not a UE package (magic {magic:#x})")
    o = 8 + 16                       # legacy version + UE3/UE4/UE5/licensee versions
    (ncv,) = struct.unpack_from("<i", data, o); o += 4 + 20 * ncv
    (header_size,) = struct.unpack_from("<i", data, o); o += 4
    pkg_name, o = _fstring(data, o)
    (flags,) = struct.unpack_from("<I", data, o); o += 4
    name_count, name_off = struct.unpack_from("<ii", data, o); o += 8
    o += 8                           # soft object paths count/offset
    o += 8                           # gatherable text count/offset
    export_count, export_off = struct.unpack_from("<ii", data, o); o += 8
    import_count, import_off = struct.unpack_from("<ii", data, o); o += 8

    pkg = Package(pkg_name, flags, header_size)

    p = name_off
    for _ in range(name_count):
        s, p = _fstring(data, p)
        p += 4                       # two uint16 name hashes
        pkg.names.append(s)

    def nm(i):
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
