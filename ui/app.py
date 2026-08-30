#!/usr/bin/env python3
"""Commander Quest mod tool - browse and edit the game's data assets."""
from __future__ import annotations
import struct, sys, traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt, QSize, QThread, QUrl, Signal
from PySide6.QtGui import (QAction, QDesktopServices, QImage, QPixmap,
                           QKeySequence, QColor)
from PySide6.QtWidgets import (
    QCheckBox, QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QListWidgetItem, QTableWidget, QTableWidgetItem, QLineEdit,
    QLabel, QPushButton, QTabWidget, QTextEdit, QPlainTextEdit, QFileDialog,
    QMessageBox, QHeaderView, QAbstractItemView, QComboBox, QGroupBox,
    QFormLayout, QStatusBar, QProgressDialog, QToolBar, QSizePolicy, QComboBox,
    QInputDialog,
)

from datetime import datetime

from cqmod import config, catalog, texture, locres, uasset, diff, mods, usmap
from cqmod.mods import ModError, ModInfo, ModManager, display_name
from cqmod.pak import PakReader
from cqmod.project import Project

ACCENT = "#B03A0B"


class LoadThread(QThread):
    """Opens the pak and builds the catalog off the UI thread.

    Emits :attr:`done` with ``(reader, assets, error)``; on failure the first
    two are None and the third carries a traceback for display.
    """
    done = Signal(object, object, str)

    def run(self):
        """Open the archive and index it, emitting the result.

        Exceptions are captured and forwarded rather than raised, since an
        exception escaping a Qt thread would terminate the process.
        """
        try:
            reader = PakReader(config.pak_path(), config.aes_key())
            assets = catalog.build(reader)
            try:
                # Sizing struct and array properties needs the whole catalog, so
                # it happens here rather than blocking the first selection.
                usmap.Usmap.load().solve_sizes(reader, assets)
            except Exception:
                pass
            self.done.emit(reader, assets, "")
        except Exception as e:
            self.done.emit(None, None, f"{e}\n\n{traceback.format_exc()}")


class MainWindow(QMainWindow):
    """The main editor window.

    Left to right: a class filter, the asset list, and a detail pane with tabs
    for text, art, raw values and staged edits. Edits accumulate in a
    :class:`cqmod.project.Project` until *Build & Install* compiles them.
    """
    def __init__(self):
        """Build the window and start loading the archive in the background.
        """
        super().__init__()
        self.setWindowTitle("Commander Quest Mod Tool")
        self.resize(1450, 900)
        self.reader = None
        self.assets = []
        self.filtered = []
        self.current: catalog.Asset | None = None
        self.project = Project()
        self.project_path: Path | None = None
        self.mods = ModManager(config.paks_dir(), config.mods_dir(), config.pak_path())
        self.usmap = None
        self.all_tags = []
        self.art_paths = []
        self._payload = b""

        self._build_ui()
        self._start_load()

    # ---------------------------------------------------------------- UI
    def _build_ui(self):
        """Construct the toolbar, filter list, asset table and detail pane.
        """
        tb = QToolBar("Main")
        tb.setMovable(False)
        self.addToolBar(tb)
        self.search = QLineEdit(placeholderText="Search name, title or description...")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._refilter)
        self.search.setMaximumWidth(420)
        tb.addWidget(self.search)
        tb.addSeparator()
        for text, slot in (("Open Project", self.open_project),
                           ("Save Project", self.save_project)):
            a = QAction(text, self); a.triggered.connect(slot); tb.addAction(a)
        tb.addSeparator()
        spacer = QWidget(); spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        self.build_btn = QPushButton("Build && Install Mod")
        self.build_btn.setStyleSheet(
            "QPushButton{background:%s;color:white;padding:6px 16px;"
            "font-weight:600;border-radius:3px}"
            "QPushButton:disabled{background:#888}" % ACCENT)
        self.build_btn.clicked.connect(self.build_and_install)
        tb.addWidget(self.build_btn)

        split = QSplitter(Qt.Horizontal)

        self.classes = QListWidget()
        self.classes.currentItemChanged.connect(self._refilter)
        self.classes.setMinimumWidth(170)
        classes_box = self._boxed("Asset type", self.classes)
        classes_box.setMaximumWidth(320)
        split.addWidget(classes_box)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Name", "Title", "Category"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._select_asset)
        assets_box = self._boxed("Assets", self.table)
        assets_box.setMinimumWidth(380)
        split.addWidget(assets_box)

        detail = self._detail_panel()
        detail.setMinimumWidth(420)
        split.addWidget(detail)

        for i in range(3):
            split.setCollapsible(i, False)
        # The filter list keeps its width; the asset table and the detail pane
        # share whatever the window gains.
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 3)
        split.setStretchFactor(2, 4)
        split.setSizes([210, 520, 720])

        self.main_tabs = QTabWidget()
        self.main_tabs.addTab(split, "Edit assets")
        self.main_tabs.addTab(self._mods_panel(), "Mods")
        self.main_tabs.currentChanged.connect(
            lambda i: self._refresh_mods() if i == 1 else None)
        self.setCentralWidget(self.main_tabs)
        self.setStatusBar(QStatusBar())

    @staticmethod
    def _boxed(title, w):
        """Wrap a widget in a titled group box.

        Args:
            title (str): Caption for the box.
            w (QWidget): Widget to wrap.

        Returns:
            QGroupBox: The wrapped widget.
        """
        g = QGroupBox(title); lay = QVBoxLayout(g)
        lay.setContentsMargins(6, 6, 6, 6); lay.addWidget(w)
        return g

    def _detail_panel(self):
        """Build the tabbed detail pane.

        Returns:
            QTabWidget: Tabs for text, art, raw values and staged edits.
        """
        self.tabs = QTabWidget()

        # --- Overview / text ---
        page = QWidget(); lay = QVBoxLayout(page)
        self.info = QLabel("Select an asset"); self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.info)
        self.links_box = QGroupBox("Linked assets")
        self.links_layout = QVBoxLayout(self.links_box)
        self.links_layout.setContentsMargins(6, 6, 6, 6)
        self.links_box.setVisible(False)
        lay.addWidget(self.links_box)
        form = QFormLayout()
        self.title_edit = QLineEdit(); self.title_edit.setEnabled(False)
        self.desc_edit = QPlainTextEdit(); self.desc_edit.setEnabled(False)
        self.desc_edit.setMaximumHeight(130)
        form.addRow("Title", self.title_edit)
        form.addRow("Description", self.desc_edit)
        lay.addLayout(form)
        row = QHBoxLayout()
        self.apply_text_btn = QPushButton("Stage text change")
        self.apply_text_btn.clicked.connect(self._stage_text)
        self.apply_text_btn.setEnabled(False)
        row.addWidget(self.apply_text_btn); row.addStretch()
        lay.addLayout(row)
        lay.addStretch()
        self.tabs.addTab(page, "Text")

        # --- Art ---
        page = QWidget(); lay = QVBoxLayout(page)
        self.art = QLabel(alignment=Qt.AlignCenter)
        self.art.setMinimumHeight(360)
        self.art.setStyleSheet("border:1px solid #555;")
        lay.addWidget(self.art)
        self.art_info = QLabel(""); lay.addWidget(self.art_info)
        row = QHBoxLayout()
        self.replace_art_btn = QPushButton("Replace art...")
        self.replace_art_btn.clicked.connect(self._replace_art)
        self.replace_art_btn.setEnabled(False)
        self.copy_art_btn = QPushButton("Copy from game art...")
        self.copy_art_btn.setToolTip(
            "Use another texture from the game. It is re-encoded to this "
            "texture's size and format, so the two need not match.")
        self.copy_art_btn.clicked.connect(self._copy_art)
        self.copy_art_btn.setEnabled(False)
        self.export_art_btn = QPushButton("Export PNG...")
        self.export_art_btn.clicked.connect(self._export_art)
        self.export_art_btn.setEnabled(False)
        row.addWidget(self.replace_art_btn); row.addWidget(self.copy_art_btn)
        row.addWidget(self.export_art_btn); row.addStretch()
        lay.addLayout(row)
        self.tabs.addTab(page, "Art")

        # --- Values ---
        page = QWidget(); lay = QVBoxLayout(page)
        self.values_hint = QLabel()
        self.values_hint.setWordWrap(True)
        lay.addWidget(self.values_hint)
        row = QHBoxLayout()
        self.find_btn = QPushButton("Find fields vs '+' variant")
        self.find_btn.setToolTip(
            "Diff this asset against its upgraded '+' variant. Values that differ "
            "are almost always the gameplay numbers.")
        self.find_btn.clicked.connect(self._find_fields)
        self.find_btn.setEnabled(False)
        self.raw_mode = QCheckBox("Raw bytes")
        self.raw_mode.setToolTip(
            "List every byte offset interpreted as an integer, instead of named "
            "properties. Useful where the schema cannot place a property.")
        self.raw_mode.toggled.connect(lambda _: self.current and self._load_values(self.current))
        row.addWidget(self.raw_mode)
        self.only_cand = QCheckBox("Show only candidates")
        self.only_cand.toggled.connect(lambda _: self.current and self._load_values(self.current))
        self.only_cand.setEnabled(False)
        row.addWidget(self.find_btn); row.addWidget(self.only_cand); row.addStretch()
        lay.addLayout(row)
        self.values = QTableWidget(0, 6)
        self.values.setHorizontalHeaderLabels(
            ["Export", "Property", "Type", "Offset", "Value", "New value"])
        self.values.verticalHeader().setVisible(False)
        self.values.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.values.itemChanged.connect(self._value_changed)
        lay.addWidget(self.values)
        self.tabs.addTab(page, "Values")

        # --- Pending edits ---
        page = QWidget(); lay = QVBoxLayout(page)
        self.edits = QPlainTextEdit(readOnly=True)
        lay.addWidget(self.edits)
        row = QHBoxLayout()
        b = QPushButton("Clear all edits"); b.clicked.connect(self._clear_edits)
        row.addWidget(b); row.addStretch(); lay.addLayout(row)
        self.tabs.addTab(page, "Pending edits")
        return self.tabs

    def _mods_panel(self):
        """Build the mod manager tab.

        Returns:
            QWidget: A table of installed mods over a row of actions.
        """
        page = QWidget()
        lay = QVBoxLayout(page)
        intro = QLabel(
            "The game loads every pak in its Paks folder, so a mod is <b>enabled</b> "
            "when its file lives there and <b>disabled</b> when it is held in the "
            "staging folder. Toggling moves the file between the two.")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        self.mods_table = QTableWidget(0, 6)
        self.mods_table.setHorizontalHeaderLabels(
            ["Enabled", "Mod", "Assets", "Size", "Built", "File"])
        self.mods_table.verticalHeader().setVisible(False)
        self.mods_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.mods_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.mods_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.mods_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.mods_table.itemChanged.connect(self._mod_toggled)
        lay.addWidget(self.mods_table)

        self.mods_hint = QLabel("")
        self.mods_hint.setWordWrap(True)
        lay.addWidget(self.mods_hint)

        row = QHBoxLayout()
        for text, slot in (("Refresh", self._refresh_mods),
                           ("Import pak...", self._import_mod),
                           ("Delete", self._delete_mod),
                           ("Open staging folder", self._open_staging)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        return page

    def _refresh_mods(self):
        """Rescan both directories and repopulate the mod table."""
        try:
            found = self.mods.list()
        except Exception as e:
            self.mods_hint.setText(f"Could not list mods: {e}")
            return
        self._mod_rows = found
        t = self.mods_table
        t.blockSignals(True)
        t.setRowCount(len(found))
        for r, m in enumerate(found):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            chk.setCheckState(Qt.Checked if m.enabled else Qt.Unchecked)
            chk.setData(Qt.UserRole, r)
            t.setItem(r, 0, chk)
            cells = [m.name,
                     "-" if m.file_count is None else str(m.file_count),
                     f"{m.size:,}",
                     m.modified.strftime("%Y-%m-%d %H:%M"),
                     m.filename]
            for c, text in enumerate(cells, start=1):
                it = QTableWidgetItem(text)
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                if m.error:
                    it.setForeground(QColor("#b04a4a"))
                t.setItem(r, c, it)
        t.resizeColumnsToContents()
        t.blockSignals(False)

        problems = [m for m in found if m.error or not m.is_patch_pak]
        if problems:
            bits = []
            for m in problems:
                if m.error:
                    bits.append(f"{m.filename} could not be read ({m.error})")
                else:
                    bits.append(f"{m.filename} does not end in _P.pak, so it will not "
                                "override base game assets")
            self.mods_hint.setText("Warning: " + "; ".join(bits))
        else:
            n = sum(1 for m in found if m.enabled)
            self.mods_hint.setText(
                f"{len(found)} mod(s), {n} enabled. Restart the game after changing this."
                if found else
                f"No mods yet. Build one from the Edit assets tab, or import a pak. "
                f"Staging folder: {self.mods.staging_dir}")

    def _mod_toggled(self, item):
        """Enable or disable a mod when its checkbox changes.

        Args:
            item (QTableWidgetItem): The checkbox cell that changed.
        """
        if item.column() != 0:
            return
        mod = self._mod_rows[item.data(Qt.UserRole)]
        want = item.checkState() == Qt.Checked
        if want == mod.enabled:
            return
        try:
            self.mods.set_enabled(mod, want)
        except ModError as e:
            QMessageBox.warning(self, "Could not change mod state", str(e))
        self._refresh_mods()
        self.statusBar().showMessage(
            f"{mod.name} {'enabled' if want else 'disabled'}; restart the game", 8000)

    def _selected_mod(self):
        """The mod highlighted in the table.

        Returns:
            ModInfo | None: The selection, or None if nothing is selected.
        """
        rows = self.mods_table.selectionModel().selectedRows()
        return self._mod_rows[rows[0].row()] if rows else None

    def _import_mod(self):
        """Copy an external pak into staging after validating it."""
        p, _ = QFileDialog.getOpenFileName(self, "Import a mod pak", "", "Pak (*.pak)")
        if not p:
            return
        try:
            m = self.mods.import_pak(p)
        except ModError as e:
            QMessageBox.warning(self, "Import failed", str(e))
            return
        self._refresh_mods()
        self.statusBar().showMessage(f"imported {m.filename} (disabled)", 8000)

    def _delete_mod(self):
        """Permanently delete the selected mod after confirmation."""
        mod = self._selected_mod()
        if not mod:
            QMessageBox.information(self, "No mod selected", "Select a mod first.")
            return
        if QMessageBox.question(
                self, "Delete mod",
                f"Permanently delete {mod.filename}?\n\n{mod.path}") != QMessageBox.Yes:
            return
        try:
            self.mods.delete(mod)
        except ModError as e:
            QMessageBox.warning(self, "Delete failed", str(e))
        self._refresh_mods()

    def _open_staging(self):
        """Open the staging folder in the desktop file manager."""
        self.mods.staging_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.mods.staging_dir)))

    # ------------------------------------------------------------- load
    def _start_load(self):
        """Kick off background loading of the pak and catalog.
        """
        self.build_btn.setEnabled(False)
        self.statusBar().showMessage("Opening pak and indexing assets...")
        self._loader = LoadThread()
        self._loader.done.connect(self._loaded)
        self._loader.start()

    def _loaded(self, reader, assets, err):
        """Populate the window once loading finishes.

        Args:
            reader (cqmod.pak.PakReader | None): The open archive, or None on failure.
            assets (list | None): The catalog, or None on failure.
            err (str): Error text; empty on success.
        """
        if err:
            QMessageBox.critical(self, "Could not open the game pak", err)
            self.statusBar().showMessage("Load failed")
            return
        self.reader, self.assets = reader, assets
        try:
            self.usmap = usmap.Usmap.load()
            n = self.usmap.solve_sizes(reader, assets)
            self.all_tags = self.usmap.collect_tags(reader, assets)
            self.art_paths = sorted(
                p[:-5] for p in reader.files()
                if p.endswith(".uexp") and Path(p).name.startswith("T_"))
            self.statusBar().showMessage(
                f"resolved {n} struct/array sizes, {len(self.all_tags)} gameplay tags", 6000)
        except usmap.UsmapError:
            self.usmap = None
        classes = sorted({a.class_name for a in assets if a.class_name})
        self.classes.addItem(f"All ({len(assets)})")
        for c in classes:
            n = sum(1 for a in assets if a.class_name == c)
            self.classes.addItem(f"{c} ({n})")
        self.classes.setCurrentRow(0)
        self.build_btn.setEnabled(True)
        self.statusBar().showMessage(
            f"{len(assets)} data assets from {len(reader)} pak files", 8000)
        self._refilter()

    # ----------------------------------------------------------- filter
    def _refilter(self):
        """Re-apply the search box and class filter to the asset table.
        """
        if not self.assets:
            return
        q = self.search.text().strip().lower()
        item = self.classes.currentItem()
        cls = None
        if item and not item.text().startswith("All ("):
            cls = item.text().rsplit(" (", 1)[0]
        out = []
        for a in self.assets:
            if cls and a.class_name != cls:
                continue
            if q and q not in a.name.lower() and q not in (a.title or "").lower() \
               and q not in (a.description or "").lower():
                continue
            out.append(a)
        self.filtered = out
        self.table.setRowCount(len(out))
        for r, a in enumerate(out):
            self.table.setItem(r, 0, QTableWidgetItem(a.name))
            self.table.setItem(r, 1, QTableWidgetItem(a.title))
            self.table.setItem(r, 2, QTableWidgetItem(a.category))
        self.statusBar().showMessage(f"{len(out)} assets")

    # ----------------------------------------------------------- detail
    def _select_asset(self):
        """Show the selected asset across every detail tab.
        """
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        a = self.filtered[rows[0].row()]
        self.current = a
        self.info.setText(
            f"<b style='font-size:15px'>{a.title or a.name}</b><br>"
            f"<span style='color:#888'>{a.package}</span><br><br>"
            f"<b>Class</b> {a.class_name}<br><b>Category</b> {a.category}<br>"
            f"<b>Text keys</b> {a.namespace}/{a.title_key or '-'} , {a.desc_key or '-'}<br>"
            f"<b>Exports</b> {len(a.exports)}")
        self.title_edit.setText(a.title)
        self.desc_edit.setPlainText(a.description)
        has_text = bool(a.namespace and (a.title_key or a.desc_key))
        for w in (self.title_edit, self.desc_edit, self.apply_text_btn):
            w.setEnabled(has_text)
        self._candidates = {}
        self.only_cand.setChecked(False)
        self.only_cand.setEnabled(False)
        self.find_btn.setEnabled(bool(self._variant_of(a)))
        self._load_values(a)
        self._load_links(a)
        self._load_art(a)

    def _load_links(self, a):
        """Offer buttons for the assets this one references.

        A summon card names its unit through an object reference, and the unit
        is where attack and health live, so following the link is quicker than
        searching for the DA_Unit_* asset by hand.

        Args:
            a (cqmod.catalog.Asset): The selected asset.
        """
        while self.links_layout.count():
            w = self.links_layout.takeAt(0).widget()
            if w:
                w.deleteLater()
        found = []
        if self.usmap and getattr(self, "_pkg", None):
            index = {x.name: x for x in self.assets}
            for prop, target in self.usmap.links(a, self._pkg, self._payload):
                hit = index.get(target) or index.get(target.removesuffix("_C"))
                if hit and hit.name != a.name:
                    found.append((prop, hit))
        seen = set()
        for prop, hit in found:
            if hit.name in seen:
                continue
            seen.add(hit.name)
            b = QPushButton(f"{prop}:  {hit.title or hit.name}")
            b.setToolTip(f"{hit.name}\n{hit.class_name}")
            b.clicked.connect(lambda _, n=hit.name: self._goto_asset(n))
            self.links_layout.addWidget(b)
        self.links_box.setVisible(bool(seen))

    def _goto_asset(self, name):
        """Select another asset by name, widening the filter if needed.

        Args:
            name (str): Asset name to jump to.
        """
        if not any(x.name == name for x in self.filtered):
            self.classes.setCurrentRow(0)
            self.search.setText(name)
            self._refilter()
        for row, x in enumerate(self.filtered):
            if x.name == name:
                self.table.selectRow(row)
                self.table.scrollToItem(self.table.item(row, 0))
                return

    def _load_art(self, a):
        """Show an asset's art, or why it cannot be shown.

        Args:
            a (cqmod.catalog.Asset): The selected asset.
        """
        self.art.clear(); self.art_info.setText("")
        self.replace_art_btn.setEnabled(False); self.export_art_btn.setEnabled(False)
        self.copy_art_btn.setEnabled(False)
        if not a.texture or (a.texture + ".uexp") not in self.reader:
            self.art.setText("no art"); return
        try:
            bulk_path = a.texture + ".ubulk"
            bulk = self.reader.read(bulk_path) if bulk_path in self.reader else b""
            tex = texture.parse(self.reader.read(a.texture + ".uexp"), bulk)
            img = texture.to_image(tex)
            self._art_image = img
            data = img.tobytes("raw", "RGBA")
            qi = QImage(data, img.width, img.height, QImage.Format_RGBA8888)
            self.art.setPixmap(QPixmap.fromImage(qi).scaled(
                QSize(430, 430), Qt.KeepAspectRatio, Qt.SmoothTransformation))
            edit = next((t for t in self.project.textures
                         if t.texture_path == a.texture), None)
            pending = (edit.image_path or edit.source_texture) if edit else None
            self.art_info.setText(
                f"{tex.width}x{tex.height} {tex.pixel_format}"
                + (f", {len(tex.mips)} mips" if tex.is_block else "")
                + (f"   <b style='color:{ACCENT}'>staged: {Path(pending).name}</b>" if pending else ""))
            self.replace_art_btn.setEnabled(True); self.export_art_btn.setEnabled(True)
            self.copy_art_btn.setEnabled(True)
        except Exception as e:
            self.art.setText(f"cannot display art:\n{e}")

    def _load_values(self, a):
        """Show the asset's property values.

        With the recovered schema each row is a named property placed at its
        real offset. Without it, or in raw mode, the table falls back to listing
        every byte offset interpreted as an integer, stepping one byte at a time
        because property layouts interleave sizes and a four-byte stride skips
        real fields.

        Args:
            a (cqmod.catalog.Asset): The selected asset.
        """
        self.values.blockSignals(True)
        self.values.setRowCount(0)
        try:
            self._payload = self.reader.read(a.uexp)
        except Exception:
            self._payload = b""

        try:
            self._pkg = uasset.parse(self.reader.read(a.uasset))
            self._names = self._pkg.names
        except Exception:
            self._pkg, self._names = None, []

        named = bool(self.usmap) and not self.raw_mode.isChecked()
        rows = []
        if named:
            for e in a.exports:
                for f in self.usmap.place(e, self._payload):
                    rows.append((e.index, f.name, f.type, f.offset, f.value,
                                 f.editable, None))
                    if self.usmap.is_tag_container(e, f.index):
                        for n, (off, nidx) in enumerate(
                                self.usmap.tags(f, self._payload)):
                            label = (self._names[nidx] if nidx < len(self._names)
                                     else f"<name {nidx}>")
                            rows.append((e.index, f"    tag[{n}]", "GameplayTag",
                                         off, label, False, nidx))
            if not rows:
                named = False
        if not named:
            for e in a.exports:
                start = e.start + e.header_bytes
                end = min(e.end, len(self._payload))
                for off in range(start, max(start, end - 3)):
                    (v,) = struct.unpack_from("<i", self._payload, off)
                    rows.append((e.index, e.class_name, "", off, v, True, None))

        if self.usmap is None:
            self.values_hint.setText(
                "No property schema loaded, so values are addressed by byte offset. "
                "Launch the game and run <tt>tools/usmap/dump.py</tt> to recover "
                "property names.")
        elif named:
            self.values_hint.setText(
                "Named properties recovered from the game's own reflection data. "
                "Rows marked <i>zero</i> are stored in the header bitmap and occupy "
                "no bytes. Gameplay tags can be swapped for any other name the "
                "asset already references; adding a brand new tag would need the "
                "package name table rebuilt.")
        else:
            self.values_hint.setText(
                "Raw byte offsets, each read as a 32-bit integer. Offsets advance one "
                "byte at a time because property layouts interleave sizes.")

        cand = getattr(self, "_candidates", {})
        if cand and self.only_cand.isChecked():
            rows = [r for r in rows if r[3] in cand]
        staged = {v.offset: v.value for v in self.project.values if v.asset_path == a.path}
        staged_tags = {t.offset: t.tag for t in self.project.tags if t.asset_path == a.path}
        self.values.setRowCount(len(rows))
        for r, (ei, name, typ, off, val, editable, tag_index) in enumerate(rows):
            cells = [f"+{ei}", name, typ,
                     str(off) if off >= 0 else "zero",
                     "" if val is None else str(val)]
            for c, text in enumerate(cells):
                it = QTableWidgetItem(text)
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                self.values.setItem(r, c, it)
            if off in cand:
                self.values.item(r, 4).setText(f"{val}   (variant: {cand[off]})")
                for c in range(5):
                    self.values.item(r, c).setBackground(QColor("#2d4f1e"))
                    self.values.item(r, c).setForeground(QColor("#d9f2c8"))
            if tag_index is not None:
                # Tags are FName references, so the choices are the names this
                # package already carries.
                combo = QComboBox()
                choices = list(self.all_tags)
                current_tag = staged_tags.get(off) or (
                    self._names[tag_index] if tag_index < len(self._names) else "")
                if current_tag and current_tag not in choices:
                    choices.insert(0, current_tag)
                combo.addItems(choices)
                if current_tag in choices:
                    combo.setCurrentIndex(choices.index(current_tag))
                combo.currentTextChanged.connect(
                    lambda text, o=off, nm=name: self._tag_changed(o, text, nm))
                self.values.setCellWidget(r, 5, combo)
                continue
            new = QTableWidgetItem("" if off not in staged else str(staged[off]))
            new.setData(Qt.UserRole, off)
            if not editable or off < 0:
                new.setFlags(new.flags() & ~Qt.ItemIsEditable)
            if off in staged:
                new.setBackground(QColor(ACCENT))
                new.setForeground(QColor("white"))
            self.values.setItem(r, 5, new)
        self.values.resizeColumnsToContents()
        self.values.blockSignals(False)

    def _variant_of(self, a):
        """Find an asset's upgraded counterpart.

        Args:
            a (cqmod.catalog.Asset): The asset to look up.

        Returns:
            cqmod.catalog.Asset | None: Its ``+`` variant, or None if it has
            none or is already one.
        """
        want = diff.variant_name(a.name)
        if want == a.name:
            return None
        return next((x for x in self.assets if x.name == want), None)

    def _find_fields(self):
        """Diff the current asset against its variant to locate gameplay values.

        Populates the candidate highlight and filters the value list to it. If
        nothing differs, the status bar explains that the upgrade probably
        changes text or adds an effect rather than a number.
        """
        a = self.current
        b = self._variant_of(a)
        if not b:
            return
        try:
            pa, pb = self.reader.read(a.uexp), self.reader.read(b.uexp)
            na = uasset.parse(self.reader.read(a.uasset)).names
            nb = uasset.parse(self.reader.read(b.uasset)).names
            found = diff.compare(a, pa, b, pb, na, nb, only_plausible=True)
        except Exception as e:
            QMessageBox.warning(self, "Compare failed", str(e))
            return
        self._candidates = {f.offset_a: f.value_b for f in found}
        self.only_cand.setEnabled(bool(found))
        self.only_cand.setChecked(bool(found))
        self._load_values(a)
        skipped = ""
        if not found.fully_comparable:
            skipped = (f"; {len(found.skipped)} export(s) could not be compared "
                       "because the two assets set different properties")
        self.statusBar().showMessage(
            f"{len(found)} candidate field(s) differ from {b.name}{skipped}"
            if found else
            f"no integer fields differ from {b.name}{skipped}", 9000)

    # ------------------------------------------------------------ edits
    def _stage_text(self):
        """Stage title and description changes that differ from the original.
        """
        a = self.current
        if not a:
            return
        n = 0
        if a.title_key and self.title_edit.text() != a.title:
            self.project.set_text(a.namespace, a.title_key, self.title_edit.text()); n += 1
        if a.desc_key and self.desc_edit.toPlainText() != a.description:
            self.project.set_text(a.namespace, a.desc_key, self.desc_edit.toPlainText()); n += 1
        self._refresh_edits()
        self.statusBar().showMessage(f"staged {n} text change(s)" if n else "no text changes", 4000)

    def _replace_art(self):
        """Pick a replacement image and stage it for the current asset.
        """
        a = self.current
        if not a:
            return
        p, _ = QFileDialog.getOpenFileName(self, "Choose replacement art", "",
                                           "Images (*.png *.jpg *.jpeg *.bmp *.webp)")
        if p:
            self.project.set_texture(a.texture, p)
            self._refresh_edits(); self._load_art(a)

    def _copy_art(self):
        """Replace this asset's art with another texture from the game.

        The source is re-encoded to the target's size and pixel format, so a
        large uncompressed source can be written into a small block-compressed
        target without the user matching them up.
        """
        a = self.current
        if not a or not self.art_paths:
            return
        pick, ok = QInputDialog.getItem(
            self, "Copy art from", "Texture:",
            [Path(p).name for p in self.art_paths], 0, True)
        if not ok or not pick:
            return
        src = next((p for p in self.art_paths if Path(p).name == pick), None)
        if not src:
            QMessageBox.warning(self, "Not found", f"No texture named {pick}")
            return
        self.project.set_texture(a.texture, source_texture=src)
        self._refresh_edits(); self._load_art(a)
        self.statusBar().showMessage(f"art will be copied from {pick}", 8000)

    def _export_art(self):
        """Save the current asset's art to a PNG file.
        """
        if not getattr(self, "_art_image", None):
            return
        p, _ = QFileDialog.getSaveFileName(self, "Export art",
                                           f"{self.current.name}.png", "PNG (*.png)")
        if p:
            self._art_image.save(p)
            self.statusBar().showMessage(f"wrote {p}", 5000)

    def _tag_changed(self, offset, tag, label):
        """Stage a gameplay tag change.

        Any tag used anywhere in the game can be chosen. If this asset has never
        referenced it, the build appends the name to the package's name table.

        Args:
            offset (int): Byte offset of the tag's name index.
            tag (str): The tag to set.
            label (str): Row label, unused beyond readability.
        """
        if not self.current or not tag:
            return
        self.project.set_tag(self.current.path, offset, tag)
        self._refresh_edits()
        self.statusBar().showMessage(f"tag set to {tag}", 6000)

    def _value_changed(self, item):
        """Stage or clear a value edit when a cell is edited.

        Clearing the cell removes the edit. Non-numeric input is rejected with
        a warning rather than silently ignored.

        Args:
            item (QTableWidgetItem): The edited cell.
        """
        if item.column() != 5 or not self.current:
            return
        off = item.data(Qt.UserRole)
        text = item.text().strip()
        if not text:
            self.project.values = [v for v in self.project.values
                                   if not (v.asset_path == self.current.path and v.offset == off)]
        else:
            try:
                self.project.set_value(self.current.path, off, int(text, 0),
                                       f"{self.current.name}@{off}")
            except ValueError:
                QMessageBox.warning(self, "Not a number",
                                    f"{text!r} is not an integer.")
                item.setText("")
                return
        self._refresh_edits()

    def _refresh_edits(self):
        """Redraw the staged-edit list and update the tab's count badge.
        """
        p = self.project
        lines = []
        for t in p.texts:
            lines.append(f"text    [{t.locale}] {t.namespace}/{t.key} = {t.value!r}")
        for t in p.textures:
            src = t.image_path or f"game art: {Path(t.source_texture).name}"
            lines.append(f"art     {Path(t.texture_path).name} <- {src}")
        for v in p.values:
            lines.append(f"value   {Path(v.asset_path).name} @{v.offset} = {v.value}")
        for t in p.tags:
            lines.append(f"tag     {Path(t.asset_path).name} @{t.offset} = {t.tag}")
        self.edits.setPlainText("\n".join(lines) or "(no edits staged)")
        n = len(p.texts) + len(p.textures) + len(p.values) + len(p.tags)
        self.tabs.setTabText(3, f"Pending edits ({n})" if n else "Pending edits")

    def _clear_edits(self):
        """Discard every staged edit, keeping the project name.
        """
        self.project = Project(name=self.project.name)
        self._refresh_edits()
        if self.current:
            self._select_asset()

    # ---------------------------------------------------------- project
    def open_project(self):
        """Load a project file, replacing the staged edits.
        """
        p, _ = QFileDialog.getOpenFileName(self, "Open project", "", "JSON (*.json)")
        if p:
            self.project = Project.load(p); self.project_path = Path(p)
            self._refresh_edits()
            if self.current:
                self._select_asset()

    def save_project(self):
        """Write the staged edits to a project file.
        """
        p, _ = QFileDialog.getSaveFileName(self, "Save project",
                                           str(self.project_path or "mod_project.json"),
                                           "JSON (*.json)")
        if p:
            self.project.save(p); self.project_path = Path(p)
            self.statusBar().showMessage(f"saved {p}", 5000)

    def build_and_install(self):
        """Compile the staged edits and install the mod pak.

        On success reports what was written and how to uninstall; on failure
        shows the error together with the partial build log.
        """
        if self.project.is_empty:
            QMessageBox.information(self, "Nothing to build",
                                    "Stage some edits first.")
            return
        log = []
        try:
            raw = self.project.build(self.reader, log=log.append)
            dest = self.mods.staging_path_for(self.project.name)
            # Rebuilding replaces any previous copy in either directory.
            for old in (dest, self.mods.paks_dir / dest.name):
                if old.exists():
                    old.unlink()
            dest.write_bytes(raw)
            log.append(f"wrote {dest.name} ({len(raw):,} bytes)")
            st = dest.stat()
            info = ModInfo(dest.name, display_name(dest.name), dest, False,
                           st.st_size, datetime.fromtimestamp(st.st_mtime))
            self.mods.enable(info)
            log.append(f"enabled: {info.path}")
        except Exception as e:
            QMessageBox.critical(self, "Build failed",
                                 f"{e}\n\n" + "\n".join(log))
            return
        self._refresh_mods()
        QMessageBox.information(
            self, "Mod installed",
            "\n".join(log) + "\n\nRestart the game to load it.\n"
            "Use the Mods tab to disable it without deleting it.")


def main():
    """Start the application.

    Returns:
        None

    Raises:
        SystemExit: Always, carrying the Qt event loop's exit code.
    """
    app = QApplication(sys.argv)
    app.setApplicationName("Commander Quest Mod Tool")
    w = MainWindow(); w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
