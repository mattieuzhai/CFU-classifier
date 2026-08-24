"""Drawing annotations onto a copy of a plate photo, for the export folder.

These are review copies: the same colours and labels as the on-screen canvas,
burned into the image so counts can be checked without the app. Line widths and
text scale with the image, so a 10000-pixel plate doesn't end up with hairline
boxes and unreadable labels.
"""

from pathlib import Path

from PyQt5.QtCore import QRectF, Qt
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPen

from .canvas import class_color

JPEG_QUALITY = 90
SUFFIX = "_annotated"


def output_name(image_name):
    """plate1.jpg -> plate1_annotated.jpg"""
    stem = Path(image_name).stem
    suffix = Path(image_name).suffix.lower()
    if suffix not in (".jpg", ".jpeg", ".png"):
        suffix = ".jpg"
    return f"{stem}{SUFFIX}{suffix}"


def render(source_path, boxes, class_names, dest_path, show_confidence=False):
    """Draw `boxes` on a copy of `source_path` and save it to `dest_path`.

    Returns None on success, or a message explaining why it failed.
    """
    image = QImage(str(source_path))
    if image.isNull():
        return f"could not open {Path(source_path).name}"
    if image.format() != QImage.Format_RGB32:
        image = image.convertToFormat(QImage.Format_RGB32)

    # One "unit" per 1000 px of the longest side, so a 10000 px plate gets
    # 10x thicker lines and larger text than a 1000 px thumbnail would.
    unit = max(image.width(), image.height()) / 1000.0
    pen_width = max(2.0, 2.2 * unit)
    font_size = max(11.0, 13.0 * unit)

    font = QFont()
    font.setPixelSize(int(round(font_size)))
    font.setBold(True)
    metrics = QFontMetrics(font)
    pad = max(2.0, 0.25 * font_size)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    painter.setFont(font)

    for box in boxes:
        x1, y1, x2, y2 = box["xyxy"]
        rect = QRectF(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))
        color = class_color(int(box["cls"]))

        pen = QPen(color)
        pen.setWidthF(pen_width)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(rect)

        index = int(box["cls"])
        if index < 0:
            text = "?"
        elif index < len(class_names):
            text = class_names[index]
        else:
            text = f"class {index}"
        if box.get("unconfirmed") and index >= 0:
            text = f"{text}?"
        if show_confidence and box.get("conf") is not None:
            text = f"{text} {box['conf']:.2f}"

        label = QRectF(
            rect.left(),
            rect.top() - metrics.height() - 2 * pad,
            metrics.horizontalAdvance(text) + 2 * pad,
            metrics.height() + 2 * pad,
        )
        if label.top() < 0:                       # box at the top edge
            label.moveTop(rect.top())
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawRect(label)
        painter.setPen(QPen(QColor("#ffffff")))
        painter.drawText(
            label.adjusted(pad, 0, -pad, 0), Qt.AlignLeft | Qt.AlignVCenter, text
        )

    painter.end()

    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    ok = image.save(str(dest), quality=JPEG_QUALITY)
    return None if ok else f"could not write {dest.name}"
