"""Tests for custom export names, contamination, undo, and label import.

Run with:  ../.venv/bin/python tests/test_v4.py     (from annotator/)
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
TMP = Path(tempfile.mkdtemp(prefix="cfu_v4_"))

from PyQt5.QtCore import QEvent, QPointF, QRectF, QSettings, Qt   # noqa: E402
from PyQt5.QtGui import QImage, QMouseEvent                        # noqa: E402
from PyQt5.QtTest import QTest                                     # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox              # noqa: E402

IMG = TMP / "images"; IMG.mkdir(parents=True)
LAB = TMP / "labels"; LAB.mkdir(parents=True)
OUT = TMP / "output"; OUT.mkdir(parents=True)

fails = []
def check(n, c, x=""):
    print(("PASS  " if c else "FAIL  ") + n + (f"   {x}" if x else "")); sys.stdout.flush()
    if not c: fails.append(n)

answer = {"q": QMessageBox.Yes}
log = []
QMessageBox.question = staticmethod(lambda *a, **k: (log.append(("q", a[1], a[2] if len(a) > 2 else "")), answer["q"])[1])
QMessageBox.information = staticmethod(lambda *a, **k: log.append(("i", a[1], a[2] if len(a) > 2 else "")))
QMessageBox.warning = staticmethod(lambda *a, **k: log.append(("w", a[1], a[2] if len(a) > 2 else "")))
QMessageBox.critical = staticmethod(lambda *a, **k: log.append(("c", a[1], a[2] if len(a) > 2 else "")))
QMessageBox.exec_ = lambda self: (log.append(("box", self.windowTitle(), self.text(), self.informativeText())), 0)[1]

APP = QApplication.instance() or QApplication(sys.argv)
QSettings("StJude", "CFU Annotator").clear()

if not MODEL.is_file():
    print(f"SKIP  every test   (model not found at {MODEL})"); sys.exit(0)

from cfu_annotator.canvas import MODE_DRAW, MODE_SELECT            # noqa: E402
from cfu_annotator.detector import Detector                        # noqa: E402
from cfu_annotator.mainwindow import MainWindow                    # noqa: E402
from cfu_annotator import export, scan, status                     # noqa: E402

for i in range(3):
    im = QImage(1000, 800, QImage.Format_RGB32); im.fill(Qt.darkGray)
    im.save(str(IMG / f"plate{i+1}.png"))
det = Detector(MODEL)

def new_window():
    w = MainWindow(); w.resize(1400, 900); w.show(); QTest.qWait(30)
    res = scan.scan_folder(IMG)
    w.image_folder = IMG; w.images = res.images
    w._on_model_loaded(det); w.output_folder = OUT
    w.check_remember.setChecked(False)
    w._rebuild_image_list(); w._go_to(0)
    w.canvas.zoom_to_actual_size()
    return w

w = new_window()
c = w.canvas

def send(kind, pos, button=Qt.LeftButton, buttons=Qt.NoButton):
    gp = c.viewport().mapToGlobal(pos)
    QApplication.sendEvent(c.viewport(), QMouseEvent(
        kind, QPointF(pos), QPointF(gp), button, buttons, Qt.NoModifier))
def drag(a, b):
    p1, p2 = c.mapFromScene(*a), c.mapFromScene(*b)
    send(QEvent.MouseButtonPress, p1, Qt.LeftButton, Qt.LeftButton)
    send(QEvent.MouseMove, p2, Qt.NoButton, Qt.LeftButton)
    send(QEvent.MouseButtonRelease, p2, Qt.LeftButton, Qt.NoButton)
def click(pt):
    p1 = c.mapFromScene(*pt)
    send(QEvent.MouseButtonPress, p1, Qt.LeftButton, Qt.LeftButton)
    send(QEvent.MouseButtonRelease, p1, Qt.LeftButton, Qt.NoButton)

# ============ 1. custom export folder name ============
w._on_image_done("plate1.png", [{"cls": 0, "conf": 0.9, "xyxy": [10, 10, 60, 60]},
                                {"cls": 1, "conf": 0.8, "xyxy": [200, 200, 260, 260]}])
w.check_csv.setChecked(True); w.check_yolo.setChecked(True); w.check_images.setChecked(False)

w.edit_export_name.setText("March 2026 plate counts")
w.export_now()
made = sorted(p.name for p in OUT.iterdir() if p.is_dir())
check("custom folder name used verbatim", made == ["March 2026 plate counts"], made)
check("csv lands inside the named folder",
      (OUT / "March 2026 plate counts" / export.CSV_NAME).exists())

w.export_now()
made = sorted(p.name for p in OUT.iterdir() if p.is_dir())
check("re-export with the same name gets -2, not overwritten",
      made == ["March 2026 plate counts", "March 2026 plate counts-2"], made)

w.edit_export_name.setText("bad/name:with*chars?")
w.export_now()
made = sorted(p.name for p in OUT.iterdir() if p.is_dir())
check("illegal characters are replaced", "bad-name-with-chars" in made, made)

w.edit_export_name.setText("")
w.export_now()
made = [p.name for p in OUT.iterdir() if p.is_dir()]
check("blank name falls back to the dated default",
      any(n.startswith(export.EXPORT_PREFIX) for n in made), made)
check("the field's placeholder shows the dated default",
      w.edit_export_name.placeholderText().startswith(export.EXPORT_PREFIX),
      w.edit_export_name.placeholderText())

# ============ 2. contaminated ============
w2 = new_window(); c = w2.canvas
w2._on_image_done("plate1.png", [{"cls": 0, "conf": 0.9, "xyxy": [100, 100, 160, 160]},
                                 {"cls": 1, "conf": 0.8, "xyxy": [300, 300, 360, 360]}])
check("starts annotated", w2._status_of("plate1.png") == status.ANNOTATED)

log.clear(); answer["q"] = QMessageBox.Cancel
w2.toggle_contaminated()
check("asks before discarding counts", any(k == "q" for k, *r in log), log)
check("cancelling leaves the counts alone", len(c.get_boxes()) == 2)
check("cancelling leaves the status alone", w2._status_of("plate1.png") == status.ANNOTATED)

answer["q"] = QMessageBox.Yes
w2.toggle_contaminated()
check("marking contaminated sets the status",
      w2._status_of("plate1.png") == status.CONTAMINATED, w2._status_of("plate1.png"))
check("counts are discarded immediately", len(c.get_boxes()) == 0, len(c.get_boxes()))
check("record holds no boxes", w2.records["plate1.png"]["boxes"] == [])
check("canvas is locked", c.locked)
check("badge says contaminated", c.locked_reason == status.CONTAMINATED)
check("row icon is the contaminated one",
      w2.list_images.item(0).icon().cacheKey()
      == status.status_icon(status.CONTAMINATED).cacheKey())
check("button offers to undo it", "click to undo" in w2.button_contaminated.text().lower(),
      w2.button_contaminated.text())
check("finalize is disabled on a contaminated plate", not w2.button_finalize.isEnabled())

# locked against every edit
c.set_mode(MODE_DRAW); drag((400, 400), (460, 460)); c.set_mode(MODE_SELECT)
check("cannot draw on a contaminated plate", len(c.get_boxes()) == 0)
check("cannot clear", c.clear_boxes() == 0)
log.clear(); w2.annotate_current()
check("the model refuses a contaminated plate",
      any("locked" in str(r) for k, m, *r in log), log)
log.clear(); w2.toggle_finalized()
check("cannot finalize a contaminated plate",
      w2._status_of("plate1.png") == status.CONTAMINATED)

# csv reports it with zeros
w2.check_csv.setChecked(True); w2.check_yolo.setChecked(True); w2.check_images.setChecked(False)
w2.edit_export_name.setText("contam-check")
w2.export_now()
folder = OUT / "contam-check"
rows = {r[0]: r for r in list(csvmod.reader(open(folder / export.CSV_NAME)))[1:]}
check("csv status is contaminated", rows["plate1.png"][6] == "contaminated", rows["plate1.png"])
check("csv counts are zero", rows["plate1.png"][1:6] == ["0", "0", "0", "0", "0"],
      rows["plate1.png"])
check("no YOLO label written for a contaminated plate",
      not (export.yolo_dir(folder) / "plate1.txt").exists())
info = (folder / export.INFO_NAME).read_text()
check("run log tallies contaminated plates", "contaminated" in info)

# undoing contamination
w2.toggle_contaminated()
check("un-marking clears the status", w2._status_of("plate1.png") == status.NOT_ANNOTATED,
      w2._status_of("plate1.png"))
check("plate is editable again", not c.locked)

# ============ 3. undo ============
w3 = new_window(); c = w3.canvas
w3._on_image_done("plate1.png", [{"cls": 0, "conf": 0.9, "xyxy": [100, 100, 160, 160]}])
base = [tuple(b["xyxy"]) for b in c.get_boxes()]

c.set_mode(MODE_DRAW); drag((300, 300), (380, 380)); c.set_mode(MODE_SELECT)
check("drew a second box", len(c.get_boxes()) == 2)
check("undo is enabled", w3.action_undo.isEnabled())
w3.undo()
check("undo removes the drawn box", len(c.get_boxes()) == 1, len(c.get_boxes()))
check("undo restores the original geometry",
      [tuple(b["xyxy"]) for b in c.get_boxes()] == base)
check("undo restores the status too",
      w3._status_of("plate1.png") == status.ANNOTATED, w3._status_of("plate1.png"))

# undo a move
click((130, 130)); drag((130, 130), (230, 230))
moved = [tuple(b["xyxy"]) for b in c.get_boxes()]
check("box moved", moved != base, moved)
w3.undo()
check("undo restores a move", [tuple(b["xyxy"]) for b in c.get_boxes()] == base)

# undo a delete
click((130, 130)); c.delete_selected()
check("box deleted", len(c.get_boxes()) == 0)
w3.undo()
check("undo restores a delete", len(c.get_boxes()) == 1)

# undo a relabel
item = c.box_items()[0]; item.setSelected(True); c.set_class_of_selected(3)
check("relabelled", c.box_items()[0].cls_id == 3)
w3.undo()
check("undo restores the label", c.box_items()[0].cls_id == 0, c.box_items()[0].cls_id)

# undo the contamination wipe — the important one
answer["q"] = QMessageBox.Yes
w3.toggle_contaminated()
check("contaminated wiped the counts", len(c.get_boxes()) == 0)
w3.undo()
check("undo brings the counts back after contamination",
      len(c.get_boxes()) == 1, len(c.get_boxes()))
check("undo clears the contaminated status",
      w3._status_of("plate1.png") == status.ANNOTATED, w3._status_of("plate1.png"))
check("plate editable again after undo", not c.locked)

# undo a finalize, and multi-step history
w3.toggle_finalized()
check("finalized", w3._status_of("plate1.png") == status.FINALIZED)
w3.undo()
check("undo un-finalizes", w3._status_of("plate1.png") == status.ANNOTATED)

for i in range(4):
    c.set_mode(MODE_DRAW)          # one-shot by design; re-arm each time
    drag((400 + i * 60, 400), (440 + i * 60, 440))
c.set_mode(MODE_SELECT)
check("four more boxes", len(c.get_boxes()) == 5, len(c.get_boxes()))
for _ in range(4):
    w3.undo()
check("undo steps back through all four", len(c.get_boxes()) == 1, len(c.get_boxes()))

# undo across images
w3._go_to(1)
w3.canvas.set_mode(MODE_DRAW); drag((100, 100), (180, 180)); w3.canvas.set_mode(MODE_SELECT)
check("drew on image 2", len(w3.canvas.get_boxes()) == 1)
w3._go_to(0)
w3.undo()
check("undo jumps back to the image it belongs to", w3.index == 1, w3.index)
check("and undoes there", len(w3.canvas.get_boxes()) == 0)

while w3.action_undo.isEnabled():
    w3.undo()
log.clear(); w3.undo()
check("undo with empty history is harmless",
      "Nothing to undo" in w3.status_label.text(), w3.status_label.text())
check("undo action disabled when history is empty", not w3.action_undo.isEnabled())

# ============ 4. label import ============
w4 = new_window()
(LAB / "plate1.txt").write_text("0 0.5 0.5 0.1 0.2\n1 0.25 0.25 0.05 0.05\n")
(LAB / "plate2.txt").write_text("")
(LAB / "classes.txt").write_text("BFU\nGM\nE\nGEMM\n")
(LAB / "unrelated.csv").write_text("a,b")
(LAB / "plate9.txt").write_text("0 0.5 0.5 0.1 0.1\n")

log.clear()
w4._import_labels(LAB)
check("labels folder recorded", w4.labels_folder == LAB)
check("plate1 got its boxes", len(w4.records["plate1.png"]["boxes"]) == 2,
      len(w4.records.get("plate1.png", {}).get("boxes", [])))
check("plate1 counts as annotated", w4._status_of("plate1.png") == status.ANNOTATED)
check("plate2 empty label = annotated with zero",
      w4._status_of("plate2.png") == status.ANNOTATED
      and w4.records["plate2.png"]["boxes"] == [])
check("plate3 without labels stays unannotated",
      w4._status_of("plate3.png") == status.NOT_ANNOTATED)
check("classes.txt ignored", "classes" not in w4.records)
check("label with no matching image ignored", "plate9.png" not in w4.records)
check("import is attributed in the record",
      "imported from" in (w4.records["plate1.png"]["model"] or ""),
      w4.records["plate1.png"]["model"])
# records are sorted by position, so compare the whole set
got = sorted(tuple(round(v) for v in b["xyxy"])
             for b in w4.records["plate1.png"]["boxes"])
want = sorted([(450, 320, 550, 480),      # 0 0.5 0.5 0.1 0.2 on 1000x800
               (225, 180, 275, 220)])     # 1 0.25 0.25 0.05 0.05
check("boxes denormalised to pixels", got == want, got)
check("imported boxes carry no confidence",
      all(b["conf"] is None for b in w4.records["plate1.png"]["boxes"]))
check("imported classes preserved",
      sorted(b["cls"] for b in w4.records["plate1.png"]["boxes"]) == [0, 1],
      [b["cls"] for b in w4.records["plate1.png"]["boxes"]])
w4._go_to(0)
check("imported boxes show on the canvas", len(w4.canvas.get_boxes()) == 2)
check("sidebar reports what loaded", "2 box" in w4.label_labels.text(),
      w4.label_labels.text())

# imported labels export like any other annotation
w4.check_csv.setChecked(True); w4.check_yolo.setChecked(True); w4.check_images.setChecked(False)
w4.edit_export_name.setText("from-labels")
w4.export_now()
rows = {r[0]: r for r in list(csvmod.reader(open(OUT / "from-labels" / export.CSV_NAME)))[1:]}
check("imported counts reach the csv", rows["plate1.png"][6] == "annotated"
      and int(rows["plate1.png"][5]) == 2, rows["plate1.png"])
check("imported labels round-trip to YOLO",
      (export.yolo_dir(OUT / "from-labels") / "plate1.txt").exists())
lines = [l for l in (export.yolo_dir(OUT / "from-labels") / "plate1.txt")
         .read_text().splitlines() if l]
round_tripped = sorted(tuple(round(float(v), 3) for v in l.split()) for l in lines)
check("round-tripped geometry matches the source exactly",
      round_tripped == sorted([(0.0, 0.5, 0.5, 0.1, 0.2),
                               (1.0, 0.25, 0.25, 0.05, 0.05)]),
      round_tripped)

# an empty/mismatched folder is reported, not fatal
empty = TMP / "empty_labels"; empty.mkdir()
(empty / "nothing.txt").write_text("0 0.5 0.5 0.1 0.1\n")
log.clear(); ok = w4._import_labels(empty)
check("folder with no matching labels is refused", ok is False)
check("and explained", any("No matching label files" in str(m) for k, m, *r in log), log)

QSettings("StJude", "CFU Annotator").clear()
shutil.rmtree(TMP, ignore_errors=True)
print(); print("FAILURES:", fails if fails else "none")
sys.stdout.flush(); os._exit(1 if fails else 0)
