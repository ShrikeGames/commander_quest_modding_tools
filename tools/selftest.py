#!/usr/bin/env python3
"""End-to-end checks against the real game pak.

Every check is self-validating: the pak stores a SHA-1 per entry and per index,
packages carry a magic and a header size, and locres/texture writers are checked
by round-tripping. Run this after changing anything in cqmod/.
"""
from __future__ import annotations
import io, struct, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cqmod import config, catalog, locres, texture, uasset, unversioned, diff
from cqmod.pak import PakReader, build_pak
from cqmod.project import Project

CARD = "Commander/Content/Data/Cards/SupplyCards/DA_Card_Supply_Human_Insight"
TEX = "Commander/Content/UI/ArtResources/Card/Human/T_Image_Card_Supply_Human_Insight"
LOC = "Commander/Content/Localization/Game/en/Game.locres"

passed = failed = 0


def check(name, cond, detail=""):
    """Record one check result and print it.

    Args:
        name (str): What was checked, phrased as the property that should hold.
        cond (bool): Whether it holds.
        detail (str): Optional measured value, shown either way so passing
            checks still report what they saw.
    """
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}" + (f"  ({detail})" if detail else ""))
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


def main():
    """Run every check against the real game pak.

    Returns:
        int: 0 if all checks passed, 1 otherwise, for use as an exit status.
    """
    print("opening pak...")
    t = time.time()
    r = PakReader(config.pak_path(), config.aes_key())
    check("pak index decrypts and hashes match", True,
          f"{len(r)} files in {time.time()-t:.2f}s")
    check("pak is the expected build", r.version == 11 and r.encrypted_index,
          f"v{r.version} encryptedIndex={r.encrypted_index}")

    print("\nOodle decode:")
    n = ok = 0
    for p, e in r.entries.items():
        if e.is_compressed and p.endswith(".uasset"):
            n += 1
            if n > 400:
                break
            d = r.read(p)
            if len(d) == e.uncompressed_size and struct.unpack_from("<I", d, 0)[0] == uasset.PACKAGE_MAGIC:
                ok += 1
    check("compressed .uasset files decode to exact size + magic", ok == min(n, 400),
          f"{ok}/{min(n,400)}")

    print("\npackage parsing:")
    pkg = uasset.parse(r.read(CARD + ".uasset"))
    payload = r.read(CARD + ".uexp")
    total = sum(e.serial_size for e in pkg.exports)
    check("export payload sizes tile the .uexp", total == len(payload) - 4,
          f"{total} vs {len(payload)-4}")
    check("package is unversioned (needs .usmap for property names)", pkg.unversioned)
    h = unversioned.parse(payload, 0)
    check("unversioned header parses", h.indices == [0, 1, 7, 11, 16, 19], str(h.indices))

    print("\nlocres:")
    loc = locres.load(r.read(LOC))
    check("locres round-trips byte-identically", locres.save(loc) == loc.raw,
          f"{len(loc.strings)} strings")
    loc2 = locres.load(r.read(LOC))
    loc2.set("ST_Card_Supply", "Insight_Title", "Pot of Greed")
    out = locres.save(loc2)
    back = locres.load(out)
    non_ascii_kept = sum(1 for s, _ in back.strings if any(ord(c) > 127 for c in s))
    orig_non_ascii = sum(1 for s, _ in loc.strings if any(ord(c) > 127 for c in s))
    check("edited locres keeps non-ASCII strings intact",
          non_ascii_kept == orig_non_ascii, f"{non_ascii_kept} vs {orig_non_ascii}")
    check("only the edited string changed",
          sum(1 for a, b in zip(loc.strings, back.strings) if a[0] != b[0]) == 1)

    print("\ntexture:")
    tex = texture.parse(r.read(TEX + ".uexp"))
    check("card art is uncompressed BGRA", tex.pixel_format == "PF_B8G8R8A8",
          f"{tex.width}x{tex.height}")
    img = texture.to_png_bytes(tex)
    check("texture decode/encode round-trips", texture.replace(tex, img) == tex.raw)

    print("\npak writer:")
    files = [("Commander/Content/A/x.uexp", bytes(range(256)) * 5),
             ("Commander/Content/A/B/y.uasset", b"payload" * 900)]
    raw = build_pak(files)
    with tempfile.NamedTemporaryFile(suffix=".pak", delete=False) as f:
        f.write(raw); tmp = f.name
    r2 = PakReader(tmp)
    check("written pak reads back", all(r2.read(k) == v for k, v in files),
          f"{len(raw)} bytes")
    r2.close(); Path(tmp).unlink()

    print("\nfield finder:")
    assets = catalog.build(r)
    by = {a.name: a for a in assets}
    a, b = by["DA_Card_Supply_Human_Insight"], by["DA_Card_Supply_Human_Insight+"]
    na = uasset.parse(r.read(a.uasset)).names
    nb = uasset.parse(r.read(b.uasset)).names
    d = diff.compare(a, r.read(a.uexp), b, r.read(b.uexp), na, nb, only_plausible=True)
    check("diff isolates the two MoveCard counts",
          sorted(f.offset_a for f in d) == [104, 119], str([f.offset_a for f in d]))

    print("\nproject build (Pot of Greed):")
    p = Project(name="SelfTest")
    p.set_text("ST_Card_Supply", "Insight_Title", "Pot of Greed")
    p.set_text("ST_Card_Supply", "Insight_Desc", "Draw 3 cards.")
    p.set_value(CARD, 104, 0, "send count")
    p.set_value(CARD, 119, 3, "draw count")
    raw = p.build(r)
    with tempfile.NamedTemporaryFile(suffix=".pak", delete=False) as f:
        f.write(raw); tmp = f.name
    m = PakReader(tmp)
    card = m.read(CARD + ".uexp")
    check("built card has send=0 draw=3",
          struct.unpack_from("<i", card, 104)[0] == 0 and
          struct.unpack_from("<i", card, 119)[0] == 3)
    check("built card keeps its original byte length", len(card) == len(payload),
          f"{len(card)}")
    ml = locres.load(m.read(LOC))
    check("built locres carries the new title",
          ml.get("ST_Card_Supply", "Insight_Title") == "Pot of Greed")
    m.close(); Path(tmp).unlink()
    r.close()

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
