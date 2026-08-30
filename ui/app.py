#!/usr/bin/env python3
"""Commander Quest mod tool - browse and edit the game's data assets."""
from __future__ import annotations
import struct, sys, traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt, QSize, QThread, Signal
from PySide6.QtGui import QAction, QImage, QPixmap, QKeySequence, QColor
from PySide6.QtWidgets import (
    QCheckBox, QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QListWidgetItem, QTableWidget, QTableWidgetItem, QLineEdit,
    QLabel, QPushButton, QTabWidget, QTextEdit, QPlainTextEdit, QFileDialog,
    QMessageBox, QHeaderView, QAbstractItemView, QComboBox, QGroupBox,
    QFormLayout, QStatusBar, QProgressDialog, QToolBar, QSizePolicy,
)

from cqmod import config, catalog, texture, locres, uasset, diff
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
        self.classes.setMinimumWidth(230)
        split.addWidget(self._boxed("Asset type", self.classes))

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Name", "Title", "Category"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._select_asset)
        split.addWidget(self._boxed("Assets", self.table))

        split.addWidget(self._detail_panel())
        split.setSizes([230, 460, 760])
        self.setCentralWidget(split)
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
        self.export_art_btn = QPushButton("Export PNG...")
        self.export_art_btn.clicked.connect(self._export_art)
        self.export_art_btn.setEnabled(False)
        row.addWidget(self.replace_art_btn); row.addWidget(self.export_art_btn); row.addStretch()
        lay.addLayout(row)
        self.tabs.addTab(page, "Art")

        # --- Values ---
        page = QWidget(); lay = QVBoxLayout(page)
        lay.addWidget(QLabel(
            "Integer slots inside each export payload. Property <i>names</i> need a "
            ".usmap, so these are addressed by offset - identify a field by comparing "
            "a card with its '+' upgrade variant, then edit here."))
        row = QHBoxLayout()
        self.find_btn = QPushButton("Find fields vs '+' variant")
        self.find_btn.setToolTip(
            "Diff this asset against its upgraded '+' variant. Values that differ "
            "are almost always the gameplay numbers.")
        self.find_btn.clicked.connect(self._find_fields)
        self.find_btn.setEnabled(False)
        self.only_cand = QCheckBox("Show only candidates")
        self.only_cand.toggled.connect(lambda _: self.current and self._load_values(self.current))
        self.only_cand.setEnabled(False)
        row.addWidget(self.find_btn); row.addWidget(self.only_cand); row.addStretch()
        lay.addLayout(row)
        self.values = QTableWidget(0, 5)
        self.values.setHorizontalHeaderLabels(
            ["Export", "Class", "Offset", "Value", "New value"])
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
        self._load_art(a)

    def _load_art(self, a):
        """Show an asset's art, or why it cannot be shown.

        Args:
            a (cqmod.catalog.Asset): The selected asset.
        """
        self.art.clear(); self.art_info.setText("")
        self.replace_art_btn.setEnabled(False); self.export_art_btn.setEnabled(False)
        if not a.texture or (a.texture + ".uexp") not in self.reader:
            self.art.setText("no art"); return
        try:
            tex = texture.parse(self.reader.read(a.texture + ".uexp"))
            img = texture.to_png_bytes(tex)
            self._art_image = img
            data = img.tobytes("raw", "RGBA")
            qi = QImage(data, img.width, img.height, QImage.Format_RGBA8888)
            self.art.setPixmap(QPixmap.fromImage(qi).scaled(
                QSize(430, 430), Qt.KeepAspectRatio, Qt.SmoothTransformation))
            pending = next((t.image_path for t in self.project.textures
                            if t.texture_path == a.texture), None)
            self.art_info.setText(
                f"{tex.width}x{tex.height} {tex.pixel_format}"
                + (f"   <b style='color:{ACCENT}'>staged: {Path(pending).name}</b>" if pending else ""))
            self.replace_art_btn.setEnabled(True); self.export_art_btn.setEnabled(True)
        except Exception as e:
            self.art.setText(f"cannot display art:\n{e}")

    def _load_values(self, a):
        """List the integer slots in an asset's export payloads.

        Offsets advance one byte at a time rather than four: real property
        layouts interleave smaller types, so a four-byte stride silently skips
        fields. Candidates from the field finder are highlighted, and staged
        edits are shown in the accent colour.

        Args:
            a (cqmod.catalog.Asset): The selected asset.
        """
        self.values.blockSignals(True)
        self.values.setRowCount(0)
        try:
            self._payload = self.reader.read(a.uexp)
        except Exception:
            self._payload = b""
        rows = []
        for e in a.exports:
            start = e.start + e.header_bytes
            end = min(e.end, len(self._payload))
            for off in range(start, max(start, end - 3)):
                (v,) = struct.unpack_from("<i", self._payload, off)
                rows.append((e.index, e.class_name, off, v))
        cand = getattr(self, "_candidates", {})
        if cand and self.only_cand.isChecked():
            rows = [r for r in rows if r[2] in cand]
        self.values.setRowCount(len(rows))
        staged = {v.offset: v.value for v in self.project.values
                  if v.asset_path == a.path}
        for r, (ei, cls, off, v) in enumerate(rows):
            for c, text in ((0, f"+{ei}"), (1, cls), (2, str(off)), (3, str(v))):
                it = QTableWidgetItem(text)
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                self.values.setItem(r, c, it)
            if off in cand:
                self.values.item(r, 3).setText(f"{v}   (variant: {cand[off]})")
                for c in range(4):
                    self.values.item(r, c).setBackground(QColor("#2d4f1e"))
                    self.values.item(r, c).setForeground(QColor("#d9f2c8"))
            new = QTableWidgetItem("" if off not in staged else str(staged[off]))
            new.setData(Qt.UserRole, off)
            if off in staged:
                new.setBackground(QColor(ACCENT)); new.setForeground(QColor("white"))
            self.values.setItem(r, 4, new)
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
        self.statusBar().showMessage(
            f"{len(found)} candidate field(s) differ from {b.name}"
            if found else
            f"no integer fields differ from {b.name} (the upgrade may change text "
            "or add an effect instead)", 9000)

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

    def _value_changed(self, item):
        """Stage or clear a value edit when a cell is edited.

        Clearing the cell removes the edit. Non-numeric input is rejected with
        a warning rather than silently ignored.

        Args:
            item (QTableWidgetItem): The edited cell.
        """
        if item.column() != 4 or not self.current:
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
            lines.append(f"art     {Path(t.texture_path).name} <- {t.image_path}")
        for v in p.values:
            lines.append(f"value   {Path(v.asset_path).name} @{v.offset} = {v.value}")
        self.edits.setPlainText("\n".join(lines) or "(no edits staged)")
        n = len(p.texts) + len(p.textures) + len(p.values)
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
            out = self.project.install(self.reader, config.paks_dir(), log=log.append)
        except Exception as e:
            QMessageBox.critical(self, "Build failed",
                                 f"{e}\n\n" + "\n".join(log))
            return
        QMessageBox.information(
            self, "Mod installed",
            "\n".join(log) + f"\n\nRestart the game to load it.\n"
            f"To uninstall, delete:\n{out}")


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
