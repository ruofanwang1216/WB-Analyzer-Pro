"""Status bar messages follow the display language."""
from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication

from gui.status_messages import LocalizedStatusBar, localize_status_message
from utils.i18n import LANG_EN, LANG_ZH_CN


class StatusMessageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_english_and_unknown_text_pass_through(self) -> None:
        text = "Loaded: blot.tif — draw a ROI to continue."
        self.assertEqual(localize_status_message(text, LANG_EN), text)
        self.assertEqual(
            localize_status_message("Something new.", LANG_ZH_CN), "Something new."
        )
        self.assertEqual(localize_status_message("已载入。", LANG_ZH_CN), "已载入。")

    def test_fixed_sentences_use_the_catalog(self) -> None:
        self.assertEqual(
            localize_status_message("Measuring — please wait…", LANG_ZH_CN),
            "正在测量，请稍候…",
        )

    def test_sentences_with_values_keep_their_values(self) -> None:
        self.assertEqual(
            localize_status_message(
                "Loaded: 04_MEK (v2).tif — draw a ROI to continue.", LANG_ZH_CN
            ),
            "已载入 04_MEK (v2).tif。请在图像上框选泳道。",
        )
        self.assertEqual(
            localize_status_message(
                "Lane ROI set (181 × 234 px) — now draw a band ROI to continue.",
                LANG_ZH_CN,
            ),
            "泳道已框选（181 × 234 像素）。接下来请框选条带。",
        )
        self.assertEqual(
            localize_status_message(
                "Autofilled 3 value(s) into Replicate 2 — Loading control.",
                LANG_ZH_CN,
            ),
            "已把 3 个数值填入“重复 2 — 内参条带”。",
        )

    def test_status_bar_translates_as_it_shows(self) -> None:
        language = [LANG_ZH_CN]
        bar = LocalizedStatusBar(lambda: language[0])
        bar.showMessage("Image removed.")
        self.assertEqual(bar.currentMessage(), "已移除图像。")
        language[0] = LANG_EN
        bar.showMessage("Image removed.", 3000)
        self.assertEqual(bar.currentMessage(), "Image removed.")


if __name__ == "__main__":
    unittest.main()
