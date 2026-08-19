import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from PySide6.QtCore import QRectF

from core.band_detector import load_auto_detection_pixels
from core.band_auto_fit import aspect_fit_placement, calculate_band_auto_fit
from core.figure_project import ImageBBox
from core.image_transform import GeometryTransform, apply_geometry_to_display
from gui.main_window import _run_auto_fit_guided_with_polarity_fallback


def _lane(index: int, x: float, band_y: float, band_h: float = 10.0) -> dict:
    return {
        "lane_index": index,
        "lane_rect": QRectF(x, 20.0, 20.0, 60.0),
        "bands": [
            {
                "band_index": 1,
                "row_index": 1,
                "band_rect": QRectF(x, band_y, 20.0, band_h),
            }
        ],
    }


class BandAutoFitTests(unittest.TestCase):
    def test_aspect_fit_uses_one_scale_and_centers(self) -> None:
        placement = aspect_fit_placement(200, 100, 100, 100)
        self.assertAlmostEqual(placement.scale, 0.5)
        self.assertAlmostEqual(placement.width, 100)
        self.assertAlmostEqual(placement.height, 50)
        self.assertAlmostEqual(placement.x, 0)
        self.assertAlmostEqual(placement.y, 25)
        self.assertAlmostEqual(placement.width / placement.height, 2.0)

    def test_multi_lane_crop_preserves_one_strip_and_margins(self) -> None:
        result = calculate_band_auto_fit(
            [_lane(1, 20, 42), _lane(2, 50, 44), _lane(3, 80, 43)],
            search_roi=QRectF(10, 10, 110, 90),
            image_width=150,
            image_height=120,
            horizontal_margin_px=5,
            vertical_margin_px=6,
            expected_lane_count=3,
        )
        self.assertEqual(result.lane_count, 3)
        self.assertEqual(result.band_count, 3)
        self.assertAlmostEqual(result.crop_box.x, 15)
        self.assertAlmostEqual(result.crop_box.w, 90)
        self.assertLessEqual(result.crop_box.y, 36)
        self.assertGreaterEqual(result.crop_box.y + result.crop_box.h, 60)
        self.assertFalse(result.low_confidence)

    def test_crop_clamps_to_search_and_reports_margin_clipping(self) -> None:
        result = calculate_band_auto_fit(
            [_lane(1, 0, 1)],
            search_roi=QRectF(0, 0, 30, 30),
            image_width=30,
            image_height=30,
            horizontal_margin_px=20,
            vertical_margin_px=20,
            expected_lane_count=1,
        )
        self.assertEqual(result.crop_box.x, 0)
        self.assertEqual(result.crop_box.y, 0)
        self.assertLessEqual(result.crop_box.x + result.crop_box.w, 30)
        self.assertLessEqual(result.crop_box.y + result.crop_box.h, 30)
        self.assertTrue(result.margin_clipped)

    def test_missing_expected_lanes_is_low_confidence(self) -> None:
        result = calculate_band_auto_fit(
            [_lane(1, 20, 42)],
            search_roi=QRectF(0, 0, 120, 100),
            image_width=120,
            image_height=100,
            expected_lane_count=4,
        )
        self.assertTrue(result.low_confidence)
        self.assertLess(result.confidence, 0.55)

    def test_zero_implicit_lane_padding_keeps_requested_horizontal_margin(self) -> None:
        result = calculate_band_auto_fit(
            [_lane(1, 20, 42), _lane(2, 50, 42), _lane(3, 80, 42)],
            search_roi=QRectF(10, 10, 110, 90),
            image_width=150,
            image_height=120,
            horizontal_margin_px=1,
            vertical_margin_px=1,
            expected_lane_count=3,
        )
        self.assertAlmostEqual(result.crop_box.x, 19.0)
        self.assertAlmostEqual(result.crop_box.x + result.crop_box.w, 101.0)
        self.assertEqual(result.lane_crop_boxes, ())

    def test_crop_margins_are_exact_around_signal_not_lane_boxes(self) -> None:
        lanes = [_lane(1, 20, 40, 20), _lane(2, 50, 40, 20)]
        lanes[0]["bands"][0]["signal_rect"] = QRectF(24, 45, 12, 8)
        lanes[1]["bands"][0]["signal_rect"] = QRectF(54, 45, 12, 8)
        result = calculate_band_auto_fit(
            lanes,
            search_roi=QRectF(0, 0, 100, 90),
            image_width=120,
            image_height=100,
            horizontal_margin_px=3,
            vertical_margin_px=2,
            expected_lane_count=2,
        )
        self.assertEqual(result.crop_box, ImageBBox(21.0, 43.0, 48.0, 12.0))

    def test_tall_signal_crop_stays_tight_regardless_of_frame_shape(self) -> None:
        lanes = [_lane(1, 80, 40, 20), _lane(2, 110, 40, 20)]
        result = calculate_band_auto_fit(
            lanes,
            search_roi=QRectF(60, 20, 100, 70),
            image_width=240,
            image_height=120,
            horizontal_margin_px=5,
            vertical_margin_px=5,
            expected_lane_count=2,
        )
        self.assertEqual(result.crop_box, ImageBBox(75.0, 35.0, 60.0, 30.0))
        self.assertLessEqual(result.crop_box.x, 80.0)
        self.assertGreaterEqual(result.crop_box.x + result.crop_box.w, 130.0)
        self.assertLessEqual(result.crop_box.y, 40.0)
        self.assertGreaterEqual(result.crop_box.y + result.crop_box.h, 60.0)

    def test_wide_signal_crop_does_not_add_vertical_whitespace(self) -> None:
        lanes = [_lane(1, 50, 50), _lane(2, 100, 50), _lane(3, 150, 50)]
        result = calculate_band_auto_fit(
            lanes,
            search_roi=QRectF(30, 20, 160, 70),
            image_width=240,
            image_height=120,
            horizontal_margin_px=5,
            vertical_margin_px=5,
            expected_lane_count=3,
        )
        self.assertEqual(result.crop_box, ImageBBox(45.0, 45.0, 130.0, 20.0))

    def test_tight_crop_clips_only_requested_margin_at_source_edge(self) -> None:
        result = calculate_band_auto_fit(
            [_lane(1, 0, 2, 10), _lane(2, 30, 2, 10)],
            search_roi=QRectF(0, 0, 70, 40),
            image_width=100,
            image_height=80,
            horizontal_margin_px=0,
            vertical_margin_px=2,
            expected_lane_count=2,
        )
        self.assertEqual(result.crop_box, ImageBBox(0.0, 0.0, 50.0, 14.0))
        self.assertGreaterEqual(result.crop_box.y, 0.0)
        self.assertGreaterEqual(result.crop_box.y + result.crop_box.h, 12.0)

    def test_small_peak_jitter_keeps_one_continuous_source_strip(self) -> None:
        lanes = [
            _lane(1, 20, 42, 12),
            _lane(2, 50, 43, 12),
            _lane(3, 80, 41, 12),
            _lane(4, 110, 42, 12),
        ]
        for lane, peak in zip(lanes, (48.0, 49.0, 47.0, 48.0)):
            lane["bands"][0]["peak_y"] = peak
        result = calculate_band_auto_fit(
            lanes,
            search_roi=QRectF(10, 30, 140, 40),
            image_width=160,
            image_height=100,
            horizontal_margin_px=2,
            vertical_margin_px=2,
            alignment="auto",
            expected_lane_count=4,
        )
        self.assertEqual(result.lane_crop_boxes, ())
        self.assertEqual(result.composite_width, result.crop_box.w)
        self.assertEqual(result.composite_height, result.crop_box.h)

    def test_auto_alignment_uses_stable_top_edge_for_downward_smear(self) -> None:
        lanes = [
            _lane(1, 20, 42, 10),
            _lane(2, 50, 42, 10),
            _lane(3, 80, 42, 10),
            _lane(4, 110, 42, 30),
        ]
        for lane in lanes:
            lane["bands"][0]["row_center"] = 47.0
        result = calculate_band_auto_fit(
            lanes,
            search_roi=QRectF(10, 10, 140, 90),
            image_width=160,
            image_height=120,
            horizontal_margin_px=1,
            vertical_margin_px=2,
            alignment="auto",
            expected_lane_count=4,
        )
        self.assertEqual(result.alignment_used, "top")
        self.assertEqual(result.row_anchor_y, 47.0)
        self.assertLessEqual(result.crop_box.y, 40.0)
        self.assertGreaterEqual(
            result.crop_box.y + result.crop_box.h,
            74.0,
        )

    def test_staggered_signal_peaks_keep_one_continuous_source_crop(self) -> None:
        first = _lane(1, 20, 40, 14)
        second = _lane(2, 50, 50, 24)
        first["bands"][0]["peak_y"] = 46.0
        second["bands"][0]["peak_y"] = 56.0
        result = calculate_band_auto_fit(
            [first, second],
            search_roi=QRectF(10, 20, 90, 70),
            image_width=120,
            image_height=100,
            horizontal_margin_px=2,
            vertical_margin_px=3,
            alignment="auto",
            expected_lane_count=2,
        )
        self.assertEqual(result.alignment_used, "peak")
        self.assertEqual(result.lane_crop_boxes, ())
        self.assertEqual(result.composite_width, result.crop_box.w)
        self.assertEqual(result.composite_height, result.crop_box.h)
        first_peak_in_crop = 46.0 - result.crop_box.y
        second_peak_in_crop = 56.0 - result.crop_box.y
        self.assertAlmostEqual(
            second_peak_in_crop - first_peak_in_crop,
            10.0,
        )

    def test_out_of_band_local_peak_coordinate_cannot_create_tall_frame(self) -> None:
        lanes = [_lane(1, 20, 70, 12), _lane(2, 50, 72, 12)]
        lanes[0]["bands"][0]["peak_y"] = 8.0
        lanes[1]["bands"][0]["peak_y"] = 10.0
        result = calculate_band_auto_fit(
            lanes,
            search_roi=QRectF(10, 60, 90, 30),
            image_width=120,
            image_height=100,
            horizontal_margin_px=2,
            vertical_margin_px=2,
            alignment="auto",
            expected_lane_count=2,
        )
        self.assertNotEqual(result.alignment_used, "peak")
        self.assertLessEqual(result.composite_height, 20.0)

    def test_requested_missing_row_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            calculate_band_auto_fit(
                [_lane(1, 20, 42)],
                search_roi=QRectF(0, 0, 120, 100),
                image_width=120,
                image_height=100,
                target_row=2,
            )

    def test_auto_fit_retries_dark_bands_on_light_background(self) -> None:
        fallback = [_lane(1, 20, 42)]
        with (
            patch("gui.main_window._infer_auto_fit_dark_on_light", return_value=False),
            patch(
                "core.band_detector.auto_detect_guided",
                side_effect=[
                    ([], {"failure_stage": "horizontal_zone"}),
                    (fallback, {"failure_stage": None}),
                ],
            ) as detector,
        ):
            detections, metadata = (
                _run_auto_fit_guided_with_polarity_fallback(
                    "source.png",
                    search_roi=QRectF(0, 0, 100, 80),
                    expected_lane_count=1,
                )
            )

        self.assertEqual(detections, fallback)
        self.assertEqual(metadata["detected_polarity"], "dark_on_light")
        self.assertEqual(detector.call_count, 2)
        self.assertFalse(detector.call_args_list[0].kwargs["dark_on_light"])
        self.assertTrue(detector.call_args_list[1].kwargs["dark_on_light"])

    def test_auto_fit_detects_four_dark_bands_on_light_background(self) -> None:
        with TemporaryDirectory() as tmp:
            path = f"{tmp}/dark_bands.png"
            image = Image.new("L", (420, 120), 245)
            draw = ImageDraw.Draw(image)
            for index in range(4):
                draw.rounded_rectangle(
                    (25 + index * 95, 45, 90 + index * 95, 70),
                    radius=10,
                    fill=25,
                )
            image.filter(ImageFilter.GaussianBlur(3)).save(path)

            rois = [QRectF(10, 20, 400, 80), QRectF(0, 0, 420, 120)]
            runs = []
            for roi in rois:
                detections, metadata = _run_auto_fit_guided_with_polarity_fallback(
                    path,
                    search_roi=roi,
                    expected_lane_count=4,
                )
                result = calculate_band_auto_fit(
                    detections,
                    search_roi=roi,
                    image_width=420,
                    image_height=120,
                    horizontal_margin_px=0,
                    vertical_margin_px=0,
                    alignment="auto",
                    expected_lane_count=4,
                )
                runs.append((detections, metadata, result))

        detections, metadata, first_result = runs[0]
        self.assertEqual(metadata["detected_polarity"], "dark_on_light")
        self.assertEqual(len(detections), 4)
        self.assertEqual(sum(len(lane["bands"]) for lane in detections), 4)
        for lane in detections:
            band = lane["bands"][0]
            rect = band["band_rect"]
            self.assertGreaterEqual(band["peak_y"], rect.y())
            self.assertLessEqual(band["peak_y"], rect.y() + rect.height())
        self.assertEqual(first_result.crop_box, runs[1][2].crop_box)

    def test_guided_pixels_identity_matches_existing_path_api_exactly(self) -> None:
        with TemporaryDirectory() as tmp:
            path = f"{tmp}/identity.png"
            image = Image.new("L", (420, 120), 245)
            draw = ImageDraw.Draw(image)
            for index in range(4):
                draw.rounded_rectangle(
                    (25 + index * 95, 45, 90 + index * 95, 70),
                    radius=10,
                    fill=25,
                )
            image.filter(ImageFilter.GaussianBlur(3)).save(path)
            roi = QRectF(10, 20, 400, 80)
            path_result = _run_auto_fit_guided_with_polarity_fallback(
                path,
                search_roi=roi,
                expected_lane_count=4,
            )
            pixels_result = _run_auto_fit_guided_with_polarity_fallback(
                path,
                detector_pixels=load_auto_detection_pixels(path),
                search_roi=roi,
                expected_lane_count=4,
            )

        self.assertEqual(pixels_result, path_result)

    def test_auto_fit_detects_after_rotation_flip_and_combinations(self) -> None:
        image = Image.new("L", (420, 160), 245)
        draw = ImageDraw.Draw(image)
        for index in range(4):
            draw.rounded_rectangle(
                (25 + index * 95, 65, 90 + index * 95, 90),
                radius=10,
                fill=25,
            )
        horizontal_presentation = np.asarray(
            image.filter(ImageFilter.GaussianBlur(3)),
            dtype=np.uint8,
        )
        transforms = (
            GeometryTransform(rotation=12.0),
            GeometryTransform(rotation=-12.0),
            GeometryTransform(flip_x=True),
            GeometryTransform(flip_y=True),
            GeometryTransform(rotation=9.0, flip_x=True),
            GeometryTransform(rotation=-9.0, flip_y=True),
            GeometryTransform(rotation=7.0, flip_x=True, flip_y=True),
        )

        for geometry in transforms:
            with self.subTest(geometry=geometry):
                flip_count = int(geometry.flip_x) + int(geometry.flip_y)
                inverse_rotation = (
                    geometry.rotation if flip_count == 1 else -geometry.rotation
                )
                raw_pixels = apply_geometry_to_display(
                    horizontal_presentation,
                    GeometryTransform(
                        rotation=inverse_rotation,
                        flip_x=geometry.flip_x,
                        flip_y=geometry.flip_y,
                    ),
                )
                presentation_pixels = apply_geometry_to_display(
                    raw_pixels,
                    geometry,
                )
                height, width = presentation_pixels.shape
                roi = QRectF(0, 0, width, height)

                detections, metadata = (
                    _run_auto_fit_guided_with_polarity_fallback(
                        "unused-path",
                        detector_pixels=presentation_pixels,
                        search_roi=roi,
                        expected_lane_count=4,
                    )
                )
                result = calculate_band_auto_fit(
                    detections,
                    search_roi=roi,
                    image_width=width,
                    image_height=height,
                    horizontal_margin_px=4,
                    vertical_margin_px=4,
                    expected_lane_count=4,
                )

                self.assertEqual(metadata["detected_polarity"], "dark_on_light")
                self.assertEqual(len(detections), 4)
                self.assertEqual(
                    sum(len(lane["bands"]) for lane in detections),
                    4,
                )
                signal_rects = [
                    band.get("signal_rect", band["band_rect"])
                    for lane in detections
                    for band in lane["bands"]
                ]
                expected_left = max(
                    0.0,
                    min(rect.x() for rect in signal_rects) - 4.0,
                )
                expected_top = max(
                    0.0,
                    min(rect.y() for rect in signal_rects) - 4.0,
                )
                expected_right = min(
                    float(width),
                    max(rect.right() for rect in signal_rects) + 4.0,
                )
                expected_bottom = min(
                    float(height),
                    max(rect.bottom() for rect in signal_rects) + 4.0,
                )
                self.assertEqual(
                    result.crop_box,
                    ImageBBox(
                        expected_left,
                        expected_top,
                        expected_right - expected_left,
                        expected_bottom - expected_top,
                    ),
                )
                self.assertGreaterEqual(result.crop_box.x, 0.0)
                self.assertGreaterEqual(result.crop_box.y, 0.0)
                self.assertLessEqual(result.crop_box.x + result.crop_box.w, width)
                self.assertLessEqual(result.crop_box.y + result.crop_box.h, height)


if __name__ == "__main__":
    unittest.main()
