"""Locating the game and its pak encryption key.

The AES key is deliberately NOT committed. It is specific to a build of a
commercial game, so it lives in a gitignored local config (or an env var), and
`tools/find_aes_key.py` can recover it from your own installed copy.
"""
from __future__ import annotations
import json, os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCAL_CONFIG = REPO_ROOT / "cqmod_config.local.json"

DEFAULT_GAME_DIR = REPO_ROOT.parent
PAK_RELPATH = "Commander/Content/Paks/Commander-Windows.pak"


class ConfigError(RuntimeError):
    pass


def _load_local() -> dict:
    if LOCAL_CONFIG.is_file():
        try:
            return json.loads(LOCAL_CONFIG.read_text())
        except json.JSONDecodeError as e:
            raise ConfigError(f"{LOCAL_CONFIG} is not valid JSON: {e}") from e
    return {}


def game_dir() -> Path:
    cfg = _load_local()
    p = os.environ.get("CQMOD_GAME_DIR") or cfg.get("game_dir")
    return Path(p) if p else DEFAULT_GAME_DIR


def pak_path() -> Path:
    p = game_dir() / PAK_RELPATH
    if not p.is_file():
        raise ConfigError(
            f"Game pak not found at {p}.\n"
            f"Set 'game_dir' in {LOCAL_CONFIG.name} or the CQMOD_GAME_DIR env var."
        )
    return p


def paks_dir() -> Path:
    return game_dir() / "Commander/Content/Paks"


def aes_key() -> bytes:
    cfg = _load_local()
    raw = os.environ.get("CQMOD_AES_KEY") or cfg.get("aes_key")
    if not raw:
        raise ConfigError(
            "No pak AES key configured.\n"
            f"Add 'aes_key' to {LOCAL_CONFIG.name}, set CQMOD_AES_KEY, or run:\n"
            "    python3 tools/find_aes_key.py --launch-game-first"
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
    cfg = _load_local()
    cfg.update(kv)
    LOCAL_CONFIG.write_text(json.dumps(cfg, indent=2) + "\n")
