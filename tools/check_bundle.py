#!/usr/bin/env python3
"""Check that a packaged Windows build carries every DLL it imports.

Compiling and packaging cleanly says nothing about whether the result starts.
A PyInstaller bundle that is missing one dependency fails at launch with
"DLL load failed while importing QtCore: Module not found", which names the
extension that failed and not the library that was actually absent.

This walks the import table of every extension and DLL in the bundle and
reports anything that is neither present alongside it nor part of Windows. It
turns a failure a user would hit on their own machine into one CI catches.

    python tools/check_bundle.py dist/CommanderQuestModTool
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

# Libraries that ship with Windows, or with the compiler's redistributable, and
# so are resolved from the system rather than the bundle. The api-ms-win-* and
# ext-ms-* families are API sets, which are virtual names the loader maps to
# real system libraries.
SYSTEM_PREFIXES = ("api-ms-win-", "ext-ms-", "vcruntime", "msvcp", "msvcr",
                   "ucrtbase", "concrt")

SYSTEM_DLLS = {
    "kernel32.dll", "user32.dll", "gdi32.dll", "advapi32.dll", "shell32.dll",
    "ole32.dll", "oleaut32.dll", "comdlg32.dll", "comctl32.dll", "ws2_32.dll",
    "wsock32.dll", "winmm.dll", "version.dll", "psapi.dll", "shlwapi.dll",
    "crypt32.dll", "secur32.dll", "netapi32.dll", "userenv.dll", "imm32.dll",
    "dwmapi.dll", "uxtheme.dll", "setupapi.dll", "cfgmgr32.dll", "bcrypt.dll",
    "ntdll.dll", "rpcrt4.dll", "iphlpapi.dll", "dnsapi.dll", "mpr.dll",
    "winspool.drv", "opengl32.dll", "glu32.dll", "d3d9.dll", "d3d11.dll",
    "d3d12.dll", "dxgi.dll", "dxva2.dll", "mf.dll", "mfplat.dll",
    "mfreadwrite.dll", "authz.dll", "wtsapi32.dll", "powrprof.dll",
    "propsys.dll", "dcomp.dll", "windowscodecs.dll", "msimg32.dll",
    "normaliz.dll", "wldap32.dll", "usp10.dll", "oleacc.dll", "winhttp.dll",
    "bcryptprimitives.dll", "cryptbase.dll", "profapi.dll", "sechost.dll",
    "combase.dll", "kernelbase.dll", "msvcrt.dll", "dwrite.dll", "d2d1.dll",
    "uiautomationcore.dll", "ncrypt.dll", "imagehlp.dll", "d3dcompiler_47.dll",
    "avicap32.dll", "evr.dll", "ksuser.dll", "wintrust.dll",
}

# Libraries a recent Windows provides but which are not safe to rely on. Qt
# links ICU, and from PySide6 6.10 the Windows wheels import icuuc.dll without
# shipping it, expecting the copy Windows has carried since version 1903. The
# result starts on a current Windows and dies everywhere else, Wine and older
# Windows included, with "DLL load failed while importing QtCore". Depending on
# these is treated as a failure so a bundle stays self-contained.
HOST_PROVIDED = {"icuuc.dll", "icuin.dll", "icudt.dll", "icu.dll"}


def is_system(name: str) -> bool:
    """Test whether a DLL is expected to come from Windows itself.

    Args:
        name (str): The imported library's name.

    Returns:
        bool: True when it does not need to be in the bundle.
    """
    low = name.lower()
    return low in SYSTEM_DLLS or low.startswith(SYSTEM_PREFIXES)


def collect(root: Path) -> dict:
    """Index every binary the bundle ships, by lowercased file name.

    Args:
        root (Path): The packaged application folder.

    Returns:
        dict[str, pathlib.Path]: File name to its location.
    """
    found = {}
    for p in root.rglob("*"):
        if p.suffix.lower() in (".dll", ".pyd", ".exe") and p.is_file():
            found.setdefault(p.name.lower(), p)
    return found


def imports_of(path: Path) -> list:
    """Read the DLLs a binary imports.

    Args:
        path (Path): A PE file.

    Returns:
        list[str]: Imported library names, empty if the file has no import
        table or cannot be parsed.
    """
    import pefile

    try:
        pe = pefile.PE(str(path), fast_load=True)
        pe.parse_data_directories(
            directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]])
    except Exception:
        return []
    names = []
    for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []) or []:
        if entry.dll:
            names.append(entry.dll.decode(errors="replace"))
    pe.close()
    return names


def main():
    """Audit a bundle and report anything it cannot resolve.

    Returns:
        int: 0 when every dependency resolves, 1 otherwise.
    """
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("bundle", help="the packaged application folder")
    ap.add_argument("--focus", default="QtCore",
                    help="binary whose dependencies matter most, named in the "
                         "summary even when everything resolves")
    args = ap.parse_args()

    root = Path(args.bundle)
    if not root.is_dir():
        sys.exit(f"{root} is not a directory")

    present = collect(root)
    print(f"{len(present)} binaries in the bundle")

    missing = {}
    for name, path in sorted(present.items()):
        for dep in imports_of(path):
            if dep.lower() in present or is_system(dep):
                continue
            missing.setdefault(dep, []).append(path.relative_to(root).as_posix())

    focus = [n for n in present if args.focus.lower() in n]
    for name in focus:
        deps = imports_of(present[name])
        gone = [d for d in deps if d.lower() not in present and not is_system(d)]
        print(f"{name}: {len(deps)} imports, {len(gone)} unresolved")
        for d in deps:
            mark = "MISSING" if d in gone else "ok"
            print(f"    {mark:<8} {d}")

    host = {d: u for d, u in missing.items() if d.lower() in HOST_PROVIDED}
    unknown = {d: u for d, u in missing.items() if d.lower() not in HOST_PROVIDED}

    if host:
        print("\nDepends on libraries the host may not have:")
        for dep, users in sorted(host.items()):
            print(f"  {dep}")
            for u in users[:4]:
                print(f"      needed by {u}")
        print("  Windows has shipped these since version 1903, but Wine and "
              "older Windows have not, and the application will not start "
              "without them. PySide6 below 6.10 does not need them.")

    if unknown:
        print(f"\n{len(unknown)} unresolved imports:")
        for dep, users in sorted(unknown.items()):
            print(f"  {dep}")
            for u in users[:4]:
                print(f"      needed by {u}")
            if len(users) > 4:
                print(f"      and {len(users) - 4} more")

    if not missing:
        print("\nevery imported library is present or provided by Windows")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
