"""An index of the game's data assets: cards, units, gear, commanders and more.

Built by walking every ``DA_*.uasset`` under ``Content/Data``, reading its
primary export's class, pulling string-table ``FText`` values out of the payload,
and resolving those against the localization resource.

None of this needs a ``.usmap``: the class comes from the import table and text
keys are self-describing, so everything shown in the browser is fully
recoverable. Numeric property *values* are a different matter. They are
addressed by byte offset, see :mod:`cqmod.unversioned` and :mod:`cqmod.diff`.

Indexing the whole game takes roughly a tenth of a second, so the catalog is
rebuilt on startup rather than cached; :func:`save` and :func:`load` exist for
diffing catalogs across game patches.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

from . import uasset, ftext, locres, unversioned

DATA_ROOT = "Commander/Content/Data/"
DEFAULT_LOCALE = "en"
LOCRES_PATH = "Commander/Content/Localization/Game/{locale}/Game.locres"


@dataclass
class ExportInfo:
    """Summary of one export within an asset.

    Attributes:
        index (int): 1-based export index, matching ``FPackageIndex``.
        name (str): The export object's name.
        class_name (str): Its class, e.g. ``CMEffectData_MoveCard``. Usually the
            best available clue about what an export's numbers mean.
        start (int): Offset of its payload in the ``.uexp``.
        end (int): One past the payload's last byte.
        prop_indices (list[int]): Property indices carrying a value.
        header_bytes (int): Size of the unversioned header; value data starts at
            ``start + header_bytes``.
    """

    index: int
    name: str
    class_name: str
    start: int
    end: int
    prop_indices: list = field(default_factory=list)
    header_bytes: int = 0


@dataclass
class Asset:
    """One indexed data asset.

    Attributes:
        path (str): Pak path without extension; add ``.uasset``/``.uexp``.
        package (str): Package path, e.g. ``/Game/Data/Cards/...``.
        name (str): File name, e.g. ``DA_Card_Supply_Human_Insight``.
        class_name (str): Primary export's class, e.g. ``CMCardData_Supply``.
        category (str): Folder beneath ``Data/``, e.g. ``Cards/SupplyCards``.
        title (str): Localized title, empty if the asset has none.
        description (str): Localized description.
        title_key (str): Locres key for the title, e.g. ``Insight_Title``.
        desc_key (str): Locres key for the description.
        namespace (str): Locres namespace, e.g. ``ST_Card_Supply``.
        texture (str): Pak path of the art package without extension, if any.
        exports (list[ExportInfo]): Exports in file order.
    """

    path: str
    package: str
    name: str
    class_name: str
    category: str
    title: str = ""
    description: str = ""
    title_key: str = ""
    desc_key: str = ""
    namespace: str = ""
    texture: str = ""
    exports: list = field(default_factory=list)

    @property
    def uasset(self) -> str:
        """Pak path of the header file.

        Returns:
            str: :attr:`path` with ``.uasset`` appended.
        """
        return self.path + ".uasset"

    @property
    def uexp(self) -> str:
        """Pak path of the payload file.

        Returns:
            str: :attr:`path` with ``.uexp`` appended.
        """
        return self.path + ".uexp"


def _texture_package(pkg) -> str:
    """Find the art package an asset references.

    A ``Texture2D`` import's outer points at the package import that contains it,
    which is where the usable path lives.

    Args:
        pkg (cqmod.uasset.Package): A parsed package header.

    Returns:
        str: Pak path of the texture without extension, or an empty string if
        the asset references no texture.
    """
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
    """Index every ``DA_*`` data asset in the archive.

    Assets that fail to parse are skipped rather than aborting the scan, so one
    unusual file cannot break the browser.

    Args:
        reader (cqmod.pak.PakReader): An open archive.
        locale (str): Locale whose text to resolve. Missing locales simply leave
            titles and descriptions empty.
        progress (Callable[[int, int], None] | None): Called periodically with
            ``(done, total)``.

    Returns:
        list[Asset]: Indexed assets, sorted by pak path.
    """
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
            path=base, package=pkg.name, name=aname,
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
    """Write a catalog to JSON.

    Args:
        assets (list[Asset]): Catalog from :func:`build`.
        path (str | Path): Destination file.
    """
    Path(path).write_text(json.dumps([asdict(a) for a in assets], indent=1))


def load(path) -> list:
    """Read a catalog previously written by :func:`save`.

    Args:
        path (str | Path): File to read.

    Returns:
        list[Asset]: The catalog, with exports restored to :class:`ExportInfo`.
    """
    raw = json.loads(Path(path).read_text())
    out = []
    for d in raw:
        ex = [ExportInfo(**e) for e in d.pop("exports", [])]
        out.append(Asset(**d, exports=ex))
    return out
