"""Shared button stylesheets for the WB figure workspace."""
from __future__ import annotations

# Control heights shared by the canvas bar and the step panel
_TOOLBAR_BTN_H = 30
_PANEL_BTN_H = 36


_APPLY_BTN_STYLE = (
    "QPushButton {"
    "  background-color: #1F6B4A;"
    "  border: 1px solid #1F6B4A;"
    "  border-radius: 8px;"
    "  color: #FFFFFF;"
    "  padding: 6px 12px;"
    "  font-size: 13px;"
    "  font-weight: 600;"
    "}"
    "QPushButton:hover { background-color: #134A32; }"
    "QPushButton:disabled { background-color: #9DBBAD; border-color: #9DBBAD; }"
)


_SMALL_BTN_STYLE = (
    "QPushButton {"
    "  background-color: #FFFFFF;"
    "  border: 1px solid #C9D3CE;"
    "  border-radius: 8px;"
    "  color: #3B4A43;"
    "  padding: 4px 12px;"
    "  font-size: 12px;"
    "}"
    "QPushButton:hover { background-color: #E3EFE8; border-color: #1F6B4A; }"
    "QPushButton:disabled { color: #9AA8A1; border-color: #E4E9E6; }"
)


_HIGHLIGHT_BTN_STYLE = (
    "QPushButton {"
    "  background-color: #FFFFFF;"
    "  border: 1px solid #1F6B4A;"
    "  border-radius: 8px;"
    "  color: #1F6B4A;"
    "  padding: 4px 12px;"
    "  font-size: 12px;"
    "  font-weight: 600;"
    "}"
    "QPushButton:hover { background-color: #E3EFE8; }"
    "QPushButton:pressed { background-color: #CFE3D8; }"
)


_SECONDARY_EXPORT_BTN_STYLE = (
    "QPushButton {"
    "  background-color: #FFFFFF;"
    "  border: 1px solid #1F6B4A;"
    "  border-radius: 8px;"
    "  color: #1F6B4A;"
    "  padding: 6px 8px;"
    "  font-size: 13px;"
    "  font-weight: 600;"
    "}"
    "QPushButton:hover { background-color: #E3EFE8; }"
    "QPushButton:disabled { background-color: #F3F5F4; color: #9AA8A1; border-color: #E4E9E6; }"
)


# ── Step panel (right-hand side of the Figure Layout workspace) ───────────────

_PANEL_BTN_STYLE = (
    "QPushButton {"
    "  background-color: #FFFFFF;"
    "  border: 1px solid #C9D3CE;"
    "  border-radius: 8px;"
    "  color: #1C2622;"
    "  padding: 6px 12px;"
    "  font-size: 13px;"
    "  text-align: left;"
    "}"
    "QPushButton:hover { background-color: #E3EFE8; border-color: #1F6B4A; }"
)

_PANEL_ACCENT_BTN_STYLE = (
    "QPushButton {"
    "  background-color: #FFFFFF;"
    "  border: 1px solid #1F6B4A;"
    "  border-radius: 8px;"
    "  color: #1F6B4A;"
    "  padding: 6px 12px;"
    "  font-size: 13px;"
    "  font-weight: 600;"
    "}"
    "QPushButton:hover { background-color: #E3EFE8; }"
)

_PANEL_TOOL_BTN_STYLE = (
    "QToolButton {"
    "  background-color: #FFFFFF;"
    "  border: 1px solid #C9D3CE;"
    "  border-radius: 6px;"
    "  color: #1C2622;"
    "  font-size: 13px;"
    "  padding: 2px 6px;"
    "}"
    "QToolButton:checked { background-color: #E3EFE8; border-color: #1F6B4A; color: #134A32; }"
    "QToolButton:hover { background-color: #E3EFE8; }"
    "QToolButton:disabled { color: #9AA8A1; border-color: #E4E9E6; }"
)

_PANEL_HEADING_STYLE = (
    "font-size:12px; font-weight:600; color:#4A5A53; background:transparent;"
)

_SMALL_ACCENT_BTN_STYLE = (
    "QPushButton {"
    "  background-color: #FFFFFF;"
    "  border: 1px solid #1F6B4A;"
    "  border-radius: 8px;"
    "  color: #1F6B4A;"
    "  padding: 4px 12px;"
    "  font-size: 12px;"
    "  font-weight: 600;"
    "}"
    "QPushButton:hover { background-color: #E3EFE8; }"
)
