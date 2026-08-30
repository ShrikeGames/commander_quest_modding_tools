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
from cqmod import config, catalog, locres, texture, uasset, unversioned, diff, mods, schema, usmap
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
    print("\nrecovered property names:")
    try:
        um = usmap.Usmap.load()
    except usmap.UsmapError as e:
        check("property schema is present", False, str(e))
        um = None
    if um:
        check("schema covers the card and effect classes",
              "CMCardData_Supply" in um and "CMEffectData_MoveCard" in um)
        names = [p["name"] for p in um.properties("CMCardData_Supply")]
        check("card property order matches the serializer",
              [names[i] for i in (0, 1, 7, 11, 16, 19)] ==
              ["cardName", "CardDesc", "CardIllustration", "UsingSound",
               "UseEffects", "EnhancedCardData"],
              str([names[i] for i in (0, 1, 7, 11, 16, 19)]))
        card = by["DA_Card_Supply_Human_Insight"]
        pl = r.read(card.uexp)
        placed = [f for e in card.exports for f in um.place(e, pl)]
        counts = [f for f in placed if f.name == "Count"]
        check("Count lands on the offsets found by hand",
              sorted(f.offset for f in counts) == [104, 119],
              str(sorted(f.offset for f in counts)))
        zeroed = [f for f in placed if f.offset == -1]
        check("zero-valued properties are reported as such",
              any(f.name == "PileLocation" for f in zeroed))

    print("\nproperty schema:")
    layouts = schema.build(r, assets)
    solved = sum(l.solved_count for l in layouts.values())
    total = sum(len(l.properties) for l in layouts.values())
    checkable = [l for l in layouts.values() if l.checked]
    perfect = [l for l in checkable if l.confidence == 1.0]
    check("solver derives sizes for a useful share of properties", solved > 250,
          f"{solved}/{total}")
    check("every solved layout reproduces its observations",
          len(perfect) == len(checkable), f"{len(perfect)}/{len(checkable)}")
    mv = layouts["CMEffectData_MoveCard"]
    check("MoveCard sizes are forced by the data",
          mv.properties[13].size == 4 and mv.properties[11].size == 12,
          f"idx13={mv.properties[13].size} idx11={mv.properties[11].size}")
    zero_seen = any(e.zero_indices for a in assets for e in a.exports)
    check("zero-valued properties are decoded from the header bitmap", zero_seen)

    m.close(); Path(tmp).unlink()
    r.close()

    print("\nmod manager:")
    with tempfile.TemporaryDirectory() as td:
        live, stage = Path(td) / "Paks", Path(td) / "staging"
        live.mkdir(); stage.mkdir()
        base = live / "Commander-Windows.pak"
        base.write_bytes(build_pak([("Commander/Content/base.txt", b"base")]))
        (live / "ZZZ_Demo_P.pak").write_bytes(
            build_pak([("Commander/Content/a.uexp", b"demo")]))
        mgr = mods.ModManager(live, stage, base)

        found = mgr.list()
        check("base game archive is excluded from the mod list",
              [m.filename for m in found] == ["ZZZ_Demo_P.pak"],
              str([m.filename for m in found]))
        check("mod reports its asset count", found[0].file_count == 1)
        check("display name strips prefix and suffix", found[0].name == "Demo",
              found[0].name)

        mgr.disable(found[0])
        check("disabling moves the pak out of the live folder",
              not (live / "ZZZ_Demo_P.pak").exists()
              and (stage / "ZZZ_Demo_P.pak").exists())
        check("a disabled mod is still listed",
              [(m.name, m.enabled) for m in mgr.list()] == [("Demo", False)])

        mgr.enable(mgr.list()[0])
        check("enabling moves it back",
              (live / "ZZZ_Demo_P.pak").exists()
              and not (stage / "ZZZ_Demo_P.pak").exists())

        try:
            mgr.disable(mods.ModInfo(base.name, "base", base, True, 0,
                                     found[0].modified))
            protected = False
        except mods.ModError:
            protected = True
        check("base game archive cannot be moved", protected)
        check("base game archive still present", base.exists())

        junk = Path(td) / "not-a-pak.pak"
        junk.write_bytes(b"nonsense")
        try:
            mgr.import_pak(junk)
            rejected = False
        except mods.ModError:
            rejected = True
        check("importing a non-pak is rejected", rejected)

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
