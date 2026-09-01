#!/usr/bin/env python3
"""Build the two native pieces the tools need, on Linux or Windows.

There are only two, and neither depends on anything but a C or C++ compiler:

``libooz``
    The Oodle Kraken decoder, vendored from powzix/ooz. The sources are kept
    pristine, so two edits are applied here as the upstream ones assume MSVC:
    the Windows-only tail of ``kraken.cpp`` is dropped, and ``stdafx.h`` is
    swapped for the shim that provides the MSVC builtins under GCC.

``aes_finder``
    The pak key scanner, which carries its own AES and so needs no crypto
    library.

This exists instead of a Makefile so a Windows build needs no ``make``. Output
goes to ``native/``, which is the directory the packaged app looks in and the
one the release workflow bundles.
"""
from __future__ import annotations
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OOZ = ROOT / "third_party" / "ooz"
FINDER = ROOT / "tools" / "aes_finder"
OUT = ROOT / "native"

IS_WINDOWS = os.name == "nt"
LIB_NAME = "libooz.dll" if IS_WINDOWS else "libooz.so"
FINDER_NAME = "aes_finder.exe" if IS_WINDOWS else "aes_finder"


def compiler(kind: str) -> str:
    """Pick a compiler, honouring the usual environment variables.

    Args:
        kind (str): Either ``cc`` or ``cxx``.

    Returns:
        str: The command to invoke.

    Raises:
        SystemExit: If no compiler can be found on PATH.
    """
    env = os.environ.get("CXX" if kind == "cxx" else "CC")
    if env:
        return env
    for name in (["g++", "clang++"] if kind == "cxx" else ["gcc", "clang", "cc"]):
        if shutil.which(name):
            return name
    sys.exit(f"no {kind} compiler found on PATH")


def prepare_ooz_sources() -> list:
    """Rewrite the vendored sources into forms that build outside MSVC.

    Returns:
        list[Path]: The generated translation units.
    """
    generated = []
    for stem in ("kraken", "lzna", "bitknit"):
        src = (OOZ / f"{stem}.cpp").read_text(errors="replace")
        if stem == "kraken":
            # Cut at the first Windows-only declaration rather than a line
            # number, so the trim survives an upstream refresh.
            marker = "typedef int WINAPI OodLZ_CompressFunc("
            at = src.find(marker)
            if at >= 0:
                src = src[:at]
        src = src.replace('#include "stdafx.h"', '#include "ooz_linux.h"')
        out = OOZ / f"{stem}_lib.cpp"
        out.write_text(src)
        generated.append(out)
    return generated


def build_ooz() -> Path:
    """Compile the Kraken decoder into a shared library.

    Returns:
        Path: The library that was written.
    """
    sources = prepare_ooz_sources() + [OOZ / "ooz_export.cpp"]
    out = OUT / LIB_NAME
    cmd = [compiler("cxx"), "-O2", "-msse4.1", "-w", "-shared",
           "-o", str(out)] + [str(s) for s in sources]
    if not IS_WINDOWS:
        cmd.insert(1, "-fPIC")
    else:
        # A MinGW DLL should not depend on the compiler's runtime DLLs, or the
        # packaged build would have to carry them too.
        cmd += ["-static-libgcc", "-static-libstdc++"]
    print(" ".join(cmd))
    subprocess.run(cmd, check=True)
    return out


def build_finder() -> Path:
    """Compile the key scanner.

    Returns:
        Path: The executable that was written.
    """
    out = OUT / FINDER_NAME
    cmd = [compiler("cc"), "-O2", "-o", str(out), str(FINDER / "aes_finder.c")]
    if IS_WINDOWS:
        cmd += ["-lpsapi", "-static"]
    print(" ".join(cmd))
    subprocess.run(cmd, check=True)
    return out


def main():
    """Build both components into ``native/``.

    Returns:
        None
    """
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", choices=["ooz", "finder"],
                    help="build just one component")
    args = ap.parse_args()

    OUT.mkdir(exist_ok=True)
    built = []
    if args.only != "finder":
        built.append(build_ooz())
    if args.only != "ooz":
        built.append(build_finder())
    for p in built:
        print(f"  {p.relative_to(ROOT)}  {p.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
