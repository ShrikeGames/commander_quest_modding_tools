"""Entry point for the packaged application.

A packaged build has no checkout to run ``ui/app.py`` from, so this is the
script PyInstaller analyses and the one the built executable starts at. It does
nothing but hand over to the GUI, which keeps everything about being packaged
out of the application itself.
"""
from __future__ import annotations
import multiprocessing
import sys


def main():
    """Start the GUI.

    Returns:
        None
    """
    # Without this a frozen build re-runs the whole program in any child
    # process it starts, which shows up as the window opening several times.
    multiprocessing.freeze_support()
    from ui.app import main as run
    run()


if __name__ == "__main__":
    sys.exit(main())
