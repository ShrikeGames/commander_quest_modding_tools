"""Enabling and disabling installed mods.

The game loads every ``.pak`` it finds in ``Commander/Content/Paks``, so there is
no built-in notion of a disabled mod. This module adds one by keeping a separate
staging directory: a mod is *enabled* when its file sits in the game's pak
folder, and *disabled* when it sits in staging. Toggling moves the file between
the two.

Keeping staging outside the game directory means mods survive a Steam file
verification, which may remove unrecognised files from the pak folder.

The base game archive is identified and protected: it is never listed as a mod
and cannot be moved or deleted through this interface.
"""
from __future__ import annotations
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

PATCH_SUFFIX = "_P.pak"
"""UE mounts a pak whose name ends in ``_P`` above the base archive."""


class ModError(RuntimeError):
    """Raised when a mod cannot be moved, imported or removed."""


@dataclass
class ModInfo:
    """One mod pak, in either the live or the staging directory.

    Attributes:
        filename (str): File name including the ``.pak`` extension.
        name (str): Display name, with the ``ZZZ_`` prefix and ``_P.pak`` suffix
            stripped.
        path (Path): Where the file currently lives.
        enabled (bool): True if it is in the game's pak folder.
        size (int): File size in bytes.
        modified (datetime): Last modification time.
        file_count (int | None): Number of assets inside, or None if the pak
            could not be read.
        error (str): Why the pak could not be read, empty if it was fine.
    """

    filename: str
    name: str
    path: Path
    enabled: bool
    size: int
    modified: datetime
    file_count: int | None = None
    error: str = ""

    @property
    def is_patch_pak(self) -> bool:
        """Whether the name gives this mod patch priority.

        Returns:
            bool: True if the file ends in ``_P.pak``. A mod without that suffix
            mounts at base priority and will not reliably override game assets.
        """
        return self.filename.endswith(PATCH_SUFFIX)


def display_name(filename: str) -> str:
    """Derive a readable name from a pak filename.

    Args:
        filename (str): File name, e.g. ``ZZZ_PotOfGreed_P.pak``.

    Returns:
        str: The name without the sorting prefix and patch suffix, e.g.
        ``PotOfGreed``.
    """
    n = filename
    if n.endswith(PATCH_SUFFIX):
        n = n[:-len(PATCH_SUFFIX)]
    elif n.endswith(".pak"):
        n = n[:-len(".pak")]
    return n[4:] if n.startswith("ZZZ_") else n


class ModManager:
    """Moves mod paks between the game's pak folder and a staging folder.

    Args:
        paks_dir (str | Path): The game's ``Content/Paks`` directory.
        staging_dir (str | Path): Where disabled mods are kept. Created on demand.
        base_pak (str | Path | None): The base game archive, which is excluded
            from listings and protected from every operation.

    Attributes:
        paks_dir (Path): The live directory.
        staging_dir (Path): The staging directory.
        base_pak (Path | None): The protected base archive.
    """

    def __init__(self, paks_dir, staging_dir, base_pak=None):
        """Create a manager over a live and a staging directory."""
        self.paks_dir = Path(paks_dir)
        self.staging_dir = Path(staging_dir)
        self.base_pak = Path(base_pak) if base_pak else None

    def _is_base(self, p: Path) -> bool:
        """Whether a path is the protected base game archive.

        Args:
            p (Path): Path to test.

        Returns:
            bool: True if it is the base archive.
        """
        return self.base_pak is not None and p.name == self.base_pak.name

    def _scan(self, directory: Path, enabled: bool, read_contents: bool) -> list:
        """Collect mods from one directory.

        Args:
            directory (Path): Directory to scan. Missing directories yield nothing.
            enabled (bool): Value to record for :attr:`ModInfo.enabled`.
            read_contents (bool): Whether to open each pak to count its assets.

        Returns:
            list[ModInfo]: One entry per ``.pak`` found, base archive excluded.
        """
        out = []
        if not directory.is_dir():
            return out
        for p in sorted(directory.glob("*.pak")):
            if self._is_base(p):
                continue
            st = p.stat()
            info = ModInfo(
                filename=p.name, name=display_name(p.name), path=p,
                enabled=enabled, size=st.st_size,
                modified=datetime.fromtimestamp(st.st_mtime),
            )
            if read_contents:
                try:
                    from .pak import PakReader
                    with PakReader(p) as r:
                        info.file_count = len(r)
                except Exception as e:
                    info.error = str(e)
            out.append(info)
        return out

    def list(self, read_contents: bool = True) -> list:
        """List every mod in both directories.

        Args:
            read_contents (bool): Open each pak to count its assets. Set False
                to skip the reads if listing feels slow.

        Returns:
            list[ModInfo]: Enabled mods first, then disabled, each alphabetical.
        """
        return (self._scan(self.paks_dir, True, read_contents)
                + self._scan(self.staging_dir, False, read_contents))

    def enable(self, mod: ModInfo) -> Path:
        """Move a mod into the game's pak folder.

        Args:
            mod (ModInfo): The mod to enable. Already-enabled mods are returned
                unchanged.

        Returns:
            Path: The mod's new location.

        Raises:
            ModError: If the file has gone missing, the name collides with
                something already live, or the move fails.
        """
        if mod.enabled:
            return mod.path
        return self._move(mod, self.paks_dir)

    def disable(self, mod: ModInfo) -> Path:
        """Move a mod out of the game's pak folder into staging.

        Args:
            mod (ModInfo): The mod to disable. Already-disabled mods are
                returned unchanged.

        Returns:
            Path: The mod's new location.

        Raises:
            ModError: If the mod is the base archive, the file has gone missing,
                the name collides in staging, or the move fails.
        """
        if self._is_base(mod.path):
            raise ModError("refusing to move the base game archive")
        if not mod.enabled:
            return mod.path
        return self._move(mod, self.staging_dir)

    def _move(self, mod: ModInfo, dest_dir: Path) -> Path:
        """Move a mod file into a directory.

        Args:
            mod (ModInfo): The mod to move.
            dest_dir (Path): Destination directory, created if absent.

        Returns:
            Path: The new location.

        Raises:
            ModError: If the source is gone, the destination is occupied, or the
                move fails.
        """
        if not mod.path.is_file():
            raise ModError(f"{mod.filename} is no longer at {mod.path.parent}")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / mod.filename
        if dest.exists():
            raise ModError(f"{dest} already exists; remove or rename it first")
        try:
            shutil.move(str(mod.path), str(dest))
        except OSError as e:
            raise ModError(f"could not move {mod.filename}: {e}") from e
        mod.path = dest
        mod.enabled = dest_dir == self.paks_dir
        return dest

    def set_enabled(self, mod: ModInfo, enabled: bool) -> Path:
        """Enable or disable a mod.

        Args:
            mod (ModInfo): The mod to toggle.
            enabled (bool): Desired state.

        Returns:
            Path: The mod's location afterwards.

        Raises:
            ModError: Propagated from :meth:`enable` or :meth:`disable`.
        """
        return self.enable(mod) if enabled else self.disable(mod)

    def import_pak(self, src, enable: bool = False) -> ModInfo:
        """Copy an external pak into staging.

        The file is validated by opening it, so a corrupt or encrypted pak is
        rejected before it can reach the game's folder.

        Args:
            src (str | Path): The pak to import.
            enable (bool): Enable it immediately after importing.

        Returns:
            ModInfo: The imported mod.

        Raises:
            ModError: If the file is not a readable pak or the name is taken.
        """
        src = Path(src)
        try:
            from .pak import PakReader
            with PakReader(src) as r:
                count = len(r)
        except Exception as e:
            raise ModError(f"{src.name} is not a readable pak: {e}") from e

        self.staging_dir.mkdir(parents=True, exist_ok=True)
        dest = self.staging_dir / src.name
        if dest.exists() or (self.paks_dir / src.name).exists():
            raise ModError(f"a mod named {src.name} already exists")
        shutil.copy2(src, dest)

        st = dest.stat()
        mod = ModInfo(dest.name, display_name(dest.name), dest, False,
                      st.st_size, datetime.fromtimestamp(st.st_mtime), count)
        if enable:
            self.enable(mod)
        return mod

    def delete(self, mod: ModInfo) -> None:
        """Permanently delete a mod's pak file.

        Args:
            mod (ModInfo): The mod to delete.

        Raises:
            ModError: If the mod is the base archive or deletion fails.
        """
        if self._is_base(mod.path):
            raise ModError("refusing to delete the base game archive")
        try:
            mod.path.unlink()
        except OSError as e:
            raise ModError(f"could not delete {mod.filename}: {e}") from e

    def staging_path_for(self, name: str) -> Path:
        """Where a freshly built mod of this name should be written.

        Args:
            name (str): Project name, without prefix or extension.

        Returns:
            Path: The staging path, e.g. ``<staging>/ZZZ_MyMod_P.pak``.
        """
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        return self.staging_dir / f"ZZZ_{name}{PATCH_SUFFIX}"
