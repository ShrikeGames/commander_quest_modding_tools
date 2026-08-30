"""Locating the game installation and its pak encryption key.

The AES key is deliberately not committed to the repository. It is specific to a
build of a commercial game, so it lives in a gitignored local config file (or an
environment variable), and ``tools/find_aes_key.py`` can recover it from your own
installed copy.

Settings are resolved in this order, first match winning:

1. environment variable (``CQMOD_GAME_DIR``, ``CQMOD_AES_KEY``)
2. ``cqmod_config.local.json`` beside the repository root
3. a built-in default, where one makes sense
"""
from __future__ import annotations
import json, os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCAL_CONFIG = REPO_ROOT / "cqmod_config.local.json"

DEFAULT_GAME_DIR = REPO_ROOT.parent
PAK_RELPATH = "Commander/Content/Paks/Commander-Windows.pak"


class ConfigError(RuntimeError):
    """Raised when the game or its encryption key cannot be located.

    The message is written for a human reading a terminal: it names the file to
    edit and the command to run, rather than only stating what was missing.
    """


def _load_local() -> dict:
    """Read the gitignored local config file.

    Returns:
        dict: Parsed settings, or an empty dict if the file does not exist.

    Raises:
        ConfigError: If the file exists but is not valid JSON.
    """
    if LOCAL_CONFIG.is_file():
        try:
            return json.loads(LOCAL_CONFIG.read_text())
        except json.JSONDecodeError as e:
            raise ConfigError(f"{LOCAL_CONFIG} is not valid JSON: {e}") from e
    return {}


def game_dir() -> Path:
    """Locate the Commander Quest installation directory.

    Returns:
        Path: The configured game directory, defaulting to the repository's
        parent (the layout you get by cloning into the game folder). The path is
        not checked for existence; use :func:`pak_path` for that.
    """
    cfg = _load_local()
    p = os.environ.get("CQMOD_GAME_DIR") or cfg.get("game_dir")
    return Path(p) if p else DEFAULT_GAME_DIR


def pak_path() -> Path:
    """Locate the game's main content archive.

    Returns:
        Path: Absolute path to ``Commander-Windows.pak``.

    Raises:
        ConfigError: If no pak exists there, with instructions for pointing the
            tools at the right directory.
    """
    p = game_dir() / PAK_RELPATH
    if not p.is_file():
        raise ConfigError(
            f"Game pak not found at {p}.\n"
            f"Set 'game_dir' in {LOCAL_CONFIG.name} or the CQMOD_GAME_DIR env var."
        )
    return p


def paks_dir() -> Path:
    """Locate the folder mod paks are installed into.

    Returns:
        Path: The game's ``Content/Paks`` directory. Any ``*_P.pak`` placed here
        is mounted at higher priority than the base archive.
    """
    return game_dir() / "Commander/Content/Paks"


def aes_key() -> bytes:
    """Load the pak index decryption key.

    Accepts hex with or without a ``0x`` prefix, in any case.

    Returns:
        bytes: The 32-byte AES-256 key.

    Raises:
        ConfigError: If no key is configured, the value is not hex, or it is not
            exactly 32 bytes. The message names the recovery tool.
    """
    cfg = _load_local()
    raw = os.environ.get("CQMOD_AES_KEY") or cfg.get("aes_key")
    if not raw:
        raise ConfigError(
            "No pak AES key configured.\n"
            f"Add 'aes_key' to {LOCAL_CONFIG.name}, set CQMOD_AES_KEY, or run:\n"
            "    python3 tools/find_aes_key.py --save"
        )
    raw = raw.strip().removeprefix("0x").removeprefix("0X")
    try:
        key = bytes.fromhex(raw)
    except ValueError as e:
        raise ConfigError(f"aes_key is not valid hex: {e}") from e
    if len(key) != 32:
        raise ConfigError(f"aes_key must be 32 bytes (64 hex chars), got {len(key)}")
    return key


def save_local(**kv) -> None:
    """Merge settings into the local config file, creating it if needed.

    Args:
        **kv: Settings to write, e.g. ``aes_key="D6F5..."`` or
            ``game_dir="/path/to/game"``. Existing unrelated keys are preserved.
    """
    cfg = _load_local()
    cfg.update(kv)
    LOCAL_CONFIG.write_text(json.dumps(cfg, indent=2) + "\n")
