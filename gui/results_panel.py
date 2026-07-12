"""Bottom results panel — single horizontal comparison table + export."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pandas as pd
from PySide6.QtCore import Qt, Signal, QRect
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QFileDialog, QMessageBox, QHeaderView,
)

_COLS = ["Run", "Band", "Lane", "Area", "Mean", "Min", "Max", "IntDen", "RawIntDen"]
_METRICS = ["Area", "Mean", "Min", "Max", "IntDen", "RawIntDen"]
_ODD_RUN_BG = QColor(239, 244, 248, 235)      # neutral blue
_EVEN_RUN_BG = QColor(212, 237, 228, 220)     # soft green, clearly visible
_ODD_RUN_HEADER_TINT = QColor(220, 233, 243)
_EVEN_RUN_HEADER_TINT = QColor(206, 234, 221)


class _BandHeaderView(QHeaderView):
    deleteRequested = Signal(int)
    checkboxToggled = Signal(int, bool)

    def __init__(self, orientation: Qt.Orientation, parent=None) -> None:
        super().__init__(orientation, parent)
        self._section_tints: dict[int, QColor] = {}
        self._checked_sections: set[int] = set()
        self._band_sections: set[int] = set()

    def set_section_tints(self, tints: dict[int, QColor]) -> None:
        self._section_tints = dict(tints)
        self.viewport().update()

    def set_band_sections(self, sections: set[int]) -> None:
        self._band_sections = set(sections)
        self.viewport().update()

    def set_checked_sections(self, sections: set[int]) -> None:
        self._checked_sections = set(sections)
        self.viewport().update()

    def clear_checked_sections(self) -> None:
        self._checked_sections.clear()
        self.viewport().update()

    def checked_sections(self) -> set[int]:
        return set(self._checked_sections)

    def _section_rect(self, logical_index: int) -> QRect:
        x = self.sectionViewportPosition(logical_index)
        w = self.sectionSize(logical_index)
        return QRect(x, 0, w, self.height())

    def _checkbox_rect(self, section_rect: QRect) -> QRect:
        box = 12
        margin = 6
        return QRect(
            section_rect.left() + margin,
            section_rect.center().y() - (box // 2),
            box,
            box,
        )

    def _delete_icon_rect(self, section_rect: QRect) -> QRect:
        size = 14
        margin = 6
        return QRect(
            section_rect.right() - margin - size,
            section_rect.center().y() - (size // 2),
            size,
            size,
        )

    def mousePressEvent(self, event) -> None:
        logical = self.logicalIndexAt(event.pos())
        if logical < 0 or logical not in self._band_sections:
            super().mousePressEvent(event)
            return

        rect = self._section_rect(logical)
        checkbox_rect = self._checkbox_rect(rect)
        delete_rect = self._delete_icon_rect(rect)

        if checkbox_rect.contains(event.pos()):
            checked = logical not in self._checked_sections
            if checked:
                self._checked_sections.add(logical)
            else:
                self._checked_sections.discard(logical)
            self.checkboxToggled.emit(logical, checked)
            self.viewport().update(rect)
            event.accept()
            return

        if delete_rect.contains(event.pos()):
            self.deleteRequested.emit(logical)
            event.accept()
            return

        super().mousePressEvent(event)

    def paintSection(self, painter: QPainter, rect, logicalIndex: int) -> None:
        super().paintSection(painter, rect, logicalIndex)
        tint = self._section_tints.get(logicalIndex)
        painter.save()
        if tint is not None:
            painter.fillRect(rect, tint)

        if logicalIndex == 0:
            painter.restore()
            return

        checkbox_rect = self._checkbox_rect(rect)
        delete_rect = self._delete_icon_rect(rect)

        # Checkbox: small white box with subtle border.
        painter.setPen(QPen(QColor("#8FA1AE"), 1))
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawRect(checkbox_rect)

        if logicalIndex in self._checked_sections:
            painter.setPen(QPen(QColor("#2A5E48"), 1.6))
            painter.drawLine(
                checkbox_rect.left() + 2,
                checkbox_rect.center().y(),
                checkbox_rect.left() + 5,
                checkbox_rect.bottom() - 2,
            )
            painter.drawLine(
                checkbox_rect.left() + 5,
                checkbox_rect.bottom() - 2,
                checkbox_rect.right() - 2,
                checkbox_rect.top() + 2,
            )

        # Delete icon: gray x in subtle circle (ⓧ style).
        painter.setPen(QPen(QColor("#9AA9B4"), 1))
        painter.setBrush(QColor(245, 248, 250, 235))
        painter.drawEllipse(delete_rect)
        painter.setPen(QPen(QColor("#8A98A3"), 1.3))
        painter.drawLine(delete_rect.left() + 4, delete_rect.top() + 4, delete_rect.right() - 4, delete_rect.bottom() - 4)
        painter.drawLine(delete_rect.right() - 4, delete_rect.top() + 4, delete_rect.left() + 4, delete_rect.bottom() - 4)

        # Redraw centered text between checkbox and delete icon.
        text = str(self.model().headerData(logicalIndex, self.orientation(), Qt.ItemDataRole.DisplayRole) or "")
        text_rect = QRect(
            checkbox_rect.right() + 6,
            rect.top(),
            max(0, delete_rect.left() - checkbox_rect.right() - 12),
            rect.height(),
        )
        painter.setPen(QColor("#5C6F7D"))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter, text)
        painter.restore()


class _ResultsTableWidget(QTableWidget):
    returnPressed = Signal()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.returnPressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class ResultsPanel(QWidget):
    meanAutofillRequested = Signal(list)

    def __init__(
        self,
        parent=None,
        *,
        default_export_dir_provider: Callable[[], Path] | None = None,
        export_dir_changed: Callable[[Path], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._default_export_dir_provider = default_export_dir_provider
        self._export_dir_changed = export_dir_changed
        self._all_rows: list[dict[str, Any]] = []   # one entry per measured ROI
        self._run_counter: int = 0
        self._next_entry_id: int = 1
        self._visible_entry_ids: list[int] = []
        self._run_column_ranges: dict[int, tuple[int, int]] = {}
        self._checked_entry_ids: set[int] = set()
        self._build_ui()

    def _build_ui(self) -> None:
        self.setStyleSheet("""
            QWidget {
                background-color: #F5F8FA;
            }
            QTableWidget {
                background-color: #F5F8FA;
                alternate-background-color: #EFF4F8;
                gridline-color: #D8E6EE;
                border: none;
                font-size: 12px;
                color: #35393D;
            }
            QTableWidget::item {
                padding: 4px 12px;
                border-bottom: 1px solid #D8E6EE;
            }
            QHeaderView::section {
                background-color: #DCE9F2;
                color: #A0B4C0;
                font-size: 10px;
                font-weight: bold;
                letter-spacing: 1px;
                padding: 5px 12px;
                border: none;
                border-bottom: 1px solid #C8D8E4;
                border-right: 1px solid #D8E6EE;
            }
            QPushButton#export_btn {
                background-color: #D4EDE4;
                border: 1px solid #8AB4A0;
                border-radius: 5px;
                color: #2A5E48;
                padding: 3px 10px;
                font-size: 11px;
            }
            QPushButton#export_btn:hover {
                background-color: #8AB4A0;
                color: #F5F8FA;
            }
            QPushButton#clear_btn {
                background-color: #F5F8FA;
                border: 1px solid #C8D8E4;
                border-radius: 5px;
                color: #6E8494;
                padding: 3px 10px;
                font-size: 11px;
            }
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 4, 6, 4)
        root.setSpacing(6)

        bar = QHBoxLayout()
        self._title = QLabel("Results")
        self._title.setStyleSheet("font-weight: bold; color: #35393D;")
        bar.addWidget(self._title)
        bar.addStretch()

        self._delete_btn = QPushButton("Delete")
        self._delete_btn.setObjectName("clear_btn")
        self._delete_btn.setEnabled(False)
        self._delete_btn.clicked.connect(self._delete_selected_columns)
        bar.addWidget(self._delete_btn)

        self._export_btn = QPushButton("Export Results")
        self._export_btn.setObjectName("export_btn")
        self._export_btn.setEnabled(False)
        self._export_btn.clicked.connect(self._export)
        bar.addWidget(self._export_btn)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setObjectName("clear_btn")
        self._clear_btn.setEnabled(False)
        self._clear_btn.clicked.connect(self.clear)
        bar.addWidget(self._clear_btn)

        root.addLayout(bar)

        # Single continuous wide table; horizontal scrolling is the main navigation.
        self._table = _ResultsTableWidget()
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(False)
        self._table.verticalHeader().setVisible(False)
        self._table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._table.returnPressed.connect(self._on_results_return_pressed)
        header = _BandHeaderView(Qt.Orientation.Horizontal, self._table)
        header.setSectionsClickable(True)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setMinimumSectionSize(92)
        header.deleteRequested.connect(self._on_header_delete_requested)
        header.checkboxToggled.connect(self._on_header_checkbox_toggled)
        self._table.setHorizontalHeader(header)
        root.addWidget(self._table)

    # ── Public API ─────────────────────────────────────────────────────────────

    def show_results(self, df: pd.DataFrame) -> None:
        """Append one analysis run as new columns on the right."""
        self._run_counter += 1
        run_label = f"Run {self._run_counter}"
        run_index = self._run_counter

        for _, row in df.iterrows():
            entry: dict[str, Any] = {
                "_id": self._next_entry_id,
                "_run_index": run_index,
                "Run": run_label,
            }
            self._next_entry_id += 1
            for col in ("Band", "Lane", "Area", "Mean", "Min", "Max", "IntDen", "RawIntDen"):
                entry[col] = row[col] if col in df.columns else None
            self._all_rows.append(entry)

        self._refresh_table()
        self._update_panel_state()

    def clear(self) -> None:
        self._all_rows.clear()
        self._run_counter = 0
        self._next_entry_id = 1
        self._visible_entry_ids = []
        self._run_column_ranges = {}
        self._checked_entry_ids.clear()
        self._table.clear()
        self._table.setRowCount(0)
        self._table.setColumnCount(0)
        header = self._table.horizontalHeader()
        if isinstance(header, _BandHeaderView):
            header.set_section_tints({})
            header.set_band_sections(set())
            header.clear_checked_sections()
        self._update_panel_state()

    # ── Internal helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _normalize_band_name(entry: dict[str, Any], fallback_index: int) -> str:
        band = entry.get("Band")
        if band is None or (isinstance(band, float) and pd.isna(band)):
            return f"#{fallback_index}"
        text = str(band).strip()
        return text or f"#{fallback_index}"

    @staticmethod
    def _format_cell(val: Any) -> str:
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return ""
        return str(val)

    @staticmethod
    def _run_colors(run_index: int) -> tuple[QColor, QColor]:
        # Keep each run's color fixed by run order so older groups do not change
        # when a new analysis is appended.
        if run_index % 2 == 0:
            return _EVEN_RUN_BG, _EVEN_RUN_HEADER_TINT
        return _ODD_RUN_BG, _ODD_RUN_HEADER_TINT

    def _refresh_table(self) -> None:
        self._table.clear()
        if not self._all_rows:
            self._visible_entry_ids = []
            self._run_column_ranges = {}
            self._checked_entry_ids.clear()
            header = self._table.horizontalHeader()
            if isinstance(header, _BandHeaderView):
                header.set_section_tints({})
                header.set_band_sections(set())
                header.clear_checked_sections()
            self._table.setRowCount(0)
            self._table.setColumnCount(0)
            return

        visible_entries = list(self._all_rows)  # preserve append order across runs
        self._visible_entry_ids = [int(entry["_id"]) for entry in visible_entries]
        self._run_column_ranges = self._compute_run_column_ranges(visible_entries)
        self._checked_entry_ids.intersection_update(self._visible_entry_ids)

        self._table.setRowCount(len(_METRICS))
        self._table.setColumnCount(1 + len(visible_entries))

        metric_header = QTableWidgetItem("Metric")
        metric_header.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setHorizontalHeaderItem(0, metric_header)

        header_tints: dict[int, QColor] = {}
        checked_sections: set[int] = set()
        for col_idx, entry in enumerate(visible_entries, start=1):
            band_name = self._normalize_band_name(entry, col_idx)
            header_item = QTableWidgetItem(band_name)
            header_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            run_index = int(entry.get("_run_index", 0) or 0)
            run_bg, header_tint = self._run_colors(run_index)
            header_item.setBackground(run_bg)
            header_tints[col_idx] = header_tint
            self._table.setHorizontalHeaderItem(col_idx, header_item)
            if int(entry["_id"]) in self._checked_entry_ids:
                checked_sections.add(col_idx)

        for row_idx, metric in enumerate(_METRICS):
            metric_item = QTableWidgetItem(metric)
            metric_item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            self._table.setItem(row_idx, 0, metric_item)

            for col_idx, entry in enumerate(visible_entries, start=1):
                val_item = QTableWidgetItem(self._format_cell(entry.get(metric)))
                val_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                run_index = int(entry.get("_run_index", 0) or 0)
                run_bg, _ = self._run_colors(run_index)
                val_item.setBackground(run_bg)
                self._table.setItem(row_idx, col_idx, val_item)

        header = self._table.horizontalHeader()
        if isinstance(header, _BandHeaderView):
            header.set_section_tints(header_tints)
            header.set_band_sections(set(range(1, 1 + len(visible_entries))))
            header.set_checked_sections(checked_sections)

    def _compute_run_column_ranges(
        self,
        visible_entries: list[dict[str, Any]],
    ) -> dict[int, tuple[int, int]]:
        ranges: dict[int, list[int]] = {}
        for col_idx, entry in enumerate(visible_entries, start=1):
            run_index = int(entry.get("_run_index", 0) or 0)
            if run_index not in ranges:
                ranges[run_index] = [col_idx, col_idx]
            else:
                ranges[run_index][1] = col_idx
        return {run: (bounds[0], bounds[1]) for run, bounds in ranges.items()}

    def _on_header_checkbox_toggled(self, column_index: int, checked: bool) -> None:
        if column_index <= 0:
            return
        entry_id = self._entry_id_for_column(column_index)
        if entry_id is None:
            return
        if checked:
            self._checked_entry_ids.add(entry_id)
        else:
            self._checked_entry_ids.discard(entry_id)
        self._update_panel_state()

    def _on_header_delete_requested(self, column_index: int) -> None:
        if column_index <= 0:
            return
        target_id = self._entry_id_for_column(column_index)
        if target_id is None:
            return

        target_entry = next((entry for entry in self._all_rows if int(entry.get("_id", -1)) == target_id), None)
        if target_entry is None:
            return

        band_name = self._normalize_band_name(target_entry, column_index)
        confirm = QMessageBox.question(
            self,
            "Remove Band",
            f"Remove {band_name} from results?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        self._delete_entry_ids({target_id})

    def _entry_id_for_column(self, column_index: int) -> int | None:
        offset = column_index - 1
        if offset < 0 or offset >= len(self._visible_entry_ids):
            return None
        return self._visible_entry_ids[offset]

    def _delete_selected_columns(self) -> None:
        if not self._checked_entry_ids:
            QMessageBox.information(self, "Delete", "No band selected.")
            return

        confirm = QMessageBox.question(
            self,
            "Delete Selected Bands",
            f"Delete {len(self._checked_entry_ids)} selected band column(s)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._delete_entry_ids(set(self._checked_entry_ids))

    def _delete_entry_ids(self, entry_ids: set[int]) -> None:
        if not entry_ids:
            return
        self._all_rows = [
            entry for entry in self._all_rows if int(entry.get("_id", -1)) not in entry_ids
        ]
        # Requirement: clear all checkbox states after deletion.
        self._checked_entry_ids.clear()
        self._refresh_table()
        self._update_panel_state()

    def _update_panel_state(self) -> None:
        total = len(self._all_rows)
        has_rows = total > 0
        has_checked = bool(self._checked_entry_ids)
        self._export_btn.setEnabled(has_rows)
        self._clear_btn.setEnabled(has_rows)
        self._delete_btn.setEnabled(has_rows and has_checked)
        if has_rows:
            self._title.setText(f"Results — {total} row(s) across {self._run_counter} run(s)")
        else:
            self._title.setText("Results")

    def _to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame(self._all_rows, columns=_COLS)

    def to_dataframe(self) -> pd.DataFrame:
        return self._to_dataframe().copy()

    def selected_mean_values(self) -> list[str]:
        if self._table.rowCount() <= 0:
            return []
        try:
            mean_row = _METRICS.index("Mean")
        except ValueError:
            return []
        selected_cols = sorted(
            {
                idx.column()
                for idx in self._table.selectedIndexes()
                if idx.row() == mean_row and idx.column() > 0
            }
        )
        values: list[str] = []
        for col in selected_cols:
            item = self._table.item(mean_row, col)
            values.append(item.text() if item is not None else "")
        return values

    def _on_results_return_pressed(self) -> None:
        self.meanAutofillRequested.emit(self.selected_mean_values())

    # ── Export ─────────────────────────────────────────────────────────────────

    def _export(self) -> None:
        if not self._all_rows:
            return
        default_dir = (
            self._default_export_dir_provider()
            if self._default_export_dir_provider is not None
            else Path.home()
        )
        default_target = str(default_dir / "WB_results")
        path, selected_filter = QFileDialog.getSaveFileName(
            self, "Export Results", default_target,
            "Excel (*.xlsx);;CSV (*.csv);;All files (*)"
        )
        if not path:
            return

        df = self._to_dataframe()
        try:
            if path.endswith(".xlsx") or "Excel" in selected_filter:
                if not path.endswith(".xlsx"):
                    path += ".xlsx"
                df.to_excel(path, index=False, engine="openpyxl")
            else:
                if not path.endswith(".csv"):
                    path += ".csv"
                df.to_csv(path, index=False)
            if self._export_dir_changed is not None:
                self._export_dir_changed(Path(path).expanduser().resolve().parent)
            QMessageBox.information(self, "Exported", f"Saved to:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))
