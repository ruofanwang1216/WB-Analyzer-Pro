"""Reusable QGraphicsItems for the WB layout editor."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QLineF
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QImage,
    QPainter,
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

from core.image_transform import ImageTransformParams, image_array_to_uint16_luminance, transform_pixels_16_to_8
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
    _ROTATED_TEXT_REPAINT_OPACITY = 0.28
    _ROTATED_TEXT_REPAINT_OFFSET = 0.12

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
        self.init_resize_handles()

    def paint(self, painter: QPainter, option, widget=None) -> None:
        if (
            abs(float(self.rotation())) < 0.01
            or self.textInteractionFlags() != Qt.TextInteractionFlag.NoTextInteraction
        ):
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
            painter.setFont(self.font())
            painter.setPen(self.defaultTextColor())
            painter.setOpacity(1.0)
            painter.drawText(rect, flags, self.toPlainText())
            painter.setOpacity(self._ROTATED_TEXT_REPAINT_OPACITY)
            painter.translate(self._ROTATED_TEXT_REPAINT_OFFSET, 0.0)
            painter.drawText(rect, flags, self.toPlainText())
        finally:
            painter.restore()

    def mouseDoubleClickEvent(self, event) -> None:
        self._old_text = self.toPlainText()
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        super().mouseDoubleClickEvent(event)

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
            scene = self.scene()
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
            self._move_drag_group(delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        was_dragging = self._is_user_dragging
        if was_dragging and self._drag_start_scene_pos is not None:
            self._move_drag_group(event.scenePos() - self._drag_start_scene_pos)
        self._is_user_dragging = False
        self._drag_start_scene_pos = None
        self._drag_start_item_pos = None
        self._drag_group_start_positions.clear()
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
            scene.record_text_edit(self, self._old_text, new_text)
        super().focusOutEvent(event)

    def itemChange(self, change, value):
        self.handle_item_change(change, value)
        return super().itemChange(change, value)

    def editor_rect(self) -> QRectF:
        return QRectF(0.0, 0.0, max(_MIN_WIDTH, self.textWidth()), self._box_height)

    def resize_to_local_size(self, width: float, height: float) -> None:
        self.prepareGeometryChange()
        self.setTextWidth(max(_MIN_WIDTH, width))
        self._box_height = max(_MIN_HEIGHT, height)

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
        image_path: str | None = None,
        roi: dict[str, float] | None = None,
        transform: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(QRectF(0, 0, rect.width(), rect.height()))
        self.setPos(rect.topLeft())
        self.image_path = image_path
        self.roi = dict(roi or {})
        self.transform = dict(transform or {})
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
        painter.drawImage(self.rect(), preview)
        painter.setPen(self._frame_pen())
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self.rect())

    def _make_preview_image(self) -> QImage | None:
        if not self.image_path or not Path(self.image_path).exists():
            return None
        try:
            with Image.open(self.image_path) as img:
                arr = np.array(img)
            if self.roi:
                x = max(0, int(self.roi.get("x", 0)))
                y = max(0, int(self.roi.get("y", 0)))
                w = max(1, int(self.roi.get("w", arr.shape[1] - x)))
                h = max(1, int(self.roi.get("h", arr.shape[0] - y)))
                arr = arr[y:y + h, x:x + w]
            source = image_array_to_uint16_luminance(arr)
            params = ImageTransformParams(
                low=float(self.transform.get("low", 0.0)),
                high=float(self.transform.get("high", 65535.0)),
                gamma=float(self.transform.get("gamma", 1.0)),
                inverted=bool(self.transform.get("inverted", True)),
            )
            mapped = transform_pixels_16_to_8(source, params)
            mapped = np.ascontiguousarray(mapped)
            self._preview_buffer = mapped.tobytes()
            return QImage(
                self._preview_buffer,
                mapped.shape[1],
                mapped.shape[0],
                mapped.shape[1],
                QImage.Format.Format_Grayscale8,
            )
        except Exception:
            return None

    def to_json(self) -> dict:
        rect = self.rect()
        return {
            "type": self.TypeName,
            "x": self.pos().x(),
            "y": self.pos().y(),
            "width": rect.width(),
            "height": rect.height(),
            "rotation": self.rotation(),
            "image_path": self.image_path,
            "roi": self.roi,
            "transform": self.transform,
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
            image_path=data.get("image_path"),
            roi=data.get("roi"),
            transform=data.get("transform"),
        )
        item.setRotation(float(data.get("rotation", 0.0)))
        return item
