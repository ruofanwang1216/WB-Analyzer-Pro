from __future__ import annotations

import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image
from PySide6.QtCore import QRectF
from PySide6.QtWidgets import QApplication

from core.image_transform import GeometryTransform, ImageTransformParams
from core.measure import (
    measure_all_lanes,
    measure_all_lanes_in_array,
    quantify_roi,
)
from gui.image_canvas import ImageCanvas
from gui.figure_mode_window import FigureModeWindow
from gui.main_window import MainWindow
from core.template_engine import TemplateEngine


class NativeDepthQuantificationTests(unittest.TestCase):
    def test_16bit_tiff_metrics_keep_native_values(self) -> None:
        pixels = np.array(
            [[1000, 2000, 3000], [40000, 50000, 60000]],
            dtype=np.uint16,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "native-16bit.tif"
            Image.fromarray(pixels).save(path)
            result = measure_all_lanes(
                str(path),
                [{"x": 0, "y": 0, "width": 3, "height": 2}],
            )[0]

        self.assertEqual(result["Area"], 6)
        self.assertEqual(result["Mean"], 26000.0)
        self.assertEqual(result["Min"], 1000)
        self.assertEqual(result["Max"], 60000)
        self.assertEqual(result["IntDen"], 156000.0)
        self.assertEqual(result["RawIntDen"], 156000)

    def test_display_transform_argument_is_ignored_by_quantification(self) -> None:
        pixels = np.array([[0, 128], [255, 64]], dtype=np.uint8)
        roi = [{"x": 0, "y": 0, "width": 2, "height": 2}]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sample.png"
            Image.fromarray(pixels).save(path)
            baseline = measure_all_lanes(str(path), roi)
            transformed = measure_all_lanes(
                str(path),
                roi,
                image_transform={
                    "low": 32896,
                    "high": 65535,
                    "gamma": 3.5,
                    "inverted": True,
                },
            )
        self.assertEqual(transformed, baseline)

    def test_polygon_mask_does_not_quantify_its_bounding_box(self) -> None:
        pixels = np.zeros((5, 5), dtype=np.uint16)
        pixels[0:4, 0:4] = 1000
        triangle = {
            "points": [
                {"x": 0.0, "y": 0.0},
                {"x": 4.0, "y": 0.0},
                {"x": 0.0, "y": 4.0},
            ]
        }
        result = quantify_roi(pixels, triangle)

        self.assertEqual(result["Area"], 10)
        self.assertEqual(result["RawIntDen"], 10000)
        self.assertLess(result["Area"], 16)

    def test_array_quantification_preserves_metadata(self) -> None:
        pixels = np.array([[10, 20], [30, 40]], dtype=np.uint16)
        result = measure_all_lanes_in_array(
            pixels,
            [{
                "x": 0,
                "y": 0,
                "width": 2,
                "height": 2,
                "lane": 3,
                "band": 2,
                "band_label": "Target",
            }],
        )[0]
        self.assertEqual(result["lane"], 3)
        self.assertEqual(result["band"], "Target")
        self.assertEqual(result["RawIntDen"], 100)

    def test_blot_project_round_trip_preserves_geometry_metadata(self) -> None:
        project = TemplateEngine.build_project("normal_wb", 1, 1, 4)
        expected = {"rotation": -2.14, "flip_x": True, "flip_y": False}
        project.panels[0].blot_slots[0].geometry_transform = dict(expected)

        restored = FigureModeWindow._project_from_blot_file_data(asdict(project))

        self.assertEqual(
            restored.panels[0].blot_slots[0].geometry_transform,
            expected,
        )


class CanvasScientificIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _loaded_canvas(self, pixels: np.ndarray) -> tuple[tempfile.TemporaryDirectory, Path, ImageCanvas]:
        tmpdir = tempfile.TemporaryDirectory()
        path = Path(tmpdir.name) / "source.tif"
        Image.fromarray(pixels).save(path)
        canvas = ImageCanvas()
        canvas.resize(640, 480)
        canvas.load_image_blocking(path)
        return tmpdir, path, canvas

    def test_low_high_gamma_invert_and_zoom_do_not_change_raw_pixels_or_result(self) -> None:
        pixels = (np.arange(120, dtype=np.uint16).reshape(10, 12) * 401)
        tmpdir, path, canvas = self._loaded_canvas(pixels)
        self.addCleanup(tmpdir.cleanup)
        canvas_roi = {"x": 2.0, "y": 3.0, "width": 5.0, "height": 4.0}
        raw_roi_before = canvas.map_canvas_roi_to_raw(canvas_roi)
        baseline = measure_all_lanes(str(path), [raw_roi_before])
        raw_pixels_before = canvas.current_analysis_pixels()

        canvas.set_image_transform_params(
            ImageTransformParams(
                low=7000,
                high=32000,
                gamma=2.7,
                inverted=not canvas.get_image_transform_params().inverted,
            )
        )
        canvas.scale(2.25, 2.25)

        raw_roi_after = canvas.map_canvas_roi_to_raw(canvas_roi)
        after = measure_all_lanes(str(path), [raw_roi_after])
        np.testing.assert_array_equal(canvas.current_analysis_pixels(), raw_pixels_before)
        self.assertEqual(canvas.current_analysis_pixels().dtype, np.uint16)
        self.assertEqual(after, baseline)
        self.assertEqual(raw_roi_after, raw_roi_before)
        self.assertFalse(canvas.has_quantitative_image_transform())

    def test_canvas_keeps_internal_raw_pixels_read_only(self) -> None:
        tmpdir, _path, canvas = self._loaded_canvas(
            np.arange(20, dtype=np.uint16).reshape(4, 5)
        )
        self.addCleanup(tmpdir.cleanup)
        with self.assertRaises(ValueError):
            canvas._raw_quantification_pixels[0, 0] = 999

    def test_large_image_uses_sampled_preview_with_full_resolution_scene_coordinates(self) -> None:
        pixels = np.arange(8 * 5000, dtype=np.uint16).reshape(8, 5000)
        tmpdir, _path, canvas = self._loaded_canvas(pixels)
        self.addCleanup(tmpdir.cleanup)

        self.assertEqual(canvas._display_preview_stride, 2)
        self.assertEqual(canvas._pixmap_item.pixmap().width(), 2500)
        self.assertEqual(canvas.image_scene_size(), canvas.raw_image_size())
        np.testing.assert_array_equal(canvas.current_analysis_pixels(), pixels)

    def test_rotate_and_flip_round_trip_roi_to_same_raw_pixels(self) -> None:
        pixels = np.arange(20 * 30, dtype=np.uint16).reshape(20, 30)
        tmpdir, _path, canvas = self._loaded_canvas(pixels)
        self.addCleanup(tmpdir.cleanup)
        raw_polygon = np.array(
            ((4.0, 5.0), (15.0, 5.0), (15.0, 12.0), (4.0, 12.0)),
            dtype=np.float64,
        )
        baseline = quantify_roi(pixels, {"points": raw_polygon.tolist()})

        for geometry in (
            GeometryTransform(rotation=-17.25),
            GeometryTransform(flip_x=True),
            GeometryTransform(flip_y=True),
            GeometryTransform(rotation=23.5, flip_x=True, flip_y=True),
        ):
            canvas.set_geometry_transform(geometry)
            canvas_polygon = canvas.map_raw_points_to_canvas(raw_polygon)
            inverse_mapped = canvas.map_canvas_roi_to_raw(
                {"points": canvas_polygon.tolist()}
            )
            result = quantify_roi(pixels, inverse_mapped)
            self.assertEqual(result, baseline)
            np.testing.assert_allclose(
                np.array([[p["x"], p["y"]] for p in inverse_mapped["points"]]),
                raw_polygon,
                atol=1e-9,
            )

    def test_arbitrary_rotation_maps_rectangular_canvas_roi_to_polygon(self) -> None:
        pixels = np.arange(20 * 30, dtype=np.uint16).reshape(20, 30)
        tmpdir, _path, canvas = self._loaded_canvas(pixels)
        self.addCleanup(tmpdir.cleanup)
        canvas.set_geometry_transform(GeometryTransform(rotation=-12.4))

        raw_roi = canvas.map_canvas_roi_to_raw(
            {"x": 7.0, "y": 6.0, "width": 9.0, "height": 5.0}
        )
        self.assertEqual(len(raw_roi["points"]), 4)
        xs = {round(point["x"], 6) for point in raw_roi["points"]}
        ys = {round(point["y"], 6) for point in raw_roi["points"]}
        self.assertGreater(len(xs), 2)
        self.assertGreater(len(ys), 2)

    def test_analysis_dispatches_inverse_mapped_raw_rois_only(self) -> None:
        pixels = np.arange(100, dtype=np.uint16).reshape(10, 10)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "source.tif"
            Image.fromarray(pixels).save(path)
            with patch("gui.main_window.AppPersistence.update_config", return_value=None):
                window = MainWindow()
            canvas = window._image_panels[0].canvas
            canvas.load_image_blocking(path)
            canvas.set_geometry_transform(
                GeometryTransform(rotation=-8.0, flip_x=True)
            )
            window._slot_states[0]["path"] = str(path)
            window._active_slot_index = 0
            window._image_path = str(path)
            window.canvas = canvas

            band_rois = [{
                "x": 2.0,
                "y": 2.0,
                "width": 4.0,
                "height": 3.0,
                "lane": 1,
            }]
            expected = canvas.map_canvas_rois_to_raw(band_rois)
            with (
                patch.object(canvas, "get_roi", return_value=QRectF(1, 1, 7, 7)),
                patch.object(canvas, "get_band_roi", return_value=QRectF(2, 2, 4, 3)),
                patch.object(window, "_construct_band_rois", return_value=band_rois),
                patch.object(window._persistence, "remember_analysis_debug"),
                patch.object(window, "_start_measurement_worker") as start_worker,
            ):
                window._run_analysis()

            start_worker.assert_called_once_with(str(path), expected)

    def test_flip_and_undo_keep_original_path_and_create_no_tiff(self) -> None:
        pixels = np.arange(24, dtype=np.uint16).reshape(4, 6)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "immutable-source.tif"
            Image.fromarray(pixels).save(path)
            with patch("gui.main_window.AppPersistence.update_config", return_value=None):
                window = MainWindow()
            canvas = window._image_panels[0].canvas
            canvas.load_image_blocking(path)
            window._slot_states[0]["path"] = str(path)
            window._active_slot_index = 0
            window._image_path = str(path)
            window.canvas = canvas
            cache_before = set(window._conversion_cache_dir.iterdir())

            window._on_flip_image_requested(vertical=False)

            self.assertEqual(window._image_path, str(path))
            self.assertEqual(window._slot_states[0]["path"], str(path))
            self.assertTrue(canvas.get_geometry_transform().flip_x)
            self.assertEqual(set(window._conversion_cache_dir.iterdir()), cache_before)

            window._on_undo_image_operation_requested()

            self.assertEqual(canvas.get_geometry_transform(), GeometryTransform())
            self.assertEqual(window._image_path, str(path))

    def test_custom_rotation_applies_opposite_of_crosshair_angle(self) -> None:
        pixels = np.arange(24, dtype=np.uint16).reshape(4, 6)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "deskew-source.tif"
            Image.fromarray(pixels).save(path)
            with patch("gui.main_window.AppPersistence.update_config", return_value=None):
                window = MainWindow()
            canvas = window._image_panels[0].canvas
            canvas.load_image_blocking(path)
            window._slot_states[0]["path"] = str(path)
            window._active_slot_index = 0
            window._image_path = str(path)
            window.canvas = canvas
            self.assertTrue(canvas.enter_rotation_mode())
            canvas._rotation_angle_deg = 12.5

            window._on_rotate_requested()

            self.assertEqual(
                canvas.get_geometry_transform(),
                GeometryTransform(rotation=-12.5),
            )


if __name__ == "__main__":
    unittest.main()
