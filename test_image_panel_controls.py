from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint, QRectF, QSizeF, Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication

from gui.image_canvas import ImageCanvas
from gui.main_window import MainWindow, _ImagePanelWidget


class _LeftPressEvent:
    def __init__(self, pos: QPoint) -> None:
        self._pos = QPoint(pos)
        self.accepted = False

    def button(self):
        return Qt.MouseButton.LeftButton

    def pos(self) -> QPoint:
        return QPoint(self._pos)

    def accept(self) -> None:
        self.accepted = True


class ImagePanelControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    @staticmethod
    def _red_pixel_count(widget) -> int:
        image = widget.grab().toImage()
        count = 0
        for y in range(image.height()):
            for x in range(image.width()):
                color = QColor(image.pixel(x, y))
                if (
                    color.red() > 150
                    and color.red() > color.green() * 1.35
                    and color.red() > color.blue() * 1.2
                ):
                    count += 1
        return count

    def _make_window(self) -> MainWindow:
        with (
            patch("gui.main_window.AppPersistence.update_config", return_value=None),
            patch("gui.main_window.AppPersistence.read_config", return_value={"ui": {}}),
        ):
            return MainWindow()

    def test_selected_checkbox_adds_red_tick_only_when_checked(self) -> None:
        panel = _ImagePanelWidget()
        try:
            panel.resize(420, 260)
            panel.show()
            self._app.processEvents()

            panel.select_checkbox.setChecked(False)
            self._app.processEvents()
            unchecked_red = self._red_pixel_count(panel.select_checkbox)

            panel.select_checkbox.setChecked(True)
            panel.select_checkbox.setEnabled(False)
            self._app.processEvents()
            checked_red = self._red_pixel_count(panel.select_checkbox)

            self.assertEqual(unchecked_red, 0)
            self.assertGreater(checked_red, 0)
        finally:
            panel.close()

    def test_each_panel_has_working_zoom_buttons_that_activate_its_image(self) -> None:
        window = self._make_window()
        try:
            window._slot_states[0]["path"] = "first.tif"
            window._slot_states[0]["selected"] = True
            window._slot_states[1]["path"] = "second.tif"
            window._refresh_image_panel_layout()

            second_panel = window._image_panels[1]
            self.assertFalse(second_panel.zoom_in_btn.icon().isNull())
            self.assertFalse(second_panel.zoom_out_btn.icon().isNull())
            self.assertTrue(second_panel.zoom_in_btn.isEnabled())
            self.assertEqual(window._active_slot_index, 0)

            scale_before = second_panel.canvas.transform().m11()
            second_panel.zoom_in_btn.click()

            self.assertEqual(window._active_slot_index, 1)
            self.assertTrue(second_panel.select_checkbox.isChecked())
            self.assertFalse(window._image_panels[0].select_checkbox.isChecked())
            self.assertAlmostEqual(
                second_panel.canvas.transform().m11(),
                scale_before * 1.2,
            )

            second_panel.zoom_out_btn.click()
            self.assertAlmostEqual(second_panel.canvas.transform().m11(), scale_before)
        finally:
            window.close()

    def _canvas_with_lane_roi(self) -> ImageCanvas:
        canvas = ImageCanvas()
        canvas.resize(240, 140)
        pixmap = QPixmap(200, 100)
        pixmap.fill(QColor("white"))
        canvas._pixmap_item = canvas._scene.addPixmap(pixmap)
        canvas._pixmap_original_size = QSizeF(200.0, 100.0)
        canvas._scene.setSceneRect(canvas._pixmap_item.boundingRect())
        canvas._roi_item = canvas._scene.addRect(QRectF(20.0, 20.0, 50.0, 30.0))
        canvas.show()
        self._app.processEvents()
        return canvas

    def test_click_more_than_four_pixels_outside_lane_roi_cancels_it(self) -> None:
        canvas = self._canvas_with_lane_roi()
        try:
            cleared: list[bool] = []
            canvas.roi_cleared.connect(lambda: cleared.append(True))
            viewport_rect = canvas.mapFromScene(canvas.get_roi()).boundingRect()
            event = _LeftPressEvent(
                QPoint(viewport_rect.right() + 5, viewport_rect.center().y())
            )

            canvas.mousePressEvent(event)

            self.assertTrue(event.accepted)
            self.assertEqual(cleared, [True])
            self.assertIsNone(canvas.get_roi())
            self.assertFalse(canvas._drawing)
        finally:
            canvas.close()

    def test_click_within_four_pixel_lane_margin_continues_to_band_roi(self) -> None:
        canvas = self._canvas_with_lane_roi()
        try:
            cleared: list[bool] = []
            canvas.roi_cleared.connect(lambda: cleared.append(True))
            viewport_rect = canvas.mapFromScene(canvas.get_roi()).boundingRect()
            click = QPoint(viewport_rect.right() + 4, viewport_rect.center().y())
            self.assertTrue(canvas._lane_roi_contains_viewport_pos(click))
            event = _LeftPressEvent(click)

            canvas.mousePressEvent(event)

            self.assertTrue(event.accepted)
            self.assertEqual(cleared, [])
            self.assertIsNotNone(canvas.get_roi())
            self.assertTrue(canvas._drawing)
            self.assertTrue(canvas._drawing_band_roi)
        finally:
            canvas.close()


if __name__ == "__main__":
    unittest.main()
