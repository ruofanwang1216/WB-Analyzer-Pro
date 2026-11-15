"""Application-wide Qt stylesheet built from the shared colour palette."""
from __future__ import annotations

from config.settings import (
    COLOR_ACCENT,
    COLOR_ACCENT_DARK,
    COLOR_ACCENT_LIGHT,
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
QToolTip {{
    background-color: #1C2622;
    color: #FEFEFE;
    border: none;
    padding: 4px 8px;
    font-size: 12px;
}}
"""
