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
from core.figure_project import (
    BlotSlot, ConditionTable, FigureProject,
    GlobalLayout, ImageBBox, LaneROI, Panel, SourceRef,
)
from core.layout_engine import LayoutEngine, LayoutItem, LayoutResult, pt_to_scene
from core.template_engine import TemplateEngine
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


class _ConditionPreviewWidget(QWidget):
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
        targets = self._current_condition_targets()
        if not targets:
            QMessageBox.information(
                self,
                "Create Blot Condition Template",
                "No Western panel with detected lanes is available.",
            )
            return

        panel_count = len(targets)
        lane_counts = [lane_count for _panel_index, lane_count in targets]
        shared_lane_count = max(1, min(lane_counts))

        dialog = QDialog(self)
        dialog.setObjectName("modernConditionDialog")
        dialog.setWindowTitle("Create Blot Condition Template")
        dialog.setModal(True)
        dialog.setMinimumWidth(620)
        dialog.setStyleSheet(
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
            "QDialog#modernConditionDialog QWidget#conditionLaneDistributionColumn { "
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

        outer = QVBoxLayout(dialog)
        outer.setContentsMargins(22, 20, 22, 18)
        outer.setSpacing(14)

        def section_title(text: str) -> QLabel:
            label = QLabel(tr(text, self._language))
            label.setObjectName("conditionSectionTitle")
            return label

        def field_label(text: str) -> QLabel:
            label = QLabel(tr(text, self._language))
            label.setObjectName("conditionFieldLabel")
            label.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            return label

        def panel_label(panel_position: int) -> QLabel:
            label = QLabel(
                tr("Panel {number}", self._language, number=panel_position + 1)
            )
            label.setObjectName("conditionPanelLabel")
            label.setMinimumWidth(54)
            label.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            return label

        def group_heading_label(text: str = "") -> QLabel:
            label = QLabel(text)
            label.setObjectName("conditionGroupHeading")
            label.setAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
            return label

        def popup_selector(
            object_name: str,
            text: str,
            width: int,
        ) -> tuple[QToolButton, QMenu]:
            selector = QToolButton()
            selector.setObjectName(object_name)
            selector.setProperty("conditionModeSelector", True)
            selector.setText(tr(text, self._language))
            selector.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            selector.setPopupMode(
                QToolButton.ToolButtonPopupMode.InstantPopup
            )
            selector.setFixedSize(width, 22)
            menu = QMenu(selector)
            menu.setObjectName("conditionModeMenu")
            selector.setMenu(menu)
            return selector, menu

        def section_mode_selector(
            object_name: str,
        ) -> tuple[QToolButton, QAction, QAction]:
            selector, menu = popup_selector(
                object_name,
                "Apply to all panels",
                146,
            )
            apply_all = menu.addAction(tr("Apply to all panels", self._language))
            individual = menu.addAction(tr("Set individual panels", self._language))
            apply_all.setCheckable(True)
            individual.setCheckable(True)
            apply_all.setChecked(True)
            selector.setVisible(panel_count > 1)
            return selector, apply_all, individual

        modes = {
            "rows_individual": False,
            "groups_individual": False,
        }

        def finish_spin_input_on_return(spin: QSpinBox) -> None:
            editor = spin.lineEdit()

            def finish_input() -> None:
                spin.interpretText()
                editor.deselect()
                spin.clearFocus()

            editor.returnPressed.connect(
                lambda: QTimer.singleShot(0, finish_input)
            )

        # Condition rows section.
        rows_card = QFrame()
        rows_card.setObjectName("conditionRowsCard")
        rows_card_layout = QVBoxLayout(rows_card)
        rows_card_layout.setContentsMargins(16, 14, 16, 14)
        rows_card_layout.setSpacing(9)
        rows_header = QHBoxLayout()
        rows_header.setSpacing(9)
        rows_header.addWidget(section_title("Condition rows"))
        rows_mode_selector, rows_apply_action, rows_individual_action = (
            section_mode_selector("conditionRowsModeSelector")
        )
        rows_header.addWidget(rows_mode_selector)
        rows_header.addStretch(1)
        rows_card_layout.addLayout(rows_header)

        rows_shared = QWidget()
        rows_shared.setObjectName("conditionRowsShared")
        rows_shared_layout = QHBoxLayout(rows_shared)
        rows_shared_layout.setContentsMargins(0, 0, 0, 0)
        rows_shared_layout.setSpacing(8)
        rows_shared_layout.addWidget(group_heading_label("Condition rows #:"))
        shared_rows_spin = self._make_structure_spin(1, 20, 1)
        finish_spin_input_on_return(shared_rows_spin)
        shared_rows_spin.setObjectName("conditionRowsSpin_common")
        rows_shared_layout.addWidget(shared_rows_spin)
        rows_shared_layout.addStretch(1)
        rows_card_layout.addWidget(rows_shared)

        rows_individual = QWidget()
        rows_individual.setObjectName("conditionRowsIndividual")
        rows_individual_layout = QGridLayout(rows_individual)
        rows_individual_layout.setContentsMargins(0, 0, 0, 0)
        rows_individual_layout.setHorizontalSpacing(8)
        rows_individual_layout.setVerticalSpacing(6)
        rows_individual_layout.addWidget(
            group_heading_label("Condition rows #:"),
            0,
            0,
            1,
            2,
            Qt.AlignmentFlag.AlignLeft,
        )
        panel_rows_spins: list[QSpinBox] = []
        for panel_position in range(panel_count):
            spin = self._make_structure_spin(1, 20, 1)
            finish_spin_input_on_return(spin)
            spin.setObjectName(
                f"conditionRowsSpin_panel_{panel_position + 1}"
            )
            rows_individual_layout.addWidget(
                panel_label(panel_position), panel_position + 1, 0
            )
            rows_individual_layout.addWidget(
                spin,
                panel_position + 1,
                1,
                Qt.AlignmentFlag.AlignLeft,
            )
            panel_rows_spins.append(spin)
        rows_individual_layout.setColumnStretch(2, 1)
        rows_individual.hide()
        rows_card_layout.addWidget(rows_individual)
        outer.addWidget(rows_card)

        # Lane groups section.
        lane_card = QFrame()
        lane_card.setObjectName("conditionLaneGroupsCard")
        lane_layout = QVBoxLayout(lane_card)
        lane_layout.setContentsMargins(16, 14, 16, 14)
        lane_layout.setSpacing(10)
        lane_header = QHBoxLayout()
        lane_header.setSpacing(9)
        lane_header.addWidget(section_title("Lane groups"))
        groups_mode_selector, groups_apply_action, groups_individual_action = (
            section_mode_selector("laneGroupsModeSelector")
        )
        lane_header.addWidget(groups_mode_selector)
        lane_header.addStretch(1)
        lane_layout.addLayout(lane_header)

        levels_host = QWidget()
        levels_host.setObjectName("conditionLevelsHost")
        levels_layout = QHBoxLayout(levels_host)
        levels_layout.setContentsMargins(0, 0, 0, 0)
        levels_layout.setSpacing(10)
        levels_layout.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        lane_layout.addWidget(levels_host)

        levels: list[dict] = []
        def make_add_level_button() -> QToolButton:
            button = QToolButton()
            button.setText("+")
            button.setToolTip(tr("Add another lane-group level", self._language))
            button.setFixedSize(22, 22)
            button.setStyleSheet(
                "QToolButton { border:1px solid #8FB7A6; "
                "border-radius:5px; background:#E8F3EE; color:#24513D; "
                "font-size:13px; font-weight:700; } "
                "QToolButton:hover { background:#D3E9DF; }"
            )
            return button

        add_level_buttons = [
            make_add_level_button() for _panel_position in range(panel_count)
        ]
        add_level_btn = add_level_buttons[0]

        empty_row = QWidget()
        empty_layout = QHBoxLayout(empty_row)
        empty_layout.setContentsMargins(0, 0, 0, 0)
        empty_layout.setSpacing(7)
        empty_level_label = QLabel(tr("No lane groups", self._language))
        empty_level_label.setObjectName("conditionHint")
        empty_layout.addWidget(empty_level_label)
        empty_layout.addWidget(add_level_btn)
        empty_layout.addStretch(1)
        levels_layout.addWidget(empty_row)
        empty_individual_rows = QWidget()
        empty_individual_layout = QVBoxLayout(empty_individual_rows)
        empty_individual_layout.setContentsMargins(0, 0, 0, 0)
        empty_individual_layout.setSpacing(6)
        empty_panel_layouts: list[QHBoxLayout] = []
        for panel_position in range(panel_count):
            panel_empty_row = QWidget(empty_individual_rows)
            panel_empty_row.setObjectName("conditionPanelLevelRow")
            panel_empty_layout = QHBoxLayout(panel_empty_row)
            panel_empty_layout.setContentsMargins(0, 0, 0, 0)
            panel_empty_layout.setSpacing(6)
            panel_empty_layout.addWidget(panel_label(panel_position))
            panel_empty_layout.addStretch(1)
            empty_individual_layout.addWidget(panel_empty_row)
            empty_panel_layouts.append(panel_empty_layout)
        empty_individual_rows.hide()
        levels_layout.addWidget(empty_individual_rows)
        levels_layout.addStretch(1)

        preview = _ConditionPreviewWidget(dialog)
        preview.setFixedWidth(
            430 if panel_count == 1 else min(810, panel_count * 270)
        )
        preview.setStyleSheet(
            "background:#FFFFFF; border:1px solid #D6E0DB; "
            "border-radius:8px;"
        )

        def resize_dialog_to_visible_content() -> None:
            layout = dialog.layout()
            if layout is None:
                return
            layout.invalidate()
            layout.activate()
            hint = dialog.sizeHint()
            dialog.resize(max(dialog.minimumWidth(), hint.width()), hint.height())

        def condition_rows_for(panel_position: int) -> int:
            if modes["rows_individual"] and panel_count > 1:
                return panel_rows_spins[panel_position].value()
            return shared_rows_spin.value()

        def control_ranges(
            control: dict,
            lanes: int,
        ) -> list[tuple[int, int]]:
            groups = min(control["group_spin"].value(), max(1, lanes))
            if groups <= 0:
                return []
            if (
                control["group_mode"].currentData() == "custom"
                and control["custom_lane_count"] == lanes
                and control["custom_ranges"] is not None
                and len(control["custom_ranges"]) == groups
            ):
                return list(control["custom_ranges"])
            return self._even_lane_group_ranges(lanes, groups)

        def level_control(level: dict, panel_position: int) -> dict:
            if modes["groups_individual"] and panel_count > 1:
                return level["panel_controls"][panel_position]
            return level["shared_control"]

        def current_levels(
            panel_position: int,
        ) -> list[list[tuple[int, int]]]:
            lanes = lane_counts[panel_position]
            result: list[list[tuple[int, int]]] = []
            for level in levels:
                control = level_control(level, panel_position)
                if (
                    modes["groups_individual"]
                    and panel_count > 1
                    and not control["active"]
                ):
                    continue
                result.append(control_ranges(control, lanes))
            return result

        def refresh_preview() -> None:
            preview_project = copy.deepcopy(self._project)
            if preview_project is None:
                return
            preview_conditions = [
                (
                    lane_count,
                    condition_rows_for(panel_position),
                    current_levels(panel_position),
                )
                for panel_position, lane_count in enumerate(lane_counts)
            ]
            preview.set_conditions(preview_conditions)
            for panel_position, (panel_index, lane_count) in enumerate(targets):
                preview_project.panels[panel_index].condition_table = (
                    self._make_custom_condition_table(
                        lane_count,
                        preview_conditions[panel_position][1],
                        preview_conditions[panel_position][2],
                    )
                )
            preview_project.global_layout.show_condition_table = True
            preview_project.global_layout.condition_table_row_height_pt = 13.0
            preview.set_layout_project(preview_project)

        def update_control(control: dict, lanes: int) -> None:
            control["group_spin"].setMaximum(max(1, lanes))
            has_groups = control["group_spin"].value() > 0
            # A zero-valued individual placeholder is still the panel's Level
            # 1 control. Keep its selected distribution mode intact so adding
            # the level again restores the same setup instead of silently
            # resetting it to Evenly.
            control["group_mode"].setEnabled(True)
            evenly = control["group_mode"].currentData() != "custom"
            control["selector_layout"].setContentsMargins(
                0,
                12 if evenly else 0,
                0,
                0,
            )
            control["mode_selector"].setEnabled(True)
            selector_width = 53
            control["mode_selector"].setFixedSize(selector_width, 22)
            control["mode_selector"].setText(
                tr("Evenly", self._language)
                if evenly
                else tr("Custom", self._language)
            )
            control["mode_selector"].setToolTip(
                tr("Divide lanes evenly", self._language)
                if evenly
                else tr("Custom lane ranges…", self._language)
            )
            control["custom_btn"].setFixedSize(selector_width, 22)
            control["layout"].setAlignment(
                control["individual_remove_btn"],
                Qt.AlignmentFlag.AlignVCenter
                if evenly
                else Qt.AlignmentFlag.AlignTop,
            )
            control["default_action"].setChecked(evenly)
            control["custom_action"].setChecked(not evenly)
            control["custom_btn"].setVisible(has_groups and not evenly)

        def place_add_level_buttons() -> None:
            for button in add_level_buttons:
                button.setParent(levels_host)
                button.hide()
            if not levels:
                individual = (
                    modes["groups_individual"] and panel_count > 1
                )
                empty_row.setVisible(not individual)
                empty_individual_rows.setVisible(individual)
                if individual:
                    for panel_position, button in enumerate(
                        add_level_buttons
                    ):
                        target_layout = empty_panel_layouts[panel_position]
                        target_layout.insertWidget(
                            max(1, target_layout.count() - 1),
                            button,
                        )
                        button.show()
                else:
                    empty_layout.insertWidget(1, add_level_btn)
                    add_level_btn.show()
                return
            empty_row.hide()
            empty_individual_rows.hide()
            last_level = levels[-1]
            if modes["groups_individual"] and panel_count > 1:
                for panel_position, button in enumerate(add_level_buttons):
                    active_levels = [
                        level
                        for level in levels
                        if level["panel_controls"][panel_position]["active"]
                    ]
                    if active_levels:
                        target_control = active_levels[-1]["panel_controls"][
                            panel_position
                        ]
                        target_layout = target_control["layout"]
                    else:
                        target_control = levels[0]["panel_controls"][
                            panel_position
                        ]
                        target_control["panel_row"].show()
                        target_control["panel_label"].show()
                        target_control["row"].show()
                        with QSignalBlocker(target_control["group_spin"]):
                            target_control["group_spin"].setValue(0)
                        update_control(
                            target_control,
                            lane_counts[panel_position],
                        )
                        target_control["individual_remove_btn"].hide()
                        target_layout = target_control["layout"]
                    target_layout.insertWidget(
                        max(0, target_layout.count() - 1),
                        button,
                        0,
                        (
                            Qt.AlignmentFlag.AlignVCenter
                            if target_control["group_mode"].currentData()
                            != "custom"
                            else Qt.AlignmentFlag.AlignTop
                        ),
                    )
                    button.show()
            else:
                target_layout = last_level["shared_control"]["layout"]
                target_layout.insertWidget(
                    max(0, target_layout.count() - 1),
                    add_level_btn,
                    0,
                    (
                        Qt.AlignmentFlag.AlignVCenter
                        if last_level["shared_control"]["group_mode"]
                        .currentData()
                        != "custom"
                        else Qt.AlignmentFlag.AlignTop
                    ),
                )
                add_level_btn.show()

        def update_level_names() -> None:
            for level_index, level in enumerate(levels, start=1):
                level["heading"].setText(
                    tr("Group Level {number}", self._language, number=level_index)
                )
                level["remove_btn"].setObjectName(
                    f"removeLaneGroupLevel_common_level{level_index}"
                )
                level["remove_btn"].setToolTip(
                    tr(
                        "Remove Group Level {number}",
                        self._language,
                        number=level_index,
                    )
                )
                shared = level["shared_control"]
                shared["group_spin"].setObjectName(
                    "laneGroupSpin_common"
                    if level_index == 1
                    else f"laneGroupSpin_common_level{level_index}"
                )
                shared["group_mode"].setObjectName(
                    "laneGroupingCombo_common"
                    if level_index == 1
                    else f"laneGroupingCombo_common_level{level_index}"
                )
                shared["mode_selector"].setObjectName(
                    "laneGroupingSelector_common"
                    if level_index == 1
                    else f"laneGroupingSelector_common_level{level_index}"
                )
                for panel_position, control in enumerate(
                    level["panel_controls"], start=1
                ):
                    suffix = (
                        f"panel_{panel_position}"
                        if level_index == 1
                        else f"panel_{panel_position}_level{level_index}"
                    )
                    control["group_spin"].setObjectName(
                        f"laneGroupSpin_{suffix}"
                    )
                    control["group_mode"].setObjectName(
                        f"laneGroupingCombo_{suffix}"
                    )
                    control["mode_selector"].setObjectName(
                        f"laneGroupingSelector_{suffix}"
                    )
                    control["individual_remove_btn"].setObjectName(
                        f"removeLaneGroupLevel_{suffix}"
                    )
            for panel_position, button in enumerate(add_level_buttons):
                active_count = (
                    sum(
                        level["panel_controls"][panel_position]["active"]
                        for level in levels
                    )
                    if modes["groups_individual"] and panel_count > 1
                    else len(levels)
                )
                prefix = (
                    "common"
                    if panel_position == 0
                    else f"panel_{panel_position + 1}"
                )
                button.setObjectName(
                    f"addLaneGroupLevel_{prefix}_level{active_count}"
                    if active_count
                    else f"addLaneGroupLevel_{prefix}_empty"
                )
            place_add_level_buttons()

        def update_levels() -> None:
            individual = modes["groups_individual"] and panel_count > 1
            for level in levels:
                update_control(level["shared_control"], shared_lane_count)
                level["shared_control"]["individual_remove_btn"].hide()
                for panel_position, control in enumerate(
                    level["panel_controls"]
                ):
                    if control["active"]:
                        update_control(control, lane_counts[panel_position])
                    else:
                        control["group_spin"].setMaximum(
                            lane_counts[panel_position]
                        )
                        with QSignalBlocker(control["group_spin"]):
                            control["group_spin"].setValue(0)
                        update_control(
                            control,
                            lane_counts[panel_position],
                        )
                    # Keep an empty row slot in every level column. Without
                    # it, an active Panel 2 Level 2 collapses upward into the
                    # Panel 1 row when Panel 1 has no Level 2.
                    control["panel_row"].setVisible(individual)
                    control["panel_label"].setVisible(
                        individual and control["active"]
                    )
                    control["row"].setVisible(control["active"])
                    control["individual_remove_btn"].setVisible(
                        individual and control["active"]
                    )
                level["shared_row"].setVisible(
                    not individual
                )
                level["individual_rows"].setVisible(
                    individual
                )
                level["remove_btn"].setVisible(not individual)
            if individual:
                for panel_position in range(panel_count):
                    panel_row_height = max([
                        26,
                        *(
                            level["panel_controls"][panel_position]["row"]
                            .sizeHint()
                            .height()
                            for level in levels
                        ),
                    ])
                    for level in levels:
                        level["panel_controls"][panel_position][
                            "panel_row"
                        ].setFixedHeight(panel_row_height)
            update_level_names()
            refresh_preview()

        def edit_custom_ranges(control: dict, lanes: int) -> bool:
            groups = min(control["group_spin"].value(), lanes)
            defaults = control_ranges(control, lanes)
            result = self._request_custom_lane_ranges(
                lanes,
                groups,
                defaults,
            )
            if result is None:
                return False
            control["custom_ranges"] = result
            control["custom_lane_count"] = lanes
            refresh_preview()
            return True

        def on_group_mode_changed(control: dict, lanes: int) -> None:
            if control["group_spin"].value() <= 0:
                control["custom_ranges"] = None
                control["custom_lane_count"] = None
                update_levels()
                return
            if control["group_mode"].currentData() == "custom":
                groups = min(control["group_spin"].value(), lanes)
                control["custom_ranges"] = self._even_lane_group_ranges(
                    lanes,
                    groups,
                )
                control["custom_lane_count"] = lanes
                if not edit_custom_ranges(control, lanes):
                    control["group_mode"].blockSignals(True)
                    control["group_mode"].setCurrentIndex(0)
                    control["group_mode"].blockSignals(False)
                    control["custom_ranges"] = None
                    control["custom_lane_count"] = None
            else:
                control["custom_ranges"] = None
                control["custom_lane_count"] = None
            update_levels()

        def make_group_control(parent: QWidget, lanes: int) -> dict:
            row = QWidget(parent)
            row.setObjectName("conditionLevelControls")
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(6)
            group_spin = self._make_structure_spin(0, lanes, min(1, lanes))
            finish_spin_input_on_return(group_spin)
            group_mode = QComboBox(parent)
            group_mode.addItem(tr("Divide lanes evenly", self._language), "default")
            group_mode.addItem(tr("Custom lane ranges…", self._language), "custom")
            group_mode.hide()
            mode_selector, mode_menu = popup_selector("", "Evenly", 53)
            default_action = mode_menu.addAction(
                tr("Divide lanes evenly", self._language)
            )
            custom_action = mode_menu.addAction(
                tr("Custom lane ranges…", self._language)
            )
            default_action.setCheckable(True)
            custom_action.setCheckable(True)
            default_action.setChecked(True)
            custom_btn = QPushButton(tr("Edit", self._language))
            custom_btn.setToolTip(tr("Edit Custom Ranges…", self._language))
            custom_btn.setStyleSheet(_SMALL_BTN_STYLE)
            custom_btn.setFixedSize(53, 22)
            custom_btn.hide()
            selector_column = QWidget(row)
            selector_column.setObjectName(
                "conditionLaneDistributionColumn"
            )
            # Reserve the two compact button slots in both modes. Switching
            # between Evenly and Custom therefore never changes the control,
            # row, card, or dialog dimensions.
            selector_column.setFixedSize(53, 47)
            selector_layout = QVBoxLayout(selector_column)
            selector_layout.setContentsMargins(0, 0, 0, 0)
            selector_layout.setSpacing(3)
            selector_layout.addWidget(mode_selector)
            selector_layout.addWidget(custom_btn)
            selector_layout.addStretch(1)
            individual_remove_btn = QToolButton()
            individual_remove_btn.setText("×")
            individual_remove_btn.setToolTip(
                tr("Remove this panel's group level", self._language)
            )
            individual_remove_btn.setFixedSize(20, 20)
            individual_remove_btn.setStyleSheet(
                "QToolButton { border:1px solid #B8C5BF; border-radius:5px; "
                "background:#F7FAF8; color:#52625A; font-weight:700; } "
                "QToolButton:hover { border-color:#C98282; "
                "background:#FBECEC; color:#9B3F3F; }"
            )
            individual_remove_btn.hide()
            layout.addWidget(group_spin)
            layout.addWidget(selector_column, 0, Qt.AlignmentFlag.AlignTop)
            layout.addWidget(
                individual_remove_btn,
                0,
                Qt.AlignmentFlag.AlignTop,
            )
            layout.addStretch(1)
            control = {
                "row": row,
                "layout": layout,
                "group_spin": group_spin,
                "group_mode": group_mode,
                "mode_selector": mode_selector,
                "default_action": default_action,
                "custom_action": custom_action,
                "custom_btn": custom_btn,
                "selector_column": selector_column,
                "selector_layout": selector_layout,
                "individual_remove_btn": individual_remove_btn,
                "custom_ranges": None,
                "custom_lane_count": None,
                "active": True,
            }
            group_spin.valueChanged.connect(update_levels)
            group_mode.currentIndexChanged.connect(
                lambda _=0, ctl=control, count=lanes: (
                    on_group_mode_changed(ctl, count)
                )
            )
            default_action.triggered.connect(
                lambda _=False, combo=group_mode: combo.setCurrentIndex(0)
            )
            custom_action.triggered.connect(
                lambda _=False, combo=group_mode: combo.setCurrentIndex(1)
            )
            custom_btn.clicked.connect(
                lambda _=False, ctl=control, count=lanes: (
                    edit_custom_ranges(ctl, count)
                )
            )
            return control

        def remove_level(level: dict) -> None:
            if level not in levels:
                return
            levels.remove(level)
            level["container"].hide()
            level["container"].setParent(None)
            level["container"].deleteLater()
            update_levels()
            QTimer.singleShot(0, resize_dialog_to_visible_content)

        def copy_panel_control_state(source: dict, target: dict) -> None:
            with QSignalBlocker(target["group_spin"]):
                target["group_spin"].setValue(source["group_spin"].value())
            with QSignalBlocker(target["group_mode"]):
                target["group_mode"].setCurrentIndex(
                    source["group_mode"].currentIndex()
                )
            target["custom_ranges"] = copy.deepcopy(source["custom_ranges"])
            target["custom_lane_count"] = source["custom_lane_count"]
            target["active"] = source["active"]

        def remove_panel_level(panel_position: int, level: dict) -> None:
            if level not in levels:
                return
            level_index = levels.index(level)
            control = level["panel_controls"][panel_position]
            if not control["active"]:
                return
            panel_active_count = sum(
                candidate["panel_controls"][panel_position]["active"]
                for candidate in levels
            )
            if panel_active_count == 1:
                # The final Level 1 is a persistent per-panel placeholder:
                # deleting it changes only its number to zero. Its lane
                # distribution selection stays attached to that panel.
                with QSignalBlocker(control["group_spin"]):
                    control["group_spin"].setValue(0)
                update_levels()
                QTimer.singleShot(0, resize_dialog_to_visible_content)
                return
            for index in range(level_index, len(levels) - 1):
                source = levels[index + 1]["panel_controls"][panel_position]
                target = levels[index]["panel_controls"][panel_position]
                copy_panel_control_state(source, target)
            last_control = levels[-1]["panel_controls"][panel_position]
            last_control["active"] = False
            last_control["custom_ranges"] = None
            last_control["custom_lane_count"] = None
            while len(levels) > 1 and not any(
                control["active"]
                for control in levels[-1]["panel_controls"]
            ):
                removed = levels.pop()
                removed["container"].hide()
                removed["container"].setParent(None)
                removed["container"].deleteLater()
            update_levels()
            QTimer.singleShot(0, resize_dialog_to_visible_content)

        def create_level(active_panels: set[int] | None = None) -> None:
            container = QWidget()
            container.setObjectName("conditionLevelContainer")
            container.setSizePolicy(
                QSizePolicy.Policy.Maximum,
                QSizePolicy.Policy.Preferred,
            )
            container_layout = QVBoxLayout(container)
            container_layout.setContentsMargins(0, 0, 0, 0)
            container_layout.setSpacing(6)

            heading_row = QWidget(container)
            heading_row.setObjectName("conditionLevelHeading")
            heading_layout = QHBoxLayout(heading_row)
            heading_layout.setContentsMargins(0, 0, 0, 0)
            heading_layout.setSpacing(5)
            heading = group_heading_label()
            remove_btn = QToolButton()
            remove_btn.setText("×")
            remove_btn.setFixedSize(20, 20)
            remove_btn.setStyleSheet(
                "QToolButton { border:1px solid #B8C5BF; border-radius:5px; "
                "background:#F7FAF8; color:#52625A; font-weight:700; } "
                "QToolButton:hover { border-color:#C98282; "
                "background:#FBECEC; color:#9B3F3F; }"
            )
            heading_layout.addWidget(heading)
            heading_layout.addWidget(remove_btn)
            heading_layout.addStretch(1)
            container_layout.addWidget(heading_row)

            shared_control = make_group_control(
                container,
                shared_lane_count,
            )
            shared_row = shared_control["row"]
            container_layout.addWidget(shared_row)

            individual_rows = QWidget(container)
            individual_rows.setObjectName("conditionPanelLevelControls")
            individual_layout = QVBoxLayout(individual_rows)
            individual_layout.setContentsMargins(0, 0, 0, 0)
            individual_layout.setSpacing(6)
            panel_controls: list[dict] = []
            for panel_position, lanes in enumerate(lane_counts):
                panel_row = QWidget(individual_rows)
                panel_row.setObjectName("conditionPanelLevelRow")
                panel_row_layout = QHBoxLayout(panel_row)
                panel_row_layout.setContentsMargins(0, 0, 0, 0)
                panel_row_layout.setSpacing(8)
                row_panel_label = panel_label(panel_position)
                panel_row_layout.addWidget(row_panel_label)
                control = make_group_control(panel_row, lanes)
                panel_row_layout.addWidget(control["row"])
                control["panel_row"] = panel_row
                control["panel_label"] = row_panel_label
                control["panel_row_layout"] = panel_row_layout
                control["active"] = (
                    active_panels is None or panel_position in active_panels
                )
                panel_controls.append(control)
                individual_layout.addWidget(panel_row)
            individual_rows.hide()
            container_layout.addWidget(individual_rows)

            level = {
                "container": container,
                "heading": heading,
                "remove_btn": remove_btn,
                "shared_row": shared_row,
                "shared_control": shared_control,
                "individual_rows": individual_rows,
                "panel_controls": panel_controls,
            }
            levels.append(level)
            levels_layout.insertWidget(
                levels_layout.indexOf(empty_row),
                container,
                0,
                Qt.AlignmentFlag.AlignTop,
            )
            remove_btn.clicked.connect(
                lambda _=False, lv=level: remove_level(lv)
            )
            for panel_position, control in enumerate(panel_controls):
                control["individual_remove_btn"].clicked.connect(
                    lambda _=False, position=panel_position, lv=level: (
                        remove_panel_level(position, lv)
                    )
                )
            update_levels()
            QTimer.singleShot(0, resize_dialog_to_visible_content)

        def add_level_for_panel(panel_position: int) -> None:
            individual = modes["groups_individual"] and panel_count > 1
            if not individual:
                create_level()
                return
            active_count = sum(
                level["panel_controls"][panel_position]["active"]
                for level in levels
            )
            if active_count < len(levels):
                control = levels[active_count]["panel_controls"][
                    panel_position
                ]
                control["active"] = True
                with QSignalBlocker(control["group_spin"]):
                    control["group_spin"].setValue(1)
                if control["group_mode"].currentData() == "custom":
                    control["custom_ranges"] = self._even_lane_group_ranges(
                        lane_counts[panel_position],
                        1,
                    )
                    control["custom_lane_count"] = lane_counts[panel_position]
                else:
                    control["custom_ranges"] = None
                    control["custom_lane_count"] = None
                update_levels()
                QTimer.singleShot(0, resize_dialog_to_visible_content)
                return
            create_level({panel_position})

        for panel_position, button in enumerate(add_level_buttons):
            button.clicked.connect(
                lambda _=False, position=panel_position: (
                    add_level_for_panel(position)
                )
            )

        def set_rows_mode(individual: bool) -> None:
            new_individual = bool(individual and panel_count > 1)
            if new_individual and not modes["rows_individual"]:
                for spin in panel_rows_spins:
                    with QSignalBlocker(spin):
                        spin.setValue(shared_rows_spin.value())
            modes["rows_individual"] = new_individual
            rows_mode_selector.setText(
                tr("Set individual panels", self._language)
                if modes["rows_individual"]
                else tr("Apply to all panels", self._language)
            )
            rows_apply_action.setChecked(not modes["rows_individual"])
            rows_individual_action.setChecked(modes["rows_individual"])
            rows_shared.setVisible(not modes["rows_individual"])
            rows_individual.setVisible(modes["rows_individual"])
            refresh_preview()
            QTimer.singleShot(0, resize_dialog_to_visible_content)

        def set_groups_mode(individual: bool) -> None:
            new_individual = bool(individual and panel_count > 1)
            if new_individual and not modes["groups_individual"]:
                for level in levels:
                    shared_control = level["shared_control"]
                    for panel_control in level["panel_controls"]:
                        copy_panel_control_state(
                            shared_control,
                            panel_control,
                        )
                        panel_control["active"] = True
            modes["groups_individual"] = new_individual
            groups_mode_selector.setText(
                tr("Set individual panels", self._language)
                if modes["groups_individual"]
                else tr("Apply to all panels", self._language)
            )
            groups_apply_action.setChecked(not modes["groups_individual"])
            groups_individual_action.setChecked(modes["groups_individual"])
            update_levels()
            QTimer.singleShot(0, resize_dialog_to_visible_content)

        rows_apply_action.triggered.connect(
            lambda _=False: set_rows_mode(False)
        )
        rows_individual_action.triggered.connect(
            lambda _=False: set_rows_mode(True)
        )
        groups_apply_action.triggered.connect(
            lambda _=False: set_groups_mode(False)
        )
        groups_individual_action.triggered.connect(
            lambda _=False: set_groups_mode(True)
        )
        shared_rows_spin.valueChanged.connect(refresh_preview)
        for spin in panel_rows_spins:
            spin.valueChanged.connect(refresh_preview)

        outer.addWidget(lane_card)
        outer.addWidget(section_title("Condition Preview"))
        outer.addWidget(preview, 0, Qt.AlignmentFlag.AlignHCenter)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("conditionCancelButton")
        create_btn = QPushButton("Create")
        create_btn.setObjectName("conditionCreateButton")
        # Return/Enter belongs to the focused editor in this dialog. Creation
        # and cancellation must remain explicit button actions.
        for button in (cancel_btn, create_btn):
            button.setAutoDefault(False)
            button.setDefault(False)
        button_row.addWidget(cancel_btn)
        button_row.addWidget(create_btn)
        outer.addLayout(button_row)
        cancel_btn.clicked.connect(dialog.reject)
        create_btn.clicked.connect(dialog.accept)

        if self._tutorial_mode:
            # Screenshot-defined tutorial preset: one row and one evenly
            # distributed first group level. Normal dialogs still start with
            # no lane-group level until the user adds one.
            create_level()
        set_rows_mode(False)
        set_groups_mode(False)

        self._retranslate_widget_tree(dialog)
        if self._tutorial_mode:
            self.workflowEvent.emit("condition_template_dialog_opened")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            if self._tutorial_mode:
                self.workflowEvent.emit("condition_template_dialog_cancelled")
            return

        self._apply_condition_templates_to_panels([
            (
                panel_index,
                lane_count,
                condition_rows_for(panel_position),
                current_levels(panel_position),
            )
            for panel_position, (panel_index, lane_count) in enumerate(targets)
        ])
        self.workflowEvent.emit("condition_template_applied")

    @staticmethod
    def _even_lane_group_ranges(
        lane_count: int, group_count: int
    ) -> list[tuple[int, int]]:
        lane_count = max(1, lane_count)
        group_count = min(group_count, lane_count)
        if group_count <= 0:
            return []
        base, remainder = divmod(lane_count, group_count)
        ranges: list[tuple[int, int]] = []
        start = 1
        for group_index in range(group_count):
            size = base + (1 if group_index < remainder else 0)
            end = start + size - 1
            ranges.append((start, end))
            start = end + 1
        return ranges

    def _request_custom_lane_ranges(
        self,
        lane_count: int,
        group_count: int,
        defaults: list[tuple[int, int]],
    ) -> list[tuple[int, int]] | None:
        dialog = QDialog(self)
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
            start_spin = self._make_structure_spin(
                1, lane_count, defaults[group_index][0]
            )
            end_spin = self._make_structure_spin(
                1, lane_count, defaults[group_index][1]
            )
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(QLabel("Lane"))
            row_layout.addWidget(start_spin)
            row_layout.addWidget(QLabel("–"))
            row_layout.addWidget(end_spin)
            form.addRow(
                tr("Group {number}:", self._language, number=group_index + 1),
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
        self._retranslate_widget_tree(dialog)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return [
            (min(start.value(), end.value()), max(start.value(), end.value()))
            for start, end in range_spins
        ]

    def _request_custom_panel_lane_ranges(
        self,
        lane_counts: list[int],
        group_count: int,
        defaults: list[tuple[int, int]],
        *,
        panel_number_offset: int = 0,
    ) -> list[tuple[int, int]] | None:
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

        dialog = QDialog(self)
        dialog.setWindowTitle("Custom Panel/Lane Ranges")
        dialog.setModal(True)
        dialog.setStyleSheet(
            "QDialog { background:#F3F6F5; color:#26322D; "
            "font-family:'Avenir Next','Helvetica Neue',Arial; } "
            "QComboBox, QSpinBox { background:#FFFFFF; border:1px solid #BCC9C3; "
            "border-radius:5px; padding:3px 6px; }"
        )
        outer = QVBoxLayout(dialog)
        outer.setContentsMargins(18, 16, 18, 14)
        outer.setSpacing(10)
        info = QLabel("Choose the first and last panel/lane for each group.")
        info.setStyleSheet("color:#5C7167; font-size:10px;")
        outer.addWidget(info)
        form = QFormLayout()
        form.setHorizontalSpacing(12)
        range_controls: list[tuple[QComboBox, QSpinBox, QComboBox, QSpinBox]] = []

        for group_index in range(group_count):
            default_start, default_end = defaults[group_index]
            start_panel_index, start_lane_value = address(default_start)
            end_panel_index, end_lane_value = address(default_end)
            start_panel = QComboBox()
            end_panel = QComboBox()
            for panel_index in range(len(lane_counts)):
                label = tr(
                    "Panel {number}",
                    self._language,
                    number=panel_number_offset + panel_index + 1,
                )
                start_panel.addItem(label, panel_index)
                end_panel.addItem(label, panel_index)
            start_panel.setCurrentIndex(start_panel_index)
            end_panel.setCurrentIndex(end_panel_index)
            start_lane = self._make_structure_spin(
                1, lane_counts[start_panel_index], start_lane_value
            )
            end_lane = self._make_structure_spin(
                1, lane_counts[end_panel_index], end_lane_value
            )
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
                tr("Group {number}:", self._language, number=group_index + 1),
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
        self._retranslate_widget_tree(dialog)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None

        result: list[tuple[int, int]] = []
        for start_panel, start_lane, end_panel, end_lane in range_controls:
            start_global = cumulative[start_panel.currentIndex()] + start_lane.value()
            end_global = cumulative[end_panel.currentIndex()] + end_lane.value()
            result.append((min(start_global, end_global), max(start_global, end_global)))
        return result

    @staticmethod
    def _make_custom_condition_table(
        lane_count: int,
        condition_rows: int,
        group_ranges: object,
    ) -> ConditionTable:
        lane_count = max(1, int(lane_count))
        headers = [f"Lane {index + 1}" for index in range(lane_count)]
        raw_groups = list(group_ranges) if isinstance(group_ranges, list) else []
        if raw_groups and isinstance(raw_groups[0], tuple):
            group_levels = [raw_groups]
        elif raw_groups and isinstance(raw_groups[0], list):
            group_levels = [list(level) for level in raw_groups]
        else:
            group_levels = []
        rows: list[list[str]] = []
        # Highest level is drawn first; Level 1 stays closest to conditions.
        for level_index in reversed(range(len(group_levels))):
            if not group_levels[level_index]:
                continue
            group_row = (
                ["__groups__"]
                if level_index == 0
                else ["__groups_level__", str(level_index + 1)]
            )
            for group_index, (start, end) in enumerate(
                group_levels[level_index]
            ):
                group_row.extend([
                    f"{start}-{end}",
                    f"Group {group_index + 1}",
                ])
            rows.append(group_row)
        for row_index in range(max(1, int(condition_rows))):
            values = [
                "+" if (lane_index + row_index) % 3 == 1 else "-"
                for lane_index in range(lane_count)
            ]
            rows.append([f"Condition {row_index + 1}"] + values)
        return ConditionTable(headers=headers, rows=rows)

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
        self._auto_fit_h_margin.setValue(3)
        self._auto_fit_h_margin.setSuffix(" px")
        self._auto_fit_h_margin.setFixedHeight(22)
        self._auto_fit_h_margin.setStyleSheet(
            "QSpinBox { background:#FFFFFF; border:1px solid #C7D6CE; "
            "border-radius:4px; padding:1px 3px; font-size:9px; }"
        )
        self._auto_fit_h_margin.setToolTip(
            "Original-image background retained to the left and right of each "
            "detected lane crop. It does not set condition-column alignment."
        )
        self._auto_fit_v_margin = QSpinBox()
        self._auto_fit_v_margin.setRange(0, 200)
        self._auto_fit_v_margin.setValue(3)
        self._auto_fit_v_margin.setSuffix(" px")
        self._auto_fit_v_margin.setFixedHeight(22)
        self._auto_fit_v_margin.setStyleSheet(self._auto_fit_h_margin.styleSheet())
        self._auto_fit_v_margin.setToolTip(
            "Original-image background retained above and below each aligned band crop."
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
                    display_width_pt=bd.get("display_width_pt"),
                    display_height_pt=bd.get("display_height_pt"),
                    image_transform=dict(bd.get("image_transform")) if isinstance(bd.get("image_transform"), dict) else None,
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

    def _selected_auto_fit_target(self) -> tuple[list, SourceRef | None, int] | None:
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
        roi = crop.to_dict()
        blot_view_state = self._canvas.capture_blot_view_state()
        self._remember_canvas_undo_state()
        if floating_blots:
            for item in floating_blots:
                item.image_path = image_path
                item.roi = dict(roi)
                item.transform = dict(image_transform or {})
                item.update()
            self._canvas.viewport().update()
        elif ref is not None and ref.panel_idx is not None and ref.slot_idx is not None:
            slot = self._get_slot(ref.panel_idx, ref.slot_idx)
            if slot is None:
                return False
            slot.source_image_path = image_path
            slot.bounding_box = crop
            slot.image_transform = image_transform
            slot.lane_crops = list(result.lane_crop_boxes)
            slot.saved_preview_path = ""
            # ROI assignment is a content replacement only.  Keep the slot's
            # lane model and display dimensions untouched so condition-table
            # geometry, frame size, and every existing layout position remain
            # exactly as designed.
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
            QMessageBox.warning(
                self,
                "No Blot Frame",
                "Complete Step 1: Choose Layout, then select a Blot Frame.",
            )
            return False
        payload = self._active_image_roi_payload()
        if payload is None:
            return False
        image_path, roi, image_transform = payload

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
        slot.lane_crops = []
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
            slot.lane_crops = []
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
