"""Tools for reading and modifying Commander Quest's game data.

The package is layered: :mod:`cqmod.pak` reads and writes the Unreal Engine
archive, :mod:`cqmod.uasset` and friends interpret the assets inside it,
:mod:`cqmod.catalog` indexes them, and :mod:`cqmod.project` compiles edits back
into a mod pak.

See ``docs/architecture.md`` for how the pieces fit together.
"""

__version__ = "0.1.0"
