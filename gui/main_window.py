"""Main application window — orchestrates pure-Python band densitometry."""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from PySide6.QtCore import Qt, QObject, QThread, QRectF, QSize, QSizeF, QEvent
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QSplitter, QVBoxLayout, QStackedWidget,
    QToolBar, QStatusBar, QDockWidget, QMessageBox,
    QFileDialog,  # noqa: F401 - used by the mixins; kept importable from here
    QHBoxLayout, QToolButton, QPushButton, QFrame, QSizePolicy, QLabel, QListWidget, QDialog, QGridLayout, QComboBox,
)

from config.settings import (
    APP_NAME,
    COLOR_ACCENT,
    COLOR_ACCENT_DARK,
    COLOR_ACCENT_LIGHT,
    COLOR_ACCENT_TEXT,
    COLOR_BORDER,
    COLOR_BORDER_LIGHT,
    COLOR_ROW_ALT,
    COLOR_SURFACE,
    COLOR_TEXT,
    COLOR_TEXT_HINT,
    COLOR_TEXT_MUTED,
)
from core.band_detector import group_auto_detected_rows
from core.image_transform import (
    ImageTransformParams,
    apply_geometry_to_display,
    geometry_transform_to_dict,
    image_transform_to_dict,
)
from gui.figure_generation import FigureTypeDialog, ColumnSetupDialog, ColumnTableWindow
from gui.image_canvas import ImageCanvas
from gui.image_transform_dialog import ImageTransformDialog
from gui.param_panel import ParamPanel
from gui.results_panel import ResultsPanel
from gui.redesign_styles import restyle_param_panel
from gui.status_messages import LocalizedStatusBar
from gui.tutorial import TutorialController, TutorialModeDialog
from utils.logger import get_logger
from utils.persistence import AppPersistence
from utils.i18n import LANG_EN, LANG_ZH_CN, tr
from gui.icons import (
    _folder_open_icon,
)
from gui.workers import (
    MeasurementWorker,
    UploadConversionWorker,
)
from gui.image_panel_widgets import (
    _ImagePanelWidget,
)
from gui.main_window_upload import UploadMixin
from gui.main_window_image_ops import ImageOperationsMixin
from gui.main_window_fixed_roi import FixedRoiMixin
from gui.main_window_auto_lanes import AutoLaneMixin

log = get_logger(__name__)

_MAX_IMAGE_PANELS = 4


def _infer_auto_fit_dark_on_light_pixels(
    detector_pixels: np.ndarray,
    search_roi: QRectF | None,
) -> bool:
    """Infer whether the rough ROI contains dark bands on a light background."""
    try:
        pixels = np.asarray(detector_pixels)
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


def _infer_auto_fit_dark_on_light(
    image_path: str,
    search_roi: QRectF | None,
) -> bool:
    """Backward-compatible path wrapper for Auto-Fit polarity inference."""
    from core.band_detector import load_auto_detection_pixels

    try:
        pixels = load_auto_detection_pixels(image_path)
    except Exception:
        log.exception("Could not load WB Auto-Fit pixels; using light-on-dark first")
        return False
    return _infer_auto_fit_dark_on_light_pixels(pixels, search_roi)


def _run_auto_fit_guided_with_polarity_fallback(
    image_path: str,
    *,
    search_roi: QRectF | None,
    expected_lane_count: int | None,
    sensitivity: float = 0.5,
    detector_pixels: np.ndarray | None = None,
) -> tuple[list[dict], dict]:
    """Detect a figure strip and retry with reversed polarity when needed."""
    from core.band_detector import auto_detect_guided, auto_detect_guided_pixels

    common = {
        "sensitivity": sensitivity,
        "search_rect": search_roi,
        "expected_lane_count": expected_lane_count,
        "target_band_row": None,
        "expected_rows_per_lane": None,
        "return_metadata": True,
    }
    if detector_pixels is None:
        primary_dark_on_light = _infer_auto_fit_dark_on_light(
            image_path, search_roi
        )
        detector = auto_detect_guided
        detector_source = image_path
    else:
        presentation_pixels = np.asarray(detector_pixels)
        primary_dark_on_light = _infer_auto_fit_dark_on_light_pixels(
            presentation_pixels, search_roi
        )
        detector = auto_detect_guided_pixels
        detector_source = presentation_pixels

    detections, metadata = detector(
        detector_source,
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

    reversed_detections, reversed_metadata = detector(
        detector_source,
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

# ── Main window ────────────────────────────────────────────────────────────────

class MainWindow(AutoLaneMixin, FixedRoiMixin, ImageOperationsMixin, UploadMixin, QMainWindow):
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
        self._files_panel_last_width = 236
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

        self._persist_layout_on_close = False
        self._layout_memory: dict[str, list[int]] = self._load_layout_memory()
        saved_files_width = self._layout_memory.get("files_width")
        if saved_files_width:
            self._files_panel_last_width = max(
                self._files_panel_min_width,
                min(saved_files_width[0], self._files_panel_max_width),
            )
        self._build_ui()
        self._tutorial_controller = TutorialController(self)
        self._apply_persisted_ui_preferences()
        self._connect_signals()
        self._register_shortcuts()
        self._set_language(self._language, persist=False)
        self.canvas.set_interaction_mode(self._current_mode)

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # ── Top bar: Import · workspace tabs · tutorial · language ────────────
        tb = QToolBar("Main", self)
        tb.setMovable(False)
        tb.setFloatable(False)
        tb.setStyleSheet(f"""
            QToolBar {{
                background-color: {COLOR_SURFACE};
                border: none;
                border-bottom: 1px solid #D9E0DC;
                spacing: 10px;
                padding: 8px 12px;
            }}
            QToolButton#tb_open_btn {{
                background-color: {COLOR_ACCENT};
                border: 1px solid {COLOR_ACCENT};
                border-radius: 8px;
                color: #FFFFFF;
                padding: 8px 16px;
                font-size: 14px;
                font-weight: 600;
            }}
            QToolButton#tb_open_btn:hover {{
                background-color: {COLOR_ACCENT_DARK};
            }}
            QToolButton#tb_open_btn:disabled {{
                background-color: #9DBBAD;
                border-color: #9DBBAD;
            }}
            QFrame#section2_group {{
                background-color: #E9EEEB;
                border: none;
                border-radius: 10px;
            }}
            QPushButton#figure_generation_btn, QPushButton#wb_plot_generation_btn {{
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 7px;
                color: #3B4A43;
                padding: 7px 16px;
                font-size: 14px;
            }}
            QPushButton#figure_generation_btn:hover, QPushButton#wb_plot_generation_btn:hover {{
                background-color: rgba(255, 255, 255, 0.6);
            }}
            QPushButton#figure_generation_btn:checked, QPushButton#wb_plot_generation_btn:checked {{
                background-color: {COLOR_SURFACE};
                border-color: {COLOR_BORDER};
                color: {COLOR_ACCENT_TEXT};
                font-weight: 600;
            }}
            QComboBox#language_selector {{
                background-color: {COLOR_SURFACE};
                border: 1px solid {COLOR_BORDER};
                border-radius: 8px;
                color: #3B4A43;
                padding: 7px 10px;
                min-width: 92px;
                font-size: 13px;
            }}
            QPushButton#tutorial_btn {{
                background-color: {COLOR_SURFACE};
                border: 1px solid {COLOR_BORDER};
                border-radius: 8px;
                color: #3B4A43;
                padding: 7px 14px;
                font-size: 13px;
            }}
            QPushButton#tutorial_btn:hover {{
                background-color: {COLOR_ACCENT_LIGHT};
                border-color: {COLOR_ACCENT};
            }}
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
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_toolbar_action = tb.addWidget(open_btn)

        # The two workspaces as a segmented control.  The attribute names are
        # kept from the earlier toolbar so the tutorial keeps finding them.
        self._workspace_toolbar_group = QFrame()
        self._workspace_toolbar_group.setObjectName("section2_group")
        workspace_layout = QHBoxLayout(self._workspace_toolbar_group)
        workspace_layout.setContentsMargins(4, 4, 4, 4)
        workspace_layout.setSpacing(4)
        self._figure_generation_btn = QPushButton("① Quantify && Plot")
        self._figure_generation_btn.setObjectName("figure_generation_btn")
        self._figure_generation_btn.setCheckable(True)
        self._figure_generation_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._figure_generation_btn.clicked.connect(self._on_figure_generation_clicked)
        workspace_layout.addWidget(self._figure_generation_btn)
        self._wb_plot_generation_btn = QPushButton("② Figure Layout")
        self._wb_plot_generation_btn.setObjectName("wb_plot_generation_btn")
        self._wb_plot_generation_btn.setCheckable(True)
        self._wb_plot_generation_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._wb_plot_generation_btn.clicked.connect(self._on_wb_plot_mode)
        workspace_layout.addWidget(self._wb_plot_generation_btn)
        self._workspace_toolbar_action = tb.addWidget(self._workspace_toolbar_group)
        self._workspace_toolbar_action.setVisible(True)

        # Keep tutorial and language anchored at the far right.
        stretch = QWidget()
        stretch.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        stretch.setStyleSheet("background: transparent;")
        tb.addWidget(stretch)

        self._tutorial_btn = QPushButton("New User Tutorial")
        self._tutorial_btn.setObjectName("tutorial_btn")
        self._tutorial_toolbar_action = tb.addWidget(self._tutorial_btn)

        self._language_combo = QComboBox()
        self._language_combo.setObjectName("language_selector")
        self._language_combo.addItem("English", LANG_EN)
        self._language_combo.addItem("中文", LANG_ZH_CN)
        self._language_combo.setToolTip("Language")
        self._language_toolbar_action = tb.addWidget(self._language_combo)

        # ── Left sidebar: files on top, lane settings below (both visible) ────
        self._main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._main_splitter.setChildrenCollapsible(False)
        self._main_splitter.setHandleWidth(7)

        self._files_panel = QFrame()
        self._files_panel.setObjectName("uploaded_files_panel")
        self._files_panel.setStyleSheet(f"""
            QFrame#uploaded_files_panel {{
                background-color: {COLOR_SURFACE};
                border: none;
                border-right: 1px solid #D9E0DC;
            }}
            QLabel#files_panel_title {{
                background: transparent;
                color: {COLOR_TEXT_MUTED};
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton#files_panel_toggle {{
                background-color: {COLOR_SURFACE};
                border: 1px solid {COLOR_BORDER};
                border-radius: 6px;
                color: #3B4A43;
                min-width: 22px;
                max-width: 22px;
                min-height: 22px;
                max-height: 22px;
                padding: 0px;
                font-size: 11px;
                font-weight: 700;
            }}
            QPushButton#files_panel_toggle:hover {{
                background-color: {COLOR_ACCENT_LIGHT};
                border-color: {COLOR_ACCENT};
            }}
            QListWidget#uploaded_files_list {{
                background-color: {COLOR_SURFACE};
                border: none;
                color: {COLOR_TEXT};
                font-size: 13px;
                padding: 0px;
                outline: none;
            }}
            QListWidget#uploaded_files_list::item {{
                padding: 0px;
                border-radius: 6px;
            }}
            QListWidget#uploaded_files_list::item:selected {{
                background-color: {COLOR_ACCENT_LIGHT};
                color: {COLOR_ACCENT_TEXT};
            }}
            QLabel#files_drop_hint {{
                background: transparent;
                border: 1px dashed #B5C2BC;
                border-radius: 6px;
                color: {COLOR_TEXT_HINT};
                font-size: 12px;
                padding: 8px;
            }}
            QPushButton#export_all_tiffs_btn {{
                background-color: {COLOR_SURFACE};
                border: 1px solid {COLOR_BORDER};
                border-radius: 8px;
                color: #3B4A43;
                padding: 7px 10px;
                font-size: 12px;
            }}
            QPushButton#export_all_tiffs_btn:hover {{
                background-color: {COLOR_ACCENT_LIGHT};
                border-color: {COLOR_ACCENT};
            }}
            QPushButton#export_all_tiffs_btn:disabled {{
                color: #9AA8A1;
                border-color: {COLOR_BORDER_LIGHT};
            }}
        """)
        files_layout = QVBoxLayout(self._files_panel)
        files_layout.setContentsMargins(10, 10, 10, 10)
        files_layout.setSpacing(8)
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
        self._files_list.setMinimumHeight(72)
        files_layout.addWidget(self._files_list, 1)
        self._files_drop_hint = QLabel("Drop .scn / .tif files here")
        self._files_drop_hint.setObjectName("files_drop_hint")
        self._files_drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._files_drop_hint.setWordWrap(True)
        files_layout.addWidget(self._files_drop_hint)
        self._export_all_tiffs_btn = QPushButton("Export All TIFFs")
        self._export_all_tiffs_btn.setObjectName("export_all_tiffs_btn")
        self._export_all_tiffs_btn.setEnabled(False)
        files_layout.addWidget(self._export_all_tiffs_btn)

        # Lane settings live under the file list so both stay on screen.
        self.param_panel = ParamPanel()
        restyle_param_panel(self.param_panel)
        self._param_panel_host = QWidget()
        self._param_panel_host.setObjectName("param_panel_host")
        self._param_panel_host.setStyleSheet(
            "QWidget#param_panel_host { background: transparent; "
            "border-top: 1px solid #E4E9E6; }"
        )
        self._param_panel_layout = QVBoxLayout(self._param_panel_host)
        self._param_panel_layout.setContentsMargins(0, 6, 0, 0)
        self._param_panel_layout.setSpacing(0)
        self._param_panel_layout.addWidget(self.param_panel)
        files_layout.addWidget(self._param_panel_host)
        self._main_splitter.addWidget(self._files_panel)

        # ── Workspace: [image over results] | [data table over bar chart] ─────
        self._workspace_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._workspace_splitter.setChildrenCollapsible(False)
        self._workspace_splitter.setHandleWidth(7)
        self._workspace_splitter.setOpaqueResize(True)

        self._center_splitter = QSplitter(Qt.Orientation.Vertical)
        self._center_splitter.setChildrenCollapsible(False)
        self._center_splitter.setHandleWidth(7)
        self._center_splitter.setOpaqueResize(True)

        self._viewer_column = QWidget()
        self._viewer_column.setObjectName("viewer_column")
        viewer_column_layout = QVBoxLayout(self._viewer_column)
        viewer_column_layout.setContentsMargins(8, 8, 4, 4)
        viewer_column_layout.setSpacing(6)

        # Analyze sits right above the image it measures.
        self._analyze_toolbar_group = QFrame()
        self._analyze_toolbar_group.setObjectName("analyze_bar")
        self._analyze_toolbar_group.setStyleSheet(f"""
            QFrame#analyze_bar {{ background: transparent; border: none; }}
            QLabel#analyze_hint {{
                background: transparent;
                color: {COLOR_TEXT_MUTED};
                font-size: 12px;
            }}
            QToolButton#analyze_btn {{
                background-color: {COLOR_ACCENT};
                border: 1px solid {COLOR_ACCENT};
                border-radius: 8px;
                color: #FFFFFF;
                font-size: 14px;
                font-weight: 600;
                padding: 7px 20px;
            }}
            QToolButton#analyze_btn:hover {{
                background-color: {COLOR_ACCENT_DARK};
            }}
            QToolButton#analyze_btn:disabled {{
                background-color: #E9EEEB;
                color: #8A9891;
                border-color: #D9E0DC;
            }}
        """)
        analyze_layout = QHBoxLayout(self._analyze_toolbar_group)
        analyze_layout.setContentsMargins(0, 0, 0, 0)
        analyze_layout.setSpacing(8)
        self._analyze_hint = QLabel(
            "Draw the lane ROI, then the band ROI, then press Enter."
        )
        self._analyze_hint.setObjectName("analyze_hint")
        analyze_layout.addWidget(self._analyze_hint, 1)
        self._analyze_toolbar_btn = _make_action_button(
            self._act_analyze,
            "analyze_btn",
        )
        self._analyze_toolbar_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        analyze_layout.addWidget(self._analyze_toolbar_btn)
        self._analyze_toolbar_group.setVisible(False)
        viewer_column_layout.addWidget(self._analyze_toolbar_group)

        self._viewer_container = QWidget()
        self._viewer_container.setObjectName("workspace_viewer_panel")
        self._viewer_layout = QGridLayout(self._viewer_container)
        self._viewer_layout.setContentsMargins(0, 0, 0, 0)
        self._viewer_layout.setSpacing(6)
        self._image_panels = [_ImagePanelWidget() for _ in range(_MAX_IMAGE_PANELS)]
        for panel in self._image_panels:
            panel.setVisible(False)
        viewer_column_layout.addWidget(self._viewer_container, 1)
        # Keep `self.canvas` alias for backward compatibility with existing methods.
        self.canvas = self._image_panels[0].canvas

        # Empty state shown until the first image is loaded.
        self._empty_state = QLabel()
        self._empty_state.setObjectName("viewer_empty_state")
        self._empty_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_state.setWordWrap(True)
        self._empty_state.setTextFormat(Qt.TextFormat.RichText)
        self._empty_state.setStyleSheet(
            "QLabel#viewer_empty_state {"
            f"background-color: {COLOR_SURFACE};"
            "border: 1px dashed #B5C2BC;"
            "border-radius: 10px;"
            f"color: {COLOR_TEXT_MUTED};"
            "font-size: 14px;"
            "padding: 32px;"
            "}"
        )
        viewer_column_layout.addWidget(self._empty_state, 1)
        self._center_splitter.addWidget(self._viewer_column)

        # Results sit under the image.  The attribute keeps its old name.
        self.results_panel = ResultsPanel(
            default_export_dir_provider=self._persistence.default_results_export_dir,
            export_dir_changed=self._persistence.remember_results_export_dir,
        )
        self._results_dock = QFrame()
        self._results_dock.setObjectName("results_host")
        self._results_dock.setStyleSheet(
            "QFrame#results_host { background: transparent; border: none; }"
        )
        results_host_layout = QVBoxLayout(self._results_dock)
        results_host_layout.setContentsMargins(8, 4, 4, 8)
        results_host_layout.setSpacing(0)
        results_host_layout.addWidget(self.results_panel)
        self._results_dock.setMinimumHeight(120)
        self._results_dock.setVisible(False)
        self._center_splitter.addWidget(self._results_dock)
        self._center_splitter.setStretchFactor(0, 4)
        self._center_splitter.setStretchFactor(1, 1)
        self._center_splitter.setSizes([560, 190])
        self._workspace_splitter.addWidget(self._center_splitter)

        self._figure_workspace = QWidget()
        self._figure_workspace.setMinimumWidth(360)
        self._figure_workspace.setStyleSheet(
            "QWidget#figure_workspace { background-color: transparent; border: none; }"
        )
        self._figure_workspace.setObjectName("figure_workspace")
        figure_workspace_layout = QVBoxLayout(self._figure_workspace)
        figure_workspace_layout.setContentsMargins(4, 8, 8, 8)
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
        self._figure_table_placeholder.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 12px; padding: 10px;")
        self._figure_table_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._figure_table_host_layout.addWidget(self._figure_table_placeholder)

        self._figure_preview_host = QWidget()
        self._figure_preview_host.setObjectName("workspace_figure_panel")
        self._figure_preview_host_layout = QVBoxLayout(self._figure_preview_host)
        self._figure_preview_host_layout.setContentsMargins(0, 0, 0, 0)
        self._figure_preview_host_layout.setSpacing(0)
        self._figure_preview_placeholder = QLabel("Generated figure preview will appear here.")
        self._figure_preview_placeholder.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 12px; padding: 10px;")
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
        self._wb_plot_placeholder.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 12px; padding: 10px;")
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

        self._main_splitter.setStretchFactor(0, 0)
        self._main_splitter.setStretchFactor(1, 1)
        self._files_panel.setMinimumWidth(self._files_panel_min_width)
        self._files_panel.setMaximumWidth(self._files_panel_max_width)
        self._main_splitter.setSizes([self._files_panel_last_width, 1140])

        # ── App shell ─────────────────────────────────────────────────────────
        self._mode_container = QStackedWidget()
        self._mode_container.addWidget(self._main_splitter)
        self._mode_container.setCurrentWidget(self._main_splitter)
        self.setCentralWidget(self._mode_container)
        self.setAcceptDrops(True)

        # Home: sidebar with the file list, empty-state canvas, no analysis
        # or figure panels until images are imported or a workspace is chosen.
        self._param_panel_host.setVisible(False)

        # Status bar
        self._status_bar = LocalizedStatusBar(lambda: self._language)
        self.setStatusBar(self._status_bar)
        self._refresh_image_panel_layout()
        self._sync_workspace_tabs()
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
            self._files_drop_hint.setVisible(False)
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
            self._files_drop_hint.setVisible(True)
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
        workspace_width = max(200, total - files_width)
        self._main_splitter.setSizes([files_width, workspace_width])
        if persist:
            self._persistence.remember_ui_state(files_panel_collapsed=collapsed)

    def _sync_param_panel_visibility(self) -> None:
        """Show lane settings under the file list in the Quantify workspace."""
        show_roi = (
            not self._files_panel_collapsed
            and not self._home_mode_active
            and not self._is_wb_plot_workspace_active()
        )
        self._param_panel_host.setVisible(show_roi)

    def _sync_workspace_tabs(self) -> None:
        """Highlight the top tab of the workspace that is on screen."""
        quant = not self._home_mode_active and not self._is_wb_plot_workspace_active()
        layout = not self._home_mode_active and self._is_wb_plot_workspace_active()
        self._figure_generation_btn.setChecked(quant)
        self._wb_plot_generation_btn.setChecked(layout)

    def _sync_empty_state(self, has_images: bool) -> None:
        """Swap the image grid for the import hint while nothing is loaded."""
        self._empty_state.setVisible(not has_images)
        self._viewer_container.setVisible(has_images)

    def _apply_persisted_ui_preferences(self) -> None:
        config = self._persistence.read_config()
        ui = config.get("ui", {})
        saved_mode = ui.get("mode")
        if isinstance(saved_mode, str):
            self.param_panel.set_mode(saved_mode)
            self._current_mode = self.param_panel.get_mode()
        collapsed = ui.get("sidebar_collapsed")
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
        # "&&" shows a literal ampersand instead of a keyboard mnemonic.
        self._figure_generation_btn.setText(
            tr("① Quantify & Plot", language).replace("&", "&&")
        )
        self._wb_plot_generation_btn.setText(tr("② Figure Layout", language))
        self._files_drop_hint.setText(tr("Drop .scn / .tif files here", language))
        self._analyze_hint.setText(
            tr("Draw the lane ROI, then the band ROI, then press Enter.", language)
        )
        self._empty_state.setText(
            "<div style='font-size:16px; font-weight:600; color:#1C2622;'>"
            + tr("Drop .scn or .tif files here, or click Import Images", language)
            + "</div><br>"
            + tr("1. Import images", language) + "<br>"
            + tr("2. Draw the lanes and the band", language) + "<br>"
            + tr("3. Analyze — the table and bar chart fill in automatically", language)
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
            panel.fit_btn.setText(tr("Fit", language))
            panel.fit_btn.setToolTip(tr("Fit the whole image to this panel", language))
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

    def _tutorial_images_ready(self) -> bool:
        """Return whether both bundled tutorial canvases have quantitative data."""
        required = {
            str(path.expanduser().resolve())
            for path in self._tutorial_asset_paths().values()
        }
        ready: set[str] = set()
        for index, state in enumerate(self._slot_states):
            current = state.get("path")
            if not current:
                continue
            resolved = str(Path(current).expanduser().resolve())
            canvas = self._image_panels[index].canvas
            if (
                resolved in required
                and not canvas.is_loading()
                and canvas.has_image_transform_source()
            ):
                ready.add(resolved)
        return ready == required

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
        self._analyze_toolbar_group.setVisible(visible)

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
        self._sync_workspace_tabs()
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
            f"color: {COLOR_TEXT_MUTED}; font-size: 12px; padding: 10px;"
        )
        self._figure_table_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._figure_table_host_layout.addWidget(self._figure_table_placeholder)
        self._figure_preview_placeholder = QLabel(
            tr("Generated figure preview will appear here.", self._language)
        )
        self._figure_preview_placeholder.setStyleSheet(
            f"color: {COLOR_TEXT_MUTED}; font-size: 12px; padding: 10px;"
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
            f"color: {COLOR_TEXT_MUTED}; font-size: 12px; padding: 10px;"
        )
        self._wb_plot_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._wb_plot_workspace_layout.addWidget(self._wb_plot_placeholder)

        self._show_home_page()
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
        self._act_analyze.setEnabled(
            self._band_roi is not None or self._quant_auto_active()
        )
        self._workspace_toolbar_action.setVisible(True)
        self._set_workspace_focus_target("image")
        self._sync_workspace_tabs()
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
            self._act_analyze.setEnabled(
            self._band_roi is not None or self._quant_auto_active()
        )
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
        def _style(name: str, active: bool) -> str:
            border = COLOR_ACCENT if active else "#D9E0DC"
            return (
                f"QWidget#{name} {{ background-color: {COLOR_SURFACE}; "
                f"border: 1px solid {border}; border-radius: 10px; }}"
            )

        self._viewer_container.setStyleSheet(
            _style("workspace_viewer_panel", self._workspace_focus_target == "image")
        )
        self._figure_table_host.setStyleSheet(
            _style("workspace_table_panel", self._workspace_focus_target == "table")
        )
        self._figure_preview_host.setStyleSheet(
            _style("workspace_figure_panel", self._workspace_focus_target == "figure")
        )

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
        self._embedded_preview_placeholder.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 12px; padding: 10px;")
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
        # Results flow straight into the table and chart, except during the
        # tutorial, which teaches the manual transfer step by step.
        table_window.set_auto_figure_enabled(not self._tutorial_controller.active)
        table_window.set_language(self._language)
        self._figure_windows = [table_window]
        self._remember_layout_sizes()
        self._show_figure_workspace("densitometry")
        self._apply_layout_sizes()
        self._set_workspace_focus_target("table")

    def _on_figure_generation_clicked(self) -> None:
        """Open the Quantify & Plot workspace: image, results, table, chart."""
        self._home_mode_active = False
        self._set_import_toolbar_visible(True)
        self._set_analyze_toolbar_visible(True)
        self._workspace_toolbar_action.setVisible(True)
        self._mode_container.setCurrentWidget(self._main_splitter)
        self._remember_layout_sizes()
        self._show_figure_workspace("densitometry")
        self._apply_layout_sizes()
        self._sync_workspace_tabs()

        tutorial_direct_column = (
            self._tutorial_controller.active
            and self._tutorial_controller.mode == "densitometry"
            and self._tutorial_controller.current_step_key == "create_column_table"
        )
        if tutorial_direct_column and self._embedded_column_table is not None:
            # The lesson starts from its own 3 × 2 table, not the user's.
            self._embedded_column_table.set_figure_preview_host(None)
            self._embedded_column_table.deleteLater()
            self._embedded_column_table = None
            self._figure_windows.clear()

        if self._embedded_column_table is not None:
            self._set_workspace_focus_target("image")
            self._status_bar.showMessage(
                tr("Returned to Data Graphs workspace.", self._language),
                3000,
            )
            return

        samples, replicates = 3, 3
        if tutorial_direct_column:
            # The tutorial still walks through the setup dialog step by step.
            setup = ColumnSetupDialog(self, default_samples=3, default_replicates=2)
            setup.set_language(self._language)
            self._tutorial_controller.notify_column_setup_opened()
            if setup.exec() != QDialog.DialogCode.Accepted:
                self._tutorial_controller.notify_column_setup_cancelled()
                return
            setup_values = setup.get_input()
            if setup_values.samples <= 0 or setup_values.replicates <= 0:
                QMessageBox.warning(
                    self,
                    tr("Invalid Input", self._language),
                    tr(
                        "Number of samples and replicates must be positive integers.",
                        self._language,
                    ),
                )
                return
            samples, replicates = setup_values.samples, setup_values.replicates

        # Outside the tutorial the table simply appears with a sensible
        # default; its size is edited in place above the table.
        table_window = ColumnTableWindow(
            samples=samples,
            replicates=replicates,
            parent=self,
        )
        table_window.set_language(self._language)
        self._mount_column_table_in_workspace(table_window)
        self._set_workspace_focus_target("image")
        self._sync_workspace_tabs()
        self._status_bar.showMessage(
            tr("Opened Densitometry workspace.", self._language),
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
        canvas.clear_image()

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
        self._act_analyze.setEnabled(
            self._band_roi is not None or self._quant_auto_active()
        )
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
        self._sync_empty_state(bool(loaded))
        self._relayout_image_panels(loaded)

        if len(loaded) == 1:
            idx = loaded[0]
            for panel_idx in range(len(self._image_panels)):
                self._set_panel_checkbox(panel_idx, panel_idx == idx, False)
                self._set_panel_transform_enabled(
                    panel_idx,
                    panel_idx == idx and not self._image_panels[panel_idx].canvas.is_loading(),
                )
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
                self._set_panel_transform_enabled(
                    idx,
                    idx in loaded and not self._image_panels[idx].canvas.is_loading(),
                )
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

    def _on_panel_fit_requested(self, slot_index: int) -> None:
        """Fit one image to its panel and make it the active image."""
        if slot_index not in self._loaded_slot_indices():
            return
        self._set_active_slot_from_interaction(slot_index)
        self._image_panels[slot_index].canvas.reset_zoom()

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
        self._remember_layout_sizes()
        self._show_figure_workspace("wb_plot")
        self._apply_layout_sizes()
        self._set_workspace_focus_target("figure")
        self._sync_workspace_tabs()
        self._status_bar.showMessage(
            tr("Opened WB Figure Layout workspace.", self._language),
            3000,
        )

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
            "geometry_transform": geometry_transform_to_dict(
                self.canvas.get_geometry_transform()
            ),
            "auto_detections": self._clone_auto_detections(self._auto_detections),
            "image_size": self.canvas.image_scene_size(),
            "raw_image_size": self.canvas.raw_image_size(),
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
            from core.band_detector import load_auto_detection_pixels

            # Auto-Fit deliberately bypasses the sampled GUI preview and tone
            # controls.  Geometry is applied to the detector's established
            # full-resolution 8-bit representation, placing every subsequent
            # operation in presentation coordinates.
            detector_pixels = load_auto_detection_pixels(image_path)
            detector_pixels = apply_geometry_to_display(
                detector_pixels,
                self.canvas.get_geometry_transform(),
            )
            detector_height, detector_width = detector_pixels.shape
            source["image_size"] = QSizeF(detector_width, detector_height)
            # With a known lane count, use the same lane-fitting detector as
            # densitometry auto-detect; the older guided detector remains the
            # fallback (no lane count, or nothing found).
            lane_fit = self._wb_plot_lane_fit_detections(search_roi, expected_lane_count)
            if lane_fit is not None:
                detections, metadata = lane_fit
            else:
                detections, metadata = _run_auto_fit_guided_with_polarity_fallback(
                    image_path,
                    sensitivity=0.5,
                    search_roi=search_roi if isinstance(search_roi, QRectF) else None,
                    expected_lane_count=expected_lane_count,
                    detector_pixels=detector_pixels,
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
        self.canvas.set_auto_detect_overlays(
            self._auto_detections, quiet_lanes=lane_fit is not None
        )
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
            panel.canvas.image_preview_ready.connect(
                lambda path, i=idx: self._on_image_preview_ready_for_slot(i, path)
            )
            panel.canvas.image_load_finished.connect(
                lambda path, i=idx: self._on_image_load_finished_for_slot(i, path)
            )
            panel.canvas.image_load_failed.connect(
                lambda path, message, i=idx: self._on_image_load_failed_for_slot(i, path, message)
            )
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
            panel.fit_btn.clicked.connect(
                lambda _=False, i=idx: self._on_panel_fit_requested(i)
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
        self.param_panel.auto_lane_detect_requested.connect(self._run_quant_auto_lane_detect)
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
        self._leave_quant_auto_review()
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
            if self._quant_auto_active():
                # Lane count changed during auto review → detect again.
                self._run_quant_auto_lane_detect()
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
        if not self.canvas.get_geometry_transform().is_identity():
            QMessageBox.information(
                self,
                "Manual Band ROI Required",
                "After Rotate/Flip, draw the band ROI manually. This preserves "
                "the exact inverse-mapped polygon instead of approximating it "
                "with a raw-image bounding box.",
            )
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
        if self._quant_auto_active():
            self._on_quant_auto_rois_edited(detections)
            return
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

        if self._quant_auto_active():
            self._run_quant_auto_analysis()
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
        raw_band_rois = self.canvas.map_canvas_rois_to_raw(band_rois)
        self._start_measurement_worker(
            self._image_path,
            raw_band_rois,
        )

    def _refresh_detection_actions(self) -> None:
        """Keep detection controls in sync with image/mode state."""
        has_image = (
            bool(self._loaded_slot_indices())
            and self._active_slot_index is not None
            and not self.canvas.is_loading()
            and self.canvas.has_image_transform_source()
        )
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
    ) -> None:
        """Launch MeasurementWorker in background thread."""
        if self._worker_thread is not None:
            # Analyze can be re-enabled (e.g. by drawing a new band ROI) while
            # a measurement is still running. Replacing a running QThread
            # would destroy it mid-run and abort the app.
            self._status_bar.showMessage("A measurement is already running — please wait…")
            return
        self._act_analyze.setEnabled(False)
        self._status_bar.showMessage("Measuring — please wait…")

        self._worker_thread = QThread(self)
        self._worker = MeasurementWorker(
            image_path,
            band_rois,
        )
        self._worker.moveToThread(self._worker_thread)

        self._worker_thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._status_bar.showMessage)
        self._worker.finished.connect(self._on_measurement_done)   # bound method → AutoConnection → queued
        self._worker.error.connect(self._on_measurement_error)
        self._worker.finished.connect(self._worker_thread.quit)
        self._worker.error.connect(self._worker_thread.quit)
        self._worker_thread.finished.connect(self._on_measurement_thread_finished)
        self._worker_thread.finished.connect(self._worker_thread.deleteLater)

        self._worker_thread.start()

    def _on_measurement_thread_finished(self) -> None:
        # Drop the references before deleteLater runs so nothing can touch a
        # deleted QThread, and so the next analysis can start.
        self._worker = None
        self._worker_thread = None

    def _on_measurement_done(self, results: list[dict]) -> None:
        """Aggregate measurement results and display. Runs in main thread."""
        self._act_analyze.setEnabled(True)

        try:
            # Build results DataFrame
            df = pd.DataFrame(results)
            ordered_cols = [
                "lane", "band", "Area", "Mean", "Min", "Max", "IntDen", "RawIntDen",
                "Background", "Net Mean", "Net IntDen",
            ]
            available_cols = [col for col in ordered_cols if col in df.columns]
            df = df[available_cols]
            df.rename(columns={"lane": "Lane", "band": "Band"}, inplace=True)

            self.results_panel.show_results(df)
            self._status_bar.showMessage(
                tr(
                    "Done — {count} band ROI(s) measured. Results are in memory; use Export Results to save.",
                    self._language,
                    count=len(df),
                )
            )
            self._auto_fill_table_from_results(df)
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

    # ── Results → data table → chart ───────────────────────────────────────────

    @staticmethod
    def _split_results_into_band_rows(df: pd.DataFrame) -> list[pd.DataFrame]:
        if "Lane" not in df.columns or len(df) <= 1:
            return [df]
        starts = [0]
        previous: float | None = None
        for position, lane in enumerate(df["Lane"].tolist()):
            try:
                current = float(lane)
            except (TypeError, ValueError):
                return [df]
            if previous is not None and current <= previous:
                starts.append(position)
            previous = current
        bounds = starts + [len(df)]
        return [df.iloc[bounds[i]:bounds[i + 1]] for i in range(len(starts))]

    def _auto_fill_table_from_results(self, df: pd.DataFrame) -> None:
        """Send one finished analysis run into the data table and chart."""
        table = self._embedded_column_table
        if (
            table is None
            or self._tutorial_controller.active
            or not table.auto_fill_enabled()
            or "Mean" not in df.columns
        ):
            return
        # One table row per band row.  A manual run measures a single band
        # across the lanes; an auto-detected run can hold several rows.
        # Rows are listed lane by lane, so the lane number starting over
        # marks the next band row.
        runs = self._split_results_into_band_rows(df)
        groups, _replicates = table.table_dimensions()
        filled_rows: list[str] = []
        written_total = 0
        extra = 0
        for run in runs:
            values = [
                self.results_panel._format_cell(value)
                for value in run["Mean"].tolist()
            ]
            values = [value for value in values if value]
            if not values:
                continue
            filled = table.autofill_next(values)
            if filled is None:
                break
            description, written = filled
            filled_rows.append(description)
            written_total += written
            groups, _replicates = table.table_dimensions()
            extra = max(extra, len(values) - groups)
        if not filled_rows:
            self._status_bar.showMessage(
                tr(
                    "Analysis done. The data table is full — click a row to overwrite it, or add replicates.",
                    self._language,
                ),
                6000,
            )
            return
        if len(filled_rows) > 1:
            message = tr(
                "Analysis done — {count} value(s) filled into {rows} table rows.",
                self._language,
                count=written_total,
                rows=len(filled_rows),
            )
        else:
            message = tr(
                "Analysis done — {count} value(s) filled into {row}.",
                self._language,
                count=written_total,
                row=filled_rows[0],
            )
        if extra > 0:
            message += " " + tr(
                "{extra} extra lane(s) did not fit; raise Groups to include them.",
                self._language,
                extra=extra,
            )
        self._status_bar.showMessage(message, 6000)

    # ── Drag & drop import ─────────────────────────────────────────────────────

    def _droppable_paths(self, event) -> list[str]:
        mime = event.mimeData()
        if mime is None or not mime.hasUrls():
            return []
        allowed = set(self._supported_upload_exts) | set(self._direct_tiff_exts)
        return [
            url.toLocalFile()
            for url in mime.urls()
            if url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() in allowed
        ]

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if self._droppable_paths(event) and not self._tutorial_controller.active:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._droppable_paths(event) and not self._tutorial_controller.active:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # type: ignore[override]
        paths = self._droppable_paths(event)
        if not paths or self._tutorial_controller.active:
            event.ignore()
            return
        event.acceptProposedAction()
        self._import_paths(paths)

    # ── Remembered layout ──────────────────────────────────────────────────────

    def _current_workspace_key(self) -> str | None:
        if self._home_mode_active or self._figure_workspace.isHidden():
            return None
        return "workspace_layout" if self._is_wb_plot_workspace_active() else "workspace_quant"

    def _remember_layout_sizes(self) -> None:
        """Note where the dividers are before the workspace changes."""
        memory = self._layout_memory
        key = self._current_workspace_key()
        if key is not None:
            sizes = [int(v) for v in self._workspace_splitter.sizes()]
            if len(sizes) == 2 and all(v > 0 for v in sizes):
                memory[key] = sizes
        if key == "workspace_quant":
            for name, splitter in (
                ("center", self._center_splitter),
                ("figure", self._figure_right_splitter),
            ):
                sizes = [int(v) for v in splitter.sizes()]
                if len(sizes) == 2 and all(v > 0 for v in sizes):
                    memory[name] = sizes
        if not self._files_panel_collapsed:
            memory["files_width"] = [int(self._files_panel_last_width)]

    def _apply_layout_sizes(self) -> None:
        """Put the dividers back where the user last left them."""
        memory = self._layout_memory
        key = self._current_workspace_key()
        if key is None:
            return
        sizes = memory.get(key)
        total = max(sum(self._workspace_splitter.sizes()), 1)
        if sizes and len(sizes) == 2:
            scale = total / max(sum(sizes), 1)
            self._workspace_splitter.setSizes([max(1, int(v * scale)) for v in sizes])
        elif key == "workspace_layout":
            # The preview plus the fixed step panel need a little over half.
            left = max(360, int(total * 0.42))
            self._workspace_splitter.setSizes([left, max(360, total - left)])
        if key == "workspace_quant":
            for name, splitter in (
                ("center", self._center_splitter),
                ("figure", self._figure_right_splitter),
            ):
                sizes = memory.get(name)
                if sizes and len(sizes) == 2:
                    splitter.setSizes(sizes)

    def _load_layout_memory(self) -> dict[str, list[int]]:
        try:
            saved = self._persistence.read_config().get("ui", {}).get("splitters", {})
        except Exception:
            return {}
        if not isinstance(saved, dict):
            return {}
        return {
            str(name): [int(v) for v in sizes]
            for name, sizes in saved.items()
            if isinstance(sizes, list)
            and sizes
            and all(isinstance(v, int) and v > 0 for v in sizes)
        }

    def _save_layout_state(self) -> None:
        """Persist the divider positions for the next launch."""
        self._remember_layout_sizes()
        if not self._persist_layout_on_close or not self._layout_memory:
            return
        try:
            self._persistence.update_config(ui={"splitters": dict(self._layout_memory)})
        except Exception:
            log.exception("Could not save the panel layout")

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
        if self._worker_thread is not None:
            # Let an in-flight measurement finish instead of destroying its
            # thread while it is still running.
            self._worker_thread.quit()
            self._worker_thread.wait()
        for panel in self._image_panels:
            panel.canvas.cancel_image_load(wait=True)
        shutil.rmtree(self._conversion_cache_dir, ignore_errors=True)
        self._save_layout_state()
        super().closeEvent(event)
