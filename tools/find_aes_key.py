#!/usr/bin/env python3
"""Recover the pak index AES key from the running game.

The key is assembled at runtime rather than stored, so scanning the shipped
binaries finds nothing -- it has to be read out of live process memory. Each
candidate is confirmed by decrypting the entire index and comparing SHA-1
against the pak footer's hash, so a reported key is proven, not guessed.

    1. launch Commander Quest
    2. python3 tools/find_aes_key.py --save
"""
from __future__ import annotations
import argparse, struct, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cqmod import config

PROC_NAME = "CommanderGame-Win64-Shipping"
FINDER_DIR = Path(__file__).resolve().parent / "aes_finder"


def find_pid():
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


def pak_index_info(pak: Path):
    with open(pak, "rb") as f:
        f.seek(0, 2); size = f.tell()
        f.seek(size - 221); foot = f.read(221)
        i = foot.find(struct.pack("<I", 0x5A6F12E1))
        if i < 0:
            sys.exit(f"{pak} does not look like a .pak")
        off, isz = struct.unpack_from("<qq", foot, i + 8)
        return off, isz, foot[i + 24:i + 44].hex()


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pid", type=int, help="game PID (default: auto-detect)")
    ap.add_argument("--save", action="store_true",
                    help=f"write the key into {config.LOCAL_CONFIG.name}")
    args = ap.parse_args()

    pid = args.pid or find_pid()
    if not pid:
        sys.exit(f"No running {PROC_NAME} found -- launch the game first.")

    finder = FINDER_DIR / "aes_finder"
    if not finder.is_file():
        print("building aes_finder...")
        try:
            subprocess.run(["make", "-C", str(FINDER_DIR)], check=True,
                           capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            sys.exit(f"build failed (is libssl-dev installed?):\n{e.stderr}")

    pak = config.pak_path()
    off, size, sha1 = pak_index_info(pak)
    print(f"scanning pid {pid} for the key to {pak.name} ...")
    r = subprocess.run([str(finder), str(pid), str(pak), hex(off), str(size), sha1],
                       capture_output=True, text=True)
    sys.stderr.write(r.stderr)
    if r.returncode == 3:
        sys.exit("Cannot read process memory. Try:\n"
                 "    sudo sysctl -w kernel.yama.ptrace_scope=0")
    if r.returncode != 0:
        sys.exit("Key not found. Make sure the game is past the loading screen.")

    key = next(l.split("=", 1)[1] for l in r.stdout.splitlines() if l.startswith("KEY="))
    print(f"\n{key}")
    if args.save:
        config.save_local(aes_key=key)
        print(f"saved to {config.LOCAL_CONFIG.name}")
    else:
        print("\n(re-run with --save to store it)")


if __name__ == "__main__":
    main()
