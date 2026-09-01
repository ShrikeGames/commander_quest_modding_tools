"""Oodle Kraken decompression via the vendored ``ooz`` decoder.

Commander Quest's pak is Kraken-compressed and Oodle is statically linked into
the shipping binary, so reading assets needs a standalone decoder. The one in
``third_party/ooz`` is built on demand the first time this module decompresses
anything.

Only decompression is needed. Mod paks are written with uncompressed entries, so
there is no dependency on an Oodle *compressor* anywhere in these tools.
"""
from __future__ import annotations
import ctypes, os, subprocess
from pathlib import Path

from . import resources

_OOZ_DIR = Path(__file__).resolve().parent.parent / "third_party" / "ooz"
_LIB_NAME = "libooz.dll" if os.name == "nt" else "libooz.so"
_LIB_PATH = _OOZ_DIR / _LIB_NAME

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
    prebuilt = resources.ooz_library()
    if prebuilt is not None:
        return prebuilt
    if resources.is_frozen():
        raise OodleError(
            "This build is missing its Oodle decoder, which should have been "
            "packaged with it. Please report this as a packaging bug.")
    try:
        subprocess.run(["make", "-C", str(_OOZ_DIR)], check=True,
                       capture_output=True, text=True)
    except FileNotFoundError as e:
        raise OodleError("'make' not found; cannot build third_party/ooz") from e
    except subprocess.CalledProcessError as e:
        raise OodleError(f"building {_LIB_NAME} failed:\n{e.stderr}") from e
    return _LIB_PATH


def _load():
    """Bind the decoder entry point, building and loading the library if needed.

    Returns:
        ctypes._FuncPtr: ``int ooz_kraken_decompress(const byte*, size_t,
        byte*, size_t)``, the wrapper in ``ooz_export.cpp``.
    """
    global _fn
    if _fn is None:
        lib = ctypes.CDLL(str(_ensure_built()))
        # Prefer the wrapper's unmangled name. A library built before that
        # existed only carries the C++ symbol, whose mangling encodes the
        # size_t width and so differs between platforms.
        try:
            fn = lib.ooz_kraken_decompress
        except AttributeError:
            try:
                fn = lib._Z17Kraken_DecompressPKhmPhm
            except AttributeError as e:
                raise OodleError(
                    "the Oodle library exports no known decompress entry "
                    "point; rebuild it with 'make -C third_party/ooz'") from e
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
