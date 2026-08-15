"""Undo/redo commands for the WB layout editor scene."""
from __future__ import annotations

from typing import Any

from PySide6.QtGui import QUndoCommand


class EditTextCommand(QUndoCommand):
    """Undoable text content change."""

    def __init__(self, item: Any, old_text: str, new_text: str) -> None:
        super().__init__("Edit text")
        self._item = item
        self._old_text = old_text
        self._new_text = new_text

    def undo(self) -> None:
        self._item.setPlainText(self._old_text)

    def redo(self) -> None:
        self._item.setPlainText(self._new_text)


class ResizeItemCommand(QUndoCommand):
    """Undoable resize for items exposing editor_geometry methods."""

    def __init__(self, item: Any, old_geometry: dict, new_geometry: dict) -> None:
        super().__init__("Resize item")
        self._item = item
        self._old_geometry = dict(old_geometry)
        self._new_geometry = dict(new_geometry)

    def undo(self) -> None:
        self._item.set_editor_geometry(self._old_geometry)

    def redo(self) -> None:
        self._item.set_editor_geometry(self._new_geometry)
