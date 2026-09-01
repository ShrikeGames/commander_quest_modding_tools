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
    QInputDialog, QSpinBox, QDoubleSpinBox, QDialog, QDialogButtonBox,
)

from datetime import datetime

from cqmod import (config, catalog, texture, locres, uasset, diff, mods,
                   usmap, artchain, decks, randomizer, keyfinder, resources)
from cqmod.mods import ModError, ModInfo, ModManager, display_name
from cqmod.pak import PakReader
from cqmod.project import Project

ACCENT = "#B03A0B"

DETAIL_LINE_LIMIT = 2000
"""How many log lines a report will show before it starts summarising."""


def report(parent, title, summary, details=(), icon=QMessageBox.Information):
    """Show a message whose detail can run to thousands of lines.

    A plain message box grows to fit its text, so handing one a build log with
    an edit per line produces a dialog taller than the screen with no way to
    scroll it. Randomizing everything stages a few thousand edits, which is
    exactly when the log is worth reading, so the detail goes in the collapsible
    pane instead. That pane scrolls and keeps its own size, and the box itself
    stays as small as the summary.

    Args:
        parent (QWidget): Dialog parent.
        title (str): Window title.
        summary (str): The short version, always visible.
        details (Iterable[str]): Lines shown under "Show Details".
        icon (QMessageBox.Icon): Which icon to use.

    Returns:
        None
    """
    lines = list(details)
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setText(summary)
    if lines:
        if len(lines) > DETAIL_LINE_LIMIT:
            hidden = len(lines) - DETAIL_LINE_LIMIT
            lines = lines[:DETAIL_LINE_LIMIT] + [
                "", f"... and {hidden:,} more lines."]
        box.setDetailedText("\n".join(lines))
        # The pane Qt builds for detailed text is small and wraps, which turns
        # a log of one edit per line into an unreadable block. Give it room and
        # let it scroll sideways instead, so each edit stays on its own line.
        pane = box.findChild(QTextEdit)
        if pane is not None:
            pane.setLineWrapMode(QTextEdit.NoWrap)
            pane.setMinimumSize(720, 380)
    box.exec()


def summarise_log(lines):
    """Count a build log by the kind of edit each line records.

    Args:
        lines (Iterable[str]): The log :meth:`cqmod.project.Project.build`
            produced.

    Returns:
        str: One line per kind of edit, or an empty string for a log that
        records no edits.
    """
    kinds = {"value": "values", "ref": "references", "name": "names",
             "text": "text", "art": "art", "add": "added properties"}
    counts = {}
    for line in lines:
        parts = line.split()
        if not (line.startswith("  ") and parts and parts[0] in kinds):
            continue
        # A name edit logs twice when the asset has to grow its name table,
        # once for the insertion and once for the write. Only the write is an
        # edit, so the insertion line is skipped to keep the count honest.
        if parts[0] == "name" and "+=" in parts:
            continue
        counts[kinds[parts[0]]] = counts.get(kinds[parts[0]], 0) + 1
    return "\n".join(f"{n:,} {kind}" for kind, n in sorted(counts.items()))


class KeyThread(QThread):
    """Recovers the pak key without freezing the window.

    A scan reads gigabytes of another process's memory, so it can take a minute
    or two. Running it on the GUI thread would make the application look hung
    for the whole time.
    """

    progress = Signal(str)
    done = Signal(str, str)

    def __init__(self, pak):
        """Prepare a scan.

        Args:
            pak (pathlib.Path): The archive whose key is wanted.
        """
        super().__init__()
        self.pak = pak

    def run(self):
        """Scan for the key and report the result.

        Returns:
            None
        """
        try:
            key = keyfinder.find_key(self.pak, progress=self.progress.emit)
            self.done.emit(key, "")
        except Exception as e:
            self.done.emit("", str(e))


class SetupDialog(QDialog):
    """First-run setup: where the game is, and the key needed to read it.

    A packaged build is launched from wherever it was unzipped, so unlike a
    checkout it cannot assume it sits inside the game folder, and it starts with
    no key. Both are asked for here rather than in a config file, so that
    nothing about the tool requires editing JSON by hand.
    """

    def __init__(self, parent=None):
        """Build the dialog, prefilling anything that can be worked out.

        Args:
            parent (QWidget | None): Dialog parent.
        """
        super().__init__(parent)
        self.setWindowTitle("Setup")
        self.setMinimumWidth(620)
        self._thread = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "<b>Commander Quest Mod Tool needs two things before it can start."
            "</b>"))

        game_box = QGroupBox("1. Where Commander Quest is installed")
        game_layout = QVBoxLayout(game_box)
        row = QHBoxLayout()
        self.game_edit = QLineEdit(str(config.game_dir()))
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        row.addWidget(self.game_edit); row.addWidget(browse)
        game_layout.addLayout(row)
        self.game_status = QLabel()
        game_layout.addWidget(self.game_status)
        layout.addWidget(game_box)

        key_box = QGroupBox("2. The archive key for this version of the game")
        key_layout = QVBoxLayout(key_box)
        key_layout.addWidget(QLabel(
            "The game builds this key while it runs, so it has to be read from "
            "the running game once. It is the same for everyone on a given "
            "version, and is only needed again after a patch changes it."))
        self.key_edit = QLineEdit()
        self.key_edit.setPlaceholderText("64 hex characters")
        try:
            self.key_edit.setText(config.aes_key().hex().upper())
        except config.ConfigError:
            pass
        key_layout.addWidget(self.key_edit)
        self.recover_btn = QPushButton("Recover key from the running game")
        self.recover_btn.clicked.connect(self._recover)
        key_layout.addWidget(self.recover_btn)
        self.key_status = QLabel()
        self.key_status.setWordWrap(True)
        key_layout.addWidget(self.key_status)
        layout.addWidget(key_box)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.game_edit.textChanged.connect(self._check_game)
        self._check_game()

    def _browse(self):
        """Ask for the game folder with a directory picker."""
        picked = QFileDialog.getExistingDirectory(
            self, "Select the Commander Quest folder", self.game_edit.text())
        if picked:
            self.game_edit.setText(picked)

    def _check_game(self):
        """Report whether the chosen folder really holds the game."""
        ok = config.is_game_dir(self.game_edit.text())
        self.game_status.setText(
            "Found the game archive." if ok else
            "No game archive here. Pick the folder containing "
            "Commander/Content/Paks.")
        self.game_status.setStyleSheet(
            "color: green" if ok else f"color: {ACCENT}")
        return ok

    def _recover(self):
        """Start a background scan for the key."""
        if not self._check_game():
            return
        pak = Path(self.game_edit.text()) / config.PAK_RELPATH
        self.recover_btn.setEnabled(False)
        self.key_status.setText("Looking for the running game...")
        self._thread = KeyThread(pak)
        self._thread.progress.connect(self.key_status.setText)
        self._thread.done.connect(self._recovered)
        self._thread.start()

    def _recovered(self, key, err):
        """Show the recovered key, or why the scan failed.

        Args:
            key (str): The key, or empty on failure.
            err (str): The error, or empty on success.
        """
        self.recover_btn.setEnabled(True)
        if err:
            self.key_status.setText(err)
            self.key_status.setStyleSheet(f"color: {ACCENT}")
            return
        self.key_edit.setText(key)
        self.key_status.setText("Key recovered and verified against the archive.")
        self.key_status.setStyleSheet("color: green")

    def _save(self):
        """Validate both settings and write them to the local config."""
        if not self._check_game():
            QMessageBox.warning(self, "Game not found",
                                "Pick the folder that contains "
                                "Commander/Content/Paks.")
            return
        key = self.key_edit.text().strip().removeprefix("0x")
        try:
            if len(bytes.fromhex(key)) != 32:
                raise ValueError("a key is 32 bytes, or 64 hex characters")
        except ValueError as e:
            QMessageBox.warning(self, "Key does not look right", str(e))
            return
        config.save_local(game_dir=self.game_edit.text(), aes_key=key.upper())
        self.accept()


def needs_setup() -> bool:
    """Test whether the tool has enough configuration to start.

    Returns:
        bool: True when the game or its key is still unknown.
    """
    try:
        config.pak_path()
        config.aes_key()
        return False
    except config.ConfigError:
        return True


class ArtView(QLabel):
    """A label that fits its image to whatever room it is given.

    QLabel clips a pixmap that is larger than the widget rather than scaling it,
    so a fixed-size preview loses the top and bottom of a texture as soon as the
    pane is shorter than the image. This keeps the decoded image and rescales it
    on every resize instead.
    """

    def __init__(self, *a, **kw):
        """Create an empty view."""
        super().__init__(*a, **kw)
        self._source = None
        self.setMinimumSize(QSize(120, 120))
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)

    def set_image(self, image):
        """Show an image, or clear the view.

        Args:
            image (QImage | None): The image to display.
        """
        self._source = QPixmap.fromImage(image) if image is not None else None
        self._rescale()

    def clear(self):
        """Drop the current image."""
        self._source = None
        super().clear()

    def _rescale(self):
        """Fit the stored image to the widget, preserving aspect ratio."""
        if self._source is None or self._source.isNull():
            return
        target = self.size()
        if target.width() < 8 or target.height() < 8:
            return
        super().setPixmap(self._source.scaled(
            target, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, event):
        """Rescale when the available space changes.

        Args:
            event (QResizeEvent): The resize event.
        """
        super().resizeEvent(event)
        self._rescale()


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
        self.card_rows = []
        self.unit_bp = {}
        self._art_candidates = []
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
        tb.addWidget(QLabel("  Mod name "))
        self.name_edit = QLineEdit(self.project.name)
        self.name_edit.setMaximumWidth(200)
        self.name_edit.setToolTip(
            "Used for the pak filename, ZZZ_<name>_P.pak, and shown in the Mods tab.")
        self.name_edit.textChanged.connect(self._name_changed)
        tb.addWidget(self.name_edit)
        tb.addSeparator()
        for text, slot in (("Open Project", self.open_project),
                           ("Save Project", self.save_project),
                           ("Setup", self.open_setup)):
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
        self.main_tabs.addTab(self._randomizer_panel(), "Randomizer")
        self.main_tabs.addTab(self._mods_panel(), "Mods")
        self.main_tabs.currentChanged.connect(
            lambda i: self._refresh_mods() if i == 2 else None)
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
        self.art_pick = QComboBox()
        self.art_pick.setToolTip(
            "Textures this asset's model uses. A unit reaches several through "
            "its Blueprint and mesh, so pick the one you mean.")
        self.art_pick.currentIndexChanged.connect(self._art_pick_changed)
        self.art_pick.setVisible(False)
        lay.addWidget(self.art_pick)
        self.art = ArtView(alignment=Qt.AlignCenter)
        self.art.setMinimumHeight(240)
        self.art.setStyleSheet("border:1px solid #555;")
        lay.addWidget(self.art, 1)
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

        # --- Deck ---
        page = QWidget(); lay = QVBoxLayout(page)
        self.deck_hint = QLabel()
        self.deck_hint.setWordWrap(True)
        lay.addWidget(self.deck_hint)
        self.deck_table = QTableWidget(0, 3)
        self.deck_table.setHorizontalHeaderLabels(["Deck", "Slot", "Card"])
        self.deck_table.verticalHeader().setVisible(False)
        self.deck_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        lay.addWidget(self.deck_table)
        self.tabs.addTab(page, "Deck")

        # --- Pending edits ---
        page = QWidget(); lay = QVBoxLayout(page)
        self.edits = QPlainTextEdit(readOnly=True)
        lay.addWidget(self.edits)
        row = QHBoxLayout()
        b = QPushButton("Clear all edits"); b.clicked.connect(self._clear_edits)
        row.addWidget(b); row.addStretch(); lay.addLayout(row)
        self.tabs.addTab(page, "Pending edits")
        return self.tabs

    def _randomizer_panel(self):
        """Build the randomizer tab.

        Returns:
            QWidget: Seed and options above a list of categories.
        """
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addWidget(QLabel(
            "Pick what to randomize, then generate. The result is staged as ordinary "
            "edits, so it can be reviewed on the Pending edits tab and built like any "
            "other mod. A run is reproducible: the same seed and options always give "
            "the same mod."))

        row = QHBoxLayout()
        row.addWidget(QLabel("Seed"))
        self.seed_spin = QSpinBox()
        self.seed_spin.setRange(0, 2_000_000_000)
        self.seed_spin.setValue(1234)
        row.addWidget(self.seed_spin)
        roll = QPushButton("Roll")
        roll.clicked.connect(lambda: self.seed_spin.setValue(
            __import__("random").randint(0, 2_000_000_000)))
        row.addWidget(roll)
        row.addSpacing(20)
        row.addWidget(QLabel("Variance"))
        self.variance_spin = QDoubleSpinBox()
        self.variance_spin.setRange(0.0, 2.0)
        self.variance_spin.setSingleStep(0.1)
        self.variance_spin.setValue(0.0)
        self.variance_spin.setToolTip(
            "How far a rolled value may fall outside the range the game itself "
            "uses. 0 keeps rolls within it.")
        row.addWidget(self.variance_spin)
        self.enemies_box = QCheckBox("Include enemy units")
        self.enemies_box.setChecked(True)
        row.addWidget(self.enemies_box)
        self.commanders_box = QCheckBox("Include commanders")
        self.commanders_box.setToolTip(
            "Off by default. Rolling a commander's health down to a few points "
            "makes a run unwinnable rather than interesting.")
        row.addWidget(self.commanders_box)
        row.addStretch()
        lay.addLayout(row)

        self.rando_table = QTableWidget(len(randomizer.CATEGORIES), 3)
        self.rando_table.setHorizontalHeaderLabels(["Randomize", "Mode", "What it does"])
        self.rando_table.verticalHeader().setVisible(False)
        self.rando_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        for r, c in enumerate(randomizer.CATEGORIES):
            box = QTableWidgetItem(c.label)
            box.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            box.setCheckState(Qt.Unchecked)
            self.rando_table.setItem(r, 0, box)
            combo = QComboBox()
            combo.addItems(list(c.modes))
            combo.currentIndexChanged.connect(lambda _: self._rando_estimate())
            self.rando_table.setCellWidget(r, 1, combo)
            desc = QTableWidgetItem(c.description)
            desc.setFlags(desc.flags() & ~Qt.ItemIsEditable)
            self.rando_table.setItem(r, 2, desc)
        self.rando_table.resizeRowsToContents()
        lay.addWidget(self.rando_table)

        self.rando_note = QLabel()
        self.rando_note.setWordWrap(True)
        lay.addWidget(self.rando_note)
        # Connected only once the rows and the note exist, since populating the
        # table emits itemChanged for every cell.
        self.rando_table.itemChanged.connect(lambda _: self._rando_estimate())

        row = QHBoxLayout()
        gen = QPushButton("Generate randomized edits")
        gen.setStyleSheet(
            "QPushButton{background:%s;color:white;padding:6px 16px;"
            "font-weight:600;border-radius:3px}" % ACCENT)
        gen.clicked.connect(self._rando_generate)
        row.addWidget(gen)
        sel = QPushButton("Select all")
        sel.clicked.connect(lambda: self._rando_set_all(True))
        clr = QPushButton("Select none")
        clr.clicked.connect(lambda: self._rando_set_all(False))
        row.addWidget(sel); row.addWidget(clr); row.addStretch()
        lay.addLayout(row)
        self._rando_estimate()
        return page

    def _rando_set_all(self, on):
        """Tick or clear every category.

        Args:
            on (bool): Whether to select them.
        """
        for r in range(self.rando_table.rowCount()):
            self.rando_table.item(r, 0).setCheckState(
                Qt.Checked if on else Qt.Unchecked)

    def _rando_settings(self):
        """Read the chosen options.

        Returns:
            cqmod.randomizer.Settings: The current selection.
        """
        choices = {}
        for r, c in enumerate(randomizer.CATEGORIES):
            box = self.rando_table.item(r, 0)
            combo = self.rando_table.cellWidget(r, 1)
            if box is None or combo is None:
                continue
            if box.checkState() == Qt.Checked:
                choices[c.key] = combo.currentText()
        return randomizer.Settings(seed=self.seed_spin.value(), choices=choices,
                                   variance=self.variance_spin.value(),
                                   include_enemies=self.enemies_box.isChecked(),
                                   include_commanders=self.commanders_box.isChecked())

    def _rando_estimate(self):
        """Warn about the size the current selection would produce."""
        chosen = self._rando_settings().choices
        if not chosen:
            self.rando_note.setText("Nothing selected.")
            return
        note = f"{len(chosen)} categor{'y' if len(chosen) == 1 else 'ies'} selected."
        if self.reader and self.assets:
            size = randomizer.estimate_bytes(self.reader, self.assets,
                                             self._rando_settings())
            if size:
                note += f"  Roughly <b>{size / 2**20:.1f} MB</b>."
            else:
                note += "  Around a megabyte."
        if chosen.keys() & {"card_art", "gear_icons"}:
            note += ("  Image categories repoint each asset at an existing "
                     "texture instead of copying it, so they cost a rewritten "
                     "header rather than a picture.")
        self.rando_note.setText(note)

    def _rando_generate(self):
        """Generate randomized edits into the current project."""
        if not self.reader or not self.usmap:
            QMessageBox.warning(self, "Not ready",
                                "The archive and property schema must load first.")
            return
        settings = self._rando_settings()
        if not settings.choices:
            QMessageBox.information(self, "Nothing selected",
                                    "Tick at least one category to randomize.")
            return
        if self.project.values or self.project.tags or self.project.textures:
            if QMessageBox.question(
                    self, "Replace staged edits?",
                    "Generating will add to the edits already staged. Clear them "
                    "first?") == QMessageBox.Yes:
                self.project = Project(name=self.project.name)
        try:
            summary = randomizer.run(self.reader, self.assets, self.usmap,
                                     settings, self.project)
        except Exception as e:
            QMessageBox.critical(self, "Randomize failed", str(e))
            return
        self._refresh_edits()
        if self.current:
            self._select_asset()
        lines = [f"{randomizer.CATEGORY_BY_KEY[k].label}: {v:,} change(s)"
                 for k, v in summary.items()]
        report(self, "Randomized",
               f"Seed {settings.seed}\n\n"
               f"{sum(summary.values()):,} changes across "
               f"{len(summary)} categor"
               f"{'y' if len(summary) == 1 else 'ies'}.\n\n"
               "Review them on the Pending edits tab, then Build & Install.",
               lines)

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

    def open_setup(self):
        """Reopen setup, for a new game location or a key changed by a patch."""
        if SetupDialog(self).exec() == QDialog.Accepted:
            QMessageBox.information(
                self, "Settings saved",
                "Restart the tool for the new settings to take effect.")

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
            self.unit_bp = artchain.unit_blueprints(reader, assets, self.usmap)
            self.card_rows = decks.card_rows(reader)
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
        self._load_deck(a)
        self._load_art(a)

    def _load_deck(self, a):
        """Show a commander's starting decks, one row per card slot.

        Deck slots name rows of the card table rather than card assets, so the
        choices are the table's row names. A card the commander has never used
        is fine: the build adds the name to its package.

        Args:
            a (cqmod.catalog.Asset): The selected asset.
        """
        self.deck_table.setRowCount(0)
        found = decks.parse(self.reader, a) if a.class_name == "CMCommanderData" else []
        self.tabs.setTabVisible(4, bool(found))
        if not found:
            self.deck_hint.setText("")
            return
        self.deck_hint.setText(
            f"{len(found)} starting deck(s), {sum(len(d) for d in found)} cards. "
            "Slots name rows of DT_Cards; picking one the commander has never "
            "used adds it to the package at build time.")
        staged = {t.offset: t.tag for t in self.project.tags if t.asset_path == a.path}
        rows = [(n, i, c) for n, d in enumerate(found) for i, c in enumerate(d.cards)]
        self.deck_table.setRowCount(len(rows))
        for r, (deck_no, slot, card) in enumerate(rows):
            for col, text in ((0, f"deck {deck_no + 1}"), (1, str(slot + 1))):
                it = QTableWidgetItem(text)
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                self.deck_table.setItem(r, col, it)
            combo = QComboBox()
            choices = list(self.card_rows)
            current = staged.get(card.offset, card.row)
            if current not in choices:
                choices.insert(0, current)
            combo.addItems(choices)
            combo.setCurrentIndex(choices.index(current))
            combo.currentTextChanged.connect(
                lambda text, o=card.offset: self._deck_changed(o, text))
            self.deck_table.setCellWidget(r, 2, combo)
        self.deck_table.resizeColumnsToContents()

    def _deck_changed(self, offset, row):
        """Stage a starting deck change.

        Args:
            offset (int): Byte offset of the slot's name index.
            row (str): Card table row to put in the slot.
        """
        if not self.current or not row:
            return
        self.project.set_name_ref(self.current.path, offset, row)
        self._refresh_edits()
        self.statusBar().showMessage(f"deck slot set to {row}", 6000)

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
        """Show an asset's art, following the model chain where needed.

        A card names its illustration directly. A unit does not: its appearance
        hangs off the Blueprint the summon card points at, reached through the
        Blueprint's materials and its mesh's material slots. Several textures
        turn up that way, so they are all offered and the best guess selected.

        Args:
            a (cqmod.catalog.Asset): The selected asset.
        """
        self.art.clear(); self.art_info.setText("")
        for b in (self.replace_art_btn, self.export_art_btn, self.copy_art_btn):
            b.setEnabled(False)

        candidates = []
        if a.texture and (a.texture + ".uexp") in self.reader:
            candidates = [a.texture]
        else:
            bp = self.unit_bp.get(a.name)
            if bp:
                candidates = artchain.model_textures(self.reader, bp)
        self._art_candidates = candidates

        self.art_pick.blockSignals(True)
        self.art_pick.clear()
        self.art_pick.addItems([Path(c).name for c in candidates])
        self.art_pick.setVisible(len(candidates) > 1)
        self.art_pick.blockSignals(False)

        if not candidates:
            self.art.setText("no art")
            return
        self._show_art(candidates[0])

    def _art_pick_changed(self, index):
        """Preview a different texture from the model chain.

        Args:
            index (int): Position in the candidate list.
        """
        if 0 <= index < len(self._art_candidates):
            self._show_art(self._art_candidates[index])

    def _show_art(self, path):
        """Decode and display one texture.

        Args:
            path (str): Archive path of the texture, without extension.
        """
        self._art_path = path
        try:
            bulk_path = path + ".ubulk"
            bulk = self.reader.read(bulk_path) if bulk_path in self.reader else b""
            tex = texture.parse(self.reader.read(path + ".uexp"), bulk)
            img = texture.to_image(tex)
            self._art_image = img
            data = img.tobytes("raw", "RGBA")
            # Keep a reference: QImage does not copy the buffer it is given.
            self._art_bytes = data
            self.art.set_image(
                QImage(data, img.width, img.height, QImage.Format_RGBA8888))
            edit = next((t for t in self.project.textures
                         if t.texture_path == path), None)
            pending = (edit.image_path or edit.source_texture) if edit else None
            self.art_info.setText(
                f"{Path(path).name}   {tex.width}x{tex.height} {tex.pixel_format}"
                + (f", {len(tex.mips)} mips" if tex.is_block else "")
                + (f"   <b style='color:{ACCENT}'>staged: {Path(pending).name}</b>"
                   if pending else ""))
            for b in (self.replace_art_btn, self.export_art_btn, self.copy_art_btn):
                b.setEnabled(True)
        except ImportError as e:
            self.art.setText(str(e)); self.art.setWordWrap(True)
        except Exception as e:
            self.art.setText(f"cannot display art:\n{e}"); self.art.setWordWrap(True)

    def _load_values(self, a):
        """Show the asset's property values, and those of what it links to.

        A card carries no stats of its own: a summon card's health and attack
        live on the ``DA_Unit_*`` asset it points at. Those are listed here too,
        marked with the asset they belong to, so the numbers shown on a card can
        be edited without going to find the unit.

        With the recovered schema each row is a named property at its real
        offset. Without it, or in raw mode, the table falls back to every byte
        offset read as an integer, stepping one byte at a time because property
        layouts interleave sizes.

        Args:
            a (cqmod.catalog.Asset): The selected asset.
        """
        self.values.blockSignals(True)
        self.values.setRowCount(0)
        self._payloads = {}
        try:
            self._payload = self.reader.read(a.uexp)
            self._payloads[a.path] = self._payload
        except Exception:
            self._payload = b""

        try:
            self._pkg = uasset.parse(self.reader.read(a.uasset))
            self._names = self._pkg.names
        except Exception:
            self._pkg, self._names = None, []

        named = bool(self.usmap) and not self.raw_mode.isChecked()
        rows = []

        pkg_cache = {}

        def pkg_of(asset):
            """Parse and cache an asset's header, for resolving references.

            Args:
                asset (cqmod.catalog.Asset): The asset.

            Returns:
                cqmod.uasset.Package | None: Its header, or None if unreadable.
            """
            if asset.path not in pkg_cache:
                try:
                    pkg_cache[asset.path] = uasset.parse(self.reader.read(asset.uasset))
                except Exception:
                    pkg_cache[asset.path] = None
            return pkg_cache[asset.path]

        def add_asset_rows(asset, payload, owned):
            """Append one asset's property rows.

            Args:
                asset (cqmod.catalog.Asset): Asset to list.
                payload (bytes): Its ``.uexp``.
                owned (bool): False when the asset is only linked to, which
                    labels its rows and stages edits against its own path.
            """
            for n, e in enumerate(asset.exports):
                placed = self.usmap.place(e, payload)
                prefix = "" if owned else f"{asset.name}  "
                for f in placed:
                    shown = f.value
                    if f.is_reference and f.value is not None:
                        # Show what a reference points at; the raw index means
                        # nothing to a reader and inviting edits to it is unsafe.
                        target = pkg_of(asset).resolve(f.value) if pkg_of(asset) else ""
                        shown = f"-> {target}" if target else f.value
                    rows.append(dict(ex=e.index, name=prefix + f.name, type=f.type,
                                     off=f.offset, val=shown, editable=f.editable,
                                     tag=None, add=None, path=asset.path, owned=owned))
                    if self.usmap.is_tag_container(e, f.index):
                        for k, (off, nidx) in enumerate(self.usmap.tags(f, payload)):
                            nm = self._names[nidx] if owned and nidx < len(self._names) \
                                else f"<name {nidx}>"
                            rows.append(dict(ex=e.index, name=f"    tag[{k}]",
                                             type="GameplayTag", off=off, val=nm,
                                             editable=False, tag=nidx, add=None,
                                             path=asset.path, owned=owned))
                if len(placed) == len(e.prop_indices):
                    have = set(e.prop_indices)
                    for q in self.usmap.properties(e.class_name):
                        if q["index"] in have or q["type"] not in usmap.SERIALIZED_SIZE:
                            continue
                        if q["type"] in ("ObjectProperty", "ClassProperty",
                                         "SoftObjectProperty"):
                            continue        # a reference cannot be typed in
                        rows.append(dict(ex=e.index, name=prefix + q["name"],
                                         type=q["type"], off=-2, val=None,
                                         editable=True, tag=None,
                                         add=(n, q["index"]), path=asset.path,
                                         owned=owned))

        if named:
            add_asset_rows(a, self._payload, True)
            for _, target in self.usmap.links(a, self._pkg, self._payload) \
                    if self._pkg else []:
                linked = next((x for x in self.assets if x.name == target), None)
                if linked is None or linked.name == a.name or not linked.exports:
                    continue
                try:
                    lp = self.reader.read(linked.uexp)
                except Exception:
                    continue
                self._payloads[linked.path] = lp
                add_asset_rows(linked, lp, False)
            if not rows:
                named = False

        if not named:
            for e in a.exports:
                start = e.start + e.header_bytes
                end = min(e.end, len(self._payload))
                for off in range(start, max(start, end - 3)):
                    (v,) = struct.unpack_from("<i", self._payload, off)
                    rows.append(dict(ex=e.index, name=e.class_name, type="", off=off,
                                     val=v, editable=True, tag=None, add=None,
                                     path=a.path, owned=True))

        if self.usmap is None:
            self.values_hint.setText(
                "No property schema loaded, so values are addressed by byte offset. "
                "Launch the game and run <tt>tools/usmap/dump.py</tt> to recover "
                "property names.")
        elif named:
            self.values_hint.setText(
                "Named properties from the game's own reflection data. Rows prefixed "
                "with an asset name belong to something this one links to, such as the "
                "unit a summon card creates, which is where its health and attack live. "
                "Rows marked <i>not set</i> are left at their default and can be added.")
        else:
            self.values_hint.setText(
                "Raw byte offsets, each read as a 32-bit integer. Offsets advance one "
                "byte at a time because property layouts interleave sizes.")

        cand = getattr(self, "_candidates", {})
        if cand and self.only_cand.isChecked():
            rows = [x for x in rows if x["off"] in cand and x["owned"]]
        staged = {(v.asset_path, v.offset): v.value for v in self.project.values}
        staged_tags = {(t.asset_path, t.offset): t.tag for t in self.project.tags}

        self.values.setRowCount(len(rows))
        for r, row in enumerate(rows):
            off, path = row["off"], row["path"]
            cells = [f"+{row['ex']}", row["name"], row["type"],
                     "not set" if off == -2 else ("zero" if off < 0 else str(off)),
                     "" if row["val"] is None else str(row["val"])]
            for c, text in enumerate(cells):
                it = QTableWidgetItem(text)
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                if not row["owned"]:
                    it.setForeground(QColor("#8fb0c8"))
                self.values.setItem(r, c, it)
            if off in cand and row["owned"]:
                self.values.item(r, 4).setText(f"{row['val']}   (variant: {cand[off]})")
                for c in range(5):
                    self.values.item(r, c).setBackground(QColor("#2d4f1e"))
                    self.values.item(r, c).setForeground(QColor("#d9f2c8"))

            if row["tag"] is not None:
                combo = QComboBox()
                choices = list(self.all_tags)
                current = staged_tags.get((path, off)) or row["val"]
                if current and current not in choices:
                    choices.insert(0, current)
                combo.addItems(choices)
                if current in choices:
                    combo.setCurrentIndex(choices.index(current))
                combo.currentTextChanged.connect(
                    lambda text, o=off, p=path: self._tag_changed(o, text, p))
                self.values.setCellWidget(r, 5, combo)
                continue

            if row["add"] is not None:
                pending = next((x for x in self.project.added
                                if x.asset_path == path
                                and (x.export_index, x.prop_index) == row["add"]), None)
                for c in range(5):
                    self.values.item(r, c).setForeground(QColor("#7d8a86"))
                cell = QTableWidgetItem("" if pending is None else str(pending.value))
                cell.setData(Qt.UserRole, -2)
                cell.setData(Qt.UserRole + 1, (row["add"], path, row["name"]))
                cell.setToolTip("Left at its default, so absent from the payload. "
                                "Enter a value to add it.")
                if pending is not None:
                    cell.setBackground(QColor(ACCENT)); cell.setForeground(QColor("white"))
                self.values.setItem(r, 5, cell)
                continue

            key = (path, off)
            cell = QTableWidgetItem("" if key not in staged else str(staged[key]))
            cell.setData(Qt.UserRole, off)
            cell.setData(Qt.UserRole + 2, path)
            if not row["editable"] or off < 0:
                cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                if row["type"] in ("ObjectProperty", "ClassProperty",
                                   "SoftObjectProperty"):
                    cell.setToolTip(
                        "A reference to another object, stored as a package index. "
                        "Typing a number here would repoint it at something else "
                        "rather than change a value, so it is read only.")
            if key in staged:
                cell.setBackground(QColor(ACCENT)); cell.setForeground(QColor("white"))
            self.values.setItem(r, 5, cell)
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
            self.project.set_texture(self._art_path, image_path=p)
            self._refresh_edits(); self._show_art(self._art_path)

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
        self.project.set_texture(self._art_path, source_texture=src)
        self._refresh_edits(); self._show_art(self._art_path)
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

    def _tag_changed(self, offset, tag, path=None):
        """Stage a gameplay tag change.

        Any tag used anywhere in the game can be chosen. If this asset has never
        referenced it, the build appends the name to the package's name table.

        Args:
            offset (int): Byte offset of the tag's name index.
            tag (str): The tag to set.
            path (str | None): Asset to edit; defaults to the selected one.
        """
        if not self.current or not tag:
            return
        self.project.set_tag(path or self.current.path, offset, tag)
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
        spec = item.data(Qt.UserRole + 1)
        path = item.data(Qt.UserRole + 2) or self.current.path

        if off == -2 and spec is not None:
            (export_index, prop_index), owner, label = spec
            if not text:
                self.project.added = [
                    x for x in self.project.added
                    if not (x.asset_path == owner
                            and (x.export_index, x.prop_index) == (export_index, prop_index))]
            else:
                try:
                    self.project.add_property(owner, export_index, prop_index,
                                              int(text, 0), label.strip())
                except ValueError:
                    QMessageBox.warning(self, "Not a number",
                                        f"{text!r} is not an integer.")
                    item.setText("")
                    return
            self._refresh_edits()
            return

        if not text:
            self.project.values = [v for v in self.project.values
                                   if not (v.asset_path == path and v.offset == off)]
        else:
            try:
                self.project.set_value(path, off, int(text, 0),
                                       self.values.item(item.row(), 1).text().strip())
            except ValueError:
                QMessageBox.warning(self, "Not a number", f"{text!r} is not an integer.")
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
        for x in p.added:
            lines.append(f"add     {Path(x.asset_path).name} {x.label} = {x.value}")
        for v in p.values:
            lines.append(f"value   {Path(v.asset_path).name} @{v.offset} = {v.value}")
        for t in p.tags:
            lines.append(f"name    {Path(t.asset_path).name} @{t.offset} = {t.tag}")
        self.edits.setPlainText("\n".join(lines) or "(no edits staged)")
        n = (len(p.texts) + len(p.textures) + len(p.values) + len(p.tags)
             + len(p.added))
        self.tabs.setTabText(3, f"Pending edits ({n})" if n else "Pending edits")

    def _clear_edits(self):
        """Discard every staged edit, keeping the project name.
        """
        self.project = Project(name=self.project.name)
        self._refresh_edits()
        if self.current:
            self._select_asset()

    # ---------------------------------------------------------- project
    def _name_changed(self, text):
        """Rename the mod being built.

        Args:
            text (str): New name. Characters that cannot appear in a filename
                are replaced, since the name becomes the pak's filename.
        """
        clean = "".join(c if c.isalnum() or c in "-_" else "_" for c in text).strip("_")
        self.project.name = clean or "MyMod"

    def open_project(self):
        """Load a project file, replacing the staged edits.
        """
        p, _ = QFileDialog.getOpenFileName(self, "Open project", "", "JSON (*.json)")
        if p:
            self.project = Project.load(p); self.project_path = Path(p)
            self.name_edit.setText(self.project.name)
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
            report(self, "Build failed", str(e), log, QMessageBox.Critical)
            return
        self._refresh_mods()
        counted = summarise_log(log)
        report(self, "Mod installed",
               f"Installed {dest.name} ({len(raw):,} bytes).\n\n"
               + (counted + "\n\n" if counted else "")
               + "Restart the game to load it.\n"
               "Use the Mods tab to disable it without deleting it.",
               log)


def main():
    """Start the application.

    Returns:
        None

    Raises:
        SystemExit: Always, carrying the Qt event loop's exit code.
    """
    app = QApplication(sys.argv)
    app.setApplicationName("Commander Quest Mod Tool")
    # Setup runs before the window exists. The window resolves the game path
    # while it is being built, so there has to be a usable configuration by
    # then, which on a fresh install there is not.
    if needs_setup() and SetupDialog().exec() != QDialog.Accepted:
        return
    try:
        w = MainWindow()
    except config.ConfigError as e:
        QMessageBox.critical(None, "Setup incomplete", str(e))
        return
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
