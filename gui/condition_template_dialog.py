"""Condition-template dialog UI and interaction controller.

This module deliberately has no dependency on FigureModeWindow.  The window
supplies the few application callbacks needed by the dialog, keeping the Qt
workflow boundary explicit and avoiding a circular GUI import.
"""
from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypedDict

from PySide6.QtCore import QPointF, QRectF, Qt, QSignalBlocker, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QMenu, QPushButton, QSizePolicy, QSpinBox,
    QToolButton, QVBoxLayout, QWidget,
)

from core.condition_template import even_lane_group_ranges, make_condition_table
from core.figure_project import FigureProject
from core.layout_engine import LayoutEngine, LayoutItem
from utils.i18n import tr


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


@dataclass
class ConditionGroupingState:
    """UI-independent switches that select shared or per-panel editing."""

    rows_individual: bool = False
    groups_individual: bool = False


class LaneGroupControl(TypedDict, total=False):
    """Widgets and draft values that make up one lane-group control."""

    row: QWidget
    layout: QHBoxLayout
    group_spin: QSpinBox
    group_mode: QComboBox
    mode_selector: QToolButton
    default_action: QAction
    custom_action: QAction
    custom_btn: QPushButton
    selector_column: QWidget
    selector_layout: QVBoxLayout
    individual_remove_btn: QToolButton
    custom_ranges: list[tuple[int, int]] | None
    custom_lane_count: int | None
    active: bool
    panel_row: QWidget
    panel_label: QLabel
    panel_row_layout: QHBoxLayout


class LaneGroupLevel(TypedDict):
    """All shared and per-panel controls for one grouping level."""

    container: QWidget
    heading: QLabel
    remove_btn: QToolButton
    shared_row: QWidget
    shared_control: LaneGroupControl
    individual_rows: QWidget
    panel_controls: list[LaneGroupControl]


def request_custom_lane_ranges(
    parent: QWidget,
    language: str,
    make_spin: Callable[[int, int, int], QSpinBox],
    retranslate: Callable[[QWidget], None],
    lane_count: int,
    group_count: int,
    defaults: list[tuple[int, int]],
) -> list[tuple[int, int]] | None:
    """Collect inclusive custom lane ranges without exposing UI to the window."""
    dialog = QDialog(parent)
    dialog.setWindowTitle("Custom Lane Groups")
    dialog.setModal(True)
    outer = QVBoxLayout(dialog)
    outer.setContentsMargins(18, 16, 18, 14)
    info = QLabel("Choose the inclusive lane range for each group.")
    info.setStyleSheet("color:#5C7167; font-size:10px;")
    outer.addWidget(info)
    form = QFormLayout()
    range_spins: list[tuple[QSpinBox, QSpinBox]] = []
    for group_index in range(group_count):
        start_spin = make_spin(1, lane_count, defaults[group_index][0])
        end_spin = make_spin(1, lane_count, defaults[group_index][1])
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(QLabel("Lane"))
        row_layout.addWidget(start_spin)
        row_layout.addWidget(QLabel("–"))
        row_layout.addWidget(end_spin)
        form.addRow(
            tr("Group {number}:", language, number=group_index + 1),
            row,
        )
        range_spins.append((start_spin, end_spin))
    outer.addLayout(form)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Cancel
        | QDialogButtonBox.StandardButton.Ok
    )
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    outer.addWidget(buttons)
    retranslate(dialog)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return [
        (min(start.value(), end.value()), max(start.value(), end.value()))
        for start, end in range_spins
    ]


def request_custom_panel_lane_ranges(
    parent: QWidget,
    language: str,
    make_spin: Callable[[int, int, int], QSpinBox],
    retranslate: Callable[[QWidget], None],
    lane_counts: list[int],
    group_count: int,
    defaults: list[tuple[int, int]],
    *,
    panel_number_offset: int = 0,
) -> list[tuple[int, int]] | None:
    """Collect ranges addressed across multiple panels and lane indices."""
    lane_counts = [max(1, int(count)) for count in lane_counts]
    if not lane_counts or group_count <= 0:
        return []

    cumulative: list[int] = [0]
    for count in lane_counts:
        cumulative.append(cumulative[-1] + count)

    def address(global_lane: int) -> tuple[int, int]:
        lane = max(1, min(cumulative[-1], int(global_lane)))
        for panel_index, end in enumerate(cumulative[1:]):
            if lane <= end:
                return panel_index, lane - cumulative[panel_index]
        return len(lane_counts) - 1, lane_counts[-1]

    dialog = QDialog(parent)
    dialog.setWindowTitle("Custom Panel/Lane Ranges")
    dialog.setModal(True)
    dialog.setStyleSheet(
        "QDialog { background:#F3F6F5; color:#26322D; "
        "font-family:'Avenir Next','Helvetica Neue',Arial; } "
        "QComboBox, QSpinBox { background:#FFFFFF; "
        "border:1px solid #BCC9C3; border-radius:5px; padding:3px 6px; }"
    )
    outer = QVBoxLayout(dialog)
    outer.setContentsMargins(18, 16, 18, 14)
    outer.setSpacing(10)
    info = QLabel("Choose the first and last panel/lane for each group.")
    info.setStyleSheet("color:#5C7167; font-size:10px;")
    outer.addWidget(info)
    form = QFormLayout()
    form.setHorizontalSpacing(12)
    range_controls: list[
        tuple[QComboBox, QSpinBox, QComboBox, QSpinBox]
    ] = []

    for group_index in range(group_count):
        default_start, default_end = defaults[group_index]
        start_panel_index, start_lane_value = address(default_start)
        end_panel_index, end_lane_value = address(default_end)
        start_panel = QComboBox()
        end_panel = QComboBox()
        for panel_index in range(len(lane_counts)):
            label = tr(
                "Panel {number}",
                language,
                number=panel_number_offset + panel_index + 1,
            )
            start_panel.addItem(label, panel_index)
            end_panel.addItem(label, panel_index)
        start_panel.setCurrentIndex(start_panel_index)
        end_panel.setCurrentIndex(end_panel_index)
        start_lane = make_spin(
            1, lane_counts[start_panel_index], start_lane_value
        )
        end_lane = make_spin(1, lane_counts[end_panel_index], end_lane_value)
        start_panel.currentIndexChanged.connect(
            lambda index, spin=start_lane: spin.setMaximum(lane_counts[index])
        )
        end_panel.currentIndexChanged.connect(
            lambda index, spin=end_lane: spin.setMaximum(lane_counts[index])
        )
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)
        row_layout.addWidget(start_panel)
        row_layout.addWidget(QLabel("Lane"))
        row_layout.addWidget(start_lane)
        row_layout.addWidget(QLabel("→"))
        row_layout.addWidget(end_panel)
        row_layout.addWidget(QLabel("Lane"))
        row_layout.addWidget(end_lane)
        form.addRow(
            tr("Group {number}:", language, number=group_index + 1),
            row,
        )
        range_controls.append(
            (start_panel, start_lane, end_panel, end_lane)
        )

    outer.addLayout(form)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Cancel
        | QDialogButtonBox.StandardButton.Ok
    )
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    outer.addWidget(buttons)
    retranslate(dialog)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None

    result: list[tuple[int, int]] = []
    for start_panel, start_lane, end_panel, end_lane in range_controls:
        start_global = (
            cumulative[start_panel.currentIndex()] + start_lane.value()
        )
        end_global = cumulative[end_panel.currentIndex()] + end_lane.value()
        result.append(
            (min(start_global, end_global), max(start_global, end_global))
        )
    return result


class ConditionTemplateDialogController:
    """Own the condition dialog, draft state, synchronization, and signals.

    The Figure window creates this controller and only consumes its model-ready
    result.  All widget object names and signal behavior remain encapsulated
    here so the surrounding window does not need to know dialog internals.
    """

    def __init__(
        self,
        parent: QWidget,
        targets: list[tuple[int, int]],
        project: FigureProject,
        language: str,
        tutorial_mode: bool,
        *,
        preview_factory: Callable[[QWidget], "ConditionPreviewWidget"],
        make_spin: Callable[[int, int, int], QSpinBox],
        request_custom_ranges: Callable[
            [int, int, list[tuple[int, int]]],
            list[tuple[int, int]] | None,
        ],
        retranslate: Callable[[QWidget], None],
    ) -> None:
        self._parent = parent
        self.targets = list(targets)
        self._project = project
        self._language = language
        self._tutorial_mode = tutorial_mode
        self._preview_factory = preview_factory
        self._make_spin = make_spin
        self._request_custom_ranges = request_custom_ranges
        self._retranslate = retranslate
        self._build_shell()
        self._build_rows_section()
        self._build_lane_groups_section()
        self._connect_actions()

    def _build_shell(self) -> None:
        self.panel_count = len(self.targets)
        self.lane_counts = [lane_count for _panel_index, lane_count in self.targets]
        self.shared_lane_count = max(1, min(self.lane_counts))
        self.dialog = QDialog(self._parent)
        self.dialog.setObjectName('modernConditionDialog')
        self.dialog.setWindowTitle('Create Blot Condition Template')
        self.dialog.setModal(True)
        self.dialog.setMinimumWidth(620)
        self.dialog.setStyleSheet(
            "QDialog#modernConditionDialog { background:#F3F6F5; "
            "color:#26322D; font-family:'Avenir Next','Helvetica Neue',Arial; "
            "font-size:12px; } "
            "QDialog#modernConditionDialog QLabel, "
            "QDialog#modernConditionDialog QWidget#conditionRowsHost, "
            "QDialog#modernConditionDialog QWidget#conditionRowsShared, "
            "QDialog#modernConditionDialog QWidget#conditionRowsIndividual, "
            "QDialog#modernConditionDialog QWidget#conditionLevelsHost, "
            "QDialog#modernConditionDialog QWidget#conditionLevelContainer, "
            "QDialog#modernConditionDialog QWidget#conditionLevelHeading, "
            "QDialog#modernConditionDialog QWidget#conditionLevelControls, "
            "QDialog#modernConditionDialog QWidget#conditionPanelLevelControls, "
            "QDialog#modernConditionDialog QWidget#conditionPanelLevelRow, "
            "QDialog#modernConditionDialog "
            "QWidget#conditionLaneDistributionColumn { "
            "background:transparent; border:none; } "
            "QFrame#conditionRowsCard, QFrame#conditionLaneGroupsCard { "
            "background:#FFFFFF; border:1px solid #D6E0DB; "
            "border-radius:10px; } "
            "QLabel#conditionSectionTitle { color:#214B39; font-size:12px; "
            "font-weight:600; } "
            "QLabel#conditionFieldLabel { color:#34423B; font-size:11px; } "
            "QLabel#conditionGroupHeading { color:#285A44; font-size:11px; "
            "font-weight:600; } "
            "QLabel#conditionPanelLabel { color:#34423B; font-size:11px; } "
            "QLabel#conditionHint { color:#6C7B74; font-size:10px; } "
            "QSpinBox, QComboBox { background:#FFFFFF; color:#26322D; "
            "border:1px solid #BCC9C3; border-radius:5px; padding:3px 6px; "
            "min-height:20px; } "
            "QSpinBox:focus, QComboBox:focus { border:1px solid #5E9A7F; } "
            "QToolButton[conditionModeSelector=\"true\"] { "
            "background:#F7FAF8; border:1px solid #C5D1CB; "
            "border-radius:4px; padding:2px 5px; color:#34423B; "
            "font-size:10px; text-align:center; } "
            "QToolButton[conditionModeSelector=\"true\"]:hover { "
            "border-color:#7FA590; background:#EDF5F1; } "
            "QToolButton[conditionModeSelector=\"true\"]::menu-indicator { "
            "image:none; width:0px; } "
            "QMenu#conditionModeMenu { background:#FFFFFF; color:#26322D; "
            "border:1px solid #C5D1CB; padding:4px; } "
            "QMenu#conditionModeMenu::item { padding:5px 20px 5px 8px; "
            "border-radius:4px; } "
            "QMenu#conditionModeMenu::item:selected { background:#E4F0EA; "
            "color:#214B39; } "
            "QPushButton#conditionCreateButton { background:#315F4B; "
            "border:1px solid #315F4B; border-radius:6px; padding:6px 18px; "
            "color:#FFFFFF; font-weight:600; } "
            "QPushButton#conditionCreateButton:hover { background:#274E3D; } "
            "QPushButton#conditionCancelButton { background:#FFFFFF; "
            "border:1px solid #C5D1CB; border-radius:6px; padding:6px 18px; "
            "color:#34423B; }"
        )
        self.outer = QVBoxLayout(self.dialog)
        self.outer.setContentsMargins(22, 20, 22, 18)
        self.outer.setSpacing(14)
        self.state = ConditionGroupingState()

    def _build_rows_section(self) -> None:
        rows_card = QFrame()
        rows_card.setObjectName('conditionRowsCard')
        rows_card_layout = QVBoxLayout(rows_card)
        rows_card_layout.setContentsMargins(16, 14, 16, 14)
        rows_card_layout.setSpacing(9)
        rows_header = QHBoxLayout()
        rows_header.setSpacing(9)
        rows_header.addWidget(self.section_title('Condition rows'))
        (
            self.rows_mode_selector,
            self.rows_apply_action,
            self.rows_individual_action,
        ) = self.section_mode_selector('conditionRowsModeSelector')
        rows_header.addWidget(self.rows_mode_selector)
        rows_header.addStretch(1)
        rows_card_layout.addLayout(rows_header)
        self.rows_shared = QWidget()
        self.rows_shared.setObjectName('conditionRowsShared')
        rows_shared_layout = QHBoxLayout(self.rows_shared)
        rows_shared_layout.setContentsMargins(0, 0, 0, 0)
        rows_shared_layout.setSpacing(8)
        rows_shared_layout.addWidget(self.group_heading_label('Condition rows #:'))
        self.shared_rows_spin = self._make_spin(1, 20, 1)
        self.finish_spin_input_on_return(self.shared_rows_spin)
        self.shared_rows_spin.setObjectName('conditionRowsSpin_common')
        rows_shared_layout.addWidget(self.shared_rows_spin)
        rows_shared_layout.addStretch(1)
        rows_card_layout.addWidget(self.rows_shared)
        self.rows_individual = QWidget()
        self.rows_individual.setObjectName('conditionRowsIndividual')
        rows_individual_layout = QGridLayout(self.rows_individual)
        rows_individual_layout.setContentsMargins(0, 0, 0, 0)
        rows_individual_layout.setHorizontalSpacing(8)
        rows_individual_layout.setVerticalSpacing(6)
        rows_individual_layout.addWidget(
            self.group_heading_label('Condition rows #:'),
            0,
            0,
            1,
            2,
            Qt.AlignmentFlag.AlignLeft,
        )
        self.panel_rows_spins: list[QSpinBox] = []
        for panel_position in range(self.panel_count):
            spin = self._make_spin(1, 20, 1)
            self.finish_spin_input_on_return(spin)
            spin.setObjectName(f'conditionRowsSpin_panel_{panel_position + 1}')
            rows_individual_layout.addWidget(self.panel_label(panel_position), panel_position + 1, 0)
            rows_individual_layout.addWidget(spin, panel_position + 1, 1, Qt.AlignmentFlag.AlignLeft)
            self.panel_rows_spins.append(spin)
        rows_individual_layout.setColumnStretch(2, 1)
        self.rows_individual.hide()
        rows_card_layout.addWidget(self.rows_individual)
        self.outer.addWidget(rows_card)

    def _build_lane_groups_section(self) -> None:
        self.lane_card = QFrame()
        self.lane_card.setObjectName('conditionLaneGroupsCard')
        lane_layout = QVBoxLayout(self.lane_card)
        lane_layout.setContentsMargins(16, 14, 16, 14)
        lane_layout.setSpacing(10)
        lane_header = QHBoxLayout()
        lane_header.setSpacing(9)
        lane_header.addWidget(self.section_title('Lane groups'))
        (
            self.groups_mode_selector,
            self.groups_apply_action,
            self.groups_individual_action,
        ) = self.section_mode_selector('laneGroupsModeSelector')
        lane_header.addWidget(self.groups_mode_selector)
        lane_header.addStretch(1)
        lane_layout.addLayout(lane_header)
        self.levels_host = QWidget()
        self.levels_host.setObjectName('conditionLevelsHost')
        self.levels_layout = QHBoxLayout(self.levels_host)
        self.levels_layout.setContentsMargins(0, 0, 0, 0)
        self.levels_layout.setSpacing(10)
        self.levels_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        lane_layout.addWidget(self.levels_host)
        self.levels: list[LaneGroupLevel] = []
        self.add_level_buttons = [self.make_add_level_button() for _panel_position in range(self.panel_count)]
        self.add_level_btn = self.add_level_buttons[0]
        self.empty_row = QWidget()
        self.empty_layout = QHBoxLayout(self.empty_row)
        self.empty_layout.setContentsMargins(0, 0, 0, 0)
        self.empty_layout.setSpacing(7)
        empty_level_label = QLabel(tr('No lane groups', self._language))
        empty_level_label.setObjectName('conditionHint')
        self.empty_layout.addWidget(empty_level_label)
        self.empty_layout.addWidget(self.add_level_btn)
        self.empty_layout.addStretch(1)
        self.levels_layout.addWidget(self.empty_row)
        self.empty_individual_rows = QWidget()
        empty_individual_layout = QVBoxLayout(self.empty_individual_rows)
        empty_individual_layout.setContentsMargins(0, 0, 0, 0)
        empty_individual_layout.setSpacing(6)
        self.empty_panel_layouts: list[QHBoxLayout] = []
        for panel_position in range(self.panel_count):
            panel_empty_row = QWidget(self.empty_individual_rows)
            panel_empty_row.setObjectName('conditionPanelLevelRow')
            panel_empty_layout = QHBoxLayout(panel_empty_row)
            panel_empty_layout.setContentsMargins(0, 0, 0, 0)
            panel_empty_layout.setSpacing(6)
            panel_empty_layout.addWidget(self.panel_label(panel_position))
            panel_empty_layout.addStretch(1)
            empty_individual_layout.addWidget(panel_empty_row)
            self.empty_panel_layouts.append(panel_empty_layout)
        self.empty_individual_rows.hide()
        self.levels_layout.addWidget(self.empty_individual_rows)
        self.levels_layout.addStretch(1)
        self.preview = self._preview_factory(self.dialog)
        self.preview.setFixedWidth(430 if self.panel_count == 1 else min(810, self.panel_count * 270))
        self.preview.setStyleSheet('background:#FFFFFF; border:1px solid #D6E0DB; border-radius:8px;')

    def section_title(self, text: str) -> QLabel:
        label = QLabel(tr(text, self._language))
        label.setObjectName('conditionSectionTitle')
        return label

    def panel_label(self, panel_position: int) -> QLabel:
        label = QLabel(tr('Panel {number}', self._language, number=panel_position + 1))
        label.setObjectName('conditionPanelLabel')
        label.setMinimumWidth(54)
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return label

    def group_heading_label(self, text: str='') -> QLabel:
        label = QLabel(text)
        label.setObjectName('conditionGroupHeading')
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return label

    def popup_selector(self, object_name: str, text: str, width: int) -> tuple[QToolButton, QMenu]:
        selector = QToolButton()
        selector.setObjectName(object_name)
        selector.setProperty('conditionModeSelector', True)
        selector.setText(tr(text, self._language))
        selector.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        selector.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        selector.setFixedSize(width, 22)
        menu = QMenu(selector)
        menu.setObjectName('conditionModeMenu')
        selector.setMenu(menu)
        return (selector, menu)

    def section_mode_selector(self, object_name: str) -> tuple[QToolButton, QAction, QAction]:
        selector, menu = self.popup_selector(object_name, 'Apply to all panels', 146)
        apply_all = menu.addAction(tr('Apply to all panels', self._language))
        individual = menu.addAction(tr('Set individual panels', self._language))
        apply_all.setCheckable(True)
        individual.setCheckable(True)
        apply_all.setChecked(True)
        selector.setVisible(self.panel_count > 1)
        return (selector, apply_all, individual)

    def finish_spin_input_on_return(self, spin: QSpinBox) -> None:
        editor = spin.lineEdit()

        def finish_input() -> None:
            spin.interpretText()
            editor.deselect()
            spin.clearFocus()
        editor.returnPressed.connect(lambda: QTimer.singleShot(0, finish_input))

    def make_add_level_button(self) -> QToolButton:
        button = QToolButton()
        button.setText('+')
        button.setToolTip(tr('Add another lane-group level', self._language))
        button.setFixedSize(22, 22)
        button.setStyleSheet(
            'QToolButton { border:1px solid #8FB7A6; border-radius:5px; '
            'background:#E8F3EE; color:#24513D; font-size:13px; '
            'font-weight:700; } '
            'QToolButton:hover { background:#D3E9DF; }'
        )
        return button

    def resize_dialog_to_visible_content(self) -> None:
        layout = self.dialog.layout()
        if layout is None:
            return
        layout.invalidate()
        layout.activate()
        hint = self.dialog.sizeHint()
        self.dialog.resize(max(self.dialog.minimumWidth(), hint.width()), hint.height())

    def condition_rows_for(self, panel_position: int) -> int:
        if self.state.rows_individual and self.panel_count > 1:
            return self.panel_rows_spins[panel_position].value()
        return self.shared_rows_spin.value()

    def control_ranges(
        self, control: LaneGroupControl, lanes: int
    ) -> list[tuple[int, int]]:
        groups = min(control['group_spin'].value(), max(1, lanes))
        if groups <= 0:
            return []
        if (
            control['group_mode'].currentData() == 'custom'
            and control['custom_lane_count'] == lanes
            and control['custom_ranges'] is not None
            and len(control['custom_ranges']) == groups
        ):
            return list(control['custom_ranges'])
        return even_lane_group_ranges(lanes, groups)

    def level_control(
        self, level: LaneGroupLevel, panel_position: int
    ) -> LaneGroupControl:
        if self.state.groups_individual and self.panel_count > 1:
            return level['panel_controls'][panel_position]
        return level['shared_control']

    def current_levels(self, panel_position: int) -> list[list[tuple[int, int]]]:
        lanes = self.lane_counts[panel_position]
        result: list[list[tuple[int, int]]] = []
        for level in self.levels:
            control = self.level_control(level, panel_position)
            if self.state.groups_individual and self.panel_count > 1 and (not control['active']):
                continue
            result.append(self.control_ranges(control, lanes))
        return result

    def refresh_preview(self) -> None:
        preview_project = copy.deepcopy(self._project)
        if preview_project is None:
            return
        preview_conditions = [
            (
                lane_count,
                self.condition_rows_for(panel_position),
                self.current_levels(panel_position),
            )
            for panel_position, lane_count in enumerate(self.lane_counts)
        ]
        self.preview.set_conditions(preview_conditions)
        for panel_position, (panel_index, lane_count) in enumerate(self.targets):
                preview_project.panels[panel_index].condition_table = (
                    make_condition_table(
                        lane_count,
                        preview_conditions[panel_position][1],
                        preview_conditions[panel_position][2],
                    )
                )
        preview_project.global_layout.show_condition_table = True
        preview_project.global_layout.condition_table_row_height_pt = 13.0
        self.preview.set_layout_project(preview_project)

    def update_control(self, control: LaneGroupControl, lanes: int) -> None:
        control['group_spin'].setMaximum(max(1, lanes))
        has_groups = control['group_spin'].value() > 0
        control['group_mode'].setEnabled(True)
        evenly = control['group_mode'].currentData() != 'custom'
        control['selector_layout'].setContentsMargins(0, 12 if evenly else 0, 0, 0)
        control['mode_selector'].setEnabled(True)
        selector_width = 53
        control['mode_selector'].setFixedSize(selector_width, 22)
        control['mode_selector'].setText(tr('Evenly', self._language) if evenly else tr('Custom', self._language))
        control['mode_selector'].setToolTip(
            tr('Divide lanes evenly', self._language)
            if evenly
            else tr('Custom lane ranges…', self._language)
        )
        control['custom_btn'].setFixedSize(selector_width, 22)
        control['layout'].setAlignment(
            control['individual_remove_btn'],
            Qt.AlignmentFlag.AlignVCenter
            if evenly
            else Qt.AlignmentFlag.AlignTop,
        )
        control['default_action'].setChecked(evenly)
        control['custom_action'].setChecked(not evenly)
        control['custom_btn'].setVisible(has_groups and (not evenly))

    def place_add_level_buttons(self) -> None:
        for button in self.add_level_buttons:
            button.setParent(self.levels_host)
            button.hide()
        if not self.levels:
            individual = self.state.groups_individual and self.panel_count > 1
            self.empty_row.setVisible(not individual)
            self.empty_individual_rows.setVisible(individual)
            if individual:
                for panel_position, button in enumerate(self.add_level_buttons):
                    target_layout = self.empty_panel_layouts[panel_position]
                    target_layout.insertWidget(max(1, target_layout.count() - 1), button)
                    button.show()
            else:
                self.empty_layout.insertWidget(1, self.add_level_btn)
                self.add_level_btn.show()
            return
        self.empty_row.hide()
        self.empty_individual_rows.hide()
        last_level = self.levels[-1]
        if self.state.groups_individual and self.panel_count > 1:
            for panel_position, button in enumerate(self.add_level_buttons):
                active_levels = [level for level in self.levels if level['panel_controls'][panel_position]['active']]
                if active_levels:
                    target_control = active_levels[-1]['panel_controls'][panel_position]
                    target_layout = target_control['layout']
                else:
                    target_control = self.levels[0]['panel_controls'][panel_position]
                    target_control['panel_row'].show()
                    target_control['panel_label'].show()
                    target_control['row'].show()
                    with QSignalBlocker(target_control['group_spin']):
                        target_control['group_spin'].setValue(0)
                    self.update_control(target_control, self.lane_counts[panel_position])
                    target_control['individual_remove_btn'].hide()
                    target_layout = target_control['layout']
                target_layout.insertWidget(
                    max(0, target_layout.count() - 1),
                    button,
                    0,
                    Qt.AlignmentFlag.AlignVCenter
                    if target_control['group_mode'].currentData() != 'custom'
                    else Qt.AlignmentFlag.AlignTop,
                )
                button.show()
        else:
            target_layout = last_level['shared_control']['layout']
            target_layout.insertWidget(
                max(0, target_layout.count() - 1),
                self.add_level_btn,
                0,
                Qt.AlignmentFlag.AlignVCenter
                if (
                    last_level['shared_control']['group_mode'].currentData()
                    != 'custom'
                )
                else Qt.AlignmentFlag.AlignTop,
            )
            self.add_level_btn.show()

    def update_level_names(self) -> None:
        for level_index, level in enumerate(self.levels, start=1):
            level['heading'].setText(tr('Group Level {number}', self._language, number=level_index))
            level['remove_btn'].setObjectName(f'removeLaneGroupLevel_common_level{level_index}')
            level['remove_btn'].setToolTip(tr('Remove Group Level {number}', self._language, number=level_index))
            shared = level['shared_control']
            shared['group_spin'].setObjectName(
                'laneGroupSpin_common'
                if level_index == 1
                else f'laneGroupSpin_common_level{level_index}'
            )
            shared['group_mode'].setObjectName(
                'laneGroupingCombo_common'
                if level_index == 1
                else f'laneGroupingCombo_common_level{level_index}'
            )
            shared['mode_selector'].setObjectName(
                'laneGroupingSelector_common'
                if level_index == 1
                else f'laneGroupingSelector_common_level{level_index}'
            )
            for panel_position, control in enumerate(level['panel_controls'], start=1):
                suffix = (
                    f'panel_{panel_position}'
                    if level_index == 1
                    else f'panel_{panel_position}_level{level_index}'
                )
                control['group_spin'].setObjectName(f'laneGroupSpin_{suffix}')
                control['group_mode'].setObjectName(f'laneGroupingCombo_{suffix}')
                control['mode_selector'].setObjectName(f'laneGroupingSelector_{suffix}')
                control['individual_remove_btn'].setObjectName(f'removeLaneGroupLevel_{suffix}')
        for panel_position, button in enumerate(self.add_level_buttons):
            active_count = (
                sum(
                    level['panel_controls'][panel_position]['active']
                    for level in self.levels
                )
                if self.state.groups_individual and self.panel_count > 1
                else len(self.levels)
            )
            prefix = 'common' if panel_position == 0 else f'panel_{panel_position + 1}'
            button.setObjectName(
                f'addLaneGroupLevel_{prefix}_level{active_count}'
                if active_count
                else f'addLaneGroupLevel_{prefix}_empty'
            )
        self.place_add_level_buttons()

    def update_levels(self) -> None:
        individual = self.state.groups_individual and self.panel_count > 1
        for level in self.levels:
            self.update_control(level['shared_control'], self.shared_lane_count)
            level['shared_control']['individual_remove_btn'].hide()
            for panel_position, control in enumerate(level['panel_controls']):
                if control['active']:
                    self.update_control(control, self.lane_counts[panel_position])
                else:
                    control['group_spin'].setMaximum(self.lane_counts[panel_position])
                    with QSignalBlocker(control['group_spin']):
                        control['group_spin'].setValue(0)
                    self.update_control(control, self.lane_counts[panel_position])
                control['panel_row'].setVisible(individual)
                control['panel_label'].setVisible(individual and control['active'])
                control['row'].setVisible(control['active'])
                control['individual_remove_btn'].setVisible(individual and control['active'])
            level['shared_row'].setVisible(not individual)
            level['individual_rows'].setVisible(individual)
            level['remove_btn'].setVisible(not individual)
        if individual:
            for panel_position in range(self.panel_count):
                panel_row_height = max([
                    26,
                    *(
                        level['panel_controls'][panel_position]['row']
                        .sizeHint()
                        .height()
                        for level in self.levels
                    ),
                ])
                for level in self.levels:
                    level['panel_controls'][panel_position]['panel_row'].setFixedHeight(panel_row_height)
        self.update_level_names()
        self.refresh_preview()

    def edit_custom_ranges(
        self, control: LaneGroupControl, lanes: int
    ) -> bool:
        groups = min(control['group_spin'].value(), lanes)
        defaults = self.control_ranges(control, lanes)
        result = self._request_custom_ranges(lanes, groups, defaults)
        if result is None:
            return False
        control['custom_ranges'] = result
        control['custom_lane_count'] = lanes
        self.refresh_preview()
        return True

    def on_group_mode_changed(
        self, control: LaneGroupControl, lanes: int
    ) -> None:
        if control['group_spin'].value() <= 0:
            control['custom_ranges'] = None
            control['custom_lane_count'] = None
            self.update_levels()
            return
        if control['group_mode'].currentData() == 'custom':
            groups = min(control['group_spin'].value(), lanes)
            control['custom_ranges'] = even_lane_group_ranges(lanes, groups)
            control['custom_lane_count'] = lanes
            if not self.edit_custom_ranges(control, lanes):
                control['group_mode'].blockSignals(True)
                control['group_mode'].setCurrentIndex(0)
                control['group_mode'].blockSignals(False)
                control['custom_ranges'] = None
                control['custom_lane_count'] = None
        else:
            control['custom_ranges'] = None
            control['custom_lane_count'] = None
        self.update_levels()

    def make_group_control(
        self, parent: QWidget, lanes: int
    ) -> LaneGroupControl:
        row = QWidget(parent)
        row.setObjectName('conditionLevelControls')
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        group_spin = self._make_spin(0, lanes, min(1, lanes))
        self.finish_spin_input_on_return(group_spin)
        group_mode = QComboBox(parent)
        group_mode.addItem(tr('Divide lanes evenly', self._language), 'default')
        group_mode.addItem(tr('Custom lane ranges…', self._language), 'custom')
        group_mode.hide()
        mode_selector, mode_menu = self.popup_selector('', 'Evenly', 53)
        default_action = mode_menu.addAction(tr('Divide lanes evenly', self._language))
        custom_action = mode_menu.addAction(tr('Custom lane ranges…', self._language))
        default_action.setCheckable(True)
        custom_action.setCheckable(True)
        default_action.setChecked(True)
        custom_btn = QPushButton(tr('Edit', self._language))
        custom_btn.setToolTip(tr('Edit Custom Ranges…', self._language))
        custom_btn.setStyleSheet(_SMALL_BTN_STYLE)
        custom_btn.setFixedSize(53, 22)
        custom_btn.hide()
        selector_column = QWidget(row)
        selector_column.setObjectName('conditionLaneDistributionColumn')
        selector_column.setFixedSize(53, 47)
        selector_layout = QVBoxLayout(selector_column)
        selector_layout.setContentsMargins(0, 0, 0, 0)
        selector_layout.setSpacing(3)
        selector_layout.addWidget(mode_selector)
        selector_layout.addWidget(custom_btn)
        selector_layout.addStretch(1)
        individual_remove_btn = QToolButton()
        individual_remove_btn.setText('×')
        individual_remove_btn.setToolTip(tr("Remove this panel's group level", self._language))
        individual_remove_btn.setFixedSize(20, 20)
        individual_remove_btn.setStyleSheet(
            'QToolButton { border:1px solid #B8C5BF; border-radius:5px; '
            'background:#F7FAF8; color:#52625A; font-weight:700; } '
            'QToolButton:hover { border-color:#C98282; '
            'background:#FBECEC; color:#9B3F3F; }'
        )
        individual_remove_btn.hide()
        layout.addWidget(group_spin)
        layout.addWidget(selector_column, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(individual_remove_btn, 0, Qt.AlignmentFlag.AlignTop)
        layout.addStretch(1)
        control: LaneGroupControl = {
            'row': row,
            'layout': layout,
            'group_spin': group_spin,
            'group_mode': group_mode,
            'mode_selector': mode_selector,
            'default_action': default_action,
            'custom_action': custom_action,
            'custom_btn': custom_btn,
            'selector_column': selector_column,
            'selector_layout': selector_layout,
            'individual_remove_btn': individual_remove_btn,
            'custom_ranges': None,
            'custom_lane_count': None,
            'active': True,
        }
        group_spin.valueChanged.connect(self.update_levels)
        group_mode.currentIndexChanged.connect(
            lambda _=0, ctl=control, count=lanes: (
                self.on_group_mode_changed(ctl, count)
            )
        )
        default_action.triggered.connect(lambda _=False, combo=group_mode: combo.setCurrentIndex(0))
        custom_action.triggered.connect(lambda _=False, combo=group_mode: combo.setCurrentIndex(1))
        custom_btn.clicked.connect(lambda _=False, ctl=control, count=lanes: self.edit_custom_ranges(ctl, count))
        return control

    def remove_level(self, level: LaneGroupLevel) -> None:
        if level not in self.levels:
            return
        self.levels.remove(level)
        level['container'].hide()
        level['container'].setParent(None)
        level['container'].deleteLater()
        self.update_levels()
        QTimer.singleShot(0, self.resize_dialog_to_visible_content)

    def copy_panel_control_state(
        self, source: LaneGroupControl, target: LaneGroupControl
    ) -> None:
        with QSignalBlocker(target['group_spin']):
            target['group_spin'].setValue(source['group_spin'].value())
        with QSignalBlocker(target['group_mode']):
            target['group_mode'].setCurrentIndex(source['group_mode'].currentIndex())
        target['custom_ranges'] = copy.deepcopy(source['custom_ranges'])
        target['custom_lane_count'] = source['custom_lane_count']
        target['active'] = source['active']

    def remove_panel_level(
        self, panel_position: int, level: LaneGroupLevel
    ) -> None:
        if level not in self.levels:
            return
        level_index = self.levels.index(level)
        control = level['panel_controls'][panel_position]
        if not control['active']:
            return
        panel_active_count = sum((candidate['panel_controls'][panel_position]['active'] for candidate in self.levels))
        if panel_active_count == 1:
            with QSignalBlocker(control['group_spin']):
                control['group_spin'].setValue(0)
            self.update_levels()
            QTimer.singleShot(0, self.resize_dialog_to_visible_content)
            return
        for index in range(level_index, len(self.levels) - 1):
            source = self.levels[index + 1]['panel_controls'][panel_position]
            target = self.levels[index]['panel_controls'][panel_position]
            self.copy_panel_control_state(source, target)
        last_control = self.levels[-1]['panel_controls'][panel_position]
        last_control['active'] = False
        last_control['custom_ranges'] = None
        last_control['custom_lane_count'] = None
        while len(self.levels) > 1 and (not any((control['active'] for control in self.levels[-1]['panel_controls']))):
            removed = self.levels.pop()
            removed['container'].hide()
            removed['container'].setParent(None)
            removed['container'].deleteLater()
        self.update_levels()
        QTimer.singleShot(0, self.resize_dialog_to_visible_content)

    def create_level(self, active_panels: set[int] | None=None) -> None:
        container = QWidget()
        container.setObjectName('conditionLevelContainer')
        container.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(6)
        heading_row = QWidget(container)
        heading_row.setObjectName('conditionLevelHeading')
        heading_layout = QHBoxLayout(heading_row)
        heading_layout.setContentsMargins(0, 0, 0, 0)
        heading_layout.setSpacing(5)
        heading = self.group_heading_label()
        remove_btn = QToolButton()
        remove_btn.setText('×')
        remove_btn.setFixedSize(20, 20)
        remove_btn.setStyleSheet(
            'QToolButton { border:1px solid #B8C5BF; border-radius:5px; '
            'background:#F7FAF8; color:#52625A; font-weight:700; } '
            'QToolButton:hover { border-color:#C98282; '
            'background:#FBECEC; color:#9B3F3F; }'
        )
        heading_layout.addWidget(heading)
        heading_layout.addWidget(remove_btn)
        heading_layout.addStretch(1)
        container_layout.addWidget(heading_row)
        shared_control = self.make_group_control(container, self.shared_lane_count)
        shared_row = shared_control['row']
        container_layout.addWidget(shared_row)
        individual_rows = QWidget(container)
        individual_rows.setObjectName('conditionPanelLevelControls')
        individual_layout = QVBoxLayout(individual_rows)
        individual_layout.setContentsMargins(0, 0, 0, 0)
        individual_layout.setSpacing(6)
        panel_controls: list[LaneGroupControl] = []
        for panel_position, lanes in enumerate(self.lane_counts):
            panel_row = QWidget(individual_rows)
            panel_row.setObjectName('conditionPanelLevelRow')
            panel_row_layout = QHBoxLayout(panel_row)
            panel_row_layout.setContentsMargins(0, 0, 0, 0)
            panel_row_layout.setSpacing(8)
            row_panel_label = self.panel_label(panel_position)
            panel_row_layout.addWidget(row_panel_label)
            control = self.make_group_control(panel_row, lanes)
            panel_row_layout.addWidget(control['row'])
            control['panel_row'] = panel_row
            control['panel_label'] = row_panel_label
            control['panel_row_layout'] = panel_row_layout
            control['active'] = active_panels is None or panel_position in active_panels
            panel_controls.append(control)
            individual_layout.addWidget(panel_row)
        individual_rows.hide()
        container_layout.addWidget(individual_rows)
        level: LaneGroupLevel = {
            'container': container,
            'heading': heading,
            'remove_btn': remove_btn,
            'shared_row': shared_row,
            'shared_control': shared_control,
            'individual_rows': individual_rows,
            'panel_controls': panel_controls,
        }
        self.levels.append(level)
        self.levels_layout.insertWidget(
            self.levels_layout.indexOf(self.empty_row),
            container,
            0,
            Qt.AlignmentFlag.AlignTop,
        )
        remove_btn.clicked.connect(lambda _=False, lv=level: self.remove_level(lv))
        for panel_position, control in enumerate(panel_controls):
            control['individual_remove_btn'].clicked.connect(
                lambda _=False, position=panel_position, lv=level: (
                    self.remove_panel_level(position, lv)
                )
            )
        self.update_levels()
        QTimer.singleShot(0, self.resize_dialog_to_visible_content)

    def add_level_for_panel(self, panel_position: int) -> None:
        individual = self.state.groups_individual and self.panel_count > 1
        if not individual:
            self.create_level()
            return
        active_count = sum((level['panel_controls'][panel_position]['active'] for level in self.levels))
        if active_count < len(self.levels):
            control = self.levels[active_count]['panel_controls'][panel_position]
            control['active'] = True
            with QSignalBlocker(control['group_spin']):
                control['group_spin'].setValue(1)
            if control['group_mode'].currentData() == 'custom':
                control['custom_ranges'] = even_lane_group_ranges(
                    self.lane_counts[panel_position], 1
                )
                control['custom_lane_count'] = self.lane_counts[panel_position]
            else:
                control['custom_ranges'] = None
                control['custom_lane_count'] = None
            self.update_levels()
            QTimer.singleShot(0, self.resize_dialog_to_visible_content)
            return
        self.create_level({panel_position})

    def set_rows_mode(self, individual: bool) -> None:
        new_individual = bool(individual and self.panel_count > 1)
        if new_individual and (not self.state.rows_individual):
            for spin in self.panel_rows_spins:
                with QSignalBlocker(spin):
                    spin.setValue(self.shared_rows_spin.value())
        self.state.rows_individual = new_individual
        self.rows_mode_selector.setText(
            tr('Set individual panels', self._language)
            if self.state.rows_individual
            else tr('Apply to all panels', self._language)
        )
        self.rows_apply_action.setChecked(not self.state.rows_individual)
        self.rows_individual_action.setChecked(self.state.rows_individual)
        self.rows_shared.setVisible(not self.state.rows_individual)
        self.rows_individual.setVisible(self.state.rows_individual)
        self.refresh_preview()
        QTimer.singleShot(0, self.resize_dialog_to_visible_content)

    def set_groups_mode(self, individual: bool) -> None:
        new_individual = bool(individual and self.panel_count > 1)
        if new_individual and (not self.state.groups_individual):
            for level in self.levels:
                shared_control = level['shared_control']
                for panel_control in level['panel_controls']:
                    self.copy_panel_control_state(shared_control, panel_control)
                    panel_control['active'] = True
        self.state.groups_individual = new_individual
        self.groups_mode_selector.setText(
            tr('Set individual panels', self._language)
            if self.state.groups_individual
            else tr('Apply to all panels', self._language)
        )
        self.groups_apply_action.setChecked(not self.state.groups_individual)
        self.groups_individual_action.setChecked(self.state.groups_individual)
        self.update_levels()
        QTimer.singleShot(0, self.resize_dialog_to_visible_content)

    def _connect_actions(self) -> None:
        for panel_position, button in enumerate(self.add_level_buttons):
            button.clicked.connect(lambda _=False, position=panel_position: self.add_level_for_panel(position))
        self.rows_apply_action.triggered.connect(lambda _=False: self.set_rows_mode(False))
        self.rows_individual_action.triggered.connect(lambda _=False: self.set_rows_mode(True))
        self.groups_apply_action.triggered.connect(lambda _=False: self.set_groups_mode(False))
        self.groups_individual_action.triggered.connect(lambda _=False: self.set_groups_mode(True))
        self.shared_rows_spin.valueChanged.connect(self.refresh_preview)
        for spin in self.panel_rows_spins:
            spin.valueChanged.connect(self.refresh_preview)
        self.outer.addWidget(self.lane_card)
        self.outer.addWidget(self.section_title('Condition Preview'))
        self.outer.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignHCenter)
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        cancel_btn = QPushButton('Cancel')
        cancel_btn.setObjectName('conditionCancelButton')
        create_btn = QPushButton('Create')
        create_btn.setObjectName('conditionCreateButton')
        for button in (cancel_btn, create_btn):
            button.setAutoDefault(False)
            button.setDefault(False)
        button_row.addWidget(cancel_btn)
        button_row.addWidget(create_btn)
        self.outer.addLayout(button_row)
        cancel_btn.clicked.connect(self.dialog.reject)
        create_btn.clicked.connect(self.dialog.accept)
        if self._tutorial_mode:
            self.create_level()
        self.set_rows_mode(False)
        self.set_groups_mode(False)
        self._retranslate(self.dialog)

    def result(
        self,
    ) -> list[tuple[int, int, int, list[list[tuple[int, int]]]]]:
        return [
            (
                panel_index,
                lane_count,
                self.condition_rows_for(panel_position),
                self.current_levels(panel_position),
            )
            for panel_position, (panel_index, lane_count) in enumerate(
                self.targets
            )
        ]

    def exec(
        self,
    ) -> list[tuple[int, int, int, list[list[tuple[int, int]]]]] | None:
        if self.dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return self.result()


class ConditionPreviewWidget(QWidget):
    """Live miniature of a condition matrix and its lane groups."""

    _GROUP_ROW_HEIGHT = 30.0
    _CONDITION_ROW_HEIGHT = 20.0
    _GROUP_FONT_SIZE_PT = 12.0
    _CONDITION_FONT_SIZE_PT = 13.0

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._lane_count = 6
        self._row_count = 3
        self._group_ranges = [(1, 3), (4, 6)]
        self._conditions = [
            (self._lane_count, self._row_count, [list(self._group_ranges)])
        ]
        self._cross_preview: dict | None = None
        self._layout_items: list[LayoutItem] | None = None
        self._layout_bounds: QRectF | None = None
        self._layout_panel_markers: list[tuple[str, float, float, float]] = []
        self.setFixedHeight(100)
        self.setStyleSheet(
            "background:#FFFFFF; border:1px solid #B8C7DA; border-radius:6px;"
        )

    def set_condition(
        self,
        lanes: int,
        rows: int,
        group_ranges: list[tuple[int, int]],
    ) -> None:
        self.set_conditions([(lanes, rows, group_ranges)])

    def set_conditions(
        self,
        conditions: list[tuple[int, int, object]],
    ) -> None:
        self._layout_items = None
        self._layout_bounds = None
        self._layout_panel_markers = []
        self._cross_preview = None
        normalized = []
        for lanes, rows, raw_groups in conditions:
            groups = list(raw_groups) if isinstance(raw_groups, list) else []
            if groups and isinstance(groups[0], tuple):
                levels = [list(groups)]
            elif groups and isinstance(groups[0], list):
                levels = [list(level) for level in groups]
            else:
                levels = []
            normalized.append((
                max(1, int(lanes)),
                max(1, int(rows)),
                levels,
            ))
        if not normalized:
            normalized = [(1, 1, [[(1, 1)]])]
        self._conditions = normalized
        self._lane_count, self._row_count, levels = normalized[0]
        self._group_ranges = list(levels[0]) if levels else []
        # Keep the preview compact like the actual condition table instead of
        # stretching it to consume all remaining dialog height.
        max_rows = max(rows for _lanes, rows, _levels in normalized)
        max_levels = max(
            sum(bool(level) for level in levels)
            for _lanes, _rows, levels in normalized
        )
        panel_title_height = 13 if len(normalized) > 1 else 0
        self.setFixedHeight(
            min(
                300,
                10
                + panel_title_height
                + max_levels * self._GROUP_ROW_HEIGHT
                + max_rows * self._CONDITION_ROW_HEIGHT,
            )
        )
        self.update()

    def set_cross_panel_conditions(
        self,
        lane_counts: list[int],
        rows: int,
        group_levels: list[list[tuple[int, int]]],
        panel_start: int,
        panel_end: int,
    ) -> None:
        self._layout_items = None
        self._layout_bounds = None
        self._layout_panel_markers = []
        normalized_lanes = [max(1, int(value)) for value in lane_counts]
        if not normalized_lanes:
            normalized_lanes = [1]
        start = max(0, min(len(normalized_lanes) - 1, int(panel_start)))
        end = max(start, min(len(normalized_lanes) - 1, int(panel_end)))
        normalized_levels = [list(level) for level in group_levels]
        self._cross_preview = {
            "lane_counts": normalized_lanes,
            "rows": max(1, int(rows)),
            "levels": normalized_levels,
            "panel_start": start,
            "panel_end": end,
        }
        self._conditions = [
            (lanes, max(1, int(rows)), []) for lanes in normalized_lanes
        ]
        self._lane_count = normalized_lanes[0]
        self._row_count = max(1, int(rows))
        self._group_ranges = []
        visible_levels = sum(bool(level) for level in normalized_levels)
        self.setFixedHeight(
            min(
                300,
                23
                + visible_levels * self._GROUP_ROW_HEIGHT
                + self._row_count * self._CONDITION_ROW_HEIGHT,
            )
        )
        self.update()

    def condition(self) -> tuple[int, int, list[tuple[int, int]]]:
        return self._lane_count, self._row_count, list(self._group_ranges)

    def conditions(self) -> list[tuple[int, int, object]]:
        result: list[tuple[int, int, object]] = []
        for lanes, rows, levels in self._conditions:
            group_data: object = (
                list(levels[0])
                if len(levels) == 1
                else [list(level) for level in levels]
            )
            result.append((lanes, rows, group_data))
        return result

    def set_layout_project(self, project: FigureProject) -> None:
        """Preview the exact condition-table layout produced after Create."""
        layout = LayoutEngine().compute(project)
        items = [
            item
            for item in layout.items
            if (
                item.source_ref is not None
                and item.source_ref.field in {
                    "condition_cell",
                    "condition_line",
                }
            )
        ]
        self._layout_items = items
        self._cross_preview = None
        self._layout_panel_markers = []
        if not items:
            self._layout_bounds = None
            self.setFixedHeight(40)
            self.update()
            return
        left = min(item.x_pt for item in items)
        top = min(item.y_pt for item in items)
        right = max(item.x_pt + item.w_pt for item in items)
        bottom = max(item.y_pt + max(0.0, item.h_pt) for item in items)
        panel_indices = sorted({
            item.source_ref.panel_idx
            for item in items
            if item.source_ref is not None
            and item.source_ref.panel_idx is not None
        })
        if len(panel_indices) > 1:
            marker_ranges: list[tuple[str, float, float]] = []
            panel_tops: list[float] = []
            for panel_number, panel_index in enumerate(panel_indices, start=1):
                panel_items = [
                    item
                    for item in items
                    if (
                        item.source_ref is not None
                        and item.source_ref.panel_idx == panel_index
                    )
                ]
                if not panel_items:
                    continue
                table = project.panels[panel_index].condition_table
                condition_row_indices = {
                    row_index
                    for row_index, row in enumerate(table.rows if table else [])
                    if row and row[0].startswith("Condition ")
                }
                lane_items = [
                    item
                    for item in panel_items
                    if (
                        item.source_ref is not None
                        and item.source_ref.table_row in condition_row_indices
                        and item.source_ref.table_col is not None
                        and item.source_ref.table_col > 0
                    )
                ] or panel_items
                panel_left = min(item.x_pt for item in lane_items)
                panel_right = max(
                    item.x_pt + item.w_pt for item in lane_items
                )
                panel_top = min(item.y_pt for item in panel_items)
                marker_ranges.append((
                    f"Panel {panel_number}",
                    panel_left,
                    panel_right,
                ))
                panel_tops.append(panel_top)
            marker_y = min(panel_tops, default=top) - 15.0
            for text, panel_left, panel_right in marker_ranges:
                self._layout_panel_markers.append((
                    text,
                    panel_left,
                    panel_right,
                    marker_y,
                ))
            top = min(top, marker_y)
        self._layout_bounds = QRectF(
            left,
            top,
            max(1.0, right - left),
            max(1.0, bottom - top),
        )
        available_width = max(40.0, self.width() - 20.0)
        scale = available_width / self._layout_bounds.width()
        self.setFixedHeight(
            min(
                300,
                max(40, int(round(self._layout_bounds.height() * scale + 10))),
            )
        )
        self.update()

    def layout_items(self) -> list[LayoutItem]:
        return list(self._layout_items or [])

    def layout_panel_labels(self) -> list[str]:
        return [marker[0] for marker in self._layout_panel_markers]

    def layout_panel_markers(
        self,
    ) -> list[tuple[str, float, float, float]]:
        return list(self._layout_panel_markers)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._layout_items is not None:
            self._paint_layout_conditions(painter)
            return
        if self._cross_preview is not None:
            self._paint_cross_panel_condition(painter)
            return
        area = QRectF(self.rect()).adjusted(10.0, 5.0, -10.0, -5.0)
        panel_gap = 10.0 if len(self._conditions) > 1 else 0.0
        panel_width = max(
            40.0,
            (
                area.width()
                - panel_gap * (len(self._conditions) - 1)
            ) / len(self._conditions),
        )
        for panel_index, condition in enumerate(self._conditions):
            panel_area = QRectF(
                area.left() + panel_index * (panel_width + panel_gap),
                area.top(),
                panel_width,
                area.height(),
            )
            self._paint_condition(
                painter,
                panel_area,
                condition,
                panel_index if len(self._conditions) > 1 else None,
            )

    def _paint_layout_conditions(self, painter: QPainter) -> None:
        bounds = self._layout_bounds
        items = self._layout_items or []
        if bounds is None or not items:
            return
        area = QRectF(self.rect()).adjusted(10.0, 5.0, -10.0, -5.0)
        scale = min(
            area.width() / bounds.width(),
            area.height() / bounds.height(),
        )
        offset_x = area.left() + (area.width() - bounds.width() * scale) / 2.0
        offset_y = area.top() + (area.height() - bounds.height() * scale) / 2.0
        if self._layout_panel_markers:
            marker_font = QFont("Arial")
            marker_font.setBold(True)
            marker_font.setPointSizeF(
                max(7.0, 10.0 * min(scale, 1.5) / 1.5)
            )
            painter.setFont(marker_font)
            painter.setPen(QColor("#456455"))
            for text, marker_left, marker_right, marker_y in (
                self._layout_panel_markers
            ):
                x = offset_x + (marker_left - bounds.left()) * scale
                y = offset_y + (marker_y - bounds.top()) * scale
                painter.drawText(
                    QRectF(
                        x,
                        y,
                        max(1.0, marker_right - marker_left) * scale,
                        15.0 * scale,
                    ),
                    Qt.AlignmentFlag.AlignCenter,
                    text,
                )
        for item in sorted(items, key=lambda candidate: candidate.z_order):
            x = offset_x + (item.x_pt - bounds.left()) * scale
            y = offset_y + (item.y_pt - bounds.top()) * scale
            width = item.w_pt * scale
            height = item.h_pt * scale
            if item.kind == "line":
                painter.setPen(QPen(
                    QColor(item.line_color),
                    max(0.7, item.line_width_pt * scale),
                ))
                painter.drawLine(
                    QPointF(x, y),
                    QPointF(x + width, y + height),
                )
                continue
            font = QFont(item.font_family)
            font.setPointSizeF(
                max(5.0, item.font_size_pt * min(scale, 1.5) / 1.5)
            )
            font.setBold(item.bold)
            font.setItalic(item.italic)
            font.setUnderline(item.underline)
            painter.setFont(font)
            painter.setPen(QColor("#111111"))
            alignment = Qt.AlignmentFlag.AlignVCenter
            if item.align == "center":
                alignment |= Qt.AlignmentFlag.AlignHCenter
            elif item.align == "right":
                alignment |= Qt.AlignmentFlag.AlignRight
            else:
                alignment |= Qt.AlignmentFlag.AlignLeft
            painter.drawText(
                QRectF(x, y, width, height),
                alignment,
                item.text,
            )

    def _paint_cross_panel_condition(self, painter: QPainter) -> None:
        model = self._cross_preview
        if model is None:
            return
        lane_counts = model["lane_counts"]
        row_count = model["rows"]
        levels = [level for level in model["levels"] if level]
        panel_start = model["panel_start"]
        panel_end = model["panel_end"]
        area = QRectF(self.rect()).adjusted(10.0, 5.0, -10.0, -5.0)
        label_w = min(92.0, area.width() * 0.24)
        matrix_x = area.left() + label_w
        matrix_w = max(60.0, area.width() - label_w)
        panel_gap = 10.0 if len(lane_counts) > 1 else 0.0
        usable_w = max(40.0, matrix_w - panel_gap * (len(lane_counts) - 1))
        total_lanes = max(1, sum(lane_counts))
        panel_widths = [usable_w * lanes / total_lanes for lanes in lane_counts]
        panel_lefts: list[float] = []
        cursor_x = matrix_x
        for width in panel_widths:
            panel_lefts.append(cursor_x)
            cursor_x += width + panel_gap

        panel_font = painter.font()
        panel_font.setPointSizeF(7.5)
        panel_font.setBold(True)
        painter.setFont(panel_font)
        painter.setPen(QColor("#456455"))
        for panel_index, (left, width) in enumerate(
            zip(panel_lefts, panel_widths)
        ):
            painter.setPen(QColor("#456455"))
            painter.drawText(
                QRectF(left, area.top(), width, 13.0),
                Qt.AlignmentFlag.AlignCenter,
                f"Panel {panel_index + 1}",
            )
            if panel_index > 0:
                boundary_x = left - panel_gap / 2.0
                boundary_pen = QPen(QColor("#A8B5AF"), 0.8)
                boundary_pen.setStyle(Qt.PenStyle.DotLine)
                painter.setPen(boundary_pen)
                painter.drawLine(
                    QPointF(boundary_x, area.top() + 13.0),
                    QPointF(boundary_x, area.bottom()),
                )

        selected_cells: list[tuple[float, float]] = []
        for panel_index in range(panel_start, panel_end + 1):
            lane_width = panel_widths[panel_index] / lane_counts[panel_index]
            selected_cells.extend([
                (
                    panel_lefts[panel_index] + lane_index * lane_width,
                    lane_width,
                )
                for lane_index in range(lane_counts[panel_index])
            ])

        group_font = painter.font()
        group_font.setPointSizeF(self._GROUP_FONT_SIZE_PT)
        group_font.setBold(True)
        painter.setFont(group_font)
        painter.setPen(QColor("#111111"))
        for visual_row, group_ranges in enumerate(reversed(levels)):
            level_y = (
                area.top()
                + 13.0
                + visual_row * self._GROUP_ROW_HEIGHT
            )
            for group_index, (start, end) in enumerate(group_ranges):
                if not selected_cells:
                    continue
                start = max(1, min(len(selected_cells), start))
                end = max(start, min(len(selected_cells), end))
                group_x = selected_cells[start - 1][0]
                last_x, last_w = selected_cells[end - 1]
                group_w = last_x + last_w - group_x
                title_w = max(52.0, group_w)
                painter.drawText(
                    QRectF(
                        group_x - (title_w - group_w) / 2.0,
                        level_y,
                        title_w,
                        20.0,
                    ),
                    Qt.AlignmentFlag.AlignCenter,
                    f"Group {group_index + 1}",
                )
                inset = max(5.0, min(10.0, group_w * 0.10))
                painter.setPen(QPen(QColor("#111111"), 1.1))
                painter.drawLine(
                    QPointF(group_x + inset, level_y + 24.0),
                    QPointF(group_x + group_w - inset, level_y + 24.0),
                )

        group_height = self._GROUP_ROW_HEIGHT * len(levels)
        body_top = area.top() + 13.0 + group_height
        body_h = max(
            self._CONDITION_ROW_HEIGHT,
            (area.bottom() - body_top) / row_count,
        )
        body_font = painter.font()
        body_font.setPointSizeF(self._CONDITION_FONT_SIZE_PT)
        body_font.setBold(False)
        for row_index in range(row_count):
            y = body_top + row_index * body_h
            label_font = QFont(body_font)
            label_font.setBold(True)
            painter.setFont(label_font)
            painter.setPen(QColor("#111111"))
            painter.drawText(
                QRectF(area.left(), y, label_w - 5.0, body_h),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"Condition {row_index + 1}",
            )
            painter.setFont(body_font)
            for panel_index, lane_count in enumerate(lane_counts):
                lane_width = panel_widths[panel_index] / lane_count
                for lane_index in range(lane_count):
                    value = "+" if (lane_index + row_index) % 3 == 1 else "-"
                    painter.drawText(
                        QRectF(
                            panel_lefts[panel_index] + lane_index * lane_width,
                            y,
                            lane_width,
                            body_h,
                        ),
                        Qt.AlignmentFlag.AlignCenter,
                        value,
                    )

    def _paint_condition(
        self,
        painter: QPainter,
        area: QRectF,
        condition: tuple[int, int, list[list[tuple[int, int]]]],
        panel_index: int | None,
    ) -> None:
        lane_count, row_count, group_levels = condition
        visible_group_levels = [
            group_ranges for group_ranges in group_levels if group_ranges
        ]
        panel_title_h = 13.0 if panel_index is not None else 0.0
        if panel_index is not None:
            panel_font = painter.font()
            panel_font.setPointSizeF(7.5)
            panel_font.setBold(True)
            painter.setFont(panel_font)
            painter.setPen(QColor("#456455"))
            painter.drawText(
                QRectF(area.left(), area.top(), area.width(), panel_title_h),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                f"Panel {panel_index + 1}",
            )
        area = area.adjusted(0.0, panel_title_h, 0.0, 0.0)
        label_w = (
            min(115.0, area.width() * 0.42)
            if panel_index is not None
            else min(105.0, area.width() * 0.30)
        )
        matrix_x = area.left() + label_w
        matrix_w = max(40.0, area.width() - label_w)
        col_w = matrix_w / lane_count
        group_h = self._GROUP_ROW_HEIGHT * len(visible_group_levels)
        row_h = max(
            self._CONDITION_ROW_HEIGHT,
            (area.height() - group_h) / row_count,
        )

        group_font = painter.font()
        group_font.setPointSizeF(self._GROUP_FONT_SIZE_PT)
        group_font.setBold(True)
        painter.setFont(group_font)
        painter.setPen(QColor("#111111"))
        # Higher levels appear above Level 1, matching the final canvas.
        for visual_row, group_ranges in enumerate(reversed(visible_group_levels)):
            level_y = area.top() + visual_row * self._GROUP_ROW_HEIGHT
            for group_index, (start, end) in enumerate(group_ranges):
                start = max(1, min(lane_count, start))
                end = max(start, min(lane_count, end))
                x = matrix_x + (start - 1) * col_w
                width = (end - start + 1) * col_w
                group_font.setPointSizeF(self._GROUP_FONT_SIZE_PT)
                painter.setFont(group_font)
                title_width = max(width, 54.0)
                painter.drawText(
                    QRectF(
                        x - (title_width - width) / 2.0,
                        level_y,
                        title_width,
                        20.0,
                    ),
                    Qt.AlignmentFlag.AlignCenter,
                    f"Group {group_index + 1}",
                )
                inset = max(5.0, min(12.0, width * 0.10))
                painter.setPen(QPen(QColor("#111111"), 1.2))
                painter.drawLine(
                    QPointF(x + inset, level_y + 24.0),
                    QPointF(x + width - inset, level_y + 24.0),
                )

        body_font = painter.font()
        body_font.setPointSizeF(self._CONDITION_FONT_SIZE_PT)
        body_font.setBold(False)
        painter.setFont(body_font)
        for row_index in range(row_count):
            y = area.top() + group_h + row_index * row_h
            label_font = QFont(body_font)
            label_font.setBold(True)
            painter.setFont(label_font)
            painter.setPen(QColor("#111111"))
            painter.drawText(
                QRectF(area.left(), y, label_w - 5.0, row_h),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"Condition {row_index + 1}",
            )
            painter.setFont(body_font)
            for lane_index in range(lane_count):
                value = "+" if (lane_index + row_index) % 3 == 1 else "-"
                painter.drawText(
                    QRectF(matrix_x + lane_index * col_w, y, col_w, row_h),
                    Qt.AlignmentFlag.AlignCenter,
                    value,
                )
