"""Shared button stylesheets for the WB figure workspace."""
from __future__ import annotations

# Toolbar button height cap — keeps the annotation bar compact
_TOOLBAR_BTN_H = 26


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
