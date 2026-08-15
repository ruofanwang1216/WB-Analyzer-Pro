"""Main application window — orchestrates pure-Python band densitometry."""
from __future__ import annotations

import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image
from PySide6.QtCore import Qt, QObject, QThread, Signal, QRectF, QSize, QSizeF, QPointF, QEvent
from PySide6.QtGui import (
    QAction,
    QColor,
    QCloseEvent,
    QIcon,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QSplitter, QVBoxLayout, QStackedWidget,
    QToolBar, QStatusBar, QDockWidget, QFileDialog, QMessageBox,
    QHBoxLayout, QToolButton, QPushButton, QFrame, QSizePolicy, QCheckBox,
    QLabel, QListWidget, QListWidgetItem, QDialog, QGridLayout, QMenu, QComboBox,
)

from config.settings import APP_NAME
from core.band_detector import group_auto_detected_rows
from core.image_transform import (
    ImageTransformParams,
    flip_display_pixels_to_file,
    image_transform_from_dict,
    image_transform_to_dict,
    rotate_display_pixels_to_file,
)
from gui.figure_generation import FigureTypeDialog, ColumnSetupDialog, ColumnTableWindow
from gui.image_canvas import ImageCanvas
from gui.image_transform_dialog import ImageTransformDialog
from gui.param_panel import ParamPanel
from gui.results_panel import ResultsPanel
from gui.tutorial import TutorialController, TutorialModeDialog
from utils.logger import get_logger
from utils.persistence import AppPersistence
from utils.i18n import LANG_EN, LANG_ZH_CN, tr

log = get_logger(__name__)

_MAX_IMAGE_PANELS = 4


def _folder_open_icon(size: int = 22) -> QIcon:
    """Return a compact outline folder icon matching the Import Images control."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(size / 24.0, size / 24.0)
    pen = QPen(QColor("#29483B"), 1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    folder_back = QPainterPath()
    folder_back.moveTo(3.0, 19.0)
    folder_back.lineTo(3.0, 6.0)
    folder_back.cubicTo(3.0, 4.7, 3.7, 4.0, 5.0, 4.0)
    folder_back.lineTo(9.0, 4.0)
    folder_back.lineTo(11.2, 7.0)
    folder_back.lineTo(19.0, 7.0)
    folder_back.cubicTo(20.3, 7.0, 21.0, 7.7, 21.0, 9.0)
    folder_back.lineTo(21.0, 10.5)
    painter.drawPath(folder_back)

    folder_front = QPainterPath()
    folder_front.moveTo(3.2, 18.8)
    folder_front.lineTo(5.7, 11.8)
    folder_front.cubicTo(6.1, 10.7, 6.8, 10.2, 8.0, 10.2)
    folder_front.lineTo(20.5, 10.2)
    folder_front.cubicTo(21.8, 10.2, 22.2, 11.0, 21.8, 12.2)
    folder_front.lineTo(19.4, 19.0)
    folder_front.cubicTo(19.0, 20.2, 18.2, 20.7, 17.0, 20.7)
    folder_front.lineTo(5.0, 20.7)
    folder_front.cubicTo(3.7, 20.7, 2.8, 20.0, 3.2, 18.8)
    painter.drawPath(folder_front)
    painter.end()
    return QIcon(pixmap)


def _zoom_icon(*, zoom_in: bool, size: int = 18) -> QIcon:
    """Return a compact magnifier icon for an image-panel zoom control."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(size / 18.0, size / 18.0)
    pen = QPen(QColor("#405967"), 1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(QRectF(2.5, 2.0, 9.5, 9.5))
    painter.drawLine(QPointF(10.2, 10.0), QPointF(15.2, 15.0))
    painter.drawLine(QPointF(5.0, 6.75), QPointF(9.5, 6.75))
    if zoom_in:
        painter.drawLine(QPointF(7.25, 4.5), QPointF(7.25, 9.0))
    painter.end()
    return QIcon(pixmap)


def _image_operation_icon(operation: str, size: int = 18) -> QIcon:
    """Draw compact, font-independent arrows for the image operation menu."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(size / 18.0, size / 18.0)
    pen = QPen(QColor("#26322D"), 1.65)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    if operation == "flip-horizontal":
        painter.drawLine(QPointF(3.0, 9.0), QPointF(15.0, 9.0))
        painter.drawLine(QPointF(3.0, 9.0), QPointF(6.0, 6.0))
        painter.drawLine(QPointF(3.0, 9.0), QPointF(6.0, 12.0))
        painter.drawLine(QPointF(15.0, 9.0), QPointF(12.0, 6.0))
        painter.drawLine(QPointF(15.0, 9.0), QPointF(12.0, 12.0))
    elif operation == "flip-vertical":
        painter.drawLine(QPointF(9.0, 3.0), QPointF(9.0, 15.0))
        painter.drawLine(QPointF(9.0, 3.0), QPointF(6.0, 6.0))
        painter.drawLine(QPointF(9.0, 3.0), QPointF(12.0, 6.0))
        painter.drawLine(QPointF(9.0, 15.0), QPointF(6.0, 12.0))
        painter.drawLine(QPointF(9.0, 15.0), QPointF(12.0, 12.0))
    elif operation == "custom-rotate":
        # One continuous, two-headed curved arrow mirrors the reference icon
        # while remaining crisp on systems without the corresponding glyph.
        curve = QPainterPath(QPointF(3.0, 12.5))
        curve.cubicTo(4.0, 5.0, 14.0, 5.0, 15.0, 12.5)
        painter.drawPath(curve)
        painter.drawLine(QPointF(3.0, 12.5), QPointF(3.2, 8.7))
        painter.drawLine(QPointF(3.0, 12.5), QPointF(6.7, 11.7))
        painter.drawLine(QPointF(15.0, 12.5), QPointF(14.8, 8.7))
        painter.drawLine(QPointF(15.0, 12.5), QPointF(11.3, 11.7))
    elif operation == "undo":
        path = QPainterPath(QPointF(5.0, 6.0))
        path.cubicTo(13.5, 4.0, 15.0, 8.2, 14.0, 12.0)
        painter.drawPath(path)
        painter.drawLine(QPointF(5.0, 6.0), QPointF(8.2, 3.8))
        painter.drawLine(QPointF(5.0, 6.0), QPointF(7.7, 8.7))

    painter.end()
    return QIcon(pixmap)


def _infer_auto_fit_dark_on_light(
    image_path: str,
    search_roi: QRectF | None,
) -> bool:
    """Infer whether the rough ROI contains dark bands on a light background."""
    try:
        with Image.open(image_path) as image:
            pixels = np.asarray(image)
        if pixels.ndim == 3:
            rgb = pixels[:, :, :3].astype(np.float64)
            pixels = (
                0.299 * rgb[:, :, 0]
                + 0.587 * rgb[:, :, 1]
                + 0.114 * rgb[:, :, 2]
            )
        else:
            pixels = pixels.astype(np.float64)
        if pixels.ndim != 2 or pixels.size == 0:
            return False

        if search_roi is not None:
            height, width = pixels.shape
            left = max(0, min(width - 1, int(search_roi.left())))
            top = max(0, min(height - 1, int(search_roi.top())))
            right = max(left + 1, min(width, int(np.ceil(search_roi.right()))))
            bottom = max(top + 1, min(height, int(np.ceil(search_roi.bottom()))))
            pixels = pixels[top:bottom, left:right]
        if pixels.size == 0:
            return False

        border = np.concatenate((
            pixels[0, :].reshape(-1),
            pixels[-1, :].reshape(-1),
            pixels[:, 0].reshape(-1),
            pixels[:, -1].reshape(-1),
        ))
        background = float(np.median(border))
        low = float(np.percentile(pixels, 5.0))
        high = float(np.percentile(pixels, 95.0))
        dark_contrast = max(0.0, background - low)
        light_contrast = max(0.0, high - background)
        return dark_contrast > light_contrast
    except Exception:
        log.exception("Could not infer WB Auto-Fit polarity; using light-on-dark first")
        return False


def _run_auto_fit_guided_with_polarity_fallback(
    image_path: str,
    *,
    search_roi: QRectF | None,
    expected_lane_count: int | None,
    sensitivity: float = 0.5,
) -> tuple[list[dict], dict]:
    """Detect a figure strip and retry with reversed polarity when needed."""
    from core.band_detector import auto_detect_guided

    common = {
        "sensitivity": sensitivity,
        "search_rect": search_roi,
        "expected_lane_count": expected_lane_count,
        "target_band_row": None,
        "expected_rows_per_lane": None,
        "return_metadata": True,
    }
    primary_dark_on_light = _infer_auto_fit_dark_on_light(
        image_path, search_roi
    )
    detections, metadata = auto_detect_guided(
        image_path,
        dark_on_light=primary_dark_on_light,
        **common,
    )
    total_bands = sum(len(lane.get("bands", [])) for lane in detections)
    if total_bands > 0:
        metadata = dict(metadata)
        metadata["detected_polarity"] = (
            "dark_on_light" if primary_dark_on_light else "light_on_dark"
        )
        return detections, metadata

    reversed_detections, reversed_metadata = auto_detect_guided(
        image_path,
        dark_on_light=not primary_dark_on_light,
        **common,
    )
    reversed_total = sum(
        len(lane.get("bands", [])) for lane in reversed_detections
    )
    if reversed_total > total_bands:
        reversed_metadata = dict(reversed_metadata)
        reversed_metadata["detected_polarity"] = (
            "light_on_dark" if primary_dark_on_light else "dark_on_light"
        )
        return reversed_detections, reversed_metadata

    metadata = dict(metadata)
    metadata["detected_polarity"] = (
        "dark_on_light" if primary_dark_on_light else "light_on_dark"
    )
    return detections, metadata


# ── Background measurement worker ──────────────────────────────────────────

class MeasurementWorker(QObject):
    """
    Measures all band ROIs in a background thread using pure-Python
    Pillow + numpy, replicating ImageJ 8-bit measurement behavior.
    """

    progress = Signal(str)      # status text
    finished = Signal(list)     # list of measurement dicts: {lane, Area, Mean, Min, Max, IntDen, RawIntDen}
    error = Signal(str)         # error message

    def __init__(
        self,
        image_path: str,
        band_rois: list,
        image_transform: dict | None = None,
        image_pixels=None,
    ) -> None:
        super().__init__()
        self.image_path = image_path
        self.band_rois = band_rois
        self.image_transform = dict(image_transform) if isinstance(image_transform, dict) else None
        self.image_pixels = image_pixels.copy() if image_pixels is not None else None

    def run(self) -> None:
        from core.measure import measure_all_lanes, measure_all_lanes_in_array
        self.progress.emit(f"Measuring {len(self.band_rois)} lane(s)…")
        try:
            if self.image_pixels is not None:
                results = measure_all_lanes_in_array(self.image_pixels, self.band_rois)
            else:
                results = measure_all_lanes(
                    self.image_path,
                    self.band_rois,
                    image_transform=self.image_transform,
                )
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


class _PanelSelectionCheckBox(QCheckBox):
    """Image selector that adds a small red tick to the existing checked style."""

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self.isChecked():
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor("#D74A43"), 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        left = (self.width() - 14.0) / 2.0
        top = (self.height() - 14.0) / 2.0
        painter.drawLine(QPointF(left + 3.2, top + 7.2), QPointF(left + 5.8, top + 9.8))
        painter.drawLine(QPointF(left + 5.8, top + 9.8), QPointF(left + 10.8, top + 4.2))
        painter.end()


class _ImagePanelWidget(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("image_panel")
        self.setMinimumSize(180, 150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("""
            QFrame#image_panel {
                background-color: #EDF3F7;
                border: 1px solid #C6D6DF;
                border-radius: 8px;
            }
            QCheckBox#panel_select_checkbox {
                background: transparent;
                min-width: 14px;
                max-width: 14px;
                min-height: 14px;
                max-height: 14px;
                margin: 0px;
                padding: 0px;
            }
            QCheckBox#panel_select_checkbox::indicator {
                width: 12px;
                height: 12px;
                border-radius: 2px;
                border: 1px solid #9CB0BC;
                background: #FFFFFF;
            }
            QCheckBox#panel_select_checkbox::indicator:checked {
                border: 1px solid #7DA897;
                background: #D4EDE4;
            }
            QLabel#panel_filename_label {
                background: transparent;
                color: #4A6070;
                font-size: 10px;
            }
            QPushButton#panel_remove_btn {
                background-color: rgba(245, 248, 250, 225);
                border: 1px solid #A7B7C1;
                border-radius: 7px;
                color: #738694;
                min-width: 16px;
                max-width: 16px;
                min-height: 16px;
                max-height: 16px;
                padding: 0px;
                font-size: 10px;
            }
            QPushButton#panel_remove_btn:hover {
                color: #C0504A;
                border-color: #B8908C;
            }
            QPushButton#panel_transform_btn {
                background-color: rgba(245, 248, 250, 225);
                border: 1px solid #9FB3BE;
                border-radius: 7px;
                color: #405967;
                min-width: 22px;
                max-width: 22px;
                min-height: 18px;
                max-height: 18px;
                padding: 0px;
                font-size: 13px;
                font-weight: 700;
            }
            QPushButton#panel_transform_btn:hover {
                background-color: #D4EDE4;
                border-color: #7DA897;
                color: #2A5E48;
            }
            QToolButton#panel_rotate_btn {
                background-color: rgba(245, 248, 250, 225);
                border: 1px solid #9FB3BE;
                border-radius: 7px;
                color: #405967;
                min-width: 22px;
                max-width: 22px;
                min-height: 18px;
                max-height: 18px;
                padding: 0px;
                font-size: 13px;
                font-weight: 700;
            }
            QToolButton#panel_rotate_btn:hover {
                background-color: #D4EDE4;
                border-color: #7DA897;
                color: #2A5E48;
            }
            QToolButton#panel_rotate_btn::menu-indicator {
                image: none;
                width: 0px;
            }
            QToolButton#panel_zoom_btn {
                background-color: rgba(245, 248, 250, 225);
                border: 1px solid #9FB3BE;
                border-radius: 7px;
                min-width: 22px;
                max-width: 22px;
                min-height: 18px;
                max-height: 18px;
                padding: 0px;
            }
            QToolButton#panel_zoom_btn:hover {
                background-color: #D4EDE4;
                border-color: #7DA897;
            }
            QToolButton#panel_zoom_btn:pressed {
                background-color: #C5E2D7;
            }
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(4)
        self.transform_btn = QPushButton("◐")
        self.transform_btn.setObjectName("panel_transform_btn")
        self.transform_btn.setToolTip("Image Transform: Low / High / Gamma")
        top_row.addWidget(self.transform_btn)
        self.rotate_btn = QToolButton()
        self.rotate_btn.setObjectName("panel_rotate_btn")
        self.rotate_btn.setText("↻")
        self.rotate_btn.setToolTip("Rotate Image")
        self.rotate_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.rotate_menu = QMenu(self.rotate_btn)
        self.rotate_menu.setToolTipsVisible(True)
        self.flip_horizontal_action = self.rotate_menu.addAction(
            _image_operation_icon("flip-horizontal"), "Horizontal"
        )
        self.flip_vertical_action = self.rotate_menu.addAction(
            _image_operation_icon("flip-vertical"), "Vertical"
        )
        self.rotate_menu.addSeparator()
        self.rotate_custom_action = self.rotate_menu.addAction(
            _image_operation_icon("custom-rotate"), "Custom"
        )
        self.rotate_custom_action.setToolTip("Press Enter/Return to apply")
        self.rotate_menu.addSeparator()
        self.undo_image_operation_action = self.rotate_menu.addAction(
            _image_operation_icon("undo"), "Undo"
        )
        self.rotate_btn.setMenu(self.rotate_menu)
        top_row.addWidget(self.rotate_btn)
        self.select_checkbox = _PanelSelectionCheckBox()
        self.select_checkbox.setObjectName("panel_select_checkbox")
        self.select_checkbox.setToolTip("Select this image")
        top_row.addWidget(self.select_checkbox)
        self.zoom_out_btn = QToolButton()
        self.zoom_out_btn.setObjectName("panel_zoom_btn")
        self.zoom_out_btn.setIcon(_zoom_icon(zoom_in=False))
        self.zoom_out_btn.setIconSize(QSize(18, 18))
        self.zoom_out_btn.setToolTip("Zoom out image")
        top_row.addWidget(self.zoom_out_btn)
        self.zoom_in_btn = QToolButton()
        self.zoom_in_btn.setObjectName("panel_zoom_btn")
        self.zoom_in_btn.setIcon(_zoom_icon(zoom_in=True))
        self.zoom_in_btn.setIconSize(QSize(18, 18))
        self.zoom_in_btn.setToolTip("Zoom in image")
        top_row.addWidget(self.zoom_in_btn)
        self.filename_label = QLabel("")
        self.filename_label.setObjectName("panel_filename_label")
        self.filename_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.filename_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top_row.addWidget(self.filename_label)
        self.remove_btn = QPushButton("ⓧ")
        self.remove_btn.setObjectName("panel_remove_btn")
        self.remove_btn.setToolTip("Remove image")
        top_row.addWidget(self.remove_btn)
        root.addLayout(top_row)

        self.canvas = ImageCanvas()
        self.canvas.setCursor(Qt.CursorShape.CrossCursor)
        root.addWidget(self.canvas, 1)

    def set_filename(self, name: str) -> None:
        self.filename_label.setText(name)
        self.filename_label.setToolTip(name)


class _UploadedFileListRow(QWidget):
    open_requested = Signal(str)
    remove_requested = Signal(str)

    def __init__(self, source_key: str, name: str, tooltip: str, parent=None) -> None:
        super().__init__(parent)
        self._source_key = source_key
        self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("""
            QWidget {
                background: transparent;
            }
            QPushButton#uploaded_file_remove_btn {
                background-color: #FFFFFF;
                border: 1px solid #AFC1CC;
                border-radius: 7px;
                color: #6D7F8B;
                min-width: 14px;
                max-width: 14px;
                min-height: 14px;
                max-height: 14px;
                padding: 0px;
                font-size: 10px;
                font-weight: 700;
            }
            QPushButton#uploaded_file_remove_btn:hover {
                background-color: #F4DDDD;
                border-color: #C78D8D;
                color: #9D3F3F;
            }
            QLabel#uploaded_file_name_lbl {
                color: #385161;
                font-size: 10px;
                background: transparent;
            }
        """)

        row = QHBoxLayout(self)
        row.setContentsMargins(4, 1, 4, 1)
        row.setSpacing(6)

        remove_btn = QPushButton("×")
        remove_btn.setObjectName("uploaded_file_remove_btn")
        remove_btn.setToolTip(f"Remove {name} from Uploaded Files")
        remove_btn.clicked.connect(lambda _=False: self.remove_requested.emit(self._source_key))
        row.addWidget(remove_btn)

        label = QLabel(name)
        label.setObjectName("uploaded_file_name_lbl")
        label.setToolTip(tooltip)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        row.addWidget(label, 1)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            self.open_requested.emit(self._source_key)
            event.accept()
            return
        super().mouseReleaseEvent(event)


# ── Main window ────────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1200, 800)
        self.setMinimumSize(900, 600)

        self._image_path: str | None = None
        self._lane_rects: list[QRectF] = []      # N subdivided lane ROIs
        self._band_roi: QRectF | None = None     # user-drawn band window
        self._auto_detections: list[dict] = []
        self._active_slot_index: int | None = None
        self._slot_states: list[dict] = [
            {
                "path": None,
                "selected": False,
                "lane_rects": [],
                "band_roi": None,
                "auto_detections": [],
                "image_operation_history": [],
            }
            for _ in range(_MAX_IMAGE_PANELS)
        ]
        self._current_mode = "manual"
        self._persistence = AppPersistence()
        self._persistence.update_config()
        saved_language = self._persistence.read_config().get("ui", {}).get("language", LANG_EN)
        self._language = saved_language if saved_language in {LANG_EN, LANG_ZH_CN} else LANG_EN

        # Worker thread references (kept to avoid GC)
        self._worker: MeasurementWorker | None = None
        self._worker_thread: QThread | None = None
        self._upload_worker: UploadConversionWorker | None = None
        self._upload_thread: QThread | None = None
        self._pending_upload: dict[str, list[Path]] | None = None
        self._direct_exporter = None
        self._supported_upload_exts: set[str] = {".scn", ".sscn", ".mscn", ".smscn"}
        self._direct_tiff_exts: set[str] = {".tif", ".tiff"}
        self._conversion_cache_dir = Path(tempfile.mkdtemp(prefix="wb_analyzer_tiff_cache_"))
        self._converted_documents: dict[str, dict] = {}
        self._main_splitter: QSplitter | None = None
        self._viewer_splitters: list[QSplitter] = []
        self._files_panel_collapsed = False
        self._files_panel_last_width = 260
        self._files_panel_min_width = 190
        self._files_panel_max_width = 460
        self._files_panel_collapsed_width = 40
        self._figure_windows: list[ColumnTableWindow] = []
        self._embedded_column_table: ColumnTableWindow | None = None
        self._figure_mode_window = None
        self._workspace_focus_target: str = "image"
        self._home_mode_active = True
        self._analyze_return_shortcuts: list[QShortcut] = []
        self._image_transform_dialog: ImageTransformDialog | None = None

        self._build_ui()
        self._tutorial_controller = TutorialController(self)
        self._apply_persisted_ui_preferences()
        self._connect_signals()
        self._register_shortcuts()
        self._set_language(self._language, persist=False)
        self.canvas.set_interaction_mode(self._current_mode)

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # Toolbar (UI layout only; actions/logic stay unchanged)
        tb = QToolBar("Main", self)
        tb.setMovable(False)
        tb.setFloatable(False)
        tb.setStyleSheet("""
            QToolBar {
                background-color: #ECF2F0;
                border: none;
                spacing: 8px;
                padding: 6px 8px;
            }
            QToolButton#tb_open_btn {
                background-color: #DCE9E2;
                border: 1px solid #B8CCC1;
                border-radius: 8px;
                color: #2F4B3F;
                padding: 7px 13px;
                font-weight: 600;
            }
            QToolButton#tb_open_btn:hover {
                background-color: #D1E2D9;
            }
            QFrame#section1_group {
                background-color: #D3E2DA;
                border: 1px solid #B3C8BC;
                border-radius: 10px;
            }
            QFrame#section2_group {
                background-color: #C7D8CE;
                border: 1px solid #AEBFB5;
                border-radius: 10px;
            }
            QToolButton#section_btn {
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 7px;
                color: #314A3F;
                padding: 5px 10px;
            }
            QToolButton#section_btn:hover {
                background-color: rgba(255, 255, 255, 0.35);
                border-color: #AFC3B7;
            }
            QToolButton#section_btn:disabled {
                color: #7F9588;
            }
            QToolButton#analyze_btn {
                background-color: #B9D5C5;
                border: 1px solid #90B3A0;
                border-radius: 7px;
                color: #274E3B;
                font-weight: 600;
                padding: 5px 12px;
            }
            QToolButton#analyze_btn:hover {
                background-color: #A9C9B7;
            }
            QToolButton#analyze_btn:disabled {
                background-color: #D8E4DE;
                color: #81988D;
                border-color: #C1D0C8;
            }
            QPushButton#figure_generation_btn {
                background-color: #C2D3C8;
                border: 1px solid #9EB3A8;
                border-radius: 7px;
                color: #2C4A3D;
                padding: 5px 12px;
            }
            QPushButton#figure_generation_btn:hover {
                background-color: #B4C8BB;
            }
            QPushButton#wb_plot_generation_btn {
                background-color: #C8C2D3;
                border: 1px solid #A89EB3;
                border-radius: 7px;
                color: #3A2C4A;
                padding: 5px 12px;
            }
            QPushButton#wb_plot_generation_btn:hover {
                background-color: #BBB4C8;
            }
            QComboBox#language_selector {
                background-color: #F5F8FA;
                border: 1px solid #B6C8D5;
                border-radius: 8px;
                color: #345060;
                padding: 5px 8px;
                min-width: 92px;
                font-weight: 600;
            }
            QPushButton#tutorial_btn {
                background-color: #FFF7E3;
                border: 1px solid #D7B86A;
                border-radius: 8px;
                color: #655020;
                padding: 6px 11px;
                font-weight: 600;
            }
            QPushButton#tutorial_btn:hover {
                background-color: #F8EBC8;
                border-color: #C99E39;
            }
        """)
        self.addToolBar(tb)

        self._act_open = QAction("Import Images", self)
        self._act_open.setIcon(_folder_open_icon())
        self._act_open.setShortcut(QKeySequence.StandardKey.Open)

        self._act_image_transform = QAction("Image Transform", self)

        self._act_analyze = QAction("Analyze", self)
        self._act_analyze.setShortcut(QKeySequence("Ctrl+Return"))
        self._act_analyze.setEnabled(False)

        def _make_action_button(action: QAction, name: str) -> QToolButton:
            btn = QToolButton()
            btn.setDefaultAction(action)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            btn.setObjectName(name)
            btn.setAutoRaise(False)
            return btn

        open_btn = _make_action_button(self._act_open, "tb_open_btn")
        self._open_toolbar_btn = open_btn
        open_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        open_btn.setIconSize(QSize(22, 22))
        self._open_toolbar_action = tb.addWidget(open_btn)

        gap1 = QWidget()
        gap1.setFixedWidth(8)
        tb.addWidget(gap1)

        # Analyze belongs to the densitometry workspace.  Keep its toolbar
        # group hidden until that workspace is selected.
        self._analyze_toolbar_group = QFrame()
        self._analyze_toolbar_group.setObjectName("section1_group")
        analyze_layout = QHBoxLayout(self._analyze_toolbar_group)
        analyze_layout.setContentsMargins(8, 4, 8, 4)
        analyze_layout.setSpacing(6)
        self._analyze_toolbar_btn = _make_action_button(
            self._act_analyze,
            "analyze_btn",
        )
        analyze_layout.addWidget(self._analyze_toolbar_btn)
        self._analyze_toolbar_group.setVisible(False)
        self._analyze_toolbar_action = tb.addWidget(self._analyze_toolbar_group)
        self._analyze_toolbar_action.setVisible(False)

        gap2 = QWidget()
        gap2.setFixedWidth(8)
        gap2.setVisible(False)
        self._analyze_toolbar_gap = gap2
        self._analyze_toolbar_gap_action = tb.addWidget(gap2)
        self._analyze_toolbar_gap_action.setVisible(False)

        # Preserve the existing workspace controls.  They are hidden only on Home.
        self._workspace_toolbar_group = QFrame()
        self._workspace_toolbar_group.setObjectName("section2_group")
        workspace_layout = QHBoxLayout(self._workspace_toolbar_group)
        workspace_layout.setContentsMargins(8, 4, 8, 4)
        workspace_layout.setSpacing(6)
        self._figure_generation_btn = QPushButton("Densitometry Figure Generation")
        self._figure_generation_btn.setObjectName("figure_generation_btn")
        self._figure_generation_btn.clicked.connect(self._on_figure_generation_clicked)
        workspace_layout.addWidget(self._figure_generation_btn)
        self._wb_plot_generation_btn = QPushButton("WB Plot Figure Generation")
        self._wb_plot_generation_btn.setObjectName("wb_plot_generation_btn")
        self._wb_plot_generation_btn.clicked.connect(self._on_wb_plot_mode)
        workspace_layout.addWidget(self._wb_plot_generation_btn)
        self._workspace_toolbar_action = tb.addWidget(self._workspace_toolbar_group)
        self._workspace_toolbar_action.setVisible(True)

        self._tutorial_btn = QPushButton("New User Tutorial")
        self._tutorial_btn.setObjectName("tutorial_btn")
        self._tutorial_toolbar_action = tb.addWidget(self._tutorial_btn)

        # Keep language selection anchored at the far right.
        stretch = QWidget()
        stretch.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(stretch)

        self._language_combo = QComboBox()
        self._language_combo.setObjectName("language_selector")
        self._language_combo.addItem("English", LANG_EN)
        self._language_combo.addItem("中文", LANG_ZH_CN)
        self._language_combo.setToolTip("Language")
        self._language_toolbar_action = tb.addWidget(self._language_combo)

        # Central splitter: files rail + ROI settings + image/figure workspace.
        # The first two panels share the left-side interaction area: expanding
        # Uploaded Files hides ROI Settings; collapsing it reveals ROI Settings.
        self._main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._main_splitter.setChildrenCollapsible(False)

        self._files_panel = QFrame()
        self._files_panel.setObjectName("uploaded_files_panel")
        self._files_panel.setStyleSheet("""
            QFrame#uploaded_files_panel {
                background-color: #EFF4F8;
                border-right: 1px solid #D4E0E8;
            }
            QLabel#files_panel_title {
                color: #5D7180;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton#files_panel_toggle {
                background-color: #E2ECF3;
                border: 1px solid #C2D1DC;
                border-radius: 6px;
                color: #5A7080;
                min-width: 18px;
                max-width: 18px;
                min-height: 18px;
                max-height: 18px;
                padding: 0px;
                font-size: 11px;
                font-weight: 700;
            }
            QPushButton#files_panel_toggle:hover {
                background-color: #D6E3ED;
            }
            QListWidget#uploaded_files_list {
                background-color: #F7FAFC;
                border: 1px solid #D0DEE7;
                border-radius: 6px;
                color: #385161;
                padding: 2px;
            }
            QListWidget#uploaded_files_list::item {
                padding: 4px 6px;
            }
            QListWidget#uploaded_files_list::item:selected {
                background-color: #D4EDE4;
                color: #2A5E48;
            }
            QPushButton#export_all_tiffs_btn {
                background-color: #E6EEF3;
                border: 1px solid #BACAD5;
                border-radius: 6px;
                color: #4B6473;
                padding: 5px 8px;
            }
            QPushButton#export_all_tiffs_btn:hover {
                background-color: #DCE7EE;
            }
        """)
        files_layout = QVBoxLayout(self._files_panel)
        files_layout.setContentsMargins(8, 8, 8, 8)
        files_layout.setSpacing(6)
        files_header = QHBoxLayout()
        files_header.setContentsMargins(0, 0, 0, 0)
        files_header.setSpacing(6)
        self._files_toggle_btn = QPushButton("◀")
        self._files_toggle_btn.setObjectName("files_panel_toggle")
        self._files_toggle_btn.setToolTip("Collapse Uploaded Files panel")
        files_header.addWidget(self._files_toggle_btn)
        self._files_title = QLabel("Uploaded Files")
        self._files_title.setObjectName("files_panel_title")
        files_header.addWidget(self._files_title)
        files_header.addStretch(1)
        files_layout.addLayout(files_header)
        self._files_list = QListWidget()
        self._files_list.setObjectName("uploaded_files_list")
        files_layout.addWidget(self._files_list, 1)
        self._export_all_tiffs_btn = QPushButton("Export All TIFFs")
        self._export_all_tiffs_btn.setObjectName("export_all_tiffs_btn")
        self._export_all_tiffs_btn.setEnabled(False)
        files_layout.addWidget(self._export_all_tiffs_btn)
        self._main_splitter.addWidget(self._files_panel)

        self._workspace_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._workspace_splitter.setChildrenCollapsible(False)
        self._workspace_splitter.setHandleWidth(7)
        self._workspace_splitter.setOpaqueResize(True)

        self._viewer_container = QWidget()
        self._viewer_container.setObjectName("workspace_viewer_panel")
        self._viewer_layout = QGridLayout(self._viewer_container)
        self._viewer_layout.setContentsMargins(0, 0, 0, 0)
        self._viewer_layout.setSpacing(6)
        self._image_panels = [_ImagePanelWidget() for _ in range(_MAX_IMAGE_PANELS)]
        for panel in self._image_panels:
            panel.setVisible(False)
        self._workspace_splitter.addWidget(self._viewer_container)
        # Keep `self.canvas` alias for backward compatibility with existing methods.
        self.canvas = self._image_panels[0].canvas

        self._figure_workspace = QWidget()
        self._figure_workspace.setMinimumWidth(360)
        self._figure_workspace.setStyleSheet(
            "QWidget#figure_workspace {"
            "background-color: #F3F7FA;"
            "border-left: 1px solid #D0DEE7;"
            "}"
        )
        self._figure_workspace.setObjectName("figure_workspace")
        figure_workspace_layout = QVBoxLayout(self._figure_workspace)
        figure_workspace_layout.setContentsMargins(6, 6, 6, 6)
        figure_workspace_layout.setSpacing(6)

        self._figure_right_splitter = QSplitter(Qt.Orientation.Vertical)
        self._figure_right_splitter.setChildrenCollapsible(False)
        self._figure_right_splitter.setHandleWidth(7)
        self._figure_right_splitter.setOpaqueResize(True)

        self._figure_table_host = QWidget()
        self._figure_table_host.setObjectName("workspace_table_panel")
        self._figure_table_host_layout = QVBoxLayout(self._figure_table_host)
        self._figure_table_host_layout.setContentsMargins(0, 0, 0, 0)
        self._figure_table_host_layout.setSpacing(0)
        self._figure_table_placeholder = QLabel("Figure Generation table will appear here.")
        self._figure_table_placeholder.setStyleSheet("color: #6E8494; font-size: 11px; padding: 10px;")
        self._figure_table_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._figure_table_host_layout.addWidget(self._figure_table_placeholder)

        self._figure_preview_host = QWidget()
        self._figure_preview_host.setObjectName("workspace_figure_panel")
        self._figure_preview_host_layout = QVBoxLayout(self._figure_preview_host)
        self._figure_preview_host_layout.setContentsMargins(0, 0, 0, 0)
        self._figure_preview_host_layout.setSpacing(0)
        self._figure_preview_placeholder = QLabel("Generated figure preview will appear here.")
        self._figure_preview_placeholder.setStyleSheet("color: #6E8494; font-size: 11px; padding: 10px;")
        self._figure_preview_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._figure_preview_host_layout.addWidget(self._figure_preview_placeholder)

        self._figure_right_splitter.addWidget(self._figure_table_host)
        self._figure_right_splitter.addWidget(self._figure_preview_host)
        self._figure_right_splitter.setStretchFactor(0, 1)
        self._figure_right_splitter.setStretchFactor(1, 1)
        self._figure_right_splitter.setSizes([420, 280])

        self._figure_workspace_stack = QStackedWidget()
        self._densitometry_figure_page = QWidget()
        densitometry_layout = QVBoxLayout(self._densitometry_figure_page)
        densitometry_layout.setContentsMargins(0, 0, 0, 0)
        densitometry_layout.setSpacing(0)
        densitometry_layout.addWidget(self._figure_right_splitter, 1)

        self._wb_plot_workspace_host = QWidget()
        self._wb_plot_workspace_layout = QVBoxLayout(self._wb_plot_workspace_host)
        self._wb_plot_workspace_layout.setContentsMargins(0, 0, 0, 0)
        self._wb_plot_workspace_layout.setSpacing(0)
        self._wb_plot_placeholder = QLabel("WB Plot Figure Generation will appear here.")
        self._wb_plot_placeholder.setStyleSheet("color: #6E8494; font-size: 11px; padding: 10px;")
        self._wb_plot_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._wb_plot_workspace_layout.addWidget(self._wb_plot_placeholder)

        self._figure_workspace_stack.addWidget(self._densitometry_figure_page)
        self._figure_workspace_stack.addWidget(self._wb_plot_workspace_host)
        figure_workspace_layout.addWidget(self._figure_workspace_stack, 1)

        self._figure_workspace.setVisible(False)
        self._workspace_splitter.addWidget(self._figure_workspace)
        self._workspace_splitter.setStretchFactor(0, 3)
        self._workspace_splitter.setStretchFactor(1, 2)
        self._workspace_splitter.setSizes([900, 0])
        self._viewer_container.installEventFilter(self)
        self._figure_table_host.installEventFilter(self)
        self._figure_preview_host.installEventFilter(self)
        self._apply_workspace_focus_styles()
        self._main_splitter.addWidget(self._workspace_splitter)

        self.param_panel = ParamPanel()
        self._param_panel_host = QWidget()
        self._param_panel_layout = QVBoxLayout(self._param_panel_host)
        self._param_panel_layout.setContentsMargins(0, 0, 0, 0)
        self._param_panel_layout.setSpacing(0)
        self._param_panel_layout.addWidget(self.param_panel)
        self._main_splitter.insertWidget(1, self._param_panel_host)
        self._main_splitter.setStretchFactor(0, 0)
        self._main_splitter.setStretchFactor(1, 0)
        self._main_splitter.setStretchFactor(2, 1)
        self._files_panel.setMinimumWidth(self._files_panel_min_width)
        self._files_panel.setMaximumWidth(self._files_panel_max_width)
        self._main_splitter.setSizes([self._files_panel_last_width, 150, 990])

        # ── App shell: Home is the existing blank interactive image canvas.
        self._mode_container = QStackedWidget()
        self._mode_container.addWidget(self._main_splitter)
        self._mode_container.setCurrentWidget(self._main_splitter)
        self.setCentralWidget(self._mode_container)

        # Match the Home reference: collapsed file rail, full blank canvas, and
        # no analysis/figure side panels until a tool is chosen.
        self._set_files_panel_collapsed(True, persist=False)
        self._param_panel_host.setVisible(False)

        # Results dock (bottom)
        self.results_panel = ResultsPanel(
            default_export_dir_provider=self._persistence.default_results_export_dir,
            export_dir_changed=self._persistence.remember_results_export_dir,
        )
        dock = QDockWidget("Results", self)
        self._results_dock = dock
        dock.setWidget(self.results_panel)
        dock.setAllowedAreas(
            Qt.DockWidgetArea.BottomDockWidgetArea | Qt.DockWidgetArea.TopDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)
        dock.setMinimumHeight(180)
        dock.setVisible(False)

        # Status bar
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._refresh_image_panel_layout()
        self._status_bar.showMessage("Choose a tool or import images.")

    def _set_files_panel_collapsed(
        self,
        collapsed: bool,
        *,
        persist: bool = True,
    ) -> None:
        if self._main_splitter is None:
            return

        if collapsed == self._files_panel_collapsed:
            self._sync_param_panel_visibility()
            return

        sizes = self._main_splitter.sizes()
        total = max(sum(sizes), self._main_splitter.width(), 1)

        if collapsed:
            current_width = self._files_panel.width()
            if current_width > self._files_panel_collapsed_width:
                self._files_panel_last_width = max(
                    self._files_panel_min_width,
                    min(current_width, self._files_panel_max_width),
                )
            self._files_title.setVisible(False)
            self._files_list.setVisible(False)
            self._export_all_tiffs_btn.setVisible(False)
            self._files_panel.setMinimumWidth(self._files_panel_collapsed_width)
            self._files_panel.setMaximumWidth(self._files_panel_collapsed_width)
            self._files_toggle_btn.setText("▶")
            self._files_toggle_btn.setToolTip("Expand Uploaded Files panel")
        else:
            self._files_panel.setMinimumWidth(self._files_panel_min_width)
            self._files_panel.setMaximumWidth(self._files_panel_max_width)
            self._files_title.setVisible(True)
            self._files_list.setVisible(True)
            self._export_all_tiffs_btn.setVisible(True)
            restored_width = max(
                self._files_panel_min_width,
                min(self._files_panel_last_width, self._files_panel_max_width),
            )
            self._files_toggle_btn.setText("◀")
            self._files_toggle_btn.setToolTip("Collapse Uploaded Files panel")

        self._files_panel_collapsed = collapsed
        self._sync_param_panel_visibility()
        files_width = (
            self._files_panel_collapsed_width if collapsed else restored_width
        )
        roi_width = self.param_panel.width() if self._param_panel_host.isVisible() else 0
        workspace_width = max(200, total - files_width - roi_width)
        self._main_splitter.setSizes([files_width, roi_width, workspace_width])
        if persist:
            self._persistence.remember_ui_state(files_panel_collapsed=collapsed)

    def _sync_param_panel_visibility(self) -> None:
        """Show ROI Settings only in the collapsed-files densitometry layout."""
        show_roi = (
            self._files_panel_collapsed
            and not self._home_mode_active
            and not self._is_wb_plot_workspace_active()
        )
        self._param_panel_host.setVisible(show_roi)

    def _apply_persisted_ui_preferences(self) -> None:
        config = self._persistence.read_config()
        ui = config.get("ui", {})
        saved_mode = ui.get("mode")
        if isinstance(saved_mode, str):
            self.param_panel.set_mode(saved_mode)
            self._current_mode = self.param_panel.get_mode()
        collapsed = ui.get("files_panel_collapsed")
        if isinstance(collapsed, bool):
            self._set_files_panel_collapsed(collapsed, persist=False)

    def _set_language(self, language: str, *, persist: bool = True) -> None:
        """Apply the display language while keeping analysis data untouched."""
        if language not in {LANG_EN, LANG_ZH_CN}:
            language = LANG_EN
        self._language = language
        combo_index = self._language_combo.findData(language)
        if combo_index >= 0 and self._language_combo.currentIndex() != combo_index:
            self._language_combo.blockSignals(True)
            self._language_combo.setCurrentIndex(combo_index)
            self._language_combo.blockSignals(False)

        self._language_combo.setToolTip(tr("Language", language))
        self._act_open.setText(tr("Import Images", language))
        self._act_image_transform.setText(tr("Image Transform", language))
        self._act_analyze.setText(tr("Analyze", language))
        self._figure_generation_btn.setText(
            tr("Densitometry Figure Generation", language)
        )
        self._wb_plot_generation_btn.setText(
            tr("WB Plot Figure Generation", language)
        )
        self._tutorial_btn.setText(tr("New User Tutorial", language))
        self._files_title.setText(tr("Uploaded Files", language))
        self._export_all_tiffs_btn.setText(tr("Export All TIFFs", language))
        if self._embedded_column_table is None:
            self._figure_table_placeholder.setText(tr("Figure Generation table will appear here.", language))
            self._figure_preview_placeholder.setText(tr("Generated figure preview will appear here.", language))
        self._wb_plot_placeholder.setText(tr("WB Plot Figure Generation will appear here.", language))
        self._refresh_language_targets(language)
        self._tutorial_controller.refresh_language()

        self._files_toggle_btn.setToolTip(tr(
            "Expand Uploaded Files panel"
            if self._files_panel_collapsed
            else "Collapse Uploaded Files panel",
            language,
        ))

        for panel in self._image_panels:
            panel.transform_btn.setToolTip(tr("Image Transform: Low / High / Gamma", language))
            panel.rotate_btn.setToolTip(tr("Rotate Image", language))
            panel.select_checkbox.setToolTip(tr("Select this image", language))
            panel.zoom_out_btn.setToolTip(tr("Zoom out image", language))
            panel.zoom_in_btn.setToolTip(tr("Zoom in image", language))
            panel.remove_btn.setToolTip(tr("Remove image", language))
            panel.rotate_custom_action.setText(tr("Custom", language))
            panel.rotate_custom_action.setToolTip(tr("Press Enter/Return to apply", language))
            panel.flip_vertical_action.setText(tr("Vertical", language))
            panel.flip_horizontal_action.setText(tr("Horizontal", language))
            panel.undo_image_operation_action.setText(tr("Undo", language))
        if self._home_mode_active:
            self._status_bar.showMessage(
                tr("Choose a tool or import images.", language)
            )
        elif self._is_wb_plot_workspace_active():
            self._status_bar.showMessage(
                tr("Opened WB Figure Layout workspace.", language)
            )
        elif not self._figure_workspace.isHidden():
            self._status_bar.showMessage(
                tr("Returned to Data Graphs workspace.", language)
            )
        else:
            self._status_bar.showMessage(
                tr("Opened Densitometry workspace.", language)
            )
        if persist:
            self._persistence.remember_ui_state(language=language)

    def _refresh_language_targets(self, language: str) -> None:
        """Refresh every live tool independently so one failure cannot block others."""
        # The visible WB Figure workspace is intentionally first. It is the
        # most time-sensitive target when the toolbar selector changes.
        candidates = [
            self._figure_mode_window,
            self._embedded_column_table,
            self.param_panel,
            self.results_panel,
            self._image_transform_dialog,
            *self._figure_windows,
        ]
        candidates.extend(
            widget
            for widget in QApplication.topLevelWidgets()
            if widget is not self
        )

        refreshed: set[int] = set()
        for target in candidates:
            if target is None or id(target) in refreshed:
                continue
            setter = getattr(target, "set_language", None)
            if not callable(setter):
                continue
            refreshed.add(id(target))
            try:
                setter(language)
            except Exception:
                log.exception(
                    "Failed to refresh %s to language %s",
                    type(target).__name__,
                    language,
                )

    def _on_language_changed(self, index: int) -> None:
        language = self._language_combo.itemData(index)
        self._set_language(str(language))

    def _show_tutorial_selector(self) -> None:
        chooser = TutorialModeDialog(self, language=self._language)
        if chooser.exec() != QDialog.DialogCode.Accepted:
            return
        if chooser.selection is not None:
            self._tutorial_controller.start(chooser.selection)

    @staticmethod
    def _tutorial_asset_paths() -> dict[str, Path]:
        if getattr(sys, "frozen", False):
            root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
        else:
            root = Path(__file__).resolve().parents[1]
        tutorial_dir = root / "assets" / "tutorial"
        return {
            "loading": tutorial_dir / "tutorial_loading_control.tif",
            "target": tutorial_dir / "tutorial_target_protein.tif",
        }

    def _select_tutorial_image(self, role: str) -> bool:
        path = self._tutorial_asset_paths().get(role)
        if path is None:
            return False
        resolved = path.expanduser().resolve()
        for index, state in enumerate(self._slot_states):
            current = state.get("path")
            if current and Path(current).expanduser().resolve() == resolved:
                self._set_active_slot_from_interaction(index)
                return True
        return False

    def _load_tutorial_images(self) -> bool:
        """Load bundled examples without changing the normal Import workflow."""
        assets = self._tutorial_asset_paths()
        missing_files = [path for path in assets.values() if not path.is_file()]
        if missing_files:
            QMessageBox.critical(
                self,
                tr("Tutorial Images Missing", self._language),
                tr(
                    "The built-in tutorial images could not be found. Please reinstall the application.",
                    self._language,
                ),
            )
            return False

        loaded_paths = {
            str(Path(state["path"]).expanduser().resolve())
            for state in self._slot_states
            if state.get("path")
        }
        required_new_slots = sum(
            str(path.resolve()) not in loaded_paths for path in assets.values()
        )
        if required_new_slots > self._available_image_slot_count():
            QMessageBox.information(
                self,
                tr("Tutorial Needs Two Image Windows", self._language),
                tr(
                    "The tutorial needs two free image windows. Remove enough currently open images, then click Import Images again.",
                    self._language,
                ),
            )
            return False

        ordered_paths = [assets["loading"], assets["target"]]
        self._complete_upload(
            results=[],
            selected_paths=ordered_paths,
            direct_tiffs=ordered_paths,
            unsupported=[],
        )
        tutorial_paths = {str(path.resolve()) for path in ordered_paths}
        now_loaded = {
            str(Path(state["path"]).expanduser().resolve())
            for state in self._slot_states
            if state.get("path")
        }
        if not tutorial_paths.issubset(now_loaded):
            return False
        self._select_tutorial_image("loading")
        self._status_bar.showMessage(
            tr(
                "Loaded the built-in Loading Control and Target Protein tutorial images.",
                self._language,
            ),
            5000,
        )
        return True

    def _toggle_files_panel(self) -> None:
        self._set_files_panel_collapsed(not self._files_panel_collapsed)

    def _on_main_splitter_moved(self, _pos: int, _index: int) -> None:
        if self._files_panel_collapsed:
            return
        current_width = self._files_panel.width()
        if current_width >= self._files_panel_min_width:
            self._files_panel_last_width = max(
                self._files_panel_min_width,
                min(current_width, self._files_panel_max_width),
            )

    @staticmethod
    def _clear_container_layout(container: QWidget) -> None:
        layout = container.layout()
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _restore_param_panel(self) -> None:
        self.param_panel.set_wb_plot_simplified(False)
        self._set_wb_plot_roi_only(False)
        if self.param_panel.parentWidget() is not self._param_panel_host:
            self.param_panel.setParent(self._param_panel_host)
            self._param_panel_layout.addWidget(self.param_panel)
        self._sync_param_panel_visibility()

    def _dock_param_panel_in_wb_plot(self) -> None:
        if self._figure_mode_window is None:
            return
        self.param_panel.set_wb_plot_simplified(True)
        self._set_wb_plot_roi_only(True)
        self._figure_mode_window.set_context_controls_widget(None)
        self._param_panel_host.setVisible(False)

    def _set_wb_plot_roi_only(self, enabled: bool) -> None:
        for panel in self._image_panels:
            panel.canvas.set_wb_plot_roi_only(enabled)

    def _set_analyze_toolbar_visible(self, visible: bool) -> None:
        """Show Analyze only while the densitometry workspace is selected."""
        self._analyze_toolbar_action.setVisible(visible)
        self._analyze_toolbar_gap_action.setVisible(visible)
        self._analyze_toolbar_group.setVisible(visible)
        self._analyze_toolbar_gap.setVisible(visible)

    def _set_import_toolbar_visible(self, visible: bool) -> None:
        """Expose image import only inside a selected workspace."""
        self._open_toolbar_action.setVisible(visible)

    def _show_home_page(self) -> None:
        """Restore the blank-canvas Home shell without clearing loaded images."""
        self._home_mode_active = True
        self._mode_container.setCurrentWidget(self._main_splitter)
        if self._figure_mode_window is not None:
            self._figure_mode_window.set_context_controls_widget(None)
        self._restore_param_panel()
        self._figure_workspace.setVisible(False)
        self._sync_param_panel_visibility()
        self._results_dock.setVisible(False)
        self._set_import_toolbar_visible(True)
        self._set_analyze_toolbar_visible(False)
        self._workspace_toolbar_action.setVisible(True)
        self._set_workspace_focus_target("image")
        self._status_bar.showMessage(
            tr("Choose a tool or import images.", self._language)
        )

    def _restore_initial_home_after_tutorial(self) -> None:
        """Discard tutorial artifacts and reproduce the fresh-launch Home UI."""
        self._active_slot_index = None
        self._image_path = None
        self._lane_rects.clear()
        self._band_roi = None
        self._auto_detections = []
        for index, panel in enumerate(self._image_panels):
            self._slot_states[index] = {
                "path": None,
                "selected": False,
                "lane_rects": [],
                "band_roi": None,
                "auto_detections": [],
                "image_operation_history": [],
            }
            self._reset_canvas_widget(panel.canvas)
            panel.set_filename("")
        self.canvas = self._image_panels[0].canvas
        self._refresh_image_panel_layout()
        self._converted_documents.clear()
        self._refresh_uploaded_files_list()
        self.results_panel.clear()

        if self._embedded_column_table is not None:
            self._embedded_column_table.set_figure_preview_host(None)
            self._embedded_column_table.deleteLater()
        self._embedded_column_table = None
        self._figure_windows.clear()
        self._clear_container_layout(self._figure_table_host)
        self._clear_container_layout(self._figure_preview_host)
        self._figure_table_placeholder = QLabel(
            tr("Figure Generation table will appear here.", self._language)
        )
        self._figure_table_placeholder.setStyleSheet(
            "color: #6E8494; font-size: 11px; padding: 10px;"
        )
        self._figure_table_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._figure_table_host_layout.addWidget(self._figure_table_placeholder)
        self._figure_preview_placeholder = QLabel(
            tr("Generated figure preview will appear here.", self._language)
        )
        self._figure_preview_placeholder.setStyleSheet(
            "color: #6E8494; font-size: 11px; padding: 10px;"
        )
        self._figure_preview_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._figure_preview_host_layout.addWidget(self._figure_preview_placeholder)

        if self._figure_mode_window is not None:
            self._figure_mode_window.set_context_controls_widget(None)
            self._figure_mode_window.setParent(None)
            self._figure_mode_window.deleteLater()
        self._figure_mode_window = None
        self._clear_container_layout(self._wb_plot_workspace_host)
        self._wb_plot_placeholder = QLabel(
            tr("WB Plot Figure Generation will appear here.", self._language)
        )
        self._wb_plot_placeholder.setStyleSheet(
            "color: #6E8494; font-size: 11px; padding: 10px;"
        )
        self._wb_plot_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._wb_plot_workspace_layout.addWidget(self._wb_plot_placeholder)

        self._show_home_page()
        self._set_files_panel_collapsed(True, persist=False)
        self._status_bar.showMessage(
            tr("Choose a tool or import images.", self._language)
        )

    def _on_densitometry_mode(self) -> None:
        """Open the analysis-first view of the existing densitometry workspace."""
        self._home_mode_active = False
        self._mode_container.setCurrentWidget(self._main_splitter)
        self._set_import_toolbar_visible(True)
        if self._figure_mode_window is not None:
            self._figure_mode_window.set_context_controls_widget(None)
        self._figure_workspace.setVisible(False)
        self._restore_param_panel()
        self._results_dock.setVisible(True)
        self._set_analyze_toolbar_visible(True)
        self._act_analyze.setEnabled(self._band_roi is not None)
        self._workspace_toolbar_action.setVisible(True)
        self._set_workspace_focus_target("image")
        self._status_bar.showMessage(
            tr("Opened Densitometry workspace.", self._language),
            3000,
        )

    def _show_figure_workspace(self, page: str = "densitometry") -> None:
        self._figure_workspace.setVisible(True)
        if page == "wb_plot":
            self._figure_workspace_stack.setCurrentWidget(self._wb_plot_workspace_host)
            self._results_dock.setVisible(False)
            self._dock_param_panel_in_wb_plot()
        else:
            self._figure_workspace_stack.setCurrentWidget(self._densitometry_figure_page)
            if self._figure_mode_window is not None:
                self._figure_mode_window.set_context_controls_widget(None)
            self._restore_param_panel()
            self._results_dock.setVisible(True)
            self._act_analyze.setEnabled(self._band_roi is not None)
        total = max(sum(self._workspace_splitter.sizes()), 1)
        left = max(420, int(total * 0.50 if page == "wb_plot" else total * 0.58))
        right = max(360, total - left)
        self._workspace_splitter.setSizes([left, right])
        if page != "wb_plot":
            self._figure_right_splitter.setSizes([430, 290])
        self._apply_workspace_focus_styles()

    def _on_workspace_panel_focus_requested(self, panel: str) -> None:
        self._set_workspace_focus_target(panel)

    def _set_workspace_focus_target(self, panel: str) -> None:
        if panel not in {"image", "table", "figure"}:
            return
        self._workspace_focus_target = panel
        self._apply_workspace_focus_styles()

    def _apply_workspace_focus_styles(self) -> None:
        def _style(active: bool, bg: str) -> str:
            border = "#8CB4A2" if active else "#D0DEE7"
            return (
                f"QWidget {{ background-color: {bg}; border: 2px solid {border}; border-radius: 6px; }}"
            )

        self._viewer_container.setStyleSheet(_style(self._workspace_focus_target == "image", "#FFFFFF"))
        self._figure_table_host.setStyleSheet(_style(self._workspace_focus_target == "table", "#F7FAFC"))
        self._figure_preview_host.setStyleSheet(_style(self._workspace_focus_target == "figure", "#FFFFFF"))

    def eventFilter(self, watched: QObject, event) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress:
            if watched is self._viewer_container:
                self._set_workspace_focus_target("image")
            elif watched is self._figure_table_host:
                self._set_workspace_focus_target("table")
            elif watched is self._figure_preview_host:
                self._set_workspace_focus_target("figure")
        return super().eventFilter(watched, event)

    def _mount_column_table_in_workspace(self, table_window: ColumnTableWindow) -> None:
        if self._embedded_column_table is not None and self._embedded_column_table is not table_window:
            self._embedded_column_table.deleteLater()
        content = table_window.take_content_widget()
        if content is None:
            raise RuntimeError("Figure table content is unavailable.")

        self._clear_container_layout(self._figure_table_host)
        self._clear_container_layout(self._figure_preview_host)

        content.setParent(self._figure_table_host)
        self._figure_table_host_layout.addWidget(content)

        self._embedded_preview_placeholder = QLabel(
            tr("Generated figure preview will appear here.", self._language)
        )
        self._embedded_preview_placeholder.setStyleSheet("color: #6E8494; font-size: 11px; padding: 10px;")
        self._embedded_preview_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._figure_preview_host_layout.addWidget(self._embedded_preview_placeholder)

        table_window.setParent(self)
        table_window.set_figure_preview_host(self._figure_preview_host)
        table_window.panelFocusRequested.connect(self._on_workspace_panel_focus_requested)
        table_window.activeTargetRowChanged.connect(
            self._tutorial_controller.notify_table_row_selected
        )
        table_window.tutorialEvent.connect(
            self._tutorial_controller.notify_column_table_event
        )
        self._embedded_column_table = table_window
        table_window.set_language(self._language)
        self._figure_windows = [table_window]
        self._show_figure_workspace("densitometry")
        self._set_workspace_focus_target("table")

    def _on_figure_generation_clicked(self) -> None:
        self._home_mode_active = False
        self._set_import_toolbar_visible(True)
        self._set_analyze_toolbar_visible(True)
        self._workspace_toolbar_action.setVisible(True)
        self._mode_container.setCurrentWidget(self._main_splitter)
        self._show_figure_workspace("densitometry")
        if self._embedded_column_table is not None:
            self._set_workspace_focus_target("table")
            self._status_bar.showMessage(
                tr("Returned to Data Graphs workspace.", self._language),
                3000,
            )
            return

        tutorial_direct_column = (
            self._tutorial_controller.active
            and self._tutorial_controller.mode == "densitometry"
            and self._tutorial_controller.current_step_key == "create_column_table"
        )
        if tutorial_direct_column:
            selection = "column"
        else:
            chooser = FigureTypeDialog(self)
            chooser.set_language(self._language)
            if chooser.exec() != QDialog.DialogCode.Accepted:
                return
            selection = chooser.selection
        if selection == "grouped":
            QMessageBox.information(
                self,
                "Figure Generation",
                "Grouped workflow is not implemented yet.",
            )
            return
        if selection != "column":
            return

        tutorial_column_defaults = (
            self._tutorial_controller.active
            and self._tutorial_controller.mode == "densitometry"
        )
        setup = ColumnSetupDialog(
            self,
            default_samples=3,
            default_replicates=2 if tutorial_column_defaults else 3,
        )
        setup.set_language(self._language)
        if tutorial_direct_column:
            self._tutorial_controller.notify_column_setup_opened()
        if setup.exec() != QDialog.DialogCode.Accepted:
            if tutorial_direct_column:
                self._tutorial_controller.notify_column_setup_cancelled()
            return

        setup_values = setup.get_input()
        if setup_values.samples <= 0 or setup_values.replicates <= 0:
            QMessageBox.warning(
                self,
                "Invalid Input",
                "Number of samples and replicates must be positive integers.",
            )
            return

        table_window = ColumnTableWindow(
            samples=setup_values.samples,
            replicates=setup_values.replicates,
            parent=self,
        )
        table_window.set_language(self._language)
        self._mount_column_table_in_workspace(table_window)
        self._status_bar.showMessage(
            f"Opened integrated Column table workspace: {setup_values.samples} sample(s) × {setup_values.replicates} replicate(s).",
            4000,
        )
        self._tutorial_controller.notify_column_table_ready()

    def _loaded_slot_indices(self) -> list[int]:
        return [idx for idx, state in enumerate(self._slot_states) if state["path"]]

    def _available_image_slot_count(self) -> int:
        return sum(1 for state in self._slot_states if not state["path"])

    def _make_image_panel_splitter(self, orientation: Qt.Orientation) -> QSplitter:
        splitter = QSplitter(orientation)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(8)
        splitter.setOpaqueResize(True)
        splitter.setStyleSheet("""
            QSplitter::handle {
                background-color: #D6E3ED;
                border: 1px solid #C4D4DF;
                border-radius: 3px;
            }
            QSplitter::handle:hover {
                background-color: #C7D8E4;
            }
        """)
        self._viewer_splitters.append(splitter)
        return splitter

    def _clear_image_panel_layout(self) -> None:
        for panel in self._image_panels:
            panel.setParent(None)

        for splitter in self._viewer_splitters:
            splitter.setParent(None)
            splitter.deleteLater()
        self._viewer_splitters.clear()

        while self._viewer_layout.count():
            item = self._viewer_layout.takeAt(0)
            widget = item.widget()
            if widget is not None and widget not in self._image_panels:
                widget.setParent(None)
                widget.deleteLater()
        for row in range(2):
            self._viewer_layout.setRowStretch(row, 0)
        for col in range(2):
            self._viewer_layout.setColumnStretch(col, 0)

    def _relayout_image_panels(self, loaded: list[int]) -> None:
        self._clear_image_panel_layout()

        for idx, panel in enumerate(self._image_panels):
            panel.setVisible(idx in loaded)

        if not loaded:
            return

        if len(loaded) == 1:
            self._viewer_layout.addWidget(self._image_panels[loaded[0]], 0, 0)
            self._viewer_layout.setRowStretch(0, 1)
            self._viewer_layout.setColumnStretch(0, 1)
            return

        if len(loaded) == 2:
            root = self._make_image_panel_splitter(Qt.Orientation.Vertical)
            for slot_index in loaded:
                root.addWidget(self._image_panels[slot_index])
            root.setSizes([1, 1])
        else:
            root = self._make_image_panel_splitter(Qt.Orientation.Horizontal)
            columns = [loaded[::2], loaded[1::2]]
            for column_slots in columns:
                column = self._make_image_panel_splitter(Qt.Orientation.Vertical)
                for slot_index in column_slots:
                    column.addWidget(self._image_panels[slot_index])
                if len(column_slots) > 1:
                    column.setSizes([1 for _ in column_slots])
                root.addWidget(column)
            root.setSizes([1, 1])

        self._viewer_layout.addWidget(root, 0, 0)
        self._viewer_layout.setRowStretch(0, 1)
        self._viewer_layout.setColumnStretch(0, 1)

    @staticmethod
    def _clone_auto_detections(detections: list[dict]) -> list[dict]:
        cloned: list[dict] = []
        for lane in detections:
            lane_copy = {
                "lane_index": lane["lane_index"],
                "lane_rect": QRectF(lane["lane_rect"]),
                "bands": [],
            }
            for band in lane.get("bands", []):
                band_copy = dict(band)
                band_copy["band_rect"] = QRectF(band["band_rect"])
                lane_copy["bands"].append(band_copy)
            cloned.append(lane_copy)
        return cloned

    @staticmethod
    def _reset_canvas_widget(canvas: ImageCanvas) -> None:
        canvas.clear_tutorial_roi_hint()
        canvas._scene.clear()
        canvas._pixmap_item = None
        canvas._pixmap_original_size = None
        canvas._display_source_pixels = None
        canvas._roi_item = None
        canvas._band_roi_item = None
        canvas._lane_items.clear()
        canvas._auto_band_items.clear()
        canvas._auto_band_labels.clear()
        canvas._manual_band_labels.clear()
        canvas._auto_lane_frames.clear()
        canvas._final_crop_item = None
        canvas._rotation_mode = False
        canvas._rotation_dragging = False
        canvas._rotation_angle_deg = 0.0
        canvas._rotation_h_line = None
        canvas._rotation_v_line = None
        canvas._rotation_angle_label = None
        canvas._rotation_drag_start_mouse_angle_deg = None
        canvas._rotation_drag_start_crosshair_angle_deg = 0.0
        canvas._fixed_roi_enabled = False
        canvas._fixed_roi_size = None
        canvas._fixed_band_roi_relative = None
        canvas._moving_fixed_roi = False
        canvas._image_default_transform_params = ImageTransformParams(inverted=True)
        canvas._image_transform_params = canvas._image_default_transform_params

    def _save_active_slot_state(self) -> None:
        if self._active_slot_index is None:
            return
        slot = self._slot_states[self._active_slot_index]
        slot["path"] = self._image_path
        slot["lane_rects"] = [QRectF(rect) for rect in self._lane_rects]
        slot["band_roi"] = QRectF(self._band_roi) if self._band_roi is not None else None
        slot["auto_detections"] = self._clone_auto_detections(self._auto_detections)

    def _load_slot_state(self, slot_index: int) -> None:
        slot = self._slot_states[slot_index]
        self._image_path = slot["path"]
        self._lane_rects = [QRectF(rect) for rect in slot["lane_rects"]]
        self._band_roi = QRectF(slot["band_roi"]) if slot["band_roi"] is not None else None
        self._auto_detections = self._clone_auto_detections(slot["auto_detections"])

    def _set_active_slot(self, slot_index: int | None) -> None:
        if self._active_slot_index == slot_index:
            return
        self._save_active_slot_state()
        self._active_slot_index = slot_index
        if slot_index is None:
            self._image_path = None
            self._lane_rects.clear()
            self._band_roi = None
            self._auto_detections = []
            self.canvas = self._image_panels[0].canvas
            self._act_analyze.setEnabled(False)
            self._refresh_rotation_controls()
            self._sync_image_transform_dialog()
            return
        self.canvas = self._image_panels[slot_index].canvas
        self.canvas.set_interaction_mode(self._current_mode)
        self._load_slot_state(slot_index)
        self._act_analyze.setEnabled(self._band_roi is not None)
        self._refresh_rotation_controls()
        self._sync_image_transform_dialog()

    def _set_panel_checkbox(self, slot_index: int, checked: bool, enabled: bool) -> None:
        checkbox = self._image_panels[slot_index].select_checkbox
        checkbox.blockSignals(True)
        checkbox.setChecked(checked)
        checkbox.setEnabled(enabled)
        checkbox.blockSignals(False)
        self._slot_states[slot_index]["selected"] = checked

    def _set_panel_transform_enabled(self, slot_index: int, enabled: bool) -> None:
        panel = self._image_panels[slot_index]
        panel.transform_btn.setEnabled(enabled)
        panel.rotate_btn.setEnabled(enabled)
        panel.zoom_out_btn.setEnabled(enabled)
        panel.zoom_in_btn.setEnabled(enabled)

    def _refresh_image_panel_layout(self) -> None:
        loaded = self._loaded_slot_indices()
        self._relayout_image_panels(loaded)

        if len(loaded) == 1:
            idx = loaded[0]
            for panel_idx in range(len(self._image_panels)):
                self._set_panel_checkbox(panel_idx, panel_idx == idx, False)
                self._set_panel_transform_enabled(panel_idx, panel_idx == idx)
            self._set_active_slot(idx)
            return

        if len(loaded) > 1:
            selected = [idx for idx in loaded if self._slot_states[idx]["selected"]]
            if len(selected) > 1:
                keep = selected[-1]
                for idx in loaded:
                    self._set_panel_checkbox(idx, idx == keep, True)
                selected = [keep]
            for idx in loaded:
                self._set_panel_checkbox(idx, self._slot_states[idx]["selected"], True)
            for idx in range(len(self._image_panels)):
                if idx not in loaded:
                    self._set_panel_checkbox(idx, False, False)
                self._set_panel_transform_enabled(idx, idx in loaded)
            self._set_active_slot(selected[0] if selected else None)
            return

        for idx in range(len(self._image_panels)):
            self._set_panel_checkbox(idx, False, False)
            self._set_panel_transform_enabled(idx, False)
        self._set_active_slot(None)

    def _on_panel_checkbox_toggled(self, slot_index: int, checked: bool) -> None:
        loaded = self._loaded_slot_indices()
        if slot_index not in loaded:
            return

        if len(loaded) <= 1:
            self._set_panel_checkbox(slot_index, True, False)
            self._set_active_slot(slot_index)
            self._refresh_detection_actions()
            return

        if checked:
            for idx in loaded:
                self._set_panel_checkbox(idx, idx == slot_index, True)
            self._set_active_slot(slot_index)
        else:
            self._set_panel_checkbox(slot_index, False, True)
            self._set_active_slot(None)
        self._refresh_detection_actions()

    def _on_panel_remove_requested(self, slot_index: int) -> None:
        slot = self._slot_states[slot_index]
        if not slot["path"]:
            return

        was_active = self._active_slot_index == slot_index
        if was_active:
            self._save_active_slot_state()
            self._active_slot_index = None
            self._image_path = None
            self._lane_rects.clear()
            self._band_roi = None
            self._auto_detections = []

        slot["path"] = None
        slot["selected"] = False
        slot["lane_rects"] = []
        slot["band_roi"] = None
        slot["auto_detections"] = []
        slot["image_operation_history"] = []
        self._reset_canvas_widget(self._image_panels[slot_index].canvas)
        self._image_panels[slot_index].set_filename("")

        self._refresh_image_panel_layout()
        loaded = self._loaded_slot_indices()
        if not loaded:
            self.param_panel.set_auto_edit_enabled(False)
            self.canvas.set_auto_edit_mode(False)
            self._status_bar.showMessage("No image loaded.")
        elif len(loaded) == 1:
            self._status_bar.showMessage("Image removed. Returned to single-image mode.")
        else:
            self._status_bar.showMessage("Image removed.")
        self._refresh_detection_actions()

    def _on_panel_transform_requested(self, slot_index: int) -> None:
        if slot_index not in self._loaded_slot_indices():
            return
        self._set_active_slot_from_interaction(slot_index)
        self._show_image_transform_dialog()

    def _on_panel_zoom_requested(self, slot_index: int, *, zoom_in: bool) -> None:
        """Zoom one image panel and make it the active Densitometry image."""
        if slot_index not in self._loaded_slot_indices():
            return
        self._set_active_slot_from_interaction(slot_index)
        canvas = self._image_panels[slot_index].canvas
        if zoom_in:
            canvas.zoom_in()
        else:
            canvas.zoom_out()

    def _on_panel_custom_rotate_requested(self, slot_index: int) -> None:
        if slot_index not in self._loaded_slot_indices():
            return
        self._set_active_slot_from_interaction(slot_index)
        self._on_custom_rotate_requested()

    def _on_panel_flip_requested(self, slot_index: int, *, vertical: bool) -> None:
        if slot_index not in self._loaded_slot_indices():
            return
        self._set_active_slot_from_interaction(slot_index)
        self._on_flip_image_requested(vertical=vertical)

    def _on_panel_undo_image_operation_requested(self, slot_index: int) -> None:
        if slot_index not in self._loaded_slot_indices():
            return
        self._set_active_slot_from_interaction(slot_index)
        self._on_undo_image_operation_requested()

    def _set_active_slot_from_interaction(self, slot_index: int) -> None:
        loaded = self._loaded_slot_indices()
        if slot_index not in loaded:
            return
        if len(loaded) > 1:
            for idx in loaded:
                self._set_panel_checkbox(idx, idx == slot_index, True)
        elif len(loaded) == 1:
            self._set_panel_checkbox(slot_index, True, False)
        self._set_active_slot(slot_index)
        self._set_workspace_focus_target("image")
        self._refresh_detection_actions()

    def _on_roi_changed_for_slot(self, slot_index: int, roi: QRectF) -> None:
        self._set_active_slot_from_interaction(slot_index)
        self._on_roi_changed(roi)
        self._tutorial_controller.notify_roi_changed()

    def _on_band_roi_changed_for_slot(self, slot_index: int, band_roi: QRectF) -> None:
        self._set_active_slot_from_interaction(slot_index)
        self._on_band_roi_changed(band_roi)
        self._tutorial_controller.notify_band_roi_changed()

    def _on_auto_rois_changed_for_slot(self, slot_index: int, detections: list[dict]) -> None:
        self._set_active_slot_from_interaction(slot_index)
        self._on_auto_rois_changed(detections)

    def _reset_zoom_active(self) -> None:
        if self._is_wb_plot_workspace_active() and self._workspace_focus_target == "figure":
            self._figure_mode_window.reset_zoom()
            return
        if self._embedded_column_table is not None and self._figure_workspace.isVisible():
            if self._workspace_focus_target == "table":
                self._embedded_column_table.fit_table_view()
                return
            if self._workspace_focus_target == "figure":
                self._embedded_column_table.fit_figure_preview()
                return
        loaded = self._loaded_slot_indices()
        if not loaded:
            return
        if self._active_slot_index is None:
            if len(loaded) == 1:
                self._set_active_slot(loaded[0])
            else:
                QMessageBox.information(self, "Select Image", "Please select an image first.")
                return
        self.canvas.reset_zoom()

    def _zoom_in_active(self) -> None:
        if self._is_wb_plot_workspace_active() and self._workspace_focus_target == "figure":
            self._figure_mode_window.zoom_in()
            return
        if self._embedded_column_table is not None and self._figure_workspace.isVisible():
            if self._workspace_focus_target == "table":
                self._embedded_column_table.zoom_table(True)
                return
            if self._workspace_focus_target == "figure":
                self._embedded_column_table.zoom_figure_preview(True)
                return
        loaded = self._loaded_slot_indices()
        if not loaded:
            return
        if self._active_slot_index is None:
            if len(loaded) == 1:
                self._set_active_slot(loaded[0])
            else:
                QMessageBox.information(self, "Select Image", "Please select an image first.")
                return
        self.canvas.zoom_in()

    def _zoom_out_active(self) -> None:
        if self._is_wb_plot_workspace_active() and self._workspace_focus_target == "figure":
            self._figure_mode_window.zoom_out()
            return
        if self._embedded_column_table is not None and self._figure_workspace.isVisible():
            if self._workspace_focus_target == "table":
                self._embedded_column_table.zoom_table(False)
                return
            if self._workspace_focus_target == "figure":
                self._embedded_column_table.zoom_figure_preview(False)
                return
        loaded = self._loaded_slot_indices()
        if not loaded:
            return
        if self._active_slot_index is None:
            if len(loaded) == 1:
                self._set_active_slot(loaded[0])
            else:
                QMessageBox.information(self, "Select Image", "Please select an image first.")
                return
        self.canvas.zoom_out()

    def _active_image_transform_canvas(self) -> ImageCanvas | None:
        loaded = self._loaded_slot_indices()
        if not loaded:
            return None
        if self._active_slot_index is None:
            if len(loaded) == 1:
                self._set_active_slot(loaded[0])
            else:
                return None
        if self._active_slot_index is None:
            return None
        return self._image_panels[self._active_slot_index].canvas

    def _show_image_transform_dialog(self) -> None:
        canvas = self._active_image_transform_canvas()
        if canvas is None:
            loaded = self._loaded_slot_indices()
            if loaded:
                QMessageBox.information(self, "Select Image", "Please select one image panel first.")
            else:
                QMessageBox.information(self, "No Image", "Upload files first.")
            return

        if self._image_transform_dialog is None:
            self._image_transform_dialog = ImageTransformDialog(self)
            self._image_transform_dialog.set_language(self._language)
            self._image_transform_dialog.paramsChanged.connect(self._apply_image_transform_params)
            self._image_transform_dialog.autoScaleRequested.connect(self._auto_scale_image_transform)
            self._image_transform_dialog.resetRequested.connect(self._reset_image_transform)

        self._sync_image_transform_dialog()
        self._image_transform_dialog.show()
        self._image_transform_dialog.raise_()
        self._image_transform_dialog.activateWindow()

    def _sync_image_transform_dialog(self) -> None:
        if self._image_transform_dialog is None:
            return
        canvas = self._active_image_transform_canvas() if self._loaded_slot_indices() else None
        has_source = canvas is not None and canvas.has_image_transform_source()
        self._image_transform_dialog.set_controls_enabled(has_source)
        if has_source and canvas is not None:
            self._image_transform_dialog.set_params(canvas.get_image_transform_params())
        else:
            self._image_transform_dialog.set_params(ImageTransformParams())

    def _apply_image_transform_params(self, params: ImageTransformParams) -> None:
        canvas = self._active_image_transform_canvas()
        if canvas is None:
            return
        canvas.set_image_transform_params(params)
        self._status_bar.showMessage(
            f"Image Transform applied: Low {params.low}, High {params.high}, Gamma {params.gamma:.2f}.",
            3000,
        )

    def _auto_scale_image_transform(self) -> None:
        canvas = self._active_image_transform_canvas()
        if canvas is None:
            return
        params = canvas.auto_scale_image_transform()
        if self._image_transform_dialog is not None:
            self._image_transform_dialog.set_params(params)
        self._status_bar.showMessage(
            f"Auto Scale applied: Low {params.low}, High {params.high}, Gamma {params.gamma:.2f}.",
            3000,
        )

    def _reset_image_transform(self) -> None:
        canvas = self._active_image_transform_canvas()
        if canvas is None:
            return
        params = canvas.reset_image_transform()
        if self._image_transform_dialog is not None:
            self._image_transform_dialog.set_params(params)
        self._status_bar.showMessage("Image Transform reset to full 16-bit range.", 3000)

    def _normalize_auto_detections(self, detections: list[dict], preserve_target_row: bool = False) -> list[dict]:
        params = self.param_panel.get_params()
        target_row = params.get("target_band_row")

        existing_row_indices = any(
            band.get("row_index") is not None
            for lane in detections
            for band in lane.get("bands", [])
        )

        if preserve_target_row and target_row is not None:
            normalized = []
            for lane in detections:
                lane_copy = {
                    "lane_index": lane["lane_index"],
                    "lane_rect": QRectF(lane["lane_rect"]),
                    "bands": [],
                }
                normalized.append(lane_copy)
                for index, band in enumerate(sorted(lane.get("bands", []), key=lambda item: item["band_rect"].y()), start=1):
                    band_copy = dict(band)
                    band_copy["band_rect"] = QRectF(band["band_rect"])
                    band_copy["row_index"] = int(target_row)
                    band_copy["row_member_index"] = index
                    band_copy["display_name"] = f"Row {int(target_row)}"
                    lane_copy["bands"].append(band_copy)
            return normalized

        if existing_row_indices:
            normalized = []
            for lane in detections:
                lane_copy = {
                    "lane_index": lane["lane_index"],
                    "lane_rect": QRectF(lane["lane_rect"]),
                    "bands": [],
                }
                normalized.append(lane_copy)
                for band in lane.get("bands", []):
                    band_copy = dict(band)
                    band_copy["band_rect"] = QRectF(band["band_rect"])
                    lane_copy["bands"].append(band_copy)
        else:
            normalized = group_auto_detected_rows(
                detections,
                expected_rows_per_lane=params.get("expected_rows_per_lane"),
                target_band_row=target_row,
            )

        for lane in normalized:
            counts: dict[int, int] = {}
            for band in lane["bands"]:
                row_index = int(band.get("row_index", band.get("band_index", 1)))
                counts[row_index] = counts.get(row_index, 0) + 1
                band["row_member_index"] = counts[row_index]
                band["global_band_index"] = row_index
                band["display_name"] = f"Row {row_index}"
        return normalized

    # ── WB Plot mode ──────────────────────────────────────────────────────────

    def _on_wb_plot_mode(self) -> None:
        """Show WB Plot generation beside the active WB image viewer."""
        self._home_mode_active = False
        self._set_import_toolbar_visible(True)
        self._set_analyze_toolbar_visible(False)
        self._workspace_toolbar_action.setVisible(True)
        self._mode_container.setCurrentWidget(self._main_splitter)
        if self._figure_mode_window is None:
            from gui.figure_mode_window import FigureModeWindow

            self._clear_container_layout(self._wb_plot_workspace_host)
            self._figure_mode_window = FigureModeWindow(
                self._wb_plot_workspace_host,
                language=self._language,
            )
            self._figure_mode_window.set_active_image_provider(self._active_wb_plot_source)
            self._figure_mode_window.set_auto_fit_detection_handler(
                self._run_wb_plot_auto_fit_detection
            )
            self._figure_mode_window.set_auto_fit_overlay_handler(
                self._set_wb_plot_auto_fit_overlay
            )
            self._figure_mode_window.set_fixed_roi_request_handler(self._on_add_fixed_wb_plot_roi)
            self._figure_mode_window.set_fixed_roi_cancel_handler(self._on_cancel_fixed_wb_plot_roi)
            self._figure_mode_window.set_fixed_roi_size_selected_handler(self._on_select_fixed_wb_plot_roi_size)
            self._figure_mode_window.set_focus_request_handler(lambda: self._set_workspace_focus_target("figure"))
            self._figure_mode_window.workflowEvent.connect(
                self._tutorial_controller.notify_wb_event
            )
            self._wb_plot_workspace_layout.addWidget(self._figure_mode_window, 1)
        # Always resynchronise on entry. This covers both a workspace created
        # after the language changed and an existing workspace shown again.
        self._figure_mode_window.set_language(self._language)
        self._show_figure_workspace("wb_plot")
        self._set_workspace_focus_target("figure")
        self._status_bar.showMessage(
            tr("Opened WB Figure Layout workspace.", self._language),
            3000,
        )

    def _on_add_fixed_wb_plot_roi(self) -> QSizeF | None:
        if self._image_path is None:
            QMessageBox.warning(self, "No Image", "Upload or select a WB image first.")
            return None
        had_size = self.canvas.set_fixed_roi_mode(True)
        size = self.canvas.get_fixed_roi_viewport_size()
        self._set_workspace_focus_target("image")
        if had_size and size is not None:
            self._status_bar.showMessage(
                "Fixed ROI size captured. Select its saved size, drag it on any WB image, then press Return/Enter to apply.",
                5000,
            )
            return QSizeF(size)
        else:
            self._status_bar.showMessage(
                "Fixed ROI mode armed. Draw one ROI to set its size, then click Fix ROI again to save it.",
                5000,
            )
            return None

    def _on_cancel_fixed_wb_plot_roi(self) -> None:
        for idx, panel in enumerate(self._image_panels):
            panel.canvas.cancel_fixed_roi_mode(clear_current_roi=True)
            self._slot_states[idx]["lane_rects"] = []
            self._slot_states[idx]["band_roi"] = None
        self._lane_rects.clear()
        self._band_roi = None
        self._act_analyze.setEnabled(False)
        self._status_bar.showMessage("Fixed ROI mode canceled for all WB image windows.", 3000)

    def _on_select_fixed_wb_plot_roi_size(self, size: QSizeF) -> None:
        applied = 0
        for idx, panel in enumerate(self._image_panels):
            if self._slot_states[idx].get("path") is None:
                continue
            panel.canvas.set_fixed_roi_viewport_size(size, enabled=True)
            applied += 1
        self._set_workspace_focus_target("image")
        self._status_bar.showMessage(
            f"Fixed ROI size selected for {applied} WB image window(s). Click/drag to place it.",
            4000,
        )

    def _on_add_fixed_general_roi(self) -> dict[str, object] | None:
        if self._active_slot_index is None or self._image_path is None:
            QMessageBox.warning(self, "No Image", "Upload or select an image first.")
            return None
        if self.param_panel.get_mode() != "manual":
            self._status_bar.showMessage("Fixed ROI is available in Manual mode.", 3000)
            return None

        roi = self.canvas.get_roi()
        band_roi = self.canvas.get_band_roi()
        if roi is not None:
            profile = self._build_fixed_general_roi_profile(roi, band_roi)
            self._set_workspace_focus_target("image")
            if band_roi is not None:
                self._status_bar.showMessage(
                    "Fixed lane & band ROI saved. Select it on another image to place both ROIs and analyze directly.",
                    5000,
                )
            else:
                self._status_bar.showMessage(
                    "Fixed Lane ROI saved. Select it on another image, then draw a band ROI before Analyze.",
                    5000,
                )
            return profile

        self.canvas.set_fixed_roi_mode(True)
        self._set_workspace_focus_target("image")
        self._status_bar.showMessage(
            "Fixed ROI mode armed. Draw one lane ROI to set its size, then click Fix ROI again to save it.",
            5000,
        )
        return None

    def _build_fixed_general_roi_profile(
        self,
        roi: QRectF,
        band_roi: QRectF | None,
    ) -> dict[str, object]:
        image_size = self.canvas.image_scene_size()
        image_w = max(image_size.width(), 1.0) if image_size is not None else max(roi.right(), 1.0)
        image_h = max(image_size.height(), 1.0) if image_size is not None else max(roi.bottom(), 1.0)
        profile: dict[str, object] = {
            "kind": "lane_band" if band_roi is not None else "lane",
            "lane_size": QSizeF(roi.width(), roi.height()),
            "lane_size_norm": QSizeF(roi.width() / image_w, roi.height() / image_h),
        }
        if band_roi is not None and roi.width() > 0 and roi.height() > 0:
            profile["band_relative"] = QRectF(
                (band_roi.x() - roi.x()) / roi.width(),
                (band_roi.y() - roi.y()) / roi.height(),
                band_roi.width() / roi.width(),
                band_roi.height() / roi.height(),
            )
        return profile

    def _on_cancel_fixed_general_roi(self) -> None:
        if self._active_slot_index is None:
            return
        self.canvas.cancel_fixed_roi_mode(clear_current_roi=True)
        self._lane_rects.clear()
        self._band_roi = None
        self._auto_detections = []
        self._act_analyze.setEnabled(False)
        self.param_panel.set_auto_edit_enabled(False)
        self._save_active_slot_state()
        self._status_bar.showMessage("Fixed ROI mode canceled for the active image.", 3000)

    def _on_select_fixed_general_roi_size(self, profile: dict[str, object]) -> None:
        if self._active_slot_index is None or self._image_path is None:
            self._status_bar.showMessage("Select an image before applying a fixed ROI size.", 3000)
            return
        if self.param_panel.get_mode() != "manual":
            self._status_bar.showMessage("Fixed ROI is available in Manual mode.", 3000)
            return
        lane_size = self._fixed_general_lane_size_for_active_image(profile)
        if lane_size is None:
            self._status_bar.showMessage("Could not apply the saved fixed ROI size.", 3000)
            return
        band_relative = profile.get("band_relative")
        band_profile = band_relative if isinstance(band_relative, QRectF) else None
        self.canvas.clear_roi()
        self._lane_rects.clear()
        self._band_roi = None
        self._auto_detections = []
        self._act_analyze.setEnabled(False)
        self.canvas.set_fixed_roi_profile(
            lane_size,
            band_relative=band_profile,
            enabled=True,
        )
        self._set_workspace_focus_target("image")
        if profile.get("kind") == "lane_band":
            self._status_bar.showMessage(
                "Fixed lane & band ROI selected. Click/drag on the active image to place both ROIs.",
                4000,
            )
        else:
            self._status_bar.showMessage(
                "Fixed Lane ROI selected. Click/drag on the active image to place it, press Return/Enter to lock it, then draw a band ROI.",
                4000,
            )

    def _fixed_general_lane_size_for_active_image(self, profile: dict[str, object]) -> QSizeF | None:
        image_size = self.canvas.image_scene_size()
        norm_size = profile.get("lane_size_norm")
        if image_size is not None and isinstance(norm_size, QSizeF):
            width = norm_size.width() * image_size.width()
            height = norm_size.height() * image_size.height()
            if width > 0 and height > 0:
                return QSizeF(width, height)
        lane_size = profile.get("lane_size")
        if isinstance(lane_size, QSizeF):
            return QSizeF(lane_size)
        return None

    def _active_wb_plot_source(self) -> dict[str, object]:
        if self._image_path is None:
            return {"error": "Upload or select a WB image first."}
        roi = self.canvas.get_roi()
        if roi is None:
            return {"error": "Draw a lane ROI on the active WB image first."}
        return {
            "image_path": self._image_path,
            "roi": QRectF(roi),
            "lane_count": 1,
            "image_transform": image_transform_to_dict(self.canvas.get_image_transform_params()),
            "auto_detections": self._clone_auto_detections(self._auto_detections),
            "image_size": self.canvas.image_scene_size(),
        }

    def _run_wb_plot_auto_fit_detection(
        self,
        expected_lane_count: int | None,
        reuse_existing: bool,
    ) -> dict[str, object]:
        """Run/reuse guided detection without changing densitometry mode."""
        source = self._active_wb_plot_source()
        if source.get("error"):
            return source

        if reuse_existing and self._auto_detections:
            source["reused"] = True
            return source

        image_path = str(source["image_path"])
        search_roi = source["roi"]
        try:
            detections, metadata = _run_auto_fit_guided_with_polarity_fallback(
                image_path,
                sensitivity=0.5,
                search_roi=search_roi if isinstance(search_roi, QRectF) else None,
                expected_lane_count=expected_lane_count,
            )
        except Exception as exc:
            log.exception("WB Plot Auto-Fit detection failed")
            return {"error": f"Band Auto-Fit detection failed: {exc}"}

        self._auto_detections = self._normalize_auto_detections(detections)
        if self._active_slot_index is not None:
            self._slot_states[self._active_slot_index]["auto_detections"] = (
                self._clone_auto_detections(self._auto_detections)
            )
        self.canvas.set_auto_edit_mode(True)
        self.canvas.set_auto_detect_overlays(self._auto_detections)
        source["auto_detections"] = self._clone_auto_detections(self._auto_detections)
        source["metadata"] = metadata
        source["reused"] = False
        return source

    def _set_wb_plot_auto_fit_overlay(self, crop: QRectF | None) -> None:
        """Show/clear Auto-Fit review overlays in the active source viewer."""
        if crop is None:
            self.canvas.clear_auto_overlays()
            self.canvas.set_auto_edit_mode(False)
            self._auto_detections = []
            if self._active_slot_index is not None:
                self._slot_states[self._active_slot_index]["auto_detections"] = []
            return
        self.canvas.set_final_crop_overlay(crop)

    def _is_wb_plot_workspace_active(self) -> bool:
        return (
            not self._figure_workspace.isHidden()
            and self._figure_workspace_stack.currentWidget() is self._wb_plot_workspace_host
            and self._figure_mode_window is not None
        )

    # ────────────────────────────────────────────────────────────────────────

    def _register_shortcuts(self) -> None:
        # macOS-first shortcuts (Command on macOS); keep Ctrl variants for compatibility.
        QShortcut(QKeySequence("Meta++"), self).activated.connect(self._zoom_in_active)
        QShortcut(QKeySequence("Meta+="), self).activated.connect(self._zoom_in_active)
        QShortcut(QKeySequence("Meta+-"), self).activated.connect(self._zoom_out_active)
        QShortcut(QKeySequence("Ctrl++"), self).activated.connect(self._zoom_in_active)
        QShortcut(QKeySequence("Ctrl+="), self).activated.connect(self._zoom_in_active)
        QShortcut(QKeySequence("Ctrl+-"), self).activated.connect(self._zoom_out_active)
        QShortcut(QKeySequence("Ctrl+0"), self).activated.connect(self._reset_zoom_active)
        # Enter / Return → Analyze only while working in an image canvas.
        # Keeping this off the main window lets results-table Return continue
        # to drive mean autofill into the Column table.
        for panel in self._image_panels:
            for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                shortcut = QShortcut(QKeySequence(key), panel.canvas)
                shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                shortcut.activated.connect(self._trigger_analyze_from_return_shortcut)
                self._analyze_return_shortcuts.append(shortcut)

    def _trigger_analyze_from_return_shortcut(self) -> None:
        if self.canvas.is_rotation_mode():
            self._on_rotate_requested()
            return
        if self._is_wb_plot_workspace_active() and self._figure_mode_window is not None:
            if self._figure_mode_window.apply_roi_to_selected_slot():
                self._status_bar.showMessage("Applied active WB ROI to the selected plot target.", 3000)
            return
        if (
            self.param_panel.get_mode() == "manual"
            and self.canvas.finish_fixed_lane_roi_placement()
        ):
            self._status_bar.showMessage(
                "Fixed Lane ROI locked. Draw a band ROI, then click Analyze.",
                4000,
            )
            return
        if self._act_analyze.isEnabled():
            self._act_analyze.trigger()

    def _connect_signals(self) -> None:
        self._act_open.triggered.connect(self._upload_files)
        self._act_image_transform.triggered.connect(self._show_image_transform_dialog)
        self._act_analyze.triggered.connect(self._run_analysis)
        self._tutorial_btn.clicked.connect(self._show_tutorial_selector)
        self._language_combo.currentIndexChanged.connect(self._on_language_changed)

        for idx, panel in enumerate(self._image_panels):
            panel.canvas.roi_changed.connect(lambda roi, i=idx: self._on_roi_changed_for_slot(i, roi))
            panel.canvas.band_roi_changed.connect(lambda band_roi, i=idx: self._on_band_roi_changed_for_slot(i, band_roi))
            panel.canvas.auto_rois_changed.connect(lambda detections, i=idx: self._on_auto_rois_changed_for_slot(i, detections))
            panel.canvas.rotation_angle_changed.connect(lambda angle, i=idx: self._on_rotation_angle_changed_for_slot(i, angle))
            panel.canvas.rotation_mode_changed.connect(lambda enabled, i=idx: self._on_rotation_mode_changed_for_slot(i, enabled))
            panel.canvas.panel_interacted.connect(lambda i=idx: self._set_active_slot_from_interaction(i))
            panel.canvas.roi_cleared.connect(lambda i=idx: self._on_canvas_roi_cleared_for_slot(i))
            panel.select_checkbox.toggled.connect(lambda checked, i=idx: self._on_panel_checkbox_toggled(i, checked))
            panel.zoom_out_btn.clicked.connect(
                lambda _=False, i=idx: self._on_panel_zoom_requested(i, zoom_in=False)
            )
            panel.zoom_in_btn.clicked.connect(
                lambda _=False, i=idx: self._on_panel_zoom_requested(i, zoom_in=True)
            )
            panel.remove_btn.clicked.connect(lambda _, i=idx: self._on_panel_remove_requested(i))
            panel.transform_btn.clicked.connect(lambda _, i=idx: self._on_panel_transform_requested(i))
            panel.rotate_custom_action.triggered.connect(
                lambda _=False, i=idx: self._on_panel_custom_rotate_requested(i)
            )
            panel.flip_vertical_action.triggered.connect(
                lambda _=False, i=idx: self._on_panel_flip_requested(i, vertical=True)
            )
            panel.flip_horizontal_action.triggered.connect(
                lambda _=False, i=idx: self._on_panel_flip_requested(i, vertical=False)
            )
            panel.undo_image_operation_action.triggered.connect(
                lambda _=False, i=idx: self._on_panel_undo_image_operation_requested(i)
            )

        self._files_list.itemClicked.connect(self._on_uploaded_file_clicked)
        self._export_all_tiffs_btn.clicked.connect(self._export_all_tiffs)
        self._files_toggle_btn.clicked.connect(self._toggle_files_panel)
        if self._main_splitter is not None:
            self._main_splitter.splitterMoved.connect(self._on_main_splitter_moved)

        self.param_panel.params_changed.connect(self._on_params_changed)
        self.param_panel.status_message.connect(self._status_bar.showMessage)
        self.param_panel.detect_requested.connect(self._detect_bands)
        self.param_panel.custom_rotate_requested.connect(self._on_custom_rotate_requested)
        self.param_panel.rotate_requested.connect(self._on_rotate_requested)
        self.param_panel.cancel_rotate_requested.connect(self._on_cancel_rotate_requested)
        self.param_panel.set_fixed_roi_request_handler(self._on_add_fixed_general_roi)
        self.param_panel.set_fixed_roi_cancel_handler(self._on_cancel_fixed_general_roi)
        self.param_panel.set_fixed_roi_size_selected_handler(self._on_select_fixed_general_roi_size)
        self.results_panel.meanAutofillRequested.connect(self._on_mean_autofill_requested)

    def _active_column_table_window(self) -> ColumnTableWindow | None:
        if self._embedded_column_table is not None:
            return self._embedded_column_table
        visible_windows = [w for w in self._figure_windows if w is not None and w.isVisible()]
        if not visible_windows:
            return None
        for window in visible_windows:
            if window.isActiveWindow():
                return window
        return visible_windows[-1]

    def _on_mean_autofill_requested(self, selected_values: list[str]) -> None:
        table_window = self._active_column_table_window()
        if table_window is None:
            return
        if not selected_values:
            self._status_bar.showMessage("No Mean values selected for autofill.", 3000)
            return
        if not table_window.has_active_target_row():
            QMessageBox.information(
                self,
                "Select Target Row",
                "Please click a Target band or Loading control row in the Column table first.",
            )
            return

        inserted_count = table_window.autofill_active_row(selected_values)
        if inserted_count <= 0:
            self._status_bar.showMessage("No values inserted into the selected row.", 3000)
            return

        target_desc = table_window.active_target_description()
        self._status_bar.showMessage(
            f"Autofilled {inserted_count} value(s) into {target_desc}.",
            4000,
        )
        self._tutorial_controller.notify_autofill_completed()

    # ── Actions ────────────────────────────────────────────────────────────────

    def _ensure_direct_exporter(self):
        if self._direct_exporter is not None:
            return self._direct_exporter

        exporter_root = self._resolve_exporter_root()
        if exporter_root is None:
            raise FileNotFoundError(
                "WB-TIFF-exporter module not found (checked bundled vendor copy "
                "and sibling WB-TIFF-exporter folder)"
            )
        if str(exporter_root) not in sys.path:
            sys.path.insert(0, str(exporter_root))

        from direct_exporter import DirectExporter
        from scn_parser import SUPPORTED_EXTENSIONS as SUPPORTED_DOC_EXTENSIONS

        self._supported_upload_exts = set(SUPPORTED_DOC_EXTENSIONS)
        self._direct_exporter = DirectExporter()
        return self._direct_exporter

    @staticmethod
    def _resolve_exporter_root() -> Path | None:
        """Locate the direct_exporter/scn_parser/tiff_writer module folder.

        Checks, in order: a PyInstaller-bundled copy (packaged builds), a
        vendored copy shipped inside this repo, then the original sibling
        "WB-TIFF-exporter" project folder used during local development.
        """
        candidates: list[Path] = []
        if getattr(sys, "frozen", False):
            meipass = getattr(sys, "_MEIPASS", "")
            if meipass:
                candidates.append(Path(meipass) / "vendor" / "wb_tiff_exporter")
        candidates.append(Path(__file__).resolve().parents[1] / "vendor" / "wb_tiff_exporter")
        candidates.append(Path(__file__).resolve().parents[2] / "WB-TIFF-exporter")

        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    @staticmethod
    def _ensure_unique_path(path: Path) -> Path:
        if not path.exists():
            return path
        counter = 2
        while True:
            candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
            if not candidate.exists():
                return candidate
            counter += 1

    def _refresh_uploaded_files_list(self) -> None:
        self._files_list.clear()
        for source_key, entry in sorted(
            self._converted_documents.items(),
            key=lambda item: item[1]["source"].name.lower(),
        ):
            name = entry["source"].name
            if not entry["display_tiff"]:
                name = f"{name} (conversion failed)"
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, source_key)
            item.setSizeHint(QSize(0, 26))
            item.setToolTip(str(entry["source"]))
            self._files_list.addItem(item)
            row = _UploadedFileListRow(source_key, name, str(entry["source"]))
            row.open_requested.connect(self._open_uploaded_file_entry)
            row.remove_requested.connect(self._remove_uploaded_file_entry)
            self._files_list.setItemWidget(item, row)
        self._export_all_tiffs_btn.setEnabled(bool(self._converted_documents))

    def _open_uploaded_file_entry(self, source_key: str) -> None:
        entry = self._converted_documents.get(source_key)
        if not entry:
            return

        display_tiff: Path | None = entry.get("display_tiff")
        if display_tiff is None:
            QMessageBox.warning(
                self,
                "No TIFF",
                f"No TIFF was generated for {entry['source'].name}.",
            )
            return
        self._load_converted_tiff_into_viewer(display_tiff, clear_results_on_first=False)

    def _remove_uploaded_file_entry(self, source_key: str) -> None:
        entry = self._converted_documents.pop(source_key, None)
        if not entry:
            return
        self._refresh_uploaded_files_list()
        self._status_bar.showMessage(
            f"Removed {entry['source'].name} from Uploaded Files.",
            3000,
        )

    def _load_converted_tiff_into_viewer(
        self,
        image_path: Path,
        *,
        clear_results_on_first: bool = False,
    ) -> bool:
        normalized_path = str(image_path.expanduser().resolve())
        for idx, state in enumerate(self._slot_states):
            if state["path"] and Path(state["path"]).expanduser().resolve() == Path(normalized_path):
                self._set_active_slot_from_interaction(idx)
                return True

        loaded = self._loaded_slot_indices()
        if len(loaded) >= len(self._image_panels):
            QMessageBox.information(
                self,
                "Maximum Images Reached",
                f"You can open a maximum of {len(self._image_panels)} images. Please remove one before adding another.",
            )
            return False

        target_slot = next(
            (idx for idx, state in enumerate(self._slot_states) if state["path"] is None),
            None,
        )
        if target_slot is None:
            return False
        try:
            log.info("Opening uploaded TIFF in viewer slot %d: %s", target_slot + 1, image_path)
            if self._active_slot_index is not None:
                self._save_active_slot_state()

            self._image_panels[target_slot].canvas.load_image(normalized_path)
            self._image_panels[target_slot].set_filename(Path(normalized_path).name)
            self._slot_states[target_slot]["path"] = normalized_path
            self._slot_states[target_slot]["lane_rects"] = []
            self._slot_states[target_slot]["band_roi"] = None
            self._slot_states[target_slot]["auto_detections"] = []
            self._slot_states[target_slot]["image_operation_history"] = []
            self._slot_states[target_slot]["selected"] = len(loaded) == 0

            if clear_results_on_first and len(loaded) == 0:
                self.results_panel.clear()

            self._current_mode = self.param_panel.get_mode()
            self.param_panel.set_auto_edit_enabled(False)
            self.canvas.set_auto_edit_mode(False)
            self._refresh_image_panel_layout()
            self._refresh_detection_actions()

            loaded_after = self._loaded_slot_indices()
            if len(loaded_after) > 1:
                self._status_bar.showMessage(
                    f"Loaded: {image_path.name}  —  viewer showing {len(loaded_after)} images. Select one image to draw ROIs."
                )
            else:
                self._status_bar.showMessage(
                    f"Loaded: {image_path.name}  —  draw a ROI to continue."
                )
            log.info("Loaded converted TIFF: %s", image_path)
            return True
        except Exception as e:
            log.exception("Image load error for %s", image_path)
            QMessageBox.critical(self, "Image Load Error", str(e))
            return False

    def _upload_files(self) -> None:
        if self._tutorial_controller.handle_import_request():
            return
        if self._upload_thread is not None and self._upload_thread.isRunning():
            self._status_bar.showMessage("An upload is already in progress — please wait.")
            return

        converter_error: Exception | None = None
        try:
            self._ensure_direct_exporter()
        except Exception as e:
            converter_error = e

        doc_exts = set(self._supported_upload_exts)
        tiff_exts = set(self._direct_tiff_exts)
        exts = " ".join(f"*{ext}" for ext in sorted(doc_exts | tiff_exts))
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            tr("Import Images", self._language),
            str(self._persistence.default_open_dir()),
            f"Image Lab docs + TIFF ({exts});;All files (*)",
        )
        if not paths:
            return

        selected_paths = [Path(p).expanduser().resolve() for p in paths]
        if selected_paths:
            self._persistence.remember_open_dir(selected_paths[0].parent)
        docs = [p for p in selected_paths if p.suffix.lower() in doc_exts]
        direct_tiffs = [p for p in selected_paths if p.suffix.lower() in tiff_exts]
        unsupported = [p for p in selected_paths if p.suffix.lower() not in (doc_exts | tiff_exts)]

        if docs and self._direct_exporter is None:
            QMessageBox.critical(
                self,
                "Conversion Error",
                f"Image Lab document conversion is unavailable:\n\n{converter_error}",
            )
            docs = []
        elif docs:
            self._start_upload_conversion(
                docs=docs,
                selected_paths=selected_paths,
                direct_tiffs=direct_tiffs,
                unsupported=unsupported,
            )
            return

        self._complete_upload(
            results=[],
            selected_paths=selected_paths,
            direct_tiffs=direct_tiffs,
            unsupported=unsupported,
        )

    def _start_upload_conversion(
        self,
        *,
        docs: list[Path],
        selected_paths: list[Path],
        direct_tiffs: list[Path],
        unsupported: list[Path],
    ) -> None:
        """Start document conversion in a worker thread and keep the UI responsive."""
        self._pending_upload = {
            "selected_paths": list(selected_paths),
            "direct_tiffs": list(direct_tiffs),
            "unsupported": list(unsupported),
        }
        self._act_open.setEnabled(False)
        self._status_bar.showMessage(
            f"Uploading {len(selected_paths)} file(s) — converting Image Lab documents…"
        )
        log.info(
            "Upload started: %d selected, %d document(s), %d direct TIFF(s)",
            len(selected_paths),
            len(docs),
            len(direct_tiffs),
        )

        thread = QThread(self)
        worker = UploadConversionWorker(
            self._direct_exporter,
            docs,
            self._conversion_cache_dir,
        )
        self._upload_thread = thread
        self._upload_worker = worker
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.progress.connect(self._on_upload_progress)
        worker.finished.connect(self._on_upload_conversion_done)
        worker.error.connect(self._on_upload_conversion_error)
        worker.finished.connect(worker.deleteLater)
        worker.error.connect(worker.deleteLater)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(self._on_upload_thread_finished)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _on_upload_progress(self, message: str) -> None:
        log.info("[Upload/Convert] %s", message)
        if message.startswith("Parsing file:"):
            self._status_bar.showMessage(f"Uploading — {message}")

    def _on_upload_conversion_done(self, results: list) -> None:
        pending = self._pending_upload
        self._pending_upload = None
        if pending is None:
            log.error("Upload conversion finished without pending upload context")
            return
        log.info("Upload conversion finished; updating the file list and viewers")
        try:
            self._complete_upload(
                results=results,
                selected_paths=pending["selected_paths"],
                direct_tiffs=pending["direct_tiffs"],
                unsupported=pending["unsupported"],
            )
        except Exception as exc:
            log.exception("Upload post-processing failed")
            self._status_bar.showMessage("Upload post-processing failed — see error dialog.")
            QMessageBox.critical(self, "Upload Error", str(exc))

    def _on_upload_conversion_error(self, message: str) -> None:
        self._pending_upload = None
        log.error("Upload conversion failed: %s", message)
        self._status_bar.showMessage("Upload failed — see error dialog.")
        QMessageBox.critical(self, "Conversion Error", message)

    def _on_upload_thread_finished(self) -> None:
        self._act_open.setEnabled(True)
        self._upload_worker = None
        self._upload_thread = None

    def _complete_upload(
        self,
        *,
        results: list,
        selected_paths: list[Path],
        direct_tiffs: list[Path],
        unsupported: list[Path],
    ) -> None:
        """Apply completed conversion results on the main Qt thread."""
        log.info("Upload post-processing started")

        display_tiff_by_source: dict[str, Path] = {}
        failed_sources: list[str] = []
        for result in results:
            source_key = str(result.source)
            display_tiff = result.exported_files[0] if result.exported_files else None
            self._converted_documents[source_key] = {
                "source": result.source,
                "tiffs": list(result.exported_files),
                "display_tiff": display_tiff,
            }
            if display_tiff:
                display_tiff_by_source[source_key] = display_tiff
            if not result.exported_files:
                failed_sources.append(result.source.name)

        for tiff_path in direct_tiffs:
            source_key = str(tiff_path)
            self._converted_documents[source_key] = {
                "source": tiff_path,
                "tiffs": [tiff_path],
                "display_tiff": tiff_path,
            }
            display_tiff_by_source[source_key] = tiff_path

        log.info("Refreshing Uploaded Files list")
        self._refresh_uploaded_files_list()
        log.info("Uploaded Files list refreshed with %d item(s)", len(self._converted_documents))

        if failed_sources:
            QMessageBox.warning(
                self,
                "Conversion Warning",
                "Some files could not be converted:\n\n" + "\n".join(failed_sources),
            )
        if unsupported:
            skipped = "\n".join(path.name for path in unsupported[:8])
            if len(unsupported) > 8:
                skipped += f"\n... and {len(unsupported) - 8} more"
            QMessageBox.information(
                self,
                "Unsupported Files Skipped",
                f"Only Image Lab documents and TIFF files are supported.\n\nSkipped:\n{skipped}",
            )

        selected_display_tiffs: list[Path] = []
        seen_display_tiffs: set[str] = set()
        for selected in selected_paths:
            candidate = display_tiff_by_source.get(str(selected))
            if candidate is not None:
                key = str(candidate.expanduser().resolve())
                if key not in seen_display_tiffs:
                    seen_display_tiffs.add(key)
                    selected_display_tiffs.append(candidate)

        if selected_display_tiffs:
            existing_loaded = {
                str(Path(state["path"]).expanduser().resolve())
                for state in self._slot_states
                if state["path"]
            }
            was_empty = not existing_loaded
            loaded_new = 0
            deferred_tiffs: list[Path] = []
            for display_index, display_tiff in enumerate(selected_display_tiffs):
                key = str(display_tiff.expanduser().resolve())
                if key in existing_loaded:
                    self._load_converted_tiff_into_viewer(display_tiff, clear_results_on_first=False)
                    continue
                if self._available_image_slot_count() <= 0:
                    deferred_tiffs.extend(selected_display_tiffs[display_index:])
                    break
                if self._load_converted_tiff_into_viewer(
                    display_tiff,
                    clear_results_on_first=was_empty and loaded_new == 0,
                ):
                    existing_loaded.add(key)
                    loaded_new += 1

            if deferred_tiffs:
                if self._files_panel_collapsed:
                    self._set_files_panel_collapsed(False)
                self._status_bar.showMessage(
                    f"Upload complete. {len(deferred_tiffs)} TIFF(s) are ready in Uploaded Files, "
                    f"but all {len(self._image_panels)} image windows are full."
                )
                QMessageBox.information(
                    self,
                    "Upload Complete — Image Windows Full",
                    f"The upload completed successfully, but all {len(self._image_panels)} image windows are already in use.\n\n"
                    f"{len(deferred_tiffs)} new TIFF file(s) are ready in Uploaded Files. "
                    "Remove an image from a window, then click the file there to open it.",
                )
                log.info(
                    "Upload complete: %d TIFF(s) deferred because all %d viewers are full",
                    len(deferred_tiffs),
                    len(self._image_panels),
                )
            else:
                self._status_bar.showMessage(
                    f"Upload complete — opened {loaded_new} new image(s); "
                    f"{len(selected_display_tiffs)} selected TIFF(s) are ready."
                )
        else:
            self._status_bar.showMessage(
                f"Uploaded {len(selected_paths)} file(s). TIFF images are ready in the left file list."
            )
        log.info("Upload post-processing finished")

    def _on_uploaded_file_clicked(self, item: QListWidgetItem) -> None:
        source_key = item.data(Qt.ItemDataRole.UserRole)
        self._open_uploaded_file_entry(str(source_key))

    def _export_all_tiffs(self) -> None:
        all_tiffs: list[Path] = []
        seen: set[str] = set()
        for entry in self._converted_documents.values():
            for tiff_path in entry.get("tiffs", []):
                key = str(Path(tiff_path).expanduser().resolve())
                if key in seen:
                    continue
                seen.add(key)
                all_tiffs.append(Path(tiff_path))
        if not all_tiffs:
            QMessageBox.information(self, "Export All TIFFs", "No TIFF files to export.")
            return

        dest_dir = QFileDialog.getExistingDirectory(
            self,
            "Export All TIFFs",
            str(self._persistence.default_tiff_export_dir()),
        )
        if not dest_dir:
            return

        destination_root = Path(dest_dir).expanduser().resolve()
        destination_root.mkdir(parents=True, exist_ok=True)
        self._persistence.remember_tiff_export_dir(destination_root)

        copied = 0
        for tiff_path in all_tiffs:
            target = self._ensure_unique_path(destination_root / tiff_path.name)
            shutil.copy2(tiff_path, target)
            copied += 1
        QMessageBox.information(
            self,
            "Export Complete",
            f"Exported {copied} TIFF file(s) to:\n{destination_root}",
        )

    def _refresh_rotation_controls(self) -> None:
        has_active = self._active_slot_index is not None and self._image_path is not None
        self.param_panel.set_rotation_controls_enabled(has_active)
        if not has_active:
            self.param_panel.set_rotation_mode_active(False)
            self.param_panel.set_rotation_angle(0.0)
            return
        canvas = self.canvas
        self.param_panel.set_rotation_mode_active(canvas.is_rotation_mode())
        self.param_panel.set_rotation_angle(canvas.get_rotation_angle())

    def _on_rotation_angle_changed_for_slot(self, slot_index: int, angle: float) -> None:
        if self._active_slot_index != slot_index:
            return
        self.param_panel.set_rotation_angle(angle)

    def _on_rotation_mode_changed_for_slot(self, slot_index: int, enabled: bool) -> None:
        if self._active_slot_index != slot_index:
            return
        self.param_panel.set_rotation_mode_active(enabled)
        if not enabled:
            self.param_panel.set_rotation_angle(0.0)

    def _on_custom_rotate_requested(self) -> None:
        if self._active_slot_index is None or self._image_path is None:
            QMessageBox.information(self, "Select Image", "Please select an image first.")
            return

        if not self.canvas.enter_rotation_mode():
            QMessageBox.warning(self, "Rotation", "No image is loaded in the selected window.")
            return

        self.canvas.setFocus()
        self._act_analyze.setEnabled(False)
        self.param_panel.set_rotation_mode_active(True)
        self.param_panel.set_rotation_angle(self.canvas.get_rotation_angle())
        self._refresh_detection_actions()
        self._status_bar.showMessage(
            "Custom rotation mode enabled — drag to align the red crosshair, then press Return/Enter to apply."
        )

    def _on_cancel_rotate_requested(self) -> None:
        if self._active_slot_index is None or self._image_path is None:
            return
        self.canvas.cancel_rotation_mode()
        self._refresh_detection_actions()
        self._refresh_rotation_controls()
        self._status_bar.showMessage("Custom rotation canceled.")

    def _image_operation_snapshot(self) -> dict | None:
        if self._active_slot_index is None or self._image_path is None:
            return None
        return {
            "path": self._image_path,
            "filename": self._image_panels[self._active_slot_index].filename_label.text(),
            "display_transform": image_transform_to_dict(
                self.canvas.get_image_transform_params()
            ),
        }

    def _operation_output_path(self, operation: str) -> Path:
        source_path = Path(self._image_path or "image.tif").expanduser().resolve()
        suffix = source_path.suffix if source_path.suffix else ".tif"
        return self._conversion_cache_dir / (
            f"{source_path.stem}_{operation}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}{suffix}"
        )

    def _commit_image_operation(
        self,
        output_path: Path,
        display_transform: ImageTransformParams,
        snapshot: dict,
        status: str,
    ) -> None:
        if self._active_slot_index is None:
            return
        slot_index = self._active_slot_index
        panel_canvas = self._image_panels[slot_index].canvas
        self._load_rotated_image_preserving_transform(
            panel_canvas,
            str(output_path),
            display_transform,
        )
        panel_canvas.cancel_rotation_mode()
        self._image_panels[slot_index].set_filename(output_path.name)

        self._image_path = str(output_path)
        slot = self._slot_states[slot_index]
        slot.setdefault("image_operation_history", []).append(snapshot)
        slot["path"] = self._image_path
        slot["lane_rects"] = []
        slot["band_roi"] = None
        slot["auto_detections"] = []
        self._lane_rects.clear()
        self._band_roi = None
        self._auto_detections = []
        self.param_panel.set_auto_edit_enabled(False)
        self.canvas.set_auto_edit_mode(False)
        self._refresh_detection_actions()
        self._refresh_rotation_controls()
        self._status_bar.showMessage(status)

    def _on_flip_image_requested(self, *, vertical: bool) -> None:
        if self._active_slot_index is None or self._image_path is None:
            QMessageBox.information(self, "Select Image", "Please select an image first.")
            return
        if self.canvas.is_rotation_mode():
            self.canvas.cancel_rotation_mode()
        snapshot = self._image_operation_snapshot()
        if snapshot is None:
            return
        direction = "vertical" if vertical else "horizontal"
        output_path = self._operation_output_path(f"flip_{direction}")
        try:
            display_transform = flip_display_pixels_to_file(
                self.canvas.current_display_pixels(),
                output_path,
                vertical=vertical,
            )
            self._commit_image_operation(
                output_path,
                display_transform,
                snapshot,
                f"Image flipped {direction}. Use Rotate → Undo Image Operation to revert.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Image Flip Error", f"Failed to flip image:\n{exc}")
            log.error("Image flip failed: %s", exc)

    def _on_undo_image_operation_requested(self) -> None:
        if self._active_slot_index is None or self._image_path is None:
            return
        if self.canvas.is_rotation_mode():
            self.canvas.cancel_rotation_mode()
            self._refresh_detection_actions()
            self._refresh_rotation_controls()
            self._status_bar.showMessage("Custom rotation canceled.")
            return
        slot = self._slot_states[self._active_slot_index]
        history = slot.setdefault("image_operation_history", [])
        if not history:
            self._status_bar.showMessage("No image operation to undo.", 3000)
            return
        snapshot = history.pop()
        previous_path = str(snapshot["path"])
        previous_transform = image_transform_from_dict(snapshot.get("display_transform"))
        try:
            self._load_rotated_image_preserving_transform(
                self.canvas,
                previous_path,
                previous_transform,
            )
        except Exception as exc:
            history.append(snapshot)
            QMessageBox.critical(self, "Undo Image Operation", f"Failed to restore image:\n{exc}")
            log.error("Image operation undo failed: %s", exc)
            return

        self._image_path = previous_path
        slot["path"] = previous_path
        slot["lane_rects"] = []
        slot["band_roi"] = None
        slot["auto_detections"] = []
        self._lane_rects.clear()
        self._band_roi = None
        self._auto_detections = []
        self._image_panels[self._active_slot_index].set_filename(
            str(snapshot.get("filename") or Path(previous_path).name)
        )
        self.param_panel.set_auto_edit_enabled(False)
        self.canvas.set_auto_edit_mode(False)
        self._refresh_detection_actions()
        self._refresh_rotation_controls()
        self._status_bar.showMessage("Undid the last image operation.")

    def _on_rotate_requested(self) -> None:
        if self._active_slot_index is None or self._image_path is None:
            QMessageBox.information(self, "Select Image", "Please select an image first.")
            return
        if not self.canvas.is_rotation_mode():
            QMessageBox.information(self, "Custom Rotate", "Click Custom Rotate first.")
            return

        angle_deg = self.canvas.get_rotation_angle()
        max_deskew_angle = 45.0
        if abs(angle_deg) > max_deskew_angle:
            clamped = max(-max_deskew_angle, min(max_deskew_angle, angle_deg))
            QMessageBox.warning(
                self,
                "Rotation Angle Clamped",
                f"The selected angle ({angle_deg:+.2f}°) is outside the deskew range.\n"
                f"It will be clamped to {clamped:+.2f}°.",
            )
            angle_deg = clamped
        if abs(angle_deg) < 0.01:
            self.canvas.cancel_rotation_mode()
            self._refresh_detection_actions()
            self._refresh_rotation_controls()
            self._status_bar.showMessage("Rotation skipped (angle is near zero).")
            return

        source_path = Path(self._image_path).expanduser().resolve()
        snapshot = self._image_operation_snapshot()
        if snapshot is None:
            return
        rotated_path = self._operation_output_path("rot")

        try:
            applied_rotation = float(angle_deg)
            display_pixels = self.canvas.current_display_pixels()
            log.info(
                "Custom rotate apply: displayed_angle=%+.3f deg, rotation_to_apply=%+.3f deg",
                float(self.canvas.get_rotation_angle()),
                applied_rotation,
            )
            rotated_display_transform = rotate_display_pixels_to_file(
                display_pixels,
                rotated_path,
                angle_deg=angle_deg,
            )
            self._commit_image_operation(
                rotated_path,
                rotated_display_transform,
                snapshot,
                f"Rotation applied ({angle_deg:+.2f}° reference). Use Rotate → Undo Image Operation to revert.",
            )
        except Exception as e:
            QMessageBox.critical(self, "Rotation Error", f"Failed to rotate image:\n{e}")
            log.error("Rotation failed for %s: %s", source_path, e)

    @staticmethod
    def _load_rotated_image_preserving_transform(
        canvas: ImageCanvas,
        image_path: str,
        display_transform: ImageTransformParams,
    ) -> None:
        # load_image() fits every newly loaded pixmap to the viewport.  Custom
        # rotation creates a new, expanded TIFF, so that automatic fit used to
        # make the WB image visibly jump to a different zoom level.  Preserve
        # the existing view transform so source pixels keep the same on-screen
        # scale before and after the operation.
        view_transform = canvas.transform()
        canvas.load_image(image_path)
        canvas.setTransform(view_transform)
        if canvas._pixmap_item is not None:
            canvas.centerOn(canvas._pixmap_item)
        canvas.set_image_transform_params(display_transform)

    def _clear_roi(self) -> None:
        """Clear all ROIs and lane overlays."""
        if self._active_slot_index is None:
            self._status_bar.showMessage("No active image panel to clear.")
            return
        self.canvas.clear_roi()
        self._lane_rects.clear()
        self._band_roi = None
        self._auto_detections = []
        self.param_panel.set_auto_edit_enabled(False)
        self.canvas.set_auto_edit_mode(False)
        self._save_active_slot_state()
        self._refresh_detection_actions()
        self._act_analyze.setEnabled(False)
        self._status_bar.showMessage("ROI cleared.")

    def _on_canvas_roi_cleared_for_slot(self, slot_index: int) -> None:
        """Reset state after the canvas cancels or clears a manual ROI set.
        The canvas has already cleared its visual items; this updates the main-window state."""
        if slot_index != self._active_slot_index:
            # Make the right-clicked panel active first so state is consistent
            self._set_active_slot(slot_index)
        self._lane_rects.clear()
        self._band_roi = None
        self._auto_detections = []
        self.param_panel.set_auto_edit_enabled(False)
        self.canvas.set_auto_edit_mode(False)
        self._save_active_slot_state()
        self._refresh_detection_actions()
        self._act_analyze.setEnabled(False)

    # ── ROI / lane helpers ─────────────────────────────────────────────────────

    def _on_roi_changed(self, roi: QRectF) -> None:
        """User drew large lane ROI → subdivide into lanes."""
        if self._is_wb_plot_workspace_active():
            self._auto_detections = []
            self._band_roi = None
            self._lane_rects = [QRectF(roi)]
            self.canvas.clear_auto_overlays()
            self._act_analyze.setEnabled(False)
            self._status_bar.showMessage(
                f"WB Plot ROI set ({roi.width():.0f} × {roi.height():.0f} px) — select a blot target and press Return/Enter."
            )
            return
        self._auto_detections = []
        self._band_roi = None
        self._act_analyze.setEnabled(False)
        self._recalculate_lanes(roi)
        self.canvas.set_lane_overlays(self._lane_rects)
        self.canvas.set_manual_band_labels(self._lane_rects, None)
        self._refresh_detection_actions()
        self._status_bar.showMessage(
            f"Lane ROI set ({roi.width():.0f} × {roi.height():.0f} px) — "
            f"now draw a band ROI to continue."
        )

    def _on_band_roi_changed(self, band_roi: QRectF) -> None:
        """User drew band ROI → ready to analyze."""
        if self._is_wb_plot_workspace_active():
            self._band_roi = None
            self.canvas.clear_auto_overlays()
            self._status_bar.showMessage("WB Plot mode uses one large ROI only.")
            return
        if self.param_panel.get_mode() != "manual":
            self.canvas.clear_roi()
            self._band_roi = None
            self._auto_detections = []
            self._act_analyze.setEnabled(False)
            self._status_bar.showMessage("Band ROI drawing is only used in Manual mode.")
            return

        self._band_roi = band_roi
        self.canvas.set_manual_band_labels(self._lane_rects, self._band_roi)
        self._act_analyze.setEnabled(True)
        self._status_bar.showMessage(
            f"Band ROI set ({band_roi.width():.0f} × {band_roi.height():.0f} px) — "
            f"click Analyze to measure."
        )

    def _on_params_changed(self) -> None:
        """Lane count changed → recalculate the manual lane subdivision."""
        roi = self.canvas.get_roi()
        if roi:
            if self._is_wb_plot_workspace_active():
                self._lane_rects = [QRectF(roi)]
                self.canvas.clear_auto_overlays()
                self._refresh_detection_actions()
                return
            self._recalculate_lanes(roi)
            self.canvas.set_lane_overlays(self._lane_rects)
            self.canvas.set_manual_band_labels(self._lane_rects, self._band_roi)
        self._refresh_detection_actions()

    def _recalculate_lanes(self, roi: QRectF) -> None:
        """Subdivide main ROI into N equal lane ROIs."""
        n = self.param_panel.get_lane_count()
        lane_w = roi.width() / n
        self._lane_rects = [
            QRectF(roi.x() + i * lane_w, roi.y(), lane_w, roi.height())
            for i in range(n)
        ]

    def _construct_band_rois(self) -> list[QRectF]:
        """
        For each lane, construct a band measurement ROI using:
        - lane's x-range (left, width)
        - band's y-range (top, height)

        Coordinates are scaled from scene space to original image pixel space
        before being passed to the pure-Python measurement pipeline.
        """
        if not self._band_roi:
            raise ValueError("Band ROI not set")

        scale = self.canvas.get_image_scale()

        band_y = self._band_roi.y() * scale
        band_h = self._band_roi.height() * scale

        return [
            QRectF(
                lane.x() * scale,
                band_y,
                lane.width() * scale,
                band_h,
            )
            for lane in self._lane_rects
        ]

    # ── Band detection ─────────────────────────────────────────────────────────

    def _detect_bands(self) -> None:
        from core.band_detector import detect_band_roi

        loaded = self._loaded_slot_indices()
        if len(loaded) > 1 and self._active_slot_index is None:
            QMessageBox.warning(
                self, "Select Image",
                "Please select an image first."
            )
            return
        if len(loaded) == 1:
            self._set_active_slot(loaded[0])

        roi = self.canvas.get_roi()
        if roi is None:
            QMessageBox.warning(self, "No ROI", "Draw a lane ROI first.")
            return
        if self._image_path is None:
            QMessageBox.warning(self, "No Image", "Upload files first.")
            return

        params = self.param_panel.get_params()
        bands_per_lane = params["bands_per_lane"]
        target_band = params["target_band"]
        sensitivity = params.get("sensitivity", 0.5)

        scale = self.canvas.get_image_scale()
        roi_img = QRectF(
            roi.x() * scale, roi.y() * scale,
            roi.width() * scale, roi.height() * scale,
        )

        try:
            band_roi_img = detect_band_roi(
                image_path=self._image_path,
                lane_roi=roi_img,
                bands_per_lane=bands_per_lane,
                target_band=target_band,
                dark_on_light=False,
                sensitivity=sensitivity,
            )
        except Exception as e:
            log.error("Band detection error: %s", e)
            QMessageBox.critical(
                self, "Detection Error",
                f"Band detection failed with an error:\n\n{e}"
            )
            return

        if band_roi_img is None:
            QMessageBox.warning(
                self, "Detection Failed",
                f"Could not detect band {target_band} of {bands_per_lane}.\n"
                "Try adjusting the ROI or band settings, or draw the band ROI manually."
            )
            return

        band_roi_scene = QRectF(
            band_roi_img.x() / scale, band_roi_img.y() / scale,
            band_roi_img.width() / scale, band_roi_img.height() / scale,
        ) if scale > 0 else band_roi_img

        self.canvas.set_band_roi(band_roi_scene)
        self._band_roi = band_roi_scene
        self._act_analyze.setEnabled(True)
        self._status_bar.showMessage(
            f"Band {target_band} detected — review overlay, then click Analyze."
        )

    def _on_auto_rois_changed(self, detections: list[dict]) -> None:
        """Keep WB Plot Auto-Fit review overlays synchronized with the source canvas."""
        self._auto_detections = self._normalize_auto_detections(detections, preserve_target_row=True)
        self.canvas.set_auto_detect_overlays(self._auto_detections)
        total_bands = sum(len(lane["bands"]) for lane in self._auto_detections)
        self._act_analyze.setEnabled(False)
        self._status_bar.showMessage(
            f"Edited WB Plot Auto-Fit ROIs — {total_bands} band ROI(s) currently selected."
        )

    # ── Analysis ───────────────────────────────────────────────────────────────

    def _run_analysis(self) -> None:
        """
        Orchestrate pure-Python band measurement workflow:
        1. Validate ROIs and parameters
        2. Construct band measurement ROIs in memory
        3. Record lightweight debug state in config.json
        4. Launch MeasurementWorker (background thread)
        """
        loaded = self._loaded_slot_indices()
        if len(loaded) > 1 and self._active_slot_index is None:
            QMessageBox.warning(
                self, "Select Image",
                "Please select an image first."
            )
            return
        if len(loaded) == 1:
            self._set_active_slot(loaded[0])

        if self._image_path is None:
            QMessageBox.warning(self, "No Image", "Please open an image first.")
            return

        roi = self.canvas.get_roi()
        band_roi = self.canvas.get_band_roi()
        if roi is None:
            QMessageBox.warning(self, "No ROI", "Please draw a large lane ROI first.")
            return
        if band_roi is None:
            QMessageBox.warning(self, "No Band ROI", "Please draw a band ROI first.")
            return

        try:
            band_rois = self._construct_band_rois()
        except ValueError as e:
            QMessageBox.critical(self, "ROI Error", str(e))
            return
        total_band_rois = len(band_rois)

        self._persistence.remember_analysis_debug(
            image_path=self._image_path,
            mode="manual",
            lane_count=len(self._lane_rects),
            band_count=total_band_rois,
        )
        image_pixels = None
        image_transform = None
        if self.canvas.has_quantitative_image_transform():
            image_pixels = self.canvas.current_analysis_pixels()
            image_transform = image_transform_to_dict(
                self.canvas.get_analysis_transform_params()
            )
        self._start_measurement_worker(
            self._image_path,
            band_rois,
            image_transform=image_transform,
            image_pixels=image_pixels,
        )

    def _refresh_detection_actions(self) -> None:
        """Keep detection controls in sync with image/mode state."""
        has_image = bool(self._loaded_slot_indices())
        is_manual = self.param_panel.get_mode() == "manual"
        has_roi = self.canvas.get_roi() is not None
        rotation_active = self._active_slot_index is not None and self.canvas.is_rotation_mode()

        if rotation_active:
            self.param_panel.set_auto_detect_enabled(False)
            self.param_panel.set_auto_edit_enabled(False)
            self.param_panel.set_detect_enabled(False)
            self._act_analyze.setEnabled(False)
            self._refresh_rotation_controls()
            return

        self.param_panel.set_auto_detect_enabled(has_image)
        self.param_panel.set_auto_edit_enabled((self._active_slot_index is not None) and has_image and bool(self._auto_detections))

        manual_enabled = (self._active_slot_index is not None) and has_image and is_manual and has_roi
        self.param_panel.set_detect_enabled(manual_enabled)
        self._refresh_rotation_controls()

    def _start_measurement_worker(
        self,
        image_path: str,
        band_rois: list,
        image_transform: dict | None = None,
        image_pixels=None,
    ) -> None:
        """Launch MeasurementWorker in background thread."""
        self._act_analyze.setEnabled(False)
        self._status_bar.showMessage("Measuring — please wait…")

        self._worker_thread = QThread()
        self._worker = MeasurementWorker(
            image_path,
            band_rois,
            image_transform=image_transform,
            image_pixels=image_pixels,
        )
        self._worker.moveToThread(self._worker_thread)

        self._worker_thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._status_bar.showMessage)
        self._worker.finished.connect(self._on_measurement_done)   # bound method → AutoConnection → queued
        self._worker.error.connect(self._on_measurement_error)
        self._worker.finished.connect(self._worker_thread.quit)
        self._worker.error.connect(self._worker_thread.quit)
        self._worker_thread.finished.connect(self._worker_thread.deleteLater)

        self._worker_thread.start()

    def _on_measurement_done(self, results: list[dict]) -> None:
        """Aggregate measurement results and display. Runs in main thread."""
        self._act_analyze.setEnabled(True)

        try:
            # Build results DataFrame
            df = pd.DataFrame(results)
            ordered_cols = ["lane", "band", "Area", "Mean", "Min", "Max", "IntDen", "RawIntDen"]
            available_cols = [col for col in ordered_cols if col in df.columns]
            df = df[available_cols]
            df.rename(columns={"lane": "Lane", "band": "Band"}, inplace=True)

            self.results_panel.show_results(df)
            self._status_bar.showMessage(
                f"Done — {len(df)} band ROI(s) measured. Results are in memory; use Export Results to save."
            )
            log.info("Analysis complete: %s rows measured in-memory", len(df))
            self._tutorial_controller.notify_analysis_started()

        except Exception as e:
            QMessageBox.critical(self, "Result Error", f"Failed to compile results:\n{e}")
            log.error("Result error: %s", e)

    def _on_measurement_error(self, message: str) -> None:
        """Handle measurement error."""
        self._act_analyze.setEnabled(True)
        self._status_bar.showMessage("Analysis failed — see error dialog.")
        log.error("Measurement error: %s", message)
        QMessageBox.critical(
            self, "Measurement Error",
            f"Measurement failed:\n\n{message}\n\n"
            "Check that the image file is accessible and is a supported format\n"
            "(TIFF, PNG, JPG — 8-bit or 16-bit grayscale, RGB)."
        )

    def closeEvent(self, event: QCloseEvent) -> None:  # type: ignore[override]
        if self._upload_thread is not None and self._upload_thread.isRunning():
            self._status_bar.showMessage("Upload is still finishing — close the app after it completes.")
            QMessageBox.information(
                self,
                "Upload in Progress",
                "An upload is still being converted. Please wait for it to finish before closing the app.",
            )
            event.ignore()
            return
        shutil.rmtree(self._conversion_cache_dir, ignore_errors=True)
        super().closeEvent(event)
