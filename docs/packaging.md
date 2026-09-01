# Packaging

The tools are also shipped as a download that runs on its own, so that using
them needs no Python, no compiler and no setup. Releases are built by
[`.github/workflows/release.yml`](../.github/workflows/release.yml) and appear
on the repository's Releases page.

## What has to be in the box

Three things are needed at runtime and none of them are Python:

| file | why |
|---|---|
| `schema/usmap.json` | the property schema, without which values cannot be named or placed |
| `libooz.so` / `libooz.dll` | the Kraken decoder, without which nothing in the archive can be read |
| `aes_finder` | the key scanner, needed once per game version |

`cqmod/resources.py` finds all three, looking inside the bundle when frozen and
in the checkout otherwise, so the rest of the code does not care which it is.

## Building

```bash
pip install -r requirements-dev.txt
python tools/build_native.py          # writes native/
pyinstaller --noconfirm packaging/cqmod.spec
```

`tools/build_native.py` replaces the Makefile for this purpose, because a
Windows build should not need `make`. It applies the same two edits to the
vendored ooz sources that the Makefile does: the Windows-only tail of
`kraken.cpp` is dropped, and `stdafx.h` is swapped for the shim that supplies
the MSVC builtins under GCC.

## Two changes the packaging needed

**A stable symbol for the decoder.** Upstream ooz declares `Kraken_Decompress`
as a plain C++ function, so the exported symbol is a mangled name that encodes
the `size_t` width. That differs between 64-bit Linux and 64-bit MinGW, and
again under MSVC, so a prebuilt library could not be loaded by name.
`third_party/ooz/ooz_export.cpp` adds one `extern "C"` wrapper,
`ooz_kraken_decompress`, which every platform can look up the same way. The
loader still falls back to the mangled name for a library built before this
existed.

**Settings that outlive the run.** A frozen build unpacks itself into a
temporary directory that is deleted on exit, so `cqmod_config.local.json` is
written beside the executable instead. From a checkout that is still the
repository root, so nothing changes there.

## Size

A build is around 213 MB unpacked, most of it Qt and numpy.

Excluding a Qt module by import name is not enough, because PySide6's
PyInstaller hook adds every Qt library and plugin as a *binary* rather than an
import. Those survive the excludes and are filtered out of the collected lists
in the spec instead. Dropping Qt Quick, QML, PDF and the virtual keyboard,
which a widgets application never loads, takes about 27 MB off.

Matching is on the file's own name with any `lib` prefix removed, since the
same module is `Qt6Quick.dll` on Windows and `libQt6Quick.so.6` on Linux, and
anchored at the start: testing the whole path for a substring would drop
anything that happened to sit in a folder with an unlucky name. An over-eager
filter does not fail the build, it produces a bundle that packages cleanly and
then cannot start, which is why the audit above exists.

numpy stays. It is only used by `cqmod/dxt.py`, but that is real vectorised
work on block-compressed textures, and replacing it would be both a rewrite and
much slower.

## PySide6 is capped below 6.10

From 6.10 the Windows wheels ship a `Qt6Core.dll` that imports `icuuc.dll`
without shipping it, expecting the copy Windows has provided since version
1903. The Linux wheels bundle their own `libicuuc.so.73`, so only Windows is
affected, and only away from a current Windows: on Wine or an older build the
packaged application dies at startup with

    ImportError: DLL load failed while importing QtCore: Module not found.

which names the extension that failed to load rather than the library that was
missing. Checking the wheels directly shows where it changed:

| PySide6 | Windows `Qt6Core.dll` needs host ICU |
|---|---|
| 6.11.2 | yes |
| 6.10.1 | yes |
| 6.9.2 | no |
| 6.8.3 | no |
| 6.7.3 | no |

`requirements.txt` therefore pins `PySide6<6.10`, which keeps the bundle
self-contained.

## Checking the bundle

`tools/check_bundle.py` walks the import table of every DLL and extension in a
packaged build and reports anything neither present alongside it nor part of
Windows. The release workflow runs it and fails the build on a bad result, so
a missing dependency is caught in CI rather than on a user's machine.

It separates two cases, because they need different answers:

- **unresolved imports**, which mean something was dropped that should not have
  been. Trimming Qt found this: removing `Qt6Qml` and `Qt6Quick` while keeping
  `Qt6VirtualKeyboard`, which loads them, left the bundle referring to
  libraries it no longer shipped.
- **host dependencies**, the ICU case above, where the file is genuinely
  absent from the bundle by design and the build only works where the host
  happens to supply it.

## Releasing

Push a tag:

```bash
git tag v0.1.0
git push origin v0.1.0
```

The workflow builds on `ubuntu-latest` and `windows-latest`, since PyInstaller
bundles the running interpreter and cannot cross-compile, then attaches both
archives to a release. Every other push builds the same way without publishing,
so packaging breakage shows up before a tag rather than during one.

The Linux job also runs the built application under `QT_QPA_PLATFORM=offscreen`
and treats a timeout as success: a GUI that starts correctly is still running
when the clock runs out, and one that fails to start is not.
