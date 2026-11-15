from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QPushButton

from gui import theming
from gui.main_window import MainWindow


class ThemingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        theming.set_theme(theming.THEME_LIGHT)

    def _make_window(self, ui: dict | None = None) -> MainWindow:
        with (
            patch("gui.main_window.AppPersistence.update_config", return_value=None),
            patch(
                "gui.main_window.AppPersistence.read_config",
                return_value={"ui": dict(ui or {})},
            ),
        ):
            window = MainWindow()
        window._persistence.remember_ui_state = Mock()
        return window

    def test_light_theme_leaves_colours_untouched(self) -> None:
        theming.set_theme(theming.THEME_LIGHT)
        css = "QPushButton { background: #1F6B4A; color: #FFFFFF; }"
        self.assertEqual(theming.translate_css(css), css)
        self.assertEqual(theming.color("#1F6B4A").name().upper(), "#1F6B4A")

    def test_dark_theme_uses_graphite_and_muted_blue(self) -> None:
        theming.set_theme(theming.THEME_DARK)
        css = theming.translate_css(
            "QWidget { background: #E7ECE9; color: #1C2622; border: 1px solid #1F6B4A; }"
        )
        self.assertIn(theming.DARK_BG, css)
        self.assertIn(theming.DARK_TEXT, css)
        self.assertIn(theming.DARK_ACCENT, css)
        # Unlisted greens still land in the blue family, never stay green.
        other = theming.color("#2F8A64")
        self.assertGreater(other.blue(), other.green())
        # Alpha survives translation.
        self.assertEqual(theming.color(QColor(10, 20, 30, 77)).alpha(), 77)

    def test_widget_stylesheets_follow_the_theme_and_return(self) -> None:
        theming.install()
        button = QPushButton("x")
        light_css = "QPushButton { background: #FFFFFF; color: #1C2622; }"
        button.setStyleSheet(light_css)
        theming.set_theme(theming.THEME_DARK)
        self.assertEqual(button.styleSheet(), light_css)
        self.assertIn(theming.DARK_PANEL, theming._orig_style_sheet(button))
        theming.set_theme(theming.THEME_LIGHT)
        self.assertEqual(theming._orig_style_sheet(button), light_css)

    def test_toggle_switches_theme_icon_and_remembers_choice(self) -> None:
        window = self._make_window()
        self.assertEqual(window._theme, theming.THEME_LIGHT)
        self.assertEqual(window._theme_toggle_btn.toolTip(), "Switch to dark theme")

        window._theme_toggle_btn.click()

        self.assertEqual(window._theme, theming.THEME_DARK)
        self.assertTrue(theming.is_dark())
        self.assertEqual(window._theme_toggle_btn.toolTip(), "Switch to light theme")
        window._persistence.remember_ui_state.assert_called_with(theme="dark")
        # Same controls, same places: only the colours changed.
        self.assertFalse(window._files_list.isHidden())

        window._theme_toggle_btn.click()
        self.assertEqual(window._theme, theming.THEME_LIGHT)

    def test_saved_dark_theme_is_restored_on_launch(self) -> None:
        window = self._make_window({"theme": "dark"})
        self.assertEqual(window._theme, theming.THEME_DARK)
        self.assertIn(
            theming.DARK_PANEL, theming._orig_style_sheet(window._files_panel)
        )

    def test_analyze_lives_in_each_image_toolbar(self) -> None:
        window = self._make_window()
        window.show()
        window._on_densitometry_mode()
        for panel in window._image_panels:
            self.assertFalse(panel.analyze_btn.isHidden())
            self.assertIs(panel.analyze_btn.defaultAction(), window._act_analyze)
        self.assertIs(window._analyze_toolbar_btn, window._image_panels[0].analyze_btn)


if __name__ == "__main__":
    unittest.main()
