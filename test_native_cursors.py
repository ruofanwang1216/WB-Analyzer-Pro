"""Guard against the macOS cursor crash.

Qt draws some cursor shapes from bitmaps on macOS.  Building one of those
while the pointer moved over the figure preview segfaulted inside
CoreGraphics (QImage::toCGImage), taking the whole app down.  Only shapes
backed by a native macOS cursor may be used anywhere in the GUI.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QApplication

from gui.figure_mode_window import FigureModeWindow
from gui.layout_editor_items import ResizeHandleItem

_BITMAP_SHAPES = (
    "SizeVerCursor", "SizeHorCursor", "SizeBDiagCursor", "SizeFDiagCursor",
    "SizeAllCursor", "WaitCursor", "BusyCursor", "UpArrowCursor",
    "WhatsThisCursor", "BitmapCursor",
)
_NATIVE = {
    Qt.CursorShape.ArrowCursor, Qt.CursorShape.CrossCursor,
    Qt.CursorShape.PointingHandCursor, Qt.CursorShape.SplitHCursor,
    Qt.CursorShape.SplitVCursor, Qt.CursorShape.OpenHandCursor,
    Qt.CursorShape.ClosedHandCursor, Qt.CursorShape.IBeamCursor,
}


class _HoverEvent:
    def __init__(self, pos: QPointF) -> None:
        self._pos = pos

    def pos(self) -> QPointF:
        return self._pos

    def ignore(self) -> None:
        pass

    def accept(self) -> None:
        pass


class NativeCursorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_no_gui_module_uses_a_bitmap_cursor_shape(self) -> None:
        pattern = re.compile(r"CursorShape\.(" + "|".join(_BITMAP_SHAPES) + r")\b")
        offenders = []
        for path in sorted(Path(__file__).parent.glob("gui/*.py")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                code = line.split("#", 1)[0]
                if pattern.search(code):
                    offenders.append(f"{path.name}:{number}: {line.strip()}")
        self.assertEqual(offenders, [])

    def test_resize_handles_use_native_cursors(self) -> None:
        for handle in (
            "top_left", "top", "top_right", "left", "right",
            "bottom_left", "bottom", "bottom_right",
        ):
            self.assertIn(ResizeHandleItem._cursor_for_handle(handle), _NATIVE, handle)

    def test_blot_frame_hover_uses_native_cursors_and_sets_them_once(self) -> None:
        window = FigureModeWindow()
        window._on_apply_structure()
        frame = next(iter(window._canvas._blot_frames.values()))
        rect = frame.rect()
        calls = []
        original = frame.setCursor

        def record(shape) -> None:
            calls.append(shape)
            original(shape)

        frame.setCursor = record
        spots = {
            "middle": rect.center(),
            "right edge": QPointF(rect.right(), rect.center().y()),
            "bottom edge": QPointF(rect.center().x(), rect.bottom()),
            "corner": rect.bottomRight(),
        }
        for name, pos in spots.items():
            before = len(calls)
            for _ in range(5):  # the pointer keeps moving over the same spot
                try:
                    frame.hoverMoveEvent(_HoverEvent(pos))
                except TypeError:
                    pass  # the Qt base class rejects the stand-in event
            self.assertEqual(len(calls) - before, 1, name)
            self.assertIn(calls[-1], _NATIVE, name)


if __name__ == "__main__":
    unittest.main()
