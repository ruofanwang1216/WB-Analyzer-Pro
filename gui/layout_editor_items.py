"""Reusable QGraphicsItems for the WB layout editor."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QLineF
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QTextBlockFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsRectItem,
    QGraphicsSimpleTextItem,
    QGraphicsTextItem,
)
# Direct reference to avoid MRO ambiguity in mousePressEvent bypass
_QGraphicsItemBase = QGraphicsItem

from core.export_engine import _crop_qimage
from gui.layout_editor_commands import ResizeItemCommand


_HANDLE_SIZE = 8.0
_MIN_WIDTH = 12.0
_MIN_HEIGHT = 8.0
_HANDLE_IDS = (
    "top_left",
    "top",
    "top_right",
    "right",
    "bottom_right",
    "bottom",
    "bottom_left",
    "left",
)


class ResizeHandleItem(QGraphicsRectItem):
    """Small draggable square used by resizable editor items."""

    def __init__(self, owner: "ResizableItemMixin", handle_id: str) -> None:
        half = _HANDLE_SIZE / 2.0
        super().__init__(-half, -half, _HANDLE_SIZE, _HANDLE_SIZE, owner)  # type: ignore[arg-type]
        self.owner = owner
        self.handle_id = handle_id
        self.setBrush(QBrush(QColor("#FFFFFF")))
        self.setPen(QPen(QColor("#2A5E48"), 1.0))
        self.setZValue(1000)
        self.setVisible(False)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setCursor(self._cursor_for_handle(handle_id))

    @staticmethod
    def _cursor_for_handle(handle_id: str) -> Qt.CursorShape:
        if handle_id in {"top_left", "bottom_right"}:
            return Qt.CursorShape.SizeFDiagCursor
        if handle_id in {"top_right", "bottom_left"}:
            return Qt.CursorShape.SizeBDiagCursor
        if handle_id in {"left", "right"}:
            return Qt.CursorShape.SizeHorCursor
        if handle_id in {"top", "bottom"}:
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.ArrowCursor

    def mousePressEvent(self, event) -> None:
        self.owner.begin_resize(self.handle_id)
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        local_pos = self.owner.mapFromScene(event.scenePos())  # type: ignore[attr-defined]
        self.owner.resize_from_handle(self.handle_id, local_pos)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self.owner.finish_resize()
        event.accept()


class RotationHandleItem(QGraphicsEllipseItem):
    """PPT-style circular handle used to rotate a text box with the mouse."""

    _RADIUS = 6.0

    def __init__(self, owner: "EditableTextItem") -> None:
        radius = self._RADIUS
        super().__init__(-radius, -radius, radius * 2.0, radius * 2.0, owner)
        self.owner = owner
        self.setBrush(QBrush(QColor("#FFFFFF")))
        self.setPen(QPen(QColor("#66736D"), 1.0))
        self.setZValue(1002)
        self.setVisible(False)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        super().paint(painter, option, widget)
        painter.save()
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(QPen(QColor("#52615A"), 1.15))
            arc_rect = self.rect().adjusted(3.0, 3.0, -3.0, -3.0)
            painter.drawArc(arc_rect, 35 * 16, 285 * 16)
            tip = QPointF(arc_rect.right() - 0.2, arc_rect.top() + 0.8)
            painter.drawLine(tip, tip + QPointF(-2.4, -0.2))
            painter.drawLine(tip, tip + QPointF(-0.5, 2.2))
        finally:
            painter.restore()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.owner.begin_rotation()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if bool(event.buttons() & Qt.MouseButton.LeftButton):
            self.owner.rotate_from_scene_pos(event.scenePos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.owner.rotate_from_scene_pos(event.scenePos())
            self.owner.finish_rotation()
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class RotationAngleLabelItem(QGraphicsSimpleTextItem):
    """Upright live-angle label displayed to the right of the rotate handle."""

    def __init__(self, owner: "EditableTextItem") -> None:
        super().__init__("0°", owner)
        font = QFont("Arial")
        font.setPointSizeF(8.0)
        font.setBold(True)
        self.setFont(font)
        self.setBrush(QBrush(QColor("#2F3C36")))
        self.setZValue(1003)
        self.setVisible(False)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setFlag(
            QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations,
            True,
        )

    def boundingRect(self) -> QRectF:
        return super().boundingRect().adjusted(-3.0, -1.5, 3.0, 1.5)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.save()
        try:
            painter.setPen(QPen(QColor("#CAD5D0"), 0.8))
            painter.setBrush(QBrush(QColor(255, 255, 255, 235)))
            painter.drawRoundedRect(self.boundingRect(), 3.0, 3.0)
        finally:
            painter.restore()
        super().paint(painter, option, widget)


class ResizableItemMixin:
    """Mixin for rectangular items with 8 PowerPoint-style handles."""

    def init_resize_handles(self) -> None:
        self._resize_handles = {
            handle_id: ResizeHandleItem(self, handle_id) for handle_id in _HANDLE_IDS
        }
        self._resize_start_geometry: dict | None = None
        self.update_resize_handles()

    def editor_geometry(self) -> dict:
        rect = self.editor_rect()
        return {
            "pos": QPointF(self.pos()),  # type: ignore[attr-defined]
            "rect": QRectF(rect),
        }

    def set_editor_geometry(self, geometry: dict) -> None:
        self.setPos(QPointF(geometry["pos"]))  # type: ignore[attr-defined]
        rect = QRectF(geometry["rect"])
        self.resize_to_local_size(rect.width(), rect.height())
        self.update_resize_handles()

    def editor_rect(self) -> QRectF:
        raise NotImplementedError

    def resize_to_local_size(self, width: float, height: float) -> None:
        raise NotImplementedError

    def handle_item_change(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.set_resize_handles_visible(bool(value))
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.update_resize_handles()
        return value

    def set_resize_handles_visible(self, visible: bool) -> None:
        for handle in getattr(self, "_resize_handles", {}).values():
            handle.setVisible(visible)

    def update_resize_handles(self) -> None:
        handles = getattr(self, "_resize_handles", {})
        if not handles:
            return
        rect = self.editor_rect()
        points = {
            "top_left": rect.topLeft(),
            "top": QPointF(rect.center().x(), rect.top()),
            "top_right": rect.topRight(),
            "right": QPointF(rect.right(), rect.center().y()),
            "bottom_right": rect.bottomRight(),
            "bottom": QPointF(rect.center().x(), rect.bottom()),
            "bottom_left": rect.bottomLeft(),
            "left": QPointF(rect.left(), rect.center().y()),
        }
        for handle_id, handle in handles.items():
            handle.setPos(points[handle_id])

    def begin_resize(self, _handle_id: str) -> None:
        scene = self.scene()  # type: ignore[attr-defined]
        cb = getattr(scene, "record_state_before_change", None)
        if cb is not None:
            cb()
        self._resize_start_geometry = self.editor_geometry()

    def resize_from_handle(self, handle_id: str, local_pos: QPointF) -> None:
        if self._resize_start_geometry is None:
            return
        start_rect = QRectF(self._resize_start_geometry["rect"])
        start_pos = QPointF(self._resize_start_geometry["pos"])
        rect = QRectF(start_rect)

        if "left" in handle_id:
            rect.setLeft(local_pos.x())
        if "right" in handle_id:
            rect.setRight(local_pos.x())
        if "top" in handle_id:
            rect.setTop(local_pos.y())
        if "bottom" in handle_id:
            rect.setBottom(local_pos.y())

        rect = rect.normalized()
        if rect.width() < _MIN_WIDTH:
            rect.setWidth(_MIN_WIDTH)
        if rect.height() < _MIN_HEIGHT:
            rect.setHeight(_MIN_HEIGHT)

        delta = rect.topLeft() - start_rect.topLeft()
        self.setPos(start_pos + delta)  # type: ignore[attr-defined]
        self.resize_to_local_size(rect.width(), rect.height())
        self.update_resize_handles()

    def finish_resize(self) -> None:
        old_geometry = self._resize_start_geometry
        self._resize_start_geometry = None
        if old_geometry is None:
            return
        new_geometry = self.editor_geometry()
        if (
            old_geometry["pos"] == new_geometry["pos"]
            and old_geometry["rect"] == new_geometry["rect"]
        ):
            return
        scene = self.scene()  # type: ignore[attr-defined]
        if scene is not None and hasattr(scene, "undo_stack"):
            scene.undo_stack.push(ResizeItemCommand(self, old_geometry, new_geometry))


class EditableTextItem(ResizableItemMixin, QGraphicsTextItem):
    """Editable text box with wrapping, font settings and resize handles."""

    TypeName = "text"
    # Render text once at high resolution and rotate that stable glyph layer.
    # Letting QPainter rasterize glyphs after applying an arbitrary rotation
    # switches antialiasing modes on macOS and makes identical fonts look thin.
    _TEXT_LAYER_SCALE = 4.0
    _DRAG_THRESHOLD = 2.0
    _ROTATION_HANDLE_GAP = 20.0
    _ROTATION_SNAP_TOLERANCE = 4.0
    _CARDINAL_ANGLES = (0, 90, 180, 270)

    def __init__(
        self,
        text: str = "Text",
        rect: QRectF | None = None,
        *,
        font_family: str = "Arial",
        font_size: float = 12.0,
        bold: bool = False,
        italic: bool = False,
        underline: bool = False,
        text_align: str = "left",
    ) -> None:
        super().__init__(text)
        rect = rect or QRectF(0, 0, 160, 42)
        self.setPos(rect.topLeft())
        self._box_height = max(_MIN_HEIGHT, rect.height())
        font = QFont(font_family)
        font.setPointSizeF(font_size)
        font.setBold(bold)
        font.setItalic(italic)
        font.setUnderline(underline)
        self.setFont(font)
        self.setTextWidth(max(_MIN_WIDTH, rect.width()))
        self.setDefaultTextColor(QColor("#111111"))
        self._text_align = "left"
        self.set_text_align(text_align)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        # ItemIsMovable is intentionally omitted: QGraphicsTextItem's built-in
        # ItemIsMovable drag sets a wrong initial position on the second click,
        # causing the item to jump to the scene origin.  Drag is implemented
        # manually below so we control the anchor point precisely every press.
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self._old_text = text
        self._is_user_dragging = False
        self._drag_start_scene_pos: QPointF | None = None
        self._drag_start_item_pos: QPointF | None = None
        self._drag_group_start_positions: dict[QGraphicsTextItem, QPointF] = {}
        self._drag_threshold_crossed = False
        self._rotation_active = False
        self._rotation_start_angle = 0.0
        self._adaptive_rotation_mode: int | None = 0
        self._text_layer_cache_key: tuple | None = None
        self._text_layer_cache = QImage()
        self.setTransformOriginPoint(self.editor_rect().center())
        self.init_resize_handles()
        self._rotation_connector = QGraphicsLineItem(self)
        self._rotation_connector.setPen(QPen(QColor("#8A9690"), 1.0))
        self._rotation_connector.setZValue(1000)
        self._rotation_connector.setVisible(False)
        self._rotation_connector.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._rotation_handle = RotationHandleItem(self)
        self._rotation_angle_label = RotationAngleLabelItem(self)
        self.update_rotation_controls()
        self._live_text_resize_in_progress = False
        self.document().contentsChanged.connect(self._fit_width_during_edit)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        if self.textInteractionFlags() != Qt.TextInteractionFlag.NoTextInteraction:
            super().paint(painter, option, widget)
            return
        painter.save()
        try:
            flags = {
                "center": Qt.AlignmentFlag.AlignHCenter,
                "right": Qt.AlignmentFlag.AlignRight,
            }.get(self._text_align, Qt.AlignmentFlag.AlignLeft)
            flags |= Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap
            rect = self.editor_rect()
            painter.setOpacity(1.0)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawImage(rect, self._stable_text_layer(rect, flags))
        finally:
            painter.restore()

    def _stable_text_layer(self, rect: QRectF, flags) -> QImage:
        """Return one angle-independent glyph raster for consistent weight."""
        font = self.font()
        color = self.defaultTextColor()
        key = (
            self.toPlainText(),
            round(rect.width(), 4),
            round(rect.height(), 4),
            font.family(),
            font.pointSizeF(),
            font.weight(),
            font.bold(),
            font.italic(),
            font.underline(),
            font.strikeOut(),
            font.letterSpacingType(),
            font.letterSpacing(),
            font.wordSpacing(),
            repr(font.styleStrategy()),
            self._text_align,
            color.rgba(),
        )
        if key == self._text_layer_cache_key and not self._text_layer_cache.isNull():
            return self._text_layer_cache

        scale = self._TEXT_LAYER_SCALE
        width = max(1, int(math.ceil(rect.width() * scale)))
        height = max(1, int(math.ceil(rect.height() * scale)))
        layer = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        layer.fill(Qt.GlobalColor.transparent)
        layer_painter = QPainter(layer)
        try:
            layer_painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            layer_painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            layer_painter.scale(scale, scale)
            layer_painter.setFont(font)
            layer_painter.setPen(color)
            layer_painter.setOpacity(1.0)
            layer_painter.drawText(
                QRectF(0.0, 0.0, rect.width(), rect.height()),
                flags,
                self.toPlainText(),
            )
        finally:
            layer_painter.end()

        self._text_layer_cache_key = key
        self._text_layer_cache = layer
        return layer

    def mouseDoubleClickEvent(self, event) -> None:
        # A graphics-scene double click is preceded by a normal mouse press.
        # That press arms our manual drag implementation; if it remains armed,
        # tiny pointer jitter while the caret is active can move the whole text
        # box and interrupt typing. Enter editing from a clean drag state.
        self._is_user_dragging = False
        self._drag_start_scene_pos = None
        self._drag_start_item_pos = None
        self._drag_threshold_crossed = False
        self._drag_group_start_positions.clear()
        scene = self.scene()
        clear_guides = getattr(scene, "clear_smart_guides", None)
        if clear_guides is not None:
            clear_guides()
        self._old_text = self.toPlainText()
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        super().mouseDoubleClickEvent(event)

    def _fit_width_during_edit(self) -> None:
        """Grow or shrink to the current text immediately while it is edited."""
        if (
            self._live_text_resize_in_progress
            or self.textInteractionFlags()
            == Qt.TextInteractionFlag.NoTextInteraction
        ):
            return
        self._live_text_resize_in_progress = True
        try:
            # Live typing always keeps the left edge fixed so the box expands
            # horizontally to the right, independent of paragraph alignment.
            self.fit_width_to_text(preserve_anchor=False)
        finally:
            self._live_text_resize_in_progress = False

    def mousePressEvent(self, event) -> None:
        if (
            event.button() == Qt.MouseButton.LeftButton
            and event.modifiers()
            & (
                Qt.KeyboardModifier.MetaModifier
                | Qt.KeyboardModifier.ControlModifier
                | Qt.KeyboardModifier.ShiftModifier
            )
        ):
            self.setSelected(not self.isSelected())
            event.accept()
            return

        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.textInteractionFlags() == Qt.TextInteractionFlag.NoTextInteraction
        ):
            # Manual drag: record anchor on every press so second/later clicks
            # never jump.  Qt's built-in ItemIsMovable is NOT used.
            self._is_user_dragging = True
            self._drag_start_scene_pos = QPointF(event.scenePos())
            self._drag_start_item_pos = QPointF(self.pos())
            self._drag_threshold_crossed = False
            scene = self.scene()
            begin_guides = getattr(scene, "begin_smart_guides", None)
            if begin_guides is not None:
                begin_guides()
            cb = getattr(scene, "record_state_before_change", None)
            if cb is not None:
                cb()
            if not self.isSelected():
                if scene is not None:
                    scene.clearSelection()
                self.setSelected(True)
            self._drag_group_start_positions = {}
            if scene is not None:
                group_mover = getattr(scene, "move_group_item", None)
                for item in scene.selectedItems():
                    if (
                        item.parentItem() is None
                        and (
                            isinstance(item, QGraphicsTextItem)
                            or group_mover is not None
                        )
                    ):
                        self._drag_group_start_positions[item] = QPointF(item.pos())
            if not self._drag_group_start_positions:
                self._drag_group_start_positions = {self: QPointF(self.pos())}
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (
            self._is_user_dragging
            and self._drag_start_scene_pos is not None
            and self._drag_start_item_pos is not None
            and bool(event.buttons() & Qt.MouseButton.LeftButton)
        ):
            delta = event.scenePos() - self._drag_start_scene_pos
            if (
                self._drag_threshold_crossed
                or abs(delta.x()) >= self._DRAG_THRESHOLD
                or abs(delta.y()) >= self._DRAG_THRESHOLD
            ):
                self._drag_threshold_crossed = True
                self._move_drag_group(delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        was_dragging = self._is_user_dragging
        if (
            was_dragging
            and self._drag_threshold_crossed
            and self._drag_start_scene_pos is not None
        ):
            self._move_drag_group(event.scenePos() - self._drag_start_scene_pos)
        self._is_user_dragging = False
        self._drag_start_scene_pos = None
        self._drag_start_item_pos = None
        self._drag_threshold_crossed = False
        self._drag_group_start_positions.clear()
        scene = self.scene()
        clear_guides = getattr(scene, "clear_smart_guides", None)
        if clear_guides is not None:
            clear_guides()
        if was_dragging:
            event.accept()
            return
        super().mouseReleaseEvent(event)

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

    def focusOutEvent(self, event) -> None:
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        new_text = self.toPlainText()
        scene = self.scene()
        if scene is not None and hasattr(scene, "record_text_edit") and new_text != self._old_text:
            self.fit_width_to_text()
            scene.record_text_edit(self, self._old_text, new_text)
        super().focusOutEvent(event)

    def itemChange(self, change, value):
        self.handle_item_change(change, value)
        return super().itemChange(change, value)

    def set_resize_handles_visible(self, visible: bool) -> None:
        super().set_resize_handles_visible(visible)
        connector = getattr(self, "_rotation_connector", None)
        handle = getattr(self, "_rotation_handle", None)
        if connector is not None:
            connector.setVisible(visible)
        if handle is not None:
            handle.setVisible(visible)
        if not visible:
            label = getattr(self, "_rotation_angle_label", None)
            if label is not None:
                label.setVisible(False)

    def update_resize_handles(self) -> None:
        super().update_resize_handles()
        if hasattr(self, "_rotation_handle"):
            self.update_rotation_controls()

    def update_rotation_controls(self) -> None:
        """Keep the connector, handle, and live label above the text box."""
        if not hasattr(self, "_rotation_handle"):
            return
        rect = self.editor_rect()
        center_x = rect.center().x()
        handle_y = rect.top() - self._ROTATION_HANDLE_GAP
        self._rotation_handle.setPos(center_x, handle_y)
        self._rotation_connector.setLine(
            center_x,
            rect.top(),
            center_x,
            handle_y + RotationHandleItem._RADIUS,
        )
        self._rotation_angle_label.setPos(center_x + 10.0, handle_y - 6.0)

    @staticmethod
    def _normalized_rotation(angle: float) -> float:
        normalized = float(angle) % 360.0
        return 0.0 if abs(normalized - 360.0) < 0.001 else normalized

    @classmethod
    def _snap_rotation_angle(cls, angle: float) -> float:
        normalized = cls._normalized_rotation(angle)
        for cardinal in cls._CARDINAL_ANGLES:
            distance = abs((normalized - cardinal + 180.0) % 360.0 - 180.0)
            if distance <= cls._ROTATION_SNAP_TOLERANCE:
                return float(cardinal)
        return normalized

    def setRotation(self, angle: float) -> None:
        """Rotate around the box centre and track cardinal adaptive modes."""
        self.setTransformOriginPoint(self.editor_rect().center())
        super().setRotation(float(angle))
        normalized = self._normalized_rotation(angle)
        self._adaptive_rotation_mode = next(
            (
                cardinal
                for cardinal in self._CARDINAL_ANGLES
                if abs((normalized - cardinal + 180.0) % 360.0 - 180.0) < 0.01
            ),
            None,
        )
        self.update_rotation_controls()

    def adaptive_rotation_mode(self) -> int | None:
        """Return the active 0/90/180/270-degree snap mode, if any."""
        return self._adaptive_rotation_mode

    def begin_rotation(self) -> None:
        scene = self.scene()
        cb = getattr(scene, "record_state_before_change", None)
        if cb is not None:
            cb()
        self._rotation_active = True
        self._rotation_start_angle = self._normalized_rotation(self.rotation())
        self._update_rotation_angle_label(self._rotation_start_angle)
        self._rotation_angle_label.setVisible(True)

    def rotate_from_scene_pos(self, scene_pos: QPointF) -> float:
        """Rotate toward a scene point, snapping near the four cardinal angles."""
        center = self.mapToScene(self.editor_rect().center())
        delta = QPointF(scene_pos) - center
        if abs(delta.x()) < 0.001 and abs(delta.y()) < 0.001:
            return self._normalized_rotation(self.rotation())
        raw_angle = math.degrees(math.atan2(delta.x(), -delta.y()))
        angle = self._snap_rotation_angle(raw_angle)
        self.setRotation(angle)
        self._update_rotation_angle_label(angle)
        self._rotation_angle_label.setVisible(True)
        self._notify_rotation_changed(final=False)
        return angle

    def finish_rotation(self) -> None:
        if not self._rotation_active:
            return
        self._rotation_active = False
        self._rotation_angle_label.setVisible(False)
        self._notify_rotation_changed(final=True)

    def _update_rotation_angle_label(self, angle: float) -> None:
        normalized = self._normalized_rotation(angle)
        rounded = round(normalized, 1)
        if abs(rounded - round(rounded)) < 0.05:
            text = f"{int(round(rounded))}°"
        else:
            text = f"{rounded:.1f}°"
        self._rotation_angle_label.setText(text)
        self.update_rotation_controls()

    def _notify_rotation_changed(self, *, final: bool) -> None:
        scene = self.scene()
        callback = getattr(scene, "text_rotation_changed", None)
        if callback is not None:
            callback(self, final)

    def editor_rect(self) -> QRectF:
        return QRectF(0.0, 0.0, max(_MIN_WIDTH, self.textWidth()), self._box_height)

    def resize_to_local_size(self, width: float, height: float) -> None:
        self.prepareGeometryChange()
        self.setTextWidth(max(_MIN_WIDTH, width))
        self._box_height = max(_MIN_HEIGHT, height)
        self.setTransformOriginPoint(self.editor_rect().center())
        self.update_rotation_controls()

    def natural_text_width(self, horizontal_padding: float | None = None) -> float:
        """Return a compact single-line width for the current text and font."""
        if horizontal_padding is None:
            # QTextDocument reserves a margin on both sides while editing.
            # Include it so the last word never wraps just before auto-fit.
            horizontal_padding = self.document().documentMargin() * 2.0 + 2.0
        metrics = QFontMetricsF(self.font())
        lines = self.toPlainText().splitlines() or [""]
        text_width = max(
            max(
                metrics.horizontalAdvance(line),
                metrics.boundingRect(line).width(),
            )
            for line in lines
        )
        return max(_MIN_WIDTH, text_width + max(0.0, horizontal_padding))

    def fit_width_to_text(self, *, preserve_anchor: bool = True) -> float:
        """Shrink/expand horizontally to the text while keeping its alignment anchor."""
        old_width = self.editor_rect().width()
        new_width = self.natural_text_width()
        if abs(old_width - new_width) < 0.01:
            return new_width

        shift_x = 0.0
        if preserve_anchor:
            if self._text_align == "center":
                shift_x = (old_width - new_width) / 2.0
            elif self._text_align == "right":
                shift_x = old_width - new_width

        self.prepareGeometryChange()
        self.setTextWidth(new_width)
        self.setTransformOriginPoint(self.editor_rect().center())
        if abs(shift_x) >= 0.01:
            self.setPos(self.pos() + QPointF(shift_x, 0.0))
        self.update_resize_handles()
        return new_width

    def set_text_align(self, align: str) -> None:
        align = align if align in {"left", "center", "right"} else "left"
        self._text_align = align
        block_format = QTextBlockFormat()
        block_format.setAlignment({
            "center": Qt.AlignmentFlag.AlignHCenter,
            "right": Qt.AlignmentFlag.AlignRight,
        }.get(align, Qt.AlignmentFlag.AlignLeft))
        cursor = QTextCursor(self.document())
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.mergeBlockFormat(block_format)

    def text_align(self) -> str:
        return self._text_align

    def to_json(self) -> dict:
        font = self.font()
        return {
            "type": self.TypeName,
            "text": self.toPlainText(),
            "x": self.pos().x(),
            "y": self.pos().y(),
            "width": self.textWidth(),
            "height": self._box_height,
            "rotation": self.rotation(),
            "font_family": font.family(),
            "font_size": font.pointSizeF(),
            "bold": font.bold(),
            "italic": font.italic(),
            "underline": font.underline(),
            "text_align": self._text_align,
        }

    @classmethod
    def from_json(cls, data: dict) -> "EditableTextItem":
        item = cls(
            str(data.get("text", "")),
            QRectF(
                float(data.get("x", 0.0)),
                float(data.get("y", 0.0)),
                float(data.get("width", 160.0)),
                float(data.get("height", 42.0)),
            ),
            font_family=str(data.get("font_family", "Arial")),
            font_size=float(data.get("font_size", 12.0)),
            bold=bool(data.get("bold", False)),
            italic=bool(data.get("italic", False)),
            underline=bool(data.get("underline", False)),
            text_align=str(data.get("text_align", "left")),
        )
        item.setRotation(float(data.get("rotation", 0.0)))
        return item


class LineEndpointHandle(QGraphicsEllipseItem):
    """Round handle for a line endpoint."""

    def __init__(self, owner: "LineElementItem", endpoint: str) -> None:
        super().__init__(-5, -5, 10, 10, owner)
        self.owner = owner
        self.endpoint = endpoint
        self.setBrush(QBrush(QColor("#FFFFFF")))
        self.setPen(QPen(QColor("#2A5E48"), 1.0))
        self.setZValue(1000)
        self.setVisible(False)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setCursor(Qt.CursorShape.SizeAllCursor)

    def mousePressEvent(self, event) -> None:
        self.owner.begin_endpoint_drag()
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        self.owner.update_endpoint(self.endpoint, self.owner.mapFromScene(event.scenePos()))
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self.owner.end_endpoint_drag()
        event.accept()

    def mouseUngrabEvent(self, event) -> None:
        self.owner.end_endpoint_drag()
        super().mouseUngrabEvent(event)


class LineElementItem(QGraphicsLineItem):
    """Editable line with two draggable endpoint handles."""

    TypeName = "line"
    _MIN_HIT_WIDTH = 16.0

    def __init__(self, line: QLineF | None = None, *, dashed: bool = False) -> None:
        super().__init__(line or QLineF(0, 0, 120, 0))
        self.dashed = dashed
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        pen = QPen(QColor("#222222"), 1.5)
        if dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        self.setPen(pen)
        self.start_handle = LineEndpointHandle(self, "start")
        self.end_handle = LineEndpointHandle(self, "end")
        self._angle_label = QGraphicsSimpleTextItem(self)
        self._angle_label.setBrush(QBrush(QColor("#2A5E48")))
        angle_font = QFont("Arial")
        angle_font.setPointSizeF(8.0)
        angle_font.setBold(True)
        self._angle_label.setFont(angle_font)
        self._angle_label.setZValue(1001)
        self._angle_label.setVisible(False)
        self._endpoint_drag_active = False
        self._is_user_dragging = False
        self._drag_start_scene_pos: QPointF | None = None
        self._drag_group_start_positions: dict[QGraphicsItem, QPointF] = {}
        self.update_handles()

    def shape(self) -> QPainterPath:
        """Use a generous invisible hit target while keeping the line thin."""
        path = QPainterPath()
        path.moveTo(self.line().p1())
        path.lineTo(self.line().p2())
        stroker = QPainterPathStroker()
        stroker.setWidth(max(self._MIN_HIT_WIDTH, self.pen().widthF()))
        stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
        return stroker.createStroke(path)

    def boundingRect(self) -> QRectF:
        return super().boundingRect().united(self.shape().boundingRect())

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            visible = bool(value)
            self.start_handle.setVisible(visible)
            self.end_handle.setVisible(visible)
            if not visible:
                self.end_endpoint_drag()
        return super().itemChange(change, value)

    def mousePressEvent(self, event) -> None:
        if (
            event.button() == Qt.MouseButton.LeftButton
            and event.modifiers()
            & (
                Qt.KeyboardModifier.MetaModifier
                | Qt.KeyboardModifier.ControlModifier
                | Qt.KeyboardModifier.ShiftModifier
            )
        ):
            self.setSelected(not self.isSelected())
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_user_dragging = True
            self._drag_start_scene_pos = QPointF(event.scenePos())
            scene = self.scene()
            begin_guides = getattr(scene, "begin_smart_guides", None)
            if begin_guides is not None:
                begin_guides()
            cb = getattr(scene, "record_state_before_change", None)
            if cb is not None:
                cb()
            if not self.isSelected():
                if scene is not None:
                    scene.clearSelection()
                self.setSelected(True)
            self._drag_group_start_positions = {}
            if scene is not None:
                for item in scene.selectedItems():
                    if item.parentItem() is None:
                        self._drag_group_start_positions[item] = QPointF(item.pos())
            if not self._drag_group_start_positions:
                self._drag_group_start_positions = {self: QPointF(self.pos())}
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (
            self._is_user_dragging
            and self._drag_start_scene_pos is not None
            and bool(event.buttons() & Qt.MouseButton.LeftButton)
        ):
            self._move_drag_group(event.scenePos() - self._drag_start_scene_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        was_dragging = self._is_user_dragging
        if was_dragging and self._drag_start_scene_pos is not None:
            self._move_drag_group(event.scenePos() - self._drag_start_scene_pos)
        self._is_user_dragging = False
        self._drag_start_scene_pos = None
        self._drag_group_start_positions.clear()
        scene = self.scene()
        clear_guides = getattr(scene, "clear_smart_guides", None)
        if clear_guides is not None:
            clear_guides()
        if was_dragging:
            event.accept()
            return
        super().mouseReleaseEvent(event)

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

    def update_endpoint(self, endpoint: str, local_pos: QPointF) -> None:
        line = QLineF(self.line())
        if endpoint == "start":
            line.setP1(local_pos)
        else:
            line.setP2(local_pos)
        self.setLine(line)
        self.update_handles()
        self.update_angle_label()

    def update_handles(self) -> None:
        line = self.line()
        self.start_handle.setPos(line.p1())
        self.end_handle.setPos(line.p2())

    def begin_endpoint_drag(self) -> None:
        scene = self.scene()
        if scene is not None and hasattr(scene, "record_state_before_change"):
            scene.record_state_before_change()
        self._endpoint_drag_active = True
        self.update_angle_label()
        self._angle_label.setVisible(True)

    def end_endpoint_drag(self) -> None:
        self._endpoint_drag_active = False
        self._angle_label.setVisible(False)

    def update_angle_label(self) -> None:
        line = self.line()
        dx = line.dx()
        dy = line.dy()
        if abs(dx) < 0.001 and abs(dy) < 0.001:
            angle = 0.0
        else:
            angle = math.degrees(math.atan2(dy, dx))
        self._angle_label.setText(f"{angle:.0f} deg")
        midpoint = QPointF(
            (line.x1() + line.x2()) / 2.0,
            (line.y1() + line.y2()) / 2.0,
        )
        self._angle_label.setPos(midpoint + QPointF(8.0, -18.0))
        self._angle_label.setVisible(self._endpoint_drag_active)

    def to_json(self) -> dict:
        line = self.line()
        return {
            "type": self.TypeName,
            "x": self.pos().x(),
            "y": self.pos().y(),
            "x1": line.x1(),
            "y1": line.y1(),
            "x2": line.x2(),
            "y2": line.y2(),
            "rotation": self.rotation(),
            "dashed": self.dashed,
        }

    @classmethod
    def from_json(cls, data: dict) -> "LineElementItem":
        item = cls(
            QLineF(
                float(data.get("x1", 0.0)),
                float(data.get("y1", 0.0)),
                float(data.get("x2", 120.0)),
                float(data.get("y2", 0.0)),
            ),
            dashed=bool(data.get("dashed", False)),
        )
        item.setPos(float(data.get("x", 0.0)), float(data.get("y", 0.0)))
        item.setRotation(float(data.get("rotation", 0.0)))
        item.update_handles()
        return item


class BlotPlaceholderItem(ResizableItemMixin, QGraphicsRectItem):
    """WB blot placeholder that can paint either a grey box or an ROI crop."""

    TypeName = "blot"

    def __init__(
        self,
        rect: QRectF,
        *,
        lane_count: int = 4,
        image_path: str | None = None,
        roi: dict[str, float] | None = None,
        transform: dict[str, Any] | None = None,
        geometry_transform: dict[str, Any] | None = None,
        preserve_aspect: bool = False,
    ) -> None:
        super().__init__(QRectF(0, 0, rect.width(), rect.height()))
        self.setPos(rect.topLeft())
        self.lane_count = max(1, int(lane_count))
        self.image_path = image_path
        self.roi = dict(roi or {})
        self.transform = dict(transform or {})
        self.geometry_transform = dict(geometry_transform or {})
        self.preserve_aspect = bool(preserve_aspect)
        self._preview_buffer: bytes | None = None
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setPen(QPen(QColor("#000000"), 1.0, Qt.PenStyle.SolidLine))
        self.setBrush(QBrush(QColor("#D8D8D8")))
        self._is_user_dragging = False
        self._drag_start_scene_pos: QPointF | None = None
        self._drag_group_start_positions: dict[QGraphicsItem, QPointF] = {}
        self.init_resize_handles()

    def _frame_pen(self) -> QPen:
        if self.isSelected():
            return QPen(QColor("#B96F73"), 3.0, Qt.PenStyle.SolidLine)
        return QPen(QColor("#000000"), 1.0, Qt.PenStyle.SolidLine)

    def itemChange(self, change, value):
        self.handle_item_change(change, value)
        result = super().itemChange(change, value)
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.setPen(self._frame_pen())
            self.update()
        return result

    def editor_rect(self) -> QRectF:
        return self.rect()

    def resize_to_local_size(self, width: float, height: float) -> None:
        self.setRect(QRectF(0, 0, max(_MIN_WIDTH, width), max(_MIN_HEIGHT, height)))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_user_dragging = True
            self._drag_start_scene_pos = QPointF(event.scenePos())
            scene = self.scene()
            begin_guides = getattr(scene, "begin_smart_guides", None)
            if begin_guides is not None:
                begin_guides()
            cb = getattr(scene, "record_state_before_change", None)
            if cb is not None:
                cb()
            additive = bool(
                event.modifiers()
                & (
                    Qt.KeyboardModifier.MetaModifier
                    | Qt.KeyboardModifier.ControlModifier
                    | Qt.KeyboardModifier.ShiftModifier
                )
            )
            selector = getattr(scene, "select_overlay_blot_item", None) if scene is not None else None
            if selector is not None:
                selector(self, additive)
            elif additive:
                self.setSelected(not self.isSelected())
            elif not self.isSelected():
                if scene is not None:
                    scene.clearSelection()
                self.setSelected(True)
            self._drag_group_start_positions = {}
            if scene is not None:
                for item in scene.selectedItems():
                    if item.parentItem() is None:
                        self._drag_group_start_positions[item] = QPointF(item.pos())
            if not self._drag_group_start_positions:
                self._drag_group_start_positions = {self: QPointF(self.pos())}
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (
            self._is_user_dragging
            and self._drag_start_scene_pos is not None
            and bool(event.buttons() & Qt.MouseButton.LeftButton)
        ):
            self._move_drag_group(event.scenePos() - self._drag_start_scene_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        was_dragging = self._is_user_dragging
        if was_dragging and self._drag_start_scene_pos is not None:
            self._move_drag_group(event.scenePos() - self._drag_start_scene_pos)
        self._is_user_dragging = False
        self._drag_start_scene_pos = None
        self._drag_group_start_positions.clear()
        scene = self.scene()
        clear_guides = getattr(scene, "clear_smart_guides", None)
        if clear_guides is not None:
            clear_guides()
        if was_dragging:
            event.accept()
            return
        super().mouseReleaseEvent(event)

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

    def paint(self, painter: QPainter, option, widget=None) -> None:
        preview = self._make_preview_image()
        if preview is None:
            painter.setPen(self._frame_pen())
            painter.setBrush(self.brush())
            painter.drawRect(self.rect())
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No image")
            return
        rect = self.rect()
        if self.preserve_aspect:
            source_width = max(1.0, float(preview.width()))
            source_height = max(1.0, float(preview.height()))
            scale = min(
                rect.width() / source_width,
                rect.height() / source_height,
            )
            target_width = source_width * scale
            target_height = source_height * scale
            target = QRectF(
                rect.center().x() - target_width / 2.0,
                rect.center().y() - target_height / 2.0,
                target_width,
                target_height,
            )
            painter.drawImage(target, preview)
        else:
            painter.drawImage(rect, preview)
        painter.setPen(self._frame_pen())
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self.rect())

    def _make_preview_image(self) -> QImage | None:
        if not self.image_path or not Path(self.image_path).exists():
            return None
        try:
            image = _crop_qimage(
                self.image_path,
                self.roi,
                self.transform,
                geometry_transform=self.geometry_transform,
            )
            return None if image.isNull() else image
        except Exception:
            return None

    def preview_image(self) -> QImage | None:
        """Return a detached copy of the exact currently rendered blot crop."""
        preview = self._make_preview_image()
        return preview.copy() if preview is not None and not preview.isNull() else None

    def to_json(self) -> dict:
        rect = self.rect()
        return {
            "type": self.TypeName,
            "x": self.pos().x(),
            "y": self.pos().y(),
            "width": rect.width(),
            "height": rect.height(),
            "lane_count": self.lane_count,
            "rotation": self.rotation(),
            "image_path": self.image_path,
            "roi": self.roi,
            "transform": self.transform,
            "geometry_transform": self.geometry_transform,
            "preserve_aspect": self.preserve_aspect,
        }

    @classmethod
    def from_json(cls, data: dict) -> "BlotPlaceholderItem":
        item = cls(
            QRectF(
                float(data.get("x", 0.0)),
                float(data.get("y", 0.0)),
                float(data.get("width", 180.0)),
                float(data.get("height", 45.0)),
            ),
            lane_count=max(1, int(data.get("lane_count", 4))),
            image_path=data.get("image_path"),
            roi=data.get("roi"),
            transform=data.get("transform"),
            geometry_transform=data.get("geometry_transform"),
            preserve_aspect=bool(data.get("preserve_aspect", False)),
        )
        item.setRotation(float(data.get("rotation", 0.0)))
        return item
