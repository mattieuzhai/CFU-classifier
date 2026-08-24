"""End-to-end inference: the real worker calling the real model.

Run with:  ../.venv/bin/python tests/test_inference.py    (from annotator/)

This file exists because of a shipped bug. Every other UI test injects fake
detections through _on_image_done(), so nothing exercised the path
MainWindow -> InferenceWorker -> Detector.predict(**settings). When the
labelling mode was added to the settings dict, it was splatted into predict()
and every annotate run died with:

    Detector.predict() got an unexpected keyword argument 'labelling'

The signature check below catches that class of mistake without even loading a
model; the rest actually runs the thing.
"""

import inspect
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
MODEL = ROOT.parent / "nuc" / "best.pt"
TMP = Path(tempfile.mkdtemp(prefix="cfu_infer_"))

from PyQt5.QtCore import Qt, QSettings                       # noqa: E402
from PyQt5.QtGui import QImage                               # noqa: E402
from PyQt5.QtTest import QTest                               # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox        # noqa: E402

FAILURES = []
def check(name, condition, detail=""):
    print(("PASS  " if condition else "FAIL  ") + name + (f"   {detail}" if detail else ""))
    sys.stdout.flush()
    if not condition:
        FAILURES.append(name)

QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.warning = staticmethod(lambda *a, **k: None)
QMessageBox.critical = staticmethod(lambda *a, **k: None)

APP = QApplication.instance() or QApplication(sys.argv)
QSettings("StJude", "CFU Annotator").clear()

from cfu_annotator.detector import Detector                  # noqa: E402
from cfu_annotator.mainwindow import (                       # noqa: E402
    LABEL_MODE_MANUAL, LABEL_MODE_MODEL, LABEL_MODE_SUGGEST, MainWindow,
)
from cfu_annotator.canvas import UNLABELLED                  # noqa: E402
from cfu_annotator.workers import InferenceWorker            # noqa: E402
from cfu_annotator import scan                               # noqa: E402


# ---- structural: no model needed, catches the bug at its source ----------

def test_signature_agreement():
    accepted = set(inspect.signature(Detector.predict).parameters) - {"self"}

    check("worker only forwards keys predict accepts",
          set(InferenceWorker.PREDICT_KEYS) <= accepted,
          sorted(set(InferenceWorker.PREDICT_KEYS) - accepted))

    window = MainWindow()
    kwargs = set(window._predict_kwargs())
    check("_predict_kwargs() are all accepted by predict()",
          kwargs <= accepted, sorted(kwargs - accepted))
    check("_predict_kwargs() are all forwardable by the worker",
          kwargs <= set(InferenceWorker.PREDICT_KEYS),
          sorted(kwargs - set(InferenceWorker.PREDICT_KEYS)))

    recorded = set(window._detection_settings())
    check("_detection_settings() records more than it passes",
          recorded > kwargs, (sorted(recorded), sorted(kwargs)))
    check("the extra recorded keys are bookkeeping, not predict args",
          "labelling" in recorded - kwargs, sorted(recorded - kwargs))

    # The guard itself
    try:
        InferenceWorker(None, [], {"labelling": "manual", "conf": 0.25})
        check("worker rejects settings predict cannot use", False)
    except TypeError as exc:
        check("worker rejects settings predict cannot use", "labelling" in str(exc),
              str(exc))
    try:
        InferenceWorker(None, [], {"conf": 0.25, "tiling": False})
        check("worker accepts a clean settings dict", True)
    except TypeError as exc:
        check("worker accepts a clean settings dict", False, str(exc))


# ---- behavioural: really run the model through the worker ---------------

def run_annotate(window, timeout_ms=120000):
    waited = 0
    while window.infer_worker is not None and waited < timeout_ms:
        APP.processEvents()
        QTest.qWait(50)
        waited += 50
    return window.infer_worker is None


def test_real_run():
    images = TMP / "images"
    images.mkdir(parents=True, exist_ok=True)
    for i in range(2):
        img = QImage(900, 700, QImage.Format_RGB32)
        img.fill(Qt.darkGray)
        img.save(str(images / f"plate{i+1}.png"))

    detector = Detector(MODEL)
    for mode, label in ((LABEL_MODE_MODEL, "model labels"),
                        (LABEL_MODE_SUGGEST, "suggest"),
                        (LABEL_MODE_MANUAL, "detect only")):
        window = MainWindow()
        window.resize(1200, 800)
        window.show()
        QTest.qWait(20)
        window.image_folder = images
        window.images = scan.scan_folder(images).images
        window._on_model_loaded(detector)
        window.check_remember.setChecked(False)
        window.combo_labelling.setCurrentIndex(
            window.combo_labelling.findData(mode)
        )
        window.check_tiling.setChecked(False)      # small images: one pass
        window._rebuild_image_list()
        window._go_to(0)

        window.annotate_current()
        finished = run_annotate(window)
        check(f"[{label}] annotate this image completes", finished)
        record = window.records.get("plate1.png")
        check(f"[{label}] the image is marked annotated",
              bool(record and record.get("annotated")), record and record.keys())
        check(f"[{label}] the run's settings were recorded",
              bool(record and record.get("params")), record and record.get("params"))
        check(f"[{label}] the labelling mode is on the record",
              record and record["params"].get("labelling") == mode,
              record and record["params"].get("labelling"))

        # whatever came back must obey the mode
        boxes = record["boxes"] if record else []
        if mode == LABEL_MODE_MANUAL:
            check(f"[{label}] every box is unlabelled",
                  all(b["cls"] == UNLABELLED for b in boxes),
                  [b["cls"] for b in boxes][:5])
        elif mode == LABEL_MODE_SUGGEST:
            check(f"[{label}] every box is an unconfirmed suggestion",
                  all(b.get("unconfirmed") for b in boxes) if boxes else True)
        else:
            check(f"[{label}] boxes carry real classes",
                  all(b["cls"] >= 0 for b in boxes) if boxes else True)

        window.annotate_all()
        finished = run_annotate(window)
        check(f"[{label}] annotate all remaining completes", finished)
        check(f"[{label}] every image ends up annotated",
              all((window.records.get(p.name) or {}).get("annotated")
                  for p in window.images),
              {p.name: bool((window.records.get(p.name) or {}).get("annotated"))
               for p in window.images})
        window.close()


def main():
    test_signature_agreement()
    if MODEL.is_file():
        test_real_run()
    else:
        print(f"SKIP  real inference   (model not found at {MODEL})")
    QSettings("StJude", "CFU Annotator").clear()
    shutil.rmtree(TMP, ignore_errors=True)
    print()
    print("FAILURES:", FAILURES if FAILURES else "none")
    sys.stdout.flush()
    os._exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
