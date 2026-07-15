"""gui/figure_canvas.py — Live-preview QGraphicsView for WB Plot figures.

Renders a LayoutResult into a QGraphicsScene.  Each LayoutItem becomes one or
more QGraphicsItems:

  blot        → QGraphicsPixmapItem   (cropped in IMAGE_PX, scaled to scene)
  label / mw / title / panel_letter / table_cell
              → EditableTextItem      (double-click to edit inline)
  line        → QGraphicsLineItem
  divider     → QGraphicsLineItem     (dashed, not editable)

Coordinate spaces:
  All item positions come from LayoutResult in PT.
  pt_to_scene() (SCREEN_SCALE × pt) converts to QGraphicsScene units.
  No raw PT↔PX arithmetic is performed here — layout_engine helpers only.

Fine-position adjustment:
  Editable items are moved by manual mouse-drag handling. Movement is clamped to
  ±MAX_OFFSET_SCENE scene units from their computed position.
  Offsets survive re-renders via self._offsets keyed on SourceRef.key().

Text editing:
  Double-click enters edit mode.  Focus-out commits the new text by calling
  the on_text_edited callback: (SourceRef, new_text) → None.
  The callback is set by FigureModeWindow and calls FigureProject.apply_edit().

Overlay annotations:
  User-added text boxes and lines (from layout_editor_items) live in
  self._overlay_items.  They are removed before render() clears the scene
  and re-added afterward, so they survive layout re-computations.
  save_overlay() / load_overlay() serialize them to JSON.
  overlay_as_layout_items() converts them to LayoutItem objects for export.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image
from PySide6.QtCore import (
    QLineF, QPoint, QPointF, QRect, QRectF, QSize, Qt, QSignalBlocker, QTimer,
)
from PySide6.QtGui import (
    QBrush, QColor, QFont, QImage, QPainter, QPen, QPixmap, QUndoStack,
    QKeySequence, QTextBlockFormat, QTextCursor, QTransform, QWheelEvent,
)
from PySide6.QtWidgets import (
    QGraphicsItem, QGraphicsPixmapItem,
    QGraphicsRectItem, QGraphicsScene, QGraphicsTextItem,
    QGraphicsView, QRubberBand,
)

from core.figure_project import FigureProject, SourceRef
from core.layout_engine import (
    LayoutItem, LayoutResult,
    pt_to_scene, scene_to_pt,
)
from core.image_transform import (
    default_inverted_for_pil_image,
    image_transform_from_dict,
    image_array_to_uint16_luminance,
    transform_pixels_16_to_8,
)
from gui.layout_editor_items import (
    BlotPlaceholderItem as _OverlayBlotItem,
    EditableTextItem as _OverlayTextItem,
    LineElementItem as _OverlayLineItem,
)

# Maximum fine-position offset in scene units for label/table fine positioning.
MAX_OFFSET_SCENE: float = 240.0
DEFAULT_OVERLAY_TEXT_W: float = 46.0
DEFAULT_OVERLAY_TEXT_H: float = 24.0
DEFAULT_OVERLAY_BLOT_W: float = 180.0
DEFAULT_OVERLAY_BLOT_H: float = 36.0


# ── Overlay-capable scene ─────────────────────────────────────────────────────

class _OverlayScene(QGraphicsScene):
    """Plain QGraphicsScene extended with undo/redo for overlay annotation items.

    layout_editor_items.ResizableItemMixin and EditableTextItem look for
    scene().undo_stack and scene().record_text_edit() — this class provides
    both so that user-added text boxes and lines support undo/redo of resizes
    and text edits without touching the main WB-plot render pipeline.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.undo_stack = QUndoStack(self)
        self.undo_stack.setUndoLimit(10)
        self.record_state_before_change: Callable[[], None] | None = None
        self.move_group_item: Callable[[QGraphicsItem, QPointF], None] | None = None
        self.select_overlay_blot_item: Callable[[QGraphicsItem, bool], None] | None = None

    def record_text_edit(self, item: _OverlayTextItem, old_text: str, new_text: str) -> None:
        from gui.layout_editor_commands import EditTextCommand
        if old_text != new_text:
            if self.record_state_before_change is not None:
                self.record_state_before_change()
            self.undo_stack.push(EditTextCommand(item, old_text, new_text))


class ResizableBlotFrameItem(QGraphicsRectItem):
    _HANDLE_MARGIN = 7.0
    _MIN_SIZE = 18.0

    def __init__(
        self,
        rect: QRectF,
        source_ref: SourceRef,
        canvas: "FigureCanvas",
        parent=None,
    ) -> None:
        super().__init__(QRectF(0, 0, rect.width(), rect.height()), parent)
        self.setPos(rect.topLeft())
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setZValue(40)
        self.source_ref = source_ref
        self._canvas = canvas
        self._active_handle: str | None = None
        self._start_rect = QRectF()
        self._start_scene_pos = QPointF()
        self._moved_during_drag = False
        self._selected = False
        self._apply_style()

    def set_frame_selected(self, selected: bool) -> None:
        self._selected = selected
        self._apply_style()

    def _apply_style(self) -> None:
        if self._selected:
            self.setPen(QPen(QColor("#B96F73"), 3.0, Qt.PenStyle.SolidLine))
        else:
            self.setPen(QPen(QColor("#000000"), 1.0, Qt.PenStyle.SolidLine))
        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.update()

    def _handle_for_pos(self, pos: QPointF) -> str | None:
        rect = self.rect()
        near_right = abs(pos.x() - rect.right()) <= self._HANDLE_MARGIN
        near_bottom = abs(pos.y() - rect.bottom()) <= self._HANDLE_MARGIN
        if near_right and near_bottom:
            return "bottom_right"
        if near_right:
            return "right"
        if near_bottom:
            return "bottom"
        return None

    def hoverMoveEvent(self, event) -> None:
        handle = self._handle_for_pos(event.pos())
        if handle == "bottom_right":
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif handle == "right":
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif handle == "bottom":
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        additive = bool(
            event.modifiers()
            & (
                Qt.KeyboardModifier.MetaModifier
                | Qt.KeyboardModifier.ControlModifier
                | Qt.KeyboardModifier.ShiftModifier
            )
        )
        self._canvas._select_blot_frame(self, additive=additive)
        self._active_handle = self._handle_for_pos(event.pos())
        self._start_rect = QRectF(self.rect())
        self._start_scene_pos = event.scenePos()
        self._moved_during_drag = False
        if self._active_handle is None:
            self._active_handle = "move"
            self._canvas._begin_blot_frame_move()
        else:
            # Resize: save undo state before starting
            self._canvas._notify_state_about_to_change()
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._active_handle is None:
            event.accept()
            return
        delta = event.scenePos() - self._start_scene_pos
        if self._active_handle == "move":
            self._canvas._preview_blot_frame_move(delta)
            self._moved_during_drag = True
            event.accept()
            return
        rect = QRectF(self._start_rect)
        if self._active_handle in {"right", "bottom_right"}:
            rect.setWidth(max(self._MIN_SIZE, self._start_rect.width() + delta.x()))
        if self._active_handle in {"bottom", "bottom_right"}:
            rect.setHeight(max(self._MIN_SIZE, self._start_rect.height() + delta.y()))
        self.setRect(rect)
        self._canvas._preview_blot_resize(rect.width(), rect.height(), self)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self._active_handle == "move":
            delta = event.scenePos() - self._start_scene_pos
            self._canvas._commit_blot_frame_move(delta if self._moved_during_drag else QPointF(0.0, 0.0))
        elif self._active_handle is not None:
            self._canvas._commit_blot_resize_from_frame(self)
        self._active_handle = None
        event.accept()


# ── Editable text item ────────────────────────────────────────────────────────

class EditableTextItem(_OverlayTextItem):
    """Text item with:
      • Double-click → inline editing mode.
      • Focus-out → commit via on_commit callback.
      • Drag → fine-position adjustment clamped to ±MAX_OFFSET_SCENE.
      • source_ref carries the stable SourceRef for write-back.
    """

    def __init__(
        self,
        text: str,
        source_ref: SourceRef,
        on_commit: Callable[[SourceRef, str], None],
        font_family: str = "Arial",
        font_size_pt: float = 7.0,
        bold: bool = False,
        italic: bool = False,
        underline: bool = False,
        align: str = "left",
        computed_pos: QPointF | None = None,
        box_width: float = DEFAULT_OVERLAY_TEXT_W,
        box_height: float = DEFAULT_OVERLAY_TEXT_H,
        on_position_changed: Callable[["EditableTextItem"], None] | None = None,
        parent=None,
    ) -> None:
        super().__init__(
            text,
            QRectF(
                0.0,
                0.0,
                box_width,
                box_height,
            ),
            font_family=font_family,
            font_size=font_size_pt,
            bold=bold,
            italic=italic,
            underline=underline,
            text_align=align,
        )
        if parent is not None:
            self.setParentItem(parent)
        self._source_ref = source_ref
        self._on_commit = on_commit
        self._computed_pos: QPointF = computed_pos or QPointF(0.0, 0.0)
        self._on_position_changed = on_position_changed
        self.setDefaultTextColor(QColor("#000000"))

    # ── Event overrides ───────────────────────────────────────────────────

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        # Guard: scene may be None if this item is being destroyed during clear()
        if self.scene() is not None and self._on_commit is not None:
            self._on_commit(self._source_ref, self.toPlainText())

    def resize_to_local_size(self, width: float, height: float) -> None:
        super().resize_to_local_size(width, height)
        self._emit_position_changed()

    @property
    def source_ref(self) -> SourceRef:
        return self._source_ref

    def current_offset(self) -> QPointF:
        """Current fine-position offset from computed position (scene units)."""
        return self.pos() - self._computed_pos

    def _emit_position_changed(self) -> None:
        if self._on_position_changed is not None:
            self._on_position_changed(self)

    def _move_drag_group(self, delta: QPointF) -> None:
        targets = self._drag_group_start_positions or {self: QPointF(self.pos())}
        scene = self.scene()
        group_mover = getattr(scene, "move_group_item", None)
        for item, start_pos in targets.items():
            target_pos = start_pos + delta
            if group_mover is not None:
                group_mover(item, target_pos)
            else:
                item.setPos(target_pos)
            if isinstance(item, EditableTextItem) and group_mover is None:
                item._emit_position_changed()


# ── Figure canvas ─────────────────────────────────────────────────────────────

class FigureCanvas(QGraphicsView):
    """Scrollable, zoomable preview of the WB figure.

    Call render(layout, project) to populate.  Fine-position offsets are
    preserved across renders via self._offsets keyed by SourceRef.key().

    on_text_edited is set by FigureModeWindow; its signature is:
        (SourceRef, str) → None
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scene = _OverlayScene(self)
        self.setScene(self._scene)

        # Fine-position offsets: {SourceRef.key() → QPointF} in scene units
        self._offsets: dict[tuple, QPointF] = {}

        # User-added annotation items (text boxes, lines) — preserved across renders
        self._overlay_items: list[QGraphicsItem] = []

        # Pending placement mode: "text" | "line" | None
        self._pending_place: str | None = None

        # Per-slot position offsets for blot frames (scene units, arrow-key movement)
        self._blot_offsets: dict[tuple, QPointF] = {}
        # Tracks the primary visible content item (pixmap or placeholder rect) per slot
        self._blot_content_items: dict[tuple, list] = {}

        # Callback for text edits — set by the parent window before use
        self.on_text_edited: Callable[[SourceRef, str], None] | None = None
        self.on_blot_resized: Callable[[list[SourceRef], float, float], None] | None = None
        self.on_blot_selected: Callable[[SourceRef], None] | None = None
        self.on_blot_selection_cleared: Callable[[], None] | None = None
        self.on_view_interacted: Callable[[], None] | None = None
        self.on_state_about_to_change: Callable[[], None] | None = None
        self.on_undo_requested: Callable[[], None] | None = None
        self._selected_blot_keys: set[tuple] = set()
        self._syncing_blot_selection = False
        self._blot_frames: dict[tuple, ResizableBlotFrameItem] = {}
        self._blot_layout_items: dict[tuple, LayoutItem] = {}
        self._blot_move_start_offsets: dict[tuple, QPointF] = {}
        self._blot_move_start_frame_pos: dict[tuple, QPointF] = {}
        self._blot_move_start_content_pos: dict[tuple, list[QPointF]] = {}
        self._selected_text_keys: set[tuple] = set()
        self._text_items: dict[tuple, EditableTextItem] = {}
        self._text_box_sizes: dict[tuple, tuple[float, float]] = {}
        self._hidden_text_keys: set[tuple] = set()
        self._copied_text_items_data: list[dict] = []
        self._skip_next_render_offset_sync = False

        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)

        self._background_item: QGraphicsRectItem | None = None
        self._panning = False
        self._pan_start: QPointF | None = None

        self._rubberband: QRubberBand | None = None
        self._rubberband_origin: QPoint | None = None

        self.setBackgroundBrush(QBrush(QColor("#FFFFFF")))
        self.setStyleSheet("background-color: #FFFFFF;")
        self._scene.record_state_before_change = self._notify_state_about_to_change
        self._scene.move_group_item = self._move_group_item_to_position
        self._scene.select_overlay_blot_item = self._select_overlay_blot_item
        self._scene.selectionChanged.connect(self._sync_blot_selection_from_scene)

    def _notify_state_about_to_change(self) -> None:
        if self.on_state_about_to_change is not None:
            self.on_state_about_to_change()

    def _sync_text_offsets_from_items(self) -> None:
        for item in self._text_items.values():
            self._handle_text_position_changed(item)

    def _sync_blot_offsets_from_frames(self) -> None:
        for key, frame in self._blot_frames.items():
            layout_item = self._blot_layout_items.get(key)
            if layout_item is None:
                continue
            computed_pos = QPointF(
                pt_to_scene(layout_item.x_pt),
                pt_to_scene(layout_item.y_pt),
            )
            offset = frame.pos() - computed_pos
            if offset.manhattanLength() > 0.1:
                self._blot_offsets[key] = offset
            else:
                self._blot_offsets.pop(key, None)

    # ── Public API ────────────────────────────────────────────────────────

    def render(self, layout: LayoutResult, project: FigureProject) -> None:
        """Clear and re-populate the scene from *layout*.

        Fine-position offsets stored in self._offsets are re-applied so
        the user's manual adjustments survive layout re-computations.
        """
        self._syncing_blot_selection = True
        try:
            # Save current offsets from any existing editable items. Snapshot
            # restoration has already supplied the historical offsets, so the next
            # render after undo must not overwrite them with the current item state.
            if self._skip_next_render_offset_sync:
                self._skip_next_render_offset_sync = False
            else:
                self._sync_text_offsets_from_items()

            # Drop focus/selection so Qt releases internal item references before clear
            self._scene.clearFocus()
            self._scene.clearSelection()

            # Lift overlay items out before clearing so they are not destroyed
            for ov_item in self._overlay_items:
                if ov_item.scene() is self._scene:
                    self._scene.removeItem(ov_item)

            # Release Python wrappers before scene.clear() deletes remaining C++ items.
            # Keeping wrappers to scene-owned items past clear can double-destroy them
            # during shortcut callbacks on PySide 6.11.
            self._background_item = None
            self._blot_frames.clear()
            self._blot_layout_items.clear()
            self._text_items.clear()
            self._blot_content_items.clear()

            self._scene.clear()

            if not layout.items:
                for ov_item in self._overlay_items:
                    if ov_item.scene() is not self._scene:
                        self._scene.addItem(ov_item)
                return

            # White page background
            bg_w = pt_to_scene(layout.canvas_width_pt)
            bg_h = pt_to_scene(layout.canvas_height_pt)
            self._background_item = self._scene.addRect(
                QRectF(0.0, 0.0, bg_w, bg_h),
                QPen(Qt.PenStyle.NoPen),
                QBrush(QColor("#FFFFFF")),
            )
            self._background_item.setZValue(-1)

            for item in sorted(layout.items, key=lambda i: i.z_order):
                self._create_scene_item(item)

            self._scene.setSceneRect(QRectF(-20.0, -20.0, bg_w + 40.0, bg_h + 40.0))
            for key, item in self._text_items.items():
                item.setSelected(key in self._selected_text_keys)

            # Restore overlay items on top of the rendered layout
            for ov_item in self._overlay_items:
                if ov_item.scene() is not self._scene:
                    self._scene.addItem(ov_item)
        finally:
            self._syncing_blot_selection = False

    def state_snapshot(self) -> dict:
        """Return a lightweight undo snapshot for canvas-level edits."""
        self._sync_text_offsets_from_items()
        self._sync_blot_offsets_from_frames()

        def encode_offsets(offsets: dict[tuple, QPointF]) -> list[dict]:
            return [
                {"key": list(key), "x": value.x(), "y": value.y()}
                for key, value in offsets.items()
            ]

        return {
            "overlay_items": self.overlay_items_as_json_data(),
            "hidden_text_keys": [list(key) for key in self._hidden_text_keys],
            "fine_offsets": encode_offsets(self._offsets),
            "blot_offsets": encode_offsets(self._blot_offsets),
            "text_box_sizes": [
                {"key": list(key), "w": value[0], "h": value[1]}
                for key, value in self._text_box_sizes.items()
            ],
        }

    def restore_state_snapshot(self, snapshot: dict, *, repopulate_scene: bool = True) -> None:
        """Restore a snapshot previously returned by state_snapshot()."""
        def decode_offsets(data: list[dict]) -> dict[tuple, QPointF]:
            result: dict[tuple, QPointF] = {}
            for entry in data:
                key = tuple(entry.get("key", []))
                result[key] = QPointF(float(entry.get("x", 0.0)), float(entry.get("y", 0.0)))
            return result

        self._restore_overlay_from_data(
            snapshot.get("overlay_items", []),
            add_to_scene=repopulate_scene,
        )
        self._hidden_text_keys = {
            tuple(key) for key in snapshot.get("hidden_text_keys", [])
        }
        self._offsets = decode_offsets(snapshot.get("fine_offsets", []))
        self._blot_offsets = decode_offsets(snapshot.get("blot_offsets", []))
        self._text_box_sizes = {
            tuple(entry.get("key", [])): (
                float(entry.get("w", DEFAULT_OVERLAY_TEXT_W)),
                float(entry.get("h", DEFAULT_OVERLAY_TEXT_H)),
            )
            for entry in snapshot.get("text_box_sizes", [])
        }
        self._skip_next_render_offset_sync = True

    def clear_all(self) -> None:
        """Reset the editable preview to a blank canvas."""
        self._scene.clearFocus()
        self._scene.clearSelection()
        self._offsets.clear()
        self._overlay_items.clear()
        self._pending_place = None
        self._blot_offsets.clear()
        self._blot_content_items.clear()
        self._background_item = None
        self._blot_frames.clear()
        self._blot_layout_items.clear()
        self._blot_move_start_offsets.clear()
        self._blot_move_start_frame_pos.clear()
        self._blot_move_start_content_pos.clear()
        self._selected_blot_keys.clear()
        self._selected_text_keys.clear()
        self._text_items.clear()
        self._text_box_sizes.clear()
        self._hidden_text_keys.clear()
        self._copied_text_items_data.clear()
        self._skip_next_render_offset_sync = False
        self._scene.undo_stack.clear()
        self._scene.clear()
        self._scene.setSceneRect(QRectF())
        self.resetTransform()

    def fit_to_view(self) -> None:
        if self._background_item:
            self.fitInView(
                self._background_item,
                Qt.AspectRatioMode.KeepAspectRatio,
            )
            self.scale(1.18, 1.18)

    def render_page_image(self, *, scale: float = 2.0) -> QImage:
        """Render the current visible figure page to a raster image for exact export."""
        source = (
            self._background_item.sceneBoundingRect()
            if self._background_item is not None
            else self._scene.itemsBoundingRect()
        )
        if source.isNull() or source.width() <= 0.0 or source.height() <= 0.0:
            return QImage()

        scale = max(1.0, float(scale))
        image = QImage(
            max(1, int(round(source.width() * scale))),
            max(1, int(round(source.height() * scale))),
            QImage.Format.Format_ARGB32,
        )
        image.fill(QColor("#FFFFFF"))

        selected_items = list(self._scene.selectedItems())
        selected_blot_keys = set(self._selected_blot_keys)
        with QSignalBlocker(self._scene):
            self._syncing_blot_selection = True
            try:
                for item in selected_items:
                    item.setSelected(False)
                for frame in self._blot_frames.values():
                    frame.set_frame_selected(False)

                painter = QPainter(image)
                try:
                    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                    self._scene.render(
                        painter,
                        QRectF(0.0, 0.0, image.width(), image.height()),
                        source,
                    )
                finally:
                    painter.end()

                for item in selected_items:
                    item.setSelected(True)
                for key, frame in self._blot_frames.items():
                    frame.set_frame_selected(key in selected_blot_keys)
            finally:
                self._syncing_blot_selection = False
        return image

    # ── Item factory ──────────────────────────────────────────────────────

    def _create_scene_item(self, item: LayoutItem) -> None:
        x = pt_to_scene(item.x_pt)
        y = pt_to_scene(item.y_pt)
        w = pt_to_scene(item.w_pt)
        h = pt_to_scene(item.h_pt)

        if item.kind == "blot":
            self._add_blot(item, x, y, w, h)

        elif item.kind in ("label", "mw", "title", "panel_letter", "table_cell"):
            if item.source_ref is not None and item.source_ref.key() in self._hidden_text_keys:
                return
            self._add_text(item, x, y, w, h)

        elif item.kind == "line":
            self._add_line(item, x, y, w, h)

        elif item.kind == "divider":
            self._add_divider(item, x, y, h)

    # ── Blot ──────────────────────────────────────────────────────────────

    def _add_blot(
        self, item: LayoutItem,
        x: float, y: float, w: float, h: float,
    ) -> None:
        key = item.source_ref.key() if item.source_ref is not None else None
        off = self._blot_offsets.get(key, QPointF(0.0, 0.0)) if key else QPointF(0.0, 0.0)
        ox, oy = off.x(), off.y()

        content_group: list = []
        pm = self._make_blot_pixmap(item, w, h)
        if pm:
            gi = QGraphicsPixmapItem(pm)
            gi.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            gi.setPos(x + ox, y + oy)
            sx = w / max(1, pm.width())
            sy = h / max(1, pm.height())
            gi.setTransform(QTransform.fromScale(sx, sy), False)
            gi.setZValue(item.z_order)
            self._scene.addItem(gi)
            content_group.append(gi)
        else:
            # Grey placeholder
            rect_item = self._scene.addRect(
                QRectF(x + ox, y + oy, w, h),
                QPen(QColor("#000000"), 1.0, Qt.PenStyle.SolidLine),
                QBrush(QColor("#D8D8D8")),
            )
            rect_item.setZValue(item.z_order)
            content_group.append(rect_item)
            # "No image" label
            lbl = self._scene.addText("[ no image ]")
            lbl.setDefaultTextColor(QColor("#999999"))
            font = QFont("Arial")
            font.setPointSizeF(6.0)
            lbl.setFont(font)
            lbl.setPos(x + ox + 4, y + oy + h / 2 - 6)
            lbl.setZValue(item.z_order + 1)
            content_group.append(lbl)

        if key is not None:
            self._blot_content_items[key] = content_group
            self._blot_layout_items[key] = item

        if item.source_ref is not None:
            frame = ResizableBlotFrameItem(
                QRectF(x + ox, y + oy, w, h),
                item.source_ref,
                self,
            )
            selected = key in self._selected_blot_keys
            frame.setSelected(selected)
            frame.set_frame_selected(selected)
            self._scene.addItem(frame)
            self._blot_frames[key] = frame

    def _make_blot_pixmap(
        self, item: LayoutItem, w_scene: float, h_scene: float
    ) -> QPixmap | None:
        if not item.image_path or not Path(item.image_path).exists():
            return None
        try:
            with Image.open(item.image_path) as img:
                default_inverted = default_inverted_for_pil_image(img, fallback=True)
                pixels = image_array_to_uint16_luminance(np.array(img))
        except Exception:
            return None

        # Crop in IMAGE_PX coordinates
        if item.image_crop_px:
            c = item.image_crop_px
            img_h, img_w = pixels.shape
            if img_w <= 0 or img_h <= 0:
                return None
            x = max(0, min(img_w - 1, int(round(float(c.get("x", 0.0))))))
            y = max(0, min(img_h - 1, int(round(float(c.get("y", 0.0))))))
            crop_w = max(1, int(round(float(c.get("w", img_w - x)))))
            crop_h = max(1, int(round(float(c.get("h", img_h - y)))))
            right = max(x + 1, min(img_w, x + crop_w))
            bottom = max(y + 1, min(img_h, y + crop_h))
            pixels = pixels[y:bottom, x:right]
        if pixels.size == 0:
            return None

        display = transform_pixels_16_to_8(
            pixels,
            image_transform_from_dict(
                item.image_transform,
                default_inverted=default_inverted,
            ),
        )
        display = np.ascontiguousarray(display)
        height, width = display.shape
        qimage = QImage(
            display.data,
            width,
            height,
            display.strides[0],
            QImage.Format.Format_Grayscale8,
        ).copy()

        return QPixmap.fromImage(qimage)

    # ── Text ──────────────────────────────────────────────────────────────

    def _add_text(
        self, item: LayoutItem,
        x: float, y: float, w: float, h: float,
    ) -> None:
        computed_pos = QPointF(x, y + h * 0.25)   # vertically centre-ish

        # Re-apply saved offset (if any)
        offset = QPointF(0.0, 0.0)
        if item.source_ref is not None:
            offset = self._offsets.get(item.source_ref.key(), QPointF(0.0, 0.0))
        box_w = w
        box_h = h
        if item.source_ref is not None:
            box_w, box_h = self._text_box_sizes.get(item.source_ref.key(), (w, h))

        gi = EditableTextItem(
            text=item.text,
            source_ref=item.source_ref,  # type: ignore[arg-type]
            on_commit=self._handle_text_edit,
            font_family=item.font_family,
            font_size_pt=item.font_size_pt,
            bold=item.bold,
            italic=item.italic,
            underline=item.underline,
            align=item.align,
            computed_pos=computed_pos,
            box_width=box_w,
            box_height=box_h,
            on_position_changed=self._handle_text_position_changed,
        )
        gi.setPos(computed_pos + offset)
        gi.setRotation(item.rotation)
        gi.setZValue(item.z_order)
        self._scene.addItem(gi)
        if item.source_ref is not None:
            self._text_items[item.source_ref.key()] = gi

    def _handle_text_edit(self, ref: SourceRef, new_text: str) -> None:
        if self.on_text_edited is not None:
            self.on_text_edited(ref, new_text)

    def _handle_text_position_changed(self, item: EditableTextItem) -> None:
        key = item.source_ref.key()
        offset = item.current_offset()
        if offset.manhattanLength() > 0.1:
            self._offsets[key] = offset
        else:
            self._offsets.pop(key, None)
        rect = item.editor_rect()
        self._text_box_sizes[key] = (rect.width(), rect.height())

    def selected_text_refs(self) -> list[SourceRef]:
        refs: list[SourceRef] = []
        self._selected_text_keys.clear()
        for key, item in self._text_items.items():
            if item.isSelected():
                self._selected_text_keys.add(key)
                refs.append(item.source_ref)
        return refs

    def hidden_text_keys(self) -> set[tuple]:
        return set(self._hidden_text_keys)

    def selected_text_items(self) -> list[QGraphicsTextItem]:
        result: list[QGraphicsTextItem] = []
        for item in self._text_items.values():
            if item.isSelected():
                result.append(item)
        for item in self._overlay_items:
            if isinstance(item, _OverlayTextItem) and item.isSelected():
                result.append(item)
        return result

    def selected_layout_item_count(self) -> int:
        return len(self._selected_layout_items())

    def _selected_layout_items(self) -> list[QGraphicsItem]:
        items: list[QGraphicsItem] = list(self.selected_text_items())
        items.extend(
            item
            for item in self._overlay_items
            if isinstance(item, _OverlayBlotItem) and item.isSelected()
        )
        for key in self._selected_blot_keys:
            frame = self._blot_frames.get(key)
            if frame is not None:
                items.append(frame)
        return items

    def _key_for_blot_frame(self, frame: ResizableBlotFrameItem) -> tuple | None:
        for key, candidate in self._blot_frames.items():
            if candidate is frame:
                return key
        return None

    def select_blot_refs(self, refs: list[SourceRef]) -> None:
        keys = {
            ref.key()
            for ref in refs
            if ref.panel_idx is not None and ref.slot_idx is not None
        }
        self._selected_blot_keys = keys
        self._syncing_blot_selection = True
        try:
            for key, frame in self._blot_frames.items():
                selected = key in keys
                frame.setSelected(selected)
                frame.set_frame_selected(selected)
        finally:
            self._syncing_blot_selection = False
        if refs and self.on_blot_selected is not None:
            self.on_blot_selected(refs[0])

    def _move_blot_frame_by_delta(self, key: tuple, delta: QPointF) -> None:
        if delta.manhattanLength() <= 0.0:
            return
        self._blot_offsets[key] = self._blot_offsets.get(key, QPointF(0.0, 0.0)) + delta
        frame = self._blot_frames.get(key)
        if frame is not None:
            frame.setPos(frame.pos() + delta)
        for content_item in self._blot_content_items.get(key, []):
            content_item.setPos(content_item.pos() + delta)

    def _move_group_item_to_position(self, item: QGraphicsItem, target_pos: QPointF) -> None:
        delta = target_pos - item.pos()
        if isinstance(item, ResizableBlotFrameItem):
            key = self._key_for_blot_frame(item)
            if key is not None:
                self._move_blot_frame_by_delta(key, delta)
            else:
                item.setPos(target_pos)
            return
        item.setPos(target_pos)
        if isinstance(item, EditableTextItem):
            self._handle_text_position_changed(item)

    def _sync_blot_selection_from_scene(self) -> None:
        if self._syncing_blot_selection:
            return
        selected = {
            key
            for key, frame in self._blot_frames.items()
            if frame.isSelected()
        }
        if selected == self._selected_blot_keys:
            for key, frame in self._blot_frames.items():
                frame.set_frame_selected(key in selected)
            return

        had_selection = bool(self._selected_blot_keys)
        self._selected_blot_keys = selected
        for key, frame in self._blot_frames.items():
            frame.set_frame_selected(key in selected)
        if selected:
            ref = next(
                (
                    frame.source_ref
                    for key, frame in self._blot_frames.items()
                    if key in selected
                ),
                None,
            )
            if ref is not None and self.on_blot_selected is not None:
                self.on_blot_selected(ref)
        elif had_selection and self.on_blot_selection_cleared is not None:
            self.on_blot_selection_cleared()

    def selected_text_style_overrides(self) -> dict[tuple, dict]:
        styles: dict[tuple, dict] = {}
        for key, item in self._text_items.items():
            if not item.isSelected():
                continue
            font = item.font()
            styles[key] = {
                "font_family": font.family(),
                "font_size_pt": font.pointSizeF(),
                "bold": font.bold(),
                "italic": font.italic(),
                "underline": font.underline(),
                "align": item.text_align(),
                "rotation": item.rotation(),
            }
        return styles

    def _select_blot_frame(self, frame: ResizableBlotFrameItem, *, additive: bool) -> None:
        if self.on_view_interacted is not None:
            self.on_view_interacted()
        key = frame.source_ref.key()
        if additive:
            if key in self._selected_blot_keys:
                self._selected_blot_keys.remove(key)
            else:
                self._selected_blot_keys.add(key)
        elif key in self._selected_blot_keys and len(self._selected_blot_keys) > 1:
            # Keep PPT-like group selection when clicking an already-selected
            # frame to move or resize it.
            pass
        else:
            self._selected_blot_keys = {key}
        self._syncing_blot_selection = True
        try:
            if not additive:
                for item in self._overlay_items:
                    if isinstance(item, _OverlayBlotItem):
                        item.setSelected(False)
            for item_key, item in self._blot_frames.items():
                selected = item_key in self._selected_blot_keys
                item.setSelected(selected)
                item.set_frame_selected(selected)
        finally:
            self._syncing_blot_selection = False
        if not additive and self.on_blot_selected is not None:
            self.on_blot_selected(frame.source_ref)

    def _select_overlay_blot_item(self, item: QGraphicsItem, additive: bool) -> None:
        if self.on_view_interacted is not None:
            self.on_view_interacted()
        self._syncing_blot_selection = True
        try:
            if additive:
                item.setSelected(not item.isSelected())
            else:
                self._selected_blot_keys.clear()
                for frame in self._blot_frames.values():
                    frame.setSelected(False)
                    frame.set_frame_selected(False)
                for overlay_item in self._overlay_items:
                    if isinstance(overlay_item, _OverlayBlotItem):
                        overlay_item.setSelected(overlay_item is item)
        finally:
            self._syncing_blot_selection = False

    def selected_blot_refs(self) -> list[SourceRef]:
        return [
            item.source_ref
            for key, item in self._blot_frames.items()
            if key in self._selected_blot_keys
        ]

    def selected_overlay_blot_items(self) -> list[_OverlayBlotItem]:
        return [
            item
            for item in self._overlay_items
            if isinstance(item, _OverlayBlotItem) and item.isSelected()
        ]

    def _clear_blot_selection(self) -> None:
        if not self._selected_blot_keys:
            return
        self._selected_blot_keys.clear()
        self._syncing_blot_selection = True
        try:
            for item in self._blot_frames.values():
                item.setSelected(False)
                item.set_frame_selected(False)
        finally:
            self._syncing_blot_selection = False
        if self.on_blot_selection_cleared is not None:
            self.on_blot_selection_cleared()

    def _begin_blot_frame_move(self) -> None:
        self._notify_state_about_to_change()
        keys = set(self._selected_blot_keys)
        self._blot_move_start_offsets = {
            key: QPointF(self._blot_offsets.get(key, QPointF(0.0, 0.0)))
            for key in keys
        }
        self._blot_move_start_frame_pos = {
            key: QPointF(frame.pos())
            for key, frame in self._blot_frames.items()
            if key in keys
        }
        self._blot_move_start_content_pos = {
            key: [QPointF(item.pos()) for item in self._blot_content_items.get(key, [])]
            for key in keys
        }

    def _preview_blot_frame_move(self, delta: QPointF) -> None:
        for key, start_pos in self._blot_move_start_frame_pos.items():
            frame = self._blot_frames.get(key)
            if frame is not None:
                frame.setPos(start_pos + delta)
            content_items = self._blot_content_items.get(key, [])
            start_content = self._blot_move_start_content_pos.get(key, [])
            for item, item_start in zip(content_items, start_content):
                item.setPos(item_start + delta)

    def _commit_blot_frame_move(self, delta: QPointF) -> None:
        for key, start_offset in self._blot_move_start_offsets.items():
            self._blot_offsets[key] = start_offset + delta
        self._blot_move_start_offsets.clear()
        self._blot_move_start_frame_pos.clear()
        self._blot_move_start_content_pos.clear()

    def _commit_blot_resize_from_frame(self, frame: ResizableBlotFrameItem) -> None:
        if self.on_blot_resized is None:
            return
        if frame.source_ref.key() not in self._selected_blot_keys:
            self._selected_blot_keys = {frame.source_ref.key()}
        refs = [
            item.source_ref
            for key, item in self._blot_frames.items()
            if key in self._selected_blot_keys
        ]
        if not refs:
            refs = [frame.source_ref]
        width_pt = scene_to_pt(frame.rect().width())
        height_pt = scene_to_pt(frame.rect().height())
        QTimer.singleShot(
            0,
            lambda refs=list(refs), width_pt=width_pt, height_pt=height_pt:
                self._emit_blot_resized(refs, width_pt, height_pt),
        )

    def _emit_blot_resized(
        self,
        refs: list[SourceRef],
        width_pt: float,
        height_pt: float,
    ) -> None:
        if self.on_blot_resized is not None:
            self.on_blot_resized(refs, width_pt, height_pt)

    def _preview_blot_resize(
        self, width: float, height: float, source_frame: "ResizableBlotFrameItem"
    ) -> None:
        """Visually update selected blot frames and their visible content."""
        new_rect = QRectF(0.0, 0.0, width, height)
        for key, frame in self._blot_frames.items():
            if key not in self._selected_blot_keys:
                continue
            if frame is not source_frame:
                frame.setRect(new_rect)
            self._resize_blot_content_to_frame(key, frame, width, height)

    def _resize_blot_content_to_frame(
        self,
        key: tuple,
        frame: ResizableBlotFrameItem,
        width: float,
        height: float,
    ) -> None:
        frame_pos = frame.pos()
        for item in self._blot_content_items.get(key, []):
            if isinstance(item, QGraphicsPixmapItem):
                pm = item.pixmap()
                if not pm.isNull():
                    sx = width / max(1, pm.width())
                    sy = height / max(1, pm.height())
                    item.setPos(frame_pos)
                    item.setTransform(QTransform.fromScale(sx, sy), False)
            elif isinstance(item, QGraphicsRectItem):
                item.setPos(QPointF(0.0, 0.0))
                item.setRect(QRectF(frame_pos.x(), frame_pos.y(), width, height))
            elif isinstance(item, QGraphicsTextItem):
                item.setPos(frame_pos.x() + 4.0, frame_pos.y() + height / 2.0 - 6.0)

    # ── Line / divider ────────────────────────────────────────────────────

    def _add_line(
        self, item: LayoutItem, x: float, y: float, w: float, h: float
    ) -> None:
        pen = QPen(QColor(item.line_color or "#AAAAAA"))
        pen.setWidthF(pt_to_scene(item.line_width_pt))
        li = _OverlayLineItem(QLineF(x, y, x + w, y + h))
        li.setPen(pen)
        li.start_handle.setVisible(False)
        li.end_handle.setVisible(False)
        li.setZValue(item.z_order)
        self._scene.addItem(li)

    def _add_divider(
        self, item: LayoutItem, x: float, y: float, h: float
    ) -> None:
        pen = QPen(QColor("#CCCCCC"))
        pen.setWidthF(0.5)
        pen.setStyle(Qt.PenStyle.DashLine)
        li = self._scene.addLine(x, y, x, y + h, pen)
        li.setZValue(item.z_order)

    # ── Overlay annotations ───────────────────────────────────────────────

    def add_overlay_text_box(self) -> _OverlayTextItem:
        """Add an editable text box at the center of the current viewport."""
        self._notify_state_about_to_change()
        center = self.mapToScene(self.viewport().rect().center())
        item = _OverlayTextItem(
            "Text",
            QRectF(
                center.x() - DEFAULT_OVERLAY_TEXT_W / 2.0,
                center.y() - DEFAULT_OVERLAY_TEXT_H / 2.0,
                DEFAULT_OVERLAY_TEXT_W,
                DEFAULT_OVERLAY_TEXT_H,
            ),
        )
        self._scene.addItem(item)
        self._overlay_items.append(item)
        self._scene.clearSelection()
        item.setSelected(True)
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        return item

    def add_overlay_line(self) -> _OverlayLineItem:
        """Add a horizontal line at the center of the current viewport."""
        self._notify_state_about_to_change()
        center = self.mapToScene(self.viewport().rect().center())
        item = _OverlayLineItem(QLineF(center.x() - 60, center.y(), center.x() + 60, center.y()))
        self._scene.addItem(item)
        self._overlay_items.append(item)
        self._scene.clearSelection()
        item.setSelected(True)
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        return item

    def add_overlay_blot_frame(self) -> _OverlayBlotItem:
        """Add a free-position WB blot frame that can receive ROI image sync."""
        self._notify_state_about_to_change()
        source_frame = next(
            (
                frame for key, frame in self._blot_frames.items()
                if key in self._selected_blot_keys
            ),
            None,
        )
        if source_frame is None and self._blot_frames:
            source_frame = next(iter(self._blot_frames.values()))

        if source_frame is not None:
            rect = QRectF(
                source_frame.pos().x() + source_frame.rect().width() + 18.0,
                source_frame.pos().y(),
                source_frame.rect().width(),
                source_frame.rect().height(),
            )
        else:
            center = self.mapToScene(self.viewport().rect().center())
            rect = QRectF(
                center.x() - DEFAULT_OVERLAY_BLOT_W / 2.0,
                center.y() - DEFAULT_OVERLAY_BLOT_H / 2.0,
                DEFAULT_OVERLAY_BLOT_W,
                DEFAULT_OVERLAY_BLOT_H,
            )
        self._scene.clearSelection()
        self._clear_blot_selection()
        item = _OverlayBlotItem(rect)
        self._scene.addItem(item)
        self._overlay_items.append(item)
        item.setSelected(True)
        return item

    def save_overlay(self, path: str | Path) -> None:
        """Serialize all overlay annotation items to JSON."""
        data = {
            "version": 1,
            "items": self.overlay_items_as_json_data(),
        }
        Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")

    def load_overlay(self, path: str | Path) -> None:
        """Replace current overlay items from a JSON file."""
        self._scene.clearFocus()
        self._scene.clearSelection()
        self._scene.undo_stack.clear()
        for item in self._overlay_items:
            self._scene.removeItem(item)
        self._overlay_items.clear()

        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        factories = {
            _OverlayTextItem.TypeName: _OverlayTextItem.from_json,
            _OverlayBlotItem.TypeName: _OverlayBlotItem.from_json,
            _OverlayLineItem.TypeName: _OverlayLineItem.from_json,
        }
        for item_data in raw.get("items", []):
            factory = factories.get(item_data.get("type"))
            if factory is None:
                continue
            item = factory(item_data)
            self._scene.addItem(item)
            self._overlay_items.append(item)

    def set_selected_line_width(self, width: float) -> None:
        """Update pen width of all selected editable LineElementItems."""
        selected = [
            item for item in self._scene.selectedItems()
            if isinstance(item, _OverlayLineItem) and item.isSelected()
        ]
        if selected:
            self._notify_state_about_to_change()
        for item in selected:
            pen = item.pen()
            pen.setWidthF(max(0.5, width))
            item.setPen(pen)

    def apply_selected_text_font(
        self,
        family: str | None = None,
        size: float | None = None,
        bold: bool | None = None,
        italic: bool | None = None,
        underline: bool | None = None,
    ) -> dict[tuple, dict]:
        """Apply font changes to all selected text items.

        Returns style data for built-in WB text items so callers can persist
        the edits across layout recomputes and exports.
        """
        from PySide6.QtGui import QFont as _QFont
        built_in_styles: dict[tuple, dict] = {}
        candidates: list[QGraphicsTextItem] = []
        candidates.extend(item for item in self._text_items.values() if item.isSelected())
        candidates.extend(
            item
            for item in self._overlay_items
            if isinstance(item, _OverlayTextItem) and item.isSelected()
        )
        for item in candidates:
            font = _QFont(item.font())
            if family is not None:
                font.setFamily(family)
            if size is not None:
                font.setPointSizeF(max(1.0, size))
            if bold is not None:
                font.setBold(bold)
            if italic is not None:
                font.setItalic(italic)
            if underline is not None:
                font.setUnderline(underline)
            item.setFont(font)
            if isinstance(item, EditableTextItem):
                built_in_styles[item.source_ref.key()] = {
                    "font_family": font.family(),
                    "font_size_pt": font.pointSizeF(),
                    "bold": font.bold(),
                    "italic": font.italic(),
                    "underline": font.underline(),
                    "align": item.text_align(),
                    "rotation": item.rotation(),
                }
                item.update_resize_handles()
                self._handle_text_position_changed(item)
            elif isinstance(item, _OverlayTextItem):
                item.update_resize_handles()
        return built_in_styles

    def apply_selected_text_rotation(self, angle: float) -> dict[tuple, dict]:
        """Set rotation angle in degrees for all selected text items."""
        items = self.selected_text_items()
        if not items:
            return {}
        self._notify_state_about_to_change()
        angle = max(-180.0, min(180.0, float(angle)))
        built_in_styles: dict[tuple, dict] = {}
        for item in items:
            item.setRotation(angle)
            if isinstance(item, EditableTextItem):
                font = item.font()
                built_in_styles[item.source_ref.key()] = {
                    "font_family": font.family(),
                    "font_size_pt": font.pointSizeF(),
                    "bold": font.bold(),
                    "italic": font.italic(),
                    "underline": font.underline(),
                    "align": item.text_align(),
                    "rotation": item.rotation(),
                }
                self._handle_text_position_changed(item)
            elif isinstance(item, _OverlayTextItem):
                item.update_resize_handles()
        return built_in_styles

    def apply_overlay_font(
        self,
        family: str | None = None,
        size: float | None = None,
        bold: bool | None = None,
        italic: bool | None = None,
        underline: bool | None = None,
    ) -> None:
        """Backward-compatible wrapper for older toolbar wiring."""
        self.apply_selected_text_font(family, size, bold, italic, underline)

    def align_selected_text_boxes(self, action: str) -> bool:
        """Align or distribute selected text boxes and blot frames."""
        items = self._selected_layout_items()
        if not items:
            return False
        if action in {"distribute_h", "distribute_v"} and len(items) < 3:
            return False

        self._notify_state_about_to_change()
        bounds = [item.sceneBoundingRect() for item in items]
        left = min(rect.left() for rect in bounds)
        right = max(rect.right() for rect in bounds)
        top = min(rect.top() for rect in bounds)
        bottom = max(rect.bottom() for rect in bounds)
        center_x = (left + right) / 2.0
        center_y = (top + bottom) / 2.0

        def move_item(item: QGraphicsItem, dx: float, dy: float) -> None:
            delta = QPointF(dx, dy)
            if isinstance(item, ResizableBlotFrameItem):
                key = self._key_for_blot_frame(item)
                if key is not None:
                    self._move_blot_frame_by_delta(key, delta)
                else:
                    item.setPos(item.pos() + delta)
            elif isinstance(item, EditableTextItem):
                item.setPos(item.pos() + delta)
                self._handle_text_position_changed(item)
            else:
                item.setPos(item.pos() + delta)

        if action in {"left", "center_h", "right", "top", "middle_v", "bottom"}:
            for item, rect in zip(items, bounds):
                dx = 0.0
                dy = 0.0
                if action == "left":
                    dx = left - rect.left()
                elif action == "center_h":
                    dx = center_x - rect.center().x()
                elif action == "right":
                    dx = right - rect.right()
                elif action == "top":
                    dy = top - rect.top()
                elif action == "middle_v":
                    dy = center_y - rect.center().y()
                elif action == "bottom":
                    dy = bottom - rect.bottom()
                move_item(item, dx, dy)
            return True

        if action == "distribute_h":
            ordered = sorted(zip(items, bounds), key=lambda pair: pair[1].center().x())
            first = ordered[0][1].center().x()
            last = ordered[-1][1].center().x()
            step = (last - first) / (len(ordered) - 1)
            for idx, (item, rect) in enumerate(ordered):
                move_item(item, first + idx * step - rect.center().x(), 0.0)
            return True

        if action == "distribute_v":
            ordered = sorted(zip(items, bounds), key=lambda pair: pair[1].center().y())
            first = ordered[0][1].center().y()
            last = ordered[-1][1].center().y()
            step = (last - first) / (len(ordered) - 1)
            for idx, (item, rect) in enumerate(ordered):
                move_item(item, 0.0, first + idx * step - rect.center().y())
            return True

        return False

    def match_selected_item_sizes(self, mode: str) -> bool:
        """Resize selected text boxes and blot frames to the largest/smallest item."""
        items = self._selected_layout_items()
        if len(items) < 2:
            return False
        sizes = [
            (item, *self._layout_item_size(item))
            for item in items
        ]
        if mode == "smallest":
            _target, target_w, target_h = min(sizes, key=lambda entry: entry[1] * entry[2])
        else:
            _target, target_w, target_h = max(sizes, key=lambda entry: entry[1] * entry[2])

        target_w = max(8.0, float(target_w))
        target_h = max(8.0, float(target_h))
        self._notify_state_about_to_change()

        resized_blot_refs: list[SourceRef] = []
        for item in items:
            if isinstance(item, EditableTextItem):
                item.resize_to_local_size(target_w, target_h)
                item.update_resize_handles()
                self._handle_text_position_changed(item)
            elif isinstance(item, (_OverlayTextItem, _OverlayBlotItem)):
                item.resize_to_local_size(target_w, target_h)
                item.update_resize_handles()
            elif isinstance(item, ResizableBlotFrameItem):
                key = self._key_for_blot_frame(item)
                item.setRect(QRectF(0.0, 0.0, target_w, target_h))
                if key is not None:
                    self._resize_blot_content_to_frame(key, item, target_w, target_h)
                resized_blot_refs.append(item.source_ref)

        if resized_blot_refs and self.on_blot_resized is not None:
            self._emit_blot_resized(
                resized_blot_refs,
                scene_to_pt(target_w),
                scene_to_pt(target_h),
            )
        return True

    def _layout_item_size(self, item: QGraphicsItem) -> tuple[float, float]:
        if isinstance(item, ResizableBlotFrameItem):
            rect = item.rect()
            return rect.width(), rect.height()
        if isinstance(item, _OverlayTextItem):
            rect = item.editor_rect()
            return rect.width(), rect.height()
        if isinstance(item, _OverlayBlotItem):
            rect = item.rect()
            return rect.width(), rect.height()
        if isinstance(item, QGraphicsTextItem):
            rect = item.sceneBoundingRect()
            return max(8.0, item.textWidth()), rect.height()
        rect = item.sceneBoundingRect()
        return rect.width(), rect.height()

    def apply_selected_text_content_alignment(self, align: str) -> dict[tuple, dict]:
        """Set paragraph alignment inside selected text boxes."""
        align = align if align in {"left", "center", "right"} else "left"
        items = self.selected_text_items()
        if not items:
            return {}
        self._notify_state_about_to_change()
        built_in_styles: dict[tuple, dict] = {}
        for item in items:
            if isinstance(item, EditableTextItem):
                item.set_text_align(align)
                font = item.font()
                built_in_styles[item.source_ref.key()] = {
                    "font_family": font.family(),
                    "font_size_pt": font.pointSizeF(),
                    "bold": font.bold(),
                    "italic": font.italic(),
                    "underline": font.underline(),
                    "align": align,
                    "rotation": item.rotation(),
                }
            elif isinstance(item, _OverlayTextItem):
                item.set_text_align(align)
        return built_in_styles

    def _text_item_to_overlay_data(self, item: QGraphicsTextItem) -> dict:
        font = item.font()
        if isinstance(item, _OverlayTextItem):
            data = item.to_json()
            data["x"] = item.scenePos().x()
            data["y"] = item.scenePos().y()
            return data
        rect = item.sceneBoundingRect()
        align = item.text_align() if isinstance(item, EditableTextItem) else "left"
        return {
            "type": _OverlayTextItem.TypeName,
            "text": item.toPlainText(),
            "x": rect.left(),
            "y": rect.top(),
            "width": max(12.0, item.textWidth()),
            "height": max(8.0, rect.height()),
            "rotation": item.rotation(),
            "font_family": font.family(),
            "font_size": font.pointSizeF(),
            "bold": font.bold(),
            "italic": font.italic(),
            "underline": font.underline(),
            "text_align": align,
        }

    def _blot_frame_to_overlay_data(self, frame: ResizableBlotFrameItem) -> dict:
        key = frame.source_ref.key()
        layout_item = self._blot_layout_items.get(key)
        roi = dict(layout_item.image_crop_px or {}) if layout_item is not None else {}
        transform = dict(layout_item.image_transform or {}) if layout_item is not None else {}
        return {
            "type": _OverlayBlotItem.TypeName,
            "x": frame.pos().x(),
            "y": frame.pos().y(),
            "width": frame.rect().width(),
            "height": frame.rect().height(),
            "rotation": frame.rotation(),
            "image_path": layout_item.image_path if layout_item is not None else None,
            "roi": roi,
            "transform": transform,
        }

    def copy_selected_text_boxes(self) -> bool:
        copied: list[dict] = [
            self._text_item_to_overlay_data(item)
            for item in self.selected_text_items()
        ]
        copied.extend(
            item.to_json()
            for item in self._overlay_items
            if isinstance(item, _OverlayBlotItem) and item.isSelected()
        )
        copied.extend(
            self._blot_frame_to_overlay_data(frame)
            for key, frame in self._blot_frames.items()
            if key in self._selected_blot_keys
        )
        if not copied:
            return False
        self._copied_text_items_data = copied
        return True

    def paste_copied_text_boxes(self) -> bool:
        if not self._copied_text_items_data:
            return False
        self._notify_state_about_to_change()
        self._scene.clearSelection()
        pasted: list[QGraphicsItem] = []
        factories = {
            _OverlayTextItem.TypeName: _OverlayTextItem.from_json,
            _OverlayBlotItem.TypeName: _OverlayBlotItem.from_json,
        }
        for data in self._copied_text_items_data:
            clone_data = dict(data)
            clone_data["x"] = float(clone_data.get("x", 0.0)) + 12.0
            clone_data["y"] = float(clone_data.get("y", 0.0)) + 12.0
            factory = factories.get(clone_data.get("type"))
            if factory is None:
                continue
            item = factory(clone_data)
            self._scene.addItem(item)
            self._overlay_items.append(item)
            item.setSelected(True)
            pasted.append(item)
        self._copied_text_items_data = [
            item.to_json()
            for item in pasted
            if hasattr(item, "to_json")
        ]
        return bool(pasted)

    def overlay_items_as_json_data(self) -> list[dict]:
        """Return the overlay items serialized as JSON-ready dicts (for template saving)."""
        result: list[dict] = []
        for item in self._overlay_items:
            if not hasattr(item, "to_json"):
                continue
            if isinstance(item, _OverlayTextItem) and not item.toPlainText().strip():
                continue
            result.append(item.to_json())
        return result

    def _restore_overlay_from_data(
        self,
        items_data: list[dict],
        *,
        add_to_scene: bool = True,
    ) -> None:
        """Restore overlay items from deserialized JSON data (used by template loading)."""
        with QSignalBlocker(self._scene):
            self._scene.clearFocus()
            self._scene.clearSelection()
            # Clear undo stack FIRST — commands hold item refs; freeing commands before
            # items prevents PySide6 from trying to call methods on partially-freed wrappers.
            self._scene.undo_stack.clear()
            for item in self._overlay_items:
                if item.scene() is self._scene:
                    self._scene.removeItem(item)
            self._overlay_items.clear()

            factories = {
                _OverlayBlotItem.TypeName: _OverlayBlotItem.from_json,
                _OverlayTextItem.TypeName: _OverlayTextItem.from_json,
                _OverlayLineItem.TypeName: _OverlayLineItem.from_json,
            }
            for item_data in items_data:
                factory = factories.get(item_data.get("type"))
                if factory is None:
                    continue
                item = factory(item_data)
                if add_to_scene:
                    self._scene.addItem(item)
                self._overlay_items.append(item)

    def overlay_as_layout_items(self) -> list[LayoutItem]:
        """Convert overlay items to LayoutItem objects for PDF/PPTX export."""
        result: list[LayoutItem] = []
        z = 200
        for item in self._overlay_items:
            if isinstance(item, _OverlayTextItem):
                if not item.toPlainText().strip():
                    continue
                pos = item.pos()
                result.append(LayoutItem(
                    kind="label",
                    x_pt=scene_to_pt(pos.x()),
                    y_pt=scene_to_pt(pos.y()),
                    w_pt=scene_to_pt(item.textWidth()),
                    h_pt=scene_to_pt(item._box_height),
                    text=item.toPlainText(),
                    font_family=item.font().family(),
                    font_size_pt=item.font().pointSizeF(),
                    bold=item.font().bold(),
                    italic=item.font().italic(),
                    underline=item.font().underline(),
                    align=item.text_align(),
                    rotation=item.rotation(),
                    z_order=z,
                ))
            elif isinstance(item, _OverlayBlotItem):
                pos = item.pos()
                rect = item.rect()
                result.append(LayoutItem(
                    kind="blot",
                    x_pt=scene_to_pt(pos.x()),
                    y_pt=scene_to_pt(pos.y()),
                    w_pt=scene_to_pt(rect.width()),
                    h_pt=scene_to_pt(rect.height()),
                    image_path=item.image_path or None,
                    image_crop_px=item.roi or None,
                    image_transform=item.transform or None,
                    z_order=z,
                ))
            elif isinstance(item, _OverlayLineItem):
                line = item.line()
                p1 = item.mapToScene(line.p1())
                p2 = item.mapToScene(line.p2())
                pen = item.pen()
                result.append(LayoutItem(
                    kind="line",
                    x_pt=scene_to_pt(p1.x()),
                    y_pt=scene_to_pt(p1.y()),
                    w_pt=scene_to_pt(p2.x() - p1.x()),
                    h_pt=scene_to_pt(p2.y() - p1.y()),
                    line_color=pen.color().name(),
                    line_width_pt=scene_to_pt(pen.widthF()),
                    z_order=z,
                ))
            z += 1
        return result

    def adjusted_layout_items_for_export(self, items: list[LayoutItem]) -> list[LayoutItem]:
        """Return layout items with current on-canvas fine-position offsets applied."""
        self._sync_text_offsets_from_items()
        adjusted: list[LayoutItem] = []
        for item in items:
            out = replace(item)
            if item.source_ref is not None:
                key = item.source_ref.key()
                if item.kind in {"label", "mw", "title", "panel_letter", "table_cell"}:
                    text_item = self._text_items.get(key)
                    if text_item is not None:
                        out.x_pt = scene_to_pt(text_item.pos().x())
                        out.y_pt = scene_to_pt(text_item.pos().y())
                        rect = text_item.editor_rect()
                        out.w_pt = scene_to_pt(rect.width())
                        out.h_pt = scene_to_pt(rect.height())
                        out.rotation = text_item.rotation()
                    else:
                        offset = self._offsets.get(key)
                        if offset is not None:
                            out.x_pt += scene_to_pt(offset.x())
                            out.y_pt += scene_to_pt(offset.y())
                        size = self._text_box_sizes.get(key)
                        if size is not None:
                            out.w_pt = scene_to_pt(size[0])
                            out.h_pt = scene_to_pt(size[1])
                elif item.kind == "blot":
                    offset = self._blot_offsets.get(key)
                    if offset is not None:
                        out.x_pt += scene_to_pt(offset.x())
                        out.y_pt += scene_to_pt(offset.y())
            adjusted.append(out)
        return adjusted

    # ── Zoom / pan ────────────────────────────────────────────────────────

    def zoom_in(self) -> None:
        self.scale(1.12, 1.12)

    def zoom_out(self) -> None:
        self.scale(1.0 / 1.12, 1.0 / 1.12)

    def reset_zoom(self) -> None:
        self.fit_to_view()

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self.on_view_interacted is not None:
            self.on_view_interacted()
        factor = 1.12 if event.angleDelta().y() > 0 else 1.0 / 1.12
        self.scale(factor, factor)

    def enter_place_mode(self, kind: str) -> None:
        """Switch to placement mode: next left-click places a 'text' or 'line' item."""
        self._pending_place = kind
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)

    def mousePressEvent(self, event) -> None:
        # ── Placement mode: drop item at clicked scene position ───────────
        if self._pending_place and event.button() == Qt.MouseButton.LeftButton:
            kind = self._pending_place
            self._pending_place = None
            self.unsetCursor()
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
            sp = self.mapToScene(event.pos())
            self._notify_state_about_to_change()
            if kind == "text":
                item = _OverlayTextItem(
                    "Text",
                    QRectF(
                        sp.x() - DEFAULT_OVERLAY_TEXT_W / 2.0,
                        sp.y() - DEFAULT_OVERLAY_TEXT_H / 2.0,
                        DEFAULT_OVERLAY_TEXT_W,
                        DEFAULT_OVERLAY_TEXT_H,
                    ),
                )
            else:
                item = _OverlayLineItem(QLineF(sp.x() - 60, sp.y(), sp.x() + 60, sp.y()))
            self._scene.addItem(item)
            self._overlay_items.append(item)
            self._scene.clearSelection()
            item.setSelected(True)
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            event.accept()
            return

        if self.on_view_interacted is not None:
            self.on_view_interacted()
        if event.button() == Qt.MouseButton.MiddleButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self._panning = True
            self._pan_start = event.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            scene_pos = self.mapToScene(event.pos())
            hit_items = [
                item for item in self._scene.items(scene_pos)
                if item is not self._background_item
            ]
            if not hit_items:
                self._clear_text_editing_state()
                self._clear_blot_selection()
                self._scene.clearSelection()
                # Start rubber-band drag on empty space
                self._rubberband_origin = QPoint(event.pos())
                if self._rubberband is None:
                    self._rubberband = QRubberBand(QRubberBand.Shape.Rectangle, self.viewport())
                self._rubberband.setGeometry(QRect(self._rubberband_origin, QSize()))
                self._rubberband.show()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
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
        if self._rubberband is not None and self._rubberband_origin is not None:
            self._rubberband.setGeometry(
                QRect(self._rubberband_origin, event.pos()).normalized()
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = False
            self.unsetCursor()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._rubberband is not None:
            rb_geom = self._rubberband.geometry()
            self._rubberband.hide()
            self._rubberband_origin = None
            if rb_geom.width() > 4 or rb_geom.height() > 4:
                # Convert viewport rect to scene rect and select items within it
                top_left_s = self.mapToScene(rb_geom.topLeft())
                bot_right_s = self.mapToScene(rb_geom.bottomRight())
                scene_rect = QRectF(top_left_s, bot_right_s)
                for item in self._scene.items(
                    scene_rect, Qt.ItemSelectionMode.IntersectsItemShape
                ):
                    if (
                        item is not self._background_item
                        and item.parentItem() is None
                        and bool(item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
                    ):
                        item.setSelected(True)
                self._sync_blot_selection_from_scene()
            self.selected_text_refs()
            event.accept()
            return
        super().mouseReleaseEvent(event)
        self._sync_blot_selection_from_scene()
        self.selected_text_refs()

    def _clear_text_editing_state(self) -> None:
        """Exit text editing and clear any in-text cursor selection."""
        focus_item = self._scene.focusItem()
        if isinstance(focus_item, QGraphicsTextItem):
            focus_item.clearFocus()
        self._scene.clearFocus()
        for item in self._scene.items():
            if not isinstance(item, QGraphicsTextItem):
                continue
            cursor = item.textCursor()
            if cursor.hasSelection():
                cursor.clearSelection()
                item.setTextCursor(cursor)
            if bool(item.textInteractionFlags() & Qt.TextInteractionFlag.TextEditorInteraction):
                item.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)

    def _has_active_text_editor(self) -> bool:
        focus_item = self._scene.focusItem()
        if (
            isinstance(focus_item, QGraphicsTextItem)
            and bool(focus_item.textInteractionFlags() & Qt.TextInteractionFlag.TextEditorInteraction)
        ):
            return True
        return any(
            isinstance(item, QGraphicsTextItem)
            and item.hasFocus()
            and bool(item.textInteractionFlags() & Qt.TextInteractionFlag.TextEditorInteraction)
            for item in self._scene.selectedItems()
        )

    def keyPressEvent(self, event) -> None:
        editing_text = self._has_active_text_editor()

        if event.matches(QKeySequence.StandardKey.Copy):
            if editing_text:
                super().keyPressEvent(event)
                return
            if self.copy_selected_text_boxes():
                event.accept()
                return

        if event.matches(QKeySequence.StandardKey.Paste):
            if editing_text:
                super().keyPressEvent(event)
                return
            if self.paste_copied_text_boxes():
                event.accept()
                return

        is_undo = False
        try:
            is_undo = event.matches(QKeySequence.StandardKey.Undo)
        except Exception:
            is_undo = False
        if (
            not is_undo
            and event.key() == Qt.Key.Key_Z
            and bool(
                event.modifiers()
                & (Qt.KeyboardModifier.MetaModifier | Qt.KeyboardModifier.ControlModifier)
            )
        ):
            is_undo = True
        if is_undo:
            if editing_text:
                super().keyPressEvent(event)
                return
            if self.on_undo_requested is not None:
                self.on_undo_requested()
                event.accept()
                return

        # Cancel placement mode
        if event.key() == Qt.Key.Key_Escape and self._pending_place:
            self._pending_place = None
            self.unsetCursor()
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
            event.accept()
            return

        # Delete selected overlay items
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if editing_text:
                super().keyPressEvent(event)
                return
            to_remove = [i for i in self._overlay_items if i.isSelected()]
            built_in_to_remove = [
                (key, item)
                for key, item in self._text_items.items()
                if item.isSelected()
            ]
            if to_remove or built_in_to_remove:
                self._notify_state_about_to_change()
                for i in to_remove:
                    self._scene.removeItem(i)
                    self._overlay_items.remove(i)
                for key, item in built_in_to_remove:
                    self._hidden_text_keys.add(key)
                    self._selected_text_keys.discard(key)
                    if self.on_text_edited is not None:
                        self.on_text_edited(item.source_ref, "")
                    self._scene.removeItem(item)
                    self._text_items.pop(key, None)
                event.accept()
                return

        key_deltas = {
            Qt.Key.Key_Left: QPointF(-1.0, 0.0),
            Qt.Key.Key_Right: QPointF(1.0, 0.0),
            Qt.Key.Key_Up: QPointF(0.0, -1.0),
            Qt.Key.Key_Down: QPointF(0.0, 1.0),
        }
        if event.key() in key_deltas:
            if editing_text:
                super().keyPressEvent(event)
                return
            delta = key_deltas[event.key()]
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                delta *= 5.0
            moved = False
            selected_overlay_texts = [
                item for item in self._overlay_items
                if isinstance(item, _OverlayTextItem) and item.isSelected()
            ]
            selected_overlay_lines = [
                item for item in self._overlay_items
                if isinstance(item, _OverlayLineItem) and item.isSelected()
            ]
            selected_overlay_blots = [
                item for item in self._overlay_items
                if isinstance(item, _OverlayBlotItem) and item.isSelected()
            ]
            changed = (
                any(item.isSelected() for item in self._text_items.values())
                or bool(self._selected_blot_keys)
                or bool(selected_overlay_texts)
                or bool(selected_overlay_lines)
                or bool(selected_overlay_blots)
            )
            if changed:
                self._notify_state_about_to_change()
            # Move selected WB text items (existing behaviour)
            for item in self._text_items.values():
                if item.isSelected():
                    item.setPos(item.pos() + delta)
                    self._handle_text_position_changed(item)
                    moved = True
            # Move selected blot frames + their content with arrow keys
            for key in self._selected_blot_keys:
                self._move_blot_frame_by_delta(key, delta)
                moved = True
            # Move selected user-added text boxes with arrow keys
            for item in selected_overlay_texts:
                item.setPos(item.pos() + delta)
                moved = True
            # Move selected user-added lines with arrow keys
            for item in selected_overlay_lines:
                item.setPos(item.pos() + delta)
                moved = True
            for item in selected_overlay_blots:
                item.setPos(item.pos() + delta)
                moved = True
            if moved:
                event.accept()
                return
        super().keyPressEvent(event)
