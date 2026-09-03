"""Colony sizes in the export (v1.6.2).

Run with:  ../.venv/bin/python tests/test_v10.py     (from annotator/)

Colony size matters as much as colony count here, and the numbers have to be
usable without anyone knowing how many pixels a millimetre is. So the two size
measures are pinned to the properties that make them worth having:

  * area_fraction  survives re-photographing the same plate at a different
    resolution;
  * relative_area  is 1.0 for a median colony, whatever the plate.
"""

import csv
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="cfu_v10_"))

from PyQt5.QtCore import QRectF, QSettings, Qt                # noqa: E402
from PyQt5.QtGui import QImage                                # noqa: E402
from PyQt5.QtTest import QTest                                # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox         # noqa: E402

fails = []
def check(n, c, x=""):
    print(("PASS  " if c else "FAIL  ") + n + (f"   {x}" if x else "")); sys.stdout.flush()
    if not c: fails.append(n)

asked = []
QMessageBox.question = staticmethod(lambda *a, **k: (asked.append(("q", a[1])), QMessageBox.Yes)[1])
QMessageBox.information = staticmethod(lambda *a, **k: asked.append(("i", a[1])))
QMessageBox.warning = staticmethod(lambda *a, **k: asked.append(("w", a[1])))
QMessageBox.critical = staticmethod(lambda *a, **k: asked.append(("c", a[1])))
# Some dialogs are built and exec_'d directly rather than through the helpers,
# and a real modal never returns with no one to click it.
QMessageBox.exec_ = lambda self: (asked.append(("box", self.windowTitle())), 0)[1]

APP = QApplication.instance() or QApplication(sys.argv)
QSettings("StJude", "CFU Annotator").clear()

from cfu_annotator import export, scan                        # noqa: E402
from cfu_annotator.mainwindow import MainWindow               # noqa: E402

CLASSES = ["Round", "Diffuse"]


def plate(boxes, **flags):
    record = {"boxes": boxes, "annotated": True, "edited": False,
              "finalized": False, "contaminated": False}
    record.update(flags)
    return record


def square(x, y, side, cls=0, conf=0.9):
    return {"cls": cls, "conf": conf, "xyxy": [x, y, x + side, y + side]}


def read(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


print("\n-- the file's shape --")
out = TMP / "e1"
sizes = {"a.jpg": (6400, 6400)}
recs = {"a.jpg": plate([square(10, 10, 20), square(50, 50, 40, cls=1)])}
path, rows, skipped = export.write_areas(out, ["a.jpg"], CLASSES, recs, sizes)
check("the file is named CFU_areas.csv", path.name == export.AREAS_NAME, path.name)
check("one row per colony", rows == 2, f"{rows}")
data = read(path)
check("columns are as declared",
      list(data[0].keys()) == export.AREA_HEADER, str(list(data[0].keys())))
check("class names are written out, not numbers",
      [r["class"] for r in data] == ["Round", "Diffuse"],
      str([r["class"] for r in data]))
check("nothing was skipped", skipped == [], str(skipped))

print("\n-- area_fraction survives a change of resolution --")
# The same plate, same colonies, photographed at two resolutions.
small = {f"p{i}": (6400, 6400) for i in range(1)}
SIDES = [20, 20, 22, 40, 41, 60]
recs_small = {"small.jpg": plate([square(100 + i * 300, 200 + i * 150, s)
                                 for i, s in enumerate(SIDES)])}
recs_big = {"big.jpg": plate([square(int((100 + i * 300) * 1.625),
                                     int((200 + i * 150) * 1.68),
                                     round(s * 1.65))
                              for i, s in enumerate(SIDES)])}
p_small, _, _ = export.write_areas(TMP / "e2a", ["small.jpg"], CLASSES,
                                   recs_small, {"small.jpg": (6400, 6400)})
p_big, _, _ = export.write_areas(TMP / "e2b", ["big.jpg"], CLASSES,
                                 recs_big, {"big.jpg": (10400, 10752)})
frac_small = [float(r["area_fraction"]) for r in read(p_small)]
frac_big = [float(r["area_fraction"]) for r in read(p_big)]
px_small = [float(r["area_px"]) for r in read(p_small)]
px_big = [float(r["area_px"]) for r in read(p_big)]
worst_frac = max(abs(a - b) / a for a, b in zip(frac_small, frac_big))
px_ratio = max(b / a for a, b in zip(px_small, px_big))
check("pixel area really does change a lot with resolution",
      px_ratio > 2.5, f"{px_ratio:.2f}x")
check("area_fraction stays within 5% across resolutions",
      worst_frac < 0.05, f"worst {worst_frac * 100:.1f}%")
rel_small = [float(r["relative_area"]) for r in read(p_small)]
rel_big = [float(r["relative_area"]) for r in read(p_big)]
worst_rel = max(abs(a - b) / a for a, b in zip(rel_small, rel_big))
check("relative_area stays within 5% across resolutions",
      worst_rel < 0.05, f"worst {worst_rel * 100:.1f}%")

print("\n-- relative_area is anchored on the median colony --")
# Five colonies; the middle one by area is the median, so it must read 1.0.
recs = {"m.jpg": plate([square(0, 0, 10), square(100, 0, 20), square(200, 0, 30),
                        square(300, 0, 40), square(400, 0, 50)])}
p3, _, _ = export.write_areas(TMP / "e3", ["m.jpg"], CLASSES, recs,
                              {"m.jpg": (6400, 6400)})
rel = [float(r["relative_area"]) for r in read(p3)]
check("the median colony reads exactly 1.0", rel[2] == 1.0, f"{rel}")
check("a colony of twice the median area reads 2.0",
      abs(rel[3] - (40 * 40) / (30 * 30)) < 1e-3, f"{rel[3]:.4f}")
check("smaller colonies read below 1", all(v < 1 for v in rel[:2]), f"{rel[:2]}")
check("larger colonies read above 1", all(v > 1 for v in rel[3:]), f"{rel[3:]}")

print("\n-- what is left out, and why --")
recs = {
    "good.jpg": plate([square(0, 0, 20), square(100, 0, 30)]),
    "dirty.jpg": plate([square(0, 0, 20)], contaminated=True),
    "part.jpg": plate([square(0, 0, 20), {"cls": -1, "conf": None,
                                          "xyxy": [100, 0, 130, 30]}]),
    "nosize.jpg": plate([square(0, 0, 20)]),
    "empty.jpg": plate([]),
}
order = ["good.jpg", "dirty.jpg", "part.jpg", "nosize.jpg", "empty.jpg"]
sizes = {n: (6400, 6400) for n in order}
del sizes["nosize.jpg"]
p4, rows, skipped = export.write_areas(TMP / "e4", order, CLASSES, recs, sizes)
seen = {r["image"] for r in read(p4)}
check("a contaminated plate contributes no colonies", "dirty.jpg" not in seen, str(seen))
check("an unlabelled box is not measured",
      sum(1 for r in read(p4) if r["image"] == "part.jpg") == 1)
check("a plate with no readable size is reported, not silently dropped",
      skipped == ["nosize.jpg"], str(skipped))
check("a plate with no colonies simply has no rows", "empty.jpg" not in seen)
check("everything else is there", rows == 3, f"{rows}")

print("\n-- row counts line up with the count summary --")
counts_path = export.write_csv(TMP / "e4", order, CLASSES, recs)
by_image = {r["image"]: r for r in read(counts_path)}
for name in ("good.jpg", "part.jpg"):
    n_rows = sum(1 for r in read(p4) if r["image"] == name)
    check(f"{name}: {n_rows} size row(s) matches its count summary total",
          n_rows == int(by_image[name]["total"]),
          f"{n_rows} vs {by_image[name]['total']}")

print("\n-- a box stranded beyond the class list is still measured --")
recs = {"s.jpg": plate([square(0, 0, 20), square(100, 0, 30, cls=7)])}
p5, rows, _ = export.write_areas(TMP / "e5", ["s.jpg"], CLASSES, recs,
                                 {"s.jpg": (6400, 6400)})
labels = [r["class"] for r in read(p5)]
check("it appears rather than vanishing", rows == 2, f"{rows}")
check("and is labelled by its number", "class 7" in labels, str(labels))

print("\n-- geometry edge cases --")
recs = {"g.jpg": plate([
    square(0, 0, 20),
    {"cls": 0, "conf": 0.5, "xyxy": [50, 50, 50, 90]},        # zero width
    {"cls": 0, "conf": 0.5, "xyxy": [6300, 6300, 6600, 6600]},  # runs off the edge
])}
p6, rows, _ = export.write_areas(TMP / "e6", ["g.jpg"], CLASSES, recs,
                                 {"g.jpg": (6400, 6400)})
data = read(p6)
check("a zero-width box is dropped", rows == 2, f"{rows}")
edge = [r for r in data if float(r["width_px"]) == 100.0]
check("a box running off the edge is clipped to the image", len(edge) == 1,
      str([r["width_px"] for r in data]))
check("no area_fraction exceeds 1",
      all(float(r["area_fraction"]) <= 1.0 for r in data))

print("\n-- colonies are numbered in reading order --")
recs = {"o.jpg": plate([square(500, 900, 20), square(100, 100, 20),
                        square(900, 100, 20)])}
p7, _, _ = export.write_areas(TMP / "e7", ["o.jpg"], CLASSES, recs,
                              {"o.jpg": (6400, 6400)})
tops = [float(r["center_y"]) for r in read(p7)]
check("numbering runs top to bottom", tops == sorted(tops), str(tops))
check("numbers start at 1 and are consecutive",
      [int(r["colony"]) for r in read(p7)] == [1, 2, 3])

print("\n-- through the window --")
IMG = TMP / "images"; IMG.mkdir(parents=True)
for name in ("plate1.png", "plate2.png"):
    im = QImage(800, 600, QImage.Format_RGB32); im.fill(Qt.darkGray)
    im.save(str(IMG / name))
OUT = TMP / "out"; OUT.mkdir()

win = MainWindow(); win.resize(1300, 900); win.show(); QTest.qWait(20)
check("colony sizes are exported by default", win.check_areas.isChecked())
win.image_folder = IMG
win.images = scan.scan_folder(IMG).images
win._rebuild_image_list()
win._set_class_names(CLASSES, custom=True, source=None)
win.output_folder = OUT
win._go_to(0)
while win.decode_worker is not None:
    QTest.qWait(5)
for i, side in enumerate((20, 30, 40)):
    win.canvas.add_box(QRectF(20 + i * 100, 20, side, side), i % 2)
win.check_images.setChecked(False)          # slow, and not what we're testing
win.edit_export_name.setText("areas-run")
asked.clear()
win.export_now()
QTest.qWait(50)
folder = OUT / "areas-run"
areas_file = folder / export.AREAS_NAME
check("the export writes the colony sizes file", areas_file.is_file(),
      str(sorted(p.name for p in folder.iterdir())) if folder.is_dir() else "no folder")
exported = read(areas_file)
check("every box on the plate is measured", len(exported) == 3, f"{len(exported)}")
check("the sizes are ordered as drawn",
      [float(r["area_px"]) for r in exported] == [400.0, 900.0, 1600.0],
      str([r["area_px"] for r in exported]))
info = (folder / export.INFO_NAME).read_text()
check("the run log explains the two measures",
      "relative_area" in info and "area_fraction" in info)
check("the run log lists the file",
      export.AREAS_NAME in info)

print("\n-- the preference is remembered --")
win.check_areas.setChecked(False)
win.check_remember.setChecked(True)
win._save_settings()
win2 = MainWindow(); win2.resize(1300, 900); win2.show(); QTest.qWait(20)
check("unticking it survives a restart", not win2.check_areas.isChecked())
win2.close()
win.check_areas.setChecked(True)
win._save_settings()

print("\n-- it can be the only output --")
win.check_csv.setChecked(False)
win.check_yolo.setChecked(False)
win.check_images.setChecked(False)
win.edit_export_name.setText("areas-only")
asked.clear()
win.export_now()
QTest.qWait(50)
only = OUT / "areas-only"
check("colony sizes alone is a valid export",
      (only / export.AREAS_NAME).is_file(),
      str(sorted(p.name for p in only.iterdir())) if only.is_dir() else "no folder")
check("no 'nothing selected' complaint",
      not any(a[1] == "Nothing selected" for a in asked), str(asked))

win.check_areas.setChecked(False)
asked.clear()
win.export_now()
check("with every output unticked the user is told",
      any(a[1] == "Nothing selected" for a in asked), str(asked))

win.close(); QTest.qWait(20)
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'ALL PASSED' if not fails else 'FAILURES: ' + ', '.join(fails)}")
sys.stdout.flush()
sys.exit(1 if fails else 0)
