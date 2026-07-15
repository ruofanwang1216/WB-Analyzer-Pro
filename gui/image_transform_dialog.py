"""Modeless Image Transform controls for WB image preview display."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QCheckBox,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
)

from core.image_transform import ImageTransformParams, MAX_16BIT_VALUE
from utils.i18n import LANG_EN, tr


class ImageTransformDialog(QDialog):
    paramsChanged = Signal(object)
    autoScaleRequested = Signal()
    resetRequested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Image Transform")
        self.setMinimumWidth(420)

        self._updating = False
        self._language = LANG_EN

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self._note = QLabel("Analyze follows the current Low / High / Gamma. Invert remains preview-only.")
        self._note.setWordWrap(True)
        self._note.setStyleSheet("color: #526675; font-size: 11px;")
        root.addWidget(self._note)

        controls = QFrame()
        controls.setStyleSheet(
            "QFrame { background-color: #F4F8FA; border: 1px solid #CEDDE6; border-radius: 8px; }"
            "QLabel { color: #334D5C; font-size: 11px; font-weight: 600; }"
        )
        grid = QGridLayout(controls)
        grid.setContentsMargins(10, 10, 10, 10)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)

        self._low_slider, self._low_spin = self._make_int_control(0, MAX_16BIT_VALUE, 0)
        self._high_slider, self._high_spin = self._make_int_control(0, MAX_16BIT_VALUE, MAX_16BIT_VALUE)
        self._gamma_slider = QSlider(Qt.Orientation.Horizontal)
        self._gamma_slider.setRange(10, 400)
        self._gamma_slider.setValue(100)
        self._gamma_spin = QDoubleSpinBox()
        self._gamma_spin.setRange(0.1, 4.0)
        self._gamma_spin.setDecimals(2)
        self._gamma_spin.setSingleStep(0.05)
        self._gamma_spin.setValue(1.0)
        self._invert_checkbox = QCheckBox("Invert display")
        self._invert_checkbox.setToolTip("Show bright signal as dark bands on a light background")
        self._invert_checkbox.setStyleSheet("color: #334D5C; font-size: 11px; font-weight: 600;")

        self._control_labels = []
        self._add_control_row(grid, 0, "Low", self._low_slider, self._low_spin)
        self._add_control_row(grid, 1, "High", self._high_slider, self._high_spin)
        self._add_control_row(grid, 2, "Gamma", self._gamma_slider, self._gamma_spin)
        grid.addWidget(self._invert_checkbox, 3, 1, 1, 2)
        root.addWidget(controls)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        self._auto_btn = QPushButton("Auto Scale")
        self._reset_btn = QPushButton("Reset")
        self._close_btn = QPushButton("Close")
        for btn in (self._auto_btn, self._reset_btn, self._close_btn):
            btn.setStyleSheet(
                "QPushButton { background-color: #DCE9E2; border: 1px solid #B8CCC1; "
                "border-radius: 7px; color: #2F4B3F; padding: 5px 10px; font-weight: 600; }"
                "QPushButton:hover { background-color: #D1E2D9; }"
            )
        buttons.addWidget(self._auto_btn)
        buttons.addWidget(self._reset_btn)
        buttons.addStretch(1)
        buttons.addWidget(self._close_btn)
        root.addLayout(buttons)

        self._low_slider.valueChanged.connect(lambda value: self._sync_int_from_slider(self._low_spin, value))
        self._high_slider.valueChanged.connect(lambda value: self._sync_int_from_slider(self._high_spin, value))
        self._low_spin.valueChanged.connect(lambda value: self._sync_slider_from_int(self._low_slider, value))
        self._high_spin.valueChanged.connect(lambda value: self._sync_slider_from_int(self._high_slider, value))
        self._gamma_slider.valueChanged.connect(self._sync_gamma_from_slider)
        self._gamma_spin.valueChanged.connect(self._sync_gamma_from_spin)
        self._invert_checkbox.toggled.connect(lambda _checked: self._emit_params_changed())
        self._auto_btn.clicked.connect(self.autoScaleRequested.emit)
        self._reset_btn.clicked.connect(self.resetRequested.emit)
        self._close_btn.clicked.connect(self.close)

    def set_language(self, language: str) -> None:
        self._language = language
        self.setWindowTitle(tr("Image Transform", language))
        self._note.setText(tr("Analyze follows the current Low / High / Gamma. Invert remains preview-only.", language))
        self._invert_checkbox.setText(tr("Invert display", language))
        self._invert_checkbox.setToolTip(tr("Show bright signal as dark bands on a light background", language))
        for label, source in zip(self._control_labels, ("Low", "High", "Gamma")):
            label.setText(tr(source, language))
        self._auto_btn.setText(tr("Auto Scale", language))
        self._reset_btn.setText(tr("Reset", language))
        self._close_btn.setText(tr("Close", language))

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
        self._control_labels.append(label_widget)
        grid.addWidget(label_widget, row, 0)
        grid.addWidget(slider, row, 1)
        grid.addWidget(spin, row, 2)

    def set_controls_enabled(self, enabled: bool) -> None:
        for widget in (
            self._low_slider,
            self._low_spin,
            self._high_slider,
            self._high_spin,
            self._gamma_slider,
            self._gamma_spin,
            self._invert_checkbox,
            self._auto_btn,
            self._reset_btn,
        ):
            widget.setEnabled(enabled)

    def set_params(self, params: ImageTransformParams) -> None:
        p = params.sanitized()
        self._updating = True
        try:
            self._low_slider.setValue(p.low)
            self._low_spin.setValue(p.low)
            self._high_slider.setValue(p.high)
            self._high_spin.setValue(p.high)
            gamma_value = round(p.gamma, 2)
            self._gamma_slider.setValue(int(round(gamma_value * 100)))
            self._gamma_spin.setValue(gamma_value)
            self._invert_checkbox.setChecked(p.inverted)
        finally:
            self._updating = False

    def _current_params(self) -> ImageTransformParams:
        return ImageTransformParams(
            low=self._low_spin.value(),
            high=self._high_spin.value(),
            gamma=float(self._gamma_spin.value()),
            inverted=self._invert_checkbox.isChecked(),
        ).sanitized()

    def _emit_params_changed(self) -> None:
        if self._updating:
            return
        params = self._current_params()
        if (
            params.low != self._low_spin.value()
            or params.high != self._high_spin.value()
            or abs(params.gamma - float(self._gamma_spin.value())) > 1e-6
            or params.inverted != self._invert_checkbox.isChecked()
        ):
            self.set_params(params)
        self.paramsChanged.emit(params)

    def _sync_int_from_slider(self, spin: QSpinBox, value: int) -> None:
        if spin.value() != value:
            spin.setValue(value)
            return
        self._emit_params_changed()

    def _sync_slider_from_int(self, slider: QSlider, value: int) -> None:
        if slider.value() != value:
            slider.setValue(value)
            return
        self._emit_params_changed()

    def _sync_gamma_from_slider(self, value: int) -> None:
        gamma = max(0.1, min(4.0, value / 100.0))
        if abs(float(self._gamma_spin.value()) - gamma) > 1e-6:
            self._gamma_spin.setValue(gamma)
            return
        self._emit_params_changed()

    def _sync_gamma_from_spin(self, value: float) -> None:
        slider_value = int(round(float(value) * 100))
        if self._gamma_slider.value() != slider_value:
            self._gamma_slider.setValue(slider_value)
            return
        self._emit_params_changed()
