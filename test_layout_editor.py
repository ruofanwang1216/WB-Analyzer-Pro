import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtWidgets import QApplication

from gui.layout_editor_scene import GraphicsEditorScene, update_font_for_selected_items


class LayoutEditorSceneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_save_and_load_editable_items(self) -> None:
        scene = GraphicsEditorScene()
        text = scene.add_text_box(QPointF(10, 20), "Hello")
        text.setSelected(True)
        update_font_for_selected_items(scene, size=16, bold=True)
        scene.add_line(QPointF(0, 0), QPointF(100, 30), dashed=True)
        scene.add_blot_placeholder(QRectF(5, 5, 80, 20))

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "layout.json"
            scene.save_layout_to_json(path)

            loaded = GraphicsEditorScene()
            loaded.load_layout_from_json(path)

        top_level = [item for item in loaded.items() if item.parentItem() is None]
        self.assertEqual(len(top_level), 3)

    def test_font_update_only_affects_selected_text(self) -> None:
        scene = GraphicsEditorScene()
        text = scene.add_text_box(QPointF(0, 0), "A")
        scene.add_text_box(QPointF(0, 40), "B")
        text.setSelected(True)

        update_font_for_selected_items(scene, size=18, bold=True)

        self.assertEqual(text.font().pointSizeF(), 18)
        self.assertTrue(text.font().bold())

    def test_line_endpoint_drag_updates_angle_label(self) -> None:
        scene = GraphicsEditorScene()
        line = scene.add_line(QPointF(0, 0), QPointF(100, 0))

        line.begin_endpoint_drag()
        line.update_endpoint("end", QPointF(100, 100))

        self.assertTrue(line._angle_label.isVisible())
        self.assertEqual(line._angle_label.text(), "45 deg")

        line.end_endpoint_drag()
        self.assertFalse(line._angle_label.isVisible())

    def test_line_angle_label_hides_when_drag_is_interrupted(self) -> None:
        scene = GraphicsEditorScene()
        line = scene.add_line(QPointF(0, 0), QPointF(100, 0))
        line.setSelected(True)

        line.begin_endpoint_drag()
        line.update_endpoint("end", QPointF(60, 80))
        line.setSelected(False)

        self.assertFalse(line._angle_label.isVisible())


if __name__ == "__main__":
    unittest.main()
