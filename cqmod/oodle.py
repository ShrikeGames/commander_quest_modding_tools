"""Oodle Kraken decompression via the vendored ``ooz`` decoder.

Commander Quest's pak is Kraken-compressed and Oodle is statically linked into
the shipping binary, so reading assets needs a standalone decoder. The one in
``third_party/ooz`` is built on demand the first time this module decompresses
anything.

Only decompression is needed. Mod paks are written with uncompressed entries, so
there is no dependency on an Oodle *compressor* anywhere in these tools.
"""
from __future__ import annotations
import ctypes, subprocess
from pathlib import Path

_OOZ_DIR = Path(__file__).resolve().parent.parent / "third_party" / "ooz"
_LIB_PATH = _OOZ_DIR / "libooz.so"

_SAFE_SPACE = 512
"""Slack appended to output buffers; ooz may write a little past the end."""

_fn = None
"""Cached ``Kraken_Decompress`` function pointer, bound on first use."""


class OodleError(RuntimeError):
    """Raised when the decoder cannot be built, loaded, or produces bad output."""


def _ensure_built() -> Path:
    """Build ``libooz.so`` if it is not already present.

    Returns:
        Path: Location of the shared library.

    Raises:
        OodleError: If ``make`` is unavailable or the compile fails, quoting the
            compiler's own stderr.
    """
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
    """Bind the decoder entry point, building and loading the library if needed.

    Returns:
        ctypes._FuncPtr: ``int Kraken_Decompress(const byte*, size_t, byte*, size_t)``.
        The mangled C++ name is looked up directly because upstream declares no
        ``extern "C"`` interface.
    """
    global _fn
    if _fn is None:
        lib = ctypes.CDLL(str(_ensure_built()))
        fn = lib._Z17Kraken_DecompressPKhmPhm
        fn.restype = ctypes.c_int
        fn.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_size_t]
        _fn = fn
    return _fn


def decompress(src: bytes, uncompressed_size: int) -> bytes:
    """Decode a single Oodle-compressed block.

    Args:
        src (bytes): The compressed block, exactly as stored in the pak.
        uncompressed_size (int): Expected output size. The pak records this per
            block, so it is known ahead of time rather than guessed.

    Returns:
        bytes: The decoded block, exactly ``uncompressed_size`` long.

    Raises:
        OodleError: If the decoder returns a different length, which means the
            input was not valid Kraken data or was truncated.
    """
    fn = _load()
    out = ctypes.create_string_buffer(uncompressed_size + _SAFE_SPACE)
    n = fn(src, len(src), out, uncompressed_size)
    if n != uncompressed_size:
        raise OodleError(f"Oodle decode failed: got {n} bytes, expected {uncompressed_size}")
    return out.raw[:uncompressed_size]
