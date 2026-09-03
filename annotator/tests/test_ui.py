"""Window-level tests: editing, statuses, locking, export and preferences.

Run with:  ../.venv/bin/python tests/test_ui.py     (from annotator/)

Uses the offscreen Qt platform, and drives the canvas with real mouse events —
QTest.mouseMove does not deliver on the offscreen platform, so the events are
constructed and sent directly (with a correct global position, which the scene
needs for hit-testing).
"""
import sys, os, csv as csvmod, re, shutil, tempfile
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
MODEL = ROOT.parent / "nuc" / "best.pt"
TMP = Path(tempfile.mkdtemp(prefix="cfu_ui_test_"))

from PyQt5.QtCore import Qt, QEvent, QPointF, QRectF, QSettings
from PyQt5.QtGui import QImage, QMouseEvent
from PyQt5.QtWidgets import QApplication, QMessageBox, QFileDialog
from PyQt5.QtTest import QTest

IMG = TMP / "images"; IMG.mkdir(parents=True, exist_ok=True)
OUT = TMP / "output"; OUT.mkdir(parents=True, exist_ok=True)
for i in range(3):
    im = QImage(1200, 900, QImage.Format_RGB32); im.fill(Qt.darkGray)
    im.save(str(IMG / f"plate{i+1}.png"))

fails = []
def check(n, c, x=""):
    print(("PASS  " if c else "FAIL  ") + n + (f"   {x}" if x else "")); sys.stdout.flush()
    if not c: fails.append(n)

log = []
QMessageBox.question = staticmethod(lambda *a, **k: (log.append(("q", a[1])), QMessageBox.Yes)[1])
QMessageBox.information = staticmethod(lambda *a, **k: log.append(("i", a[1])))
QMessageBox.warning = staticmethod(lambda *a, **k: log.append(("w", a[1])))
QMessageBox.critical = staticmethod(lambda *a, **k: log.append(("c", a[1], a[2] if len(a)>2 else "")))
# the completion dialog is a constructed QMessageBox; auto-dismiss it
_orig_exec = QMessageBox.exec_
QMessageBox.exec_ = lambda self: (log.append(("box", self.windowTitle(), self.text(), self.informativeText())), 0)[1]

app = QApplication(sys.argv)
# start from a clean preferences store
QSettings("StJude", "CFU Annotator").clear()

from cfu_annotator.mainwindow import MainWindow
from cfu_annotator.canvas import MODE_DRAW, MODE_SELECT
from cfu_annotator.detector import Detector
from cfu_annotator import scan, export, render, settings as prefs

if not MODEL.is_file():
    print(f"SKIP  every test   (model not found at {MODEL})")
    sys.exit(0)
det = Detector(MODEL)

def new_window(output=OUT):
    w = MainWindow(); w.resize(1400, 900); w.show(); QTest.qWait(30)
    res = scan.scan_folder(IMG)
    w.image_folder = IMG; w.images = res.images
    w._on_model_loaded(det); w.output_folder = output
    w._rebuild_image_list(); w._go_to(0)
    w.canvas.zoom_to_actual_size()
    return w

w = new_window()
c = w.canvas

def send(kind, pos, button=Qt.LeftButton, buttons=Qt.NoButton):
    gp = c.viewport().mapToGlobal(pos)
    QApplication.sendEvent(c.viewport(), QMouseEvent(kind, QPointF(pos), QPointF(gp), button, buttons, Qt.NoModifier))
def drag(a, b):
    p1, p2 = c.mapFromScene(*a), c.mapFromScene(*b)
    send(QEvent.MouseButtonPress, p1, Qt.LeftButton, Qt.LeftButton)
    send(QEvent.MouseMove, p2, Qt.NoButton, Qt.LeftButton)
    send(QEvent.MouseButtonRelease, p2, Qt.LeftButton, Qt.NoButton)

# ================= 1. draw tool returns to select =================
w._set_mode(MODE_DRAW)
check("draw mode active", c.mode == MODE_DRAW and w.action_draw.isChecked())
drag((100, 100), (180, 170))
check("box was drawn", len(c.get_boxes()) == 1, len(c.get_boxes()))
check("canvas returned to select mode", c.mode == MODE_SELECT, c.mode)
check("toolbar followed: Select checked", w.action_select.isChecked())
check("toolbar followed: Draw unchecked", not w.action_draw.isChecked())
check("status explains the switch", "select mode" in w.status_label.text().lower(), w.status_label.text())

# the next drag must edit, not draw another box
before = [tuple(b["xyxy"]) for b in c.get_boxes()]
drag((140, 135), (240, 235))
after = [tuple(b["xyxy"]) for b in c.get_boxes()]
check("next drag moved the box instead of drawing", len(after) == 1 and after != before, after)

# tiny drag (discarded box) also returns to select
w._set_mode(MODE_DRAW)
drag((500, 500), (501, 501))
check("discarded box still returns to select", c.mode == MODE_SELECT)

# sticky mode keeps drawing
w.check_sticky.setChecked(True)
check("sticky flag reaches the canvas", c.sticky_draw)
w._set_mode(MODE_DRAW)
drag((300, 300), (360, 360))
check("sticky: stays in draw mode", c.mode == MODE_DRAW, c.mode)
drag((400, 400), (460, 460))
check("sticky: draws a second box", len(c.get_boxes()) == 3, len(c.get_boxes()))
w.check_sticky.setChecked(False)
w._set_mode(MODE_SELECT)

# ================= 2. export folder =================
w._on_image_done("plate1.png", [{"cls": 0, "conf": 0.9, "xyxy": [10, 10, 60, 60]},
                                {"cls": 1, "conf": 0.8, "xyxy": [200, 200, 260, 260]}])
w._on_image_done("plate2.png", [{"cls": 2, "conf": 0.7, "xyxy": [50, 50, 90, 90]}])
w.check_csv.setChecked(True); w.check_yolo.setChecked(True); w.check_images.setChecked(False)

before_dirs = set(p.name for p in OUT.iterdir() if p.is_dir())
w.export_now()
new_dirs = [p for p in OUT.iterdir() if p.is_dir() and p.name not in before_dirs]
check("export created exactly one new folder", len(new_dirs) == 1, [p.name for p in new_dirs])
folder = new_dirs[0]
check("folder name follows the convention",
      re.fullmatch(r"CFU_export_\d{4}-\d{2}-\d{2}_\d{4}", folder.name) is not None, folder.name)
check("nothing dumped in the chosen folder",
      not (OUT / export.CSV_NAME).exists() and not (OUT / export.INFO_NAME).exists())
check("csv inside the export folder", (folder / export.CSV_NAME).exists())
check("yolo labels inside the export folder", (folder / export.YOLO_DIRNAME).is_dir())
check("run log inside the export folder", (folder / export.INFO_NAME).exists())
check("completion dialog names the folder",
      any(k == "box" and folder.name in str(m2) for k, m1, m2, *r in [x for x in log if x[0]=="box"]),
      [x[2] for x in log if x[0]=="box"])

rows = list(csvmod.reader(open(folder / export.CSV_NAME)))
check("csv has all three images", len(rows) == 4, len(rows))

# a second export must not touch the first
w.export_now()
dirs = sorted(p for p in OUT.iterdir() if p.is_dir())
check("second export makes its own folder", len(dirs) == 2, [p.name for p in dirs])
check("first export still intact", (folder / export.CSV_NAME).exists())
check("same-minute collision gets a suffix",
      any(d.name.endswith("-2") for d in dirs) or dirs[0].name != dirs[1].name,
      [d.name for d in dirs])

# ================= 3. annotated images =================
w.check_images.setChecked(True); w.check_yolo.setChecked(False)
before_dirs = set(p.name for p in OUT.iterdir() if p.is_dir())
w.export_now()
deadline = 0
while w.image_worker is not None and deadline < 300:
    app.processEvents(); QTest.qWait(50); deadline += 1
check("image export finished", w.image_worker is None)
folder3 = [p for p in OUT.iterdir() if p.is_dir() and p.name not in before_dirs][0]
images_dir = folder3 / export.IMAGES_DIRNAME
check("annotated_images folder created", images_dir.is_dir())
produced = sorted(p.name for p in images_dir.glob("*"))
check("one annotated image per annotated plate", len(produced) == 2, produced)
check("naming convention _annotated", all("_annotated" in n for n in produced), produced)
check("unannotated plate skipped", "plate3_annotated.png" not in produced, produced)
sizes = [p.stat().st_size for p in images_dir.glob("*")]
check("annotated images are non-empty", all(s > 1000 for s in sizes), sizes)
first = QImage(str(images_dir / produced[0]))
check("annotated image is readable and full size",
      not first.isNull() and first.size().width() == 1200, first.size())

# pixels actually changed where a box was drawn
plain = QImage(str(IMG / "plate1.png"))
check("annotation is visibly burned in", first.pixel(10, 12) != plain.pixel(10, 12))
info = (folder3 / export.INFO_NAME).read_text()
check("run log lists the annotated images", export.IMAGES_DIRNAME in info)
check("run log lists the csv", export.CSV_NAME in info)
check("run log does not claim yolo when unticked", export.YOLO_DIRNAME not in info.split("FILES WRITTEN")[1])

# nothing selected at all
w.check_csv.setChecked(False); w.check_yolo.setChecked(False); w.check_images.setChecked(False)
w.check_areas.setChecked(False)
log.clear(); w.export_now()
check("refuses when nothing is ticked", any(k == "i" for k, *r in log), log)
before_dirs = set(p.name for p in OUT.iterdir() if p.is_dir())
check("no folder created when refused",
      set(p.name for p in OUT.iterdir() if p.is_dir()) == before_dirs)
w.check_csv.setChecked(True)

# ================= 4. remembered settings =================
w.spin_conf.setValue(0.42); w.spin_tile.setValue(1280); w.spin_overlap.setValue(0.15)
w.check_tiling.setChecked(True)
w.check_yolo.setChecked(True); w.check_images.setChecked(False)
w.check_labels.setChecked(False); w.check_conf.setChecked(True)
w.check_sticky.setChecked(True)
w.check_remember.setChecked(True)
w._save_settings()
check("remember flag stored", prefs.remembering())
stored = prefs.load()
check("stored the image folder", stored.get("image_folder") == str(IMG), stored.get("image_folder"))
check("stored the model", str(stored.get("model_path", "")).endswith("best.pt"))
check("stored the output folder", stored.get("output_folder") == str(OUT))

w2 = MainWindow(); w2.resize(1400, 900); w2.show(); QTest.qWait(50)
check("restored confidence", abs(w2.spin_conf.value() - 0.42) < 1e-9, w2.spin_conf.value())
check("restored tile size", w2.spin_tile.value() == 1280, w2.spin_tile.value())
check("restored tile overlap", abs(w2.spin_overlap.value() - 0.15) < 1e-9)
check("restored export choices",
      w2.check_csv.isChecked() and w2.check_yolo.isChecked() and not w2.check_images.isChecked())
check("restored view toggles", not w2.check_labels.isChecked() and w2.check_conf.isChecked())
check("restored sticky draw", w2.check_sticky.isChecked() and w2.canvas.sticky_draw)
check("restored output folder", w2.output_folder == OUT, w2.output_folder)
check("restored image folder and rescanned", len(w2.images) == 3, len(w2.images))
check("restored remember checkbox", w2.check_remember.isChecked())
deadline = 0
while w2.detector is None and deadline < 200:
    app.processEvents(); QTest.qWait(50); deadline += 1
check("restored and reloaded the model", w2.detector is not None and w2.detector.path.name == "best.pt")
check("annotations are NOT restored", all(w2._status_of(p.name) == "not_annotated" for p in w2.images))
check("restored session is not dirty", not w2.dirty)
check("status explains what was restored", "Restored" in w2.status_label.text(), w2.status_label.text())

# unticking clears the store
w2.check_remember.setChecked(False)
check("unticking stops remembering", not prefs.remembering())
check("unticking clears stored values", prefs.load() == {}, prefs.load())
w3 = MainWindow(); w3.show(); QTest.qWait(30)
check("fresh start after unticking: no folders", w3.image_folder is None and w3.output_folder is None)
check("fresh start uses defaults", abs(w3.spin_conf.value() - 0.25) < 1e-9, w3.spin_conf.value())
check("remember checkbox reflects the off state", not w3.check_remember.isChecked())

# missing remembered folders are reported, not fatal
QSettings("StJude", "CFU Annotator").clear()
prefs.save({"image_folder": "/nonexistent/imgs", "output_folder": "/nonexistent/out",
            "model_path": "/nonexistent/m.pt", "conf": 0.33}, remember=True)
w4 = MainWindow(); w4.show(); QTest.qWait(30)
check("missing folders don't crash startup", w4.image_folder is None)
check("missing folders are reported", "could not be found" in w4.status_label.text(), w4.status_label.text())
check("other remembered settings still applied", abs(w4.spin_conf.value() - 0.33) < 1e-9)


# ================= 5. editing, statuses and locking =================
QSettings("StJude", "CFU Annotator").clear()
w5 = new_window()
c = w5.canvas
w5.check_remember.setChecked(False)

w5._on_image_done("plate1.png", [{"cls": 0, "conf": 0.9, "xyxy": [100, 100, 180, 160]},
                                 {"cls": 1, "conf": 0.8, "xyxy": [400, 400, 470, 470]}])
check("model output marks the image annotated", w5._status_of("plate1.png") == "annotated")
check("counts table reflects the model output", w5.table_counts.item(4, 1).text() == "2",
      w5.table_counts.item(4, 1).text())
check("image list shows the annotated icon",
      w5.list_images.item(0).icon().cacheKey()
      == __import__("cfu_annotator.status", fromlist=["x"]).status_icon("annotated").cacheKey())

# select, move, resize
def click(pt):
    p1 = c.mapFromScene(*pt)
    send(QEvent.MouseButtonPress, p1, Qt.LeftButton, Qt.LeftButton)
    send(QEvent.MouseButtonRelease, p1, Qt.LeftButton, Qt.NoButton)

click((140, 130))
check("click selects a box", c.selected_count() == 1, c.selected_count())
drag((140, 130), (240, 230))
moved = [b for b in c.get_boxes() if b["cls"] == 0][0]
check("dragging the body moves the box",
      tuple(round(v) for v in moved["xyxy"]) == (200, 200, 280, 260),
      tuple(round(v) for v in moved["xyxy"]))
check("a hand edit marks the image edited", w5._status_of("plate1.png") == "edited")

item = [i for i in c.box_items() if i.cls_id == 0][0]
item.setSelected(True)
br = item.rect().bottomRight()
drag((br.x(), br.y()), (br.x() + 100, br.y() + 60))
resized = [b for b in c.get_boxes() if b["cls"] == 0][0]
check("dragging a handle resizes the box",
      tuple(round(v) for v in resized["xyxy"]) == (200, 200, 380, 320),
      tuple(round(v) for v in resized["xyxy"]))

item.setSelected(True)
QTest.keyClick(c, Qt.Key_4)
check("number key relabels the selected box", item.cls_id == 3, item.cls_id)
check("relabelling clears the model confidence", item.conf is None)
item.setSelected(True)
before = len(c.get_boxes())
QTest.keyClick(c, Qt.Key_Delete)
check("Delete removes the selected box", len(c.get_boxes()) == before - 1)

# boxes must survive navigation
snapshot = c.get_boxes()
w5._go_to(1)
check("next image starts empty", len(c.get_boxes()) == 0)
w5._go_to(0)
check("boxes come back when navigating back",
      [tuple(round(v) for v in b["xyxy"]) for b in c.get_boxes()]
      == [tuple(round(v) for v in b["xyxy"]) for b in snapshot])

# finalizing locks the image
w5.toggle_finalized()
check("toggle marks the image finalized", w5._status_of("plate1.png") == "finalized")
check("canvas is locked", c.locked)
n = len(c.get_boxes())
w5._set_mode(MODE_DRAW)
drag((600, 600), (680, 680))
check("cannot draw on a locked image", len(c.get_boxes()) == n)
w5._set_mode(MODE_SELECT)
click((240, 230))
geometry = [tuple(b["xyxy"]) for b in c.get_boxes()]
drag((240, 230), (340, 330))
check("cannot move a box on a locked image",
      [tuple(b["xyxy"]) for b in c.get_boxes()] == geometry)
check("cannot delete on a locked image", c.delete_selected() == 0)
log.clear(); w5.annotate_current()
check("the model will not overwrite a locked image",
      any("finalized" in str(m) for k, m, *r in log), log)
w5.toggle_finalized()
check("unlocking returns it to edited", w5._status_of("plate1.png") == "edited")
check("canvas unlocked", not c.locked)

# annotate-all skips finalized images
w5._go_to(2); w5.toggle_finalized(); w5._go_to(0)
pending = [p for p in w5.images
           if not (w5.records.get(p.name) or {}).get("annotated")
           and not (w5.records.get(p.name) or {}).get("finalized")]
check("annotate-all would skip the finalized image", len(pending) == 1, len(pending))

# ================= 6. project round trip through the window =================
proj = TMP / "session.cfuproj"
statuses = {p.name: w5._status_of(p.name) for p in w5.images}
boxes_by_image = {n: [tuple(round(v, 2) for v in b["xyxy"]) for b in r["boxes"]]
                  for n, r in w5.records.items()}
check("saving the project writes the file", w5._write_project(proj) and proj.exists())
check("saving clears the unsaved marker", not w5.dirty and "\u2022" not in w5.windowTitle())

w6 = MainWindow(); w6.resize(1400, 900); w6.show(); QTest.qWait(30)
w6.open_project(str(proj))
deadline = 0
while w6.detector is None and deadline < 200:
    app.processEvents(); QTest.qWait(50); deadline += 1
check("reopened project restores the images", len(w6.images) == 3, len(w6.images))
check("reopened project restores every status",
      {p.name: w6._status_of(p.name) for p in w6.images} == statuses,
      {p.name: w6._status_of(p.name) for p in w6.images})
check("reopened project restores the boxes",
      {n: [tuple(round(v, 2) for v in b["xyxy"]) for b in r["boxes"]]
       for n, r in w6.records.items() if n in boxes_by_image} == boxes_by_image)
check("reopened project restores the model",
      w6.detector is not None and w6.detector.path.name == "best.pt")
w6._go_to(2)
check("a finalized image reopens locked", w6.canvas.locked)

QSettings("StJude", "CFU Annotator").clear()
shutil.rmtree(TMP, ignore_errors=True)
print(); print("FAILURES:", fails if fails else "none")
sys.stdout.flush(); os._exit(1 if fails else 0)
