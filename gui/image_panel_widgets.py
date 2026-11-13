"""Image viewer panel and uploaded-file row widgets."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from config.settings import COLOR_ACCENT_LIGHT, COLOR_ACCENT_TEXT

# Clear without being loud: a mid green between the accent and its tint.
SELECTED_PANEL_BORDER = "#4FA683"
from gui.icons import _image_operation_icon, _zoom_icon
from gui.image_canvas import ImageCanvas


class _PanelSelectionCheckBox(QCheckBox):
    """Holds which image is selected; the panel frame shows it, not a tick.

    The box itself stays hidden.  It remains the single source of truth so
    the window's selection logic is unchanged, and it tells its panel to
    restyle whenever the state is set, including with signals blocked.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.on_state_set = None

    def setChecked(self, checked: bool) -> None:  # noqa: N802 - Qt API
        super().setChecked(checked)
        if self.on_state_set is not None:
            self.on_state_set(bool(checked))

    def nextCheckState(self) -> None:  # noqa: N802 - Qt API
        super().nextCheckState()
        if self.on_state_set is not None:
            self.on_state_set(self.isChecked())


class _ImagePanelWidget(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("image_panel")
        self.setMinimumSize(180, 150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet(f"""
            QFrame#image_panel {{
                background-color: #FFFFFF;
                border: 2px solid transparent;
                border-radius: 8px;
            }}
            QFrame#image_panel[selected="true"] {{
                border: 2px solid {SELECTED_PANEL_BORDER};
            }}
            QCheckBox#panel_select_checkbox {{
                background: transparent;
                min-width: 22px;
                max-width: 22px;
                min-height: 22px;
                max-height: 22px;
                margin: 0px 2px 0px 0px;
                padding: 0px;
            }}
            QCheckBox#panel_select_checkbox::indicator {{
                width: 16px;
                height: 16px;
                margin-left: 2px;
                border-radius: 4px;
                border: 1px solid #9AA8A1;
                background: #FFFFFF;
            }}
            QCheckBox#panel_select_checkbox::indicator:checked {{
                border: 1px solid #1F6B4A;
                background: {COLOR_ACCENT_LIGHT};
            }}
            QLabel#panel_filename_label {{
                background: transparent;
                color: #4A5A53;
                font-size: 12px;
            }}
            QPushButton#panel_remove_btn {{
                background-color: #FFFFFF;
                border: 1px solid #C9D3CE;
                border-radius: 7px;
                color: #738694;
                min-width: 22px;
                max-width: 22px;
                min-height: 22px;
                max-height: 22px;
                padding: 0px;
                font-size: 12px;
            }}
            QPushButton#panel_remove_btn:hover {{
                color: #C0504A;
                border-color: #B8908C;
            }}
            QPushButton#panel_transform_btn {{
                background-color: #FFFFFF;
                border: 1px solid #C9D3CE;
                border-radius: 7px;
                color: #3B4A43;
                min-width: 30px;
                max-width: 30px;
                min-height: 26px;
                max-height: 26px;
                padding: 0px;
                font-size: 15px;
                font-weight: 700;
            }}
            QPushButton#panel_transform_btn:hover {{
                background-color: {COLOR_ACCENT_LIGHT};
                border-color: #1F6B4A;
                color: {COLOR_ACCENT_TEXT};
            }}
            QToolButton#panel_rotate_btn {{
                background-color: #FFFFFF;
                border: 1px solid #C9D3CE;
                border-radius: 7px;
                color: #3B4A43;
                min-width: 30px;
                max-width: 30px;
                min-height: 26px;
                max-height: 26px;
                padding: 0px;
                font-size: 15px;
                font-weight: 700;
            }}
            QToolButton#panel_rotate_btn:hover {{
                background-color: {COLOR_ACCENT_LIGHT};
                border-color: #1F6B4A;
                color: {COLOR_ACCENT_TEXT};
            }}
            QToolButton#panel_rotate_btn::menu-indicator {{
                image: none;
                width: 0px;
            }}
            QToolButton#panel_zoom_btn {{
                background-color: #FFFFFF;
                border: 1px solid #C9D3CE;
                border-radius: 7px;
                min-width: 30px;
                max-width: 30px;
                min-height: 26px;
                max-height: 26px;
                padding: 0px;
            }}
            QToolButton#panel_zoom_btn:hover {{
                background-color: {COLOR_ACCENT_LIGHT};
                border-color: #1F6B4A;
            }}
            QToolButton#panel_zoom_btn:pressed {{
                background-color: #C5E2D7;
            }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)
        # Which image is in use is shown by the panel's frame colour.
        self.select_checkbox = _PanelSelectionCheckBox()
        self.select_checkbox.setObjectName("panel_select_checkbox")
        self.select_checkbox.setToolTip("Select this image")
        self.select_checkbox.setParent(self)
        self.select_checkbox.hide()
        self.select_checkbox.on_state_set = self._show_selected
        self.transform_btn = QPushButton("◐")
        self.transform_btn.setObjectName("panel_transform_btn")
        self.transform_btn.setToolTip("Image Transform: Low / High / Gamma")
        top_row.addWidget(self.transform_btn)
        self.rotate_btn = QToolButton()
        self.rotate_btn.setObjectName("panel_rotate_btn")
        self.rotate_btn.setText("↻")
        self.rotate_btn.setToolTip("Rotate Image")
        self.rotate_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.rotate_menu = QMenu(self.rotate_btn)
        self.rotate_menu.setToolTipsVisible(True)
        self.flip_horizontal_action = self.rotate_menu.addAction(
            _image_operation_icon("flip-horizontal"), "Horizontal"
        )
        self.flip_vertical_action = self.rotate_menu.addAction(
            _image_operation_icon("flip-vertical"), "Vertical"
        )
        self.rotate_menu.addSeparator()
        self.rotate_custom_action = self.rotate_menu.addAction(
            _image_operation_icon("custom-rotate"), "Custom"
        )
        self.rotate_custom_action.setToolTip("Press Enter/Return to apply")
        self.rotate_menu.addSeparator()
        self.undo_image_operation_action = self.rotate_menu.addAction(
            _image_operation_icon("undo"), "Undo"
        )
        self.rotate_btn.setMenu(self.rotate_menu)
        top_row.addWidget(self.rotate_btn)
        self.zoom_out_btn = QToolButton()
        self.zoom_out_btn.setObjectName("panel_zoom_btn")
        self.zoom_out_btn.setIcon(_zoom_icon(zoom_in=False))
        self.zoom_out_btn.setIconSize(QSize(20, 20))
        self.zoom_out_btn.setToolTip("Zoom out image")
        top_row.addWidget(self.zoom_out_btn)
        self.zoom_in_btn = QToolButton()
        self.zoom_in_btn.setObjectName("panel_zoom_btn")
        self.zoom_in_btn.setIcon(_zoom_icon(zoom_in=True))
        self.zoom_in_btn.setIconSize(QSize(20, 20))
        self.zoom_in_btn.setToolTip("Zoom in image")
        top_row.addWidget(self.zoom_in_btn)
        self.filename_label = QLabel("")
        self.filename_label.setObjectName("panel_filename_label")
        self.filename_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.filename_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top_row.addWidget(self.filename_label)
        self.remove_btn = QPushButton("ⓧ")
        self.remove_btn.setObjectName("panel_remove_btn")
        self.remove_btn.setToolTip("Remove image")
        top_row.addWidget(self.remove_btn)
        root.addLayout(top_row)

        self.canvas = ImageCanvas()
        self.canvas.setCursor(Qt.CursorShape.CrossCursor)
        root.addWidget(self.canvas, 1)

    def set_filename(self, name: str) -> None:
        self.filename_label.setText(name)
        self.filename_label.setToolTip(name)

    def is_shown_selected(self) -> bool:
        return self.property("selected") == "true"

    def _show_selected(self, selected: bool) -> None:
        """Frame the panel in green while it is the image being worked on."""
        value = "true" if selected else "false"
        if self.property("selected") == value:
            return
        self.setProperty("selected", value)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def mousePressEvent(self, event) -> None:
        # A click on the header (not only on the image) selects the panel.
        if event.button() == Qt.MouseButton.LeftButton:
            self.canvas.panel_interacted.emit()
        super().mousePressEvent(event)


class _UploadedFileListRow(QWidget):
    open_requested = Signal(str)
    remove_requested = Signal(str)

    def __init__(self, source_key: str, name: str, tooltip: str, parent=None) -> None:
        super().__init__(parent)
        self._source_key = source_key
        self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("""
            QWidget {
                background: transparent;
            }
            QPushButton#uploaded_file_remove_btn {
                background-color: #FFFFFF;
                border: 1px solid #AFC1CC;
                border-radius: 7px;
                color: #6D7F8B;
                min-width: 18px;
                max-width: 18px;
                min-height: 18px;
                max-height: 18px;
                padding: 0px;
                font-size: 11px;
                font-weight: 700;
            }
            QPushButton#uploaded_file_remove_btn:hover {
                background-color: #F4DDDD;
                border-color: #C78D8D;
                color: #9D3F3F;
            }
            QLabel#uploaded_file_name_lbl {
                color: #1C2622;
                font-size: 13px;
                background: transparent;
            }
        """)

        row = QHBoxLayout(self)
        row.setContentsMargins(4, 1, 4, 1)
        row.setSpacing(6)

        remove_btn = QPushButton("×")
        remove_btn.setObjectName("uploaded_file_remove_btn")
        remove_btn.setToolTip(f"Remove {name} from Uploaded Files")
        remove_btn.clicked.connect(lambda _=False: self.remove_requested.emit(self._source_key))
        row.addWidget(remove_btn)

        label = QLabel(name)
        label.setObjectName("uploaded_file_name_lbl")
        label.setToolTip(tooltip)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        row.addWidget(label, 1)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            self.open_requested.emit(self._source_key)
            event.accept()
            return
        super().mouseReleaseEvent(event)
