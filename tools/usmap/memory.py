"""Reading another process's memory through /proc.

Used by the reflection walker to recover property names from the running game.
Requires the game to be running and, on most distributions, ptrace to be
permitted (``sudo sysctl -w kernel.yama.ptrace_scope=0``).
"""
from __future__ import annotations
import os, re, struct
from dataclasses import dataclass


@dataclass
class Region:
    """One mapped, readable range of a process's address space.

    Attributes:
        lo (int): First address.
        hi (int): One past the last address.
        path (str): Backing file, empty for anonymous memory.
    """

    lo: int
    hi: int
    path: str

    @property
    def size(self) -> int:
        """Length of the region in bytes.

        Returns:
            int: ``hi - lo``.
        """
        return self.hi - self.lo


class ProcessMemory:
    """Random-access reader over a live process.

    Args:
        pid (int): Process to read.

    Raises:
        PermissionError: If ptrace is restricted. The caller should suggest
            relaxing ``kernel.yama.ptrace_scope``.
    """

    def __init__(self, pid: int):
        """Open the process and enumerate its readable regions."""
        self.pid = pid
        self.regions = []
        for line in open(f"/proc/{pid}/maps"):
            parts = line.split()
            if len(parts) < 2 or "r" not in parts[1]:
                continue
            path = parts[5] if len(parts) > 5 else ""
            if ".pak" in path or "/dev/" in path:
                continue
            lo, hi = (int(x, 16) for x in parts[0].split("-"))
            self.regions.append(Region(lo, hi, path))
        self._fd = os.open(f"/proc/{pid}/mem", os.O_RDONLY)

    def close(self):
        """Close the memory handle."""
        os.close(self._fd)

    def __enter__(self):
        """Enter a context manager.

        Returns:
            ProcessMemory: This reader.
        """
        return self

    def __exit__(self, *a):
        """Close on leaving a context manager.

        Args:
            *a: Standard exception triple, ignored.
        """
        self.close()

    def read(self, addr: int, size: int) -> bytes:
        """Read raw bytes.

        Args:
            addr (int): Address to read from.
            size (int): Byte count.

        Returns:
            bytes: The data, or an empty bytes object if the read failed.
        """
        try:
            return os.pread(self._fd, size, addr)
        except OSError:
            return b""

    def u16(self, addr):
        """Read an unsigned 16-bit value.

        Args:
            addr (int): Address to read.

        Returns:
            int | None: The value, or None if unreadable.
        """
        d = self.read(addr, 2)
        return struct.unpack("<H", d)[0] if len(d) == 2 else None

    def u32(self, addr):
        """Read an unsigned 32-bit value.

        Args:
            addr (int): Address to read.

        Returns:
            int | None: The value, or None if unreadable.
        """
        d = self.read(addr, 4)
        return struct.unpack("<I", d)[0] if len(d) == 4 else None

    def u64(self, addr):
        """Read an unsigned 64-bit value, typically a pointer.

        Args:
            addr (int): Address to read.

        Returns:
            int | None: The value, or None if unreadable.
        """
        d = self.read(addr, 8)
        return struct.unpack("<Q", d)[0] if len(d) == 8 else None

    def find(self, needle: bytes, limit: int = 200, chunk: int = 32 << 20):
        """Search every readable region for a byte pattern.

        Args:
            needle (bytes): Pattern to find.
            limit (int): Stop after this many hits.
            chunk (int): Read size per pass.

        Returns:
            list[int]: Addresses where the pattern occurs.
        """
        hits = []
        for reg in self.regions:
            off = 0
            while off < reg.size:
                n = min(reg.size - off, chunk)
                buf = self.read(reg.lo + off, n)
                if not buf:
                    break
                start = 0
                while True:
                    i = buf.find(needle, start)
                    if i < 0:
                        break
                    hits.append(reg.lo + off + i)
                    start = i + 1
                    if len(hits) >= limit:
                        return hits
                if len(buf) < n:
                    break
                off += n - len(needle)
        return hits

    def region_of(self, addr: int):
        """Find the region containing an address.

        Args:
            addr (int): Address to locate.

        Returns:
            Region | None: The containing region, or None.
        """
        for r in self.regions:
            if r.lo <= addr < r.hi:
                return r
        return None
