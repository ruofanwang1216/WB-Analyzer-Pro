from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np
from PIL import Image

from core.image_transform import (
    ImageTransformParams,
    auto_scale_range_16,
    border_median_fill_value,
    default_inverted_for_pil_image,
    flip_display_pixels_to_file,
    image_array_to_uint16_luminance,
    rotate_display_pixels_to_file,
    transform_pixel_16_to_8,
    transform_pixels_16_to_8,
)


class ImageTransformTests(unittest.TestCase):
    def test_linear_stretch_clamps_and_maps_midpoint(self) -> None:
        params = ImageTransformParams(low=100, high=1100, gamma=1.0)

        self.assertEqual(transform_pixel_16_to_8(0, params), 0)
        self.assertEqual(transform_pixel_16_to_8(100, params), 0)
        self.assertEqual(transform_pixel_16_to_8(600, params), 128)
        self.assertEqual(transform_pixel_16_to_8(1100, params), 255)
        self.assertEqual(transform_pixel_16_to_8(2000, params), 255)

    def test_gamma_is_applied_after_linear_stretch(self) -> None:
        pixels = np.array([100, 600, 1100], dtype=np.uint16)
        params = ImageTransformParams(low=100, high=1100, gamma=2.0)

        self.assertEqual(transform_pixels_16_to_8(pixels, params).tolist(), [0, 64, 255])

    def test_invert_display_flips_stretched_output(self) -> None:
        pixels = np.array([100, 600, 1100], dtype=np.uint16)
        params = ImageTransformParams(low=100, high=1100, gamma=1.0, inverted=True)

        self.assertEqual(transform_pixels_16_to_8(pixels, params).tolist(), [255, 128, 0])

    def test_auto_scale_trims_low_and_high_extremes(self) -> None:
        pixels = np.array([0] * 10 + list(range(100, 200)) + [65535] * 10, dtype=np.uint16)

        low, high = auto_scale_range_16(pixels, trim_fraction=0.1)

        self.assertEqual((low, high), (102, 197))

    def test_8bit_luminance_expands_to_16bit_slider_range(self) -> None:
        arr = np.array([[0, 128, 255]], dtype=np.uint8)

        expanded = image_array_to_uint16_luminance(arr)

        self.assertEqual(expanded.dtype, np.uint16)
        self.assertEqual(expanded.tolist(), [[0, 32896, 65535]])

    def test_tiff_black_is_zero_defaults_to_non_inverted_display(self) -> None:
        class FakeImage:
            tag_v2 = {262: 1}

        self.assertFalse(default_inverted_for_pil_image(FakeImage()))

    def test_tiff_white_is_zero_defaults_to_inverted_display(self) -> None:
        class FakeImage:
            tag_v2 = {262: 0}

        self.assertTrue(default_inverted_for_pil_image(FakeImage(), fallback=False))

    def test_missing_photometric_uses_fallback_display_polarity(self) -> None:
        class FakeImage:
            tag_v2 = {}

        self.assertFalse(default_inverted_for_pil_image(FakeImage(), fallback=False))
        self.assertTrue(default_inverted_for_pil_image(FakeImage(), fallback=True))

    def test_tiff_rgb_photometric_defaults_to_non_inverted_display(self) -> None:
        class FakeImage:
            mode = "RGB"
            tag_v2 = {262: 2}

        self.assertFalse(default_inverted_for_pil_image(FakeImage(), fallback=True))

    def test_rgb_image_without_photometric_defaults_to_non_inverted_display(self) -> None:
        class FakeImage:
            mode = "RGB"
            tag_v2 = {}

        self.assertFalse(default_inverted_for_pil_image(FakeImage(), fallback=True))

    def test_border_median_fill_uses_white_background_for_dark_band_tiff(self) -> None:
        pixels = np.full((20, 30), 60000, dtype=np.uint16)
        pixels[8:12, 6:24] = 1000

        self.assertEqual(border_median_fill_value(pixels), 60000.0)

    def test_border_median_fill_supports_rgb_images(self) -> None:
        pixels = np.zeros((10, 12, 3), dtype=np.uint8)
        pixels[:, :] = [240, 241, 242]
        pixels[4:6, 4:8] = [10, 11, 12]

        self.assertEqual(border_median_fill_value(pixels), (240.0, 241.0, 242.0))

    def test_rotate_display_pixels_saves_visible_polarity_as_black_is_zero(self) -> None:
        pixels = np.full((80, 120), 255, dtype=np.uint8)
        pixels[30:38, 30:95] = 25

        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "rotated.tif"
            transform = rotate_display_pixels_to_file(pixels, path, angle_deg=7.0)

            with Image.open(path) as img:
                rotated = np.array(img)
                self.assertEqual(img.mode, "L")
                self.assertEqual(img.tag_v2.get(262), 1)

        self.assertFalse(transform.inverted)
        self.assertGreaterEqual(int(rotated[0, 0]), 250)
        self.assertLessEqual(int(rotated.min()), 30)

    def test_flip_display_pixels_flips_the_visible_image(self) -> None:
        pixels = np.array([[1, 2, 3], [4, 5, 6]], dtype=np.uint8)

        with TemporaryDirectory() as tmpdir:
            vertical_path = Path(tmpdir) / "vertical.tif"
            horizontal_path = Path(tmpdir) / "horizontal.tif"
            flip_display_pixels_to_file(pixels, vertical_path, vertical=True)
            flip_display_pixels_to_file(pixels, horizontal_path, vertical=False)

            with Image.open(vertical_path) as image:
                self.assertEqual(np.array(image).tolist(), [[4, 5, 6], [1, 2, 3]])
            with Image.open(horizontal_path) as image:
                self.assertEqual(np.array(image).tolist(), [[3, 2, 1], [6, 5, 4]])


if __name__ == "__main__":
    unittest.main()
