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

The sidebar is non-linear: every step group is always visible and can be
expanded/collapsed freely.  "Apply" buttons on Steps 1 and 2 trigger
structural rebuilds; Step 3 applies the active WB ROI to the selected blot
frame; the top toolbar controls annotations and text alignment.
"""
from __future__ import annotations

import copy
import json
import re
import shutil
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QPointF, QSize, QSizeF, QSignalBlocker, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame, QGroupBox,
    QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QInputDialog, QListWidget, QListWidgetItem,
    QComboBox, QMenu, QScrollArea, QSizePolicy, QSpinBox, QDoubleSpinBox, QSplitter,
    QToolButton, QVBoxLayout, QWidget, QFontComboBox,
)

from core.export_engine import PPTX_AVAILABLE, PDFExporter, PPTXExporter
from core.figure_project import (
    BlotSlot, ConditionTable, FigureProject,
    GlobalLayout, ImageBBox, LaneROI, Panel, SourceRef,
)
from core.layout_engine import LayoutEngine, LayoutResult, pt_to_scene
from core.template_engine import TemplateEngine
from gui.figure_canvas import FigureCanvas

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


# ── Collapsible group box ─────────────────────────────────────────────────────

class _CollapseGroup(QWidget):
    """A titled group with a ▾/▸ toggle button to show/hide its body."""

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self._expanded = True

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 4)
        outer.setSpacing(0)

        # Header row
        header = QFrame()
        header.setStyleSheet(
            "QFrame { background-color: #D6E4DE; border-radius: 5px; }"
        )
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(8, 4, 8, 4)
        h_layout.setSpacing(6)

        self._toggle_btn = QToolButton()
        self._toggle_btn.setText("▾")
        self._toggle_btn.setStyleSheet(
            "QToolButton { border: none; background: transparent; "
            "font-weight: bold; color: #2C5040; }"
        )
        self._toggle_btn.clicked.connect(self._toggle)
        h_layout.addWidget(self._toggle_btn)

        self._title_label = QLabel(title)
        self._title_label.setStyleSheet("font-weight: 600; color: #1E3D2F; font-size: 11px;")
        h_layout.addWidget(self._title_label)
        h_layout.addStretch()
        outer.addWidget(header)

        # Body widget
        self._body = QWidget()
        self._body.setStyleSheet(
            "QWidget { background-color: #F2F7F4; border-radius: 0 0 5px 5px; }"
        )
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(10, 8, 10, 8)
        self._body_layout.setSpacing(6)
        outer.addWidget(self._body)

    def body_layout(self) -> QVBoxLayout:
        return self._body_layout

    def title_text(self) -> str:
        return self._title_label.text()

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self._body.setVisible(self._expanded)
        self._toggle_btn.setText("▾" if self._expanded else "▸")


# ── Main window widget ────────────────────────────────────────────────────────

class FigureModeWindow(QWidget):
    """Container for the WB Plot Figure Generation mode."""

    # Keep the workflow controls compact so the figure canvas receives more
    # horizontal space.  This is two-thirds of the former 310 px width.
    _SIDEBAR_WIDTH = 207

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        TemplateEngine.load_user_templates()

        self._project: FigureProject | None = None
        self._layout_engine = LayoutEngine()
        self._layout_result: LayoutResult | None = None
        self._active_image_provider: Callable[[], dict[str, object]] | None = None
        self._fixed_roi_requested: Callable[[], QSizeF | None] | None = None
        self._fixed_roi_cancel_requested: Callable[[], None] | None = None
        self._fixed_roi_size_selected: Callable[[QSizeF], None] | None = None
        self._focus_requested: Callable[[], None] | None = None
        self._context_controls_widget: QWidget | None = None
        self._text_style_overrides: dict[tuple, dict] = {}
        self._fixed_roi_sizes: list[tuple[str, QSizeF]] = []
        self._active_slot_ref: SourceRef | None = None
        self._active_template_id: str | None = None
        self._active_blot_file_id: str | None = None
        self._active_table_style: str = "none"
        self._canvas_undo_stack: list[dict] = []
        self._restoring_canvas_undo = False
        self._canvas_undo_queued = False

        # Dynamic sidebar sub-widgets rebuilt when project structure changes
        self._step4_slot_widgets: list[QWidget] = []

        self._build_ui()

    def set_active_image_provider(self, provider: Callable[[], dict[str, object]]) -> None:
        self._active_image_provider = provider

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
        sidebar_layout.setContentsMargins(2, 12, 2, 12)
        sidebar_layout.setSpacing(6)
        self._sidebar_layout = sidebar_layout

        # Title
        title_lbl = QLabel("WB Plot Figure Generation")
        title_lbl.setStyleSheet(
            "font-weight: 700; font-size: 13px; color: #1E3D2F; padding-bottom: 6px;"
        )
        sidebar_layout.addWidget(title_lbl)

        # Step groups
        self._grp1 = self._build_step1()
        self._grp2 = self._build_step2()
        self._grp4 = self._build_apply_roi_step()
        self._grp5 = self._build_saved_blot_files_step()
        self._grp6 = self._build_step6()

        for grp in (self._grp2, self._grp1, self._grp4, self._grp5, self._grp6):
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
        grp = _CollapseGroup("Saved Template")
        bl = grp.body_layout()

        # Template list
        self._template_list = QListWidget()
        self._template_list.setMaximumHeight(140)
        self._template_list.setStyleSheet(
            "QListWidget { background:#FFFFFF; border:1px solid #B8C7DA; border-radius:6px; font-size:10px; }"
            "QListWidget::item { padding:2px 4px; }"
            "QListWidget::item:selected { background:#C5D8EF; color:#1E2D3F; }"
        )
        self._template_list.itemClicked.connect(self._on_template_list_clicked)
        bl.addWidget(self._template_list)

        self._populate_template_list()

        apply_btn = QPushButton("Apply Template")
        apply_btn.setStyleSheet(_APPLY_BTN_STYLE)
        apply_btn.clicked.connect(self._on_apply_template)
        bl.addWidget(apply_btn)

        return grp

    def _populate_template_list(self) -> None:
        self._template_list.clear()
        for tmpl in TemplateEngine.all_templates():
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, tmpl.id)
            item.setSizeHint(QSize(0, 26))
            self._template_list.addItem(item)
            self._install_template_list_item(item, tmpl)
        if self._template_list.count():
            self._template_list.setCurrentRow(0)

    def _install_template_list_item(self, item: QListWidgetItem, tmpl) -> None:
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(6, 0, 4, 0)
        row_layout.setSpacing(4)
        lbl = _TemplateNameLabel(tmpl.display_name, tmpl.id)
        lbl.setStyleSheet("font-size:10px; color:#1E2D3F;")
        if tmpl.is_user_template:
            lbl.setCursor(Qt.CursorShape.IBeamCursor)
            lbl.setToolTip("Double-click to rename this saved template")
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

    def _on_delete_template(self, template_id: str) -> None:
        tmpl = TemplateEngine.get_template(template_id)
        action = "Hide built-in template" if TemplateEngine.is_builtin(template_id) else "Delete template"
        reply = QMessageBox.question(
            self, "Delete Template",
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
            self, "Rename Template", "Template name:", text=tmpl.display_name
        )
        if not ok:
            return
        name = name.strip()
        if not name or name == tmpl.display_name:
            return
        try:
            TemplateEngine.rename_user_template(template_id, name)
        except Exception as exc:
            QMessageBox.critical(self, "Rename Template", f"Failed:\n{exc}")
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

    def _build_step2(self) -> _CollapseGroup:
        grp = _CollapseGroup("Create a template")
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)

        self._panels_spin = QSpinBox()
        self._panels_spin.setRange(1, 15)
        self._panels_spin.setValue(1)
        form.addRow("Panels:", self._panels_spin)

        self._blots_spin = QSpinBox()
        self._blots_spin.setRange(1, 15)
        self._blots_spin.setValue(2)
        form.addRow("Blots", self._blots_spin)

        self._lanes_spin = QSpinBox()
        self._lanes_spin.setRange(2, 12)
        self._lanes_spin.setValue(4)
        form.addRow("Lanes:", self._lanes_spin)

        grp.body_layout().addLayout(form)

        apply_btn = QPushButton("Apply Structure")
        apply_btn.setStyleSheet(_APPLY_BTN_STYLE)
        apply_btn.clicked.connect(self._on_apply_structure)
        grp.body_layout().addWidget(apply_btn)

        add_blot_btn = QPushButton("Add Blot Frame")
        add_blot_btn.setStyleSheet(_SMALL_BTN_STYLE)
        add_blot_btn.setToolTip(
            "Add a free-position blot frame. Select it, draw a WB ROI, then press Return/Enter to fill it."
        )
        add_blot_btn.clicked.connect(self._on_add_blot_frame)
        grp.body_layout().addWidget(add_blot_btn)

        return grp

    # ── Draw band with ROI ────────────────────────────────────────────────

    def _build_apply_roi_step(self) -> _CollapseGroup:
        grp = _CollapseGroup("Draw Band with ROI")

        self._selected_slot_lbl = QLabel("Selected target: none")
        self._selected_slot_lbl.setWordWrap(True)
        self._selected_slot_lbl.setStyleSheet("color:#334; font-size:10px; font-weight:600;")
        grp.body_layout().addWidget(self._selected_slot_lbl)

        hint = QLabel(
            "Select a blot frame in the preview, draw the ROI on the WB image, then press Return/Enter."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#667; font-size:9px;")
        grp.body_layout().addWidget(hint)

        fixed_row = QHBoxLayout()
        fixed_row.setContentsMargins(0, 0, 0, 0)
        fixed_row.setSpacing(6)

        fix_btn = QPushButton("Fix ROI")
        fix_btn.setStyleSheet(_SMALL_BTN_STYLE)
        fix_btn.setToolTip("Capture the current ROI size, or arm the next drawn ROI as the fixed size.")
        fix_btn.clicked.connect(self._on_add_fixed_roi_clicked)
        fixed_row.addWidget(fix_btn)

        cancel_btn = QPushButton("Cancel fixed ROI")
        cancel_btn.setStyleSheet(_SMALL_BTN_STYLE)
        cancel_btn.setToolTip("Return all WB image windows to freehand ROI drawing.")
        cancel_btn.clicked.connect(self._on_cancel_fixed_roi_clicked)
        fixed_row.addWidget(cancel_btn)
        grp.body_layout().addLayout(fixed_row)

        self._fixed_roi_list = QListWidget()
        self._fixed_roi_list.setMaximumHeight(58)
        self._fixed_roi_list.setStyleSheet(
            "QListWidget { background:#FFFFFF; border:1px solid #CBD9D1; border-radius:6px; font-size:10px; }"
            "QListWidget::item { padding:1px 3px; }"
            "QListWidget::item:selected { background:#C9DED2; color:#1E3D2F; }"
        )
        self._fixed_roi_list.itemClicked.connect(self._on_fixed_roi_item_selected)
        self._fixed_roi_list.itemDoubleClicked.connect(self._on_fixed_roi_item_double_clicked)
        grp.body_layout().addWidget(self._fixed_roi_list)
        return grp

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
        grp = _CollapseGroup("Saved Blot Files")
        bl = grp.body_layout()

        self._blot_file_list = QListWidget()
        self._blot_file_list.setMaximumHeight(86)
        self._blot_file_list.setStyleSheet(
            "QListWidget { background:#FFFFFF; border:1px solid #C9B6B6; border-radius:6px; font-size:10px; }"
            "QListWidget::item { padding:2px 4px; }"
            "QListWidget::item:selected { background:#E6CACA; color:#4A2428; }"
        )
        self._blot_file_list.itemClicked.connect(self._on_blot_file_list_clicked)
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
        lbl = QLabel(str(summary["name"]))
        lbl.setStyleSheet("font-size:10px; color:#4A2428;")
        row_layout.addWidget(lbl, 1)
        del_btn = QToolButton()
        del_btn.setText("×")
        del_btn.setFixedSize(16, 16)
        del_btn.setToolTip(f'Delete blot file "{summary["name"]}"')
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
            f'Delete blot file "{name}"?',
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
            QMessageBox.warning(self, "Open Blot File", "Select a saved blot file first.")
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
                    display_width_pt=bd.get("display_width_pt"),
                    display_height_pt=bd.get("display_height_pt"),
                    image_transform=dict(bd.get("image_transform")) if isinstance(bd.get("image_transform"), dict) else None,
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
            self._selected_slot_lbl.setText("Selected target: none")
            return
        slot = self._get_slot(self._active_slot_ref.panel_idx, self._active_slot_ref.slot_idx)
        if slot is None:
            self._selected_slot_lbl.setText("Selected target: none")
            return
        panel_label = ""
        if self._project is not None and len(self._project.panels) > 1:
            panel = self._project.panels[self._active_slot_ref.panel_idx]
            panel_label = f"Panel {panel.panel_letter} - "
        self._selected_slot_lbl.setText(
            f"Selected target: {panel_label}[{self._active_slot_ref.slot_idx + 1}] {slot.label}"
        )

    def _on_canvas_blot_selected(self, ref: SourceRef) -> None:
        if ref.panel_idx is None or ref.slot_idx is None:
            return
        self._active_slot_ref = ref
        self._refresh_selected_slot_label()
        self._on_canvas_selection_changed()

    def _on_canvas_blot_selection_cleared(self) -> None:
        self._active_slot_ref = None
        self._refresh_selected_slot_label()
        self._on_canvas_selection_changed()

    def _on_add_blot_frame(self) -> None:
        item = self._canvas.add_overlay_blot_frame()
        item.setFocus()
        self._active_slot_ref = None
        self._selected_slot_lbl.setText("Selected target: added blot frame")
        self._on_canvas_selection_changed()

    def _on_canvas_view_interacted(self) -> None:
        if self._focus_requested is not None:
            self._focus_requested()

    def apply_roi_to_selected_slot(self) -> bool:
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
                "Select a blot frame in the preview before pressing Return/Enter.",
            )
            return False
        return self._on_use_active_image_roi(ref.panel_idx, ref.slot_idx)

    def _active_image_roi_payload(self) -> tuple[str, dict[str, float], dict | None] | None:
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
        return (
            image_path,
            {
                "x": float(roi.x()),
                "y": float(roi.y()),
                "w": float(roi.width()),
                "h": float(roi.height()),
            },
            transform,
        )

    def _on_use_active_image_roi_for_overlay(self, items: list) -> bool:
        payload = self._active_image_roi_payload()
        if payload is None:
            return False
        image_path, roi, transform = payload
        self._remember_canvas_undo_state()
        for item in items:
            item.image_path = image_path
            item.roi = dict(roi)
            item.transform = dict(transform or {})
            item.update()
        self._canvas.viewport().update()
        return True

    def _on_use_active_image_roi(self, pi: int, si: int) -> bool:
        slot = self._get_slot(pi, si)
        if slot is None:
            QMessageBox.warning(self, "No Slot", "Apply a template first, then choose a blot slot.")
            return False
        payload = self._active_image_roi_payload()
        if payload is None:
            return False
        image_path, roi, image_transform = payload

        slot.source_image_path = image_path
        slot.bounding_box = ImageBBox(
            x=roi["x"],
            y=roi["y"],
            w=roi["w"],
            h=roi["h"],
        )
        slot.image_transform = image_transform
        slot.lane_count = max(1, int(self._lanes_spin.value()))
        lane_width = 1.0 / slot.lane_count
        slot.lane_rois = [
            LaneROI(
                lane_index=i,
                x_offset=round(i * lane_width, 8),
                width=round(lane_width, 8),
            )
            for i in range(slot.lane_count)
        ]

        self._rebuild_step4()
        self._recompute_and_refresh()
        return True

    # ── Step 4: Export ────────────────────────────────────────────────────

    def _build_step6(self) -> _CollapseGroup:
        grp = _CollapseGroup("Step 4 — Export")
        bl = grp.body_layout()

        pdf_btn = QPushButton("Export PDF")
        pdf_btn.setStyleSheet(_EXPORT_BTN_STYLE)
        pdf_btn.clicked.connect(self._on_export_pdf)
        bl.addWidget(pdf_btn)

        pptx_btn = QPushButton("Export PPTX")
        pptx_btn.setStyleSheet(_EXPORT_BTN_STYLE)
        if not PPTX_AVAILABLE:
            pptx_btn.setEnabled(False)
            pptx_btn.setToolTip(
                "python-pptx is not installed.\n"
                "Run:  pip install python-pptx"
            )
        pptx_btn.clicked.connect(self._on_export_pptx)
        bl.addWidget(pptx_btn)

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

        save_tmpl_btn = QPushButton("Save Template")
        save_tmpl_btn.setStyleSheet(_SMALL_BTN_STYLE)
        save_tmpl_btn.setToolTip(
            "Save current layout (structure, labels, annotations)\n"
            "as a reusable template in Saved Template"
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
        self._toolbar_font_family_combo.setFixedWidth(130)
        self._toolbar_font_family_combo.setFixedHeight(_TOOLBAR_BTN_H)
        self._toolbar_font_family_combo.setStyleSheet("font-size:9px;")
        self._toolbar_font_family_combo.setEnabled(False)
        self._toolbar_font_family_combo.currentFontChanged.connect(
            lambda f: self._apply_toolbar_font(family=f.family())
        )
        row2.addWidget(self._toolbar_font_family_combo)

        self._toolbar_font_size_spin = QDoubleSpinBox()
        self._toolbar_font_size_spin.setRange(4.0, 72.0)
        self._toolbar_font_size_spin.setSingleStep(1.0)
        self._toolbar_font_size_spin.setDecimals(1)
        self._toolbar_font_size_spin.setValue(12.0)
        self._toolbar_font_size_spin.setFixedWidth(58)
        self._toolbar_font_size_spin.setFixedHeight(_TOOLBAR_BTN_H)
        self._toolbar_font_size_spin.setStyleSheet("font-size:9px;")
        self._toolbar_font_size_spin.setEnabled(False)
        self._toolbar_font_size_spin.valueChanged.connect(
            lambda v: self._apply_toolbar_font(size=v)
        )
        row2.addWidget(self._toolbar_font_size_spin)

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

        rotation_lbl = QLabel("Rotate:")
        rotation_lbl.setStyleSheet("font-size:9px; color:#3D4D56;")
        row2.addWidget(rotation_lbl)

        self._text_rotation_spin = QDoubleSpinBox()
        self._text_rotation_spin.setRange(-180.0, 180.0)
        self._text_rotation_spin.setSingleStep(1.0)
        self._text_rotation_spin.setDecimals(1)
        self._text_rotation_spin.setValue(0.0)
        self._text_rotation_spin.setFixedWidth(72)
        self._text_rotation_spin.setFixedHeight(_TOOLBAR_BTN_H)
        self._text_rotation_spin.setSuffix(" deg")
        self._text_rotation_spin.setStyleSheet("font-size:9px;")
        self._text_rotation_spin.setEnabled(False)
        self._text_rotation_spin.valueChanged.connect(self._apply_toolbar_text_rotation)
        row2.addWidget(self._text_rotation_spin)

        sep3 = QFrame()
        sep3.setFrameShape(QFrame.Shape.VLine)
        sep3.setStyleSheet("QFrame { color:#AABDB4; }")
        sep3.setFixedWidth(1)
        row2.addWidget(sep3)

        line_w_lbl = QLabel("Line:")
        line_w_lbl.setStyleSheet("font-size:9px; color:#3D4D56;")
        row2.addWidget(line_w_lbl)

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
        row2.addWidget(self._line_width_spin)

        self._match_size_btn = QToolButton()
        self._match_size_btn.setText("Same Size")
        self._match_size_btn.setToolTip("Make selected text boxes or blot frames the same size")
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
        vbox.addLayout(row2)
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
            restored_project = snapshot.get("project")
            if restored_project is not None:
                self._project = restored_project
            self._canvas.restore_state_snapshot(
                snapshot.get("canvas", {}),
                repopulate_scene=self._project is None,
            )
            with QSignalBlocker(self._canvas._scene):
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

    def _apply_selected_line_width(self, width: float) -> None:
        self._canvas.set_selected_line_width(width)

    def _apply_toolbar_text_rotation(self, angle: float) -> None:
        if not self._canvas.selected_text_items():
            return
        self._remember_canvas_undo_state()
        styles = self._canvas.apply_selected_text_rotation(angle)
        if styles:
            self._text_style_overrides.update(styles)
            self._apply_text_style_overrides_to_layout()

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
            self._selected_slot_lbl.setText("Selected target: added blot frame")
        elif not has_blot and self._active_slot_ref is None:
            self._refresh_selected_slot_label()
        can_align_or_size = has_text or has_blot or has_floating_blot
        for w in (self._toolbar_font_family_combo, self._toolbar_font_size_spin,
                  self._bold_btn, self._italic_btn, self._underline_btn,
                  self._text_inside_left_btn,
                  self._text_inside_center_btn, self._text_inside_right_btn,
                  self._text_rotation_spin):
            w.setEnabled(has_text)
        self._align_text_boxes_combo.setEnabled(can_align_or_size)
        self._match_size_btn.setEnabled(self._canvas.selected_layout_item_count() >= 2)
        if has_text:
            font = sel_text.font()
            self._toolbar_font_family_combo.blockSignals(True)
            self._toolbar_font_family_combo.setCurrentFont(font)
            self._toolbar_font_family_combo.blockSignals(False)
            self._toolbar_font_size_spin.blockSignals(True)
            self._toolbar_font_size_spin.setValue(font.pointSizeF())
            self._toolbar_font_size_spin.blockSignals(False)
            self._bold_btn.blockSignals(True)
            self._bold_btn.setChecked(font.bold())
            self._bold_btn.blockSignals(False)
            self._italic_btn.blockSignals(True)
            self._italic_btn.setChecked(font.italic())
            self._italic_btn.blockSignals(False)
            self._underline_btn.blockSignals(True)
            self._underline_btn.setChecked(font.underline())
            self._underline_btn.blockSignals(False)
            self._text_rotation_spin.blockSignals(True)
            self._text_rotation_spin.setValue(sel_text.rotation())
            self._text_rotation_spin.blockSignals(False)
        else:
            self._text_rotation_spin.blockSignals(True)
            self._text_rotation_spin.setValue(0.0)
            self._text_rotation_spin.blockSignals(False)

        # Line width control
        self._line_width_spin.setEnabled(sel_line is not None)
        if sel_line is not None:
            self._line_width_spin.blockSignals(True)
            self._line_width_spin.setValue(sel_line.pen().widthF())
            self._line_width_spin.blockSignals(False)

    def _on_save_template(self) -> bool:
        if self._project is None:
            QMessageBox.warning(
                self, "No Figure",
                "Apply a template first before saving.",
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
        choice.setWindowTitle("Save Template")
        choice.setText("How would you like to save this template?")
        if can_update_current and active_template is not None:
            choice.setInformativeText(
                f'Choose whether to create a new template or update "{active_template.display_name}".'
            )
        else:
            choice.setInformativeText(
                "Save to current template is available after applying a saved user template."
            )
        new_btn = choice.addButton(
            "Save as new template",
            QMessageBox.ButtonRole.AcceptRole,
        )
        current_btn = choice.addButton(
            "Save to current template",
            QMessageBox.ButtonRole.ActionRole,
        )
        current_btn.setEnabled(can_update_current)
        choice.addButton(QMessageBox.StandardButton.Cancel)
        choice.exec()

        clicked = choice.clickedButton()
        if clicked is current_btn and active_template_id:
            return self._save_to_current_template(active_template_id)
        if clicked is new_btn:
            return self._save_as_new_template()
        return False

    def _save_as_new_template(self) -> bool:
        name, ok = QInputDialog.getText(
            self, "Save Template", "Template name:",
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
                self, "Template Saved",
                f'Template \u201c{name}\u201d saved and added to the Saved Template section.',
            )
            return True
        except Exception as exc:
            QMessageBox.critical(self, "Save Template", f"Failed:\n{exc}")
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
                self, "Template Saved",
                f'Template \u201c{tmpl.display_name}\u201d updated.',
            )
            return True
        except Exception as exc:
            QMessageBox.critical(self, "Save Template", f"Failed:\n{exc}")
            return False

    def _on_save_blot_file(self) -> bool:
        if self._project is None:
            QMessageBox.warning(
                self,
                "No Figure",
                "Apply a template first before saving a blot file.",
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
        choice.setText("How would you like to save this blot file?")
        if can_update_current and active_name:
            choice.setInformativeText(
                f'Choose whether to create a new blot file or update "{active_name}".'
            )
        else:
            choice.setInformativeText(
                "Save to current blot file is available after opening a saved blot file."
            )
        new_btn = choice.addButton(
            "Save as new blot file",
            QMessageBox.ButtonRole.AcceptRole,
        )
        current_btn = choice.addButton(
            "Save to current blot file",
            QMessageBox.ButtonRole.ActionRole,
        )
        current_btn.setEnabled(can_update_current)
        choice.addButton(QMessageBox.StandardButton.Cancel)
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
            "Blot file name:",
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
        self._copy_blot_file_assets(blot_file_id, project_data, canvas_state)
        payload = {
            "format": "wb_blot_file",
            "version": 1,
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
        self._blot_file_path(blot_file_id).write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )

    def _copy_blot_file_assets(
        self,
        blot_file_id: str,
        project_data: dict,
        canvas_state: dict,
    ) -> None:
        assets_dir = USER_BLOT_FILES_DIR / f"{blot_file_id}_assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        copied: dict[str, str] = {}

        def copy_path(path_value) -> str:
            if not path_value:
                return ""
            source = Path(str(path_value)).expanduser()
            if not source.exists() or not source.is_file():
                return str(path_value)
            source_key = str(source.resolve())
            if source_key in copied:
                return copied[source_key]
            suffix = source.suffix or ".img"
            asset_path = assets_dir / f"image_{len(copied) + 1}{suffix}"
            if source.resolve() != asset_path.resolve():
                shutil.copy2(source, asset_path)
            copied[source_key] = str(asset_path)
            return copied[source_key]

        for panel in project_data.get("panels", []):
            for blot in panel.get("blot_slots", []):
                blot["source_image_path"] = copy_path(blot.get("source_image_path"))

        for item in canvas_state.get("overlay_items", []):
            if item.get("type") == "blot":
                item["image_path"] = copy_path(item.get("image_path"))

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
            "Save Current Template?",
            "Do you want to save the current WB plot template before applying the new template or structure?",
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

    def _on_apply_template(self) -> None:
        tid, table_style = self._current_template_selection()
        if not self._confirm_save_current_template_before_apply():
            return
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
                QMessageBox.critical(self, "Load Template", f"Failed to load template:\n{exc}")
                return
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
            return

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
        if n_panels > old_panel_count and old_overlay_data and old_layout is not None:
            self._duplicate_first_panel_overlays_for_new_panels(
                old_overlay_data,
                old_layout,
                old_panel_count,
                n_panels,
            )
        self._rebuild_step4()
        self._recompute_and_refresh()

    @staticmethod
    def _panel_anchor_scene_y(layout: LayoutResult, panel_idx: int) -> float | None:
        y_values = [
            item.y_pt
            for item in layout.items
            if item.source_ref is not None and item.source_ref.panel_idx == panel_idx
        ]
        if not y_values:
            return None
        return pt_to_scene(min(y_values))

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

        first_anchor = cls._panel_anchor_scene_y(old_layout, 0)
        second_anchor = cls._panel_anchor_scene_y(old_layout, 1)
        if first_anchor is None or second_anchor is None:
            return [dict(item) for item in overlay_data]
        upper = (first_anchor + second_anchor) / 2.0
        return [
            dict(item)
            for item in overlay_data
            if cls._overlay_item_center_y(item) < upper
        ]

    @staticmethod
    def _shift_overlay_item_y(item_data: dict, delta_y: float) -> dict:
        shifted = dict(item_data)
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

        old_first_anchor = self._panel_anchor_scene_y(old_layout, 0)
        if old_first_anchor is None:
            return
        new_layout = self._layout_engine.compute(self._project)
        combined = [dict(item) for item in old_overlay_data]
        for panel_idx in range(old_panel_count, new_panel_count):
            target_anchor = self._panel_anchor_scene_y(new_layout, panel_idx)
            if target_anchor is None:
                continue
            delta_y = target_anchor - old_first_anchor
            combined.extend(
                self._shift_overlay_item_y(item, delta_y)
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
            self._canvas.fit_to_view()

    def _apply_text_style_overrides_to_layout(self) -> None:
        if self._layout_result is None:
            return
        for item in self._layout_result.items:
            if item.source_ref is None:
                continue
            style = self._text_style_overrides.get(item.source_ref.key())
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
                "Apply a template and configure the figure before exporting.",
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

_EXPORT_BTN_STYLE = (
    "QPushButton {"
    "  background-color: #C2D3C8;"
    "  border: 1px solid #9EB3A8;"
    "  border-radius: 6px;"
    "  color: #2C4A3D;"
    "  padding: 5px 12px;"
    "  font-size: 11px;"
    "  font-weight: 600;"
    "}"
    "QPushButton:hover { background-color: #B4C8BB; }"
    "QPushButton:disabled { background-color: #E0E8E4; color: #8BA098; }"
)
