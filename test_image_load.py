from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
from PIL import Image
from PySide6.QtCore import QEventLoop, QThread, QTimer
from PySide6.QtWidgets import QApplication

from core.image_transform import (
    image_array_to_raw_luminance,
    image_array_to_uint16_luminance,
)
from gui.image_canvas import ImageCanvas


class AsyncImageLoadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _wait_for_load(self, canvas: ImageCanvas, timeout_ms: int = 5000) -> None:
        if not canvas.is_loading():
            return
        loop = QEventLoop()
        failure: list[str] = []
        canvas.image_load_finished.connect(loop.quit)
        canvas.image_load_failed.connect(
            lambda _path, message: (failure.append(message), loop.quit())
        )
        QTimer.singleShot(timeout_ms, loop.quit)
        loop.exec()
        self.assertFalse(canvas.is_loading(), "image load timed out")
        self.assertFalse(failure, failure[0] if failure else "")

    def test_uncompressed_uint16_load_is_async_two_stage_and_memory_mapped(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "large-raw.tif"
            pixels = np.arange(32 * 5000, dtype=np.uint16).reshape(32, 5000)
            Image.fromarray(pixels).save(path, compression="raw")

            canvas = ImageCanvas()
            canvas.resize(640, 480)
            events: list[tuple[str, QThread]] = []
            canvas.image_preview_ready.connect(
                lambda _path: events.append(("preview", QThread.currentThread()))
            )
            canvas.image_load_finished.connect(
                lambda _path: events.append(("finished", QThread.currentThread()))
            )

            started = time.perf_counter()
            canvas.load_image(path)
            elapsed = time.perf_counter() - started
            self.assertTrue(canvas.is_loading())
            self.assertLess(elapsed, 0.5)

            self._wait_for_load(canvas)
            self.assertEqual([name for name, _thread in events], ["preview", "finished"])
            self.assertTrue(all(thread is self._app.thread() for _name, thread in events))
            self.assertTrue(canvas.is_memory_mapped())
            self.assertEqual(canvas._display_preview_stride, 2)
            self.assertEqual(canvas._pixmap_item.pixmap().width(), 2500)
            self.assertEqual(canvas.image_scene_size(), canvas.raw_image_size())
            np.testing.assert_array_equal(canvas.current_analysis_pixels(), pixels)
            self.assertFalse(canvas._raw_quantification_pixels.flags.writeable)
            canvas.cancel_image_load(wait=True)
            self._app.processEvents()

    def test_compressed_rgb_tiff_falls_back_to_pillow_without_depth_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "compressed-rgb.tif"
            pixels = np.stack(
                (
                    np.arange(99, dtype=np.uint8).reshape(9, 11),
                    np.full((9, 11), 80, dtype=np.uint8),
                    np.full((9, 11), 210, dtype=np.uint8),
                ),
                axis=2,
            )
            Image.fromarray(pixels).save(path, compression="tiff_lzw")
            expected = image_array_to_raw_luminance(pixels)

            canvas = ImageCanvas()
            canvas.load_image(path)
            self._wait_for_load(canvas)

            self.assertFalse(canvas.is_memory_mapped())
            self.assertEqual(canvas.current_analysis_pixels().dtype, np.uint8)
            np.testing.assert_array_equal(canvas.current_analysis_pixels(), expected)
            canvas.cancel_image_load(wait=True)
            self._app.processEvents()

    def test_big_endian_uint16_is_not_treated_as_8bit_display_data(self) -> None:
        pixels = np.array([[1, 256, 4096, 65535]], dtype=">u2")
        converted = image_array_to_uint16_luminance(pixels)
        self.assertEqual(converted.dtype, np.uint16)
        np.testing.assert_array_equal(
            converted,
            np.array([[1, 256, 4096, 65535]], dtype=np.uint16),
        )


if __name__ == "__main__":
    unittest.main()
