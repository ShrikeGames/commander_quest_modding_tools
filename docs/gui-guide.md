# GUI guide

```bash
python3 ui/app.py
```

The archive is opened and indexed on a background thread; the window is usable
after roughly a fifth of a second.

## Layout

**Left: asset type.** Every class found in the archive, with counts.
`CMCardData_Summon` (406), `CMCardData_Tactics` (113), `CMCardData_Supply` (83),
`CMCardData_Power` (51), `CMCardData_Curse` (13), plus `CMUnitData` (325),
`CMGearDefinition` (151), `CMCommanderData` (5) and many smaller ones.

**Centre: assets.** Name, title and category for the current filter. The search
box matches name, title and description text.

**Right: detail**, in four tabs.

## Text tab

Shows the asset's package path, class, category, localization keys and export
count, then editable title and description fields.

*Stage text change* records whichever fields you actually changed. Edits go to
the localization resource for one locale, so other languages keep the original
text.

Assets without string-table text have these fields disabled, since many internal
definitions have no display text at all.

## Art tab

Displays the card art, its dimensions and pixel format.

- *Replace art…* stages any image file. It is scaled to cover and centre-cropped
  to the original dimensions, preserving aspect ratio.
- *Export PNG…* writes the current art out, which is the easy way to get a base
  to paint over.

Staged replacements are noted under the preview. Assets whose art is not
`PF_B8G8R8A8` report why they cannot be shown rather than failing silently.

## Values tab

Every integer slot in the asset's export payloads: which export, its class, the
byte offset, and the current value.

Offsets advance **one byte at a time**, not four. Real property layouts
interleave smaller types, so a four-byte stride skips fields, including the one
that motivated the change: Insight's draw count at offset 119.

Most rows are meaningless. Use **Find fields vs '+' variant** to narrow them; see
[finding fields](finding-fields.md). Candidates are highlighted in green with the
variant's value alongside, and *Show only candidates* hides everything else.

Type into the **New value** column to stage an edit; clear the cell to remove it.
Staged rows are highlighted in the accent colour.

## Pending edits tab

Every staged change as a plain list, with a count on the tab label. *Clear all
edits* discards them.

## Toolbar

- **Open / Save Project**. Projects are small readable JSON files listing the
  staged edits, so a mod can be kept under version control and rebuilt later.
- **Build & Install Mod**. Compiles everything into
  `ZZZ_<name>_P.pak` in the game's `Content/Paks`, and reports what was written.

Restart the game to load a mod. To uninstall, delete that one file. Nothing else
is ever modified.
