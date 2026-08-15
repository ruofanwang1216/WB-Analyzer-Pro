"""Right-hand parameter panel — persistence info and lane analysis settings."""
from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Signal, Qt, QSize, QSizeF
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QGroupBox,
    QSpinBox, QAbstractSpinBox, QPushButton,
    QHBoxLayout, QLabel, QSizePolicy,
    QListWidget, QListWidgetItem, QInputDialog, QToolButton,
)
from utils.i18n import LANG_EN, tr

_ACTIVE_BTN = """
    QPushButton {
        background-color: #8AB4A0;
        border: 1px solid #6A9E88;
        border-radius: 6px;
        color: #F5F8FA;
        font-weight: bold;
        padding: 4px 16px;
    }
"""
_INACTIVE_BTN = """
    QPushButton {
        background-color: #F5F8FA;
        border: 1px solid #C8D8E4;
        border-radius: 6px;
        color: #6E8494;
        padding: 4px 16px;
    }
"""

_FIXED_ROI_NAME_ROLE = int(Qt.ItemDataRole.UserRole) + 1


class ParamPanel(QWidget):
    """Sidebar for analysis parameters and lane settings."""

    params_changed = Signal()           # emitted when parameters change
    status_message = Signal(str)       # emitted for status bar warnings
    detect_requested = Signal()        # kept for toolbar compatibility
    custom_rotate_requested = Signal()
    rotate_requested = Signal()
    cancel_rotate_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedWidth(150)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self._mode = "manual"
        self._fixed_roi_requested: Callable[[], dict[str, Any] | None] | None = None
        self._fixed_roi_cancel_requested: Callable[[], None] | None = None
        self._fixed_roi_size_selected: Callable[[dict[str, Any]], None] | None = None
        self._fixed_roi_profiles: list[dict[str, Any]] = []
        self._language = LANG_EN
        self._build_ui()

    # ── Build ──────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.setStyleSheet("""
            QWidget {
                background-color: #EBF1F6;
            }
            QLabel {
                color: #6E8494;
                font-size: 9px;
            }
            QGroupBox {
                color: #A0B4C0;
                font-size: 9px;
                font-weight: bold;
                letter-spacing: 1px;
                border: 1px solid #D8E6EE;
                border-radius: 6px;
                margin-top: 8px;
                padding-top: 8px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 8px;
            }
            QLineEdit, QSpinBox, QComboBox {
                background-color: #F5F8FA;
                border: 1px solid #C8D8E4;
                border-radius: 5px;
                padding: 3px 4px;
                color: #35393D;
                font-size: 10px;
            }
            QSpinBox::up-button, QSpinBox::down-button {
                border: none;
                background: transparent;
                color: #8AB4A0;
            }
            QComboBox::drop-down {
                border: none;
                background: transparent;
            }
            QComboBox::down-arrow {
                image: none;
                width: 0;
            }
            QPushButton {
                background-color: #8AB4A0;
                border: 1px solid #6A9E88;
                border-radius: 6px;
                color: #F5F8FA;
                padding: 4px 5px;
                font-size: 9px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #6A9E88;
            }
            QPushButton#lane_dec, QPushButton#lane_inc {
                background-color: #F5F8FA;
                border: 1px solid #C8D8E4;
                border-radius: 5px;
                color: #35393D;
                font-size: 14px;
                font-weight: 400;
                padding: 0px;
            }
            QPushButton#lane_dec:hover, QPushButton#lane_inc:hover {
                background-color: #D4EDE4;
                border-color: #8AB4A0;
                color: #2A5E48;
            }
            QPushButton#lane_dec:pressed, QPushButton#lane_inc:pressed {
                background-color: #8AB4A0;
                color: #F5F8FA;
            }
            QSlider::groove:horizontal {
                height: 4px;
                background: #C8D8E4;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #8AB4A0;
                border: 1px solid #6A9E88;
                width: 14px;
                height: 14px;
                margin: -5px 0;
                border-radius: 7px;
            }
            QSlider::sub-page:horizontal {
                background: #8AB4A0;
                border-radius: 2px;
            }
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(10)

        # ── ROI Settings ────────────────────────────────────────────────────────
        self._roi_group = QGroupBox("ROI Settings")
        roi_layout = QVBoxLayout(self._roi_group)
        roi_layout.setContentsMargins(8, 4, 8, 8)
        roi_layout.setSpacing(8)

        # ── Manual ROI controls ────────────────────────────────────────────────
        self._manual_widget = QWidget()
        manual_layout = QVBoxLayout(self._manual_widget)
        manual_layout.setContentsMargins(0, 0, 0, 0)
        manual_layout.setSpacing(6)

        # Lane Settings sub-section
        self._lane_section_label = QLabel("Lane Settings")
        lane_section_label = self._lane_section_label
        lane_section_label.setStyleSheet(
            "color: #A0B4C0; font-size: 9px; font-weight: bold; letter-spacing: 0.5px;"
        )
        manual_layout.addWidget(lane_section_label)

        lane_form = QFormLayout()
        lane_form.setSpacing(6)

        self.lane_count = QSpinBox()
        self.lane_count.setRange(1, 24)
        self.lane_count.setValue(3)
        self.lane_count.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.lane_count.valueChanged.connect(self.params_changed)

        lane_dec = QPushButton("-")
        lane_dec.setObjectName("lane_dec")
        lane_dec.setFixedSize(28, 28)
        lane_dec.clicked.connect(lambda: self.lane_count.setValue(self.lane_count.value() - 1))

        lane_inc = QPushButton("+")
        lane_inc.setObjectName("lane_inc")
        lane_inc.setFixedSize(28, 28)
        lane_inc.clicked.connect(lambda: self.lane_count.setValue(self.lane_count.value() + 1))

        lane_row = QHBoxLayout()
        lane_row.setSpacing(4)
        lane_row.addWidget(self.lane_count)
        lane_row.addWidget(lane_dec)
        lane_row.addWidget(lane_inc)
        self._lanes_label = QLabel("Lanes:")
        lane_form.addRow(self._lanes_label)
        lane_form.addRow(lane_row)

        manual_layout.addLayout(lane_form)

        self._fixed_label = QLabel("Fixed ROI")
        fixed_label = self._fixed_label
        fixed_label.setStyleSheet(
            "color: #A0B4C0; font-size: 9px; font-weight: bold; letter-spacing: 0.5px;"
        )
        manual_layout.addWidget(fixed_label)

        fixed_row = QVBoxLayout()
        fixed_row.setContentsMargins(0, 0, 0, 0)
        fixed_row.setSpacing(6)

        self._fix_btn = QPushButton("Fix ROI")
        fix_btn = self._fix_btn
        fix_btn.setToolTip("Capture the current lane ROI size, or arm the next drawn ROI as the fixed size.")
        fix_btn.clicked.connect(self._on_add_fixed_roi_clicked)
        fixed_row.addWidget(fix_btn)

        self._cancel_fixed_btn = QPushButton("Cancel fixed ROI")
        cancel_btn = self._cancel_fixed_btn
        cancel_btn.setToolTip("Return Manual mode to freehand ROI drawing.")
        cancel_btn.clicked.connect(self._on_cancel_fixed_roi_clicked)
        fixed_row.addWidget(cancel_btn)
        manual_layout.addLayout(fixed_row)

        self._fixed_roi_list = QListWidget()
        self._fixed_roi_list.setMaximumHeight(58)
        self._fixed_roi_list.setStyleSheet(
            "QListWidget { background:#FFFFFF; border:1px solid #CBD9D1; border-radius:6px; font-size:10px; }"
            "QListWidget::item { padding:1px 3px; }"
            "QListWidget::item:selected { background:#C9DED2; color:#1E3D2F; }"
        )
        self._fixed_roi_list.itemClicked.connect(self._on_fixed_roi_item_selected)
        self._fixed_roi_list.itemDoubleClicked.connect(self._on_fixed_roi_item_double_clicked)
        manual_layout.addWidget(self._fixed_roi_list)

        roi_layout.addWidget(self._manual_widget)

        root.addWidget(self._roi_group)
        root.addStretch()

        # ── Help note ──────────────────────────────────────────────────────────
        self._help_note = QLabel(
            "Manual: draw lane ROI → draw band ROI → Analyze\n"
            "Hold Space + drag to pan. Scroll to zoom."
        )
        self._help_note.setStyleSheet("color: #6E8494; font-size: 9px;")
        self._help_note.setWordWrap(True)
        root.addWidget(self._help_note)

    # ── Validation ─────────────────────────────────────────────────────────────

    def set_language(self, language: str) -> None:
        """Refresh user-facing WB terminology without changing analysis state."""
        self._language = language
        self._roi_group.setTitle(tr("ROI Settings", language))
        self._lane_section_label.setText(tr("Lane Settings", language))
        self._lanes_label.setText(tr("Lanes:", language))
        self._fixed_label.setText(tr("Fixed ROI", language))
        self._fix_btn.setText(tr("Fix ROI", language))
        self._cancel_fixed_btn.setText(tr("Cancel fixed ROI", language))
        self._fix_btn.setToolTip(tr("Capture the current lane ROI size, or arm the next drawn ROI as the fixed size.", language))
        self._cancel_fixed_btn.setToolTip(tr("Return Manual mode to freehand ROI drawing.", language))
        self._help_note.setText(tr(
            "Manual: draw lane ROI → draw band ROI → Analyze\nHold Space + drag to pan. Scroll to zoom.",
            language,
        ))

    # ── Public API ─────────────────────────────────────────────────────────────

    def get_mode(self) -> str:
        """Densitometry uses the manual ROI workflow only."""
        return "manual"

    def set_mode(self, mode: str) -> None:
        # Ignore legacy saved "auto" preferences.
        self._mode = "manual"

    def set_fixed_roi_request_handler(self, handler: Callable[[], dict[str, Any] | None]) -> None:
        self._fixed_roi_requested = handler

    def set_fixed_roi_cancel_handler(self, handler: Callable[[], None]) -> None:
        self._fixed_roi_cancel_requested = handler

    def set_fixed_roi_size_selected_handler(self, handler: Callable[[dict[str, Any]], None]) -> None:
        self._fixed_roi_size_selected = handler

    def _on_add_fixed_roi_clicked(self) -> None:
        if self._fixed_roi_requested is None:
            return
        profile = self._fixed_roi_requested()
        if not profile:
            return
        kind = str(profile.get("kind", "lane"))
        if kind not in {"lane", "lane_band"}:
            return
        name = self._next_fixed_roi_name(kind)
        profile = dict(profile)
        profile["name"] = name
        self._fixed_roi_profiles.append(profile)
        item = QListWidgetItem(name)
        item.setSizeHint(QSize(0, 24))
        item.setData(Qt.ItemDataRole.UserRole, profile)
        item.setData(_FIXED_ROI_NAME_ROLE, name)
        lane_size = profile.get("lane_size")
        if isinstance(lane_size, QSizeF):
            item.setToolTip(f"{lane_size.width():.0f} x {lane_size.height():.0f} px")
        self._fixed_roi_list.addItem(item)
        self._install_fixed_roi_item_widget(item)
        self._fixed_roi_list.setCurrentItem(item)
        self._on_fixed_roi_item_selected(item)

    def _on_cancel_fixed_roi_clicked(self) -> None:
        self._fixed_roi_list.clearSelection()
        if self._fixed_roi_cancel_requested is not None:
            self._fixed_roi_cancel_requested()

    def _on_fixed_roi_item_selected(self, item: QListWidgetItem) -> None:
        profile = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(profile, dict):
            row = self._fixed_roi_list.row(item)
            if 0 <= row < len(self._fixed_roi_profiles):
                profile = self._fixed_roi_profiles[row]
        if isinstance(profile, dict) and self._fixed_roi_size_selected is not None:
            self._fixed_roi_size_selected(dict(profile))

    def _on_fixed_roi_item_double_clicked(self, item: QListWidgetItem) -> None:
        old_name = str(item.data(_FIXED_ROI_NAME_ROLE) or item.text())
        new_name, ok = QInputDialog.getText(self, "Rename Fixed ROI", "Name:", text=old_name)
        if not ok:
            return
        new_name = new_name.strip() or old_name
        row = self._fixed_roi_list.row(item)
        if 0 <= row < len(self._fixed_roi_profiles):
            self._fixed_roi_profiles[row]["name"] = new_name
        item.setText(new_name)
        item.setData(_FIXED_ROI_NAME_ROLE, new_name)
        profile = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(profile, dict):
            profile = dict(profile)
            profile["name"] = new_name
            item.setData(Qt.ItemDataRole.UserRole, profile)
        self._install_fixed_roi_item_widget(item)

    def _install_fixed_roi_item_widget(self, item: QListWidgetItem) -> None:
        row_widget = QWidget()
        layout = QHBoxLayout(row_widget)
        layout.setContentsMargins(5, 0, 2, 0)
        layout.setSpacing(3)
        label = QLabel(str(item.data(_FIXED_ROI_NAME_ROLE) or item.text()))
        label.setStyleSheet("font-size:10px; color:#2C4A3D;")
        layout.addWidget(label, 1)
        delete_btn = QToolButton()
        delete_btn.setText("x")
        delete_btn.setToolTip("Delete this fixed ROI size")
        delete_btn.setFixedSize(16, 16)
        delete_btn.setStyleSheet(
            "QToolButton { border:1px solid #9EB3A8; border-radius:8px; background:#F7FAF8; color:#6A7A72; font-size:10px; }"
            "QToolButton:hover { background:#F0D7D7; color:#8A3B3B; border-color:#C99595; }"
        )
        delete_btn.clicked.connect(lambda _checked=False, it=item: self._delete_fixed_roi_item(it))
        layout.addWidget(delete_btn)
        self._fixed_roi_list.setItemWidget(item, row_widget)

    def _delete_fixed_roi_item(self, item: QListWidgetItem) -> None:
        row = self._fixed_roi_list.row(item)
        if row < 0:
            return
        was_current = item is self._fixed_roi_list.currentItem()
        if 0 <= row < len(self._fixed_roi_profiles):
            self._fixed_roi_profiles.pop(row)
        self._fixed_roi_list.takeItem(row)
        if was_current and self._fixed_roi_cancel_requested is not None:
            self._fixed_roi_cancel_requested()

    def _next_fixed_roi_name(self, kind: str) -> str:
        if kind == "lane_band":
            count = sum(1 for profile in self._fixed_roi_profiles if profile.get("kind") == "lane_band") + 1
            return f"Fixed lane & band ROI {count}"
        count = sum(1 for profile in self._fixed_roi_profiles if profile.get("kind") == "lane") + 1
        return f"Fixed Lane ROI {count}"

    def set_wb_plot_simplified(self, enabled: bool) -> None:
        """Hide densitometry-only controls while WB Plot mode is active."""
        self._roi_group.setVisible(not enabled)
        self._help_note.setVisible(not enabled)

    def set_detect_enabled(self, enabled: bool) -> None:
        """Manual panel no longer has a Detect Bands button."""
        return

    def set_auto_detect_enabled(self, enabled: bool) -> None:
        return

    def set_auto_edit_enabled(self, enabled: bool) -> None:
        return

    def set_auto_edit_mode(self, enabled: bool) -> None:
        return

    def get_params(self) -> dict:
        return {
            "mode": "manual",
            "lane_count": self.lane_count.value(),
            "polarity": "Light on Dark",
            "bands_per_lane": 1,
            "target_band": 1,
            "sensitivity": 0.5,
        }

    def get_lane_count(self) -> int:
        return self.lane_count.value()

    def set_rotation_controls_enabled(self, enabled: bool) -> None:
        # Rotation is controlled from each image panel's own menu.
        return

    def set_rotation_mode_active(self, active: bool) -> None:
        return

    def set_rotation_angle(self, angle_deg: float) -> None:
        return
