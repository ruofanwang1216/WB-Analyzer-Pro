"""Restyling applied on top of widgets whose own modules are left untouched.

The lane-settings panel is being extended on another branch, so the redesign
changes its look from the outside instead of editing its file.
"""
from __future__ import annotations

from PySide6.QtWidgets import QSizePolicy, QWidget

from config.settings import (
    COLOR_ACCENT,
    COLOR_ACCENT_LIGHT,
    COLOR_ACCENT_TEXT,
    COLOR_BORDER,
    COLOR_BORDER_LIGHT,
    COLOR_SURFACE,
    COLOR_TEXT,
    COLOR_TEXT_HINT,
    COLOR_TEXT_MUTED,
)

_QWIDGETSIZE_MAX = 16777215

PARAM_PANEL_STYLESHEET = f"""
    QWidget {{
        background-color: {COLOR_SURFACE};
    }}
    QLabel {{
        color: {COLOR_TEXT};
        font-size: 13px;
        background: transparent;
    }}
    QGroupBox {{
        color: {COLOR_TEXT_MUTED};
        font-size: 12px;
        font-weight: 600;
        border: none;
        margin-top: 18px;
        padding-top: 4px;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 0px;
    }}
    QLineEdit, QSpinBox, QComboBox {{
        background-color: {COLOR_SURFACE};
        border: 1px solid {COLOR_BORDER};
        border-radius: 6px;
        padding: 4px 6px;
        color: {COLOR_TEXT};
        font-size: 14px;
        font-weight: 600;
        min-height: 22px;
    }}
    QComboBox::drop-down {{
        border: none;
        background: transparent;
    }}
    QPushButton {{
        background-color: {COLOR_SURFACE};
        border: 1px solid {COLOR_BORDER};
        border-radius: 8px;
        color: #3B4A43;
        padding: 7px 8px;
        font-size: 13px;
        font-weight: 400;
    }}
    QPushButton:hover {{
        background-color: {COLOR_ACCENT_LIGHT};
        border-color: {COLOR_ACCENT};
        color: {COLOR_ACCENT_TEXT};
    }}
    QPushButton:disabled {{
        color: #9AA8A1;
        border-color: {COLOR_BORDER_LIGHT};
    }}
    QPushButton#lane_dec, QPushButton#lane_inc {{
        font-size: 16px;
        padding: 0px;
        border-radius: 6px;
    }}
    QPushButton#lane_dec:pressed, QPushButton#lane_inc:pressed {{
        background-color: {COLOR_ACCENT};
        color: #FFFFFF;
    }}
    QListWidget {{
        background: {COLOR_SURFACE};
        border: 1px solid {COLOR_BORDER_LIGHT};
        border-radius: 6px;
        font-size: 12px;
    }}
    QListWidget::item {{
        padding: 2px 4px;
    }}
    QListWidget::item:selected {{
        background: {COLOR_ACCENT_LIGHT};
        color: {COLOR_ACCENT_TEXT};
    }}
"""


def restyle_param_panel(panel: QWidget) -> None:
    """Give the lane-settings panel the sidebar look and let it fill the width."""
    panel.setMinimumWidth(0)
    panel.setMaximumWidth(_QWIDGETSIZE_MAX)
    panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    panel.setStyleSheet(PARAM_PANEL_STYLESHEET)
    layout = panel.layout()
    if layout is not None:
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
    section_style = (
        f"color: {COLOR_TEXT_MUTED}; font-size: 12px; font-weight: 600;"
    )
    for name in ("_lane_section_label", "_fixed_label"):
        label = getattr(panel, name, None)
        if label is not None:
            label.setStyleSheet(section_style)
    lanes_label = getattr(panel, "_lanes_label", None)
    if lanes_label is not None:
        lanes_label.setStyleSheet(f"color: {COLOR_TEXT}; font-size: 13px;")
    help_note = getattr(panel, "_help_note", None)
    if help_note is not None:
        help_note.setStyleSheet(
            f"color: {COLOR_TEXT_HINT}; font-size: 12px;"
        )
    fixed_list = getattr(panel, "_fixed_roi_list", None)
    if fixed_list is not None:
        fixed_list.setStyleSheet("")
    # The outlined primary look marks the one-click helper when it exists.
    auto_btn = getattr(panel, "_auto_lane_btn", None)
    if auto_btn is not None:
        auto_btn.setStyleSheet(
            f"QPushButton {{ border-color: {COLOR_ACCENT}; color: {COLOR_ACCENT}; "
            "font-weight: 600; }"
        )
