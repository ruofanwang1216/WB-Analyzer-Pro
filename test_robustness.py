"""Regression tests for crash-safety and error-path fixes."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from core.export_engine import _qimage_to_png_bytes
from gui.main_window import MainWindow
from utils.persistence import AppPersistence


class PersistenceFallbackTests(unittest.TestCase):
    def test_unwritable_project_dir_falls_back_instead_of_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            blocked = Path(tmpdir) / "blocked"
            fallback_cwd = Path(tmpdir) / "cwd"
            fallback_cwd.mkdir()
            original_mkdir = Path.mkdir

            def mkdir(self, *args, **kwargs):
                if self == blocked.resolve():
                    raise PermissionError("read-only location")
                return original_mkdir(self, *args, **kwargs)

            with (
                patch.object(Path, "mkdir", mkdir),
                patch.object(Path, "cwd", return_value=fallback_cwd),
            ):
                persistence = AppPersistence(blocked)

            self.assertEqual(persistence.project_dir.parent, fallback_cwd.resolve())
            self.assertTrue((persistence.project_dir / "config.json").exists())

    def test_analysis_debug_timestamp_is_timezone_aware_utc(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            persistence = AppPersistence(Path(tmpdir))
            persistence.remember_analysis_debug(
                image_path="blot.tif", mode="manual", lane_count=3, band_count=3
            )
            config = json.loads((Path(tmpdir) / "config.json").read_text("utf-8"))
        self.assertTrue(
            config["debug"]["last_analysis"]["timestamp_utc"].endswith("+00:00")
        )


class PngEncodingTests(unittest.TestCase):
    def test_in_memory_png_round_trips_pixels(self) -> None:
        image = QImage(5, 3, QImage.Format.Format_RGB32)
        image.fill(0xFF336699)
        data = _qimage_to_png_bytes(image)
        self.assertIsNotNone(data)
        decoded = QImage.fromData(data, "PNG")
        self.assertEqual((decoded.width(), decoded.height()), (5, 3))
        self.assertEqual(decoded.pixel(2, 1), image.pixel(2, 1))
        self.assertIsNone(_qimage_to_png_bytes(QImage()))


class MainWindowRobustnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _window(self) -> MainWindow:
        with (
            patch("gui.main_window.AppPersistence.update_config", return_value=None),
            patch("gui.main_window.AppPersistence.read_config", return_value={"ui": {}}),
        ):
            return MainWindow()

    def _wait_until(self, predicate, timeout_ms: int = 5000) -> None:
        loop = QEventLoop()
        poll = QTimer()
        poll.timeout.connect(lambda: predicate() and loop.quit())
        poll.start(10)
        QTimer.singleShot(timeout_ms, loop.quit)
        loop.exec()
        poll.stop()
        self.assertTrue(predicate())

    def test_measurement_thread_runs_and_releases_its_references(self) -> None:
        window = self._window()
        pixels = np.full((20, 30), 1000, dtype=np.uint16)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "blot.tif"
            Image.fromarray(pixels).save(path)
            roi = {"x": 2.0, "y": 2.0, "width": 4.0, "height": 5.0, "lane": 1}
            results: list = []
            with patch.object(
                window.results_panel, "show_results", side_effect=results.append
            ):
                window._start_measurement_worker(str(path), [roi])
                first_thread = window._worker_thread
                self.assertIsNotNone(first_thread)

                # A second request while the first is running must not replace
                # (and thereby destroy) the running thread.
                window._start_measurement_worker(str(path), [roi])
                self.assertIs(window._worker_thread, first_thread)

                self._wait_until(lambda: window._worker_thread is None)

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["Mean"].tolist(), [1000.0])
            self.assertIsNone(window._worker)

            # The next analysis can start once the previous one has finished.
            with patch.object(window.results_panel, "show_results"):
                window._start_measurement_worker(str(path), [roi])
                self.assertIsNotNone(window._worker_thread)
                self._wait_until(lambda: window._worker_thread is None)
        window.close()

    def test_failed_conversion_still_opens_directly_selected_tiffs(self) -> None:
        window = self._window()
        tiff = Path("/tmp/direct.tif")
        doc = Path("/tmp/document.scn")
        window._pending_upload = {
            "selected_paths": [doc, tiff],
            "direct_tiffs": [tiff],
            "unsupported": [],
        }
        with (
            patch("gui.main_window.QMessageBox.critical"),
            patch.object(window, "_complete_upload") as complete,
        ):
            window._on_upload_conversion_error("conversion failed")

        complete.assert_called_once_with(
            results=[],
            selected_paths=[tiff],
            direct_tiffs=[tiff],
            unsupported=[],
        )
        self.assertIsNone(window._pending_upload)
        window.close()

    def test_tiff_export_reports_copy_failure(self) -> None:
        window = self._window()
        with tempfile.TemporaryDirectory() as tmpdir:
            missing = Path(tmpdir) / "gone.tif"
            window._converted_documents["gone"] = {
                "source": missing,
                "tiffs": [missing],
                "display_tiff": missing,
            }
            destination = Path(tmpdir) / "out"
            with (
                patch(
                    "gui.main_window.QFileDialog.getExistingDirectory",
                    return_value=str(destination),
                ),
                patch.object(window._persistence, "remember_tiff_export_dir"),
                patch("gui.main_window.QMessageBox.critical") as critical,
                patch("gui.main_window.QMessageBox.information") as information,
            ):
                window._export_all_tiffs()

            critical.assert_called_once()
            information.assert_not_called()
        window.close()


if __name__ == "__main__":
    unittest.main()
