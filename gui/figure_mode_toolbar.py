"""Canvas annotation toolbar, formatting handlers and undo.

Mixed into FigureModeWindow (gui.figure_mode_window); methods rely on its attributes.
"""
from __future__ import annotations

import copy

from PySide6.QtCore import QSignalBlocker, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFontComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.layout_engine import LayoutResult
from gui.figure_mode_styles import (
    _HIGHLIGHT_BTN_STYLE,
    _SMALL_BTN_STYLE,
    _TOOLBAR_BTN_H,
)
from utils.i18n import tr


class CanvasToolbarMixin:
    """Canvas annotation toolbar, formatting handlers and undo."""

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
        from gui.layout_editor_items import EditableTextItem as _OT
        from gui.layout_editor_items import LineElementItem as _OL
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
