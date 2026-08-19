"""gui/figure_mode_window.py — WB Plot Figure Generation main container.

This widget fills stack[1] of the MainWindow mode-switcher.  It is
completely independent of the densitometry pipeline:

  • No import of param_panel, results_panel, image_canvas, band_detector,
    or measure.
  • No shared Qt signals with the densitometry stack.
  • No mutation of any densitometry object.

Layout:
  Left — scrollable workflow sidebar (4 collapsible step groups).
  Right — FigureCanvas live preview in a QScrollArea.

The sidebar is non-linear: every section remains available and can be
expanded/collapsed freely.  Step 1 contains frame, condition, and saved
template controls; Step 2 fills the selected blot frame from the active WB
ROI.  The remaining sections retain blot-file and export actions, while the
top toolbar controls annotations and text alignment.
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from statistics import median
from typing import Callable

from PySide6.QtCore import Qt, QPointF, QRectF, QSize, QSizeF, QSignalBlocker, QTimer, Signal
from PySide6.QtGui import (
    QAction, QColor, QFont, QIcon, QKeySequence, QPainter, QPen, QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame, QGroupBox,
    QCheckBox, QGridLayout, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QInputDialog, QListWidget, QListWidgetItem,
    QComboBox, QMenu, QRadioButton, QScrollArea, QSizePolicy, QSpinBox, QDoubleSpinBox, QSplitter,
    QToolButton, QVBoxLayout, QWidget, QFontComboBox,
)

from core.export_engine import (
    PPTX_AVAILABLE,
    PDFExporter,
    PPTXExporter,
    TIFFExporter,
)
from core.band_auto_fit import calculate_band_auto_fit
from core.image_transform import geometry_transform_from_dict
from core.condition_template import even_lane_group_ranges, make_condition_table
from core.figure_project import (
    BlotSlot, ConditionTable, FigureProject,
    GlobalLayout, ImageBBox, LaneROI, Panel, SourceRef,
)
from core.layout_engine import (
    LayoutEngine,
    LayoutItem,
    LayoutResult,
    pt_to_scene,
    scene_to_pt,
)
from core.template_engine import TemplateEngine
from gui.condition_template_dialog import (
    ConditionPreviewWidget as _ConditionPreviewWidget,
    ConditionTemplateDialogController,
    request_custom_lane_ranges,
    request_custom_panel_lane_ranges,
)
from gui.figure_canvas import FigureCanvas
from utils.i18n import LANG_EN, LANG_ZH_CN, tr, tr_display

# Toolbar button height cap — keeps the annotation bar compact
_TOOLBAR_BTN_H = 26

_FIXED_ROI_NAME_ROLE = int(Qt.ItemDataRole.UserRole) + 1
USER_BLOT_FILES_DIR: Path = Path.home() / ".wb_analyzer" / "blot_files"


class _TemplateNameLabel(QLabel):
    """Label that emits the template id when its text is double-clicked."""

    doubleClicked = Signal(str)

    def __init__(self, text: str, template_id: str, parent=None) -> None:
        super().__init__(text, parent)
        self._template_id = template_id

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.doubleClicked.emit(self._template_id)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class _FramePreviewWidget(QWidget):
    """Compact live preview used only by the Create Template dialog."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._panel_count = 1
        self._blot_count = 2
        self._lane_count = 4
        self.setMinimumHeight(180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet(
            "background:#FFFFFF; border:1px solid #B8C7DA; border-radius:6px;"
        )

    def set_structure(self, panels: int, blots: int, lanes: int) -> None:
        structure = (max(1, panels), max(1, blots), max(1, lanes))
        if structure == (self._panel_count, self._blot_count, self._lane_count):
            return
        self._panel_count, self._blot_count, self._lane_count = structure
        self.update()

    def structure(self) -> tuple[int, int, int]:
        return self._panel_count, self._blot_count, self._lane_count

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        area = QRectF(self.rect()).adjusted(10.0, 10.0, -10.0, -10.0)
        gap = 8.0
        label_gap = max(3.0, area.width() * 0.01)
        label_w = max(54.0, area.width() * 0.18)
        panels_width = max(
            20.0,
            area.width()
            - label_gap
            - label_w
            - gap * (self._panel_count - 1),
        )
        panel_w = max(8.0, panels_width / self._panel_count)
        panel_h = max(28.0, area.height())

        title_font = painter.font()
        title_font.setPointSizeF(7.0)
        title_font.setBold(True)

        for panel_index in range(self._panel_count):
            panel_rect = QRectF(
                area.left() + panel_index * (panel_w + gap),
                area.top(),
                panel_w,
                panel_h,
            )
            if self._panel_count > 1:
                painter.setFont(title_font)
                painter.setPen(QColor("#456455"))
                painter.drawText(
                    panel_rect.adjusted(2.0, 0.0, -2.0, 0.0),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                    f"Panel {panel_index + 1}",
                )
                content = panel_rect.adjusted(2.0, 13.0, -2.0, -2.0)
            else:
                content = panel_rect.adjusted(2.0, 2.0, -2.0, -2.0)

            blot_gap = 3.0
            blot_h = max(
                3.0,
                (content.height() - blot_gap * (self._blot_count - 1))
                / self._blot_count,
            )
            blot_w = max(6.0, content.width())

            for blot_index in range(self._blot_count):
                blot = QRectF(
                    content.left(),
                    content.top() + blot_index * (blot_h + blot_gap),
                    blot_w,
                    blot_h,
                )
                painter.setPen(QPen(QColor("#111111"), 1.1))
                painter.setBrush(QColor("#D7D7D7"))
                painter.drawRect(blot)

                # One simulated WB band per lane makes the selected lane count
                # immediately readable without adding artificial dividers.
                lane_w = blot.width() / self._lane_count
                band_h = max(1.2, min(blot.height() * 0.18, 5.0))
                for lane_index in range(self._lane_count):
                    center_x = blot.left() + lane_w * (lane_index + 0.5)
                    band_w = max(1.5, lane_w * 0.68)
                    center_y = blot.center().y() + (
                        ((lane_index + blot_index) % 3) - 1
                    ) * min(1.2, blot.height() * 0.04)
                    band = QRectF(
                        center_x - band_w / 2.0,
                        center_y - band_h / 2.0,
                        band_w,
                        band_h,
                    )
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(
                        QColor(30, 30, 30, (165, 210, 185, 225)[lane_index % 4])
                    )
                    painter.drawRoundedRect(band, band_h / 2.0, band_h / 2.0)

                if panel_index == self._panel_count - 1:
                    label_font = painter.font()
                    label_font.setPointSizeF(
                        max(5.0, min(9.0, blot.height() * 0.28))
                    )
                    label_font.setBold(True)
                    label_font.setItalic(True)
                    painter.setFont(label_font)
                    painter.setPen(QColor("#111111"))
                    label_rect = QRectF(
                        blot.right() + label_gap,
                        blot.top(),
                        label_w,
                        blot.height(),
                    )
                    painter.drawText(
                        label_rect,
                        Qt.AlignmentFlag.AlignLeft
                        | Qt.AlignmentFlag.AlignVCenter,
                        f"IB: Protein {blot_index + 1}",
                    )




# ── Collapsible group box ─────────────────────────────────────────────────────

class _CollapseGroup(QWidget):
    """A workflow section with a large clickable header and collapsible body."""

    def __init__(self, title: str, parent=None, *, step_number: int | None = None) -> None:
        super().__init__(parent)
        self._expanded = True
        self._step_number = step_number

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 4)
        outer.setSpacing(0)

        # Header row
        self._header = QFrame()
        self._header.setObjectName("workflowSectionHeader")
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header.setMinimumHeight(38)
        h_layout = QHBoxLayout(self._header)
        h_layout.setContentsMargins(8, 5, 6, 5)
        h_layout.setSpacing(5)

        self._step_badge: QLabel | None = None
        if step_number is not None:
            self._step_badge = QLabel(str(step_number))
            self._step_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._step_badge.setFixedSize(22, 22)
            self._step_badge.setStyleSheet(
                "QLabel { background:#2F8A64; color:#FFFFFF; border-radius:11px; "
                "font-weight:700; font-size:10px; }"
            )
            self._step_badge.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents,
                True,
            )
            h_layout.addWidget(self._step_badge)

        self._title_label = QLabel(title)
        self._title_label.setStyleSheet(
            "font-weight:600; color:#24352E; font-size:11px; background:transparent;"
        )
        self._title_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        h_layout.addWidget(self._title_label)
        h_layout.addStretch()

        self._toggle_btn = QToolButton()
        self._toggle_btn.setText("›")
        self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_btn.setFixedSize(24, 24)
        self._toggle_btn.setStyleSheet(
            "QToolButton { border:none; background:transparent; color:#31433B; "
            "font-size:19px; font-weight:500; }"
        )
        self._toggle_btn.clicked.connect(self._toggle)
        h_layout.addWidget(self._toggle_btn)
        outer.addWidget(self._header)

        # Body widget
        self._body = QWidget()
        self._body.setStyleSheet(
            "QWidget { background-color:#FFFFFF; }"
        )
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(8, 8, 8, 7)
        self._body_layout.setSpacing(6)
        outer.addWidget(self._body)

        # Make the complete header clickable, matching the supplied workflow
        # reference instead of requiring the user to hit the small chevron.
        self._header.mousePressEvent = self._on_header_pressed
        self.set_expanded(True)

    def body_layout(self) -> QVBoxLayout:
        return self._body_layout

    def title_text(self) -> str:
        return self._title_label.text()

    def _toggle(self) -> None:
        self.set_expanded(not self._expanded)

    def _on_header_pressed(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle()
            event.accept()

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        self._body.setVisible(self._expanded)
        self._toggle_btn.setText("⌄" if self._expanded else "›")
        self._refresh_header_style()

    def _refresh_header_style(self) -> None:
        border = "#C9DCD2" if self._expanded else "#D9E1DD"
        self._header.setStyleSheet(
            "QFrame#workflowSectionHeader {"
            f"background-color:#FFFFFF; border:1px solid {border}; "
            "border-radius:7px;"
            "}"
        )


class _InlineDisclosure(QWidget):
    """Compact in-place dropdown used by Auto Detect and Manual ROI modes."""

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self._expanded = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._header = QFrame()
        self._header.setObjectName("inlineDisclosureHeader")
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header.setFixedHeight(26)
        self._header.setStyleSheet(
            "QFrame#inlineDisclosureHeader { background:#FFFFFF; "
            "border:1px solid #D3DDD8; border-radius:6px; }"
        )
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(7, 1, 4, 1)
        header_layout.setSpacing(4)
        self._title_label = QLabel(title)
        self._title_label.setStyleSheet(
            "font-size:10px; font-weight:500; color:#2F3D36; background:transparent;"
        )
        self._title_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        header_layout.addWidget(self._title_label)
        header_layout.addStretch(1)
        self._button = QToolButton()
        self._button.setText("›")
        self._button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._button.setFixedSize(22, 22)
        self._button.setStyleSheet(
            "QToolButton { border:none; background:transparent; color:#31433B; "
            "font-size:17px; }"
        )
        self._button.clicked.connect(self._toggle)
        header_layout.addWidget(self._button)
        self._header.mousePressEvent = self._on_header_pressed
        outer.addWidget(self._header)

        self._body = QWidget()
        self._body.setObjectName("inlineDisclosureBody")
        self._body.setStyleSheet(
            "QWidget#inlineDisclosureBody { background:#F7FAF8; border-left:1px solid #D3DDD8; "
            "border-right:1px solid #D3DDD8; border-bottom:1px solid #D3DDD8; "
            "border-radius:0 0 6px 6px; }"
        )
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(5, 4, 5, 4)
        self._body_layout.setSpacing(3)
        outer.addWidget(self._body)
        self.set_expanded(False)

    def body_layout(self) -> QVBoxLayout:
        return self._body_layout

    def is_expanded(self) -> bool:
        return self._expanded

    def _toggle(self) -> None:
        self.set_expanded(not self._expanded)

    def _on_header_pressed(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle()
            event.accept()

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        self._body.setVisible(self._expanded)
        self._button.setText("⌄" if self._expanded else "›")


# ── Main window widget ────────────────────────────────────────────────────────

class _TemplatePreviewCanvas(FigureCanvas):
    """Read-only canvas with a fixed scale for saved-template previews."""

    def wheelEvent(self, event) -> None:
        # This is a browser preview, not a second editing canvas. Consuming
        # wheel and trackpad gestures keeps its prescribed scale fixed.
        event.accept()


class FigureModeWindow(QWidget):
    """Container for the WB Plot Figure Generation mode."""

    workflowEvent = Signal(str)

    # The workflow panel needs enough room for the two-column detection and
    # template controls without clipping.  It remains substantially narrower
    # than the source-image and figure-canvas workspaces around it.
    _SIDEBAR_WIDTH = 210

    def __init__(self, parent=None, *, language: str = LANG_EN) -> None:
        super().__init__(parent)
        TemplateEngine.load_user_templates()

        self._project: FigureProject | None = None
        self._layout_engine = LayoutEngine()
        self._layout_result: LayoutResult | None = None
        self._active_image_provider: Callable[[], dict[str, object]] | None = None
        self._auto_fit_detection_handler: Callable[[int | None, bool], dict[str, object]] | None = None
        self._auto_fit_overlay_handler: Callable[[QRectF | None], None] | None = None
        self._fixed_roi_requested: Callable[[], QSizeF | None] | None = None
        self._fixed_roi_cancel_requested: Callable[[], None] | None = None
        self._fixed_roi_size_selected: Callable[[QSizeF], None] | None = None
        self._focus_requested: Callable[[], None] | None = None
        self._context_controls_widget: QWidget | None = None
        self._template_preview_canvas: FigureCanvas | None = None
        self._template_browser_dialog: QDialog | None = None
        self._text_style_overrides: dict[tuple, dict] = {}
        self._fixed_roi_sizes: list[tuple[str, QSizeF]] = []
        self._active_slot_ref: SourceRef | None = None
        self._active_template_id: str | None = None
        self._active_blot_file_id: str | None = None
        self._active_table_style: str = "none"
        self._canvas_undo_stack: list[dict] = []
        self._restoring_canvas_undo = False
        self._canvas_undo_queued = False
        self._roi_fill_mode = "auto"
        self._auto_fit_review_pending = False
        self._tutorial_mode = False
        self._tutorial_saved_roi_fill_mode: str | None = None
        self._language = language if language in {LANG_EN, LANG_ZH_CN} else LANG_EN

        # Dynamic sidebar sub-widgets rebuilt when project structure changes
        self._step4_slot_widgets: list[QWidget] = []

        self._build_ui()
        # Build directly in the requested display language. This prevents a
        # lazily-created WB Figure workspace from falling back to English.
        self.set_language(self._language)
        self._undo_shortcut = QShortcut(QKeySequence.StandardKey.Undo, self)
        self._undo_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._undo_shortcut.activated.connect(self._queue_canvas_undo)

    def set_active_image_provider(self, provider: Callable[[], dict[str, object]]) -> None:
        self._active_image_provider = provider

    def set_auto_fit_detection_handler(
        self,
        handler: Callable[[int | None, bool], dict[str, object]],
    ) -> None:
        self._auto_fit_detection_handler = handler

    def set_auto_fit_overlay_handler(
        self,
        handler: Callable[[QRectF | None], None],
    ) -> None:
        self._auto_fit_overlay_handler = handler

    def set_fixed_roi_request_handler(self, handler: Callable[[], QSizeF | None]) -> None:
        self._fixed_roi_requested = handler

    def set_fixed_roi_cancel_handler(self, handler: Callable[[], None]) -> None:
        self._fixed_roi_cancel_requested = handler

    def set_fixed_roi_size_selected_handler(self, handler: Callable[[QSizeF], None]) -> None:
        self._fixed_roi_size_selected = handler

    def set_focus_request_handler(self, handler: Callable[[], None]) -> None:
        self._focus_requested = handler

    def set_context_controls_widget(self, widget: QWidget | None) -> None:
        if self._context_controls_widget is widget:
            return
        if self._context_controls_widget is not None:
            self._context_controls_widget.setParent(None)
        self._context_controls_widget = widget
        if widget is not None:
            self._sidebar_layout.insertWidget(1, widget)

    def set_tutorial_mode(self, enabled: bool) -> None:
        """Enable tutorial-only dialog defaults without changing normal defaults."""
        enabled = bool(enabled)
        if enabled and not self._tutorial_mode:
            self._tutorial_saved_roi_fill_mode = self._roi_fill_mode
            # The tutorial demonstrates an exact user-drawn crop. Manual mode
            # preserves that ROI instead of replacing it with Auto-Fit's much
            # tighter band-only crop, which can make bands look oversized.
            self._manual_detect_radio.setChecked(True)
            self._roi_fill_mode = "manual"
        elif not enabled and self._tutorial_mode:
            restore = self._tutorial_saved_roi_fill_mode or "auto"
            if restore == "manual":
                self._manual_detect_radio.setChecked(True)
            else:
                self._auto_detect_radio.setChecked(True)
            self._roi_fill_mode = restore
            self._tutorial_saved_roi_fill_mode = None
        self._tutorial_mode = enabled

    def set_language(self, language: str) -> None:
        """Refresh the WB image-layout workspace without touching project data."""
        self._language = language
        self._retranslate_widget_tree(self)

        # Template names that encode layout counts are rendered naturally in
        # Chinese; the stored name is never modified.
        for index in range(self._template_list.count()):
            item = self._template_list.item(index)
            template_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
            try:
                template = TemplateEngine.get_template(template_id)
            except KeyError:
                continue
            row = self._template_list.itemWidget(item)
            label = row.findChild(_TemplateNameLabel) if row is not None else None
            if label is not None:
                label.setText(self._localized_template_name(template.display_name, template.is_user_template))

        # The selected target is regenerated as the user interacts with the
        # canvas, so refresh it through its source-aware helper as well.
        self._refresh_selected_slot_label()

    def _retranslate_widget_tree(self, root: QWidget) -> None:
        """Translate an existing WB Figure widget tree in either direction."""
        widgets = [root, *root.findChildren(QWidget)]
        for widget in widgets:
            if widget.windowTitle():
                widget.setWindowTitle(tr_display(widget.windowTitle(), self._language))

        for label in root.findChildren(QLabel):
            label.setText(tr_display(label.text(), self._language))
            label.setToolTip(tr_display(label.toolTip(), self._language))
        for button_type in (QPushButton, QToolButton, QRadioButton, QCheckBox):
            for button in root.findChildren(button_type):
                button.setText(tr_display(button.text(), self._language))
                button.setToolTip(tr_display(button.toolTip(), self._language))
        for group_box in root.findChildren(QGroupBox):
            group_box.setTitle(tr_display(group_box.title(), self._language))
        for combo in root.findChildren(QComboBox):
            for index in range(combo.count()):
                combo.setItemText(
                    index,
                    tr_display(combo.itemText(index), self._language),
                )
            combo.setToolTip(tr_display(combo.toolTip(), self._language))
        for spin_type in (QSpinBox, QDoubleSpinBox):
            for spin in root.findChildren(spin_type):
                spin.setSuffix(tr_display(spin.suffix(), self._language))
                spin.setToolTip(tr_display(spin.toolTip(), self._language))
        for action in root.findChildren(QAction):
            action.setText(tr_display(action.text(), self._language))
            action.setToolTip(tr_display(action.toolTip(), self._language))

    def _localized_template_name(self, name: str, _is_user_template: bool) -> str:
        if self._language != "zh_CN":
            return name
        translated = re.sub(r"\b(\d+) panels?\b", r"\1 个版面", name, flags=re.IGNORECASE)
        translated = re.sub(r"\b(\d+) blots?\b", r"\1 张印迹图", translated, flags=re.IGNORECASE)
        return re.sub(r"\b(\d+) lanes?\b", r"\1 条泳道", translated, flags=re.IGNORECASE)

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Left: scrollable sidebar ──────────────────────────────────────
        sidebar_scroll = QScrollArea()
        sidebar_scroll.setFixedWidth(self._SIDEBAR_WIDTH)
        sidebar_scroll.setWidgetResizable(True)
        sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sidebar_scroll.setStyleSheet(
            "QScrollArea { border: none; background: #EAF2EE; }"
        )

        sidebar_widget = QWidget()
        sidebar_widget.setStyleSheet("background: #EAF2EE;")
        sidebar_layout = QVBoxLayout(sidebar_widget)
        sidebar_layout.setContentsMargins(2, 8, 2, 8)
        sidebar_layout.setSpacing(4)
        self._sidebar_layout = sidebar_layout

        # Title
        title_lbl = QLabel("Workflow")
        title_lbl.setStyleSheet(
            "font-weight:700; font-size:14px; color:#24352E; padding:3px 6px 7px 6px;"
        )
        sidebar_layout.addWidget(title_lbl)

        # Step groups
        self._grp1 = self._build_step1()
        self._grp4 = self._build_apply_roi_step()
        self._grp5 = self._build_saved_blot_files_step()
        self._grp6 = self._build_step6()
        self._grp4.set_expanded(False)

        for grp in (self._grp1, self._grp4, self._grp5, self._grp6):
            sidebar_layout.addWidget(grp)
        sidebar_layout.addStretch()

        sidebar_scroll.setWidget(sidebar_widget)
        root.addWidget(sidebar_scroll)

        # ── Separator ─────────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        sep.setStyleSheet("color: #BDD0C6;")
        root.addWidget(sep)

        # ── Right: annotation toolbar + canvas ────────────────────────────
        self._canvas = FigureCanvas(self)
        self._canvas.on_text_edited = self._on_canvas_text_edited
        self._canvas.on_text_rotation_changed = self._on_canvas_text_rotation_changed
        self._canvas.on_blot_resized = self._on_canvas_blot_resized
        self._canvas.on_blot_selected = self._on_canvas_blot_selected
        self._canvas.on_blot_selection_cleared = self._on_canvas_blot_selection_cleared
        self._canvas.on_view_interacted = self._on_canvas_view_interacted
        self._canvas.on_state_about_to_change = self._remember_canvas_undo_state
        self._canvas.on_undo_requested = self._queue_canvas_undo

        canvas_scroll = QScrollArea()
        canvas_scroll.setWidgetResizable(True)
        canvas_scroll.setStyleSheet(
            "QScrollArea { border: none; background: #FFFFFF; }"
        )
        canvas_scroll.setWidget(self._canvas)

        right_widget = QWidget()
        right_widget.setStyleSheet("background: #FFFFFF;")
        right_vbox = QVBoxLayout(right_widget)
        right_vbox.setContentsMargins(0, 0, 0, 0)
        right_vbox.setSpacing(0)
        right_vbox.addWidget(self._build_canvas_toolbar())
        right_vbox.addWidget(canvas_scroll, 1)
        root.addWidget(right_widget, 1)

        # Wire scene selection → font/line controls
        self._canvas._scene.selectionChanged.connect(self._on_canvas_selection_changed)

    # ── Saved template library ────────────────────────────────────────────

    def _build_step1(self) -> _CollapseGroup:
        grp = _CollapseGroup("Step 1: Choose Layout", step_number=1)
        bl = grp.body_layout()
        bl.setContentsMargins(6, 6, 6, 7)
        bl.setSpacing(0)

        # Keep the existing list as the source of template selection state,
        # but host it only inside the Saved Templates browser dialog.
        self._template_list = QListWidget()
        self._template_list.setMinimumWidth(180)
        self._template_list.setStyleSheet(
            "QListWidget { background:#FFFFFF; border:1px solid #D1DDD7; "
            "border-radius:7px; font-size:11px; outline:none; }"
            "QListWidget::item { padding:4px 5px; border-bottom:1px solid #EEF3F0; }"
            "QListWidget::item:selected { background:#E5F1EB; color:#183B2B; }"
        )
        self._template_list.itemClicked.connect(self._on_template_list_clicked)
        self._template_list.currentItemChanged.connect(
            lambda current, _previous: self._refresh_template_browser_preview(current)
        )
        self._populate_template_list()
        self._template_list.setParent(self)
        self._template_list.hide()

        def task_badge(number: str) -> QLabel:
            badge = QLabel(number)
            badge.setObjectName("step1TaskBadge")
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setFixedSize(24, 24)
            badge.setStyleSheet(
                "QLabel#step1TaskBadge { background:#FFFFFF; color:#176B50; "
                "border:1px solid #238160; border-radius:12px; "
                "font-size:10px; font-weight:700; }"
            )
            return badge

        def task_card(title: str) -> tuple[QFrame, QVBoxLayout]:
            card = QFrame()
            card.setObjectName("step1TaskCard")
            card.setSizePolicy(
                QSizePolicy.Policy.Ignored,
                QSizePolicy.Policy.Preferred,
            )
            card.setStyleSheet(
                "QFrame#step1TaskCard { background:#FAFCFB; "
                "border:1px solid #D4E0DA; border-radius:8px; }"
            )
            layout = QVBoxLayout(card)
            layout.setContentsMargins(7, 7, 7, 7)
            layout.setSpacing(4)

            heading = QLabel(title)
            heading.setObjectName("step1TaskTitle")
            heading.setStyleSheet(
                "QLabel#step1TaskTitle { color:#23483A; font-size:11px; "
                "font-weight:700; background:transparent; border:none; }"
            )
            layout.addWidget(heading)
            return card, layout

        flow = QWidget()
        flow.setObjectName("step1Flow")
        flow.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        flow.setStyleSheet("QWidget#step1Flow { background:transparent; }")
        flow_layout = QGridLayout(flow)
        flow_layout.setContentsMargins(0, 0, 0, 0)
        flow_layout.setHorizontalSpacing(4)
        flow_layout.setVerticalSpacing(0)
        flow_layout.setColumnStretch(1, 1)

        frame_rail = QWidget()
        frame_rail.setObjectName("step1FrameRail")
        frame_rail.setFixedWidth(24)
        frame_rail.setStyleSheet("QWidget#step1FrameRail { background:transparent; }")
        frame_rail_layout = QVBoxLayout(frame_rail)
        frame_rail_layout.setContentsMargins(0, 0, 0, 0)
        frame_rail_layout.setSpacing(0)
        frame_rail_layout.addWidget(task_badge("1"), 0, Qt.AlignmentFlag.AlignTop)
        frame_line = QFrame()
        frame_line.setObjectName("step1Connector")
        frame_line.setFixedWidth(2)
        frame_line.setStyleSheet(
            "QFrame#step1Connector { background:#8DBEAA; border:none; }"
        )
        frame_rail_layout.addWidget(frame_line, 1, Qt.AlignmentFlag.AlignHCenter)
        flow_layout.addWidget(frame_rail, 0, 0)

        frame_card, frame_layout = task_card("Blot Frame")

        create_btn = QPushButton("Create Blot Frame Template")
        create_btn.setObjectName("step1PrimaryButton")
        create_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        create_btn.setStyleSheet(
            "QPushButton#step1PrimaryButton { background:#176D4E; color:#FFFFFF; "
            "border:1px solid #176D4E; border-radius:6px; font-size:9px; "
            "font-weight:600; padding:3px 5px; }"
            "QPushButton#step1PrimaryButton:hover { background:#125D42; "
            "border-color:#125D42; }"
            "QPushButton#step1PrimaryButton:pressed { background:#0E4F38; }"
        )
        create_btn.setFixedHeight(28)
        create_btn.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Fixed,
        )
        create_btn.clicked.connect(self._on_create_template)
        frame_layout.addWidget(create_btn)

        add_blot_btn = QPushButton("Add Extra Blot Frame")
        add_blot_btn.setObjectName("step1TextButton")
        add_blot_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_blot_btn.setStyleSheet(
            "QPushButton#step1TextButton { background:transparent; color:#176D4E; "
            "border:none; border-radius:5px; font-size:9px; font-weight:600; "
            "padding:2px 3px; }"
            "QPushButton#step1TextButton:hover { background:#EAF4EF; }"
            "QPushButton#step1TextButton:pressed { background:#DDECE5; }"
        )
        add_blot_btn.setFixedHeight(24)
        add_blot_btn.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Fixed,
        )
        add_blot_btn.setToolTip(
            "Add a free-position Blot Frame. Select it, draw a WB ROI, then press Enter/Return to fill it."
        )
        add_blot_btn.clicked.connect(self._on_add_blot_frame)
        frame_layout.addWidget(add_blot_btn)
        flow_layout.addWidget(frame_card, 0, 1)

        connector_row = QWidget()
        connector_row.setObjectName("step1ConnectorRow")
        connector_row.setFixedSize(24, 10)
        connector_row.setStyleSheet(
            "QWidget#step1ConnectorRow { background:transparent; }"
        )
        connector_layout = QVBoxLayout(connector_row)
        connector_layout.setContentsMargins(0, 0, 0, 0)
        connector_layout.setSpacing(0)
        connector = QFrame()
        connector.setObjectName("step1Connector")
        connector.setFixedWidth(2)
        connector.setStyleSheet(
            "QFrame#step1Connector { background:#8DBEAA; border:none; }"
        )
        connector_layout.addWidget(connector, 1, Qt.AlignmentFlag.AlignHCenter)
        flow_layout.addWidget(connector_row, 1, 0)

        condition_badge = task_badge("2")
        flow_layout.addWidget(condition_badge, 2, 0, 1, 1, Qt.AlignmentFlag.AlignTop)

        condition_card, condition_layout = task_card("Blot Conditions")
        create_condition_btn = QPushButton("Create Blot Condition Template")
        create_condition_btn.setObjectName("step1SecondaryButton")
        create_condition_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        create_condition_btn.setStyleSheet(
            "QPushButton#step1SecondaryButton { background:#FFFFFF; color:#176D4E; "
            "border:1px solid #3C9A75; border-radius:6px; font-size:8px; "
            "font-weight:600; padding:2px; }"
            "QPushButton#step1SecondaryButton:hover { background:#F0F7F3; "
            "border-color:#247B5C; }"
            "QPushButton#step1SecondaryButton:pressed { background:#E3F0E9; }"
        )
        create_condition_btn.setFixedHeight(28)
        create_condition_btn.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Fixed,
        )
        create_condition_btn.clicked.connect(self._on_create_condition_template)
        condition_layout.addWidget(create_condition_btn)
        flow_layout.addWidget(condition_card, 2, 1)
        bl.addWidget(flow)
        bl.addSpacing(6)

        self._saved_templates_btn = QPushButton("Saved Templates   ›")
        self._saved_templates_btn.setObjectName("step1SavedTemplatesButton")
        self._saved_templates_btn.setFixedHeight(25)
        self._saved_templates_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._saved_templates_btn.setStyleSheet(
            "QPushButton#step1SavedTemplatesButton { background:transparent; "
            "border:none; color:#29483B; font-size:10px; font-weight:600; "
            "padding:1px 6px; text-align:left; }"
            "QPushButton#step1SavedTemplatesButton:hover { background:#F0F6F3; }"
        )
        self._saved_templates_btn.clicked.connect(self._show_saved_templates_dialog)

        saved_templates_card = QFrame()
        saved_templates_card.setObjectName("step1SavedTemplatesCard")
        saved_templates_card.setStyleSheet(
            "QFrame#step1SavedTemplatesCard { background:#FFFFFF; "
            "border:1px solid #D4E0DA; border-radius:7px; }"
        )
        saved_templates_layout = QVBoxLayout(saved_templates_card)
        saved_templates_layout.setContentsMargins(2, 3, 2, 4)
        saved_templates_layout.setSpacing(0)
        saved_templates_layout.addWidget(self._saved_templates_btn)
        saved_templates_helper = QLabel("Reuse a previous layout")
        saved_templates_helper.setObjectName("step1SavedTemplatesHelper")
        saved_templates_helper.setStyleSheet(
            "QLabel#step1SavedTemplatesHelper { color:#6A7B73; font-size:8px; "
            "padding:0 8px 2px 8px; background:transparent; border:none; }"
        )
        saved_templates_layout.addWidget(saved_templates_helper)
        bl.addWidget(saved_templates_card)

        # Hidden state controls keep structure values in sync with loaded or
        # applied projects.  The dialog uses temporary controls so closing it
        # never leaves this window holding deleted Qt children.
        self._panels_spin = self._make_structure_spin(1, 15, 1)
        self._blots_spin = self._make_structure_spin(1, 15, 2)
        self._lanes_spin = self._make_structure_spin(2, 12, 4)
        for spin in (self._panels_spin, self._blots_spin, self._lanes_spin):
            spin.setParent(self)
            spin.hide()

        return grp

    def _populate_template_list(self) -> None:
        self._template_list.clear()
        for tmpl in TemplateEngine.all_templates():
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, tmpl.id)
            item.setSizeHint(QSize(0, 23))
            self._template_list.addItem(item)
            self._install_template_list_item(item, tmpl)
        if self._template_list.count():
            self._template_list.setCurrentRow(0)

    def _install_template_list_item(self, item: QListWidgetItem, tmpl) -> None:
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(6, 0, 4, 0)
        row_layout.setSpacing(4)
        lbl = _TemplateNameLabel(
            self._localized_template_name(tmpl.display_name, tmpl.is_user_template), tmpl.id
        )
        lbl.setStyleSheet("font-size:10px; color:#1E2D3F;")
        if tmpl.is_user_template:
            lbl.setCursor(Qt.CursorShape.IBeamCursor)
            lbl.setToolTip("Double-click to rename this Figure Template")
            lbl.doubleClicked.connect(self._on_rename_template)
        row_layout.addWidget(lbl, 1)
        del_btn = QToolButton()
        del_btn.setText("×")
        del_btn.setFixedSize(16, 16)
        del_btn.setStyleSheet(
            "QToolButton { border:1px solid #9EB3A8; border-radius:8px; "
            "background:#F7FAF8; color:#6A7A72; font-size:10px; }"
            "QToolButton:hover { background:#F0D7D7; color:#8A3B3B; border-color:#C99595; }"
        )
        del_btn.setToolTip(f'Delete template "{tmpl.display_name}"')
        del_btn.clicked.connect(
            lambda _=False, tid=tmpl.id: self._on_delete_template(tid)
        )
        row_layout.addWidget(del_btn)
        self._template_list.setItemWidget(item, row_widget)

    def _on_template_list_clicked(self, item: QListWidgetItem) -> None:
        self._template_list.setCurrentItem(item)

    def _show_saved_templates_dialog(self) -> None:
        """Browse saved templates with a true FigureCanvas preview."""
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("Saved Templates", self._language))
        dialog.setModal(True)
        dialog.setFixedSize(680, 420)
        dialog.setStyleSheet(
            "QDialog { background:#F5F8F6; }"
            "QLabel#templateBrowserTitle { color:#26382F; font-size:12px; font-weight:700; }"
        )
        self._template_browser_dialog = dialog

        root = QVBoxLayout(dialog)
        root.setContentsMargins(10, 10, 10, 9)
        root.setSpacing(8)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(7)

        list_panel = QWidget()
        list_layout = QVBoxLayout(list_panel)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(5)
        list_title = QLabel(tr("Saved Templates", self._language))
        list_title.setObjectName("templateBrowserTitle")
        list_layout.addWidget(list_title)
        self._template_list.setParent(list_panel)
        self._template_list.show()
        list_layout.addWidget(self._template_list, 1)
        splitter.addWidget(list_panel)

        preview_panel = QWidget()
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(5)
        preview_title = QLabel(tr("Template Preview", self._language))
        preview_title.setObjectName("templateBrowserTitle")
        preview_layout.addWidget(preview_title)
        preview_canvas = _TemplatePreviewCanvas(preview_panel)
        preview_canvas.setInteractive(False)
        preview_canvas.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        preview_canvas.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        preview_canvas.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        preview_canvas.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_canvas.setStyleSheet(
            "QGraphicsView { background:#FFFFFF; border:1px solid #D1DDD7; border-radius:7px; }"
        )
        preview_layout.addWidget(preview_canvas, 1)
        splitter.addWidget(preview_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([200, 460])
        root.addWidget(splitter, 1)

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        cancel_btn = QPushButton(tr("Cancel", self._language))
        cancel_btn.setStyleSheet(_SMALL_BTN_STYLE)
        cancel_btn.setFixedHeight(25)
        cancel_btn.clicked.connect(dialog.reject)
        action_row.addWidget(cancel_btn)
        apply_btn = QPushButton(tr("Apply", self._language))
        apply_btn.setFixedSize(86, 25)
        apply_btn.setStyleSheet(_APPLY_BTN_STYLE)
        apply_btn.clicked.connect(lambda: self._apply_template_from_browser(dialog))
        action_row.addWidget(apply_btn)
        root.addLayout(action_row)

        self._template_preview_canvas = preview_canvas
        self._refresh_template_browser_preview(self._template_list.currentItem())
        self._retranslate_widget_tree(dialog)
        QTimer.singleShot(0, lambda: self._fit_template_preview(preview_canvas))
        try:
            dialog.exec()
        finally:
            self._template_preview_canvas = None
            self._template_browser_dialog = None
            self._template_list.setParent(self)
            self._template_list.hide()

    def _apply_template_from_browser(self, dialog: QDialog) -> None:
        if self._template_list.currentItem() is None:
            return
        if self._on_apply_template():
            dialog.accept()

    def _refresh_template_browser_preview(
        self,
        item: QListWidgetItem | None,
    ) -> None:
        canvas = self._template_preview_canvas
        if canvas is None or item is None:
            return
        template_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if not template_id:
            return
        try:
            project, canvas_state, overlay_data, styles = (
                self._template_preview_payload(template_id)
            )
            canvas.clear_all()
            if canvas_state:
                canvas.restore_state_snapshot(canvas_state, repopulate_scene=False)
            elif overlay_data:
                canvas._restore_overlay_from_data(overlay_data)

            layout = LayoutEngine().compute(project)
            self._apply_text_styles_to_layout(layout, styles)
            hidden = canvas.hidden_text_keys()
            if hidden:
                layout.items = [
                    layout_item
                    for layout_item in layout.items
                    if (
                        layout_item.source_ref is None
                        or layout_item.source_ref.key() not in hidden
                    )
                ]
            canvas.render(layout, project)
            self._fit_template_preview(canvas)
            QTimer.singleShot(0, lambda: self._fit_template_preview(canvas))
        except Exception as exc:
            canvas.clear_all()
            canvas.setToolTip(f"Unable to preview this Figure Template: {exc}")

    @staticmethod
    def _fit_template_preview(canvas: FigureCanvas) -> None:
        if canvas._background_item is None:
            return
        # Fit the actual template rather than the surrounding white page.
        # The fixed fill ratio makes the preview large and stable while still
        # leaving breathing room around every condition and blot label.
        canvas.fit_frame_content_to_view(fill_ratio=0.76)

    def _template_preview_payload(
        self,
        template_id: str,
    ) -> tuple[FigureProject, dict, list[dict], dict[tuple, dict]]:
        if not TemplateEngine.is_builtin(template_id):
            project, overlay_data = TemplateEngine.restore_user_project(template_id)
            format_state = TemplateEngine.restore_user_template_format_state(template_id)
            return (
                project,
                dict(format_state.get("canvas_state") or {}),
                list(overlay_data or []),
                dict(format_state.get("text_style_overrides") or {}),
            )

        template = TemplateEngine.get_template(template_id)
        table_style = self._default_table_style_for_template(template_id)
        project = TemplateEngine.build_project(template_id)
        for panel in project.panels:
            panel.condition_table = TemplateEngine.make_condition_table(
                table_style,
                template.default_lane_count,
            )
        project.global_layout.show_condition_table = table_style != "none"
        return project, {}, [], {}

    def _on_delete_template(self, template_id: str) -> None:
        tmpl = TemplateEngine.get_template(template_id)
        action = (
            "Hide built-in Figure Template"
            if TemplateEngine.is_builtin(template_id)
            else "Delete Figure Template"
        )
        reply = QMessageBox.question(
            self, "Delete Figure Template",
            f'{action} "{tmpl.display_name}"?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if TemplateEngine.is_builtin(template_id):
            TemplateEngine.hide_builtin_template(template_id)
        else:
            TemplateEngine.delete_user_template(template_id)
        self._populate_template_list()

    def _on_rename_template(self, template_id: str) -> None:
        if TemplateEngine.is_builtin(template_id):
            return
        try:
            tmpl = TemplateEngine.get_template(template_id)
        except KeyError:
            return
        name, ok = QInputDialog.getText(
            self,
            "Rename Figure Template",
            "Figure Template name:",
            text=tmpl.display_name,
        )
        if not ok:
            return
        name = name.strip()
        if not name or name == tmpl.display_name:
            return
        try:
            TemplateEngine.rename_user_template(template_id, name)
        except Exception as exc:
            QMessageBox.critical(self, "Rename Figure Template", f"Failed:\n{exc}")
            return
        self._populate_template_list()
        self._select_template_id(template_id)

    def _select_template_id(self, template_id: str) -> None:
        for row in range(self._template_list.count()):
            item = self._template_list.item(row)
            if str(item.data(Qt.ItemDataRole.UserRole)) == template_id:
                self._template_list.setCurrentItem(item)
                return

    def _current_template_selection(self) -> tuple[str, str]:
        current = self._template_list.currentItem()
        if current is None:
            return "normal_wb", "none"
        tid = str(current.data(Qt.ItemDataRole.UserRole) or "normal_wb")
        if not TemplateEngine.is_builtin(tid):
            return tid, "none"
        return tid, self._default_table_style_for_template(tid)

    def _default_table_style_for_template(self, template_id: str) -> str:
        if not TemplateEngine.is_builtin(template_id):
            return "none"
        tmpl = TemplateEngine.get_template(template_id)
        if not tmpl.has_condition_table:
            return "none"
        if template_id == "dose_response":
            return "group_dose"
        if template_id == "ip_coip":
            return "ip_input"
        return "vector_matrix"

    # ── Create template structure ─────────────────────────────────────────

    @staticmethod
    def _make_structure_spin(minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        return spin

    def _on_create_template(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Create Blot Frame Template")
        dialog.setModal(True)
        dialog.setMinimumSize(420, 420)

        outer = QVBoxLayout(dialog)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(14)

        heading = QLabel("Create a new layout")
        heading.setStyleSheet("font-size:14px; font-weight:600; color:#1E3D2F;")
        outer.addWidget(heading)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(10)
        panels_default = 1 if self._tutorial_mode else self._panels_spin.value()
        blots_default = 2 if self._tutorial_mode else self._blots_spin.value()
        lanes_default = 3 if self._tutorial_mode else self._lanes_spin.value()
        panels_spin = self._make_structure_spin(1, 15, panels_default)
        blots_spin = self._make_structure_spin(1, 15, blots_default)
        lanes_spin = self._make_structure_spin(2, 12, lanes_default)
        panels_spin.setObjectName("frameTemplatePanelsSpin")
        blots_spin.setObjectName("frameTemplateBlotsSpin")
        lanes_spin.setObjectName("frameTemplateLanesSpin")
        form.addRow("Panels:", panels_spin)
        form.addRow("Blot Frames:", blots_spin)
        form.addRow("Lanes:", lanes_spin)
        outer.addLayout(form)

        preview_label = QLabel("Frame Preview")
        preview_label.setStyleSheet(
            "font-size:10px; font-weight:600; color:#5C7167;"
        )
        outer.addWidget(preview_label)

        preview = _FramePreviewWidget(dialog)
        preview.set_structure(
            panels_spin.value(), blots_spin.value(), lanes_spin.value()
        )
        outer.addWidget(preview, 1)

        def refresh_preview() -> None:
            preview.set_structure(
                panels_spin.value(), blots_spin.value(), lanes_spin.value()
            )

        panels_spin.valueChanged.connect(refresh_preview)
        blots_spin.valueChanged.connect(refresh_preview)
        lanes_spin.valueChanged.connect(refresh_preview)

        buttons = QDialogButtonBox()
        cancel_btn = buttons.addButton(
            QDialogButtonBox.StandardButton.Cancel
        )
        apply_btn = buttons.addButton(
            "Apply Frame", QDialogButtonBox.ButtonRole.AcceptRole
        )
        apply_btn.setObjectName("frameTemplateApplyButton")
        apply_btn.setStyleSheet(_APPLY_BTN_STYLE)
        cancel_btn.clicked.connect(dialog.reject)
        apply_btn.clicked.connect(dialog.accept)
        outer.addWidget(buttons)

        self._retranslate_widget_tree(dialog)
        if self._tutorial_mode:
            self.workflowEvent.emit("frame_template_dialog_opened")
        dialog_result = dialog.exec()
        if dialog_result == QDialog.DialogCode.Accepted:
            self._panels_spin.setValue(panels_spin.value())
            self._blots_spin.setValue(blots_spin.value())
            self._lanes_spin.setValue(lanes_spin.value())
            self._on_apply_structure()
            self.workflowEvent.emit("frame_template_applied")
        elif self._tutorial_mode:
            self.workflowEvent.emit("frame_template_dialog_cancelled")

    def _current_condition_target(self) -> tuple[int, int] | None:
        if self._project is None:
            return None
        refs = self._canvas.selected_blot_refs()
        if refs:
            ref = refs[0]
            if ref.panel_idx is not None and ref.slot_idx is not None:
                slot = self._get_slot(ref.panel_idx, ref.slot_idx)
                if slot is not None:
                    return ref.panel_idx, max(1, slot.lane_count)
        if self._active_slot_ref is not None:
            ref = self._active_slot_ref
            if ref.panel_idx is not None and ref.slot_idx is not None:
                slot = self._get_slot(ref.panel_idx, ref.slot_idx)
                if slot is not None:
                    return ref.panel_idx, max(1, slot.lane_count)
        for panel_index, panel in enumerate(self._project.panels):
            if panel.blot_slots:
                return panel_index, max(1, panel.blot_slots[0].lane_count)
        return None

    def _current_condition_targets(self) -> list[tuple[int, int]]:
        if self._project is None:
            return []
        return [
            (panel_index, max(1, panel.blot_slots[0].lane_count))
            for panel_index, panel in enumerate(self._project.panels)
            if panel.blot_slots
        ]

    def _on_create_condition_template(self) -> None:
        """Open the condition-template workflow and apply its model result."""
        targets = self._current_condition_targets()
        if not targets:
            QMessageBox.information(
                self,
                "Create Blot Condition Template",
                "No Western panel with detected lanes is available.",
            )
            return

        controller = ConditionTemplateDialogController(
            self,
            targets,
            self._project,
            self._language,
            self._tutorial_mode,
            preview_factory=_ConditionPreviewWidget,
            make_spin=self._make_structure_spin,
            request_custom_ranges=self._request_custom_lane_ranges,
            retranslate=self._retranslate_widget_tree,
        )
        if self._tutorial_mode:
            self.workflowEvent.emit("condition_template_dialog_opened")
        result = controller.exec()
        if result is None:
            if self._tutorial_mode:
                self.workflowEvent.emit("condition_template_dialog_cancelled")
            return

        self._apply_condition_templates_to_panels(result)
        self.workflowEvent.emit("condition_template_applied")


    @staticmethod
    def _even_lane_group_ranges(
        lane_count: int, group_count: int
    ) -> list[tuple[int, int]]:
        """Compatibility wrapper for the extracted grouping service."""
        return even_lane_group_ranges(lane_count, group_count)

    def _request_custom_lane_ranges(
        self,
        lane_count: int,
        group_count: int,
        defaults: list[tuple[int, int]],
    ) -> list[tuple[int, int]] | None:
        return request_custom_lane_ranges(
            self,
            self._language,
            self._make_structure_spin,
            self._retranslate_widget_tree,
            lane_count,
            group_count,
            defaults,
        )

    def _request_custom_panel_lane_ranges(
        self,
        lane_counts: list[int],
        group_count: int,
        defaults: list[tuple[int, int]],
        *,
        panel_number_offset: int = 0,
    ) -> list[tuple[int, int]] | None:
        return request_custom_panel_lane_ranges(
            self,
            self._language,
            self._make_structure_spin,
            self._retranslate_widget_tree,
            lane_counts,
            group_count,
            defaults,
            panel_number_offset=panel_number_offset,
        )

    @staticmethod
    def _make_custom_condition_table(
        lane_count: int,
        condition_rows: int,
        group_ranges: object,
    ) -> ConditionTable:
        """Compatibility wrapper for the extracted model converter."""
        return make_condition_table(lane_count, condition_rows, group_ranges)

    def _apply_condition_templates_to_panels(
        self,
        panel_settings: list[
            tuple[int, int, int, object]
        ],
    ) -> None:
        if self._project is None or not panel_settings:
            return
        valid_settings = [
            setting
            for setting in panel_settings
            if 0 <= setting[0] < len(self._project.panels)
        ]
        if not valid_settings:
            return

        blot_view_state = self._canvas.capture_blot_view_state()
        self._remember_canvas_undo_state()
        for panel_index, lane_count, condition_rows, group_ranges in valid_settings:
            self._project.panels[panel_index].condition_table = (
                self._make_custom_condition_table(
                    lane_count,
                    condition_rows,
                    group_ranges,
                )
            )

        self._active_table_style = "custom"
        self._project.global_layout.show_condition_table = True
        self._project.global_layout.condition_table_row_height_pt = 13.0
        self._rebuild_step4()
        # Preserve the user's current zoom/pan while the horizontal layout
        # aligns every blot stack below the tallest condition table.
        self._recompute_and_refresh(fit_view=False)
        self._canvas.restore_blot_view_state(blot_view_state)

    def _apply_condition_template(
        self,
        *,
        attach_current: bool,
        target_panel_idx: int | None,
        lane_count: int,
        condition_rows: int,
        group_ranges: object,
    ) -> None:
        if not attach_current or target_panel_idx is None:
            return
        self._apply_condition_templates_to_panels([
            (
                target_panel_idx,
                lane_count,
                condition_rows,
                group_ranges,
            )
        ])

    # ── Draw band with ROI ────────────────────────────────────────────────

    def _build_apply_roi_step(self) -> _CollapseGroup:
        grp = _CollapseGroup("Step 2: Fill Blot Frames", step_number=2)
        self._selected_slot_lbl = QLabel("Selected target: none")
        self._selected_slot_lbl.hide()

        detection_lbl = QLabel("Detection")
        detection_lbl.setStyleSheet(
            "font-size:10px; font-weight:600; color:#3A4B43; padding:1px;"
        )
        grp.body_layout().addWidget(detection_lbl)

        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(9)
        self._roi_mode_group = QButtonGroup(self)
        self._roi_mode_group.setExclusive(True)
        self._auto_detect_radio = QRadioButton("Auto Detect")
        self._manual_detect_radio = QRadioButton("Manual")
        self._auto_detect_radio.setChecked(True)
        for button in (self._auto_detect_radio, self._manual_detect_radio):
            button.setStyleSheet(
                "QRadioButton { font-size:9px; color:#2F3D36; spacing:4px; }"
                "QRadioButton::indicator { width:13px; height:13px; }"
            )
            self._roi_mode_group.addButton(button)
            mode_row.addWidget(button)
        mode_row.addStretch(1)
        self._auto_detect_radio.toggled.connect(self._on_roi_fill_mode_changed)
        grp.body_layout().addLayout(mode_row)

        enter_hint = QLabel("select then hit Enter/Return")
        enter_hint.setStyleSheet(
            "font-size:8px; color:#73867D; padding:0 1px 2px 1px;"
        )
        grp.body_layout().addWidget(enter_hint)

        self._auto_disclosure = _InlineDisclosure("Advanced")
        grp.body_layout().addWidget(self._auto_disclosure)

        self._auto_fit_options = QWidget()
        options_layout = QFormLayout(self._auto_fit_options)
        options_layout.setContentsMargins(0, 0, 0, 0)
        options_layout.setHorizontalSpacing(5)
        options_layout.setVerticalSpacing(2)
        self._auto_fit_h_margin = QSpinBox()
        self._auto_fit_h_margin.setRange(0, 200)
        self._auto_fit_h_margin.setValue(4)
        self._auto_fit_h_margin.setSingleStep(4)
        self._auto_fit_h_margin.setAccelerated(True)
        self._auto_fit_h_margin.setSuffix(" px")
        self._auto_fit_h_margin.setFixedHeight(22)
        self._auto_fit_h_margin.setStyleSheet(
            "QSpinBox { background:#FFFFFF; border:1px solid #C7D6CE; "
            "border-radius:4px; padding:1px 3px; font-size:9px; }"
        )
        self._auto_fit_h_margin.setToolTip(
            "Original-image background retained to the left and right of each "
            "detected lane crop. Arrow buttons adjust 4 px per step; type a "
            "value for 1 px precision."
        )
        self._auto_fit_v_margin = QSpinBox()
        self._auto_fit_v_margin.setRange(0, 200)
        self._auto_fit_v_margin.setValue(4)
        self._auto_fit_v_margin.setSingleStep(4)
        self._auto_fit_v_margin.setAccelerated(True)
        self._auto_fit_v_margin.setSuffix(" px")
        self._auto_fit_v_margin.setFixedHeight(22)
        self._auto_fit_v_margin.setStyleSheet(self._auto_fit_h_margin.styleSheet())
        self._auto_fit_v_margin.setToolTip(
            "Original-image background retained above and below each aligned "
            "band crop. Arrow buttons adjust 4 px per step; type a value for "
            "1 px precision."
        )
        self._auto_fit_alignment = QComboBox()
        self._auto_fit_alignment.addItem("Auto (Smear-resistant)", "auto")
        self._auto_fit_alignment.addItem("Band Center", "center")
        self._auto_fit_alignment.addItem("Band Top", "top")
        self._auto_fit_alignment.addItem("Band Bottom", "bottom")
        self._auto_fit_alignment.setCurrentIndex(0)
        self._auto_fit_alignment.setParent(self)
        self._auto_fit_alignment.hide()
        self._auto_fit_alignment.setToolTip(
            "Auto uses the more stable band edge when one-sided trailing is detected. "
            "No band pixels are redrawn, stretched, or erased."
        )
        margin_label_style = "font-size:9px; color:#3A4B43; background:transparent;"
        h_margin_lbl = QLabel("H margin")
        h_margin_lbl.setStyleSheet(margin_label_style)
        v_margin_lbl = QLabel("V margin")
        v_margin_lbl.setStyleSheet(margin_label_style)
        options_layout.addRow(h_margin_lbl, self._auto_fit_h_margin)
        options_layout.addRow(v_margin_lbl, self._auto_fit_v_margin)
        self._auto_disclosure.body_layout().addWidget(self._auto_fit_options)

        self._fixed_roi_disclosure = _InlineDisclosure("Fix ROI")
        self._fixed_roi_disclosure.setVisible(False)
        grp.body_layout().addWidget(self._fixed_roi_disclosure)

        fixed_row = QHBoxLayout()
        fixed_row.setContentsMargins(0, 0, 0, 0)
        fixed_row.setSpacing(6)

        fix_btn = QPushButton("Fix ROI")
        fix_btn.setStyleSheet(_SMALL_BTN_STYLE)
        fix_btn.setFixedHeight(24)
        fix_btn.setToolTip("Capture the current ROI size, or arm the next drawn ROI as the fixed size.")
        fix_btn.clicked.connect(self._on_add_fixed_roi_clicked)
        fixed_row.addWidget(fix_btn)

        cancel_btn = QPushButton("Cancel Fixed ROI")
        cancel_btn.setStyleSheet(_SMALL_BTN_STYLE)
        cancel_btn.setFixedHeight(24)
        cancel_btn.setToolTip("Return all WB image windows to freehand ROI drawing.")
        cancel_btn.clicked.connect(self._on_cancel_fixed_roi_clicked)
        fixed_row.addWidget(cancel_btn)
        self._fixed_roi_disclosure.body_layout().addLayout(fixed_row)

        self._fixed_roi_list = QListWidget()
        self._fixed_roi_list.setMinimumHeight(54)
        self._fixed_roi_list.setMaximumHeight(82)
        self._fixed_roi_list.setStyleSheet(
            "QListWidget { background:#FFFFFF; border:1px solid #CBD9D1; border-radius:6px; font-size:10px; }"
            "QListWidget::item { padding:1px 3px; }"
            "QListWidget::item:selected { background:#C9DED2; color:#1E3D2F; }"
        )
        self._fixed_roi_list.itemClicked.connect(self._on_fixed_roi_item_selected)
        self._fixed_roi_list.itemDoubleClicked.connect(self._on_fixed_roi_item_double_clicked)
        self._fixed_roi_disclosure.body_layout().addWidget(self._fixed_roi_list)
        return grp

    def _on_roi_fill_mode_changed(self, auto_enabled: bool) -> None:
        self._roi_fill_mode = "auto" if auto_enabled else "manual"
        self._auto_fit_review_pending = False
        self._auto_disclosure.setVisible(auto_enabled)
        self._fixed_roi_disclosure.setVisible(not auto_enabled)
        if self._auto_fit_overlay_handler is not None:
            self._auto_fit_overlay_handler(None)

    def _rebuild_step4(self) -> None:
        """Refresh the active target label after project structure changes."""
        self._step4_slot_widgets.clear()
        if self._project is None:
            self._active_slot_ref = None
        elif self._active_slot_ref is not None:
            slot = self._get_slot(
                self._active_slot_ref.panel_idx or -1,
                self._active_slot_ref.slot_idx or -1,
            )
            if slot is None:
                self._active_slot_ref = None
        self._refresh_selected_slot_label()

    def _on_add_fixed_roi_clicked(self) -> None:
        if self._fixed_roi_requested is None:
            return
        size = self._fixed_roi_requested()
        if size is None or size.width() <= 0 or size.height() <= 0:
            return
        name = self._next_fixed_roi_name()
        self._fixed_roi_sizes.append((name, QSizeF(size)))
        item = QListWidgetItem(name)
        item.setSizeHint(QSize(0, 24))
        item.setData(Qt.ItemDataRole.UserRole, QSizeF(size))
        item.setData(_FIXED_ROI_NAME_ROLE, name)
        item.setToolTip(f"{size.width():.0f} x {size.height():.0f} px")
        self._fixed_roi_list.addItem(item)
        self._install_fixed_roi_item_widget(item)
        self._fixed_roi_list.setCurrentItem(item)
        self._on_fixed_roi_item_selected(item)

    def _on_cancel_fixed_roi_clicked(self) -> None:
        self._fixed_roi_list.clearSelection()
        if self._fixed_roi_cancel_requested is not None:
            self._fixed_roi_cancel_requested()

    def _on_fixed_roi_item_selected(self, item: QListWidgetItem) -> None:
        size = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(size, QSizeF):
            row = self._fixed_roi_list.row(item)
            if 0 <= row < len(self._fixed_roi_sizes):
                size = self._fixed_roi_sizes[row][1]
        if isinstance(size, QSizeF) and self._fixed_roi_size_selected is not None:
            self._fixed_roi_size_selected(QSizeF(size))

    def _on_fixed_roi_item_double_clicked(self, item: QListWidgetItem) -> None:
        old_name = str(item.data(_FIXED_ROI_NAME_ROLE) or item.text())
        new_name, ok = QInputDialog.getText(self, "Rename Fixed ROI", "Name:", text=old_name)
        if not ok:
            return
        new_name = new_name.strip() or old_name
        row = self._fixed_roi_list.row(item)
        if 0 <= row < len(self._fixed_roi_sizes):
            _old_name, size = self._fixed_roi_sizes[row]
            self._fixed_roi_sizes[row] = (new_name, size)
        item.setText(new_name)
        item.setData(_FIXED_ROI_NAME_ROLE, new_name)
        self._install_fixed_roi_item_widget(item)

    def _install_fixed_roi_item_widget(self, item: QListWidgetItem) -> None:
        row_widget = QWidget()
        layout = QHBoxLayout(row_widget)
        layout.setContentsMargins(5, 0, 2, 0)
        layout.setSpacing(3)
        label = QLabel(str(item.data(_FIXED_ROI_NAME_ROLE) or item.text()))
        label.setStyleSheet("font-size:10px; color:#2C4A3D;")
        layout.addWidget(label, 1)
        delete_btn = QToolButton()
        delete_btn.setText("×")
        delete_btn.setToolTip("Delete this fixed ROI size")
        delete_btn.setFixedSize(16, 16)
        delete_btn.setStyleSheet(
            "QToolButton { border:1px solid #9EB3A8; border-radius:8px; background:#F7FAF8; color:#6A7A72; font-size:10px; }"
            "QToolButton:hover { background:#F0D7D7; color:#8A3B3B; border-color:#C99595; }"
        )
        delete_btn.clicked.connect(lambda _checked=False, it=item: self._delete_fixed_roi_item(it))
        layout.addWidget(delete_btn)
        self._fixed_roi_list.setItemWidget(item, row_widget)

    def _delete_fixed_roi_item(self, item: QListWidgetItem) -> None:
        row = self._fixed_roi_list.row(item)
        if row < 0:
            return
        was_current = item is self._fixed_roi_list.currentItem()
        if 0 <= row < len(self._fixed_roi_sizes):
            self._fixed_roi_sizes.pop(row)
        self._fixed_roi_list.takeItem(row)
        if was_current and self._fixed_roi_cancel_requested is not None:
            self._fixed_roi_cancel_requested()

    def _next_fixed_roi_name(self) -> str:
        return f"Fixed ROI {len(self._fixed_roi_sizes) + 1}"

    # ── Saved blot files ─────────────────────────────────────────────────

    def _build_saved_blot_files_step(self) -> _CollapseGroup:
        grp = _CollapseGroup("Saved Blot Files", step_number=3)
        bl = grp.body_layout()

        self._blot_file_list = QListWidget()
        self._blot_file_list.setMaximumHeight(86)
        self._blot_file_list.setStyleSheet(
            "QListWidget { background:#FFFFFF; border:1px solid #C9B6B6; border-radius:6px; font-size:10px; }"
            "QListWidget::item { padding:2px 4px; }"
            "QListWidget::item:selected { background:#E6CACA; color:#4A2428; }"
        )
        self._blot_file_list.itemClicked.connect(self._on_blot_file_list_clicked)
        self._blot_file_list.itemDoubleClicked.connect(
            lambda item: self._on_rename_blot_file(
                str(item.data(Qt.ItemDataRole.UserRole) or "")
            )
        )
        bl.addWidget(self._blot_file_list)

        open_btn = QPushButton("Open Blot File")
        open_btn.setStyleSheet(_SMALL_BTN_STYLE)
        open_btn.clicked.connect(self._on_open_blot_file)
        bl.addWidget(open_btn)

        self._populate_blot_file_list()
        return grp

    def _load_blot_file_summaries(self) -> list[dict]:
        if not USER_BLOT_FILES_DIR.exists():
            return []
        summaries: list[dict] = []
        for path in sorted(USER_BLOT_FILES_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if data.get("format") != "wb_blot_file":
                continue
            summaries.append({
                "id": str(data.get("id") or path.stem),
                "name": str(data.get("name") or path.stem),
                "path": path,
            })
        return summaries

    def _populate_blot_file_list(self) -> None:
        self._blot_file_list.clear()
        for summary in self._load_blot_file_summaries():
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, summary["id"])
            item.setSizeHint(QSize(0, 24))
            self._blot_file_list.addItem(item)
            self._install_blot_file_list_item(item, summary)
        if self._blot_file_list.count():
            self._blot_file_list.setCurrentRow(0)

    def _install_blot_file_list_item(self, item: QListWidgetItem, summary: dict) -> None:
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(6, 0, 4, 0)
        row_layout.setSpacing(4)
        lbl = _TemplateNameLabel(str(summary["name"]), str(summary["id"]))
        lbl.setStyleSheet("font-size:10px; color:#4A2428;")
        lbl.setCursor(Qt.CursorShape.IBeamCursor)
        lbl.setToolTip("Double-click to rename this saved Blot File")
        lbl.doubleClicked.connect(self._on_rename_blot_file)
        row_layout.addWidget(lbl, 1)
        del_btn = QToolButton()
        del_btn.setText("×")
        del_btn.setFixedSize(16, 16)
        del_btn.setToolTip(f'Delete Blot File "{summary["name"]}"')
        del_btn.setStyleSheet(
            "QToolButton { border:1px solid #BCA0A0; border-radius:8px; "
            "background:#FDF8F8; color:#7A5B5B; font-size:10px; }"
            "QToolButton:hover { background:#F0D7D7; color:#8A3B3B; border-color:#C99595; }"
        )
        del_btn.clicked.connect(lambda _=False, bid=summary["id"]: self._on_delete_blot_file(bid))
        row_layout.addWidget(del_btn)
        self._blot_file_list.setItemWidget(item, row_widget)

    def _on_blot_file_list_clicked(self, item: QListWidgetItem) -> None:
        self._blot_file_list.setCurrentItem(item)

    def _on_rename_blot_file(self, blot_file_id: str) -> None:
        if not blot_file_id:
            return
        path = self._blot_file_path(blot_file_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Rename Blot File",
                f"Failed to read the Blot File:\n{exc}",
            )
            return
        old_name = str(data.get("name") or blot_file_id)
        new_name, ok = QInputDialog.getText(
            self,
            "Rename Blot File",
            "Blot file name:",
            text=old_name,
        )
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name or new_name == old_name:
            return

        data["name"] = new_name
        USER_BLOT_FILES_DIR.mkdir(parents=True, exist_ok=True)
        fd, staged_value = tempfile.mkstemp(
            prefix=f".{blot_file_id}-rename-",
            suffix=".json",
            dir=str(USER_BLOT_FILES_DIR),
        )
        os.close(fd)
        staged_path = Path(staged_value)
        try:
            staged_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            json.loads(staged_path.read_text(encoding="utf-8"))
            os.replace(staged_path, path)
        except Exception as exc:
            staged_path.unlink(missing_ok=True)
            QMessageBox.critical(
                self,
                "Rename Blot File",
                f"Failed to rename the Blot File:\n{exc}",
            )
            return

        self._populate_blot_file_list()
        self._select_blot_file_id(blot_file_id)

    def _current_blot_file_selection(self) -> str | None:
        current = self._blot_file_list.currentItem()
        if current is None:
            return None
        return str(current.data(Qt.ItemDataRole.UserRole) or "")

    def _blot_file_path(self, blot_file_id: str) -> Path:
        return USER_BLOT_FILES_DIR / f"{blot_file_id}.json"

    def _on_delete_blot_file(self, blot_file_id: str) -> None:
        path = self._blot_file_path(blot_file_id)
        name = blot_file_id
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            name = str(data.get("name") or blot_file_id)
        except Exception:
            pass
        reply = QMessageBox.question(
            self,
            "Delete Blot File",
            f'Delete Blot File "{name}"?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if path.exists():
            path.unlink()
        assets_dir = USER_BLOT_FILES_DIR / f"{blot_file_id}_assets"
        if assets_dir.exists() and assets_dir.is_dir():
            shutil.rmtree(assets_dir)
        if self._active_blot_file_id == blot_file_id:
            self._active_blot_file_id = None
        self._populate_blot_file_list()

    def _on_open_blot_file(self) -> None:
        blot_file_id = self._current_blot_file_selection()
        if not blot_file_id:
            QMessageBox.warning(self, "Open Blot File", "Select a saved Blot File first.")
            return
        try:
            self._load_blot_file(blot_file_id)
        except Exception as exc:
            QMessageBox.critical(self, "Open Blot File", f"Failed:\n{exc}")

    def _load_blot_file(self, blot_file_id: str) -> None:
        path = self._blot_file_path(blot_file_id)
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("format") != "wb_blot_file":
            raise ValueError(f"Not a blot file: {blot_file_id!r}")
        self._project = self._project_from_blot_file_data(data.get("project", {}))
        self._active_blot_file_id = blot_file_id
        self._active_template_id = str(data.get("active_template_id") or self._project.template_type or "normal_wb")
        self._active_table_style = str(data.get("active_table_style") or "none")
        self._text_style_overrides = TemplateEngine._decode_text_style_overrides(
            data.get("text_style_overrides", [])
        )
        self._canvas.restore_state_snapshot(data.get("canvas_state", {}), repopulate_scene=False)
        if self._project.panels:
            for spin, val in [
                (self._panels_spin, len(self._project.panels)),
                (self._blots_spin, len(self._project.panels[0].blot_slots)),
                (
                    self._lanes_spin,
                    self._project.panels[0].blot_slots[0].lane_count
                    if self._project.panels[0].blot_slots else 4,
                ),
            ]:
                spin.blockSignals(True)
                spin.setValue(val)
                spin.blockSignals(False)
        self._active_slot_ref = None
        self._rebuild_step4()
        self._recompute_and_refresh()
        self._select_blot_file_id(blot_file_id)

    def _select_blot_file_id(self, blot_file_id: str) -> None:
        for row in range(self._blot_file_list.count()):
            item = self._blot_file_list.item(row)
            if str(item.data(Qt.ItemDataRole.UserRole)) == blot_file_id:
                self._blot_file_list.setCurrentItem(item)
                return

    @staticmethod
    def _project_from_blot_file_data(data: dict) -> FigureProject:
        base_gl = GlobalLayout()
        gl_raw = data.get("global_layout", {})
        gl = GlobalLayout(**{k: v for k, v in gl_raw.items() if hasattr(base_gl, k)})
        panels: list[Panel] = []
        for pd in data.get("panels", []):
            blots: list[BlotSlot] = []
            for bd in pd.get("blot_slots", []):
                bbox = bd.get("bounding_box")
                slot = BlotSlot(
                    label=str(bd.get("label", "IB: Protein")),
                    mw_marker=str(bd.get("mw_marker", "50 kDa")),
                    source_image_path=str(bd.get("source_image_path", "")),
                    bounding_box=ImageBBox.from_dict(bbox) if isinstance(bbox, dict) else None,
                    lane_count=int(bd.get("lane_count", 4)),
                    lane_rois=[
                        LaneROI(
                            lane_index=int(lr.get("lane_index", idx)),
                            x_offset=float(lr.get("x_offset", 0.0)),
                            width=float(lr.get("width", 1.0)),
                        )
                        for idx, lr in enumerate(bd.get("lane_rois", []))
                        if isinstance(lr, dict)
                    ],
                    lane_crops=[
                        ImageBBox.from_dict(lane_crop)
                        for lane_crop in bd.get("lane_crops", [])
                        if isinstance(lane_crop, dict)
                    ],
                    preserve_image_aspect=bool(
                        bd.get("preserve_image_aspect", bd.get("lane_crops"))
                    ),
                    display_width_pt=bd.get("display_width_pt"),
                    display_height_pt=bd.get("display_height_pt"),
                    image_transform=dict(bd.get("image_transform")) if isinstance(bd.get("image_transform"), dict) else None,
                    geometry_transform=dict(bd.get("geometry_transform")) if isinstance(bd.get("geometry_transform"), dict) else None,
                    saved_preview_path=str(bd.get("saved_preview_path", "")),
                )
                if not slot.lane_rois:
                    slot.reset_equal_lanes()
                blots.append(slot)
            ct = None
            ct_data = pd.get("condition_table")
            if isinstance(ct_data, dict):
                ct = ConditionTable(
                    headers=list(ct_data.get("headers", [])),
                    rows=[list(row) for row in ct_data.get("rows", [])],
                )
            panels.append(Panel(
                panel_letter=str(pd.get("panel_letter", "A")),
                title=str(pd.get("title", "")),
                blot_slots=blots,
                condition_table=ct,
            ))
        return FigureProject(
            template_type=str(data.get("template_type", "normal_wb")),
            global_layout=gl,
            panels=panels,
            metadata=dict(data.get("metadata", {})),
        )

    def _refresh_selected_slot_label(self) -> None:
        if self._active_slot_ref is None or self._active_slot_ref.panel_idx is None or self._active_slot_ref.slot_idx is None:
            self._selected_slot_lbl.setText(tr("Selected target: none", self._language))
            self._update_roi_step_visibility()
            return
        slot = self._get_slot(self._active_slot_ref.panel_idx, self._active_slot_ref.slot_idx)
        if slot is None:
            self._selected_slot_lbl.setText(tr("Selected target: none", self._language))
            self._update_roi_step_visibility()
            return
        panel_label = ""
        if self._project is not None and len(self._project.panels) > 1:
            panel = self._project.panels[self._active_slot_ref.panel_idx]
            panel_label = f"Panel {panel.panel_letter} - "
        target = f"{panel_label}[{self._active_slot_ref.slot_idx + 1}] {slot.label}"
        self._selected_slot_lbl.setText(tr("Selected target: {target}", self._language, target=target))
        self._update_roi_step_visibility()

    def _update_roi_step_visibility(self) -> None:
        has_structured_slot = (
            self._active_slot_ref is not None
            and self._active_slot_ref.panel_idx is not None
            and self._active_slot_ref.slot_idx is not None
        )
        has_floating_slot = bool(self._canvas.selected_overlay_blot_items())
        has_target = has_structured_slot or has_floating_slot
        self._grp4.setVisible(True)
        self._grp4.set_expanded(has_target)

    def _on_canvas_blot_selected(self, ref: SourceRef) -> None:
        if ref.panel_idx is None or ref.slot_idx is None:
            return
        self._active_slot_ref = ref
        self._refresh_selected_slot_label()
        self._on_canvas_selection_changed()
        if self._tutorial_mode:
            self.workflowEvent.emit("blot_frame_selected")

    def _on_canvas_blot_selection_cleared(self) -> None:
        self._active_slot_ref = None
        self._refresh_selected_slot_label()
        self._on_canvas_selection_changed()

    def _on_add_blot_frame(self) -> None:
        default_lane_count = max(1, int(self._lanes_spin.value()))
        lane_count, accepted = QInputDialog.getInt(
            self,
            "Add Extra Blot Frame",
            "Lane number:",
            default_lane_count,
            1,
            24,
            1,
        )
        if not accepted:
            return
        item = self._canvas.add_overlay_blot_frame(lane_count)
        item.setFocus()
        self._active_slot_ref = None
        self._selected_slot_lbl.setText(tr("Selected target: added blot frame", self._language))
        self._update_roi_step_visibility()
        self._on_canvas_selection_changed()

    def _on_canvas_view_interacted(self) -> None:
        if self._focus_requested is not None:
            self._focus_requested()

    def apply_roi_to_selected_slot(self) -> bool:
        if self._roi_fill_mode == "auto":
            applied = self._auto_fit_active_roi_to_selected_slot()
        else:
            if self._auto_fit_overlay_handler is not None:
                self._auto_fit_overlay_handler(None)
            applied = self._apply_manual_roi_to_selected_slot()
        if applied:
            self.workflowEvent.emit("blot_roi_applied")
        return applied

    def _apply_manual_roi_to_selected_slot(self) -> bool:
        floating_blots = self._canvas.selected_overlay_blot_items()
        if floating_blots:
            return self._on_use_active_image_roi_for_overlay(floating_blots)

        ref = self._active_slot_ref
        if ref is None:
            refs = self._canvas.selected_blot_refs()
            ref = refs[0] if refs else None
        if ref is None or ref.panel_idx is None or ref.slot_idx is None:
            QMessageBox.warning(
                self,
                "No Target",
                "Select a Blot Frame in the preview before pressing Enter/Return.",
            )
            return False
        return self._on_use_active_image_roi(ref.panel_idx, ref.slot_idx)

    def _selected_auto_fit_target(
        self,
    ) -> tuple[list, SourceRef | None, int] | None:
        floating_blots = self._canvas.selected_overlay_blot_items()
        if floating_blots:
            return (
                floating_blots,
                None,
                max(1, int(getattr(floating_blots[0], "lane_count", 1))),
            )

        ref = self._active_slot_ref
        if ref is None:
            refs = self._canvas.selected_blot_refs()
            ref = refs[0] if refs else None
        if ref is None or ref.panel_idx is None or ref.slot_idx is None:
            QMessageBox.warning(
                self,
                "No Target",
                "Select a Blot Frame in the preview before pressing Enter/Return.",
            )
            return None
        slot = self._get_slot(ref.panel_idx, ref.slot_idx)
        if slot is None:
            QMessageBox.warning(
                self,
                "No Blot Frame",
                "Complete Step 1: Choose Layout, then select a Blot Frame.",
            )
            return None
        return [], ref, max(1, int(slot.lane_count))

    def _auto_fit_active_roi_to_selected_slot(self) -> bool:
        target = self._selected_auto_fit_target()
        if target is None:
            return False
        floating_blots, ref, expected_lane_count = target
        if self._auto_fit_detection_handler is None:
            QMessageBox.warning(
                self,
                "Band Auto-Fit",
                "The active WB image detector is not connected.",
            )
            return False

        source = self._auto_fit_detection_handler(
            expected_lane_count,
            self._auto_fit_review_pending,
        )
        error = source.get("error")
        if error:
            QMessageBox.warning(self, "Band Auto-Fit", str(error))
            return False

        image_path = str(source.get("image_path", ""))
        search_roi = source.get("roi")
        detections = source.get("auto_detections")
        image_size = source.get("image_size")
        if (
            not image_path
            or search_roi is None
            or not isinstance(detections, list)
            or not detections
        ):
            metadata = source.get("metadata")
            detail = ""
            if isinstance(metadata, dict):
                detail = str(metadata.get("message") or metadata.get("failure_stage") or "")
            QMessageBox.warning(
                self,
                "Nothing Detected",
                "No usable lanes or bands were found inside the rough ROI."
                + (f"\n\n{detail}" if detail else ""),
            )
            return False

        if isinstance(image_size, QSizeF):
            image_width = int(round(image_size.width()))
            image_height = int(round(image_size.height()))
        else:
            from PIL import Image
            with Image.open(image_path) as image:
                image_width, image_height = image.size

        try:
            result = calculate_band_auto_fit(
                detections,
                search_roi=search_roi,
                image_width=image_width,
                image_height=image_height,
                horizontal_margin_px=self._auto_fit_h_margin.value(),
                vertical_margin_px=self._auto_fit_v_margin.value(),
                alignment=str(self._auto_fit_alignment.currentData() or "auto"),
                expected_lane_count=expected_lane_count,
            )
        except (TypeError, ValueError) as exc:
            QMessageBox.warning(self, "Band Auto-Fit", str(exc))
            return False

        crop = result.crop_box
        crop_rect = QRectF(crop.x, crop.y, crop.w, crop.h)
        if self._auto_fit_overlay_handler is not None:
            self._auto_fit_overlay_handler(crop_rect)

        reused_review = bool(source.get("reused")) and self._auto_fit_review_pending
        if result.low_confidence and not reused_review:
            self._auto_fit_review_pending = True
            QMessageBox.warning(
                self,
                "Low-confidence detection",
                "Detection confidence is low. Review or resize the orange band boxes, "
                "then press Enter/Return again to confirm this crop.",
            )
            return False

        transform = source.get("image_transform")
        image_transform = dict(transform) if isinstance(transform, dict) else None
        geometry_value = source.get("geometry_transform")
        geometry_transform = (
            dict(geometry_value) if isinstance(geometry_value, dict) else None
        )
        if geometry_transform_from_dict(geometry_transform).is_identity():
            # Preserve the legacy identity representation while retaining all
            # non-identity presentation geometry with its presentation crop.
            geometry_transform = None
        roi = crop.to_dict()
        crop_aspect_ratio = crop.w / crop.h
        blot_view_state = self._canvas.capture_blot_view_state()
        self._remember_canvas_undo_state()
        if floating_blots:
            for item in floating_blots:
                frame_width = item.rect().width()
                item.resize_to_local_size(
                    frame_width,
                    frame_width / crop_aspect_ratio,
                )
                item.image_path = image_path
                item.roi = dict(roi)
                item.transform = dict(image_transform or {})
                item.geometry_transform = dict(geometry_transform or {})
                item.preserve_aspect = True
                item.update()
            self._canvas.viewport().update()
        elif ref is not None and ref.panel_idx is not None and ref.slot_idx is not None:
            slot = self._get_slot(ref.panel_idx, ref.slot_idx)
            if slot is None:
                return False
            slot.source_image_path = image_path
            slot.bounding_box = crop
            slot.image_transform = image_transform
            slot.geometry_transform = geometry_transform
            slot.lane_crops = list(result.lane_crop_boxes)
            slot.preserve_image_aspect = True
            slot.saved_preview_path = ""
            frame = self._canvas._blot_frames.get(ref.key())
            if frame is not None:
                frame_width_pt = scene_to_pt(frame.rect().width())
                slot.display_height_pt = frame_width_pt / crop_aspect_ratio
            # Keep lane definitions and frame width unchanged. Only Auto-Fit
            # height follows the tight crop so contain-scaling fills the frame.
            self._rebuild_step4()
            self._recompute_and_refresh(fit_view=False)
            self._canvas.restore_blot_view_state(blot_view_state)

        self._auto_fit_review_pending = False
        if result.margin_clipped:
            QMessageBox.information(
                self,
                "Band Auto-Fit",
                "The Blot Frame was filled successfully. Part of the requested margin "
                "was clipped by the source-image boundary.",
            )
        elif result.padding_required:
            QMessageBox.information(
                self,
                "Band Auto-Fit",
                "The Blot Frame was filled successfully. A lane crop crossed the "
                "source-image edge, so background-value padding was added "
                "without stretching or generating band pixels.",
            )
        return True

    @staticmethod
    def _lane_rois_for_auto_fit(
        source_centers_x: tuple[float, ...],
        crop: ImageBBox,
    ) -> list[LaneROI]:
        """Keep condition cells centred over lanes in a continuous crop."""
        if crop.w <= 0 or not source_centers_x:
            return []
        centers = [
            min(1.0, max(0.0, (float(center) - crop.x) / crop.w))
            for center in source_centers_x
        ]
        if len(centers) >= 2:
            spacings = [
                right - left
                for left, right in zip(centers, centers[1:])
                if right - left > 1e-6
            ]
            width = median(spacings) if spacings else 1.0 / len(centers)
        else:
            width = 1.0
        width = min(1.0, max(1e-6, width))
        rois: list[LaneROI] = []
        for index, center in enumerate(centers):
            half_width = min(width / 2.0, center, 1.0 - center)
            half_width = max(5e-7, half_width)
            left = max(0.0, center - half_width)
            right = min(1.0, center + half_width)
            rois.append(LaneROI(
                lane_index=index,
                x_offset=round(left, 8),
                width=round(max(1e-6, right - left), 8),
            ))
        return rois

    def _active_image_roi_payload(
        self,
    ) -> tuple[str, dict[str, float], dict | None, dict | None] | None:
        if self._active_image_provider is None:
            QMessageBox.warning(self, "No Active Image", "The main WB image viewer is not connected.")
            return None

        source = self._active_image_provider()
        error = source.get("error")
        if error:
            QMessageBox.warning(self, "No Active ROI", str(error))
            return None

        image_path = str(source.get("image_path", ""))
        roi = source.get("roi")
        if not image_path or roi is None:
            QMessageBox.warning(self, "No Active ROI", "Draw a lane ROI on the active WB image first.")
            return None

        image_transform = source.get("image_transform")
        transform = dict(image_transform) if isinstance(image_transform, dict) else None
        geometry_value = source.get("geometry_transform")
        geometry_transform = (
            dict(geometry_value) if isinstance(geometry_value, dict) else None
        )
        return (
            image_path,
            {
                "x": float(roi.x()),
                "y": float(roi.y()),
                "w": float(roi.width()),
                "h": float(roi.height()),
            },
            transform,
            geometry_transform,
        )

    def _on_use_active_image_roi_for_overlay(self, items: list) -> bool:
        payload = self._active_image_roi_payload()
        if payload is None:
            return False
        image_path, roi, transform, geometry_transform = payload
        self._remember_canvas_undo_state()
        for item in items:
            item.image_path = image_path
            item.roi = dict(roi)
            item.transform = dict(transform or {})
            item.geometry_transform = dict(geometry_transform or {})
            item.preserve_aspect = False
            item.update()
        self._canvas.viewport().update()
        return True

    def _on_use_active_image_roi(self, pi: int, si: int) -> bool:
        slot = self._get_slot(pi, si)
        if slot is None:
            QMessageBox.warning(
                self,
                "No Blot Frame",
                "Complete Step 1: Choose Layout, then select a Blot Frame.",
            )
            return False
        payload = self._active_image_roi_payload()
        if payload is None:
            return False
        image_path, roi, image_transform, geometry_transform = payload

        blot_view_state = self._canvas.capture_blot_view_state()
        self._remember_canvas_undo_state()
        slot.source_image_path = image_path
        slot.bounding_box = ImageBBox(
            x=roi["x"],
            y=roi["y"],
            w=roi["w"],
            h=roi["h"],
        )
        slot.image_transform = image_transform
        slot.geometry_transform = geometry_transform
        slot.lane_crops = []
        slot.preserve_image_aspect = False
        slot.saved_preview_path = ""
        # Preserve lane_count, lane_rois, display width/height and all canvas
        # offsets.  The selected ROI only changes the pixels inside the frame.

        self._rebuild_step4()
        self._recompute_and_refresh(fit_view=False)
        self._canvas.restore_blot_view_state(blot_view_state)
        return True

    # ── Step 4: Export ────────────────────────────────────────────────────

    def _build_step6(self) -> _CollapseGroup:
        grp = _CollapseGroup("Export Figure", step_number=4)
        bl = grp.body_layout()

        formats_row = QHBoxLayout()
        formats_row.setContentsMargins(0, 0, 0, 0)
        formats_row.setSpacing(5)

        self._export_pdf_btn = QPushButton("PDF")
        self._export_pdf_btn.setObjectName("exportFormatButton")
        self._export_pdf_btn.setFixedHeight(25)
        self._export_pdf_btn.setStyleSheet(_SECONDARY_EXPORT_BTN_STYLE)
        self._export_pdf_btn.setToolTip("Export Figure as PDF")
        self._export_pdf_btn.clicked.connect(self._on_export_pdf)
        formats_row.addWidget(self._export_pdf_btn)

        self._export_tiff_btn = QPushButton("TIFF")
        self._export_tiff_btn.setObjectName("exportFormatButton")
        self._export_tiff_btn.setFixedHeight(25)
        self._export_tiff_btn.setStyleSheet(_SECONDARY_EXPORT_BTN_STYLE)
        self._export_tiff_btn.setToolTip("Export Figure as TIFF")
        self._export_tiff_btn.clicked.connect(self._on_export_tiff)
        formats_row.addWidget(self._export_tiff_btn)

        self._export_pptx_btn = QPushButton("PPTX")
        self._export_pptx_btn.setObjectName("exportFormatButton")
        self._export_pptx_btn.setFixedHeight(25)
        self._export_pptx_btn.setStyleSheet(_SECONDARY_EXPORT_BTN_STYLE)
        self._export_pptx_btn.setToolTip("Export Figure as PPTX")
        if not PPTX_AVAILABLE:
            self._export_pptx_btn.setEnabled(False)
            self._export_pptx_btn.setToolTip(
                "python-pptx is not installed.\n"
                "Run:  pip install python-pptx"
            )
        self._export_pptx_btn.clicked.connect(self._on_export_pptx)
        formats_row.addWidget(self._export_pptx_btn)
        bl.addLayout(formats_row)

        if not PPTX_AVAILABLE:
            note = QLabel(
                "PPTX export requires python-pptx.\n"
                "pip install python-pptx"
            )
            note.setStyleSheet("color:#A66; font-size:9px;")
            bl.addWidget(note)

        return grp

    # ── Canvas annotation toolbar ─────────────────────────────────────────

    def _build_canvas_toolbar(self) -> QWidget:
        outer = QWidget()
        outer.setStyleSheet(
            "QWidget { background: #DDE8E2; border-bottom: 1px solid #BDD0C6; }"
        )
        vbox = QVBoxLayout(outer)
        vbox.setContentsMargins(8, 4, 8, 4)
        vbox.setSpacing(3)

        # ── Row 1: action buttons ─────────────────────────────────────────
        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(6)

        add_text_btn = QPushButton("+ Text")
        add_text_btn.setStyleSheet(_SMALL_BTN_STYLE)
        add_text_btn.setToolTip("Click then click on canvas to place a text box (Esc to cancel)")
        add_text_btn.setFixedHeight(_TOOLBAR_BTN_H)
        add_text_btn.clicked.connect(self._on_add_overlay_text)
        row1.addWidget(add_text_btn)

        add_line_btn = QPushButton("+ Line")
        add_line_btn.setStyleSheet(_SMALL_BTN_STYLE)
        add_line_btn.setToolTip("Click then click on canvas to place a line (Esc to cancel)")
        add_line_btn.setFixedHeight(_TOOLBAR_BTN_H)
        add_line_btn.clicked.connect(self._on_add_overlay_line)
        row1.addWidget(add_line_btn)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.VLine)
        sep1.setStyleSheet("QFrame { color:#AABDB4; }")
        sep1.setFixedWidth(1)
        row1.addWidget(sep1)

        self._annot_undo_btn = QPushButton("Undo")
        self._annot_undo_btn.setStyleSheet(_SMALL_BTN_STYLE)
        self._annot_undo_btn.setFixedHeight(_TOOLBAR_BTN_H)
        self._annot_undo_btn.setEnabled(False)
        row1.addWidget(self._annot_undo_btn)
        self._annot_undo_btn.clicked.connect(self._undo_canvas_state)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setStyleSheet("QFrame { color:#AABDB4; }")
        sep2.setFixedWidth(1)
        row1.addWidget(sep2)

        save_tmpl_btn = QPushButton("Save Figure Template")
        save_tmpl_btn.setStyleSheet(_SMALL_BTN_STYLE)
        save_tmpl_btn.setToolTip(
            "Save current layout (structure, labels, annotations)\n"
            "as a reusable Figure Template in Saved Templates"
        )
        save_tmpl_btn.setFixedHeight(_TOOLBAR_BTN_H)
        save_tmpl_btn.clicked.connect(self._on_save_template)
        row1.addWidget(save_tmpl_btn)

        save_blot_btn = QPushButton("Save Blot File")
        save_blot_btn.setStyleSheet(_SMALL_BTN_STYLE)
        save_blot_btn.setToolTip(
            "Save current layout, annotations, blot images, ROIs, and editable state"
        )
        save_blot_btn.setFixedHeight(_TOOLBAR_BTN_H)
        save_blot_btn.clicked.connect(self._on_save_blot_file)
        row1.addWidget(save_blot_btn)

        self._fit_center_btn = QPushButton("Fit & Center")
        self._fit_center_btn.setStyleSheet(_HIGHLIGHT_BTN_STYLE)
        self._fit_center_btn.setToolTip(
            "Reset zoom and place the complete WB figure in the canvas center"
        )
        self._fit_center_btn.setFixedHeight(_TOOLBAR_BTN_H)
        self._fit_center_btn.clicked.connect(
            lambda: self._canvas.fit_frame_content_to_view()
        )
        row1.addWidget(self._fit_center_btn)

        reset_btn = QPushButton("Reset")
        reset_btn.setStyleSheet(_SMALL_BTN_STYLE)
        reset_btn.setToolTip("Clear only the WB Plot Figure Generation canvas and controls")
        reset_btn.setFixedHeight(_TOOLBAR_BTN_H)
        reset_btn.clicked.connect(self._on_reset_figure_generation)
        row1.addWidget(reset_btn)

        row1.addStretch()
        vbox.addLayout(row1)

        # ── Row 2: font & line-width controls ────────────────────────────
        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(4)

        font_lbl = QLabel("Font:")
        font_lbl.setStyleSheet("font-size:9px; color:#3D4D56;")
        row2.addWidget(font_lbl)

        self._toolbar_font_family_combo = QFontComboBox()
        self._toolbar_font_family_combo.setFixedWidth(104)
        self._toolbar_font_family_combo.setFixedHeight(_TOOLBAR_BTN_H)
        self._toolbar_font_family_combo.setStyleSheet(
            "QFontComboBox { font-size:9px; padding:0 18px 0 5px; "
            "background:#FFFFFF; border:1px solid #B0C8BB; border-radius:4px; }"
            "QFontComboBox::drop-down { width:18px; border-left:1px solid #C7D8D0; }"
        )
        self._toolbar_font_family_combo.setEnabled(False)
        self._toolbar_font_family_combo.currentFontChanged.connect(
            lambda f: self._apply_toolbar_font(family=f.family())
        )
        row2.addWidget(self._toolbar_font_family_combo)

        self._toolbar_font_menu_btn = QToolButton()
        self._toolbar_font_menu_btn.setText("▾")
        self._toolbar_font_menu_btn.setToolTip("Choose font")
        self._toolbar_font_menu_btn.setFixedSize(18, _TOOLBAR_BTN_H)
        self._toolbar_font_menu_btn.setStyleSheet(
            "QToolButton { border:1px solid #B0C8BB; border-radius:4px; "
            "background:#EBF3EE; color:#2D4A3D; font-size:11px; }"
            "QToolButton:hover { background:#CDDFD5; }"
        )
        self._toolbar_font_menu_btn.setEnabled(False)
        self._toolbar_font_menu_btn.clicked.connect(self._toolbar_font_family_combo.showPopup)
        row2.addWidget(self._toolbar_font_menu_btn)

        size_lbl = QLabel("Size:")
        size_lbl.setStyleSheet("font-size:9px; color:#3D4D56;")
        row2.addWidget(size_lbl)

        self._toolbar_font_size_combo = QComboBox()
        self._toolbar_font_size_combo.setEditable(True)
        self._toolbar_font_size_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._toolbar_font_size_combo.addItems(
            ["6", "7", "8", "9", "10", "11", "12", "14", "16", "18", "20", "24", "28", "36", "48", "72"]
        )
        self._toolbar_font_size_combo.setCurrentText("12")
        self._toolbar_font_size_combo.setFixedWidth(52)
        self._toolbar_font_size_combo.setFixedHeight(_TOOLBAR_BTN_H)
        self._toolbar_font_size_combo.setStyleSheet(
            "QComboBox { font-size:9px; padding:0 16px 0 5px; "
            "background:#FFFFFF; border:1px solid #B0C8BB; border-radius:4px; }"
            "QComboBox::drop-down { width:16px; border-left:1px solid #C7D8D0; }"
        )
        self._toolbar_font_size_combo.setEnabled(False)
        self._toolbar_font_size_combo.currentTextChanged.connect(
            self._on_toolbar_font_size_changed
        )
        row2.addWidget(self._toolbar_font_size_combo)

        def _fmt_btn(text: str, tip: str) -> QToolButton:
            btn = QToolButton()
            btn.setText(text)
            btn.setCheckable(True)
            btn.setFixedSize(_TOOLBAR_BTN_H, _TOOLBAR_BTN_H)
            btn.setToolTip(tip)
            btn.setStyleSheet(
                "QToolButton { border:1px solid #B0C8BB; border-radius:4px; "
                "background:#EBF3EE; font-size:10px; }"
                "QToolButton:checked { background:#A8D4BC; border-color:#6AAB8E; }"
                "QToolButton:hover { background:#CDDFD5; }"
            )
            return btn

        self._bold_btn = _fmt_btn("B", "Bold")
        _bfont = self._bold_btn.font()
        _bfont.setBold(True)
        self._bold_btn.setFont(_bfont)
        self._bold_btn.toggled.connect(lambda v: self._apply_toolbar_font(bold=v))
        self._bold_btn.setEnabled(False)
        row2.addWidget(self._bold_btn)

        self._italic_btn = _fmt_btn("I", "Italic")
        self._italic_btn.toggled.connect(lambda v: self._apply_toolbar_font(italic=v))
        self._italic_btn.setEnabled(False)
        row2.addWidget(self._italic_btn)

        self._underline_btn = _fmt_btn("U", "Underline")
        self._underline_btn.toggled.connect(lambda v: self._apply_toolbar_font(underline=v))
        self._underline_btn.setEnabled(False)
        row2.addWidget(self._underline_btn)

        # Continue Row 2 with text-box layout controls.

        self._align_text_boxes_combo = QComboBox()
        self._align_text_boxes_combo.setToolTip("Align selected text boxes")
        self._align_text_boxes_combo.setFixedWidth(132)
        self._align_text_boxes_combo.setFixedHeight(_TOOLBAR_BTN_H)
        self._align_text_boxes_combo.setStyleSheet("font-size:9px;")
        self._align_text_boxes_combo.addItem("Align text Boxes", "")
        for label, action in [
            ("Align Left", "left"),
            ("Align Center", "center_h"),
            ("Align Right", "right"),
            ("Align Top", "top"),
            ("Align Middle", "middle_v"),
            ("Align Bottom", "bottom"),
            ("Distribute Horizontally", "distribute_h"),
            ("Distribute Vertically", "distribute_v"),
        ]:
            self._align_text_boxes_combo.addItem(label, action)
        self._align_text_boxes_combo.currentIndexChanged.connect(self._on_align_text_boxes_combo_changed)
        self._align_text_boxes_combo.setEnabled(False)
        row2.addWidget(self._align_text_boxes_combo)

        def _content_align_btn(align: str, tip: str) -> QToolButton:
            btn = QToolButton()
            btn.setFixedSize(_TOOLBAR_BTN_H, _TOOLBAR_BTN_H)
            btn.setIcon(self._make_text_align_icon(align))
            btn.setIconSize(QSize(22, 22))
            btn.setToolTip(tip)
            btn.setStyleSheet(
                "QToolButton { border:1px solid #B0C8BB; border-radius:4px; "
                "background:#EBF3EE; padding:2px; }"
                "QToolButton:hover { background:#CDDFD5; }"
            )
            btn.clicked.connect(lambda _checked=False, a=align: self._apply_text_content_alignment(a))
            return btn

        self._text_inside_left_btn = _content_align_btn("left", "Text inside box: left")
        self._text_inside_left_btn.setEnabled(False)
        row2.addWidget(self._text_inside_left_btn)

        self._text_inside_center_btn = _content_align_btn("center", "Text inside box: center")
        self._text_inside_center_btn.setEnabled(False)
        row2.addWidget(self._text_inside_center_btn)

        self._text_inside_right_btn = _content_align_btn("right", "Text inside box: right")
        self._text_inside_right_btn.setEnabled(False)
        row2.addWidget(self._text_inside_right_btn)

        # ── Row 3: selection-only line controls ──────────────────────────
        self._selection_detail_toolbar = QWidget()
        row4 = QHBoxLayout(self._selection_detail_toolbar)
        row4.setContentsMargins(0, 0, 0, 0)
        row4.setSpacing(4)

        self._line_width_label = QLabel("Line:")
        self._line_width_label.setStyleSheet("font-size:9px; color:#3D4D56;")
        row4.addWidget(self._line_width_label)

        self._line_width_spin = QDoubleSpinBox()
        self._line_width_spin.setRange(0.5, 10.0)
        self._line_width_spin.setSingleStep(0.5)
        self._line_width_spin.setDecimals(1)
        self._line_width_spin.setValue(1.5)
        self._line_width_spin.setFixedWidth(58)
        self._line_width_spin.setFixedHeight(_TOOLBAR_BTN_H)
        self._line_width_spin.setSuffix(" pt")
        self._line_width_spin.setStyleSheet("font-size:9px;")
        self._line_width_spin.setEnabled(False)
        self._line_width_spin.valueChanged.connect(self._apply_selected_line_width)
        row4.addWidget(self._line_width_spin)

        self._match_size_btn = QToolButton()
        self._match_size_btn.setText("Same Size")
        self._match_size_btn.setToolTip("Make selected text boxes or Blot Frames the same size")
        self._match_size_btn.setFixedHeight(_TOOLBAR_BTN_H)
        self._match_size_btn.setEnabled(False)
        self._match_size_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._match_size_btn.setStyleSheet(
            "QToolButton { border:1px solid #B0C8BB; border-radius:4px; "
            "background:#EBF3EE; font-size:9px; padding:2px 8px; }"
            "QToolButton:hover { background:#CDDFD5; }"
            "QToolButton::menu-indicator { image:none; width:0px; }"
        )
        match_menu = QMenu(self._match_size_btn)
        largest_action = match_menu.addAction("Match Largest")
        largest_action.triggered.connect(lambda: self._match_selected_item_sizes("largest"))
        smallest_action = match_menu.addAction("Match Smallest")
        smallest_action.triggered.connect(lambda: self._match_selected_item_sizes("smallest"))
        self._match_size_btn.setMenu(match_menu)
        row2.addWidget(self._match_size_btn)

        row2.addStretch()
        row4.addStretch()
        vbox.addLayout(row2)
        # Reserve this row permanently. Showing line controls must not change
        # toolbar height and push the blot canvas vertically.
        self._selection_detail_toolbar.setFixedHeight(_TOOLBAR_BTN_H)
        self._line_width_label.setVisible(False)
        self._line_width_spin.setVisible(False)
        self._selection_detail_toolbar.setVisible(True)
        vbox.addWidget(self._selection_detail_toolbar)
        return outer

    # ── Annotation / format handlers ──────────────────────────────────────

    def _on_add_overlay_text(self) -> None:
        self._canvas.enter_place_mode("text")

    def _on_add_overlay_line(self) -> None:
        self._canvas.enter_place_mode("line")

    def _make_text_align_icon(self, align: str) -> QIcon:
        pm = QPixmap(24, 24)
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor("#2D3B38"), 1.8)
        pen.setCapStyle(Qt.PenCapStyle.SquareCap)
        painter.setPen(pen)
        widths = [15, 21, 12, 18]
        ys = [6, 10, 14, 18]
        for width, y in zip(widths, ys):
            if align == "center":
                x = (24 - width) / 2.0
            elif align == "right":
                x = 21 - width
            else:
                x = 3
            painter.drawLine(int(x), y, int(x + width), y)
        painter.end()
        return QIcon(pm)

    def keyPressEvent(self, event) -> None:
        if self._is_undo_key_event(event):
            self._queue_canvas_undo()
            event.accept()
            return
        super().keyPressEvent(event)

    def _is_undo_key_event(self, event) -> bool:
        try:
            if event.matches(QKeySequence.StandardKey.Undo):
                return True
        except Exception:
            pass
        return (
            event.key() == Qt.Key.Key_Z
            and bool(
                event.modifiers()
                & (Qt.KeyboardModifier.MetaModifier | Qt.KeyboardModifier.ControlModifier)
            )
        )

    def _remember_canvas_undo_state(self) -> None:
        if self._restoring_canvas_undo:
            return
        snapshot = {
            "canvas": self._canvas.state_snapshot(),
            "text_style_overrides": dict(self._text_style_overrides),
            "project": copy.deepcopy(self._project),
        }
        if self._canvas_undo_stack and self._canvas_undo_stack[-1] == snapshot:
            return
        self._canvas_undo_stack.append(snapshot)
        if len(self._canvas_undo_stack) > 10:
            self._canvas_undo_stack.pop(0)
        self._annot_undo_btn.setEnabled(True)

    def _queue_canvas_undo(self) -> None:
        if self._restoring_canvas_undo or self._canvas_undo_queued or not self._canvas_undo_stack:
            return
        self._canvas_undo_queued = True
        with QSignalBlocker(self._canvas._scene):
            self._canvas._scene.clearFocus()
            self._canvas._scene.clearSelection()
        # Keyboard undo can arrive while QGraphicsView/QGraphicsScene are still
        # dispatching the key event to selected items.  Wait two event-loop
        # turns before rebuilding the scene so Qt has released those refs.
        QTimer.singleShot(0, lambda: QTimer.singleShot(0, self._run_queued_canvas_undo))

    def _run_queued_canvas_undo(self) -> None:
        self._canvas_undo_queued = False
        self._undo_canvas_state()

    def _undo_canvas_state(self) -> None:
        if not self._canvas_undo_stack:
            return
        snapshot = self._canvas_undo_stack.pop()
        self._restoring_canvas_undo = True
        try:
            self._text_style_overrides = dict(snapshot.get("text_style_overrides", {}))
            if "project" in snapshot:
                self._project = snapshot.get("project")
            self._canvas.restore_state_snapshot(
                snapshot.get("canvas", {}),
                repopulate_scene=self._project is None,
            )
            with QSignalBlocker(self._canvas._scene):
                if self._project is None:
                    self._canvas.render(LayoutResult(), None)  # type: ignore[arg-type]
                    self._layout_result = None
                else:
                    self._recompute_and_refresh(fit_view=False)
            self._on_canvas_selection_changed()
        finally:
            self._restoring_canvas_undo = False
            self._annot_undo_btn.setEnabled(bool(self._canvas_undo_stack))

    def _apply_toolbar_font(
        self,
        family: str | None = None,
        size: float | None = None,
        bold: bool | None = None,
        italic: bool | None = None,
        underline: bool | None = None,
    ) -> None:
        if not self._canvas.selected_text_items():
            return
        self._remember_canvas_undo_state()
        styles = self._canvas.apply_selected_text_font(
            family=family,
            size=size,
            bold=bold,
            italic=italic,
            underline=underline,
        )
        if styles:
            self._text_style_overrides.update(styles)
            self._apply_text_style_overrides_to_layout()

    def _on_toolbar_font_size_changed(self, text: str) -> None:
        try:
            size = float(text.strip())
        except ValueError:
            return
        if 4.0 <= size <= 72.0:
            self._apply_toolbar_font(size=size)

    def _apply_selected_line_width(self, width: float) -> None:
        self._canvas.set_selected_line_width(width)

    def _on_align_text_boxes_combo_changed(self, index: int) -> None:
        action = str(self._align_text_boxes_combo.itemData(index) or "")
        if action:
            self._align_selected_text_boxes(action)
        self._align_text_boxes_combo.blockSignals(True)
        self._align_text_boxes_combo.setCurrentIndex(0)
        self._align_text_boxes_combo.blockSignals(False)

    def _align_selected_text_boxes(self, action: str) -> None:
        self._canvas.align_selected_text_boxes(action)

    def _match_selected_item_sizes(self, mode: str) -> None:
        self._canvas.match_selected_item_sizes(mode)

    def _apply_text_content_alignment(self, align: str) -> None:
        styles = self._canvas.apply_selected_text_content_alignment(align)
        if styles:
            self._text_style_overrides.update(styles)
            self._apply_text_style_overrides_to_layout()

    def _on_canvas_selection_changed(self) -> None:
        """Update font/line-width controls to reflect the current selection."""
        from gui.figure_canvas import EditableTextItem as _BT
        from gui.layout_editor_items import EditableTextItem as _OT, LineElementItem as _OL
        sel = self._canvas._scene.selectedItems()

        # Find first selected overlay text and line
        sel_text = next(
            (i for i in sel if isinstance(i, (_OT, _BT))),
            None,
        )
        sel_line = next(
            (i for i in sel if isinstance(i, _OL)),
            None,
        )

        # Font controls
        has_text = sel_text is not None
        has_blot = bool(self._canvas.selected_blot_refs())
        has_floating_blot = bool(self._canvas.selected_overlay_blot_items())
        if has_floating_blot:
            self._active_slot_ref = None
            self._selected_slot_lbl.setText(tr("Selected target: added blot frame", self._language))
        elif not has_blot and self._active_slot_ref is None:
            self._refresh_selected_slot_label()
        can_align_or_size = has_text or has_blot or has_floating_blot
        for w in (self._toolbar_font_family_combo, self._toolbar_font_menu_btn,
                  self._toolbar_font_size_combo,
                  self._bold_btn, self._italic_btn, self._underline_btn,
                  self._text_inside_left_btn,
                  self._text_inside_center_btn, self._text_inside_right_btn):
            w.setEnabled(has_text)
        self._align_text_boxes_combo.setEnabled(can_align_or_size)
        self._match_size_btn.setEnabled(self._canvas.selected_layout_item_count() >= 2)
        if has_text:
            font = sel_text.font()
            self._toolbar_font_family_combo.blockSignals(True)
            self._toolbar_font_family_combo.setCurrentFont(font)
            self._toolbar_font_family_combo.blockSignals(False)
            self._toolbar_font_size_combo.blockSignals(True)
            self._toolbar_font_size_combo.setCurrentText(f"{font.pointSizeF():g}")
            self._toolbar_font_size_combo.blockSignals(False)
            self._bold_btn.blockSignals(True)
            self._bold_btn.setChecked(font.bold())
            self._bold_btn.blockSignals(False)
            self._italic_btn.blockSignals(True)
            self._italic_btn.setChecked(font.italic())
            self._italic_btn.blockSignals(False)
            self._underline_btn.blockSignals(True)
            self._underline_btn.setChecked(font.underline())
            self._underline_btn.blockSignals(False)

        # Line width control
        self._line_width_spin.setEnabled(sel_line is not None)
        if sel_line is not None:
            self._line_width_spin.blockSignals(True)
            self._line_width_spin.setValue(sel_line.pen().widthF())
            self._line_width_spin.blockSignals(False)
        self._line_width_label.setVisible(sel_line is not None)
        self._line_width_spin.setVisible(sel_line is not None)
        # The row stays visible at a fixed height so selection changes never
        # move or resize the canvas below it.
        self._update_roi_step_visibility()

    def _on_save_template(self) -> bool:
        if self._project is None:
            QMessageBox.warning(
                self, "No Figure",
                "Complete Step 1: Choose Layout before saving a Figure Template.",
            )
            return False
        active_template_id = self._active_template_id
        active_template = None
        can_update_current = bool(
            active_template_id and not TemplateEngine.is_builtin(active_template_id)
        )
        if can_update_current:
            try:
                active_template = TemplateEngine.get_template(active_template_id)
            except KeyError:
                can_update_current = False

        choice = QMessageBox(self)
        choice.setWindowTitle("Save Figure Template")
        choice.setText("How would you like to save this Figure Template?")
        if can_update_current and active_template is not None:
            choice.setInformativeText(
                f'Choose whether to create a new Figure Template or update "{active_template.display_name}".'
            )
        else:
            choice.setInformativeText(
                "Update Current Figure Template is available after applying an item from Saved Templates."
            )
        new_btn = choice.addButton(
            "Save as New Figure Template",
            QMessageBox.ButtonRole.AcceptRole,
        )
        current_btn = choice.addButton(
            "Update Current Figure Template",
            QMessageBox.ButtonRole.ActionRole,
        )
        current_btn.setEnabled(can_update_current)
        choice.addButton(QMessageBox.StandardButton.Cancel)
        self._retranslate_widget_tree(choice)
        choice.exec()

        clicked = choice.clickedButton()
        if clicked is current_btn and active_template_id:
            return self._save_to_current_template(active_template_id)
        if clicked is new_btn:
            return self._save_as_new_template()
        return False

    def _save_as_new_template(self) -> bool:
        name, ok = QInputDialog.getText(
            self, "Save Figure Template", "Figure Template name:",
        )
        if not ok or not name.strip():
            return False
        name = name.strip()
        try:
            overlay_data = self._canvas.overlay_items_as_json_data()
            canvas_state = self._canvas.state_snapshot()
            TemplateEngine.save_user_template(
                name,
                self._project,
                overlay_data,
                canvas_state=canvas_state,
                text_style_overrides=self._text_style_overrides,
            )
            self._populate_template_list()
            QMessageBox.information(
                self, "Figure Template Saved",
                f'Figure Template \u201c{name}\u201d was added to Saved Templates.',
            )
            return True
        except Exception as exc:
            QMessageBox.critical(self, "Save Figure Template", f"Failed:\n{exc}")
            return False

    def _save_to_current_template(self, template_id: str) -> bool:
        try:
            tmpl = TemplateEngine.get_template(template_id)
            overlay_data = self._canvas.overlay_items_as_json_data()
            canvas_state = self._canvas.state_snapshot()
            TemplateEngine.update_user_template(
                template_id,
                self._project,
                overlay_data,
                canvas_state=canvas_state,
                text_style_overrides=self._text_style_overrides,
            )
            self._populate_template_list()
            self._select_template_id(template_id)
            QMessageBox.information(
                self, "Figure Template Saved",
                f'Figure Template \u201c{tmpl.display_name}\u201d updated.',
            )
            return True
        except Exception as exc:
            QMessageBox.critical(self, "Save Figure Template", f"Failed:\n{exc}")
            return False

    def _on_save_blot_file(self) -> bool:
        if self._project is None:
            QMessageBox.warning(
                self,
                "No Figure",
                "Complete Step 1: Choose Layout before saving a Blot File.",
            )
            return False

        active_blot_file_id = self._active_blot_file_id
        active_name = None
        can_update_current = bool(active_blot_file_id and self._blot_file_path(active_blot_file_id).exists())
        if can_update_current and active_blot_file_id:
            try:
                data = json.loads(self._blot_file_path(active_blot_file_id).read_text(encoding="utf-8"))
                active_name = str(data.get("name") or active_blot_file_id)
            except Exception:
                can_update_current = False

        choice = QMessageBox(self)
        choice.setWindowTitle("Save Blot File")
        choice.setText("How would you like to save this Blot File?")
        if can_update_current and active_name:
            choice.setInformativeText(
                f'Choose whether to create a new Blot File or update "{active_name}".'
            )
        else:
            choice.setInformativeText(
                "Update Current Blot File is available after opening a saved Blot File."
            )
        new_btn = choice.addButton(
            "Save as New Blot File",
            QMessageBox.ButtonRole.AcceptRole,
        )
        current_btn = choice.addButton(
            "Update Current Blot File",
            QMessageBox.ButtonRole.ActionRole,
        )
        current_btn.setEnabled(can_update_current)
        choice.addButton(QMessageBox.StandardButton.Cancel)
        self._retranslate_widget_tree(choice)
        choice.exec()

        clicked = choice.clickedButton()
        if clicked is current_btn and active_blot_file_id:
            return self._save_to_current_blot_file(active_blot_file_id)
        if clicked is new_btn:
            return self._save_as_new_blot_file()
        return False

    def _save_as_new_blot_file(self) -> bool:
        name, ok = QInputDialog.getText(
            self,
            "Save Blot File",
            "Blot File name:",
        )
        if not ok or not name.strip():
            return False
        name = name.strip()
        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "blot_file"
        blot_file_id = f"blot_{slug}_{int(time.time())}"
        try:
            self._write_blot_file(blot_file_id, name)
            self._active_blot_file_id = blot_file_id
            self._populate_blot_file_list()
            self._select_blot_file_id(blot_file_id)
            QMessageBox.information(
                self,
                "Blot File Saved",
                f'Blot file "{name}" saved and added to the Saved Blot Files section.',
            )
            return True
        except Exception as exc:
            QMessageBox.critical(self, "Save Blot File", f"Failed:\n{exc}")
            return False

    def _save_to_current_blot_file(self, blot_file_id: str) -> bool:
        try:
            path = self._blot_file_path(blot_file_id)
            data = json.loads(path.read_text(encoding="utf-8"))
            name = str(data.get("name") or blot_file_id)
            self._write_blot_file(blot_file_id, name, created=str(data.get("created", time.strftime("%Y-%m-%d"))))
            self._populate_blot_file_list()
            self._select_blot_file_id(blot_file_id)
            QMessageBox.information(
                self,
                "Blot File Saved",
                f'Blot file "{name}" updated.',
            )
            return True
        except Exception as exc:
            QMessageBox.critical(self, "Save Blot File", f"Failed:\n{exc}")
            return False

    def _write_blot_file(
        self,
        blot_file_id: str,
        name: str,
        *,
        created: str | None = None,
    ) -> None:
        if self._project is None:
            raise ValueError("No project to save.")
        USER_BLOT_FILES_DIR.mkdir(parents=True, exist_ok=True)
        project_data = asdict(self._project)
        canvas_state = self._canvas.state_snapshot()
        assets_dir, staging_dir = self._copy_blot_file_assets(
            blot_file_id,
            project_data,
            canvas_state,
        )
        payload = {
            "format": "wb_blot_file",
            "version": 2,
            "id": blot_file_id,
            "name": name,
            "created": created or time.strftime("%Y-%m-%d"),
            "active_template_id": self._active_template_id,
            "active_table_style": self._active_table_style,
            "project": project_data,
            "canvas_state": canvas_state,
            "text_style_overrides": TemplateEngine._encode_text_style_overrides(
                self._text_style_overrides
            ),
        }
        payload_text = json.dumps(payload, indent=2)
        target_path = self._blot_file_path(blot_file_id)
        backup_dir = staging_dir.with_name(f"{staging_dir.name}-previous")
        fd, staged_json_value = tempfile.mkstemp(
            prefix=f".{blot_file_id}-",
            suffix=".json",
            dir=str(USER_BLOT_FILES_DIR),
        )
        os.close(fd)
        staged_json = Path(staged_json_value)
        try:
            staged_json.write_text(payload_text, encoding="utf-8")
            json.loads(staged_json.read_text(encoding="utf-8"))
            if assets_dir.exists():
                os.replace(assets_dir, backup_dir)
            try:
                os.replace(staging_dir, assets_dir)
                os.replace(staged_json, target_path)
            except Exception:
                if assets_dir.exists():
                    shutil.rmtree(assets_dir)
                if backup_dir.exists():
                    os.replace(backup_dir, assets_dir)
                raise
            if backup_dir.exists():
                shutil.rmtree(backup_dir, ignore_errors=True)
            if self._active_blot_file_id == blot_file_id:
                self._rebase_live_blot_asset_paths(project_data, canvas_state)
        finally:
            if staged_json.exists():
                staged_json.unlink()
            if staging_dir.exists():
                shutil.rmtree(staging_dir)
            if backup_dir.exists() and not assets_dir.exists():
                os.replace(backup_dir, assets_dir)

    def _copy_blot_file_assets(
        self,
        blot_file_id: str,
        project_data: dict,
        canvas_state: dict,
    ) -> tuple[Path, Path]:
        assets_dir = USER_BLOT_FILES_DIR / f"{blot_file_id}_assets"
        USER_BLOT_FILES_DIR.mkdir(parents=True, exist_ok=True)
        # Loaded blot files read their images from assets_dir itself. Copy every
        # source into an isolated directory before replacing any live image_N;
        # otherwise renumbering frames can overwrite a source not yet copied.
        staging_dir = Path(tempfile.mkdtemp(
            prefix=f".{blot_file_id}_assets-",
            dir=str(USER_BLOT_FILES_DIR),
        ))
        copied: dict[str, str] = {}

        def copy_path(path_value) -> str:
            if not path_value:
                return ""
            source = Path(str(path_value)).expanduser()
            if not source.exists() or not source.is_file():
                raise FileNotFoundError(
                    f"Cannot save the Blot File because a source image is missing: {source}"
                )
            source_key = str(source.resolve())
            if source_key in copied:
                return copied[source_key]
            suffix = source.suffix or ".img"
            filename = f"image_{len(copied) + 1}{suffix}"
            staged_asset_path = staging_dir / filename
            final_asset_path = assets_dir / filename
            shutil.copy2(source, staged_asset_path)
            copied[source_key] = str(final_asset_path)
            return str(final_asset_path)

        try:
            for panel_idx, panel in enumerate(project_data.get("panels", [])):
                for slot_idx, blot in enumerate(panel.get("blot_slots", [])):
                    had_visible_source = bool(
                        blot.get("source_image_path")
                        or blot.get("saved_preview_path")
                    )
                    blot["source_image_path"] = copy_path(
                        blot.get("source_image_path")
                    )
                    preview = self._canvas.blot_preview_image((
                        panel_idx,
                        slot_idx,
                        None,
                        None,
                        None,
                        "blot",
                    ))
                    if preview is not None and not preview.isNull():
                        preview_name = (
                            f"preview_panel_{panel_idx + 1}_blot_{slot_idx + 1}.png"
                        )
                        staged_preview = staging_dir / preview_name
                        if not preview.save(str(staged_preview), "PNG"):
                            raise RuntimeError(
                                "Cannot save the lossless preview for "
                                f"panel {panel_idx + 1}, blot {slot_idx + 1}."
                            )
                        blot["saved_preview_path"] = str(
                            assets_dir / preview_name
                        )
                    elif had_visible_source:
                        raise RuntimeError(
                            "Cannot save the Blot File because the visible "
                            f"pixels for panel {panel_idx + 1}, blot "
                            f"{slot_idx + 1} could not be captured."
                        )
                    else:
                        blot["saved_preview_path"] = copy_path(
                            blot.get("saved_preview_path")
                        )

            saved_overlay_blots = [
                item
                for item in canvas_state.get("overlay_items", [])
                if item.get("type") == "blot"
            ]
            live_overlay_blots = [
                item
                for item in self._canvas._overlay_items
                if getattr(item, "TypeName", "") == "blot"
            ]
            for overlay_idx, item in enumerate(saved_overlay_blots):
                had_visible_source = bool(item.get("image_path"))
                original_path = copy_path(item.get("image_path"))
                live_item = (
                    live_overlay_blots[overlay_idx]
                    if overlay_idx < len(live_overlay_blots)
                    else None
                )
                preview = (
                    live_item.preview_image()
                    if live_item is not None
                    and hasattr(live_item, "preview_image")
                    else None
                )
                if preview is not None and not preview.isNull():
                    preview_name = f"preview_overlay_{overlay_idx + 1}.png"
                    if not preview.save(str(staging_dir / preview_name), "PNG"):
                        raise RuntimeError(
                            "Cannot save the lossless preview for added blot "
                            f"frame {overlay_idx + 1}."
                        )
                    item["original_image_path"] = original_path
                    item["image_path"] = str(assets_dir / preview_name)
                    item["roi"] = {}
                    item["transform"] = {
                        "low": 0,
                        "high": 65535,
                        "gamma": 1.0,
                        "inverted": False,
                    }
                elif had_visible_source:
                    raise RuntimeError(
                        "Cannot save the Blot File because the visible pixels "
                        f"for added blot frame {overlay_idx + 1} could not be "
                        "captured."
                    )
                else:
                    item["image_path"] = original_path
        except Exception:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise

        # The caller commits the staged directory together with the JSON file.
        # This keeps both halves of a blot file on the same saved version.
        return assets_dir, staging_dir

    def _rebase_live_blot_asset_paths(
        self,
        project_data: dict,
        canvas_state: dict,
    ) -> None:
        """Keep an opened blot file bound to its newly committed assets."""
        if self._project is not None:
            for live_panel, saved_panel in zip(
                self._project.panels,
                project_data.get("panels", []),
            ):
                for live_slot, saved_slot in zip(
                    live_panel.blot_slots,
                    saved_panel.get("blot_slots", []),
                ):
                    live_slot.source_image_path = str(
                        saved_slot.get("source_image_path", "")
                    )
                    live_slot.saved_preview_path = str(
                        saved_slot.get("saved_preview_path", "")
                    )

        saved_overlay_blots = [
            item
            for item in canvas_state.get("overlay_items", [])
            if item.get("type") == "blot"
        ]
        live_overlay_blots = [
            item
            for item in self._canvas._overlay_items
            if getattr(item, "TypeName", "") == "blot"
        ]
        for live_item, saved_item in zip(live_overlay_blots, saved_overlay_blots):
            live_item.image_path = saved_item.get("image_path")
            live_item.roi = dict(saved_item.get("roi") or {})
            live_item.transform = dict(saved_item.get("transform") or {})

    def _on_reset_figure_generation(self) -> None:
        self._project = None
        self._layout_result = None
        self._active_slot_ref = None
        self._active_template_id = None
        self._active_blot_file_id = None
        self._active_table_style = "none"
        self._text_style_overrides.clear()
        self._canvas_undo_stack.clear()
        self._canvas_undo_queued = False
        self._restoring_canvas_undo = False
        self._roi_fill_mode = "auto"
        self._auto_fit_review_pending = False
        self._auto_detect_radio.setChecked(True)
        self._auto_disclosure.setVisible(True)
        self._auto_disclosure.set_expanded(False)
        self._fixed_roi_disclosure.setVisible(False)
        self._fixed_roi_disclosure.set_expanded(False)
        if self._auto_fit_overlay_handler is not None:
            self._auto_fit_overlay_handler(None)
        self._canvas.clear_all()

        self._fixed_roi_sizes.clear()
        self._fixed_roi_list.clear()
        if self._fixed_roi_cancel_requested is not None:
            self._fixed_roi_cancel_requested()

        for spin, value in (
            (self._panels_spin, 1),
            (self._blots_spin, 2),
            (self._lanes_spin, 4),
        ):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
        if self._template_list.count():
            self._template_list.setCurrentRow(0)

        self._rebuild_step4()
        self._annot_undo_btn.setEnabled(False)
        self._on_canvas_selection_changed()

    # ── Structural actions ────────────────────────────────────────────────

    def _confirm_save_current_template_before_apply(self) -> bool:
        if self._project is None or self._layout_result is None:
            return True
        reply = QMessageBox.question(
            self,
            "Save Current Figure Template?",
            "Do you want to save the current Figure Template before applying a new layout?",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Cancel:
            return False
        if reply == QMessageBox.StandardButton.Yes:
            return self._on_save_template()
        return True

    def _on_apply_template(self) -> bool:
        tid, table_style = self._current_template_selection()
        if not self._confirm_save_current_template_before_apply():
            return False
        tmpl = TemplateEngine.get_template(tid)
        self._active_template_id = tid
        self._active_table_style = table_style
        self._text_style_overrides.clear()
        self._canvas._hidden_text_keys.clear()

        if not TemplateEngine.is_builtin(tid):
            # User template: restore saved project + overlay items
            try:
                project, overlay_data = TemplateEngine.restore_user_project(tid)
                format_state = TemplateEngine.restore_user_template_format_state(tid)
            except Exception as exc:
                QMessageBox.critical(
                    self,
                    "Load Figure Template",
                    f"Failed to load the Figure Template:\n{exc}",
                )
                return False
            self._project = project
            self._text_style_overrides = dict(
                format_state.get("text_style_overrides", {})
            )
            self._active_table_style = "vector_matrix" if project.global_layout.show_condition_table else "none"
            for spin, val in [
                (self._panels_spin, len(project.panels)),
                (self._blots_spin,  len(project.panels[0].blot_slots) if project.panels else 1),
                (self._lanes_spin,  project.panels[0].blot_slots[0].lane_count
                 if project.panels and project.panels[0].blot_slots else 4),
            ]:
                spin.blockSignals(True)
                spin.setValue(val)
                spin.blockSignals(False)
            canvas_state = format_state.get("canvas_state") or {}
            if canvas_state:
                self._canvas.restore_state_snapshot(canvas_state, repopulate_scene=False)
            elif overlay_data:
                self._canvas._restore_overlay_from_data(overlay_data)
            self._rebuild_step4()
            self._recompute_and_refresh()
            return True

        # Built-in template
        for spin, val in [
            (self._panels_spin, tmpl.default_panel_count),
            (self._blots_spin,  tmpl.default_blot_count),
            (self._lanes_spin,  tmpl.default_lane_count),
        ]:
            spin.blockSignals(True)
            spin.setValue(val)
            spin.blockSignals(False)

        self._project = TemplateEngine.build_project(tid)
        for panel in self._project.panels:
            panel.condition_table = TemplateEngine.make_condition_table(
                table_style,
                self._lanes_spin.value(),
            )
        self._project.global_layout.show_condition_table = table_style != "none"

        self._rebuild_step4()
        self._recompute_and_refresh()
        return True

    def _on_apply_structure(self) -> None:
        if not self._confirm_save_current_template_before_apply():
            return
        n_panels = self._panels_spin.value()
        n_blots  = self._blots_spin.value()
        n_lanes  = self._lanes_spin.value()
        old_panel_count = len(self._project.panels) if self._project is not None else 0
        old_layout = self._layout_engine.compute(self._project) if self._project is not None else None
        old_overlay_data = self._canvas.overlay_items_as_json_data()

        if self._project is None:
            tid, table_style = self._current_template_selection()
            self._active_template_id = tid
            self._active_table_style = table_style
            self._project = TemplateEngine.build_project(tid, n_panels, n_blots, n_lanes)
        else:
            table_style = self._active_table_style

        self._resize_current_project(n_panels, n_blots, n_lanes, table_style)
        self._apply_created_frame_defaults()
        if n_panels > old_panel_count and old_overlay_data and old_layout is not None:
            self._duplicate_first_panel_overlays_for_new_panels(
                old_overlay_data,
                old_layout,
                old_panel_count,
                n_panels,
            )
        self._rebuild_step4()
        self._recompute_and_refresh(fit_view=False)
        self._canvas.fit_frame_content_to_view()

    def _apply_created_frame_defaults(self) -> None:
        """Apply the labeling rules specific to a newly created frame."""
        if self._project is None:
            return
        layout = self._project.global_layout
        layout.show_mw_labels = False
        layout.panel_layout = (
            "horizontal" if len(self._project.panels) > 1 else "vertical"
        )
        layout.share_ib_labels = len(self._project.panels) > 1
        for panel in self._project.panels:
            for slot_index, slot in enumerate(panel.blot_slots):
                slot.label = f"IB: Protein {slot_index + 1}"
                slot.mw_marker = ""

    @staticmethod
    def _panel_anchor_scene_y(layout: LayoutResult, panel_idx: int) -> float | None:
        anchor = FigureModeWindow._panel_anchor_scene_point(layout, panel_idx)
        return anchor[1] if anchor is not None else None

    @staticmethod
    def _panel_anchor_scene_point(
        layout: LayoutResult,
        panel_idx: int,
    ) -> tuple[float, float] | None:
        panel_items = [
            item
            for item in layout.items
            if (
                item.kind == "blot"
                and item.source_ref is not None
                and item.source_ref.panel_idx == panel_idx
            )
        ]
        if not panel_items:
            panel_items = [
                item
                for item in layout.items
                if (
                    item.source_ref is not None
                    and item.source_ref.panel_idx == panel_idx
                )
            ]
        if not panel_items:
            return None
        return (
            pt_to_scene(min(item.x_pt for item in panel_items)),
            pt_to_scene(min(item.y_pt for item in panel_items)),
        )

    @staticmethod
    def _overlay_item_center_x(item_data: dict) -> float:
        x = float(item_data.get("x", 0.0))
        if item_data.get("type") == "line":
            return x + (
                float(item_data.get("x1", 0.0))
                + float(item_data.get("x2", 0.0))
            ) / 2.0
        return x + float(item_data.get("width", 0.0)) / 2.0

    @staticmethod
    def _overlay_item_center_y(item_data: dict) -> float:
        y = float(item_data.get("y", 0.0))
        if item_data.get("type") == "line":
            return y + (
                float(item_data.get("y1", 0.0))
                + float(item_data.get("y2", 0.0))
            ) / 2.0
        return y + float(item_data.get("height", 0.0)) / 2.0

    @classmethod
    def _first_panel_overlay_data(
        cls,
        overlay_data: list[dict],
        old_layout: LayoutResult,
        old_panel_count: int,
    ) -> list[dict]:
        if old_panel_count <= 1:
            return [dict(item) for item in overlay_data]

        first_anchor = cls._panel_anchor_scene_point(old_layout, 0)
        second_anchor = cls._panel_anchor_scene_point(old_layout, 1)
        if first_anchor is None or second_anchor is None:
            return [dict(item) for item in overlay_data]
        delta_x = abs(second_anchor[0] - first_anchor[0])
        delta_y = abs(second_anchor[1] - first_anchor[1])
        horizontal = delta_x > delta_y
        upper = (
            (first_anchor[0] + second_anchor[0]) / 2.0
            if horizontal
            else (first_anchor[1] + second_anchor[1]) / 2.0
        )
        return [
            dict(item)
            for item in overlay_data
            if (
                cls._overlay_item_center_x(item)
                if horizontal
                else cls._overlay_item_center_y(item)
            ) < upper
        ]

    @staticmethod
    def _shift_overlay_item(
        item_data: dict,
        delta_x: float,
        delta_y: float,
    ) -> dict:
        shifted = dict(item_data)
        shifted["x"] = float(shifted.get("x", 0.0)) + delta_x
        shifted["y"] = float(shifted.get("y", 0.0)) + delta_y
        return shifted

    def _duplicate_first_panel_overlays_for_new_panels(
        self,
        old_overlay_data: list[dict],
        old_layout: LayoutResult,
        old_panel_count: int,
        new_panel_count: int,
    ) -> None:
        if self._project is None:
            return
        first_panel_overlays = self._first_panel_overlay_data(
            old_overlay_data,
            old_layout,
            old_panel_count,
        )
        if not first_panel_overlays:
            return

        old_first_anchor = self._panel_anchor_scene_point(old_layout, 0)
        if old_first_anchor is None:
            return
        new_layout = self._layout_engine.compute(self._project)
        combined = [dict(item) for item in old_overlay_data]
        for panel_idx in range(old_panel_count, new_panel_count):
            target_anchor = self._panel_anchor_scene_point(
                new_layout,
                panel_idx,
            )
            if target_anchor is None:
                continue
            delta_x = target_anchor[0] - old_first_anchor[0]
            delta_y = target_anchor[1] - old_first_anchor[1]
            combined.extend(
                self._shift_overlay_item(item, delta_x, delta_y)
                for item in first_panel_overlays
            )
        self._canvas._restore_overlay_from_data(combined)

    def _resize_current_project(
        self,
        n_panels: int,
        n_blots: int,
        n_lanes: int,
        table_style: str,
    ) -> None:
        if self._project is None:
            return
        template_id = self._active_template_id or self._project.template_type or "normal_wb"
        skeleton_id = template_id if TemplateEngine.is_builtin(template_id) else "normal_wb"
        skeleton = TemplateEngine.build_project(skeleton_id, n_panels, n_blots, n_lanes)
        old_panels = list(self._project.panels)
        resized_panels: list[Panel] = []

        for pi in range(n_panels):
            if pi < len(old_panels):
                panel = old_panels[pi]
            else:
                blueprint = old_panels[0] if old_panels else skeleton.panels[pi]
                panel = self._clone_panel_structure(
                    blueprint,
                    skeleton.panels[pi],
                    n_blots,
                    n_lanes,
                    table_style,
                )
            if not panel.panel_letter:
                panel.panel_letter = skeleton.panels[pi].panel_letter

            old_slots = list(panel.blot_slots)
            resized_slots: list[BlotSlot] = []
            for si in range(n_blots):
                if si < len(old_slots):
                    slot = old_slots[si]
                else:
                    slot = skeleton.panels[pi].blot_slots[si]
                if slot.lane_count != max(1, n_lanes):
                    slot.lane_crops = []
                slot.lane_count = max(1, n_lanes)
                if slot.bounding_box is not None or slot.lane_rois:
                    slot.reset_equal_lanes()
                resized_slots.append(slot)
            panel.blot_slots = resized_slots
            panel.condition_table = self._resize_condition_table(
                panel.condition_table,
                n_lanes,
                table_style,
            )
            resized_panels.append(panel)

        self._project.panels = resized_panels
        self._project.global_layout.show_condition_table = table_style != "none"

    def _clone_panel_structure(
        self,
        source_panel: Panel,
        skeleton_panel: Panel,
        n_blots: int,
        n_lanes: int,
        table_style: str,
    ) -> Panel:
        panel = copy.deepcopy(source_panel)
        panel.panel_letter = skeleton_panel.panel_letter
        source_slots = list(panel.blot_slots)
        cloned_slots: list[BlotSlot] = []
        for si in range(n_blots):
            if si < len(source_slots):
                slot = source_slots[si]
            elif si < len(skeleton_panel.blot_slots):
                slot = copy.deepcopy(skeleton_panel.blot_slots[si])
            else:
                slot = copy.deepcopy(source_slots[-1]) if source_slots else copy.deepcopy(skeleton_panel.blot_slots[-1])
            slot.source_image_path = ""
            slot.bounding_box = None
            slot.image_transform = None
            slot.geometry_transform = None
            slot.lane_crops = []
            slot.preserve_image_aspect = False
            slot.saved_preview_path = ""
            slot.lane_count = max(1, n_lanes)
            slot.reset_equal_lanes()
            cloned_slots.append(slot)
        panel.blot_slots = cloned_slots
        panel.condition_table = self._resize_condition_table(
            copy.deepcopy(source_panel.condition_table),
            n_lanes,
            table_style,
        )
        return panel

    def _resize_condition_table(
        self,
        table: ConditionTable | None,
        n_lanes: int,
        table_style: str,
    ) -> ConditionTable | None:
        if table_style == "none":
            return None
        if table is None:
            return TemplateEngine.make_condition_table(table_style, n_lanes)

        headers = (list(table.headers) + [f"Lane {i + 1}" for i in range(len(table.headers), n_lanes)])[:n_lanes]
        rows: list[list[str]] = []
        for row in table.rows:
            if row and row[0] == "__span__":
                rows.append(list(row[:2]))
                continue
            if row and row[0] in {
                "__cross_groups__",
                "__cross_groups_level__",
                "__cross_group_space__",
            }:
                rows.append(list(row))
                continue
            if row and row[0] in {"__groups__", "__groups_level__"}:
                group_data_start = 2 if row[0] == "__groups_level__" else 1
                resized_group_row = list(row[:group_data_start])
                for data_index in range(group_data_start, len(row) - 1, 2):
                    try:
                        start_text, end_text = row[data_index].split("-", 1)
                        start = max(1, min(n_lanes, int(start_text)))
                        end = max(start, min(n_lanes, int(end_text)))
                    except (AttributeError, TypeError, ValueError):
                        continue
                    resized_group_row.extend([
                        f"{start}-{end}",
                        row[data_index + 1],
                    ])
                rows.append(resized_group_row)
                continue
            label = row[0] if row else ""
            values = list(row[1:])
            values = (values + [""] * n_lanes)[:n_lanes]
            rows.append([label] + values)
        return ConditionTable(headers=headers, rows=rows)

    # ── Live re-render ────────────────────────────────────────────────────

    def _recompute_and_refresh(self, *, fit_view: bool = True) -> None:
        if self._project is None:
            return
        self._layout_result = self._layout_engine.compute(self._project)
        self._apply_text_style_overrides_to_layout()
        hidden = self._canvas.hidden_text_keys()
        if hidden:
            self._layout_result.items = [
                item for item in self._layout_result.items
                if item.source_ref is None or item.source_ref.key() not in hidden
            ]
        self._canvas.render(self._layout_result, self._project)
        if fit_view:
            self._canvas.fit_frame_content_to_view()

    def _apply_text_style_overrides_to_layout(self) -> None:
        if self._layout_result is None:
            return
        self._apply_text_styles_to_layout(
            self._layout_result,
            self._text_style_overrides,
        )

    @staticmethod
    def _apply_text_styles_to_layout(
        layout: LayoutResult,
        styles: dict[tuple, dict],
    ) -> None:
        for item in layout.items:
            if item.source_ref is None:
                continue
            style = styles.get(item.source_ref.key())
            if not style:
                continue
            if "font_family" in style:
                item.font_family = str(style["font_family"])
            if "font_size_pt" in style:
                item.font_size_pt = float(style["font_size_pt"])
            if "bold" in style:
                item.bold = bool(style["bold"])
            if "italic" in style:
                item.italic = bool(style["italic"])
            if "underline" in style:
                item.underline = bool(style["underline"])
            if "align" in style:
                item.align = str(style["align"])
            if "rotation" in style:
                item.rotation = float(style["rotation"])

    def zoom_in(self) -> None:
        self._canvas.zoom_in()

    def zoom_out(self) -> None:
        self._canvas.zoom_out()

    def reset_zoom(self) -> None:
        self._canvas.reset_zoom()

    def _sync_existing_slot_lanes(self, lane_count: int) -> None:
        if self._project is None:
            return
        for panel in self._project.panels:
            panel.condition_table = TemplateEngine.make_condition_table(
                self._active_table_style,
                lane_count,
            )
            for slot in panel.blot_slots:
                if slot.bounding_box is None:
                    continue
                slot.lane_count = max(1, lane_count)
                slot.reset_equal_lanes()

    # ── Canvas text edit callback ─────────────────────────────────────────

    def _on_canvas_text_edited(self, ref: SourceRef, new_text: str) -> None:
        if self._project is not None:
            self._project.apply_edit(ref, new_text)

    def _on_canvas_text_rotation_changed(self, styles: dict[tuple, dict]) -> None:
        """Persist a completed on-canvas rotation for built-in text items."""
        if not styles:
            return
        self._text_style_overrides.update(styles)
        self._apply_text_style_overrides_to_layout()

    def _on_canvas_blot_resized(
        self,
        refs: list[SourceRef],
        width_pt: float,
        height_pt: float,
    ) -> None:
        if self._project is None:
            return
        width_pt = max(40.0, min(520.0, float(width_pt)))
        height_pt = max(10.0, min(160.0, float(height_pt)))
        for ref in refs:
            if ref.panel_idx is None or ref.slot_idx is None:
                continue
            slot = self._project.get_slot(ref.panel_idx, ref.slot_idx)
            if slot is None:
                continue
            slot.display_width_pt = width_pt
            slot.display_height_pt = height_pt
        self._recompute_and_refresh()

    # ── Export actions ────────────────────────────────────────────────────

    def _layout_with_annotations(self) -> LayoutResult:
        """Return LayoutResult with overlay annotations appended for export."""
        base = self._layout_result
        self._apply_text_style_overrides_to_layout()
        hidden = self._canvas.hidden_text_keys()
        base_items = [
            item for item in base.items
            if item.source_ref is None or item.source_ref.key() not in hidden
        ]
        base_items = self._canvas.adjusted_layout_items_for_export(base_items)
        extra = self._canvas.overlay_as_layout_items()
        if not extra:
            return LayoutResult(
                items=base_items,
                canvas_width_pt=base.canvas_width_pt,
                canvas_height_pt=base.canvas_height_pt,
            )
        return LayoutResult(
            items=base_items + extra,
            canvas_width_pt=base.canvas_width_pt,
            canvas_height_pt=base.canvas_height_pt,
        )

    def _on_export_pdf(self) -> None:
        if not self._check_export_ready():
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export PDF", str(Path.home() / "wb_figure.pdf"),
            "PDF (*.pdf)"
        )
        if not path:
            return
        try:
            layout = self._layout_with_annotations()
            snapshot = self._canvas.render_page_image(scale=4.0)
            PDFExporter().export_image(
                snapshot,
                layout.canvas_width_pt,
                layout.canvas_height_pt,
                path,
            )
            QMessageBox.information(self, "Export PDF", f"Saved to:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export PDF", f"Export failed:\n{exc}")

    def _on_export_tiff(self) -> None:
        if not self._check_export_ready():
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export TIFF",
            str(Path.home() / "wb_figure.tiff"),
            "TIFF Image (*.tif *.tiff)",
        )
        if not path:
            return
        if Path(path).suffix.lower() not in {".tif", ".tiff"}:
            path = f"{path}.tiff"
        try:
            snapshot = self._canvas.render_page_image(
                scale=TIFFExporter.render_scale_for_dpi(),
                include_overflow=True,
            )
            TIFFExporter().export_image(snapshot, path)
            QMessageBox.information(self, "Export TIFF", f"Saved to:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export TIFF", f"Export failed:\n{exc}")

    def _on_export_pptx(self) -> None:
        if not PPTX_AVAILABLE:
            QMessageBox.warning(
                self,
                "PPTX Unavailable",
                "python-pptx is not installed.\n\nRun:  pip install python-pptx",
            )
            return
        if not self._check_export_ready():
            return

        # Ask user how to save
        dlg = QDialog(self)
        dlg.setWindowTitle("Export PPTX")
        dlg.setMinimumWidth(340)
        vl = QVBoxLayout(dlg)
        vl.setSpacing(12)
        vl.addWidget(QLabel("How would you like to save the slide?"))
        btn_new = QPushButton("Save as a new file")
        btn_new.setStyleSheet("QPushButton { padding: 8px 16px; text-align: left; }")
        btn_append = QPushButton("Save as a slide in an existing file")
        btn_append.setStyleSheet("QPushButton { padding: 8px 16px; text-align: left; }")
        btn_cancel = QPushButton("Cancel")
        vl.addWidget(btn_new)
        vl.addWidget(btn_append)
        vl.addWidget(btn_cancel)
        choice = [None]

        def _pick(val):
            choice[0] = val
            dlg.accept()

        btn_new.clicked.connect(lambda: _pick("new"))
        btn_append.clicked.connect(lambda: _pick("append"))
        btn_cancel.clicked.connect(dlg.reject)
        self._retranslate_widget_tree(dlg)
        if dlg.exec() != QDialog.DialogCode.Accepted or choice[0] is None:
            return

        layout = self._layout_with_annotations()
        if choice[0] == "new":
            path, _ = QFileDialog.getSaveFileName(
                self, "Export PPTX", str(Path.home() / "wb_figure.pptx"),
                "PowerPoint (*.pptx)"
            )
            if not path:
                return
            try:
                PPTXExporter().export(layout, path)
                QMessageBox.information(self, "Export PPTX", f"Saved to:\n{path}")
            except Exception as exc:
                QMessageBox.critical(self, "Export PPTX", f"Export failed:\n{exc}")
        else:
            existing, _ = QFileDialog.getOpenFileName(
                self, "Select Existing PPTX", str(Path.home()),
                "PowerPoint (*.pptx)"
            )
            if not existing:
                return
            try:
                PPTXExporter().export_append_slide(layout, existing)
                QMessageBox.information(self, "Export PPTX", f"Slide appended to:\n{existing}")
            except Exception as exc:
                QMessageBox.critical(self, "Export PPTX", f"Export failed:\n{exc}")

    def _check_export_ready(self) -> bool:
        if self._layout_result is None or not self._layout_result.items:
            QMessageBox.warning(
                self, "Nothing to Export",
                "Complete Step 1: Choose Layout before exporting the Figure.",
            )
            return False
        return True

    # ── Helpers ───────────────────────────────────────────────────────────

    def _get_slot(self, pi: int, si: int) -> BlotSlot | None:
        if self._project is None:
            return None
        return self._project.get_slot(pi, si)

# ── Shared button stylesheets ─────────────────────────────────────────────────

_APPLY_BTN_STYLE = (
    "QPushButton {"
    "  background-color: #B8D4C2;"
    "  border: 1px solid #8FB5A0;"
    "  border-radius: 6px;"
    "  color: #1E3D2F;"
    "  padding: 4px 10px;"
    "  font-size: 11px;"
    "  font-weight: 600;"
    "}"
    "QPushButton:hover { background-color: #A8C8B4; }"
)

_SMALL_BTN_STYLE = (
    "QPushButton {"
    "  background-color: #DCE9E2;"
    "  border: 1px solid #B0C8BB;"
    "  border-radius: 5px;"
    "  color: #2C4A3D;"
    "  padding: 2px 8px;"
    "  font-size: 10px;"
    "}"
    "QPushButton:hover { background-color: #CCDFD5; }"
)

_HIGHLIGHT_BTN_STYLE = (
    "QPushButton {"
    "  background-color: #91BFA5;"
    "  border: 1px solid #6F9E82;"
    "  border-radius: 5px;"
    "  color: #173C2C;"
    "  padding: 2px 9px;"
    "  font-size: 10px;"
    "  font-weight: 600;"
    "}"
    "QPushButton:hover { background-color: #82B497; }"
    "QPushButton:pressed { background-color: #73A689; }"
)

_SECONDARY_EXPORT_BTN_STYLE = (
    "QPushButton {"
    "  background-color: #FFFFFF;"
    "  border: 1px solid #B8C9C0;"
    "  border-radius: 5px;"
    "  color: #385248;"
    "  padding: 2px 7px;"
    "  font-size: 9px;"
    "  font-weight: 500;"
    "}"
    "QPushButton:hover { background-color: #EEF5F1; border-color: #91AE9F; }"
    "QPushButton:disabled { background-color: #E0E8E4; color: #8BA098; }"
)
