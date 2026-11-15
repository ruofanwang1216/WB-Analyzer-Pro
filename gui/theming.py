"""Light / dark theme switch for the whole application.

The interface is styled by many small Qt stylesheets written against the
light (green) palette.  Rather than maintain a second copy of every sheet,
the dark theme is derived: each colour that appears in a stylesheet is
translated to its dark counterpart just before Qt receives it.

* ``install()`` wraps ``QWidget.setStyleSheet`` so every sheet is recorded in
  its original (light) form and applied in the form of the active theme.
* ``set_theme()`` re-applies every recorded sheet, the application sheet and
  the palette, then tells registered listeners to repaint anything they draw
  by hand.
* ``color()`` gives painters the themed version of a light-palette colour.

Content that must look the same in both themes (blot images, the figure
preview page, plots and every export) is painted with plain ``QColor`` and is
therefore untouched.
"""
from __future__ import annotations

import colorsys
import re
import weakref
from functools import lru_cache
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QWidget

THEME_LIGHT = "light"
THEME_DARK = "dark"
THEMES = (THEME_LIGHT, THEME_DARK)

_CSS_PROP = "_theme_css"
_STATIC_PROP = "_theme_static"

# Dark palette (graphite with a muted "Morandi" blue accent).
DARK_BG = "#161618"
DARK_PANEL = "#242426"
DARK_CONTROL = "#2C2C2E"
DARK_INSET = "#1C1C1E"
DARK_SELECTED = "#3A3A3C"
DARK_BORDER = "#3F3F42"
DARK_TEXT = "#F5F5F7"
DARK_TEXT_MUTED = "#D1D1D6"
DARK_TEXT_HINT = "#A1A1A6"
DARK_ACCENT = "#8FA6BA"
DARK_ACCENT_TEXT = "#A9BDCE"
DARK_ON_ACCENT = "#1C1C1E"

# Light colour -> dark colour for the tokens that define the look.
_EXPLICIT: dict[str, str] = {
    "#E7ECE9": DARK_BG,           # window background
    "#FFFFFF": DARK_PANEL,        # surfaces (and text on accent buttons)
    "#F3F6F5": DARK_PANEL,        # toolbar
    "#F3F5F4": DARK_CONTROL,      # table / section headers
    "#F7F9F8": "#29292B",         # alternate rows
    "#F7FAF8": "#29292B",
    "#F5F8F6": "#29292B",
    "#C9D3CE": DARK_BORDER,       # borders
    "#E4E9E6": "#323235",         # light borders
    "#D9E0DC": "#38383B",
    "#D3DDD8": "#3A3A3D",
    "#1C2622": DARK_TEXT,
    "#3B4A43": DARK_TEXT_MUTED,
    "#4A5A53": "#C4C4C9",
    "#5A6B63": DARK_TEXT_HINT,
    "#9AA8A1": "#77777D",
    "#1F6B4A": DARK_ACCENT,       # accent
    "#1B6B4D": DARK_ACCENT,
    "#134A32": DARK_ACCENT_TEXT,  # accent text / pressed accent
    "#E3EFE8": "#343C44",         # accent tint (hover / selected)
    "#8AAEC4": "#4F6F8C",         # band ROI stroke
}

_HEX_RE = re.compile(r"#([0-9A-Fa-f]{6})\b")
_RGBA_RE = re.compile(
    r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(,\s*[0-9.]+\s*)?\)"
)

# Lightness of a light-theme colour -> lightness of its dark counterpart.
_LIGHTNESS_CURVE = (
    (0.00, 0.98),
    (0.13, 0.95),
    (0.30, 0.80),
    (0.45, 0.64),
    (0.60, 0.46),
    (0.80, 0.27),
    (0.90, 0.21),
    (0.95, 0.17),
    (1.00, 0.145),
)

_theme = THEME_LIGHT
_installed = False
_app_css: str | None = None
_light_palette: QPalette | None = None
_listeners: list = []
_reverse: dict[tuple[int, int, int], tuple[int, int, int]] = {}
_orig_set_style_sheet = QWidget.setStyleSheet
_orig_style_sheet = QWidget.styleSheet


def current() -> str:
    return _theme


def is_dark() -> bool:
    return _theme == THEME_DARK


def _curve(lightness: float) -> float:
    points = _LIGHTNESS_CURVE
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if lightness <= x1:
            span = (x1 - x0) or 1.0
            return y0 + (y1 - y0) * (lightness - x0) / span
    return points[-1][1]


@lru_cache(maxsize=1024)
def _dark_rgb(r: int, g: int, b: int) -> tuple[int, int, int]:
    key = f"#{r:02X}{g:02X}{b:02X}"
    mapped = _EXPLICIT.get(key)
    if mapped is not None:
        return int(mapped[1:3], 16), int(mapped[3:5], 16), int(mapped[5:7], 16)

    hue, lightness, saturation = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    new_lightness = _curve(lightness)
    degrees = hue * 360.0
    if saturation < 0.16:
        # Grey (usually with a green cast in the light theme) -> graphite.
        hue, saturation = 240 / 360, 0.03
    elif 95.0 <= degrees <= 185.0:
        # The green family becomes the muted blue accent family.
        hue = 208 / 360
        saturation = min(saturation * 0.6, 0.30)
        if lightness < 0.5:
            new_lightness = min(new_lightness, 0.74)
    else:
        # Pale tints stay quiet on the dark surface; solid colours keep more.
        saturation = min(saturation * 0.6, 0.28 if lightness > 0.75 else 0.5)
    nr, ng, nb = colorsys.hls_to_rgb(hue, new_lightness, saturation)
    return round(nr * 255), round(ng * 255), round(nb * 255)


def translate_hex(value: str) -> str:
    """Return the active-theme version of a ``#RRGGBB`` light-theme colour."""
    if _theme != THEME_DARK:
        return value
    r, g, b = _dark_rgb(int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16))
    return f"#{r:02X}{g:02X}{b:02X}"


def _css_for(theme: str, css: str) -> str:
    if theme != THEME_DARK or not css:
        return css

    def _hex(match: re.Match) -> str:
        text = match.group(1)
        r, g, b = _dark_rgb(int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
        return f"#{r:02X}{g:02X}{b:02X}"

    def _rgba(match: re.Match) -> str:
        r, g, b = _dark_rgb(*(min(255, int(match.group(i))) for i in (1, 2, 3)))
        alpha = match.group(4)
        if alpha:
            return f"rgba({r}, {g}, {b}{alpha})"
        return f"rgb({r}, {g}, {b})"

    return _RGBA_RE.sub(_rgba, _HEX_RE.sub(_hex, css))


def translate_css(css: str) -> str:
    """Return *css* (written for the light theme) in the active theme."""
    return _css_for(_theme, css)


def color(value) -> QColor:
    """Return the active-theme ``QColor`` for a light-theme colour."""
    source = QColor(value)
    if _theme != THEME_DARK:
        return source
    r, g, b = _dark_rgb(source.red(), source.green(), source.blue())
    _reverse[(r, g, b)] = (source.red(), source.green(), source.blue())
    themed = QColor(r, g, b)
    themed.setAlpha(source.alpha())
    return themed


def retint_items(table) -> None:
    """Re-colour the item backgrounds / foregrounds of a table for the theme.

    Items coloured through ``color()`` are found again by value: going dark
    translates them, going light looks the original colour back up.
    """
    from PySide6.QtGui import QBrush

    def _swap(brush):
        if not isinstance(brush, (QBrush, QColor)):
            return None
        source = brush.color() if isinstance(brush, QBrush) else brush
        if isinstance(brush, QBrush) and brush.style() == Qt.BrushStyle.NoBrush:
            return None
        key = (source.red(), source.green(), source.blue())
        if _theme == THEME_DARK:
            if key in _reverse:
                return None  # already dark
            return color(source)
        light = _reverse.get(key)
        if light is None:
            return None
        themed = QColor(*light)
        themed.setAlpha(source.alpha())
        return themed

    roles = (Qt.ItemDataRole.BackgroundRole, Qt.ItemDataRole.ForegroundRole)
    items = [
        table.item(row, col)
        for row in range(table.rowCount())
        for col in range(table.columnCount())
    ]
    items += [table.horizontalHeaderItem(col) for col in range(table.columnCount())]
    items += [table.verticalHeaderItem(row) for row in range(table.rowCount())]
    for item in items:
        if item is None:
            continue
        for role in roles:
            swapped = _swap(item.data(role))
            if swapped is not None:
                item.setData(role, swapped)


def keep_static(widget: QWidget) -> None:
    """Exclude *widget*'s own stylesheet from theme translation."""
    widget.setProperty(_STATIC_PROP, True)


def _themed_set_style_sheet(self: QWidget, css: str) -> None:
    css = css or ""
    try:
        self.setProperty(_CSS_PROP, css)
        static = bool(self.property(_STATIC_PROP))
    except RuntimeError:
        static = True
    _orig_set_style_sheet(self, css if static else _css_for(_theme, css))


def _themed_style_sheet(self: QWidget) -> str:
    recorded = self.property(_CSS_PROP)
    if isinstance(recorded, str):
        return recorded
    return _orig_style_sheet(self)


def install(app_stylesheet: str | None = None) -> None:
    """Start routing widget stylesheets through the theme (idempotent)."""
    global _installed, _app_css, _light_palette
    app = QApplication.instance()
    if not _installed:
        QWidget.setStyleSheet = _themed_set_style_sheet
        QWidget.styleSheet = _themed_style_sheet
        _installed = True
    if app is None:
        return
    if _light_palette is None:
        _light_palette = QPalette(app.palette())
    if app_stylesheet is not None:
        _app_css = app_stylesheet
        app.setStyleSheet(_css_for(_theme, app_stylesheet))


def on_change(callback: Callable[[], None]) -> None:
    """Call *callback* after every theme change (held weakly for methods)."""
    try:
        ref = weakref.WeakMethod(callback)
    except TypeError:
        ref = lambda: callback  # noqa: E731 - plain functions live forever
    _listeners.append(ref)


def _dark_palette() -> QPalette:
    palette = QPalette()
    roles = QPalette.ColorRole
    pairs = {
        roles.Window: DARK_BG,
        roles.WindowText: DARK_TEXT,
        roles.Base: DARK_PANEL,
        roles.AlternateBase: "#29292B",
        roles.Text: DARK_TEXT,
        roles.Button: DARK_CONTROL,
        roles.ButtonText: DARK_TEXT,
        roles.ToolTipBase: DARK_SELECTED,
        roles.ToolTipText: DARK_TEXT,
        roles.Highlight: DARK_ACCENT,
        roles.HighlightedText: DARK_ON_ACCENT,
        roles.PlaceholderText: "#77777D",
        roles.Link: DARK_ACCENT_TEXT,
        roles.Light: "#48484A",
        roles.Midlight: DARK_SELECTED,
        roles.Mid: DARK_BORDER,
        roles.Dark: DARK_INSET,
        roles.Shadow: "#000000",
    }
    for role, value in pairs.items():
        palette.setColor(role, QColor(value))
    disabled = QPalette.ColorGroup.Disabled
    for role in (roles.WindowText, roles.Text, roles.ButtonText):
        palette.setColor(disabled, role, QColor("#6C6C70"))
    return palette


def set_theme(theme: str) -> str:
    """Switch the whole application to *theme* and return the active theme."""
    global _theme
    theme = theme if theme in THEMES else THEME_LIGHT
    install()
    changed = theme != _theme
    _theme = theme
    app = QApplication.instance()
    if app is None or not changed:
        return _theme

    hints = app.styleHints()
    if hasattr(hints, "setColorScheme"):
        try:
            hints.setColorScheme(
                Qt.ColorScheme.Dark if theme == THEME_DARK else Qt.ColorScheme.Light
            )
        except Exception:
            pass
    if theme == THEME_DARK:
        app.setPalette(_dark_palette())
    elif _light_palette is not None:
        app.setPalette(_light_palette)
    if _app_css is not None:
        app.setStyleSheet(_css_for(theme, _app_css))

    for widget in app.allWidgets():
        try:
            recorded = widget.property(_CSS_PROP)
            if not isinstance(recorded, str) or not recorded:
                continue
            if widget.property(_STATIC_PROP):
                continue
            _orig_set_style_sheet(widget, _css_for(theme, recorded))
        except RuntimeError:
            continue

    alive = []
    for ref in _listeners:
        callback = ref()
        if callback is None:
            continue
        alive.append(ref)
        try:
            callback()
        except RuntimeError:
            pass
    _listeners[:] = alive

    for widget in app.topLevelWidgets():
        try:
            widget.update()
        except RuntimeError:
            pass
    return _theme
