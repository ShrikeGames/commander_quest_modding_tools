"""Recovering the pak index key from the running game.

Unreal bakes a pak's encryption key into the build at cook time, so every copy
of a given version shares one key and recovering it is a one-time step rather
than something to repeat. The key is assembled at runtime instead of stored, so
it cannot be read out of the shipped files and has to be taken from the live
process.

The key is deliberately never distributed with these tools. It belongs to a
commercial game, and shipping it would turn a toolkit that reads your own copy
into a way to decrypt someone else's. Recovering it locally also survives a
patch that rotates the key, which a bundled copy would not.

Work is split between a small native scanner and this module. The scanner tries
every window of process memory against the index's first ciphertext block and
prints the few that decrypt to a plausible ``FString`` length. This module then
proves a candidate by decrypting the whole index and comparing SHA-1 against the
hash in the pak footer, so a key that comes back is confirmed rather than
guessed.
"""
from __future__ import annotations
import ctypes
import hashlib
import os
import struct
import subprocess
import sys
from pathlib import Path

from . import resources

PROC_NAME = "CommanderGame-Win64-Shipping"
"""The shipping binary's name, the same under Proton as on Windows."""

PAK_MAGIC = 0x5A6F12E1
"""Marker that locates the footer inside the last 221 bytes of a pak."""

FOOTER_SIZE = 221


class KeyFinderError(RuntimeError):
    """Raised when the key cannot be recovered, explaining what to do next."""


def find_game_pid() -> int | None:
    """Locate the running game process.

    Returns:
        int | None: The process id, or None if the game is not running.
    """
    if os.name == "nt":
        return _find_pid_windows()
    return _find_pid_linux()


def _find_pid_linux() -> int | None:
    """Scan ``/proc`` for the shipping binary.

    Matching is on the full command line because under Proton the process name
    alone is not distinctive.

    Returns:
        int | None: The process id, or None.
    """
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if PROC_NAME.encode() in (entry / "cmdline").read_bytes():
                return int(entry.name)
        except OSError:
            continue
    return None


def _find_pid_windows() -> int | None:
    """Ask Windows for the process list and match on the executable name.

    Returns:
        int | None: The process id, or None.
    """
    psapi = ctypes.WinDLL("psapi")
    kernel32 = ctypes.WinDLL("kernel32")
    count = 4096
    pids = (ctypes.c_uint32 * count)()
    needed = ctypes.c_uint32()
    if not psapi.EnumProcesses(ctypes.byref(pids), ctypes.sizeof(pids),
                               ctypes.byref(needed)):
        return None
    # PROCESS_QUERY_LIMITED_INFORMATION is enough to read a name and, unlike
    # the fuller rights, is granted without elevation.
    for pid in pids[:needed.value // ctypes.sizeof(ctypes.c_uint32)]:
        if not pid:
            continue
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            continue
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.c_uint32(len(buf))
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf,
                                                   ctypes.byref(size)):
                if PROC_NAME.lower() in buf.value.lower():
                    return pid
        finally:
            kernel32.CloseHandle(handle)
    return None


def pak_index_info(pak: Path):
    """Read where a pak's index lives and how to recognise it.

    Args:
        pak (Path): The archive to inspect.

    Returns:
        tuple[int, int, bytes]: Index offset, index size, and the index SHA-1.
        The hash is what turns a candidate key into a proven one.

    Raises:
        KeyFinderError: If the file does not carry a pak footer.
    """
    with open(pak, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(size - FOOTER_SIZE)
        footer = f.read(FOOTER_SIZE)
    at = footer.find(struct.pack("<I", PAK_MAGIC))
    if at < 0:
        raise KeyFinderError(f"{pak.name} does not look like an Unreal .pak")
    offset, index_size = struct.unpack_from("<qq", footer, at + 8)
    return offset, index_size, footer[at + 24:at + 44]


def _read_index(pak: Path, offset: int, size: int) -> bytes:
    """Read the encrypted index out of a pak.

    Args:
        pak (Path): The archive.
        offset (int): Where the index starts.
        size (int): How long it is.

    Returns:
        bytes: The still-encrypted index.
    """
    with open(pak, "rb") as f:
        f.seek(offset)
        return f.read(size)


def confirms(key: bytes, index: bytes, want_sha1: bytes) -> bool:
    """Test whether a key really decrypts a pak index.

    Args:
        key (bytes): A 32-byte candidate.
        index (bytes): The encrypted index.
        want_sha1 (bytes): The hash the footer records for the plaintext.

    Returns:
        bool: True when the decrypted index hashes to the recorded value.
    """
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    plain = decryptor.update(index) + decryptor.finalize()
    return hashlib.sha1(plain).digest() == want_sha1


def find_key(pak: Path, pid: int = None, progress=None) -> str:
    """Recover and prove the pak's index key.

    Memory is swept twice. The first pass looks only at eight-byte aligned
    windows, which is where an allocated key almost always sits and covers a
    few gigabytes in about a minute. Only if that finds nothing does the second
    pass try every offset, which is thorough but several times slower.

    Args:
        pak (Path): The game archive whose key is wanted.
        pid (int | None): The game's process id, found automatically if omitted.
        progress (Callable[[str], None] | None): Called with status lines.

    Returns:
        str: The key as 64 uppercase hex digits.

    Raises:
        KeyFinderError: If the game is not running, its memory cannot be read,
            or no key is found. Each message names the fix.
    """
    say = progress or (lambda _: None)
    scanner = resources.aes_finder()
    if scanner is None:
        raise KeyFinderError(
            "The key scanner is missing. A packaged build ships it; from a "
            "checkout, build it with 'make -C tools/aes_finder'.")
    if pid is None:
        pid = find_game_pid()
    if not pid:
        raise KeyFinderError(
            "Commander Quest is not running. Launch the game, wait for the "
            "main menu, then try again.")

    offset, size, want = pak_index_info(pak)
    index = _read_index(pak, offset, size)
    block = index[:16].hex().upper()

    for step, label in ((8, "aligned"), (1, "exhaustive")):
        say(f"scanning process memory ({label} pass)...")
        key = _run_scanner(scanner, pid, block, step, index, want, say)
        if key:
            return key
        say(f"{label} pass found nothing")
    raise KeyFinderError(
        "No key found. Make sure the game is past the loading screen and on "
        "the main menu, then try again.")


def _run_scanner(scanner: Path, pid: int, block: str, step: int,
                 index: bytes, want: bytes, say) -> str | None:
    """Run one pass of the native scanner and confirm what it reports.

    Candidates are confirmed as they arrive and the scanner is stopped at the
    first proven key, so a scan usually ends well before it has read all of
    memory.

    Args:
        scanner (Path): The scanner executable.
        pid (int): Process to read.
        block (str): First 16 ciphertext bytes as hex.
        step (int): Window stride.
        index (bytes): The encrypted index, for confirmation.
        want (bytes): The SHA-1 the plaintext must have.
        say (Callable[[str], None]): Status callback.

    Returns:
        str | None: The proven key, or None if this pass found none.

    Raises:
        KeyFinderError: If process memory cannot be read at all.
    """
    proc = subprocess.Popen(
        [str(scanner), "--pid", str(pid), "--block", block, "--step", str(step)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    tried = 0
    try:
        for line in proc.stdout:
            if not line.startswith("CANDIDATE="):
                continue
            candidate = bytes.fromhex(line.split("=", 1)[1].split()[0])
            tried += 1
            if tried % 200 == 0:
                say(f"checked {tried:,} candidates...")
            if confirms(candidate, index, want):
                say(f"confirmed after {tried:,} candidates")
                return candidate.hex().upper()
    finally:
        if proc.poll() is None:
            proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    if proc.returncode == 3:
        raise KeyFinderError(_permission_hint())
    return None


def _permission_hint() -> str:
    """Explain how to grant permission to read the game's memory.

    Returns:
        str: A message naming the platform's actual fix.
    """
    if os.name == "nt":
        return ("Cannot read the game's memory. Close the tool and start it "
                "again as administrator.")
    return ("Cannot read the game's memory. Either run this once:\n"
            "    sudo sysctl -w kernel.yama.ptrace_scope=0\n"
            "or start the tool with sudo.")
