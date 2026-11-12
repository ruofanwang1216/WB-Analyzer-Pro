"""Background QObject workers used by the main window."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from utils.logger import get_logger

log = get_logger(__name__)


class MeasurementWorker(QObject):
    """
    Measures inverse-mapped raw-image ROIs at the source image's native depth.
    """

    progress = Signal(str)      # status text
    finished = Signal(list)     # list of measurement dicts: {lane, Area, Mean, Min, Max, IntDen, RawIntDen}
    error = Signal(str)         # error message

    def __init__(
        self,
        image_path: str,
        band_rois: list,
    ) -> None:
        super().__init__()
        self.image_path = image_path
        self.band_rois = band_rois

    def run(self) -> None:
        from core.measure import measure_all_lanes
        self.progress.emit(f"Measuring {len(self.band_rois)} lane(s)…")
        try:
            results = measure_all_lanes(self.image_path, self.band_rois)
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))


class UploadConversionWorker(QObject):
    """Convert Image Lab documents without blocking the Qt event loop."""

    progress = Signal(str)
    finished = Signal(list)
    error = Signal(str)

    def __init__(
        self,
        exporter,
        document_paths: list[Path],
        output_dir: Path,
    ) -> None:
        super().__init__()
        self.exporter = exporter
        self.document_paths = list(document_paths)
        self.output_dir = output_dir

    def run(self) -> None:
        try:
            results = self.exporter.export_documents(
                self.document_paths,
                self.output_dir,
                debug=False,
                log=self.progress.emit,
            )
            self.finished.emit(results)
        except Exception as exc:
            log.exception("Background upload conversion failed")
            self.error.emit(str(exc))
