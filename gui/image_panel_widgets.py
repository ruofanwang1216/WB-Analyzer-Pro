"""Image viewer panel and uploaded-file row widgets."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen  # noqa: F401 - kept for callers
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
from gui import theming
from gui.icons import _image_operation_icon, _zoom_icon
from gui.image_canvas import ImageCanvas


AUTO_STRAIGHTEN_TOOLTIP = (
    "Auto-straighten: level tilted bands in one click (arrow: more rotate options)"
)


class _ElidedLabel(QLabel):
    """Label that shortens text that does not fit with "…" in the middle.

    A plain centred label cuts long file names off at both edges.  Here the
    start and the end of the name stay readable; the full name is still what
    ``text()`` returns and what the tooltip shows.
    """

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        hint = super().minimumSizeHint()
        return QSize(min(hint.width(), 40), hint.height())

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        rect = self.contentsRect()
        shown = self.fontMetrics().elidedText(
            self.text(), Qt.TextElideMode.ElideMiddle, max(0, rect.width())
        )
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(rect, int(self.alignment()), shown)
        painter.end()

    def shown_text(self) -> str:
        return self.fontMetrics().elidedText(
            self.text(), Qt.TextElideMode.ElideMiddle, max(0, self.contentsRect().width())
        )


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
            QToolButton#analyze_btn {{
                background-color: #1F6B4A;
                border: 1px solid #1F6B4A;
                border-radius: 7px;
                color: #FEFEFE;
                font-size: 12px;
                font-weight: 600;
                padding: 0px 14px;
                min-height: 22px;
                max-height: 22px;
            }}
            QToolButton#analyze_btn:hover {{
                background-color: #134A32;
                border-color: #134A32;
            }}
            QToolButton#analyze_btn:disabled {{
                background-color: #E9EEEB;
                color: #8A9891;
                border-color: #D9E0DC;
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
                padding: 0px 13px 0px 0px;
                font-size: 15px;
                font-weight: 700;
            }}
            QToolButton#panel_rotate_btn:hover {{
                background-color: {COLOR_ACCENT_LIGHT};
                border-color: #1F6B4A;
                color: {COLOR_ACCENT_TEXT};
            }}
            QToolButton#panel_rotate_btn::menu-button {{
                subcontrol-origin: border;
                subcontrol-position: center right;
                width: 13px;
                border: none;
                border-left: 1px solid #C9D3CE;
                border-top-right-radius: 7px;
                border-bottom-right-radius: 7px;
                background: transparent;
            }}
            QToolButton#panel_rotate_btn::menu-button:hover {{
                background-color: #C9D3CE;
            }}
            QToolButton#panel_rotate_btn::menu-arrow {{
                width: 7px;
                height: 7px;
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
            QToolButton#panel_fit_btn {{
                background-color: #FFFFFF;
                border: 1px solid #C9D3CE;
                border-radius: 7px;
                color: #3B4A43;
                min-height: 26px;
                max-height: 26px;
                padding: 0px 10px;
                font-size: 12px;
            }}
            QToolButton#panel_fit_btn:hover {{
                background-color: {COLOR_ACCENT_LIGHT};
                border-color: #1F6B4A;
                color: {COLOR_ACCENT_TEXT};
            }}
            QToolButton#panel_fit_btn:disabled {{
                color: #9AA8A1;
                border-color: #E4E9E6;
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
        self.rotate_btn.setToolTip(AUTO_STRAIGHTEN_TOOLTIP)
        # Split button: the main part straightens the blot in one click, the
        # narrow arrow opens the other rotate / flip / undo choices.
        self.rotate_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.rotate_menu = QMenu(self.rotate_btn)
        self.rotate_menu.setToolTipsVisible(True)
        self.auto_straighten_action = self.rotate_menu.addAction(
            _image_operation_icon("auto-straighten"), "Auto Straighten"
        )
        self.auto_straighten_action.setToolTip(
            "Detect how tilted the bands are and level them"
        )
        self.rotate_menu.addSeparator()
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
        theming.on_change(self._refresh_theme_icons)
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
        # One click brings the whole blot back, as large as the panel allows.
        self.fit_btn = QToolButton()
        self.fit_btn.setObjectName("panel_fit_btn")
        self.fit_btn.setText("Fit")
        self.fit_btn.setToolTip("Fit the whole image to this panel")
        top_row.addWidget(self.fit_btn)
        self.filename_label = _ElidedLabel("")
        self.filename_label.setObjectName("panel_filename_label")
        self.filename_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.filename_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top_row.addWidget(self.filename_label)
        # Analyze sits in the toolbar of the image it measures, so the blot
        # gets the full height of the panel.  The main window binds the action.
        self.analyze_btn = QToolButton()
        self.analyze_btn.setObjectName("analyze_btn")
        self.analyze_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.analyze_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.analyze_btn.pressed.connect(self.canvas_interacted)
        # Never squeezed below its text: with four images side by side the
        # layout used to shrink it to an empty pill.
        self.analyze_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.analyze_btn.hide()
        top_row.addWidget(self.analyze_btn)
        self.remove_btn = QPushButton("ⓧ")
        self.remove_btn.setObjectName("panel_remove_btn")
        self.remove_btn.setToolTip("Remove image")
        top_row.addWidget(self.remove_btn)
        root.addLayout(top_row)
        self._top_row = top_row
        self._root_layout = root
        self._name_on_own_line = False

        self.canvas = ImageCanvas()
        self.canvas.setCursor(Qt.CursorShape.CrossCursor)
        root.addWidget(self.canvas, 1)

    def set_filename(self, name: str) -> None:
        self.filename_label.setText(name)
        self.filename_label.setToolTip(name)

    # Room the file name needs beside the buttons before it moves to a line
    # of its own.
    _MIN_INLINE_NAME_WIDTH = 160

    def _buttons_width(self) -> int:
        """Width the one-line toolbar needs, without the file name."""
        row = self._top_row
        widths = [
            row.itemAt(i).widget().sizeHint().width()
            for i in range(row.count())
            if row.itemAt(i).widget() is not None
            and row.itemAt(i).widget() is not self.filename_label
            and row.itemAt(i).widget() is not self.analyze_btn
            and not row.itemAt(i).widget().isHidden()
        ]
        if not self.analyze_btn.isHidden():
            # Analyze shares the line with the buttons when there is room.
            widths.append(self.analyze_btn.sizeHint().width())
        margins = self._root_layout.contentsMargins()
        return (
            sum(widths)
            + row.spacing() * len(widths)
            + margins.left()
            + margins.right()
            + 4  # the 2px selection frame on each side
        )

    def _update_toolbar_layout(self) -> None:
        """Split the toolbar over two lines when the panel is narrow.

        With four images side by side each panel is only about 300px wide,
        not enough for the buttons, Analyze and the file name on one line;
        Analyze used to be squeezed into an empty pill.  In a narrow panel
        the image buttons keep the first line and the file name and Analyze
        move to a second line under it.
        """
        narrow = self.width() < self._buttons_width() + self._MIN_INLINE_NAME_WIDTH
        if narrow == self._name_on_own_line:
            return
        self._name_on_own_line = narrow
        label = self.filename_label
        top = self._top_row
        top.removeWidget(label)
        top.removeWidget(self.analyze_btn)
        if narrow:
            second = QHBoxLayout()
            second.setContentsMargins(0, 0, 0, 0)
            second.setSpacing(6)
            label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            second.addWidget(label, 1)
            second.addWidget(self.analyze_btn)
            # Keep the close button at the far right of the first line.
            top.insertStretch(top.indexOf(self.remove_btn), 1)
            self._root_layout.insertLayout(1, second)
            self._second_row = second
        else:
            second = getattr(self, "_second_row", None)
            if second is not None:
                second.removeWidget(label)
                second.removeWidget(self.analyze_btn)
                self._root_layout.removeItem(second)
                second.deleteLater()
                self._second_row = None
            stretch_index = top.indexOf(self.remove_btn) - 1
            item = top.itemAt(stretch_index)
            if item is not None and item.spacerItem() is not None:
                top.takeAt(stretch_index)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            index = top.indexOf(self.remove_btn)
            top.insertWidget(index, label)
            top.insertWidget(index + 1, self.analyze_btn)

    def name_on_own_line(self) -> bool:
        return self._name_on_own_line

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._update_toolbar_layout()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        self._update_toolbar_layout()

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


    def canvas_interacted(self) -> None:
        """Make this panel the active one (same as clicking its header)."""
        self.canvas.panel_interacted.emit()

    def _refresh_theme_icons(self) -> None:
        self.auto_straighten_action.setIcon(_image_operation_icon("auto-straighten"))
        self.flip_horizontal_action.setIcon(_image_operation_icon("flip-horizontal"))
        self.flip_vertical_action.setIcon(_image_operation_icon("flip-vertical"))
        self.rotate_custom_action.setIcon(_image_operation_icon("custom-rotate"))
        self.undo_image_operation_action.setIcon(_image_operation_icon("undo"))
        self.zoom_out_btn.setIcon(_zoom_icon(zoom_in=False))
        self.zoom_in_btn.setIcon(_zoom_icon(zoom_in=True))


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
                border-radius: 9px; /* exact */
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

        label = _ElidedLabel(name)
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
