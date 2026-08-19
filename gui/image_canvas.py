"""Zoomable image canvas with rectangular ROI drawing and lane overlays."""
from __future__ import annotations

from pathlib import Path
from math import atan2, degrees

import numpy as np
from PIL import Image
from PySide6.QtCore import (
    Qt, QCoreApplication, QEvent, QObject, QThread, Signal, Slot,
    QRectF, QPointF, QSizeF, QPoint, QTimer,
)
from PySide6.QtGui import (
    QPixmap, QPen, QBrush, QColor, QPainter, QWheelEvent, QImage,
    QMouseEvent, QKeyEvent, QPainterPath, QTransform,
)
from PySide6.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QGraphicsRectItem, QGraphicsItem, QGraphicsLineItem, QGraphicsSimpleTextItem,
    QGraphicsPathItem,
)

from core.image_transform import (
    GeometryTransform,
    ImageTransformParams,
    apply_geometry_to_display,
    auto_scale_range_16,
    default_inverted_for_pil_image,
    image_array_to_raw_luminance,
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
_LANE_ROI_CANCEL_TOLERANCE_PX = 4
_MAX_PREVIEW_EDGE_PX = 4096


def _readonly(array: np.ndarray) -> np.ndarray:
    """Return *array* as a read-only ndarray without copying when possible."""
    result = np.ascontiguousarray(np.asarray(array))
    result.setflags(write=False)
    return result


def _uncompressed_uint16_tiff_memmap(
    path: Path,
    image: Image.Image,
) -> np.ndarray | None:
    """Map a simple, contiguous uncompressed 16-bit grayscale TIFF.

    Pillow itself maps a subset of these files internally, but exporting that
    image through ``np.asarray`` materializes a bytes object.  Mapping the
    contiguous strip payload directly keeps the quantitative buffer zero-copy.
    TIFF layouts that are tiled, oriented, planar, compressed, or non-contiguous
    deliberately fall back to Pillow's fully-tested decoder.
    """
    if path.suffix.lower() not in {".tif", ".tiff"}:
        return None
    tags = getattr(image, "tag_v2", None)
    if tags is None or image.mode not in {"I;16", "I;16L", "I;16B"}:
        return None

    try:
        width, height = image.size
        bits = tags.get(258, (16,))
        if isinstance(bits, int):
            bits = (bits,)
        compression = int(tags.get(259, 1))
        samples_per_pixel = int(tags.get(277, 1))
        planar_configuration = int(tags.get(284, 1))
        orientation = int(tags.get(274, 1))
        sample_format = tags.get(339, (1,))
        if isinstance(sample_format, int):
            sample_format = (sample_format,)
        if (
            tuple(int(value) for value in bits) != (16,)
            or compression != 1
            or samples_per_pixel != 1
            or planar_configuration != 1
            or orientation != 1
            or tuple(int(value) for value in sample_format) != (1,)
            or tags.get(324) is not None  # tiled TIFF
        ):
            return None

        offsets = tags.get(273)
        byte_counts = tags.get(279)
        if isinstance(offsets, int):
            offsets = (offsets,)
        if isinstance(byte_counts, int):
            byte_counts = (byte_counts,)
        if not offsets or not byte_counts or len(offsets) != len(byte_counts):
            return None

        rows_per_strip = max(1, int(tags.get(278, height)))
        row_bytes = width * 2
        expected_offset = int(offsets[0])
        rows_remaining = height
        for offset, byte_count in zip(offsets, byte_counts):
            strip_rows = min(rows_per_strip, rows_remaining)
            expected_bytes = strip_rows * row_bytes
            if int(offset) != expected_offset or int(byte_count) < expected_bytes:
                return None
            expected_offset += expected_bytes
            rows_remaining -= strip_rows
        if rows_remaining != 0:
            return None

        endian = getattr(tags, "_endian", "<")
        dtype = np.dtype(">u2" if endian == ">" else "<u2")
        mapped = np.memmap(
            path,
            dtype=dtype,
            mode="r",
            offset=int(offsets[0]),
            shape=(height, width),
            order="C",
        )
        return _readonly(mapped)
    except (OSError, TypeError, ValueError, OverflowError):
        return None


def _preview_stride(width: int, height: int) -> int:
    return max(1, int(np.ceil(max(width, height) / _MAX_PREVIEW_EDGE_PX)))


def _render_default_preview(
    quantitative_pixels: np.ndarray,
    stride: int,
    params: ImageTransformParams,
) -> np.ndarray:
    sampled = quantitative_pixels[::stride, ::stride]
    display_source = image_array_to_uint16_luminance(sampled)
    return _readonly(transform_pixels_16_to_8(display_source, params))


class ImageLoadWorker(QObject):
    """Decode and prepare an image without touching GUI-owned Qt objects."""

    preview_ready = Signal(int, str, object)
    finished = Signal(int, str, object)
    error = Signal(int, str, str)
    cancelled = Signal(int)

    def __init__(self, request_id: int, path: str) -> None:
        super().__init__()
        self.request_id = request_id
        self.path = path
        self.failure_message: str | None = None

    def _interrupted(self) -> bool:
        return QThread.currentThread().isInterruptionRequested()

    @Slot()
    def run(self) -> None:
        path = Path(self.path)
        try:
            with Image.open(path) as image:
                width, height = image.size
                if width <= 0 or height <= 0:
                    raise ValueError(f"Image has invalid dimensions: {image.size}")
                default_params = ImageTransformParams(
                    inverted=default_inverted_for_pil_image(image, fallback=True)
                )
                stride = _preview_stride(width, height)
                quantitative = _uncompressed_uint16_tiff_memmap(path, image)

                if quantitative is not None:
                    preview = _render_default_preview(
                        quantitative, stride, default_params
                    )
                    self.preview_ready.emit(
                        self.request_id,
                        self.path,
                        {
                            "pixels": preview,
                            "raw_shape": (height, width),
                            "stride": stride,
                            "default_params": default_params,
                            "memory_mapped": True,
                        },
                    )
                    if self._interrupted():
                        self.cancelled.emit(self.request_id)
                        return
                    self.finished.emit(
                        self.request_id,
                        self.path,
                        {
                            "quantitative_pixels": quantitative,
                            "raw_shape": (height, width),
                            "stride": stride,
                            "default_params": default_params,
                            "memory_mapped": True,
                        },
                    )
                    return

                # Pillow is the compatibility decoder for compressed, tiled,
                # multichannel, or otherwise non-trivial TIFF layouts.  The
                # decoded source is retained only until native-depth scalar
                # quantitative pixels have been prepared.
                decoded = np.asarray(image)
                if decoded.ndim not in {2, 3} or decoded.size == 0:
                    raise ValueError(f"Unexpected image shape: {decoded.shape}")

                # Prepare and emit the sampled preview before the potentially
                # expensive full-resolution RGB -> luminance conversion.
                sampled_quantitative = image_array_to_raw_luminance(
                    decoded[::stride, ::stride]
                )
                preview = _render_default_preview(
                    sampled_quantitative, 1, default_params
                )
                self.preview_ready.emit(
                    self.request_id,
                    self.path,
                    {
                        "pixels": preview,
                        "raw_shape": (height, width),
                        "stride": stride,
                        "default_params": default_params,
                        "memory_mapped": False,
                    },
                )
                if self._interrupted():
                    self.cancelled.emit(self.request_id)
                    return

                quantitative = _readonly(
                    image_array_to_raw_luminance(decoded)
                )
                self.finished.emit(
                    self.request_id,
                    self.path,
                    {
                        "quantitative_pixels": quantitative,
                        "raw_shape": (height, width),
                        "stride": stride,
                        "default_params": default_params,
                        "memory_mapped": False,
                    },
                )
        except Exception as exc:
            self.failure_message = str(exc)
            self.error.emit(self.request_id, self.path, self.failure_message)


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
    roi_cleared = Signal()               # emitted when a manual ROI selection is cleared in-canvas
    image_preview_ready = Signal(str)
    image_load_finished = Signal(str)
    image_load_failed = Signal(str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        self._pixmap_item: QGraphicsPixmapItem | None = None
        self._pixmap_original_size: QSizeF | None = None  # Original image size before any scaling
        self._raw_image_pixels: np.ndarray | None = None
        self._raw_quantification_pixels: np.ndarray | None = None
        self._display_source_pixels: np.ndarray | None = None
        self._raw_image_shape: tuple[int, int] | None = None
        self._display_preview_stride = 1
        self._image_memory_mapped = False
        self._image_load_request_id = 0
        self._image_load_jobs: dict[int, tuple[QThread, ImageLoadWorker]] = {}
        self._pending_image_load_results: dict[int, tuple[str, str, str | None]] = {}
        self._image_loading = False
        self._loading_item: QGraphicsSimpleTextItem | None = None
        self._geometry_transform = GeometryTransform()
        self._image_default_transform_params = self._default_image_transform_params()
        self._image_transform_params = self._image_default_transform_params
        self._roi_item: QGraphicsRectItem | None = None
        self._band_roi_item: QGraphicsRectItem | None = None
        self._lane_items: list[QGraphicsItem] = []
        self._auto_band_items: list[EditableBandRectItem] = []
        self._auto_band_labels: list[QGraphicsItem] = []
        self._manual_band_labels: list[QGraphicsItem] = []
        self._auto_lane_frames: list[dict] = []
        self._final_crop_item: QGraphicsRectItem | None = None
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
        self._tutorial_hint_item: QGraphicsRectItem | None = None
        self._tutorial_cursor_item: QGraphicsPathItem | None = None
        self._tutorial_hint_rect: QRectF | None = None
        self._tutorial_cursor_progress = 0.0
        self._tutorial_cursor_timer = QTimer(self)
        self._tutorial_cursor_timer.setInterval(32)
        self._tutorial_cursor_timer.timeout.connect(
            self._advance_tutorial_roi_cursor
        )

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

    def _reset_image_state(self) -> None:
        self.clear_tutorial_roi_hint()
        self._scene.clear()
        self._pixmap_item = None
        self._pixmap_original_size = None
        self._raw_image_pixels = None
        self._raw_quantification_pixels = None
        self._display_source_pixels = None
        self._raw_image_shape = None
        self._display_preview_stride = 1
        self._image_memory_mapped = False
        self._loading_item = None
        self._geometry_transform = GeometryTransform()
        self._image_default_transform_params = self._default_image_transform_params()
        self._image_transform_params = self._image_default_transform_params
        self._roi_item = None
        self._band_roi_item = None
        self._lane_items.clear()
        self._auto_band_items.clear()
        self._auto_band_labels.clear()
        self._manual_band_labels.clear()
        self._auto_lane_frames.clear()
        self._final_crop_item = None
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

    def _show_loading_state(self, message: str) -> None:
        if self._loading_item is not None and self._loading_item.scene() is self._scene:
            self._loading_item.setText(message)
            return
        if self._pixmap_item is None:
            width = max(320, self.viewport().width())
            height = max(180, self.viewport().height())
            self._scene.setSceneRect(0, 0, width, height)
            position = QPointF(width / 2.0, height / 2.0)
        else:
            position = self._pixmap_item.sceneBoundingRect().center()
        item = self._scene.addSimpleText(message)
        item.setBrush(QBrush(QColor("#385161")))
        font = item.font()
        font.setBold(True)
        font.setPointSize(10)
        item.setFont(font)
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        bounds = item.boundingRect()
        item.setPos(position.x() - bounds.width() / 2.0, position.y() - bounds.height() / 2.0)
        item.setZValue(10000)
        self._loading_item = item

    def load_image(self, path: str | Path) -> int:
        """Start two-stage image loading and return the request identifier.

        TIFF decode, native-depth quantitative data, and the sampled preview
        are prepared by :class:`ImageLoadWorker`.  This method returns before
        decoding finishes; only QPixmap creation and scene updates run here on
        the GUI thread.
        """
        normalized_path = str(Path(path).expanduser().resolve())
        if not Path(normalized_path).is_file():
            raise FileNotFoundError(normalized_path)

        self.cancel_image_load(wait=False)
        self._image_load_request_id += 1
        request_id = self._image_load_request_id
        self._reset_image_state()
        self._image_loading = True
        self._show_loading_state("Loading image…")

        thread = QThread(self)
        thread.setProperty("image_load_request_id", request_id)
        worker = ImageLoadWorker(request_id, normalized_path)
        worker.moveToThread(thread)
        self._image_load_jobs[request_id] = (thread, worker)

        thread.started.connect(worker.run)
        worker.preview_ready.connect(self._on_image_preview_ready)
        worker.finished.connect(self._on_image_load_finished)
        worker.error.connect(self._on_image_load_error)
        worker.cancelled.connect(self._on_image_load_cancelled)
        for terminal_signal in (worker.finished, worker.error, worker.cancelled):
            terminal_signal.connect(worker.deleteLater)
            terminal_signal.connect(thread.quit, Qt.ConnectionType.DirectConnection)
        thread.finished.connect(thread.deleteLater)
        thread.destroyed.connect(self._on_image_load_thread_destroyed)
        thread.start()
        return request_id

    def load_image_blocking(self, path: str | Path) -> None:
        """Synchronous compatibility helper for scripts and deterministic tests."""
        normalized_path = str(Path(path).expanduser().resolve())
        if not Path(normalized_path).is_file():
            raise FileNotFoundError(normalized_path)
        self.cancel_image_load(wait=True)
        self._image_load_request_id += 1
        request_id = self._image_load_request_id
        self._reset_image_state()
        self._image_loading = True
        self._show_loading_state("Loading image…")
        worker = ImageLoadWorker(request_id, normalized_path)
        worker.preview_ready.connect(self._on_image_preview_ready)
        worker.finished.connect(self._on_image_load_finished)
        worker.error.connect(self._on_image_load_error)
        worker.run()
        if worker.failure_message is not None:
            raise ValueError(worker.failure_message)

    @Slot(int, str, object)
    def _on_image_preview_ready(self, request_id: int, path: str, payload: object) -> None:
        if request_id != self._image_load_request_id or not isinstance(payload, dict):
            return
        preview = np.asarray(payload["pixels"], dtype=np.uint8)
        height, width = payload["raw_shape"]
        self._raw_image_shape = (int(height), int(width))
        self._display_preview_stride = int(payload["stride"])
        self._image_default_transform_params = payload["default_params"]
        self._image_transform_params = self._image_default_transform_params
        self._image_memory_mapped = bool(payload["memory_mapped"])

        pixmap = self._pixmap_from_display_pixels(preview)
        if pixmap.isNull():
            self._on_image_load_error(request_id, path, f"Could not load image: {path}")
            return
        self._scene.clear()
        self._loading_item = None
        self._pixmap_item = QGraphicsPixmapItem(pixmap)
        self._pixmap_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._scene.addItem(self._pixmap_item)
        self._update_pixmap_scene_geometry()
        self.resetTransform()
        self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
        self._show_loading_state("Loading full-resolution data…")
        self.image_preview_ready.emit(path)

    @Slot(int, str, object)
    def _on_image_load_finished(self, request_id: int, path: str, payload: object) -> None:
        if request_id != self._image_load_request_id or not isinstance(payload, dict):
            return
        quantitative = _readonly(payload["quantitative_pixels"])
        self._raw_quantification_pixels = quantitative
        # Presentation conversion is now performed only on the sampled view;
        # retaining a separate full-resolution uint16 display buffer is wasteful.
        self._display_source_pixels = quantitative
        self._raw_image_pixels = quantitative
        height, width = payload["raw_shape"]
        self._raw_image_shape = (int(height), int(width))
        self._display_preview_stride = int(payload["stride"])
        self._image_memory_mapped = bool(payload["memory_mapped"])
        if self._loading_item is not None and self._loading_item.scene() is self._scene:
            self._scene.removeItem(self._loading_item)
        self._loading_item = None
        if request_id in self._image_load_jobs:
            self._pending_image_load_results[request_id] = ("finished", path, None)
        else:
            self._image_loading = False
            self.image_load_finished.emit(path)

    @Slot(int, str, str)
    def _on_image_load_error(self, request_id: int, path: str, message: str) -> None:
        if request_id != self._image_load_request_id:
            return
        self._reset_image_state()
        self._show_loading_state("Image load failed")
        if request_id in self._image_load_jobs:
            self._pending_image_load_results[request_id] = ("failed", path, message)
        else:
            self._image_loading = False
            self.image_load_failed.emit(path, message)

    @Slot(int)
    def _on_image_load_cancelled(self, request_id: int) -> None:
        if request_id == self._image_load_request_id:
            self._image_loading = False

    @Slot(QObject)
    def _on_image_load_thread_destroyed(self, thread_object: QObject | None = None) -> None:
        if thread_object is None:
            return
        request_id = int(thread_object.property("image_load_request_id") or -1)
        self._image_load_jobs.pop(request_id, None)
        result = self._pending_image_load_results.pop(request_id, None)
        if request_id != self._image_load_request_id or result is None:
            return
        self._image_loading = False
        status, path, message = result
        if status == "finished":
            self.image_load_finished.emit(path)
        else:
            self.image_load_failed.emit(path, message or "Unknown image load error")

    def cancel_image_load(self, *, wait: bool = False) -> None:
        for thread, _worker in tuple(self._image_load_jobs.values()):
            if thread.isRunning():
                thread.requestInterruption()
        if wait:
            for thread, _worker in tuple(self._image_load_jobs.values()):
                if thread.isRunning():
                    thread.wait()
                # ``wait`` does not run the GUI event queue, so explicitly
                # finish deferred QThread deletion before a canvas/window can
                # be destroyed. This prevents stale QObject ownership during
                # fast close/reload paths.
                thread.deleteLater()
                QCoreApplication.sendPostedEvents(
                    thread, QEvent.Type.DeferredDelete
                )

    def clear_image(self) -> None:
        self.cancel_image_load(wait=False)
        self._image_load_request_id += 1
        self._image_loading = False
        self._reset_image_state()

    def is_loading(self) -> bool:
        return self._image_loading

    def is_memory_mapped(self) -> bool:
        return self._image_memory_mapped

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
        self._update_pixmap_scene_geometry()

    def has_modified_image_transform(self) -> bool:
        return self._image_transform_params != self._image_default_transform_params

    def has_quantitative_image_transform(self) -> bool:
        """Compatibility API: display transforms are never quantitative."""
        return False

    def current_display_pixels(self) -> np.ndarray | None:
        if self._display_source_pixels is None:
            return None
        preview_source = self._display_source_pixels[
            ::self._display_preview_stride,
            ::self._display_preview_stride,
        ]
        preview_source = image_array_to_uint16_luminance(preview_source)
        toned = transform_pixels_16_to_8(
            preview_source,
            self._image_transform_params,
        )
        return apply_geometry_to_display(toned, self._geometry_transform)

    def current_analysis_pixels(self) -> np.ndarray | None:
        """Return a safe copy of native-depth raw quantification pixels."""
        if self._raw_quantification_pixels is None:
            return None
        return self._raw_quantification_pixels.copy()

    def get_analysis_transform_params(self) -> ImageTransformParams:
        """Deprecated compatibility API; quantification has no tone transform."""
        return ImageTransformParams(inverted=False)

    def get_geometry_transform(self) -> GeometryTransform:
        return self._geometry_transform

    def set_geometry_transform(self, transform: GeometryTransform) -> None:
        """Apply non-destructive presentation geometry to the preview only."""
        self._geometry_transform = transform.sanitized()
        if self._pixmap_item is None or self._display_source_pixels is None:
            return
        pixmap = self._make_display_pixmap()
        self._pixmap_item.setPixmap(pixmap)
        self._update_pixmap_scene_geometry()

    def raw_image_size(self) -> QSizeF | None:
        if self._raw_image_shape is None:
            return None
        height, width = self._raw_image_shape
        return QSizeF(width, height)

    def map_canvas_points_to_raw(self, points: np.ndarray) -> np.ndarray:
        size = self.raw_image_size()
        if size is None:
            raise ValueError("No raw image is loaded.")
        return self._geometry_transform.map_points_to_raw(
            points, int(size.width()), int(size.height())
        )

    def map_raw_points_to_canvas(self, points: np.ndarray) -> np.ndarray:
        size = self.raw_image_size()
        if size is None:
            raise ValueError("No raw image is loaded.")
        return self._geometry_transform.map_points_to_canvas(
            points, int(size.width()), int(size.height())
        )

    def map_canvas_roi_to_raw(self, roi) -> dict:
        """Inverse-map a Canvas rectangle/polygon to a raw-image polygon."""
        metadata: dict = {}
        if isinstance(roi, dict):
            metadata = {
                key: value
                for key, value in roi.items()
                if key not in {"x", "y", "width", "height", "w", "h", "points"}
            }
            if "points" in roi:
                canvas_points = np.asarray(
                    [
                        (point["x"], point["y"]) if isinstance(point, dict) else point
                        for point in roi["points"]
                    ],
                    dtype=np.float64,
                )
            else:
                x = float(roi.get("x", 0.0))
                y = float(roi.get("y", 0.0))
                width = float(roi.get("width", roi.get("w", 1.0)))
                height = float(roi.get("height", roi.get("h", 1.0)))
                canvas_points = np.array(
                    ((x, y), (x + width, y), (x + width, y + height), (x, y + height)),
                    dtype=np.float64,
                )
        else:
            x, y = float(roi.x()), float(roi.y())
            width, height = float(roi.width()), float(roi.height())
            canvas_points = np.array(
                ((x, y), (x + width, y), (x + width, y + height), (x, y + height)),
                dtype=np.float64,
            )
        raw_points = self.map_canvas_points_to_raw(canvas_points)
        return {
            **metadata,
            "points": [
                {"x": float(point[0]), "y": float(point[1])}
                for point in raw_points
            ],
        }

    def map_canvas_rois_to_raw(self, rois: list) -> list[dict]:
        return [self.map_canvas_roi_to_raw(roi) for roi in rois]

    def reset_image_transform(self) -> ImageTransformParams:
        params = self._image_default_transform_params
        self.set_image_transform_params(params)
        return params

    def auto_scale_image_transform(self) -> ImageTransformParams:
        if self._display_source_pixels is None:
            params = self._image_default_transform_params
        else:
            source = self._display_source_pixels
            if source.dtype == np.uint8:
                low, high = auto_scale_range_16(source)
                low *= 257
                high *= 257
            else:
                low, high = auto_scale_range_16(
                    image_array_to_uint16_luminance(source)
                )
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
        return self._pixmap_from_display_pixels(display)

    @staticmethod
    def _pixmap_from_display_pixels(display: np.ndarray) -> QPixmap:
        display = np.ascontiguousarray(np.asarray(display, dtype=np.uint8))
        height, width = display.shape
        qimage = QImage(
            display.data,
            width,
            height,
            display.strides[0],
            QImage.Format.Format_Grayscale8,
        )
        # QPixmap.fromImage() takes its own native pixel storage synchronously;
        # copying QImage first only duplicates the full preview buffer.
        return QPixmap.fromImage(qimage)

    def _update_pixmap_scene_geometry(self) -> None:
        """Keep scene coordinates at full raw/presentation resolution.

        Large previews are sampled for responsive display, but the pixmap item
        is scaled back to the exact full-resolution presentation bounds.  ROI
        coordinates therefore remain independent of preview resolution.
        """
        if self._pixmap_item is None:
            return
        raw_size = self.raw_image_size()
        pixmap = self._pixmap_item.pixmap()
        if raw_size is None or pixmap.isNull():
            self._pixmap_original_size = None
            return
        _matrix, output_size = self._geometry_transform.affine(
            int(raw_size.width()),
            int(raw_size.height()),
        )
        scene_width, scene_height = output_size
        scale_x = scene_width / max(1, pixmap.width())
        scale_y = scene_height / max(1, pixmap.height())
        self._pixmap_item.setTransform(QTransform.fromScale(scale_x, scale_y))
        self._pixmap_original_size = QSizeF(scene_width, scene_height)
        self._scene.setSceneRect(self._pixmap_item.sceneBoundingRect())

    def get_roi(self) -> QRectF | None:
        """Return current main lane ROI in image/scene coordinates, or None.

        Converts from scene coordinates to original image pixel coordinates.
        """
        if self._roi_item is None or self._pixmap_original_size is None:
            return None

        scene_rect = self._roi_item.rect()
        # The preview pixmap may be sampled, but its item transform keeps scene
        # coordinates at full presentation resolution. Return the ROI as-is.
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
        rect = self._pixmap_item.sceneBoundingRect()
        return QSizeF(rect.width(), rect.height())

    def show_tutorial_roi_hint(self, rect: QRectF) -> None:
        """Show a visual-only suggested ROI and a looping drag gesture."""
        self.clear_tutorial_roi_hint()
        if self._pixmap_item is None:
            return
        image_rect = self._pixmap_item.sceneBoundingRect()
        hint = QRectF(rect).intersected(image_rect)
        if hint.width() <= 4 or hint.height() <= 4:
            return

        hint_pen = QPen(QColor(222, 139, 0, 235), 3.0, Qt.PenStyle.DashLine)
        hint_pen.setDashPattern([6.0, 4.0])
        hint_pen.setCosmetic(True)
        self._tutorial_hint_item = self._scene.addRect(
            QRectF(hint.topLeft(), QSizeF(1.0, 1.0)),
            hint_pen,
            QBrush(QColor(255, 188, 45, 48)),
        )
        self._tutorial_hint_item.setZValue(1000)
        self._tutorial_hint_item.setAcceptedMouseButtons(
            Qt.MouseButton.NoButton
        )

        cursor_path = QPainterPath()
        cursor_path.moveTo(0.0, 0.0)
        cursor_path.lineTo(0.0, 25.0)
        cursor_path.lineTo(6.5, 18.5)
        cursor_path.lineTo(12.5, 31.0)
        cursor_path.lineTo(17.5, 28.5)
        cursor_path.lineTo(11.5, 16.5)
        cursor_path.lineTo(21.0, 16.5)
        cursor_path.closeSubpath()
        self._tutorial_cursor_item = self._scene.addPath(
            cursor_path,
            QPen(QColor("#8A5600"), 2.0),
            QBrush(QColor(255, 250, 235, 245)),
        )
        self._tutorial_cursor_item.setZValue(1002)
        self._tutorial_cursor_item.setFlag(
            QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations,
            True,
        )
        self._tutorial_cursor_item.setAcceptedMouseButtons(
            Qt.MouseButton.NoButton
        )
        self._tutorial_hint_rect = hint
        self._tutorial_cursor_progress = 0.0
        self._tutorial_cursor_timer.start()
        self._advance_tutorial_roi_cursor()

    def clear_tutorial_roi_hint(self) -> None:
        self._tutorial_cursor_timer.stop()
        for item in (
            self._tutorial_hint_item,
            self._tutorial_cursor_item,
        ):
            if item is not None and item.scene() is self._scene:
                self._scene.removeItem(item)
        self._tutorial_hint_item = None
        self._tutorial_cursor_item = None
        self._tutorial_hint_rect = None
        self._tutorial_cursor_progress = 0.0

    def _advance_tutorial_roi_cursor(self) -> None:
        rect = self._tutorial_hint_rect
        hint_item = self._tutorial_hint_item
        cursor = self._tutorial_cursor_item
        if rect is None or hint_item is None or cursor is None:
            self._tutorial_cursor_timer.stop()
            return
        # Reproduce the real interaction: the ROI itself grows with the mouse.
        # No cursor trail is drawn because the actual canvas does not show one.
        self._tutorial_cursor_progress = (
            self._tutorial_cursor_progress + 0.018
        ) % 1.42
        cycle = self._tutorial_cursor_progress
        moving = cycle <= 1.0
        cursor.setVisible(moving)
        if not moving:
            hint_item.setRect(rect)
            return
        eased = cycle * cycle * (3.0 - 2.0 * cycle)
        start = rect.topLeft()
        end = rect.bottomRight()
        point = QPointF(
            start.x() + (end.x() - start.x()) * eased,
            start.y() + (end.y() - start.y()) * eased,
        )
        cursor.setPos(point)
        hint_item.setRect(QRectF(start, point).normalized())

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
        if self._final_crop_item is not None:
            self._scene.removeItem(self._final_crop_item)
            self._final_crop_item = None

    def set_final_crop_overlay(self, rect: QRectF | None) -> None:
        """Show the source-pixel crop that Auto-Fit will apply to the figure."""
        if self._final_crop_item is not None:
            self._scene.removeItem(self._final_crop_item)
            self._final_crop_item = None
        if rect is None or rect.width() <= 0 or rect.height() <= 0:
            return
        pen = QPen(QColor("#9B6CC2"), 2.5, Qt.PenStyle.DashDotLine)
        pen.setCosmetic(True)
        brush = QBrush(QColor(155, 108, 194, 24))
        self._final_crop_item = self._scene.addRect(QRectF(rect), pen, brush)
        self._final_crop_item.setZValue(10.5)
        self._final_crop_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

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
                    self._pixmap_item.sceneBoundingRect() if self._pixmap_item else QRectF(),
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
        bounds = self._pixmap_item.sceneBoundingRect()
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

        Scene coordinates deliberately stay at full raw/presentation resolution,
        even when a large display preview is sampled and its pixmap item is scaled.
        Therefore scene coordinates already equal image coordinates.
        """
        return 1.0

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

    def _lane_roi_contains_viewport_pos(self, viewport_pos: QPoint) -> bool:
        """Return whether a click is within the lane ROI plus a 4 px visual margin."""
        if self._roi_item is None:
            return False
        viewport_rect = self.mapFromScene(self._roi_item.rect()).boundingRect()
        viewport_rect.adjust(
            -_LANE_ROI_CANCEL_TOLERANCE_PX,
            -_LANE_ROI_CANCEL_TOLERANCE_PX,
            _LANE_ROI_CANCEL_TOLERANCE_PX,
            _LANE_ROI_CANCEL_TOLERANCE_PX,
        )
        return viewport_rect.contains(viewport_pos)

    # ── Mouse events ───────────────────────────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._image_loading:
            event.accept()
            return
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
                    self._pixmap_item.sceneBoundingRect(),
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

        # After the first manual Lane ROI is complete, an out-of-frame click
        # cancels it instead of accidentally starting a Band ROI elsewhere.
        # The tolerance is measured in viewport pixels so it feels identical
        # at every zoom level.
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._pixmap_item is not None
            and self.dragMode() == QGraphicsView.DragMode.NoDrag
            and self._interaction_mode != "auto"
            and not self._wb_plot_roi_only
            and self._roi_item is not None
            and self._band_roi_item is None
            and not self._drawing
            and not self._lane_roi_contains_viewport_pos(event.pos())
        ):
            self.clear_roi()
            self.roi_cleared.emit()
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton and self._pixmap_item:
            if self.dragMode() == QGraphicsView.DragMode.NoDrag:
                sp = self.mapToScene(event.pos())
                if self._pixmap_item.sceneBoundingRect().contains(sp):
                    if (
                        self._wb_plot_roi_only
                        and self._auto_edit_enabled
                        and isinstance(self.itemAt(event.pos()), EditableBandRectItem)
                    ):
                        # Low-confidence WB Plot Auto-Fit review reuses the
                        # existing editable band items instead of starting a
                        # replacement rough ROI on top of them.
                        super().mousePressEvent(event)
                        return
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
        if self._image_loading:
            event.accept()
            return
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
        if self._image_loading:
            event.accept()
            return
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
        if self._image_loading:
            event.accept()
            return
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
        b = self._pixmap_item.sceneBoundingRect()
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
        bounds = self._pixmap_item.sceneBoundingRect()
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
