"""Guarding hand-made annotations against an accidental model re-run (v1.6.1).

Run with:  ../.venv/bin/python tests/test_v9.py     (from annotator/)

Three separate faults could throw away a session's labelling:

  * "Annotate all remaining" treated a plate someone had annotated *by hand* as
    "not annotated yet" and replaced every box on it, silently, without the
    plate even being on screen;
  * the Image menu carried its own copies of the two Annotate actions, so only
    the toolbar's were disabled while the model ran — the menu's `R` shortcut
    could start a second inference run on top of the first;
  * that shortcut was a bare `R`, one keyboard row above the `4` and `5` keys
    that labelling with 1-9 leans on all day.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="cfu_v9_"))

from PyQt5.QtCore import QRectF, QSettings, Qt                # noqa: E402
from PyQt5.QtGui import QImage, QKeySequence                  # noqa: E402
from PyQt5.QtTest import QTest                                # noqa: E402
from PyQt5.QtWidgets import (                                 # noqa: E402
    QAction, QApplication, QMessageBox,
)

fails = []
def check(n, c, x=""):
    print(("PASS  " if c else "FAIL  ") + n + (f"   {x}" if x else "")); sys.stdout.flush()
    if not c: fails.append(n)

asked = []          # (title, body) of every dialog raised
reply = {"value": QMessageBox.Yes}
QMessageBox.question = staticmethod(
    lambda *a, **k: (asked.append(("q", a[1], a[2])), reply["value"])[1])
QMessageBox.information = staticmethod(
    lambda *a, **k: asked.append(("i", a[1], a[2])))
QMessageBox.warning = staticmethod(
    lambda *a, **k: asked.append(("w", a[1], a[2])))
QMessageBox.critical = staticmethod(
    lambda *a, **k: asked.append(("c", a[1], a[2])))

APP = QApplication.instance() or QApplication(sys.argv)
QSettings("StJude", "CFU Annotator").clear()

from cfu_annotator import scan, status                         # noqa: E402
from cfu_annotator.mainwindow import MainWindow               # noqa: E402

IMG = TMP / "images"; IMG.mkdir(parents=True)
NAMES = ["plateA.png", "plateB.png", "plateC.png", "plateD.png"]
for name in NAMES:
    im = QImage(600, 500, QImage.Format_RGB32); im.fill(Qt.darkGray)
    im.save(str(IMG / name))

runs = []               # every set of paths handed to the model


def new_window():
    w = MainWindow(); w.resize(1300, 900); w.show(); QTest.qWait(20)
    w.image_folder = IMG
    w.images = scan.scan_folder(IMG).images
    w._rebuild_image_list()
    w._set_class_names(["Round", "Diffuse"], custom=True, source=None)
    w.detector = object()                    # a model is loaded, but never run
    w._start_inference = lambda paths: runs.append([p.name for p in paths])
    return w


def settle(w):
    while w.decode_worker is not None:
        QTest.qWait(5)


def hand_annotate(w, row, count=4):
    """Draw and label boxes by hand, as a user in detect-only mode would."""
    w._go_to(row); settle(w)
    for i in range(count):
        w.canvas.add_box(QRectF(10 + i * 30, 10, 20, 20), 1)


win = new_window()

print("\n-- a plate annotated by hand is 'edited', not 'annotated' --")
hand_annotate(win, 0)
record = win.records["plateA.png"]
check("hand-drawn boxes do not set the model's 'annotated' flag",
      not record.get("annotated"), f"annotated={record.get('annotated')}")
check("they do mark the plate as edited by hand", record.get("edited"))
check("its status is 'edited'", status.status_of(record) == status.EDITED,
      status.status_of(record))
check("the boxes are recorded", len(record["boxes"]) == 4,
      f"{len(record['boxes'])}")

print("\n-- 'Annotate all remaining' leaves hand-made work alone --")
runs.clear(); asked.clear()
reply["value"] = QMessageBox.Yes
win.annotate_all()
check("the model runs on something", len(runs) == 1, f"{runs}")
targets = runs[0] if runs else []
check("the hand-annotated plate is NOT in the run", "plateA.png" not in targets,
      f"{targets}")
check("the untouched plates still are",
      set(targets) == {"plateB.png", "plateC.png", "plateD.png"}, f"{targets}")
body = asked[-1][2] if asked else ""
check("the dialog says the hand-annotated plate is being skipped",
      "annotated by hand" in body, body[:120])
check("the hand-drawn boxes survive",
      len(win.records["plateA.png"]["boxes"]) == 4)

print("\n-- a model-annotated plate the user then tweaked is still skipped --")
win.records["plateB.png"] = {"boxes": [{"cls": 0, "conf": 0.9,
                                       "xyxy": [1, 1, 9, 9]}],
                             "annotated": True, "edited": True,
                             "finalized": False, "contaminated": False}
runs.clear(); asked.clear()
win.annotate_all()
check("an already-annotated plate is not re-run",
      "plateB.png" not in (runs[0] if runs else []), f"{runs}")

print("\n-- locked plates are still skipped, and said to be --")
win.records["plateC.png"] = {"boxes": [], "annotated": False, "edited": False,
                             "finalized": True, "contaminated": False}
runs.clear(); asked.clear()
win.annotate_all()
check("a finalized plate is not re-run",
      "plateC.png" not in (runs[0] if runs else []), f"{runs}")
check("the dialog mentions the locked plate",
      "finalized or contaminated" in (asked[-1][2] if asked else ""),
      (asked[-1][2] if asked else "")[:120])

print("\n-- nothing left to do --")
for name in NAMES:
    win.records[name] = {"boxes": [], "annotated": False, "edited": True,
                         "finalized": False, "contaminated": False}
runs.clear(); asked.clear()
win.annotate_all()
check("no run is started when every plate is hand-annotated", runs == [], f"{runs}")
check("the user is told why", asked and asked[-1][1] == "Nothing to do",
      str(asked[-1][:2]) if asked else "no dialog")

print("\n-- re-running one image warns that the work is the user's --")
win2 = new_window(); settle(win2)
hand_annotate(win2, 0)
runs.clear(); asked.clear()
reply["value"] = QMessageBox.Cancel
win2.annotate_current()
check("a confirmation is raised", len(asked) == 1, f"{asked}")
title = asked[0][1] if asked else ""
check("the title names what is at stake",
      title == "Discard your hand-made annotations?", title)
check("the body says the annotations are the user's own",
      "by hand" in (asked[0][2] if asked else ""), (asked[0][2] if asked else "")[:120])
check("cancelling starts no run", runs == [], f"{runs}")
check("cancelling leaves every box in place",
      len(win2.canvas.get_boxes()) == 4, f"{len(win2.canvas.get_boxes())}")

print("\n-- and agreeing still works --")
runs.clear(); asked.clear()
reply["value"] = QMessageBox.Yes
win2.annotate_current()
check("saying yes runs the model on just that image",
      runs == [["plateA.png"]], f"{runs}")

print("\n-- a model-annotated plate gets the milder wording --")
win2.records["plateA.png"] = {"boxes": [{"cls": 0, "conf": 0.9,
                                        "xyxy": [1, 1, 9, 9]}],
                              "annotated": True, "edited": False,
                              "finalized": False, "contaminated": False}
win2._go_to(0); settle(win2)
runs.clear(); asked.clear()
reply["value"] = QMessageBox.Cancel
win2.annotate_current()
check("the plain 'Replace existing boxes?' confirm is used",
      asked and asked[0][1] == "Replace existing boxes?",
      str(asked[0][1]) if asked else "no dialog")

print("\n-- one action per command, both disabled while the model runs --")
annotate_actions = [a for a in win2.findChildren(QAction)
                    if a.text() and "nnotate" in a.text()]
check("there is exactly one action per Annotate command",
      len(annotate_actions) == 2,
      "; ".join(repr(a.text()) for a in annotate_actions))
win2._set_busy(True, "Running the model…")
check("every Annotate action is disabled while busy",
      all(not a.isEnabled() for a in annotate_actions),
      "; ".join(f"{a.text()!r}={a.isEnabled()}" for a in annotate_actions))
runs.clear()
win2.annotate_current()
check("annotate_current refuses to start a second run", runs == [], f"{runs}")
win2.annotate_all()
check("annotate_all refuses to start a second run", runs == [], f"{runs}")
win2._set_busy(False)

print("\n-- the re-run shortcut is behind a modifier --")
shortcuts = {a.text(): a.shortcut().toString() for a in annotate_actions}
check("Annotate image is on Ctrl+R, not a bare letter",
      win2.button_annotate.shortcut() == QKeySequence("Ctrl+R"),
      win2.button_annotate.shortcut().toString())
check("no Annotate action is on a bare single letter",
      all(len(s) != 1 for s in shortcuts.values()), str(shortcuts))

win2._go_to(0); settle(win2)
win2.canvas.set_boxes([])
for i in range(3):
    win2.canvas.add_box(QRectF(10 + i * 30, 10, 20, 20), 0)
runs.clear(); asked.clear()
win2.canvas.setFocus(Qt.OtherFocusReason); QTest.qWait(5)
QTest.keyClick(APP.focusWidget(), Qt.Key_R)
QTest.qWait(20)
check("pressing plain 'R' while labelling does nothing at all",
      runs == [] and asked == [], f"runs={runs} dialogs={[a[1] for a in asked]}")
check("...and the boxes are untouched", len(win2.canvas.get_boxes()) == 3,
      f"{len(win2.canvas.get_boxes())}")

print("\n-- 1-9 still label, and never reach the model --")
win2.canvas.box_items()[0].setSelected(True)
runs.clear(); asked.clear()
for key in (Qt.Key_1, Qt.Key_2):
    QTest.keyClick(APP.focusWidget(), key); QTest.qWait(10)
check("number keys never start a model run", runs == [], f"{runs}")
check("number keys raise no dialogs", asked == [], f"{[a[1] for a in asked]}")
labelled = [b["cls"] for b in win2.canvas.get_boxes()]
check("number keys did label boxes", any(c >= 0 for c in labelled), f"{labelled}")

print("\n-- accidental navigation still loses nothing --")
win2._go_to(0); settle(win2)
win2.canvas.set_boxes([])
for i in range(4):
    win2.canvas.add_box(QRectF(10 + i * 30, 10, 20, 20), 1)
win2.canvas.setFocus(Qt.OtherFocusReason); QTest.qWait(5)
QTest.keyClick(APP.focusWidget(), Qt.Key_D); QTest.qWait(20); settle(win2)
check("'D' moves to the next image", win2.index == 1, f"{win2.index}")
check("the boxes left behind are kept",
      len(win2.records["plateA.png"]["boxes"]) == 4,
      f"{len(win2.records['plateA.png']['boxes'])}")
QTest.keyClick(APP.focusWidget(), Qt.Key_A); QTest.qWait(20); settle(win2)
check("'A' comes back to them intact", len(win2.canvas.get_boxes()) == 4,
      f"{len(win2.canvas.get_boxes())}")

win.close(); win2.close(); QTest.qWait(30)
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'ALL PASSED' if not fails else 'FAILURES: ' + ', '.join(fails)}")
sys.stdout.flush()
sys.exit(1 if fails else 0)
