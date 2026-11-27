"""Image Lab / ImageJ compatible display controls."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).parent / "vendor" / "wb_tiff_exporter"))

from scn_parser import _DisplayTransform, _display_settings_description  # noqa: E402
from tiff_writer import build_grayscale16_tiff  # noqa: E402

from core.image_transform import (  # noqa: E402
    ImageTransformParams,
    auto_scale_range_16,
    bake_display_into_uint16,
    default_display_params,
    display_histogram,
    embedded_display_settings,
    imagej_adjust_brightness,
    imagej_adjust_contrast,
    imagej_slider_positions,
    saturated_mask,
)
from gui.image_canvas import ImageCanvas  # noqa: E402
from gui.image_transform_dialog import (  # noqa: E402
    ImageTransformDialog,
    _gamma_to_slider,
    _slider_to_gamma,
)


def _blot(seed: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    pixels = rng.normal(5000, 300, size=(120, 160)).clip(0, 65535)
    pixels[40:50, 20:60] += 20000          # a band
    pixels[80:90, 100:120] = 65535         # a blown-out band
    return pixels.astype(np.uint16)


def _write_tiff(path: Path, pixels: np.ndarray, description: str | None = None) -> None:
    path.write_bytes(
        build_grayscale16_tiff(
            pixels.astype("<u2").tobytes(),
            width=pixels.shape[1],
            height=pixels.shape[0],
            image_description=description,
        )
    )


class ImageLabCompatibilityTests(unittest.TestCase):
    def test_auto_scale_is_nth_darkest_and_nth_brightest_pixel(self) -> None:
        pixels = _blot()
        ordered = np.sort(pixels.reshape(-1))
        n = int(round(ordered.size * 0.001))

        self.assertEqual(
            auto_scale_range_16(pixels),
            (int(ordered[n - 1]), int(ordered[-n])),
        )

    def test_converted_document_keeps_raw_pixels_and_remembers_display(self) -> None:
        pixels = _blot()
        description = _display_settings_description(
            _DisplayTransform(low=4200, high=34276, gamma=0.8, inverted=False),
            white_is_zero=True,
        )
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "blot.tif"
            _write_tiff(path, pixels, description)
            with Image.open(path) as image:
                stored = np.asarray(image)
                settings = embedded_display_settings(image)
                opened = default_display_params(image, stored)

        # Nothing was clipped at the High setting: the data is untouched.
        np.testing.assert_array_equal(stored, pixels)
        self.assertEqual(settings, ImageTransformParams(4200, 34276, 0.8, True))
        self.assertEqual(opened, settings)

    def test_image_lab_invert_flips_the_default_polarity(self) -> None:
        description = _display_settings_description(
            _DisplayTransform(low=0, high=1000, gamma=1.0, inverted=True),
            white_is_zero=True,
        )
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "blot.tif"
            _write_tiff(path, _blot(), description)
            with Image.open(path) as image:
                self.assertFalse(embedded_display_settings(image).inverted)

    def test_plain_16bit_tiff_opens_auto_scaled_and_8bit_at_full_range(self) -> None:
        pixels = _blot()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "raw.tif"
            _write_tiff(path, pixels)
            with Image.open(path) as image:
                opened = default_display_params(image, np.asarray(image))
        self.assertEqual((opened.low, opened.high), auto_scale_range_16(pixels))
        self.assertTrue(opened.inverted)

        eight_bit = Image.fromarray((pixels // 257).astype(np.uint8))
        self.assertEqual(
            default_display_params(eight_bit, np.asarray(eight_bit)),
            ImageTransformParams(inverted=True),
        )

    def test_exported_copy_matches_the_old_baked_stretch(self) -> None:
        pixels = np.array([[0, 100, 600, 1100, 5000]], dtype=np.uint16)
        params = ImageTransformParams(low=100, high=1100, gamma=1.0, inverted=True)

        baked = bake_display_into_uint16(pixels, params)

        self.assertEqual(baked.tolist(), [[0, 0, 32768, 65535, 65535]])

    def test_histogram_spans_the_values_present(self) -> None:
        counts, low, high = display_histogram(_blot(), bins=64)
        self.assertEqual(int(counts.sum()), 120 * 160)
        self.assertEqual(high, 65535)
        self.assertGreater(low, 0)

    def test_saturated_mask_marks_only_the_ceiling(self) -> None:
        self.assertEqual(int(saturated_mask(_blot()).sum()), 10 * 20)


class ImageJCompatibilityTests(unittest.TestCase):
    def test_brightness_moves_the_window_without_changing_its_width(self) -> None:
        low, high = imagej_adjust_brightness(2000, 6000, 192, 0, 8000)
        self.assertEqual((low, high), (0, 4000))

    def test_contrast_changes_width_around_the_centre(self) -> None:
        self.assertEqual(imagej_adjust_contrast(2000, 6000, 128, 0, 8000), (0, 8000))
        self.assertEqual(imagej_adjust_contrast(2000, 6000, 192, 0, 8000), (2000, 6000))

    def test_slider_positions_round_trip(self) -> None:
        brightness, contrast = imagej_slider_positions(2000, 6000, 0, 8000)
        self.assertEqual((brightness, contrast), (128, 192))
        self.assertEqual(imagej_adjust_contrast(2000, 6000, contrast, 0, 8000), (2000, 6000))


class ImageTransformDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _dialog(self):
        dialog = ImageTransformDialog()
        emitted: list[ImageTransformParams] = []
        dialog.paramsChanged.connect(emitted.append)
        counts, low, high = display_histogram(_blot())
        dialog.set_image_statistics(
            {"histogram": counts, "min": low, "max": high, "saturated": 0.0104}
        )
        dialog.set_reference_window(4000, 30000)
        dialog.set_params(ImageTransformParams(low=4000, high=30000, inverted=True))
        return dialog, emitted, (low, high)

    def test_sliders_start_centred_and_move_both_ways(self) -> None:
        dialog, emitted, _ = self._dialog()
        for slider, value in ((dialog._low_slider, 4000), (dialog._high_slider, 30000)):
            self.assertEqual(slider.value(), value)
            self.assertEqual(slider.value() - slider.minimum(), slider.maximum() - slider.value())
        # Low may go below the darkest pixel; the range grows for far values.
        dialog._low_slider.setValue(dialog._low_slider.minimum())
        self.assertEqual(emitted[-1].low, 4000 - 26000)
        dialog.set_params(ImageTransformParams(low=4000, high=65535, inverted=True))
        self.assertEqual(dialog._high_slider.value(), 65535)
        emitted.clear()
        dialog.set_params(ImageTransformParams(low=4000, high=30000, inverted=True))
        self.assertEqual(emitted, [])
        self.assertIn("1.0", dialog._info_label.text())

    def test_moving_low_keeps_high_and_emits_once(self) -> None:
        dialog, emitted, _ = self._dialog()
        dialog._low_spin.setValue(6000)
        self.assertEqual(emitted, [ImageTransformParams(6000, 30000, 1.0, True)])
        self.assertEqual(dialog._low_slider.value(), 6000)

    def test_low_cannot_pass_high(self) -> None:
        dialog, emitted, _ = self._dialog()
        dialog._low_spin.setValue(50000)
        self.assertEqual(emitted[-1].low, 29999)
        self.assertEqual(emitted[-1].high, 30000)

    def test_gamma_slider_is_logarithmic_and_centred_on_one(self) -> None:
        self.assertEqual(_gamma_to_slider(1.0), 0)
        self.assertEqual(_slider_to_gamma(0), 1.0)
        self.assertEqual(_gamma_to_slider(2.0), -_gamma_to_slider(0.5))
        dialog, emitted, _ = self._dialog()
        dialog._gamma_slider.setValue(_gamma_to_slider(2.0))
        self.assertAlmostEqual(emitted[-1].gamma, 2.0, places=1)
        self.assertAlmostEqual(dialog._gamma_spin.value(), emitted[-1].gamma, places=2)

    def test_brightness_and_contrast_sliders_use_imagej_math(self) -> None:
        dialog, emitted, (low, high) = self._dialog()
        dialog._contrast_slider.setValue(200)
        self.assertEqual(
            (emitted[-1].low, emitted[-1].high),
            imagej_adjust_contrast(4000, 30000, 200, low, high),
        )
        self.assertEqual(dialog._contrast_slider.value(), 200)
        window = (emitted[-1].low, emitted[-1].high)
        dialog._brightness_slider.setValue(100)
        self.assertEqual(
            (emitted[-1].low, emitted[-1].high),
            imagej_adjust_brightness(*window, 100, low, high),
        )

    def test_histogram_marker_drag_and_option_signals(self) -> None:
        dialog, emitted, _ = self._dialog()
        dialog._histogram.rangeEdited.emit(5000, 20000)
        self.assertEqual((emitted[-1].low, emitted[-1].high), (5000, 20000))

        flags: list[bool] = []
        dialog.highlightSaturatedChanged.connect(flags.append)
        dialog._saturated_checkbox.setChecked(False)
        self.assertEqual(flags, [False])
        dialog.resize(480, 520)
        dialog._log_checkbox.setChecked(True)
        dialog.grab()  # paints the histogram in both modes without error


class CanvasDisplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _canvas(self, folder: str, description: str | None = None) -> ImageCanvas:
        path = Path(folder) / "blot.tif"
        _write_tiff(path, _blot(), description)
        canvas = ImageCanvas()
        canvas.resize(400, 300)
        canvas.load_image_blocking(str(path))
        return canvas

    def test_opens_with_stored_settings_and_resets_to_them(self) -> None:
        description = _display_settings_description(
            _DisplayTransform(low=4200, high=34276, gamma=1.0, inverted=False),
            white_is_zero=True,
        )
        with tempfile.TemporaryDirectory() as folder:
            canvas = self._canvas(folder, description)
            stored = ImageTransformParams(4200, 34276, 1.0, True)
            self.assertEqual(canvas.get_image_transform_params(), stored)
            # Measurements still see the untouched data, blown-out band included.
            self.assertEqual(int(canvas.current_analysis_pixels().max()), 65535)

            auto = canvas.auto_scale_image_transform()
            self.assertEqual((auto.low, auto.high), auto_scale_range_16(_blot()))
            self.assertEqual(canvas.reset_image_transform(), stored)

    def test_saturated_pixels_are_red_only_while_highlighting(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            canvas = self._canvas(folder)
            stats = canvas.display_statistics()
            self.assertAlmostEqual(stats["saturated"], 200 / (120 * 160))

            def pixel(x: int, y: int):
                colour = canvas._pixmap_item.pixmap().toImage().pixelColor(x, y)
                return colour.red(), colour.green(), colour.blue()

            self.assertEqual(pixel(110, 85), (255, 0, 0))
            self.assertEqual(len(set(pixel(30, 45))), 1)  # ordinary band stays grey
            canvas.set_highlight_saturated(False)
            self.assertEqual(pixel(110, 85), (0, 0, 0))
            # Analysis and detection always get plain grey display pixels.
            self.assertEqual(canvas.current_display_pixels().ndim, 2)


if __name__ == "__main__":
    unittest.main()
