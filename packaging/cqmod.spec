# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for the Commander Quest modding tools.

Produces a self-contained folder holding the GUI, the Python runtime, Qt, and
the three non-Python files the tools need at runtime: the property schema, the
Oodle decoder and the key scanner. A user unzips it and runs it; nothing is
installed and no toolchain is needed.

The native pieces have to exist before this runs. Build them first with
``python tools/build_native.py``, which writes them into ``native/``.

Built with:
    pyinstaller packaging/cqmod.spec
"""
import os
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
NATIVE = ROOT / "native"
IS_WINDOWS = os.name == "nt"

datas = [(str(ROOT / "schema" / "usmap.json"), "schema")]
for pattern in ("libooz.*", "aes_finder*"):
    for found in NATIVE.glob(pattern):
        datas.append((str(found), "native"))

# Qt ships far more than a desktop tool uses, and each unused module is tens of
# megabytes in the download.
excludes = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngine",
    "PySide6.QtQuick", "PySide6.QtQml", "PySide6.Qt3DCore", "PySide6.Qt3DRender",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtBluetooth", "PySide6.QtNetworkAuth",
    "PySide6.QtPositioning", "PySide6.QtSerialPort", "PySide6.QtSql",
    "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp",
    "tkinter", "matplotlib", "scipy", "pandas", "pytest", "setuptools",
]

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=["cqmod", "ui.app"],
    excludes=excludes,
    noarchive=False,
)

# Excluding a Qt module by import name is not enough. PySide6's hook adds every
# Qt shared library and plugin as a binary, so the unused ones survive the
# excludes above and have to be dropped from the collected lists by hand. Qt
# Quick, QML and PDF alone are around a quarter of the download, and a widgets
# application links against none of them.
UNUSED_QT = (
    "Qt6Quick", "Qt6Qml", "Qt6QmlModels", "Qt6QmlWorkerScript", "Qt6Pdf",
    "Qt6Designer", "Qt6Help", "Qt6Test", "Qt6Sql", "Qt6Multimedia",
    "Qt6Charts", "Qt6DataVisualization", "Qt6Bluetooth", "Qt6NetworkAuth",
    "Qt6Positioning", "Qt6SerialPort", "Qt6WebEngine", "Qt63D",
    # Needs Qt Quick and QML, so it cannot stay once those are gone.
    "Qt6VirtualKeyboard",
)

# Plugins whose only purpose is to front one of the modules above. Removing a
# library and keeping the plugin that loads it leaves the bundle referring to
# something it no longer ships.
UNUSED_PLUGINS = ("qpdf.", "qtvirtualkeyboardplugin.", "qtvirtualkeyboard_")

UNUSED_DIRS = ("PySide6/Qt/qml/", "PySide6/qml/",
               "PySide6/Qt/translations/", "PySide6/translations/",
               "PySide6/Qt/plugins/sqldrivers/", "PySide6/plugins/sqldrivers/",
               "PySide6/Qt/plugins/multimedia/", "PySide6/plugins/multimedia/",
               "PySide6/Qt/plugins/virtualkeyboard/",
               "PySide6/plugins/virtualkeyboard/")


def wanted(entry):
    """Test whether a collected file is worth shipping.

    Matching is on the file's own name and anchored at its start. Testing the
    whole path for a substring would drop anything that merely sat in a folder
    with an unlucky name, and an over-eager filter here does not fail the
    build: it produces a bundle that packages cleanly and then cannot start.

    Args:
        entry (tuple): A PyInstaller table row, whose first item is the
            destination path.

    Returns:
        bool: False for Qt modules this application never loads.
    """
    dest = str(entry[0]).replace("\\", "/")
    name = dest.rsplit("/", 1)[-1]
    # The same Qt module is Qt6Quick.dll on Windows and libQt6Quick.so.6 on
    # Linux, so the lib prefix comes off before matching.
    stem = name[3:] if name.startswith("lib") else name
    if any(stem.startswith(part) for part in UNUSED_QT):
        return False
    if any(name.lower().startswith(part) for part in UNUSED_PLUGINS):
        return False
    return not any(d in dest for d in UNUSED_DIRS)


a.binaries = TOC([e for e in a.binaries if wanted(e)])
a.datas = TOC([e for e in a.datas if wanted(e)])

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CommanderQuestModTool",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="CommanderQuestModTool",
)
