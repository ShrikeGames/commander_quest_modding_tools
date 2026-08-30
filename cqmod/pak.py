"""Reading and writing Unreal Engine ``.pak`` archives (version 11).

Reading handles Commander Quest's shipping archive: an AES-256-ECB encrypted
index over Oodle Kraken compressed entries. Writing produces mod paks with an
*unencrypted* index and *uncompressed* entries.

That asymmetry is deliberate and is what makes modding practical: UE accepts
both forms, so building a mod needs neither the encryption key nor an Oodle
compressor. Decryption and Oodle are only ever needed to read the original.

An archive is laid out as a run of entries, then the primary index, then the
full directory index, then a fixed 221-byte footer holding the magic, version,
index location and index hash. See ``docs/formats/pak.md``.
"""
from __future__ import annotations
import hashlib, struct
from dataclasses import dataclass
from pathlib import Path

from . import oodle

PAK_MAGIC = 0x5A6F12E1
"""Magic in the footer, located by search since the footer is fixed-size."""

FOOTER_SIZE = 221
"""guid(16) + encrypted(1) + magic(4) + version(4) + offset(8) + size(8) + hash(20) + method names(160)."""

ENTRY_HEADER_BASE = 48
"""Offset(8) + Size(8) + UncompressedSize(8) + CompressionMethodIndex(4) + Hash(20)."""

DEFAULT_MOUNT = "../../../"
"""Mount point the game's own pak uses; paths are stored relative to it."""


class PakError(RuntimeError):
    """Raised for malformed archives, hash mismatches, or a wrong AES key."""


@dataclass
class PakEntry:
    """Location and encoding of one file inside an archive.

    Attributes:
        offset (int): Absolute offset of the entry's inline header.
        size (int): Stored size, i.e. compressed size when compressed.
        uncompressed_size (int): Size after decompression.
        compression (int): 0 for stored, otherwise a 1-based index into the
            archive's compression method names.
        encrypted (bool): Whether this entry's data is individually encrypted.
            Only 71 of 27,932 entries in the game pak are.
        blocks (list[tuple[int, int]]): ``(start, end)`` of each compressed
            block, relative to the entry's own start.
        block_size (int): Uncompressed size each block decodes to.
    """

    offset: int
    size: int
    uncompressed_size: int
    compression: int
    encrypted: bool
    blocks: list
    block_size: int

    @property
    def is_compressed(self) -> bool:
        """Whether the entry needs decompression.

        Returns:
            bool: True unless stored verbatim.
        """
        return self.compression != 0


def _fstring(buf: bytes, off: int):
    """Read an ``FString``.

    Args:
        buf (bytes): Buffer to read from.
        off (int): Offset of the length prefix.

    Returns:
        tuple[str, int]: The string, and the offset just past it. Negative
        lengths denote UTF-16LE.
    """
    (n,) = struct.unpack_from("<i", buf, off)
    off += 4
    if n == 0:
        return "", off
    if n < 0:
        s = buf[off:off - n * 2].decode("utf-16-le").rstrip("\0")
        return s, off - n * 2
    return buf[off:off + n - 1].decode("latin-1"), off + n


def pack_fstring(s: str) -> bytes:
    """Serialize an ``FString`` the way UE expects to read it back.

    ASCII stays single-byte; anything else must be UTF-16, because UE reads a
    positive length as ANSI rather than UTF-8.

    Args:
        s (str): Text to encode.

    Returns:
        bytes: Length prefix followed by null-terminated character data.
    """
    if all(ord(c) < 128 for c in s):
        b = s.encode("ascii") + b"\0"
        return struct.pack("<i", len(b)) + b
    b = s.encode("utf-16-le") + b"\0\0"
    return struct.pack("<i", -(len(b) // 2)) + b


class PakReader:
    """Random-access reader for a ``.pak`` archive.

    Opens the file, decrypts and validates the index, and exposes entries by
    their mount-relative path. Both the primary and directory indexes are
    checked against the SHA-1 hashes stored in the archive, so a wrong key is
    reported immediately rather than producing garbage.

    Example:
        >>> with PakReader(config.pak_path(), config.aes_key()) as pak:
        ...     data = pak.read("Commander/Content/Data/Cards/DT_Cards.uasset")

    Attributes:
        path (Path): The archive's location.
        file_size (int): Size in bytes.
        entries (dict[str, PakEntry]): Every file, keyed by mount-relative path.
        version (int): Pak format version.
        encrypted_index (bool): Whether the index required decryption.
        mount_point (str): Prefix paths are relative to.
        compression_methods (list[str]): Method names, index 1 upward.
    """

    def __init__(self, path, aes_key: bytes | None = None):
        """Open an archive and read its index.

        Args:
            path (str | Path): Archive to open.
            aes_key (bytes | None): 32-byte AES-256 key. Required only if the
                index is encrypted; mod paks written by :func:`build_pak` are not.

        Raises:
            PakError: If the file is not a pak, or a hash check fails, which
                most often means the key is wrong.
            OSError: If the file cannot be opened.
        """
        self.path = Path(path)
        self._aes_key = aes_key
        self._f = open(self.path, "rb")
        self._f.seek(0, 2)
        self.file_size = self._f.tell()
        self.entries: dict[str, PakEntry] = {}
        self._read_index()

    def close(self):
        """Close the underlying file handle."""
        self._f.close()

    def __enter__(self):
        """Enter a context manager.

        Returns:
            PakReader: This reader.
        """
        return self

    def __exit__(self, *a):
        """Close the archive on leaving a context manager.

        Args:
            *a: Standard exception triple, ignored.
        """
        self.close()

    def _decrypt(self, data: bytes) -> bytes:
        """Decrypt an index block with AES-256-ECB.

        Args:
            data (bytes): Ciphertext, a multiple of 16 bytes.

        Returns:
            bytes: The plaintext.

        Raises:
            PakError: If no key was supplied.
        """
        if self._aes_key is None:
            raise PakError("pak index is encrypted but no AES key was supplied")
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        d = Cipher(algorithms.AES(self._aes_key), modes.ECB()).decryptor()
        return d.update(data) + d.finalize()

    def _read_index(self):
        """Read, decrypt and validate the archive index into :attr:`entries`.

        Raises:
            PakError: If the magic is missing, a SHA-1 check fails, the archive
                has no full directory index, or the entry count disagrees with
                the directory listing.
        """
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
        o += 8
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
        """Decode one entry from the packed entry table.

        Entries use a compact bitfield: sizes are stored as 32-bit when they fit,
        and block layout is elided when it can be inferred.

        Args:
            enc (bytes): The encoded entry table.
            off (int): Offset of this entry's record.

        Returns:
            PakEntry: The decoded entry.
        """
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

    def __contains__(self, path):
        """Test whether a path exists in the archive.

        Args:
            path (str): Mount-relative path.

        Returns:
            bool: True if present.
        """
        return path in self.entries

    def __len__(self):
        """Count files in the archive.

        Returns:
            int: Number of entries.
        """
        return len(self.entries)

    def files(self):
        """List every path in the archive.

        Returns:
            KeysView[str]: Mount-relative paths.
        """
        return self.entries.keys()

    def read(self, path: str) -> bytes:
        """Read and decompress one file.

        The inline entry header is re-read here rather than trusted from the
        index, since it carries the authoritative block layout.

        Args:
            path (str): Mount-relative path, e.g.
                ``Commander/Content/Data/Cards/DT_Cards.uasset``.

        Returns:
            bytes: The file's decompressed contents.

        Raises:
            KeyError: If the path is not in the archive.
            PakError: If the decompressed size does not match what was recorded.
            cqmod.oodle.OodleError: If a compressed block fails to decode.
        """
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
        hs += 1
        (cbs,) = struct.unpack_from("<I", head, hs); hs += 4

        if not e.is_compressed:
            self._f.seek(e.offset + hs)
            if e.encrypted:
                # Encrypted data is stored padded to the AES block size, so the
                # padding is read, decrypted, then discarded.
                padded = (e.size + 15) & ~15
                return self._decrypt(self._f.read(padded))[:e.size]
            return self._f.read(e.size)

        out = bytearray()
        remaining = e.uncompressed_size
        for (bs, be) in blocks:
            self._f.seek(e.offset + bs)
            raw_len = be - bs
            if e.encrypted:
                # Encrypted blocks are stored padded to the AES block size.
                # Decrypt the padded span, then hand the decoder only the real
                # compressed length: trailing padding makes it fail outright.
                comp = self._decrypt(self._f.read((raw_len + 15) & ~15))[:raw_len]
            else:
                comp = self._f.read(raw_len)
            n = min(cbs, remaining) if cbs else remaining
            out += oodle.decompress(comp, n)
            remaining -= n
        if len(out) != e.uncompressed_size:
            raise PakError(f"{path}: decompressed {len(out)}, expected {e.uncompressed_size}")
        return bytes(out)


def build_pak(entries, mount: str = DEFAULT_MOUNT) -> bytes:
    """Build a mod pak from in-memory files.

    Entries are stored uncompressed and the index is left unencrypted, both of
    which UE accepts. Name a mod pak ``ZZZ_<something>_P.pak`` and drop it in the
    game's ``Content/Paks``: the ``_P`` suffix marks it as a patch so it mounts
    above the base archive, and the prefix keeps it sorting last.

    Args:
        entries (Iterable[tuple[str, bytes]]): ``(mount-relative path, contents)``
            pairs. Paths use forward slashes and no leading slash, e.g.
            ``Commander/Content/Data/Cards/X.uexp``.
        mount (str): Mount point to record. Leave at :data:`DEFAULT_MOUNT` to
            match the game's own archive.

    Returns:
        bytes: A complete ``.pak`` file.

    Raises:
        PakError: If an entry exceeds 4 GiB, which would need the 64-bit entry
            encoding this writer does not emit.
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
        """Serialize the primary index.

        Args:
            fd_off (int): Absolute offset the directory index will be written at.

        Returns:
            bytes: The primary index. Its length does not depend on ``fd_off``,
            so it can be built once to measure and again with the real offset.
        """
        p = bytearray()
        p += pack_fstring(mount)
        p += struct.pack("<i", len(entries))
        p += struct.pack("<Q", 0)
        p += struct.pack("<i", 0)
        p += struct.pack("<i", 1)
        p += struct.pack("<qq", fd_off, len(fd))
        p += hashlib.sha1(fd).digest()
        p += struct.pack("<i", len(enc)) + bytes(enc)
        p += struct.pack("<i", 0)
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
