"""Zoomable image canvas with rectangular ROI drawing and lane overlays."""
from __future__ import annotations

from pathlib import Path
from math import atan2, degrees

import numpy as np
from PIL import Image
from PySide6.QtCore import Qt, Signal, QRectF, QPointF, QSizeF, QPoint
from PySide6.QtGui import (
    QPixmap, QPen, QBrush, QColor, QPainter, QWheelEvent, QImage,
    QMouseEvent, QKeyEvent,
)
from PySide6.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QGraphicsRectItem, QGraphicsItem, QGraphicsLineItem, QGraphicsSimpleTextItem,
)

from core.image_transform import (
    ImageTransformParams,
    auto_scale_range_16,
    default_inverted_for_pil_image,
    image_array_to_uint16_luminance,
    transform_pixels_16_to_8,
)

# Morandi sage-green palette — muted, distinct but harmonious across 16 lanes
_LANE_COLORS = [
    "#8AB4A0", "#7AAEC8", "#A0B4C8", "#94B8A4", "#8AAEC4",
    "#7AB4A8", "#A0A8C4", "#8AB8B0", "#94A4C0", "#7ABCA8",
    "#A4B0C8", "#88B8A8", "#9AACC4", "#80B4AC", "#A8ACC0", "#8CB0B8",
]
_MAX_DESKEW_ANGLE_DEG = 45.0


class EditableBandRectItem(QGraphicsRectItem):
    _HANDLE_MARGIN = 7.0
    _MIN_SIZE = 4.0

    def __init__(self, rect: QRectF, image_rect: QRectF, notify_change, parent=None) -> None:
        super().__init__(QRectF(0, 0, rect.width(), rect.height()), parent)
        self.setPos(rect.topLeft())
        self.setAcceptHoverEvents(True)
        self.setZValue(11)

        self._image_rect = QRectF(image_rect)
        self._notify_change = notify_change
        self._editing_enabled = False
        self._active_handle: str | None = None
        self._start_scene_rect = QRectF()
        self._start_pos = QPointF()

    def set_editing_enabled(self, enabled: bool) -> None:
        self._editing_enabled = enabled
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, enabled)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, enabled)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, enabled)
        if not enabled:
            self.setSelected(False)
            self.unsetCursor()
        self.update()

    def scene_rect(self) -> QRectF:
        return QRectF(self.pos().x(), self.pos().y(), self.rect().width(), self.rect().height())

    def _handle_for_pos(self, pos: QPointF) -> str | None:
        if not self._editing_enabled:
            return None

        rect = self.rect()
        left = abs(pos.x() - rect.left()) <= self._HANDLE_MARGIN
        right = abs(pos.x() - rect.right()) <= self._HANDLE_MARGIN
        top = abs(pos.y() - rect.top()) <= self._HANDLE_MARGIN
        bottom = abs(pos.y() - rect.bottom()) <= self._HANDLE_MARGIN

        if left and top:
            return "top_left"
        if right and top:
            return "top_right"
        if left and bottom:
            return "bottom_left"
        if right and bottom:
            return "bottom_right"
        if left:
            return "left"
        if right:
            return "right"
        if top:
            return "top"
        if bottom:
            return "bottom"
        return None

    def _cursor_for_handle(self, handle: str | None) -> Qt.CursorShape:
        if handle in {"top_left", "bottom_right"}:
            return Qt.CursorShape.SizeFDiagCursor
        if handle in {"top_right", "bottom_left"}:
            return Qt.CursorShape.SizeBDiagCursor
        if handle in {"left", "right"}:
            return Qt.CursorShape.SizeHorCursor
        if handle in {"top", "bottom"}:
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.SizeAllCursor

    def hoverMoveEvent(self, event) -> None:
        if self._editing_enabled:
            self.setCursor(self._cursor_for_handle(self._handle_for_pos(event.pos())))
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        if self._editing_enabled and self._active_handle is None:
            self.unsetCursor()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if not self._editing_enabled:
            event.ignore()
            return

        self._active_handle = self._handle_for_pos(event.pos())
        self._start_scene_rect = self.scene_rect()
        self._start_pos = QPointF(self.pos())
        if self._active_handle is not None:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._active_handle is None:
            super().mouseMoveEvent(event)
            return

        delta = event.scenePos() - event.buttonDownScenePos(Qt.MouseButton.LeftButton)
        rect = QRectF(self._start_scene_rect)
        if "left" in self._active_handle:
            rect.setLeft(rect.left() + delta.x())
        if "right" in self._active_handle:
            rect.setRight(rect.right() + delta.x())
        if "top" in self._active_handle:
            rect.setTop(rect.top() + delta.y())
        if "bottom" in self._active_handle:
            rect.setBottom(rect.bottom() + delta.y())
        self._set_scene_rect(rect.normalized())
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        moved = self.pos() != self._start_pos
        if self._active_handle is not None:
            self._active_handle = None
            self._notify_change()
            event.accept()
            return

        super().mouseReleaseEvent(event)
        if moved:
            self._notify_change()

    def _set_scene_rect(self, rect: QRectF) -> None:
        bounded = QRectF(rect)
        bounded.setWidth(max(self._MIN_SIZE, bounded.width()))
        bounded.setHeight(max(self._MIN_SIZE, bounded.height()))

        if bounded.left() < self._image_rect.left():
            bounded.moveLeft(self._image_rect.left())
        if bounded.top() < self._image_rect.top():
            bounded.moveTop(self._image_rect.top())
        if bounded.right() > self._image_rect.right():
            bounded.moveRight(self._image_rect.right())
        if bounded.bottom() > self._image_rect.bottom():
            bounded.moveBottom(self._image_rect.bottom())

        bounded.setWidth(min(bounded.width(), self._image_rect.width()))
        bounded.setHeight(min(bounded.height(), self._image_rect.height()))

        self.prepareGeometryChange()
        self.setPos(bounded.topLeft())
        self.setRect(0, 0, bounded.width(), bounded.height())
        self.update()

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self._editing_enabled and self.scene():
            rect = self.scene_rect()
            proposed = QPointF(value)
            max_x = self._image_rect.right() - rect.width()
            max_y = self._image_rect.bottom() - rect.height()
            return QPointF(
                max(self._image_rect.left(), min(max_x, proposed.x())),
                max(self._image_rect.top(), min(max_y, proposed.y())),
            )
        return super().itemChange(change, value)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        rect = self.rect()
        pen = QPen(QColor("#E8A87C"), 2, Qt.PenStyle.SolidLine)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(QBrush(QColor(232, 168, 124, 50)))
        painter.drawRect(rect)

        center_pen = QPen(QColor(140, 79, 33, 160), 1, Qt.PenStyle.SolidLine)
        center_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        center_pen.setCosmetic(True)
        painter.setPen(center_pen)
        center_y = rect.center().y()
        painter.drawLine(QPointF(rect.left() + 2, center_y), QPointF(rect.right() - 2, center_y))

        if self._editing_enabled and self.isSelected():
            handle_pen = QPen(QColor("#2A5E48"), 1, Qt.PenStyle.SolidLine)
            painter.setPen(handle_pen)
            painter.setBrush(QBrush(QColor("#D4EDE4")))
            for handle in self._handle_rects():
                painter.drawRect(handle)

    def _handle_rects(self) -> list[QRectF]:
        rect = self.rect()
        size = 6.0
        half = size / 2.0
        points = [
            rect.topLeft(),
            QPointF(rect.center().x(), rect.top()),
            rect.topRight(),
            QPointF(rect.left(), rect.center().y()),
            QPointF(rect.right(), rect.center().y()),
            rect.bottomLeft(),
            QPointF(rect.center().x(), rect.bottom()),
            rect.bottomRight(),
        ]
        return [QRectF(point.x() - half, point.y() - half, size, size) for point in points]


class ImageCanvas(QGraphicsView):
    """
    Displays a WB image with zoom/pan and dual ROI drawing.

    Workflow:
    - First ROI: large horizontal ROI across all lanes (lane ROI)
    - Second ROI: small vertical band ROI for target band window (band ROI)

    Left-click + drag  → draw / replace ROI (primary first, secondary after).
    Space + drag       → pan the view.
    Scroll wheel       → zoom (anchored at cursor).
    """

    roi_changed = Signal(QRectF)         # main lane ROI
    band_roi_changed = Signal(QRectF)    # band measurement ROI
    auto_rois_changed = Signal(list)     # edited auto detections
    rotation_angle_changed = Signal(float)
    rotation_mode_changed = Signal(bool)
    panel_interacted = Signal()          # click/wheel/pan interaction for panel activation
    roi_cleared = Signal()               # emitted when ROI is cleared via right-click shortcut

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        self._pixmap_item: QGraphicsPixmapItem | None = None
        self._pixmap_original_size: QSizeF | None = None  # Original image size before any scaling
        self._display_source_pixels: np.ndarray | None = None
        self._image_default_transform_params = self._default_image_transform_params()
        self._image_transform_params = self._image_default_transform_params
        self._roi_item: QGraphicsRectItem | None = None
        self._band_roi_item: QGraphicsRectItem | None = None
        self._lane_items: list[QGraphicsItem] = []
        self._auto_band_items: list[EditableBandRectItem] = []
        self._auto_band_labels: list[QGraphicsItem] = []
        self._manual_band_labels: list[QGraphicsItem] = []
        self._auto_lane_frames: list[dict] = []
        self._interaction_mode = "manual"
        self._auto_edit_enabled = False
        self._auto_edit_tool = "move"
        self._rotation_mode = False
        self._rotation_dragging = False
        self._rotation_angle_deg = 0.0
        self._rotation_center = QPointF()
        self._rotation_h_line: QGraphicsLineItem | None = None
        self._rotation_v_line: QGraphicsLineItem | None = None
        self._rotation_angle_label: QGraphicsSimpleTextItem | None = None
        self._rotation_drag_start_mouse_angle_deg: float | None = None
        self._rotation_drag_start_crosshair_angle_deg: float = 0.0

        # ROI drawing state
        self._drawing = False
        self._drawing_band_roi = False  # Locked at mousePressEvent
        self._draw_start = QPointF()
        self._right_panning = False
        self._pan_last_pos = QPoint()
        self._fixed_roi_enabled = False
        self._fixed_roi_size: QSizeF | None = None
        self._fixed_roi_viewport_size: QSizeF | None = None
        self._fixed_band_roi_relative: QRectF | None = None
        self._moving_fixed_roi = False
        self._fixed_roi_move_offset = QPointF()
        self._wb_plot_roi_only = False

        # High-quality rendering for both shapes and pixmaps
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setBackgroundBrush(QColor("#EAF0F4"))
        self.setMouseTracking(True)

    # ── Public API ─────────────────────────────────────────────────────────────

    @staticmethod
    def _default_image_transform_params() -> ImageTransformParams:
        # Bio-Rad/Image Lab-style WB viewing usually presents bright signal as
        # dark bands on a light background. This affects display only.
        return ImageTransformParams(inverted=True)

    def load_image(self, path: str | Path) -> None:
        """Load an image file at full resolution and fit it in the view.

        The original image resolution is preserved in memory. Display scaling
        uses high-quality SmoothTransformation. ROI coordinates are in image space.
        """
        self._scene.clear()
        self._pixmap_item = None
        self._pixmap_original_size = None
        self._display_source_pixels = None
        self._image_default_transform_params = self._default_image_transform_params()
        self._image_transform_params = self._image_default_transform_params
        self._roi_item = None
        self._band_roi_item = None
        self._lane_items.clear()
        self._auto_band_items.clear()
        self._auto_band_labels.clear()
        self._manual_band_labels.clear()
        self._auto_lane_frames.clear()
        self._rotation_mode = False
        self._rotation_dragging = False
        self._rotation_angle_deg = 0.0
        self._rotation_center = QPointF()
        self._rotation_h_line = None
        self._rotation_v_line = None
        self._rotation_angle_label = None
        self._rotation_drag_start_mouse_angle_deg = None
        self._rotation_drag_start_crosshair_angle_deg = 0.0
        self._fixed_roi_enabled = False
        self._fixed_roi_size = None
        self._fixed_roi_viewport_size = None
        self._fixed_band_roi_relative = None
        self._moving_fixed_roi = False

        with Image.open(path) as img:
            default_inverted = default_inverted_for_pil_image(img, fallback=True)
            arr = np.array(img)
        self._image_default_transform_params = ImageTransformParams(inverted=default_inverted)
        self._image_transform_params = self._image_default_transform_params
        self._display_source_pixels = image_array_to_uint16_luminance(arr)

        px = self._make_display_pixmap()
        if px.isNull():
            raise ValueError(f"Could not load image: {path}")

        # Store original size before any scaling
        self._pixmap_original_size = QSizeF(px.width(), px.height())

        self._pixmap_item = QGraphicsPixmapItem(px)
        # Use high-quality transformation for this item during display
        self._pixmap_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._scene.addItem(self._pixmap_item)
        self._scene.setSceneRect(self._pixmap_item.boundingRect())
        self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
        self.resetTransform()
        self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)

    def has_image_transform_source(self) -> bool:
        return self._display_source_pixels is not None

    def get_image_transform_params(self) -> ImageTransformParams:
        return self._image_transform_params

    def set_image_transform_params(self, params: ImageTransformParams) -> None:
        """Apply display-only transform parameters without changing ROI geometry."""
        self._image_transform_params = params.sanitized()
        if self._pixmap_item is None or self._display_source_pixels is None:
            return
        self._pixmap_item.setPixmap(self._make_display_pixmap())
        self._scene.setSceneRect(self._pixmap_item.boundingRect())

    def has_modified_image_transform(self) -> bool:
        return self._image_transform_params != self._image_default_transform_params

    def has_quantitative_image_transform(self) -> bool:
        default = self._image_default_transform_params
        params = self._image_transform_params
        return (
            params.low != default.low
            or params.high != default.high
            or abs(params.gamma - default.gamma) > 1e-6
        )

    def current_display_pixels(self) -> np.ndarray | None:
        if self._display_source_pixels is None:
            return None
        return np.ascontiguousarray(
            transform_pixels_16_to_8(
                self._display_source_pixels,
                self._image_transform_params,
            )
        )

    def current_analysis_pixels(self) -> np.ndarray | None:
        if self._display_source_pixels is None:
            return None
        params = ImageTransformParams(
            low=self._image_transform_params.low,
            high=self._image_transform_params.high,
            gamma=self._image_transform_params.gamma,
            inverted=False,
        )
        return np.ascontiguousarray(
            transform_pixels_16_to_8(
                self._display_source_pixels,
                params,
            )
        )

    def reset_image_transform(self) -> ImageTransformParams:
        params = self._image_default_transform_params
        self.set_image_transform_params(params)
        return params

    def auto_scale_image_transform(self) -> ImageTransformParams:
        if self._display_source_pixels is None:
            params = self._image_default_transform_params
        else:
            low, high = auto_scale_range_16(self._display_source_pixels)
            params = ImageTransformParams(
                low=low,
                high=high,
                gamma=1.0,
                inverted=self._image_transform_params.inverted,
            )
        self.set_image_transform_params(params)
        return params

    def _make_display_pixmap(self) -> QPixmap:
        display = self.current_display_pixels()
        if display is None:
            return QPixmap()
        height, width = display.shape
        qimage = QImage(
            display.data,
            width,
            height,
            display.strides[0],
            QImage.Format.Format_Grayscale8,
        )
        return QPixmap.fromImage(qimage.copy())

    def get_roi(self) -> QRectF | None:
        """Return current main lane ROI in image/scene coordinates, or None.

        Converts from scene coordinates to original image pixel coordinates.
        """
        if self._roi_item is None or self._pixmap_original_size is None:
            return None

        scene_rect = self._roi_item.rect()
        # Scene coordinates ARE original image coordinates (no scaling of the pixmap itself)
        # Just return as-is
        return scene_rect

    def get_band_roi(self) -> QRectF | None:
        """Return current band ROI in image/scene coordinates, or None."""
        if self._band_roi_item is None:
            return None
        return self._band_roi_item.rect()

    def image_scene_size(self) -> QSizeF | None:
        """Return the loaded image size in scene/image coordinates."""
        if self._pixmap_item is None:
            return None
        rect = self._pixmap_item.boundingRect()
        return QSizeF(rect.width(), rect.height())

    def set_fixed_roi_mode(self, enabled: bool) -> bool:
        """Enable fixed-size main ROI placement for WB Plot cropping.

        If a main ROI already exists its width/height become the locked size.
        Otherwise the next drawn ROI establishes the locked size.
        """
        self._fixed_roi_enabled = enabled
        self._moving_fixed_roi = False
        self._fixed_band_roi_relative = None
        if enabled and self._roi_item is not None:
            rect = self._roi_item.rect()
            if rect.width() > 5 and rect.height() > 5:
                self._fixed_roi_size = QSizeF(rect.width(), rect.height())
                if self._band_roi_item is not None:
                    self._scene.removeItem(self._band_roi_item)
                    self._band_roi_item = None
                return True
        if not enabled:
            self._fixed_roi_size = None
            self._fixed_roi_viewport_size = None
        return False

    def cancel_fixed_roi_mode(self, *, clear_current_roi: bool = False) -> None:
        self._fixed_roi_enabled = False
        self._moving_fixed_roi = False
        self._fixed_roi_size = None
        self._fixed_roi_viewport_size = None
        self._fixed_band_roi_relative = None
        if clear_current_roi:
            self.clear_roi()

    def get_fixed_roi_size(self) -> QSizeF | None:
        return QSizeF(self._fixed_roi_size) if self._fixed_roi_size is not None else None

    def get_fixed_roi_viewport_size(self) -> QSizeF | None:
        """Return the fixed ROI's current visual size in viewport pixels."""
        if self._roi_item is None:
            return None
        rect = self.transform().mapRect(self._roi_item.rect())
        if rect.width() <= 0 or rect.height() <= 0:
            return None
        return QSizeF(rect.width(), rect.height())

    def set_fixed_roi_size(self, size: QSizeF, *, enabled: bool = True) -> None:
        if size.width() <= 0 or size.height() <= 0:
            return
        self._fixed_roi_size = QSizeF(size)
        self._fixed_roi_viewport_size = None
        self._fixed_band_roi_relative = None
        self._fixed_roi_enabled = enabled
        self._moving_fixed_roi = False

    def set_fixed_roi_viewport_size(self, size: QSizeF, *, enabled: bool = True) -> None:
        """Lock the ROI to a visual size, independent of source image dimensions."""
        if size.width() <= 0 or size.height() <= 0:
            return
        self._fixed_roi_viewport_size = QSizeF(size)
        self._fixed_roi_size = self._scene_size_for_viewport_size(size)
        self._fixed_band_roi_relative = None
        self._fixed_roi_enabled = enabled
        self._moving_fixed_roi = False

    def set_fixed_roi_profile(
        self,
        lane_size: QSizeF,
        *,
        band_relative: QRectF | None = None,
        enabled: bool = True,
    ) -> None:
        if lane_size.width() <= 0 or lane_size.height() <= 0:
            return
        self._fixed_roi_size = QSizeF(lane_size)
        self._fixed_roi_viewport_size = None
        self._fixed_band_roi_relative = QRectF(band_relative) if band_relative is not None else None
        self._fixed_roi_enabled = enabled
        self._moving_fixed_roi = False

    def finish_fixed_lane_roi_placement(self) -> bool:
        """Exit fixed-lane placement while keeping the placed lane ROI."""
        if (
            not self._fixed_roi_enabled
            or self._fixed_band_roi_relative is not None
            or self._roi_item is None
        ):
            return False
        self._fixed_roi_enabled = False
        self._moving_fixed_roi = False
        self._fixed_roi_size = None
        self._fixed_roi_viewport_size = None
        return True

    def set_wb_plot_roi_only(self, enabled: bool) -> None:
        """Use single large ROI behavior for WB Plot cropping."""
        self._wb_plot_roi_only = enabled
        if enabled:
            if self._band_roi_item is not None:
                self._scene.removeItem(self._band_roi_item)
                self._band_roi_item = None
            self.clear_auto_overlays()

    def set_lane_overlays(self, rects: list[QRectF]) -> None:
        """Replace lane overlay rectangles (scene/image coordinates) with smooth rendering."""
        self.clear_auto_overlays()

        for i, rect in enumerate(rects):
            color = QColor(_LANE_COLORS[i % len(_LANE_COLORS)])

            # Semi-transparent fill (15% opacity)
            fill = QColor(color)
            fill.setAlpha(38)

            pen = QPen(QColor(color), 1.5, Qt.PenStyle.DashLine)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            box = self._scene.addRect(rect, pen, QBrush(fill))
            box.setZValue(5)

            # Lane number label
            label = self._scene.addText(f"L{i + 1}")
            label.setDefaultTextColor(QColor("#2A5E48"))
            font = label.font()
            font.setPointSize(8)
            font.setBold(True)
            label.setFont(font)
            label.setPos(rect.x() + 3, rect.y() + 3)
            label.setZValue(6)

            self._lane_items.extend([box, label])

    def clear_roi(self) -> None:
        """Remove ROI, band ROI, and lane overlays."""
        if self._roi_item:
            self._scene.removeItem(self._roi_item)
            self._roi_item = None
        if self._band_roi_item:
            self._scene.removeItem(self._band_roi_item)
            self._band_roi_item = None
        self._moving_fixed_roi = False
        if not self._fixed_roi_enabled:
            self._fixed_roi_size = None
        self.clear_auto_overlays()

    def clear_auto_overlays(self) -> None:
        for item in self._lane_items:
            self._scene.removeItem(item)
        self._lane_items.clear()
        for item in self._auto_band_items:
            self._scene.removeItem(item)
        self._auto_band_items.clear()
        for label in self._auto_band_labels:
            self._scene.removeItem(label)
        self._auto_band_labels.clear()
        self.clear_manual_band_labels()
        self._auto_lane_frames.clear()

    def clear_manual_band_labels(self) -> None:
        for label in self._manual_band_labels:
            self._scene.removeItem(label)
        self._manual_band_labels.clear()

    def set_band_roi(self, rect: QRectF) -> None:
        """Set the band ROI programmatically (e.g. from auto-detection)."""
        if self._band_roi_item is not None:
            self._scene.removeItem(self._band_roi_item)
            self._band_roi_item = None

        pen = QPen(QColor("#E8A87C"), 2, Qt.PenStyle.SolidLine)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        brush = QBrush(QColor(232, 168, 124, 50))
        self._band_roi_item = self._scene.addRect(rect, pen, brush)
        self._band_roi_item.setZValue(11)
        self.band_roi_changed.emit(rect)

    def set_manual_band_labels(self, lane_rects: list[QRectF], band_roi: QRectF | None) -> None:
        """Render manual-mode band numbers (#1..#N) left-to-right above the small band ROI."""
        self.clear_manual_band_labels()
        if not lane_rects or band_roi is None:
            return

        ordered_lanes = sorted(lane_rects, key=lambda rect: (rect.center().x(), rect.x()))
        scene_top = self._scene.sceneRect().top()
        for idx, lane_rect in enumerate(ordered_lanes, start=1):
            label = self._scene.addText(f"#{idx}")
            label.setDefaultTextColor(QColor("#8C4F21"))
            font = label.font()
            font.setPointSize(8)
            font.setBold(True)
            label.setFont(font)
            x = lane_rect.x() + 2.0
            y = max(scene_top + 2.0, band_roi.y() - 14.0)
            label.setPos(x, y)
            label.setZValue(12)
            self._manual_band_labels.append(label)

    def set_auto_detect_overlays(self, detections: list[dict]) -> None:
        """
        Draw lane frames (blue) and band boxes (orange) from auto_detect_all()
        results. Clears any previous overlays first.
        """
        self.clear_auto_overlays()
        if self._band_roi_item is not None:
            self._scene.removeItem(self._band_roi_item)
            self._band_roi_item = None
        self._auto_lane_frames = [
            {
                "lane_index": lane["lane_index"],
                "lane_rect": QRectF(lane["lane_rect"]),
            }
            for lane in detections
        ]

        for lane in detections:
            lane_rect = lane["lane_rect"]

            pen = QPen(QColor("#8AAEC4"), 1.5, Qt.PenStyle.DashLine)
            item = self._scene.addRect(
                lane_rect,
                pen,
                QBrush(QColor(138, 174, 196, 20)),
            )
            item.setZValue(8)
            self._lane_items.append(item)

            label = self._scene.addText(f"L{lane['lane_index']}")
            label.setDefaultTextColor(QColor("#2A5E48"))
            font = label.font()
            font.setPointSize(8)
            font.setBold(True)
            label.setFont(font)
            label.setPos(lane_rect.x() + 3, lane_rect.y() + 3)
            label.setZValue(9)
            self._lane_items.append(label)

            for band in lane["bands"]:
                item = EditableBandRectItem(
                    QRectF(band["band_rect"]),
                    self._pixmap_item.boundingRect() if self._pixmap_item else QRectF(),
                    self._emit_auto_rois_changed,
                )
                row_index = band.get("row_index")
                try:
                    if row_index is not None:
                        item.setData(0, int(row_index))
                except (TypeError, ValueError):
                    pass
                item.set_editing_enabled(self._auto_edit_enabled)
                self._scene.addItem(item)
                self._auto_band_items.append(item)
        self._refresh_auto_band_labels()

    def set_interaction_mode(self, mode: str) -> None:
        self._interaction_mode = mode

    def is_rotation_mode(self) -> bool:
        return self._rotation_mode

    def get_rotation_angle(self) -> float:
        return float(self._rotation_angle_deg)

    def enter_rotation_mode(self) -> bool:
        if self._pixmap_item is None:
            return False
        if self._rotation_mode:
            return True
        self._rotation_mode = True
        self._rotation_dragging = False
        self._rotation_angle_deg = 0.0
        self._rotation_drag_start_mouse_angle_deg = None
        self._rotation_drag_start_crosshair_angle_deg = 0.0
        self._ensure_rotation_overlay()
        self.rotation_angle_changed.emit(self._rotation_angle_deg)
        self.rotation_mode_changed.emit(True)
        return True

    def cancel_rotation_mode(self) -> None:
        self._rotation_mode = False
        self._rotation_dragging = False
        self._rotation_drag_start_mouse_angle_deg = None
        self._rotation_drag_start_crosshair_angle_deg = 0.0
        self._remove_rotation_overlay()
        self.rotation_mode_changed.emit(False)

    def clear_rotation_overlay(self) -> None:
        self._remove_rotation_overlay()
        self._rotation_mode = False
        self._rotation_dragging = False
        self._rotation_angle_deg = 0.0
        self._rotation_drag_start_mouse_angle_deg = None
        self._rotation_drag_start_crosshair_angle_deg = 0.0
        self.rotation_mode_changed.emit(False)

    def set_auto_edit_mode(self, enabled: bool, tool: str | None = None) -> None:
        self._auto_edit_enabled = enabled
        if tool is not None:
            self._auto_edit_tool = tool
        for item in self._auto_band_items:
            item.set_editing_enabled(enabled)
        if not enabled:
            self._scene.clearSelection()

    def set_auto_edit_tool(self, tool: str) -> None:
        self._auto_edit_tool = tool

    def get_auto_detections(self) -> list[dict]:
        detections = [
            {
                "lane_index": lane["lane_index"],
                "lane_rect": QRectF(lane["lane_rect"]),
                "bands": [],
            }
            for lane in self._auto_lane_frames
        ]
        if not detections:
            return []

        for item in self._auto_band_items:
            rect = item.scene_rect()
            lane_index = self._lane_index_for_rect(rect)
            if lane_index is None:
                continue
            detections[lane_index - 1]["bands"].append({"band_rect": rect})

        for lane in detections:
            lane["bands"].sort(key=lambda band: (band["band_rect"].y(), band["band_rect"].x()))
            for band_index, band in enumerate(lane["bands"], start=1):
                band["band_index"] = band_index
        return detections

    def _emit_auto_rois_changed(self) -> None:
        self.auto_rois_changed.emit(self.get_auto_detections())

    def _refresh_auto_band_labels(self) -> None:
        for label in self._auto_band_labels:
            self._scene.removeItem(label)
        self._auto_band_labels.clear()

        if not self._auto_band_items:
            return

        ordered_items = self._ordered_auto_band_items_row_major()
        scene_top = self._scene.sceneRect().top()
        for index, item in enumerate(ordered_items, start=1):
            rect = item.scene_rect()
            label = self._scene.addText(f"#{index}")
            label.setDefaultTextColor(QColor("#8C4F21"))
            font = label.font()
            font.setPointSize(8)
            font.setBold(True)
            label.setFont(font)
            label_y = max(scene_top + 2.0, rect.y() - 14.0)
            label.setPos(rect.x() + 2.0, label_y)
            label.setZValue(12)
            self._auto_band_labels.append(label)

    def _ordered_auto_band_items_row_major(self) -> list[EditableBandRectItem]:
        if not self._auto_band_items:
            return []

        records: list[tuple[EditableBandRectItem, int | None, float, float, float]] = []
        for item in self._auto_band_items:
            rect = item.scene_rect()
            row_raw = item.data(0)
            row_index: int | None = None
            try:
                if row_raw is not None:
                    row_index = int(row_raw)
            except (TypeError, ValueError):
                row_index = None
            records.append((item, row_index, rect.center().y(), rect.center().x(), rect.height()))

        # Prefer explicit row index when available (top row first, then lower rows).
        if records and all(row_index is not None for _, row_index, _, _, _ in records):
            ordered = sorted(records, key=lambda rec: (int(rec[1]), rec[3], rec[2]))
            return [item for item, _, _, _, _ in ordered]

        # Fallback: geometry-based row clustering (top->bottom rows, left->right within row).
        heights = sorted(max(1.0, rec[4]) for rec in records)
        median_height = heights[len(heights) // 2] if heights else 10.0
        row_tolerance = max(4.0, median_height * 0.55)

        by_y = sorted(records, key=lambda rec: (rec[2], rec[3]))
        clusters: list[dict] = []
        for rec in by_y:
            item, _, cy, cx, _ = rec
            if not clusters:
                clusters.append({"center_y": cy, "items": [(item, cy, cx)]})
                continue

            current = clusters[-1]
            if abs(cy - float(current["center_y"])) <= row_tolerance:
                members = current["items"]
                members.append((item, cy, cx))
                current["center_y"] = sum(member[1] for member in members) / len(members)
            else:
                clusters.append({"center_y": cy, "items": [(item, cy, cx)]})

        ordered_items: list[EditableBandRectItem] = []
        for cluster in clusters:
            members = sorted(cluster["items"], key=lambda member: (member[2], member[1]))
            ordered_items.extend(member[0] for member in members)
        return ordered_items

    def _lane_index_for_rect(self, rect: QRectF) -> int | None:
        if not self._auto_lane_frames:
            return None

        center_x = rect.center().x()
        best_lane_index = self._auto_lane_frames[0]["lane_index"]
        best_distance = None
        for lane in self._auto_lane_frames:
            lane_rect = lane["lane_rect"]
            if lane_rect.left() <= center_x <= lane_rect.right():
                return lane["lane_index"]
            lane_center = lane_rect.center().x()
            distance = abs(center_x - lane_center)
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_lane_index = lane["lane_index"]
        return best_lane_index

    def _default_auto_band_rect(self, center: QPointF) -> QRectF:
        lane_index = self._lane_index_for_rect(QRectF(center.x(), center.y(), 1, 1))
        lane_rect = None
        for lane in self._auto_lane_frames:
            if lane["lane_index"] == lane_index:
                lane_rect = lane["lane_rect"]
                break

        default_height = 14.0
        if self._auto_band_items:
            heights = sorted(item.scene_rect().height() for item in self._auto_band_items)
            default_height = heights[len(heights) // 2]

        if lane_rect is None:
            width = max(18.0, default_height * 2.0)
            return QRectF(center.x() - (width / 2.0), center.y() - (default_height / 2.0), width, default_height)

        top = center.y() - (default_height / 2.0)
        return QRectF(lane_rect.x(), top, lane_rect.width(), default_height)

    def _ensure_rotation_overlay(self) -> None:
        if self._pixmap_item is None:
            return

        self._remove_rotation_overlay()
        bounds = self._pixmap_item.boundingRect()
        self._rotation_center = bounds.center()

        pen = QPen(QColor("#D84A4A"), 1.0, Qt.PenStyle.SolidLine)
        pen.setCosmetic(True)

        self._rotation_h_line = self._scene.addLine(
            bounds.left(),
            self._rotation_center.y(),
            bounds.right(),
            self._rotation_center.y(),
            pen,
        )
        self._rotation_h_line.setZValue(200)
        self._rotation_h_line.setTransformOriginPoint(self._rotation_center)

        self._rotation_v_line = self._scene.addLine(
            self._rotation_center.x(),
            bounds.top(),
            self._rotation_center.x(),
            bounds.bottom(),
            pen,
        )
        self._rotation_v_line.setZValue(200)
        self._rotation_v_line.setTransformOriginPoint(self._rotation_center)

        self._rotation_angle_label = self._scene.addSimpleText(self._angle_label_text())
        self._rotation_angle_label.setBrush(QBrush(QColor("#C73C3C")))
        self._rotation_angle_label.setPos(bounds.left() + 8.0, bounds.top() + 6.0)
        self._rotation_angle_label.setZValue(201)
        self._update_rotation_overlay()

    def _remove_rotation_overlay(self) -> None:
        for item in (self._rotation_h_line, self._rotation_v_line, self._rotation_angle_label):
            if item is not None:
                self._scene.removeItem(item)
        self._rotation_h_line = None
        self._rotation_v_line = None
        self._rotation_angle_label = None
        self._rotation_drag_start_mouse_angle_deg = None

    def _update_rotation_overlay(self) -> None:
        for line in (self._rotation_h_line, self._rotation_v_line):
            if line is not None:
                line.setRotation(self._rotation_angle_deg)
        if self._rotation_angle_label is not None:
            self._rotation_angle_label.setText(self._angle_label_text())

    def _mouse_angle_from_center(self, scene_pos: QPointF) -> float | None:
        dx = scene_pos.x() - self._rotation_center.x()
        dy = scene_pos.y() - self._rotation_center.y()
        if abs(dx) < 1e-6 and abs(dy) < 1e-6:
            return None
        return degrees(atan2(dy, dx))

    @staticmethod
    def _normalize_signed_angle(angle_deg: float) -> float:
        while angle_deg <= -180.0:
            angle_deg += 360.0
        while angle_deg > 180.0:
            angle_deg -= 360.0
        return angle_deg

    @staticmethod
    def _clamp_deskew_angle(angle_deg: float) -> float:
        return max(-_MAX_DESKEW_ANGLE_DEG, min(_MAX_DESKEW_ANGLE_DEG, angle_deg))

    def _begin_rotation_drag(self, scene_pos: QPointF) -> None:
        mouse_angle = self._mouse_angle_from_center(scene_pos)
        self._rotation_drag_start_mouse_angle_deg = mouse_angle
        self._rotation_drag_start_crosshair_angle_deg = self._rotation_angle_deg
        self._rotation_dragging = mouse_angle is not None

    def _update_rotation_drag(self, scene_pos: QPointF) -> None:
        current_mouse_angle = self._mouse_angle_from_center(scene_pos)
        if current_mouse_angle is None:
            return
        if self._rotation_drag_start_mouse_angle_deg is None:
            self._rotation_drag_start_mouse_angle_deg = current_mouse_angle
            self._rotation_drag_start_crosshair_angle_deg = self._rotation_angle_deg
            return

        delta = self._normalize_signed_angle(
            current_mouse_angle - self._rotation_drag_start_mouse_angle_deg
        )
        proposed = self._rotation_drag_start_crosshair_angle_deg + delta
        self._rotation_angle_deg = self._clamp_deskew_angle(self._normalize_signed_angle(proposed))
        self._update_rotation_overlay()
        self.rotation_angle_changed.emit(self._rotation_angle_deg)

    def _end_rotation_drag(self) -> None:
        self._rotation_dragging = False
        self._rotation_drag_start_mouse_angle_deg = None
        self._rotation_drag_start_crosshair_angle_deg = self._rotation_angle_deg

    def _angle_label_text(self) -> str:
        return f"Angle: {self._rotation_angle_deg:+.2f}°"

    def get_image_scale(self) -> float:
        """Return scale factor to convert scene coords → original image pixel coords.

        In this canvas the pixmap item is stored at full resolution in the scene and
        only the view transform (fitInView) is used for display scaling, so scene
        coordinates already equal image pixel coordinates and this returns 1.0.
        The method exists as an explicit contract so callers don't need to know the
        internal representation, and will remain correct if the pixmap item is ever
        scaled directly (e.g. via setScale or setTransform on the item itself).
        """
        if self._pixmap_item is None:
            return 1.0
        scene_w = self._pixmap_item.boundingRect().width()
        img_w = self._pixmap_item.pixmap().width()
        return img_w / scene_w if scene_w > 0 else 1.0

    def zoom_in(self) -> None:
        """Zoom in 20%, centered on viewport center. Max 800%."""
        if self.transform().m11() < 8.0:
            self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
            self.scale(1.2, 1.2)
            self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

    def zoom_out(self) -> None:
        """Zoom out 20%, centered on viewport center. Min 10%."""
        if self.transform().m11() > 0.1:
            self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
            self.scale(1 / 1.2, 1 / 1.2)
            self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

    def reset_zoom(self) -> None:
        if self._pixmap_item:
            self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)

    # ── Mouse events ───────────────────────────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._pixmap_item and event.button() in (
            Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton
        ):
            self.panel_interacted.emit()

        if event.button() == Qt.MouseButton.RightButton and self._pixmap_item:
            self._right_panning = True
            self._pan_last_pos = event.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return

        if self._rotation_mode and self._pixmap_item and event.button() == Qt.MouseButton.LeftButton:
            scene_pos = self._clamp_to_image(self.mapToScene(event.pos()))
            self._begin_rotation_drag(scene_pos)
            event.accept()
            return

        if self._fixed_roi_enabled and self._pixmap_item and event.button() == Qt.MouseButton.LeftButton:
            sp = self._clamp_to_image(self.mapToScene(event.pos()))
            current_fixed_size = self._current_fixed_roi_scene_size()
            if current_fixed_size is not None:
                self._fixed_roi_move_offset = QPointF(
                    current_fixed_size.width() / 2.0,
                    current_fixed_size.height() / 2.0,
                )
                self._set_fixed_roi_top_left(sp - self._fixed_roi_move_offset)
                self._moving_fixed_roi = True
                event.accept()
                return
            self._drawing = True
            self._drawing_band_roi = False
            self._draw_start = sp
            if self._band_roi_item is not None:
                self._scene.removeItem(self._band_roi_item)
                self._band_roi_item = None
            event.accept()
            return

        if self._interaction_mode == "auto" and self._auto_edit_enabled and self._pixmap_item:
            item = self.itemAt(event.pos())
            if self._auto_edit_tool == "delete" and isinstance(item, EditableBandRectItem):
                self._scene.removeItem(item)
                if item in self._auto_band_items:
                    self._auto_band_items.remove(item)
                self._emit_auto_rois_changed()
                event.accept()
                return
            if self._auto_edit_tool == "add" and event.button() == Qt.MouseButton.LeftButton:
                scene_pos = self._clamp_to_image(self.mapToScene(event.pos()))
                rect = self._default_auto_band_rect(scene_pos)
                item = EditableBandRectItem(
                    rect,
                    self._pixmap_item.boundingRect(),
                    self._emit_auto_rois_changed,
                )
                item.set_editing_enabled(True)
                self._scene.addItem(item)
                item.setSelected(True)
                self._auto_band_items.append(item)
                self._emit_auto_rois_changed()
                event.accept()
                return
            if self._auto_edit_tool == "move":
                super().mousePressEvent(event)
                return

        if event.button() == Qt.MouseButton.LeftButton and self._pixmap_item:
            if self.dragMode() == QGraphicsView.DragMode.NoDrag:
                sp = self.mapToScene(event.pos())
                if self._pixmap_item.boundingRect().contains(sp):
                    if self._wb_plot_roi_only:
                        if self._roi_item is not None:
                            self._scene.removeItem(self._roi_item)
                            self._roi_item = None
                        if self._band_roi_item is not None:
                            self._scene.removeItem(self._band_roi_item)
                            self._band_roi_item = None
                        self.clear_auto_overlays()
                        self._drawing = True
                        self._drawing_band_roi = False
                        self._draw_start = sp
                        event.accept()
                        return
                    # In manual mode: if a complete ROI set (lane + band) is already drawn,
                    # left-click on a non-band area clears it so the user can draw a new set
                    if (
                        self._interaction_mode != "auto"
                        and self._roi_item is not None
                        and self._band_roi_item is not None
                        and not self._band_roi_item.rect().contains(sp)
                    ):
                        self.clear_roi()
                        self.roi_cleared.emit()
                        event.accept()
                        return
                    self._drawing = True
                    if self._interaction_mode == "auto":
                        self._drawing_band_roi = False
                        if self._roi_item is not None:
                            self._scene.removeItem(self._roi_item)
                            self._roi_item = None
                    else:
                        # Lock in which ROI type we're drawing at start of operation
                        self._drawing_band_roi = (self._roi_item is not None and self._band_roi_item is None)
                    self._draw_start = sp
                    event.accept()
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._right_panning:
            delta = event.pos() - self._pan_last_pos
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            self._pan_last_pos = event.pos()
            event.accept()
            return

        if self._rotation_mode and self._rotation_dragging and self._pixmap_item:
            scene_pos = self._clamp_to_image(self.mapToScene(event.pos()))
            self._update_rotation_drag(scene_pos)
            event.accept()
            return

        if self._fixed_roi_enabled and self._moving_fixed_roi and self._pixmap_item:
            sp = self._clamp_to_image(self.mapToScene(event.pos()))
            self._set_fixed_roi_top_left(sp - self._fixed_roi_move_offset)
            event.accept()
            return

        if self._drawing and self._pixmap_item:
            sp = self._clamp_to_image(self.mapToScene(event.pos()))
            rect = QRectF(self._draw_start, sp).normalized()

            # Use locked decision from mousePressEvent
            if self._interaction_mode != "auto" and self._drawing_band_roi:
                # Drawing band ROI
                if self._band_roi_item is None:
                    pen = QPen(QColor("#8AAEC4"), 2, Qt.PenStyle.SolidLine)  # Blue band ROI
                    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                    brush = QBrush(QColor(138, 174, 196, 30))  # 12% opacity
                    self._band_roi_item = self._scene.addRect(rect, pen, brush)
                    self._band_roi_item.setZValue(11)
                else:
                    self._band_roi_item.setRect(rect)
            else:
                # Drawing lane ROI
                if self._roi_item is None:
                    pen = QPen(QColor("#8AB4A0"), 2, Qt.PenStyle.SolidLine)  # Sage green lane ROI
                    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                    fill = QBrush(QColor(138, 180, 160, 38))  # 15% opacity
                    self._roi_item = self._scene.addRect(rect, pen, fill)
                    self._roi_item.setZValue(10)
                else:
                    self._roi_item.setRect(rect)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton and self._right_panning:
            self._right_panning = False
            self.setCursor(Qt.CursorShape.CrossCursor)
            event.accept()
            return

        if self._rotation_mode and event.button() == Qt.MouseButton.LeftButton and self._rotation_dragging:
            self._end_rotation_drag()
            event.accept()
            return

        if self._fixed_roi_enabled and self._moving_fixed_roi and event.button() == Qt.MouseButton.LeftButton:
            self._moving_fixed_roi = False
            if self._roi_item is not None:
                self.roi_changed.emit(self._roi_item.rect())
            if self._fixed_band_roi_relative is not None and self._band_roi_item is not None:
                self.band_roi_changed.emit(self._band_roi_item.rect())
            event.accept()
            return

        if self._drawing and event.button() == Qt.MouseButton.LeftButton:
            self._drawing = False

            # Use locked decision from mousePressEvent
            if self._interaction_mode != "auto" and self._drawing_band_roi:
                # Band ROI was drawn
                rect = self._band_roi_item.rect()
                if rect.width() > 5 and rect.height() > 5:
                    self.band_roi_changed.emit(rect)
                else:
                    # Too small — discard
                    self._scene.removeItem(self._band_roi_item)
                    self._band_roi_item = None
            else:
                # Main ROI was drawn
                if self._roi_item:
                    rect = self._roi_item.rect()
                    if rect.width() > 5 and rect.height() > 5:
                        if self._fixed_roi_enabled and self._fixed_roi_size is None:
                            self._fixed_roi_size = QSizeF(rect.width(), rect.height())
                            self._fixed_roi_viewport_size = None
                        self.roi_changed.emit(rect)
                    else:
                        # Too small — discard
                        self._scene.removeItem(self._roi_item)
                        self._roi_item = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self._pixmap_item is None:
            super().wheelEvent(event)
            return

        self.panel_interacted.emit()

        # Trackpad two-finger scroll usually provides pixelDelta -> pan.
        pixel_delta = event.pixelDelta()
        if not pixel_delta.isNull():
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - pixel_delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - pixel_delta.y())
            event.accept()
            return

        angle_y = event.angleDelta().y()
        if angle_y == 0:
            super().wheelEvent(event)
            return

        current_scale = self.transform().m11()
        zoom_factor = 1.15 if angle_y > 0 else 1.0 / 1.15
        next_scale = current_scale * zoom_factor
        if not (0.1 <= next_scale <= 8.0):
            event.accept()
            return
        self.scale(zoom_factor, zoom_factor)
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self._auto_edit_enabled and event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            removed = False
            for item in list(self._auto_band_items):
                if item.isSelected():
                    self._scene.removeItem(item)
                    self._auto_band_items.remove(item)
                    removed = True
            if removed:
                self._emit_auto_rois_changed()
                event.accept()
                return
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.setCursor(Qt.CursorShape.CrossCursor)
        super().keyReleaseEvent(event)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _clamp_to_image(self, pt: QPointF) -> QPointF:
        b = self._pixmap_item.boundingRect()
        return QPointF(
            max(b.left(), min(b.right(), pt.x())),
            max(b.top(), min(b.bottom(), pt.y())),
        )

    def _scene_size_for_viewport_size(self, size: QSizeF) -> QSizeF:
        transform = self.transform()
        sx = abs(transform.m11()) or 1.0
        sy = abs(transform.m22()) or 1.0
        return QSizeF(size.width() / sx, size.height() / sy)

    def _current_fixed_roi_scene_size(self) -> QSizeF | None:
        if self._fixed_roi_viewport_size is not None:
            self._fixed_roi_size = self._scene_size_for_viewport_size(self._fixed_roi_viewport_size)
        return QSizeF(self._fixed_roi_size) if self._fixed_roi_size is not None else None

    def _set_fixed_roi_top_left(self, top_left: QPointF) -> None:
        fixed_size = self._current_fixed_roi_scene_size()
        if self._pixmap_item is None or fixed_size is None:
            return
        bounds = self._pixmap_item.boundingRect()
        width = min(fixed_size.width(), bounds.width())
        height = min(fixed_size.height(), bounds.height())
        x = max(bounds.left(), min(bounds.right() - width, top_left.x()))
        y = max(bounds.top(), min(bounds.bottom() - height, top_left.y()))
        rect = QRectF(x, y, width, height)
        if self._roi_item is None:
            pen = QPen(QColor("#8AB4A0"), 2, Qt.PenStyle.SolidLine)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            fill = QBrush(QColor(138, 180, 160, 38))
            self._roi_item = self._scene.addRect(rect, pen, fill)
            self._roi_item.setZValue(10)
        else:
            self._roi_item.setRect(rect)
        self._set_fixed_band_roi_for_lane(rect)

    def _set_fixed_band_roi_for_lane(self, lane_rect: QRectF) -> None:
        rel = self._fixed_band_roi_relative
        if rel is None:
            if self._band_roi_item is not None:
                self._scene.removeItem(self._band_roi_item)
                self._band_roi_item = None
            return
        band_rect = QRectF(
            lane_rect.x() + rel.x() * lane_rect.width(),
            lane_rect.y() + rel.y() * lane_rect.height(),
            rel.width() * lane_rect.width(),
            rel.height() * lane_rect.height(),
        )
        if self._band_roi_item is None:
            pen = QPen(QColor("#E8A87C"), 2, Qt.PenStyle.SolidLine)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            brush = QBrush(QColor(232, 168, 124, 50))
            self._band_roi_item = self._scene.addRect(band_rect, pen, brush)
            self._band_roi_item.setZValue(11)
        else:
            self._band_roi_item.setRect(band_rect)
