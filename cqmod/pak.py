"""Reading and writing Unreal Engine .pak archives (version 11).

Reading handles Commander Quest's shipping pak: AES-256-ECB encrypted index,
Oodle Kraken compressed entries. Writing produces mod paks with an
*unencrypted* index and *uncompressed* entries -- UE accepts both, which means
a mod pak needs neither the AES key nor an Oodle compressor.
"""
from __future__ import annotations
import hashlib, struct
from dataclasses import dataclass
from pathlib import Path

from . import oodle

PAK_MAGIC = 0x5A6F12E1
FOOTER_SIZE = 221          # v11: guid(16)+enc(1)+magic(4)+ver(4)+off(8)+size(8)+hash(20)+names(160)
ENTRY_HEADER_BASE = 48     # Offset(8)+Size(8)+UncompressedSize(8)+CMI(4)+Hash(20)
DEFAULT_MOUNT = "../../../"


class PakError(RuntimeError):
    pass


@dataclass
class PakEntry:
    offset: int
    size: int
    uncompressed_size: int
    compression: int          # 0 = stored, otherwise index into the pak's method names
    encrypted: bool
    blocks: list              # [(start, end)] relative to entry start
    block_size: int

    @property
    def is_compressed(self) -> bool:
        return self.compression != 0


def _fstring(buf: bytes, off: int):
    """Deserialize an FString. Positive length is ANSI, negative is UTF-16LE."""
    (n,) = struct.unpack_from("<i", buf, off)
    off += 4
    if n == 0:
        return "", off
    if n < 0:
        s = buf[off:off - n * 2].decode("utf-16-le").rstrip("\0")
        return s, off - n * 2
    return buf[off:off + n - 1].decode("latin-1"), off + n


def pack_fstring(s: str) -> bytes:
    """Serialize an FString the way UE expects: ASCII stays single-byte, anything
    else must go out as UTF-16 or UE will read the bytes back as Latin-1."""
    if all(ord(c) < 128 for c in s):
        b = s.encode("ascii") + b"\0"
        return struct.pack("<i", len(b)) + b
    b = s.encode("utf-16-le") + b"\0\0"
    return struct.pack("<i", -(len(b) // 2)) + b


class PakReader:
    def __init__(self, path, aes_key: bytes | None = None):
        self.path = Path(path)
        self._aes_key = aes_key
        self._f = open(self.path, "rb")
        self._f.seek(0, 2)
        self.file_size = self._f.tell()
        self.entries: dict[str, PakEntry] = {}
        self._read_index()

    def close(self):
        self._f.close()

    def __enter__(self): return self
    def __exit__(self, *a): self.close()

    # -- index -----------------------------------------------------------
    def _decrypt(self, data: bytes) -> bytes:
        if self._aes_key is None:
            raise PakError("pak index is encrypted but no AES key was supplied")
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        d = Cipher(algorithms.AES(self._aes_key), modes.ECB()).decryptor()
        return d.update(data) + d.finalize()

    def _read_index(self):
        self._f.seek(self.file_size - FOOTER_SIZE)
        foot = self._f.read(FOOTER_SIZE)
        i = foot.find(struct.pack("<I", PAK_MAGIC))
        if i < 0:
            raise PakError(f"{self.path} is not a .pak (magic not found)")
        self.encrypted_index = bool(foot[i - 1])
        (self.version,) = struct.unpack_from("<i", foot, i + 4)
        idx_off, idx_size = struct.unpack_from("<qq", foot, i + 8)
        idx_hash = foot[i + 24:i + 44]
        self.compression_methods = [
            foot[i + 44 + 32 * k: i + 44 + 32 * (k + 1)].rstrip(b"\0").decode("latin-1")
            for k in range(5)
        ]
        self.compression_methods = [m for m in self.compression_methods if m]

        self._f.seek(idx_off)
        primary = self._f.read(idx_size)
        if self.encrypted_index:
            primary = self._decrypt(primary)
        if hashlib.sha1(primary).digest() != idx_hash:
            raise PakError("pak index hash mismatch (wrong AES key?)")

        o = 0
        self.mount_point, o = _fstring(primary, o)
        (num_entries,) = struct.unpack_from("<i", primary, o); o += 4
        o += 8  # PathHashSeed
        (has_path_hash,) = struct.unpack_from("<i", primary, o); o += 4
        if has_path_hash:
            o += 16 + 20
        (has_full_dir,) = struct.unpack_from("<i", primary, o); o += 4
        if not has_full_dir:
            raise PakError("pak has no full directory index; unsupported")
        fd_off, fd_size = struct.unpack_from("<qq", primary, o); o += 16
        fd_hash = primary[o:o + 20]; o += 20
        (enc_size,) = struct.unpack_from("<i", primary, o); o += 4
        encoded = primary[o:o + enc_size]

        self._f.seek(fd_off)
        fd = self._f.read(fd_size)
        if self.encrypted_index:
            fd = self._decrypt(fd)
        if hashlib.sha1(fd).digest() != fd_hash:
            raise PakError("pak directory index hash mismatch")

        p = 0
        (ndirs,) = struct.unpack_from("<i", fd, p); p += 4
        for _ in range(ndirs):
            dname, p = _fstring(fd, p)
            (nfiles,) = struct.unpack_from("<i", fd, p); p += 4
            for _ in range(nfiles):
                fname, p = _fstring(fd, p)
                (eoff,) = struct.unpack_from("<I", fd, p); p += 4
                self.entries[dname + fname] = self._decode_entry(encoded, eoff)
        if len(self.entries) != num_entries:
            raise PakError(f"entry count mismatch: {len(self.entries)} vs {num_entries}")

    @staticmethod
    def _decode_entry(enc: bytes, off: int) -> PakEntry:
        (v,) = struct.unpack_from("<I", enc, off); o = off + 4
        cmi = (v >> 23) & 0x3F
        if v & (1 << 31):
            (offset,) = struct.unpack_from("<I", enc, o); o += 4
        else:
            (offset,) = struct.unpack_from("<q", enc, o); o += 8
        if v & (1 << 30):
            (usize,) = struct.unpack_from("<I", enc, o); o += 4
        else:
            (usize,) = struct.unpack_from("<q", enc, o); o += 8
        if cmi:
            if v & (1 << 29):
                (size,) = struct.unpack_from("<I", enc, o); o += 4
            else:
                (size,) = struct.unpack_from("<q", enc, o); o += 8
        else:
            size = usize
        encrypted = bool(v & (1 << 22))
        nblocks = (v >> 6) & 0xFFFF
        bsize = 0
        blocks = []
        if nblocks:
            bsize = (v & 0x3F) << 11
            if (v & 0x3F) == 0x3F:
                (bsize,) = struct.unpack_from("<I", enc, o); o += 4
        if nblocks == 1 and not encrypted:
            blocks = [(0, size)]
        elif nblocks:
            cur = 0
            for _ in range(nblocks):
                (bs,) = struct.unpack_from("<I", enc, o); o += 4
                blocks.append((cur, bs)); cur += bs
        return PakEntry(offset, size, usize, cmi, encrypted, blocks, bsize)

    # -- reading ---------------------------------------------------------
    def __contains__(self, path): return path in self.entries
    def __len__(self): return len(self.entries)
    def files(self): return self.entries.keys()

    def read(self, path: str) -> bytes:
        e = self.entries.get(path)
        if e is None:
            raise KeyError(path)
        self._f.seek(e.offset)
        head = self._f.read(4096)
        hs = ENTRY_HEADER_BASE
        blocks = None
        if e.is_compressed:
            (nb,) = struct.unpack_from("<i", head, hs); hs += 4
            need = hs + 16 * nb + 5
            if need > len(head):
                self._f.seek(e.offset)
                head = self._f.read(need)
            blocks = [struct.unpack_from("<qq", head, hs + 16 * i) for i in range(nb)]
            hs += 16 * nb
        hs += 1  # Flags
        (cbs,) = struct.unpack_from("<I", head, hs); hs += 4

        if not e.is_compressed:
            self._f.seek(e.offset + hs)
            return self._f.read(e.size)

        out = bytearray()
        remaining = e.uncompressed_size
        for (bs, be) in blocks:
            self._f.seek(e.offset + bs)
            comp = self._f.read(be - bs)
            n = min(cbs, remaining) if cbs else remaining
            out += oodle.decompress(comp, n)
            remaining -= n
        if len(out) != e.uncompressed_size:
            raise PakError(f"{path}: decompressed {len(out)}, expected {e.uncompressed_size}")
        return bytes(out)


def build_pak(entries, mount: str = DEFAULT_MOUNT) -> bytes:
    """Build a mod pak: uncompressed entries, unencrypted index.

    entries: iterable of (path_relative_to_mount, bytes)
    """
    entries = list(entries)
    body = bytearray()
    offsets = {}
    for path, data in entries:
        offsets[path] = len(body)
        body += struct.pack("<qqqi", 0, len(data), len(data), 0)
        body += hashlib.sha1(data).digest()
        body += struct.pack("<BI", 0, 0)
        body += data

    enc = bytearray()
    enc_off = {}
    for path, data in entries:
        enc_off[path] = len(enc)
        if offsets[path] > 0xFFFFFFFF or len(data) > 0xFFFFFFFF:
            raise PakError("mod pak entry exceeds 4 GiB; 64-bit encoding not implemented")
        enc += struct.pack("<III", (1 << 31) | (1 << 30), offsets[path], len(data))

    dirs = {}
    for path, _ in entries:
        d, _, f = path.rpartition("/")
        dirs.setdefault(d + "/", []).append(f)
    fd = bytearray(struct.pack("<i", len(dirs)))
    for d, files in dirs.items():
        fd += pack_fstring(d) + struct.pack("<i", len(files))
        for f in files:
            fd += pack_fstring(f) + struct.pack("<I", enc_off[d + f])
    fd = bytes(fd)

    def primary(fd_off: int) -> bytes:
        p = bytearray()
        p += pack_fstring(mount)
        p += struct.pack("<i", len(entries))
        p += struct.pack("<Q", 0)      # PathHashSeed (unused; no path hash index)
        p += struct.pack("<i", 0)      # bHasPathHashIndex
        p += struct.pack("<i", 1)      # bHasFullDirectoryIndex
        p += struct.pack("<qq", fd_off, len(fd))
        p += hashlib.sha1(fd).digest()
        p += struct.pack("<i", len(enc)) + bytes(enc)
        p += struct.pack("<i", 0)      # NumFiles with non-encodable entries
        return bytes(p)

    idx_off = len(body)
    size = len(primary(0))
    prim = primary(idx_off + size)
    assert len(prim) == size

    out = bytearray(body) + prim + fd
    footer = b"\0" * 16 + b"\0" + struct.pack("<I", PAK_MAGIC) + struct.pack("<i", 11)
    footer += struct.pack("<qq", idx_off, len(prim))
    footer += hashlib.sha1(prim).digest()
    footer += b"\0" * (32 * 5)
    assert len(footer) == FOOTER_SIZE
    return bytes(out + footer)
