#!/usr/bin/env python3
"""End-to-end checks against the real game pak.

Every check is self-validating: the pak stores a SHA-1 per entry and per index,
packages carry a magic and a header size, and locres/texture writers are checked
by round-tripping. Run this after changing anything in cqmod/.
"""
from __future__ import annotations
import io, os, struct, subprocess, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cqmod import (config, catalog, locres, texture, uasset, unversioned,
                   diff, mods, schema, usmap, artchain, decks, randomizer,
                   keyfinder, resources)
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

    print("\nencrypted config files:")
    enc = [(k, v) for k, v in r.entries.items() if v.encrypted]
    good = 0
    for path, ent in enc:
        try:
            if len(r.read(path)) == ent.uncompressed_size:
                good += 1
        except Exception:
            pass
    check("every per-entry encrypted file decodes", good == len(enc),
          f"{good}/{len(enc)}, all .ini")
    tags_ini = "Commander/Config/DefaultGameplayTags.ini"
    if tags_ini in r:
        text = r.read(tags_ini).decode("utf-8", "replace")
        registered = text.count('Tag="')
        check("the gameplay tag registry is readable", registered > 200,
              f"{registered} tags")

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
    tlf = by.get("DA_Gear_ThreeLionFlag")
    if tlf and um:
        tp = r.read(tlf.uexp)
        branch = next((f for e in tlf.exports for f in um.place(e, tp)
                       if f.name == "TargetUnitClassBranch"), None)
        check("an enum is editable and one byte wide",
              branch is not None and branch.editable and branch.size == 1,
              f"value {branch.value}" if branch else "not found")
        if branch:
            proj5 = Project(name="EnumCheck")
            proj5.set_value(tlf.path, branch.offset, 4, "TargetUnitClassBranch")
            raw5 = proj5.build(r)
            with tempfile.NamedTemporaryFile(suffix=".pak", delete=False) as f5:
                f5.write(raw5); tmp5 = f5.name
            m5 = PakReader(tmp5)
            g5 = catalog.build_one(m5, tlf.path)[0]
            p5 = m5.read(tlf.uexp)
            v5 = {f.name: f.value for e in g5.exports for f in um.place(e, p5)}
            check("an enum edit writes one byte, not four",
                  v5.get("TargetUnitClassBranch") == 4 and v5.get("BuffValue") == 3
                  and len(p5) == len(tp),
                  f"branch={v5.get('TargetUnitClassBranch')} buff={v5.get('BuffValue')}")
            m5.close(); Path(tmp5).unlink()

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

    print("\nrandomizer:")
    if um:
        every = {c.key: c.modes[0] for c in randomizer.CATEGORIES if c.key != "card_art"}
        st = randomizer.Settings(seed=99, choices=every)
        rp1 = Project(name="Rando")
        sm1 = randomizer.run(r, assets, um, st, rp1)
        check("every category produces edits", all(v > 0 for v in sm1.values()),
              str(sm1))

        rp2 = Project(name="Rando")
        sm2 = randomizer.run(r, assets, um,
                             randomizer.Settings(seed=99, choices=every), rp2)
        same = ([(v.asset_path, v.offset, v.value) for v in rp1.values]
                == [(v.asset_path, v.offset, v.value) for v in rp2.values])
        check("the same seed reproduces the same mod", same and sm1 == sm2)

        rp3 = Project(name="Rando")
        randomizer.run(r, assets, um,
                       randomizer.Settings(seed=100, choices=every), rp3)
        check("a different seed gives a different mod",
              [(v.asset_path, v.offset, v.value) for v in rp3.values]
              != [(v.asset_path, v.offset, v.value) for v in rp1.values])

        # The guard is about commander units, whose health decides whether a run
        # is winnable. Commander cards are ordinary cards and may be touched.
        # Match on the asset name, not the path: the game's content root is
        # itself called "Commander", so every pak path contains the word.
        touched_units = [v.asset_path for v in rp1.values
                         if Path(v.asset_path).name.startswith("DA_Unit_")
                         and "Commander" in Path(v.asset_path).name]
        check("commander units are left alone by default", not touched_units,
              str(touched_units[:2]))
        rp4 = Project(name="Rando")
        randomizer.run(r, assets, um,
                       randomizer.Settings(seed=99, choices=every,
                                           include_commanders=True), rp4)
        check("commander units are randomized when asked",
              any(Path(v.asset_path).name.startswith("DA_Unit_")
                  and "Commander" in Path(v.asset_path).name for v in rp4.values))

        gear_only = {"gear_values": "shuffle", "gear_rarity": "shuffle"}
        rp5 = Project(name="Relics")
        sm5 = randomizer.run(r, assets, um,
                             randomizer.Settings(seed=5, choices=gear_only), rp5)
        check("relics are randomized", all(v > 0 for v in sm5.values()), str(sm5))
        gear_names = {x.name for x in assets if x.class_name == "CMGearDefinition"}
        check("relic edits land on relic assets",
              all(Path(v.asset_path).name in gear_names for v in rp5.values))

        icons = randomizer.Settings(seed=5, choices={"gear_icons": "shuffle"})
        art = randomizer.Settings(seed=5, choices={"card_art": "shuffle"})
        icon_size = randomizer.estimate_bytes(r, assets, icons)
        art_size = randomizer.estimate_bytes(r, assets, art)
        # Shuffling art repoints each asset at an existing texture instead of
        # copying pixels, so the cost follows the number of assets touched and
        # not the size of the images. Copying card art used to run to roughly a
        # gigabyte, and the ceiling here guards against that coming back.
        check("shuffling art costs references, not images",
              0 < icon_size < art_size < 20 * 2**20,
              f"icons {icon_size / 2**20:.1f} MB, "
              f"card art {art_size / 2**20:.1f} MB")

        art_project = Project(name="Art")
        art_count = randomizer.run(r, assets, um, art, art_project)["card_art"]
        check("art shuffling reaches summon cards as well as supply cards",
              any("Summon" in x.asset_path for x in art_project.references)
              and any("Supply" in x.asset_path for x in art_project.references))
        with tempfile.NamedTemporaryFile(suffix=".pak", delete=False) as fa:
            fa.write(art_project.build(r))
            art_pak = fa.name
        ma = PakReader(art_pak)
        landed = 0
        for ref in art_project.references:
            owner = by[Path(ref.asset_path).name]
            index = struct.unpack_from("<i", ma.read(owner.uexp), ref.offset)[0]
            landed += (uasset.parse(ma.read(owner.uasset)).resolve(index)
                       == Path(ref.target).name)
        ma.close(); Path(art_pak).unlink()
        check("every repointed reference resolves to its new texture",
              landed == len(art_project.references) == art_count,
              f"{landed}/{len(art_project.references)}")

        # Range tiers are disjoint between melee and projectile attack types, so
        # randomizing must not move a unit between the two bands.
        raw6 = rp1.build(r)
        with tempfile.NamedTemporaryFile(suffix=".pak", delete=False) as f6:
            f6.write(raw6); tmp6 = f6.name
        m6 = PakReader(tmp6)
        bands = {}
        for x in assets:
            if x.class_name != "CMUnitData" or x.uexp not in m6:
                continue
            g6 = catalog.build_one(m6, x.path)[0]
            b6 = m6.read(x.uexp)
            for e in g6.exports:
                for f in um.place(e, b6):
                    if f.name == "AttackRangeStatus":
                        bands.setdefault(e.class_name, set()).add(f.value)
        melee = set().union(*[v for k, v in bands.items() if "Melee" in k] or [set()])
        ranged = set().union(*[v for k, v in bands.items() if "Project" in k] or [set()])
        check("melee and ranged keep their own range bands",
              melee and ranged and not (melee & ranged),
              f"melee {sorted(melee)}, ranged {sorted(ranged)}")
        m6.close(); Path(tmp6).unlink()

    print("\nquests and events:")
    quest_keys = ("quest_rarity", "quest_conditions", "quest_rewards")
    event_keys = ("event_values", "event_health", "event_gear_rarity")
    qe = {k: randomizer.SHUFFLE for k in quest_keys + event_keys}
    rp7 = Project(name="QuestsEvents")
    sm7 = randomizer.run(r, assets, um,
                         randomizer.Settings(seed=7, choices=qe), rp7)
    check("every quest and event category produces edits",
          all(sm7.get(k, 0) > 0 for k in quest_keys + event_keys), str(sm7))

    quest_names = {x.name for x in assets
                   if x.class_name == randomizer.QUEST_CLASS}
    event_names = {x.name for x in assets
                   if x.class_name == randomizer.EVENT_CLASS}
    touched = {Path(v.asset_path).name for v in rp7.values} \
        | {Path(t.asset_path).name for t in rp7.tags}
    check("edits land only on quest and event assets",
          touched <= quest_names | event_names,
          str(sorted(touched - quest_names - event_names)[:3]))

    # A talk box's IntValue is the page a line of dialogue belongs to, not an
    # amount. Rerolling it would send a conversation to the wrong line, so the
    # collector must never see it.
    talk = set()
    for a in (x for x in assets if x.class_name == randomizer.EVENT_CLASS):
        body7 = r.read(a.uexp)
        for e in a.exports:
            if not e.class_name.startswith("CMTalkBoxActionParameter"):
                continue
            for f in um.place(e, body7):
                if f.offset >= 0:
                    talk.add((a.name, f.offset))
    edited = {(Path(v.asset_path).name, v.offset) for v in rp7.values}
    check("dialogue page numbers are never randomized",
          talk and not (talk & edited), f"{len(talk)} talk box fields")

    # TakeAmount means consumables on one class and gold on another. Pooling
    # the two would have an event hand out three gold or three hundred potions.
    gold = randomizer._quantities(
        r, um, sorted((x for x in assets
                       if x.class_name == randomizer.EVENT_CLASS),
                      key=lambda a: a.name), "CMEventActionParameter")
    gold_pool = {v for _, _, v, _ in gold[
        ("CMEventActionParameter_TakeGold", "TakeAmount")]}
    consumable_pool = {v for _, _, v, _ in gold[
        ("CMEventActionParameter_TakeConsumable", "TakeAmount")]}
    check("gold and consumable amounts stay in separate pools",
          gold_pool and consumable_pool and not (gold_pool & consumable_pool),
          f"gold {sorted(gold_pool)}, consumables {sorted(consumable_pool)}")

    rewards = randomizer._reward_rows(
        r, sorted((x for x in assets
                   if x.class_name == randomizer.QUEST_CLASS),
                  key=lambda a: a.name))
    check("quest rewards are found for cards, relics and consumables",
          set(rewards) == {"DT_Cards", "DT_Gears", "DT_Consumables"},
          str(sorted(rewards)))
    pools = {t: {row for _, _, row in v} for t, v in rewards.items()}
    staged = {(Path(t.asset_path).name, t.offset): t.tag for t in rp7.tags}
    kinds_ok = True
    for table, entries in rewards.items():
        for a, off, _ in entries:
            new = staged.get((a.name, off))
            if new is not None and new not in pools[table]:
                kinds_ok = False
    check("a reward stays the kind of thing it was", kinds_ok)

    with tempfile.NamedTemporaryFile(suffix=".pak", delete=False) as f7:
        f7.write(rp7.build(r)); tmp7 = f7.name
    m7 = PakReader(tmp7)
    floats_ok = names_ok = 0
    float_edits = [v for v in rp7.values if v.is_float]
    for v in float_edits:
        a = by[Path(v.asset_path).name]
        got = struct.unpack_from("<f", m7.read(a.uexp), v.offset)[0]
        floats_ok += abs(got - v.value) < 1e-3
    check("health ratios are written as floats",
          float_edits and floats_ok == len(float_edits),
          f"{floats_ok}/{len(float_edits)}")
    # Consumable rows are keyed in Korean, so a shuffled reward usually needs a
    # name the quest has never carried, in the wide FString form.
    wide = [t for t in rp7.tags if any(ord(c) > 127 for c in t.tag)]
    for t in rp7.tags:
        a = by[Path(t.asset_path).name]
        index = struct.unpack_from("<I", m7.read(a.uexp), t.offset)[0]
        after7 = uasset.parse(m7.read(a.uasset))
        names_ok += (index < len(after7.names)
                     and after7.names[index] == t.tag)
    m7.close(); Path(tmp7).unlink()
    check("every shuffled reward resolves to its new row",
          names_ok == len(rp7.tags), f"{names_ok}/{len(rp7.tags)}")
    check("rewards keyed in Korean survive the round trip", len(wide) > 0,
          f"{len(wide)} non-ASCII rows")

    print("\nunit models:")
    visuals = randomizer.unit_visuals(r)
    check("unit blueprints yield swappable appearances",
          len(visuals) > 200, f"{len(visuals)} of 234 blueprints")
    check("a mesh is referenced twice, as SkeletalMesh and SkinnedAsset",
          all(len(v.mesh[2]) >= 2 for v in visuals),
          f"{sum(1 for v in visuals if len(v.mesh[2]) < 2)} with fewer")
    check("every appearance resolves to a /Game package",
          all(v.mesh[0].startswith("/Game/") for v in visuals))

    rp8 = Project(name="Models")
    sm8 = randomizer.run(r, assets, um,
                         randomizer.Settings(seed=5,
                                             choices={"unit_models": "shuffle"}),
                         rp8)
    check("model shuffling reskins most units", sm8["unit_models"] > 180,
          f"{sm8['unit_models']} of {len(visuals)}")
    check("model edits touch only unit blueprints",
          all(x.asset_path.startswith(randomizer.UNIT_BLUEPRINT_DIR)
              for x in rp8.references))

    # An appearance is only coherent as a package. The animation blueprint is
    # built against one skeleton and the material overrides are written for one
    # mesh's slots, so a unit must take all three from the same donor or it
    # animates against the wrong skeleton wearing another creature's textures.
    by_path = {v.path: v for v in visuals}
    real = {(v.mesh[1], v.anim[1] if v.anim else None,
             tuple(x[1] for x in v.materials)) for v in visuals}
    staged = {}
    for x in rp8.references:
        staged.setdefault(x.asset_path, {})[x.offset] = x.object_name
    coherent = 0
    for path, edits in staged.items():
        v = by_path[path]
        mesh = edits.get(v.mesh[2][0], v.mesh[1])
        anim = edits.get(v.anim[2][0], v.anim[1]) if v.anim else None
        mats = tuple(edits.get(x[2][0], x[1]) for x in v.materials)
        coherent += (mesh, anim, mats) in real
    check("a reskinned unit wears one donor's whole appearance",
          coherent == len(staged), f"{coherent}/{len(staged)}")

    with tempfile.NamedTemporaryFile(suffix=".pak", delete=False) as f8:
        f8.write(rp8.build(r)); tmp8 = f8.name
    m8 = PakReader(tmp8)
    landed = 0
    for x in rp8.references:
        pk8 = uasset.parse(m8.read(x.asset_path + ".uasset"))
        index = struct.unpack_from(
            "<i", m8.read(x.asset_path + ".uexp"), x.offset)[0]
        landed += pk8.resolve(index) == x.object_name
    m8.close(); Path(tmp8).unlink()
    check("every model reference resolves to its new object",
          landed == len(rp8.references), f"{landed}/{len(rp8.references)}")

    # A generated class is not named after its package: the animation blueprint
    # in /Game/.../ABP_Human_Assassin holds a class called ABP_Human_Assassin_C.
    probe8 = r.read(next(f for f in r.files()
                         if f.endswith("BP_Unit_Assassin.uasset")))
    grown8, index8 = uasset.add_asset_reference(
        probe8, "/Game/SelfTest/ABP_Probe", "AnimBlueprintGeneratedClass",
        object_name="ABP_Probe_C")
    check("a reference can name an object its package does not",
          uasset.parse(grown8).resolve(index8) == "ABP_Probe_C")

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

    print("\nimport table insertion:")
    grown_ok = grown_bad = 0
    for path in data_assets:
        raw3 = r.read(path)
        try:
            before3 = uasset.parse(raw3)
            out3, index3 = uasset.add_asset_reference(
                raw3, "/Game/SelfTest/T_Probe", "Texture2D")
            after3 = uasset.parse(out3)
        except Exception:
            grown_bad += 1
            continue
        if (after3.resolve(index3) == "T_Probe"
                and len(after3.imports) == len(before3.imports) + 2
                and after3.header_size == len(out3)
                and len(after3.exports) == len(before3.exports)
                and all(b.uexp_slice(before3.header_size)
                        == a3.uexp_slice(after3.header_size)
                        for b, a3 in zip(before3.exports, after3.exports))):
            grown_ok += 1
        else:
            grown_bad += 1
    check("every data asset survives an import table insertion",
          grown_bad == 0, f"{grown_ok}/{len(data_assets)}")

    raw4 = r.read(data_assets[0])
    out4, index4 = uasset.add_asset_reference(
        raw4, "/Game/SelfTest/T_Probe", "Texture2D")
    after4 = uasset.parse(out4)
    check("the new import is the last one in the table",
          index4 == -len(after4.imports))
    check("its outer is the package that contains it",
          after4.resolve(after4.imports[-1].outer_index)
          == "/Game/SelfTest/T_Probe")
    # Asking twice for the same asset must reuse both entries, or a package
    # repointed at one texture from several properties would collect duplicates.
    out5, index5 = uasset.add_asset_reference(
        out4, "/Game/SelfTest/T_Probe", "Texture2D")
    check("asking for the same asset twice reuses its imports",
          out5 == out4 and index5 == index4)
    # A different asset is a different package, so it costs a fresh pair.
    out6, index6 = uasset.add_asset_reference(
        out4, "/Game/SelfTest/T_Other", "Texture2D")
    check("a different asset gets its own package and object entries",
          len(uasset.parse(out6).imports) == len(after4.imports) + 2
          and uasset.parse(out6).resolve(index6) == "T_Other")

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

    print("\npackaging:")
    check("the schema is found where the app looks for it",
          resources.usmap_path().is_file(), str(resources.usmap_path()))
    check("settings persist outside the bundle",
          config.LOCAL_CONFIG.parent == resources.app_dir())
    check("a game folder is recognised by its archive",
          config.is_game_dir(config.game_dir())
          and not config.is_game_dir(Path(__file__).parent))

    ooz_lib = resources.ooz_library()
    check("the Oodle decoder is built and discoverable",
          ooz_lib is not None and ooz_lib.is_file(),
          str(ooz_lib) if ooz_lib else "run tools/build_native.py")
    if ooz_lib:
        # The wrapper's unmangled name is what lets one prebuilt library work
        # everywhere; the C++ symbol's mangling encodes the size_t width.
        symbols = subprocess.run(["nm", "-D", str(ooz_lib)],
                                 capture_output=True, text=True).stdout
        check("it exports a portable entry point",
              "ooz_kraken_decompress" in symbols or not symbols,
              "nm unavailable" if not symbols else "symbol present")

    scanner = resources.aes_finder()
    check("the key scanner is built and discoverable",
          scanner is not None and scanner.is_file(),
          str(scanner) if scanner else "run tools/build_native.py")

    offset, index_size, want_sha1 = keyfinder.pak_index_info(config.pak_path())
    check("the pak footer gives up its index and hash",
          index_size > 0 and len(want_sha1) == 20,
          f"index {index_size:,} bytes at {offset:#x}")
    with open(config.pak_path(), "rb") as f:
        f.seek(offset)
        encrypted_index = f.read(index_size)
    real_key = config.aes_key()
    check("the real key is confirmed against the index hash",
          keyfinder.confirms(real_key, encrypted_index, want_sha1))
    check("a wrong key is rejected",
          not keyfinder.confirms(bytes(32), encrypted_index, want_sha1))

    # The scanner normally reads a live game. Pointing it at a file with a key
    # planted in it exercises the same AES and the same filter without needing
    # the game to be running, which is what makes it testable at all.
    if scanner:
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f9:
            blob = bytearray(os.urandom(4 << 20))
            blob[1_000_003:1_000_035] = real_key   # deliberately unaligned
            f9.write(blob); dump = f9.name
        found = subprocess.run(
            [str(scanner), "--file", dump, "--block",
             encrypted_index[:16].hex().upper()],
            capture_output=True, text=True)
        Path(dump).unlink()
        reported = [l.split("=", 1)[1].split()[0]
                    for l in found.stdout.splitlines()
                    if l.startswith("CANDIDATE=")]
        check("the scanner finds a key planted in a buffer",
              real_key.hex().upper() in reported,
              f"{len(reported)} candidates from 4 MB")
        confirmed = [k for k in reported
                     if keyfinder.confirms(bytes.fromhex(k), encrypted_index,
                                           want_sha1)]
        check("only the real key survives confirmation",
              confirmed == [real_key.hex().upper()],
              f"{len(confirmed)} of {len(reported)} confirmed")

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
