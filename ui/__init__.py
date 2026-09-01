"""The desktop application.

This is a package so that a packaged build can import ``ui.app`` by name.
PyInstaller analyses ``packaging/entry.py``, which imports it that way rather
than running a loose script.
"""
