"""Regression tests for the canvas geometry contract.

Run with:  ../.venv/bin/python tests/test_canvas_geometry.py   (from annotator/)

Background — the crash this guards against
------------------------------------------
QGraphicsScene caches each item's `boundingRect()` in its spatial index. An item
whose bounding rect silently changes therefore leaves the index describing
geometry that no longer exists.

`QGraphicsView::scale()` makes that fatal rather than merely wrong: it dispatches
a synthetic mouse-move *while* applying the new transform, so the stale index is
walked immediately. With a zoom-dependent `boundingRect()` (an earlier version
padded by `HANDLE_PX / zoom` so selection handles fit inside it), scrolling the
wheel over an annotated plate segfaulted inside
`QGraphicsScene::mouseMoveEvent`:

    Exception Type: EXC_BAD_ACCESS (SIGSEGV) at 0x145
    QGraphicsScene::mouseMoveEvent
    QGraphicsViewPrivate::mouseMoveEventHandler
    QGraphicsView::setTransform / scale
    sipQGraphicsView::wheelEvent            <- our zoom handler

Handles and labels are now drawn by the view in device coordinates, so item
geometry depends only on the box rect. These tests hold that line.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt5.QtCore import QEvent, QPoint, QPointF, QRectF, Qt          # noqa: E402
from PyQt5.QtGui import QImage, QMouseEvent, QPainter, QPixmap, QWheelEvent  # noqa: E402
from PyQt5.QtWidgets import QApplication                              # noqa: E402

from cfu_annotator.canvas import MODE_DRAW, MODE_SELECT, ImageCanvas  # noqa: E402

FAILURES = []


def check(name, condition, detail=""):
    print(("PASS  " if condition else "FAIL  ") + name + (f"   {detail}" if detail else ""))
    sys.stdout.flush()
    if not condition:
        FAILURES.append(name)


def make_canvas(app, tmp_image):
    canvas = ImageCanvas()
    canvas.set_class_names(["BFU", "GM", "E", "GEMM"])
    canvas.resize(1000, 800)
    canvas.show()
    app.processEvents()
    canvas.load_image(tmp_image)
    return canvas


def send_wheel(canvas, delta, pos):
    event = QWheelEvent(
        QPointF(pos), QPointF(canvas.viewport().mapToGlobal(pos)),
        QPoint(0, delta), QPoint(0, delta), Qt.NoButton, Qt.NoModifier,
        Qt.NoScrollPhase, False,
    )
    QApplication.sendEvent(canvas.viewport(), event)


def send_mouse(canvas, kind, pos, button=Qt.LeftButton, buttons=Qt.NoButton):
    QApplication.sendEvent(canvas.viewport(), QMouseEvent(
        kind, QPointF(pos), QPointF(canvas.viewport().mapToGlobal(pos)),
        button, buttons, Qt.NoModifier,
    ))


def main():
    tmp = Path(os.environ.get("TMPDIR", "/tmp")) / "cfu_canvas_geometry_test.png"
    if not tmp.exists():
        image = QImage(4000, 4000, QImage.Format_RGB32)
        image.fill(Qt.darkGray)
        image.save(str(tmp))

    app = QApplication.instance() or QApplication(sys.argv)
    canvas = make_canvas(app, tmp)

    boxes = []
    for i in range(150):
        x, y = 60 + (i % 12) * 320, 60 + (i // 12) * 300
        boxes.append({"cls": i % 4, "conf": 0.8, "xyxy": [x, y, x + 70, y + 70]})
    canvas.set_boxes(boxes)
    check("test fixture loaded", len(canvas.get_boxes()) == 150)

    # -- the contract ------------------------------------------------------
    item = canvas.box_items()[0]
    seen_bounds, seen_shapes = set(), set()
    for zoom_call in (
        canvas.zoom_to_actual_size,
        lambda: canvas.zoom_by(0.2),
        lambda: canvas.zoom_by(20.0),
        canvas.fit_to_window,
        lambda: canvas.zoom_by(8.0),
    ):
        zoom_call()
        rect = item.boundingRect()
        seen_bounds.add((rect.x(), rect.y(), rect.width(), rect.height()))
        bounds = item.shape().boundingRect()
        seen_shapes.add((bounds.x(), bounds.y(), bounds.width(), bounds.height()))

    check("boundingRect() is identical at every zoom level",
          len(seen_bounds) == 1, seen_bounds)
    check("shape() is identical at every zoom level",
          len(seen_shapes) == 1, seen_shapes)
    check("boundingRect() contains the box",
          item.boundingRect().contains(item.rect().normalized()))
    check("boundingRect() changes when the rect does — with notification",
          _rect_tracks_geometry(item))

    # Selecting a box must not change its geometry either: handles are drawn by
    # the view, so they cannot leak into the item's bounds.
    before = QRectF(item.boundingRect())
    item.setSelected(True)
    check("selecting a box does not change its boundingRect",
          item.boundingRect() == before, (before, item.boundingRect()))
    item.setSelected(False)

    # -- the crash path ----------------------------------------------------
    canvas.fit_to_window()
    for box in canvas.box_items()[:5]:
        box.setSelected(True)

    for cycle in range(30):
        for i in range(12):
            pos = QPoint(200 + (i * 37) % 500, 150 + (i * 53) % 400)
            send_mouse(canvas, QEvent.MouseMove, pos, Qt.NoButton, Qt.NoButton)
            send_wheel(canvas, 120 if cycle % 2 == 0 else -120, pos)
            app.processEvents()
    check("wheel-zooming over 150 boxes survives 360 events", True)

    # Zoom while a box is mid-drag — the transform changes with a drag in flight.
    canvas.zoom_to_actual_size()
    target = canvas.box_items()[0]
    canvas._scene.clearSelection()
    target.setSelected(True)
    centre = canvas.mapFromScene(target.rect().center())
    send_mouse(canvas, QEvent.MouseButtonPress, centre, Qt.LeftButton, Qt.LeftButton)
    for step in range(10):
        moved = QPoint(centre.x() + step * 4, centre.y() + step * 3)
        send_mouse(canvas, QEvent.MouseMove, moved, Qt.NoButton, Qt.LeftButton)
        send_wheel(canvas, 120 if step % 2 else -120, moved)
        app.processEvents()
    send_mouse(canvas, QEvent.MouseButtonRelease, moved, Qt.LeftButton, Qt.NoButton)
    check("zooming during a box drag survives", True)

    # Drawing a new box while zooming.
    canvas.set_mode(MODE_DRAW)
    start = QPoint(400, 400)
    send_mouse(canvas, QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton)
    for step in range(8):
        moved = QPoint(start.x() + step * 9, start.y() + step * 7)
        send_mouse(canvas, QEvent.MouseMove, moved, Qt.NoButton, Qt.LeftButton)
        send_wheel(canvas, -120, moved)
        app.processEvents()
    send_mouse(canvas, QEvent.MouseButtonRelease, moved, Qt.LeftButton, Qt.NoButton)
    canvas.set_mode(MODE_SELECT)
    check("zooming while drawing a box survives", True)

    # Painting at extreme zooms must not fault either.
    for zoom in (0.05, 1.0, 40.0):
        canvas.zoom_to_actual_size()
        canvas.zoom_by(zoom)
        pixmap = QPixmap(600, 400)
        painter = QPainter(pixmap)
        canvas.render(painter)
        painter.end()
        app.processEvents()
    check("painting at 0.05x, 1x and 40x survives", True)

    canvas.shutdown()
    print()
    print("FAILURES:", FAILURES if FAILURES else "none")
    sys.stdout.flush()
    # os._exit avoids Qt teardown noise in CI logs.
    os._exit(1 if FAILURES else 0)


def _rect_tracks_geometry(item):
    """setRect must move the bounding rect (Qt notifies the scene for us)."""
    original = QRectF(item.rect())
    item.setRect(QRectF(original.x() + 500, original.y() + 500,
                        original.width() * 2, original.height() * 2))
    grew = item.boundingRect().width() > original.width()
    item.setRect(original)
    return grew


if __name__ == "__main__":
    main()
