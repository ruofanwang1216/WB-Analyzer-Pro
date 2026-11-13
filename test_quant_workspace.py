"""Quantify & Plot workspace: results flow into the table and the chart."""
from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import pandas as pd
from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtWidgets import QApplication, QMessageBox

from gui.figure_generation import ColumnTableWindow
from gui.main_window import MainWindow
from utils.i18n import LANG_ZH_CN


def _cell(table: ColumnTableWindow, row: int, group: int) -> str:
    item = table._table.item(row, 2 + group)
    return item.text() if item is not None else ""


class ColumnTableAutoFillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _table(self, samples: int = 3, replicates: int = 2) -> ColumnTableWindow:
        table = ColumnTableWindow(samples=samples, replicates=replicates)
        table.set_auto_figure_enabled(True)
        return table

    def test_runs_fill_target_then_loading_then_next_replicate(self) -> None:
        table = self._table()

        first = table.autofill_next(["10", "20", "30"])
        second = table.autofill_next(["5", "5", "5"])
        third = table.autofill_next(["12", "18", "33"])

        self.assertEqual(first, ("Replicate 1 — Target band", 3))
        self.assertEqual(second, ("Replicate 1 — Loading control", 3))
        self.assertEqual(third, ("Replicate 2 — Target band", 3))
        self.assertEqual([_cell(table, 0, g) for g in range(3)], ["10", "20", "30"])
        self.assertEqual([_cell(table, 1, g) for g in range(3)], ["5", "5", "5"])
        self.assertEqual([_cell(table, 3, g) for g in range(3)], ["12", "18", "33"])
        # The next run is aimed at the loading row of replicate 2.
        self.assertEqual(table._active_target_row, 4)

    def test_clicked_row_is_filled_first(self) -> None:
        table = self._table()
        table.set_active_target_row(4)

        table.autofill_next(["1", "2", "3"])

        self.assertEqual([_cell(table, 4, g) for g in range(3)], ["1", "2", "3"])
        self.assertEqual(_cell(table, 0, 0), "")
        # Then it wraps round to the first empty row.
        self.assertEqual(table._active_target_row, 0)

    def test_first_run_sets_the_number_of_groups(self) -> None:
        table = self._table(samples=3, replicates=3)

        table.autofill_next(["1", "2", "3", "4", "5", "6"])

        self.assertEqual(table.table_dimensions(), (6, 3))
        self.assertEqual(_cell(table, 0, 5), "6")

    def test_full_table_reports_nothing_filled(self) -> None:
        table = self._table(samples=2, replicates=1)
        self.assertIsNotNone(table.autofill_next(["1", "2"]))
        self.assertIsNotNone(table.autofill_next(["3", "4"]))

        self.assertIsNone(table.autofill_next(["9", "9"]))
        self.assertEqual(_cell(table, 0, 0), "1")

    def test_chart_appears_once_a_target_and_loading_pair_exists(self) -> None:
        table = self._table()
        host_window = MainWindowFactory.make()
        host_window._mount_column_table_in_workspace(table)
        table.set_auto_figure_enabled(True)

        table.autofill_next(["10", "20", "30"])
        self.assertFalse(table.has_generated_figure())

        with patch.object(QMessageBox, "warning") as warning:
            table.autofill_next(["5", "5", "5"])

        warning.assert_not_called()
        self.assertTrue(table.has_generated_figure())
        # The first group is the baseline until the user picks another.
        self.assertEqual(table._negative_control_group_index, 0)
        self.assertEqual(_cell(table, 2, 0), "1")
        self.assertEqual(_cell(table, 2, 1), "2")

    def test_resize_keeps_values_names_and_baseline(self) -> None:
        table = self._table(samples=3, replicates=2)
        table.autofill_next(["10", "20", "30"])
        table.autofill_next(["5", "5", "5"])
        table._group_names[1] = "Drug A"
        table._negative_control_group_index = 1

        table.resize_table(4, 3)

        self.assertEqual(table.table_dimensions(), (4, 3))
        self.assertEqual([_cell(table, 0, g) for g in range(4)], ["10", "20", "30", ""])
        self.assertEqual(_cell(table, 1, 2), "5")
        self.assertEqual(table._group_names[1], "Drug A")
        self.assertEqual(table._negative_control_group_index, 1)
        self.assertEqual(table._samples_spin.value(), 4)
        self.assertEqual(table._replicates_spin.value(), 3)

        table.resize_table(2, 1)
        self.assertEqual([_cell(table, 0, g) for g in range(2)], ["10", "20"])

    def test_manual_button_still_warns_without_a_baseline(self) -> None:
        table = ColumnTableWindow(samples=2, replicates=1)
        with patch.object(QMessageBox, "warning") as warning:
            self.assertFalse(table._on_figures_generation_clicked())
        warning.assert_called_once()

    def test_reset_in_workspace_clears_values_without_dialogs(self) -> None:
        table = self._table(samples=2, replicates=1)
        table.autofill_next(["1", "2"])
        with (
            patch("gui.figure_generation.FigureTypeDialog.exec") as type_dialog,
            patch.object(
                QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
            ),
        ):
            table._on_reset_table_clicked()
        type_dialog.assert_not_called()
        self.assertEqual(table.table_dimensions(), (2, 1))
        self.assertEqual(_cell(table, 0, 0), "")


class MainWindowFactory:
    @staticmethod
    def make(ui: dict | None = None) -> MainWindow:
        with (
            patch("gui.main_window.AppPersistence.update_config", return_value=None),
            patch(
                "gui.main_window.AppPersistence.read_config",
                return_value={"ui": dict(ui or {})},
            ),
        ):
            window = MainWindow()
        window._persistence.remember_ui_state = Mock()
        window._persistence.update_config = Mock()
        return window


class QuantWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _results(self, means: list[float]) -> list[dict]:
        return [
            {"lane": index + 1, "band": 1, "Area": 10.0, "Mean": mean}
            for index, mean in enumerate(means)
        ]

    def test_analysis_results_flow_into_table_and_chart(self) -> None:
        window = MainWindowFactory.make()
        window._on_figure_generation_clicked()
        table = window._embedded_column_table

        window._on_measurement_done(self._results([10.0, 20.0, 30.0]))
        window._on_measurement_done(self._results([5.0, 5.0, 5.0]))

        self.assertEqual(_cell(table, 0, 1), "20.0")
        self.assertEqual(_cell(table, 1, 2), "5.0")
        self.assertTrue(table.has_generated_figure())
        self.assertIn("Replicate 1", window._status_bar.currentMessage())

    def test_auto_fill_can_be_switched_off(self) -> None:
        window = MainWindowFactory.make()
        window._on_figure_generation_clicked()
        table = window._embedded_column_table
        table._auto_fill_checkbox.setChecked(False)

        window._on_measurement_done(self._results([10.0, 20.0, 30.0]))

        self.assertEqual(_cell(table, 0, 0), "")

    def test_status_message_is_localised(self) -> None:
        window = MainWindowFactory.make({"language": LANG_ZH_CN})
        window._on_figure_generation_clicked()

        window._on_measurement_done(self._results([10.0, 20.0, 30.0]))

        self.assertEqual(
            window._status_bar.currentMessage(),
            "分析完成：已把 3 个数值填入“重复 1 — 目的蛋白条带”。",
        )

    def test_tutorial_keeps_the_manual_transfer(self) -> None:
        window = MainWindowFactory.make()
        window._tutorial_controller.start("densitometry")
        table = ColumnTableWindow(samples=3, replicates=2, parent=window)
        window._mount_column_table_in_workspace(table)

        window._on_measurement_done(self._results([10.0, 20.0, 30.0]))

        self.assertFalse(table.auto_fill_enabled())
        self.assertEqual(_cell(table, 0, 0), "")
        window._tutorial_controller.stop()

    def test_dropped_images_are_imported(self) -> None:
        window = MainWindowFactory.make()
        mime = QMimeData()
        mime.setUrls([
            QUrl.fromLocalFile("/tmp/a.scn"),
            QUrl.fromLocalFile("/tmp/b.TIF"),
            QUrl.fromLocalFile("/tmp/notes.txt"),
        ])
        event = Mock()
        event.mimeData.return_value = mime

        with patch.object(window, "_import_paths") as import_paths:
            window.dropEvent(event)

        import_paths.assert_called_once_with(["/tmp/a.scn", "/tmp/b.TIF"])
        event.acceptProposedAction.assert_called_once()

    def test_divider_positions_are_remembered_per_workspace(self) -> None:
        window = MainWindowFactory.make()
        window.resize(1400, 900)
        window.show()
        self._app.processEvents()
        window._on_figure_generation_clicked()
        self._app.processEvents()
        window._workspace_splitter.setSizes([500, 600])
        self._app.processEvents()
        chosen = window._workspace_splitter.sizes()

        window._on_wb_plot_mode()
        self._app.processEvents()
        window._on_figure_generation_clicked()
        self._app.processEvents()

        restored = window._workspace_splitter.sizes()
        self.assertAlmostEqual(restored[0], chosen[0], delta=4)
        self.assertEqual(window._layout_memory["workspace_quant"], chosen)

        # Tests never write the user's real settings.
        window._save_layout_state()
        window._persistence.update_config.assert_not_called()
        window._persist_layout_on_close = True
        window._save_layout_state()
        window._persistence.update_config.assert_called_once()


if __name__ == "__main__":
    unittest.main()
