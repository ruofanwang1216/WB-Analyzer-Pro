from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from PySide6.QtWidgets import QApplication, QTableWidgetItem
from PySide6.QtCore import QPointF, QRectF, QSizeF, Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtTest import QTest

from core.band_detector import auto_detect_all, auto_detect_guided, group_auto_detected_rows, _build_auto_params, _soft_constrain_lane_centers, _merge_oversplit_lanes
from gui.figure_generation import ColumnTableWindow
from gui.main_window import MainWindow
from gui.image_canvas import ImageCanvas
from gui.param_panel import ParamPanel


class AutoDetectPerLaneBandRoiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_auto_detect_preserves_true_per_lane_band_rectangles(self) -> None:
        lane_candidates = [
            {
                "peak": 20,
                "x_range": (10, 30),
                "lane_rect": QRectF(10, 24, 20, 60),
            },
            {
                "peak": 60,
                "x_range": (50, 72),
                "lane_rect": QRectF(50, 24, 22, 60),
            },
        ]
        lane_1_bands = [
            {
                "band_index": 1,
                "band_rect": QRectF(10, 34, 20, 9),
            },
        ]
        lane_2_bands = [
            {
                "band_index": 1,
                "band_rect": QRectF(50, 72, 22, 17),
            },
        ]

        with (
            patch("core.band_detector._load_8bit", return_value=np.zeros((120, 100), dtype=np.uint8)),
            patch("core.band_detector._prepare_signal_for_auto_detection", return_value=np.zeros((120, 100), dtype=np.float64)),
            patch(
                "core.band_detector._find_band_rich_horizontal_zone",
                return_value=((24, 84), {"failure_stage": None, "message": "", "selected_peaks": [40], "peak_widths": [18.0]}),
            ),
            patch(
                "core.band_detector._detect_lanes_within_zone",
                return_value=(lane_candidates, {"failure_stage": None, "message": "", "lane_candidates": 2, "kept_lanes": 2}),
            ),
            patch(
                "core.band_detector._detect_bands_in_lane",
                side_effect=[
                    (lane_1_bands, {"failure_stage": None, "message": "", "band_peaks": 1}),
                    (lane_2_bands, {"failure_stage": None, "message": "", "band_peaks": 1}),
                ],
            ),
        ):
            detections = auto_detect_all("synthetic.png", return_metadata=False)

        self.assertEqual(len(detections), 2)

        band_1_rect = detections[0]["bands"][0]["band_rect"]
        band_2_rect = detections[1]["bands"][0]["band_rect"]

        self.assertEqual((band_1_rect.x(), band_1_rect.y(), band_1_rect.width(), band_1_rect.height()), (10.0, 34.0, 20.0, 9.0))
        self.assertEqual((band_2_rect.x(), band_2_rect.y(), band_2_rect.width(), band_2_rect.height()), (50.0, 72.0, 22.0, 17.0))
        self.assertNotEqual(band_1_rect.y(), band_2_rect.y())
        self.assertNotEqual(band_1_rect.height(), band_2_rect.height())
        self.assertEqual(detections[0]["bands"][0]["row_index"], 1)
        self.assertEqual(detections[1]["bands"][0]["row_index"], 2)

    def test_group_auto_detected_rows_handles_missing_bands_without_reindexing_per_lane(self) -> None:
        detections = [
            {
                "lane_index": 1,
                "lane_rect": QRectF(0, 0, 20, 100),
                "bands": [
                    {"band_index": 1, "band_rect": QRectF(0, 18, 20, 10)},
                    {"band_index": 2, "band_rect": QRectF(0, 58, 20, 12)},
                ],
            },
            {
                "lane_index": 2,
                "lane_rect": QRectF(24, 0, 20, 100),
                "bands": [
                    {"band_index": 1, "band_rect": QRectF(24, 20, 20, 9)},
                ],
            },
            {
                "lane_index": 3,
                "lane_rect": QRectF(48, 0, 20, 100),
                "bands": [
                    {"band_index": 1, "band_rect": QRectF(48, 56, 20, 11)},
                ],
            },
        ]

        grouped = group_auto_detected_rows(detections)

        self.assertEqual([band["row_index"] for band in grouped[0]["bands"]], [1, 2])
        self.assertEqual([band["row_index"] for band in grouped[1]["bands"]], [1])
        self.assertEqual([band["row_index"] for band in grouped[2]["bands"]], [2])

    def test_group_auto_detected_rows_merges_split_rows_and_collapses_lane_duplicates(self) -> None:
        detections = [
            {
                "lane_index": 1,
                "lane_rect": QRectF(0, 0, 20, 220),
                "bands": [
                    {"band_index": 1, "band_rect": QRectF(0, 18, 20, 12)},
                    {"band_index": 2, "band_rect": QRectF(0, 34, 20, 30)},
                    {"band_index": 3, "band_rect": QRectF(0, 114, 20, 30)},
                    {"band_index": 4, "band_rect": QRectF(0, 149, 20, 29)},
                ],
            },
            {
                "lane_index": 2,
                "lane_rect": QRectF(24, 0, 20, 220),
                "bands": [
                    {"band_index": 1, "band_rect": QRectF(24, 20, 20, 11)},
                    {"band_index": 2, "band_rect": QRectF(24, 36, 20, 31)},
                    {"band_index": 3, "band_rect": QRectF(24, 114, 20, 30)},
                    {"band_index": 4, "band_rect": QRectF(24, 149, 20, 31)},
                ],
            },
        ]

        grouped = group_auto_detected_rows(detections)

        self.assertEqual([band["row_index"] for band in grouped[0]["bands"]], [1, 2])
        self.assertEqual([band["row_index"] for band in grouped[1]["bands"]], [1, 2])
        self.assertEqual(tuple(grouped[0]["bands"][0]["band_rect"].getRect()), (0.0, 18.0, 20.0, 46.0))
        self.assertEqual(tuple(grouped[0]["bands"][1]["band_rect"].getRect()), (0.0, 114.0, 20.0, 64.0))

    def test_group_auto_detected_rows_keeps_well_separated_rows_distinct(self) -> None:
        detections = [
            {
                "lane_index": 1,
                "lane_rect": QRectF(0, 0, 20, 260),
                "bands": [
                    {"band_index": 1, "band_rect": QRectF(0, 70, 20, 54)},
                    {"band_index": 2, "band_rect": QRectF(0, 146, 20, 62)},
                    {"band_index": 3, "band_rect": QRectF(0, 231, 20, 57)},
                    {"band_index": 4, "band_rect": QRectF(0, 314, 20, 53)},
                ],
            },
            {
                "lane_index": 2,
                "lane_rect": QRectF(24, 0, 20, 260),
                "bands": [
                    {"band_index": 1, "band_rect": QRectF(24, 71, 20, 56)},
                    {"band_index": 2, "band_rect": QRectF(24, 148, 20, 61)},
                    {"band_index": 3, "band_rect": QRectF(24, 233, 20, 57)},
                    {"band_index": 4, "band_rect": QRectF(24, 317, 20, 54)},
                ],
            },
        ]

        grouped = group_auto_detected_rows(detections)

        self.assertEqual([band["row_index"] for band in grouped[0]["bands"]], [1, 2, 3, 4])
        self.assertEqual([band["row_index"] for band in grouped[1]["bands"]], [1, 2, 3, 4])

    def test_group_auto_detected_rows_target_row_selection_keeps_global_row_numbers(self) -> None:
        detections = [
            {
                "lane_index": 1,
                "lane_rect": QRectF(0, 0, 20, 160),
                "bands": [
                    {"band_index": 1, "band_rect": QRectF(0, 18, 20, 10)},
                    {"band_index": 2, "band_rect": QRectF(0, 58, 20, 12)},
                ],
            },
            {
                "lane_index": 2,
                "lane_rect": QRectF(24, 0, 20, 160),
                "bands": [
                    {"band_index": 1, "band_rect": QRectF(24, 56, 20, 11)},
                ],
            },
            {
                "lane_index": 3,
                "lane_rect": QRectF(48, 0, 20, 160),
                "bands": [
                    {"band_index": 1, "band_rect": QRectF(48, 20, 20, 9)},
                ],
            },
        ]

        grouped = group_auto_detected_rows(detections, target_band_row=2)

        self.assertEqual([band["row_index"] for band in grouped[0]["bands"]], [2])
        self.assertEqual([band["row_index"] for band in grouped[1]["bands"]], [2])
        self.assertEqual(grouped[2]["bands"], [])

    def test_group_auto_detected_rows_negative_control_lane_does_not_shift_row_numbering(self) -> None:
        detections = [
            {
                "lane_index": 1,
                "lane_rect": QRectF(0, 0, 20, 180),
                "bands": [
                    {"band_index": 1, "band_rect": QRectF(0, 18, 20, 10)},
                    {"band_index": 2, "band_rect": QRectF(0, 58, 20, 10)},
                ],
            },
            {
                "lane_index": 2,
                "lane_rect": QRectF(24, 0, 20, 180),
                "bands": [],
            },
            {
                "lane_index": 3,
                "lane_rect": QRectF(48, 0, 20, 180),
                "bands": [
                    {"band_index": 1, "band_rect": QRectF(48, 56, 20, 11)},
                ],
            },
        ]

        grouped = group_auto_detected_rows(detections)

        self.assertEqual([band["row_index"] for band in grouped[0]["bands"]], [1, 2])
        self.assertEqual(grouped[1]["bands"], [])
        self.assertEqual([band["row_index"] for band in grouped[2]["bands"]], [2])

    def test_auto_detect_offsets_results_back_to_full_image_when_search_roi_is_used(self) -> None:
        lane_candidates = [
            {
                "peak": 14,
                "x_range": (6, 24),
                "lane_rect": QRectF(6, 0, 18, 40),
            },
        ]
        lane_bands = [
            {
                "band_index": 1,
                "band_rect": QRectF(6, 12, 18, 8),
            },
        ]

        with (
            patch("core.band_detector._load_8bit", return_value=np.zeros((160, 120), dtype=np.uint8)),
            patch("core.band_detector._prepare_signal_for_auto_detection", return_value=np.zeros((60, 50), dtype=np.float64)),
            patch(
                "core.band_detector._find_band_rich_horizontal_zone",
                return_value=((10, 44), {"failure_stage": None, "message": "", "selected_peaks": [20], "peak_widths": [10.0]}),
            ),
            patch(
                "core.band_detector._detect_lanes_within_zone",
                return_value=(lane_candidates, {"failure_stage": None, "message": "", "lane_candidates": 1, "kept_lanes": 1}),
            ),
            patch(
                "core.band_detector._detect_bands_in_lane",
                return_value=(lane_bands, {"failure_stage": None, "message": "", "band_peaks": 1}),
            ),
        ):
            detections, metadata = auto_detect_guided(
                "synthetic.png",
                search_rect=QRectF(40, 50, 50, 60),
                return_metadata=True,
            )

        self.assertEqual(metadata["search_region"], (40, 50, 50, 60))
        self.assertEqual(len(detections), 1)
        self.assertEqual(tuple(detections[0]["lane_rect"].getRect()), (46.0, 50.0, 18.0, 60.0))
        self.assertEqual(tuple(detections[0]["bands"][0]["band_rect"].getRect()), (46.0, 62.0, 18.0, 8.0))

    def test_guided_auto_detect_groups_rows_without_harmonizing_band_rectangles(self) -> None:
        lane_candidates = [
            {
                "peak": 20,
                "x_range": (10, 30),
                "lane_rect": QRectF(10, 24, 20, 60),
            },
            {
                "peak": 60,
                "x_range": (50, 72),
                "lane_rect": QRectF(50, 24, 22, 60),
            },
        ]
        lane_1_bands = [
            {
                "band_index": 1,
                "band_rect": QRectF(10, 34, 20, 9),
            },
        ]
        lane_2_bands = [
            {
                "band_index": 1,
                "band_rect": QRectF(50, 38, 22, 17),
            },
        ]

        with (
            patch("core.band_detector._load_8bit", return_value=np.zeros((120, 100), dtype=np.uint8)),
            patch("core.band_detector._prepare_signal_for_auto_detection", return_value=np.zeros((120, 100), dtype=np.float64)),
            patch(
                "core.band_detector._detect_global_band_rows",
                return_value=[(32, 58)],
            ),
            patch(
                "core.band_detector._find_band_rich_horizontal_zone",
                return_value=((24, 84), {"failure_stage": None, "message": "", "selected_peaks": [40], "peak_widths": [18.0]}),
            ),
            patch(
                "core.band_detector._detect_lanes_within_zone",
                return_value=(lane_candidates, {"failure_stage": None, "message": "", "lane_candidates": 2, "kept_lanes": 2}),
            ),
            patch(
                "core.band_detector._detect_bands_in_lane",
                side_effect=[
                    (lane_1_bands, {"failure_stage": None, "message": "", "band_peaks": 1}),
                    (lane_2_bands, {"failure_stage": None, "message": "", "band_peaks": 1}),
                ],
            ),
        ):
            detections = auto_detect_guided("synthetic.png", target_band_row=1, return_metadata=False)

        self.assertEqual(len(detections), 2)
        self.assertEqual([band["row_index"] for band in detections[0]["bands"]], [1])
        self.assertEqual([band["row_index"] for band in detections[1]["bands"]], [1])

        band_1_rect = detections[0]["bands"][0]["band_rect"]
        band_2_rect = detections[1]["bands"][0]["band_rect"]
        self.assertEqual((band_1_rect.x(), band_1_rect.y(), band_1_rect.width(), band_1_rect.height()), (10.0, 34.0, 20.0, 9.0))
        self.assertEqual((band_2_rect.x(), band_2_rect.y(), band_2_rect.width(), band_2_rect.height()), (50.0, 38.0, 22.0, 17.0))
        self.assertNotEqual((band_1_rect.y(), band_1_rect.height()), (band_2_rect.y(), band_2_rect.height()))

    def test_soft_lane_constraint_merges_weakly_separated_centers(self) -> None:
        x_profile = np.zeros(140, dtype=np.float64)
        x_profile[20] = 10.0
        x_profile[42] = 9.0
        x_profile[70] = 8.0
        x_profile[100] = 7.5
        x_profile[31] = 8.0
        x_profile[56] = 1.0
        x_profile[85] = 0.5

        params = _build_auto_params(120, 140, 0.5)
        constrained = _soft_constrain_lane_centers([20, 42, 70, 100], 3, x_profile, params)

        self.assertEqual(len(constrained), 3)
        self.assertTrue(any(24 <= center <= 38 for center in constrained))
        self.assertIn(70, constrained)
        self.assertIn(100, constrained)

    def test_main_window_keeps_existing_target_row_assignments(self) -> None:
        window = MainWindow()
        try:
            window.param_panel._set_mode("auto")
            window.param_panel._auto_target_row.setValue(2)
            detections = [
                {
                    "lane_index": 1,
                    "lane_rect": QRectF(0, 0, 20, 100),
                    "bands": [
                        {
                            "band_index": 1,
                            "row_index": 2,
                            "band_rect": QRectF(0, 40, 20, 12),
                        },
                    ],
                },
                {
                    "lane_index": 2,
                    "lane_rect": QRectF(24, 0, 20, 100),
                    "bands": [
                        {
                            "band_index": 1,
                            "row_index": 2,
                            "band_rect": QRectF(24, 42, 20, 12),
                        },
                    ],
                },
            ]

            normalized = window._normalize_auto_detections(detections)
        finally:
            window.close()

        self.assertEqual([band["row_index"] for band in normalized[0]["bands"]], [2])
        self.assertEqual([band["row_index"] for band in normalized[1]["bands"]], [2])

    def test_main_window_uses_default_auto_path_without_guided_constraints(self) -> None:
        window = MainWindow()
        detections = [
            {
                "lane_index": 1,
                "lane_rect": QRectF(0, 0, 20, 100),
                "bands": [{"band_index": 1, "row_index": 1, "band_rect": QRectF(0, 20, 20, 10)}],
            },
        ]
        try:
            window.param_panel._set_mode("auto")
            window._image_path = "synthetic.png"
            with (
                patch.object(window.canvas, "get_roi", return_value=None),
                patch("core.band_detector.auto_detect_all", return_value=(detections, {"failure_stage": None, "message": ""})) as default_mock,
                patch("core.band_detector.auto_detect_guided") as guided_mock,
            ):
                window._auto_detect()
        finally:
            window.close()

        default_mock.assert_called_once()
        guided_mock.assert_not_called()
        self.assertFalse(default_mock.call_args.kwargs["dark_on_light"])

    def test_main_window_uses_guided_auto_path_when_constraints_are_present(self) -> None:
        window = MainWindow()
        detections = [
            {
                "lane_index": 1,
                "lane_rect": QRectF(0, 0, 20, 100),
                "bands": [{"band_index": 1, "row_index": 2, "band_rect": QRectF(0, 40, 20, 12)}],
            },
        ]
        try:
            window.param_panel._set_mode("auto")
            window.param_panel._auto_target_row.setValue(2)
            window._image_path = "synthetic.png"
            with (
                patch.object(window.canvas, "get_roi", return_value=None),
                patch("core.band_detector.auto_detect_all") as default_mock,
                patch("core.band_detector.auto_detect_guided", return_value=(detections, {"failure_stage": None, "message": ""})) as guided_mock,
            ):
                window._auto_detect()
        finally:
            window.close()

        guided_mock.assert_called_once()
        default_mock.assert_not_called()
        self.assertEqual(guided_mock.call_args.kwargs["target_band_row"], 2)
        self.assertFalse(guided_mock.call_args.kwargs["dark_on_light"])

    def test_auto_param_panel_returns_none_for_empty_guided_fields(self) -> None:
        panel = ParamPanel()
        try:
            panel._set_mode("auto")
            params = panel.get_params()
        finally:
            panel.close()

        self.assertIsNone(params["auto_lane_count"])
        self.assertIsNone(params["expected_rows_per_lane"])
        self.assertIsNone(params["target_band_row"])
        self.assertEqual(params["polarity"], "Light on Dark")
        self.assertEqual(panel.polarity.text(), "Light on Dark")
        self.assertEqual(panel._auto_polarity.text(), "Light on Dark")

    def test_auto_param_panel_backspace_clears_field_and_allows_retyping(self) -> None:
        panel = ParamPanel()
        try:
            panel._set_mode("auto")
            panel._auto_lane_count.setValue(6)
            editor = panel._auto_lane_count.lineEdit()
            self.assertIsNotNone(editor)
            assert editor is not None
            editor.setFocus()
            QTest.keyClick(editor, Qt.Key.Key_Backspace)
            QApplication.processEvents()
            self.assertIsNone(panel.get_params()["auto_lane_count"])

            editor.setText("8")
            panel._auto_lane_count.interpretText()
            QApplication.processEvents()
            self.assertEqual(panel.get_params()["auto_lane_count"], 8)
        finally:
            panel.close()

    def test_manual_param_panel_saves_and_selects_fixed_roi_size(self) -> None:
        panel = ParamPanel()
        selected: list[dict] = []
        canceled: list[bool] = []
        try:
            panel.set_fixed_roi_request_handler(
                lambda: {
                    "kind": "lane",
                    "lane_size": QSizeF(42.0, 18.0),
                    "lane_size_norm": QSizeF(0.42, 0.18),
                }
            )
            panel.set_fixed_roi_size_selected_handler(lambda profile: selected.append(dict(profile)))
            panel.set_fixed_roi_cancel_handler(lambda: canceled.append(True))

            panel._on_add_fixed_roi_clicked()

            self.assertEqual(panel._fixed_roi_list.count(), 1)
            self.assertEqual(panel._fixed_roi_profiles[0]["name"], "Fixed Lane ROI 1")
            self.assertEqual(panel._fixed_roi_profiles[0]["kind"], "lane")
            self.assertAlmostEqual(selected[-1]["lane_size"].width(), 42.0)
            self.assertAlmostEqual(selected[-1]["lane_size"].height(), 18.0)

            panel.set_fixed_roi_request_handler(
                lambda: {
                    "kind": "lane_band",
                    "lane_size": QSizeF(50.0, 20.0),
                    "lane_size_norm": QSizeF(0.5, 0.2),
                    "band_relative": QRectF(0.0, 0.4, 1.0, 0.25),
                }
            )
            panel._on_add_fixed_roi_clicked()

            self.assertEqual(panel._fixed_roi_list.count(), 2)
            self.assertEqual(panel._fixed_roi_profiles[1]["name"], "Fixed lane & band ROI 1")
            self.assertEqual(selected[-1]["kind"], "lane_band")

            panel._on_cancel_fixed_roi_clicked()

            self.assertEqual(canceled, [True])
        finally:
            panel.close()

    def test_image_canvas_fixed_lane_band_profile_places_band_relative_to_lane(self) -> None:
        canvas = ImageCanvas()
        try:
            pixmap = QPixmap(200, 100)
            pixmap.fill(QColor("white"))
            canvas._pixmap_item = canvas._scene.addPixmap(pixmap)
            canvas._pixmap_original_size = QSizeF(200.0, 100.0)
            canvas._scene.setSceneRect(canvas._pixmap_item.boundingRect())
            canvas.set_fixed_roi_profile(
                QSizeF(100.0, 50.0),
                band_relative=QRectF(0.1, 0.4, 0.8, 0.2),
                enabled=True,
            )
            canvas._set_fixed_roi_top_left(QPointF(20.0, 10.0))

            self.assertEqual(canvas.get_roi(), QRectF(20.0, 10.0, 100.0, 50.0))
            self.assertEqual(canvas.get_band_roi(), QRectF(30.0, 30.0, 80.0, 10.0))
        finally:
            canvas.close()

    def test_image_canvas_can_lock_fixed_lane_roi_before_drawing_band_roi(self) -> None:
        canvas = ImageCanvas()
        try:
            pixmap = QPixmap(200, 100)
            pixmap.fill(QColor("white"))
            canvas._pixmap_item = canvas._scene.addPixmap(pixmap)
            canvas._pixmap_original_size = QSizeF(200.0, 100.0)
            canvas._scene.setSceneRect(canvas._pixmap_item.boundingRect())
            canvas.set_fixed_roi_profile(QSizeF(80.0, 30.0), enabled=True)
            canvas._set_fixed_roi_top_left(QPointF(20.0, 10.0))

            self.assertTrue(canvas.finish_fixed_lane_roi_placement())

            self.assertEqual(canvas.get_roi(), QRectF(20.0, 10.0, 80.0, 30.0))
            self.assertIsNone(canvas.get_band_roi())
            self.assertFalse(canvas.finish_fixed_lane_roi_placement())
        finally:
            canvas.close()

    def test_image_canvas_fixed_roi_viewport_size_is_visual_not_image_scaled(self) -> None:
        small_view = ImageCanvas()
        large_view = ImageCanvas()
        try:
            for canvas, scale in ((small_view, 0.5), (large_view, 2.0)):
                pixmap = QPixmap(200, 100)
                pixmap.fill(QColor("white"))
                canvas._pixmap_item = canvas._scene.addPixmap(pixmap)
                canvas._pixmap_original_size = QSizeF(200.0, 100.0)
                canvas._scene.setSceneRect(canvas._pixmap_item.boundingRect())
                canvas.scale(scale, scale)
                canvas.set_fixed_roi_viewport_size(QSizeF(40.0, 20.0), enabled=True)
                canvas._set_fixed_roi_top_left(QPointF(0.0, 0.0))

            self.assertEqual(small_view.get_roi(), QRectF(0.0, 0.0, 80.0, 40.0))
            self.assertEqual(large_view.get_roi(), QRectF(0.0, 0.0, 20.0, 10.0))
            self.assertEqual(small_view.get_fixed_roi_viewport_size(), QSizeF(40.0, 20.0))
            self.assertEqual(large_view.get_fixed_roi_viewport_size(), QSizeF(40.0, 20.0))
        finally:
            small_view.close()
            large_view.close()

    def test_column_table_reset_rebuilds_current_table(self) -> None:
        window = ColumnTableWindow(samples=2, replicates=2)
        try:
            item = QTableWidgetItem("12.5")
            window._table.setItem(0, 2, item)
            window._negative_control_group_index = 1
            window._active_target_row = 0
            window._reset_to_column_table(samples=3, replicates=1)

            self.assertEqual(window._samples, 3)
            self.assertEqual(window._replicates, 1)
            self.assertEqual(window._table.rowCount(), 2)
            self.assertEqual(window._table.columnCount(), 5)
            self.assertEqual(window._group_names, ["Group A", "Group B", "Group C"])
            self.assertIsNone(window._negative_control_group_index)
            self.assertIsNone(window._active_target_row)
            self.assertEqual(window._table.item(0, 2).text(), "")
        finally:
            window.close()

    def test_column_table_export_includes_figure_calculation_sheets(self) -> None:
        window = ColumnTableWindow(samples=2, replicates=2)
        try:
            values = {
                (0, 2): "10",
                (1, 2): "2",
                (2, 2): "12",
                (3, 2): "3",
                (0, 3): "18",
                (1, 3): "3",
                (2, 3): "20",
                (3, 3): "4",
            }
            for (row, col), text in values.items():
                window._table.item(row, col).setText(text)
            window._negative_control_group_index = 0

            with tempfile.TemporaryDirectory() as tmpdir:
                export_path = Path(tmpdir) / "column_table.xlsx"
                window.export_table_xlsx(export_path)

                raw_df = pd.read_excel(export_path, sheet_name="Column Table")
                detail_df = pd.read_excel(export_path, sheet_name="Figure Calculations")
                summary_df = pd.read_excel(export_path, sheet_name="Figure Summary")

            self.assertEqual(
                raw_df[["Replicate", "Band Type", "Group A", "Group B"]].to_dict("records"),
                [
                    {"Replicate": "Replicate 1", "Band Type": "Target band", "Group A": 10, "Group B": 18},
                    {"Replicate": "Replicate 1", "Band Type": "Loading control", "Group A": 2, "Group B": 3},
                    {"Replicate": "Replicate 2", "Band Type": "Target band", "Group A": 12, "Group B": 20},
                    {"Replicate": "Replicate 2", "Band Type": "Loading control", "Group A": 3, "Group B": 4},
                ],
            )
            self.assertEqual(detail_df["Group"].tolist(), ["Group A", "Group A", "Group B", "Group B"])
            self.assertEqual(detail_df["Replicate"].tolist(), [1, 2, 1, 2])
            self.assertTrue(np.allclose(detail_df["Target/Loading Ratio"], [5.0, 4.0, 6.0, 5.0]))
            self.assertTrue(np.allclose(detail_df["Normalized Value"], [10 / 9, 8 / 9, 4 / 3, 10 / 9]))
            self.assertTrue(np.allclose(detail_df["Baseline Mean (Target/Loading)"], [4.5, 4.5, 4.5, 4.5]))
            self.assertEqual(detail_df["Negative Control Group"].tolist(), ["Group A"] * 4)

            self.assertEqual(summary_df["Group"].tolist(), ["Group A", "Group B"])
            self.assertTrue(np.allclose(summary_df["Normalized Mean"], [1.0, 11 / 9]))
            self.assertTrue(
                np.allclose(
                    summary_df["Normalized SD"],
                    [np.std([10 / 9, 8 / 9], ddof=1), np.std([4 / 3, 10 / 9], ddof=1)],
                )
            )
            self.assertEqual(summary_df["Valid Replicates"].tolist(), [2, 2])
            self.assertEqual(summary_df["Negative Control Group"].tolist(), ["Group A", "Group A"])
            self.assertTrue(np.allclose(summary_df["Baseline Mean (Target/Loading)"], [4.5, 4.5]))
        finally:
            window.close()

    def test_main_window_passes_guided_parameters(self) -> None:
        window = MainWindow()
        detections = [
            {
                "lane_index": 1,
                "lane_rect": QRectF(0, 0, 20, 100),
                "bands": [{"band_index": 1, "row_index": 3, "band_rect": QRectF(0, 40, 20, 12)}],
            },
        ]
        try:
            window.param_panel._set_mode("auto")
            window.param_panel._auto_lane_count.setValue(6)
            window.param_panel._auto_expected_rows.setValue(4)
            window.param_panel._auto_target_row.setValue(3)
            window._image_path = "synthetic.png"
            with (
                patch.object(window.canvas, "get_roi", return_value=None),
                patch("core.band_detector.auto_detect_all") as default_mock,
                patch("core.band_detector.auto_detect_guided", return_value=(detections, {"failure_stage": None, "message": ""})) as guided_mock,
            ):
                window._auto_detect()
        finally:
            window.close()

        guided_mock.assert_called_once()
        self.assertEqual(guided_mock.call_args.kwargs["expected_lane_count"], 6)
        self.assertEqual(guided_mock.call_args.kwargs["expected_rows_per_lane"], 4)
        self.assertEqual(guided_mock.call_args.kwargs["target_band_row"], 3)
        self.assertFalse(guided_mock.call_args.kwargs["dark_on_light"])
        default_mock.assert_not_called()

    def test_main_window_uses_guided_auto_path_when_search_roi_is_present(self) -> None:
        window = MainWindow()
        detections = [
            {
                "lane_index": 1,
                "lane_rect": QRectF(40, 50, 20, 100),
                "bands": [{"band_index": 1, "row_index": 1, "band_rect": QRectF(40, 70, 20, 10)}],
            },
        ]
        search_rect = QRectF(40, 50, 120, 140)
        try:
            window.param_panel._set_mode("auto")
            window._image_path = "synthetic.png"
            with (
                patch.object(window.canvas, "get_roi", return_value=search_rect),
                patch("core.band_detector.auto_detect_all") as default_mock,
                patch("core.band_detector.auto_detect_guided", return_value=(detections, {"failure_stage": None, "message": ""})) as guided_mock,
            ):
                window._auto_detect()
        finally:
            window.close()

        guided_mock.assert_called_once()
        self.assertEqual(guided_mock.call_args.kwargs["search_rect"], search_rect)
        self.assertFalse(guided_mock.call_args.kwargs["dark_on_light"])
        default_mock.assert_not_called()

    def test_guided_oversplit_merge_is_stronger_than_default(self) -> None:
        params = _build_auto_params(120, 120, 0.5)
        zone = (20, 90)
        x_profile = np.zeros(120, dtype=np.float64)
        x_profile[18] = 8.5
        x_profile[26] = 8.2
        x_profile[58] = 8.8
        x_profile[22] = 4.2  # weak valley between split peaks

        lanes = [
            {"peak": 18, "score": 8.5, "x_range": (12, 22), "lane_rect": QRectF(12, 20, 10, 70)},
            {"peak": 26, "score": 8.2, "x_range": (22, 32), "lane_rect": QRectF(22, 20, 10, 70)},
            {"peak": 58, "score": 8.8, "x_range": (50, 66), "lane_rect": QRectF(50, 20, 16, 70)},
        ]

        default_merged = _merge_oversplit_lanes(lanes, x_profile, zone, params, guided_mode=False)
        guided_merged = _merge_oversplit_lanes(lanes, x_profile, zone, params, guided_mode=True)

        self.assertEqual(len(default_merged), 3)
        self.assertEqual(len(guided_merged), 2)


if __name__ == "__main__":
    unittest.main()
