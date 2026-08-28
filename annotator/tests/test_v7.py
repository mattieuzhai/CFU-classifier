"""Getting back to the model's classes after loading a custom list (v1.5.1).

Run with:  ../.venv/bin/python tests/test_v7.py     (from annotator/)

Loading your own class list used to be a one-way door: it persisted between
sessions, and the only way back to the model's classes was uploading another
file.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
MODEL = ROOT.parent / "nuc" / "best.pt"
TMP = Path(tempfile.mkdtemp(prefix="cfu_v7_"))

from PyQt5.QtCore import QSettings, Qt                     # noqa: E402
from PyQt5.QtGui import QImage                             # noqa: E402
from PyQt5.QtTest import QTest                             # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox      # noqa: E402

IMG = TMP / "images"; IMG.mkdir(parents=True)

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

APP = QApplication.instance() or QApplication(sys.argv)
QSettings("StJude", "CFU Annotator").clear()
if not MODEL.is_file():
    print(f"SKIP  every test   (model not found at {MODEL})"); sys.exit(0)

from cfu_annotator.detector import Detector                # noqa: E402
from cfu_annotator.mainwindow import (                     # noqa: E402
    LABEL_MODE_MANUAL, LABEL_MODE_MODEL, LABEL_MODE_SUGGEST, MainWindow,
)
from cfu_annotator import scan                             # noqa: E402

im = QImage(900, 700, QImage.Format_RGB32); im.fill(Qt.darkGray)
im.save(str(IMG / "plate1.png"))
MINE = ["Round", "Diffuse", "Mixed"]
MODEL_CLASSES = ["BFU", "GM", "E", "GEMM"]
det = Detector(MODEL)

def new_window(mode=LABEL_MODE_MANUAL, custom=True, remember=False):
    w = MainWindow(); w.resize(1300, 900); w.show(); QTest.qWait(20)
    w.image_folder = IMG; w.images = scan.scan_folder(IMG).images
    w._on_model_loaded(det)
    w.check_remember.setChecked(remember)
    w.combo_labelling.blockSignals(True)
    w.combo_labelling.setCurrentIndex(w.combo_labelling.findData(mode))
    w.combo_labelling.blockSignals(False)
    w._rebuild_image_list(); w._go_to(0)
    if custom:
        w._set_class_names(MINE, custom=True, source="mine.txt")
    return w

# ============ the button ============
w = new_window()
check("custom list in force", w.canvas.class_names == MINE and w.custom_classes)
check("the reset button is offered", w.button_model_classes.isEnabled())
check("its label says what it does",
      "model" in w.button_model_classes.text().lower(), w.button_model_classes.text())

log.clear(); answer["q"] = QMessageBox.Cancel
w.use_model_classes()
check("it asks before replacing the list", any(k == "q" for k, *r in log), log)
check("cancelling keeps the custom list", w.canvas.class_names == MINE)
check("still marked custom", w.custom_classes)

answer["q"] = QMessageBox.Yes
w.use_model_classes()
check("confirming restores the model's classes",
      w.canvas.class_names == MODEL_CLASSES, w.canvas.class_names)
check("no longer marked custom", not w.custom_classes)
check("source line credits the model",
      "model" in w.label_class_source.text().lower(), w.label_class_source.text())
check("the class widget was rebuilt", w.list_classes.count() == 4)
check("the button switches itself off", not w.button_model_classes.isEnabled())
log.clear(); w.use_model_classes()
check("pressing it again is harmless",
      w.canvas.class_names == MODEL_CLASSES and "Already" in w.status_label.text(),
      w.status_label.text())

# ============ within a session: switching mode offers it ============
for mode, label in ((LABEL_MODE_MODEL, "model labels"),
                    (LABEL_MODE_SUGGEST, "suggest")):
    w2 = new_window(mode=LABEL_MODE_MANUAL)
    log.clear(); answer["q"] = QMessageBox.Yes
    w2.combo_labelling.setCurrentIndex(w2.combo_labelling.findData(mode))
    asked = [m for k, m, *r in log if k == "q"]
    check(f"[{label}] switching mode offers the model's classes",
          any("Switch to the model's classes?" in str(m) for m in asked), asked)
    check(f"[{label}] and applies them on yes",
          w2.canvas.class_names == MODEL_CLASSES, w2.canvas.class_names)
    check(f"[{label}] custom flag cleared", not w2.custom_classes)

w3 = new_window(mode=LABEL_MODE_MANUAL)
log.clear(); answer["q"] = QMessageBox.No
w3.combo_labelling.setCurrentIndex(w3.combo_labelling.findData(LABEL_MODE_MODEL))
check("declining keeps the custom list", w3.canvas.class_names == MINE,
      w3.canvas.class_names)
check("and it is still custom", w3.custom_classes)

# no prompt when there is nothing to reconcile
w4 = new_window(mode=LABEL_MODE_MANUAL, custom=False)
log.clear()
w4.combo_labelling.setCurrentIndex(w4.combo_labelling.findData(LABEL_MODE_MODEL))
check("no prompt when already on the model's classes",
      not any(k == "q" for k, *r in log), log)
w5 = new_window(mode=LABEL_MODE_MODEL)
log.clear()
w5.combo_labelling.setCurrentIndex(w5.combo_labelling.findData(LABEL_MODE_MANUAL))
check("no prompt when switching to detect-only", not any(k == "q" for k, *r in log), log)

# ============ between sessions ============
QSettings("StJude", "CFU Annotator").clear()
w6 = new_window(remember=True)
w6._save_settings()
w7 = MainWindow(); w7.show(); QTest.qWait(20)
check("a remembered custom list comes back", w7.canvas.class_names == MINE,
      w7.canvas.class_names)
w7._on_model_loaded(det)
check("the reset button is available in the new session",
      w7.button_model_classes.isEnabled())
answer["q"] = QMessageBox.Yes
w7.use_model_classes()
check("resetting works in a fresh session", w7.canvas.class_names == MODEL_CLASSES)
w7._save_settings()

w8 = MainWindow(); w8.show(); QTest.qWait(20)
check("the reset sticks across the next restart — no custom list restored",
      not w8.custom_classes, (w8.custom_classes, w8.canvas.class_names))
check("and nothing custom is left in the stored settings",
      w8.canvas.class_names == [], w8.canvas.class_names)
w8.check_remember.setChecked(False)

# ============ stranded boxes are reported ============
QSettings("StJude", "CFU Annotator").clear()
w9 = new_window()
w9._set_class_names(MINE + ["Extra", "Another", "Sixth"], custom=True, source="big.txt")
w9.records["plate1.png"] = {"boxes": [{"cls": 5, "conf": None, "xyxy": [1, 1, 9, 9]}],
                            "annotated": True, "edited": True, "finalized": False,
                            "contaminated": False}
log.clear(); answer["q"] = QMessageBox.Yes
w9.use_model_classes()
asked = " ".join(str(m) + str(r) for k, m, *r in log if k == "q")
check("boxes left outside the model's classes are called out",
      "1 existing box" in asked, asked[:160])
check("but the switch still happens on yes", w9.canvas.class_names == MODEL_CLASSES)

# ============ with no model loaded ============
w10 = MainWindow(); w10.show(); QTest.qWait(20)
w10._set_class_names(MINE, custom=True, source="mine.txt")
check("the button is disabled with no model", not w10.button_model_classes.isEnabled())
log.clear(); w10.use_model_classes()
check("and it explains why rather than doing nothing",
      any("No model loaded" in str(m) for k, m, *r in log), log)
check("the custom list survives", w10.canvas.class_names == MINE)

QSettings("StJude", "CFU Annotator").clear()
shutil.rmtree(TMP, ignore_errors=True)
print(); print("FAILURES:", fails if fails else "none")
sys.stdout.flush(); os._exit(1 if fails else 0)
