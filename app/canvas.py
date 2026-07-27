"""The annotation canvas: an image with editable, labelled bounding boxes.

Interaction model, deliberately close to LabelImg/CVAT:

    Select mode (default)   click a box to select it, drag its body to move,
                            drag a corner/edge handle to resize, drag empty
                            space to pan, Delete to remove.
    Draw mode (W)           drag anywhere to create a new box in the active class.
    Wheel                   zoom about the cursor.
    Middle-drag / Space     pan in any mode.
    1..9                    set the class of the selected box.
"""

from PyQt5.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPen,
    QPixmap,
)
from PyQt5.QtWidgets import (
    QGraphicsItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QMenu,
)

# Colour-blind-friendly, high-contrast against agar/colony backgrounds.
CLASS_COLORS = [
    "#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4",
    "#42d4f4", "#f032e6", "#bfef45", "#fabed4", "#469990",
]

HANDLE_PX = 9.0          # on-screen handle size, constant at any zoom
MIN_BOX_PX = 4.0         # smaller drags are treated as a stray click
LABEL_MIN_WIDTH_PX = 26  # hide labels on boxes too small to read them on

MODE_SELECT = "select"
MODE_DRAW = "draw"

_HANDLE_CURSORS = {
    "tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor,
    "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor,
    "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor,
    "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor,
}


def class_color(cls_id):
    return QColor(CLASS_COLORS[cls_id % len(CLASS_COLORS)])


class BoxItem(QGraphicsRectItem):
    """One bounding box. Item coordinates are image pixel coordinates."""

    def __init__(self, rect, cls_id, canvas, conf=None):
        super().__init__(rect)
        self.cls_id = cls_id
        self.conf = conf
        self.canvas = canvas
        self._grab = None          # which handle is being dragged, or "move"
        self._grab_origin = None
        self._grab_rect = None
        self._hover = False
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.refresh_z()

    # -- geometry ----------------------------------------------------------

    def refresh_z(self):
        """Small boxes sit on top, so a colony inside a clump stays clickable."""
        r = self.rect()
        area = max(1.0, r.width() * r.height())
        self.setZValue(10.0 + 1e7 / area)

    def _scale(self):
        scene = self.scene()
        views = scene.views() if scene else []
        if not views:
            return 1.0
        return views[0].transform().m11() or 1.0

    def _handle_size(self):
        return HANDLE_PX / self._scale()

    def _handle_rects(self):
        s = self._handle_size()
        r = self.rect()
        cx, cy = r.center().x(), r.center().y()
        points = {
            "tl": QPointF(r.left(), r.top()), "tr": QPointF(r.right(), r.top()),
            "bl": QPointF(r.left(), r.bottom()), "br": QPointF(r.right(), r.bottom()),
            "t": QPointF(cx, r.top()), "b": QPointF(cx, r.bottom()),
            "l": QPointF(r.left(), cy), "r": QPointF(r.right(), cy),
        }
        return {
            key: QRectF(p.x() - s / 2, p.y() - s / 2, s, s)
            for key, p in points.items()
        }

    def boundingRect(self):
        margin = self._handle_size()
        return self.rect().normalized().adjusted(-margin, -margin, margin, margin)

    def shape(self):
        from PyQt5.QtGui import QPainterPath

        path = QPainterPath()
        path.addRect(self.rect().normalized())
        if self.isSelected():
            for handle in self._handle_rects().values():
                path.addRect(handle)
        return path

    # -- painting ----------------------------------------------------------

    def paint(self, painter, option, widget=None):
        color = class_color(self.cls_id)
        selected = self.isSelected()
        rect = self.rect().normalized()

        pen = QPen(color)
        pen.setCosmetic(True)              # constant line width at any zoom
        pen.setWidth(3 if selected else 2)
        painter.setPen(pen)
        if selected or self._hover:
            fill = QColor(color)
            fill.setAlpha(55 if selected else 30)
            painter.setBrush(fill)
        else:
            painter.setBrush(Qt.NoBrush)
        painter.drawRect(rect)

        transform = painter.transform()
        width_px = rect.width() * self._scale()

        if self.canvas.show_labels and (selected or width_px >= LABEL_MIN_WIDTH_PX):
            self._paint_label(painter, transform, rect, color)

        if selected:
            painter.setBrush(QColor("#ffffff"))
            handle_pen = QPen(color)
            handle_pen.setCosmetic(True)
            handle_pen.setWidth(2)
            painter.setPen(handle_pen)
            for handle in self._handle_rects().values():
                painter.drawRect(handle)

    def _paint_label(self, painter, transform, rect, color):
        """Draw the label in device space so it stays legible at any zoom."""
        text = self.canvas.class_name(self.cls_id)
        if self.canvas.show_confidence and self.conf is not None:
            text = f"{text} {self.conf:.2f}"

        anchor = transform.map(rect.topLeft())
        painter.save()
        painter.resetTransform()

        font = QFont(painter.font())
        font.setPointSizeF(10.0)
        font.setBold(True)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        pad = 3
        box_w = metrics.horizontalAdvance(text) + 2 * pad
        box_h = metrics.height() + 2 * pad

        x = anchor.x()
        y = anchor.y() - box_h - 1
        if y < 0:                         # box is at the top edge: label inside
            y = anchor.y() + 1
        background = QRectF(x, y, box_w, box_h)

        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawRect(background)
        painter.setPen(QPen(QColor("#ffffff")))
        painter.drawText(background.adjusted(pad, pad, -pad, -pad), Qt.AlignLeft | Qt.AlignVCenter, text)
        painter.restore()

    # -- mouse -------------------------------------------------------------

    def _handle_at(self, pos):
        if not self.isSelected():
            return None
        for key, handle in self._handle_rects().items():
            if handle.contains(pos):
                return key
        return None

    def hoverEnterEvent(self, event):
        self._hover = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._hover = False
        self.setCursor(Qt.ArrowCursor)
        self.update()
        super().hoverLeaveEvent(event)

    def hoverMoveEvent(self, event):
        handle = self._handle_at(event.pos())
        if handle:
            self.setCursor(_HANDLE_CURSORS[handle])
        elif self.canvas.mode == MODE_SELECT:
            self.setCursor(Qt.SizeAllCursor)
        else:
            self.setCursor(Qt.CrossCursor)
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton or self.canvas.mode != MODE_SELECT:
            event.ignore()
            return
        self.scene().clearSelection()
        self.setSelected(True)
        if self.canvas.locked:
            # Selecting a box on a finalized image is fine; moving it is not.
            self.canvas.edit_blocked.emit()
            event.accept()
            return
        self._grab = self._handle_at(event.pos()) or "move"
        self._grab_origin = event.scenePos()
        self._grab_rect = QRectF(self.rect())
        event.accept()

    def mouseMoveEvent(self, event):
        if not self._grab:
            return
        delta = event.scenePos() - self._grab_origin
        rect = QRectF(self._grab_rect)

        if self._grab == "move":
            rect.translate(delta)
            bounds = self.canvas.image_rect()
            # keep a moved box fully inside the image
            if rect.left() < 0:
                rect.translate(-rect.left(), 0)
            if rect.top() < 0:
                rect.translate(0, -rect.top())
            if rect.right() > bounds.width():
                rect.translate(bounds.width() - rect.right(), 0)
            if rect.bottom() > bounds.height():
                rect.translate(0, bounds.height() - rect.bottom())
        else:
            if "l" in self._grab:
                rect.setLeft(rect.left() + delta.x())
            if "r" in self._grab:
                rect.setRight(rect.right() + delta.x())
            if "t" in self._grab:
                rect.setTop(rect.top() + delta.y())
            if "b" in self._grab:
                rect.setBottom(rect.bottom() + delta.y())
            rect = rect.normalized().intersected(self.canvas.image_rect())

        self.setRect(rect)
        self.canvas.box_geometry_preview(self)
        event.accept()

    def mouseReleaseEvent(self, event):
        if not self._grab:
            return
        self._grab = None
        rect = self.rect().normalized().intersected(self.canvas.image_rect())
        if rect.width() < 1 or rect.height() < 1:
            rect = self._grab_rect          # degenerate drag: undo it
        self.setRect(rect)
        self.refresh_z()
        self.canvas.notify_boxes_changed(user_edit=True)
        event.accept()

    def mouseDoubleClickEvent(self, event):
        self.canvas.prompt_class_change(self)
        event.accept()

    # -- data --------------------------------------------------------------

    def to_dict(self):
        r = self.rect().normalized()
        return {
            "cls": self.cls_id,
            "conf": self.conf,
            "xyxy": [r.left(), r.top(), r.right(), r.bottom()],
        }


class ImageCanvas(QGraphicsView):
    """Displays one image and its boxes, and lets the user edit them."""

    boxes_changed = pyqtSignal()
    user_edited = pyqtSignal()          # only for changes the user made by hand
    selection_changed = pyqtSignal()
    status_message = pyqtSignal(str)
    cursor_moved = pyqtSignal(float, float)
    zoom_changed = pyqtSignal(float)
    edit_blocked = pyqtSignal()         # an edit was attempted on a locked image

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.NoAnchor)
        self.setResizeAnchor(QGraphicsView.NoAnchor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setBackgroundBrush(QColor("#2b2b2b"))
        self.setDragMode(QGraphicsView.NoDrag)

        self.class_names = []
        self.mode = MODE_SELECT
        self.active_class = 0
        self.show_labels = True
        self.show_confidence = False
        self.locked = False        # set for images the user marked as finalized

        self._pixmap_item = None
        self._image_size = (0, 0)
        self._draft = None          # rubber-band box being drawn
        self._draft_origin = None
        self._panning = False
        self._pan_origin = None
        self._space_held = False

        self._scene.selectionChanged.connect(self._on_scene_selection_changed)

    def _on_scene_selection_changed(self):
        self.selection_changed.emit()

    def shutdown(self):
        """Detach from the scene before teardown.

        Without this, the scene emits selectionChanged while it is being
        destroyed and PyQt prints a spurious error after the window closes.
        """
        try:
            self._scene.selectionChanged.disconnect(self._on_scene_selection_changed)
        except TypeError:
            pass
        self._scene.clear()
        self._pixmap_item = None
        self._draft = None

    # -- setup -------------------------------------------------------------

    def set_class_names(self, names):
        self.class_names = list(names)
        if self.active_class >= len(self.class_names):
            self.active_class = 0

    def class_name(self, cls_id):
        if 0 <= cls_id < len(self.class_names):
            return self.class_names[cls_id]
        return f"class {cls_id}"

    def has_image(self):
        return self._pixmap_item is not None

    def image_size(self):
        return self._image_size

    def image_rect(self):
        return QRectF(0, 0, self._image_size[0], self._image_size[1])

    def load_image(self, path):
        pixmap = QPixmap(str(path))
        self._scene.clear()
        self._pixmap_item = None
        self._draft = None
        if pixmap.isNull():
            self._image_size = (0, 0)
            self._scene.setSceneRect(QRectF(0, 0, 1, 1))
            return False
        self._pixmap_item = self._scene.addPixmap(pixmap)
        self._pixmap_item.setZValue(0)
        self._image_size = (pixmap.width(), pixmap.height())
        self._scene.setSceneRect(self.image_rect())
        self.fit_to_window()
        return True

    def clear_image(self):
        self._scene.clear()
        self._pixmap_item = None
        self._draft = None
        self._image_size = (0, 0)

    # -- boxes -------------------------------------------------------------

    def box_items(self):
        return [i for i in self._scene.items() if isinstance(i, BoxItem)]

    def locked_out(self):
        """True if this image is finalized, so edits must be refused."""
        if self.locked:
            self.edit_blocked.emit()
            return True
        return False

    def set_boxes(self, boxes):
        """Replace the boxes programmatically (model output, project load).

        Deliberately does not count as a user edit, and is allowed even on a
        locked image so a project can be reloaded.
        """
        for item in self.box_items():
            self._scene.removeItem(item)
        for box in boxes:
            x1, y1, x2, y2 = box["xyxy"]
            rect = QRectF(QPointF(x1, y1), QPointF(x2, y2)).normalized()
            item = BoxItem(rect, int(box["cls"]), self, box.get("conf"))
            self._scene.addItem(item)
        self.notify_boxes_changed()

    def get_boxes(self):
        items = sorted(
            self.box_items(), key=lambda i: (i.rect().top(), i.rect().left())
        )
        return [i.to_dict() for i in items]

    def add_box(self, rect, cls_id, conf=None):
        if self.locked_out():
            return None
        item = BoxItem(rect.normalized(), cls_id, self, conf)
        self._scene.addItem(item)
        self._scene.clearSelection()
        item.setSelected(True)
        self.notify_boxes_changed(user_edit=True)
        return item

    def delete_selected(self):
        if self.locked_out():
            return 0
        removed = 0
        for item in self._scene.selectedItems():
            if isinstance(item, BoxItem):
                self._scene.removeItem(item)
                removed += 1
        if removed:
            self.notify_boxes_changed(user_edit=True)
            self.status_message.emit(f"Deleted {removed} box(es)")
        return removed

    def clear_boxes(self):
        if self.locked_out():
            return 0
        count = 0
        for item in self.box_items():
            self._scene.removeItem(item)
            count += 1
        if count:
            self.notify_boxes_changed(user_edit=True)
        return count

    def set_class_of_selected(self, cls_id):
        if self.locked_out():
            return 0
        changed = 0
        for item in self._scene.selectedItems():
            if isinstance(item, BoxItem):
                item.cls_id = cls_id
                item.conf = None       # no longer the model's prediction
                item.update()
                changed += 1
        if changed:
            self.notify_boxes_changed(user_edit=True)
            self.status_message.emit(
                f"Set {changed} box(es) to '{self.class_name(cls_id)}'"
            )
        return changed

    def selected_count(self):
        return sum(1 for i in self._scene.selectedItems() if isinstance(i, BoxItem))

    def counts_by_class(self):
        counts = [0] * max(1, len(self.class_names))
        for item in self.box_items():
            if 0 <= item.cls_id < len(counts):
                counts[item.cls_id] += 1
        return counts

    def notify_boxes_changed(self, user_edit=False):
        self.boxes_changed.emit()
        if user_edit:
            self.user_edited.emit()

    def box_geometry_preview(self, item):
        """Live feedback while a box is being dragged."""
        r = item.rect().normalized()
        self.status_message.emit(
            f"{self.class_name(item.cls_id)}  "
            f"x {r.left():.0f}–{r.right():.0f}   y {r.top():.0f}–{r.bottom():.0f}   "
            f"({r.width():.0f} × {r.height():.0f} px)"
        )

    def prompt_class_change(self, item):
        if self.locked_out():
            return
        menu = QMenu(self)
        menu.addAction("Change label to…").setEnabled(False)
        menu.addSeparator()
        for index, name in enumerate(self.class_names):
            action = menu.addAction(f"{index + 1}.  {name}")
            action.setCheckable(True)
            action.setChecked(index == item.cls_id)
            action.triggered.connect(
                lambda _checked, i=index, it=item: self._apply_class(it, i)
            )
        menu.addSeparator()
        menu.addAction("Delete box", lambda: self._delete_item(item))
        menu.exec_(self.mapToGlobal(self.mapFromScene(item.rect().center())))

    def _apply_class(self, item, cls_id):
        if self.locked_out():
            return
        item.cls_id = cls_id
        item.conf = None
        item.update()
        self.notify_boxes_changed(user_edit=True)

    def _delete_item(self, item):
        if self.locked_out():
            return
        self._scene.removeItem(item)
        self.notify_boxes_changed(user_edit=True)

    # -- modes and zoom ----------------------------------------------------

    def set_mode(self, mode):
        self.mode = mode
        if mode == MODE_DRAW:
            self.viewport().setCursor(Qt.CrossCursor)
            self._scene.clearSelection()
        else:
            self.viewport().setCursor(Qt.ArrowCursor)

    def set_active_class(self, cls_id):
        self.active_class = cls_id

    def set_locked(self, locked):
        self.locked = bool(locked)
        self.viewport().update()

    def drawForeground(self, painter, rect):
        """A badge in the corner, so a locked image is obvious while editing."""
        super().drawForeground(painter, rect)
        if not (self.locked and self.has_image()):
            return
        text = "FINALIZED — locked"
        painter.save()
        painter.resetTransform()
        font = QFont(painter.font())
        font.setPointSizeF(11.0)
        font.setBold(True)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        pad = 8
        box = QRectF(
            10, 10,
            metrics.horizontalAdvance(text) + 2 * pad,
            metrics.height() + 2 * pad,
        )
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(31, 79, 216, 225))
        painter.drawRoundedRect(box, 5, 5)
        painter.setPen(QPen(QColor("#ffffff")))
        painter.drawText(box, Qt.AlignCenter, text)
        painter.restore()

    def fit_to_window(self):
        if not self.has_image():
            return
        self.fitInView(self.image_rect(), Qt.KeepAspectRatio)
        self._after_zoom()

    def zoom_to_actual_size(self):
        if not self.has_image():
            return
        self.resetTransform()
        self._after_zoom()

    def zoom_by(self, factor, anchor_viewport_pos=None):
        if not self.has_image():
            return
        scale = self.transform().m11()
        if not (0.01 <= scale * factor <= 60):
            return
        if anchor_viewport_pos is None:
            anchor_viewport_pos = self.viewport().rect().center()
        before = self.mapToScene(anchor_viewport_pos)
        self.scale(factor, factor)
        after = self.mapToScene(anchor_viewport_pos)
        shift = after - before
        self.translate(shift.x(), shift.y())
        self._after_zoom()

    def _after_zoom(self):
        # Handles and labels are sized in screen pixels, so their bounding rects
        # change with the zoom level.
        for item in self.box_items():
            item.prepareGeometryChange()
        self.viewport().update()
        self.zoom_changed.emit(self.zoom_percent())

    def zoom_percent(self):
        return self.transform().m11() * 100.0

    # -- events ------------------------------------------------------------

    def wheelEvent(self, event):
        if not self.has_image():
            return
        delta = event.angleDelta().y()
        if delta:
            self.zoom_by(1.15 if delta > 0 else 1 / 1.15, event.pos())
        event.accept()

    def mousePressEvent(self, event):
        if not self.has_image():
            return super().mousePressEvent(event)

        wants_pan = event.button() == Qt.MiddleButton or (
            event.button() == Qt.LeftButton and self._space_held
        )
        if wants_pan:
            self._start_pan(event.pos())
            event.accept()
            return

        if event.button() == Qt.LeftButton and self.mode == MODE_DRAW:
            if self.locked_out():
                event.accept()
                return
            self._draft_origin = self.mapToScene(event.pos())
            self._draft = BoxItem(
                QRectF(self._draft_origin, self._draft_origin), self.active_class, self
            )
            self._scene.addItem(self._draft)
            event.accept()
            return

        if event.button() == Qt.LeftButton:
            item = self.itemAt(event.pos())
            if not isinstance(item, BoxItem):
                # Empty space in select mode: pan, and drop the selection.
                self._scene.clearSelection()
                self._start_pan(event.pos())
                event.accept()
                return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.has_image():
            scene_pos = self.mapToScene(event.pos())
            self.cursor_moved.emit(scene_pos.x(), scene_pos.y())

        if self._panning:
            delta = event.pos() - self._pan_origin
            self._pan_origin = event.pos()
            h, v = self.horizontalScrollBar(), self.verticalScrollBar()
            h.setValue(h.value() - delta.x())
            v.setValue(v.value() - delta.y())
            event.accept()
            return

        if self._draft is not None:
            current = self.mapToScene(event.pos())
            rect = QRectF(self._draft_origin, current).normalized()
            self._draft.setRect(rect.intersected(self.image_rect()))
            self.box_geometry_preview(self._draft)
            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._panning:
            self._panning = False
            self.viewport().setCursor(
                Qt.CrossCursor if self.mode == MODE_DRAW else Qt.ArrowCursor
            )
            event.accept()
            return

        if self._draft is not None:
            draft, self._draft = self._draft, None
            rect = draft.rect().normalized()
            scale = max(self.transform().m11(), 1e-6)
            too_small = (
                rect.width() * scale < MIN_BOX_PX or rect.height() * scale < MIN_BOX_PX
            )
            self._scene.removeItem(draft)
            if too_small:
                self.status_message.emit("Box discarded — drag further to draw one")
            else:
                self.add_box(rect, self.active_class)
                self.status_message.emit(
                    f"Added '{self.class_name(self.active_class)}' box"
                )
            event.accept()
            return

        super().mouseReleaseEvent(event)

    def _start_pan(self, pos):
        self._panning = True
        self._pan_origin = pos
        self.viewport().setCursor(Qt.ClosedHandCursor)

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_Space:
            self._space_held = True
            self.viewport().setCursor(Qt.OpenHandCursor)
            event.accept()
            return
        if key in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_selected()
            event.accept()
            return
        if key == Qt.Key_Escape:
            self._scene.clearSelection()
            event.accept()
            return
        if Qt.Key_1 <= key <= Qt.Key_9:
            index = key - Qt.Key_1
            if index < len(self.class_names):
                if self.selected_count():
                    self.set_class_of_selected(index)
                else:
                    self.set_active_class(index)
                    self.status_message.emit(
                        f"Active class: {self.class_name(index)}"
                    )
            event.accept()
            return
        if key == Qt.Key_Plus or key == Qt.Key_Equal:
            self.zoom_by(1.25)
            event.accept()
            return
        if key == Qt.Key_Minus:
            self.zoom_by(1 / 1.25)
            event.accept()
            return
        if key == Qt.Key_F:
            self.fit_to_window()
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key_Space:
            self._space_held = False
            if not self._panning:
                self.viewport().setCursor(
                    Qt.CrossCursor if self.mode == MODE_DRAW else Qt.ArrowCursor
                )
            event.accept()
            return
        super().keyReleaseEvent(event)
