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

    def test_light_theme_is_mist_teal(self) -> None:
        theming.set_theme(theming.THEME_LIGHT)
        light = theming.spec(theming.THEME_LIGHT)
        css = theming.translate_css(
            "QPushButton { background: #1F6B4A; color: #FEFEFE; border-radius: 8px; }"
        )
        self.assertIn(light.accent, css)
        self.assertIn(light.on_accent, css)
        self.assertEqual(theming.color("#1F6B4A").name().upper(), light.accent)
        # Controls get the rounder glass shape unless a rule opts out.
        self.assertIn("border-radius: 13px", css)
        exact = theming.translate_css("QLabel { border-radius: 8px; /* exact */ }")
        self.assertIn("border-radius: 8px", exact)

    def test_dark_theme_is_mist_teal(self) -> None:
        theming.set_theme(theming.THEME_DARK)
        css = theming.translate_css(
            "QWidget { background: #E7ECE9; color: #1C2622; border: 1px solid #1F6B4A; }"
        )
        self.assertIn(theming.DARK_BG, css)
        self.assertIn(theming.DARK_TEXT, css)
        self.assertIn(theming.DARK_ACCENT, css)
        # Unlisted greens land in the teal family, never stay green.
        other = theming.color("#2F8A64")
        self.assertGreaterEqual(other.blue(), other.green() - 2)
        self.assertGreater(other.blue(), other.red())
        # Text on an accent fill flips to dark on the pale dark-theme accent.
        self.assertEqual(theming.translate_hex(theming.ON_ACCENT_SOURCE), "#1B1B19")
        # Alpha survives translation.
        self.assertEqual(theming.color(QColor(10, 20, 30, 77)).alpha(), 77)

    def test_widget_stylesheets_follow_the_theme_and_return(self) -> None:
        theming.install()
        button = QPushButton("x")
        source_css = "QPushButton { background: #FFFFFF; color: #1C2622; }"
        button.setStyleSheet(source_css)
        light = theming.spec(theming.THEME_LIGHT)
        self.assertIn(light.panel, theming._orig_style_sheet(button))
        theming.set_theme(theming.THEME_DARK)
        self.assertEqual(button.styleSheet(), source_css)
        self.assertIn(theming.DARK_PANEL, theming._orig_style_sheet(button))
        theming.set_theme(theming.THEME_LIGHT)
        self.assertIn(light.panel, theming._orig_style_sheet(button))
        self.assertNotIn(theming.DARK_PANEL, theming._orig_style_sheet(button))

    def test_window_shows_the_gradient_between_cards(self) -> None:
        window = self._make_window()
        self.assertIn("qlineargradient", theming._app_css_for(theming.THEME_DARK, ""))
        self.assertTrue(window._main_splitter.property(theming.BACKDROP_PROP))
        self.assertFalse(bool(window._files_card.property(theming.BACKDROP_PROP)))

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
