import copy
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
from PIL import Image
from PySide6.QtCore import QEvent, QLineF, QPoint, QPointF, QRectF, QSize, QSizeF, QTimer, Qt
from PySide6.QtGui import QColor, QFont, QImage, QKeyEvent, QPainter, QPen, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QGraphicsItem, QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsSceneMouseEvent, QGroupBox,
    QLabel, QPushButton, QRadioButton, QSpinBox, QToolButton, QInputDialog,
)

import core.template_engine as template_module
import gui.figure_mode_window as figure_mode_module
from core.figure_project import ImageBBox, LaneROI, SourceRef
from core.layout_engine import (
    LayoutEngine,
    LayoutItem,
    LayoutResult,
    pt_to_scene,
    scene_to_pt,
)
from core.template_engine import TemplateEngine, TEMPLATES
from gui.figure_canvas import EditableTextItem, FigureCanvas
from gui.figure_mode_window import (
    FigureModeWindow, _ConditionPreviewWidget, _FramePreviewWidget,
)
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


class _FakeWheelEvent:
    def __init__(self, *, angle_y: int = 0, pixel_y: int = 0) -> None:
        self._angle_y = angle_y
        self._pixel_y = pixel_y
        self.accepted = False
        self.ignored = False

    def angleDelta(self) -> QPoint:
        return QPoint(0, self._angle_y)

    def pixelDelta(self) -> QPoint:
        return QPoint(0, self._pixel_y)

    def accept(self) -> None:
        self.accepted = True

    def ignore(self) -> None:
        self.ignored = True


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

    def test_blot_pixmap_preserves_aspect_ratio_before_and_during_resize(self) -> None:
        with TemporaryDirectory() as tmp:
            path = f"{tmp}/strip.png"
            Image.fromarray(np.full((20, 80), 128, dtype=np.uint8)).save(path)
            project = TemplateEngine.build_project("normal_wb", 1, 1, 4)
            slot = project.panels[0].blot_slots[0]
            slot.source_image_path = path
            slot.bounding_box = ImageBBox(0.0, 0.0, 80.0, 20.0)
            slot.preserve_image_aspect = True
            canvas = FigureCanvas()
            canvas.render(LayoutEngine().compute(project), project)

            key = SourceRef(panel_idx=0, slot_idx=0, field="blot").key()
            pixmap = next(
                item
                for item in canvas._blot_content_items[key]
                if isinstance(item, QGraphicsPixmapItem)
            )
            frame = canvas._blot_frames[key]
            rendered = pixmap.sceneBoundingRect()
            self.assertAlmostEqual(
                rendered.width() / rendered.height(),
                4.0,
            )
            self.assertAlmostEqual(
                rendered.center().x(), frame.sceneBoundingRect().center().x()
            )
            self.assertAlmostEqual(
                rendered.center().y(), frame.sceneBoundingRect().center().y()
            )
            self.assertLessEqual(rendered.width(), frame.rect().width() + 0.01)
            self.assertLessEqual(rendered.height(), frame.rect().height() + 0.01)

            canvas._selected_blot_keys = {key}
            canvas._resize_blot_content_to_frame(key, frame, 90.0, 90.0)
            rendered = pixmap.sceneBoundingRect()
            self.assertAlmostEqual(rendered.width(), 90.0)
            self.assertAlmostEqual(rendered.height(), 22.5)
            self.assertAlmostEqual(rendered.center().x(), frame.pos().x() + 45.0)
            self.assertAlmostEqual(rendered.center().y(), frame.pos().y() + 45.0)

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

    def test_text_rotation_handle_shows_live_angle_and_snaps_to_cardinals(self) -> None:
        canvas = FigureCanvas()
        text = OverlayTextItem("Rotate", QRectF(20.0, 30.0, 100.0, 40.0))
        canvas._scene.addItem(text)
        text.setSelected(True)

        self.assertTrue(text._rotation_handle.isVisible())
        self.assertTrue(text._rotation_connector.isVisible())
        self.assertGreater(
            text._rotation_angle_label.pos().x(),
            text._rotation_handle.pos().x(),
        )

        center = text.mapToScene(text.editor_rect().center())
        press = _FakeMouseEvent(text._rotation_handle.scenePos())
        text._rotation_handle.mousePressEvent(press)
        move = _FakeMouseEvent(center + QPointF(99.9, -5.2))
        text._rotation_handle.mouseMoveEvent(move)

        self.assertTrue(press.accepted)
        self.assertTrue(move.accepted)
        self.assertEqual(text.rotation(), 90.0)
        self.assertEqual(text.adaptive_rotation_mode(), 90)
        self.assertTrue(text._rotation_angle_label.isVisible())
        self.assertEqual(text._rotation_angle_label.text(), "90°")
        rotated_bounds = text.mapRectToScene(text.editor_rect())
        self.assertAlmostEqual(rotated_bounds.width(), 40.0, places=4)
        self.assertAlmostEqual(rotated_bounds.height(), 100.0, places=4)

        release = _FakeMouseEvent(center + QPointF(100.0, 0.0))
        text._rotation_handle.mouseReleaseEvent(release)
        self.assertTrue(release.accepted)
        self.assertFalse(text._rotation_angle_label.isVisible())
        text.setSelected(False)
        self.assertFalse(text._rotation_handle.isVisible())
        self.assertFalse(text._rotation_connector.isVisible())

    def test_rotation_handle_supports_all_cardinal_adaptive_modes(self) -> None:
        canvas = FigureCanvas()
        text = OverlayTextItem("Rotate", QRectF(0.0, 0.0, 80.0, 30.0))
        canvas._scene.addItem(text)
        text.setSelected(True)
        center = text.mapToScene(text.editor_rect().center())

        for expected, offset in (
            (0, QPointF(0.0, -100.0)),
            (90, QPointF(100.0, 0.0)),
            (180, QPointF(0.0, 100.0)),
            (270, QPointF(-100.0, 0.0)),
        ):
            text.begin_rotation()
            angle = text.rotate_from_scene_pos(center + offset)
            text.finish_rotation()
            self.assertEqual(angle, float(expected))
            self.assertEqual(text.adaptive_rotation_mode(), expected)

    def test_builtin_handle_rotation_persists_only_after_drag_finishes(self) -> None:
        canvas = self._render_default_canvas()
        text = next(item for item in canvas._scene.items() if isinstance(item, EditableTextItem))
        updates: list[dict[tuple, dict]] = []
        canvas.on_text_rotation_changed = updates.append
        text.setSelected(True)
        center = text.mapToScene(text.editor_rect().center())

        text.begin_rotation()
        text.rotate_from_scene_pos(center + QPointF(0.0, 100.0))
        self.assertEqual(updates, [])

        text.finish_rotation()
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0][text.source_ref.key()]["rotation"], 180.0)

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

    def test_rotation_reuses_glyph_layer_and_preserves_all_font_settings(self) -> None:
        text = OverlayTextItem(
            "Protein label",
            QRectF(0.0, 0.0, 150.0, 46.0),
            font_family="Helvetica",
            font_size=16.0,
            bold=True,
            italic=True,
            underline=True,
            text_align="center",
        )

        def font_state() -> tuple:
            font = text.font()
            return (
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
                text.text_align(),
            )

        before_font = font_state()
        first_surface = QImage(220, 120, QImage.Format.Format_ARGB32)
        first_surface.fill(QColor("#FFFFFF"))
        first_painter = QPainter(first_surface)
        try:
            text.paint(first_painter, None)
        finally:
            first_painter.end()
        first_layer_key = text._text_layer_cache.cacheKey()
        first_render_key = text._text_layer_cache_key

        text.setRotation(37.0)
        second_surface = QImage(220, 120, QImage.Format.Format_ARGB32)
        second_surface.fill(QColor("#FFFFFF"))
        second_painter = QPainter(second_surface)
        try:
            text.paint(second_painter, None)
        finally:
            second_painter.end()

        self.assertEqual(font_state(), before_font)
        self.assertEqual(text._text_layer_cache_key, first_render_key)
        self.assertEqual(text._text_layer_cache.cacheKey(), first_layer_key)

    def test_builtin_text_uses_manual_drag_not_qt_movable(self) -> None:
        canvas = self._render_default_canvas()
        text_item = next(item for item in canvas._scene.items() if isinstance(item, EditableTextItem))

        self.assertFalse(
            bool(text_item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        )

    def test_wheel_zoom_is_gentle_for_mouse_and_trackpad(self) -> None:
        canvas = self._render_default_canvas()
        canvas.resetTransform()

        mouse_event = _FakeWheelEvent(angle_y=120)
        canvas.wheelEvent(mouse_event)
        mouse_scale = canvas.transform().m11()
        self.assertTrue(mouse_event.accepted)
        self.assertGreater(mouse_scale, 1.0)
        self.assertLess(mouse_scale, 1.06)

        canvas.resetTransform()
        trackpad_event = _FakeWheelEvent(pixel_y=10)
        canvas.wheelEvent(trackpad_event)
        trackpad_scale = canvas.transform().m11()
        self.assertTrue(trackpad_event.accepted)
        self.assertGreater(trackpad_scale, 1.0)
        self.assertLess(trackpad_scale, 1.03)

    def test_smart_guides_snap_left_alignment_and_clear(self) -> None:
        canvas = FigureCanvas()
        moving = OverlayTextItem("Moving", QRectF(0, 0, 40, 20))
        anchor = OverlayTextItem("Anchor", QRectF(100, 50, 40, 20))
        canvas._scene.addItem(moving)
        canvas._scene.addItem(anchor)
        canvas._overlay_items.extend([moving, anchor])
        moving.setSelected(True)

        snapped = canvas._smart_snap_position(moving, QPointF(96.0, 0.0))

        self.assertEqual(snapped.x(), 100.0)
        self.assertTrue(canvas._smart_guide_items)
        self.assertTrue(all(
            guide.pen().color() == QColor("#A83DFF")
            for guide in canvas._smart_guide_items
        ))
        canvas._clear_smart_guides()
        self.assertFalse(canvas._smart_guide_items)

    def test_smart_guides_snap_equal_horizontal_spacing(self) -> None:
        canvas = FigureCanvas()
        left = BlotPlaceholderItem(QRectF(0, 0, 40, 20))
        moving = BlotPlaceholderItem(QRectF(62, 0, 40, 20))
        right = BlotPlaceholderItem(QRectF(120, 0, 40, 20))
        for item in (left, moving, right):
            canvas._scene.addItem(item)
            canvas._overlay_items.append(item)
        moving.setSelected(True)

        snapped = canvas._smart_snap_position(moving, QPointF(62.0, 0.0))

        self.assertEqual(snapped.x(), 60.0)
        self.assertGreaterEqual(len(canvas._smart_guide_items), 2)

    def test_smart_guides_disappear_when_text_drag_is_released(self) -> None:
        canvas = FigureCanvas()
        moving = OverlayTextItem("Moving", QRectF(0, 0, 40, 20))
        anchor = OverlayTextItem("Anchor", QRectF(100, 50, 40, 20))
        canvas._scene.addItem(moving)
        canvas._scene.addItem(anchor)
        canvas._overlay_items.extend([moving, anchor])
        press = moving.sceneBoundingRect().center()

        moving.mousePressEvent(_FakeMouseEvent(press))
        moving.mouseMoveEvent(_FakeMouseEvent(press + QPointF(96.0, 0.0)))
        self.assertTrue(canvas._smart_guide_items)
        moving.mouseReleaseEvent(_FakeMouseEvent(press + QPointF(96.0, 0.0)))

        self.assertFalse(canvas._smart_guide_items)
        self.assertFalse(canvas._smart_guides_active)

    def test_click_jitter_does_not_move_builtin_text(self) -> None:
        canvas = self._render_default_canvas()
        text_item = next(
            item for item in canvas._scene.items()
            if isinstance(item, EditableTextItem)
        )
        start = QPointF(text_item.pos())
        press = text_item.sceneBoundingRect().center()

        text_item.mousePressEvent(_FakeMouseEvent(press))
        text_item.mouseMoveEvent(_FakeMouseEvent(press + QPointF(1.0, 1.0)))
        text_item.mouseReleaseEvent(_FakeMouseEvent(press + QPointF(1.0, 1.0)))

        self.assertEqual(text_item.pos(), start)
        self.assertEqual(text_item.current_offset(), QPointF(0.0, 0.0))

    def test_double_click_clears_pending_drag_before_text_editing(self) -> None:
        canvas = self._render_default_canvas()
        text_item = next(iter(canvas._text_items.values()))
        text_item._is_user_dragging = True
        text_item._drag_start_scene_pos = QPointF(10.0, 10.0)
        text_item._drag_start_item_pos = QPointF(text_item.pos())
        text_item._drag_threshold_crossed = True
        text_item._drag_group_start_positions = {
            text_item: QPointF(text_item.pos())
        }
        event = QGraphicsSceneMouseEvent(
            QEvent.Type.GraphicsSceneMouseDoubleClick
        )
        event.setButton(Qt.MouseButton.LeftButton)
        event.setButtons(Qt.MouseButton.LeftButton)
        event.setPos(text_item.editor_rect().center())
        event.setScenePos(
            text_item.mapToScene(text_item.editor_rect().center())
        )

        text_item.mouseDoubleClickEvent(event)

        self.assertFalse(text_item._is_user_dragging)
        self.assertIsNone(text_item._drag_start_scene_pos)
        self.assertIsNone(text_item._drag_start_item_pos)
        self.assertFalse(text_item._drag_threshold_crossed)
        self.assertFalse(text_item._drag_group_start_positions)
        self.assertEqual(
            text_item.textInteractionFlags(),
            Qt.TextInteractionFlag.TextEditorInteraction,
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

    def test_generated_text_boxes_fit_text_and_preserve_layout_anchor(self) -> None:
        project = TemplateEngine.build_project("normal_wb", 2, 2, 4)
        project.global_layout.panel_layout = "horizontal"
        project.global_layout.share_ib_labels = True
        project.global_layout.show_condition_table = True
        for panel in project.panels:
            panel.condition_table = FigureModeWindow._make_custom_condition_table(
                4,
                2,
                [[(1, 2), (3, 4)], [(1, 4)]],
            )
        layout = LayoutEngine().compute(project)
        canvas = FigureCanvas()
        canvas.render(layout, project)
        layout_by_key = {
            item.source_ref.key(): item
            for item in layout.items
            if item.source_ref is not None
            and item.kind in {"label", "mw", "title", "panel_letter", "table_cell"}
        }

        self.assertTrue(canvas._text_items)
        for key, text_item in canvas._text_items.items():
            source = layout_by_key[key]
            rect = text_item.editor_rect()
            self.assertAlmostEqual(rect.width(), text_item.natural_text_width())
            if source.align == "center":
                self.assertAlmostEqual(
                    text_item.pos().x() + rect.width() / 2.0,
                    pt_to_scene(source.x_pt + source.w_pt / 2.0),
                )
            elif source.align == "right":
                self.assertAlmostEqual(
                    text_item.pos().x() + rect.width(),
                    pt_to_scene(source.x_pt + source.w_pt),
                )
            else:
                self.assertAlmostEqual(text_item.pos().x(), pt_to_scene(source.x_pt))

    def test_condition_cells_keep_scene_center_while_text_length_changes(self) -> None:
        project = TemplateEngine.build_project("normal_wb", 1, 2, 4)
        project.global_layout.show_condition_table = True
        project.panels[0].condition_table = (
            FigureModeWindow._make_custom_condition_table(
                4,
                2,
                [[(1, 2), (3, 4)]],
            )
        )
        canvas = FigureCanvas()
        canvas.render(LayoutEngine().compute(project), project)
        condition_items = [
            item
            for item in canvas._text_items.values()
            if item.source_ref.field == "condition_cell"
        ]
        self.assertTrue(condition_items)

        for text_item in condition_items:
            original_center = text_item.mapToScene(text_item.editor_rect().center())
            original_offset = QPointF(text_item.current_offset())
            text_item.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextEditorInteraction
            )
            cursor = text_item.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            text_item.setTextCursor(cursor)
            text_item.keyPressEvent(QKeyEvent(
                QEvent.Type.KeyPress,
                Qt.Key.Key_X,
                Qt.KeyboardModifier.NoModifier,
                "X",
            ))
            self.assertTrue(text_item.toPlainText().endswith("X"))
            text_item.setPlainText(
                "A substantially longer condition or lane-group label"
            )

            new_center = text_item.mapToScene(text_item.editor_rect().center())
            self.assertAlmostEqual(new_center.x(), original_center.x(), places=4)
            self.assertAlmostEqual(new_center.y(), original_center.y(), places=4)
            self.assertEqual(text_item.current_offset(), original_offset)

    def test_fit_center_and_canvas_resize_do_not_change_condition_geometry(self) -> None:
        project = TemplateEngine.build_project("normal_wb", 1, 2, 4)
        project.global_layout.show_condition_table = True
        project.panels[0].condition_table = (
            FigureModeWindow._make_custom_condition_table(
                4,
                2,
                [[(1, 2), (3, 4)]],
            )
        )
        canvas = FigureCanvas()
        canvas.resize(900, 600)
        canvas.show()
        canvas.render(LayoutEngine().compute(project), project)
        self._app.processEvents()

        condition_items = {
            key: item
            for key, item in canvas._text_items.items()
            if item.source_ref.field == "condition_cell"
        }
        frame_key, frame = next(iter(canvas._blot_frames.items()))

        group_item = next(
            item
            for item in condition_items.values()
            if item.toPlainText().startswith("Group ")
        )
        original_group_center = group_item.mapToScene(
            group_item.editor_rect().center()
        )
        group_item.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextEditorInteraction
        )
        group_item.setFocus(Qt.FocusReason.OtherFocusReason)
        group_item.setPlainText("A much longer experimental lane group")
        group_item.clearFocus()
        self._app.processEvents()
        edited_group_center = group_item.mapToScene(
            group_item.editor_rect().center()
        )
        self.assertAlmostEqual(
            edited_group_center.x(), original_group_center.x(), places=4
        )
        self.assertAlmostEqual(
            edited_group_center.y(), original_group_center.y(), places=4
        )

        def relative_geometry() -> dict[tuple, tuple[float, float, float, float]]:
            frame_pos = frame.scenePos()
            return {
                key: (
                    item.scenePos().x() - frame_pos.x(),
                    item.scenePos().y() - frame_pos.y(),
                    item.editor_rect().width(),
                    item.editor_rect().height(),
                )
                for key, item in condition_items.items()
            }

        expected = relative_geometry()
        for size in (QSize(620, 420), QSize(1280, 760), QSize(840, 520)):
            canvas.resize(size)
            canvas.fit_frame_content_to_view()
            self._app.processEvents()
            self._app.processEvents()
            self.assertEqual(relative_geometry(), expected)

        self.assertIn(frame_key, canvas._blot_frames)
        canvas.close()

    def test_manually_resized_builtin_text_width_survives_rerender(self) -> None:
        project = TemplateEngine.build_project("normal_wb", 1, 2, 4)
        layout = LayoutEngine().compute(project)
        canvas = FigureCanvas()
        canvas.render(layout, project)
        key, text_item = next(iter(canvas._text_items.items()))

        text_item.resize_to_local_size(120.0, text_item.editor_rect().height())
        canvas.render(layout, project)

        self.assertAlmostEqual(canvas._text_items[key].editor_rect().width(), 120.0)

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
        actual_blot_offset = canvas._blot_offsets[blot_key]
        self.assertAlmostEqual(
            adjusted_blot.x_pt,
            original_blot.x_pt + scene_to_pt(actual_blot_offset.x()),
        )
        self.assertAlmostEqual(
            adjusted_blot.y_pt,
            original_blot.y_pt + scene_to_pt(actual_blot_offset.y()),
        )

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
        start_pos = QPointF(frame.pos())

        canvas._begin_blot_frame_move()
        canvas._preview_blot_frame_move(QPointF(12.0, 5.0))
        canvas._commit_blot_frame_move(QPointF(12.0, 5.0))

        self.assertEqual(canvas._blot_offsets[key], frame.pos() - start_pos)
        self.assertFalse(canvas._smart_guide_items)
        self.assertFalse(canvas._smart_guides_active)

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
        text_a.mouseMoveEvent(_FakeMouseEvent(QPointF(4.0, 1.0)))
        text_a.mouseReleaseEvent(_FakeMouseEvent(QPointF(4.0, 1.0)))

        self.assertEqual(text_a.pos(), QPointF(4.0, 1.0))
        self.assertEqual(text_b.pos(), QPointF(4.0, 2.0))

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

    def test_builtin_condition_line_is_easy_to_hit_nudge_and_delete(self) -> None:
        project = TemplateEngine.build_project("normal_wb", 1, 2, 4)
        project.panels[0].condition_table = (
            FigureModeWindow._make_custom_condition_table(
                4, 2, [(1, 2), (3, 4)]
            )
        )
        project.global_layout.show_condition_table = True
        layout = LayoutEngine().compute(project)
        canvas = FigureCanvas()
        canvas.render(layout, project)
        key, line = next(iter(canvas._line_items.items()))

        midpoint = line.line().pointAt(0.5)
        self.assertTrue(line.shape().contains(midpoint + QPointF(0.0, 5.0)))
        line.setSelected(True)
        canvas.keyPressEvent(QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Down,
            Qt.KeyboardModifier.NoModifier,
        ))
        self.assertEqual(line.pos(), QPointF(0.0, 1.0))
        self.assertEqual(canvas._line_offsets[key], QPointF(0.0, 1.0))

        canvas.keyPressEvent(QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Delete,
            Qt.KeyboardModifier.NoModifier,
        ))
        self.assertIn(key, canvas._hidden_line_keys)
        self.assertNotIn(key, canvas._line_items)
        adjusted = canvas.adjusted_layout_items_for_export(layout.items)
        self.assertFalse(any(
            item.source_ref is not None
            and item.source_ref.key() == key
            for item in adjusted
        ))

    def test_parallel_horizontal_lines_snap_to_exact_centerline(self) -> None:
        canvas = FigureCanvas()
        moving = LineElementItem(QLineF(20.0, 14.0, 100.0, 14.0))
        reference = LineElementItem(QLineF(20.0, 10.0, 100.0, 10.0))
        canvas._scene.addItem(moving)
        canvas._scene.addItem(reference)
        canvas._overlay_items.extend([moving, reference])
        moving.setSelected(True)

        snapped = canvas._smart_snap_position(moving, QPointF(0.0, 0.0))

        self.assertEqual(snapped, QPointF(0.0, -4.0))
        self.assertTrue(canvas._smart_guide_items)

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

    def test_text_box_expands_right_live_while_text_is_being_edited(self) -> None:
        canvas = FigureCanvas()
        text = OverlayTextItem(
            "A",
            QRectF(40.0, 60.0, 12.0, 28.0),
            font_family="Helvetica",
            font_size=12.0,
            bold=True,
            text_align="center",
        )
        canvas._scene.addItem(text)
        canvas._overlay_items.append(text)
        text.setSelected(True)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        start_pos = QPointF(text.pos())
        original_font = QFont(text.font())

        cursor = text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        text.setTextCursor(cursor)
        typed_event = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_W,
            Qt.KeyboardModifier.NoModifier,
            "W",
        )
        width_before_key = text.editor_rect().width()
        text.keyPressEvent(typed_event)
        self.assertEqual(text.toPlainText(), "AW")
        self.assertGreater(text.editor_rect().width(), width_before_key)

        text.setPlainText("A much longer protein label")

        expanded_width = text.editor_rect().width()
        self.assertGreater(expanded_width, 24.0)
        self.assertAlmostEqual(expanded_width, text.natural_text_width(), places=4)
        self.assertLessEqual(text.document().size().height(), text.editor_rect().height() + 0.1)
        self.assertEqual(text.pos(), start_pos)
        self.assertEqual(text.font(), original_font)
        self.assertAlmostEqual(
            text._resize_handles["right"].pos().x(),
            expanded_width,
            places=4,
        )
        self.assertAlmostEqual(
            text._rotation_handle.pos().x(),
            expanded_width / 2.0,
            places=4,
        )

        text.setPlainText("AB")

        self.assertLess(text.editor_rect().width(), expanded_width)
        self.assertAlmostEqual(
            text.editor_rect().width(),
            text.natural_text_width(),
            places=4,
        )
        self.assertEqual(text.pos(), start_pos)

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

    def test_render_page_image_can_include_items_outside_white_page(self) -> None:
        canvas = FigureCanvas()
        layout = LayoutResult(
            canvas_width_pt=120.0,
            canvas_height_pt=70.0,
            items=[
                LayoutItem(
                    kind="title",
                    x_pt=20.0,
                    y_pt=-24.0,
                    w_pt=80.0,
                    h_pt=18.0,
                    text="48h",
                    font_size_pt=14.0,
                    align="center",
                ),
                LayoutItem(
                    kind="line",
                    x_pt=15.0,
                    y_pt=-2.0,
                    w_pt=90.0,
                    h_pt=0.0,
                    line_width_pt=1.0,
                ),
            ],
        )
        canvas.render(layout, None)

        page_only = canvas.render_page_image(scale=1.0)
        complete = canvas.render_page_image(scale=1.0, include_overflow=True)

        self.assertEqual(page_only.width(), round(pt_to_scene(120.0)))
        self.assertEqual(page_only.height(), round(pt_to_scene(70.0)))
        self.assertGreater(complete.height(), page_only.height())
        self.assertGreaterEqual(complete.width(), page_only.width())

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

    def test_sidebar_uses_two_step_workflow_and_compact_template_browser(self) -> None:
        window = FigureModeWindow()

        self.assertEqual(window._grp1.title_text(), "Step 1: Choose Layout")
        self.assertEqual(window._grp4.title_text(), "Step 2: Fill Blot Frames")
        self.assertEqual(window._SIDEBAR_WIDTH, 210)
        self.assertTrue(window._auto_detect_radio.isChecked())
        self.assertEqual(window._auto_fit_h_margin.value(), 4)
        self.assertEqual(window._auto_fit_v_margin.value(), 4)
        self.assertEqual(window._auto_fit_h_margin.singleStep(), 4)
        self.assertEqual(window._auto_fit_v_margin.singleStep(), 4)
        self.assertEqual(window._grp5.title_text(), "Saved Blot Files")
        self.assertEqual(window._grp5._step_badge.text(), "3")
        self.assertEqual(window._grp6._step_badge.text(), "4")

        self.assertIs(window._sidebar_layout.itemAt(1).widget(), window._grp1)
        self.assertIs(window._sidebar_layout.itemAt(2).widget(), window._grp4)
        self.assertIs(window._sidebar_layout.itemAt(3).widget(), window._grp5)
        self.assertIs(window._sidebar_layout.itemAt(4).widget(), window._grp6)
        self.assertFalse(window._grp4.isHidden())
        self.assertFalse(window._grp4._expanded)

        self.assertIsNotNone(window._panels_spin)
        self.assertIsNotNone(window._template_list)
        self.assertIsNotNone(window._blot_file_list)
        button_texts = [
            button.text() for button in window._grp1.findChildren(QPushButton)
        ]
        self.assertIn("Create Blot Frame Template", button_texts)
        self.assertIn("Create Blot Condition Template", button_texts)
        self.assertIn("Add Extra Blot Frame", button_texts)
        self.assertIn("Saved Templates   ›", button_texts)
        self.assertNotIn("Apply Template", button_texts)
        self.assertNotIn("Apply Structure", button_texts)
        self.assertEqual(window._export_pdf_btn.text(), "PDF")
        self.assertEqual(window._export_tiff_btn.text(), "TIFF")
        self.assertEqual(window._export_pptx_btn.text(), "PPTX")
        self.assertEqual(
            {
                window._export_pdf_btn.objectName(),
                window._export_tiff_btn.objectName(),
                window._export_pptx_btn.objectName(),
            },
            {"exportFormatButton"},
        )
        self.assertEqual(
            {
                window._export_pdf_btn.height(),
                window._export_tiff_btn.height(),
                window._export_pptx_btn.height(),
            },
            {25},
        )
        self.assertEqual(
            {
                window._export_pdf_btn.styleSheet(),
                window._export_tiff_btn.styleSheet(),
                window._export_pptx_btn.styleSheet(),
            },
            {window._export_pdf_btn.styleSheet()},
        )
        self.assertNotIn("Or create a new layout", [
            label.text() for label in window._grp1.findChildren(QLabel)
        ])
        self.assertTrue(window._template_list.isHidden())
        self.assertEqual(window._grp1.findChildren(QGroupBox), [])
        label_texts = [label.text() for label in window._grp1.findChildren(QLabel)]
        self.assertIn("Blot Frame", label_texts)
        self.assertIn("Blot Conditions", label_texts)
        self.assertIn("Reuse a previous layout", label_texts)
        self.assertNotIn("Set up the blot frame first, then add conditions.", label_texts)
        self.assertNotIn("Define the area for lanes and bands.", label_texts)
        self.assertNotIn("Add condition labels to the frame.", label_texts)
        self.assertNotIn("Required", label_texts)
        self.assertNotIn("Next", label_texts)
        task_badges = [
            label for label in window._grp1.findChildren(QLabel)
            if label.objectName() == "step1TaskBadge"
        ]
        self.assertEqual([badge.text() for badge in task_badges], ["1", "2"])
        self.assertTrue(all("background:#FFFFFF" in badge.styleSheet() for badge in task_badges))
        self.assertTrue(all("color:#176B50" in badge.styleSheet() for badge in task_badges))
        self.assertEqual(
            len([
                frame for frame in window._grp1.findChildren(QFrame)
                if frame.objectName() == "step1Connector"
            ]),
            2,
        )
        create_frame = next(
            button for button in window._grp1.findChildren(QPushButton)
            if button.text() == "Create Blot Frame Template"
        )
        add_extra = next(
            button for button in window._grp1.findChildren(QPushButton)
            if button.text() == "Add Extra Blot Frame"
        )
        self.assertEqual(create_frame.height(), 28)
        self.assertEqual(window._saved_templates_btn.height(), 25)
        self.assertEqual(add_extra.height(), 24)
        self.assertEqual(create_frame.objectName(), "step1PrimaryButton")
        self.assertEqual(add_extra.objectName(), "step1TextButton")
        create_condition = next(
            button for button in window._grp1.findChildren(QPushButton)
            if button.text() == "Create Blot Condition Template"
        )
        self.assertEqual(create_condition.objectName(), "step1SecondaryButton")
        self.assertLessEqual(
            window._grp1.minimumSizeHint().width(),
            window._SIDEBAR_WIDTH,
        )

    def test_fill_blots_switches_inline_auto_and_manual_disclosures(self) -> None:
        window = FigureModeWindow()

        self.assertEqual(window._manual_detect_radio.text(), "Manual")
        self.assertFalse(window._auto_disclosure.isHidden())
        self.assertFalse(window._auto_disclosure.is_expanded())
        self.assertEqual(window._auto_disclosure._header.height(), 26)
        self.assertTrue(window._fixed_roi_disclosure.isHidden())
        self.assertTrue(window._selected_slot_lbl.isHidden())
        self.assertTrue(window._auto_fit_alignment.isHidden())
        self.assertEqual(window._auto_fit_alignment.currentData(), "auto")
        self.assertIn(
            "select then hit Enter/Return",
            [label.text() for label in window._grp4.findChildren(QLabel)],
        )

        window._manual_detect_radio.setChecked(True)

        self.assertTrue(window._auto_disclosure.isHidden())
        self.assertFalse(window._fixed_roi_disclosure.isHidden())
        self.assertFalse(window._fixed_roi_disclosure.is_expanded())
        fixed_button_texts = [
            button.text()
            for button in window._fixed_roi_disclosure.findChildren(QPushButton)
        ]
        self.assertEqual(
            fixed_button_texts,
            ["Fix ROI", "Cancel Fixed ROI"],
        )

    def test_saved_template_browser_previews_and_applies_selected_template(self) -> None:
        window = FigureModeWindow()
        inspected: list[object] = []

        def inspect_and_apply() -> None:
            dialog = window._template_browser_dialog
            canvas = window._template_preview_canvas
            content = canvas._frame_content_scene_rect()
            content_on_screen = canvas.mapFromScene(content).boundingRect()
            view_center = canvas.mapToScene(canvas.viewport().rect().center())
            content_center = content.center()
            inspected.extend([
                dialog is not None,
                canvas is not None,
                canvas._background_item is not None,
                dialog.width(),
                dialog.height(),
                dialog.minimumSize() == dialog.maximumSize(),
                canvas.horizontalScrollBarPolicy(),
                canvas.verticalScrollBarPolicy(),
                content_on_screen.width() / canvas.viewport().width() > 0.60,
                abs(view_center.x() - content_center.x()) < 3.0,
                abs(view_center.y() - content_center.y()) < 3.0,
            ])
            apply_button = next(
                button
                for button in dialog.findChildren(QPushButton)
                if button.text() == "Apply"
            )
            apply_button.click()

        QTimer.singleShot(20, inspect_and_apply)
        window._show_saved_templates_dialog()

        self.assertEqual(
            inspected,
            [
                True,
                True,
                True,
                680,
                420,
                True,
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
                True,
                True,
                True,
            ],
        )
        self.assertIsNotNone(window._project)
        self.assertTrue(window._template_list.isHidden())

    def test_saved_blot_file_can_be_renamed_without_changing_its_id(self) -> None:
        old_dir = figure_mode_module.USER_BLOT_FILES_DIR
        try:
            with TemporaryDirectory() as tmp:
                figure_mode_module.USER_BLOT_FILES_DIR = Path(tmp) / "blot_files"
                window = FigureModeWindow()
                window._project = TemplateEngine.build_project("normal_wb")
                window._write_blot_file("blot_original", "Original Name")
                window._populate_blot_file_list()

                with patch.object(
                    QInputDialog,
                    "getText",
                    return_value=("Renamed Blot", True),
                ):
                    window._on_rename_blot_file("blot_original")

                data = json.loads(
                    window._blot_file_path("blot_original").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(data["id"], "blot_original")
                self.assertEqual(data["name"], "Renamed Blot")
                self.assertEqual(
                    window._current_blot_file_selection(),
                    "blot_original",
                )
                row = window._blot_file_list.itemWidget(
                    window._blot_file_list.currentItem()
                )
                self.assertEqual(
                    row.findChildren(QLabel)[0].text(),
                    "Renamed Blot",
                )
        finally:
            figure_mode_module.USER_BLOT_FILES_DIR = old_dir

    def test_export_tiff_action_writes_complete_300_dpi_figure(self) -> None:
        with TemporaryDirectory() as tmp:
            window = FigureModeWindow()
            window._on_apply_template()
            chosen_path = f"{tmp}/exported_figure"
            with (
                patch.object(
                    figure_mode_module.QFileDialog,
                    "getSaveFileName",
                    return_value=(chosen_path, "TIFF Image (*.tif *.tiff)"),
                ),
                patch.object(figure_mode_module.QMessageBox, "information"),
            ):
                window._on_export_tiff()

            output_path = f"{chosen_path}.tiff"
            with Image.open(output_path) as exported:
                exported.load()
                self.assertEqual(exported.format, "TIFF")
                self.assertGreater(exported.width, 1)
                self.assertGreater(exported.height, 1)
                self.assertAlmostEqual(float(exported.tag_v2.get(282)), 300.0)
                self.assertAlmostEqual(float(exported.tag_v2.get(283)), 300.0)

    def test_create_template_frame_preview_tracks_structure_and_renders(self) -> None:
        preview = _FramePreviewWidget()
        preview.resize(400, 240)

        preview.set_structure(6, 5, 8)
        self._app.processEvents()

        self.assertEqual(preview.structure(), (6, 5, 8))
        image = preview.grab().toImage()
        self.assertFalse(image.isNull())

    def test_condition_preview_tracks_rows_lanes_and_groups(self) -> None:
        preview = _ConditionPreviewWidget()
        preview.resize(430, 240)
        preview.set_condition(8, 4, [(1, 2), (3, 5), (6, 8)])
        self._app.processEvents()

        self.assertEqual(
            preview.condition(),
            (8, 4, [(1, 2), (3, 5), (6, 8)]),
        )
        self.assertEqual(preview.height(), 120)
        self.assertFalse(preview.grab().toImage().isNull())

    def test_even_lane_group_ranges_cover_all_lanes(self) -> None:
        self.assertEqual(
            FigureModeWindow._even_lane_group_ranges(6, 0),
            [],
        )
        self.assertEqual(
            FigureModeWindow._even_lane_group_ranges(6, 2),
            [(1, 3), (4, 6)],
        )
        self.assertEqual(
            FigureModeWindow._even_lane_group_ranges(7, 3),
            [(1, 3), (4, 5), (6, 7)],
        )

    def test_condition_template_allows_zero_lane_groups(self) -> None:
        window = FigureModeWindow()
        window._project = TemplateEngine.build_project("normal_wb", 1, 2, 4)

        def create_without_groups() -> None:
            dialog = self._app.activeModalWidget()
            try:
                group_spin = dialog.findChild(QSpinBox, "laneGroupSpin_common")
                self.assertIsNone(group_spin)
                self.assertIsNotNone(dialog.findChild(
                    QToolButton,
                    "addLaneGroupLevel_common_empty",
                ))
                next(
                    button
                    for button in dialog.findChildren(QPushButton)
                    if button.text() == "Create"
                ).click()
            finally:
                if dialog.isVisible():
                    dialog.reject()

        QTimer.singleShot(0, create_without_groups)
        window._on_create_condition_template()

        table = window._project.panels[0].condition_table
        self.assertFalse(any(
            row and row[0] in {"__groups__", "__groups_level__"}
            for row in table.rows
        ))
        self.assertEqual(len(table.rows), 1)

    def test_condition_template_attaches_to_detected_panel_and_is_editable(self) -> None:
        window = FigureModeWindow()
        window._project = TemplateEngine.build_project("normal_wb", 1, 2, 6)

        window._apply_condition_template(
            attach_current=True,
            target_panel_idx=0,
            lane_count=6,
            condition_rows=3,
            group_ranges=[(1, 3), (4, 6)],
        )

        table = window._project.panels[0].condition_table
        self.assertIsNotNone(table)
        self.assertEqual(len(table.headers), 6)
        self.assertEqual(
            window._project.global_layout.condition_table_row_height_pt,
            13.0,
        )
        self.assertEqual(
            table.rows[0],
            ["__groups__", "1-3", "Group 1", "4-6", "Group 2"],
        )
        layout = LayoutEngine().compute(window._project)
        group_titles = [
            item for item in layout.items
            if item.kind == "table_cell" and item.text.startswith("Group ")
        ]
        group_lines = [
            item for item in layout.items
            if item.kind == "line" and item.line_color == "#111111"
        ]
        self.assertEqual(len(group_titles), 2)
        self.assertEqual(len(group_lines), 2)
        self.assertTrue(all(item.editable for item in group_titles))
        self.assertTrue(all(item.font_size_pt == 12.0 for item in group_titles))
        for title, line in zip(group_titles, group_lines):
            self.assertGreater(line.x_pt, title.x_pt)
            self.assertLess(line.x_pt + line.w_pt, title.x_pt + title.w_pt)
            self.assertEqual(
                line.y_pt,
                title.y_pt + title.h_pt - 1.0,
            )

    def test_condition_template_cannot_create_an_independent_panel(self) -> None:
        window = FigureModeWindow()
        window._project = TemplateEngine.build_project("normal_wb", 1, 2, 6)
        original_panel_count = len(window._project.panels)

        window._apply_condition_template(
            attach_current=False,
            target_panel_idx=None,
            lane_count=6,
            condition_rows=2,
            group_ranges=[(1, 6)],
        )

        self.assertEqual(len(window._project.panels), original_panel_count)
        self.assertIsNone(window._project.panels[0].condition_table)

    def test_condition_dialog_plus_adds_a_second_lane_group_level(self) -> None:
        window = FigureModeWindow()
        window._project = TemplateEngine.build_project("normal_wb", 1, 2, 6)

        def add_level_and_create() -> None:
            dialog = self._app.activeModalWidget()
            try:
                preview = dialog.findChild(_ConditionPreviewWidget)
                preview_width = preview.width()
                add_level = dialog.findChild(
                    QToolButton,
                    "addLaneGroupLevel_common_empty",
                )
                self.assertIsNotNone(add_level)
                add_level.click()
                self._app.processEvents()
                level_1_spin = dialog.findChild(
                    QSpinBox,
                    "laneGroupSpin_common",
                )
                self.assertEqual(level_1_spin.value(), 1)
                dialog.findChild(
                    QToolButton,
                    "addLaneGroupLevel_common_level1",
                ).click()
                self._app.processEvents()
                level_2_spin = dialog.findChild(
                    QSpinBox,
                    "laneGroupSpin_common_level2",
                )
                self.assertIsNotNone(level_2_spin)
                level_2_spin.setValue(1)
                levels = preview.conditions()[0][2]
                self.assertEqual(len(levels), 2)
                self.assertEqual(preview_width, 430)
                self.assertEqual(preview.width(), preview_width)
                level_headings = {
                    label.text(): label
                    for label in dialog.findChildren(QLabel)
                    if (
                        label.text() in {"Group Level 1", "Group Level 2"}
                        and label.isVisible()
                    )
                }
                self.assertEqual(
                    set(level_headings),
                    {"Group Level 1", "Group Level 2"},
                )
                self.assertLess(
                    level_headings["Group Level 1"].mapTo(
                        dialog, QPoint()
                    ).x(),
                    level_headings["Group Level 2"].mapTo(
                        dialog, QPoint()
                    ).x(),
                )
                self.assertAlmostEqual(
                    level_headings["Group Level 1"].mapTo(
                        dialog, QPoint()
                    ).y(),
                    level_headings["Group Level 2"].mapTo(
                        dialog, QPoint()
                    ).y(),
                    delta=2,
                )
                self.assertEqual(
                    sum(
                        label.text() == "Lane groups" and label.isVisible()
                        for label in dialog.findChildren(QLabel)
                    ),
                    1,
                )
                self.assertFalse(any(
                    label.text().startswith("Detected lanes")
                    for label in dialog.findChildren(QLabel)
                ))
                self.assertLessEqual(preview.height(), 135)
                next(
                    button
                    for button in dialog.findChildren(QPushButton)
                    if button.text() == "Create"
                ).click()
            finally:
                if dialog.isVisible():
                    dialog.reject()

        QTimer.singleShot(0, add_level_and_create)
        window._on_create_condition_template()

        rows = window._project.panels[0].condition_table.rows
        self.assertEqual(rows[0][0:2], ["__groups_level__", "2"])
        self.assertEqual(rows[1][0], "__groups__")
        layout = LayoutEngine().compute(window._project)
        group_titles = [
            item for item in layout.items
            if item.kind == "table_cell" and item.text.startswith("Group ")
        ]
        group_lines = [
            item for item in layout.items
            if (
                item.kind == "line"
                and item.source_ref is not None
                and item.source_ref.field == "condition_line"
            )
        ]
        self.assertEqual(len(group_titles), 2)
        self.assertEqual(len(group_lines), 2)
        self.assertTrue(all(item.font_size_pt == 12.0 for item in group_titles))
        titles_by_row = {
            item.source_ref.table_row: item for item in group_titles
        }
        lines_by_row = {
            item.source_ref.table_row: item for item in group_lines
        }
        self.assertEqual(
            lines_by_row[0].y_pt,
            titles_by_row[0].y_pt + titles_by_row[0].h_pt - 1.0,
        )
        self.assertEqual(
            lines_by_row[1].y_pt,
            titles_by_row[1].y_pt + titles_by_row[1].h_pt - 1.0,
        )

    def test_condition_dialog_fields_share_the_same_input_column(self) -> None:
        window = FigureModeWindow()
        window._project = TemplateEngine.build_project("normal_wb", 1, 2, 6)
        errors = []

        def inspect_alignment() -> None:
            dialog = self._app.activeModalWidget()
            try:
                rows_spin = dialog.findChild(QSpinBox, "conditionRowsSpin_common")
                add_level = dialog.findChild(
                    QToolButton,
                    "addLaneGroupLevel_common_empty",
                )
                self.assertIsNotNone(add_level)
                add_level.click()
                self._app.processEvents()
                group_spin = dialog.findChild(QSpinBox, "laneGroupSpin_common")
                self.assertIsNotNone(rows_spin)
                self.assertIsNotNone(group_spin)
                self.assertFalse(dialog.findChild(
                    QToolButton, "conditionRowsModeSelector"
                ).isVisible())
                self.assertFalse(dialog.findChild(
                    QToolButton, "laneGroupsModeSelector"
                ).isVisible())
                self.assertIsNone(dialog.findChild(
                    QRadioButton,
                    "attachIndependentConditionPanel",
                ))
                level_heading = next(
                    label
                    for label in dialog.findChildren(QLabel)
                    if label.text() == "Group Level 1" and label.isVisible()
                )
                self.assertLessEqual(
                    level_heading.mapTo(dialog, QPoint()).x(),
                    group_spin.mapTo(dialog, QPoint()).x(),
                )
                grouping_combo = dialog.findChild(
                    QComboBox,
                    "laneGroupingCombo_common",
                )
                self.assertEqual(grouping_combo.currentText(), "Divide lanes evenly")
                self.assertTrue(grouping_combo.isHidden())
                mode_selector = dialog.findChild(
                    QToolButton,
                    "laneGroupingSelector_common",
                )
                self.assertEqual(mode_selector.text(), "Evenly")
                self.assertTrue(mode_selector.isVisible())
                self.assertEqual(mode_selector.size(), QSize(53, 22))
                self.assertAlmostEqual(
                    mode_selector.mapTo(dialog, QPoint()).y()
                    + mode_selector.height() / 2.0,
                    group_spin.mapTo(dialog, QPoint()).y()
                    + group_spin.height() / 2.0,
                    delta=2,
                )
                add_level = dialog.findChild(
                    QToolButton,
                    "addLaneGroupLevel_common_level1",
                )
                self.assertGreater(
                    add_level.mapTo(dialog, QPoint()).x(),
                    mode_selector.mapTo(dialog, QPoint()).x(),
                )
                self.assertAlmostEqual(
                    add_level.mapTo(dialog, QPoint()).y(),
                    mode_selector.mapTo(dialog, QPoint()).y(),
                    delta=2,
                )
                self.assertEqual(
                    [action.text() for action in mode_selector.menu().actions()],
                    ["Divide lanes evenly", "Custom lane ranges…"],
                )
            except BaseException as error:
                errors.append(error)
            finally:
                if dialog.isVisible():
                    dialog.reject()

        QTimer.singleShot(0, inspect_alignment)
        window._on_create_condition_template()
        if errors:
            raise errors[0]

    def test_condition_dialog_return_commits_editor_without_closing(self) -> None:
        window = FigureModeWindow()
        window._project = TemplateEngine.build_project("normal_wb", 2, 2, 6)
        errors = []

        def press_return_in_spin_box() -> None:
            dialog = self._app.activeModalWidget()
            try:
                spin = dialog.findChild(
                    QSpinBox, "conditionRowsSpin_common"
                )
                spin.lineEdit().setText("7")
                spin.lineEdit().setFocus()
                self._app.sendEvent(
                    spin.lineEdit(),
                    QKeyEvent(
                        QEvent.Type.KeyPress,
                        Qt.Key.Key_Return,
                        Qt.KeyboardModifier.NoModifier,
                    ),
                )
                self._app.sendEvent(
                    spin.lineEdit(),
                    QKeyEvent(
                        QEvent.Type.KeyRelease,
                        Qt.Key.Key_Return,
                        Qt.KeyboardModifier.NoModifier,
                    ),
                )
                self._app.processEvents()
                self._app.processEvents()
                self.assertTrue(dialog.isVisible())
                self.assertEqual(spin.value(), 7)
                self.assertFalse(spin.lineEdit().hasSelectedText())
                self.assertFalse(spin.lineEdit().hasFocus())
                self.assertFalse(spin.hasFocus())
                dialog.findChild(
                    QToolButton, "addLaneGroupLevel_common_empty"
                ).click()
                group_spin = dialog.findChild(
                    QSpinBox, "laneGroupSpin_common"
                )
                group_spin.lineEdit().setText("3")
                group_spin.lineEdit().setFocus()
                self._app.sendEvent(
                    group_spin.lineEdit(),
                    QKeyEvent(
                        QEvent.Type.KeyPress,
                        Qt.Key.Key_Enter,
                        Qt.KeyboardModifier.NoModifier,
                    ),
                )
                self._app.sendEvent(
                    group_spin.lineEdit(),
                    QKeyEvent(
                        QEvent.Type.KeyRelease,
                        Qt.Key.Key_Enter,
                        Qt.KeyboardModifier.NoModifier,
                    ),
                )
                self._app.processEvents()
                self._app.processEvents()
                self.assertTrue(dialog.isVisible())
                self.assertEqual(group_spin.value(), 3)
                self.assertFalse(group_spin.lineEdit().hasSelectedText())
                self.assertFalse(group_spin.lineEdit().hasFocus())
            except BaseException as error:
                errors.append(error)
            finally:
                if dialog.isVisible():
                    dialog.reject()

        QTimer.singleShot(0, press_return_in_spin_box)
        window._on_create_condition_template()
        if errors:
            raise errors[0]

    def test_condition_dialog_can_remove_level_one_and_restore_it(self) -> None:
        window = FigureModeWindow()
        window._project = TemplateEngine.build_project("normal_wb", 1, 2, 6)
        errors = []

        def remove_and_restore_level() -> None:
            dialog = self._app.activeModalWidget()
            try:
                self.assertIsNone(dialog.findChild(
                    QToolButton,
                    "removeLaneGroupLevel_common_level1",
                ))
                dialog.findChild(
                    QToolButton,
                    "addLaneGroupLevel_common_empty",
                ).click()
                self._app.processEvents()
                remove_btn = dialog.findChild(
                    QToolButton,
                    "removeLaneGroupLevel_common_level1",
                )
                self.assertIsNotNone(remove_btn)
                remove_btn.click()
                self._app.processEvents()

                preview = dialog.findChild(_ConditionPreviewWidget)
                self.assertEqual(preview.conditions()[0][2], [])
                self.assertTrue(any(
                    label.text() == "No lane groups" and label.isVisible()
                    for label in dialog.findChildren(QLabel)
                ))

                restore_btn = dialog.findChild(
                    QToolButton,
                    "addLaneGroupLevel_common_empty",
                )
                self.assertIsNotNone(restore_btn)
                self.assertTrue(restore_btn.isVisible())
                restore_btn.click()
                self._app.processEvents()
                self.assertIsNotNone(dialog.findChild(
                    QToolButton,
                    "removeLaneGroupLevel_common_level1",
                ))
                self.assertEqual(len(preview._conditions[0][2]), 1)
                dialog.findChild(
                    QToolButton,
                    "removeLaneGroupLevel_common_level1",
                ).click()
                next(
                    button
                    for button in dialog.findChildren(QPushButton)
                    if button.text() == "Create"
                ).click()
            except BaseException as error:
                errors.append(error)
            finally:
                if dialog.isVisible():
                    dialog.reject()

        QTimer.singleShot(0, remove_and_restore_level)
        window._on_create_condition_template()
        if errors:
            raise errors[0]
        table = window._project.panels[0].condition_table
        self.assertFalse(any(
            row and row[0] in {"__groups__", "__groups_level__"}
            for row in table.rows
        ))

    def test_condition_dialog_remove_reindexes_remaining_levels(self) -> None:
        window = FigureModeWindow()
        window._project = TemplateEngine.build_project("normal_wb", 1, 2, 6)
        errors = []

        def remove_middle_level() -> None:
            dialog = self._app.activeModalWidget()
            try:
                dialog.findChild(
                    QToolButton,
                    "addLaneGroupLevel_common_empty",
                ).click()
                dialog.findChild(
                    QToolButton,
                    "addLaneGroupLevel_common_level1",
                ).click()
                dialog.findChild(
                    QToolButton,
                    "addLaneGroupLevel_common_level2",
                ).click()
                self._app.processEvents()
                dialog.findChild(
                    QSpinBox,
                    "laneGroupSpin_common_level3",
                ).setValue(1)
                dialog.findChild(
                    QToolButton,
                    "removeLaneGroupLevel_common_level2",
                ).click()
                self._app.processEvents()

                remaining_level_2 = dialog.findChild(
                    QSpinBox,
                    "laneGroupSpin_common_level2",
                )
                self.assertIsNotNone(remaining_level_2)
                self.assertEqual(remaining_level_2.value(), 1)
                preview = dialog.findChild(_ConditionPreviewWidget)
                self.assertEqual(len(preview._conditions[0][2]), 2)
                visible_headings = [
                    label.text()
                    for label in dialog.findChildren(QLabel)
                    if (
                        label.text().startswith("Group Level ")
                        and label.isVisible()
                    )
                ]
                self.assertEqual(
                    visible_headings,
                    ["Group Level 1", "Group Level 2"],
                )
            except BaseException as error:
                errors.append(error)
            finally:
                dialog.reject()

        QTimer.singleShot(0, remove_middle_level)
        window._on_create_condition_template()
        if errors:
            raise errors[0]

    def test_applied_condition_template_can_be_undone(self) -> None:
        window = FigureModeWindow()
        window._project = TemplateEngine.build_project("normal_wb", 1, 2, 6)

        window._apply_condition_template(
            attach_current=True,
            target_panel_idx=0,
            lane_count=6,
            condition_rows=2,
            group_ranges=[(1, 3), (4, 6)],
        )
        self.assertIsNotNone(window._project.panels[0].condition_table)
        self.assertTrue(window._annot_undo_btn.isEnabled())

        window._undo_canvas_state()

        self.assertIsNone(window._project.panels[0].condition_table)

    def test_custom_grouping_opens_immediately_from_mode_selection(self) -> None:
        window = FigureModeWindow()
        window._project = TemplateEngine.build_project("normal_wb", 1, 2, 6)
        calls: list[tuple[int, int]] = []

        def fake_ranges(lanes, groups, defaults):
            calls.append((lanes, groups))
            return [(1, 6)]

        window._request_custom_lane_ranges = fake_ranges

        def choose_custom_and_close():
            dialog = self._app.activeModalWidget()
            dialog.findChild(
                QToolButton,
                "addLaneGroupLevel_common_empty",
            ).click()
            self._app.processEvents()
            mode_selector = dialog.findChild(
                QToolButton,
                "laneGroupingSelector_common",
            )
            self.assertIsNotNone(mode_selector)
            self.assertTrue(mode_selector.isVisible())
            evenly_size = mode_selector.size()
            dialog_size = dialog.size()
            custom_action = mode_selector.menu().actions()[1]
            custom_action.trigger()
            self._app.processEvents()
            self.assertTrue(custom_action.isChecked())
            self.assertEqual(mode_selector.text(), "Custom")
            summaries = [
                label.text() for label in dialog.findChildren(QLabel)
                if "Group 1: Lane" in label.text()
            ]
            self.assertFalse(summaries)
            self.assertTrue(any(
                button.text() == "Edit"
                and button.isVisible()
                for button in dialog.findChildren(QPushButton)
            ))
            custom_button = next(
                button
                for button in dialog.findChildren(QPushButton)
                if (
                    button.text() == "Edit"
                    and button.isVisible()
                )
            )
            self.assertEqual(custom_button.width(), mode_selector.width())
            self.assertEqual(custom_button.size(), evenly_size)
            self.assertEqual(dialog.size(), dialog_size)
            self.assertGreater(
                custom_button.mapTo(dialog, QPoint()).y(),
                mode_selector.mapTo(dialog, QPoint()).y(),
            )
            self.assertAlmostEqual(
                custom_button.mapTo(dialog, QPoint()).x(),
                mode_selector.mapTo(dialog, QPoint()).x(),
                delta=2,
            )
            dialog.reject()

        QTimer.singleShot(0, choose_custom_and_close)
        window._on_create_condition_template()

        self.assertEqual(calls, [(6, 1)])

    def test_condition_dialog_only_targets_detected_western_panels(self) -> None:
        window = FigureModeWindow()
        window._project = TemplateEngine.build_project("normal_wb", 1, 2, 6)
        errors = []

        def inspect_single_panel_dialog() -> None:
            dialog = self._app.activeModalWidget()
            try:
                visible_text = {
                    label.text()
                    for label in dialog.findChildren(QLabel)
                    if label.isVisible()
                }
                self.assertNotIn("Attach to:", visible_text)
                self.assertNotIn("Current Western panel", visible_text)
                self.assertNotIn(
                    "Create independent condition panel",
                    visible_text,
                )
                self.assertIn("Condition rows", visible_text)
                self.assertIn("Lane groups", visible_text)
                self.assertFalse(dialog.findChild(
                    QToolButton, "conditionRowsModeSelector"
                ).isVisible())
                self.assertFalse(dialog.findChild(
                    QToolButton, "laneGroupsModeSelector"
                ).isVisible())
                self.assertEqual(
                    dialog.findChild(
                        QSpinBox, "conditionRowsSpin_common"
                    ).value(),
                    1,
                )
            except BaseException as error:
                errors.append(error)
            finally:
                dialog.reject()

        QTimer.singleShot(0, inspect_single_panel_dialog)
        window._on_create_condition_template()
        if errors:
            raise errors[0]

    def test_multi_panel_condition_defaults_to_apply_all_and_aligns_frames(
        self,
    ) -> None:
        window = FigureModeWindow()
        window._project = TemplateEngine.build_project("normal_wb", 2, 3, 4)
        window._project.global_layout.panel_layout = "horizontal"
        window._project.global_layout.share_ib_labels = True
        window._project.global_layout.show_mw_labels = False
        errors = []

        def accept_default() -> None:
            dialog = self._app.activeModalWidget()
            try:
                rows_mode = dialog.findChild(
                    QToolButton, "conditionRowsModeSelector"
                )
                groups_mode = dialog.findChild(
                    QToolButton, "laneGroupsModeSelector"
                )
                self.assertTrue(rows_mode.isVisible())
                self.assertTrue(groups_mode.isVisible())
                self.assertEqual(rows_mode.text(), "Apply to all panels")
                self.assertEqual(groups_mode.text(), "Apply to all panels")
                self.assertEqual(
                    [action.text() for action in rows_mode.menu().actions()],
                    ["Apply to all panels", "Set individual panels"],
                )
                preview = dialog.findChild(_ConditionPreviewWidget)
                self.assertEqual(len(preview.conditions()), 2)
                next(
                    button
                    for button in dialog.findChildren(QPushButton)
                    if button.text() == "Create"
                ).click()
            except BaseException as error:
                errors.append(error)
            finally:
                if dialog.isVisible():
                    dialog.reject()

        QTimer.singleShot(0, accept_default)
        window._on_create_condition_template()
        if errors:
            raise errors[0]

        self.assertTrue(all(
            panel.condition_table is not None
            for panel in window._project.panels
        ))
        self.assertEqual(
            window._project.panels[0].condition_table.rows,
            window._project.panels[1].condition_table.rows,
        )
        layout = LayoutEngine().compute(window._project)
        blot_tops = [
            min(
                item.y_pt
                for item in layout.items
                if (
                    item.kind == "blot"
                    and item.source_ref is not None
                    and item.source_ref.panel_idx == panel_index
                )
            )
            for panel_index in range(2)
        ]
        self.assertEqual(blot_tops[0], blot_tops[1])

    def test_multi_panel_individual_rows_and_groups_apply_per_panel(self) -> None:
        window = FigureModeWindow()
        window._project = TemplateEngine.build_project("normal_wb", 3, 2, 6)
        errors = []
        preview_signature = []

        def condition_layout_signature(items):
            return [
                (
                    item.kind,
                    round(item.x_pt, 6),
                    round(item.y_pt, 6),
                    round(item.w_pt, 6),
                    round(item.h_pt, 6),
                    item.text,
                    item.source_ref.panel_idx,
                    item.source_ref.table_row,
                    item.source_ref.table_col,
                    item.source_ref.field,
                )
                for item in items
                if (
                    item.source_ref is not None
                    and item.source_ref.field in {
                        "condition_cell",
                        "condition_line",
                    }
                )
            ]

        def configure_individual_panels() -> None:
            dialog = self._app.activeModalWidget()
            try:
                dialog.findChild(
                    QToolButton, "conditionRowsModeSelector"
                ).menu().actions()[1].trigger()
                dialog.findChild(
                    QToolButton, "laneGroupsModeSelector"
                ).menu().actions()[1].trigger()
                dialog.findChild(
                    QToolButton, "addLaneGroupLevel_common_empty"
                ).click()
                self._app.processEvents()
                panel_2_placeholder = dialog.findChild(
                    QSpinBox, "laneGroupSpin_panel_2"
                )
                self.assertTrue(panel_2_placeholder.isVisible())
                self.assertEqual(panel_2_placeholder.value(), 0)
                self.assertEqual(
                    dialog.findChild(
                        QToolButton, "laneGroupingSelector_panel_2"
                    ).text(),
                    dialog.findChild(
                        QToolButton, "laneGroupingSelector_panel_1"
                    ).text(),
                )
                self.assertFalse(dialog.findChild(
                    QToolButton, "removeLaneGroupLevel_panel_2"
                ).isVisible())
                self.assertTrue(dialog.findChild(
                    QToolButton, "addLaneGroupLevel_panel_2_empty"
                ).isVisible())
                dialog.findChild(
                    QToolButton, "addLaneGroupLevel_panel_2_empty"
                ).click()
                dialog.findChild(
                    QToolButton, "addLaneGroupLevel_panel_3_empty"
                ).click()
                self._app.processEvents()
                preview = dialog.findChild(_ConditionPreviewWidget)
                initial_marker_centers = [
                    (marker[1] + marker[2]) / 2.0
                    for marker in preview.layout_panel_markers()
                ]

                for panel_position, value in enumerate((1, 2, 3), start=1):
                    dialog.findChild(
                        QSpinBox,
                        f"conditionRowsSpin_panel_{panel_position}",
                    ).setValue(value)
                    group_spin = dialog.findChild(
                        QSpinBox,
                        f"laneGroupSpin_panel_{panel_position}",
                    )
                    self.assertTrue(group_spin.isVisible())
                    group_spin.setValue(value)

                self.assertFalse(dialog.findChild(
                    QSpinBox, "conditionRowsSpin_common"
                ).isVisible())
                self.assertFalse(dialog.findChild(
                    QSpinBox, "laneGroupSpin_common"
                ).isVisible())
                self.assertEqual(
                    [condition[1] for condition in preview.conditions()],
                    [1, 2, 3],
                )
                self.assertEqual(
                    [len(condition[2]) for condition in preview.conditions()],
                    [1, 2, 3],
                )
                preview_signature.extend(
                    condition_layout_signature(preview.layout_items())
                )
                self.assertEqual(
                    preview.layout_panel_labels(),
                    ["Panel 1", "Panel 2", "Panel 3"],
                )
                markers = preview.layout_panel_markers()
                self.assertEqual(len({marker[3] for marker in markers}), 1)
                self.assertEqual(
                    [(marker[1] + marker[2]) / 2.0 for marker in markers],
                    initial_marker_centers,
                )
                next(
                    button
                    for button in dialog.findChildren(QPushButton)
                    if button.text() == "Create"
                ).click()
            except BaseException as error:
                errors.append(error)
            finally:
                if dialog.isVisible():
                    dialog.reject()

        QTimer.singleShot(0, configure_individual_panels)
        window._on_create_condition_template()
        if errors:
            raise errors[0]

        for panel_position, expected in enumerate((1, 2, 3)):
            rows = window._project.panels[panel_position].condition_table.rows
            self.assertEqual(rows[0][0], "__groups__")
            self.assertEqual((len(rows[0]) - 1) // 2, expected)
            condition_rows = [
                row for row in rows if row and row[0].startswith("Condition ")
            ]
            self.assertEqual(len(condition_rows), expected)

        actual_layout = LayoutEngine().compute(window._project)
        self.assertEqual(
            preview_signature,
            condition_layout_signature(actual_layout.items),
        )

    def test_individual_panel_rows_each_add_a_horizontal_group_level(self) -> None:
        window = FigureModeWindow()
        window._project = TemplateEngine.build_project("normal_wb", 2, 2, 6)
        errors = []

        def inspect_level_layout() -> None:
            dialog = self._app.activeModalWidget()
            try:
                dialog.findChild(
                    QToolButton, "laneGroupsModeSelector"
                ).menu().actions()[1].trigger()
                dialog.findChild(
                    QToolButton, "addLaneGroupLevel_common_empty"
                ).click()
                dialog.findChild(
                    QToolButton, "addLaneGroupLevel_panel_2_empty"
                ).click()
                self._app.processEvents()
                panel_buttons = [
                    dialog.findChild(
                        QToolButton, "addLaneGroupLevel_common_level1"
                    ),
                    dialog.findChild(
                        QToolButton, "addLaneGroupLevel_panel_2_level1"
                    ),
                ]
                self.assertTrue(all(
                    button is not None and button.isVisible()
                    for button in panel_buttons
                ))
                condition_rows_heading = next(
                    label
                    for label in dialog.findChildren(QLabel)
                    if (
                        label.text() == "Condition rows #:"
                        and label.isVisible()
                    )
                )
                level_one_heading = next(
                    label
                    for label in dialog.findChildren(QLabel)
                    if (
                        label.text() == "Group Level 1"
                        and label.isVisible()
                    )
                )
                self.assertEqual(
                    condition_rows_heading.objectName(),
                    "conditionGroupHeading",
                )
                self.assertEqual(
                    condition_rows_heading.font().bold(),
                    level_one_heading.font().bold(),
                )
                self.assertAlmostEqual(
                    condition_rows_heading.mapTo(dialog, QPoint()).x(),
                    level_one_heading.mapTo(dialog, QPoint()).x(),
                    delta=2,
                )
                panel_buttons[1].click()
                self._app.processEvents()
                headings = {
                    label.text(): label
                    for label in dialog.findChildren(QLabel)
                    if (
                        label.text() in {"Group Level 1", "Group Level 2"}
                        and label.isVisible()
                    )
                }
                self.assertLess(
                    headings["Group Level 1"].mapTo(dialog, QPoint()).x(),
                    headings["Group Level 2"].mapTo(dialog, QPoint()).x(),
                )
                self.assertAlmostEqual(
                    headings["Group Level 1"].mapTo(dialog, QPoint()).y(),
                    headings["Group Level 2"].mapTo(dialog, QPoint()).y(),
                    delta=2,
                )
                self.assertTrue(dialog.findChild(
                    QToolButton, "addLaneGroupLevel_common_level1"
                ).isVisible())
                self.assertTrue(dialog.findChild(
                    QToolButton, "addLaneGroupLevel_panel_2_level2"
                ).isVisible())
                panel_1_level_1 = dialog.findChild(
                    QSpinBox, "laneGroupSpin_panel_1"
                )
                panel_2_level_1 = dialog.findChild(
                    QSpinBox, "laneGroupSpin_panel_2"
                )
                panel_2_level_2 = dialog.findChild(
                    QSpinBox, "laneGroupSpin_panel_2_level2"
                )
                self.assertGreater(
                    panel_2_level_2.mapTo(dialog, QPoint()).y(),
                    panel_1_level_1.mapTo(dialog, QPoint()).y(),
                )
                self.assertAlmostEqual(
                    panel_2_level_2.mapTo(dialog, QPoint()).y(),
                    panel_2_level_1.mapTo(dialog, QPoint()).y(),
                    delta=2,
                )
            except BaseException as error:
                errors.append(error)
            finally:
                dialog.reject()

        QTimer.singleShot(0, inspect_level_layout)
        window._on_create_condition_template()
        if errors:
            raise errors[0]

    def test_individual_panel_remove_only_changes_that_panels_levels(self) -> None:
        window = FigureModeWindow()
        window._project = TemplateEngine.build_project("normal_wb", 2, 2, 6)
        window._request_custom_lane_ranges = (
            lambda lanes, groups, defaults: list(defaults)
        )
        errors = []

        def remove_one_panel_level() -> None:
            dialog = self._app.activeModalWidget()
            try:
                dialog.findChild(
                    QToolButton, "laneGroupsModeSelector"
                ).menu().actions()[1].trigger()
                dialog.findChild(
                    QToolButton, "addLaneGroupLevel_common_empty"
                ).click()
                dialog.findChild(
                    QToolButton, "addLaneGroupLevel_panel_2_empty"
                ).click()
                dialog.findChild(
                    QToolButton, "addLaneGroupLevel_panel_2_level1"
                ).click()
                self._app.processEvents()
                self.assertFalse(dialog.findChild(
                    QToolButton, "removeLaneGroupLevel_common_level1"
                ).isVisible())
                panel_1_remove = dialog.findChild(
                    QToolButton, "removeLaneGroupLevel_panel_1"
                )
                panel_2_remove = dialog.findChild(
                    QToolButton, "removeLaneGroupLevel_panel_2"
                )
                panel_2_level_2_remove = dialog.findChild(
                    QToolButton, "removeLaneGroupLevel_panel_2_level2"
                )
                self.assertTrue(panel_1_remove.isVisible())
                self.assertTrue(panel_2_remove.isVisible())
                self.assertTrue(panel_2_level_2_remove.isVisible())
                dialog.findChild(
                    QSpinBox, "laneGroupSpin_panel_2_level2"
                ).setValue(2)
                panel_2_remove.click()
                self._app.processEvents()
                self.assertEqual(
                    dialog.findChild(
                        QSpinBox, "laneGroupSpin_panel_1"
                    ).value(),
                    1,
                )
                self.assertEqual(
                    dialog.findChild(
                        QSpinBox, "laneGroupSpin_panel_2"
                    ).value(),
                    2,
                )
                self.assertFalse(any(
                    label.text() == "Group Level 2" and label.isVisible()
                    for label in dialog.findChildren(QLabel)
                ))
                preview = dialog.findChild(_ConditionPreviewWidget)
                self.assertEqual(
                    [len(condition[2]) for condition in preview.conditions()],
                    [1, 2],
                )
                panel_2_mode_selector = dialog.findChild(
                    QToolButton, "laneGroupingSelector_panel_2"
                )
                panel_2_mode_selector.menu().actions()[1].trigger()
                self._app.processEvents()
                remaining_mode = panel_2_mode_selector.text()
                self.assertEqual(remaining_mode, "Custom")
                dialog.findChild(
                    QToolButton, "removeLaneGroupLevel_panel_2"
                ).click()
                self._app.processEvents()
                panel_2_placeholder = dialog.findChild(
                    QSpinBox, "laneGroupSpin_panel_2"
                )
                panel_2_mode = dialog.findChild(
                    QToolButton, "laneGroupingSelector_panel_2"
                )
                self.assertTrue(panel_2_placeholder.isVisible())
                self.assertEqual(panel_2_placeholder.value(), 0)
                self.assertEqual(panel_2_mode.text(), remaining_mode)
                self.assertTrue(panel_2_mode.isEnabled())
                self.assertTrue(dialog.findChild(
                    QToolButton, "removeLaneGroupLevel_panel_2"
                ).isVisible())
                self.assertTrue(dialog.findChild(
                    QToolButton, "addLaneGroupLevel_panel_2_level1"
                ).isVisible())
                panel_2_placeholder.setValue(2)
                self._app.processEvents()
                self.assertEqual(panel_2_placeholder.value(), 2)
                self.assertEqual(panel_2_mode.text(), remaining_mode)
                self.assertEqual(
                    dialog.findChild(
                        QSpinBox, "laneGroupSpin_panel_1"
                    ).value(),
                    1,
                )
            except BaseException as error:
                errors.append(error)
            finally:
                dialog.reject()

        QTimer.singleShot(0, remove_one_panel_level)
        window._on_create_condition_template()
        if errors:
            raise errors[0]

    def test_multi_panel_modes_can_switch_back_to_apply_all(self) -> None:
        window = FigureModeWindow()
        window._project = TemplateEngine.build_project("normal_wb", 3, 2, 6)
        errors = []

        def switch_modes_and_create() -> None:
            dialog = self._app.activeModalWidget()
            try:
                rows_mode = dialog.findChild(
                    QToolButton, "conditionRowsModeSelector"
                )
                groups_mode = dialog.findChild(
                    QToolButton, "laneGroupsModeSelector"
                )
                rows_mode.menu().actions()[1].trigger()
                groups_mode.menu().actions()[1].trigger()
                rows_mode.menu().actions()[0].trigger()
                groups_mode.menu().actions()[0].trigger()
                dialog.findChild(
                    QSpinBox, "conditionRowsSpin_common"
                ).setValue(2)
                dialog.findChild(
                    QToolButton, "addLaneGroupLevel_common_empty"
                ).click()
                dialog.findChild(
                    QSpinBox, "laneGroupSpin_common"
                ).setValue(2)
                self._app.processEvents()
                self.assertTrue(dialog.findChild(
                    QSpinBox, "conditionRowsSpin_common"
                ).isVisible())
                self.assertTrue(dialog.findChild(
                    QSpinBox, "laneGroupSpin_common"
                ).isVisible())
                next(
                    button
                    for button in dialog.findChildren(QPushButton)
                    if button.text() == "Create"
                ).click()
            except BaseException as error:
                errors.append(error)
            finally:
                if dialog.isVisible():
                    dialog.reject()

        QTimer.singleShot(0, switch_modes_and_create)
        window._on_create_condition_template()
        if errors:
            raise errors[0]

        table_rows = [
            panel.condition_table.rows
            for panel in window._project.panels
        ]
        self.assertTrue(all(rows == table_rows[0] for rows in table_rows[1:]))
        self.assertEqual(table_rows[0][0][0], "__groups__")
        self.assertEqual((len(table_rows[0][0]) - 1) // 2, 2)
        self.assertEqual(
            sum(
                row[0].startswith("Condition ")
                for row in table_rows[0]
                if row
            ),
            2,
        )

    def test_switching_apply_all_to_individual_preserves_existing_values(
        self,
    ) -> None:
        window = FigureModeWindow()
        window._project = TemplateEngine.build_project("normal_wb", 3, 2, 6)
        window._request_custom_lane_ranges = (
            lambda lanes, groups, defaults: list(defaults)
        )
        errors = []

        def switch_after_configuring_shared_values() -> None:
            dialog = self._app.activeModalWidget()
            try:
                dialog.findChild(
                    QSpinBox, "conditionRowsSpin_common"
                ).setValue(4)
                dialog.findChild(
                    QToolButton, "addLaneGroupLevel_common_empty"
                ).click()
                dialog.findChild(
                    QSpinBox, "laneGroupSpin_common"
                ).setValue(2)
                dialog.findChild(
                    QToolButton, "laneGroupingSelector_common"
                ).menu().actions()[1].trigger()
                dialog.findChild(
                    QToolButton, "addLaneGroupLevel_common_level1"
                ).click()
                dialog.findChild(
                    QSpinBox, "laneGroupSpin_common_level2"
                ).setValue(3)

                dialog.findChild(
                    QToolButton, "conditionRowsModeSelector"
                ).menu().actions()[1].trigger()
                dialog.findChild(
                    QToolButton, "laneGroupsModeSelector"
                ).menu().actions()[1].trigger()
                self._app.processEvents()

                for panel_position in range(1, 4):
                    self.assertEqual(dialog.findChild(
                        QSpinBox,
                        f"conditionRowsSpin_panel_{panel_position}",
                    ).value(), 4)
                    self.assertEqual(dialog.findChild(
                        QSpinBox,
                        f"laneGroupSpin_panel_{panel_position}",
                    ).value(), 2)
                    self.assertEqual(dialog.findChild(
                        QToolButton,
                        f"laneGroupingSelector_panel_{panel_position}",
                    ).text(), "Custom")
                    self.assertEqual(dialog.findChild(
                        QSpinBox,
                        f"laneGroupSpin_panel_{panel_position}_level2",
                    ).value(), 3)

                dialog.findChild(
                    QToolButton, "addLaneGroupLevel_common_level2"
                ).click()
                self._app.processEvents()
                self.assertEqual(dialog.findChild(
                    QSpinBox, "laneGroupSpin_panel_1_level3"
                ).value(), 1)
                self.assertFalse(dialog.findChild(
                    QSpinBox, "laneGroupSpin_panel_2_level3"
                ).isVisible())
            except BaseException as error:
                errors.append(error)
            finally:
                dialog.reject()

        QTimer.singleShot(0, switch_after_configuring_shared_values)
        window._on_create_condition_template()
        if errors:
            raise errors[0]

    def test_apply_condition_preserves_blot_scale_and_viewport_positions(
        self,
    ) -> None:
        window = FigureModeWindow()
        window.resize(1200, 760)
        window.show()
        self._app.processEvents()
        window._project = TemplateEngine.build_project("normal_wb", 2, 4, 3)
        window._project.global_layout.panel_layout = "horizontal"
        window._project.global_layout.share_ib_labels = True
        window._project.global_layout.show_mw_labels = False
        window._recompute_and_refresh(fit_view=False)
        window._canvas.fit_frame_content_to_view()
        window._canvas.zoom_in()
        self._app.processEvents()

        before_scale = window._canvas.transform().m11()
        before_positions = {
            key: window._canvas.mapFromScene(
                frame.sceneBoundingRect().center()
            )
            for key, frame in window._canvas._blot_frames.items()
        }

        window._apply_condition_templates_to_panels([
            (0, 3, 3, [(1, 2), (3, 3)]),
            (1, 3, 3, [(1, 2), (3, 3)]),
        ])
        self._app.processEvents()

        self.assertAlmostEqual(
            window._canvas.transform().m11(),
            before_scale,
        )
        for key, before in before_positions.items():
            after = window._canvas.mapFromScene(
                window._canvas._blot_frames[key].sceneBoundingRect().center()
            )
            self.assertAlmostEqual(after.x(), before.x(), delta=2.0)
            self.assertAlmostEqual(after.y(), before.y(), delta=2.0)

    def test_uneven_lane_groups_keep_all_group_titles_at_twelve_points(
        self,
    ) -> None:
        project = TemplateEngine.build_project("normal_wb", 2, 4, 3)
        project.global_layout.panel_layout = "horizontal"
        project.global_layout.share_ib_labels = True
        project.global_layout.show_mw_labels = False
        for panel in project.panels:
            panel.condition_table = FigureModeWindow._make_custom_condition_table(
                3,
                3,
                [(1, 2), (3, 3)],
            )
        project.global_layout.show_condition_table = True
        project.global_layout.condition_table_row_height_pt = 13.0

        layout = LayoutEngine().compute(project)
        group_titles = [
            item
            for item in layout.items
            if item.kind == "table_cell"
            and item.text.startswith("Group ")
        ]

        self.assertEqual(len(group_titles), 4)
        self.assertTrue(
            all(item.font_size_pt == 12.0 for item in group_titles)
        )
        self.assertTrue(all(item.w_pt >= 52.0 for item in group_titles))

    def test_apply_frame_keeps_layout_section_expanded(self) -> None:
        window = FigureModeWindow()
        window._grp1.set_expanded(True)

        window._on_apply_structure()

        self.assertTrue(window._grp1._expanded)

    def test_apply_template_keeps_layout_section_expanded(self) -> None:
        window = FigureModeWindow()
        window._grp1.set_expanded(True)

        window._on_apply_template()

        self.assertTrue(window._grp1._expanded)

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
        detail_row_height = window._selection_detail_toolbar.height()
        toolbar_height = window._selection_detail_toolbar.parentWidget().sizeHint().height()

        text = window._canvas.add_overlay_text_box()
        self._app.processEvents()

        self.assertTrue(text.isSelected())
        self.assertTrue(window._toolbar_font_family_combo.isEnabled())
        self.assertTrue(window._toolbar_font_menu_btn.isEnabled())
        self.assertTrue(window._toolbar_font_size_combo.isEnabled())
        self.assertFalse(window._selection_detail_toolbar.isHidden())
        self.assertEqual(window._selection_detail_toolbar.height(), detail_row_height)
        self.assertEqual(
            window._selection_detail_toolbar.parentWidget().sizeHint().height(),
            toolbar_height,
        )
        self.assertFalse(hasattr(window, "_text_rotation_spin"))
        self.assertFalse(hasattr(window, "_rotation_label"))
        self.assertTrue(window._line_width_spin.isHidden())

        window._toolbar_font_size_combo.setCurrentText("18")
        self.assertEqual(text.font().pointSizeF(), 18.0)

        window._canvas.add_overlay_line()
        self._app.processEvents()
        self.assertFalse(window._line_width_spin.isHidden())

    def test_handle_rotation_updates_window_style_state_and_layout(self) -> None:
        window = FigureModeWindow()
        window._project = TemplateEngine.build_project("normal_wb", 1, 2, 4)
        window._recompute_and_refresh(fit_view=False)
        text = next(
            item
            for item in window._canvas._scene.items()
            if isinstance(item, EditableTextItem)
        )
        key = text.source_ref.key()
        text.setSelected(True)
        center = text.mapToScene(text.editor_rect().center())

        text.begin_rotation()
        text.rotate_from_scene_pos(center + QPointF(-100.0, 0.0))
        text.finish_rotation()

        self.assertEqual(window._text_style_overrides[key]["rotation"], 270.0)
        layout_item = next(
            item
            for item in window._layout_result.items
            if item.source_ref is not None and item.source_ref.key() == key
        )
        self.assertEqual(layout_item.rotation, 270.0)
        self.assertEqual(len(window._canvas_undo_stack), 1)

        window._undo_canvas_state()
        restored = window._canvas._text_items[key]
        self.assertEqual(restored.rotation(), 0.0)

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
        self.assertEqual(resized_slot.label, "IB: Protein 1")
        self.assertEqual(resized_slot.source_image_path, "/tmp/source.tif")
        self.assertEqual(resized_slot.bounding_box, ImageBBox(1, 2, 30, 12))
        self.assertEqual(resized_slot.lane_count, 6)
        self.assertFalse(window._project.global_layout.show_mw_labels)
        self.assertTrue(all(
            blot.mw_marker == ""
            for panel in window._project.panels
            for blot in panel.blot_slots
        ))
        self.assertEqual(
            [blot.label for blot in window._project.panels[0].blot_slots],
            ["IB: Protein 1", "IB: Protein 2", "IB: Protein 3"],
        )

    def test_created_frame_labels_align_with_blots_and_have_no_left_text(self) -> None:
        project = TemplateEngine.build_project("normal_wb", 1, 3, 4)
        project.global_layout.show_mw_labels = False
        for index, slot in enumerate(project.panels[0].blot_slots):
            slot.label = f"IB: Protein {index + 1}"
            slot.mw_marker = ""

        layout = LayoutEngine().compute(project)
        blots = [item for item in layout.items if item.kind == "blot"]
        labels = [item for item in layout.items if item.kind == "label"]

        self.assertFalse(any(item.kind == "mw" for item in layout.items))
        self.assertEqual(len(blots), len(labels))
        for blot, label in zip(blots, labels):
            self.assertEqual(label.y_pt, blot.y_pt)
            self.assertEqual(label.h_pt, blot.h_pt)
            self.assertEqual(
                label.x_pt,
                blot.x_pt + blot.w_pt + project.global_layout.ib_label_gap_pt,
            )

    def test_created_multi_panel_frame_is_horizontal_with_one_shared_ib_column(
        self,
    ) -> None:
        window = FigureModeWindow()
        window._project = TemplateEngine.build_project("normal_wb", 1, 3, 3)
        window._active_template_id = "normal_wb"
        window._active_table_style = "none"
        window._panels_spin.setValue(2)
        window._blots_spin.setValue(3)
        window._lanes_spin.setValue(3)

        window._on_apply_structure()

        layout = LayoutEngine().compute(window._project)
        first_blots = [
            item for item in layout.items
            if (
                item.kind == "blot"
                and item.source_ref is not None
                and item.source_ref.panel_idx == 0
            )
        ]
        second_blots = [
            item for item in layout.items
            if (
                item.kind == "blot"
                and item.source_ref is not None
                and item.source_ref.panel_idx == 1
            )
        ]
        labels = [item for item in layout.items if item.kind == "label"]

        self.assertEqual(window._project.global_layout.panel_layout, "horizontal")
        self.assertTrue(window._project.global_layout.share_ib_labels)
        self.assertEqual(len(labels), 3)
        self.assertEqual(
            [item.text for item in labels],
            ["IB: Protein 1", "IB: Protein 2", "IB: Protein 3"],
        )
        self.assertTrue(
            all(first.y_pt == second.y_pt for first, second in zip(first_blots, second_blots))
        )
        self.assertGreater(second_blots[0].x_pt, first_blots[0].x_pt)
        self.assertLess(
            second_blots[0].x_pt - (first_blots[0].x_pt + first_blots[0].w_pt),
            window._project.global_layout.inter_panel_gap_pt,
        )
        self.assertTrue(
            all(label.x_pt > second.x_pt + second.w_pt
                for label, second in zip(labels, second_blots))
        )

    def test_created_frame_defaults_to_large_centered_canvas_view(self) -> None:
        window = FigureModeWindow()
        window.resize(1100, 720)
        window.show()
        self._app.processEvents()
        window._project = TemplateEngine.build_project("normal_wb", 1, 4, 3)
        window._active_template_id = "normal_wb"
        window._active_table_style = "none"
        window._panels_spin.setValue(2)
        window._blots_spin.setValue(4)
        window._lanes_spin.setValue(3)

        window._on_apply_structure()
        self._app.processEvents()

        content = window._canvas._frame_content_scene_rect()
        viewport = window._canvas.viewport().rect()
        content_center = window._canvas.mapFromScene(content.center())
        displayed_width = (
            content.width() * abs(window._canvas.transform().m11())
        )

        self.assertAlmostEqual(
            content_center.x(),
            viewport.center().x(),
            delta=2.0,
        )
        self.assertAlmostEqual(
            content_center.y(),
            viewport.center().y(),
            delta=2.0,
        )
        self.assertAlmostEqual(
            displayed_width / viewport.width(),
            0.95,
            delta=0.03,
        )

    def test_resizing_canvas_preserves_frame_scale_and_recenters(self) -> None:
        window = FigureModeWindow()
        window.resize(1100, 720)
        window.show()
        self._app.processEvents()
        window._project = TemplateEngine.build_project("normal_wb", 2, 3, 4)
        window._project.global_layout.panel_layout = "horizontal"
        window._project.global_layout.share_ib_labels = True
        window._recompute_and_refresh()
        self._app.processEvents()
        original_scale = window._canvas.transform().m11()
        content = window._canvas._frame_content_scene_rect()
        original_displayed_width = content.width() * abs(original_scale)

        for width in (760, 1420, 920):
            window.resize(width, 720)
            self._app.processEvents()
            self._app.processEvents()
            content = window._canvas._frame_content_scene_rect()
            mapped_center = window._canvas.mapFromScene(content.center())
            viewport_center = window._canvas.viewport().rect().center()
            self.assertAlmostEqual(
                mapped_center.x(), viewport_center.x(), delta=2.0
            )
            self.assertAlmostEqual(
                mapped_center.y(), viewport_center.y(), delta=2.0
            )
            self.assertAlmostEqual(
                window._canvas.transform().m11(),
                original_scale,
            )
            self.assertAlmostEqual(
                content.width() * abs(window._canvas.transform().m11()),
                original_displayed_width,
            )

    def test_fit_center_toolbar_button_restores_standard_view(self) -> None:
        window = FigureModeWindow()
        window.resize(1100, 720)
        window.show()
        self._app.processEvents()
        window._project = TemplateEngine.build_project("normal_wb", 2, 3, 4)
        window._project.global_layout.panel_layout = "horizontal"
        window._project.global_layout.share_ib_labels = True
        window._recompute_and_refresh()
        self._app.processEvents()

        content = window._canvas._frame_content_scene_rect()
        window._canvas.scale(1.8, 1.8)
        window._canvas.centerOn(content.topLeft())
        self._app.processEvents()
        window._fit_center_btn.click()
        self._app.processEvents()

        viewport = window._canvas.viewport().rect()
        mapped_center = window._canvas.mapFromScene(content.center())
        displayed_width = content.width() * abs(window._canvas.transform().m11())
        self.assertAlmostEqual(mapped_center.x(), viewport.center().x(), delta=2.0)
        self.assertAlmostEqual(mapped_center.y(), viewport.center().y(), delta=2.0)
        self.assertAlmostEqual(
            displayed_width / viewport.width(),
            0.95,
            delta=0.03,
        )
        self.assertIn("background-color: #91BFA5", window._fit_center_btn.styleSheet())

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

    def test_condition_cells_follow_detected_lane_centres(self) -> None:
        project = TemplateEngine.build_project("dose_response", 1, 1, 4)
        slot = project.panels[0].blot_slots[0]
        slot.bounding_box = ImageBBox(10.0, 20.0, 120.0, 24.0)
        slot.lane_rois = [
            LaneROI(0, 0.05, 0.20),
            LaneROI(1, 0.28, 0.20),
            LaneROI(2, 0.52, 0.20),
            LaneROI(3, 0.75, 0.20),
        ]

        layout = LayoutEngine().compute(project)
        blot = next(item for item in layout.items if item.kind == "blot")
        cells = sorted(
            (
                item
                for item in layout.items
                if item.kind == "table_cell"
                and item.source_ref is not None
                and item.source_ref.table_row == 1
                and (item.source_ref.table_col or 0) > 0
            ),
            key=lambda item: item.source_ref.table_col,
        )

        self.assertEqual(len(cells), 4)
        expected_centers = [0.15, 0.38, 0.62, 0.85]
        for cell, relative_center in zip(cells, expected_centers):
            self.assertAlmostEqual(
                cell.x_pt + cell.w_pt / 2.0,
                blot.x_pt + relative_center * blot.w_pt,
            )

    def test_tight_auto_crop_keeps_edge_condition_cells_on_band_centres(self) -> None:
        crop = ImageBBox(21.0, 43.0, 48.0, 12.0)
        rois = FigureModeWindow._lane_rois_for_auto_fit((30.0, 60.0), crop)
        expected_centers = [(30.0 - 21.0) / 48.0, (60.0 - 21.0) / 48.0]
        for roi, expected in zip(rois, expected_centers):
            self.assertAlmostEqual(roi.x_offset + roi.width / 2.0, expected)

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
        self.assertEqual(added_slot.label, "IB: Protein 1")
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
                source_pixels = (
                    np.arange(20 * 30, dtype=np.uint32).reshape(20, 30) * 101
                ).clip(0, 65535).astype(np.uint16)
                Image.fromarray(source_pixels).save(source_path)

                window = FigureModeWindow()
                window._on_apply_template()
                slot = window._project.panels[0].blot_slots[0]
                slot.source_image_path = str(source_path)
                slot.bounding_box = ImageBBox(2.0, 3.0, 18.0, 9.0)
                slot.image_transform = {
                    "low": 1000,
                    "high": 48000,
                    "gamma": 1.4,
                    "inverted": True,
                }
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
                window._recompute_and_refresh(fit_view=False)
                self._app.processEvents()
                blot_key = (0, 0, None, None, None, "blot")
                before_preview = window._canvas.blot_preview_image(blot_key)
                self.assertIsNotNone(before_preview)

                window._write_blot_file("blot_test", "Blot Test")

                saved_path = figure_mode_module.USER_BLOT_FILES_DIR / "blot_test.json"
                saved_data = json.loads(saved_path.read_text(encoding="utf-8"))
                self.assertEqual(saved_data["version"], 2)
                saved_slot_data = saved_data["project"]["panels"][0]["blot_slots"][0]
                saved_slot_path = Path(saved_slot_data["source_image_path"])
                saved_preview_path = Path(saved_slot_data["saved_preview_path"])
                self.assertTrue(saved_slot_path.exists())
                self.assertTrue(saved_preview_path.exists())
                self.assertNotEqual(saved_slot_path, source_path)

                restored = FigureModeWindow()
                restored._load_blot_file("blot_test")

                restored_slot = restored._project.panels[0].blot_slots[0]
                self.assertTrue(Path(restored_slot.source_image_path).exists())
                self.assertEqual(
                    Path(restored_slot.saved_preview_path),
                    saved_preview_path,
                )
                self.assertEqual(restored_slot.bounding_box, ImageBBox(2.0, 3.0, 18.0, 9.0))
                after_preview = restored._canvas.blot_preview_image(blot_key)
                self.assertIsNotNone(after_preview)
                self.assertEqual(before_preview, after_preview)
                overlays = restored._canvas.overlay_items_as_json_data()
                blot_overlays = [item for item in overlays if item.get("type") == "blot"]
                self.assertEqual(len(blot_overlays), 2)
                self.assertFalse(blot_overlays[0].get("image_path"))
                self.assertTrue(Path(blot_overlays[1].get("image_path")).exists())
        finally:
            figure_mode_module.USER_BLOT_FILES_DIR = old_dir

    def test_updating_loaded_blot_file_does_not_overwrite_later_source_assets(self) -> None:
        old_dir = figure_mode_module.USER_BLOT_FILES_DIR
        try:
            with TemporaryDirectory() as tmp:
                figure_mode_module.USER_BLOT_FILES_DIR = Path(tmp) / "blot_files"
                source_a = Path(tmp) / "source_a.tif"
                source_b = Path(tmp) / "source_b.tif"
                Image.fromarray(np.full((20, 30), 4000, dtype=np.uint16)).save(source_a)
                Image.fromarray(np.full((20, 30), 52000, dtype=np.uint16)).save(source_b)

                window = FigureModeWindow()
                window._on_apply_template()
                slots = window._project.panels[0].blot_slots
                slots[0].source_image_path = str(source_a)
                slots[0].bounding_box = ImageBBox(0.0, 0.0, 30.0, 20.0)
                slots[1].source_image_path = str(source_b)
                slots[1].bounding_box = ImageBBox(0.0, 0.0, 30.0, 20.0)
                window._recompute_and_refresh(fit_view=False)
                window._write_blot_file("blot_update", "Blot Update")

                restored = FigureModeWindow()
                restored._load_blot_file("blot_update")
                restored_slots = restored._project.panels[0].blot_slots
                saved_a = restored_slots[0].source_image_path
                saved_b = restored_slots[1].source_image_path
                # Reordering asset-backed frames reproduces the former in-place
                # image_2 -> image_1, then corrupted image_1 -> image_2 copy.
                restored_slots[0].source_image_path = saved_b
                restored_slots[1].source_image_path = saved_a
                restored._recompute_and_refresh(fit_view=False)

                restored._write_blot_file("blot_update", "Blot Update")
                updated_data = json.loads(
                    (figure_mode_module.USER_BLOT_FILES_DIR / "blot_update.json")
                    .read_text(encoding="utf-8")
                )
                updated_slots = updated_data["project"]["panels"][0]["blot_slots"]
                self.assertEqual(
                    [slot.source_image_path for slot in restored_slots[:2]],
                    [slot["source_image_path"] for slot in updated_slots[:2]],
                )

                # A second update used to read the newly renumbered assets
                # through stale in-memory image_N paths and could swap or
                # blacken the saved blots.
                restored._write_blot_file("blot_update", "Blot Update")

                reopened = FigureModeWindow()
                reopened._load_blot_file("blot_update")
                reopened_slots = reopened._project.panels[0].blot_slots
                with Image.open(reopened_slots[0].source_image_path) as first:
                    first_pixels = np.array(first)
                with Image.open(reopened_slots[1].source_image_path) as second:
                    second_pixels = np.array(second)
                self.assertTrue(np.all(first_pixels == 52000))
                self.assertTrue(np.all(second_pixels == 4000))
                self.assertNotEqual(
                    Path(reopened_slots[0].source_image_path).read_bytes(),
                    Path(reopened_slots[1].source_image_path).read_bytes(),
                )
        finally:
            figure_mode_module.USER_BLOT_FILES_DIR = old_dir

    def test_missing_nonempty_blot_source_aborts_save_without_replacing_assets(self) -> None:
        old_dir = figure_mode_module.USER_BLOT_FILES_DIR
        try:
            with TemporaryDirectory() as tmp:
                figure_mode_module.USER_BLOT_FILES_DIR = Path(tmp) / "blot_files"
                source_path = Path(tmp) / "source.tif"
                Image.fromarray(np.full((12, 18), 24000, dtype=np.uint16)).save(source_path)

                window = FigureModeWindow()
                window._on_apply_template()
                slot = window._project.panels[0].blot_slots[0]
                slot.source_image_path = str(source_path)
                slot.bounding_box = ImageBBox(0.0, 0.0, 18.0, 12.0)
                window._recompute_and_refresh(fit_view=False)
                window._write_blot_file("blot_missing", "Blot Missing")
                saved_json = (
                    figure_mode_module.USER_BLOT_FILES_DIR / "blot_missing.json"
                ).read_bytes()
                saved_asset = (
                    figure_mode_module.USER_BLOT_FILES_DIR
                    / "blot_missing_assets"
                    / "image_1.tif"
                ).read_bytes()

                slot.source_image_path = str(Path(tmp) / "deleted_source.tif")
                with self.assertRaises(FileNotFoundError):
                    window._write_blot_file("blot_missing", "Blot Missing")

                self.assertEqual(
                    (
                        figure_mode_module.USER_BLOT_FILES_DIR
                        / "blot_missing.json"
                    ).read_bytes(),
                    saved_json,
                )
                self.assertEqual(
                    (
                        figure_mode_module.USER_BLOT_FILES_DIR
                        / "blot_missing_assets"
                        / "image_1.tif"
                    ).read_bytes(),
                    saved_asset,
                )
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
        self.assertGreater(text_items[1]["x"], text_items[0]["x"])
        self.assertGreater(line_items[1]["x"], line_items[0]["x"])
        self.assertEqual(text_items[1]["y"], text_items[0]["y"])
        self.assertEqual(line_items[1]["y"], line_items[0]["y"])

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
        window._manual_detect_radio.setChecked(True)
        window._on_apply_template()
        window._project.panels[0].condition_table = (
            FigureModeWindow._make_custom_condition_table(
                4,
                2,
                [[(1, 2), (3, 4)], [(1, 4)]],
            )
        )
        window._project.global_layout.show_condition_table = True
        window._recompute_and_refresh(fit_view=False)
        key, frame = next(iter(window._canvas._blot_frames.items()))
        window._canvas._select_blot_frame(frame, additive=False)
        window._canvas.scale(1.2, 1.2)
        slot = window._project.panels[0].blot_slots[0]
        original_frame_pos = QPointF(frame.pos())
        original_frame_rect = QRectF(frame.rect())
        original_lane_count = slot.lane_count
        original_lane_rois = list(slot.lane_rois)
        original_display_size = (
            slot.display_width_pt,
            slot.display_height_pt,
        )
        original_view_scale = window._canvas.transform().m11()
        original_view_center = window._canvas.mapFromScene(
            frame.sceneBoundingRect().center()
        )
        condition_geometry = [
            (
                item.kind,
                item.source_ref.key() if item.source_ref is not None else None,
                item.x_pt,
                item.y_pt,
                item.w_pt,
                item.h_pt,
            )
            for item in window._layout_result.items
            if item.kind in {"line", "table_cell"}
        ]

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
            self._app.processEvents()

        self.assertEqual(slot.bounding_box, ImageBBox(2.0, 3.0, 18.0, 9.0))
        self.assertFalse(slot.preserve_image_aspect)
        self.assertEqual(slot.lane_count, original_lane_count)
        self.assertEqual(slot.lane_rois, original_lane_rois)
        self.assertEqual(
            (slot.display_width_pt, slot.display_height_pt),
            original_display_size,
        )
        frame = window._canvas._blot_frames[key]
        self.assertEqual(frame.pos(), original_frame_pos)
        self.assertEqual(frame.rect(), original_frame_rect)
        self.assertAlmostEqual(
            window._canvas.transform().m11(),
            original_view_scale,
        )
        current_view_center = window._canvas.mapFromScene(
            frame.sceneBoundingRect().center()
        )
        self.assertAlmostEqual(
            current_view_center.x(), original_view_center.x(), delta=2.0
        )
        self.assertAlmostEqual(
            current_view_center.y(), original_view_center.y(), delta=2.0
        )
        self.assertEqual(
            [
                (
                    item.kind,
                    item.source_ref.key() if item.source_ref is not None else None,
                    item.x_pt,
                    item.y_pt,
                    item.w_pt,
                    item.h_pt,
                )
                for item in window._layout_result.items
                if item.kind in {"line", "table_cell"}
            ],
            condition_geometry,
        )
        pixmap = next(
            item for item in window._canvas._blot_content_items[key]
            if isinstance(item, QGraphicsPixmapItem)
        )
        self.assertAlmostEqual(
            pixmap.sceneBoundingRect().width(),
            frame.rect().width(),
            places=4,
        )
        self.assertAlmostEqual(
            pixmap.sceneBoundingRect().height(),
            frame.rect().height(),
            places=4,
        )
        self.assertEqual(
            slot.image_transform,
            {
                "low": 123,
                "high": 4567,
                "gamma": 1.5,
            "inverted": False,
            },
        )

    def test_default_auto_detect_applies_fitted_crop_to_selected_slot(self) -> None:
        window = FigureModeWindow()
        window._on_apply_template()
        window._project.panels[0].condition_table = (
            FigureModeWindow._make_custom_condition_table(
                4,
                2,
                [[(1, 2), (3, 4)], [(1, 4)]],
            )
        )
        window._project.global_layout.show_condition_table = True
        window._recompute_and_refresh(fit_view=False)
        key, frame = next(iter(window._canvas._blot_frames.items()))
        window._canvas._select_blot_frame(frame, additive=False)
        slot = window._project.panels[0].blot_slots[0]
        original_frame_pos = QPointF(frame.pos())
        original_frame_rect = QRectF(frame.rect())
        original_lane_count = slot.lane_count
        original_lane_rois = list(slot.lane_rois)
        original_display_size = (
            slot.display_width_pt,
            slot.display_height_pt,
        )
        condition_geometry = [
            (
                item.kind,
                item.source_ref.key() if item.source_ref is not None else None,
                item.x_pt,
                item.y_pt,
                item.w_pt,
                item.h_pt,
            )
            for item in window._layout_result.items
            if item.kind in {"line", "table_cell"}
        ]
        overlays = []

        with TemporaryDirectory() as tmp:
            path = f"{tmp}/source.tif"
            Image.fromarray(np.full((100, 140), 30000, dtype=np.uint16)).save(path)
            detections = []
            for lane_index, x in enumerate((20.0, 45.0, 70.0, 95.0), start=1):
                detections.append({
                    "lane_index": lane_index,
                    "lane_rect": QRectF(x, 20.0, 18.0, 60.0),
                    "bands": [{
                        "band_index": 1,
                        "row_index": 1,
                        "band_rect": QRectF(x, 44.0, 18.0, 10.0),
                    }],
                })

            window.set_auto_fit_detection_handler(
                lambda expected, reuse: {
                    "image_path": path,
                    "roi": QRectF(10.0, 10.0, 120.0, 80.0),
                    "image_transform": {
                        "low": 0,
                        "high": 65535,
                        "gamma": 1.0,
                        "inverted": False,
                    },
                    "geometry_transform": {
                        "rotation": 0.0,
                        "flip_x": True,
                        "flip_y": False,
                    },
                    "auto_detections": detections,
                    "image_size": QSizeF(140.0, 100.0),
                    "reused": reuse,
                }
            )
            window.set_auto_fit_overlay_handler(lambda rect: overlays.append(rect))

            self.assertTrue(window.apply_roi_to_selected_slot())

        self.assertEqual(
            slot.bounding_box,
            ImageBBox(16.0, 40.0, 101.0, 18.0),
        )
        self.assertEqual(slot.lane_crops, [])
        self.assertTrue(slot.preserve_image_aspect)
        self.assertEqual(
            slot.geometry_transform,
            {
                "rotation": 0.0,
                "flip_x": True,
                "flip_y": False,
            },
        )
        self.assertEqual(slot.lane_count, original_lane_count)
        self.assertEqual(slot.lane_rois, original_lane_rois)
        self.assertEqual(slot.display_width_pt, original_display_size[0])
        self.assertAlmostEqual(
            slot.display_height_pt,
            scene_to_pt(original_frame_rect.width())
            / (slot.bounding_box.w / slot.bounding_box.h),
        )
        frame = window._canvas._blot_frames[key]
        self.assertEqual(frame.pos(), original_frame_pos)
        self.assertAlmostEqual(frame.rect().width(), original_frame_rect.width())
        self.assertAlmostEqual(
            frame.rect().height(),
            original_frame_rect.width()
            / (slot.bounding_box.w / slot.bounding_box.h),
        )
        self.assertAlmostEqual(
            slot.bounding_box.w / slot.bounding_box.h,
            frame.rect().width() / frame.rect().height(),
        )
        self.assertLessEqual(slot.bounding_box.x, 20.0)
        self.assertGreaterEqual(slot.bounding_box.x + slot.bounding_box.w, 113.0)
        self.assertLessEqual(slot.bounding_box.y, 44.0)
        self.assertGreaterEqual(slot.bounding_box.y + slot.bounding_box.h, 54.0)
        self.assertEqual(
            [
                (
                    item.kind,
                    item.source_ref.key() if item.source_ref is not None else None,
                    item.x_pt,
                    item.y_pt,
                    item.w_pt,
                    item.h_pt,
                )
                for item in window._layout_result.items
                if item.kind in {"line", "table_cell"}
            ],
            condition_geometry,
        )
        pixmap = next(
            item for item in window._canvas._blot_content_items[key]
            if isinstance(item, QGraphicsPixmapItem)
        )
        self.assertAlmostEqual(
            pixmap.sceneBoundingRect().width(), frame.rect().width(), delta=0.01
        )
        self.assertAlmostEqual(
            pixmap.sceneBoundingRect().height(), frame.rect().height(), delta=0.01
        )
        self.assertIsInstance(overlays[-1], QRectF)
        self.assertTrue(window._auto_detect_radio.isChecked())

    def test_overlay_auto_fit_retains_presentation_geometry(self) -> None:
        window = FigureModeWindow()
        window._on_apply_template()
        overlay = window._canvas.add_overlay_blot_frame(4)
        detections = [
            {
                "lane_index": lane_index,
                "lane_rect": QRectF(x, 15.0, 18.0, 60.0),
                "bands": [{
                    "band_index": 1,
                    "row_index": 1,
                    "band_rect": QRectF(x, 40.0, 18.0, 10.0),
                }],
            }
            for lane_index, x in enumerate((15.0, 40.0, 65.0, 90.0), start=1)
        ]
        geometry = {
            "rotation": -6.5,
            "flip_x": True,
            "flip_y": False,
        }
        original_width = overlay.rect().width()

        with TemporaryDirectory() as tmp:
            path = f"{tmp}/source.tif"
            Image.fromarray(np.full((120, 160), 30000, dtype=np.uint16)).save(path)
            window.set_auto_fit_detection_handler(
                lambda expected, reuse: {
                    "image_path": path,
                    "roi": QRectF(0.0, 0.0, 160.0, 120.0),
                    "image_transform": None,
                    "geometry_transform": geometry,
                    "auto_detections": detections,
                    "image_size": QSizeF(160.0, 120.0),
                    "reused": reuse,
                }
            )

            self.assertTrue(window.apply_roi_to_selected_slot())

        self.assertEqual(overlay.geometry_transform, geometry)
        self.assertTrue(overlay.preserve_aspect)
        self.assertIsNotNone(overlay.roi)
        crop_ratio = overlay.roi["w"] / overlay.roi["h"]
        self.assertAlmostEqual(overlay.rect().width(), original_width)
        self.assertAlmostEqual(
            overlay.rect().height(),
            original_width / crop_ratio,
        )

    def test_manual_roi_preserves_two_panel_template_geometry(self) -> None:
        window = FigureModeWindow()
        window._manual_detect_radio.setChecked(True)
        window._project = TemplateEngine.build_project("normal_wb", 2, 4, 3)
        window._project.global_layout.panel_layout = "horizontal"
        window._project.global_layout.share_ib_labels = True
        window._project.global_layout.show_mw_labels = False
        window._project.global_layout.show_condition_table = True
        for panel in window._project.panels:
            panel.condition_table = FigureModeWindow._make_custom_condition_table(
                3,
                2,
                [[(1, 2), (3, 3)], [(1, 3)]],
            )
        window._rebuild_step4()
        window._recompute_and_refresh(fit_view=False)

        def geometry_snapshot() -> list[tuple]:
            return [
                (
                    item.kind,
                    item.source_ref.key() if item.source_ref is not None else None,
                    item.x_pt,
                    item.y_pt,
                    item.w_pt,
                    item.h_pt,
                )
                for item in window._layout_result.items
                if item.kind in {"blot", "line", "table_cell"}
            ]

        before = geometry_snapshot()
        level_2_line_key, level_2_line = next(
            (key, line)
            for key, line in window._canvas._line_items.items()
            if key[0] == 0 and key[3] == 0
        )
        current_line = QLineF(level_2_line.line())
        level_2_line.update_endpoint(
            "start", current_line.p1() + QPointF(-25.0, 0.0)
        )
        level_2_line.update_endpoint(
            "end", current_line.p2() + QPointF(140.0, 0.0)
        )
        expanded_line = level_2_line.line()
        expanded_endpoints = (
            level_2_line.mapToScene(expanded_line.p1()),
            level_2_line.mapToScene(expanded_line.p2()),
        )
        target_key = SourceRef(panel_idx=0, slot_idx=0, field="blot").key()
        target_frame = window._canvas._blot_frames[target_key]
        window._canvas._select_blot_frame(target_frame, additive=False)

        with TemporaryDirectory() as tmp:
            path = f"{tmp}/source.tif"
            Image.fromarray(np.full((80, 180), 30000, dtype=np.uint16)).save(path)
            window.set_active_image_provider(
                lambda: {
                    "image_path": path,
                    "roi": QRectF(8.0, 12.0, 150.0, 9.0),
                    "image_transform": {
                        "low": 0,
                        "high": 65535,
                        "gamma": 1.0,
                        "inverted": False,
                    },
                }
            )
            self.assertTrue(window.apply_roi_to_selected_slot())

        self.assertEqual(geometry_snapshot(), before)
        restored_line = window._canvas._line_items[level_2_line_key]
        restored_geometry = restored_line.line()
        self.assertEqual(
            restored_line.mapToScene(restored_geometry.p1()),
            expanded_endpoints[0],
        )
        self.assertEqual(
            restored_line.mapToScene(restored_geometry.p2()),
            expanded_endpoints[1],
        )

    def test_condition_template_can_be_undone_from_window_shortcut(self) -> None:
        window = FigureModeWindow()
        window._project = TemplateEngine.build_project("normal_wb", 1, 1, 4)
        window._project.panels[0].condition_table = None
        window._project.global_layout.show_condition_table = False
        window._recompute_and_refresh(fit_view=False)
        window._canvas_undo_stack.clear()

        window._apply_condition_template(
            attach_current=True,
            target_panel_idx=0,
            lane_count=4,
            condition_rows=2,
            group_ranges=[(1, 4)],
        )
        self.assertIsNotNone(window._project.panels[0].condition_table)

        window._undo_shortcut.activated.emit()
        window._run_queued_canvas_undo()

        self.assertIsNone(window._project.panels[0].condition_table)
        self.assertFalse(window._project.global_layout.show_condition_table)

    def test_copied_blot_frame_is_free_movable_and_roi_syncable(self) -> None:
        window = FigureModeWindow()
        window._manual_detect_radio.setChecked(True)
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
            pasted_rect = QRectF(pasted_blot.rect())

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
            self.assertEqual(pasted_blot.rect(), pasted_rect)
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

    def test_added_blot_width_and_position_follow_lanes_and_leftmost_panel(self) -> None:
        project = TemplateEngine.build_project("normal_wb", 2, 2, 4)
        project.global_layout.panel_layout = "horizontal"
        canvas = FigureCanvas()
        canvas.render(LayoutEngine().compute(project), project)

        panel_zero_frames = sorted(
            (
                frame
                for key, frame in canvas._blot_frames.items()
                if key[0] == 0
            ),
            key=lambda frame: frame.pos().y(),
        )
        panel_one_frames = [
            frame
            for key, frame in canvas._blot_frames.items()
            if key[0] == 1
        ]
        first, second = panel_zero_frames
        expected_gap = second.pos().y() - (
            first.pos().y() + first.rect().height()
        )

        added = canvas.add_overlay_blot_frame(6)

        self.assertEqual(added.lane_count, 6)
        self.assertAlmostEqual(added.pos().x(), first.pos().x())
        self.assertAlmostEqual(
            added.rect().width(),
            first.rect().width() / 4.0 * 6.0,
        )
        self.assertAlmostEqual(
            added.pos().y(),
            second.pos().y() + second.rect().height() + expected_gap,
        )
        self.assertTrue(
            all(added.pos().x() < frame.pos().x() for frame in panel_one_frames)
        )

        next_added = canvas.add_overlay_blot_frame(2)
        self.assertAlmostEqual(next_added.pos().x(), first.pos().x())
        self.assertAlmostEqual(
            next_added.pos().y(),
            added.pos().y() + added.rect().height() + expected_gap,
        )

    def test_add_blot_frame_cancel_does_not_create_target(self) -> None:
        window = FigureModeWindow()
        window._on_apply_template()
        original_count = len(window._canvas._overlay_items)

        with patch.object(QInputDialog, "getInt", return_value=(4, False)):
            window._on_add_blot_frame()

        self.assertEqual(len(window._canvas._overlay_items), original_count)

    def test_overlay_blot_lane_count_round_trips_and_drives_auto_fit(self) -> None:
        window = FigureModeWindow()
        window._on_apply_template()
        added = window._canvas.add_overlay_blot_frame(7)

        data = added.to_json()
        restored = BlotPlaceholderItem.from_json(data)
        target = window._selected_auto_fit_target()

        self.assertEqual(restored.lane_count, 7)
        self.assertIsNotNone(target)
        self.assertEqual(target[2], 7)
        self.assertEqual(len(target), 3)

    def test_add_blot_frame_button_creates_free_roi_target(self) -> None:
        window = FigureModeWindow()
        window._manual_detect_radio.setChecked(True)
        window._project = TemplateEngine.build_project("normal_wb", 1, 2, 4)
        window._active_template_id = "normal_wb"
        window._active_table_style = "none"
        window._rebuild_step4()
        window._recompute_and_refresh(fit_view=False)
        original_canvas_height = window._layout_result.canvas_height_pt

        with patch.object(QInputDialog, "getInt", return_value=(6, True)):
            window._on_add_blot_frame()

        added = window._canvas.selected_overlay_blot_items()
        self.assertEqual(len(added), 1)
        added_blot = added[0]
        self.assertIsInstance(added_blot, BlotPlaceholderItem)
        self.assertEqual(added_blot.lane_count, 6)
        self.assertEqual(added_blot.image_path, None)
        self.assertEqual(
            window._selected_slot_lbl.text(),
            "Selected target: added blot frame",
        )
        self.assertEqual(len(window._project.panels[0].blot_slots), 2)
        self.assertEqual(window._layout_result.canvas_height_pt, original_canvas_height)

        start_pos = QPointF(added_blot.pos())
        start_rect = QRectF(added_blot.rect())
        key = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Right,
            Qt.KeyboardModifier.ShiftModifier,
        )
        window._canvas.keyPressEvent(key)
        self.assertEqual(added_blot.pos(), start_pos + QPointF(5.0, 0.0))
        self.assertEqual(added_blot.rect(), start_rect)

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
        self.assertEqual(added_blot.rect(), start_rect)
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

    def test_extra_blot_move_undo_preserves_all_condition_geometry(self) -> None:
        window = FigureModeWindow()
        window._project = TemplateEngine.build_project("normal_wb", 2, 2, 4)
        window._project.global_layout.panel_layout = "horizontal"
        window._project.global_layout.share_ib_labels = True
        window._project.global_layout.show_mw_labels = False
        window._apply_condition_templates_to_panels([
            (0, 4, 2, [(1, 4)]),
            (1, 4, 2, [(1, 4)]),
        ])
        extra = window._canvas.add_overlay_blot_frame(3)
        original_extra_pos = QPointF(extra.pos())
        original_project = copy.deepcopy(window._project)

        def non_overlay_geometry() -> dict:
            return {
                "texts": {
                    key: (
                        QPointF(item.pos()),
                        QRectF(item.editor_rect()),
                    )
                    for key, item in window._canvas._text_items.items()
                },
                "lines": {
                    key: (
                        item.mapToScene(item.line().p1()),
                        item.mapToScene(item.line().p2()),
                    )
                    for key, item in window._canvas._line_items.items()
                },
                "blots": {
                    key: (QPointF(item.pos()), QRectF(item.rect()))
                    for key, item in window._canvas._blot_frames.items()
                },
            }

        original_geometry = non_overlay_geometry()

        # Toolbar Undo restores only the extra frame's immediately preceding move.
        window._canvas_undo_stack.clear()
        window._remember_canvas_undo_state()
        extra.setPos(extra.pos() + QPointF(31.0, 17.0))
        window._annot_undo_btn.click()

        self.assertEqual(non_overlay_geometry(), original_geometry)
        self.assertEqual(window._project, original_project)
        self.assertEqual(len(window._canvas_undo_stack), 0)
        restored_extra = window._canvas.selected_overlay_blot_items()
        if not restored_extra:
            restored_extra = [
                item
                for item in window._canvas._overlay_items
                if isinstance(item, BlotPlaceholderItem)
            ]
        self.assertEqual(restored_extra[0].pos(), original_extra_pos)

        # Command+Z follows the same single-operation contract.
        window._canvas_undo_stack.clear()
        window._remember_canvas_undo_state()
        restored_extra[0].setPos(original_extra_pos + QPointF(-19.0, 23.0))
        event = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Z,
            Qt.KeyboardModifier.MetaModifier,
        )
        window._canvas.keyPressEvent(event)
        self._app.processEvents()
        self._app.processEvents()

        self.assertEqual(non_overlay_geometry(), original_geometry)
        self.assertEqual(window._project, original_project)
        self.assertEqual(len(window._canvas_undo_stack), 0)
        command_restored_extra = [
            item
            for item in window._canvas._overlay_items
            if isinstance(item, BlotPlaceholderItem)
        ]
        self.assertEqual(command_restored_extra[0].pos(), original_extra_pos)

    def test_toolbar_align_combo_label(self) -> None:
        window = FigureModeWindow()

        self.assertEqual(window._align_text_boxes_combo.itemText(0), "Align text Boxes")
        self.assertEqual(window._align_text_boxes_combo.itemText(1), "Align Left")
        self.assertEqual(window._align_text_boxes_combo.itemText(8), "Distribute Vertically")
        self.assertTrue(window._text_inside_left_btn.text() == "")
        self.assertFalse(window._text_inside_left_btn.icon().isNull())
        self.assertFalse(window._text_inside_center_btn.icon().isNull())
        self.assertFalse(window._text_inside_right_btn.icon().isNull())
        self.assertFalse(hasattr(window, "_text_rotation_spin"))
        self.assertFalse(hasattr(window, "_rotation_label"))


if __name__ == "__main__":
    unittest.main()
