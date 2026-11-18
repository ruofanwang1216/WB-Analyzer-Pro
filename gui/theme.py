"""Application-wide Qt stylesheet built from the shared colour palette."""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox

from config.settings import (
    COLOR_ACCENT,
    COLOR_ACCENT_DARK,
    COLOR_ACCENT_LIGHT,
    COLOR_ACCENT_TEXT,
    COLOR_BG,
    COLOR_BORDER,
    COLOR_RESULTS_HD,
    COLOR_SURFACE,
    COLOR_TEXT,
    COLOR_TEXT_HINT,
    COLOR_TOOLBAR,
)

APP_STYLESHEET = f"""
QMainWindow, QWidget {{
    background-color: {COLOR_BG};
    color: {COLOR_TEXT};
    font-size: 13px;
}}
QToolBar {{
    background: transparent;
    border: none;
    spacing: 10px;
    padding: 9px 14px 3px 14px;
}}
QToolBar QToolButton {{
    background-color: {COLOR_SURFACE};
    border: 1px solid {COLOR_BORDER};
    border-radius: 6px;
    padding: 4px 10px;
    color: {COLOR_TEXT};
}}
QToolBar QToolButton:hover {{
    background-color: {COLOR_ACCENT_LIGHT};
    border-color: {COLOR_ACCENT};
}}
QToolBar QToolButton[text="Analyze"] {{
    background-color: {COLOR_ACCENT};
    border-color: {COLOR_ACCENT_DARK};
    color: {COLOR_SURFACE};
    font-weight: bold;
}}
QToolBar QToolButton[text="Analyze"]:hover {{
    background-color: {COLOR_ACCENT_DARK};
}}
QStatusBar {{
    background: transparent;
    color: #3B4A43;
    border: none;
    font-size: 12px;
}}
QStatusBar QLabel, QStatusBar QSizeGrip {{
    background: transparent;
}}
QDockWidget {{
    background-color: {COLOR_SURFACE};
    border-top: 1px solid {COLOR_BORDER};
}}
QDockWidget::title {{
    background-color: {COLOR_RESULTS_HD};
    padding: 4px 10px;
    font-size: 12px;
    color: {COLOR_TEXT};
    border-bottom: 1px solid {COLOR_BORDER};
}}
QSplitter::handle {{
    background-color: transparent;
}}
QSplitter::handle:hover {{
    background-color: {COLOR_ACCENT_LIGHT};
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0px;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0px;
}}
QScrollBar::handle:vertical {{
    background: #B5C2BC;
    border-radius: 3px;
    min-height: 28px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: #B5C2BC;
    border-radius: 3px;
    min-width: 28px;
    margin: 2px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0px;
    height: 0px;
    border: none;
    background: none;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
}}
QAbstractScrollArea::corner {{
    background: transparent;
}}
QDialog QPushButton {{
    background-color: {COLOR_SURFACE};
    border: 1px solid {COLOR_BORDER};
    border-radius: 8px;
    color: {COLOR_TEXT};
    padding: 5px 14px;
    min-height: 16px;
}}
QDialog QPushButton:hover {{
    background-color: {COLOR_ACCENT_LIGHT};
    border-color: {COLOR_ACCENT};
}}
QDialog QPushButton:default {{
    background-color: {COLOR_ACCENT};
    border-color: {COLOR_ACCENT};
    color: #FEFEFE;
}}
QDialog QPushButton:disabled {{
    color: #9AA8A1;
}}
QDialog QLineEdit, QDialog QComboBox {{
    background-color: {COLOR_SURFACE};
    border: 1px solid {COLOR_BORDER};
    border-radius: 6px;
    color: {COLOR_TEXT};
    padding: 3px 8px;
    min-height: 16px;
}}
QDialog QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QDialog QComboBox::down-arrow {{
    image: url(theme:chevron-down);
    width: 10px;
    height: 10px;
    margin-right: 8px;
}}
/* Drop-down lists.  On macOS a styled QComboBox otherwise opens the native
   "pop up over the current item" menu, which Qt sizes from the closed box:
   with rounded, padded boxes it came out too short, showing half a row and
   a scroll arrow that could not be dragged.  combobox-popup: 0 makes every
   combo open an ordinary list below itself, sized to all of its items. */
QComboBox {{
    combobox-popup: 0;
}}
QComboBox QAbstractItemView {{
    background-color: {COLOR_SURFACE};
    color: {COLOR_TEXT};
    border: 1px solid {COLOR_BORDER};
    border-radius: 8px;
    padding: 4px;
    outline: none;
    selection-background-color: {COLOR_ACCENT_LIGHT};
    selection-color: {COLOR_ACCENT_TEXT};
}}
QComboBox QAbstractItemView::item {{
    min-height: 24px;
    padding: 2px 8px;
    border-radius: 5px;
}}
QToolTip {{
    background-color: #1C2622;
    color: #FEFEFE;
    border: none;
    padding: 4px 8px;
    font-size: 12px;
}}
"""


_original_show_popup = QComboBox.showPopup


def _show_popup_wide_enough(self: QComboBox) -> None:
    """Open the list at least as wide as its longest entry.

    The list otherwise takes the width of the closed box, which cut off
    longer entries such as "Distribute Horizontally".
    """
    view = self.view()
    if view is not None and self.count():
        metrics = view.fontMetrics()
        widest = max(metrics.horizontalAdvance(self.itemText(i)) for i in range(self.count()))
        # Item padding, list padding and border, and room for a scroll bar.
        needed = widest + 2 * 8 + 2 * 4 + 2 + 16
        view.setMinimumWidth(max(self.width(), min(needed, 420)))
    _original_show_popup(self)


def install_combo_popup_fix() -> None:
    """Make every drop-down list wide enough for its entries (idempotent)."""
    if QComboBox.showPopup is not _show_popup_wide_enough:
        QComboBox.showPopup = _show_popup_wide_enough
