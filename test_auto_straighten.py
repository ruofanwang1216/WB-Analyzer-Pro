"""Automatic band-tilt detection and one-click straightening."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage as ndi

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.auto_straighten import (  # noqa: E402
    MIN_USEFUL_TILT_DEG,
    estimate_band_tilt,
    straightened_geometry,
)
from core.image_transform import (  # noqa: E402
    GeometryTransform,
    apply_geometry_to_display,
)


def synthetic_blot(
    tilt_deg: float,
    *,
    height: int = 260,
    width: int = 348,
    noise: float = 0.03,
    seed: int = 0,
    inverted: bool = False,
) -> np.ndarray:
    """Three rows of six bands rotated clockwise by ``tilt_deg`` (16-bit)."""
    rng = np.random.default_rng(seed)
    big_h, big_w = height * 3, width * 3
    yy, xx = np.mgrid[0:big_h, 0:big_w].astype(np.float64)
    yy -= big_h / 2.0
    xx -= big_w / 2.0
    theta = np.radians(tilt_deg)
    along = xx * np.cos(theta) + yy * np.sin(theta)
    across = -xx * np.sin(theta) + yy * np.cos(theta)
    image = np.zeros((big_h, big_w))
    for row_centre in (-150.0, 60.0, 210.0):
        for lane in range(6):
            lane_centre = (lane - 2.5) * 120.0
            strength = 0.4 + 0.6 * rng.random()
            image += (
                strength
                * np.exp(-(((across - row_centre) / 12.0) ** 2))
                * (np.abs(along - lane_centre) < 45.0)
            )
    image = ndi.gaussian_filter(image, 4.0)[::3, ::3]
    image = image + noise * rng.standard_normal(image.shape) + 0.1
    image = np.clip(image, 0.0, 1.2) / 1.2
    if inverted:
        image = 1.0 - image
    return (image * 60000.0).astype(np.uint16)


class EstimateBandTiltTests(unittest.TestCase):
    def test_recovers_known_tilts(self) -> None:
        for tilt in (0.0, 0.3, -0.7, 1.5, -3.2, 5.0, -8.4, 12.0, -17.0, 24.0):
            with self.subTest(tilt=tilt):
                estimate = estimate_band_tilt(synthetic_blot(tilt))
                self.assertTrue(estimate.reliable)
                self.assertAlmostEqual(estimate.angle_deg, tilt, delta=0.15)

    def test_level_blot_needs_no_correction(self) -> None:
        estimate = estimate_band_tilt(synthetic_blot(0.0))
        self.assertLess(abs(estimate.angle_deg), MIN_USEFUL_TILT_DEG)
        self.assertFalse(estimate.needs_correction)

    def test_polarity_bit_depth_and_colour_do_not_matter(self) -> None:
        blot = synthetic_blot(4.0)
        dark_bands = synthetic_blot(4.0, inverted=True)
        eight_bit = (blot >> 8).astype(np.uint8)
        rgb = np.dstack([eight_bit] * 3)
        for name, pixels in (("inverted", dark_bands), ("uint8", eight_bit), ("rgb", rgb)):
            with self.subTest(source=name):
                estimate = estimate_band_tilt(pixels)
                self.assertTrue(estimate.reliable)
                self.assertAlmostEqual(estimate.angle_deg, 4.0, delta=0.2)

    def test_large_image_is_handled_at_reduced_resolution(self) -> None:
        small = synthetic_blot(-2.5)
        large = np.kron(small, np.ones((4, 4), dtype=np.uint16))
        estimate = estimate_band_tilt(large)
        self.assertTrue(estimate.reliable)
        self.assertAlmostEqual(estimate.angle_deg, -2.5, delta=0.2)

    def test_noisy_blot_is_still_measured(self) -> None:
        estimate = estimate_band_tilt(synthetic_blot(3.0, noise=0.25, seed=3))
        self.assertTrue(estimate.reliable)
        self.assertAlmostEqual(estimate.angle_deg, 3.0, delta=0.4)

    def test_images_without_bands_are_reported_unreliable(self) -> None:
        rng = np.random.default_rng(1)
        noise = (rng.random((260, 348)) * 65535).astype(np.uint16)
        self.assertFalse(estimate_band_tilt(noise).reliable)
        self.assertFalse(estimate_band_tilt(np.full((260, 348), 1200, np.uint16)).reliable)
        self.assertFalse(estimate_band_tilt(np.zeros((4, 4), np.uint16)).reliable)

    def test_input_pixels_are_not_modified(self) -> None:
        blot = synthetic_blot(2.0)
        before = blot.copy()
        blot.setflags(write=False)
        estimate_band_tilt(blot)
        np.testing.assert_array_equal(blot, before)


class StraightenedGeometryTests(unittest.TestCase):
    def _residual_tilt(self, raw: np.ndarray, geometry: GeometryTransform) -> float:
        display = (raw >> 8).astype(np.uint8)
        return estimate_band_tilt(apply_geometry_to_display(display, geometry)).angle_deg

    def test_result_is_level_for_every_starting_geometry(self) -> None:
        raw = synthetic_blot(6.0)
        tilt = estimate_band_tilt(raw).angle_deg
        starts = (
            GeometryTransform(),
            GeometryTransform(flip_x=True),
            GeometryTransform(flip_y=True),
            GeometryTransform(flip_x=True, flip_y=True),
            GeometryTransform(rotation=11.0),
            GeometryTransform(rotation=-20.0, flip_x=True),
            GeometryTransform(rotation=180.0),
        )
        for start in starts:
            with self.subTest(start=start):
                fixed = straightened_geometry(start, tilt)
                self.assertEqual((fixed.flip_x, fixed.flip_y), (start.flip_x, start.flip_y))
                self.assertLess(abs(self._residual_tilt(raw, fixed)), 0.3)

    def test_upside_down_image_stays_upside_down(self) -> None:
        fixed = straightened_geometry(GeometryTransform(rotation=180.0), 3.0)
        self.assertAlmostEqual(abs(fixed.rotation), 177.0, delta=1e-6)

    def test_straightening_twice_changes_nothing(self) -> None:
        once = straightened_geometry(GeometryTransform(), 4.2)
        twice = straightened_geometry(once, 4.2)
        self.assertAlmostEqual(once.rotation, -4.2)
        self.assertAlmostEqual(twice.rotation, once.rotation)


class SupersampledGeometryTests(unittest.TestCase):
    def test_finer_grid_covers_the_same_canvas(self) -> None:
        display = (synthetic_blot(0.0) >> 8).astype(np.uint8)
        geometry = GeometryTransform(rotation=-4.0)
        base = apply_geometry_to_display(display, geometry)
        fine = apply_geometry_to_display(display, geometry, scale=3)
        self.assertEqual(fine.shape, (base.shape[0] * 3, base.shape[1] * 3))
        # Averaging the fine render back down must reproduce the 1x render.
        pooled = fine.reshape(base.shape[0], 3, base.shape[1], 3).mean(axis=(1, 3))
        self.assertLess(float(np.mean(np.abs(pooled - base))), 1.5)

    def test_right_angles_are_never_resampled(self) -> None:
        display = (synthetic_blot(0.0) >> 8).astype(np.uint8)
        for rotation in (0.0, 90.0, 180.0, -90.0):
            with self.subTest(rotation=rotation):
                geometry = GeometryTransform(rotation=rotation)
                np.testing.assert_array_equal(
                    apply_geometry_to_display(display, geometry, scale=3),
                    apply_geometry_to_display(display, geometry),
                )


class AutoStraightenButtonTests(unittest.TestCase):
    """The Rotate button straightens in one click and can be undone."""

    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from gui.main_window import MainWindow

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.window = MainWindow()
        self.addCleanup(self.window.close)

    def _load(self, pixels: np.ndarray) -> None:
        path = Path(self._tmp.name) / "blot.tif"
        Image.fromarray(pixels).save(path)
        window = self.window
        window._image_panels[0].canvas.load_image_blocking(path)
        window._slot_states[0]["path"] = str(path)
        window._set_panel_transform_enabled(0, True)
        window._set_active_slot(0)
        self.assertEqual(window._image_path, str(path))

    def test_click_levels_the_image_without_touching_raw_pixels(self) -> None:
        raw = synthetic_blot(5.0)
        self._load(raw)
        panel = self.window._image_panels[0]
        canvas = panel.canvas

        panel.rotate_btn.click()

        self.assertAlmostEqual(canvas.get_geometry_transform().rotation, -5.0, delta=0.2)
        np.testing.assert_array_equal(canvas.current_analysis_pixels(), raw)
        self.assertIn("Auto-straightened by 5.0°", self.window._status_bar.currentMessage())

        # A second click finds nothing left to fix and adds no undo step.
        history = self.window._slot_states[0]["image_operation_history"]
        self.assertEqual(len(history), 1)
        panel.rotate_btn.click()
        self.assertEqual(len(history), 1)
        self.assertIn("already level", self.window._status_bar.currentMessage())

        panel.undo_image_operation_action.trigger()
        self.assertEqual(canvas.get_geometry_transform().rotation, 0.0)

    def test_image_without_bands_is_left_alone(self) -> None:
        rng = np.random.default_rng(2)
        self._load((rng.random((260, 348)) * 65535).astype(np.uint16))
        panel = self.window._image_panels[0]

        panel.auto_straighten_action.trigger()

        self.assertTrue(panel.canvas.get_geometry_transform().is_identity())
        self.assertIn("image left unchanged", self.window._status_bar.currentMessage())
        self.assertNotIn("image_operation_history", {
            key: value for key, value in self.window._slot_states[0].items() if value
        })


if __name__ == "__main__":
    unittest.main()
