"""gui/roi_editor.py — ROI drawing dialog for WB Plot Figure Generation.

Completely self-contained.  No shared state with ImageCanvas, param_panel,
results_panel, or any densitometry object.

Workflow:
  1. Load source image (read-only zoom/pan via scroll wheel / middle-mouse).
  2. Left-click drag → draw one bounding-box (the full blot strip region).
  3. Set lane_count via spinbox → vertical divider lines appear automatically.
  4. Drag individual dividers to adjust lane widths.
  5. "Reset Equal" resets dividers to uniform spacing.
  6. OK  → commits ImageBBox + list[LaneROI] to the caller's BlotSlot.
  7. Cancel → discards all changes.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    Qt, QPointF, QRectF, QRect,
)
from PySide6.QtGui import (
    QColor, QImage, QPen, QBrush, QPainter, QPixmap,
    QWheelEvent, QMouseEvent,
)
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QGraphicsItem, QGraphicsLineItem, QGraphicsRectItem,
    QGraphicsScene, QGraphicsView, QGraphicsPixmapItem,
    QLabel, QPushButton, QSpinBox, QSizePolicy,
    QDialogButtonBox, QFrame,
)

from core.figure_project import BlotSlot, ImageBBox, LaneROI


# ── Draggable lane-divider item ───────────────────────────────────────────────

class _LaneDividerItem(QGraphicsLineItem):
    """A vertical dashed line that the user can drag horizontally.

    Movement is constrained:
      • Horizontal only (y is fixed at scene-top).
      • x is clamped to [_min_x, _max_x] in scene coordinates.
    """

    def __init__(
        self,
        x_scene: float,
        y_top: float,
        y_bottom: float,
        min_x: float,
        max_x: float,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._min_x = min_x
        self._max_x = max_x
        self._fixed_y = y_top

        # Draw as a vertical line at (0, 0)→(0, height) in item coords;
        # position the item at (x_scene, y_top) in scene coords.
        self.setLine(0.0, 0.0, 0.0, y_bottom - y_top)
        self.setPos(x_scene, y_top)

        pen = QPen(QColor("#3A8EE6"))
        pen.setWidthF(1.5)
        pen.setStyle(Qt.PenStyle.DashLine)
        self.setPen(pen)

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setZValue(20)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            # Clamp to bbox bounds; allow horizontal movement only
            clamped_x = max(self._min_x, min(self._max_x, value.x()))
            return QPointF(clamped_x, self._fixed_y)
        return super().itemChange(change, value)

    def scene_x(self) -> float:
        return self.pos().x()


# ── ROI drawing scene ─────────────────────────────────────────────────────────

class _ROIScene(QGraphicsScene):
    """Handles left-click-drag to draw the bounding-box rectangle."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._drawing = False
        self._draw_start: QPointF | None = None
        self._rect_item: QGraphicsRectItem | None = None
        self._pixmap_bounds: QRectF = QRectF()

    def set_pixmap_bounds(self, bounds: QRectF) -> None:
        self._pixmap_bounds = bounds

    def get_bbox_rect(self) -> QRectF | None:
        if self._rect_item is None:
            return None
        return self._rect_item.rect()

    def clear_bbox(self) -> None:
        if self._rect_item is not None:
            self.removeItem(self._rect_item)
            self._rect_item = None

    # ── Mouse events ──────────────────────────────────────────────────────

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.scenePos()
            if self._pixmap_bounds.contains(pos):
                self._drawing = True
                self._draw_start = pos
                # Remove any existing box
                if self._rect_item is not None:
                    self.removeItem(self._rect_item)
                    self._rect_item = None
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drawing and self._draw_start is not None:
            pos = self._clamp(event.scenePos())
            rect = QRectF(self._draw_start, pos).normalized()
            if self._rect_item is None:
                pen = QPen(QColor("#22AA66"))
                pen.setWidthF(2.0)
                brush = QBrush(QColor(34, 170, 102, 38))
                self._rect_item = self.addRect(rect, pen, brush)
                self._rect_item.setZValue(10)
            else:
                self._rect_item.setRect(rect)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._drawing:
            self._drawing = False
            if self._rect_item is not None:
                r = self._rect_item.rect()
                if r.width() < 4 or r.height() < 4:
                    self.removeItem(self._rect_item)
                    self._rect_item = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _clamp(self, pos: QPointF) -> QPointF:
        b = self._pixmap_bounds
        return QPointF(
            max(b.left(), min(b.right(), pos.x())),
            max(b.top(), min(b.bottom(), pos.y())),
        )


# ── ROI editor view ───────────────────────────────────────────────────────────

class _ROIView(QGraphicsView):
    """Zoom with scroll wheel; pan with middle-mouse or right-click drag."""

    def __init__(self, scene: _ROIScene, parent=None) -> None:
        super().__init__(scene, parent)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self._panning = False
        self._pan_start: QPointF | None = None

    def wheelEvent(self, event: QWheelEvent) -> None:
        factor = 1.15 if event.angleDelta().y() > 0 else 1.0 / 1.15
        self.scale(factor, factor)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() in (Qt.MouseButton.MiddleButton,
                               Qt.MouseButton.RightButton):
            self._panning = True
            self._pan_start = event.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._panning and self._pan_start is not None:
            delta = event.pos() - self._pan_start
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta.x()
            )
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - delta.y()
            )
            self._pan_start = event.pos()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() in (Qt.MouseButton.MiddleButton,
                               Qt.MouseButton.RightButton):
            self._panning = False
            self.setCursor(Qt.CursorShape.CrossCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)


# ── Main dialog ───────────────────────────────────────────────────────────────

class ROIEditorDialog(QDialog):
    """Draw one bounding-box and set lane dividers for a single BlotSlot.

    After exec() returns QDialog.Accepted the caller should read:
      dialog.result_bbox    → ImageBBox | None
      dialog.result_lanes   → list[LaneROI]
    """

    def __init__(self, slot: BlotSlot, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Draw ROI — {slot.label}")
        self.setMinimumSize(780, 560)
        self.resize(900, 640)

        self.result_bbox: ImageBBox | None = None
        self.result_lanes: list[LaneROI] = []

        self._slot = slot
        self._divider_items: list[_LaneDividerItem] = []
        self._image: QImage | None = None
        self._pixmap_item: QGraphicsPixmapItem | None = None

        self._scene = _ROIScene(self)
        self._view = _ROIView(self._scene, self)

        self._build_ui()
        self._load_image()

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # Instructions
        info = QLabel(
            "<b>Step 1:</b> Left-click and drag to draw the blot bounding box.  "
            "<b>Step 2:</b> Set lane count — dividers appear automatically.  "
            "<b>Step 3:</b> Drag individual dividers to adjust boundaries."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color:#445; font-size:11px; padding:4px 0;")
        root.addWidget(info)

        # View
        self._view.setMinimumHeight(360)
        self._view.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        root.addWidget(self._view, 1)

        # Controls row
        ctrl = QHBoxLayout()
        ctrl.setSpacing(16)

        # Lane count
        lane_form = QFormLayout()
        lane_form.setContentsMargins(0, 0, 0, 0)
        lane_form.setSpacing(6)
        self._lane_spin = QSpinBox()
        self._lane_spin.setRange(1, 12)
        self._lane_spin.setValue(self._slot.lane_count)
        self._lane_spin.valueChanged.connect(self._rebuild_dividers)
        lane_form.addRow("Lanes:", self._lane_spin)
        ctrl.addLayout(lane_form)

        # Reset button
        reset_btn = QPushButton("Reset Equal")
        reset_btn.setToolTip("Redistribute dividers at equal widths")
        reset_btn.clicked.connect(self._reset_equal)
        ctrl.addWidget(reset_btn)

        # Clear ROI button
        clear_btn = QPushButton("Clear ROI")
        clear_btn.clicked.connect(self._clear_roi)
        ctrl.addWidget(clear_btn)

        ctrl.addStretch(1)

        # Status label
        self._status_lbl = QLabel("No bounding box drawn yet.")
        self._status_lbl.setStyleSheet("color:#667; font-size:10px;")
        ctrl.addWidget(self._status_lbl)

        root.addLayout(ctrl)

        # Dialog buttons
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        root.addWidget(sep)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    # ── Image loading ─────────────────────────────────────────────────────

    def _load_image(self) -> None:
        path = self._slot.source_image_path
        if not path or not Path(path).exists():
            lbl = QLabel("No image loaded for this blot slot.")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._scene.addWidget(lbl)
            self._status_lbl.setText("No image — ROI will be saved as placeholder.")
            return

        self._image = QImage(path)
        if self._image.isNull():
            self._status_lbl.setText("Could not read image file.")
            return

        pm = QPixmap.fromImage(self._image)
        self._pixmap_item = self._scene.addPixmap(pm)
        self._pixmap_item.setZValue(0)
        self._scene.set_pixmap_bounds(
            QRectF(0.0, 0.0, float(pm.width()), float(pm.height()))
        )
        self._scene.setSceneRect(QRectF(0.0, 0.0, float(pm.width()), float(pm.height())))
        self._view.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)

        # If the slot already has a bounding box, re-draw it
        if self._slot.bounding_box:
            bb = self._slot.bounding_box
            self._scene._rect_item = self._scene.addRect(
                QRectF(bb.x, bb.y, bb.w, bb.h),
                QPen(QColor("#22AA66"), 2.0),
                QBrush(QColor(34, 170, 102, 38)),
            )
            self._scene._rect_item.setZValue(10)
            self._rebuild_dividers(self._lane_spin.value())
            self._update_status()

    # ── Divider management ────────────────────────────────────────────────

    def _rebuild_dividers(self, lane_count: int) -> None:
        """Remove old dividers and place new ones at equal spacing within bbox."""
        self._remove_dividers()
        bbox = self._scene.get_bbox_rect()
        if bbox is None or bbox.width() < 4:
            return
        self._place_dividers_equal(bbox, lane_count)
        self._update_status()

    def _place_dividers_equal(self, bbox: QRectF, lane_count: int) -> None:
        if lane_count < 2:
            return
        w = bbox.width() / lane_count
        y_top = bbox.top()
        y_bot = bbox.bottom()
        # Add (lane_count - 1) dividers between lanes
        for i in range(1, lane_count):
            x = bbox.left() + i * w
            item = _LaneDividerItem(
                x_scene=x,
                y_top=y_top,
                y_bottom=y_bot,
                min_x=bbox.left() + 2.0,
                max_x=bbox.right() - 2.0,
            )
            self._scene.addItem(item)
            self._divider_items.append(item)

    def _remove_dividers(self) -> None:
        for item in self._divider_items:
            self._scene.removeItem(item)
        self._divider_items.clear()

    def _reset_equal(self) -> None:
        bbox = self._scene.get_bbox_rect()
        if bbox is None:
            return
        self._rebuild_dividers(self._lane_spin.value())

    def _clear_roi(self) -> None:
        self._scene.clear_bbox()
        self._remove_dividers()
        # Re-add pixmap (clear_bbox only removes rect, not image)
        self._update_status()

    def _update_status(self) -> None:
        bbox = self._scene.get_bbox_rect()
        if bbox is None:
            self._status_lbl.setText("No bounding box drawn yet.")
        else:
            self._status_lbl.setText(
                f"Box: ({bbox.x():.0f}, {bbox.y():.0f})  "
                f"{bbox.width():.0f} × {bbox.height():.0f} px  |  "
                f"{self._lane_spin.value()} lane(s)"
            )

    # ── Overriding scene mouse to trigger divider rebuild ─────────────────

    def _on_scene_changed(self) -> None:
        """Called when a new bounding box may have been drawn."""
        bbox = self._scene.get_bbox_rect()
        if bbox is not None and not self._divider_items:
            self._rebuild_dividers(self._lane_spin.value())
        self._update_status()

    # ── Accept / commit ───────────────────────────────────────────────────

    def _on_accept(self) -> None:
        bbox_rect = self._scene.get_bbox_rect()

        if bbox_rect is None:
            # Accept without ROI data (user skipped drawing)
            self.result_bbox = None
            self.result_lanes = []
            self.accept()
            return

        # Convert scene (IMAGE_PX) bbox → ImageBBox
        self.result_bbox = ImageBBox(
            x=bbox_rect.x(),
            y=bbox_rect.y(),
            w=bbox_rect.width(),
            h=bbox_rect.height(),
        )

        # Read divider x positions back → REL LaneROIs
        self.result_lanes = self._read_lane_rois(bbox_rect)
        self.accept()

    def _read_lane_rois(self, bbox: QRectF) -> list[LaneROI]:
        """Convert divider scene-x positions to REL LaneROI list."""
        lane_count = self._lane_spin.value()
        bw = bbox.width()
        if bw <= 0:
            return []

        # Gather divider x positions in scene coordinates; sort left→right
        div_xs = sorted(item.scene_x() for item in self._divider_items)

        # Build lane edges: [left_edge, div1, div2, …, right_edge]
        edges = [bbox.left()] + div_xs + [bbox.right()]

        rois: list[LaneROI] = []
        for i in range(len(edges) - 1):
            x_off = (edges[i] - bbox.left()) / bw
            width  = (edges[i + 1] - edges[i]) / bw
            rois.append(LaneROI(
                lane_index=i,
                x_offset=round(max(0.0, x_off), 8),
                width=round(max(0.001, width), 8),
            ))

        # Pad to lane_count if fewer dividers than expected
        while len(rois) < lane_count:
            last = rois[-1] if rois else LaneROI(0, 0.0, 1.0)
            rois.append(LaneROI(
                lane_index=len(rois),
                x_offset=last.x_offset + last.width,
                width=0.0,
            ))

        return rois[:lane_count]
