"""Oodle Kraken decompression via the vendored ooz decoder.

Only decompression is ever needed: mod paks are written with uncompressed
entries, so there is no Oodle *compressor* dependency.
"""
from __future__ import annotations
import ctypes, subprocess
from pathlib import Path

_OOZ_DIR = Path(__file__).resolve().parent.parent / "third_party" / "ooz"
_LIB_PATH = _OOZ_DIR / "libooz.so"
_SAFE_SPACE = 512  # ooz may write past the end of the output buffer

_fn = None


class OodleError(RuntimeError):
    pass


def _ensure_built() -> Path:
    if not _LIB_PATH.is_file():
        try:
            subprocess.run(["make", "-C", str(_OOZ_DIR)], check=True,
                           capture_output=True, text=True)
        except FileNotFoundError as e:
            raise OodleError("'make' not found; cannot build third_party/ooz") from e
        except subprocess.CalledProcessError as e:
            raise OodleError(f"building libooz.so failed:\n{e.stderr}") from e
    return _LIB_PATH


def _load():
    global _fn
    if _fn is None:
        lib = ctypes.CDLL(str(_ensure_built()))
        fn = lib._Z17Kraken_DecompressPKhmPhm  # int Kraken_Decompress(const byte*, size_t, byte*, size_t)
        fn.restype = ctypes.c_int
        fn.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_size_t]
        _fn = fn
    return _fn


def decompress(src: bytes, uncompressed_size: int) -> bytes:
    """Decode one Oodle block. Raises if the decoder does not produce exactly
    `uncompressed_size` bytes, which is the pak's own recorded size."""
    fn = _load()
    out = ctypes.create_string_buffer(uncompressed_size + _SAFE_SPACE)
    n = fn(src, len(src), out, uncompressed_size)
    if n != uncompressed_size:
        raise OodleError(f"Oodle decode failed: got {n} bytes, expected {uncompressed_size}")
    return out.raw[:uncompressed_size]
