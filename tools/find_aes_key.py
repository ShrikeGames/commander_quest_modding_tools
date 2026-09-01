#!/usr/bin/env python3
"""Recover the pak index AES key from the running game.

Unreal bakes the key into a build, so every copy of a given version shares one
and this only has to be run once, or again after a patch that rotates it. The
key is assembled at runtime rather than stored, so it cannot be read out of the
shipped files and has to come from the live process.

Each candidate is proven by decrypting the whole index and comparing SHA-1
against the pak footer's hash, so a reported key is confirmed, not guessed.

    1. launch Commander Quest and wait for the main menu
    2. python3 tools/find_aes_key.py --save
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cqmod import config, keyfinder


def main():
    """Recover the key and optionally store it.

    Returns:
        None

    Raises:
        SystemExit: If the game is not running, its memory cannot be read, or
            no key is found. Each case exits with a message naming the fix.
    """
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pid", type=int, help="game PID (default: auto-detect)")
    ap.add_argument("--save", action="store_true",
                    help=f"write the key into {config.LOCAL_CONFIG.name}")
    args = ap.parse_args()

    try:
        key = keyfinder.find_key(config.pak_path(), args.pid,
                                 progress=lambda m: print(m, file=sys.stderr))
    except (keyfinder.KeyFinderError, config.ConfigError) as e:
        sys.exit(str(e))

    print(f"\n{key}")
    if args.save:
        config.save_local(aes_key=key)
        print(f"saved to {config.LOCAL_CONFIG.name}")
    else:
        print("\n(re-run with --save to store it)")


if __name__ == "__main__":
    main()
