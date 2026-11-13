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
    background-color: {COLOR_TOOLBAR};
    border-bottom: 1px solid {COLOR_BORDER};
    spacing: 4px;
    padding: 4px 8px;
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
    background-color: {COLOR_TOOLBAR};
    color: {COLOR_TEXT_HINT};
    border-top: 1px solid #D9E0DC;
    font-size: 12px;
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
QToolTip {{
    background-color: #1C2622;
    color: #FFFFFF;
    border: none;
    padding: 4px 8px;
    font-size: 12px;
}}
"""
