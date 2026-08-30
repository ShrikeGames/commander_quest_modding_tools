"""Reading Unreal's reflection data out of the running game.

Cooked packages here omit property names and types, so the only place that
information still exists is the engine's own runtime structures. This module
locates them without symbols, bootstrapping from a single known name:

1. Find that name's ``FNameEntry`` in memory, then the ``FNamePool`` block table
   that contains it, which makes every name ID resolvable to a string.
2. Find the ``UClass`` describing classes (the object named ``Class`` whose own
   class is itself), then every object pointing at it, which is every class.
3. Walk each class's ``ChildProperties`` chain for names, types and sizes,
   following ``SuperStruct`` to pick up inherited properties.

Offsets below were measured against this build by dumping known structures and
matching them to values we could already verify, not taken from headers.
"""
from __future__ import annotations
import struct, subprocess
from dataclasses import dataclass, field
from pathlib import Path

from memory import ProcessMemory

SCAN = Path(__file__).resolve().parent / "scan"

# UObject
OBJ_CLASS = 0x10
OBJ_NAME = 0x18
OBJ_OUTER = 0x20
# UStruct
STRUCT_SUPER = 0x40
STRUCT_CHILDREN = 0x48
STRUCT_CHILD_PROPS = 0x50
STRUCT_PROPS_SIZE = 0x58
# FField
FIELD_CLASS = 0x08
FIELD_NEXT = 0x18
FIELD_NAME = 0x20
# FProperty
PROP_ARRAY_DIM = 0x30
PROP_ELEM_SIZE = 0x34
PROP_FLAGS = 0x38
# FStructProperty::Struct and FArrayProperty::Inner sit just past FProperty.
PROP_STRUCT = 0x70
PROP_INNER = 0x78

BLOCK_BYTES = 0x20000
"""Each FName block spans 65536 two-byte slots."""


class ReflectionError(RuntimeError):
    """Raised when the engine's structures cannot be located or make no sense."""


def scan(pid: int, mode: str, *args) -> list:
    """Run the native scanner and collect the addresses it reports.

    Args:
        pid (int): Target process.
        mode (str): ``bytes``, ``ptr`` or ``dword``.
        *args: Mode arguments, stringified.

    Returns:
        list[int]: Matching addresses.

    Raises:
        ReflectionError: If the scanner binary has not been built.
    """
    if not SCAN.is_file():
        raise ReflectionError(f"{SCAN} not built; run `make -C tools/usmap`")
    r = subprocess.run([str(SCAN), mode, str(pid), *map(str, args)],
                       capture_output=True, text=True)
    return [int(x, 16) for x in r.stdout.split()]


@dataclass
class Property:
    """One reflected property.

    Attributes:
        index (int): Position in its class's declared order, which is what
            unversioned property headers refer to.
        name (str): Property name as declared in C++.
        type (str): Property class, e.g. ``IntProperty`` or ``ArrayProperty``.
        size (int): ``ElementSize``, the bytes one element occupies in memory.
        array_dim (int): Fixed array dimension, normally 1.
        owner (str): Class that declares it, which may be a parent class.
        struct (str): For a ``StructProperty``, the struct's name, e.g.
            ``GameplayTagContainer``. Empty otherwise.
        inner (str): For an ``ArrayProperty``, its element type, e.g.
            ``ObjectProperty``. Empty otherwise.
    """

    index: int
    name: str
    type: str
    size: int
    array_dim: int = 1
    owner: str = ""
    struct: str = ""
    inner: str = ""


@dataclass
class Klass:
    """One reflected class and the properties it exposes.

    Attributes:
        name (str): Class name.
        super_name (str): Immediate parent, empty at the root.
        properties (list[Property]): Own and inherited properties, in the order
            the serializer numbers them.
        properties_size (int): Total instance size in bytes.
    """

    name: str
    super_name: str = ""
    properties: list = field(default_factory=list)
    properties_size: int = 0


class Reflection:
    """Locates and reads the engine's reflection data in a live process.

    Args:
        pid (int): The running game.
        anchor (str): A name known to exist in the game's name pool, used to
            find the pool itself.

    Raises:
        ReflectionError: If the name pool or the class registry cannot be found.
    """

    def __init__(self, pid: int, anchor: str = "CMCardData"):
        """Attach to the process and bootstrap the name pool and class list."""
        self.pid = pid
        self.m = ProcessMemory(pid)
        self.blocks = self._find_name_pool(anchor)
        self.class_class = self._find_class_class()

    def close(self):
        """Release the process handle."""
        self.m.close()

    # -- names -----------------------------------------------------------
    def _find_name_pool(self, anchor: str) -> list:
        """Locate the FName block table.

        Finds the anchor's entry, then any pointer into the 128 KB block holding
        it, then walks back to the allocator header. The header records how many
        blocks are live, which is checked against the array actually being null
        just past that point.

        Args:
            anchor (str): A name expected to be in the pool.

        Returns:
            list[int]: Block base addresses, indexed as name IDs expect.

        Raises:
            ReflectionError: If no candidate validates.
        """
        want = anchor.encode()
        for addr in scan(self.pid, "bytes", want.hex()):
            head = self.m.u16(addr - 2)
            if head is None or head & 1 or (head >> 6) != len(want):
                continue
            entry = addr - 2
            for loc in scan(self.pid, "ptr", hex(entry - BLOCK_BYTES), hex(entry + 1)):
                blocks = self._blocks_from(loc)
                if blocks and any(b <= entry < b + BLOCK_BYTES for b in blocks):
                    self._anchor_entry = entry
                    return blocks
        raise ReflectionError("could not locate the FName pool")

    def _blocks_from(self, loc: int):
        """Try to read a block table that includes the pointer at ``loc``.

        Args:
            loc (int): Address of a suspected block pointer.

        Returns:
            list[int] | None: The block table, or None if this is not one.
        """
        start = loc
        while True:
            prev = self.m.u64(start - 8)
            if prev and self.m.region_of(prev) and prev % 0x1000 == 0:
                start -= 8
                if loc - start > 8192 * 8:
                    return None
            else:
                break
        header = self.m.u64(start - 8)
        if header is None:
            return None
        current_block = header & 0xFFFFFFFF
        if not 0 < current_block < 8192:
            return None
        blocks = [self.m.u64(start + 8 * i) for i in range(current_block + 1)]
        if any(not b or not self.m.region_of(b) for b in blocks):
            return None
        if self.m.u64(start + 8 * (current_block + 1)):
            return None                      # must be null just past the last
        return blocks

    def name(self, fid):
        """Resolve a name ID to its string.

        Args:
            fid (int | None): ``FNameEntryId``, as stored in an ``FName``.

        Returns:
            str | None: The name, or None if the ID does not resolve.
        """
        if fid is None:
            return None
        b, off = fid >> 16, (fid & 0xFFFF) * 2
        if b >= len(self.blocks):
            return None
        a = self.blocks[b] + off
        h = self.m.u16(a)
        if h is None or h & 1:
            return None
        ln = h >> 6
        if not 0 < ln < 128:
            return None
        try:
            return self.m.read(a + 2, ln).decode("ascii")
        except Exception:
            return None

    def object_name(self, obj):
        """Read a UObject's name.

        Args:
            obj (int | None): Object address.

        Returns:
            str | None: Its name, or None if unreadable.
        """
        if not obj or not self.m.region_of(obj):
            return None
        return self.name(self.m.u32(obj + OBJ_NAME))

    # -- classes ---------------------------------------------------------
    def _find_class_class(self) -> int:
        """Locate the UClass that describes classes.

        Returns:
            int: Address of the object named ``Class`` whose own class is itself.

        Raises:
            ReflectionError: If it cannot be found.
        """
        for addr in scan(self.pid, "bytes", b"Class".hex()):
            h = self.m.u16(addr - 2)
            if h is None or h & 1 or (h >> 6) != 5:
                continue
            fid = self._id_of(addr - 2)
            if fid is None:
                continue
            for hit in scan(self.pid, "dword", fid):
                obj = hit - OBJ_NAME
                if self.m.u64(obj + OBJ_CLASS) == obj:
                    return obj
        raise ReflectionError("could not locate the Class object")

    def _id_of(self, entry: int):
        """Compute the name ID of an entry at a known address.

        Args:
            entry (int): Address of the ``FNameEntry`` header.

        Returns:
            int | None: The ID, or None if the entry is outside every block.
        """
        for i, b in enumerate(self.blocks):
            if b <= entry < b + BLOCK_BYTES:
                return (i << 16) | ((entry - b) // 2)
        return None

    def classes(self) -> list:
        """Find every UClass in the process.

        Returns:
            list[int]: Addresses of objects whose class is the class-class.
        """
        out = []
        for hit in scan(self.pid, "ptr", hex(self.class_class), hex(self.class_class + 1)):
            obj = hit - OBJ_CLASS
            if self.object_name(obj):
                out.append(obj)
        return out

    def read_class(self, obj: int) -> Klass:
        """Read one class and all the properties it exposes.

        Properties are collected from the root of the inheritance chain
        downwards, because that is the order the serializer numbers them in.

        Args:
            obj (int): Address of the UClass.

        Returns:
            Klass: Its name, parent, size and full property list.
        """
        k = Klass(self.object_name(obj) or "?",
                  self.object_name(self.m.u64(obj + STRUCT_SUPER)) or "",
                  properties_size=self.m.u32(obj + STRUCT_PROPS_SIZE) or 0)
        chain = []
        cur = obj
        seen = set()
        while cur and self.m.region_of(cur) and cur not in seen:
            seen.add(cur)
            chain.append(cur)
            cur = self.m.u64(cur + STRUCT_SUPER)
        # UE links a class's own properties first, then walks up to its super,
        # and the unversioned serializer numbers them in that order.
        for owner in chain:
            oname = self.object_name(owner) or "?"
            f = self.m.u64(owner + STRUCT_CHILD_PROPS)
            guard = 0
            while f and self.m.region_of(f) and guard < 512:
                guard += 1
                fc = self.m.u64(f + FIELD_CLASS)
                ptype = (self.name(self.m.u32(fc))
                         if fc and self.m.region_of(fc) else "?") or "?"
                struct_name = inner_type = ""
                if ptype == "StructProperty":
                    sp = self.m.u64(f + PROP_STRUCT)
                    struct_name = self.object_name(sp) or ""
                elif ptype in ("ArrayProperty", "SetProperty"):
                    ip = self.m.u64(f + PROP_INNER)
                    if ip and self.m.region_of(ip):
                        ic = self.m.u64(ip + FIELD_CLASS)
                        inner_type = (self.name(self.m.u32(ic))
                                      if ic and self.m.region_of(ic) else "") or ""
                k.properties.append(Property(
                    index=len(k.properties),
                    name=self.name(self.m.u32(f + FIELD_NAME)) or "?",
                    type=ptype,
                    size=self.m.u32(f + PROP_ELEM_SIZE) or 0,
                    array_dim=self.m.u32(f + PROP_ARRAY_DIM) or 1,
                    owner=oname,
                    struct=struct_name,
                    inner=inner_type,
                ))
                f = self.m.u64(f + FIELD_NEXT)
        return k
