"""QGraphicsScene manager for editable WB layout templates."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Signal, QLineF
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene
from PySide6.QtGui import QUndoStack

from gui.layout_editor_commands import EditTextCommand, MoveItemsCommand
from gui.layout_editor_items import (
    BlotPlaceholderItem,
    EditableTextItem,
    LineElementItem,
)


class GraphicsEditorScene(QGraphicsScene):
    """Scene that owns editable WB layout items and template serialization."""

    selectedItemsChanged = Signal(list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.undo_stack = QUndoStack(self)
        self._move_start_positions: dict[QGraphicsItem, QPointF] = {}
        self.selectionChanged.connect(self._emit_selected_items_changed)

    def _emit_selected_items_changed(self) -> None:
        self.selectedItemsChanged.emit(self.selectedItems())

    def mousePressEvent(self, event) -> None:
        self._move_start_positions = {
            item: QPointF(item.pos())
            for item in self.selectedItems()
            if item.parentItem() is None
        }
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if not self._move_start_positions:
            return
        moved_items: list[QGraphicsItem] = []
        old_positions: list[QPointF] = []
        new_positions: list[QPointF] = []
        for item, old_pos in self._move_start_positions.items():
            new_pos = QPointF(item.pos())
            if new_pos != old_pos:
                moved_items.append(item)
                old_positions.append(old_pos)
                new_positions.append(new_pos)
        self._move_start_positions.clear()
        if moved_items:
            self.undo_stack.push(MoveItemsCommand(moved_items, old_positions, new_positions))

    def add_text_box(self, pos: QPointF, text: str = "Text") -> EditableTextItem:
        item = EditableTextItem(text, QRectF(pos.x(), pos.y(), 160, 42))
        self.addItem(item)
        return item

    def add_line(
        self,
        start: QPointF,
        end: QPointF,
        *,
        dashed: bool = False,
    ) -> LineElementItem:
        item = LineElementItem(QLineF(start, end), dashed=dashed)
        self.addItem(item)
        return item

    def add_blot_placeholder(
        self,
        rect: QRectF,
        *,
        image_path: str | None = None,
        roi: dict | None = None,
        transform: dict | None = None,
    ) -> BlotPlaceholderItem:
        item = BlotPlaceholderItem(
            rect,
            image_path=image_path,
            roi=roi,
            transform=transform,
        )
        self.addItem(item)
        return item

    def record_text_edit(self, item: EditableTextItem, old_text: str, new_text: str) -> None:
        if old_text == new_text:
            return
        self.undo_stack.push(EditTextCommand(item, old_text, new_text))

    def save_layout_to_json(self, file_path: str | Path) -> None:
        data = {
            "version": 1,
            "items": [
                item.to_json()
                for item in self.items()
                if item.parentItem() is None and hasattr(item, "to_json")
            ],
        }
        Path(file_path).write_text(json.dumps(data, indent=2), encoding="utf-8")

    def load_layout_from_json(self, file_path: str | Path) -> None:
        data = json.loads(Path(file_path).read_text(encoding="utf-8"))
        self.clear()
        self.undo_stack.clear()
        factories = {
            EditableTextItem.TypeName: EditableTextItem.from_json,
            LineElementItem.TypeName: LineElementItem.from_json,
            BlotPlaceholderItem.TypeName: BlotPlaceholderItem.from_json,
        }
        for item_data in data.get("items", []):
            factory = factories.get(item_data.get("type"))
            if factory is None:
                continue
            self.addItem(factory(item_data))


def update_font_for_selected_items(
    scene: GraphicsEditorScene,
    *,
    family: str = "Arial",
    size: float | None = None,
    bold: bool | None = None,
) -> None:
    """Apply font changes to selected EditableTextItem instances."""

    for item in scene.selectedItems():
        if not isinstance(item, EditableTextItem):
            continue
        font = QFont(item.font())
        font.setFamily(family)
        if size is not None:
            font.setPointSizeF(size)
        if bold is not None:
            font.setBold(bold)
        item.setFont(font)
        item.update_resize_handles()
