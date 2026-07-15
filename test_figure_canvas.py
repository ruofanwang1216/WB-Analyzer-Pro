import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from PIL import Image
from PySide6.QtCore import QEvent, QLineF, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QKeyEvent, QPainter, QPen, QTextCursor
from PySide6.QtWidgets import QApplication, QGraphicsItem, QGraphicsRectItem, QLabel

import core.template_engine as template_module
import gui.figure_mode_window as figure_mode_module
from core.figure_project import ImageBBox, SourceRef
from core.layout_engine import LayoutEngine, LayoutItem, scene_to_pt
from core.template_engine import TemplateEngine, TEMPLATES
from gui.figure_canvas import EditableTextItem, FigureCanvas
from gui.figure_mode_window import FigureModeWindow
from gui.layout_editor_items import (
    BlotPlaceholderItem,
    EditableTextItem as OverlayTextItem,
    LineElementItem,
)


class _FakeMouseEvent:
    def __init__(
        self,
        scene_pos: QPointF,
        *,
        button: Qt.MouseButton = Qt.MouseButton.LeftButton,
        buttons: Qt.MouseButton = Qt.MouseButton.LeftButton,
        modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
        pos: QPointF | None = None,
    ) -> None:
        self._scene_pos = scene_pos
        self._button = button
        self._buttons = buttons
        self._modifiers = modifiers
        self._pos = pos if pos is not None else scene_pos
        self.accepted = False

    def pos(self) -> QPointF:
        return self._pos

    def scenePos(self) -> QPointF:
        return self._scene_pos

    def button(self) -> Qt.MouseButton:
        return self._button

    def buttons(self) -> Qt.MouseButton:
        return self._buttons

    def modifiers(self) -> Qt.KeyboardModifier:
        return self._modifiers

    def accept(self) -> None:
        self.accepted = True


class FigureCanvasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _render_default_canvas(self) -> FigureCanvas:
        project = TemplateEngine.build_project("normal_wb", 1, 2, 4)
        layout = LayoutEngine().compute(project)
        canvas = FigureCanvas()
        canvas.render(layout, project)
        return canvas

    def test_builtin_text_uses_toolbar_font_and_delete(self) -> None:
        canvas = self._render_default_canvas()
        text_item = next(item for item in canvas._scene.items() if isinstance(item, EditableTextItem))
        text_item.setSelected(True)

        styles = canvas.apply_selected_text_font(size=18.0, bold=True, italic=True)

        self.assertEqual(text_item.font().pointSizeF(), 18.0)
        self.assertTrue(text_item.font().bold())
        self.assertTrue(text_item.font().italic())
        self.assertIn(text_item.source_ref.key(), styles)

        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier)
        canvas.keyPressEvent(event)

        self.assertIn(text_item.source_ref.key(), canvas.hidden_text_keys())
        self.assertNotIn(text_item, canvas._scene.items())

    def test_selected_text_rotation_updates_builtin_and_overlay_text(self) -> None:
        canvas = self._render_default_canvas()
        builtin_text = next(item for item in canvas._scene.items() if isinstance(item, EditableTextItem))
        overlay_text = canvas.add_overlay_text_box()
        canvas.apply_selected_text_font()
        font = builtin_text.font()
        font.setFamily("Helvetica")
        font.setPointSizeF(17.0)
        font.setBold(True)
        font.setItalic(True)
        font.setUnderline(True)
        builtin_text.setFont(font)
        builtin_text.set_text_align("center")
        builtin_text.setSelected(True)
        overlay_text.setSelected(True)

        styles = canvas.apply_selected_text_rotation(37.0)

        self.assertEqual(builtin_text.rotation(), 37.0)
        self.assertEqual(overlay_text.rotation(), 37.0)
        self.assertEqual(styles[builtin_text.source_ref.key()]["rotation"], 37.0)
        self.assertEqual(builtin_text.font().family(), "Helvetica")
        self.assertEqual(builtin_text.font().pointSizeF(), 17.0)
        self.assertTrue(builtin_text.font().bold())
        self.assertTrue(builtin_text.font().italic())
        self.assertTrue(builtin_text.font().underline())
        self.assertEqual(builtin_text.text_align(), "center")
        self.assertEqual(styles[builtin_text.source_ref.key()]["font_family"], "Helvetica")
        self.assertEqual(styles[builtin_text.source_ref.key()]["font_size_pt"], 17.0)
        self.assertTrue(styles[builtin_text.source_ref.key()]["bold"])
        self.assertTrue(styles[builtin_text.source_ref.key()]["italic"])
        self.assertTrue(styles[builtin_text.source_ref.key()]["underline"])
        self.assertEqual(styles[builtin_text.source_ref.key()]["align"], "center")

        layout_item = next(
            item for item in canvas.adjusted_layout_items_for_export(
                LayoutEngine().compute(TemplateEngine.build_project("normal_wb", 1, 2, 4)).items
            )
            if item.source_ref and item.source_ref.key() == builtin_text.source_ref.key()
        )
        self.assertEqual(layout_item.rotation, 37.0)
        overlay_item = canvas.overlay_as_layout_items()[0]
        self.assertEqual(overlay_item.rotation, 37.0)

    def test_rotated_text_paint_keeps_black_text_color(self) -> None:
        text = OverlayTextItem("IP-MAP3K2", QRectF(30, 40, 120, 36))
        text.setDefaultTextColor(QColor("#000000"))
        text.setRotation(35.0)

        image = QImage(220, 160, QImage.Format.Format_ARGB32)
        image.fill(QColor("#FFFFFF"))
        painter = QPainter(image)
        try:
            text.paint(painter, None)
        finally:
            painter.end()

        darkest = 255
        for y in range(image.height()):
            for x in range(image.width()):
                color = QColor(image.pixel(x, y))
                darkest = min(darkest, color.red(), color.green(), color.blue())
        self.assertLess(darkest, 20)

    def test_builtin_text_uses_manual_drag_not_qt_movable(self) -> None:
        canvas = self._render_default_canvas()
        text_item = next(item for item in canvas._scene.items() if isinstance(item, EditableTextItem))

        self.assertFalse(
            bool(text_item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        )

    def test_builtin_text_uses_overlay_text_box_structure(self) -> None:
        canvas = self._render_default_canvas()
        text_item = next(item for item in canvas._scene.items() if isinstance(item, EditableTextItem))

        self.assertIsInstance(text_item, OverlayTextItem)
        self.assertTrue(hasattr(text_item, "_resize_handles"))
        text_item.setSelected(True)
        self.assertTrue(any(handle.isVisible() for handle in text_item._resize_handles.values()))

        text_item.resize_to_local_size(72.0, 30.0)

        self.assertAlmostEqual(text_item.editor_rect().width(), 72.0)
        self.assertAlmostEqual(text_item.editor_rect().height(), 30.0)

    def test_builtin_text_offsets_accumulate_across_rerenders(self) -> None:
        project = TemplateEngine.build_project("normal_wb", 1, 2, 4)
        layout = LayoutEngine().compute(project)
        canvas = FigureCanvas()
        canvas.render(layout, project)
        text_item = next(item for item in canvas._scene.items() if isinstance(item, EditableTextItem))
        key = text_item.source_ref.key()
        original_pos = QPointF(text_item.pos())

        text_item.setPos(original_pos + QPointF(15.0, 7.0))
        canvas.render(layout, project)

        text_item = canvas._text_items[key]
        text_item.setPos(text_item.pos() + QPointF(5.0, 4.0))
        canvas.render(layout, project)

        self.assertEqual(canvas._text_items[key].pos(), original_pos + QPointF(20.0, 11.0))

    def test_export_adjusted_layout_items_include_canvas_offsets(self) -> None:
        project = TemplateEngine.build_project("normal_wb", 1, 2, 4)
        layout = LayoutEngine().compute(project)
        canvas = FigureCanvas()
        canvas.render(layout, project)
        text_item = next(item for item in canvas._scene.items() if isinstance(item, EditableTextItem))
        text_key = text_item.source_ref.key()
        text_item.setPos(text_item.pos() + QPointF(15.0, 9.0))
        canvas._handle_text_position_changed(text_item)
        blot_key, frame = next(iter(canvas._blot_frames.items()))
        canvas._select_blot_frame(frame, additive=False)
        canvas._begin_blot_frame_move()
        canvas._preview_blot_frame_move(QPointF(12.0, 6.0))
        canvas._commit_blot_frame_move(QPointF(12.0, 6.0))

        adjusted = canvas.adjusted_layout_items_for_export(layout.items)

        original_text = next(item for item in layout.items if item.source_ref and item.source_ref.key() == text_key)
        adjusted_text = next(item for item in adjusted if item.source_ref and item.source_ref.key() == text_key)
        original_blot = next(item for item in layout.items if item.source_ref and item.source_ref.key() == blot_key)
        adjusted_blot = next(item for item in adjusted if item.source_ref and item.source_ref.key() == blot_key)
        self.assertAlmostEqual(adjusted_text.x_pt, scene_to_pt(text_item.pos().x()))
        self.assertAlmostEqual(adjusted_text.y_pt, scene_to_pt(text_item.pos().y()))
        self.assertAlmostEqual(adjusted_text.w_pt, scene_to_pt(text_item.editor_rect().width()))
        self.assertAlmostEqual(adjusted_text.h_pt, scene_to_pt(text_item.editor_rect().height()))
        self.assertAlmostEqual(adjusted_blot.x_pt, original_blot.x_pt + scene_to_pt(12.0))
        self.assertAlmostEqual(adjusted_blot.y_pt, original_blot.y_pt + scene_to_pt(6.0))

    def test_export_adjusted_layout_items_preserve_condition_table_cell_offsets(self) -> None:
        canvas = FigureCanvas()
        items = [
            LayoutItem(
                kind="table_cell",
                x_pt=0.0,
                y_pt=20.0,
                w_pt=20.0,
                h_pt=10.0,
                text="-",
                source_ref=SourceRef(panel_idx=0, table_row=1, table_col=0, field="condition_cell"),
            ),
            LayoutItem(
                kind="table_cell",
                x_pt=20.0,
                y_pt=20.0,
                w_pt=20.0,
                h_pt=10.0,
                text="+",
                source_ref=SourceRef(panel_idx=0, table_row=1, table_col=1, field="condition_cell"),
            ),
            LayoutItem(
                kind="table_cell",
                x_pt=40.0,
                y_pt=20.0,
                w_pt=20.0,
                h_pt=10.0,
                text="+",
                source_ref=SourceRef(panel_idx=0, table_row=1, table_col=2, field="condition_cell"),
            ),
        ]
        canvas._offsets[items[1].source_ref.key()] = QPointF(0.0, 4.0)
        canvas._offsets[items[2].source_ref.key()] = QPointF(0.0, 7.0)

        adjusted = canvas.adjusted_layout_items_for_export(items)

        self.assertEqual(
            [item.y_pt for item in adjusted],
            [20.0, 20.0 + scene_to_pt(4.0), 20.0 + scene_to_pt(7.0)],
        )

    def test_selected_blot_frame_moves_with_mouse_drag_state(self) -> None:
        canvas = self._render_default_canvas()
        key, frame = next(iter(canvas._blot_frames.items()))
        canvas._select_blot_frame(frame, additive=False)

        canvas._begin_blot_frame_move()
        canvas._preview_blot_frame_move(QPointF(12.0, 5.0))
        canvas._commit_blot_frame_move(QPointF(12.0, 5.0))

        self.assertEqual(canvas._blot_offsets[key], QPointF(12.0, 5.0))

    def test_state_snapshot_captures_current_blot_frame_position(self) -> None:
        canvas = self._render_default_canvas()
        key, frame = next(iter(canvas._blot_frames.items()))
        frame.setPos(frame.pos() + QPointF(31.0, 9.0))

        snapshot = canvas.state_snapshot()

        self.assertIn(
            {"key": list(key), "x": 31.0, "y": 9.0},
            snapshot["blot_offsets"],
        )

    def test_blot_resize_preview_updates_placeholder_content(self) -> None:
        canvas = self._render_default_canvas()
        key, frame = next(iter(canvas._blot_frames.items()))
        placeholder = next(
            item
            for item in canvas._blot_content_items[key]
            if isinstance(item, QGraphicsRectItem)
        )
        new_width = frame.rect().width() + 40.0
        new_height = frame.rect().height() + 12.0

        canvas._select_blot_frame(frame, additive=False)
        frame.setRect(QRectF(0.0, 0.0, new_width, new_height))
        canvas._preview_blot_resize(new_width, new_height, frame)

        self.assertAlmostEqual(placeholder.rect().width(), new_width)
        self.assertAlmostEqual(placeholder.rect().height(), new_height)

    def test_multi_selected_blot_resize_updates_all_placeholders(self) -> None:
        canvas = self._render_default_canvas()
        frames = list(canvas._blot_frames.items())
        self.assertGreaterEqual(len(frames), 2)
        first_key, first_frame = frames[0]
        second_key, second_frame = frames[1]
        first_placeholder = next(
            item
            for item in canvas._blot_content_items[first_key]
            if isinstance(item, QGraphicsRectItem)
        )
        second_placeholder = next(
            item
            for item in canvas._blot_content_items[second_key]
            if isinstance(item, QGraphicsRectItem)
        )
        canvas._select_blot_frame(first_frame, additive=False)
        canvas._select_blot_frame(second_frame, additive=True)
        new_width = first_frame.rect().width() + 35.0
        new_height = first_frame.rect().height() + 10.0

        canvas._preview_blot_resize(new_width, new_height, first_frame)

        self.assertAlmostEqual(first_placeholder.rect().width(), new_width)
        self.assertAlmostEqual(first_placeholder.rect().height(), new_height)
        self.assertAlmostEqual(second_frame.rect().width(), new_width)
        self.assertAlmostEqual(second_frame.rect().height(), new_height)
        self.assertAlmostEqual(second_placeholder.rect().width(), new_width)
        self.assertAlmostEqual(second_placeholder.rect().height(), new_height)

    def test_clicking_selected_blot_frame_keeps_group_selection(self) -> None:
        canvas = self._render_default_canvas()
        frames = list(canvas._blot_frames.items())
        self.assertGreaterEqual(len(frames), 2)
        first_key, first_frame = frames[0]
        second_key, second_frame = frames[1]
        canvas._select_blot_frame(first_frame, additive=False)
        canvas._select_blot_frame(second_frame, additive=True)

        first_frame.mousePressEvent(
            _FakeMouseEvent(
                first_frame.scenePos(),
                pos=first_frame.rect().bottomRight(),
            )
        )

        self.assertEqual(canvas._selected_blot_keys, {first_key, second_key})

    def test_selected_overlay_text_boxes_move_as_group(self) -> None:
        canvas = FigureCanvas()
        text_a = OverlayTextItem("A", rect=None)
        text_b = OverlayTextItem("B", rect=None)
        text_a.setPos(1.0, 1.0)
        text_b.setPos(1.0, 2.0)
        canvas._scene.addItem(text_a)
        canvas._scene.addItem(text_b)
        canvas._overlay_items.extend([text_a, text_b])
        text_a.setSelected(True)
        text_b.setSelected(True)

        text_a.mousePressEvent(_FakeMouseEvent(QPointF(1.0, 1.0)))
        text_a.mouseMoveEvent(_FakeMouseEvent(QPointF(2.0, 1.0)))
        text_a.mouseReleaseEvent(_FakeMouseEvent(QPointF(2.0, 1.0)))

        self.assertEqual(text_a.pos(), QPointF(2.0, 1.0))
        self.assertEqual(text_b.pos(), QPointF(2.0, 2.0))

    def test_rubber_band_selected_blot_frame_moves_with_text_group(self) -> None:
        canvas = self._render_default_canvas()
        text_item = next(
            item for item in canvas._scene.items()
            if isinstance(item, EditableTextItem)
        )
        key, frame = next(iter(canvas._blot_frames.items()))
        content_item = canvas._blot_content_items[key][0]
        text_item.setSelected(True)
        frame.setSelected(True)
        canvas._sync_blot_selection_from_scene()

        text_start = QPointF(text_item.pos())
        frame_start = QPointF(frame.pos())
        content_start = QPointF(content_item.pos())
        press = text_item.sceneBoundingRect().center()

        text_item.mousePressEvent(_FakeMouseEvent(press))
        text_item.mouseMoveEvent(_FakeMouseEvent(press + QPointF(12.0, 7.0)))
        text_item.mouseReleaseEvent(_FakeMouseEvent(press + QPointF(12.0, 7.0)))

        self.assertIn(key, canvas._selected_blot_keys)
        self.assertEqual(text_item.pos(), text_start + QPointF(12.0, 7.0))
        self.assertEqual(frame.pos(), frame_start + QPointF(12.0, 7.0))
        self.assertEqual(content_item.pos(), content_start + QPointF(12.0, 7.0))
        self.assertEqual(canvas._blot_offsets[key], QPointF(12.0, 7.0))

    def test_overlay_line_uses_manual_drag_without_press_jump(self) -> None:
        canvas = FigureCanvas()
        line = LineElementItem(QLineF(100.0, 10.0, 160.0, 10.0))
        canvas._scene.addItem(line)
        canvas._overlay_items.append(line)

        self.assertFalse(
            bool(line.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        )
        line.mousePressEvent(_FakeMouseEvent(QPointF(120.0, 10.0)))

        self.assertTrue(line.isSelected())
        self.assertEqual(line.pos(), QPointF(0.0, 0.0))

        line.mouseMoveEvent(_FakeMouseEvent(QPointF(124.0, 13.0)))
        line.mouseReleaseEvent(_FakeMouseEvent(QPointF(124.0, 13.0)))

        self.assertEqual(line.pos(), QPointF(4.0, 3.0))

    def test_selected_overlay_line_moves_with_arrow_keys(self) -> None:
        canvas = FigureCanvas()
        line = LineElementItem(QLineF(0.0, 0.0, 80.0, 0.0))
        canvas._scene.addItem(line)
        canvas._overlay_items.append(line)
        line.setSelected(True)

        right = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Right,
            Qt.KeyboardModifier.NoModifier,
        )
        down_fast = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Down,
            Qt.KeyboardModifier.ShiftModifier,
        )
        canvas.keyPressEvent(right)
        canvas.keyPressEvent(down_fast)

        self.assertEqual(line.pos(), QPointF(1.0, 5.0))

    def test_overlay_line_export_preserves_color_and_width(self) -> None:
        canvas = FigureCanvas()
        line = LineElementItem(QLineF(0.0, 0.0, 80.0, 0.0))
        line.setPen(QPen(line.pen().color(), 3.0))
        canvas._scene.addItem(line)
        canvas._overlay_items.append(line)

        exported = canvas.overlay_as_layout_items()

        self.assertEqual(exported[0].line_color, "#222222")
        self.assertAlmostEqual(exported[0].line_width_pt, scene_to_pt(3.0))

    def test_selected_overlay_text_box_moves_with_arrow_keys(self) -> None:
        canvas = FigureCanvas()
        text = OverlayTextItem("A", rect=None)
        text.setPos(10.0, 20.0)
        canvas._scene.addItem(text)
        canvas._overlay_items.append(text)
        text.setSelected(True)

        up = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Up,
            Qt.KeyboardModifier.NoModifier,
        )
        left_fast = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Left,
            Qt.KeyboardModifier.ShiftModifier,
        )
        canvas.keyPressEvent(up)
        canvas.keyPressEvent(left_fast)

        self.assertEqual(text.pos(), QPointF(5.0, 19.0))

    def test_editing_overlay_text_uses_arrow_keys_for_text_cursor(self) -> None:
        canvas = FigureCanvas()
        text = OverlayTextItem("ABC", rect=None)
        text.setPos(10.0, 20.0)
        canvas._scene.addItem(text)
        canvas._overlay_items.append(text)
        text.setSelected(True)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        text.setFocus(Qt.FocusReason.MouseFocusReason)

        event = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Right,
            Qt.KeyboardModifier.NoModifier,
        )
        canvas.keyPressEvent(event)

        self.assertEqual(text.pos(), QPointF(10.0, 20.0))

    def test_blot_pixmap_uses_clamped_pil_crop_for_tiff_preview(self) -> None:
        canvas = FigureCanvas()
        with TemporaryDirectory() as tmp:
            path = f"{tmp}/source.tif"
            Image.fromarray(
                np.full((20, 30), 30000, dtype=np.uint16)
            ).save(path)
            item = LayoutItem(
                kind="blot",
                x_pt=0.0,
                y_pt=0.0,
                w_pt=40.0,
                h_pt=12.0,
                image_path=path,
                image_crop_px={"x": -5.0, "y": -4.0, "w": 80.0, "h": 40.0},
            )

            pixmap = canvas._make_blot_pixmap(item, 60.0, 18.0)

        self.assertIsNotNone(pixmap)
        self.assertFalse(pixmap.isNull())
        self.assertEqual(pixmap.width(), 30)
        self.assertEqual(pixmap.height(), 20)

    def test_blot_pixmap_uses_captured_image_transform(self) -> None:
        canvas = FigureCanvas()
        with TemporaryDirectory() as tmp:
            path = f"{tmp}/source.tif"
            Image.fromarray(
                np.array([[0, 65535], [0, 65535]], dtype=np.uint16)
            ).save(path)
            item = LayoutItem(
                kind="blot",
                x_pt=0.0,
                y_pt=0.0,
                w_pt=2.0,
                h_pt=2.0,
                image_path=path,
                image_crop_px={"x": 0.0, "y": 0.0, "w": 2.0, "h": 2.0},
                image_transform={
                    "low": 0,
                    "high": 65535,
                    "gamma": 1.0,
                    "inverted": False,
                },
            )

            pixmap = canvas._make_blot_pixmap(item, 2.0, 2.0)

        self.assertIsNotNone(pixmap)
        image = pixmap.toImage()
        self.assertLess(image.pixelColor(0, 0).red(), 10)
        self.assertGreater(image.pixelColor(1, 0).red(), 245)

    def test_selected_blot_frame_uses_morandi_pale_red_border(self) -> None:
        window = FigureModeWindow()
        window._on_apply_template()
        _key, frame = next(iter(window._canvas._blot_frames.items()))

        self.assertEqual(frame.pen().color().name().lower(), "#000000")

        window._canvas._select_blot_frame(frame, additive=False)

        self.assertEqual(frame.pen().color().name().lower(), "#b96f73")
        self.assertAlmostEqual(frame.pen().widthF(), 3.0)

    def test_plain_clicking_template_blot_frame_clears_overlay_blot_selection(self) -> None:
        window = FigureModeWindow()
        window._on_apply_template()
        added = window._canvas.add_overlay_blot_frame()
        self.assertTrue(added.isSelected())

        _key, frame = next(iter(window._canvas._blot_frames.items()))
        window._canvas._select_blot_frame(frame, additive=False)

        self.assertTrue(frame.isSelected())
        self.assertFalse(added.isSelected())

    def test_plain_clicking_overlay_blot_frame_clears_template_blot_selection(self) -> None:
        window = FigureModeWindow()
        window._on_apply_template()
        _key, frame = next(iter(window._canvas._blot_frames.items()))
        added = BlotPlaceholderItem(QRectF(10.0, 10.0, 80.0, 24.0))
        window._canvas._scene.addItem(added)
        window._canvas._overlay_items.append(added)
        window._canvas._select_blot_frame(frame, additive=False)

        window._canvas._select_overlay_blot_item(added, additive=False)

        self.assertFalse(frame.isSelected())
        self.assertFalse(window._canvas.selected_blot_refs())
        self.assertTrue(added.isSelected())

    def test_selected_overlay_blot_frame_uses_same_red_highlight(self) -> None:
        window = FigureModeWindow()
        window._on_apply_template()
        added = BlotPlaceholderItem(QRectF(10.0, 10.0, 80.0, 24.0))
        window._canvas._scene.addItem(added)
        window._canvas._overlay_items.append(added)

        window._canvas._select_overlay_blot_item(added, additive=False)

        self.assertEqual(added.pen().color().name().lower(), "#b96f73")
        self.assertAlmostEqual(added.pen().widthF(), 3.0)

    def test_additive_click_can_select_template_and_overlay_blot_frames_together(self) -> None:
        window = FigureModeWindow()
        window._on_apply_template()
        _key, frame = next(iter(window._canvas._blot_frames.items()))
        added = BlotPlaceholderItem(QRectF(10.0, 10.0, 80.0, 24.0))
        window._canvas._scene.addItem(added)
        window._canvas._overlay_items.append(added)
        window._canvas._select_blot_frame(frame, additive=False)

        window._canvas._select_overlay_blot_item(added, additive=True)

        self.assertTrue(frame.isSelected())
        self.assertTrue(added.isSelected())

    def test_new_overlay_text_box_uses_compact_default_size(self) -> None:
        canvas = FigureCanvas()

        text = canvas.add_overlay_text_box()

        self.assertLessEqual(text.textWidth(), 60.0)
        self.assertLessEqual(text.editor_rect().height(), 28.0)

    def test_empty_overlay_text_boxes_are_not_serialized_or_exported(self) -> None:
        canvas = FigureCanvas()
        empty = OverlayTextItem("", rect=None)
        filled = OverlayTextItem("Keep", rect=None)
        canvas._scene.addItem(empty)
        canvas._scene.addItem(filled)
        canvas._overlay_items.extend([empty, filled])

        data = canvas.overlay_items_as_json_data()
        export_items = canvas.overlay_as_layout_items()

        self.assertEqual([item["text"] for item in data if item.get("type") == "text"], ["Keep"])
        self.assertEqual([item.text for item in export_items if item.kind == "label"], ["Keep"])

    def test_layout_engine_skips_empty_builtin_text_items(self) -> None:
        project = TemplateEngine.build_project("normal_wb", 1, 2, 4)
        project.panels[0].title = ""
        project.panels[0].panel_letter = ""
        project.panels[0].blot_slots[0].label = ""
        project.panels[0].blot_slots[0].mw_marker = ""

        layout = LayoutEngine().compute(project)

        self.assertTrue(
            all(
                item.text.strip()
                for item in layout.items
                if item.kind in {"label", "mw", "title", "panel_letter", "table_cell"}
            )
        )

    def test_align_text_boxes_can_align_selected_blot_frames(self) -> None:
        canvas = self._render_default_canvas()
        frames = list(canvas._blot_frames.items())
        self.assertGreaterEqual(len(frames), 2)
        first_key, first_frame = frames[0]
        second_key, second_frame = frames[1]
        first_content_start = [
            QPointF(item.pos()) for item in canvas._blot_content_items[first_key]
        ]
        second_content_start = [
            QPointF(item.pos()) for item in canvas._blot_content_items[second_key]
        ]
        canvas._select_blot_frame(first_frame, additive=False)
        canvas._select_blot_frame(second_frame, additive=True)

        self.assertTrue(canvas.align_selected_text_boxes("top"))

        self.assertAlmostEqual(
            first_frame.sceneBoundingRect().top(),
            second_frame.sceneBoundingRect().top(),
        )
        self.assertNotEqual(second_content_start, [
            QPointF(item.pos()) for item in canvas._blot_content_items[second_key]
        ])
        self.assertEqual(first_content_start, [
            QPointF(item.pos()) for item in canvas._blot_content_items[first_key]
        ])

    def test_match_selected_overlay_text_sizes_to_smallest(self) -> None:
        canvas = FigureCanvas()
        text_a = OverlayTextItem("A", QRectF(0, 0, 42, 22))
        text_b = OverlayTextItem("B", QRectF(80, 0, 96, 48))
        canvas._scene.addItem(text_a)
        canvas._scene.addItem(text_b)
        canvas._overlay_items.extend([text_a, text_b])
        text_a.setSelected(True)
        text_b.setSelected(True)

        self.assertTrue(canvas.match_selected_item_sizes("smallest"))

        self.assertAlmostEqual(text_b.editor_rect().width(), text_a.editor_rect().width())
        self.assertAlmostEqual(text_b.editor_rect().height(), text_a.editor_rect().height())

    def test_match_selected_blot_sizes_to_largest(self) -> None:
        canvas = self._render_default_canvas()
        frames = list(canvas._blot_frames.items())
        self.assertGreaterEqual(len(frames), 2)
        first_key, first_frame = frames[0]
        second_key, second_frame = frames[1]
        second_frame.setRect(QRectF(0, 0, first_frame.rect().width() + 30, first_frame.rect().height() + 9))
        canvas._select_blot_frame(first_frame, additive=False)
        canvas._select_blot_frame(second_frame, additive=True)

        self.assertTrue(canvas.match_selected_item_sizes("largest"))

        self.assertAlmostEqual(first_frame.rect().width(), second_frame.rect().width())
        self.assertAlmostEqual(first_frame.rect().height(), second_frame.rect().height())
        first_placeholder = next(
            item
            for item in canvas._blot_content_items[first_key]
            if isinstance(item, QGraphicsRectItem)
        )
        self.assertAlmostEqual(first_placeholder.rect().width(), second_frame.rect().width())

    def test_builtin_templates_do_not_render_template_lines(self) -> None:
        for template_id in TEMPLATES:
            project = TemplateEngine.build_project(template_id)
            layout = LayoutEngine().compute(project)
            self.assertFalse(
                any(item.kind == "line" for item in layout.items),
                template_id,
            )

    def test_text_alignment_moves_selected_text_boxes(self) -> None:
        canvas = FigureCanvas()
        left_text = OverlayTextItem("A", rect=None)
        right_text = OverlayTextItem("B", rect=None)
        left_text.setPos(0, 0)
        right_text.setPos(100, 40)
        canvas._scene.addItem(left_text)
        canvas._scene.addItem(right_text)
        canvas._overlay_items.extend([left_text, right_text])
        left_text.setSelected(True)
        right_text.setSelected(True)

        self.assertTrue(canvas.align_selected_text_boxes("right"))

        self.assertAlmostEqual(
            left_text.sceneBoundingRect().right(),
            right_text.sceneBoundingRect().right(),
        )

    def test_text_content_alignment_and_copy_paste(self) -> None:
        canvas = FigureCanvas()
        text = OverlayTextItem("A", rect=None)
        canvas._scene.addItem(text)
        canvas._overlay_items.append(text)
        text.setSelected(True)

        canvas.apply_selected_text_content_alignment("center")
        self.assertEqual(text.text_align(), "center")

        self.assertTrue(canvas.copy_selected_text_boxes())
        self.assertTrue(canvas.paste_copied_text_boxes())

        self.assertEqual(len(canvas._overlay_items), 2)
        self.assertEqual(canvas._overlay_items[-1].text_align(), "center")

    def test_empty_space_click_clears_text_highlight_and_selection(self) -> None:
        canvas = FigureCanvas()
        text = OverlayTextItem("MAP3K2", QRectF(100, 100, 80, 24))
        canvas._scene.addItem(text)
        canvas._overlay_items.append(text)
        text.setSelected(True)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        text.setFocus(Qt.FocusReason.MouseFocusReason)
        cursor = text.textCursor()
        cursor.setPosition(0)
        cursor.movePosition(
            QTextCursor.MoveOperation.Right,
            QTextCursor.MoveMode.KeepAnchor,
            3,
        )
        text.setTextCursor(cursor)

        canvas.mousePressEvent(_FakeMouseEvent(QPointF(0, 0), pos=QPoint(0, 0)))

        self.assertFalse(text.textCursor().hasSelection())
        self.assertFalse(text.isSelected())
        self.assertFalse(text.hasFocus())
        self.assertEqual(text.textInteractionFlags(), Qt.TextInteractionFlag.NoTextInteraction)

    def test_clear_blot_selection_notifies_window(self) -> None:
        canvas = self._render_default_canvas()
        _key, frame = next(iter(canvas._blot_frames.items()))
        called = []
        canvas.on_blot_selection_cleared = lambda: called.append(True)
        canvas._select_blot_frame(frame, additive=False)

        canvas._clear_blot_selection()

        self.assertEqual(canvas.selected_blot_refs(), [])
        self.assertEqual(called, [True])

    def test_blot_frame_border_styles_follow_selection_state(self) -> None:
        canvas = self._render_default_canvas()
        _key, frame = next(iter(canvas._blot_frames.items()))

        self.assertEqual(frame.pen().color().name().lower(), "#000000")
        self.assertEqual(frame.pen().style(), Qt.PenStyle.SolidLine)
        self.assertAlmostEqual(frame.pen().widthF(), 1.0)

        added = canvas.add_overlay_blot_frame()
        self.assertEqual(added.pen().color().name().lower(), "#b96f73")
        self.assertEqual(added.pen().style(), Qt.PenStyle.SolidLine)
        self.assertAlmostEqual(added.pen().widthF(), 3.0)

        added.setSelected(False)
        self.assertEqual(added.pen().color().name().lower(), "#000000")
        self.assertAlmostEqual(added.pen().widthF(), 1.0)

    def test_render_page_image_preserves_selection_state(self) -> None:
        canvas = self._render_default_canvas()
        added = canvas.add_overlay_blot_frame()
        added.setSelected(True)

        image = canvas.render_page_image(scale=1.0)

        self.assertFalse(image.isNull())
        self.assertTrue(added.isSelected())

    def test_canvas_workspace_background_is_white_without_page_outline(self) -> None:
        canvas = self._render_default_canvas()

        self.assertEqual(canvas.backgroundBrush().color().name().lower(), "#ffffff")
        self.assertIsNotNone(canvas._background_item)
        self.assertEqual(
            canvas._background_item.brush().color().name().lower(),
            "#ffffff",
        )
        self.assertEqual(
            canvas._background_item.pen().style(),
            Qt.PenStyle.NoPen,
        )


class FigureModeStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_sidebar_combines_template_and_structure_controls_in_layout_group(self) -> None:
        window = FigureModeWindow()

        self.assertEqual(window._grp1.title_text(), "Layout")
        self.assertEqual(window._grp4.title_text(), "Draw Band with ROI-Hit Enter")
        self.assertEqual(window._grp5.title_text(), "Saved Blot Files")

        self.assertIs(window._sidebar_layout.itemAt(1).widget(), window._grp1)
        self.assertIs(window._sidebar_layout.itemAt(2).widget(), window._grp4)
        self.assertIs(window._sidebar_layout.itemAt(3).widget(), window._grp5)
        self.assertIs(window._sidebar_layout.itemAt(4).widget(), window._grp6)
        self.assertFalse(window._grp4.isHidden())
        self.assertFalse(window._grp4._expanded)

        self.assertIsNotNone(window._panels_spin)
        self.assertIsNotNone(window._template_list)
        self.assertIsNotNone(window._blot_file_list)
        labels = [label.text() for label in window._grp1.findChildren(QLabel)]
        self.assertIn("Blots", labels)
        self.assertNotIn("Blots / panel:", labels)

    def test_roi_controls_expand_only_for_selected_blot_frame(self) -> None:
        window = FigureModeWindow()
        window._on_apply_structure()

        self.assertFalse(window._grp4._expanded)

        window._on_canvas_blot_selected(SourceRef(panel_idx=0, slot_idx=0))
        self.assertTrue(window._grp4._expanded)

        window._on_canvas_blot_selection_cleared()
        self.assertFalse(window._grp4._expanded)

    def test_new_overlay_text_is_selected_and_enables_font_controls(self) -> None:
        window = FigureModeWindow()

        text = window._canvas.add_overlay_text_box()
        self._app.processEvents()

        self.assertTrue(text.isSelected())
        self.assertTrue(window._toolbar_font_family_combo.isEnabled())
        self.assertTrue(window._toolbar_font_menu_btn.isEnabled())
        self.assertTrue(window._toolbar_font_size_combo.isEnabled())
        self.assertFalse(window._selection_detail_toolbar.isHidden())
        self.assertFalse(window._text_rotation_spin.isHidden())
        self.assertTrue(window._line_width_spin.isHidden())

        window._toolbar_font_size_combo.setCurrentText("18")
        self.assertEqual(text.font().pointSizeF(), 18.0)

        window._canvas.add_overlay_line()
        self._app.processEvents()
        self.assertTrue(window._text_rotation_spin.isHidden())
        self.assertFalse(window._line_width_spin.isHidden())

    def test_apply_structure_resizes_current_project_in_place(self) -> None:
        window = FigureModeWindow()
        window._project = TemplateEngine.build_project("normal_wb", 1, 2, 4)
        window._active_template_id = "normal_wb"
        window._active_table_style = "none"

        slot = window._project.panels[0].blot_slots[0]
        slot.label = "IB: Preserved"
        slot.source_image_path = "/tmp/source.tif"
        slot.bounding_box = ImageBBox(1, 2, 30, 12)
        slot.reset_equal_lanes()

        window._panels_spin.setValue(1)
        window._blots_spin.setValue(3)
        window._lanes_spin.setValue(6)
        window._on_apply_structure()

        resized_slot = window._project.panels[0].blot_slots[0]
        self.assertEqual(len(window._project.panels[0].blot_slots), 3)
        self.assertEqual(resized_slot.label, "IB: Preserved")
        self.assertEqual(resized_slot.source_image_path, "/tmp/source.tif")
        self.assertEqual(resized_slot.bounding_box, ImageBBox(1, 2, 30, 12))
        self.assertEqual(resized_slot.lane_count, 6)

    def test_layout_engine_does_not_render_panel_letters(self) -> None:
        project = TemplateEngine.build_project("multi_panel", 3, 2, 4)

        layout = LayoutEngine().compute(project)

        self.assertFalse(any(item.kind == "panel_letter" for item in layout.items))

    def test_blot_and_condition_table_width_follow_lane_count(self) -> None:
        three_lane = LayoutEngine().compute(
            TemplateEngine.build_project("dose_response", 1, 2, 3)
        )
        six_lane = LayoutEngine().compute(
            TemplateEngine.build_project("dose_response", 1, 2, 6)
        )
        eight_lane = LayoutEngine().compute(
            TemplateEngine.build_project("dose_response", 1, 2, 8)
        )

        three_lane_blot = next(item for item in three_lane.items if item.kind == "blot")
        six_lane_blot = next(item for item in six_lane.items if item.kind == "blot")
        eight_lane_blot = next(item for item in eight_lane.items if item.kind == "blot")
        three_lane_cell = next(
            item for item in three_lane.items
            if item.kind == "table_cell"
            and item.source_ref is not None
            and item.source_ref.table_row == 1
            and item.source_ref.table_col == 1
        )

        self.assertLess(three_lane_blot.w_pt, six_lane_blot.w_pt)
        self.assertGreater(eight_lane_blot.w_pt, six_lane_blot.w_pt)
        self.assertAlmostEqual(three_lane_cell.w_pt, three_lane_blot.w_pt / 3)

    def test_added_panels_clone_current_panel_template_without_image_payload(self) -> None:
        window = FigureModeWindow()
        window._project = TemplateEngine.build_project("dose_response", 1, 2, 6)
        window._active_template_id = "dose_response"
        window._active_table_style = "group_dose"

        source_panel = window._project.panels[0]
        source_panel.title = "Shared panel title"
        source_panel.condition_table.rows[1][0] = "Custom Group"
        source_panel.blot_slots[0].label = "IB: Preserved"
        source_panel.blot_slots[0].source_image_path = "/tmp/source.tif"
        source_panel.blot_slots[0].bounding_box = ImageBBox(1, 2, 30, 12)

        window._panels_spin.setValue(2)
        window._blots_spin.setValue(2)
        window._lanes_spin.setValue(5)
        window._on_apply_structure()

        added_panel = window._project.panels[1]
        added_slot = added_panel.blot_slots[0]
        self.assertEqual(added_panel.title, "Shared panel title")
        self.assertEqual(added_panel.condition_table.rows[1][0], "Custom Group")
        self.assertEqual(added_slot.label, "IB: Preserved")
        self.assertEqual(added_slot.source_image_path, "")
        self.assertIsNone(added_slot.bounding_box)
        self.assertEqual(added_slot.lane_count, 5)

    def test_user_template_preserves_canvas_format_state(self) -> None:
        old_dir = template_module.USER_TEMPLATES_DIR
        old_hidden_path = template_module.HIDDEN_BUILTIN_TEMPLATES_PATH
        old_templates = dict(TemplateEngine._user_templates)
        old_hidden = set(TemplateEngine._hidden_builtin_templates)
        try:
            with TemporaryDirectory() as tmp:
                template_module.USER_TEMPLATES_DIR = Path(tmp)
                template_module.HIDDEN_BUILTIN_TEMPLATES_PATH = Path(tmp) / "_hidden_builtin_templates.json"
                TemplateEngine._user_templates.clear()
                TemplateEngine._hidden_builtin_templates.clear()

                project = TemplateEngine.build_project("dose_response", 1, 2, 4)
                canvas_state = {
                    "hidden_text_keys": [[0, None, None, None, None, "title"]],
                    "fine_offsets": [
                        {"key": [0, 0, None, None, None, "label"], "x": 9.0, "y": 3.0}
                    ],
                    "blot_offsets": [
                        {"key": [0, 0, None, None, None, "blot"], "x": 4.0, "y": 2.0}
                    ],
                    "text_box_sizes": [
                        {"key": [0, 0, None, None, None, "label"], "w": 88.0, "h": 22.0}
                    ],
                    "overlay_items": [],
                }
                styles = {
                    (0, 0, None, None, None, "label"): {
                        "font_family": "Helvetica",
                        "font_size_pt": 18.0,
                        "bold": True,
                        "italic": False,
                        "underline": True,
                        "align": "center",
                    }
                }

                tmpl = TemplateEngine.save_user_template(
                    "Format State",
                    project,
                    [],
                    canvas_state=canvas_state,
                    text_style_overrides=styles,
                )
                _restored_project, overlay = TemplateEngine.restore_user_project(tmpl.id)
                format_state = TemplateEngine.restore_user_template_format_state(tmpl.id)

                self.assertEqual(overlay, [])
                self.assertEqual(format_state["canvas_state"]["text_box_sizes"], canvas_state["text_box_sizes"])
                self.assertEqual(format_state["text_style_overrides"], styles)
        finally:
            template_module.USER_TEMPLATES_DIR = old_dir
            template_module.HIDDEN_BUILTIN_TEMPLATES_PATH = old_hidden_path
            TemplateEngine._user_templates = old_templates
            TemplateEngine._hidden_builtin_templates = old_hidden

    def test_blot_file_save_and_load_preserves_images_and_editable_state(self) -> None:
        old_dir = figure_mode_module.USER_BLOT_FILES_DIR
        try:
            with TemporaryDirectory() as tmp:
                figure_mode_module.USER_BLOT_FILES_DIR = Path(tmp) / "blot_files"
                source_path = Path(tmp) / "source.tif"
                Image.fromarray(np.full((20, 30), 30000, dtype=np.uint16)).save(source_path)

                window = FigureModeWindow()
                window._on_apply_template()
                slot = window._project.panels[0].blot_slots[0]
                slot.source_image_path = str(source_path)
                slot.bounding_box = ImageBBox(2.0, 3.0, 18.0, 9.0)
                slot.reset_equal_lanes()
                empty_added = BlotPlaceholderItem(QRectF(20.0, 40.0, 90.0, 24.0))
                image_added = BlotPlaceholderItem(
                    QRectF(120.0, 40.0, 90.0, 24.0),
                    image_path=str(source_path),
                    roi={"x": 1.0, "y": 2.0, "w": 12.0, "h": 8.0},
                )
                window._canvas._scene.addItem(empty_added)
                window._canvas._scene.addItem(image_added)
                window._canvas._overlay_items.extend([empty_added, image_added])

                window._write_blot_file("blot_test", "Blot Test")

                saved_path = figure_mode_module.USER_BLOT_FILES_DIR / "blot_test.json"
                saved_data = json.loads(saved_path.read_text(encoding="utf-8"))
                saved_slot_path = Path(saved_data["project"]["panels"][0]["blot_slots"][0]["source_image_path"])
                self.assertTrue(saved_slot_path.exists())
                self.assertNotEqual(saved_slot_path, source_path)

                restored = FigureModeWindow()
                restored._load_blot_file("blot_test")

                restored_slot = restored._project.panels[0].blot_slots[0]
                self.assertTrue(Path(restored_slot.source_image_path).exists())
                self.assertEqual(restored_slot.bounding_box, ImageBBox(2.0, 3.0, 18.0, 9.0))
                overlays = restored._canvas.overlay_items_as_json_data()
                blot_overlays = [item for item in overlays if item.get("type") == "blot"]
                self.assertEqual(len(blot_overlays), 2)
                self.assertFalse(blot_overlays[0].get("image_path"))
                self.assertTrue(Path(blot_overlays[1].get("image_path")).exists())
        finally:
            figure_mode_module.USER_BLOT_FILES_DIR = old_dir

    def test_user_template_rename_updates_name_without_changing_template_id(self) -> None:
        old_dir = template_module.USER_TEMPLATES_DIR
        old_hidden_path = template_module.HIDDEN_BUILTIN_TEMPLATES_PATH
        old_templates = dict(TemplateEngine._user_templates)
        old_hidden = set(TemplateEngine._hidden_builtin_templates)
        try:
            with TemporaryDirectory() as tmp:
                template_module.USER_TEMPLATES_DIR = Path(tmp)
                template_module.HIDDEN_BUILTIN_TEMPLATES_PATH = Path(tmp) / "_hidden_builtin_templates.json"
                TemplateEngine._user_templates.clear()
                TemplateEngine._hidden_builtin_templates.clear()

                project = TemplateEngine.build_project("normal_wb", 1, 2, 4)
                tmpl = TemplateEngine.save_user_template("Original Name", project, [])
                renamed = TemplateEngine.rename_user_template(tmpl.id, "Renamed Template")

                data = json.loads((Path(tmp) / f"{tmpl.id}.json").read_text(encoding="utf-8"))
                restored_project, overlay = TemplateEngine.restore_user_project(tmpl.id)

                self.assertEqual(renamed.id, tmpl.id)
                self.assertEqual(data["id"], tmpl.id)
                self.assertEqual(data["name"], "Renamed Template")
                self.assertEqual(TemplateEngine.get_template(tmpl.id).display_name, "Renamed Template")
                self.assertEqual(restored_project.template_type, tmpl.id)
                self.assertEqual(overlay, [])
        finally:
            template_module.USER_TEMPLATES_DIR = old_dir
            template_module.HIDDEN_BUILTIN_TEMPLATES_PATH = old_hidden_path
            TemplateEngine._user_templates = old_templates
            TemplateEngine._hidden_builtin_templates = old_hidden

    def test_update_user_template_overwrites_existing_template_without_new_id(self) -> None:
        old_dir = template_module.USER_TEMPLATES_DIR
        old_hidden_path = template_module.HIDDEN_BUILTIN_TEMPLATES_PATH
        old_templates = dict(TemplateEngine._user_templates)
        old_hidden = set(TemplateEngine._hidden_builtin_templates)
        try:
            with TemporaryDirectory() as tmp:
                template_module.USER_TEMPLATES_DIR = Path(tmp)
                template_module.HIDDEN_BUILTIN_TEMPLATES_PATH = Path(tmp) / "_hidden_builtin_templates.json"
                TemplateEngine._user_templates.clear()
                TemplateEngine._hidden_builtin_templates.clear()

                project = TemplateEngine.build_project("normal_wb", 1, 2, 4)
                tmpl = TemplateEngine.save_user_template("Current Template", project, [])

                project.panels[0].blot_slots[0].label = "IB: Updated"
                overlay_data = [
                    {
                        "type": "line",
                        "x1": 10.0,
                        "y1": 12.0,
                        "x2": 45.0,
                        "y2": 12.0,
                        "width": 1.5,
                        "color": "#000000",
                    }
                ]
                canvas_state = {
                    "overlay_items": overlay_data,
                    "hidden_text_keys": [],
                    "fine_offsets": [],
                    "blot_offsets": [
                        {"key": [0, 0, None, None, None, "blot"], "x": 6.0, "y": 7.0}
                    ],
                    "text_box_sizes": [],
                }

                updated = TemplateEngine.update_user_template(
                    tmpl.id,
                    project,
                    overlay_data,
                    canvas_state=canvas_state,
                    text_style_overrides={},
                )
                restored_project, restored_overlay = TemplateEngine.restore_user_project(tmpl.id)
                format_state = TemplateEngine.restore_user_template_format_state(tmpl.id)
                files = list(Path(tmp).glob("*.json"))

                self.assertEqual(updated.id, tmpl.id)
                self.assertEqual(updated.display_name, "Current Template")
                self.assertEqual(len(files), 1)
                self.assertEqual(restored_project.panels[0].blot_slots[0].label, "IB: Updated")
                self.assertEqual(restored_overlay, overlay_data)
                self.assertEqual(format_state["canvas_state"]["blot_offsets"], canvas_state["blot_offsets"])
        finally:
            template_module.USER_TEMPLATES_DIR = old_dir
            template_module.HIDDEN_BUILTIN_TEMPLATES_PATH = old_hidden_path
            TemplateEngine._user_templates = old_templates
            TemplateEngine._hidden_builtin_templates = old_hidden

    def test_chained_user_template_preserves_moved_builtin_blot_frame(self) -> None:
        old_dir = template_module.USER_TEMPLATES_DIR
        old_hidden_path = template_module.HIDDEN_BUILTIN_TEMPLATES_PATH
        old_templates = dict(TemplateEngine._user_templates)
        old_hidden = set(TemplateEngine._hidden_builtin_templates)
        try:
            with TemporaryDirectory() as tmp:
                template_module.USER_TEMPLATES_DIR = Path(tmp)
                template_module.HIDDEN_BUILTIN_TEMPLATES_PATH = Path(tmp) / "_hidden_builtin_templates.json"
                TemplateEngine._user_templates.clear()
                TemplateEngine._hidden_builtin_templates.clear()

                project = TemplateEngine.build_project("normal_wb", 1, 2, 4)
                first = TemplateEngine.save_user_template(
                    "Template One",
                    project,
                    [],
                    canvas_state={
                        "overlay_items": [],
                        "hidden_text_keys": [],
                        "fine_offsets": [],
                        "blot_offsets": [
                            {
                                "key": [0, 0, None, None, None, "blot"],
                                "x": 12.0,
                                "y": 4.0,
                            }
                        ],
                        "text_box_sizes": [],
                    },
                    text_style_overrides={},
                )

                window = FigureModeWindow()
                window._active_template_id = first.id
                window._project, _overlay = TemplateEngine.restore_user_project(first.id)
                format_state = TemplateEngine.restore_user_template_format_state(first.id)
                window._canvas.restore_state_snapshot(
                    format_state["canvas_state"],
                    repopulate_scene=False,
                )
                window._recompute_and_refresh(fit_view=False)

                key, frame = next(iter(window._canvas._blot_frames.items()))
                frame.setPos(frame.pos() + QPointF(30.0, 8.0))
                second = TemplateEngine.save_user_template(
                    "Template Two",
                    window._project,
                    window._canvas.overlay_items_as_json_data(),
                    canvas_state=window._canvas.state_snapshot(),
                    text_style_overrides=window._text_style_overrides,
                )

                second_state = TemplateEngine.restore_user_template_format_state(second.id)["canvas_state"]
                self.assertIn(
                    {
                        "key": list(key),
                        "x": 42.0,
                        "y": 12.0,
                    },
                    second_state["blot_offsets"],
                )
        finally:
            template_module.USER_TEMPLATES_DIR = old_dir
            template_module.HIDDEN_BUILTIN_TEMPLATES_PATH = old_hidden_path
            TemplateEngine._user_templates = old_templates
            TemplateEngine._hidden_builtin_templates = old_hidden

    def test_added_panels_duplicate_first_panel_overlay_annotations(self) -> None:
        window = FigureModeWindow()
        window._project = TemplateEngine.build_project("dose_response", 1, 2, 5)
        window._active_template_id = "dose_response"
        window._active_table_style = "group_dose"
        window._canvas._restore_overlay_from_data([
            {
                "type": "text",
                "text": "HEK293T HA Tag",
                "x": 200.0,
                "y": 50.0,
                "width": 120.0,
                "height": 24.0,
                "font_family": "Arial",
                "font_size": 12.0,
                "bold": True,
                "italic": False,
                "underline": False,
                "text_align": "center",
            },
            {
                "type": "line",
                "x": 0.0,
                "y": 45.0,
                "x1": 160.0,
                "y1": 30.0,
                "x2": 360.0,
                "y2": 30.0,
                "rotation": 0.0,
                "dashed": False,
            },
        ])

        window._panels_spin.setValue(2)
        window._blots_spin.setValue(2)
        window._lanes_spin.setValue(5)
        window._on_apply_structure()

        overlay_data = window._canvas.overlay_items_as_json_data()
        self.assertEqual(len(overlay_data), 4)
        text_items = [item for item in overlay_data if item.get("type") == "text"]
        line_items = [item for item in overlay_data if item.get("type") == "line"]
        self.assertEqual(len(text_items), 2)
        self.assertEqual(len(line_items), 2)
        self.assertGreater(text_items[1]["y"], text_items[0]["y"])
        self.assertGreater(line_items[1]["y"], line_items[0]["y"])

    def test_inter_panel_gap_is_half_of_layout_setting(self) -> None:
        project = TemplateEngine.build_project("normal_wb", 2, 1, 4)
        layout = LayoutEngine().compute(project)
        first_panel_items = [
            item for item in layout.items
            if item.source_ref is not None and item.source_ref.panel_idx == 0
        ]
        second_panel_items = [
            item for item in layout.items
            if item.source_ref is not None and item.source_ref.panel_idx == 1
        ]

        first_bottom = max(item.y_pt + item.h_pt for item in first_panel_items)
        second_top = min(item.y_pt for item in second_panel_items)
        self.assertAlmostEqual(
            second_top - first_bottom,
            project.global_layout.inter_panel_gap_pt * 0.25,
        )

    def test_step2_allows_fifteen_panels_and_blots(self) -> None:
        window = FigureModeWindow()

        self.assertEqual(window._panels_spin.maximum(), 15)
        self.assertEqual(window._blots_spin.maximum(), 15)

    def test_many_panels_or_blots_extend_canvas_height_not_width(self) -> None:
        engine = LayoutEngine()
        few_blots = engine.compute(TemplateEngine.build_project("normal_wb", 1, 5, 6))
        many_blots = engine.compute(TemplateEngine.build_project("normal_wb", 1, 15, 6))
        few_panels = engine.compute(TemplateEngine.build_project("normal_wb", 3, 5, 6))
        many_panels = engine.compute(TemplateEngine.build_project("normal_wb", 15, 5, 6))

        self.assertEqual(many_blots.canvas_width_pt, few_blots.canvas_width_pt)
        self.assertGreater(many_blots.canvas_height_pt, few_blots.canvas_height_pt)
        self.assertEqual(many_panels.canvas_width_pt, few_panels.canvas_width_pt)
        self.assertGreater(many_panels.canvas_height_pt, few_panels.canvas_height_pt)

    def test_apply_roi_to_selected_slot_can_rerender_repeatedly(self) -> None:
        window = FigureModeWindow()
        window._on_apply_template()
        _key, frame = next(iter(window._canvas._blot_frames.items()))
        window._canvas._select_blot_frame(frame, additive=False)

        with TemporaryDirectory() as tmp:
            path = f"{tmp}/source.tif"
            Image.fromarray(
                np.full((20, 30), 30000, dtype=np.uint16)
            ).save(path)
            window.set_active_image_provider(
                lambda: {
                    "image_path": path,
                    "roi": QRectF(2.0, 3.0, 18.0, 9.0),
                    "image_transform": {
                        "low": 123,
                        "high": 4567,
                        "gamma": 1.5,
                        "inverted": False,
                    },
                }
            )

            self.assertTrue(window.apply_roi_to_selected_slot())
            self.assertTrue(window.apply_roi_to_selected_slot())

        slot = window._project.panels[0].blot_slots[0]
        self.assertEqual(slot.bounding_box, ImageBBox(2.0, 3.0, 18.0, 9.0))
        self.assertEqual(
            slot.image_transform,
            {
                "low": 123,
                "high": 4567,
                "gamma": 1.5,
            "inverted": False,
            },
        )

    def test_copied_blot_frame_is_free_movable_and_roi_syncable(self) -> None:
        window = FigureModeWindow()
        window._project = TemplateEngine.build_project("normal_wb", 2, 2, 4)
        window._active_template_id = "normal_wb"
        window._active_table_style = "none"
        window._rebuild_step4()
        window._recompute_and_refresh(fit_view=False)

        with TemporaryDirectory() as tmp:
            path = f"{tmp}/source.tif"
            Image.fromarray(
                np.full((30, 40), 30000, dtype=np.uint16)
            ).save(path)

            source_slot = window._project.panels[0].blot_slots[0]
            source_slot.label = "IB: Source"
            source_slot.mw_marker = "55 kDa"
            source_slot.source_image_path = path
            source_slot.bounding_box = ImageBBox(1.0, 2.0, 20.0, 10.0)
            source_slot.display_width_pt = 144.0
            source_slot.display_height_pt = 24.0
            source_slot.image_transform = {
                "low": 10,
                "high": 5000,
                "gamma": 1.2,
                "inverted": False,
            }
            source_slot.reset_equal_lanes()
            source_ref = SourceRef(panel_idx=0, slot_idx=0, field="blot")
            window._canvas._blot_offsets[source_ref.key()] = QPointF(6.0, 4.0)
            window._recompute_and_refresh(fit_view=False)
            original_canvas_height = window._layout_result.canvas_height_pt

            frame = window._canvas._blot_frames[source_ref.key()]
            window._canvas._select_blot_frame(frame, additive=False)
            self.assertTrue(window._canvas.copy_selected_text_boxes())
            self.assertTrue(window._canvas.paste_copied_text_boxes())

            self.assertEqual(len(window._project.panels[0].blot_slots), 2)
            self.assertEqual(len(window._project.panels[1].blot_slots), 2)
            self.assertEqual(window._layout_result.canvas_height_pt, original_canvas_height)
            pasted = window._canvas.selected_overlay_blot_items()
            self.assertEqual(len(pasted), 1)
            pasted_blot = pasted[0]
            self.assertIsInstance(pasted_blot, BlotPlaceholderItem)
            self.assertEqual(pasted_blot.image_path, path)
            self.assertEqual(pasted_blot.roi, {"x": 1.0, "y": 2.0, "w": 20.0, "h": 10.0})
            self.assertEqual(pasted_blot.transform, source_slot.image_transform)
            self.assertAlmostEqual(pasted_blot.rect().width(), frame.rect().width())
            self.assertAlmostEqual(pasted_blot.rect().height(), frame.rect().height())

            moved_pos = QPointF(frame.pos().x() + frame.rect().width() + 24.0, frame.pos().y())
            pasted_blot.setPos(moved_pos)

            window.set_active_image_provider(
                lambda: {
                    "image_path": path,
                    "roi": QRectF(5.0, 6.0, 12.0, 7.0),
                    "image_transform": {
                        "low": 123,
                        "high": 4567,
                        "gamma": 1.5,
                        "inverted": True,
                    },
                }
            )
            self.assertTrue(window.apply_roi_to_selected_slot())
            self.assertEqual(pasted_blot.pos(), moved_pos)
            self.assertEqual(len(window._project.panels[0].blot_slots), 2)
            self.assertEqual(window._layout_result.canvas_height_pt, original_canvas_height)

        self.assertEqual(
            window._project.panels[0].blot_slots[0].bounding_box,
            ImageBBox(1.0, 2.0, 20.0, 10.0),
        )
        self.assertEqual(pasted_blot.roi, {"x": 5.0, "y": 6.0, "w": 12.0, "h": 7.0})
        self.assertEqual(pasted_blot.transform, {
            "low": 123,
            "high": 4567,
            "gamma": 1.5,
            "inverted": True,
        })

    def test_add_blot_frame_button_creates_free_roi_target(self) -> None:
        window = FigureModeWindow()
        window._project = TemplateEngine.build_project("normal_wb", 1, 2, 4)
        window._active_template_id = "normal_wb"
        window._active_table_style = "none"
        window._rebuild_step4()
        window._recompute_and_refresh(fit_view=False)
        original_canvas_height = window._layout_result.canvas_height_pt

        window._on_add_blot_frame()

        added = window._canvas.selected_overlay_blot_items()
        self.assertEqual(len(added), 1)
        added_blot = added[0]
        self.assertIsInstance(added_blot, BlotPlaceholderItem)
        self.assertEqual(added_blot.image_path, None)
        self.assertEqual(
            window._selected_slot_lbl.text(),
            "Selected target: added blot frame",
        )
        self.assertEqual(len(window._project.panels[0].blot_slots), 2)
        self.assertEqual(window._layout_result.canvas_height_pt, original_canvas_height)

        start_pos = QPointF(added_blot.pos())
        key = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Right,
            Qt.KeyboardModifier.ShiftModifier,
        )
        window._canvas.keyPressEvent(key)
        self.assertEqual(added_blot.pos(), start_pos + QPointF(5.0, 0.0))

        with TemporaryDirectory() as tmp:
            path = f"{tmp}/source.tif"
            Image.fromarray(np.full((30, 40), 30000, dtype=np.uint16)).save(path)
            window.set_active_image_provider(
                lambda: {
                    "image_path": path,
                    "roi": QRectF(4.0, 5.0, 16.0, 8.0),
                    "image_transform": {
                        "low": 100,
                        "high": 6000,
                        "gamma": 1.0,
                        "inverted": False,
                    },
                }
            )
            self.assertTrue(window.apply_roi_to_selected_slot())

        self.assertEqual(added_blot.image_path, path)
        self.assertEqual(added_blot.roi, {"x": 4.0, "y": 5.0, "w": 16.0, "h": 8.0})
        self.assertEqual(added_blot.transform, {
            "low": 100,
            "high": 6000,
            "gamma": 1.0,
            "inverted": False,
        })
        self.assertEqual(added_blot.pos(), start_pos + QPointF(5.0, 0.0))
        self.assertEqual(len(window._project.panels[0].blot_slots), 2)
        self.assertEqual(window._layout_result.canvas_height_pt, original_canvas_height)

    def test_canvas_undo_restores_last_ten_snapshots(self) -> None:
        window = FigureModeWindow()

        for _ in range(12):
            window._canvas.add_overlay_text_box()

        self.assertEqual(len(window._canvas_undo_stack), 10)
        self.assertEqual(len(window._canvas._overlay_items), 12)

        window._undo_canvas_state()

        self.assertEqual(len(window._canvas._overlay_items), 11)
        self.assertEqual(len(window._canvas_undo_stack), 9)

    def test_undo_button_restores_builtin_text_position(self) -> None:
        window = FigureModeWindow()
        window._project = TemplateEngine.build_project("normal_wb", 1, 2, 4)
        window._recompute_and_refresh()
        text_item = next(
            item for item in window._canvas._scene.items()
            if isinstance(item, EditableTextItem)
        )
        key = text_item.source_ref.key()
        original_pos = QPointF(text_item.pos())

        window._remember_canvas_undo_state()
        text_item.setPos(original_pos + QPointF(20.0, 8.0))
        window._canvas._handle_text_position_changed(text_item)
        window._annot_undo_btn.click()

        self.assertEqual(window._canvas._text_items[key].pos(), original_pos)

    def test_command_z_event_restores_builtin_text_position(self) -> None:
        window = FigureModeWindow()
        window._project = TemplateEngine.build_project("normal_wb", 1, 2, 4)
        window._recompute_and_refresh()
        text_item = next(
            item for item in window._canvas._scene.items()
            if isinstance(item, EditableTextItem)
        )
        key = text_item.source_ref.key()
        original_pos = QPointF(text_item.pos())

        window._remember_canvas_undo_state()
        text_item.setPos(original_pos + QPointF(12.0, 5.0))
        window._canvas._handle_text_position_changed(text_item)
        event = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Z,
            Qt.KeyboardModifier.MetaModifier,
        )
        window._canvas.keyPressEvent(event)
        self._app.processEvents()
        self._app.processEvents()

        self.assertEqual(window._canvas._text_items[key].pos(), original_pos)

    def test_toolbar_align_combo_label(self) -> None:
        window = FigureModeWindow()

        self.assertEqual(window._align_text_boxes_combo.itemText(0), "Align text Boxes")
        self.assertEqual(window._align_text_boxes_combo.itemText(1), "Align Left")
        self.assertEqual(window._align_text_boxes_combo.itemText(8), "Distribute Vertically")
        self.assertTrue(window._text_inside_left_btn.text() == "")
        self.assertFalse(window._text_inside_left_btn.icon().isNull())
        self.assertFalse(window._text_inside_center_btn.icon().isNull())
        self.assertFalse(window._text_inside_right_btn.icon().isNull())
        self.assertEqual(window._text_rotation_spin.suffix(), " deg")


if __name__ == "__main__":
    unittest.main()
