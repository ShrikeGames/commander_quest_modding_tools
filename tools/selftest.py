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
from cqmod import config, catalog, locres, texture, uasset, unversioned, diff, mods, schema, usmap, artchain, decks
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
    check("texture decode/encode round-trips", texture.replace(tex, img)[0] == tex.raw)

    cards_art = [p[:-5] for p in r.files()
                 if p.endswith(".uexp")
                 and p.startswith("Commander/Content/UI/ArtResources/")]
    art_ok = art_bad = 0
    mipped = 0
    for base in cards_art:
        ue2 = r.read(base + ".uexp")
        ub2 = r.read(base + ".ubulk") if base + ".ubulk" in r else b""
        try:
            t2 = texture.parse(ue2, ub2)
            art_ok += 1
            if len(t2.mips) > 1:
                mipped += 1
        except Exception:
            art_bad += 1
    check("every card illustration parses", art_bad == 0,
          f"{art_ok} parsed, {mipped} of them mipmapped")

    unit_tex = "Commander/Content/ArtAssets/Model/Human/Human_Texture/T_Human_Assasin_D"
    if unit_tex + ".uexp" in r:
        ue = r.read(unit_tex + ".uexp")
        ub = r.read(unit_tex + ".ubulk") if unit_tex + ".ubulk" in r else b""
        bt = texture.parse(ue, ub)
        check("unit textures are block compressed with a mip chain",
              bt.is_block and len(bt.mips) > 1,
              f"{bt.pixel_format}, {len(bt.mips)} mips")
        check("the mip chain accounts for the whole bulk file",
              sum(m.size for m in bt.mips if m.where == "ubulk") == len(ub),
              f"{len(ub)} bytes")
        new_ue, new_ub = texture.replace(bt, texture.to_image(bt))
        check("re-encoding a block texture preserves both file lengths",
              len(new_ue) == len(ue) and len(new_ub) == len(ub))
        import numpy as _np
        a1 = _np.asarray(texture.to_image(bt).convert("RGB"), dtype=_np.int16)
        a2 = _np.asarray(texture.to_image(texture.parse(new_ue, new_ub)).convert("RGB"),
                         dtype=_np.int16)
        sheep = ("Commander/Content/UI/ArtResources/Card/Neutral/"
                 "T_Image_Card_Summon_Sheep_B")
        if sheep + ".uexp" in r:
            sue = r.read(sheep + ".uexp")
            sub = r.read(sheep + ".ubulk") if sheep + ".ubulk" in r else b""
            st = texture.parse(sue, sub)
            check("mipmapped uncompressed card art parses", len(st.mips) > 1,
                  f"{st.pixel_format} {st.width}x{st.height}, {len(st.mips)} mips")
            s_ue, s_ub = texture.replace(st, texture.to_image(st))
            check("mipmapped uncompressed art round-trips",
                  len(s_ue) == len(sue) and len(s_ub) == len(sub))

        check("the block encoder round-trips within tolerance",
              _np.abs(a1 - a2).mean() < 4.0,
              f"mean error {_np.abs(a1 - a2).mean():.2f}/255")

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

        # Struct and array sizes that the type alone cannot give, solved from
        # the game's own data. Without these, placement stops at CMUnitData's
        # Tags and never reaches MaxHealth.
        extra = um.solve_sizes(r, assets)
        check("variable-typed property sizes are solved", extra > 100, str(extra))
        stats = {}
        for nm in ("DA_Unit_Human_Cataphract", "DA_Unit_Human_GrowingSquire"):
            u = by.get(nm)
            if not u:
                continue
            pl2 = r.read(u.uexp)
            stats[nm] = {f.name: f.value for e in u.exports for f in um.place(e, pl2)}
        placed_all = 0
        units = [x for x in assets if x.class_name == "CMUnitData"]
        for u in units:
            pl3 = r.read(u.uexp)
            ex = next(x for x in u.exports if x.class_name == "CMUnitData")
            if len(um.place(ex, pl3)) == len(ex.prop_indices):
                placed_all += 1
        check("every unit asset places completely", placed_all == len(units),
              f"{placed_all}/{len(units)}")

        check("unit stats match the values shown in game",
              stats.get("DA_Unit_Human_Cataphract", {}).get("MaxHealth") == 13
              and stats.get("DA_Unit_Human_Cataphract", {}).get("AttackDamage") == 4
              and stats.get("DA_Unit_Human_GrowingSquire", {}).get("MaxHealth") == 7
              and stats.get("DA_Unit_Human_GrowingSquire", {}).get("AttackDamage") == 3,
              "Cataphract 13/4, Squire 7/3")
        cross = {}
        for nm in ("DA_Unit_Neutral_Militia", "DA_Unit_Neutral_AmbushCavalry",
                   "DA_Unit_Human_Assassin"):
            u = by.get(nm)
            if u:
                pl4 = r.read(u.uexp)
                cross[nm] = {f.name: f.value for e in u.exports for f in um.place(e, pl4)}
        arch = by.get("DA_Unit_Human_Archer")
        if arch:
            pa2 = r.read(arch.uexp)
            anames = uasset.parse(r.read(arch.uasset)).names
            tags = []
            for e in arch.exports:
                for f in um.place(e, pa2):
                    if um.is_tag_container(e, f.index):
                        tags += [anames[i] for _, i in um.tags(f, pa2) if i < len(anames)]
            check("gameplay tags resolve to real names",
                  "Minion.AttackType.Projectile" in tags, str(tags))
            placed_arch = [f.name for e in arch.exports for f in um.place(e, pa2)]
            check("a two-tag unit still places its later properties",
                  "MaxHealth" in placed_arch and "RotationSpeed" in placed_arch)

        total_ex = placed_ex = 0
        for x in assets:
            px = r.read(x.uexp)
            for e in x.exports:
                total_ex += 1
                if len(um.place(e, px)) == len(e.prop_indices):
                    placed_ex += 1
        check("most exports place completely", placed_ex / total_ex > 0.8,
              f"{placed_ex}/{total_ex}")

        check("more unit stats match the collection screen",
              cross.get("DA_Unit_Neutral_Militia", {}).get("MaxHealth") == 4
              and cross.get("DA_Unit_Neutral_AmbushCavalry", {}).get("MaxHealth") == 8
              and cross.get("DA_Unit_Human_Assassin", {}).get("AttackDamage") == 6,
              "Militia hp4, Cavalry hp8, Assassin atk6")

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

    print("\nmodel art chain:")
    if um:
        bps = artchain.unit_blueprints(r, assets, um)
        units2 = [x for x in assets if x.class_name == "CMUnitData"]
        resolved = sum(1 for u in units2
                       if bps.get(u.name) and artchain.model_textures(r, bps[u.name]))
        check("units resolve their model texture", resolved > len(units2) * 0.9,
              f"{resolved}/{len(units2)}")
        expected = {
            "DA_Unit_Human_Assassin": "T_Human_Assasin_D",
            "DA_Unit_Human_Cataphract": "T_Human_Cataphract",
            "DA_Unit_Dwarf_Gyrocopter": "T_Dwarf_Gyrocopter_D",
            "DA_Unit_Human_Archer": "T_Human_Archer",
        }
        wrong = []
        for name, want_tex in expected.items():
            bp = bps.get(name)
            got = artchain.model_textures(r, bp) if bp else []
            if not got or Path(got[0]).name != want_tex:
                wrong.append(f"{name} -> {Path(got[0]).name if got else 'none'}")
        check("the best guess is the unit's own texture", not wrong, "; ".join(wrong))
        gyro = by.get("DA_Unit_Dwarf_Gyrocopter")
        check("incidental textures are not treated as an asset's art",
              not (gyro and gyro.texture),
              "T_Unit_Notify_NoSteel is a status icon, not the Gyrocopter's skin")

    print("\nasset links:")
    card = by.get("DA_Card_Summon_Human_Assassin")
    if card and um:
        cpkg = uasset.parse(r.read(card.uasset))
        links = dict(um.links(card, cpkg, r.read(card.uexp)))
        check("a summon card links to its unit",
              links.get("UnitData") == "DA_Unit_Human_Assassin", str(links))
        linked = 0
        summons = [x for x in assets if x.class_name == "CMCardData_Summon"]
        names = {x.name for x in assets}
        for x in summons:
            try:
                lk = dict(um.links(x, uasset.parse(r.read(x.uasset)), r.read(x.uexp)))
            except Exception:
                continue
            if lk.get("UnitData") in names:
                linked += 1
        check("most summon cards resolve their unit", linked > len(summons) * 0.8,
              f"{linked}/{len(summons)}")

    print("\nadding properties:")
    rebuilt_ok = rebuilt_bad = 0
    for x in assets:
        px = r.read(x.uexp)
        for e in x.exports:
            if not e.prop_indices or e.header_bytes == 0:
                continue
            if px[e.start:e.start + e.header_bytes] == \
                    unversioned.build(e.prop_indices, e.zero_indices):
                rebuilt_ok += 1
            else:
                rebuilt_bad += 1
    check("property headers rebuild byte-identically", rebuilt_bad == 0,
          f"{rebuilt_ok} exports")

    sheep = by.get("DA_Unit_Sheep")
    if sheep and um:
        atk = next((k for k, e in enumerate(sheep.exports)
                    if e.class_name.startswith("CMUnitAttackType")), None)
        hp_export = next((e for e in sheep.exports if e.class_name == "CMUnitData"), None)
        sp = r.read(sheep.uexp)
        hp = next((f for f in um.place(hp_export, sp) if f.name == "MaxHealth"), None)
        check("the sheep has an attack export but no AttackDamage",
              atk is not None and 6 not in sheep.exports[atk].prop_indices)

        proj4 = Project(name="SheepCheck")
        proj4.add_property(sheep.path, atk, 6, 9, "AttackDamage")
        if hp:
            # An offset after the insertion, to prove staged edits are shifted.
            proj4.set_value(sheep.path, hp.offset, 25, "MaxHealth")
        raw4 = proj4.build(r)
        with tempfile.NamedTemporaryFile(suffix=".pak", delete=False) as f4:
            f4.write(raw4); tmp4 = f4.name
        m4 = PakReader(tmp4)
        got = catalog.build_one(m4, sheep.path)[0]
        pay4 = m4.read(sheep.uexp)
        vals = {f.name: f.value for e in got.exports for f in um.place(e, pay4)}
        check("a missing property can be added", vals.get("AttackDamage") == 9,
              str(vals.get("AttackDamage")))
        check("edits after the insertion are shifted to match",
              vals.get("MaxHealth") == 25, str(vals.get("MaxHealth")))
        check("the payload grew by exactly the value size",
              len(pay4) == len(sp) + 4, f"{len(sp)} -> {len(pay4)}")
        total4 = sum(e.end - e.start for e in got.exports)
        check("exports still tile the payload after insertion",
              total4 == len(pay4) - 4, f"{total4} vs {len(pay4) - 4}")
        m4.close(); Path(tmp4).unlink()

    print("\nstarting decks:")
    rows = decks.card_rows(r)
    check("the card table's row names are readable", len(rows) > 200, f"{len(rows)} rows")
    commanders = [x for x in assets if x.class_name == "CMCommanderData"]
    parsed = {x.name: decks.parse(r, x) for x in commanders}
    check("every commander has two decks",
          all(len(v) == 2 for v in parsed.values()),
          str({k: len(v) for k, v in parsed.items()}))
    check("every deck holds ten cards",
          all(len(d) == 10 for v in parsed.values() for d in v))
    jeanne = parsed.get("DA_Commander_Jeanne")
    if jeanne:
        check("Jeanne's human deck reads correctly",
              [c.row for c in jeanne[0].cards].count("Summon_Militia") == 5
              and "Infra_HealFountain" in [c.row for c in jeanne[0].cards],
              str(sorted({c.row for c in jeanne[0].cards})))
    check("non-commander assets yield no decks",
          not any(decks.parse(r, x) for x in assets[:40]
                  if x.class_name != "CMCommanderData"))

    if jeanne:
        cmd = by["DA_Commander_Jeanne"]
        before_names = uasset.parse(r.read(cmd.uasset)).names
        newcard = next(x for x in rows if x not in before_names)
        proj3 = Project(name="DeckCheck")
        proj3.set_name_ref(cmd.path, jeanne[0].cards[0].offset, newcard)
        raw3 = proj3.build(r)
        with tempfile.NamedTemporaryFile(suffix=".pak", delete=False) as f3:
            f3.write(raw3); tmp3 = f3.name
        m3 = PakReader(tmp3)

        class _Shim:
            uasset = cmd.uasset
            uexp = cmd.uexp

        after3 = decks.parse(m3, _Shim())
        check("a deck slot can be set to an unused card",
              after3 and after3[0].cards[0].row == newcard,
              after3[0].cards[0].row if after3 else "no deck")
        check("the rest of the deck is untouched",
              after3 and [c.row for c in after3[0].cards[1:]]
              == [c.row for c in jeanne[0].cards[1:]])
        m3.close(); Path(tmp3).unlink()

    print("\nname table insertion:")
    data_assets = [p for p in r.files()
                   if p.endswith(".uasset") and p.startswith("Commander/Content/Data/")]
    ok = bad = 0
    for path in data_assets:
        raw0 = r.read(path)
        try:
            before0 = uasset.parse(raw0)
            grown = uasset.add_name(raw0, "CQMOD.SelfTest")
            after0 = uasset.parse(grown)
        except Exception:
            bad += 1
            continue
        if (after0.header_size == len(grown)
                and before0.names == after0.names[:-1]
                and after0.names[-1] == "CQMOD.SelfTest"
                and len(before0.exports) == len(after0.exports)
                and all(b.uexp_slice(before0.header_size) == a2.uexp_slice(after0.header_size)
                        for b, a2 in zip(before0.exports, after0.exports))):
            ok += 1
        else:
            bad += 1
    check("every data asset survives a name table insertion", bad == 0,
          f"{ok}/{len(data_assets)}")

    arch2 = by.get("DA_Unit_Human_Archer")
    if arch2 and um:
        pay = r.read(arch2.uexp)
        tag_off = None
        for e in arch2.exports:
            for f in um.place(e, pay):
                if um.is_tag_container(e, f.index):
                    t = um.tags(f, pay)
                    if t:
                        tag_off = t[0][0]
        if tag_off is not None:
            proj2 = Project(name="TagCheck")
            proj2.set_tag(arch2.path, tag_off, "Minion.Type.Cavalry")
            raw2 = proj2.build(r)
            with tempfile.NamedTemporaryFile(suffix=".pak", delete=False) as f2:
                f2.write(raw2); tmp2 = f2.name
            m2 = PakReader(tmp2)
            nh = m2.read(arch2.uasset); nx = m2.read(arch2.uexp)
            pk2 = uasset.parse(nh)
            idx2 = struct.unpack_from("<I", nx, tag_off)[0]
            check("a tag the asset never used can be set",
                  pk2.names[idx2] == "Minion.Type.Cavalry",
                  pk2.names[idx2] if idx2 < len(pk2.names) else "?")
            check("the grown header stays self-consistent",
                  pk2.header_size == len(nh) and len(nx) == len(pay))
            m2.close(); Path(tmp2).unlink()

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
