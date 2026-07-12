import unittest

import pandas as pd
from PySide6.QtWidgets import QApplication

from gui.results_panel import ResultsPanel


class ResultsPanelColorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    @staticmethod
    def _df(label: str) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "Band": label,
                    "Lane": 1,
                    "Area": 100,
                    "Mean": 50,
                    "Min": 10,
                    "Max": 90,
                    "IntDen": 5000,
                    "RawIntDen": 5500,
                }
            ]
        )

    def test_run_colors_alternate_blue_green_by_analysis_order(self) -> None:
        panel = ResultsPanel()

        panel.show_results(self._df("Band 1"))
        panel.show_results(self._df("Band 2"))
        panel.show_results(self._df("Band 3"))
        panel.show_results(self._df("Band 4"))

        colors = [
            panel._table.item(0, column).background().color().getRgb()
            for column in range(1, 5)
        ]

        self.assertEqual(colors[0], colors[2])
        self.assertEqual(colors[1], colors[3])
        self.assertNotEqual(colors[0], colors[1])

    def test_existing_run_colors_remain_unchanged_after_deletion(self) -> None:
        panel = ResultsPanel()

        panel.show_results(self._df("Band 1"))
        panel.show_results(self._df("Band 2"))
        second_run_color = panel._table.item(0, 2).background().color().getRgb()

        first_entry_id = int(panel._all_rows[0]["_id"])
        panel._delete_entry_ids({first_entry_id})

        remaining_color = panel._table.item(0, 1).background().color().getRgb()
        self.assertEqual(remaining_color, second_run_color)


if __name__ == "__main__":
    unittest.main()
