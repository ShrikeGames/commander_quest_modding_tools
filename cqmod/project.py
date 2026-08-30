"""A mod project: a set of edits that compile into a single _P.pak.

Three edit kinds are supported today, all chosen because they preserve byte
lengths and therefore need no .uasset export-table surgery:

  TextEdit      retarget an existing localization string
  TextureEdit   replace card art (PF_B8G8R8A8, dimensions preserved)
  ValueEdit     overwrite an int32 inside an export payload

Creating genuinely *new* assets is not possible this way -- that changes byte
lengths and needs the class property schema. See README.
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
    namespace: str
    key: str
    value: str
    locale: str = "en"


@dataclass
class TextureEdit:
    texture_path: str        # pak path without extension
    image_path: str          # local PNG


@dataclass
class ValueEdit:
    asset_path: str          # pak path without extension
    offset: int              # byte offset into the .uexp
    value: int
    label: str = ""


@dataclass
class Project:
    name: str = "MyMod"
    texts: list = field(default_factory=list)
    textures: list = field(default_factory=list)
    values: list = field(default_factory=list)

    # -- persistence -----------------------------------------------------
    def save(self, path) -> None:
        Path(path).write_text(json.dumps({
            "name": self.name,
            "texts": [asdict(t) for t in self.texts],
            "textures": [asdict(t) for t in self.textures],
            "values": [asdict(v) for v in self.values],
        }, indent=1))

    @classmethod
    def load(cls, path) -> "Project":
        d = json.loads(Path(path).read_text())
        return cls(
            name=d.get("name", "MyMod"),
            texts=[TextEdit(**x) for x in d.get("texts", [])],
            textures=[TextureEdit(**x) for x in d.get("textures", [])],
            values=[ValueEdit(**x) for x in d.get("values", [])],
        )

    @property
    def is_empty(self) -> bool:
        return not (self.texts or self.textures or self.values)

    # -- editing ---------------------------------------------------------
    def set_text(self, namespace, key, value, locale="en"):
        for t in self.texts:
            if (t.namespace, t.key, t.locale) == (namespace, key, locale):
                t.value = value
                return
        self.texts.append(TextEdit(namespace, key, value, locale))

    def set_texture(self, texture_path, image_path):
        for t in self.textures:
            if t.texture_path == texture_path:
                t.image_path = image_path
                return
        self.textures.append(TextureEdit(texture_path, image_path))

    def set_value(self, asset_path, offset, value, label=""):
        for v in self.values:
            if (v.asset_path, v.offset) == (asset_path, offset):
                v.value, v.label = value, label
                return
        self.values.append(ValueEdit(asset_path, offset, value, label))

    def clear_asset(self, asset_path):
        self.values = [v for v in self.values if v.asset_path != asset_path]

    # -- building --------------------------------------------------------
    def build(self, reader, log=None) -> bytes:
        def say(m):
            if log: log(m)

        files: dict[str, bytes] = {}

        # value edits, grouped so several edits to one asset apply together
        by_asset: dict[str, list] = {}
        for v in self.values:
            by_asset.setdefault(v.asset_path, []).append(v)
        for asset, edits in by_asset.items():
            payload = bytearray(reader.read(asset + ".uexp"))
            for v in edits:
                if not (0 <= v.offset <= len(payload) - 4):
                    raise ValueError(f"{asset}: offset {v.offset} outside .uexp "
                                     f"(0..{len(payload)-4})")
                struct.pack_into("<i", payload, v.offset, v.value)
                say(f"  value  {Path(asset).name} @{v.offset} = {v.value}"
                    + (f"  ({v.label})" if v.label else ""))
            files[asset + ".uexp"] = bytes(payload)
            files[asset + ".uasset"] = reader.read(asset + ".uasset")

        # texture edits
        from PIL import Image
        for t in self.textures:
            tex = texture.parse(reader.read(t.texture_path + ".uexp"))
            img = Image.open(t.image_path)
            files[t.texture_path + ".uexp"] = texture.replace(tex, img)
            files[t.texture_path + ".uasset"] = reader.read(t.texture_path + ".uasset")
            say(f"  art    {Path(t.texture_path).name} <- {Path(t.image_path).name} "
                f"({tex.width}x{tex.height})")

        # text edits, batched per locale into one locres rewrite each
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
        """Write the mod pak into the game's Paks folder.

        The ZZZ_ prefix keeps it sorting last, and _P marks it as a patch pak so
        UE mounts it at higher priority than the base archive.
        """
        raw = self.build(reader, log=log)
        out = Path(paks_dir) / f"ZZZ_{self.name}_P.pak"
        out.write_bytes(raw)
        if log:
            log(f"installed {out} ({len(raw):,} bytes)")
        return out
