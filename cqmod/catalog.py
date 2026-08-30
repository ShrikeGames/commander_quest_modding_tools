"""An index of the game's data assets: cards, units, gear, commanders, and more.

Built by walking every DA_*.uasset under Content/Data, reading its primary
export's class, pulling the string-table FTexts out of the payload, and
resolving those against the localization resource.

This needs no .usmap: the class comes from the import table and the text keys
are self-describing, so display data is fully recoverable. Numeric property
*values* still require knowing a class's property order -- see unversioned.py.
"""
from __future__ import annotations
import json, re
from dataclasses import dataclass, field, asdict
from pathlib import Path

from . import uasset, ftext, locres, unversioned

DATA_ROOT = "Commander/Content/Data/"
DEFAULT_LOCALE = "en"
LOCRES_PATH = "Commander/Content/Localization/Game/{locale}/Game.locres"


@dataclass
class ExportInfo:
    index: int
    name: str
    class_name: str
    start: int
    end: int
    prop_indices: list = field(default_factory=list)
    header_bytes: int = 0


@dataclass
class Asset:
    path: str                # pak path without extension
    package: str             # /Game/...
    name: str                # DA_Card_...
    class_name: str          # CMCardData_Supply, CMUnitData, ...
    category: str            # folder under Data/, e.g. 'Cards/SupplyCards'
    title: str = ""
    description: str = ""
    title_key: str = ""
    desc_key: str = ""
    namespace: str = ""
    texture: str = ""        # pak path of the art package, without extension
    exports: list = field(default_factory=list)

    @property
    def uasset(self) -> str: return self.path + ".uasset"
    @property
    def uexp(self) -> str: return self.path + ".uexp"


def _texture_package(pkg) -> str:
    for imp in pkg.imports:
        if imp.class_name == "Texture2D":
            outer = imp.outer_index
            if outer < 0:
                oi = -outer - 1
                if oi < len(pkg.imports):
                    p = pkg.imports[oi].object_name
                    if p.startswith("/Game/"):
                        return "Commander/Content/" + p[len("/Game/"):]
    return ""


def build(reader, locale: str = DEFAULT_LOCALE, progress=None) -> list:
    """Index every DA_* data asset in the pak. `progress(done, total)` optional."""
    loc = None
    lp = LOCRES_PATH.format(locale=locale)
    if lp in reader:
        loc = locres.load(reader.read(lp))

    paths = sorted(
        p[:-len(".uasset")] for p in reader.files()
        if p.startswith(DATA_ROOT) and p.endswith(".uasset")
        and Path(p).name.startswith("DA_")
    )
    out = []
    for n, base in enumerate(paths):
        if progress and n % 25 == 0:
            progress(n, len(paths))
        try:
            pkg = uasset.parse(reader.read(base + ".uasset"))
            payload = reader.read(base + ".uexp") if (base + ".uexp") in reader else b""
        except Exception:
            continue
        # The main object is not reliably exports[0]; match the export named
        # after the asset itself and only then fall back to the first export.
        aname = Path(base).name
        primary = next((e for e in pkg.exports if e.object_name == aname),
                       pkg.exports[0] if pkg.exports else None)
        rel = base[len(DATA_ROOT):]
        a = Asset(
            path=base, package=pkg.name, name=Path(base).name,
            class_name=primary.class_name if primary else "",
            category=str(Path(rel).parent).replace("\\", "/"),
            texture=_texture_package(pkg),
        )
        for i, e in enumerate(pkg.exports):
            s, t = e.uexp_slice(pkg.header_size)
            info = ExportInfo(i + 1, e.object_name, e.class_name, s, t)
            if 0 <= s < t <= len(payload):
                try:
                    h = unversioned.parse(payload, s)
                    info.prop_indices, info.header_bytes = h.indices, h.size
                except Exception:
                    pass
            a.exports.append(info)

        texts = ftext.find_all(payload, pkg.names) if payload else []
        for t in texts:
            ns = t.table_name.rsplit(".", 1)[-1]
            a.namespace = a.namespace or ns
            if t.key.endswith("_Title") and not a.title_key:
                a.title_key = t.key
            elif t.key.endswith("_Desc") and not a.desc_key:
                a.desc_key = t.key
        if loc:
            if a.title_key:
                a.title = loc.get(a.namespace, a.title_key) or ""
            if a.desc_key:
                a.description = loc.get(a.namespace, a.desc_key) or ""
        out.append(a)
    if progress:
        progress(len(paths), len(paths))
    return out


def save(assets, path) -> None:
    Path(path).write_text(json.dumps([asdict(a) for a in assets], indent=1))


def load(path) -> list:
    raw = json.loads(Path(path).read_text())
    out = []
    for d in raw:
        ex = [ExportInfo(**e) for e in d.pop("exports", [])]
        out.append(Asset(**d, exports=ex))
    return out
