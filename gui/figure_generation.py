"""Figure Generation UI workflows (modular, non-analysis logic)."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
import math
from pathlib import Path
import re

from PySide6.QtCore import Qt, Signal, QPoint, QRect, QSize, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QPdfWriter, QRegion, QImage, QFont, QFontMetrics
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFontComboBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QDoubleSpinBox,
    QStackedWidget,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
import numpy as np
import pandas as pd

from utils.i18n import LANG_EN, LANG_ZH_CN, tr, tr_display


def _translate_widget_tree(root: QWidget, language: str) -> None:
    """Translate static controls in a figure-generation widget tree.

    Combobox display labels are intentionally left alone: several use their
    visible English value as an internal style key.
    """
    for label in root.findChildren(QLabel):
        label.setText(tr_display(label.text(), language))
        label.setToolTip(tr_display(label.toolTip(), language))
    for button in root.findChildren(QPushButton):
        button.setText(tr_display(button.text(), language))
        button.setToolTip(tr_display(button.toolTip(), language))
    for checkbox in root.findChildren(QCheckBox):
        checkbox.setText(tr_display(checkbox.text(), language))
        checkbox.setToolTip(tr_display(checkbox.toolTip(), language))
    for list_widget in root.findChildren(QListWidget):
        for index in range(list_widget.count()):
            item = list_widget.item(index)
            item.setText(tr_display(item.text(), language))
    for spin in root.findChildren(QSpinBox):
        spin.setPrefix(tr_display(spin.prefix(), language))
        spin.setSuffix(tr_display(spin.suffix(), language))
    for spin in root.findChildren(QDoubleSpinBox):
        spin.setPrefix(tr_display(spin.prefix(), language))
        spin.setSuffix(tr_display(spin.suffix(), language))

class _DSpin(QDoubleSpinBox):
    """QDoubleSpinBox that selects all text on click for easy replacement."""
    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self.lineEdit().selectAll()
    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.lineEdit().selectAll()


def _index_to_group_label(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA, 27 -> AB ..."""
    n = index + 1
    chars: list[str] = []
    while n > 0:
        n, rem = divmod(n - 1, 26)
        chars.append(chr(ord("A") + rem))
    return "".join(reversed(chars))


class FigureTypeDialog(QDialog):
    """Modal figure-type selector."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Figure Generation")
        self.setModal(True)
        self.setMinimumWidth(320)
        self._selection: str | None = None
        self._language = LANG_EN
        self._build_ui()

    def set_language(self, language: str) -> None:
        self._language = language
        self.setWindowTitle(tr("Figure Generation", language))
        _translate_widget_tree(self, language)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        title = QLabel("Choose table type")
        title.setStyleSheet("font-size: 13px; font-weight: 600; color: #35393D;")
        root.addWidget(title)

        row = QHBoxLayout()
        row.setSpacing(10)

        column_btn = QPushButton("Column")
        grouped_btn = QPushButton("Grouped")
        for btn in (column_btn, grouped_btn):
            btn.setMinimumHeight(36)
            btn.setStyleSheet(
                "QPushButton {"
                "background-color: #E6EEF3;"
                "border: 1px solid #BACAD5;"
                "border-radius: 6px;"
                "color: #385161;"
                "padding: 6px 10px;"
                "font-size: 12px;"
                "font-weight: 600;"
                "}"
                "QPushButton:hover {"
                "background-color: #DCE7EE;"
                "}"
            )
        column_btn.clicked.connect(lambda: self._select("column"))
        grouped_btn.clicked.connect(lambda: self._select("grouped"))
        row.addWidget(column_btn)
        row.addWidget(grouped_btn)
        root.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _select(self, selection: str) -> None:
        self._selection = selection
        self.accept()

    @property
    def selection(self) -> str | None:
        return self._selection


@dataclass(frozen=True)
class ColumnInput:
    samples: int
    replicates: int


class ColumnSetupDialog(QDialog):
    """Modal input dialog for Column table dimensions."""

    def __init__(
        self,
        parent=None,
        *,
        default_samples: int = 3,
        default_replicates: int = 3,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Column Setup")
        self.setModal(True)
        self.setMinimumWidth(300)
        self._language = LANG_EN
        self._default_samples = max(1, int(default_samples))
        self._default_replicates = max(1, int(default_replicates))
        self._build_ui()

    def set_language(self, language: str) -> None:
        self._language = language
        self.setWindowTitle(tr("Column Setup", language))
        _translate_widget_tree(self, language)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        note = QLabel("Enter table dimensions")
        note.setStyleSheet("font-size: 12px; font-weight: 600; color: #35393D;")
        root.addWidget(note)

        self._samples_spin = QSpinBox()
        self._samples_spin.setRange(1, 999)
        self._samples_spin.setValue(self._default_samples)
        self._samples_spin.setPrefix("Samples: ")
        self._samples_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._samples_spin)

        self._replicates_spin = QSpinBox()
        self._replicates_spin.setRange(1, 999)
        self._replicates_spin.setValue(self._default_replicates)
        self._replicates_spin.setPrefix("Replicates: ")
        self._replicates_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._replicates_spin)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._button_box = buttons
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_button is not None:
            ok_button.setObjectName("columnSetupOkButton")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def get_input(self) -> ColumnInput:
        return ColumnInput(
            samples=int(self._samples_spin.value()),
            replicates=int(self._replicates_spin.value()),
        )


class _ColumnTableWidget(QTableWidget):
    deleteRequested = Signal()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.deleteRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class _PreviewScrollArea(QScrollArea):
    clicked = Signal()

    def mousePressEvent(self, event) -> None:
        self.clicked.emit()
        super().mousePressEvent(event)


class _GroupHeaderView(QHeaderView):
    groupRenameRequested = Signal(int)
    groupClicked = Signal(int)

    def __init__(self, orientation: Qt.Orientation, parent=None) -> None:
        super().__init__(orientation, parent)
        self.setSectionsClickable(True)
        self.setHighlightSections(False)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_custom_context_menu)
        self.sectionClicked.connect(self._on_section_clicked)

    def _on_custom_context_menu(self, pos) -> None:
        section = self.logicalIndexAt(pos)
        if section >= 2:
            self.groupRenameRequested.emit(section - 2)

    def _on_section_clicked(self, section: int) -> None:
        if section >= 2:
            self.groupClicked.emit(section - 2)


@dataclass
class FigureStyleConfig:
    font_family: str = "Arial"
    plot_width: int = 500
    plot_height: int = 320
    title: str = ""
    title_font_size: int = 14
    title_bold: bool = True
    title_alignment: str = "center"  # left, center, right
    title_offset_x: int = 0
    title_offset_y: int = 0
    x_title: str = ""
    x_title_font_size: int = 12
    x_title_bold: bool = False
    x_title_offset_x: int = 0
    x_title_offset_y: int = 0
    y_title: str = ""
    y_title_font_size: int = 12
    y_title_bold: bool = False
    y_title_offset_x: int = 0
    y_title_offset_y: int = 0
    y_auto: bool = True
    y_min: float | None = None
    y_max: float | None = None
    y_major_interval: float | None = None
    y_minor_interval: float | None = None
    tick_label_font_size: int = 10
    frame_style: str = "left_bottom"  # no_frame, left_bottom, box_frame
    show_major_grid: bool = False
    major_grid_color: QColor = field(default_factory=lambda: QColor("#E0E0E0"))
    major_grid_width: float = 1.0
    major_grid_style: str = "solid"  # solid, dashed, dotted
    show_minor_grid: bool = False
    minor_grid_color: QColor = field(default_factory=lambda: QColor("#F0F0F0"))
    minor_grid_width: float = 0.8
    minor_grid_style: str = "dotted"  # solid, dashed, dotted
    show_x_tick_labels: bool = True
    x_tick_rotation: int = 0
    x_axis_thickness: float = 1.5
    y_axis_thickness: float = 1.5
    show_minor_tick_labels: bool = False
    bar_width: float = 0.60
    show_error: bool = True
    error_thickness: float = 1.0
    show_scatter: bool = True
    scatter_size: float = 6.0
    colors: dict[str, QColor] = field(
        default_factory=lambda: {
            "bar": QColor("#808080"),
            "error": QColor("#000000"),
            "scatter": QColor("#000000"),
        }
    )


def _copy_style_config_into(target: FigureStyleConfig, source: FigureStyleConfig) -> None:
    for key, value in source.__dict__.items():
        if key == "colors":
            setattr(target, key, {k: QColor(v) for k, v in value.items()})
        elif isinstance(value, QColor):
            setattr(target, key, QColor(value))
        else:
            setattr(target, key, copy.deepcopy(value))


def _clone_style_config(source: FigureStyleConfig) -> FigureStyleConfig:
    target = FigureStyleConfig()
    _copy_style_config_into(target, source)
    return target


def _style_config_signature(cfg: FigureStyleConfig) -> tuple:
    parts: list[tuple[str, object]] = []
    for key in sorted(cfg.__dict__.keys()):
        value = getattr(cfg, key)
        if key == "colors":
            colors = tuple(sorted((k, QColor(v).name()) for k, v in value.items()))
            parts.append((key, colors))
        elif isinstance(value, QColor):
            parts.append((key, value.name()))
        else:
            parts.append((key, value))
    return tuple(parts)


class FigureControlDialog(QDialog):
    styleChanged = Signal()
    confirmRequested = Signal()
    undoRequested = Signal()
    cancelRequested = Signal()

    _SECTIONS: list[tuple[str, str]] = [
        ("General", "general"),
        ("Axis", "axis"),
        ("Frame", "frame"),
        ("Style", "style"),
    ]

    def __init__(
        self,
        style_config: FigureStyleConfig,
        *,
        initial_section: str = "general",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Figure Modification Panel")
        self.resize(640, 420)
        self._cfg = style_config
        self._language = LANG_EN
        self._color_buttons: dict[str, QPushButton] = {}
        self._build_ui(initial_section)

    def set_language(self, language: str) -> None:
        self._language = language
        self.setWindowTitle(tr("Figure Modification Panel", language))
        _translate_widget_tree(self, language)

    def _build_ui(self, initial_section: str) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        self._sidebar = QListWidget()
        self._sidebar.setFixedWidth(145)
        for label, _key in self._SECTIONS:
            self._sidebar.addItem(label)
        root.addWidget(self._sidebar, 0)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(8)
        self._stack = QStackedWidget()
        right.addWidget(self._stack, 1)
        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(8)
        button_row.addStretch(1)

        apply_btn = QPushButton("Apply")
        apply_btn.setToolTip("Apply all parameter changes to the figure")
        apply_btn.clicked.connect(self.confirmRequested.emit)
        button_row.addWidget(apply_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setToolTip("Discard changes and close")
        cancel_btn.clicked.connect(self.cancelRequested.emit)
        button_row.addWidget(cancel_btn)

        ok_btn = QPushButton("OK")
        ok_btn.setToolTip("Apply changes, save, and close")
        ok_btn.clicked.connect(lambda: (self.confirmRequested.emit(), self.accept()))
        button_row.addWidget(ok_btn)
        right.addLayout(button_row)
        root.addLayout(right, 1)

        self._stack.addWidget(self._build_general_panel())
        self._stack.addWidget(self._build_axis_panel())
        self._stack.addWidget(self._build_frame_panel())
        self._stack.addWidget(self._build_style_panel())

        self._sidebar.currentRowChanged.connect(self._stack.setCurrentIndex)
        keys = [key for _label, key in self._SECTIONS]
        index = keys.index(initial_section) if initial_section in keys else 0
        self._sidebar.setCurrentRow(index)

    def keyPressEvent(self, event) -> None:
        """Pressing Return/Enter triggers Apply (confirms current parameters)."""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.confirmRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def switch_to_section(self, section: str) -> None:
        aliases = {
            "title": "general",
            "x_axis": "axis",
            "y_axis": "axis",
            "bars_style": "style",
            "colors": "style",
        }
        section = aliases.get(section, section)
        keys = [key for _label, key in self._SECTIONS]
        if section in keys:
            self._sidebar.setCurrentRow(keys.index(section))
            self.raise_()
            self.activateWindow()

    def _emit_changed(self) -> None:
        self.styleChanged.emit()

    @staticmethod
    def _copy_color(value: QColor) -> QColor:
        return QColor(value)

    def _apply_defaults(self) -> None:
        default = FigureStyleConfig()
        self._cfg.font_family = default.font_family
        self._cfg.plot_width = default.plot_width
        self._cfg.plot_height = default.plot_height
        self._cfg.title = default.title
        self._cfg.title_font_size = default.title_font_size
        self._cfg.title_bold = default.title_bold
        self._cfg.title_alignment = default.title_alignment
        self._cfg.title_offset_x = default.title_offset_x
        self._cfg.title_offset_y = default.title_offset_y
        self._cfg.x_title = default.x_title
        self._cfg.x_title_font_size = default.x_title_font_size
        self._cfg.x_title_offset_x = default.x_title_offset_x
        self._cfg.x_title_offset_y = default.x_title_offset_y
        self._cfg.y_title = default.y_title
        self._cfg.y_title_font_size = default.y_title_font_size
        self._cfg.y_title_offset_x = default.y_title_offset_x
        self._cfg.y_title_offset_y = default.y_title_offset_y
        self._cfg.y_auto = default.y_auto
        self._cfg.y_min = default.y_min
        self._cfg.y_max = default.y_max
        self._cfg.y_major_interval = default.y_major_interval
        self._cfg.y_minor_interval = default.y_minor_interval
        self._cfg.tick_label_font_size = default.tick_label_font_size
        self._cfg.frame_style = "left_bottom"  # always fixed
        self._cfg.show_major_grid = default.show_major_grid
        self._cfg.major_grid_color = self._copy_color(default.major_grid_color)
        self._cfg.major_grid_width = default.major_grid_width
        self._cfg.major_grid_style = default.major_grid_style
        self._cfg.show_minor_grid = default.show_minor_grid
        self._cfg.minor_grid_color = self._copy_color(default.minor_grid_color)
        self._cfg.minor_grid_width = default.minor_grid_width
        self._cfg.minor_grid_style = default.minor_grid_style
        self._cfg.show_x_tick_labels = default.show_x_tick_labels
        self._cfg.x_tick_rotation = default.x_tick_rotation
        self._cfg.x_axis_thickness = default.x_axis_thickness
        self._cfg.y_axis_thickness = default.y_axis_thickness
        self._cfg.show_minor_tick_labels = default.show_minor_tick_labels
        self._cfg.bar_width = default.bar_width
        self._cfg.show_error = default.show_error
        self._cfg.error_thickness = default.error_thickness
        self._cfg.show_scatter = default.show_scatter
        self._cfg.scatter_size = default.scatter_size
        self._cfg.colors = {k: QColor(v) for k, v in default.colors.items()}

    def _build_general_panel(self) -> QWidget:
        panel = QWidget()
        form = QFormLayout(panel)
        form.setContentsMargins(6, 6, 6, 6)
        form.setSpacing(8)

        plot_width_spin = _DSpin()
        plot_width_spin.setRange(240, 4000)
        plot_width_spin.setDecimals(0)
        plot_width_spin.setValue(float(self._cfg.plot_width))
        plot_width_spin.valueChanged.connect(
            lambda value: (setattr(self._cfg, "plot_width", int(value)), self._emit_changed())
        )
        form.addRow("Plot Width", plot_width_spin)

        plot_height_spin = _DSpin()
        plot_height_spin.setRange(200, 3000)
        plot_height_spin.setDecimals(0)
        plot_height_spin.setValue(float(self._cfg.plot_height))
        plot_height_spin.valueChanged.connect(
            lambda value: (setattr(self._cfg, "plot_height", int(value)), self._emit_changed())
        )
        form.addRow("Plot Height", plot_height_spin)

        title_edit = QLineEdit(self._cfg.title)
        title_edit.textChanged.connect(lambda text: (setattr(self._cfg, "title", text), self._emit_changed()))
        form.addRow("Figure Title", title_edit)

        size_spin = _DSpin()
        size_spin.setRange(8, 48)
        size_spin.setDecimals(0)
        size_spin.setValue(float(self._cfg.title_font_size))
        size_spin.valueChanged.connect(
            lambda value: (setattr(self._cfg, "title_font_size", int(value)), self._emit_changed())
        )
        form.addRow("Font Size", size_spin)

        bold_chk = QCheckBox("Bold")
        bold_chk.setChecked(self._cfg.title_bold)
        bold_chk.toggled.connect(
            lambda checked: (setattr(self._cfg, "title_bold", bool(checked)), self._emit_changed())
        )
        form.addRow("", bold_chk)

        font_combo = QFontComboBox()
        font_combo.setCurrentFont(QFont(self._cfg.font_family))
        font_combo.currentFontChanged.connect(
            lambda font: (setattr(self._cfg, "font_family", font.family()), self._emit_changed())
        )
        form.addRow("Font", font_combo)

        align_combo = QComboBox()
        align_combo.addItems(["left", "center", "right"])
        align_combo.setCurrentText(self._cfg.title_alignment)
        align_combo.currentTextChanged.connect(
            lambda text: (setattr(self._cfg, "title_alignment", text), self._emit_changed())
        )
        form.addRow("Alignment", align_combo)

        reset_btn = QPushButton("Reset Visual Style")
        reset_btn.clicked.connect(lambda: (self._apply_defaults(), self._emit_changed()))
        form.addRow("", reset_btn)
        return panel

    def _build_axis_panel(self) -> QWidget:
        panel = QWidget()
        root = QVBoxLayout(panel)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(10)

        x_label = QLabel("X Axis")
        x_label.setStyleSheet("font-weight: 700; color: #3A4A56;")
        root.addWidget(x_label)

        x_form = QFormLayout()
        x_form.setContentsMargins(0, 0, 0, 0)
        x_form.setSpacing(8)

        x_title_edit = QLineEdit(self._cfg.x_title)
        x_title_edit.textChanged.connect(lambda text: (setattr(self._cfg, "x_title", text), self._emit_changed()))
        x_form.addRow("Title", x_title_edit)

        x_size_spin = _DSpin()
        x_size_spin.setRange(8, 48)
        x_size_spin.setDecimals(0)
        x_size_spin.setValue(float(self._cfg.x_title_font_size))
        x_size_spin.valueChanged.connect(
            lambda value: (setattr(self._cfg, "x_title_font_size", int(value)), self._emit_changed())
        )
        x_form.addRow("Font Size", x_size_spin)

        show_ticks_chk = QCheckBox("Show tick labels")
        show_ticks_chk.setChecked(self._cfg.show_x_tick_labels)
        show_ticks_chk.toggled.connect(
            lambda checked: (setattr(self._cfg, "show_x_tick_labels", bool(checked)), self._emit_changed())
        )
        x_form.addRow("", show_ticks_chk)

        rotation_spin = _DSpin()
        rotation_spin.setRange(-180, 180)
        rotation_spin.setSingleStep(15)
        rotation_spin.setSuffix("°")
        rotation_spin.setDecimals(0)
        rotation_spin.setValue(float(self._cfg.x_tick_rotation))
        rotation_spin.valueChanged.connect(
            lambda value: (setattr(self._cfg, "x_tick_rotation", int(value)), self._emit_changed())
        )
        x_form.addRow("Title Rotation", rotation_spin)

        x_thick_spin = _DSpin()
        x_thick_spin.setRange(0.5, 5.0)
        x_thick_spin.setSingleStep(0.25)
        x_thick_spin.setDecimals(1)
        x_thick_spin.setValue(float(self._cfg.x_axis_thickness))
        x_thick_spin.valueChanged.connect(
            lambda value: (setattr(self._cfg, "x_axis_thickness", float(value)), self._emit_changed())
        )
        x_form.addRow("Axis Thickness", x_thick_spin)
        root.addLayout(x_form)

        y_label = QLabel("Y Axis")
        y_label.setStyleSheet("font-weight: 700; color: #3A4A56;")
        root.addWidget(y_label)

        y_form = QFormLayout()
        y_form.setContentsMargins(0, 0, 0, 0)
        y_form.setSpacing(8)

        y_title_edit = QLineEdit(self._cfg.y_title)
        y_title_edit.textChanged.connect(lambda text: (setattr(self._cfg, "y_title", text), self._emit_changed()))
        y_form.addRow("Title", y_title_edit)

        y_size_spin = _DSpin()
        y_size_spin.setRange(8, 48)
        y_size_spin.setDecimals(0)
        y_size_spin.setValue(float(self._cfg.y_title_font_size))
        y_size_spin.valueChanged.connect(
            lambda value: (setattr(self._cfg, "y_title_font_size", int(value)), self._emit_changed())
        )
        y_form.addRow("Font Size", y_size_spin)

        auto_chk = QCheckBox("Auto range")
        auto_chk.setChecked(self._cfg.y_auto)
        y_form.addRow("", auto_chk)

        min_spin = _DSpin()
        min_spin.setDecimals(4)
        min_spin.setRange(-1e9, 1e9)
        min_spin.setValue(0.0 if self._cfg.y_min is None else float(self._cfg.y_min))
        y_form.addRow("Min", min_spin)

        max_spin = _DSpin()
        max_spin.setDecimals(4)
        max_spin.setRange(-1e9, 1e9)
        max_spin.setValue(1.0 if self._cfg.y_max is None else float(self._cfg.y_max))
        y_form.addRow("Max", max_spin)

        major_spin = _DSpin()
        major_spin.setDecimals(4)
        major_spin.setRange(0.0, 1e9)
        major_spin.setValue(0.0 if self._cfg.y_major_interval is None else float(self._cfg.y_major_interval))
        y_form.addRow("Major Interval", major_spin)

        minor_spin = _DSpin()
        minor_spin.setDecimals(4)
        minor_spin.setRange(0.0, 1e9)
        minor_spin.setValue(0.0 if self._cfg.y_minor_interval is None else float(self._cfg.y_minor_interval))
        y_form.addRow("Minor Interval", minor_spin)

        y_thick_spin = _DSpin()
        y_thick_spin.setRange(0.5, 5.0)
        y_thick_spin.setSingleStep(0.25)
        y_thick_spin.setDecimals(1)
        y_thick_spin.setValue(float(self._cfg.y_axis_thickness))
        y_thick_spin.valueChanged.connect(
            lambda value: (setattr(self._cfg, "y_axis_thickness", float(value)), self._emit_changed())
        )
        y_form.addRow("Axis Thickness", y_thick_spin)

        tick_size_spin = _DSpin()
        tick_size_spin.setRange(6, 32)
        tick_size_spin.setSingleStep(1)
        tick_size_spin.setDecimals(0)
        tick_size_spin.setValue(float(self._cfg.tick_label_font_size))
        tick_size_spin.valueChanged.connect(
            lambda value: (setattr(self._cfg, "tick_label_font_size", int(value)), self._emit_changed())
        )
        y_form.addRow("Tick Font Size", tick_size_spin)

        minor_labels_chk = QCheckBox("Show minor tick labels")
        minor_labels_chk.setChecked(self._cfg.show_minor_tick_labels)
        minor_labels_chk.toggled.connect(
            lambda checked: (setattr(self._cfg, "show_minor_tick_labels", bool(checked)), self._emit_changed())
        )
        y_form.addRow("", minor_labels_chk)

        def _update_enabled() -> None:
            manual = not auto_chk.isChecked()
            min_spin.setEnabled(manual)
            max_spin.setEnabled(manual)

        def _apply() -> None:
            self._cfg.y_auto = bool(auto_chk.isChecked())
            self._cfg.y_min = float(min_spin.value())
            self._cfg.y_max = float(max_spin.value())
            self._cfg.y_major_interval = None if major_spin.value() <= 0 else float(major_spin.value())
            self._cfg.y_minor_interval = None if minor_spin.value() <= 0 else float(minor_spin.value())
            _update_enabled()
            self._emit_changed()

        auto_chk.toggled.connect(lambda _checked: _apply())
        min_spin.valueChanged.connect(lambda _value: _apply())
        max_spin.valueChanged.connect(lambda _value: _apply())
        major_spin.valueChanged.connect(lambda _value: _apply())
        minor_spin.valueChanged.connect(lambda _value: _apply())
        _update_enabled()
        root.addLayout(y_form)
        return panel

    def _build_frame_panel(self) -> QWidget:
        panel = QWidget()
        form = QFormLayout(panel)
        form.setContentsMargins(6, 6, 6, 6)
        form.setSpacing(8)

        # Frame style is fixed to "left_bottom" (Left and Bottom axes) — no selector shown.
        self._cfg.frame_style = "left_bottom"

        major_enable = QCheckBox("Show major grid")
        major_enable.setChecked(self._cfg.show_major_grid)
        form.addRow("Major Grid", major_enable)

        major_color_btn = QPushButton("Major Grid Color")
        self._color_buttons["major_grid_color"] = major_color_btn
        self._update_color_button("major_grid_color")
        major_color_btn.clicked.connect(lambda: self._pick_color("major_grid_color"))
        form.addRow("", major_color_btn)

        major_width_spin = _DSpin()
        major_width_spin.setRange(0.1, 5.0)
        major_width_spin.setSingleStep(0.1)
        major_width_spin.setDecimals(1)
        major_width_spin.setValue(float(self._cfg.major_grid_width))
        major_width_spin.valueChanged.connect(
            lambda value: (setattr(self._cfg, "major_grid_width", float(value)), self._emit_changed())
        )
        form.addRow("Major Width", major_width_spin)

        major_style = QComboBox()
        major_style.addItem("Solid", "solid")
        major_style.addItem("Dashed", "dashed")
        major_style.addItem("Dotted", "dotted")
        major_style.setCurrentIndex(max(0, major_style.findData(self._cfg.major_grid_style)))
        major_style.currentIndexChanged.connect(
            lambda _i: (setattr(self._cfg, "major_grid_style", str(major_style.currentData())), self._emit_changed())
        )
        form.addRow("Major Style", major_style)

        minor_enable = QCheckBox("Show minor grid")
        minor_enable.setChecked(self._cfg.show_minor_grid)
        form.addRow("Minor Grid", minor_enable)

        minor_color_btn = QPushButton("Minor Grid Color")
        self._color_buttons["minor_grid_color"] = minor_color_btn
        self._update_color_button("minor_grid_color")
        minor_color_btn.clicked.connect(lambda: self._pick_color("minor_grid_color"))
        form.addRow("", minor_color_btn)

        minor_width_spin = _DSpin()
        minor_width_spin.setRange(0.1, 5.0)
        minor_width_spin.setSingleStep(0.1)
        minor_width_spin.setDecimals(1)
        minor_width_spin.setValue(float(self._cfg.minor_grid_width))
        minor_width_spin.valueChanged.connect(
            lambda value: (setattr(self._cfg, "minor_grid_width", float(value)), self._emit_changed())
        )
        form.addRow("Minor Width", minor_width_spin)

        minor_style = QComboBox()
        minor_style.addItem("Solid", "solid")
        minor_style.addItem("Dashed", "dashed")
        minor_style.addItem("Dotted", "dotted")
        minor_style.setCurrentIndex(max(0, minor_style.findData(self._cfg.minor_grid_style)))
        minor_style.currentIndexChanged.connect(
            lambda _i: (setattr(self._cfg, "minor_grid_style", str(minor_style.currentData())), self._emit_changed())
        )
        form.addRow("Minor Style", minor_style)

        def _set_major_controls_enabled(enabled: bool) -> None:
            major_color_btn.setEnabled(enabled)
            major_width_spin.setEnabled(enabled)
            major_style.setEnabled(enabled)

        def _set_minor_controls_enabled(enabled: bool) -> None:
            minor_color_btn.setEnabled(enabled)
            minor_width_spin.setEnabled(enabled)
            minor_style.setEnabled(enabled)

        def _on_major_toggle(checked: bool) -> None:
            self._cfg.show_major_grid = bool(checked)
            _set_major_controls_enabled(bool(checked))
            self._emit_changed()

        def _on_minor_toggle(checked: bool) -> None:
            self._cfg.show_minor_grid = bool(checked)
            _set_minor_controls_enabled(bool(checked))
            self._emit_changed()

        major_enable.toggled.connect(_on_major_toggle)
        minor_enable.toggled.connect(_on_minor_toggle)
        _set_major_controls_enabled(self._cfg.show_major_grid)
        _set_minor_controls_enabled(self._cfg.show_minor_grid)
        return panel

    def _build_style_panel(self) -> QWidget:
        panel = QWidget()
        form = QFormLayout(panel)
        form.setContentsMargins(6, 6, 6, 6)
        form.setSpacing(8)

        width_spin = _DSpin()
        width_spin.setRange(0.1, 0.95)
        width_spin.setSingleStep(0.02)
        width_spin.setDecimals(2)
        width_spin.setValue(float(self._cfg.bar_width))
        width_spin.valueChanged.connect(lambda value: (setattr(self._cfg, "bar_width", float(value)), self._emit_changed()))
        form.addRow("Bar Width", width_spin)

        err_chk = QCheckBox("Show error bars")
        err_chk.setChecked(self._cfg.show_error)
        err_chk.toggled.connect(lambda checked: (setattr(self._cfg, "show_error", bool(checked)), self._emit_changed()))
        form.addRow("", err_chk)

        err_thick_spin = _DSpin()
        err_thick_spin.setRange(0.5, 4.0)
        err_thick_spin.setSingleStep(0.1)
        err_thick_spin.setDecimals(1)
        err_thick_spin.setValue(float(self._cfg.error_thickness))
        err_thick_spin.valueChanged.connect(
            lambda value: (setattr(self._cfg, "error_thickness", float(value)), self._emit_changed())
        )
        form.addRow("Error Thickness", err_thick_spin)

        scatter_chk = QCheckBox("Show scatter points")
        scatter_chk.setChecked(self._cfg.show_scatter)
        scatter_chk.toggled.connect(lambda checked: (setattr(self._cfg, "show_scatter", bool(checked)), self._emit_changed()))
        form.addRow("", scatter_chk)

        scatter_size_spin = _DSpin()
        scatter_size_spin.setRange(2.0, 14.0)
        scatter_size_spin.setSingleStep(0.5)
        scatter_size_spin.setDecimals(1)
        scatter_size_spin.setValue(float(self._cfg.scatter_size))
        scatter_size_spin.valueChanged.connect(
            lambda value: (setattr(self._cfg, "scatter_size", float(value)), self._emit_changed())
        )
        form.addRow("Scatter Size", scatter_size_spin)

        def _mk_button(key: str, label: str) -> None:
            btn = QPushButton(label)
            self._color_buttons[key] = btn
            self._update_color_button(key)
            btn.clicked.connect(lambda: self._pick_color(key))
            form.addRow(label, btn)

        _mk_button("bar", "Bar Color")
        _mk_button("error", "Error Color")
        _mk_button("scatter", "Scatter Color")
        return panel

    def _update_color_button(self, key: str) -> None:
        btn = self._color_buttons.get(key)
        if key in self._cfg.colors:
            color = self._cfg.colors.get(key, QColor("#000000"))
        else:
            color = getattr(self._cfg, key, QColor("#000000"))
        if btn is not None:
            btn.setStyleSheet(
                "QPushButton {"
                f"background-color: {color.name()};"
                "border: 1px solid #9FB1BF;"
                "border-radius: 5px;"
                "min-height: 26px;"
                "}"
            )

    def _pick_color(self, key: str) -> None:
        if key in self._cfg.colors:
            current = self._cfg.colors.get(key, QColor("#000000"))
        else:
            current = getattr(self._cfg, key, QColor("#000000"))
        chosen = QColorDialog.getColor(current, self, "Select Color")
        if not chosen.isValid():
            return
        if key in self._cfg.colors:
            self._cfg.colors[key] = QColor(chosen)
        else:
            setattr(self._cfg, key, QColor(chosen))
        self._update_color_button(key)
        self._emit_changed()


class _PrismBarPlotWidget(QWidget):
    clicked = Signal()
    openControlPanel = Signal(str)
    styleEdited = Signal()

    def __init__(
        self,
        *,
        group_names: list[str],
        grouped_values: list[list[float]],
        means: list[float],
        errors: list[float],
        style_config: FigureStyleConfig | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._group_names = group_names
        self._grouped_values = grouped_values
        self._means = means
        self._errors = errors
        self._style_config = style_config or FigureStyleConfig()
        self._title_hit_rect = QRect()
        self._x_title_hit_rect = QRect()
        self._y_title_hit_rect = QRect()
        self._x_axis_hit_rect = QRect()
        self._y_axis_hit_rect = QRect()
        self._bar_hit_rects: list[QRect] = []
        self._selected_text_object: str | None = None
        # Axis resize handle state (Prism-style)
        self._axis_handle_mode: str | None = None   # "x" or "y"
        self._axis_drag_active = False
        self._axis_drag_handle: str | None = None   # "start" or "end"
        self._axis_drag_start_pos: QPoint | None = None
        self._axis_drag_start_size: int = 0
        self._x_handle_rects: list[QRect] = [QRect(), QRect()]  # left, right
        self._y_handle_rects: list[QRect] = [QRect(), QRect()]  # top, bottom
        self._plot_rect_cache: QRect = QRect()  # cached plot area for immediate handle calc
        self.setMinimumSize(760, 460)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

    def _update_handle_rects(self) -> None:
        """Compute handle positions via a fresh compute_layout call.

        This avoids relying on _plot_rect_cache which may be stale (e.g. after a
        zoom change or before the first repaint after widget creation).
        """
        # Replicate just enough of paintEvent to get the plot QRect.
        peak_values: list[float] = []
        for mean, err in zip(self._means, self._errors):
            peak_values.append(max(0.0, mean + err))
        for vals in self._grouped_values:
            for v in vals:
                peak_values.append(float(v))
        y_min, y_max = self._resolve_y_range(peak_values)
        y_range = max(1e-9, y_max - y_min)
        major_ticks = self._generate_ticks(y_min, y_max, self._style_config.y_major_interval)
        if not major_ticks:
            ticks = 5
            major_ticks = [y_min + ((y_range * i) / ticks) for i in range(ticks + 1)]
        layout = self.compute_layout(self._style_config, self.size(), major_ticks)
        p = layout["plot"]
        if p.isNull() or p.width() <= 10 or p.height() <= 10:
            return
        _HS = 8
        self._x_handle_rects[0] = QRect(p.left() - _HS // 2,  p.bottom() - _HS // 2, _HS, _HS)
        self._x_handle_rects[1] = QRect(p.right() - _HS // 2, p.bottom() - _HS // 2, _HS, _HS)
        self._y_handle_rects[0] = QRect(p.left() - _HS // 2,  p.top()    - _HS // 2, _HS, _HS)
        self._y_handle_rects[1] = QRect(p.left() - _HS // 2,  p.bottom() - _HS // 2, _HS, _HS)
        self._plot_rect_cache = QRect(p)

    def mousePressEvent(self, event) -> None:
        self.clicked.emit()

        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()

            # 1. If handles are already visible, check for drag-start on a handle first.
            if self._axis_handle_mode == "x":
                for i, r in enumerate(self._x_handle_rects):
                    if r.adjusted(-5, -5, 5, 5).contains(pos):
                        self._axis_drag_active = True
                        self._axis_drag_handle = "start" if i == 0 else "end"
                        self._axis_drag_start_pos = pos
                        self._axis_drag_start_size = int(self._style_config.plot_width)
                        event.accept()
                        return
            elif self._axis_handle_mode == "y":
                for i, r in enumerate(self._y_handle_rects):
                    if r.adjusted(-5, -5, 5, 5).contains(pos):
                        self._axis_drag_active = True
                        self._axis_drag_handle = "start" if i == 0 else "end"
                        self._axis_drag_start_pos = pos
                        self._axis_drag_start_size = int(self._style_config.plot_height)
                        event.accept()
                        return

            # 2. Left-click on the Y axis area → show Y handles (check Y before X to
            #    avoid the corner ambiguity at plot.left(), plot.bottom()).
            if self._y_axis_hit_rect.contains(pos):
                new_mode = "y" if self._axis_handle_mode != "y" else None
                self._axis_handle_mode = new_mode
                if new_mode:
                    self._update_handle_rects()
                self.update()
                event.accept()
                return

            # 3. Left-click on the X axis area → show X handles.
            if self._x_axis_hit_rect.contains(pos):
                new_mode = "x" if self._axis_handle_mode != "x" else None
                self._axis_handle_mode = new_mode
                if new_mode:
                    self._update_handle_rects()
                self.update()
                event.accept()
                return

            # 4. Click elsewhere → dismiss handles.
            if self._axis_handle_mode is not None:
                self._axis_handle_mode = None
                self.update()

            # 5. Regular left-click handling (text labels, bars, etc.).
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            if self._title_hit_rect.contains(pos):
                self._selected_text_object = "title"
                self.update()
                event.accept()
                return
            if self._x_title_hit_rect.contains(pos):
                self._selected_text_object = "x_title"
                self.update()
                event.accept()
                return
            if self._y_title_hit_rect.contains(pos):
                self._selected_text_object = "y_title"
                self.update()
                event.accept()
                return
            for bar_rect in self._bar_hit_rects:
                if bar_rect.contains(pos):
                    self.openControlPanel.emit("style")
                    self._selected_text_object = None
                    self.update()
                    event.accept()
                    return
            self._selected_text_object = None
            self.update()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._axis_drag_active and self._axis_drag_start_pos is not None:
            pos = event.position().toPoint()
            if self._axis_handle_mode == "x":
                delta = pos.x() - self._axis_drag_start_pos.x()
                # right handle → drag right = wider; left handle → drag left = wider
                if self._axis_drag_handle == "end":
                    new_size = max(80, self._axis_drag_start_size + delta)
                else:
                    new_size = max(80, self._axis_drag_start_size - delta)
                self._style_config.plot_width = float(new_size)
                self.update()
                self.styleEdited.emit()
            elif self._axis_handle_mode == "y":
                delta = pos.y() - self._axis_drag_start_pos.y()
                # bottom handle → drag down = taller; top handle → drag up = taller
                if self._axis_drag_handle == "end":
                    new_size = max(80, self._axis_drag_start_size + delta)
                else:
                    new_size = max(80, self._axis_drag_start_size - delta)
                self._style_config.plot_height = float(new_size)
                self.update()
                self.styleEdited.emit()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._axis_drag_active:
            self._axis_drag_active = False
            self._axis_drag_handle = None
            self._axis_drag_start_pos = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        self.clicked.emit()
        pos = event.position().toPoint()
        if self._title_hit_rect.contains(pos):
            self.openControlPanel.emit("general")
        elif self._y_axis_hit_rect.contains(pos):
            self.openControlPanel.emit("axis")
        elif self._x_axis_hit_rect.contains(pos):
            self.openControlPanel.emit("axis")
        else:
            self.openControlPanel.emit("general")
        event.accept()
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event) -> None:
        if self._selected_text_object is None:
            super().keyPressEvent(event)
            return
        step = 2
        dx = 0
        dy = 0
        if event.key() == Qt.Key.Key_Left:
            dx = -step
        elif event.key() == Qt.Key.Key_Right:
            dx = step
        elif event.key() == Qt.Key.Key_Up:
            dy = -step
        elif event.key() == Qt.Key.Key_Down:
            dy = step
        else:
            super().keyPressEvent(event)
            return
        if self._selected_text_object == "title":
            self._style_config.title_offset_x += dx
            self._style_config.title_offset_y += dy
        elif self._selected_text_object == "x_title":
            self._style_config.x_title_offset_x += dx
            self._style_config.x_title_offset_y += dy
        elif self._selected_text_object == "y_title":
            self._style_config.y_title_offset_x += dx
            self._style_config.y_title_offset_y += dy
        self.update()
        self.styleEdited.emit()
        event.accept()

    @staticmethod
    def _axis_max(values: list[float]) -> float:
        if not values:
            return 1.0
        peak = max(values)
        if peak <= 0:
            return 1.0
        magnitude = 10 ** math.floor(math.log10(peak))
        scaled = peak / magnitude
        if scaled <= 1:
            nice = 1
        elif scaled <= 2:
            nice = 2
        elif scaled <= 5:
            nice = 5
        else:
            nice = 10
        return nice * magnitude

    def _resolve_y_range(self, peak_values: list[float]) -> tuple[float, float]:
        cfg = self._style_config
        if not cfg.y_auto:
            auto_max = max(1.0, self._axis_max([v for v in peak_values if v > 0] or [1.0]) * 1.08)
            min_v = 0.0 if cfg.y_min is None else float(cfg.y_min)
            max_v = auto_max if cfg.y_max is None else float(cfg.y_max)
            if max_v <= min_v:
                min_v, max_v = 0.0, auto_max
            return min_v, max_v
        # Auto mode — include negatives
        all_vals: list[float] = list(peak_values)
        for mean, err in zip(self._means, self._errors):
            all_vals.append(mean - err)
        for vals in self._grouped_values:
            all_vals.extend(vals)
        if not all_vals:
            return 0.0, 1.0
        raw_max = max(all_vals)
        raw_min = min(all_vals)
        auto_max = self._axis_max([raw_max]) * 1.08 if raw_max > 0 else 0.0
        auto_min = -self._axis_max([-raw_min]) * 1.08 if raw_min < 0 else 0.0
        auto_max = max(auto_max, 1.0) if auto_min == 0.0 else auto_max
        return auto_min, auto_max

    @staticmethod
    def _pen_style(name: str) -> Qt.PenStyle:
        if name == "dashed":
            return Qt.PenStyle.DashLine
        if name == "dotted":
            return Qt.PenStyle.DotLine
        return Qt.PenStyle.SolidLine

    def _generate_ticks(self, y_min: float, y_max: float, interval: float | None) -> list[float]:
        if interval is None or interval <= 0:
            return []
        ticks: list[float] = []
        eps = max(1e-12, abs(interval) * 1e-9)
        start = math.ceil((y_min - eps) / interval) * interval
        value = start
        guard = 0
        while value <= y_max + eps:
            if value >= y_min - eps:
                ticks.append(float(value))
            value += interval
            guard += 1
            if guard > 2000:
                break
        return ticks

    def _generate_minor_ticks(
        self,
        y_min: float,
        y_max: float,
        minor_interval: float | None,
        major_ticks: list[float],
        major_interval: float | None,
    ) -> list[float]:
        if minor_interval is None or minor_interval <= 0:
            return []
        if major_interval is not None and major_interval > 0 and minor_interval >= major_interval:
            return []
        minor_ticks = self._generate_ticks(y_min, y_max, minor_interval)
        major_set = {round(v, 10) for v in major_ticks}
        return [v for v in minor_ticks if round(v, 10) not in major_set]

    @staticmethod
    def _align_for(text_align: str) -> Qt.AlignmentFlag:
        return {
            "left": Qt.AlignmentFlag.AlignLeft,
            "center": Qt.AlignmentFlag.AlignHCenter,
            "right": Qt.AlignmentFlag.AlignRight,
        }.get(text_align, Qt.AlignmentFlag.AlignHCenter)

    def _font(self, size: int, bold: bool = False) -> QFont:
        font = QFont(self._style_config.font_family or "Arial")
        font.setPointSize(max(7, int(size)))
        font.setBold(bool(bold))
        return font

    def compute_layout(
        self,
        config: FigureStyleConfig,
        size: QSize,
        major_ticks: list[float] | None = None,
    ) -> dict[str, QRect]:
        major_ticks = major_ticks or []
        tick_font = self._font(config.tick_label_font_size, False)
        tick_metrics = QFontMetrics(tick_font)
        y_labels = [f"{tick:.2f}" for tick in major_ticks] if major_ticks else ["0.00"]
        max_y_label_width = max([tick_metrics.horizontalAdvance(label) for label in y_labels] + [16])

        y_title_space = 0
        if config.y_title.strip():
            y_title_metrics = QFontMetrics(self._font(config.y_title_font_size, config.y_title_bold))
            y_title_space = y_title_metrics.height() + 20

        title_height = 0
        if config.title.strip():
            title_height = QFontMetrics(self._font(config.title_font_size, config.title_bold)).height() + 8

        max_x_label = ""
        if self._group_names:
            max_x_label = max(self._group_names, key=len)
        x_tick_metrics = QFontMetrics(self._font(config.tick_label_font_size, False))
        x_tick_h = 0
        if config.show_x_tick_labels and self._group_names:
            rot_deg = int(config.x_tick_rotation)
            if rot_deg == 0:
                x_tick_h = x_tick_metrics.height() + 8
            else:
                # For arbitrary angles: vertical space = |label_w * sin(θ)| + |text_h * cos(θ)|
                rad = math.radians(abs(rot_deg))
                label_w = x_tick_metrics.horizontalAdvance(max_x_label)
                label_h = x_tick_metrics.height()
                x_tick_h = int(abs(label_w * math.sin(rad)) + abs(label_h * math.cos(rad))) + 8

        x_title_h = 0
        if config.x_title.strip():
            x_title_h = QFontMetrics(self._font(config.x_title_font_size, config.x_title_bold)).height() + 10

        left_margin = 16 + y_title_space + max_y_label_width + 8 + 6
        top_margin = 12 + title_height
        bottom_margin = 14 + x_tick_h + x_title_h
        right_margin = 16

        available_w = max(20, size.width() - left_margin - right_margin)
        available_h = max(20, size.height() - top_margin - bottom_margin)
        desired_w = max(20, int(config.plot_width))
        desired_h = max(20, int(config.plot_height))
        plot_w = min(available_w, desired_w)
        plot_h = min(available_h, desired_h)
        plot_left = left_margin + max(0, (available_w - plot_w) // 2)
        plot_top = top_margin + max(0, (available_h - plot_h) // 2)

        plot = QRect(plot_left, plot_top, plot_w, plot_h)
        if plot.width() < 20 or plot.height() < 20:
            return {
                "plot": QRect(),
                "title_rect": QRect(),
                "x_title_rect": QRect(),
                "y_title_rect": QRect(),
                "x_axis_hit_rect": QRect(),
                "y_axis_hit_rect": QRect(),
            }

        title_rect = QRect(plot.left(), 4, plot.width(), max(18, title_height))
        title_rect.translate(int(config.title_offset_x), int(config.title_offset_y))

        x_title_rect = QRect(
            plot.left(),
            plot.bottom() + 6 + x_tick_h,
            plot.width(),
            max(18, x_title_h),
        )
        x_title_rect.translate(int(config.x_title_offset_x), int(config.x_title_offset_y))

        y_axis_x = plot.left()
        y_label_left = y_axis_x - 6 - 8 - max_y_label_width
        y_title_metrics = QFontMetrics(self._font(config.y_title_font_size, config.y_title_bold))
        y_title_center_x = y_label_left - 20 - max(8, y_title_metrics.height() // 2)
        y_title_center_y = plot.center().y()
        y_title_rect = QRect(
            int(y_title_center_x - max(8, y_title_metrics.height() // 2)),
            int(y_title_center_y - max(10, y_title_metrics.horizontalAdvance(config.y_title) // 2)),
            max(16, y_title_metrics.height()),
            max(20, y_title_metrics.horizontalAdvance(config.y_title)),
        )
        y_title_rect.translate(int(config.y_title_offset_x), int(config.y_title_offset_y))

        return {
            "plot": plot,
            "title_rect": title_rect,
            "x_title_rect": x_title_rect,
            "y_title_rect": y_title_rect,
            "x_axis_hit_rect": QRect(plot.left(), plot.bottom() - 2, plot.width(), max(1, size.height() - plot.bottom() + 2)),
            "y_axis_hit_rect": QRect(0, plot.top(), max(1, plot.left() + 10), plot.height()),
        }

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#FFFFFF"))

        peak_values: list[float] = []
        for mean, err in zip(self._means, self._errors):
            peak_values.append(max(0.0, mean + err))
        for vals in self._grouped_values:
            for v in vals:
                peak_values.append(float(v))
        y_min, y_max = self._resolve_y_range(peak_values)
        y_range = max(1e-9, y_max - y_min)

        major_ticks = self._generate_ticks(y_min, y_max, self._style_config.y_major_interval)
        if not major_ticks:
            ticks = 5
            major_ticks = [y_min + ((y_range * i) / ticks) for i in range(ticks + 1)]

        minor_ticks = self._generate_minor_ticks(
            y_min,
            y_max,
            self._style_config.y_minor_interval,
            major_ticks,
            self._style_config.y_major_interval,
        )

        layout = self.compute_layout(self._style_config, self.size(), major_ticks)
        plot = layout["plot"]
        if plot.isNull() or plot.width() <= 10 or plot.height() <= 10:
            return

        axis_color = QColor("#333333")
        x_axis_y = plot.bottom()
        y_axis_x = plot.left()
        tick_len = 6
        tick_label_gap = 8
        y_label_font = self._font(self._style_config.tick_label_font_size, False)
        y_label_metrics = QFontMetrics(y_label_font)

        def y_to_px(value: float) -> float:
            clamped = max(y_min, min(float(value), y_max))
            return plot.bottom() - ((clamped - y_min) / y_range) * plot.height()

        if self._style_config.show_minor_grid and minor_ticks:
            minor_color = QColor(self._style_config.minor_grid_color)
            minor_color.setAlpha(150)
            minor_pen = QPen(
                minor_color,
                max(0.1, float(self._style_config.minor_grid_width)),
            )
            minor_pen.setStyle(self._pen_style(self._style_config.minor_grid_style))
            painter.setPen(minor_pen)
            for tick_val in minor_ticks:
                y = y_to_px(tick_val)
                painter.drawLine(plot.left(), int(y), plot.right(), int(y))

        if self._style_config.show_major_grid:
            major_pen = QPen(
                self._style_config.major_grid_color,
                max(0.1, float(self._style_config.major_grid_width)),
            )
            major_pen.setStyle(self._pen_style(self._style_config.major_grid_style))
            painter.setPen(major_pen)
            for tick_val in major_ticks:
                y = y_to_px(tick_val)
                painter.drawLine(plot.left(), int(y), plot.right(), int(y))

        y_ax_pen = QPen(axis_color, max(0.5, float(self._style_config.y_axis_thickness)))
        y_ax_pen.setStyle(Qt.PenStyle.SolidLine)
        x_ax_pen = QPen(axis_color, max(0.5, float(self._style_config.x_axis_thickness)))
        x_ax_pen.setStyle(Qt.PenStyle.SolidLine)
        frame_style = self._style_config.frame_style
        if frame_style == "box_frame":
            painter.setPen(x_ax_pen)
            painter.drawRect(plot)
        elif frame_style == "left_bottom":
            painter.setPen(y_ax_pen)
            painter.drawLine(plot.left(), plot.top(), plot.left(), plot.bottom())
            painter.setPen(x_ax_pen)
            painter.drawLine(plot.left(), plot.bottom(), plot.right(), plot.bottom())

        # Draw zero baseline if range spans y=0
        if y_min < 0 < y_max:
            zero_y = int(y_to_px(0.0))
            zero_pen = QPen(axis_color, max(0.5, float(self._style_config.x_axis_thickness)))
            zero_pen.setStyle(Qt.PenStyle.SolidLine)
            painter.setPen(zero_pen)
            painter.drawLine(plot.left(), zero_y, plot.right(), zero_y)

        painter.setFont(y_label_font)
        painter.setPen(QPen(axis_color, 1.0))
        for tick_val in major_ticks:
            y = y_to_px(tick_val)
            painter.drawLine(int(y_axis_x), int(y), int(y_axis_x - tick_len), int(y))
            label = f"{tick_val:.2f}"
            label_w = y_label_metrics.horizontalAdvance(label)
            text_x = int(y_axis_x - tick_len - tick_label_gap - label_w)
            painter.drawText(text_x, int(y + (y_label_metrics.ascent() / 2.0)), label)
        for tick_val in minor_ticks:
            y = y_to_px(tick_val)
            painter.drawLine(int(y_axis_x), int(y), int(y_axis_x - max(2, int(tick_len * 0.55))), int(y))

        if self._style_config.show_minor_tick_labels and minor_ticks:
            minor_label_font = self._font(max(6, int(self._style_config.tick_label_font_size) - 2), False)
            minor_label_metrics = QFontMetrics(minor_label_font)
            painter.setFont(minor_label_font)
            painter.setPen(QPen(axis_color, 0.8))
            for tick_val in minor_ticks:
                y = y_to_px(tick_val)
                label = f"{tick_val:.2f}"
                label_w = minor_label_metrics.horizontalAdvance(label)
                text_x = int(y_axis_x - 3 - label_w)
                painter.drawText(text_x, int(y + (minor_label_metrics.ascent() / 2.0)), label)

        if not self._group_names:
            return

        slot_w = plot.width() / max(len(self._group_names), 1)
        bar_w = slot_w * max(0.1, min(0.95, float(self._style_config.bar_width)))

        # Bars, error bars, replicate points.
        self._bar_hit_rects = []
        for idx, name in enumerate(self._group_names):
            cx = plot.left() + (idx + 0.5) * slot_w
            mean = float(self._means[idx])
            err = float(self._errors[idx])
            bar_top = y_to_px(mean)
            baseline_px = y_to_px(max(y_min, 0.0))
            bar_bottom = baseline_px
            bar_left = cx - (bar_w / 2)

            painter.setPen(QPen(QColor("#4D5F6D"), 1.0))
            painter.setBrush(QBrush(self._style_config.colors.get("bar", QColor("#808080"))))
            bar_rect_top = int(min(bar_top, bar_bottom))
            bar_rect_height = int(abs(bar_bottom - bar_top))
            painter.drawRect(int(bar_left), bar_rect_top, int(bar_w), bar_rect_height)
            self._bar_hit_rects.append(
                QRect(int(bar_left), bar_rect_top, int(max(1, bar_w)), int(max(1, bar_rect_height)))
            )

            # Error bars (mean ± SD).
            if self._style_config.show_error and err > 0:
                y_low = y_to_px(max(y_min, mean - err))
                y_high = y_to_px(mean + err)
                painter.setPen(
                    QPen(
                        self._style_config.colors.get("error", QColor("#2D3B46")),
                        max(0.5, float(self._style_config.error_thickness)),
                    )
                )
                painter.drawLine(int(cx), int(y_low), int(cx), int(y_high))
                cap = max(4, int(bar_w * 0.18))
                painter.drawLine(int(cx - cap), int(y_low), int(cx + cap), int(y_low))
                painter.drawLine(int(cx - cap), int(y_high), int(cx + cap), int(y_high))

            points = self._grouped_values[idx]
            if self._style_config.show_scatter and points:
                if len(points) == 1:
                    offsets = [0.0]
                else:
                    spread = bar_w * 0.34
                    offsets = np.linspace(-spread / 2.0, spread / 2.0, num=len(points)).tolist()
                painter.setPen(QPen(self._style_config.colors.get("scatter", QColor("#1F2A32")), 1.0))
                painter.setBrush(QBrush(QColor("#FDFDFD")))
                point_d = max(2.0, float(self._style_config.scatter_size))
                for off, val in zip(offsets, points):
                    px = cx + off
                    py = y_to_px(val)
                    painter.drawEllipse(
                        int(px - (point_d / 2.0)),
                        int(py - (point_d / 2.0)),
                        int(point_d),
                        int(point_d),
                    )

            painter.setPen(QPen(axis_color, 1.0))
            painter.drawLine(int(cx), int(x_axis_y), int(cx), int(x_axis_y + tick_len))

            if self._style_config.show_x_tick_labels:
                tick_font = self._font(self._style_config.tick_label_font_size, False)
                painter.setFont(tick_font)
                painter.setPen(QPen(axis_color, 1.0))
                label_rect = QRect(int(cx - (slot_w / 2)), int(x_axis_y + tick_len + 2), int(slot_w), 48)
                rotation = int(self._style_config.x_tick_rotation)
                if rotation == 0:
                    painter.drawText(
                        label_rect,
                        Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter,
                        name,
                    )
                else:
                    painter.save()
                    anchor = QPoint(int(cx), int(x_axis_y + tick_len + 2))
                    painter.translate(anchor)
                    painter.rotate(-rotation)
                    painter.drawText(QRect(-int(slot_w / 2), -8, int(slot_w), 32), Qt.AlignmentFlag.AlignLeft, name)
                    painter.restore()

        if self._style_config.x_title.strip():
            x_font = self._font(self._style_config.x_title_font_size, self._style_config.x_title_bold)
            painter.setFont(x_font)
            painter.setPen(QPen(axis_color, 1.0))
            painter.drawText(
                layout["x_title_rect"],
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                self._style_config.x_title,
            )
            self._x_title_hit_rect = layout["x_title_rect"].adjusted(-6, -4, 6, 4)
        else:
            self._x_title_hit_rect = QRect()

        if self._style_config.y_title.strip():
            y_font = self._font(self._style_config.y_title_font_size, self._style_config.y_title_bold)
            painter.save()
            painter.setFont(y_font)
            painter.setPen(QPen(axis_color, 1.0))
            y_title_rect = layout["y_title_rect"]
            anchor_x = y_title_rect.center().x()
            anchor_y = y_title_rect.center().y()
            painter.translate(anchor_x, anchor_y)
            painter.rotate(-90)
            painter.drawText(
                QRect(-plot.height() // 2, -14, plot.height(), 24),
                Qt.AlignmentFlag.AlignCenter,
                self._style_config.y_title,
            )
            painter.restore()
            self._y_title_hit_rect = y_title_rect.adjusted(-6, -4, 6, 4)
        else:
            self._y_title_hit_rect = QRect()

        title_text = self._style_config.title.strip()
        if title_text:
            title_font = self._font(self._style_config.title_font_size, self._style_config.title_bold)
            painter.setFont(title_font)
            painter.setPen(QPen(axis_color, 1.0))
            title_align = self._align_for(self._style_config.title_alignment)
            title_rect = layout["title_rect"]
            painter.drawText(title_rect, title_align | Qt.AlignmentFlag.AlignVCenter, title_text)
            self._title_hit_rect = title_rect.adjusted(-8, -4, 8, 4)
        else:
            self._title_hit_rect = QRect()

        self._y_axis_hit_rect = layout["y_axis_hit_rect"]
        self._x_axis_hit_rect = layout["x_axis_hit_rect"]
        self._plot_rect_cache = QRect(plot)  # keep for synchronous handle rect calculation

        # --- Draw axis resize handles (Prism-style) ---
        _HS = 8  # handle size in pixels
        _handle_pen = QPen(QColor("#4A6A8A"), 1.5)
        _handle_brush = QBrush(QColor("#B8D0E8"))
        if self._axis_handle_mode == "x" and not plot.isNull():
            lx, rx, ay = plot.left(), plot.right(), plot.bottom()
            self._x_handle_rects[0] = QRect(lx - _HS // 2, ay - _HS // 2, _HS, _HS)
            self._x_handle_rects[1] = QRect(rx - _HS // 2, ay - _HS // 2, _HS, _HS)
            painter.setPen(_handle_pen)
            painter.setBrush(_handle_brush)
            for r in self._x_handle_rects:
                painter.drawRect(r)
        elif self._axis_handle_mode == "y" and not plot.isNull():
            ax, ty, by = plot.left(), plot.top(), plot.bottom()
            self._y_handle_rects[0] = QRect(ax - _HS // 2, ty - _HS // 2, _HS, _HS)
            self._y_handle_rects[1] = QRect(ax - _HS // 2, by - _HS // 2, _HS, _HS)
            painter.setPen(_handle_pen)
            painter.setBrush(_handle_brush)
            for r in self._y_handle_rects:
                painter.drawRect(r)

        selected_rect = {
            "title": self._title_hit_rect,
            "x_title": self._x_title_hit_rect,
            "y_title": self._y_title_hit_rect,
        }.get(self._selected_text_object or "", QRect())
        if not selected_rect.isNull():
            sel_pen = QPen(QColor("#D87093"), 1.0)
            sel_pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(sel_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(selected_rect)


class _FiguresDialog(QDialog):
    def __init__(
        self,
        *,
        group_names: list[str],
        grouped_values: list[list[float]],
        means: list[float],
        errors: list[float],
        negative_control_name: str,
        baseline_mean: float,
        style_config: FigureStyleConfig | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._language = LANG_EN
        self._negative_control_name = negative_control_name
        self._baseline_mean = baseline_mean
        self.setWindowTitle("Figures Generation")
        self.resize(840, 560)
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self._note = QLabel()
        self._note.setStyleSheet("font-size: 11px; color: #4D6171;")
        self._refresh_note()
        root.addWidget(self._note)

        plot = _PrismBarPlotWidget(
            group_names=group_names,
            grouped_values=grouped_values,
            means=means,
            errors=errors,
            style_config=style_config,
            parent=self,
        )
        root.addWidget(plot, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        root.addWidget(buttons)

    def _refresh_note(self) -> None:
        self._note.setText(tr(
            "Normalization baseline: {control} (mean Target/Loading = {baseline})",
            self._language,
            control=self._negative_control_name,
            baseline=f"{self._baseline_mean:.4g}",
        ))

    def set_language(self, language: str) -> None:
        self._language = language
        self.setWindowTitle(tr("Figures Generation", language))
        _translate_widget_tree(self, language)
        self._refresh_note()


class ColumnTableWindow(QMainWindow):
    """Editable Prism-style column table window."""
    panelFocusRequested = Signal(str)
    activeTargetRowChanged = Signal(int)
    tutorialEvent = Signal(str)
    _ROWS_PER_REPLICATE = 3

    def __init__(self, samples: int, replicates: int, parent=None) -> None:
        super().__init__(parent)
        self._language = LANG_EN
        self._samples = samples
        self._replicates = replicates
        self._table: QTableWidget | None = None
        self._content_widget: QWidget | None = None
        self._active_target_row: int | None = None
        self._group_names: list[str] = [f"Group {_index_to_group_label(i)}" for i in range(samples)]
        self._negative_control_group_index: int | None = None
        self._negative_control_selection_mode = False
        self._figure_dialogs: list[_FiguresDialog] = []
        self._figure_preview_host: QWidget | None = None
        self._negative_btn: QPushButton | None = None
        self._hint_label: QLabel | None = None
        self._header_view: _GroupHeaderView | None = None
        self._header_editor: QLineEdit | None = None
        self._editing_group_index: int | None = None
        self._table_zoom_factor = 1.0
        self._figure_zoom_factor = 1.0
        self._preview_plot: _PrismBarPlotWidget | None = None
        self._preview_plot_base_size: tuple[int, int] | None = None
        self._preview_scroll_area: _PreviewScrollArea | None = None
        self._figure_style_config = FigureStyleConfig()
        self._figure_style_history: list[FigureStyleConfig] = []
        self._figure_style_last_snapshot: FigureStyleConfig = _clone_style_config(self._figure_style_config)
        self._figure_style_before_dialog: FigureStyleConfig | None = None
        self._figure_control_dialog: FigureControlDialog | None = None
        self.setWindowTitle("Column Table")
        self.resize(840, 520)
        self._build_ui()

    def set_language(self, language: str) -> None:
        """Update visible table terminology while retaining the underlying data."""
        self._language = language
        self.setWindowTitle(tr("Column Table", language))
        root = self._content_widget or self.centralWidget()
        if root is not None:
            _translate_widget_tree(root, language)
        if self._figure_preview_host is not None:
            _translate_widget_tree(self._figure_preview_host, language)
        self._refresh_table_language()
        if self._figure_control_dialog is not None:
            self._figure_control_dialog.set_language(language)
        for dialog in self._figure_dialogs:
            dialog.set_language(language)

    def _display_group_name(self, name: str) -> str:
        if self._language != LANG_ZH_CN:
            return name
        match = re.fullmatch(r"Group\s+(.+)", name)
        return tr("Group {name}", self._language, name=match.group(1)) if match else name

    def _refresh_table_language(self) -> None:
        if self._table is None:
            return
        headers = [tr("Replicate", self._language), tr("Band Type", self._language)]
        headers.extend(self._display_group_name(name) for name in self._group_names)
        self._table.setHorizontalHeaderLabels(headers)
        for rep_index in range(self._replicates):
            top_row = rep_index * self._ROWS_PER_REPLICATE
            labels = (
                (top_row, 0, tr("Replicate {number}", self._language, number=rep_index + 1)),
                (top_row, 1, tr("Target band", self._language)),
                (top_row + 1, 1, tr("Loading control", self._language)),
                (top_row + 2, 1, tr("Normalized result", self._language)),
            )
            for row, column, text in labels:
                item = self._table.item(row, column)
                if item is not None:
                    item.setText(text)

    def _build_ui(self) -> None:
        central = QWidget(self)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)
        title = QLabel("Column Table")
        title.setStyleSheet("font-size: 13px; font-weight: 700; color: #2F3C45;")
        top_row.addWidget(title)
        top_row.addStretch(1)
        self._negative_btn = QPushButton("Select Negative Control")
        self._negative_btn.setObjectName("selectNegativeControlButton")
        self._negative_btn.setStyleSheet(
            "QPushButton {"
            "background-color: #E6EEF3;"
            "border: 1px solid #BACAD5;"
            "border-radius: 6px;"
            "color: #385161;"
            "padding: 5px 10px;"
            "font-size: 11px;"
            "font-weight: 600;"
            "}"
            "QPushButton:hover {"
            "background-color: #DCE7EE;"
            "}"
        )
        self._negative_btn.clicked.connect(self._on_select_negative_control_clicked)
        top_row.addWidget(self._negative_btn)
        root.addLayout(top_row)

        total_rows = self._replicates * self._ROWS_PER_REPLICATE
        total_cols = self._samples + 2  # Replicate label + sub-row label + sample columns
        table = _ColumnTableWidget(total_rows, total_cols, self)
        table.setObjectName("prism_table")
        table.setAlternatingRowColors(False)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(False)
        table.setStyleSheet(
            "QTableWidget#prism_table {"
            "background-color: #F7FAFC;"
            "gridline-color: #D8E6EE;"
            "border: 1px solid #D0DEE7;"
            "border-radius: 6px;"
            "font-size: 12px;"
            "color: #2E3B45;"
            "}"
            "QHeaderView::section {"
            "background-color: #DCE9F2;"
            "color: #5A6F7F;"
            "font-size: 11px;"
            "font-weight: 700;"
            "padding: 6px;"
            "border: 1px solid #D0DEE7;"
            "}"
            "QTableWidget::item {"
            "padding: 4px 8px;"
            "}"
        )

        headers = ["Replicate", "Band Type"] + list(self._group_names)
        table.setHorizontalHeaderLabels(headers)
        header = _GroupHeaderView(Qt.Orientation.Horizontal, table)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setMinimumSectionSize(105)
        header.groupRenameRequested.connect(self._on_group_rename_requested)
        header.groupClicked.connect(self._on_group_header_clicked)
        table.setHorizontalHeader(header)
        self._header_view = header

        for rep_index in range(self._replicates):
            top_row = rep_index * self._ROWS_PER_REPLICATE
            bottom_row = top_row + 1
            normalized_row = top_row + 2

            # Replicate label spans Target, Loading, and Normalized result.
            table.setSpan(top_row, 0, self._ROWS_PER_REPLICATE, 1)
            replicate_item = QTableWidgetItem(f"Replicate {rep_index + 1}")
            replicate_item.setFlags(replicate_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            replicate_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            replicate_item.setBackground(Qt.GlobalColor.white)
            table.setItem(top_row, 0, replicate_item)

            target_item = QTableWidgetItem("Target band")
            target_item.setFlags(target_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            target_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            target_item.setBackground(Qt.GlobalColor.white)
            table.setItem(top_row, 1, target_item)

            control_item = QTableWidgetItem("Loading control")
            control_item.setFlags(control_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            control_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            control_item.setBackground(Qt.GlobalColor.white)
            table.setItem(bottom_row, 1, control_item)

            normalized_item = QTableWidgetItem("Normalized result")
            normalized_item.setFlags(normalized_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            normalized_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            normalized_item.setBackground(QColor("#EEF5F0"))
            table.setItem(normalized_row, 1, normalized_item)

            for c in range(2, total_cols):
                top_data = QTableWidgetItem("")
                top_data.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                table.setItem(top_row, c, top_data)

                bottom_data = QTableWidgetItem("")
                bottom_data.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                table.setItem(bottom_row, c, bottom_data)

                normalized_data = QTableWidgetItem("")
                normalized_data.setFlags(normalized_data.flags() & ~Qt.ItemFlag.ItemIsEditable)
                normalized_data.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                normalized_data.setBackground(QColor("#EEF5F0"))
                table.setItem(normalized_row, c, normalized_data)

            # Keep the pair visually grouped.
            table.setRowHeight(top_row, 30)
            table.setRowHeight(bottom_row, 30)
            table.setRowHeight(normalized_row, 30)

        table.resizeColumnsToContents()
        table.setColumnWidth(0, 120)
        table.setColumnWidth(1, 130)
        table.cellClicked.connect(self._on_table_cell_clicked)
        table.deleteRequested.connect(self._on_delete_requested)
        root.addWidget(table, 1)
        self._table = table
        self._apply_table_zoom()

        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)
        bottom_row.setSpacing(8)
        bottom_row.addStretch(1)
        reset_btn = QPushButton("Reset")
        reset_btn.setStyleSheet(
            "QPushButton {"
            "background-color: #F1E6E6;"
            "border: 1px solid #D6B8B8;"
            "border-radius: 7px;"
            "color: #7A4242;"
            "padding: 5px 12px;"
            "font-size: 11px;"
            "font-weight: 600;"
            "}"
            "QPushButton:hover {"
            "background-color: #EBDADA;"
            "}"
        )
        reset_btn.clicked.connect(self._on_reset_table_clicked)
        bottom_row.addWidget(reset_btn)
        export_table_btn = QPushButton("Export Table")
        export_table_btn.setStyleSheet(
            "QPushButton {"
            "background-color: #E6EEF3;"
            "border: 1px solid #BACAD5;"
            "border-radius: 7px;"
            "color: #385161;"
            "padding: 5px 12px;"
            "font-size: 11px;"
            "font-weight: 600;"
            "}"
            "QPushButton:hover {"
            "background-color: #DCE7EE;"
            "}"
        )
        export_table_btn.clicked.connect(self._on_export_table_clicked)
        bottom_row.addWidget(export_table_btn)
        figures_btn = QPushButton("Figures Generation")
        figures_btn.setObjectName("figuresGenerationButton")
        self._figures_btn = figures_btn
        figures_btn.setStyleSheet(
            "QPushButton {"
            "background-color: #C2D3C8;"
            "border: 1px solid #9EB3A8;"
            "border-radius: 7px;"
            "color: #2C4A3D;"
            "padding: 5px 12px;"
            "font-size: 11px;"
            "font-weight: 600;"
            "}"
            "QPushButton:hover {"
            "background-color: #B4C8BB;"
            "}"
        )
        figures_btn.clicked.connect(self._on_figures_generation_clicked)
        bottom_row.addWidget(figures_btn)
        root.addLayout(bottom_row)

        self._install_content_widget(central)
        self._refresh_header_visuals()
        self._refresh_row_and_column_highlights()

    def take_content_widget(self) -> QWidget | None:
        widget = self.takeCentralWidget()
        if widget is not None:
            self._content_widget = widget
        return widget

    def _install_content_widget(self, widget: QWidget) -> None:
        old = self._content_widget
        if old is not None:
            parent = old.parentWidget()
            if parent is self or self.centralWidget() is old:
                taken = self.takeCentralWidget()
                if taken is not None:
                    taken.deleteLater()
                self.setCentralWidget(widget)
                self._content_widget = widget
                return
            layout = parent.layout() if parent is not None else None
            if layout is not None:
                for index in range(layout.count()):
                    if layout.itemAt(index).widget() is old:
                        layout.takeAt(index)
                        break
                old.setParent(None)
                old.deleteLater()
                widget.setParent(parent)
                layout.addWidget(widget)
                self._content_widget = widget
                return
        self.setCentralWidget(widget)
        self._content_widget = widget

    def set_figure_preview_host(self, host: QWidget | None) -> None:
        self._figure_preview_host = host

    @staticmethod
    def _replace_host_widget(host: QWidget, widget: QWidget) -> None:
        layout = host.layout()
        if layout is None:
            layout = QVBoxLayout(host)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)
        while layout.count():
            item = layout.takeAt(0)
            old = item.widget()
            if old is not None:
                old.setParent(None)
                old.deleteLater()
        layout.addWidget(widget)

    def _build_figure_preview_widget(
        self,
        *,
        grouped_values: list[list[float]],
        means: list[float],
        errors: list[float],
        baseline_mean: float,
    ) -> QWidget:
        panel = QWidget()
        root = QVBoxLayout(panel)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        control_name = self._group_names[self._negative_control_group_index] if self._negative_control_group_index is not None else "N/A"
        note = QLabel(
            f"Normalization baseline: {control_name} "
            f"(mean Target/Loading = {baseline_mean:.4g})"
        )
        note.setStyleSheet("font-size: 11px; color: #4D6171;")
        root.addWidget(note)
        scroll = _PreviewScrollArea(panel)
        scroll.setWidgetResizable(False)
        scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.clicked.connect(lambda: self.panelFocusRequested.emit("figure"))
        plot = _PrismBarPlotWidget(
            group_names=list(self._group_names),
            grouped_values=grouped_values,
            means=means,
            errors=errors,
            style_config=self._figure_style_config,
            parent=scroll,
        )
        plot.clicked.connect(lambda: self.panelFocusRequested.emit("figure"))
        plot.openControlPanel.connect(self._on_plot_open_control_panel)
        plot.styleEdited.connect(self._on_style_config_changed)
        base_w = max(760, 210 * max(1, len(self._group_names)))
        base_h = 460
        self._preview_plot = plot
        self._preview_plot_base_size = (base_w, base_h)
        self._preview_scroll_area = scroll
        self._apply_figure_zoom()
        scroll.setWidget(plot)
        root.addWidget(scroll, 1)
        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.addStretch(1)
        export_figure_btn = QPushButton("Export Figure")
        export_figure_btn.setStyleSheet(
            "QPushButton {"
            "background-color: #E6EEF3;"
            "border: 1px solid #BACAD5;"
            "border-radius: 7px;"
            "color: #385161;"
            "padding: 5px 12px;"
            "font-size: 11px;"
            "font-weight: 600;"
            "}"
            "QPushButton:hover {"
            "background-color: #DCE7EE;"
            "}"
        )
        export_figure_btn.clicked.connect(self._on_export_figure_clicked)
        button_row.addWidget(export_figure_btn)
        root.addLayout(button_row)
        return panel

    def _set_hint(self, text: str) -> None:
        _ = text

    def _refresh_figure_control_dialog(self) -> None:
        if self._figure_control_dialog is None:
            return
        self._figure_control_dialog.close()
        self._figure_control_dialog.deleteLater()
        self._figure_control_dialog = None

    def _sync_plot_widget_size(self) -> None:
        """Resize the figure canvas so the desired plot_width/plot_height always fits.

        compute_layout clamps the plot area to (widget_size - margins).  When the
        user sets plot_height to 600 but the widget is only 460 px tall nothing
        visible happens.  This method grows (or shrinks) the underlying widget so
        the requested dimensions are never clipped.
        The horizontal margin is ~200 px (y-title + y-labels + padding + right pad).
        The vertical margin is ~150 px (title + x-labels + x-title + padding).
        """
        if self._preview_plot is None or self._preview_plot_base_size is None:
            return
        cfg = self._figure_style_config
        needed_w = max(600, int(cfg.plot_width) + 200)
        needed_h = max(400, int(cfg.plot_height) + 150)
        if self._preview_plot_base_size != (needed_w, needed_h):
            self._preview_plot_base_size = (needed_w, needed_h)
            self._apply_figure_zoom()

    def _on_style_config_changed(self, *, push_history: bool = True) -> None:
        if push_history:
            new_sig = _style_config_signature(self._figure_style_config)
            last_sig = _style_config_signature(self._figure_style_last_snapshot)
            if new_sig != last_sig:
                self._figure_style_history.append(_clone_style_config(self._figure_style_last_snapshot))
                if len(self._figure_style_history) > 100:
                    self._figure_style_history = self._figure_style_history[-100:]
                self._figure_style_last_snapshot = _clone_style_config(self._figure_style_config)
        if self._preview_plot is not None:
            try:
                self._sync_plot_widget_size()
                self._preview_plot.update()
            except RuntimeError:
                self._preview_plot = None

    def _on_figure_style_undo_clicked(self) -> None:
        if not self._figure_style_history:
            return
        section = "general"
        reopen_dialog = False
        if self._figure_control_dialog is not None:
            try:
                row = self._figure_control_dialog._sidebar.currentRow()
                if 0 <= row < len(self._figure_control_dialog._SECTIONS):
                    section = self._figure_control_dialog._SECTIONS[row][1]
                reopen_dialog = self._figure_control_dialog.isVisible()
            except Exception:
                section = "general"
        previous = self._figure_style_history.pop()
        _copy_style_config_into(self._figure_style_config, previous)
        self._figure_style_last_snapshot = _clone_style_config(self._figure_style_config)
        self._refresh_figure_control_dialog()
        self._on_style_config_changed(push_history=False)
        if reopen_dialog:
            self._show_figure_control_dialog(section)

    def _on_figure_style_confirm_clicked(self) -> None:
        self._figure_style_history.clear()
        self._figure_style_last_snapshot = _clone_style_config(self._figure_style_config)
        self._on_style_config_changed(push_history=False)

    def _on_figure_style_cancel_clicked(self) -> None:
        """Cancel: revert style to the snapshot taken when the dialog was opened, then close."""
        if self._figure_style_before_dialog is not None:
            _copy_style_config_into(self._figure_style_config, self._figure_style_before_dialog)
            self._figure_style_last_snapshot = _clone_style_config(self._figure_style_config)
        self._refresh_figure_control_dialog()
        self._on_style_config_changed(push_history=False)

    def _show_figure_control_dialog(self, section: str = "general") -> None:
        if self._preview_plot is None:
            return
        # Snapshot state for Cancel-revert only when dialog is not already visible
        if self._figure_control_dialog is None or not self._figure_control_dialog.isVisible():
            self._figure_style_before_dialog = _clone_style_config(self._figure_style_config)
        if self._figure_control_dialog is None:
            self._figure_control_dialog = FigureControlDialog(
                self._figure_style_config,
                initial_section=section,
                parent=self,
            )
            self._figure_control_dialog.set_language(self._language)
            self._figure_control_dialog.styleChanged.connect(self._on_style_config_changed)
            self._figure_control_dialog.undoRequested.connect(self._on_figure_style_undo_clicked)
            self._figure_control_dialog.confirmRequested.connect(self._on_figure_style_confirm_clicked)
            self._figure_control_dialog.cancelRequested.connect(self._on_figure_style_cancel_clicked)
        else:
            self._figure_control_dialog.switch_to_section(section)
        self._figure_control_dialog.show()
        self._figure_control_dialog.raise_()
        self._figure_control_dialog.activateWindow()

    def _on_plot_open_control_panel(self, section: str) -> None:
        self.panelFocusRequested.emit("figure")
        self._show_figure_control_dialog(section)

    def _apply_table_zoom(self) -> None:
        if self._table is None:
            return
        scale = max(0.7, min(2.4, self._table_zoom_factor))
        self._table_zoom_factor = scale
        font = self._table.font()
        font.setPointSizeF(max(8.0, 12.0 * scale))
        self._table.setFont(font)
        header = self._table.horizontalHeader()
        hfont = header.font()
        hfont.setPointSizeF(max(7.0, 11.0 * scale))
        header.setFont(hfont)
        header.setMinimumSectionSize(max(70, int(105 * scale)))
        self._table.setColumnWidth(0, max(92, int(120 * scale)))
        self._table.setColumnWidth(1, max(105, int(130 * scale)))
        row_h = max(22, int(30 * scale))
        for row in range(self._table.rowCount()):
            self._table.setRowHeight(row, row_h)

    def _apply_figure_zoom(self) -> None:
        if self._preview_plot is None or self._preview_plot_base_size is None:
            return
        scale = max(0.1, min(2.8, self._figure_zoom_factor))
        self._figure_zoom_factor = scale
        bw, bh = self._preview_plot_base_size
        target_w, target_h = int(bw * scale), int(bh * scale)
        # Lower the old minimum first; otherwise QWidget clamps resize() to the
        # previous (larger) minimum and a Fit View request cannot shrink it.
        self._preview_plot.setMinimumSize(target_w, target_h)
        self._preview_plot.resize(target_w, target_h)
        self._preview_plot.update()

    def zoom_table(self, zoom_in: bool) -> None:
        self._table_zoom_factor *= 1.12 if zoom_in else 1.0 / 1.12
        self._apply_table_zoom()
        self.panelFocusRequested.emit("table")

    def zoom_figure_preview(self, zoom_in: bool) -> None:
        self._figure_zoom_factor *= 1.12 if zoom_in else 1.0 / 1.12
        self._apply_figure_zoom()
        self.panelFocusRequested.emit("figure")

    def fit_table_view(self) -> None:
        if self._table is None:
            return
        viewport = self._table.viewport().size()
        content_w = 120 + 130 + (self._samples * 105)
        content_h = (self._replicates * self._ROWS_PER_REPLICATE * 30) + self._table.horizontalHeader().height() + 6
        if viewport.width() <= 0 or viewport.height() <= 0:
            return
        scale_w = max(0.1, float(viewport.width() - 8) / float(max(1, content_w)))
        scale_h = max(0.1, float(viewport.height() - 8) / float(max(1, content_h)))
        self._table_zoom_factor = min(scale_w, scale_h)
        self._apply_table_zoom()
        self.panelFocusRequested.emit("table")

    def fit_figure_preview(self) -> None:
        if self._preview_scroll_area is None or self._preview_plot_base_size is None:
            return
        viewport = self._preview_scroll_area.viewport().size()
        if viewport.width() <= 0 or viewport.height() <= 0:
            return
        bw, bh = self._preview_plot_base_size
        # Leave a small frame around the plot so the initial preview is clearly
        # contained within, rather than touching or spilling past the viewport.
        scale_w = max(0.1, float(viewport.width() - 24) / float(max(1, bw)))
        scale_h = max(0.1, float(viewport.height() - 24) / float(max(1, bh)))
        self._figure_zoom_factor = min(scale_w, scale_h)
        self._apply_figure_zoom()
        self.panelFocusRequested.emit("figure")

    def table_to_dataframe(self) -> pd.DataFrame:
        rows: list[dict[str, str]] = []
        for rep_index in range(self._replicates):
            top_row = rep_index * self._ROWS_PER_REPLICATE
            bottom_row = top_row + 1
            normalized_row = top_row + 2
            rep_name = f"Replicate {rep_index + 1}"
            for row_idx in (top_row, bottom_row, normalized_row):
                band_item = self._table.item(row_idx, 1) if self._table is not None else None
                default_type = "Target band" if row_idx == top_row else ("Loading control" if row_idx == bottom_row else "Normalized result")
                band_type = band_item.text() if band_item is not None else default_type
                row_data: dict[str, str] = {
                    "Replicate": rep_name,
                    "Band Type": band_type,
                }
                for g in range(self._samples):
                    col = 2 + g
                    item = self._table.item(row_idx, col) if self._table is not None else None
                    row_data[self._group_names[g]] = item.text() if item is not None else ""
                rows.append(row_data)
        return pd.DataFrame(rows)

    def export_table_xlsx(self, path: Path) -> None:
        raw_df = self.table_to_dataframe()
        figure_frames = self._build_figure_export_frames()

        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            raw_df.to_excel(writer, sheet_name="Column Table", index=False)
            if figure_frames is None:
                pd.DataFrame(
                    [
                        {
                            "Status": (
                                "Figure calculation data is unavailable. "
                                "Select a valid negative control and ensure Target/Loading values are filled in."
                            )
                        }
                    ]
                ).to_excel(writer, sheet_name="Figure Calculations", index=False)
            else:
                detail_df, summary_df = figure_frames
                detail_df.to_excel(writer, sheet_name="Figure Calculations", index=False)
                summary_df.to_excel(writer, sheet_name="Figure Summary", index=False)

    def has_generated_figure(self) -> bool:
        return self._preview_plot is not None

    def export_current_figure_pdf(self, path: Path) -> bool:
        if self._preview_plot is None:
            return False
        writer = QPdfWriter(str(path))
        writer.setResolution(300)
        painter = QPainter()
        try:
            if not painter.begin(writer):
                return False

            page_rect = writer.pageLayout().paintRectPixels(writer.resolution())
            if page_rect.width() <= 0 or page_rect.height() <= 0:
                return False

            src_w = max(1, self._preview_plot.width())
            src_h = max(1, self._preview_plot.height())

            pixmap = self._preview_plot.grab()
            image: QImage | None = None
            if pixmap.isNull():
                # Fallback: explicit render with a fully-specified PySide6 signature.
                image = QImage(src_w, src_h, QImage.Format.Format_ARGB32_Premultiplied)
                image.fill(Qt.GlobalColor.white)
                img_painter = QPainter()
                try:
                    if not img_painter.begin(image):
                        return False
                    self._preview_plot.render(
                        img_painter,
                        QPoint(0, 0),
                        QRegion(QRect(0, 0, src_w, src_h)),
                        QWidget.RenderFlag.DrawWindowBackground | QWidget.RenderFlag.DrawChildren,
                    )
                finally:
                    if img_painter.isActive():
                        img_painter.end()

            draw_source_w = max(1, pixmap.width() if image is None else image.width())
            draw_source_h = max(1, pixmap.height() if image is None else image.height())
            scale = min(page_rect.width() / draw_source_w, page_rect.height() / draw_source_h)
            draw_w = max(1, int(draw_source_w * scale))
            draw_h = max(1, int(draw_source_h * scale))
            offset_x = page_rect.x() + (page_rect.width() - draw_w) // 2
            offset_y = page_rect.y() + (page_rect.height() - draw_h) // 2
            target_rect = QRect(offset_x, offset_y, draw_w, draw_h)

            painter.fillRect(page_rect, Qt.GlobalColor.white)
            if image is None:
                painter.drawPixmap(target_rect, pixmap, pixmap.rect())
            else:
                painter.drawImage(target_rect, image, image.rect())
            return True
        except Exception:
            try:
                if path.exists():
                    path.unlink()
            except Exception:
                pass
            return False
        finally:
            if painter.isActive():
                painter.end()

    def _on_export_table_clicked(self) -> None:
        default_name = str(Path.home() / "WB_column_table.xlsx")
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Table",
            default_name,
            "Excel (*.xlsx)",
        )
        if not path:
            return
        target = Path(path)
        if target.suffix.lower() != ".xlsx":
            target = target.with_suffix(".xlsx")
        try:
            self.export_table_xlsx(target)
            QMessageBox.information(self, "Export Table", f"Saved table to:\n{target}")
        except Exception as exc:
            QMessageBox.critical(self, "Export Table Error", str(exc))

    def _on_reset_table_clicked(self) -> None:
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

        setup = ColumnSetupDialog(self)
        setup.set_language(self._language)
        if setup.exec() != QDialog.DialogCode.Accepted:
            return
        values = setup.get_input()
        if values.samples <= 0 or values.replicates <= 0:
            QMessageBox.warning(
                self,
                "Invalid Input",
                "Number of samples and replicates must be positive integers.",
            )
            return
        self._reset_to_column_table(values.samples, values.replicates)

    def _reset_to_column_table(self, samples: int, replicates: int) -> None:
        if self._header_editor is not None:
            self._header_editor.deleteLater()
            self._header_editor = None
            self._editing_group_index = None
        for dialog in list(self._figure_dialogs):
            dialog.close()
            dialog.deleteLater()
        self._figure_dialogs.clear()
        self._refresh_figure_control_dialog()

        self._samples = int(samples)
        self._replicates = int(replicates)
        self._table = None
        self._active_target_row = None
        self._group_names = [f"Group {_index_to_group_label(i)}" for i in range(self._samples)]
        self._negative_control_group_index = None
        self._negative_control_selection_mode = False
        self._negative_btn = None
        self._hint_label = None
        self._header_view = None
        self._table_zoom_factor = 1.0
        self._figure_zoom_factor = 1.0
        self._preview_plot = None
        self._preview_plot_base_size = None
        self._preview_scroll_area = None
        self._figure_style_config = FigureStyleConfig()
        self._figure_style_history.clear()
        self._figure_style_last_snapshot = _clone_style_config(self._figure_style_config)
        self._figure_style_before_dialog = None

        if self._figure_preview_host is not None:
            placeholder = QLabel("Generated figure preview will appear here.")
            placeholder.setStyleSheet("color: #6E8494; font-size: 11px; padding: 10px;")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._replace_host_widget(self._figure_preview_host, placeholder)

        self._build_ui()
        self.set_language(self._language)
        self.panelFocusRequested.emit("table")

    def _on_export_figure_clicked(self) -> None:
        if not self.has_generated_figure():
            QMessageBox.information(self, "Export Figure", "No generated figure is available yet.")
            return
        default_name = str(Path.home() / "WB_figure.pdf")
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Figure",
            default_name,
            "PDF (*.pdf)",
        )
        if not path:
            return
        target = Path(path)
        if target.suffix.lower() != ".pdf":
            target = target.with_suffix(".pdf")
        try:
            if not self.export_current_figure_pdf(target):
                QMessageBox.warning(self, "Export Figure", "Figure export is unavailable.")
                return
            QMessageBox.information(self, "Export Figure", f"Saved figure to:\n{target}")
        except Exception as exc:
            QMessageBox.critical(self, "Export Figure Error", str(exc))

    def _on_select_negative_control_clicked(self) -> None:
        self._negative_control_selection_mode = True
        if self._negative_btn is not None:
            self._negative_btn.setText("Click a Group Header…")
        self._set_hint("Negative control mode: click one group header at the top.")
        self.tutorialEvent.emit("negative_control_requested")

    def _on_group_header_clicked(self, group_index: int) -> None:
        self.panelFocusRequested.emit("table")
        if self._header_editor is not None:
            self._commit_header_edit()
        if not self._negative_control_selection_mode:
            self._begin_inline_header_edit(group_index)
        else:
            if group_index < 0 or group_index >= self._samples:
                return
            self._negative_control_group_index = group_index
            self._negative_control_selection_mode = False
            if self._negative_btn is not None:
                self._negative_btn.setText("Select Negative Control")
            self._refresh_header_visuals()
            self._refresh_row_and_column_highlights()
            self._set_hint(f"Negative control selected: {self._group_names[group_index]}")
            self.tutorialEvent.emit("negative_control_selected")

    def _on_group_rename_requested(self, group_index: int) -> None:
        self._begin_inline_header_edit(group_index)

    def _begin_inline_header_edit(self, group_index: int) -> None:
        if self._header_view is None:
            return
        if group_index < 0 or group_index >= self._samples:
            return
        if self._header_editor is not None:
            self._commit_header_edit()
        section = group_index + 2
        x = self._header_view.sectionViewportPosition(section)
        width = self._header_view.sectionSize(section)
        if width <= 6:
            return
        editor = QLineEdit(self._header_view.viewport())
        editor.setText(self._group_names[group_index])
        editor.selectAll()
        editor.setFrame(False)
        editor.setStyleSheet(
            "QLineEdit {"
            "background: #FFFFFF;"
            "border: 1px solid #B7C7D2;"
            "border-radius: 3px;"
            "padding: 1px 4px;"
            "font-size: 11px;"
            "color: #2E3B45;"
            "}"
        )
        editor.setGeometry(x + 2, 2, max(20, width - 4), max(16, self._header_view.height() - 4))
        editor.returnPressed.connect(self._commit_header_edit)
        editor.editingFinished.connect(self._commit_header_edit)
        editor.show()
        editor.setFocus()
        self._header_editor = editor
        self._editing_group_index = group_index

    def _commit_header_edit(self) -> None:
        editor = self._header_editor
        group_index = self._editing_group_index
        if editor is None:
            return
        self._header_editor = None
        self._editing_group_index = None
        updated = editor.text().strip()
        editor.deleteLater()
        if group_index is None or group_index < 0 or group_index >= self._samples:
            return
        if not updated:
            return
        self._group_names[group_index] = updated
        self._refresh_header_visuals()
        if self._negative_control_group_index == group_index:
            self._set_hint(f"Negative control selected: {updated}")

    def _refresh_header_visuals(self) -> None:
        if self._table is None:
            return
        for group_index in range(self._samples):
            col = 2 + group_index
            item = self._table.horizontalHeaderItem(col)
            if item is None:
                item = QTableWidgetItem("")
                self._table.setHorizontalHeaderItem(col, item)
            item.setText(self._group_names[group_index])
            if group_index == self._negative_control_group_index:
                item.setData(Qt.ItemDataRole.BackgroundRole, QColor(250, 222, 230))
                item.setData(Qt.ItemDataRole.ForegroundRole, QColor("#743448"))
            else:
                item.setData(Qt.ItemDataRole.BackgroundRole, None)
                item.setData(Qt.ItemDataRole.ForegroundRole, None)

    def _on_table_cell_clicked(self, row: int, _column: int) -> None:
        self.panelFocusRequested.emit("table")
        self.set_active_target_row(row)

    def _clear_single_data_cell(self, row: int, col: int) -> bool:
        if self._table is None:
            return False
        if row < 0 or row >= self._table.rowCount():
            return False
        if col < 2 or col >= self._table.columnCount():
            return False
        item = self._table.item(row, col)
        if item is None:
            item = QTableWidgetItem("")
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, col, item)
        item.setText("")
        return True

    def _clear_row_data_cells(self, row: int) -> bool:
        if self._table is None:
            return False
        if row < 0 or row >= self._table.rowCount():
            return False
        for col in range(2, self._table.columnCount()):
            self._clear_single_data_cell(row, col)
        return True

    def _on_delete_requested(self) -> None:
        if self._table is None:
            return
        selected = self._table.selectedIndexes()
        if not selected:
            return

        # Case A: exactly one data cell selected -> clear only that cell.
        if len(selected) == 1:
            idx = selected[0]
            row = idx.row()
            col = idx.column()
            self.set_active_target_row(row)
            if self._clear_single_data_cell(row, col):
                return
            # Single selected label cell means row-target delete.
            if col in (0, 1):
                self._clear_row_data_cells(row)
            return

        # Case B: one selected row target/range -> clear whole row data cells.
        rows = {idx.row() for idx in selected}
        if len(rows) == 1:
            row = next(iter(rows))
            self.set_active_target_row(row)
            self._clear_row_data_cells(row)

    def _set_cell_background(self, row: int, col: int, color: QColor | None) -> None:
        if self._table is None:
            return
        item = self._table.item(row, col)
        if item is None:
            return
        item.setData(Qt.ItemDataRole.BackgroundRole, color if color is not None else None)

    def _refresh_row_and_column_highlights(self) -> None:
        if self._table is None:
            return
        row_color = QColor(220, 235, 226)
        col_color = QColor(253, 236, 240)
        both_color = QColor(248, 221, 229)
        neg_col = None if self._negative_control_group_index is None else 2 + self._negative_control_group_index
        for row in range(self._table.rowCount()):
            for col in range(1, self._table.columnCount()):
                is_row = row == self._active_target_row
                is_neg_col = col == neg_col
                if is_row and is_neg_col:
                    self._set_cell_background(row, col, both_color)
                elif is_row:
                    self._set_cell_background(row, col, row_color)
                elif is_neg_col:
                    self._set_cell_background(row, col, col_color)
                else:
                    self._set_cell_background(row, col, None)

    def set_active_target_row(self, row: int) -> bool:
        if self._table is None:
            return False
        if row < 0 or row >= self._table.rowCount():
            return False
        if row % self._ROWS_PER_REPLICATE == 2:
            return False
        self._active_target_row = row
        self._refresh_row_and_column_highlights()
        self.activeTargetRowChanged.emit(row)
        return True

    def has_active_target_row(self) -> bool:
        return self._active_target_row is not None

    def active_target_description(self) -> str:
        if self._active_target_row is None:
            return "No active row"
        replicate_idx = (self._active_target_row // self._ROWS_PER_REPLICATE) + 1
        row_kind = self._active_target_row % self._ROWS_PER_REPLICATE
        band_type = ("Target band", "Loading control", "Normalized result")[row_kind]
        return f"Replicate {replicate_idx} — {band_type}"

    def autofill_active_row(self, values: list[str]) -> int:
        if self._table is None or self._active_target_row is None:
            return 0
        if not values:
            return 0
        limit = min(self._samples, len(values))
        for i in range(limit):
            col = 2 + i
            item = self._table.item(self._active_target_row, col)
            if item is None:
                item = QTableWidgetItem("")
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(self._active_target_row, col, item)
            item.setText(str(values[i]))
        return limit

    @staticmethod
    def _parse_float_from_item(item: QTableWidgetItem | None) -> float | None:
        if item is None:
            return None
        text = item.text().strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    def _collect_target_loading_ratios(self) -> list[list[float]]:
        if self._table is None:
            return []
        per_group: list[list[float]] = [[] for _ in range(self._samples)]
        for group_index in range(self._samples):
            col = 2 + group_index
            for rep_index in range(self._replicates):
                target_row = rep_index * self._ROWS_PER_REPLICATE
                loading_row = target_row + 1
                target_val = self._parse_float_from_item(self._table.item(target_row, col))
                loading_val = self._parse_float_from_item(self._table.item(loading_row, col))
                if target_val is None or loading_val is None:
                    continue
                if loading_val == 0:
                    continue
                per_group[group_index].append(target_val / loading_val)
        return per_group

    def _populate_normalized_result_rows(self, baseline_mean: float) -> None:
        """Write each replicate's (Target/Loading)/baseline value into the table."""
        if self._table is None or baseline_mean == 0:
            return
        for rep_index in range(self._replicates):
            target_row = rep_index * self._ROWS_PER_REPLICATE
            loading_row = target_row + 1
            normalized_row = target_row + 2
            for group_index in range(self._samples):
                col = 2 + group_index
                target_val = self._parse_float_from_item(self._table.item(target_row, col))
                loading_val = self._parse_float_from_item(self._table.item(loading_row, col))
                result_item = self._table.item(normalized_row, col)
                if result_item is None:
                    result_item = QTableWidgetItem("")
                    result_item.setFlags(result_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    result_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    self._table.setItem(normalized_row, col, result_item)
                if target_val is None or loading_val is None or loading_val == 0:
                    result_item.setText("")
                else:
                    normalized = (target_val / loading_val) / baseline_mean
                    result_item.setText(f"{normalized:.6g}")

    def _build_figure_export_frames(self) -> tuple[pd.DataFrame, pd.DataFrame] | None:
        if self._table is None or self._negative_control_group_index is None:
            return None

        detail_rows: list[dict[str, float | int | str]] = []
        ratios_by_group: list[list[float]] = [[] for _ in range(self._samples)]

        for group_index in range(self._samples):
            group_name = self._group_names[group_index]
            col = 2 + group_index
            for rep_index in range(self._replicates):
                target_row = rep_index * self._ROWS_PER_REPLICATE
                loading_row = target_row + 1
                target_val = self._parse_float_from_item(self._table.item(target_row, col))
                loading_val = self._parse_float_from_item(self._table.item(loading_row, col))
                if target_val is None or loading_val is None or loading_val == 0:
                    continue

                ratio = float(target_val / loading_val)
                ratios_by_group[group_index].append(ratio)
                detail_rows.append(
                    {
                        "Group": group_name,
                        "Replicate": rep_index + 1,
                        "Target Band Intensity": target_val,
                        "Loading Control Intensity": loading_val,
                        "Target/Loading Ratio": ratio,
                    }
                )

        if not detail_rows:
            return None

        control_group_name = self._group_names[self._negative_control_group_index]
        control_ratios = ratios_by_group[self._negative_control_group_index]
        if not control_ratios:
            return None

        baseline_mean = float(np.mean(control_ratios))
        if baseline_mean == 0:
            return None

        detail_df = pd.DataFrame(detail_rows)
        detail_df["Negative Control Group"] = control_group_name
        detail_df["Baseline Mean (Target/Loading)"] = baseline_mean
        detail_df["Normalized Value"] = detail_df["Target/Loading Ratio"] / baseline_mean

        summary_rows: list[dict[str, float | int | str]] = []
        for group_index, group_name in enumerate(self._group_names):
            group_mask = detail_df["Group"] == group_name
            group_values = detail_df.loc[group_mask, "Normalized Value"].astype(float).to_numpy()
            if len(group_values) == 0:
                normalized_mean = 0.0
                normalized_sd = 0.0
            else:
                normalized_mean = float(np.mean(group_values))
                normalized_sd = float(np.std(group_values, ddof=1)) if len(group_values) > 1 else 0.0
            summary_rows.append(
                {
                    "Group": group_name,
                    "Normalized Mean": normalized_mean,
                    "Normalized SD": normalized_sd,
                    "Valid Replicates": int(len(group_values)),
                    "Negative Control Group": control_group_name,
                    "Baseline Mean (Target/Loading)": baseline_mean,
                }
            )

        summary_df = pd.DataFrame(summary_rows)
        return detail_df, summary_df

    def _on_figures_generation_clicked(self) -> None:
        if self._negative_control_group_index is None:
            QMessageBox.warning(
                self,
                "Negative Control Required",
                "Please click 'Select Negative Control' and choose one group header first.",
            )
            return

        ratios_by_group = self._collect_target_loading_ratios()
        if not ratios_by_group:
            QMessageBox.warning(self, "No Data", "No valid Target/Loading values were found.")
            return

        control_ratios = ratios_by_group[self._negative_control_group_index]
        if not control_ratios:
            QMessageBox.warning(
                self,
                "Invalid Negative Control",
                "The selected negative control group has no valid Target/Loading values.",
            )
            return

        baseline_mean = float(np.mean(control_ratios))
        if baseline_mean == 0:
            QMessageBox.warning(
                self,
                "Invalid Baseline",
                "Negative control baseline mean is zero and cannot be used for normalization.",
            )
            return

        self._populate_normalized_result_rows(baseline_mean)

        normalized_by_group: list[list[float]] = []
        for group_vals in ratios_by_group:
            normalized_by_group.append([float(v / baseline_mean) for v in group_vals])

        means: list[float] = []
        errors: list[float] = []
        for values in normalized_by_group:
            if not values:
                means.append(0.0)
                errors.append(0.0)
                continue
            arr = np.array(values, dtype=np.float64)
            means.append(float(np.mean(arr)))
            if len(values) > 1:
                errors.append(float(np.std(arr, ddof=1)))
            else:
                errors.append(0.0)

        if not any(len(vals) > 0 for vals in normalized_by_group):
            QMessageBox.warning(self, "No Data", "No groups contain valid data for plotting.")
            return

        if self._figure_preview_host is not None:
            preview = self._build_figure_preview_widget(
                grouped_values=normalized_by_group,
                means=means,
                errors=errors,
                baseline_mean=baseline_mean,
            )
            self._replace_host_widget(self._figure_preview_host, preview)
            # Geometry is only reliable after the new preview has entered the
            # layout, so fit on the next event-loop turn.
            QTimer.singleShot(0, self.fit_figure_preview)
            self.tutorialEvent.emit("figure_generated")
            return

        dialog = _FiguresDialog(
            group_names=list(self._group_names),
            grouped_values=normalized_by_group,
            means=means,
            errors=errors,
            negative_control_name=self._group_names[self._negative_control_group_index],
            baseline_mean=baseline_mean,
            style_config=self._figure_style_config,
            parent=self,
        )
        dialog.set_language(self._language)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        def _cleanup_dialog(_obj=None, closed_dialog=dialog) -> None:
            self._figure_dialogs = [d for d in self._figure_dialogs if d is not closed_dialog]

        dialog.destroyed.connect(_cleanup_dialog)
        self._figure_dialogs.append(dialog)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        self.tutorialEvent.emit("figure_generated")
