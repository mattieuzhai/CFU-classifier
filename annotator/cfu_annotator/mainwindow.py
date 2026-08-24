"""The main window: folder/model/output pickers, the canvas, and export."""

import sys
from pathlib import Path

from PyQt5.QtCore import Qt, QSize, QUrl
from PyQt5.QtGui import (
    QColor, QDesktopServices, QIcon, QImageReader, QKeySequence, QPixmap,
)
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QAction,
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from . import (
    APP_NAME, __version__, detector as det, export, labels as labels_io,
    project, render, scan, settings as prefs, status,
)
from .canvas import MODE_DRAW, MODE_SELECT, ImageCanvas, class_color
from .workers import ImageExportWorker, InferenceWorker, ModelLoadWorker

UNDO_DEPTH = 100       # plenty for a session's worth of hand corrections

HINT = "color: #666;"
OK = "color: #1a7f37; font-weight: bold;"
WARN = "color: #b35c00;"


def _compact(group):
    """Tighter than Qt's defaults, so a full panel fits a laptop screen."""
    group.layout().setContentsMargins(8, 6, 8, 6)
    group.layout().setSpacing(4)
    return group


def _swatch(color, size=12):
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(color))
    return QIcon(pixmap)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1500, 950)

        # -- state ---------------------------------------------------------
        self.image_folder = None
        self.output_folder = None
        self.labels_folder = None
        self.images = []               # list[Path]
        self.index = -1
        self.detector = None
        # image name -> {boxes, annotated, edited, finalized, model, params}
        self.records = {}
        self.image_sizes = {}          # image name -> (w, h)
        self.project_path = None
        self.dirty = False             # unsaved changes to the project
        self.model_worker = None
        self.infer_worker = None
        self.image_worker = None
        self._pending_export = None
        # Held until the restored model finishes loading, so the summary of what
        # was restored isn't immediately overwritten by "Model ready".
        self._restore_note = None
        self._undo_stack = []
        self._pre_edit = None

        self._build_ui()
        self._restore_settings()
        self._refresh_title()
        self._update_enabled_state()

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        self.canvas = ImageCanvas()
        self.canvas.boxes_changed.connect(self._on_boxes_changed)
        self.canvas.user_edited.connect(self._on_user_edited)
        self.canvas.selection_changed.connect(self._on_selection_changed)
        self.canvas.status_message.connect(self._show_status)
        self.canvas.cursor_moved.connect(self._on_cursor_moved)
        self.canvas.edit_blocked.connect(self._on_edit_blocked)
        # The canvas drops itself back to select mode after a box is drawn, so
        # the toolbar follows the canvas rather than the other way round.
        self.canvas.mode_changed.connect(self._sync_mode_buttons)

        # Setup steps on the left, image in the middle, live editing aids on the
        # right — so the counts and class list are always visible while working.
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_sidebar())
        splitter.addWidget(self._build_center())
        splitter.addWidget(self._build_right_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([350, 830, 310])
        self.setCentralWidget(splitter)

        self._build_toolbar()
        self._build_menus()

        self.status_label = QLabel("Choose an image folder and a model to begin.")
        self.coord_label = QLabel("")
        self.zoom_label = QLabel("")
        self.statusBar().addWidget(self.status_label, 1)
        self.statusBar().addPermanentWidget(self.coord_label)
        self.statusBar().addPermanentWidget(self.zoom_label)

        # Connected last: the handler touches zoom_label, created just above.
        self.canvas.zoom_changed.connect(
            lambda percent: self.zoom_label.setText(f"{percent:.0f}%")
        )

    def _build_menus(self):
        bar = self.menuBar()
        # macOS puts this in the system menu bar; keeping it inside the window
        # means the menus are visible wherever the app is run.
        bar.setNativeMenuBar(False)

        file_menu = bar.addMenu("&Project")
        file_menu.addAction("New project", self.new_project, QKeySequence.New)
        file_menu.addAction("Open project…", self.open_project, QKeySequence.Open)
        file_menu.addSeparator()
        self.action_save = file_menu.addAction(
            "Save project", self.save_project, QKeySequence.Save
        )
        file_menu.addAction(
            "Save project as…", self.save_project_as, QKeySequence("Ctrl+Shift+S")
        )
        file_menu.addSeparator()
        file_menu.addAction("Export annotations…", self.export_now, QKeySequence("Ctrl+E"))
        file_menu.addSeparator()
        # Mirrored here as well as in the sidebar, so it stays reachable even
        # when the setup panel is scrolled.
        self.action_remember = file_menu.addAction("Remember settings for next time")
        self.action_remember.setCheckable(True)
        self.action_remember.toggled.connect(self._on_remember_action)
        file_menu.addSeparator()
        file_menu.addAction("Quit", self.close, QKeySequence.Quit)

        edit_menu = bar.addMenu("&Edit")
        self.action_undo = edit_menu.addAction("Undo", self.undo, QKeySequence.Undo)
        self.action_undo.setEnabled(False)
        edit_menu.addSeparator()
        # The same QAction the toolbar uses — one object, one Delete shortcut.
        edit_menu.addAction(self.action_delete)
        self.action_clear = edit_menu.addAction(
            "Clear all boxes on this image", self.clear_current
        )

        image_menu = bar.addMenu("&Image")
        image_menu.addAction("Previous image", lambda: self._go_to(self.index - 1),
                             QKeySequence("A"))
        image_menu.addAction("Next image", lambda: self._go_to(self.index + 1),
                             QKeySequence("D"))
        image_menu.addSeparator()
        self.action_finalize = image_menu.addAction(
            "Mark as finalized", self.toggle_finalized, QKeySequence("Ctrl+L")
        )
        self.action_finalize.setCheckable(True)
        self.action_contaminated = image_menu.addAction(
            "Mark as contaminated", self.toggle_contaminated,
            QKeySequence("Ctrl+Shift+X"),
        )
        self.action_contaminated.setCheckable(True)
        image_menu.addSeparator()
        image_menu.addAction("Annotate this image", self.annotate_current,
                             QKeySequence("R"))
        image_menu.addAction("Annotate all remaining…", self.annotate_all)

    def _group_image_list(self):
        group = QGroupBox("Images")
        layout = QVBoxLayout(group)

        self.list_images = QListWidget()
        self.list_images.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list_images.setIconSize(QSize(16, 16))
        self.list_images.setAlternatingRowColors(True)
        # Plate filenames are long; elide the middle so the date prefix and the
        # well suffix — the parts that identify a plate — both stay readable.
        self.list_images.setTextElideMode(Qt.ElideMiddle)
        self.list_images.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list_images.currentRowChanged.connect(self._on_image_row_changed)
        self.list_images.setToolTip(
            "Every image in the folder. The icon shows its annotation status; "
            "click any image to jump straight to it."
        )
        layout.addWidget(self.list_images, 1)

        self.button_finalize = QPushButton("Mark as finalized")
        self.button_finalize.setCheckable(True)
        self.button_finalize.setToolTip(
            "Lock this image's annotations as complete (⌘L). Click again to unlock."
        )
        self.button_finalize.clicked.connect(self.toggle_finalized)
        layout.addWidget(self.button_finalize)

        self.button_contaminated = QPushButton("Mark as contaminated")
        self.button_contaminated.setCheckable(True)
        self.button_contaminated.setToolTip(
            "Write this plate off: its counts are discarded and the plate is "
            "locked, and the summary reports it as contaminated with zero "
            "colonies. Undoable."
        )
        self.button_contaminated.clicked.connect(self.toggle_contaminated)
        layout.addWidget(self.button_contaminated)

        legend = QWidget()
        grid = QGridLayout(legend)
        grid.setContentsMargins(2, 2, 2, 2)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(2)
        # Two per row, so the legend costs two lines instead of four.
        for index, key in enumerate(status.ORDER):
            row, col = divmod(index, 2)
            icon = QLabel()
            icon.setPixmap(status.status_icon(key).pixmap(14, 14))
            icon.setToolTip(status.LABELS[key])
            text = QLabel(status.SHORT_LABELS[key])
            text.setStyleSheet(HINT)
            text.setToolTip(status.LABELS[key])
            grid.addWidget(icon, row, col * 2)
            grid.addWidget(text, row, col * 2 + 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        layout.addWidget(legend)
        return group

    def _build_toolbar(self):
        bar = QToolBar("Tools")
        bar.setIconSize(QSize(16, 16))
        bar.setMovable(False)
        self.addToolBar(bar)

        # The two run-the-model actions live here rather than in the sidebar:
        # they are the primary actions and the toolbar is always visible.
        self.button_annotate = QAction("▶  Annotate image", self)
        self.button_annotate.setToolTip("Run the model on the image on screen (R)")
        self.button_annotate.triggered.connect(self.annotate_current)
        bar.addAction(self.button_annotate)

        self.button_annotate_all = QAction("▶▶  Annotate all remaining…", self)
        self.button_annotate_all.setToolTip(
            "Run the model on every image that hasn't been annotated yet"
        )
        self.button_annotate_all.triggered.connect(self.annotate_all)
        bar.addAction(self.button_annotate_all)
        bar.addSeparator()

        self.action_select = QAction("Select / Edit", self, checkable=True, checked=True)
        self.action_select.setShortcut(QKeySequence("Escape"))
        self.action_select.setToolTip(
            "Select mode (Esc) — click a box to select, drag to move, "
            "drag a handle to resize, drag empty space to pan"
        )
        self.action_select.triggered.connect(lambda: self._set_mode(MODE_SELECT))

        self.action_draw = QAction("Draw box", self, checkable=True)
        self.action_draw.setShortcut(QKeySequence("W"))
        self.action_draw.setToolTip("Draw mode (W) — drag on the image to add a box")
        self.action_draw.triggered.connect(lambda: self._set_mode(MODE_DRAW))

        bar.addAction(self.action_select)
        bar.addAction(self.action_draw)

        self.check_sticky = QCheckBox("Keep drawing")
        self.check_sticky.setToolTip(
            "Normally the tool returns to Select after each box you draw. Tick "
            "this to stay in Draw mode and add several boxes in a row."
        )
        self.check_sticky.toggled.connect(self._on_sticky_toggled)
        bar.addWidget(self.check_sticky)
        bar.addSeparator()

        self.action_delete = QAction("Delete box", self)
        self.action_delete.setShortcut(QKeySequence.Delete)
        self.action_delete.setToolTip("Delete the selected box(es) (Delete)")
        self.action_delete.triggered.connect(self.canvas.delete_selected)
        bar.addAction(self.action_delete)
        bar.addSeparator()

        for text, tip, slot in (
            ("Fit", "Fit the image in the window (F)", self.canvas.fit_to_window),
            ("100%", "Show the image at actual size", self.canvas.zoom_to_actual_size),
            ("−", "Zoom out (−)", lambda: self.canvas.zoom_by(1 / 1.25)),
            ("+", "Zoom in (+)", lambda: self.canvas.zoom_by(1.25)),
        ):
            action = QAction(text, self)
            action.setToolTip(tip)
            action.triggered.connect(slot)
            bar.addAction(action)

        bar.addSeparator()
        self.check_labels = QCheckBox("Labels")
        self.check_labels.setChecked(True)
        self.check_labels.setToolTip("Show class names on the boxes")
        self.check_labels.toggled.connect(self._toggle_labels)
        bar.addWidget(self.check_labels)

        self.check_conf = QCheckBox("Confidence")
        self.check_conf.setToolTip("Also show the model's confidence in each label")
        self.check_conf.toggled.connect(self._toggle_confidence)
        bar.addWidget(self.check_conf)

    def _build_center(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.canvas, 1)

        nav = QWidget()
        nav_layout = QHBoxLayout(nav)
        nav_layout.setContentsMargins(6, 4, 6, 4)

        self.button_prev = QPushButton("◀  Previous")
        self.button_prev.setToolTip("Previous image (A)")
        self.button_prev.clicked.connect(lambda: self._go_to(self.index - 1))

        self.button_next = QPushButton("Next  ▶")
        self.button_next.setToolTip("Next image (D)")
        self.button_next.clicked.connect(lambda: self._go_to(self.index + 1))

        self.label_position = QLabel("no images loaded")
        self.label_position.setAlignment(Qt.AlignCenter)

        nav_layout.addWidget(self.button_prev)
        nav_layout.addWidget(self.label_position, 1)
        nav_layout.addWidget(self.button_next)
        layout.addWidget(nav)
        return panel

    def _build_sidebar(self):
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        for group in (
            self._group_images(), self._group_model(), self._group_output(),
            self._group_detection(), self._group_preferences(),
        ):
            _compact(group)
            layout.addWidget(group)
        layout.addStretch(1)

        area = QScrollArea()
        area.setWidget(inner)
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        area.setMinimumWidth(320)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # Setup steps above, the list of every image below. The setup pane is
        # given exactly the height its four steps need — the Annotate buttons
        # must never end up below the fold — and the image list takes whatever
        # is left, absorbing any window resize.
        column = QSplitter(Qt.Vertical)
        column.addWidget(area)
        column.addWidget(_compact(self._group_image_list()))
        column.setStretchFactor(0, 0)
        column.setStretchFactor(1, 1)
        # Headroom on top of the size hint: the folder and model labels each
        # wrap to two or three lines once real paths are shown, which the hint
        # can't know while they still say "No folder chosen".
        natural = inner.sizeHint().height() + 120
        column.setSizes([natural, max(200, 900 - natural)])
        area.setMinimumHeight(180)      # but the user can still shrink it
        return column

    def _build_right_panel(self):
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        for group in (self._group_classes(), self._group_counts(), self._group_help()):
            _compact(group)
            layout.addWidget(group)
        layout.addStretch(1)

        area = QScrollArea()
        area.setWidget(inner)
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        area.setMinimumWidth(285)
        return area

    def _group_help(self):
        group = QGroupBox("Shortcuts")
        layout = QVBoxLayout(group)
        text = QLabel(
            "<table cellspacing='2'>"
            "<tr><td><b>W</b></td><td>draw a new box</td></tr>"
            "<tr><td><b>Esc</b></td><td>back to select mode</td></tr>"
            "<tr><td><b>1</b>–<b>9</b></td><td>relabel selected box</td></tr>"
            "<tr><td><b>Delete</b></td><td>remove selected box</td></tr>"
            "<tr><td><b>R</b></td><td>run the model</td></tr>"
            "<tr><td><b>A</b> / <b>D</b></td><td>previous / next image</td></tr>"
            "<tr><td><b>F</b></td><td>fit image to window</td></tr>"
            "<tr><td><b>+</b> / <b>−</b></td><td>zoom in / out</td></tr>"
            "<tr><td>wheel</td><td>zoom at the pointer</td></tr>"
            "<tr><td>drag</td><td>pan (empty space)</td></tr>"
            "</table>"
        )
        text.setStyleSheet(HINT)
        layout.addWidget(text)
        return group

    def _group_images(self):
        group = QGroupBox("1.  Images")
        layout = QVBoxLayout(group)
        button = QPushButton("Choose image folder…")
        button.setToolTip("Pick a folder of .jpg / .png plate photos")
        button.clicked.connect(self.choose_image_folder)
        self.label_images = QLabel("No folder chosen")
        self.label_images.setWordWrap(True)
        self.label_images.setStyleSheet(HINT)
        self.button_labels = QPushButton("Choose labels folder… (optional)")
        self.button_labels.setToolTip(
            "Pre-load annotations you already have: a folder of YOLO .txt "
            "files named after the images (plate1.jpg -> plate1.txt). "
            "Anything that isn't a matching label file is ignored."
        )
        self.button_labels.clicked.connect(self.choose_labels_folder)
        self.label_labels = QLabel("No labels loaded")
        self.label_labels.setWordWrap(True)
        self.label_labels.setStyleSheet(HINT)

        layout.addWidget(button)
        layout.addWidget(self.label_images)
        layout.addWidget(self.button_labels)
        layout.addWidget(self.label_labels)
        return group

    def _group_model(self):
        group = QGroupBox("2.  Model")
        layout = QVBoxLayout(group)
        button = QPushButton("Choose model (.pt)…")
        button.setToolTip("Pick a YOLO detection model — class names are read from the file")
        button.clicked.connect(self.choose_model)
        self.label_model = QLabel("No model chosen")
        self.label_model.setWordWrap(True)
        self.label_model.setStyleSheet(HINT)
        layout.addWidget(button)
        layout.addWidget(self.label_model)
        return group

    def _group_output(self):
        group = QGroupBox("3.  Output")
        layout = QVBoxLayout(group)
        button = QPushButton("Choose output folder…")
        button.setToolTip("Where the count summary and/or YOLO labels get written")
        button.clicked.connect(self.choose_output_folder)
        self.label_output = QLabel("No folder chosen")
        self.label_output.setWordWrap(True)
        self.label_output.setStyleSheet(HINT)

        self.check_csv = QCheckBox(f"Count summary  ({export.CSV_NAME})")
        self.check_csv.setChecked(True)
        self.check_csv.setToolTip("One row per image, one column per class")

        self.check_yolo = QCheckBox(f"YOLO labels  ({export.YOLO_DIRNAME}/)")
        self.check_yolo.setToolTip("One .txt per image in YOLO format, plus classes.txt")

        self.check_images = QCheckBox("Annotated images")
        self.check_images.setChecked(True)
        self.check_images.setToolTip(
            f"A copy of each annotated plate with the boxes and labels drawn "
            f"on, written to {export.IMAGES_DIRNAME}/ — for checking counts "
            f"without the app.\n\nSaved at full resolution, so a whole-plate "
            f"photo comes out around 25 MB and takes a second or two. This is "
            f"the slowest and largest part of an export; untick it to skip."
        )

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Folder"))
        self.edit_export_name = QLineEdit()
        self.edit_export_name.setPlaceholderText(export.export_dir_name())
        self.edit_export_name.setToolTip(
            "Name for the folder this export creates. Leave it blank to get a "
            "dated name like " + export.export_dir_name() + ". If the name is "
            "already taken, -2 is appended rather than overwriting it."
        )
        name_row.addWidget(self.edit_export_name, 1)


        self.button_export = QPushButton("Export now")
        self.button_export.setToolTip("Write the selected outputs (Ctrl/Cmd+E)")
        self.button_export.clicked.connect(self.export_now)

        layout.addWidget(button)
        layout.addWidget(self.label_output)
        layout.addWidget(self.check_csv)
        layout.addWidget(self.check_yolo)
        layout.addWidget(self.check_images)
        layout.addLayout(name_row)
        layout.addWidget(self.button_export)
        return group

    def _group_preferences(self):
        group = QGroupBox("Preferences")
        layout = QVBoxLayout(group)
        self.check_remember = QCheckBox("Remember settings for next time")
        self.check_remember.setChecked(True)
        self.check_remember.setToolTip(
            "Reopen with the same folders, model, detection settings and export "
            "choices next time you start the app. Annotations are not stored "
            "this way — save a project for those."
        )
        self.check_remember.toggled.connect(self._on_remember_toggled)
        layout.addWidget(self.check_remember)
        return group

    def _group_detection(self):
        group = QGroupBox("4.  Detection")
        layout = QVBoxLayout(group)

        conf_row = QHBoxLayout()
        conf_row.addWidget(QLabel("Confidence"))
        self.spin_conf = QDoubleSpinBox()
        self.spin_conf.setRange(0.01, 0.99)
        self.spin_conf.setSingleStep(0.05)
        self.spin_conf.setValue(det.DEFAULT_CONF)
        self.spin_conf.setToolTip("Detections below this confidence are discarded")
        conf_row.addWidget(self.spin_conf)
        layout.addLayout(conf_row)

        self.check_tiling = QCheckBox("Tile large images")
        self.check_tiling.setChecked(True)
        self.check_tiling.setToolTip(
            "Whole-plate photos are far bigger than the model's input size. Tiling "
            "runs the model on overlapping crops at native resolution and merges "
            "the results, which is how this model was trained and validated. "
            "Leave this on unless your images are already small."
        )
        self.check_tiling.toggled.connect(self._toggle_tiling)
        layout.addWidget(self.check_tiling)

        tile_row = QHBoxLayout()
        tile_row.addWidget(QLabel("Tile"))
        self.spin_tile = QSpinBox()
        self.spin_tile.setRange(256, 8192)
        self.spin_tile.setSingleStep(160)
        self.spin_tile.setValue(det.DEFAULT_TILE_SIZE)
        self.spin_tile.setSuffix(" px")
        self.spin_tile.setToolTip("Size of each crop the model sees")
        tile_row.addWidget(self.spin_tile, 1)
        tile_row.addWidget(QLabel("overlap"))
        self.spin_overlap = QDoubleSpinBox()
        self.spin_overlap.setRange(0.0, 0.9)
        self.spin_overlap.setSingleStep(0.05)
        self.spin_overlap.setValue(det.DEFAULT_TILE_OVERLAP)
        self.spin_overlap.setToolTip("Overlap keeps colonies on a tile seam from being missed")
        tile_row.addWidget(self.spin_overlap, 1)
        layout.addLayout(tile_row)


        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.button_cancel = QPushButton("Cancel")
        self.button_cancel.setVisible(False)
        self.button_cancel.clicked.connect(self.cancel_inference)
        layout.addWidget(self.button_cancel)
        return group

    def _group_classes(self):
        group = QGroupBox("Classes")
        layout = QVBoxLayout(group)
        hint = QLabel(
            "Highlighted class is used for new boxes. To relabel a box: select "
            "it, then press its number key or double-click it."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(HINT)

        self.list_classes = QListWidget()
        self.list_classes.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list_classes.setMaximumHeight(112)
        self.list_classes.currentRowChanged.connect(self._on_class_row_changed)

        self.label_selected = QLabel("No box selected")
        self.label_selected.setStyleSheet(HINT)

        self.button_apply_class = QPushButton("Relabel selected box")
        self.button_apply_class.setToolTip(
            "Give the selected box the highlighted class"
        )
        self.button_apply_class.setEnabled(False)
        self.button_apply_class.clicked.connect(self._apply_class_to_selection)

        layout.addWidget(hint)
        layout.addWidget(self.list_classes)
        layout.addWidget(self.label_selected)
        layout.addWidget(self.button_apply_class)
        return group

    def _group_counts(self):
        group = QGroupBox("Counts — this image")
        layout = QVBoxLayout(group)
        self.table_counts = QTableWidget(0, 2)
        self.table_counts.setHorizontalHeaderLabels(["Class", "Count"])
        self.table_counts.verticalHeader().setVisible(False)
        self.table_counts.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_counts.setSelectionMode(QAbstractItemView.NoSelection)
        self.table_counts.setMaximumHeight(152)
        header = self.table_counts.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        layout.addWidget(self.table_counts)
        self.label_progress_summary = QLabel("")
        self.label_progress_summary.setWordWrap(True)
        self.label_progress_summary.setStyleSheet(HINT)
        layout.addWidget(self.label_progress_summary)
        return group

    # ------------------------------------------------------- input choosing

    def choose_image_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Choose the folder containing your plate images",
            str(self.image_folder or Path.home()),
        )
        if not folder:
            return
        if self.records and not self._confirm_discard("Loading a different folder"):
            return
        if self._load_image_folder(Path(folder)):
            self._mark_dirty()
            self._save_settings()

    def _load_image_folder(self, folder, keep_records=False, quiet=False):
        """Scan `folder` and show its images. Returns True if it worked.

        `keep_records` is set when opening a project, where the annotations for
        these images have already been restored and must not be wiped. `quiet`
        suppresses the pop-ups when restoring a remembered folder at startup —
        nobody wants a dialog before they've even clicked anything.
        """
        try:
            result = scan.scan_folder(folder)
        except OSError as exc:
            if not quiet:
                QMessageBox.critical(self, "Cannot read folder", str(exc))
            return False

        if result.warning and not quiet:
            QMessageBox.warning(self, "Unsupported image format", result.warning)

        if not result.images:
            if not quiet:
                QMessageBox.critical(
                    self, "No usable images",
                    f"No .jpg or .png images were found directly inside:\n\n{folder}\n\n"
                    "Subfolders are not searched — point the app at the folder that "
                    "holds the images themselves.",
                )
            return False

        self.image_folder = Path(folder)
        self.images = result.images
        if not keep_records:
            self.records = {}
            self.image_sizes = {}
            self.labels_folder = None
            self.label_labels.setText("No labels loaded")
            self.label_labels.setStyleSheet(HINT)
        self._undo_stack = []
        self._refresh_undo_action()
        self.index = -1

        note = f"<b>{self.image_folder.name}</b><br>{len(self.images)} image(s)"
        skipped = len(result.ignored)
        if skipped:
            note += f"<br><span style='color:#666'>{skipped} non-image file(s) ignored</span>"
        self.label_images.setText(note)
        self.label_images.setStyleSheet("")

        self._rebuild_image_list()
        self._go_to(0)
        self._update_enabled_state()
        self._show_status(f"Loaded {len(self.images)} image(s) from {self.image_folder}")
        return True

    def choose_labels_folder(self):
        """Pre-load annotations from a folder of YOLO .txt files."""
        if not self.images:
            QMessageBox.information(
                self, "No images yet",
                "Choose the image folder first — labels are matched to images "
                "by filename.",
            )
            return
        folder = QFileDialog.getExistingDirectory(
            self, "Choose the folder containing YOLO label files (.txt)",
            str(self.labels_folder or self.image_folder or Path.home()),
        )
        if not folder:
            return
        self._import_labels(Path(folder))

    def _import_labels(self, folder, quiet=False):
        """Load YOLO labels for the current images. Returns True if any loaded."""
        already = [p.name for p in self.images
                   if (self.records.get(p.name) or {}).get("boxes")]
        if already and not quiet:
            answer = QMessageBox.question(
                self, "Replace existing annotations?",
                f"{len(already)} image(s) already have boxes.\n\nImporting "
                f"labels replaces the boxes on every image that has a matching "
                f"label file. Images without one are left alone.",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
            )
            if answer != QMessageBox.Yes:
                return False

        try:
            report = labels_io.import_folder(
                folder, self.images,
                class_count=len(self.canvas.class_names) or None,
                known_sizes=self.image_sizes,
            )
        except OSError as exc:
            if not quiet:
                QMessageBox.critical(self, "Cannot read labels folder", str(exc))
            return False

        if not report.images_with_labels:
            self.labels_folder = None
            self.label_labels.setText("No labels loaded")
            self.label_labels.setStyleSheet(HINT)
            if not quiet:
                QMessageBox.warning(
                    self, "No matching label files",
                    f"Nothing in\n\n{folder}\n\nmatched the images in this "
                    f"folder.\n\nLabel files must be .txt named after the "
                    f"image — plate1.jpg needs plate1.txt.\n\n"
                    f"{report.summary()}",
                )
            return False

        for name, boxes in report.loaded.items():
            record = self._record(name, create=True)
            if record.get("contaminated"):
                continue          # a written-off plate keeps no counts
            record["boxes"] = boxes
            record["annotated"] = True
            record["edited"] = False
            record["model"] = f"imported from {Path(folder).name}"
            record["params"] = None
            self.image_sizes.setdefault(
                name, labels_io.image_size(self.image_folder / name)
            )

        self.labels_folder = Path(folder)
        self.label_labels.setText(
            f"<b>{self.labels_folder.name}</b><br>"
            f"{report.boxes} box(es) on {report.images_with_labels} image(s)"
        )
        self.label_labels.setStyleSheet("")

        # Show the freshly imported boxes on whatever is on screen. Deliberately
        # not _go_to(): that stores the canvas into the record first, which
        # would overwrite what we just imported for the current image.
        if self.index >= 0:
            current = self.records.get(self.images[self.index].name)
            self.canvas.set_locked(False)
            self.canvas.set_boxes(list((current or {}).get("boxes") or []))
            self._apply_locked_state()
            self._refresh_position_label()
            self._refresh_pre_edit()
        self._rebuild_image_list()
        self._mark_dirty()
        self._save_settings()
        self._refresh_counts()
        self._show_status(report.summary(self.canvas.class_names).replace("\n\n", " "))
        if not quiet and (report.bad_lines or report.out_of_range_classes
                          or report.unreadable):
            QMessageBox.warning(
                self, "Labels imported with warnings",
                report.summary(self.canvas.class_names),
            )
        return True

    def choose_model(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a YOLO model file", str(Path.home()),
            "YOLO weights (*.pt);;All files (*)",
        )
        if not path:
            return

        if Path(path).suffix.lower() != ".pt":
            QMessageBox.warning(
                self, "Not a model file",
                f"'{Path(path).name}' is not a .pt file.\n\n"
                "Choose the PyTorch weights file produced by Ultralytics YOLO "
                "training — usually called best.pt or last.pt.",
            )
            return
        self._load_model(path)
        self._mark_dirty()

    def _load_model(self, path):
        self.label_model.setText(f"Loading <b>{Path(path).name}</b>…")
        self.label_model.setStyleSheet(HINT)
        self._set_busy(True, "Loading model — this can take a few seconds…")

        self.model_worker = ModelLoadWorker(str(path), self)
        self.model_worker.loaded.connect(self._on_model_loaded)
        self.model_worker.failed.connect(self._on_model_failed)
        self.model_worker.start()

    def _set_class_names(self, names):
        """Populate the class list. Also used when a project supplies the names."""
        self.canvas.set_class_names(names)
        self.list_classes.blockSignals(True)
        self.list_classes.clear()
        for index, name in enumerate(names):
            item = QListWidgetItem(_swatch(class_color(index)), f"{index + 1}.  {name}")
            self.list_classes.addItem(item)
        self.list_classes.blockSignals(False)
        if names:
            self.list_classes.setCurrentRow(0)
        self._refresh_counts()

    def _on_model_loaded(self, detector):
        previous = list(self.canvas.class_names)
        self.detector = detector
        self._set_busy(False)
        self.label_model.setText(
            f"<b>{detector.path.name}</b><br>"
            f"task: detect &nbsp;·&nbsp; {len(detector.class_names)} classes<br>"
            f"<span style='color:#666'>{', '.join(detector.class_names)}</span>"
        )
        self.label_model.setStyleSheet("")
        self._set_class_names(detector.class_names)
        self._update_enabled_state()
        self._save_settings()
        if self._restore_note:
            self._show_status(self._restore_note)
            self._restore_note = None
        else:
            self._show_status(f"Model ready: {detector.path.name}")

        if previous and previous != detector.class_names and self.records:
            QMessageBox.warning(
                self, "Class names differ",
                "This model's classes are not the ones the existing annotations "
                f"were made with.\n\nWas: {', '.join(previous)}\n"
                f"Now: {', '.join(detector.class_names)}\n\n"
                "Existing boxes keep their class numbers, so their labels may "
                "now be wrong. Check them before exporting.",
            )

    def _on_model_failed(self, message):
        self.detector = None
        self._set_busy(False)
        self.label_model.setText("No model chosen")
        self.label_model.setStyleSheet(HINT)
        self._update_enabled_state()
        QMessageBox.warning(self, "Cannot use this model", message)

    def choose_output_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Choose where to save annotations",
            str(self.output_folder or self.image_folder or Path.home()),
        )
        if not folder:
            return
        self.output_folder = Path(folder)
        self.label_output.setText(f"<b>{self.output_folder.name}</b><br>{self.output_folder}")
        self.label_output.setStyleSheet("")
        self._save_settings()
        self._update_enabled_state()

    # ----------------------------------------------------------- navigation

    def _go_to(self, index):
        if not self.images:
            return
        if not 0 <= index < len(self.images):
            return
        self._store_current()

        self.index = index
        path = self.images[index]
        if not self.canvas.load_image(path):
            QMessageBox.warning(
                self, "Cannot open image",
                f"'{path.name}' could not be opened. It may be corrupt or not "
                "really a JPEG/PNG.",
            )
            self.canvas.clear_image()
        else:
            self.image_sizes[path.name] = self.canvas.image_size()

        record = self.records.get(path.name)
        self.canvas.set_locked(False)     # allow the programmatic load
        self.canvas.set_boxes(record["boxes"] if record else [])
        self._apply_locked_state()

        self.list_images.blockSignals(True)
        self.list_images.setCurrentRow(index)
        self.list_images.blockSignals(False)

        self._refresh_position_label()
        self._refresh_counts()
        self._update_enabled_state()
        self._refresh_pre_edit()
        self.zoom_label.setText(f"{self.canvas.zoom_percent():.0f}%")

    def _store_current(self):
        """Remember the boxes for the image we're leaving."""
        if self.index < 0 or self.index >= len(self.images):
            return
        name = self.images[self.index].name
        boxes = self.canvas.get_boxes()
        record = self.records.get(name)
        if record is None:
            if not boxes:
                return
            record = {"annotated": False, "edited": False, "finalized": False,
                      "contaminated": False}
            self.records[name] = record
        record["boxes"] = boxes

    def _record(self, name, create=False):
        record = self.records.get(name)
        if record is None and create:
            record = {
                "boxes": [], "annotated": False, "edited": False,
                "finalized": False, "contaminated": False,
            }
            self.records[name] = record
        return record

    def _status_of(self, name):
        return status.status_of(self.records.get(name))

    def _refresh_position_label(self):
        if self.index < 0:
            self.label_position.setText("no images loaded")
            return
        name = self.images[self.index].name
        state = self._status_of(name)
        self.label_position.setText(
            f"<b>{name}</b> &nbsp;—&nbsp; {self.index + 1} of {len(self.images)}"
            f" &nbsp;·&nbsp; <span style='color:{status.COLORS[state]}'>"
            f"{status.LABELS[state]}</span>"
        )

    # ------------------------------------------------------------ image list

    def _rebuild_image_list(self):
        self.list_images.blockSignals(True)
        self.list_images.clear()
        for path in self.images:
            item = QListWidgetItem(path.name)
            self.list_images.addItem(item)
            self._refresh_image_row(self.list_images.count() - 1)
        if 0 <= self.index < self.list_images.count():
            self.list_images.setCurrentRow(self.index)
        self.list_images.blockSignals(False)

    def _refresh_image_row(self, row=None):
        """Update the icon/tooltip for one row (default: the current image)."""
        if row is None:
            row = self.index
        if not 0 <= row < self.list_images.count():
            return
        item = self.list_images.item(row)
        name = self.images[row].name
        state = self._status_of(name)
        item.setIcon(status.status_icon(state))

        record = self.records.get(name)
        count = len(record["boxes"]) if record else 0
        tip = [name, status.LABELS[state]]
        if record and record.get("annotated"):
            tip.append(f"{count} box(es)")
        if record and record.get("model"):
            tip.append(f"model: {record['model']}")
        item.setToolTip("\n".join(tip))

    def _on_image_row_changed(self, row):
        if row < 0 or row == self.index:
            return
        self._go_to(row)

    def toggle_finalized(self):
        if self.index < 0:
            return
        name = self.images[self.index].name
        record = self._record(name, create=True)
        if record.get("contaminated"):
            self._show_status(
                f"'{name}' is marked contaminated — undo that before finalizing it."
            )
            self._apply_locked_state()
            return
        self._push_undo(name)
        record["finalized"] = not record.get("finalized")
        self._apply_locked_state()
        self._mark_dirty()
        self._refresh_image_row()
        self._refresh_position_label()
        self._refresh_counts()
        self._update_enabled_state()
        self._show_status(
            f"'{name}' marked as finalized — its annotations are locked. "
            "Use the same button to unlock."
            if record["finalized"]
            else f"'{name}' unlocked and can be edited again."
        )

    def toggle_contaminated(self):
        """Write a plate off entirely: its counts go, and it locks.

        Destructive, so it asks first — and the wipe is undoable, because
        losing an hour of hand-corrections to a misclick would be unforgivable.
        """
        if self.index < 0:
            return
        name = self.images[self.index].name
        record = self._record(name, create=True)

        if record.get("contaminated"):
            self._push_undo(name)
            record["contaminated"] = False
            message = (
                f"'{name}' is no longer marked contaminated. Its counts were "
                f"discarded, so re-run the model if you need them back."
            )
        else:
            existing = len(record.get("boxes") or []) or len(self.canvas.get_boxes())
            if existing:
                answer = QMessageBox.question(
                    self, "Mark as contaminated?",
                    f"'{name}' currently has {existing} box(es).\n\n"
                    f"Marking it contaminated discards those counts and locks "
                    f"the plate. The count summary will show it as "
                    f"contaminated with zero colonies.\n\n"
                    f"You can undo this with {'⌘' if sys.platform == 'darwin' else 'Ctrl+'}Z.",
                    QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
                )
                if answer != QMessageBox.Yes:
                    self._apply_locked_state()
                    return
            self._push_undo(name)
            record["contaminated"] = True
            record["finalized"] = False       # contamination supersedes it
            record["boxes"] = []
            record["annotated"] = False
            record["edited"] = False
            self.canvas.set_locked(False)     # allow the programmatic clear
            self.canvas.set_boxes([])
            message = (
                f"'{name}' marked contaminated — its counts were discarded and "
                f"the plate is locked."
            )

        self._apply_locked_state()
        self._mark_dirty()
        self._refresh_image_row()
        self._refresh_position_label()
        self._refresh_counts()
        self._update_enabled_state()
        self._show_status(message)

    # ------------------------------------------------------------------ undo

    def _snapshot(self, name):
        """Everything about one image that an edit could change."""
        record = self.records.get(name)
        boxes = (self.canvas.get_boxes()
                 if self.index >= 0 and self.images[self.index].name == name
                 else list((record or {}).get("boxes") or []))
        return {
            "name": name,
            "boxes": [dict(b) for b in boxes],
            "flags": {
                key: bool((record or {}).get(key))
                for key in ("annotated", "edited", "finalized", "contaminated")
            },
        }

    def _refresh_pre_edit(self):
        """Remember the current image's state, ready to be pushed on the next edit."""
        self._pre_edit = (
            self._snapshot(self.images[self.index].name) if self.index >= 0 else None
        )

    def _push_undo(self, name=None):
        """Record the state of `name` before it is about to change."""
        if name is None:
            if self.index < 0:
                return
            name = self.images[self.index].name
        self._undo_stack.append(self._snapshot(name))
        del self._undo_stack[:-UNDO_DEPTH]
        self._refresh_undo_action()

    def _refresh_undo_action(self):
        depth = len(self._undo_stack)
        self.action_undo.setEnabled(depth > 0)
        self.action_undo.setText("Undo" if not depth else f"Undo ({depth})")

    def undo(self):
        """Step back one edit. There is deliberately no redo."""
        if not self._undo_stack:
            self._show_status("Nothing to undo")
            return
        state = self._undo_stack.pop()
        name = state["name"]

        row = next((i for i, p in enumerate(self.images) if p.name == name), None)
        if row is None:                       # its folder changed under us
            self._show_status(f"Cannot undo — '{name}' is no longer in the folder.")
            self._refresh_undo_action()
            return
        if row != self.index:
            # Jump to the image the edit belongs to, so the user sees it happen.
            self._go_to(row)

        record = self._record(name, create=True)
        record["boxes"] = [dict(b) for b in state["boxes"]]
        record.update(state["flags"])

        self.canvas.set_locked(False)         # restore first, re-lock after
        self.canvas.set_boxes(record["boxes"])
        self._apply_locked_state()
        self._mark_dirty()
        self._refresh_image_row()
        self._refresh_position_label()
        self._refresh_counts()
        self._update_enabled_state()
        self._refresh_pre_edit()
        self._refresh_undo_action()
        self._show_status(
            f"Undone — '{name}' restored to "
            f"{len(record['boxes'])} box(es) ({status.LABELS[self._status_of(name)]})"
        )

    def _apply_locked_state(self):
        """Push the current image's finalized/contaminated state into the UI."""
        record = None
        if self.index >= 0:
            record = self.records.get(self.images[self.index].name)
        finalized = bool(record and record.get("finalized"))
        contaminated = bool(record and record.get("contaminated"))
        self.canvas.set_locked(status.is_locked(record))
        self.canvas.set_locked_reason(
            status.CONTAMINATED if contaminated
            else (status.FINALIZED if finalized else None)
        )

        for widget, checked in (
            (self.button_finalize, finalized), (self.action_finalize, finalized),
            (self.button_contaminated, contaminated),
            (self.action_contaminated, contaminated),
        ):
            widget.blockSignals(True)
            widget.setChecked(checked)
            widget.blockSignals(False)
        self.button_finalize.setText("Finalized — click to unlock" if finalized
                                     else "Mark as finalized")
        self.button_contaminated.setText(
            "Contaminated — click to undo" if contaminated
            else "Mark as contaminated"
        )
        # Finalizing a plate you have already written off is meaningless.
        self.button_finalize.setEnabled(bool(self.images) and not contaminated)
        self.action_finalize.setEnabled(bool(self.images) and not contaminated)

    def _on_edit_blocked(self):
        contaminated = False
        if self.index >= 0:
            record = self.records.get(self.images[self.index].name)
            contaminated = bool(record and record.get("contaminated"))
        self._show_status(
            "This plate is marked contaminated, so it holds no counts and "
            "cannot be edited. Click 'Contaminated — click to undo' to reopen it."
            if contaminated else
            "This image is finalized, so its annotations are locked. "
            "Click 'Finalized — click to unlock' to make changes."
        )

    # ------------------------------------------------------------ inference

    def _detection_settings(self):
        return {
            "conf": self.spin_conf.value(),
            "tiling": self.check_tiling.isChecked(),
            "tile_size": self.spin_tile.value(),
            "tile_overlap": self.spin_overlap.value(),
        }

    def annotate_current(self):
        if not self._ready_to_annotate():
            return
        path = self.images[self.index]
        if self.canvas.locked:
            QMessageBox.information(
                self, "Image is finalized",
                f"'{path.name}' is locked ({status.LABELS[self._status_of(path.name)]}), "
                "so the model won't overwrite it.\n\nUnlock it first if you want "
                "to re-run detection on it.",
            )
            return
        if self.canvas.get_boxes():
            answer = QMessageBox.question(
                self, "Replace existing boxes?",
                f"'{path.name}' already has {len(self.canvas.get_boxes())} box(es).\n\n"
                "Running the model again will replace them with fresh predictions.",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
            )
            if answer != QMessageBox.Yes:
                return
        self._start_inference([path])

    def annotate_all(self):
        if not self._ready_to_annotate():
            return
        pending, skipped_final = [], 0
        for path in self.images:
            record = self.records.get(path.name) or {}
            if status.is_locked(record):
                skipped_final += 1        # never touch what the user locked
            elif not record.get("annotated"):
                pending.append(path)

        if not pending:
            QMessageBox.information(
                self, "Nothing to do",
                "Every image has already been annotated or is finalized. Use "
                "'Annotate this image' to re-run the model on a single plate.",
            )
            return

        note = (
            f"\n\n{skipped_final} finalized image(s) will be left untouched."
            if skipped_final else ""
        )
        answer = QMessageBox.question(
            self, "Annotate all remaining",
            f"Run the model on {len(pending)} image(s)?{note}\n\n"
            "Large plate images with tiling take a while — you can cancel at "
            "any point and everything finished so far is kept.",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return
        self._start_inference(pending)

    def _ready_to_annotate(self):
        if self.detector is None:
            QMessageBox.information(self, "No model", "Choose a model (.pt) first.")
            return False
        if self.index < 0 or not self.canvas.has_image():
            QMessageBox.information(self, "No image", "Choose an image folder first.")
            return False
        return True

    def _start_inference(self, paths):
        self._store_current()
        self._set_busy(True, "Running the model…")
        self.progress.setVisible(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.button_cancel.setVisible(True)

        self.infer_worker = InferenceWorker(
            self.detector, paths, self._detection_settings(), self
        )
        self.infer_worker.image_started.connect(self._on_image_started)
        self.infer_worker.tile_progress.connect(self._on_tile_progress)
        self.infer_worker.image_done.connect(self._on_image_done)
        self.infer_worker.image_failed.connect(self._on_image_failed)
        self.infer_worker.all_done.connect(self._on_inference_done)
        self.infer_worker.start()

    def _on_image_started(self, name, index, total):
        suffix = f" ({index} of {total})" if total > 1 else ""
        self._show_status(f"Detecting colonies in {name}{suffix}…")

    def _on_tile_progress(self, done, total):
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)
        self.progress.setFormat(
            f"tile %v of %m" if total > 1 else "running…"
        )

    def _on_image_done(self, name, detections):
        self._push_undo(name)     # re-running the model is undoable
        # A fresh model run resets the 'edited by hand' flag: the boxes on screen
        # are the model's again. Settings used are kept per image so the export
        # log can report what actually produced each result.
        self.records[name] = {
            "boxes": detections,
            "annotated": True,
            "edited": False,
            "finalized": bool((self.records.get(name) or {}).get("finalized")),
            "model": self.detector.path.name if self.detector else None,
            "params": self._detection_settings(),
        }
        self._mark_dirty()
        if self.index >= 0 and self.images[self.index].name == name:
            self.canvas.set_boxes(detections)
            self._apply_locked_state()
        row = next((i for i, p in enumerate(self.images) if p.name == name), None)
        if row is not None:
            self._refresh_image_row(row)
        self._refresh_position_label()
        self._refresh_counts()
        self._refresh_pre_edit()
        self._show_status(f"{name}: found {len(detections)} colony/colonies")

    def _on_image_failed(self, name, message):
        QMessageBox.warning(
            self, "Detection failed", f"Could not run the model on '{name}':\n\n{message}"
        )

    def _on_inference_done(self, cancelled):
        self._set_busy(False)
        self.progress.setVisible(False)
        self.button_cancel.setVisible(False)
        self.infer_worker = None
        self._refresh_counts()
        self._update_enabled_state()
        if cancelled:
            self._show_status("Cancelled — annotations completed so far were kept.")

    def cancel_inference(self):
        """The one Cancel button serves whichever long job is running."""
        if self.infer_worker:
            self.infer_worker.cancel()
            self.button_cancel.setEnabled(False)
            self._show_status("Cancelling after the current tile…")
        elif self.image_worker:
            self.image_worker.cancel()
            self.button_cancel.setEnabled(False)
            self._show_status("Cancelling after the current image…")

    def clear_current(self):
        if self.index < 0:
            return
        count = len(self.canvas.get_boxes())
        if not count:
            return
        answer = QMessageBox.question(
            self, "Clear boxes",
            f"Remove all {count} box(es) from '{self.images[self.index].name}'?",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
        )
        if answer == QMessageBox.Yes:
            self.canvas.clear_boxes()

    # ------------------------------------------------------------- settings

    def _save_settings(self):
        """Remember the current setup, if the user asked us to."""
        remember = self.check_remember.isChecked()
        prefs.save({
            "image_folder": str(self.image_folder) if self.image_folder else None,
            "model_path": str(self.detector.path) if self.detector else None,
            "output_folder": str(self.output_folder) if self.output_folder else None,
            "labels_folder": str(self.labels_folder) if self.labels_folder else None,
            "export_name": self.edit_export_name.text() or None,
            "conf": self.spin_conf.value(),
            "tiling": self.check_tiling.isChecked(),
            "tile_size": self.spin_tile.value(),
            "tile_overlap": self.spin_overlap.value(),
            "export_csv": self.check_csv.isChecked(),
            "export_yolo": self.check_yolo.isChecked(),
            "export_images": self.check_images.isChecked(),
            "show_labels": self.check_labels.isChecked(),
            "show_confidence": self.check_conf.isChecked(),
            "sticky_draw": self.check_sticky.isChecked(),
        }, remember=remember)

    def _restore_settings(self):
        """Reapply the last session's setup. Annotations are never restored."""
        remember = prefs.remembering()
        for widget in (self.check_remember, self.action_remember):
            widget.blockSignals(True)
            widget.setChecked(remember)
            widget.blockSignals(False)

        values = prefs.load()
        if not values:
            return

        self.spin_conf.setValue(prefs.get_float(values, "conf", det.DEFAULT_CONF))
        self.check_tiling.setChecked(prefs.get_bool(values, "tiling", True))
        self.spin_tile.setValue(
            prefs.get_int(values, "tile_size", det.DEFAULT_TILE_SIZE)
        )
        self.spin_overlap.setValue(
            prefs.get_float(values, "tile_overlap", det.DEFAULT_TILE_OVERLAP)
        )
        self.check_csv.setChecked(prefs.get_bool(values, "export_csv", True))
        self.check_yolo.setChecked(prefs.get_bool(values, "export_yolo", False))
        self.check_images.setChecked(prefs.get_bool(values, "export_images", True))
        self.check_labels.setChecked(prefs.get_bool(values, "show_labels", True))
        self.check_conf.setChecked(prefs.get_bool(values, "show_confidence", False))
        self.check_sticky.setChecked(prefs.get_bool(values, "sticky_draw", False))
        self.edit_export_name.setText(prefs.get_str(values, "export_name", "") or "")

        restored, missing = [], []

        output = prefs.get_str(values, "output_folder")
        if output and Path(output).is_dir():
            self.output_folder = Path(output)
            self.label_output.setText(
                f"<b>{self.output_folder.name}</b><br>{self.output_folder}"
            )
            self.label_output.setStyleSheet("")
            restored.append("output folder")
        elif output:
            missing.append("output folder")

        folder = prefs.get_str(values, "image_folder")
        if folder and Path(folder).is_dir():
            if self._load_image_folder(Path(folder), quiet=True):
                restored.append(f"{len(self.images)} image(s)")
        elif folder:
            missing.append("image folder")

        label_dir = prefs.get_str(values, "labels_folder")
        if label_dir and Path(label_dir).is_dir() and self.images:
            if self._import_labels(Path(label_dir), quiet=True):
                restored.append("labels")
        elif label_dir:
            missing.append("labels folder")

        model = prefs.get_str(values, "model_path")
        if model and Path(model).is_file():
            self._load_model(model)
            restored.append("model")
        elif model:
            missing.append("model")

        self.dirty = False
        self._refresh_title()
        if restored:
            note = "Restored " + ", ".join(restored)
            if missing:
                note += f" · could not find the previous {', '.join(missing)}"
            note += ". Annotations are not restored — open a project for those."
            self._show_status(note)
            if "model" in restored:
                self._restore_note = note   # re-shown once the model has loaded
        elif missing:
            self._show_status(
                f"The previous {', '.join(missing)} could not be found — choose again."
            )

    # -------------------------------------------------------------- projects

    def _mark_dirty(self):
        if not self.dirty:
            self.dirty = True
            self._refresh_title()
        self._refresh_counts()

    def _refresh_title(self):
        name = self.project_path.name if self.project_path else "Untitled project"
        mark = " •" if self.dirty else ""
        self.setWindowTitle(f"{name}{mark} — {APP_NAME} {__version__}")

    def _project_state(self):
        return dict(
            image_folder=self.image_folder,
            model_path=self.detector.path if self.detector else None,
            output_folder=self.output_folder,
            export_options={
                "csv": self.check_csv.isChecked(),
                "yolo": self.check_yolo.isChecked(),
            },
            detection=self._detection_settings(),
            class_names=self.canvas.class_names,
            records=self.records,
            image_sizes=self.image_sizes,
        )

    def new_project(self):
        if not self._confirm_discard("Starting a new project"):
            return
        self.image_folder = None
        self.output_folder = None
        self.images = []
        self.index = -1
        self.records = {}
        self.image_sizes = {}
        self.project_path = None
        self.dirty = False
        self.labels_folder = None
        self._undo_stack = []
        self._pre_edit = None
        self._refresh_undo_action()

        self.canvas.clear_image()
        self.list_images.clear()
        self.label_labels.setText("No labels loaded")
        self.label_labels.setStyleSheet(HINT)
        self.label_images.setText("No folder chosen")
        self.label_images.setStyleSheet(HINT)
        self.label_output.setText("No folder chosen")
        self.label_output.setStyleSheet(HINT)
        self._refresh_position_label()
        self._refresh_counts()
        self._apply_locked_state()
        self._refresh_title()
        self._update_enabled_state()
        self._show_status("New project. Choose an image folder to begin.")

    def save_project(self):
        if self.project_path is None:
            return self.save_project_as()
        return self._write_project(self.project_path)

    def save_project_as(self):
        suggested = str(
            self.project_path
            or (self.image_folder or Path.home())
            / f"{(self.image_folder.name if self.image_folder else 'cfu')}{project.EXTENSION}"
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Save project as", suggested, project.FILE_FILTER
        )
        if not path:
            return False
        return self._write_project(Path(path))

    def _write_project(self, path):
        self._store_current()
        try:
            written = project.save(path, **self._project_state())
        except OSError as exc:
            QMessageBox.critical(self, "Could not save project", str(exc))
            return False
        self.project_path = written
        self.dirty = False
        self._refresh_title()
        self._refresh_counts()
        self._show_status(f"Project saved to {written}")
        return True

    def open_project(self, path=None):
        if not self._confirm_discard("Opening another project"):
            return
        if not path:
            path, _ = QFileDialog.getOpenFileName(
                self, "Open project", str(Path.home()), project.FILE_FILTER
            )
            if not path:
                return
        try:
            data = project.load(path)
        except project.ProjectError as exc:
            QMessageBox.critical(self, "Cannot open project", str(exc))
            return

        self.records = data["records"]
        self.image_sizes = data["image_sizes"]
        self._undo_stack = []
        self._pre_edit = None
        self._refresh_undo_action()
        self.project_path = Path(path)
        self.index = -1
        self.images = []
        self.canvas.clear_image()
        self.list_images.clear()

        # Detection settings and export choices first, so a re-run matches.
        settings = data["detection"]
        if "conf" in settings:
            self.spin_conf.setValue(float(settings["conf"]))
        if "tiling" in settings:
            self.check_tiling.setChecked(bool(settings["tiling"]))
        if "tile_size" in settings:
            self.spin_tile.setValue(int(settings["tile_size"]))
        if "tile_overlap" in settings:
            self.spin_overlap.setValue(float(settings["tile_overlap"]))
        options = data["export_options"]
        self.check_csv.setChecked(bool(options.get("csv", True)))
        self.check_yolo.setChecked(bool(options.get("yolo", False)))

        # Class names from the project, so saved boxes are readable even if the
        # model file has since moved.
        if data["class_names"]:
            self._set_class_names(data["class_names"])

        problems = []

        out = data["output_folder"]
        if out and Path(out).is_dir():
            self.output_folder = Path(out)
            self.label_output.setText(f"<b>{self.output_folder.name}</b><br>{self.output_folder}")
            self.label_output.setStyleSheet("")
        elif out:
            problems.append(f"Output folder no longer exists:\n    {out}")

        folder = data["image_folder"]
        if folder and Path(folder).is_dir():
            self._load_image_folder(Path(folder), keep_records=True)
            known = {p.name for p in self.images}
            missing = sorted(set(self.records) - known)
            added = sorted(known - set(self.records))
            if missing:
                problems.append(
                    f"{len(missing)} annotated image(s) from the project are no "
                    f"longer in the folder (their annotations are kept in the "
                    f"project but won't be exported):\n    "
                    + ", ".join(missing[:6]) + ("…" if len(missing) > 6 else "")
                )
            if added:
                problems.append(
                    f"{len(added)} new image(s) appeared in the folder since the "
                    f"project was saved; they show as not annotated."
                )
        elif folder:
            problems.append(f"Image folder no longer exists:\n    {folder}")

        self.dirty = False
        self._refresh_title()
        self._refresh_counts()
        self._show_status(
            f"Opened {self.project_path.name}"
            + (f" (saved {data['saved_at']})" if data.get("saved_at") else "")
        )

        model = data["model_path"]
        if model and Path(model).is_file():
            self._load_model(model)
        elif model:
            problems.append(
                f"Model file no longer exists:\n    {model}\n"
                "Choose it again to run detection; existing annotations are "
                "still editable."
            )

        if problems:
            QMessageBox.warning(
                self, "Project opened with warnings",
                "The project opened, but:\n\n" + "\n\n".join(problems),
            )

    # --------------------------------------------------------------- export

    def export_now(self):
        if not self.output_folder:
            QMessageBox.information(
                self, "No output folder", "Choose an output folder first."
            )
            return
        want_csv = self.check_csv.isChecked()
        want_yolo = self.check_yolo.isChecked()
        want_images = self.check_images.isChecked()
        if not (want_csv or want_yolo or want_images):
            QMessageBox.information(
                self, "Nothing selected",
                "Tick at least one output — the count summary, YOLO labels, "
                "annotated images, or any combination.",
            )
            return

        self._store_current()
        if want_yolo:
            self._ensure_image_sizes()

        try:
            folder = export.make_export_dir(
                self.output_folder, self.edit_export_name.text()
            )
        except OSError as exc:
            QMessageBox.critical(
                self, "Export failed",
                f"Could not create an export folder inside "
                f"{self.output_folder}:\n\n{exc}",
            )
            return

        class_names = (
            self.detector.class_names if self.detector
            else [f"class_{i}" for i in range(len(self.canvas.class_names))]
        )
        image_names = [p.name for p in self.images]
        written = []
        try:
            if want_csv:
                export.write_csv(folder, image_names, class_names, self.records)
                written.append(export.CSV_NAME)
            if want_yolo:
                _, count = export.write_yolo(
                    folder, class_names, self.records, self.image_sizes
                )
                written.append(f"{export.YOLO_DIRNAME}/  ({count} label file(s))")
                expected = sum(1 for r in self.records.values() if r.get("annotated"))
                if count < expected:
                    QMessageBox.warning(
                        self, "Some YOLO labels were skipped",
                        f"{expected - count} annotated image(s) could not be "
                        "written as YOLO labels because their pixel dimensions "
                        "could not be read. The count summary is unaffected.",
                    )
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return

        self._pending_export = {
            "folder": folder,
            "written": written,
            "class_names": class_names,
            "image_names": image_names,
        }
        self._save_settings()

        if want_images:
            self._export_annotated_images(folder, class_names)
        else:
            self._finish_export()

    def _export_annotated_images(self, folder, class_names):
        """Render the annotated plates in the background, then finish the export."""
        jobs = []
        target = export.images_dir(folder)
        for path in self.images:
            record = self.records.get(path.name)
            if not record or not record.get("annotated"):
                continue
            if record.get("contaminated"):
                continue          # nothing to draw on a discarded plate
            jobs.append((path, target / render.output_name(path.name), record["boxes"]))

        if not jobs:
            self._finish_export()
            return

        target.mkdir(parents=True, exist_ok=True)
        self._set_busy(True, f"Drawing annotations onto {len(jobs)} image(s)…")
        self.progress.setVisible(True)
        self.progress.setRange(0, len(jobs))
        self.progress.setValue(0)
        self.button_cancel.setVisible(True)

        self.image_worker = ImageExportWorker(
            jobs, class_names, self.check_conf.isChecked(), self
        )
        self.image_worker.progress.connect(self._on_image_export_progress)
        self.image_worker.finished_all.connect(self._on_images_exported)
        self.image_worker.start()

    def _on_image_export_progress(self, done, total, name):
        self.progress.setValue(done)
        self.progress.setFormat("image %v of %m")
        self._show_status(f"Drawing annotations onto {name} ({done} of {total})…")

    def _on_images_exported(self, written, errors):
        self._set_busy(False)
        self.progress.setVisible(False)
        self.button_cancel.setVisible(False)
        self.image_worker = None
        if written:
            self._pending_export["written"].append(
                f"{export.IMAGES_DIRNAME}/  ({written} image(s))"
            )
        if errors:
            QMessageBox.warning(
                self, "Some annotated images were not written",
                f"{len(errors)} image(s) could not be saved:\n\n"
                + "\n".join(f"    {e}" for e in errors[:8])
                + ("\n    …" if len(errors) > 8 else ""),
            )
        self._finish_export()

    def _finish_export(self):
        """Write the run log last, so it can list everything that was produced."""
        pending, self._pending_export = self._pending_export, None
        folder = pending["folder"]
        try:
            export.write_run_info(
                folder,
                app_version=__version__,
                project_path=self.project_path,
                image_folder=self.image_folder,
                image_names=pending["image_names"],
                records=self.records,
                class_names=pending["class_names"],
                model_info=(
                    {"path": str(self.detector.path), "task": self.detector.task}
                    if self.detector else None
                ),
                current_settings=self._detection_settings(),
                outputs_written=pending["written"],
            )
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return

        annotated = sum(1 for r in self.records.values() if r.get("annotated"))
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("Export complete")
        box.setText(f"Exported to <b>{folder.name}</b>")
        box.setInformativeText(
            f"{annotated} of {len(self.images)} image(s) annotated.\n\n"
            f"In {folder}:\n"
            + "\n".join(f"    {w}" for w in pending["written"])
            + f"\n    {export.INFO_NAME}"
        )
        reveal = box.addButton("Show in Finder", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Ok)
        box.exec_()
        if box.clickedButton() is reveal:
            self._reveal(folder)
        self._show_status(f"Exported to {folder}")

    @staticmethod
    def _reveal(path):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _ensure_image_sizes(self):
        """YOLO labels are normalised, so every annotated image needs its size.

        Sizes are recorded when an image is displayed, but 'Annotate all' can
        annotate images the user never opened — without this, their label files
        would be silently missing from the export. QImageReader reads just the
        header, so this stays cheap even for 10000-pixel plates.
        """
        for path in self.images:
            if path.name in self.image_sizes:
                continue
            record = self.records.get(path.name)
            if not record or not record.get("annotated"):
                continue
            size = QImageReader(str(path)).size()
            if size.isValid():
                self.image_sizes[path.name] = (size.width(), size.height())

    # ---------------------------------------------------------------- misc

    def _set_mode(self, mode):
        self.canvas.set_mode(mode)
        self._sync_mode_buttons(mode)
        self._show_status(
            "Draw mode — drag on the image to add a box"
            if mode == MODE_DRAW
            else "Select mode — click a box to edit it, drag empty space to pan"
        )

    def _sync_mode_buttons(self, mode):
        """Reflect the canvas's mode in the toolbar without re-triggering it."""
        for action, wanted in (
            (self.action_select, MODE_SELECT), (self.action_draw, MODE_DRAW),
        ):
            action.blockSignals(True)
            action.setChecked(mode == wanted)
            action.blockSignals(False)

    def _on_sticky_toggled(self, on):
        self.canvas.sticky_draw = on
        self._show_status(
            "Draw mode will stay on after each box"
            if on else "Draw mode returns to Select after each box"
        )

    def _on_remember_toggled(self, on):
        self.action_remember.blockSignals(True)
        self.action_remember.setChecked(on)
        self.action_remember.blockSignals(False)
        self._save_settings()
        self._show_status(
            "Settings will be restored next time you start the app"
            if on else "Settings will not be remembered"
        )

    def _on_remember_action(self, on):
        """The menu item and the sidebar checkbox are the same setting."""
        self.check_remember.setChecked(on)

    def _toggle_labels(self, on):
        self.canvas.show_labels = on
        self.canvas.viewport().update()

    def _toggle_confidence(self, on):
        self.canvas.show_confidence = on
        self.canvas.viewport().update()

    def _toggle_tiling(self, on):
        self.spin_tile.setEnabled(on)
        self.spin_overlap.setEnabled(on)

    def _on_class_row_changed(self, row):
        """The class list only chooses what class new boxes get.

        Relabelling an existing box is always an explicit act (number key,
        double-click, or the Relabel button), so picking the next class to draw
        with can never silently change a box you already have selected.
        """
        if row < 0:
            return
        self.canvas.set_active_class(row)
        self._show_status(f"Drawing new boxes as '{self.canvas.class_name(row)}'")

    def _apply_class_to_selection(self):
        row = self.list_classes.currentRow()
        if row >= 0:
            self.canvas.set_class_of_selected(row)

    def _on_selection_changed(self):
        count = self.canvas.selected_count()
        editable = not self.canvas.locked
        self.action_delete.setEnabled(count > 0 and editable)
        self.button_apply_class.setEnabled(count > 0 and editable)
        if count == 1:
            selected = next(i for i in self.canvas.box_items() if i.isSelected())
            self.label_selected.setText(
                f"Selected: <b>{self.canvas.class_name(selected.cls_id)}</b>"
            )
        elif count > 1:
            self.label_selected.setText(f"Selected: {count} boxes")
        else:
            self.label_selected.setText("No box selected")

    def _on_boxes_changed(self):
        self._refresh_counts()
        self._on_selection_changed()   # a relabel changes the 'Selected:' readout
        if self.index >= 0:
            self._store_current()
            self._refresh_image_row()
            self._refresh_position_label()

    def _on_user_edited(self):
        """Fires only for changes the user made by hand, not for model output."""
        if self.index < 0:
            return
        name = self.images[self.index].name
        # boxes_changed has already stored the new state, so the undo entry has
        # to come from the snapshot taken when this image last settled.
        if self._pre_edit and self._pre_edit["name"] == name:
            self._undo_stack.append(self._pre_edit)
            del self._undo_stack[:-UNDO_DEPTH]
            self._refresh_undo_action()
        record = self._record(name, create=True)
        record["edited"] = True
        self._mark_dirty()
        self._refresh_image_row()
        self._refresh_position_label()
        self._refresh_counts()

    def _on_cursor_moved(self, x, y):
        width, height = self.canvas.image_size()
        if 0 <= x <= width and 0 <= y <= height:
            self.coord_label.setText(f"x {x:.0f}, y {y:.0f}")
        else:
            self.coord_label.setText("")
        self.zoom_label.setText(f"{self.canvas.zoom_percent():.0f}%")

    def _refresh_counts(self):
        names = self.canvas.class_names or []
        counts = self.canvas.counts_by_class() if names else []
        self.table_counts.setRowCount(len(names) + (1 if names else 0))
        for row, name in enumerate(names):
            label = QTableWidgetItem(name)
            label.setIcon(_swatch(class_color(row)))
            self.table_counts.setItem(row, 0, label)
            self.table_counts.setItem(row, 1, QTableWidgetItem(str(counts[row])))
        if names:
            total = QTableWidgetItem("Total")
            font = total.font()
            font.setBold(True)
            total.setFont(font)
            value = QTableWidgetItem(str(sum(counts)))
            value.setFont(font)
            self.table_counts.setItem(len(names), 0, total)
            self.table_counts.setItem(len(names), 1, value)

        if self.images:
            tally = {}
            for path in self.images:
                key = self._status_of(path.name)
                tally[key] = tally.get(key, 0) + 1
            parts = [
                f"{tally[key]} {status.LABELS[key].split(' (')[0].lower()}"
                for key in status.ORDER if key in tally
            ]
            suffix = "  ·  unsaved changes" if self.dirty else ""
            self.label_progress_summary.setText(
                f"{len(self.images)} image(s): " + ", ".join(parts) + suffix
            )
        else:
            self.label_progress_summary.setText("")

    def _set_busy(self, busy, message=None):
        self._busy = busy
        for widget in (
            self.button_annotate, self.button_annotate_all, self.action_clear,
            self.button_prev, self.button_next, self.button_export,
        ):
            widget.setEnabled(not busy)
        self.button_cancel.setEnabled(True)
        if message:
            self._show_status(message)
        if not busy:
            self._update_enabled_state()

    def _update_enabled_state(self):
        if getattr(self, "_busy", False):
            return
        has_images = bool(self.images)
        has_model = self.detector is not None
        self.button_prev.setEnabled(has_images and self.index > 0)
        self.button_next.setEnabled(has_images and self.index < len(self.images) - 1)
        self.button_annotate.setEnabled(has_images and has_model)
        self.button_annotate_all.setEnabled(has_images and has_model)
        locked = self.canvas.locked
        self.action_clear.setEnabled(
            has_images and bool(self.canvas.get_boxes()) and not locked
        )
        self.button_export.setEnabled(bool(self.output_folder) and has_images)
        self.action_draw.setEnabled(has_images and not locked)
        self.action_delete.setEnabled(self.canvas.selected_count() > 0 and not locked)
        self.button_contaminated.setEnabled(has_images)
        self.action_contaminated.setEnabled(has_images)
        self.button_labels.setEnabled(has_images)
        self.button_apply_class.setEnabled(
            self.canvas.selected_count() > 0 and not locked
        )
        if locked and self.canvas.mode == MODE_DRAW:
            self._set_mode(MODE_SELECT)

    def _show_status(self, message):
        self.status_label.setText(message)

    def _confirm_discard(self, what):
        """Offer to save the project before an action that would lose work."""
        self._store_current()
        if not self.dirty:
            return True
        answer = QMessageBox.question(
            self, "Save project first?",
            f"{what} will discard changes you haven't saved.\n\n"
            "Save the project now?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if answer == QMessageBox.Cancel:
            return False
        if answer == QMessageBox.Save:
            return self.save_project()
        return True

    def closeEvent(self, event):
        for worker in (self.infer_worker, self.image_worker):
            if worker:
                worker.cancel()
                worker.wait(5000)
        if not self._confirm_discard("Quitting"):
            event.ignore()
            return
        self._save_settings()
        self.canvas.shutdown()
        event.accept()
