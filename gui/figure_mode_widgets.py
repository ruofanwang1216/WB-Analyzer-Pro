"""Small sidebar widgets used by the WB figure workspace."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gui.figure_canvas import FigureCanvas


class _TemplateNameLabel(QLabel):
    """Label that emits the template id when its text is double-clicked."""

    doubleClicked = Signal(str)

    def __init__(self, text: str, template_id: str, parent=None) -> None:
        super().__init__(text, parent)
        self._template_id = template_id

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.doubleClicked.emit(self._template_id)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class _FramePreviewWidget(QWidget):
    """Compact live preview used only by the Create Template dialog."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._panel_count = 1
        self._blot_count = 2
        self._lane_count = 4
        self.setMinimumHeight(180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet(
            "background:#FFFFFF; border:1px solid #B8C7DA; border-radius:6px;"
        )

    def set_structure(self, panels: int, blots: int, lanes: int) -> None:
        structure = (max(1, panels), max(1, blots), max(1, lanes))
        if structure == (self._panel_count, self._blot_count, self._lane_count):
            return
        self._panel_count, self._blot_count, self._lane_count = structure
        self.update()

    def structure(self) -> tuple[int, int, int]:
        return self._panel_count, self._blot_count, self._lane_count

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        area = QRectF(self.rect()).adjusted(10.0, 10.0, -10.0, -10.0)
        gap = 8.0
        label_gap = max(3.0, area.width() * 0.01)
        label_w = max(54.0, area.width() * 0.18)
        panels_width = max(
            20.0,
            area.width()
            - label_gap
            - label_w
            - gap * (self._panel_count - 1),
        )
        panel_w = max(8.0, panels_width / self._panel_count)
        panel_h = max(28.0, area.height())

        title_font = painter.font()
        title_font.setPointSizeF(7.0)
        title_font.setBold(True)

        for panel_index in range(self._panel_count):
            panel_rect = QRectF(
                area.left() + panel_index * (panel_w + gap),
                area.top(),
                panel_w,
                panel_h,
            )
            if self._panel_count > 1:
                painter.setFont(title_font)
                painter.setPen(QColor("#456455"))
                painter.drawText(
                    panel_rect.adjusted(2.0, 0.0, -2.0, 0.0),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                    f"Panel {panel_index + 1}",
                )
                content = panel_rect.adjusted(2.0, 13.0, -2.0, -2.0)
            else:
                content = panel_rect.adjusted(2.0, 2.0, -2.0, -2.0)

            blot_gap = 3.0
            blot_h = max(
                3.0,
                (content.height() - blot_gap * (self._blot_count - 1))
                / self._blot_count,
            )
            blot_w = max(6.0, content.width())

            for blot_index in range(self._blot_count):
                blot = QRectF(
                    content.left(),
                    content.top() + blot_index * (blot_h + blot_gap),
                    blot_w,
                    blot_h,
                )
                painter.setPen(QPen(QColor("#111111"), 1.1))
                painter.setBrush(QColor("#D7D7D7"))
                painter.drawRect(blot)

                # One simulated WB band per lane makes the selected lane count
                # immediately readable without adding artificial dividers.
                lane_w = blot.width() / self._lane_count
                band_h = max(1.2, min(blot.height() * 0.18, 5.0))
                for lane_index in range(self._lane_count):
                    center_x = blot.left() + lane_w * (lane_index + 0.5)
                    band_w = max(1.5, lane_w * 0.68)
                    center_y = blot.center().y() + (
                        ((lane_index + blot_index) % 3) - 1
                    ) * min(1.2, blot.height() * 0.04)
                    band = QRectF(
                        center_x - band_w / 2.0,
                        center_y - band_h / 2.0,
                        band_w,
                        band_h,
                    )
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(
                        QColor(30, 30, 30, (165, 210, 185, 225)[lane_index % 4])
                    )
                    painter.drawRoundedRect(band, band_h / 2.0, band_h / 2.0)

                if panel_index == self._panel_count - 1:
                    label_font = painter.font()
                    label_font.setPointSizeF(
                        max(5.0, min(9.0, blot.height() * 0.28))
                    )
                    label_font.setBold(True)
                    label_font.setItalic(True)
                    painter.setFont(label_font)
                    painter.setPen(QColor("#111111"))
                    label_rect = QRectF(
                        blot.right() + label_gap,
                        blot.top(),
                        label_w,
                        blot.height(),
                    )
                    painter.drawText(
                        label_rect,
                        Qt.AlignmentFlag.AlignLeft
                        | Qt.AlignmentFlag.AlignVCenter,
                        f"IB: Protein {blot_index + 1}",
                    )


class _CollapseGroup(QWidget):
    """A workflow section with a large clickable header and collapsible body."""

    def __init__(self, title: str, parent=None, *, step_number: int | None = None) -> None:
        super().__init__(parent)
        self._expanded = True
        self._step_number = step_number

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 4)
        outer.setSpacing(0)

        # Header row
        self._header = QFrame()
        self._header.setObjectName("workflowSectionHeader")
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header.setMinimumHeight(38)
        h_layout = QHBoxLayout(self._header)
        h_layout.setContentsMargins(8, 5, 6, 5)
        h_layout.setSpacing(5)

        self._step_badge: QLabel | None = None
        if step_number is not None:
            self._step_badge = QLabel(str(step_number))
            self._step_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._step_badge.setFixedSize(22, 22)
            self._step_badge.setStyleSheet(
                "QLabel { background:#2F8A64; color:#FFFFFF; border-radius:11px; "
                "font-weight:700; font-size:10px; }"
            )
            self._step_badge.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents,
                True,
            )
            h_layout.addWidget(self._step_badge)

        self._title_label = QLabel(title)
        self._title_label.setStyleSheet(
            "font-weight:600; color:#24352E; font-size:11px; background:transparent;"
        )
        self._title_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        h_layout.addWidget(self._title_label)
        h_layout.addStretch()

        self._toggle_btn = QToolButton()
        self._toggle_btn.setText("›")
        self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_btn.setFixedSize(24, 24)
        self._toggle_btn.setStyleSheet(
            "QToolButton { border:none; background:transparent; color:#31433B; "
            "font-size:19px; font-weight:500; }"
        )
        self._toggle_btn.clicked.connect(self._toggle)
        h_layout.addWidget(self._toggle_btn)
        outer.addWidget(self._header)

        # Body widget
        self._body = QWidget()
        self._body.setStyleSheet(
            "QWidget { background-color:#FFFFFF; }"
        )
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(8, 8, 8, 7)
        self._body_layout.setSpacing(6)
        outer.addWidget(self._body)

        # Make the complete header clickable, matching the supplied workflow
        # reference instead of requiring the user to hit the small chevron.
        self._header.mousePressEvent = self._on_header_pressed
        self.set_expanded(True)

    def body_layout(self) -> QVBoxLayout:
        return self._body_layout

    def title_text(self) -> str:
        return self._title_label.text()

    def _toggle(self) -> None:
        self.set_expanded(not self._expanded)

    def _on_header_pressed(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle()
            event.accept()

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        self._body.setVisible(self._expanded)
        self._toggle_btn.setText("⌄" if self._expanded else "›")
        self._refresh_header_style()

    def _refresh_header_style(self) -> None:
        border = "#C9DCD2" if self._expanded else "#D9E1DD"
        self._header.setStyleSheet(
            "QFrame#workflowSectionHeader {"
            f"background-color:#FFFFFF; border:1px solid {border}; "
            "border-radius:7px;"
            "}"
        )


class _InlineDisclosure(QWidget):
    """Compact in-place dropdown used by Auto Detect and Manual ROI modes."""

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self._expanded = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._header = QFrame()
        self._header.setObjectName("inlineDisclosureHeader")
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header.setFixedHeight(26)
        self._header.setStyleSheet(
            "QFrame#inlineDisclosureHeader { background:#FFFFFF; "
            "border:1px solid #D3DDD8; border-radius:6px; }"
        )
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(7, 1, 4, 1)
        header_layout.setSpacing(4)
        self._title_label = QLabel(title)
        self._title_label.setStyleSheet(
            "font-size:10px; font-weight:500; color:#2F3D36; background:transparent;"
        )
        self._title_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        header_layout.addWidget(self._title_label)
        header_layout.addStretch(1)
        self._button = QToolButton()
        self._button.setText("›")
        self._button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._button.setFixedSize(22, 22)
        self._button.setStyleSheet(
            "QToolButton { border:none; background:transparent; color:#31433B; "
            "font-size:17px; }"
        )
        self._button.clicked.connect(self._toggle)
        header_layout.addWidget(self._button)
        self._header.mousePressEvent = self._on_header_pressed
        outer.addWidget(self._header)

        self._body = QWidget()
        self._body.setObjectName("inlineDisclosureBody")
        self._body.setStyleSheet(
            "QWidget#inlineDisclosureBody { background:#F7FAF8; border-left:1px solid #D3DDD8; "
            "border-right:1px solid #D3DDD8; border-bottom:1px solid #D3DDD8; "
            "border-radius:0 0 6px 6px; }"
        )
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(5, 4, 5, 4)
        self._body_layout.setSpacing(3)
        outer.addWidget(self._body)
        self.set_expanded(False)

    def body_layout(self) -> QVBoxLayout:
        return self._body_layout

    def is_expanded(self) -> bool:
        return self._expanded

    def _toggle(self) -> None:
        self.set_expanded(not self._expanded)

    def _on_header_pressed(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle()
            event.accept()

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        self._body.setVisible(self._expanded)
        self._button.setText("⌄" if self._expanded else "›")


class _TemplatePreviewCanvas(FigureCanvas):
    """Read-only canvas with a fixed scale for saved-template previews."""

    def wheelEvent(self, event) -> None:
        # This is a browser preview, not a second editing canvas. Consuming
        # wheel and trackpad gestures keeps its prescribed scale fixed.
        event.accept()
