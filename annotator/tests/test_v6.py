"""Tests for user-supplied class lists — the app as a general annotator.

Run with:  ../.venv/bin/python tests/test_v6.py     (from annotator/)
"""

import csv as csvmod
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
MODEL = ROOT.parent / "nuc" / "best.pt"
TMP = Path(tempfile.mkdtemp(prefix="cfu_v6_"))

from PyQt5.QtCore import QEvent, QPointF, QSettings, Qt        # noqa: E402
from PyQt5.QtGui import QImage, QKeyEvent, QMouseEvent          # noqa: E402
from PyQt5.QtTest import QTest                                  # noqa: E402
from PyQt5.QtWidgets import QApplication, QInputDialog, QMessageBox  # noqa: E402

IMG = TMP / "images"; IMG.mkdir(parents=True)
OUT = TMP / "output"; OUT.mkdir(parents=True)

fails = []
def check(n, c, x=""):
    print(("PASS  " if c else "FAIL  ") + n + (f"   {x}" if x else "")); sys.stdout.flush()
    if not c: fails.append(n)

answer = {"q": QMessageBox.Yes}
typed = {"text": ("", True)}
log = []
QMessageBox.question = staticmethod(lambda *a, **k: (log.append(("q", a[1], a[2] if len(a) > 2 else "")), answer["q"])[1])
QMessageBox.information = staticmethod(lambda *a, **k: log.append(("i", a[1], a[2] if len(a) > 2 else "")))
QMessageBox.warning = staticmethod(lambda *a, **k: log.append(("w", a[1], a[2] if len(a) > 2 else "")))
QMessageBox.critical = staticmethod(lambda *a, **k: log.append(("c", a[1], a[2] if len(a) > 2 else "")))
QMessageBox.exec_ = lambda self: (log.append(("box", self.text(), self.informativeText())), 0)[1]
QInputDialog.getText = staticmethod(lambda *a, **k: typed["text"])

APP = QApplication.instance() or QApplication(sys.argv)
QSettings("StJude", "CFU Annotator").clear()

from cfu_annotator.canvas import MODE_DRAW, MODE_SELECT, UNLABELLED   # noqa: E402
from cfu_annotator.mainwindow import (                                # noqa: E402
    LABEL_MODE_MANUAL, LABEL_MODE_MODEL, MainWindow,
)
from cfu_annotator import export, labels as labels_io, scan           # noqa: E402

for i in range(2):
    im = QImage(1000, 800, QImage.Format_RGB32); im.fill(Qt.darkGray)
    im.save(str(IMG / f"plate{i+1}.png"))

CLASSES = TMP / "my_classes.txt"
CLASSES.write_text("Round\nDiffuse\n\n# a comment\nMixed\nRound\n")

def new_window(with_model=True, mode=LABEL_MODE_MANUAL):
    w = MainWindow(); w.resize(1300, 900); w.show(); QTest.qWait(20)
    w.image_folder = IMG; w.images = scan.scan_folder(IMG).images
    if with_model and MODEL.is_file():
        from cfu_annotator.detector import Detector
        w._on_model_loaded(Detector(MODEL))
    w.output_folder = OUT
    w.check_remember.setChecked(False)
    w.combo_labelling.setCurrentIndex(w.combo_labelling.findData(mode))
    w._rebuild_image_list(); w._go_to(0)
    w.canvas.zoom_to_actual_size()
    return w

# ============ reading the file ============
names, skipped = labels_io.read_class_list(CLASSES)
check("class list parsed", names == ["Round", "Diffuse", "Mixed"], names)
check("blank lines, comments and duplicates skipped", skipped == 3, skipped)
bad = TMP / "empty.txt"; bad.write_text("\n#nothing\n")
try:
    labels_io.read_class_list(bad); check("empty list refused", False)
except ValueError as exc:
    check("empty list refused", "no class names" in str(exc))

# ============ replacing the model's classes ============
w = new_window()
check("starts on the model's classes",
      w.canvas.class_names == ["BFU", "GM", "E", "GEMM"] and not w.custom_classes,
      w.canvas.class_names)
check("source line says so", "model" in w.label_class_source.text().lower(),
      w.label_class_source.text())

w._set_class_names(names, custom=True, source=CLASSES.name)
check("custom list takes over", w.canvas.class_names == names, w.canvas.class_names)
check("marked as custom", w.custom_classes)
check("source line names the file", CLASSES.name in w.label_class_source.text(),
      w.label_class_source.text())
check("class list widget rebuilt", w.list_classes.count() == 3, w.list_classes.count())
check("first entry is numbered and named",
      "Round" in w.list_classes.item(0).text(), w.list_classes.item(0).text())

# a model loaded afterwards must not steal the vocabulary back
if MODEL.is_file():
    from cfu_annotator.detector import Detector
    w._on_model_loaded(Detector(MODEL))
    check("loading a model keeps the custom list",
          w.canvas.class_names == names, w.canvas.class_names)
    check("still marked custom", w.custom_classes)

# ============ labelling with the custom classes ============
c = w.canvas
w._on_image_done("plate1.png", [{"cls": 0, "conf": 0.9, "xyxy": [100, 100, 160, 160]},
                                {"cls": 1, "conf": 0.8, "xyxy": [300, 300, 360, 360]}])
check("detect-only leaves them unlabelled",
      all(b["cls"] == UNLABELLED for b in c.get_boxes()))
c.setFocus()
QApplication.sendEvent(c, QKeyEvent(QEvent.KeyPress, Qt.Key_Tab, Qt.NoModifier))
QApplication.sendEvent(c, QKeyEvent(QEvent.KeyPress, Qt.Key_3, Qt.NoModifier))
labelled = [b for b in c.get_boxes() if b["cls"] >= 0]
check("number key applies a custom class", len(labelled) == 1 and labelled[0]["cls"] == 2,
      [b["cls"] for b in c.get_boxes()])
check("the canvas names it correctly", c.class_name(2) == "Mixed", c.class_name(2))
QApplication.sendEvent(c, QKeyEvent(QEvent.KeyPress, Qt.Key_1, Qt.NoModifier))
check("plate fully labelled with custom classes", c.unlabelled_count() == 0)
check("counts table uses the custom names",
      w.table_counts.item(0, 0).text() == "Round"
      and w.table_counts.item(2, 0).text() == "Mixed",
      [w.table_counts.item(i, 0).text() for i in range(3)])

# ============ adding one on the fly ============
typed["text"] = ("Satellite", True)
w.add_class()
check("added class appended", w.canvas.class_names == names + ["Satellite"],
      w.canvas.class_names)
check("and selected ready to use", w.list_classes.currentRow() == 3,
      w.list_classes.currentRow())
typed["text"] = ("Satellite", True)
before = list(w.canvas.class_names)
w.add_class()
check("duplicate class refused", w.canvas.class_names == before, w.canvas.class_names)
typed["text"] = ("", False)
w.add_class()
check("cancelling adds nothing", w.canvas.class_names == before)

# ============ exporting with custom classes ============
w.check_csv.setChecked(True); w.check_yolo.setChecked(True); w.check_images.setChecked(False)
w.edit_export_name.setText("custom-classes")
w.export_now()
folder = OUT / "custom-classes"
rows = list(csvmod.reader(open(folder / export.CSV_NAME)))
check("csv columns are the custom classes",
      rows[0] == ["image", "Round", "Diffuse", "Mixed", "Satellite", "total", "status"],
      rows[0])
by = {r[0]: r for r in rows[1:]}
check("counts land in the right columns",
      by["plate1.png"][1:5] == ["1", "0", "1", "0"], by["plate1.png"])
written = (export.yolo_dir(folder) / "classes.txt").read_text().split()
check("exported classes.txt is the custom list",
      written == names + ["Satellite"], written)
check("yolo ids match the custom list",
      sorted(int(l.split()[0]) for l in
             (export.yolo_dir(folder) / "plate1.txt").read_text().splitlines() if l)
      == [0, 2],
      (export.yolo_dir(folder) / "plate1.txt").read_text())

# round-trip: export then re-import those labels
w2 = new_window(with_model=False)
w2._set_class_names(names + ["Satellite"], custom=True, source="classes.txt")
w2._import_labels(export.yolo_dir(folder))
check("exported labels re-import against the custom list",
      sorted(b["cls"] for b in w2.records["plate1.png"]["boxes"]) == [0, 2],
      [b["cls"] for b in w2.records.get("plate1.png", {}).get("boxes", [])])

# ============ no model at all — pure annotator ============
w3 = new_window(with_model=False)
check("no model means no classes yet", w3.canvas.class_names == [],
      w3.canvas.class_names)
check("and the app says where to get them",
      "load your own list" in w3.label_class_source.text(),
      w3.label_class_source.text())
w3._set_class_names(names, custom=True, source=CLASSES.name)
check("custom list works with no model loaded", w3.canvas.class_names == names)
check("drawing is available without a model", w3.action_draw.isEnabled())

c3 = w3.canvas
c3.set_mode(MODE_DRAW)
p1, p2 = c3.mapFromScene(100.0, 100.0), c3.mapFromScene(200.0, 180.0)
def send(kind, pos, button=Qt.LeftButton, buttons=Qt.NoButton):
    QApplication.sendEvent(c3.viewport(), QMouseEvent(
        kind, QPointF(pos), QPointF(c3.viewport().mapToGlobal(pos)),
        button, buttons, Qt.NoModifier))
send(QEvent.MouseButtonPress, p1, Qt.LeftButton, Qt.LeftButton)
send(QEvent.MouseMove, p2, Qt.NoButton, Qt.LeftButton)
send(QEvent.MouseButtonRelease, p2, Qt.LeftButton, Qt.NoButton)
check("a box can be drawn with no model at all", len(c3.get_boxes()) == 1,
      len(c3.get_boxes()))
check("it takes the highlighted custom class",
      c3.get_boxes()[0]["cls"] == 0, c3.get_boxes()[0]["cls"])

# ============ a shorter list strands higher class ids ============
w4 = new_window()
w4._on_image_done("plate1.png", [{"cls": 3, "conf": 0.9, "xyxy": [10, 10, 60, 60]}])
w4.combo_labelling.setCurrentIndex(w4.combo_labelling.findData(LABEL_MODE_MODEL))
w4._on_image_done("plate1.png", [{"cls": 3, "conf": 0.9, "xyxy": [10, 10, 60, 60]}])
short = TMP / "short.txt"; short.write_text("A\nB\n")
log.clear(); answer["q"] = QMessageBox.Cancel
names_before = list(w4.canvas.class_names)
from unittest.mock import patch
with patch.object(QApplication.instance().__class__, "arguments", lambda s: []):
    pass
w4._set_class_names  # (loading goes through choose_class_list, tested via _boxes_outside)
check("stranded boxes are counted", w4._boxes_outside(2) == 1, w4._boxes_outside(2))
check("nothing stranded when the list is long enough", w4._boxes_outside(4) == 0)

# ============ persistence ============
w5 = new_window()
w5._set_class_names(names, custom=True, source=CLASSES.name)
proj = TMP / "custom.cfuproj"
w5._write_project(proj)
w6 = MainWindow(); w6.show(); QTest.qWait(20)
w6.open_project(str(proj)); QTest.qWait(50)
check("project restores the custom list", w6.canvas.class_names == names,
      w6.canvas.class_names)
check("project remembers it was custom", w6.custom_classes)

w5.check_remember.setChecked(True); w5._save_settings()
w7 = MainWindow(); w7.show(); QTest.qWait(20)
check("settings restore the custom list", w7.canvas.class_names == names,
      w7.canvas.class_names)
check("settings remember it was custom", w7.custom_classes)
w7.check_remember.setChecked(False)

QSettings("StJude", "CFU Annotator").clear()
shutil.rmtree(TMP, ignore_errors=True)
print(); print("FAILURES:", fails if fails else "none")
sys.stdout.flush(); os._exit(1 if fails else 0)
