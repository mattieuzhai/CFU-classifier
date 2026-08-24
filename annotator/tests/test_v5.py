"""Tests for the three labelling modes and the unlabelled-box export block.

Run with:  ../.venv/bin/python tests/test_v5.py     (from annotator/)
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
TMP = Path(tempfile.mkdtemp(prefix="cfu_v5_"))

from PyQt5.QtCore import QEvent, QPointF, QSettings, Qt          # noqa: E402
from PyQt5.QtGui import QImage, QKeyEvent, QMouseEvent           # noqa: E402
from PyQt5.QtTest import QTest                                    # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox             # noqa: E402

IMG = TMP / "images"; IMG.mkdir(parents=True)
OUT = TMP / "output"; OUT.mkdir(parents=True)

fails = []
def check(n, c, x=""):
    print(("PASS  " if c else "FAIL  ") + n + (f"   {x}" if x else "")); sys.stdout.flush()
    if not c: fails.append(n)

answer = {"q": QMessageBox.Yes}
clicked = {"which": None}
log = []
QMessageBox.question = staticmethod(lambda *a, **k: (log.append(("q", a[1], a[2] if len(a) > 2 else "")), answer["q"])[1])
QMessageBox.information = staticmethod(lambda *a, **k: log.append(("i", a[1], a[2] if len(a) > 2 else "")))
QMessageBox.warning = staticmethod(lambda *a, **k: log.append(("w", a[1], a[2] if len(a) > 2 else "")))
QMessageBox.critical = staticmethod(lambda *a, **k: log.append(("c", a[1], a[2] if len(a) > 2 else "")))
def fake_exec(self):
    log.append(("box", self.windowTitle(), self.text(), self.informativeText()))
    return 0
QMessageBox.exec_ = fake_exec
QMessageBox.clickedButton = lambda self: clicked["which"]

APP = QApplication.instance() or QApplication(sys.argv)
QSettings("StJude", "CFU Annotator").clear()
if not MODEL.is_file():
    print(f"SKIP  every test   (model not found at {MODEL})"); sys.exit(0)

from cfu_annotator.canvas import UNLABELLED, class_color                       # noqa: E402
from cfu_annotator.detector import Detector                                    # noqa: E402
from cfu_annotator.mainwindow import (                                         # noqa: E402
    LABEL_MODE_MANUAL, LABEL_MODE_MODEL, LABEL_MODE_SUGGEST, MainWindow,
)
from cfu_annotator import export, scan                                         # noqa: E402

for i in range(2):
    im = QImage(1000, 800, QImage.Format_RGB32); im.fill(Qt.darkGray)
    im.save(str(IMG / f"plate{i+1}.png"))
det = Detector(MODEL)
DETS = [{"cls": 0, "conf": 0.9, "xyxy": [100, 100, 160, 160]},
        {"cls": 1, "conf": 0.8, "xyxy": [300, 300, 360, 360]},
        {"cls": 2, "conf": 0.7, "xyxy": [500, 500, 560, 560]}]

def new_window(mode=LABEL_MODE_MODEL):
    w = MainWindow(); w.resize(1400, 900); w.show(); QTest.qWait(20)
    res = scan.scan_folder(IMG)
    w.image_folder = IMG; w.images = res.images
    w._on_model_loaded(det); w.output_folder = OUT
    w.check_remember.setChecked(False)
    w.combo_labelling.setCurrentIndex(w.combo_labelling.findData(mode))
    w._rebuild_image_list(); w._go_to(0)
    w.canvas.zoom_to_actual_size()
    return w

def key(canvas, k):
    QApplication.sendEvent(canvas, QKeyEvent(QEvent.KeyPress, k, Qt.NoModifier))

# ============ mode 1: model labels (unchanged behaviour) ============
w = new_window(LABEL_MODE_MODEL)
w._on_image_done("plate1.png", DETS)
boxes = w.canvas.get_boxes()
check("model mode keeps the predicted classes",
      sorted(b["cls"] for b in boxes) == [0, 1, 2], [b["cls"] for b in boxes])
check("model mode marks nothing as unconfirmed", w.canvas.unconfirmed_count() == 0)
check("model mode leaves nothing unlabelled", w.canvas.unlabelled_count() == 0)
check("counts table totals normally", w.table_counts.item(4, 1).text() == "3")

# ============ mode 2: detect only ============
w2 = new_window(LABEL_MODE_MANUAL)
w2._on_image_done("plate1.png", DETS)
boxes = w2.canvas.get_boxes()
check("detect-only discards every class",
      all(b["cls"] == UNLABELLED for b in boxes), [b["cls"] for b in boxes])
check("detect-only keeps the geometry",
      sorted(tuple(b["xyxy"]) for b in boxes)
      == sorted(tuple(d["xyxy"]) for d in DETS))
check("unlabelled counted", w2.canvas.unlabelled_count() == 3)
check("unlabelled boxes are grey",
      class_color(UNLABELLED).name() == "#8d8d8d", class_color(UNLABELLED).name())
check("unlabelled excluded from the class totals",
      w2.table_counts.item(4, 1).text() == "0", w2.table_counts.item(4, 1).text())
check("counts panel flags the outstanding work",
      w2.label_pending.isVisible() and "3 unlabelled" in w2.label_pending.text(),
      w2.label_pending.text())
check("boxes render as needing attention",
      all(i.needs_attention() for i in w2.canvas.box_items()))

# Tab walks them; number keys label and auto-advance
c = w2.canvas
c.setFocus()
key(c, Qt.Key_Tab)
check("Tab selects a box needing a label", c.selected_count() == 1)
first = next(i for i in c.box_items() if i.isSelected())
key(c, Qt.Key_1)
check("number key labels it", first.cls_id == 0, first.cls_id)
check("labelling auto-advances to the next one",
      c.selected_count() == 1 and not next(i for i in c.box_items() if i.isSelected()) is first)
check("one down, two to go", c.unlabelled_count() == 2, c.unlabelled_count())
key(c, Qt.Key_2); key(c, Qt.Key_3)
check("whole plate labelled with one keystroke each", c.unlabelled_count() == 0)
check("labels landed on the right boxes",
      sorted(b["cls"] for b in c.get_boxes()) == [0, 1, 2],
      [b["cls"] for b in c.get_boxes()])
check("pending readout disappears when done", not w2.label_pending.isVisible())
check("counts table now totals them", w2.table_counts.item(4, 1).text() == "3")

# ============ mode 3: suggestions ============
w3 = new_window(LABEL_MODE_SUGGEST)
w3._on_image_done("plate1.png", DETS)
c3 = w3.canvas
check("suggest mode keeps the predicted classes",
      sorted(b["cls"] for b in c3.get_boxes()) == [0, 1, 2])
check("suggest mode marks them unconfirmed", c3.unconfirmed_count() == 3)
check("suggest mode leaves nothing unlabelled", c3.unlabelled_count() == 0)
check("suggested boxes need attention",
      all(i.needs_attention() for i in c3.box_items()))
check("counts include suggestions", w3.table_counts.item(4, 1).text() == "3")
check("panel flags unconfirmed", "3 unconfirmed" in w3.label_pending.text(),
      w3.label_pending.text())

c3.setFocus(); key(c3, Qt.Key_Tab)
sel = next(i for i in c3.box_items() if i.isSelected())
was = sel.cls_id
key(c3, Qt.Key_Return)
check("Enter accepts the suggestion", not sel.unconfirmed)
check("accepting keeps the class", sel.cls_id == was, sel.cls_id)
check("accepting advances", c3.unconfirmed_count() == 2, c3.unconfirmed_count())
sel2 = next(i for i in c3.box_items() if i.isSelected())
key(c3, Qt.Key_4)
check("overriding a suggestion relabels and confirms",
      sel2.cls_id == 3 and not sel2.unconfirmed, (sel2.cls_id, sel2.unconfirmed))
key(c3, Qt.Key_Return)
check("all suggestions resolved", c3.unconfirmed_count() == 0)

# ============ export block ============
w4 = new_window(LABEL_MODE_MANUAL)
w4._on_image_done("plate1.png", DETS)
w4.check_csv.setChecked(True); w4.check_yolo.setChecked(True); w4.check_images.setChecked(False)
w4.edit_export_name.setText("blocked-run")
before = {p.name for p in OUT.iterdir() if p.is_dir()}
log.clear(); clicked["which"] = None
w4.export_now()
check("export refused while boxes are unlabelled",
      {p.name for p in OUT.iterdir() if p.is_dir()} == before,
      {p.name for p in OUT.iterdir() if p.is_dir()})
# macOS ignores setWindowTitle on message boxes, so match on the body text
blocked = [x for x in log if x[0] == "box" and "no label yet" in str(x[2])]
check("and says so", bool(blocked), log)
check("naming the plate and the count",
      bool(blocked) and "plate1.png" in blocked[0][3] and "3 box" in blocked[0][2],
      blocked[0][2:] if blocked else None)
check("and offers a way to jump there",
      bool(blocked) and "Tab" in blocked[0][3], blocked[0][3] if blocked else None)

# label them, then it goes through
c4 = w4.canvas; c4.setFocus(); key(c4, Qt.Key_Tab)
for k in (Qt.Key_1, Qt.Key_1, Qt.Key_2):
    key(c4, k)
check("plate fully labelled", c4.unlabelled_count() == 0)
log.clear(); w4.export_now()
check("export proceeds once everything is labelled",
      (OUT / "blocked-run").is_dir())
rows = {r[0]: r for r in list(csvmod.reader(open(OUT / "blocked-run" / export.CSV_NAME)))[1:]}
check("csv counts the hand-applied labels",
      int(rows["plate1.png"][5]) == 3, rows["plate1.png"])
check("yolo labels written for hand-labelled boxes",
      len([l for l in (export.yolo_dir(OUT / "blocked-run") / "plate1.txt")
           .read_text().splitlines() if l]) == 3)
info = (OUT / "blocked-run" / export.INFO_NAME).read_text()
check("run log records the labelling mode",
      "detection only, labelled by hand" in info,
      [l for l in info.splitlines() if "Labelling" in l])

# unconfirmed only warns
w5 = new_window(LABEL_MODE_SUGGEST)
w5._on_image_done("plate1.png", DETS)
w5.check_csv.setChecked(True); w5.check_yolo.setChecked(False); w5.check_images.setChecked(False)
w5.edit_export_name.setText("suggest-run")
log.clear(); answer["q"] = QMessageBox.Cancel
w5.export_now()
check("unconfirmed suggestions prompt rather than block",
      any(k == "q" and "not yet confirmed" in str(m) for k, m, *r in log), log)
check("cancelling the prompt writes nothing", not (OUT / "suggest-run").is_dir())
answer["q"] = QMessageBox.Yes
w5.export_now()
check("confirming exports the suggested classes", (OUT / "suggest-run").is_dir())
rows = {r[0]: r for r in list(csvmod.reader(open(OUT / "suggest-run" / export.CSV_NAME)))[1:]}
check("suggested labels count normally", int(rows["plate1.png"][5]) == 3, rows["plate1.png"])

# ============ persistence ============
w6 = new_window(LABEL_MODE_MANUAL)
w6._on_image_done("plate1.png", DETS)
proj = TMP / "s.cfuproj"
w6._write_project(proj)
w7 = MainWindow(); w7.show(); QTest.qWait(20)
w7.open_project(str(proj))
QTest.qWait(50)
check("project keeps boxes unlabelled",
      all(b["cls"] == UNLABELLED for b in w7.records["plate1.png"]["boxes"]),
      [b["cls"] for b in w7.records["plate1.png"]["boxes"]])
w8 = new_window(LABEL_MODE_SUGGEST)
w8._on_image_done("plate1.png", DETS)
proj2 = TMP / "s2.cfuproj"; w8._write_project(proj2)
w9 = MainWindow(); w9.show(); QTest.qWait(20); w9.open_project(str(proj2)); QTest.qWait(50)
check("project keeps the unconfirmed flag",
      all(b.get("unconfirmed") for b in w9.records["plate1.png"]["boxes"]),
      w9.records["plate1.png"]["boxes"][0])

w6.check_remember.setChecked(True); w6._save_settings()
w10 = MainWindow(); w10.show(); QTest.qWait(20)
check("labelling mode is remembered", w10.label_mode() == LABEL_MODE_MANUAL,
      w10.label_mode())
w10.check_remember.setChecked(False)

QSettings("StJude", "CFU Annotator").clear()
shutil.rmtree(TMP, ignore_errors=True)
print(); print("FAILURES:", fails if fails else "none")
sys.stdout.flush(); os._exit(1 if fails else 0)
