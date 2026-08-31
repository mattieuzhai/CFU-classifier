"""Performance work: off-thread decoding, foreground culling, single-pass counts.

Run with:  ../.venv/bin/python tests/test_v8.py     (from annotator/)

The optimisations these cover are all invisible when they work and quietly
wrong when they don't, so each one is pinned to the behaviour it must preserve:

  * navigating no longer waits for a 40-110 megapixel decode, but the boxes and
    the image size must still be right the instant _go_to returns;
  * a decode for an image the user has already left must never be shown;
  * drawForeground now culls, and must still label exactly what it labelled
    before;
  * canvas.tally() replaced three separate scans and must agree with them.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="cfu_v8_"))

from PyQt5.QtCore import QRectF, QSettings, Qt             # noqa: E402
from PyQt5.QtGui import QImage, QPainter                   # noqa: E402
from PyQt5.QtTest import QTest                             # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox      # noqa: E402

fails = []
def check(n, c, x=""):
    print(("PASS  " if c else "FAIL  ") + n + (f"   {x}" if x else "")); sys.stdout.flush()
    if not c: fails.append(n)

log = []
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
QMessageBox.information = staticmethod(lambda *a, **k: log.append(("i", a[1], a[2] if len(a) > 2 else "")))
QMessageBox.warning = staticmethod(lambda *a, **k: log.append(("w", a[1], a[2] if len(a) > 2 else "")))
QMessageBox.critical = staticmethod(lambda *a, **k: log.append(("c", a[1], a[2] if len(a) > 2 else "")))

APP = QApplication.instance() or QApplication(sys.argv)
QSettings("StJude", "CFU Annotator").clear()

from cfu_annotator import scan                             # noqa: E402
from cfu_annotator.canvas import (                         # noqa: E402
    ITEM_PAD, LABEL_MIN_WIDTH_PX, BoxItem, ImageCanvas,
)
from cfu_annotator.mainwindow import MainWindow            # noqa: E402

IMG = TMP / "images"; IMG.mkdir(parents=True)
SIZES = {"plate1.png": (900, 700), "plate2.png": (1100, 640), "plate3.png": (820, 980)}
for name, (w, h) in SIZES.items():
    im = QImage(w, h, QImage.Format_RGB32); im.fill(Qt.darkGray)
    im.save(str(IMG / name))
# A file with a .png name that is not an image at all.
(IMG / "plate4.png").write_bytes(b"this is not a PNG")


def wait_for_pixels(window, timeout=4000):
    """Spin the event loop until the decode worker has delivered."""
    waited = 0
    while waited < timeout:
        if window.decode_worker is None and window.canvas.showing_pixels():
            return True
        QTest.qWait(20); waited += 20
    return window.canvas.showing_pixels()


def drain(window, timeout=4000):
    """Let any outstanding decode deliver, so the next check starts clean."""
    waited = 0
    while window.decode_worker is not None and waited < timeout:
        QTest.qWait(20); waited += 20
    return window.decode_worker is None


def new_window():
    w = MainWindow(); w.resize(1300, 900); w.show(); QTest.qWait(20)
    w.image_folder = IMG
    w.images = scan.scan_folder(IMG).images
    w._rebuild_image_list()
    return w


print("\n-- navigation does not block on the decode --")
win = new_window()
win._go_to(0)
name = win.images[0].name
check("_go_to returns with the size already known",
      win.canvas.image_size() == SIZES[name], f"{win.canvas.image_size()}")
check("canvas reports it has an image straight away", win.canvas.has_image())
check("...but the pixels have not arrived yet", not win.canvas.showing_pixels())
check("the size was recorded without waiting for the decode",
      win.image_sizes.get(name) == SIZES[name])
check("boxes are editable while the plate decodes", not win.canvas.locked)
check("a decode is in flight", win.decode_worker is not None)

check("pixels arrive shortly after", wait_for_pixels(win))
check("size is unchanged once the pixels land",
      win.canvas.image_size() == SIZES[name], f"{win.canvas.image_size()}")

print("\n-- boxes drawn before the pixels arrive survive the swap --")
win._go_to(1)
win.canvas.set_boxes([{"cls": 0, "conf": None, "xyxy": [10, 10, 90, 90]},
                      {"cls": 1, "conf": None, "xyxy": [200, 200, 260, 260]}])
check("boxes are on the canvas while it is still decoding",
      len(win.canvas.box_items()) == 2 and not win.canvas.showing_pixels())
wait_for_pixels(win)
check("both boxes still there after the pixels land",
      len(win.canvas.box_items()) == 2)
check("box coordinates untouched by the swap",
      win.canvas.get_boxes()[0]["xyxy"] == [10.0, 10.0, 90.0, 90.0])

print("\n-- a size stored from an earlier session is re-checked --")
win._go_to(0)
drain(win)
win.image_sizes["plate2.png"] = (123, 456)      # as if the file had been replaced
win._go_to(1)
check("the stored size is used before the decode lands",
      win.canvas.image_size() == (123, 456), f"{win.canvas.image_size()}")
drain(win)
check("the decoded plate corrects the canvas",
      win.canvas.image_size() == SIZES["plate2.png"], f"{win.canvas.image_size()}")
check("...and corrects the recorded size the export normalises against",
      win.image_sizes["plate2.png"] == SIZES["plate2.png"],
      f"{win.image_sizes['plate2.png']}")

print("\n-- clicking quickly through a folder --")
win._go_to(0)
win._go_to(1)
win._go_to(2)                      # two navigations while the first decodes
check("only one decode runs at a time", win.decode_worker is not None)
check("the newest request is queued, not started",
      win._decode_pending is None or win._decode_pending[0] == win._decode_token)
check("canvas already shows the last image's size",
      win.canvas.image_size() == SIZES["plate3.png"], f"{win.canvas.image_size()}")
wait_for_pixels(win)
check("settles on the image the user actually stopped on",
      win.canvas.image_size() == SIZES["plate3.png"], f"{win.canvas.image_size()}")
check("no decode left running", win.decode_worker is None)

print("\n-- a stale decode is discarded --")
win._go_to(0)
stale_token = win._decode_token
win._go_to(1)
wait_for_pixels(win)
before = win.canvas.image_size()
win._on_image_decoded(stale_token, QImage(SIZES["plate1.png"][0],
                                          SIZES["plate1.png"][1],
                                          QImage.Format_RGB32))
check("a result for the image we left does not replace the current one",
      win.canvas.image_size() == before, f"{win.canvas.image_size()} vs {before}")

print("\n-- closing a project cancels a decode in flight --")
win._go_to(0)
token_before = win._decode_token
win._cancel_pending_decode()
check("cancelling invalidates the token", win._decode_token != token_before)
win._on_image_decoded(token_before, QImage(50, 50, QImage.Format_RGB32))
check("the cancelled decode is ignored", not win.canvas.showing_pixels())

print("\n-- a file that is not really an image --")
check("the worker from the cancelled decode is reaped", drain(win))
log.clear()
win._go_to(3)
check("unopenable file is reported", any(e[0] == "w" for e in log), str(log))
check("canvas is cleared for an unopenable file",
      win.canvas.image_size() == (0, 0) and not win.canvas.has_image())
check("no decode was started for it", win.decode_worker is None)
win.close(); QTest.qWait(30)

print("\n-- the undo stack is bounded by size, not just by depth --")
from cfu_annotator.mainwindow import UNDO_BOX_BUDGET, UNDO_DEPTH   # noqa: E402
w2 = new_window()
w2._go_to(0)
drain(w2)
small = [{"cls": 0, "conf": None, "xyxy": [i, i, i + 5, i + 5]} for i in range(20)]
for _ in range(UNDO_DEPTH + 30):
    w2._undo_stack.append({"name": "plate1.png", "boxes": [dict(b) for b in small],
                           "flags": {"annotated": True, "edited": True,
                                     "finalized": False, "contaminated": False}})
    w2._trim_undo()
check("ordinary plates keep the full undo depth",
      len(w2._undo_stack) == UNDO_DEPTH, f"{len(w2._undo_stack)} steps")

big = [{"cls": 0, "conf": None, "xyxy": [i, i, i + 5, i + 5]} for i in range(5000)]
w2._undo_stack = []
for _ in range(40):
    w2._undo_stack.append({"name": "plate1.png", "boxes": [dict(b) for b in big],
                           "flags": {"annotated": True, "edited": True,
                                     "finalized": False, "contaminated": False}})
    w2._trim_undo()
held = sum(len(st["boxes"]) for st in w2._undo_stack)
check("a busy plate stops the stack growing without limit",
      held <= UNDO_BOX_BUDGET, f"{held} boxes in {len(w2._undo_stack)} steps")
check("...but undo still works", len(w2._undo_stack) >= 1,
      f"{len(w2._undo_stack)} steps")
check("the most recent step is the one kept",
      w2._undo_stack[-1]["boxes"][0]["xyxy"] == [0, 0, 5, 5])

# a single snapshot larger than the whole budget must still be undoable
w2._undo_stack = []
huge = [{"cls": 0, "conf": None, "xyxy": [0, 0, 1, 1]}
        for _ in range(UNDO_BOX_BUDGET + 10)]
w2._undo_stack.append({"name": "plate1.png", "boxes": huge,
                       "flags": {"annotated": True, "edited": True,
                                 "finalized": False, "contaminated": False}})
w2._trim_undo()
check("one edit bigger than the whole budget is still undoable",
      len(w2._undo_stack) == 1, f"{len(w2._undo_stack)} steps")
w2.close(); QTest.qWait(20)

print("\n-- tally() agrees with the three scans it replaced --")
cv = ImageCanvas(); cv.resize(600, 500)
cv.set_class_names(["a", "b", "c"])
cv.load_image(str(IMG / "plate1.png"))
cv.set_boxes([
    {"cls": 0, "conf": 0.9, "xyxy": [0, 0, 40, 40]},
    {"cls": 0, "conf": 0.8, "xyxy": [50, 0, 90, 40]},
    {"cls": 2, "conf": 0.7, "xyxy": [0, 50, 40, 90], "unconfirmed": True},
    {"cls": -1, "conf": None, "xyxy": [50, 50, 90, 90]},
    {"cls": 9, "conf": None, "xyxy": [100, 100, 140, 140]},   # beyond the classes
])
counts, unlabelled, unconfirmed = cv.tally()
check("tally counts match counts_by_class()",
      counts == cv.counts_by_class(), f"{counts} vs {cv.counts_by_class()}")
check("tally unlabelled matches unlabelled_count()",
      unlabelled == cv.unlabelled_count() == 1, f"{unlabelled}")
check("tally unconfirmed matches unconfirmed_count()",
      unconfirmed == cv.unconfirmed_count() == 1, f"{unconfirmed}")
check("a class beyond the list is not counted", counts == [2, 0, 1], f"{counts}")

print("\n-- item geometry is unchanged by dropping the Python override --")
item = BoxItem(QRectF(10, 20, 30, 40), 0, None)
want = QRectF(10, 20, 30, 40).adjusted(-ITEM_PAD, -ITEM_PAD, ITEM_PAD, ITEM_PAD)
check("boundingRect still pads by ITEM_PAD", item.boundingRect() == want,
      f"{item.boundingRect()} vs {want}")
check("shape() still hugs the rect exactly",
      item.shape().boundingRect() == QRectF(10, 20, 30, 40),
      f"{item.shape().boundingRect()}")
before = item.boundingRect()
cv.zoom_by(4.0)
check("boundingRect does not move when the view zooms",
      item.boundingRect() == before, f"{item.boundingRect()}")

print("\n-- drawForeground culls without changing what gets labelled --")

class Spy(ImageCanvas):
    """Records which boxes were given a label and which got handles."""
    def __init__(self):
        super().__init__()
        self.labelled = []
        self.handled = 0
    def _draw_label(self, painter, metrics, item, device, color):
        self.labelled.append(item)
        super()._draw_label(painter, metrics, item, device, color)
    def _draw_handles(self, painter, device, color):
        self.handled += 1
        super()._draw_handles(painter, device, color)

spy = Spy(); spy.resize(600, 500); spy.set_class_names(["a", "b"])
spy.load_image(str(IMG / "plate1.png"))
spy.zoom_to_actual_size()                     # 1 image px == 1 screen px
spy.set_boxes([
    {"cls": 0, "conf": None, "xyxy": [10, 10, 10 + LABEL_MIN_WIDTH_PX + 6, 40]},
    {"cls": 1, "conf": None, "xyxy": [100, 10, 100 + LABEL_MIN_WIDTH_PX - 6, 40]},
])
big, small = sorted(spy.box_items(), key=lambda i: -i.rect().width())

def repaint(canvas):
    canvas.labelled = []; canvas.handled = 0
    img = QImage(canvas.viewport().size(), QImage.Format_ARGB32_Premultiplied)
    p = QPainter(img); canvas.render(p); p.end()

repaint(spy)
check("a box wide enough on screen is labelled", big in spy.labelled)
check("a box too narrow to read is not", small not in spy.labelled)
check("nothing is selected, so no handles", spy.handled == 0)

small.setSelected(True)
repaint(spy)
check("a selected box is labelled however narrow it is", small in spy.labelled)
check("the selected box gets handles", spy.handled == 1)
check("each box is labelled exactly once",
      len(spy.labelled) == len(set(id(i) for i in spy.labelled)),
      f"{len(spy.labelled)}")

spy._scene.clearSelection()
spy.show_labels = False
repaint(spy)
check("labels off means no labels at all", spy.labelled == [])

spy.show_labels = True
spy.fit_to_window()                            # everything now tiny on screen
repaint(spy)
check("zoomed out, nothing is big enough to label", spy.labelled == [])

big.setSelected(True)
repaint(spy)
check("...except the selected box, which is always labelled",
      spy.labelled == [big])

print("\n-- boxes outside the viewport are skipped --")
spy.zoom_to_actual_size()
spy._scene.clearSelection()
spy.set_boxes([
    {"cls": 0, "conf": None, "xyxy": [10, 10, 60, 50]},
    {"cls": 0, "conf": None, "xyxy": [800, 600, 860, 650]},   # off screen
])
spy.centerOn(30, 30)
repaint(spy)
check("only the on-screen box is labelled", len(spy.labelled) == 1,
      f"{len(spy.labelled)} labelled")

spy.shutdown(); cv.shutdown()
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'ALL PASSED' if not fails else 'FAILURES: ' + ', '.join(fails)}")
sys.stdout.flush()
sys.exit(1 if fails else 0)
