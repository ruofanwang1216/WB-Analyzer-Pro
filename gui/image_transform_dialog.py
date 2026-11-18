"""Modeless Image Transform controls for WB image preview display.

The controls follow Bio-Rad Image Lab's "Image Transform" window (histogram,
High / Low / Gamma, Auto Scale, invert, highlight saturated pixels, log
histogram) and add ImageJ's Brightness / Contrast sliders.
Everything here changes how the image is shown, never the data.
"""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.image_transform import (
    IMAGEJ_SLIDER_RANGE,
    ImageTransformParams,
    MAX_16BIT_VALUE,
    MAX_GAMMA,
    MAX_TONE_VALUE,
    MIN_GAMMA,
    MIN_TONE_VALUE,
    imagej_adjust_brightness,
    imagej_adjust_contrast,
    imagej_slider_positions,
)
from gui import theming
from utils.i18n import LANG_EN, tr

# The gamma slider is logarithmic so that 1.0 (linear) sits in the middle and
# 0.5 / 2.0 are the same distance from it.
_GAMMA_SLIDER_STEPS = 100
_GAMMA_LOG_SPAN = math.log10(MAX_GAMMA)  # MIN_GAMMA is its reciprocal


def _gamma_to_slider(gamma: float) -> int:
    gamma = max(MIN_GAMMA, min(MAX_GAMMA, float(gamma)))
    return int(round(math.log10(gamma) / _GAMMA_LOG_SPAN * _GAMMA_SLIDER_STEPS))


def _slider_to_gamma(position: int) -> float:
    return round(10.0 ** (position / _GAMMA_SLIDER_STEPS * _GAMMA_LOG_SPAN), 2)


class HistogramView(QWidget):
    """Image histogram with draggable Low / High markers and the tone curve."""

    rangeEdited = Signal(int, int)

    _MARGIN = 8
    _GRAB_PX = 9

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(132)
        self.setMouseTracking(True)
        self._counts: np.ndarray | None = None
        self._data_min = 0
        self._data_max = MAX_16BIT_VALUE
        self._low = 0
        self._high = MAX_16BIT_VALUE
        self._gamma = 1.0
        self._inverted = True
        self._log_scale = False
        self._dragging: str | None = None
        theming.on_change(self.update)

    # ── data ──────────────────────────────────────────────────────────────

    def set_histogram(self, counts, data_min: int, data_max: int) -> None:
        self._counts = None if counts is None else np.asarray(counts, dtype=np.float64)
        self._data_min = int(data_min)
        self._data_max = max(int(data_max), int(data_min) + 1)
        self.update()

    def set_display(self, low: int, high: int, gamma: float, inverted: bool) -> None:
        self._low, self._high = int(low), int(high)
        self._gamma, self._inverted = float(gamma), bool(inverted)
        self.update()

    def set_log_scale(self, enabled: bool) -> None:
        self._log_scale = bool(enabled)
        self.update()

    # ── geometry ──────────────────────────────────────────────────────────

    def _view_range(self) -> tuple[float, float]:
        """Intensity range on the x axis: the data, widened to keep both markers visible."""
        lo = min(self._data_min, self._low)
        hi = max(self._data_max, self._high)
        return float(lo), float(max(hi, lo + 1))

    def _plot_rect(self) -> QRectF:
        m = self._MARGIN
        return QRectF(m, m, max(1, self.width() - 2 * m), max(1, self.height() - 2 * m))

    def _x_for(self, value: float) -> float:
        lo, hi = self._view_range()
        rect = self._plot_rect()
        return rect.left() + (value - lo) / (hi - lo) * rect.width()

    def _value_at(self, x: float) -> int:
        lo, hi = self._view_range()
        rect = self._plot_rect()
        fraction = (x - rect.left()) / rect.width()
        return int(round(lo + max(0.0, min(1.0, fraction)) * (hi - lo)))

    # ── painting ──────────────────────────────────────────────────────────

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self._plot_rect()
        painter.fillRect(self.rect(), theming.color("#FFFFFF"))
        painter.setPen(QPen(theming.color("#C9D3CE"), 1))
        painter.drawRect(rect)

        low_x = self._x_for(self._low)
        high_x = self._x_for(self._high)

        # Bars.
        if self._counts is not None and self._counts.size and self._counts.max() > 0:
            counts = np.log1p(self._counts) if self._log_scale else self._counts
            # A single huge background bin would flatten everything else, so
            # the linear view is scaled to the second-largest bin.
            ordered = np.sort(counts)
            peak = ordered[-1] if self._log_scale or ordered.size < 2 else max(
                ordered[-2], ordered[-1] * 0.05
            )
            peak = max(peak, 1e-9)
            bins = counts.size
            data_left = self._x_for(self._data_min)
            data_width = self._x_for(self._data_max) - data_left
            bar_width = data_width / bins
            path = QPainterPath()
            path.moveTo(data_left, rect.bottom())
            for index in range(bins):
                height = min(1.0, counts[index] / peak) * (rect.height() - 2)
                x = data_left + index * bar_width
                path.lineTo(x, rect.bottom() - height)
                path.lineTo(x + bar_width, rect.bottom() - height)
            path.lineTo(data_left + data_width, rect.bottom())
            path.closeSubpath()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theming.color("#5A6B63"))
            painter.drawPath(path)

        # Everything outside Low..High is clipped to pure white / black.
        shade = QColor(theming.color("#1C2622"))
        shade.setAlpha(34)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(shade)
        if low_x > rect.left():
            painter.drawRect(QRectF(rect.left(), rect.top(), low_x - rect.left(), rect.height()))
        if high_x < rect.right():
            painter.drawRect(QRectF(high_x, rect.top(), rect.right() - high_x, rect.height()))

        # Tone curve: how intensities between Low and High map to grey.
        accent = theming.color("#1F6B4A")
        curve = QPainterPath()
        steps = 48
        for step in range(steps + 1):
            t = step / steps
            out = t ** self._gamma
            point = QPointF(
                low_x + t * (high_x - low_x),
                rect.bottom() - out * rect.height(),
            )
            if step == 0:
                curve.moveTo(point)
            else:
                curve.lineTo(point)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(accent, 1.6))
        painter.setClipRect(rect)
        painter.drawPath(curve)

        # Markers.
        painter.setPen(QPen(accent, 2))
        for x in (low_x, high_x):
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        painter.setClipping(False)
        painter.setBrush(accent)
        painter.setPen(Qt.PenStyle.NoPen)
        for x in (low_x, high_x):
            if rect.left() - 1 <= x <= rect.right() + 1:
                handle = QPainterPath()
                handle.moveTo(x, rect.top() + 7)
                handle.lineTo(x - 5, rect.top() - 1)
                handle.lineTo(x + 5, rect.top() - 1)
                handle.closeSubpath()
                painter.drawPath(handle)
        painter.end()

    # ── dragging ──────────────────────────────────────────────────────────

    def _marker_at(self, x: float) -> str | None:
        low_gap = abs(x - self._x_for(self._low))
        high_gap = abs(x - self._x_for(self._high))
        if min(low_gap, high_gap) > self._GRAB_PX:
            return None
        return "low" if low_gap <= high_gap else "high"

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or not self.isEnabled():
            return
        x = event.position().x()
        marker = self._marker_at(x)
        if marker is None:
            # Clicking in the plot grabs the nearer marker and moves it there.
            marker = (
                "low"
                if abs(x - self._x_for(self._low)) <= abs(x - self._x_for(self._high))
                else "high"
            )
        self._dragging = marker
        self._drag_to(x)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        x = event.position().x()
        if self._dragging is not None:
            self._drag_to(x)
            return
        self.setCursor(
            Qt.CursorShape.SplitHCursor
            if self._marker_at(x) is not None and self.isEnabled()
            else Qt.CursorShape.ArrowCursor
        )

    def mouseReleaseEvent(self, _event) -> None:  # noqa: N802
        self._dragging = None

    def _drag_to(self, x: float) -> None:
        value = self._value_at(x)
        if self._dragging == "low":
            low, high = min(value, self._high - 1), self._high
        else:
            low, high = self._low, max(value, self._low + 1)
        if (low, high) != (self._low, self._high):
            self.rangeEdited.emit(low, high)


class ImageTransformDialog(QDialog):
    paramsChanged = Signal(object)
    autoScaleRequested = Signal()
    resetRequested = Signal()
    applyToAllRequested = Signal()
    highlightSaturatedChanged = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Image Transform")
        self.setMinimumWidth(460)

        self._updating = False
        self._language = LANG_EN
        self._params = ImageTransformParams()
        # Range of values present in the image; None until an image is shown.
        self._data_range: tuple[int, int] | None = None
        self._reference_window: tuple[int, int] | None = None
        self._saturated_fraction = 0.0

        # Light-palette colours; the theme translates them for dark mode.
        self.setStyleSheet(
            "QLabel { background: transparent; }"
            "QCheckBox { color: #1C2622; font-size: 12px; }"
            "QPushButton { background-color: #FFFFFF; border: 1px solid #C9D3CE; "
            "border-radius: 7px; color: #1C2622; padding: 5px 10px; font-weight: 600; }"
            "QPushButton:hover { background-color: #E3EFE8; }"
            "QPushButton:disabled { color: #9AA8A1; }"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self._histogram = HistogramView()
        self._histogram.rangeEdited.connect(self._on_histogram_range_edited)
        root.addWidget(self._histogram)

        self._info_label = QLabel("")
        self._info_label.setStyleSheet("color: #5A6B63; font-size: 11px;")
        root.addWidget(self._info_label)

        controls = QFrame()
        controls.setObjectName("image_transform_controls")
        controls.setStyleSheet(
            "QFrame#image_transform_controls { background-color: #F7F9F8; "
            "border: 1px solid #C9D3CE; border-radius: 8px; }"
            "QLabel { color: #3B4A43; font-size: 11px; font-weight: 600; background: transparent; }"
        )
        grid = QGridLayout(controls)
        grid.setContentsMargins(10, 10, 10, 10)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)

        # Without an image the unmodified range sits at the midpoint of both
        # controls: Low 0 in [-65535, 65535], High 65535 in [0, 131070].  Once
        # an image is shown each slider is centred on the value the image
        # opened with (see _slider_bounds); the number boxes accept the full
        # range.
        self._low_slider, self._low_spin = self._make_int_control(
            MIN_TONE_VALUE, MAX_16BIT_VALUE, 0
        )
        self._high_slider, self._high_spin = self._make_int_control(
            0, MAX_TONE_VALUE, MAX_16BIT_VALUE
        )
        self._gamma_slider = QSlider(Qt.Orientation.Horizontal)
        self._gamma_slider.setRange(-_GAMMA_SLIDER_STEPS, _GAMMA_SLIDER_STEPS)
        self._gamma_slider.setValue(0)
        self._gamma_spin = QDoubleSpinBox()
        self._gamma_spin.setRange(MIN_GAMMA, MAX_GAMMA)
        self._gamma_spin.setDecimals(2)
        self._gamma_spin.setSingleStep(0.05)
        self._gamma_spin.setValue(1.0)
        self._gamma_spin.setMaximumWidth(92)

        self._brightness_slider = QSlider(Qt.Orientation.Horizontal)
        self._contrast_slider = QSlider(Qt.Orientation.Horizontal)
        for slider in (self._brightness_slider, self._contrast_slider):
            slider.setRange(0, IMAGEJ_SLIDER_RANGE)
            slider.setValue(IMAGEJ_SLIDER_RANGE // 2)

        self._control_labels: list[tuple[QLabel, str]] = []
        self._add_control_row(grid, 0, "High", self._high_slider, self._high_spin)
        self._add_control_row(grid, 1, "Low", self._low_slider, self._low_spin)
        self._add_control_row(grid, 2, "Gamma", self._gamma_slider, self._gamma_spin)
        self._add_control_row(grid, 3, "Brightness", self._brightness_slider, None)
        self._add_control_row(grid, 4, "Contrast", self._contrast_slider, None)
        root.addWidget(controls)

        options = QGridLayout()
        options.setContentsMargins(0, 0, 0, 0)
        options.setHorizontalSpacing(14)
        self._invert_checkbox = QCheckBox("Invert display")
        self._invert_checkbox.setToolTip("Show bright signal as dark bands on a light background")
        self._saturated_checkbox = QCheckBox("Highlight saturated pixels")
        self._saturated_checkbox.setToolTip(
            "Show pixels at the top of the measurable range in red. "
            "Saturated bands cannot be quantified accurately."
        )
        self._saturated_checkbox.setChecked(True)
        self._log_checkbox = QCheckBox("Log histogram")
        options.addWidget(self._invert_checkbox, 0, 0)
        options.addWidget(self._saturated_checkbox, 0, 1)
        options.addWidget(self._log_checkbox, 0, 2)
        options.setColumnStretch(3, 1)
        root.addLayout(options)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        self._auto_btn = QPushButton("Auto Scale")
        self._auto_btn.setToolTip(
            "Same as Auto Scale in Image Lab: ignores the darkest and brightest 0.1% of pixels."
        )
        self._reset_btn = QPushButton("Reset")
        self._reset_btn.setToolTip("Back to how the image looked when it was opened")
        self._apply_all_btn = QPushButton("Apply to All")
        self._apply_all_btn.setToolTip("Use these settings for every open image")
        self._close_btn = QPushButton("Close")
        for btn in (self._auto_btn, self._reset_btn, self._apply_all_btn):
            buttons.addWidget(btn)
        buttons.addStretch(1)
        buttons.addWidget(self._close_btn)
        root.addLayout(buttons)

        self._low_slider.valueChanged.connect(lambda v: self._on_range_widget(low=v))
        self._low_spin.valueChanged.connect(lambda v: self._on_range_widget(low=v))
        self._high_slider.valueChanged.connect(lambda v: self._on_range_widget(high=v))
        self._high_spin.valueChanged.connect(lambda v: self._on_range_widget(high=v))
        self._gamma_slider.valueChanged.connect(
            lambda v: self._on_gamma_widget(_slider_to_gamma(v))
        )
        self._gamma_spin.valueChanged.connect(self._on_gamma_widget)
        self._brightness_slider.valueChanged.connect(self._on_brightness_slider)
        self._contrast_slider.valueChanged.connect(self._on_contrast_slider)
        self._invert_checkbox.toggled.connect(self._on_invert_toggled)
        self._saturated_checkbox.toggled.connect(self._on_saturated_toggled)
        self._log_checkbox.toggled.connect(self._histogram.set_log_scale)
        self._auto_btn.clicked.connect(self.autoScaleRequested.emit)
        self._reset_btn.clicked.connect(self.resetRequested.emit)
        self._apply_all_btn.clicked.connect(self.applyToAllRequested.emit)
        self._close_btn.clicked.connect(self.close)

        self._refresh_widgets()

    # ── language ──────────────────────────────────────────────────────────

    def set_language(self, language: str) -> None:
        self._language = language
        self.setWindowTitle(tr("Image Transform", language))
        for label, source in self._control_labels:
            label.setText(tr(source, language))
        for widget, text, tip in (
            (self._invert_checkbox, "Invert display",
             "Show bright signal as dark bands on a light background"),
            (self._saturated_checkbox, "Highlight saturated pixels",
             "Show pixels at the top of the measurable range in red. "
             "Saturated bands cannot be quantified accurately."),
            (self._log_checkbox, "Log histogram", ""),
            (self._auto_btn, "Auto Scale",
             "Same as Auto Scale in Image Lab: ignores the darkest and brightest 0.1% of pixels."),
            (self._reset_btn, "Reset", "Back to how the image looked when it was opened"),
            (self._apply_all_btn, "Apply to All", "Use these settings for every open image"),
            (self._close_btn, "Close", ""),
        ):
            widget.setText(tr(text, language))
            widget.setToolTip(tr(tip, language) if tip else "")
        self._refresh_info_label()

    # ── construction helpers ──────────────────────────────────────────────

    @staticmethod
    def _make_int_control(minimum: int, maximum: int, value: int) -> tuple[QSlider, QSpinBox]:
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setValue(value)
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setMaximumWidth(92)
        return slider, spin

    def _add_control_row(self, grid: QGridLayout, row: int, label: str, slider, spin) -> None:
        label_widget = QLabel(label)
        self._control_labels.append((label_widget, label))
        grid.addWidget(label_widget, row, 0)
        if spin is None:
            grid.addWidget(slider, row, 1, 1, 2)
        else:
            grid.addWidget(slider, row, 1)
            grid.addWidget(spin, row, 2)

    # ── public state ──────────────────────────────────────────────────────

    def set_controls_enabled(self, enabled: bool) -> None:
        for widget in (
            self._histogram,
            self._low_slider,
            self._low_spin,
            self._high_slider,
            self._high_spin,
            self._gamma_slider,
            self._gamma_spin,
            self._brightness_slider,
            self._contrast_slider,
            self._invert_checkbox,
            self._saturated_checkbox,
            self._log_checkbox,
            self._auto_btn,
            self._reset_btn,
            self._apply_all_btn,
        ):
            widget.setEnabled(enabled)

    def set_image_statistics(self, statistics: dict | None) -> None:
        """Show the histogram and value range of the selected image."""
        if not statistics:
            self._data_range = None
            self._saturated_fraction = 0.0
            self._histogram.set_histogram(None, 0, MAX_16BIT_VALUE)
        else:
            data_min = int(statistics["min"])
            data_max = max(int(statistics["max"]), data_min + 1)
            self._data_range = (data_min, data_max)
            self._saturated_fraction = float(statistics.get("saturated", 0.0))
            self._histogram.set_histogram(statistics["histogram"], data_min, data_max)
        self._refresh_widgets()
        self._refresh_info_label()

    def set_highlight_saturated(self, enabled: bool) -> None:
        self._updating = True
        try:
            self._saturated_checkbox.setChecked(bool(enabled))
        finally:
            self._updating = False

    def set_params(self, params: ImageTransformParams) -> None:
        self._params = params.sanitized()
        self._refresh_widgets()

    def params(self) -> ImageTransformParams:
        return self._params

    # ── widget refresh ────────────────────────────────────────────────────

    def set_reference_window(self, low: int | None, high: int | None = None) -> None:
        """Set the Low / High the image opened with; the sliders centre on them."""
        self._reference_window = (
            None if low is None or high is None else (int(low), int(high))
        )
        self._refresh_widgets()

    def _slider_bounds(self) -> tuple[tuple[int, int], tuple[int, int]]:
        """Slider ranges for Low and High.

        Each slider is centred on the value the image opened with and reaches
        one display-window width to either side, so both can always be moved
        left and right.  A value set further out (typed, dragged on the
        histogram, or from an auto button) stretches the range to include it.
        """
        if self._data_range is None or self._reference_window is None:
            return (MIN_TONE_VALUE, MAX_16BIT_VALUE), (0, MAX_TONE_VALUE)
        ref_low, ref_high = self._reference_window
        reach = max(256, ref_high - ref_low)
        p = self._params
        low_bounds = (
            max(MIN_TONE_VALUE, min(ref_low - reach, p.low)),
            min(MAX_16BIT_VALUE, max(ref_low + reach, p.low)),
        )
        high_bounds = (
            max(0, min(ref_high - reach, p.high)),
            min(MAX_TONE_VALUE, max(ref_high + reach, p.high)),
        )
        return low_bounds, high_bounds

    def _default_window(self) -> tuple[int, int]:
        """The range ImageJ's Brightness / Contrast sliders are measured against."""
        return self._data_range if self._data_range is not None else (0, MAX_16BIT_VALUE)

    def _refresh_widgets(self) -> None:
        p = self._params
        self._updating = True
        try:
            low_bounds, high_bounds = self._slider_bounds()
            self._low_slider.setRange(*low_bounds)
            self._high_slider.setRange(*high_bounds)
            # A slider simply rests at its end when the value is outside the
            # image's own range; the number box always shows the real value.
            self._low_slider.setValue(max(low_bounds[0], min(low_bounds[1], p.low)))
            self._high_slider.setValue(max(high_bounds[0], min(high_bounds[1], p.high)))
            self._low_spin.setValue(p.low)
            self._high_spin.setValue(p.high)
            self._gamma_slider.setValue(_gamma_to_slider(p.gamma))
            self._gamma_spin.setValue(round(p.gamma, 2))
            brightness, contrast = imagej_slider_positions(
                p.low, p.high, *self._default_window()
            )
            self._brightness_slider.setValue(brightness)
            self._contrast_slider.setValue(contrast)
            self._invert_checkbox.setChecked(p.inverted)
        finally:
            self._updating = False
        self._histogram.set_display(p.low, p.high, p.gamma, p.inverted)

    def _refresh_info_label(self) -> None:
        if self._data_range is None:
            self._info_label.setText("")
            return
        text = tr(
            "Values in image: {low} to {high}",
            self._language,
            low=self._data_range[0],
            high=self._data_range[1],
        )
        if self._saturated_fraction > 0:
            percent = self._saturated_fraction * 100.0
            shown = f"{percent:.2f}" if percent < 1 else f"{percent:.1f}"
            text += "    " + tr("Saturated: {percent}%", self._language, percent=shown)
        self._info_label.setText(text)

    # ── edits ─────────────────────────────────────────────────────────────

    def _commit(self, params: ImageTransformParams) -> None:
        params = params.sanitized()
        changed = params != self._params
        self._params = params
        self._refresh_widgets()
        if changed:
            self.paramsChanged.emit(params)

    def _replace(self, **changes) -> ImageTransformParams:
        p = self._params
        return ImageTransformParams(
            low=changes.get("low", p.low),
            high=changes.get("high", p.high),
            gamma=changes.get("gamma", p.gamma),
            inverted=changes.get("inverted", p.inverted),
        )

    def _on_range_widget(self, low: int | None = None, high: int | None = None) -> None:
        if self._updating:
            return
        p = self._params
        if low is not None:
            # Moving one end never drags the other along.
            self._commit(self._replace(low=min(int(low), p.high - 1)))
        elif high is not None:
            self._commit(self._replace(high=max(int(high), p.low + 1)))

    def _on_histogram_range_edited(self, low: int, high: int) -> None:
        self._commit(self._replace(low=low, high=high))

    def _on_gamma_widget(self, gamma: float) -> None:
        if self._updating:
            return
        self._commit(self._replace(gamma=float(gamma)))

    def _on_brightness_slider(self, value: int) -> None:
        if self._updating:
            return
        low, high = imagej_adjust_brightness(
            self._params.low, self._params.high, value, *self._default_window()
        )
        self._commit_window(low, high, keep=self._brightness_slider)

    def _on_contrast_slider(self, value: int) -> None:
        if self._updating:
            return
        low, high = imagej_adjust_contrast(
            self._params.low, self._params.high, value, *self._default_window()
        )
        self._commit_window(low, high, keep=self._contrast_slider)

    def _commit_window(self, low: int, high: int, *, keep: QSlider) -> None:
        """Apply a window from the ImageJ sliders without the slider jumping back."""
        position = keep.value()
        self._commit(self._replace(low=low, high=high))
        self._updating = True
        try:
            keep.setValue(position)
        finally:
            self._updating = False

    def _on_invert_toggled(self, checked: bool) -> None:
        if self._updating:
            return
        self._commit(self._replace(inverted=bool(checked)))

    def _on_saturated_toggled(self, checked: bool) -> None:
        if self._updating:
            return
        self.highlightSaturatedChanged.emit(bool(checked))
