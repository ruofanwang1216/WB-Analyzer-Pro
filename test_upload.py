from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image
from PySide6.QtCore import QEventLoop, QThread, QTimer
from PySide6.QtWidgets import QApplication

from gui.main_window import MainWindow


class _FakeExporter:
    def __init__(self) -> None:
        self.worker_thread = None

    def export_documents(self, document_paths, output_dir, *, debug, log):
        self.worker_thread = QThread.currentThread()
        log(f"Parsing file: {document_paths[0].name}")
        return []


class UploadWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _make_window(self) -> MainWindow:
        with (
            patch("gui.main_window.AppPersistence.update_config", return_value=None),
            patch("gui.main_window.AppPersistence.read_config", return_value={"ui": {}}),
        ):
            return MainWindow()

    def test_conversion_runs_outside_gui_thread_and_reenables_upload(self) -> None:
        window = self._make_window()
        exporter = _FakeExporter()
        window._direct_exporter = exporter
        selected = [Path("/tmp/sample.scn")]

        with patch.object(window, "_complete_upload") as complete_upload:
            window._start_upload_conversion(
                docs=selected,
                selected_paths=selected,
                direct_tiffs=[],
                unsupported=[],
            )
            thread = window._upload_thread
            self.assertIsNotNone(thread)
            self.assertFalse(window._act_open.isEnabled())

            loop = QEventLoop()
            thread.finished.connect(loop.quit)
            QTimer.singleShot(3000, loop.quit)
            loop.exec()
            self._app.processEvents()

        self.assertIsNot(exporter.worker_thread, self._app.thread())
        complete_upload.assert_called_once()
        self.assertTrue(window._act_open.isEnabled())
        self.assertIsNone(window._upload_thread)

    def test_completed_upload_explains_when_all_viewers_are_full(self) -> None:
        window = self._make_window()
        for index, state in enumerate(window._slot_states):
            state["path"] = f"/tmp/already-open-{index}.tif"

        with tempfile.TemporaryDirectory() as tmpdir:
            uploaded = Path(tmpdir) / "new-image.tif"
            Image.fromarray(np.zeros((4, 4), dtype=np.uint16)).save(uploaded)

            with patch.object(window._status_bar, "showMessage") as show_status, patch(
                "gui.main_window.QMessageBox.information"
            ) as information:
                window._complete_upload(
                    results=[],
                    selected_paths=[uploaded],
                    direct_tiffs=[uploaded],
                    unsupported=[],
                )

        self.assertIn(str(uploaded), window._converted_documents)
        self.assertIn("all 4 image windows are full", show_status.call_args.args[0])
        self.assertEqual(information.call_args.args[1], "Upload Complete — Image Windows Full")


if __name__ == "__main__":
    unittest.main()
