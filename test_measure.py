from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image
from PySide6.QtCore import QRectF
from PySide6.QtWidgets import QApplication

from core.measure import measure_all_lanes, measure_all_lanes_in_array
from core.image_transform import ImageTransformParams
from gui.image_canvas import ImageCanvas
from gui.main_window import MainWindow


class MeasureAllLanesTransformTests(unittest.TestCase):
    def test_reported_screenshot_values_are_inverted_to_wb_signal(self) -> None:
        area = 280

        def roi_with_sum(total: int) -> np.ndarray:
            base, remainder = divmod(total, area)
            values = np.full(area, base, dtype=np.uint8)
            values[:remainder] += 1
            return values

        raw_brightness_sums = [35957, 40245, 36969]
        gray = np.concatenate(
            [roi_with_sum(total) for total in raw_brightness_sums]
        ).reshape(1, -1)
        pixels = np.repeat(gray[:, :, np.newaxis], 3, axis=2)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "reported-dark-bands.png"
            Image.fromarray(pixels, mode="RGB").save(path)
            results = measure_all_lanes(
                str(path),
                [
                    {"x": index * area, "y": 0, "width": area, "height": 1}
                    for index in range(3)
                ],
            )

        self.assertEqual(
            [row["Mean"] for row in results],
            [126.582, 111.268, 122.968],
        )
        self.assertEqual(
            [row["RawIntDen"] for row in results],
            [35443, 31155, 34431],
        )

    def test_dark_rgb_bands_are_reported_as_positive_signal(self) -> None:
        pixels = np.array(
            [[[20, 20, 20], [200, 200, 200], [40, 40, 40]]],
            dtype=np.uint8,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "dark-bands.png"
            Image.fromarray(pixels, mode="RGB").save(path)
            results = measure_all_lanes(
                str(path),
                [
                    {"x": 0, "y": 0, "width": 1, "height": 1, "lane": 1},
                    {"x": 1, "y": 0, "width": 1, "height": 1, "lane": 2},
                    {"x": 2, "y": 0, "width": 1, "height": 1, "lane": 3},
                ],
            )

        self.assertEqual([row["Mean"] for row in results], [235.0, 55.0, 215.0])
        self.assertGreater(results[0]["IntDen"], results[1]["IntDen"])
        self.assertGreater(results[2]["RawIntDen"], results[1]["RawIntDen"])

    def test_measure_all_lanes_uses_transformed_pixels_when_provided(self) -> None:
        pixels = np.array([[0, 128], [255, 64]], dtype=np.uint8)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sample.png"
            Image.fromarray(pixels, mode="L").save(path)

            legacy = measure_all_lanes(
                str(path),
                [{"x": 0, "y": 0, "width": 2, "height": 2, "lane": 1}],
            )
            transformed = measure_all_lanes(
                str(path),
                [{"x": 0, "y": 0, "width": 2, "height": 2, "lane": 1}],
                image_transform={
                    "low": 32896,
                    "high": 65535,
                    "gamma": 1.0,
                    "inverted": False,
                },
            )

        self.assertEqual(legacy[0]["RawIntDen"], 447)
        self.assertEqual(transformed[0]["RawIntDen"], 255)
        self.assertEqual(transformed[0]["Mean"], 63.75)


class MainWindowAnalysisTransformTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_image_canvas_reports_when_transform_has_been_modified(self) -> None:
        canvas = ImageCanvas()
        self.assertFalse(canvas.has_modified_image_transform())
        self.assertFalse(canvas.has_quantitative_image_transform())

        params = canvas.get_image_transform_params().__class__(
            low=100,
            high=1000,
            gamma=1.0,
            inverted=True,
        )
        canvas.set_image_transform_params(params)

        self.assertTrue(canvas.has_modified_image_transform())
        self.assertTrue(canvas.has_quantitative_image_transform())

    def test_image_canvas_invert_only_does_not_count_as_quantitative_transform(self) -> None:
        canvas = ImageCanvas()
        params = canvas.get_image_transform_params().__class__(
            low=0,
            high=65535,
            gamma=1.0,
            inverted=False,
        )
        canvas.set_image_transform_params(params)

        self.assertTrue(canvas.has_modified_image_transform())
        self.assertFalse(canvas.has_quantitative_image_transform())

    def test_display_inversion_does_not_change_rgb_signal_pixels(self) -> None:
        pixels = np.array([[[20, 20, 20], [200, 200, 200]]], dtype=np.uint8)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "dark-band.png"
            Image.fromarray(pixels, mode="RGB").save(path)
            canvas = ImageCanvas()
            canvas.load_image(path)
            before = canvas.current_analysis_pixels()

            params = canvas.get_image_transform_params()
            canvas.set_image_transform_params(
                ImageTransformParams(
                    low=params.low,
                    high=params.high,
                    gamma=params.gamma,
                    inverted=not params.inverted,
                )
            )
            after = canvas.current_analysis_pixels()

        np.testing.assert_array_equal(before, np.array([[235, 55]], dtype=np.uint8))
        np.testing.assert_array_equal(after, before)

    def test_run_analysis_passes_current_analysis_pixels_to_worker_when_transform_is_modified(self) -> None:
        with patch("gui.main_window.AppPersistence.update_config", return_value=None):
            window = MainWindow()
        panel_canvas = window._image_panels[0].canvas
        window._slot_states[0]["path"] = "fake-image.tif"
        analysis_pixels = np.array([[10, 20], [30, 40]], dtype=np.uint8)

        with (
            patch.object(window.param_panel, "get_params", return_value={"mode": "manual"}),
            patch.object(panel_canvas, "get_roi", return_value=QRectF(0.0, 0.0, 10.0, 10.0)),
            patch.object(panel_canvas, "get_band_roi", return_value=QRectF(1.0, 1.0, 4.0, 4.0)),
            patch.object(panel_canvas, "has_quantitative_image_transform", return_value=True),
            patch.object(panel_canvas, "current_analysis_pixels", return_value=analysis_pixels),
            patch.object(panel_canvas, "get_image_transform_params", return_value=panel_canvas.get_image_transform_params().__class__(low=100, high=1000, gamma=1.8, inverted=True)),
            patch.object(window, "_construct_band_rois", return_value=[{"x": 1.0, "y": 1.0, "width": 4.0, "height": 4.0}]),
            patch.object(window._persistence, "remember_analysis_debug"),
            patch.object(window, "_start_measurement_worker") as start_worker,
        ):
            window._run_analysis()

        args, kwargs = start_worker.call_args
        self.assertEqual(args[0], "fake-image.tif")
        self.assertEqual(args[1], [{"x": 1.0, "y": 1.0, "width": 4.0, "height": 4.0}])
        np.testing.assert_array_equal(kwargs["image_pixels"], analysis_pixels)
        self.assertEqual(
            kwargs["image_transform"],
            {
                "low": 100,
                "high": 1000,
                "gamma": 1.8,
                "inverted": False,
            },
        )

    def test_rotated_image_reload_preserves_tiff_display_transform(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "rotated-sample.tif"
            pixels = np.array(
                [
                    [60000, 60000, 60000, 60000],
                    [60000, 1000, 1000, 60000],
                    [60000, 1000, 1000, 60000],
                    [60000, 60000, 60000, 60000],
                ],
                dtype=np.uint16,
            )
            Image.fromarray(pixels).save(path)

            canvas = ImageCanvas()
            canvas.set_image_transform_params(ImageTransformParams(inverted=True))
            transform = ImageTransformParams(
                low=0,
                high=65535,
                gamma=1.0,
                inverted=False,
            )
            MainWindow._load_rotated_image_preserving_transform(
                canvas,
                str(path),
                transform,
            )

            self.assertEqual(
                canvas.get_image_transform_params(),
                transform,
            )

    def test_rotated_image_reload_preserves_view_zoom(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source_path = Path(tmpdir) / "source.tif"
            rotated_path = Path(tmpdir) / "rotated.tif"
            Image.fromarray(np.zeros((80, 120), dtype=np.uint16)).save(source_path)
            Image.fromarray(np.zeros((110, 150), dtype=np.uint16)).save(rotated_path)

            canvas = ImageCanvas()
            canvas.resize(640, 480)
            canvas.show()
            canvas.load_image(source_path)
            self._app.processEvents()
            canvas.scale(1.75, 1.75)
            zoom_before = canvas.transform().m11()

            MainWindow._load_rotated_image_preserving_transform(
                canvas,
                str(rotated_path),
                ImageTransformParams(inverted=False),
            )

            self.assertAlmostEqual(canvas.transform().m11(), zoom_before, places=7)
            self.assertAlmostEqual(canvas.transform().m22(), zoom_before, places=7)

    def test_measure_all_lanes_in_array_matches_transformed_measurement_path(self) -> None:
        pixels = np.array([[0, 0], [255, 255]], dtype=np.uint8)
        results = measure_all_lanes_in_array(
            pixels,
            [{"x": 0, "y": 0, "width": 2, "height": 2, "lane": 1}],
        )

        self.assertEqual(results[0]["RawIntDen"], 510)
        self.assertEqual(results[0]["Mean"], 127.5)


if __name__ == "__main__":
    unittest.main()
