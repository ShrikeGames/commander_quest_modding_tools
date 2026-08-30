"""A mod project: a set of staged edits that compile into a single ``_P.pak``.

Three edit kinds are supported, all chosen because they preserve byte lengths
and therefore need no ``.uasset`` export-table surgery:

======================  =====================================================
:class:`TextEdit`       retarget an existing localization string
:class:`TextureEdit`    replace card art, dimensions preserved
:class:`ValueEdit`      overwrite an ``int32`` inside an export payload
======================  =====================================================

Creating genuinely *new* assets is out of scope: that changes byte lengths and
needs the class property schema a ``.usmap`` would provide. See
``docs/limitations.md``.

Projects serialize to JSON, so a mod is a small readable file that can be kept
under version control and rebuilt after a game patch.
"""
from __future__ import annotations
import json, struct
from dataclasses import dataclass, field, asdict
from pathlib import Path

from . import locres, texture, uasset
from .pak import build_pak

LOCRES_PATH = "Commander/Content/Localization/Game/{locale}/Game.locres"


@dataclass
class TextEdit:
    """A change to one localized string.

    Attributes:
        namespace (str): Locres namespace, e.g. ``ST_Card_Supply``.
        key (str): Key within it, e.g. ``Insight_Title``.
        value (str): Replacement text.
        locale (str): Locale to edit. Only this locale changes, so other
            languages keep the original text.
    """

    namespace: str
    key: str
    value: str
    locale: str = "en"


@dataclass
class TextureEdit:
    """A replacement for one texture's art.

    Attributes:
        texture_path (str): Pak path of the art package, without extension.
        image_path (str): Local image file. Read at build time, so editing the
            file and rebuilding picks up the new version.
    """

    texture_path: str
    image_path: str


@dataclass
class ValueEdit:
    """An overwrite of one ``int32`` inside an asset payload.

    Attributes:
        asset_path (str): Pak path of the asset, without extension.
        offset (int): Byte offset into its ``.uexp``. Find these with
            :func:`cqmod.diff.compare` rather than by hand.
        value (int): Replacement value.
        label (str): Optional note recorded for the build log.
    """

    asset_path: str
    offset: int
    value: int
    label: str = ""


@dataclass
class TagEdit:
    """A gameplay tag swap.

    Tags are ``FName`` values, indices into the owning package's name table, so
    setting one to a tag the asset has never used requires appending that name
    to the table first. The build does that automatically.

    Attributes:
        asset_path (str): Pak path of the asset, without extension.
        offset (int): Byte offset of the tag's name index in the ``.uexp``.
        tag (str): The tag to set, e.g. ``Card.SummonType.Cavalry``.
    """

    asset_path: str
    offset: int
    tag: str


@dataclass
class Project:
    """A named collection of staged edits.

    Attributes:
        name (str): Mod name, used for the output filename.
        texts (list[TextEdit]): Staged text changes.
        textures (list[TextureEdit]): Staged art replacements.
        values (list[ValueEdit]): Staged value overwrites.
    """

    name: str = "MyMod"
    texts: list = field(default_factory=list)
    textures: list = field(default_factory=list)
    values: list = field(default_factory=list)
    tags: list = field(default_factory=list)

    def save(self, path) -> None:
        """Write the project to JSON.

        Args:
            path (str | Path): Destination file.
        """
        Path(path).write_text(json.dumps({
            "name": self.name,
            "texts": [asdict(t) for t in self.texts],
            "textures": [asdict(t) for t in self.textures],
            "values": [asdict(v) for v in self.values],
            "tags": [asdict(t) for t in self.tags],
        }, indent=1))

    @classmethod
    def load(cls, path) -> "Project":
        """Read a project from JSON.

        Args:
            path (str | Path): File written by :meth:`save`.

        Returns:
            Project: The restored project.
        """
        d = json.loads(Path(path).read_text())
        return cls(
            name=d.get("name", "MyMod"),
            texts=[TextEdit(**x) for x in d.get("texts", [])],
            textures=[TextureEdit(**x) for x in d.get("textures", [])],
            values=[ValueEdit(**x) for x in d.get("values", [])],
            tags=[TagEdit(**x) for x in d.get("tags", [])],
        )

    @property
    def is_empty(self) -> bool:
        """Whether anything is staged.

        Returns:
            bool: True if there is nothing to build.
        """
        return not (self.texts or self.textures or self.values or self.tags)

    def set_text(self, namespace, key, value, locale="en"):
        """Stage a text change, replacing any existing edit to the same key.

        Args:
            namespace (str): Locres namespace.
            key (str): Key within it.
            value (str): Replacement text.
            locale (str): Locale to edit.
        """
        for t in self.texts:
            if (t.namespace, t.key, t.locale) == (namespace, key, locale):
                t.value = value
                return
        self.texts.append(TextEdit(namespace, key, value, locale))

    def set_texture(self, texture_path, image_path):
        """Stage an art replacement, replacing any existing edit to the same texture.

        Args:
            texture_path (str): Pak path of the art package, without extension.
            image_path (str): Local image file.
        """
        for t in self.textures:
            if t.texture_path == texture_path:
                t.image_path = image_path
                return
        self.textures.append(TextureEdit(texture_path, image_path))

    def set_value(self, asset_path, offset, value, label=""):
        """Stage a value overwrite, replacing any existing edit at the same offset.

        Args:
            asset_path (str): Pak path of the asset, without extension.
            offset (int): Byte offset into its ``.uexp``.
            value (int): Replacement value.
            label (str): Optional note for the build log.
        """
        for v in self.values:
            if (v.asset_path, v.offset) == (asset_path, offset):
                v.value, v.label = value, label
                return
        self.values.append(ValueEdit(asset_path, offset, value, label))

    def set_tag(self, asset_path, offset, tag):
        """Stage a gameplay tag change, replacing any edit at the same offset.

        Args:
            asset_path (str): Pak path of the asset, without extension.
            offset (int): Byte offset of the tag's name index.
            tag (str): Tag to set.
        """
        for t in self.tags:
            if (t.asset_path, t.offset) == (asset_path, offset):
                t.tag = tag
                return
        self.tags.append(TagEdit(asset_path, offset, tag))

    def clear_asset(self, asset_path):
        """Drop every staged value edit for one asset.

        Args:
            asset_path (str): Pak path of the asset, without extension.
        """
        self.values = [v for v in self.values if v.asset_path != asset_path]

    def build(self, reader, log=None) -> bytes:
        """Apply every staged edit and compile a mod pak.

        Edits are grouped before being applied, so several changes to one asset
        share a single read, and all text edits for a locale are folded into one
        rewrite of that localization resource. Both the edited ``.uexp`` and its
        untouched ``.uasset`` are included, keeping the pair together.

        Args:
            reader (cqmod.pak.PakReader): An open archive to read originals from.
            log (Callable[[str], None] | None): Called with progress lines.

        Returns:
            bytes: A complete ``.pak``.

        Raises:
            ValueError: If nothing is staged, or a value edit's offset lies
                outside the asset's payload.
            cqmod.texture.TextureError: If a replacement image cannot be fitted.
            KeyError: If a referenced asset is not in the archive.
        """
        def say(m):
            """Emit one progress line if a logger was supplied.

            Args:
                m (str): The message.
            """
            if log:
                log(m)

        files: dict[str, bytes] = {}

        # Value and tag edits both rewrite an asset, so they are applied
        # together: a tag may need a name appended to the header, and the
        # resulting index is then written into the payload.
        touched = {v.asset_path for v in self.values} | {t.asset_path for t in self.tags}
        for asset in sorted(touched):
            header = reader.read(asset + ".uasset")
            payload = bytearray(reader.read(asset + ".uexp"))

            for t in [x for x in self.tags if x.asset_path == asset]:
                names = uasset.parse(header).names
                if t.tag in names:
                    index = names.index(t.tag)
                else:
                    header = uasset.add_name(header, t.tag)
                    index = len(names)
                    say(f"  name   {Path(asset).name} += {t.tag!r} (index {index})")
                if not (0 <= t.offset <= len(payload) - 4):
                    raise ValueError(f"{asset}: tag offset {t.offset} outside .uexp")
                struct.pack_into("<I", payload, t.offset, index)
                say(f"  tag    {Path(asset).name} @{t.offset} = {t.tag}")

            for v in [x for x in self.values if x.asset_path == asset]:
                if not (0 <= v.offset <= len(payload) - 4):
                    raise ValueError(f"{asset}: offset {v.offset} outside .uexp "
                                     f"(0..{len(payload)-4})")
                struct.pack_into("<i", payload, v.offset, v.value)
                say(f"  value  {Path(asset).name} @{v.offset} = {v.value}"
                    + (f"  ({v.label})" if v.label else ""))

            files[asset + ".uexp"] = bytes(payload)
            files[asset + ".uasset"] = header

        from PIL import Image
        for t in self.textures:
            tex = texture.parse(reader.read(t.texture_path + ".uexp"))
            img = Image.open(t.image_path)
            files[t.texture_path + ".uexp"] = texture.replace(tex, img)
            files[t.texture_path + ".uasset"] = reader.read(t.texture_path + ".uasset")
            say(f"  art    {Path(t.texture_path).name} <- {Path(t.image_path).name} "
                f"({tex.width}x{tex.height})")

        by_locale: dict[str, list] = {}
        for t in self.texts:
            by_locale.setdefault(t.locale, []).append(t)
        for locale, edits in by_locale.items():
            path = LOCRES_PATH.format(locale=locale)
            loc = locres.load(reader.read(path))
            for e in edits:
                loc.set(e.namespace, e.key, e.value)
                say(f"  text   [{locale}] {e.namespace}/{e.key} = {e.value!r}")
            files[path] = locres.save(loc)

        if not files:
            raise ValueError("project has no edits")
        say(f"packing {len(files)} files")
        return build_pak(sorted(files.items()))

    def install(self, reader, paks_dir, log=None) -> Path:
        """Build the mod and write it into the game's pak folder.

        The output is named ``ZZZ_<name>_P.pak``: ``_P`` marks it as a patch pak
        so UE mounts it above the base archive, and the ``ZZZ`` prefix keeps it
        sorting last among patches.

        Args:
            reader (cqmod.pak.PakReader): An open archive to read originals from.
            paks_dir (str | Path): The game's ``Content/Paks`` directory.
            log (Callable[[str], None] | None): Called with progress lines.

        Returns:
            Path: The installed file. Delete it to uninstall the mod; nothing
            else on disk is modified.

        Raises:
            ValueError: Propagated from :meth:`build`.
            OSError: If the pak folder is not writable.
        """
        raw = self.build(reader, log=log)
        out = Path(paks_dir) / f"ZZZ_{self.name}_P.pak"
        out.write_bytes(raw)
        if log:
            log(f"installed {out} ({len(raw):,} bytes)")
        return out
