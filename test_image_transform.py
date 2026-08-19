from __future__ import annotations

import unittest

import numpy as np

from core.image_transform import (
    GeometryTransform,
    ImageTransformParams,
    apply_geometry_to_display,
    auto_scale_range_16,
    border_median_fill_value,
    default_inverted_for_pil_image,
    geometry_transform_from_dict,
    geometry_transform_to_dict,
    image_array_to_uint16_luminance,
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

    def test_default_full_range_fast_path_matches_scalar_transform(self) -> None:
        pixels = np.arange(65536, dtype=np.uint16)

        for inverted in (False, True):
            params = ImageTransformParams(inverted=inverted)
            expected = np.array(
                [transform_pixel_16_to_8(int(value), params) for value in pixels],
                dtype=np.uint8,
            )
            actual = transform_pixels_16_to_8(pixels, params)

            np.testing.assert_array_equal(actual, expected)

    def test_identity_geometry_reuses_new_display_buffer(self) -> None:
        pixels = np.arange(12, dtype=np.uint8).reshape(3, 4)

        rendered = apply_geometry_to_display(pixels, GeometryTransform())

        self.assertIs(rendered, pixels)

    def test_extended_tone_range_supports_adjustment_beyond_both_source_ends(self) -> None:
        pixels = np.array([0, 65535], dtype=np.uint16)
        params = ImageTransformParams(low=-65535, high=131070, gamma=1.0)

        self.assertEqual(params.sanitized(), params)
        self.assertEqual(transform_pixels_16_to_8(pixels, params).tolist(), [85, 170])

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

    def test_geometry_transform_round_trips_points_and_serializes(self) -> None:
        transform = GeometryTransform(rotation=-17.25, flip_x=True)
        points = np.array([[0.0, 0.0], [120.0, 0.0], [30.0, 50.0]])
        mapped = transform.map_points_to_canvas(points, 120, 80)
        restored = transform.map_points_to_raw(mapped, 120, 80)

        np.testing.assert_allclose(restored, points, atol=1e-9)
        self.assertEqual(
            geometry_transform_from_dict(geometry_transform_to_dict(transform)),
            transform,
        )

    def test_geometry_flip_changes_preview_without_changing_source(self) -> None:
        pixels = np.array([[1, 2, 3], [4, 5, 6]], dtype=np.uint8)
        original = pixels.copy()

        vertical = apply_geometry_to_display(
            pixels, GeometryTransform(flip_y=True)
        )
        horizontal = apply_geometry_to_display(
            pixels, GeometryTransform(flip_x=True)
        )

        self.assertEqual(vertical.tolist(), [[4, 5, 6], [1, 2, 3]])
        self.assertEqual(horizontal.tolist(), [[3, 2, 1], [6, 5, 4]])
        np.testing.assert_array_equal(pixels, original)

    def test_arbitrary_rotation_expands_preview_without_mutating_source(self) -> None:
        pixels = np.full((20, 30), 240, dtype=np.uint8)
        pixels[8:12, 5:25] = 10
        original = pixels.copy()

        rotated = apply_geometry_to_display(
            pixels, GeometryTransform(rotation=13.0)
        )

        self.assertGreater(rotated.shape[0], pixels.shape[0])
        self.assertGreater(rotated.shape[1], pixels.shape[1])
        np.testing.assert_array_equal(pixels, original)

    def test_right_angle_rotation_has_exact_pixel_alignment(self) -> None:
        pixels = np.arange(6, dtype=np.uint8).reshape(2, 3)

        rotated = apply_geometry_to_display(
            pixels, GeometryTransform(rotation=90.0)
        )

        self.assertEqual(rotated.tolist(), [[3, 0], [4, 1], [5, 2]])


if __name__ == "__main__":
    unittest.main()
