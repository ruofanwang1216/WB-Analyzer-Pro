"""Light / dark "Mist teal glass" themes for the whole application.

The interface is styled by many small Qt stylesheets written against one
source palette (the original light green one in ``config.settings``).  Rather
than maintain a copy of every sheet per theme, both themes are derived: each
colour that appears in a stylesheet is translated to its themed counterpart
just before Qt receives it, and corner radii are enlarged to the glass shapes.

* ``install()`` wraps ``QWidget.setStyleSheet`` so every sheet is recorded in
  its source form and applied in the form of the active theme.
* ``set_theme()`` re-applies every recorded sheet, the application sheet and
  the palette, then tells registered listeners to repaint anything they draw
  by hand.
* ``color()`` gives painters the themed version of a source-palette colour.
* ``mark_backdrop()`` makes a grouping container see-through so the window
  gradient shows between the cards.

Content that must look the same in both themes (blot images, the figure
preview page, plots and every export) is painted with plain ``QColor`` and is
therefore untouched.
"""
from __future__ import annotations

import colorsys
import re
import weakref
from dataclasses import dataclass
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

# Both themes are the "Mist teal glass" look: a soft teal gradient behind
# frosted cards.  Qt cannot blur what is behind a widget, so each glass layer
# is the colour that layer has once it is composited over the backdrop.
ON_ACCENT_SOURCE = "#FEFEFE"   # write this for text / icons on accent fills
WELL_SOURCE = "#EEF2EF"        # write this for the well behind an image or figure
SEGMENT_SOURCE = "#FBFCFB"     # the selected segment of a segmented control
SEGMENT_TEXT_SOURCE = "#1B1B1A"  # text on that segment (dark in both themes)


@dataclass(frozen=True)
class _Spec:
    name: str
    dark: bool
    explicit: dict
    curve: tuple          # source lightness -> themed lightness (neutrals)
    grey_hue: float
    grey_saturation: float
    accent_hue: float
    accent_saturation_cap: float
    tint_saturation_cap: float
    solid_saturation_cap: float
    backdrop: str         # the window gradient (Qt stylesheet value)
    bg: str
    panel: str
    control: str
    inset: str
    selected: str
    border: str
    text: str
    text_muted: str
    text_hint: str
    text_disabled: str
    accent: str
    accent_text: str
    on_accent: str


def _spec(name: str, dark: bool, tokens: dict, **kwargs) -> _Spec:
    t = tokens
    explicit = {
        "#E7ECE9": t["bg"],            # window background
        "#FFFFFF": t["panel"],         # surfaces
        ON_ACCENT_SOURCE: t["on_accent"],
        "#F3F6F5": t["panel"],         # toolbar
        "#F3F5F4": t["header"],        # table / section headers
        "#F7F9F8": t["alt"],           # alternate rows
        "#F7FAF8": t["alt"],
        "#F5F8F6": t["alt"],
        "#E9EEEB": t["inset"],         # segmented-control track, disabled fill
        WELL_SOURCE: t["well"],        # image / figure wells inside a card
        SEGMENT_SOURCE: t["segment"],
        SEGMENT_TEXT_SOURCE: t["segment_text"],
        "#C9D3CE": t["border"],        # borders
        "#E4E9E6": t["border_light"],  # light borders
        "#D9E0DC": t["border_card"],
        "#D3DDD8": t["border_card"],
        "#B5C2BC": t["border_dash"],
        "#1C2622": t["text"],
        "#3B4A43": t["text_muted"],
        "#4A5A53": t["text_muted2"],
        "#5A6B63": t["text_hint"],
        "#9AA8A1": t["text_disabled"],
        "#8A9891": t["text_disabled"],
        "#1F6B4A": t["accent"],        # accent
        "#1B6B4D": t["accent"],
        "#134A32": t["accent_text"],   # accent text / pressed accent
        "#E3EFE8": t["selected"],      # accent tint (hover / selected)
        "#9DBBAD": t["accent_disabled"],
        "#8AAEC4": t["band_roi"],      # band ROI stroke
    }
    fields = {k: t[k] for k in (
        "bg", "panel", "control", "inset", "selected", "border", "text",
        "text_muted", "text_hint", "text_disabled", "accent", "accent_text",
        "on_accent",
    )}
    return _Spec(name=name, dark=dark, explicit=explicit, **fields, **kwargs)


_LIGHT = _spec(
    THEME_LIGHT,
    False,
    {
        "bg": "#B9CCCC",
        "panel": "#DCE6E6",
        "control": "#EDF3F3",
        "inset": "#CEDBDB",
        "well": "#E8EFEF",
        "segment": "#F8FAFA",
        "segment_text": "#232724",
        "header": "#E8EEEE",
        "alt": "#E3EBEB",
        "selected": "#C5D5D5",
        "border": "#F6FAFA",
        "border_light": "#EAF1F1",
        "border_card": "#F1F6F6",
        "border_dash": "#9FB3B4",
        "text": "#232724",
        "text_muted": "#424543",
        "text_muted2": "#4E5856",
        "text_hint": "#667373",
        "text_disabled": "#9AA9A9",
        "accent": "#5B8182",
        "accent_text": "#405A5B",
        "accent_disabled": "#A9BEBE",
        "on_accent": "#FFFFFF",
        "band_roi": "#7C92B0",
    },
    curve=(
        (0.00, 0.00),
        (0.55, 0.55),
        (0.80, 0.74),
        (0.90, 0.81),
        (0.95, 0.85),
        (1.00, 0.88),
    ),
    grey_hue=180 / 360,
    grey_saturation=0.14,
    accent_hue=181 / 360,
    accent_saturation_cap=0.24,
    tint_saturation_cap=0.45,
    solid_saturation_cap=0.60,
    backdrop=(
        "qlineargradient(x1:0, y1:0, x2:0.35, y2:1, "
        "stop:0 #B4CACA, stop:0.48 #A7BCBE, stop:1 #D8E4E3)"
    ),
)

_DARK = _spec(
    THEME_DARK,
    True,
    {
        "bg": "#18191A",
        "panel": "#222526",
        "control": "#2C3031",
        "inset": "#1B1D1E",
        "well": "#151718",
        "segment": "#E4E8E8",
        "segment_text": "#1B1B19",
        "header": "#202324",
        "alt": "#1F2223",
        "selected": "#353B3D",
        "border": "#41474A",
        "border_light": "#2D3133",
        "border_card": "#363B3D",
        "border_dash": "#50575A",
        "text": "#F4F2F1",
        "text_muted": "#D9D5D3",
        "text_muted2": "#CDD3D3",
        "text_hint": "#B4C6C8",
        "text_disabled": "#8B9496",
        "accent": "#99B1B2",
        "accent_text": "#CBD7D7",
        "accent_disabled": "#6E7B7C",
        "on_accent": "#1B1B19",
        "band_roi": "#AAB5CB",
    },
    curve=(
        (0.00, 0.95),
        (0.13, 0.94),
        (0.30, 0.80),
        (0.45, 0.64),
        (0.60, 0.46),
        (0.80, 0.27),
        (0.90, 0.20),
        (0.95, 0.165),
        (1.00, 0.14),
    ),
    grey_hue=192 / 360,
    grey_saturation=0.06,
    accent_hue=182 / 360,
    accent_saturation_cap=0.16,
    tint_saturation_cap=0.24,
    solid_saturation_cap=0.45,
    backdrop=(
        "qlineargradient(x1:0, y1:0, x2:0.35, y2:1, "
        "stop:0 #26292A, stop:0.48 #131415, stop:1 #202223)"
    ),
)

_SPECS = {THEME_LIGHT: _LIGHT, THEME_DARK: _DARK}

# Kept for callers that want the dark tokens directly.
DARK_BG = _DARK.bg
DARK_PANEL = _DARK.panel
DARK_CONTROL = _DARK.control
DARK_INSET = _DARK.inset
DARK_SELECTED = _DARK.selected
DARK_BORDER = _DARK.border
DARK_TEXT = _DARK.text
DARK_TEXT_MUTED = _DARK.text_muted
DARK_TEXT_HINT = _DARK.text_hint
DARK_ACCENT = _DARK.accent
DARK_ACCENT_TEXT = _DARK.accent_text
DARK_ON_ACCENT = _DARK.on_accent

_HEX_RE = re.compile(r"#([0-9A-Fa-f]{6})\b")
_RGBA_RE = re.compile(
    r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(,\s*[0-9.]+\s*)?\)"
)
_RADIUS_RE = re.compile(r"(?<![-\w])border-radius:\s*(\d+)px(?!;?\s*/\* exact)")

# Object name of the window that paints the gradient backdrop.
WINDOW_NAME = "wb_main_window"

# Marks a container that only groups cards, so the backdrop shows through.
BACKDROP_PROP = "glassBackdrop"

_theme = THEME_LIGHT
_installed = False
_app_css: str | None = None
_listeners: list = []
_reverse: dict[tuple[int, int, int], tuple[int, int, int]] = {}
_orig_set_style_sheet = QWidget.setStyleSheet
_orig_style_sheet = QWidget.styleSheet


def current() -> str:
    return _theme


def is_dark() -> bool:
    return _theme == THEME_DARK


def spec(theme: str | None = None) -> _Spec:
    """Return the colour tokens of *theme* (the active theme by default)."""
    return _SPECS.get(theme or _theme, _LIGHT)


def _curve(points: tuple, lightness: float) -> float:
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if lightness <= x1:
            span = (x1 - x0) or 1.0
            return y0 + (y1 - y0) * (lightness - x0) / span
    return points[-1][1]


@lru_cache(maxsize=4096)
def _themed_rgb(theme: str, r: int, g: int, b: int) -> tuple[int, int, int]:
    sp = _SPECS[theme]
    key = f"#{r:02X}{g:02X}{b:02X}"
    mapped = sp.explicit.get(key)
    if mapped is not None:
        return int(mapped[1:3], 16), int(mapped[3:5], 16), int(mapped[5:7], 16)

    hue, lightness, saturation = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    new_lightness = _curve(sp.curve, lightness)
    degrees = hue * 360.0
    if saturation < 0.16:
        # Grey (with a green cast in the source palette) -> teal-grey.
        hue = sp.grey_hue
        saturation = sp.grey_saturation if 0.12 < lightness < 0.97 else 0.04
    elif 95.0 <= degrees <= 185.0:
        # The green family becomes the muted teal accent family.
        hue = sp.accent_hue
        saturation = min(saturation * 0.6, sp.accent_saturation_cap)
        if lightness < 0.5:
            if sp.dark:
                new_lightness = min(new_lightness, 0.74)
            else:
                new_lightness = min(0.5, lightness + 0.12)
    else:
        # Pale tints stay quiet on the surface; solid colours keep more.
        cap = sp.tint_saturation_cap if lightness > 0.75 else sp.solid_saturation_cap
        saturation = min(saturation * (0.6 if sp.dark else 0.8), cap)
    nr, ng, nb = colorsys.hls_to_rgb(hue, new_lightness, saturation)
    return round(nr * 255), round(ng * 255), round(nb * 255)


def translate_hex(value: str) -> str:
    """Return the active-theme version of a ``#RRGGBB`` source colour."""
    r, g, b = _themed_rgb(
        _theme, int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)
    )
    return f"#{r:02X}{g:02X}{b:02X}"


def _radius(match: re.Match) -> str:
    # Glass shapes: pill-like controls, generously rounded cards.  Qt drops
    # a radius larger than half the widget, so the values stay within the
    # smallest controls that use each source radius (22 px and 26 px tall).
    # Write "border-radius: 8px /* exact */" to opt a rule out.
    value = int(match.group(1))
    if 5 <= value <= 7:
        value = 11
    elif value == 8:
        value = 13
    elif 9 <= value <= 15:
        value = 18
    return f"border-radius: {value}px"


@lru_cache(maxsize=4096)
def _css_for(theme: str, css: str) -> str:
    if not css:
        return css

    def _hex(match: re.Match) -> str:
        text = match.group(1)
        r, g, b = _themed_rgb(
            theme, int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
        )
        return f"#{r:02X}{g:02X}{b:02X}"

    def _rgba(match: re.Match) -> str:
        r, g, b = _themed_rgb(
            theme, *(min(255, int(match.group(i))) for i in (1, 2, 3))
        )
        alpha = match.group(4)
        if alpha:
            return f"rgba({r}, {g}, {b}{alpha})"
        return f"rgb({r}, {g}, {b})"

    themed = _RGBA_RE.sub(_rgba, _HEX_RE.sub(_hex, css))
    themed = _RADIUS_RE.sub(_radius, themed)
    if _CHEVRON_TOKEN in themed:
        themed = themed.replace(_CHEVRON_TOKEN, f"url({_chevron_path(theme)})")
    return themed


_CHEVRON_TOKEN = "url(theme:chevron-down)"


def _chevron_path(theme: str) -> str:
    """A small down-chevron image in the theme's text colour (made once)."""
    import tempfile
    from pathlib import Path

    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QImage, QPainter, QPen

    folder = Path(tempfile.gettempdir()) / "wb_analyzer_pro_theme"
    path = folder / f"chevron-down-{theme}.png"
    if not path.exists():
        folder.mkdir(parents=True, exist_ok=True)
        image = QImage(44, 44, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(_SPECS[theme].text_muted), 5.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPolyline([QPointF(10, 16), QPointF(22, 28), QPointF(34, 16)])
        painter.end()
        image.save(str(path))
    return path.as_posix()


def _app_css_for(theme: str, css: str) -> str:
    sp = _SPECS[theme]
    return _css_for(theme, css) + f"""
QMainWindow#{WINDOW_NAME} {{ background: {sp.backdrop}; }}
QWidget[{BACKDROP_PROP}="true"] {{ background: transparent; }}
"""


def translate_css(css: str) -> str:
    """Return *css* (written in the source palette) in the active theme."""
    return _css_for(_theme, css)


def mark_backdrop(*widgets: QWidget) -> None:
    """Let the window backdrop show through container *widgets*."""
    for widget in widgets:
        widget.setProperty(BACKDROP_PROP, True)
        # A property set after the first polish needs the rules re-evaluated.
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)


def color(value) -> QColor:
    """Return the active-theme ``QColor`` for a source-palette colour."""
    source = QColor(value)
    r, g, b = _themed_rgb(_theme, source.red(), source.green(), source.blue())
    _reverse[(r, g, b)] = (source.red(), source.green(), source.blue())
    themed = QColor(r, g, b)
    themed.setAlpha(source.alpha())
    return themed


def retint_items(table) -> None:
    """Re-colour the item backgrounds / foregrounds of a table for the theme.

    Items coloured through ``color()`` are found again by value: the source
    colour is looked back up and translated for the theme that is now active.
    """
    from PySide6.QtGui import QBrush

    def _swap(brush):
        if not isinstance(brush, (QBrush, QColor)):
            return None
        source = brush.color() if isinstance(brush, QBrush) else brush
        if isinstance(brush, QBrush) and brush.style() == Qt.BrushStyle.NoBrush:
            return None
        key = (source.red(), source.green(), source.blue())
        original = _reverse.get(key)
        if original is None:
            return None  # never themed: leave it alone
        themed = color(QColor(*original))
        themed.setAlpha(source.alpha())
        if themed == source:
            return None
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
    global _installed, _app_css
    app = QApplication.instance()
    if not _installed:
        QWidget.setStyleSheet = _themed_set_style_sheet
        QWidget.styleSheet = _themed_style_sheet
        _installed = True
    if app is None:
        return
    if app_stylesheet is not None:
        _app_css = app_stylesheet
        app.setStyleSheet(_app_css_for(_theme, app_stylesheet))
        app.setPalette(_palette(_theme))


def on_change(callback: Callable[[], None]) -> None:
    """Call *callback* after every theme change (held weakly for methods)."""
    try:
        ref = weakref.WeakMethod(callback)
    except TypeError:
        ref = lambda: callback  # noqa: E731 - plain functions live forever
    _listeners.append(ref)


def _palette(theme: str) -> QPalette:
    sp = _SPECS[theme]
    palette = QPalette()
    roles = QPalette.ColorRole
    pairs = {
        roles.Window: sp.bg,
        roles.WindowText: sp.text,
        roles.Base: sp.panel,
        roles.AlternateBase: sp.explicit["#F7F9F8"],
        roles.Text: sp.text,
        roles.Button: sp.control,
        roles.ButtonText: sp.text,
        roles.ToolTipBase: sp.selected,
        roles.ToolTipText: sp.text,
        roles.Highlight: sp.accent,
        roles.HighlightedText: sp.on_accent,
        roles.PlaceholderText: sp.text_disabled,
        roles.Link: sp.accent_text,
        roles.Light: sp.control,
        roles.Midlight: sp.selected,
        roles.Mid: sp.border,
        roles.Dark: sp.inset,
        roles.Shadow: "#000000" if sp.dark else "#3B5454",
    }
    for role, value in pairs.items():
        palette.setColor(role, QColor(value))
    disabled = QPalette.ColorGroup.Disabled
    for role in (roles.WindowText, roles.Text, roles.ButtonText):
        palette.setColor(disabled, role, QColor(sp.text_disabled))
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
    app.setPalette(_palette(theme))
    if _app_css is not None:
        app.setStyleSheet(_app_css_for(theme, _app_css))

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
