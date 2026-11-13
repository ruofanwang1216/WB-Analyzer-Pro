"""Automatic lane/band detection for the densitometry workflow."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QGraphicsItem, QGraphicsRectItem, QGraphicsTextItem,
)

from core.lane_auto_detect import detect_lanes_in_roi
from core.measure import background_strips, local_background_level, measure_all_lanes_in_array
from gui.main_window import MainWindow


def _synthetic_blot(
    lane_centres: list[float],
    amplitudes: list[float],
    *,
    rows: tuple[int, ...] = (40,),
    shape: tuple[int, int] = (90, 260),
    lane_width: float = 16.0,
    band_height: float = 3.0,
    background: float = 3000.0,
    seed: int = 7,
    adjacent: tuple[tuple[float, float, float], ...] = (),
) -> np.ndarray:
    """16-bit light-on-dark blot: soft-edged bands on a noisy, tilted background."""
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    image = background + 4.0 * xx + 2.0 * yy
    for row in rows:
        for centre, amplitude in zip(lane_centres, amplitudes):
            across = 1.0 / (1.0 + np.exp((np.abs(xx - centre) - lane_width / 2.0) / 1.2))
            along = np.exp(-0.5 * ((yy - row) / band_height) ** 2)
            image += amplitude * across * along
    for offset, relative, height in adjacent:
        for row in rows:
            for centre, amplitude in zip(lane_centres, amplitudes):
                across = 1.0 / (1.0 + np.exp((np.abs(xx - centre) - lane_width / 2.0) / 1.2))
                along = np.exp(-0.5 * ((yy - (row + offset)) / height) ** 2)
                image += relative * amplitude * across * along
    image += np.random.default_rng(seed).normal(0.0, 60.0, size=shape)
    return np.clip(image, 0, 65535).astype(np.uint16)


def _band_centres(result: dict) -> list[float]:
    return [
        lane["bands"][0]["band_rect"][0] + lane["bands"][0]["band_rect"][2] / 2.0
        for lane in result["lanes"]
    ]


class LaneAutoDetectCoreTests(unittest.TestCase):
    ROI = (10.0, 10.0, 240.0, 70.0)

    def assertCentresClose(self, found: list[float], expected: list[float], tol: float = 4.0) -> None:
        self.assertEqual(len(found), len(expected))
        for got, want in zip(found, expected):
            self.assertLess(abs(got - want), tol, f"centres {found} vs expected {expected}")

    def test_finds_evenly_spaced_lanes_and_one_band_row(self) -> None:
        centres = [40.0, 64.0, 88.0, 112.0, 136.0, 160.0]
        image = _synthetic_blot(centres, [20000.0] * 6)

        result = detect_lanes_in_roi(image, self.ROI, 6)

        self.assertEqual(len(result["lanes"]), 6)
        self.assertEqual(len(result["rows"]), 1)
        self.assertCentresClose(_band_centres(result), centres)
        for lane in result["lanes"]:
            x, y, width, height = lane["bands"][0]["band_rect"]
            self.assertLess(y, 40.0)
            self.assertGreater(y + height, 40.0)

    def test_faint_16bit_bands_are_not_lost(self) -> None:
        # Peak signal is ~1% of the 16-bit range: dividing by 256 would leave
        # these bands only a couple of grey levels above the background.
        centres = [40.0, 64.0, 88.0, 112.0]
        image = _synthetic_blot(centres, [700.0] * 4)

        result = detect_lanes_in_roi(image, self.ROI, 4)

        self.assertEqual(len(result["rows"]), 1)
        self.assertCentresClose(_band_centres(result), centres)

    def test_empty_lane_keeps_its_slot_when_counted(self) -> None:
        # Lane 4 of 7 has no band; the user counts it, so it must get a box.
        centres = [40.0, 64.0, 88.0, 112.0, 136.0, 160.0, 184.0]
        amplitudes = [20000.0, 18000.0, 22000.0, 0.0, 19000.0, 21000.0, 20000.0]
        image = _synthetic_blot(centres, amplitudes)

        result = detect_lanes_in_roi(image, self.ROI, 7)

        self.assertCentresClose(_band_centres(result), centres)
        self.assertTrue(result["lanes"][3]["bands"][0]["empty"])
        self.assertFalse(result["lanes"][2]["bands"][0]["empty"])

    def test_gap_is_skipped_when_lane_count_excludes_it(self) -> None:
        # Same blot, but the user says 6 lanes: the gap must not get a box.
        centres = [40.0, 64.0, 88.0, 136.0, 160.0, 184.0]
        image = _synthetic_blot(centres, [20000.0] * 6)

        result = detect_lanes_in_roi(image, self.ROI, 6)

        self.assertCentresClose(_band_centres(result), centres)

    def test_strong_and_weak_lanes_are_both_placed(self) -> None:
        centres = [40.0, 64.0, 88.0, 112.0, 136.0, 160.0]
        amplitudes = [40000.0, 38000.0, 42000.0, 1500.0, 1300.0, 1600.0]
        image = _synthetic_blot(centres, amplitudes)

        result = detect_lanes_in_roi(image, self.ROI, 6)

        self.assertCentresClose(_band_centres(result), centres)

    def test_two_band_rows_share_box_size_within_a_row(self) -> None:
        centres = [40.0, 64.0, 88.0, 112.0]
        image = _synthetic_blot(centres, [20000.0] * 4, rows=(25, 60))

        result = detect_lanes_in_roi(image, self.ROI, 4)

        self.assertEqual(len(result["rows"]), 2)
        for row_index in (0, 1):
            sizes = {
                (lane["bands"][row_index]["band_rect"][2], lane["bands"][row_index]["band_rect"][3])
                for lane in result["lanes"]
            }
            self.assertEqual(len(sizes), 1, "boxes of one row must have equal area")

    def test_boxes_wrap_bands_that_fill_the_lane(self) -> None:
        # Bands nearly as wide as the lane pitch, each nudged off the lane
        # centre: every box must still contain its whole band.
        centres = [41.0, 63.0, 89.0, 111.0, 137.0, 159.0]
        image = _synthetic_blot(centres, [20000.0] * 6, lane_width=21.0)

        result = detect_lanes_in_roi(image, self.ROI, 6)

        boxes = [lane["bands"][0]["band_rect"] for lane in result["lanes"]]
        for (x, _y, width, _h), centre in zip(boxes, centres):
            self.assertLessEqual(x, centre - 10.5 + 1.5)
            self.assertGreaterEqual(x + width, centre + 10.5 - 1.5)
        for left, right in zip(boxes, boxes[1:]):
            self.assertLessEqual(left[0] + left[2], right[0] + 1e-6, "boxes must not overlap")
        self.assertEqual(len({box[2] for box in boxes}), 1)

    def test_faint_adjacent_band_is_left_out_by_default_and_can_be_included(self) -> None:
        # A faint band 8 px below every main band: too weak to be a row of
        # its own, so it would silently end up inside the main band's box.
        centres = [40.0, 64.0, 88.0, 112.0]
        image = _synthetic_blot(centres, [20000.0] * 4, adjacent=((8.0, 0.25, 2.5),))

        main_only = detect_lanes_in_roi(image, self.ROI, 4)
        merged = detect_lanes_in_roi(image, self.ROI, 4, include_adjacent=True)

        self.assertEqual(main_only["adjacent_rows"], [{"row_index": 1, "side": "below"}])
        _x, y, _w, height = main_only["lanes"][0]["bands"][0]["band_rect"]
        self.assertLess(y, 40.0)
        self.assertLessEqual(y + height, 46.5, "main-only box must stop before the faint band at y=48")
        _x, y2, _w, height2 = merged["lanes"][0]["bands"][0]["band_rect"]
        self.assertGreaterEqual(y2 + height2, 50.0, "merged box must take in the faint band")
        self.assertLess(y2, 40.0)

    def test_clean_band_and_symmetric_halo_offer_no_adjacent_choice(self) -> None:
        centres = [40.0, 64.0, 88.0, 112.0]
        clean = _synthetic_blot(centres, [20000.0] * 4)
        halo = _synthetic_blot(centres, [20000.0] * 4, adjacent=((0.0, 0.25, 9.0),))

        self.assertEqual(detect_lanes_in_roi(clean, self.ROI, 4)["adjacent_rows"], [])
        self.assertEqual(detect_lanes_in_roi(halo, self.ROI, 4)["adjacent_rows"], [])

    def test_band_cut_by_a_tight_roi_edge_is_still_found(self) -> None:
        centres = [40.0, 64.0, 88.0, 112.0]
        image = _synthetic_blot(centres, [20000.0] * 4)

        # The ROI starts one pixel above the band's peak row (y=40).
        result = detect_lanes_in_roi(image, (10.0, 39.0, 240.0, 30.0), 4)

        self.assertEqual(len(result["rows"]), 1)
        self.assertCentresClose(_band_centres(result), centres)

    def test_dark_on_light_images_are_handled(self) -> None:
        centres = [40.0, 64.0, 88.0, 112.0]
        image = 65535 - _synthetic_blot(centres, [20000.0] * 4)

        result = detect_lanes_in_roi(image, self.ROI, 4)

        self.assertFalse(result["light_on_dark"])
        self.assertCentresClose(_band_centres(result), centres)

    def test_tiny_roi_returns_no_lanes(self) -> None:
        image = _synthetic_blot([40.0], [20000.0])
        result = detect_lanes_in_roi(image, (10.0, 10.0, 4.0, 4.0), 3)
        self.assertEqual(result["lanes"], [])


class LocalBackgroundTests(unittest.TestCase):
    def test_strips_hug_the_box_and_are_a_quarter_of_its_height(self) -> None:
        strips = background_strips((20.0, 30.0, 24.0, 12.0), [], (100.0, 100.0))
        self.assertEqual(strips, [(20.0, 27.0, 24.0, 3.0), (20.0, 42.0, 24.0, 3.0)])

    def test_strip_thickness_is_limited_to_two_to_four_pixels(self) -> None:
        thin = background_strips((20.0, 30.0, 24.0, 4.0), [], (100.0, 100.0))
        tall = background_strips((20.0, 30.0, 24.0, 40.0), [], (100.0, 100.0))
        self.assertEqual({strip[3] for strip in thin}, {2.0})
        self.assertEqual({strip[3] for strip in tall}, {4.0})

    def test_side_touching_another_box_or_the_image_edge_is_dropped(self) -> None:
        other_below = (20.0, 43.0, 24.0, 10.0)
        self.assertEqual(
            background_strips((20.0, 30.0, 24.0, 12.0), [other_below], (100.0, 100.0)),
            [(20.0, 27.0, 24.0, 3.0)],
        )
        self.assertEqual(
            background_strips((20.0, 1.0, 24.0, 12.0), [], (100.0, 100.0)),
            [(20.0, 13.0, 24.0, 3.0)],
        )

    def _image(self) -> np.ndarray:
        image = np.full((60, 60), 1000, dtype=np.uint16)
        image[30:42, 20:44] = 5000  # the band, exactly filling its box
        return image

    def _roi(self, strips: list[tuple[float, float, float, float]]) -> dict:
        return {
            "x": 20, "y": 30, "width": 24, "height": 12, "lane": 1, "band": 1,
            "background_rois": [
                {"x": x, "y": y, "width": w, "height": h} for x, y, w, h in strips
            ],
            "light_on_dark": True,
        }

    def test_net_values_subtract_the_local_background(self) -> None:
        image = self._image()
        strips = background_strips((20.0, 30.0, 24.0, 12.0), [], (60.0, 60.0))

        row = measure_all_lanes_in_array(image, [self._roi(strips)])[0]

        self.assertEqual(row["Mean"], 5000.0)
        self.assertEqual(row["Background"], 1000.0)
        self.assertEqual(row["Net Mean"], 4000.0)
        self.assertEqual(row["Net IntDen"], 4000.0 * 24 * 12)

    def test_contaminated_side_is_ignored(self) -> None:
        # A non-specific band right above the box must not raise the background.
        image = self._image()
        image[27:30, 20:44] = 3000
        strips = background_strips((20.0, 30.0, 24.0, 12.0), [], (60.0, 60.0))

        level = local_background_level(
            image, [{"x": x, "y": y, "width": w, "height": h} for x, y, w, h in strips]
        )

        self.assertEqual(level, 1000.0)

    def test_a_few_stray_pixels_do_not_move_the_background(self) -> None:
        image = self._image()
        image[28, 20:26] = 60000  # dust speck inside the upper strip
        strips = background_strips((20.0, 30.0, 24.0, 12.0), [], (60.0, 60.0))

        row = measure_all_lanes_in_array(image, [self._roi(strips)])[0]

        self.assertEqual(row["Background"], 1000.0)

    def test_no_usable_background_is_reported_not_guessed(self) -> None:
        row = measure_all_lanes_in_array(self._image(), [self._roi([])])[0]
        self.assertIsNone(row["Background"])
        self.assertIsNone(row["Net IntDen"])

    def test_plain_rois_keep_the_original_columns(self) -> None:
        row = measure_all_lanes_in_array(
            self._image(), [{"x": 20, "y": 30, "width": 24, "height": 12}]
        )[0]
        self.assertNotIn("Background", row)


class LaneAutoDetectWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _window_with_blot(self, adjacent: tuple = ()) -> MainWindow:
        with (
            patch("gui.main_window.AppPersistence.update_config", return_value=None),
            patch("gui.main_window.AppPersistence.read_config", return_value={"ui": {}}),
        ):
            window = MainWindow()
        centres = [40.0, 64.0, 88.0, 112.0, 136.0, 160.0]
        image = _synthetic_blot(centres, [20000.0] * 6, adjacent=adjacent)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = str(Path(folder.name) / "blot.tif")
        Image.fromarray(image).save(path)
        window._set_active_slot(0)
        window.canvas.load_image_blocking(path)
        window._image_path = path
        window._slot_states[0]["path"] = path
        roi = QRectF(10, 10, 240, 70)
        window.canvas._roi_item = window.canvas._scene.addRect(roi)
        window._on_roi_changed(roi)
        return window

    def test_auto_detect_shows_editable_boxes_and_enables_analyze(self) -> None:
        window = self._window_with_blot()
        try:
            window.param_panel.lane_count.setValue(6)
            self.assertFalse(window._act_analyze.isEnabled())

            window.param_panel._auto_lane_btn.click()

            self.assertTrue(window.canvas.is_quant_auto_review())
            self.assertEqual(len(window.canvas._auto_band_items), 6)
            self.assertTrue(window._act_analyze.isEnabled())
            rois = window._quant_auto_band_rois()
            self.assertEqual([roi["lane"] for roi in rois], [1, 2, 3, 4, 5, 6])
            self.assertEqual([roi["band_label"] for roi in rois], [f"#{i}" for i in range(1, 7)])
            self.assertTrue(all(len(roi["background_rois"]) == 2 for roi in rois))
            self.assertEqual(len(window.canvas._auto_bg_strip_items), 12)
        finally:
            window.close()

    def test_analyze_measures_the_reviewed_boxes(self) -> None:
        window = self._window_with_blot()
        try:
            window.param_panel.lane_count.setValue(6)
            window.param_panel._auto_lane_btn.click()
            removed = window.canvas._auto_band_items.pop(0)
            window.canvas._scene.removeItem(removed)
            window.canvas._emit_auto_rois_changed()

            with (
                patch.object(window, "_start_measurement_worker") as worker,
                patch.object(window._persistence, "remember_analysis_debug"),
                patch.object(
                    window.canvas, "map_canvas_rois_to_raw", side_effect=lambda rois: rois
                ),
            ):
                window._run_analysis()

            worker.assert_called_once()
            measured = worker.call_args.args[1]
            self.assertEqual([roi["lane"] for roi in measured], [2, 3, 4, 5, 6])
        finally:
            window.close()

    def test_small_box_moves_from_its_middle_and_resizes_from_its_edge(self) -> None:
        window = self._window_with_blot()
        try:
            window.param_panel.lane_count.setValue(6)
            window.param_panel._auto_lane_btn.click()
            item = window.canvas._auto_band_items[0]
            rect = item.rect()

            self.assertIsNone(item._handle_for_pos(rect.center()))
            self.assertEqual(
                item._handle_for_pos(QPointF(rect.right(), rect.center().y())), "right"
            )
        finally:
            window.close()

    def test_arrow_keys_nudge_the_selected_box_without_resizing(self) -> None:
        window = self._window_with_blot()
        try:
            window.param_panel.lane_count.setValue(6)
            window.param_panel._auto_lane_btn.click()
            item = window.canvas._auto_band_items[0]
            before = item.scene_rect()
            item.setSelected(True)

            QTest.keyClick(window.canvas, Qt.Key.Key_Right)
            QTest.keyClick(window.canvas, Qt.Key.Key_Down)

            after = item.scene_rect()
            self.assertEqual((after.x(), after.y()), (before.x() + 1.0, before.y() + 1.0))
            self.assertEqual((after.width(), after.height()), (before.width(), before.height()))
        finally:
            window.close()

    def test_review_keeps_lane_lines_and_labels_off_the_bands(self) -> None:
        window = self._window_with_blot()
        try:
            window.param_panel.lane_count.setValue(6)
            window.param_panel._auto_lane_btn.click()
            canvas = window.canvas

            frames = [item for item in canvas._lane_items if isinstance(item, QGraphicsRectItem)]
            self.assertEqual(len(frames), 6)
            for frame in frames:
                self.assertTrue(frame.pen().isCosmetic(), "lane borders must stay hairlines at any zoom")
                self.assertEqual(frame.brush().style(), Qt.BrushStyle.NoBrush)
            lane_labels = [item for item in canvas._lane_items if isinstance(item, QGraphicsTextItem)]
            self.assertTrue(lane_labels and not any(label.isVisible() for label in lane_labels))
            for label in canvas._auto_band_labels:
                self.assertTrue(
                    label.flags() & QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations
                )
        finally:
            window.close()

    def test_adjacent_band_choice_appears_only_when_found_and_reruns_detection(self) -> None:
        window = self._window_with_blot(adjacent=((8.0, 0.25, 2.5),))
        try:
            panel = window.param_panel
            panel.lane_count.setValue(6)
            self.assertFalse(panel.adjacent_band_option_available())

            panel._auto_lane_btn.click()

            self.assertTrue(panel.adjacent_band_option_available())
            self.assertFalse(panel.include_adjacent_band())
            main_height = window.canvas._auto_band_items[0].scene_rect().height()

            panel._adjacent_band_check.setChecked(True)

            self.assertTrue(window.canvas.is_quant_auto_review())
            merged_height = window.canvas._auto_band_items[0].scene_rect().height()
            self.assertGreater(merged_height, main_height + 3.0)

            window._on_roi_changed(QRectF(20, 20, 120, 40))
            self.assertFalse(panel.adjacent_band_option_available())
            self.assertFalse(panel._adjacent_band_check.isChecked())
        finally:
            window.close()

    def test_clean_blot_shows_no_adjacent_band_choice(self) -> None:
        window = self._window_with_blot()
        try:
            window.param_panel.lane_count.setValue(6)
            window.param_panel._auto_lane_btn.click()
            self.assertFalse(window.param_panel.adjacent_band_option_available())
        finally:
            window.close()

    def test_wb_plot_auto_fit_uses_the_lane_fit_detector_when_lane_count_is_known(self) -> None:
        window = self._window_with_blot()
        try:
            roi = QRectF(10, 10, 240, 70)

            found = window._wb_plot_lane_fit_detections(roi, 6)

            self.assertIsNotNone(found)
            detections, metadata = found
            self.assertEqual(metadata["mode"], "lane_fit")
            self.assertEqual([lane["lane_index"] for lane in detections], [1, 2, 3, 4, 5, 6])
            centres = [lane["bands"][0]["band_rect"].center().x() for lane in detections]
            for got, want in zip(centres, [40.0, 64.0, 88.0, 112.0, 136.0, 160.0]):
                self.assertLess(abs(got - want), 4.0)
            self.assertTrue(all(lane["bands"][0]["row_index"] == 1 for lane in detections))
        finally:
            window.close()

    def test_wb_plot_auto_fit_falls_back_without_a_lane_count(self) -> None:
        window = self._window_with_blot()
        try:
            self.assertIsNone(window._wb_plot_lane_fit_detections(QRectF(10, 10, 240, 70), None))
            self.assertIsNone(window._wb_plot_lane_fit_detections(None, 6))
        finally:
            window.close()

    def test_new_roi_returns_to_the_manual_workflow(self) -> None:
        window = self._window_with_blot()
        try:
            window.param_panel.lane_count.setValue(6)
            window.param_panel._auto_lane_btn.click()

            window._on_roi_changed(QRectF(20, 20, 120, 40))

            self.assertFalse(window.canvas.is_quant_auto_review())
            self.assertEqual(window._auto_detections, [])
            self.assertEqual(len(window._lane_rects), 6)
            self.assertFalse(window._act_analyze.isEnabled())
        finally:
            window.close()

    def test_auto_detect_without_roi_does_nothing(self) -> None:
        with (
            patch("gui.main_window.AppPersistence.update_config", return_value=None),
            patch("gui.main_window.AppPersistence.read_config", return_value={"ui": {}}),
        ):
            window = MainWindow()
        try:
            with patch("gui.main_window_auto_lanes.QMessageBox.information") as info:
                window.param_panel._auto_lane_btn.click()
            info.assert_called_once()
            self.assertFalse(window.canvas.is_quant_auto_review())
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
