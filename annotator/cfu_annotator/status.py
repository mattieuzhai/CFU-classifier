"""Per-image annotation status, and the small icons that represent it.

    not_annotated  the model hasn't run on this image and nothing was drawn
    annotated      the model produced boxes, untouched since
    edited         boxes were drawn, moved, resized, relabelled or deleted by hand
    finalized      the user declared the annotations complete (and locked them)
    contaminated   the plate is unusable; its counts are discarded and locked

Icons are drawn with QPainter rather than shipped as image files, so they stay
crisp on Retina displays and there are no assets to lose in a bundled app.
"""

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

NOT_ANNOTATED = "not_annotated"
ANNOTATED = "annotated"
EDITED = "edited"
FINALIZED = "finalized"
CONTAMINATED = "contaminated"

ORDER = [NOT_ANNOTATED, ANNOTATED, EDITED, FINALIZED, CONTAMINATED]

#: Statuses whose annotations are protected from further editing.
LOCKED = (FINALIZED, CONTAMINATED)

LABELS = {
    NOT_ANNOTATED: "Not annotated",
    ANNOTATED: "Annotated by the model",
    EDITED: "Annotated and edited by hand",
    FINALIZED: "Finalized (locked)",
    CONTAMINATED: "Contaminated — no counts (locked)",
}

SHORT_LABELS = {
    NOT_ANNOTATED: "Not annotated",
    ANNOTATED: "Annotated",
    EDITED: "Edited by hand",
    FINALIZED: "Finalized",
    CONTAMINATED: "Contaminated",
}

COLORS = {
    NOT_ANNOTATED: "#9e9e9e",
    ANNOTATED: "#1a7f37",
    EDITED: "#c26a00",
    FINALIZED: "#1f4fd8",
    CONTAMINATED: "#b3261e",
}


def is_locked(record):
    """True when a record's annotations must not be edited."""
    return status_of(record) in LOCKED


def status_of(record):
    """Derive the status of one image from its record."""
    if not record:
        return NOT_ANNOTATED
    # Contamination is terminal: it outranks everything else, including a plate
    # that was finalized before the contamination was spotted.
    if record.get("contaminated"):
        return CONTAMINATED
    if record.get("finalized"):
        return FINALIZED
    if record.get("edited"):
        return EDITED
    if record.get("annotated"):
        return ANNOTATED
    return NOT_ANNOTATED


# -- icons ------------------------------------------------------------------

_CACHE = {}


def _canvas(size):
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    return pixmap, painter


def _pen(color, width, size):
    pen = QPen(QColor(color))
    pen.setWidthF(width * size)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def _draw_not_annotated(painter, size):
    pen = _pen(COLORS[NOT_ANNOTATED], 0.075, size)
    pen.setStyle(Qt.DotLine)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawEllipse(QRectF(size * 0.22, size * 0.22, size * 0.56, size * 0.56))


def _draw_annotated(painter, size):
    """A tick mark."""
    painter.setPen(_pen(COLORS[ANNOTATED], 0.15, size))
    path = QPainterPath()
    path.moveTo(size * 0.20, size * 0.52)
    path.lineTo(size * 0.42, size * 0.76)
    path.lineTo(size * 0.82, size * 0.24)
    painter.drawPath(path)


def _draw_edited(painter, size):
    """A pencil angled up to the right: eraser, wooden body, graphite tip."""
    wood = QColor(COLORS[EDITED])
    start = QPointF(size * 0.18, size * 0.82)
    tip = QPointF(size * 0.86, size * 0.14)
    dx, dy = tip.x() - start.x(), tip.y() - start.y()

    def at(t):
        return QPointF(start.x() + dx * t, start.y() + dy * t)

    painter.setPen(Qt.NoPen)
    for t0, t1, color in (
        (0.00, 0.17, QColor("#d98cae")),   # eraser
        (0.17, 0.74, wood),                # body
    ):
        pen = QPen(color)
        pen.setWidthF(size * 0.27)
        pen.setCapStyle(Qt.FlatCap)
        painter.setPen(pen)
        painter.drawLine(at(t0), at(t1))

    # Graphite point: a triangle continuing along the pencil's axis.
    across = QPointF(0.7071, 0.7071)
    half = 0.135 * size
    base = at(0.74)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#3f3f3f"))
    painter.drawPolygon(QPolygonF([
        QPointF(base.x() + across.x() * half, base.y() + across.y() * half),
        QPointF(base.x() - across.x() * half, base.y() - across.y() * half),
        tip,
    ]))


def _draw_finalized(painter, size):
    """A padlock."""
    color = QColor(COLORS[FINALIZED])

    body = QRectF(size * 0.20, size * 0.46, size * 0.60, size * 0.40)

    # The shackle's ellipse is centred on the top of the body, so its two ends
    # meet the body instead of floating above it (which reads as 'unlocked').
    shackle_h = size * 0.44
    shackle = QRectF(
        size * 0.31, body.top() - shackle_h / 2, size * 0.38, shackle_h
    )
    painter.setPen(_pen(color, 0.11, size))
    painter.setBrush(Qt.NoBrush)
    painter.drawArc(shackle, 0, 180 * 16)

    painter.setPen(Qt.NoPen)
    painter.setBrush(color)
    painter.drawRoundedRect(body, size * 0.10, size * 0.10)

    painter.setBrush(QColor("#ffffff"))
    keyhole = size * 0.13
    painter.drawEllipse(QRectF(
        body.center().x() - keyhole / 2, body.center().y() - keyhole / 2,
        keyhole, keyhole,
    ))


def _draw_contaminated(painter, size):
    """A red 'excluded' sign — unmistakably different from the padlock."""
    color = QColor(COLORS[CONTAMINATED])
    painter.setPen(Qt.NoPen)
    painter.setBrush(color)
    disc = QRectF(size * 0.12, size * 0.12, size * 0.76, size * 0.76)
    painter.drawEllipse(disc)

    pen = _pen("#ffffff", 0.15, size)
    pen.setCapStyle(Qt.FlatCap)
    painter.setPen(pen)
    inset = size * 0.24
    painter.drawLine(
        QPointF(inset, size - inset), QPointF(size - inset, inset)
    )


_PAINTERS = {
    NOT_ANNOTATED: _draw_not_annotated,
    ANNOTATED: _draw_annotated,
    EDITED: _draw_edited,
    FINALIZED: _draw_finalized,
    CONTAMINATED: _draw_contaminated,
}


def status_icon(status, size=16):
    key = (status, size)
    if key not in _CACHE:
        pixmap, painter = _canvas(size)
        _PAINTERS.get(status, _draw_not_annotated)(painter, size)
        painter.end()
        _CACHE[key] = QIcon(pixmap)
    return _CACHE[key]
