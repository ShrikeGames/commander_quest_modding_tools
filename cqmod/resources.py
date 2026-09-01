"""Finding the files the tools ship with, from source or from a packaged build.

Running from a checkout, everything sits at a known place relative to this
file. In a packaged build there is no checkout: PyInstaller unpacks the bundle
into a temporary directory and points ``sys._MEIPASS`` at it, so data files have
to be looked up rather than assumed.

Three things are needed at runtime and none of them are Python:

``schema/usmap.json``
    The property schema, without which values cannot be named or placed.
``libooz``
    The Kraken decoder, needed to read anything out of the game's archive.
``aes_finder``
    The key scanner, needed once per game version to recover the pak key.

Packaged builds carry all three prebuilt, which is the point: a user never
needs a compiler, a copy of ``make``, or OpenSSL.
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def is_frozen() -> bool:
    """Report whether this is a packaged build rather than a checkout.

    Returns:
        bool: True when running from a PyInstaller bundle.
    """
    return getattr(sys, "frozen", False)


def bundle_dir() -> Path:
    """Locate the directory holding bundled data files.

    Returns:
        Path: ``sys._MEIPASS`` in a packaged build, otherwise the repository
        root, so callers can treat both the same way.
    """
    return Path(getattr(sys, "_MEIPASS", REPO_ROOT))


def app_dir() -> Path:
    """Locate the directory the user launched, for files that must persist.

    A bundle's own directory is temporary and is deleted when the program
    exits, so anything meant to outlive a run, such as the local config, has to
    go beside the executable instead.

    Returns:
        Path: The folder holding the executable, or the repository root when
        running from source.
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return REPO_ROOT


def usmap_path() -> Path:
    """Locate the property schema.

    Returns:
        Path: ``schema/usmap.json``, bundled or in the checkout.
    """
    return bundle_dir() / "schema" / "usmap.json"


def _native(stem: str, suffixes) -> Path | None:
    """Find a native file under the bundle or the checkout.

    Args:
        stem (str): Base name without extension.
        suffixes (Iterable[str]): Extensions to try, most specific first.

    Returns:
        Path | None: The first match, or None if nothing is present.
    """
    roots = [bundle_dir() / "native", bundle_dir(),
             REPO_ROOT / "third_party" / "ooz", REPO_ROOT / "tools" / "aes_finder"]
    for root in roots:
        for suffix in suffixes:
            p = root / f"{stem}{suffix}"
            if p.is_file():
                return p
    return None


def ooz_library() -> Path | None:
    """Locate the prebuilt Kraken decoder.

    Returns:
        Path | None: The shared library, or None if it has to be built first.
    """
    return _native("libooz", [".dll" if os.name == "nt" else ".so"])


def aes_finder() -> Path | None:
    """Locate the prebuilt key scanner.

    Returns:
        Path | None: The scanner executable, or None if it has to be built.
    """
    return _native("aes_finder", [".exe"] if os.name == "nt" else [""])
