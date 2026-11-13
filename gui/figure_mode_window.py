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

from PySide6.QtCore import Qt, QRectF, QSize, QSizeF, Signal
from PySide6.QtGui import (
    QAction, QKeySequence, QShortcut,
)
from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QDialogButtonBox, QFormLayout, QFrame, QGroupBox,
    QCheckBox, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QFileDialog,  # noqa: F401 - used by the mixins; kept importable from here
    QInputDialog, QListWidget, QListWidgetItem,
    QComboBox, QRadioButton, QScrollArea, QSizePolicy, QSpinBox, QDoubleSpinBox, QStackedWidget,
    QToolButton, QVBoxLayout, QWidget,
)

from config.paths import blot_files_dir
from core.export_engine import (
    PPTX_AVAILABLE,
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
    LayoutResult,
    scene_to_pt,
)
from core.template_engine import TemplateEngine
from gui.condition_template_dialog import (
    ConditionPreviewWidget as _ConditionPreviewWidget,
    ConditionTemplateDialogController,
    request_custom_lane_ranges,
)
from gui.figure_canvas import FigureCanvas
from utils.i18n import LANG_EN, LANG_ZH_CN, tr, tr_display
from gui.figure_mode_styles import (
    _APPLY_BTN_STYLE,
    _SECONDARY_EXPORT_BTN_STYLE,
    _SMALL_BTN_STYLE,
)
from gui.figure_mode_widgets import (
    _CollapseGroup,
    _FramePreviewWidget,
    _InlineDisclosure,
    _TemplateNameLabel,
)
from gui.figure_mode_templates import TemplateLibraryMixin
from gui.figure_mode_toolbar import CanvasToolbarMixin
from gui.figure_mode_structure import FigureStructureMixin
from gui.figure_mode_export import FigureExportMixin

_FIXED_ROI_NAME_ROLE = int(Qt.ItemDataRole.UserRole) + 1
USER_BLOT_FILES_DIR: Path = blot_files_dir()


# ── Collapsible group box ─────────────────────────────────────────────────────

# ── Main window widget ────────────────────────────────────────────────────────

class FigureModeWindow(FigureExportMixin, FigureStructureMixin, CanvasToolbarMixin, TemplateLibraryMixin, QWidget):
    """Container for the WB Plot Figure Generation mode."""

    workflowEvent = Signal(str)

    # The workflow panel needs enough room for the two-column detection and
    # template controls without clipping.  It remains substantially narrower
    # than the source-image and figure-canvas workspaces around it.
    _SIDEBAR_WIDTH = 304

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
        self._current_step = 1

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
        self._refresh_step_panel_texts()

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
        root.setSpacing(8)

        # ── Centre: slim canvas bar + figure canvas ───────────────────────
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

        canvas_card = QFrame()
        canvas_card.setObjectName("figureCanvasCard")
        canvas_card.setStyleSheet(
            "QFrame#figureCanvasCard { background: #FFFFFF; "
            "border: 1px solid #D9E0DC; border-radius: 10px; }"
        )
        canvas_vbox = QVBoxLayout(canvas_card)
        canvas_vbox.setContentsMargins(1, 1, 1, 1)
        canvas_vbox.setSpacing(0)
        # The bar carries the annotation tools; building it also creates
        # the save panel that is placed on the last step page below.
        canvas_vbox.addWidget(self._build_canvas_toolbar())
        canvas_vbox.addWidget(canvas_scroll, 1)
        root.addWidget(canvas_card, 1)

        # ── Right: fixed-width step panel ─────────────────────────────────
        panel = QFrame()
        panel.setObjectName("workflowStepPanel")
        panel.setFixedWidth(self._SIDEBAR_WIDTH)
        panel.setStyleSheet(
            "QFrame#workflowStepPanel { background: #FFFFFF; "
            "border: 1px solid #D9E0DC; border-radius: 10px; }"
        )
        self._step_panel = panel
        panel_outer = QVBoxLayout(panel)
        panel_outer.setContentsMargins(1, 1, 1, 1)
        panel_outer.setSpacing(0)

        # Tinted header: the step buttons and what the current step is for.
        header = QFrame()
        header.setObjectName("workflowStepHeader")
        header.setStyleSheet(
            "QFrame#workflowStepHeader { background: #DDE8E2; "
            "border-top-left-radius: 9px; border-top-right-radius: 9px; }"
        )
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(12, 12, 12, 12)
        header_layout.setSpacing(8)
        panel_outer.addWidget(header)

        body = QWidget()
        body.setObjectName("workflowStepBody")
        body.setStyleSheet("QWidget#workflowStepBody { background: transparent; }")
        panel_layout = QVBoxLayout(body)
        panel_layout.setContentsMargins(12, 12, 12, 12)
        panel_layout.setSpacing(10)
        panel_outer.addWidget(body, 1)
        self._sidebar_layout = panel_layout

        # Step buttons across the top, as wide as the panel.
        steps_row = QHBoxLayout()
        steps_row.setContentsMargins(0, 0, 0, 0)
        steps_row.setSpacing(6)
        self._step_buttons: list[QPushButton] = []
        for number in range(1, len(self._STEP_TITLES) + 1):
            button = QPushButton(str(number))
            button.setObjectName("workflowStepButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setMinimumHeight(52)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setStyleSheet(
                "QPushButton#workflowStepButton { background:#FFFFFF; "
                "border:1px solid #C3D2CA; border-radius:8px; color:#3B4A43; "
                "font-size:12px; padding:4px 2px; }"
                "QPushButton#workflowStepButton:hover { border-color:#1F6B4A; "
                "color:#134A32; }"
                "QPushButton#workflowStepButton:checked { background:#1F6B4A; "
                "border-color:#1F6B4A; color:#FFFFFF; font-weight:600; }"
            )
            button.clicked.connect(lambda _checked=False, n=number: self.go_to_step(n))
            steps_row.addWidget(button, 1)
            self._step_buttons.append(button)
        header_layout.addLayout(steps_row)

        self._step_title_lbl = QLabel("")
        self._step_title_lbl.setStyleSheet(
            "font-size:15px; font-weight:600; color:#134A32; background:transparent;"
        )
        header_layout.addWidget(self._step_title_lbl)
        self._step_desc_lbl = QLabel("")
        self._step_desc_lbl.setWordWrap(True)
        self._step_desc_lbl.setStyleSheet(
            "font-size:12px; color:#3B4A43; background:transparent;"
        )
        header_layout.addWidget(self._step_desc_lbl)

        # One page per step; only the current step's controls are on screen.
        self._grp1 = self._build_step1()
        self._grp4 = self._build_apply_roi_step()
        self._grp5 = self._build_saved_blot_files_step()
        self._grp6 = self._build_step6()
        for grp in (self._grp1, self._grp4, self._grp5, self._grp6):
            grp.set_flat(True)

        self._step_stack = QStackedWidget()
        self._step_pages: list[QWidget] = []
        def _page_heading(text: str) -> QLabel:
            heading = QLabel(text)
            heading.setStyleSheet(
                "font-size:12px; font-weight:600; color:#4A5A53; background:transparent;"
            )
            return heading

        page_contents: list[list[QWidget]] = [
            [self._grp1, _page_heading("Saved Blot Files"), self._grp5],
            [self._grp4],
            [_page_heading("Export Figure"), self._grp6, self._save_panel],
        ]
        for widgets in page_contents:
            page = QWidget()
            page.setObjectName("workflowStepPage")
            page.setStyleSheet("QWidget#workflowStepPage { background: #FFFFFF; }")
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(0, 0, 0, 0)
            page_layout.setSpacing(8)
            for widget in widgets:
                page_layout.addWidget(widget)
            page_layout.addStretch(1)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setStyleSheet("QScrollArea { border: none; background: #FFFFFF; }")
            scroll.viewport().setObjectName("workflowStepViewport")
            scroll.viewport().setStyleSheet(
                "QWidget#workflowStepViewport { background: #FFFFFF; }"
            )
            scroll.setWidget(page)
            self._step_stack.addWidget(scroll)
            self._step_pages.append(page)
        panel_layout.addWidget(self._step_stack, 1)

        nav_row = QHBoxLayout()
        nav_row.setContentsMargins(0, 0, 0, 0)
        nav_row.setSpacing(8)
        self._step_prev_btn = QPushButton("Previous")
        self._step_prev_btn.setObjectName("workflowStepPrevButton")
        self._step_prev_btn.setFixedHeight(36)
        self._step_prev_btn.setStyleSheet(_SMALL_BTN_STYLE)
        self._step_prev_btn.clicked.connect(lambda: self.go_to_step(self._current_step - 1))
        nav_row.addWidget(self._step_prev_btn, 1)
        self._step_next_btn = QPushButton("Next")
        self._step_next_btn.setObjectName("workflowStepNextButton")
        self._step_next_btn.setFixedHeight(36)
        self._step_next_btn.setStyleSheet(
            "QPushButton { background:#FFFFFF; border:1px solid #1F6B4A; "
            "border-radius:8px; color:#1F6B4A; font-size:12px; font-weight:600; "
            "padding:4px 12px; }"
            "QPushButton:hover { background:#E3EFE8; }"
            "QPushButton:disabled { color:#9AA8A1; border-color:#E4E9E6; }"
        )
        self._step_next_btn.clicked.connect(lambda: self.go_to_step(self._current_step + 1))
        nav_row.addWidget(self._step_next_btn, 1)
        panel_layout.addLayout(nav_row)
        root.addWidget(panel)

        # Wire scene selection → font/line controls
        self._canvas._scene.selectionChanged.connect(self._on_canvas_selection_changed)
        self.go_to_step(1)

    # ── Step panel ────────────────────────────────────────────────────────

    _STEP_TITLES = ("Choose Layout", "Fill Frames", "Save & Export")
    _STEP_DESCRIPTIONS = (
        "Decide how many blot frames and lanes the figure has, reuse a saved "
        "template, or reopen a saved Blot File.",
        "Click a frame in the preview, draw the band region on the source "
        "image, then press Enter to place it.",
        "Export the figure, or save it to keep editing later.",
    )

    def current_step(self) -> int:
        return self._current_step

    def go_to_step(self, number: int) -> None:
        """Show one step's controls; steps can be visited in any order."""
        number = max(1, min(int(number), len(self._STEP_TITLES)))
        self._current_step = number
        self._step_stack.setCurrentIndex(number - 1)
        for index, button in enumerate(self._step_buttons, start=1):
            button.setChecked(index == number)
        self._step_prev_btn.setEnabled(number > 1)
        self._step_next_btn.setEnabled(number < len(self._STEP_TITLES))
        self._refresh_step_panel_texts()

    def reveal_widget(self, widget: QWidget | None) -> None:
        """Switch to the step that holds a control (used by the tutorial)."""
        if widget is None:
            return
        for index, page in enumerate(self._step_pages, start=1):
            if page is widget or page.isAncestorOf(widget):
                if index != self._current_step:
                    self.go_to_step(index)
                return

    def _refresh_step_panel_texts(self) -> None:
        for index, button in enumerate(self._step_buttons, start=1):
            button.setText(f"{index}\n{tr(self._STEP_TITLES[index - 1], self._language)}")
        current = self._current_step
        self._step_title_lbl.setText(
            tr("Step {number}", self._language, number=current)
            + "  "
            + tr(self._STEP_TITLES[current - 1], self._language)
        )
        self._step_desc_lbl.setText(
            tr(self._STEP_DESCRIPTIONS[current - 1], self._language)
        )
        self._step_prev_btn.setText(tr("Previous", self._language))
        self._step_next_btn.setText(tr("Next", self._language))

    # ── Saved template library ────────────────────────────────────────────

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
            "font-size:12px; font-weight:600; color:#5C7167;"
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
            "font-size:12px; font-weight:600; color:#3A4B43; padding:1px;"
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
                "QRadioButton { font-size:12px; color:#2F3D36; spacing:4px; }"
                "QRadioButton::indicator { width:13px; height:13px; }"
            )
            self._roi_mode_group.addButton(button)
            mode_row.addWidget(button)
        mode_row.addStretch(1)
        self._auto_detect_radio.toggled.connect(self._on_roi_fill_mode_changed)
        grp.body_layout().addLayout(mode_row)

        enter_hint = QLabel("select then hit Enter/Return")
        enter_hint.setStyleSheet(
            "font-size:11px; color:#73867D; padding:0 1px 2px 1px;"
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
        self._auto_fit_h_margin.setFixedHeight(28)
        self._auto_fit_h_margin.setStyleSheet(
            "QSpinBox { background:#FFFFFF; border:1px solid #C7D6CE; "
            "border-radius:4px; padding:1px 3px; font-size:12px; }"
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
        self._auto_fit_v_margin.setFixedHeight(28)
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
        margin_label_style = "font-size:12px; color:#3A4B43; background:transparent;"
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
        fix_btn.setFixedHeight(30)
        fix_btn.setToolTip("Capture the current ROI size, or arm the next drawn ROI as the fixed size.")
        fix_btn.clicked.connect(self._on_add_fixed_roi_clicked)
        fixed_row.addWidget(fix_btn)

        cancel_btn = QPushButton("Cancel Fixed ROI")
        cancel_btn.setStyleSheet(_SMALL_BTN_STYLE)
        cancel_btn.setFixedHeight(30)
        cancel_btn.setToolTip("Return all WB image windows to freehand ROI drawing.")
        cancel_btn.clicked.connect(self._on_cancel_fixed_roi_clicked)
        fixed_row.addWidget(cancel_btn)
        self._fixed_roi_disclosure.body_layout().addLayout(fixed_row)

        self._fixed_roi_list = QListWidget()
        self._fixed_roi_list.setMinimumHeight(54)
        self._fixed_roi_list.setMaximumHeight(82)
        self._fixed_roi_list.setStyleSheet(
            "QListWidget { background:#FFFFFF; border:1px solid #CBD9D1; border-radius:6px; font-size:12px; }"
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
        label.setStyleSheet("font-size:12px; color:#2C4A3D;")
        layout.addWidget(label, 1)
        delete_btn = QToolButton()
        delete_btn.setText("×")
        delete_btn.setToolTip("Delete this fixed ROI size")
        delete_btn.setFixedSize(20, 20)
        delete_btn.setStyleSheet(
            "QToolButton { border:1px solid #9EB3A8; border-radius:8px; background:#F7FAF8; color:#6A7A72; font-size:12px; }"
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
        self._blot_file_list.setMaximumHeight(200)
        self._blot_file_list.setStyleSheet(
            "QListWidget { background:#FFFFFF; border:1px solid #C9B6B6; border-radius:6px; font-size:12px; }"
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
        def modified_time(path: Path) -> float:
            try:
                return path.stat().st_mtime
            except OSError:
                return 0.0

        summaries: list[dict] = []
        for path in sorted(USER_BLOT_FILES_DIR.glob("*.json"), key=modified_time, reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(data, dict) or data.get("format") != "wb_blot_file":
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
        lbl.setStyleSheet("font-size:12px; color:#4A2428;")
        lbl.setCursor(Qt.CursorShape.IBeamCursor)
        lbl.setToolTip("Double-click to rename this saved Blot File")
        lbl.doubleClicked.connect(self._on_rename_blot_file)
        row_layout.addWidget(lbl, 1)
        del_btn = QToolButton()
        del_btn.setText("×")
        del_btn.setFixedSize(20, 20)
        del_btn.setToolTip(f'Delete Blot File "{summary["name"]}"')
        del_btn.setStyleSheet(
            "QToolButton { border:1px solid #BCA0A0; border-radius:8px; "
            "background:#FDF8F8; color:#7A5B5B; font-size:12px; }"
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
        if self._current_step == 1:
            # Picking a frame is the start of filling it.
            self.go_to_step(2)
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
        self._export_pdf_btn.setFixedHeight(36)
        self._export_pdf_btn.setStyleSheet(_SECONDARY_EXPORT_BTN_STYLE)
        self._export_pdf_btn.setToolTip("Export Figure as PDF")
        self._export_pdf_btn.clicked.connect(self._on_export_pdf)
        formats_row.addWidget(self._export_pdf_btn)

        self._export_tiff_btn = QPushButton("TIFF")
        self._export_tiff_btn.setObjectName("exportFormatButton")
        self._export_tiff_btn.setFixedHeight(36)
        self._export_tiff_btn.setStyleSheet(_SECONDARY_EXPORT_BTN_STYLE)
        self._export_tiff_btn.setToolTip("Export Figure as TIFF")
        self._export_tiff_btn.clicked.connect(self._on_export_tiff)
        formats_row.addWidget(self._export_tiff_btn)

        self._export_pptx_btn = QPushButton("PPTX")
        self._export_pptx_btn.setObjectName("exportFormatButton")
        self._export_pptx_btn.setFixedHeight(36)
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
            note.setStyleSheet("color:#A66; font-size:12px;")
            bl.addWidget(note)

        return grp

    # ── Canvas annotation toolbar ─────────────────────────────────────────

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

    # ── Helpers ───────────────────────────────────────────────────────────

    def _get_slot(self, pi: int, si: int) -> BlotSlot | None:
        if self._project is None:
            return None
        return self._project.get_slot(pi, si)


# ── Shared button stylesheets ─────────────────────────────────────────────────
