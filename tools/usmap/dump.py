#!/usr/bin/env python3
"""Dump the running game's property schema to JSON.

Cooked packages omit property names and types, so the tools otherwise address
values by byte offset. This recovers the real schema from the engine's runtime
reflection data and writes it where cqmod can load it.

    1. launch Commander Quest
    2. sudo sysctl -w kernel.yama.ptrace_scope=0      (revert with =1)
    3. python3 tools/usmap/dump.py

The result is checked into place as ``schema/usmap.json`` and only needs
regenerating when the game updates.
"""
from __future__ import annotations
import argparse, json, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from reflection import Reflection, ReflectionError

PROC_NAME = "CommanderGame-Win64-Shipping"
DEFAULT_OUT = Path(__file__).resolve().parent.parent.parent / "schema" / "usmap.json"


def find_pid():
    """Locate the running game.

    Returns:
        int | None: Its PID, or None if it is not running.
    """
    try:
        out = subprocess.run(["pgrep", "-f", PROC_NAME],
                             capture_output=True, text=True).stdout
    except FileNotFoundError:
        return None
    for tok in out.split():
        pid = int(tok)
        try:
            if PROC_NAME.encode() in Path(f"/proc/{pid}/cmdline").read_bytes():
                return pid
        except OSError:
            continue
    return None


def _classes_used_by_assets():
    """Collect the classes the shipped data assets actually instantiate.

    Dumping every class in the engine produces a 13 MB file dominated by types
    no mod will ever touch, so the default output is narrowed to the classes
    that appear as exports in the game's own assets, plus their parents so that
    inherited properties still resolve.

    Returns:
        set[str] | None: Class names to keep, or None if the pak cannot be read.
    """
    try:
        from cqmod import config, catalog
        from cqmod.pak import PakReader
    except Exception:
        return None
    try:
        with PakReader(config.pak_path(), config.aes_key()) as pak:
            names = {e.class_name for a in catalog.build(pak) for e in a.exports}
            names |= {a.class_name for a in catalog.build(pak)}
    except Exception:
        return None
    return {n for n in names if n}


def main():
    """Walk every class in the running game and write the schema.

    Returns:
        None

    Raises:
        SystemExit: If the game is not running, memory cannot be read, or the
            engine structures cannot be located.
    """
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pid", type=int, help="game PID (default: auto-detect)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output JSON")
    ap.add_argument("--all", action="store_true",
                    help="dump every class, not just those the game's data uses")
    args = ap.parse_args()

    pid = args.pid or find_pid()
    if not pid:
        sys.exit(f"No running {PROC_NAME} found. Launch the game first.")

    try:
        r = Reflection(pid)
    except PermissionError:
        sys.exit("Cannot read process memory. Try:\n"
                 "    sudo sysctl -w kernel.yama.ptrace_scope=0")
    except ReflectionError as e:
        sys.exit(f"{e}\nThe game may still be loading; try again at the main menu.")

    print(f"pid {pid}: {len(r.blocks)} name blocks, Class at {r.class_class:#x}")
    t = time.time()
    addrs = r.classes()
    print(f"found {len(addrs)} classes in {time.time() - t:.1f}s")

    wanted = None
    if not args.all:
        wanted = _classes_used_by_assets()
        if wanted:
            print(f"restricting to {len(wanted)} classes used by the game's data assets")

    out = {}
    for a in addrs:
        try:
            k = r.read_class(a)
        except Exception:
            continue
        if not k.properties or k.name in out:
            continue
        if wanted is not None and k.name not in wanted:
            continue
        out[k.name] = {
            "super": k.super_name,
            "size": k.properties_size,
            "properties": [
                {"index": p.index, "name": p.name, "type": p.type,
                 "size": p.size, "owner": p.owner}
                for p in k.properties
            ],
        }
    r.close()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=1, sort_keys=True))
    props = sum(len(v["properties"]) for v in out.values())
    print(f"wrote {args.out}: {len(out)} classes, {props} properties, "
          f"{args.out.stat().st_size / 2**20:.1f} MB")


if __name__ == "__main__":
    main()
