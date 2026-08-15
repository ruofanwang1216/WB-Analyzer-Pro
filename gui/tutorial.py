"""Interactive, opt-in newcomer tutorials for the two WB workflows."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import (
    QObject,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    Qt,
    QTimer,
)
from PySide6.QtGui import QColor, QBrush, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from utils.i18n import tr


class TutorialModeDialog(QDialog):
    """Let the user choose which workflow they want to practise."""

    def __init__(self, parent=None, *, language: str = "en") -> None:
        super().__init__(parent)
        self._language = language
        self._selection: str | None = None
        self.setModal(True)
        self.setMinimumWidth(440)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(12)

        self._title = QLabel()
        self._title.setStyleSheet(
            "font-size:16px; font-weight:700; color:#214B39;"
        )
        root.addWidget(self._title)

        self._description = QLabel()
        self._description.setWordWrap(True)
        self._description.setStyleSheet("color:#61746B; font-size:11px;")
        root.addWidget(self._description)

        cards = QHBoxLayout()
        cards.setSpacing(12)
        self._densitometry_btn = QPushButton()
        self._wb_figure_btn = QPushButton()
        for button in (self._densitometry_btn, self._wb_figure_btn):
            button.setMinimumHeight(76)
            button.setStyleSheet(
                "QPushButton { background:#FFFFFF; border:1px solid #B9CEC3; "
                "border-radius:10px; color:#214B39; padding:10px; "
                "font-size:12px; font-weight:600; }"
                "QPushButton:hover { background:#EAF4EF; border-color:#5E9A7F; }"
            )
            cards.addWidget(button)
        root.addLayout(cards)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self._cancel_btn = QPushButton()
        self._cancel_btn.clicked.connect(self.reject)
        footer.addWidget(self._cancel_btn)
        root.addLayout(footer)

        self._densitometry_btn.clicked.connect(
            lambda: self._choose("densitometry")
        )
        self._wb_figure_btn.clicked.connect(lambda: self._choose("wb_figure"))
        self.set_language(language)

    def _choose(self, selection: str) -> None:
        self._selection = selection
        self.accept()

    @property
    def selection(self) -> str | None:
        return self._selection

    def set_language(self, language: str) -> None:
        self._language = language
        self.setWindowTitle(tr("New User Tutorial", language))
        self._title.setText(tr("Choose a tutorial", language))
        self._description.setText(
            tr(
                "Practise with two built-in WB images. Tutorial-only defaults do not change the normal workflows.",
                language,
            )
        )
        self._densitometry_btn.setText(
            tr("Densitometry\nROI and column-table workflow", language)
        )
        self._wb_figure_btn.setText(
            tr("WB Figure Generation\nFrame, condition and ROI workflow", language)
        )
        self._cancel_btn.setText(tr("Cancel", language))


class _TutorialPanel(QFrame):
    """Small non-modal instruction card that leaves the workspace clickable."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("tutorialPanel")
        self.setFixedWidth(370)
        self.setStyleSheet(
            "QFrame#tutorialPanel { background:#FFFFFF; border:2px solid #5E9A7F; "
            "border-radius:12px; }"
            "QLabel { border:none; background:transparent; }"
            "QPushButton { background:#F5F8F6; border:1px solid #B9CEC3; "
            "border-radius:6px; color:#29483B; padding:5px 10px; }"
            "QPushButton:hover { background:#E5F1EB; }"
            "QPushButton#tutorialNext { background:#315F4B; color:#FFFFFF; "
            "border-color:#315F4B; font-weight:600; }"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(7)
        self._progress = QLabel()
        self._progress.setStyleSheet("color:#6D8177; font-size:9px;")
        root.addWidget(self._progress)
        self._title = QLabel()
        self._title.setStyleSheet(
            "color:#214B39; font-size:13px; font-weight:700;"
        )
        root.addWidget(self._title)
        self._body = QLabel()
        self._body.setWordWrap(True)
        self._body.setStyleSheet("color:#3F5048; font-size:11px;")
        root.addWidget(self._body)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self._exit_btn = QPushButton()
        self._back_btn = QPushButton()
        self._next_btn = QPushButton()
        self._next_btn.setObjectName("tutorialNext")
        buttons.addWidget(self._exit_btn)
        buttons.addStretch(1)
        buttons.addWidget(self._back_btn)
        buttons.addWidget(self._next_btn)
        root.addLayout(buttons)
        self.hide()

    def set_content(
        self,
        *,
        progress: str,
        title: str,
        body: str,
        back_text: str,
        next_text: str,
        exit_text: str,
        can_go_back: bool,
    ) -> None:
        self._progress.setText(progress)
        self._title.setText(title)
        self._body.setText(body)
        self._back_btn.setText(back_text)
        self._next_btn.setText(next_text)
        self._exit_btn.setText(exit_text)
        self._back_btn.setEnabled(can_go_back)
        self.adjustSize()


class _TutorialHighlight(QWidget):
    """Mouse-transparent target outline with an optional pointing arrow."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._glow = QFrame(self)
        self._glow.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self._glow.setStyleSheet(
            "background:transparent; border:6px solid rgba(255, 176, 27, 125); "
            "border-radius:15px;"
        )
        self._glow_opacity = QGraphicsOpacityEffect(self._glow)
        self._glow.setGraphicsEffect(self._glow_opacity)
        self._pulse = QPropertyAnimation(self._glow_opacity, b"opacity", self)
        self._pulse.setDuration(1050)
        self._pulse.setKeyValueAt(0.0, 0.18)
        self._pulse.setKeyValueAt(0.5, 0.92)
        self._pulse.setKeyValueAt(1.0, 0.18)
        self._pulse.setLoopCount(-1)
        self._pulse.start()
        self._outline = QFrame(self)
        self._outline.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self._outline.setStyleSheet(
            "background:transparent; border:4px solid #E29A00; border-radius:10px;"
        )
        self._arrow_flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput
        )
        self._arrow = self._make_arrow(parent)
        self.hide()

    def _make_arrow(self, parent: QWidget) -> QLabel:
        arrow = QLabel("↖", parent, self._arrow_flags)
        arrow.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        arrow.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        arrow.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        arrow.setStyleSheet(
            "background:transparent; border:none; color:#D77F00; "
            "font-size:54px; font-weight:900;"
        )
        arrow_shadow = QGraphicsDropShadowEffect(arrow)
        arrow_shadow.setBlurRadius(20)
        arrow_shadow.setOffset(0, 0)
        arrow_shadow.setColor(QColor(255, 174, 0, 210))
        arrow.setGraphicsEffect(arrow_shadow)
        arrow.hide()
        return arrow

    def _ensure_arrow(self, parent: QWidget) -> None:
        if not isValid(self._arrow):
            self._arrow = self._make_arrow(parent)

    def set_target_geometry(self, rect, *, show_arrow: bool) -> None:
        glow_margin = 8
        parent = self.parentWidget()
        if parent is None:
            return
        self._ensure_arrow(parent)
        # Cover the whole containing window.  This keeps the effect above
        # application-modal dialogs and gives the arrow room near edge buttons.
        self.setGeometry(parent.rect())
        self._glow.setGeometry(rect.adjusted(-glow_margin, -glow_margin, glow_margin, glow_margin))
        self._outline.setGeometry(rect.adjusted(-4, -4, 4, 4))
        if not show_arrow:
            self._arrow.hide()
        else:
            arrow_size = 78
            if self._arrow.parentWidget() is not parent:
                self._arrow.setParent(parent, self._arrow_flags)
            global_top_left = parent.mapToGlobal(rect.topLeft())
            global_bottom_right = parent.mapToGlobal(rect.bottomRight())
            screen = QApplication.screenAt(global_bottom_right)
            screen_rect = (
                QApplication.primaryScreen().availableGeometry()
                if screen is None
                else screen.availableGeometry()
            )
            right_x = global_bottom_right.x() + 8
            bottom_y = global_bottom_right.y() + 8
            has_parent_room_on_right = (
                rect.right() + 8 + arrow_size <= parent.width()
            )
            has_screen_room = (
                right_x + arrow_size <= screen_rect.right()
                and bottom_y + arrow_size <= screen_rect.bottom()
            )
            if has_parent_room_on_right and has_screen_room:
                self._arrow.setText("↖")
                arrow_x = right_x
                arrow_y = bottom_y
            else:
                self._arrow.setText("→")
                arrow_x = global_top_left.x() - arrow_size - 8
                arrow_y = int(
                    global_top_left.y()
                    + (rect.height() - arrow_size) / 2.0
                )
            self._arrow.setGeometry(
                arrow_x,
                arrow_y,
                arrow_size,
                arrow_size,
            )
            self._arrow.show()
            self._arrow.raise_()

    def hideEvent(self, event) -> None:
        if isValid(self._arrow):
            self._arrow.hide()
        super().hideEvent(event)


class _TutorialEnterPrompt(QLabel):
    """Opaque, always-readable keyboard instruction bubble."""

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor("#E2A21A"), 2.0))
        painter.setBrush(QBrush(QColor(255, 247, 214, 250)))
        painter.drawRoundedRect(
            QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0),
            7.0,
            7.0,
        )
        painter.setPen(QColor("#5C4700"))
        painter.drawText(
            self.rect().adjusted(10, 5, -10, -5),
            Qt.AlignmentFlag.AlignCenter,
            self.text(),
        )


class _TutorialTableDragHint(QWidget):
    """Mouse-transparent simulation of dragging across table cells."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._target = QRectF()
        self._progress = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(32)
        self._timer.timeout.connect(self._advance)
        prompt_flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput
        )
        self._enter_prompt = _TutorialEnterPrompt("", parent, prompt_flags)
        self._enter_prompt.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground,
            True,
        )
        self._enter_prompt.setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground,
            True,
        )
        self._enter_prompt.setAttribute(
            Qt.WidgetAttribute.WA_ShowWithoutActivating,
            True,
        )
        self._enter_prompt.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        prompt_font = self._enter_prompt.font()
        prompt_font.setPointSize(11)
        prompt_font.setBold(True)
        self._enter_prompt.setFont(prompt_font)
        self._enter_prompt.hide()
        self.hide()

    def show_target(self, parent: QWidget, rect: QRect) -> None:
        if self.parentWidget() is not parent:
            self.hide()
            self.setParent(parent)
            self.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents,
                True,
            )
        self.setGeometry(parent.rect())
        self._target = QRectF(rect)
        self._progress = 0.0
        self._enter_prompt.hide()
        self._timer.start()
        self.show()
        self.raise_()
        self.update()

    def clear(self) -> None:
        self._timer.stop()
        self._target = QRectF()
        self._enter_prompt.hide()
        self.hide()

    def show_enter_prompt(self, text: str, visible: bool) -> None:
        if not visible or self._target.isEmpty():
            self._enter_prompt.hide()
            return
        parent = self.parentWidget()
        if parent is None:
            self._enter_prompt.hide()
            return
        prompt_height = max(
            32,
            self._enter_prompt.fontMetrics().height() + 14,
        )
        prompt_top_left = parent.mapToGlobal(
            QPoint(
                int(self._target.right() + 10),
                int(
                    self._target.center().y()
                    - prompt_height / 2.0
                ),
            )
        )
        self.show_prompt_at_global(text, prompt_top_left)

    def show_prompt_at_global(self, text: str, top_left: QPoint) -> None:
        self._enter_prompt.setText(text)
        metrics = self._enter_prompt.fontMetrics()
        self._enter_prompt.resize(
            metrics.horizontalAdvance(text) + 24,
            max(32, metrics.height() + 14),
        )
        self._enter_prompt.move(top_left)
        self._enter_prompt.show()
        self._enter_prompt.raise_()

    def hide_enter_prompt(self) -> None:
        self._enter_prompt.hide()

    def sync_parent_geometry(self) -> None:
        parent = self.parentWidget()
        if parent is not None and self.isVisible():
            self.setGeometry(parent.rect())

    def _advance(self) -> None:
        self._progress = (self._progress + 0.018) % 1.42
        self.update()

    def paintEvent(self, event) -> None:
        if self._target.isEmpty():
            return
        cycle = self._progress
        moving = cycle <= 1.0
        eased = 1.0 if not moving else cycle * cycle * (3.0 - 2.0 * cycle)
        first_cell_width = max(1.0, self._target.width() / 3.0)
        width = first_cell_width + (
            self._target.width() - first_cell_width
        ) * eased
        selection = QRectF(
            self._target.left(),
            self._target.top(),
            width,
            self._target.height(),
        )

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor(55, 126, 184, 235), 2.0))
        painter.setBrush(QBrush(QColor(82, 149, 205, 72)))
        painter.drawRect(selection.adjusted(1.0, 1.0, -1.0, -1.0))
        if moving:
            cursor = QPainterPath()
            cursor.moveTo(0.0, 0.0)
            cursor.lineTo(0.0, 25.0)
            cursor.lineTo(6.5, 18.5)
            cursor.lineTo(12.5, 31.0)
            cursor.lineTo(17.5, 28.5)
            cursor.lineTo(11.5, 16.5)
            cursor.lineTo(21.0, 16.5)
            cursor.closeSubpath()
            painter.save()
            painter.translate(
                selection.right() - 5.0,
                selection.center().y() - 5.0,
            )
            painter.setPen(QPen(QColor("#244E70"), 2.0))
            painter.setBrush(QBrush(QColor(250, 253, 255, 245)))
            painter.drawPath(cursor)
            painter.restore()


@dataclass(frozen=True)
class _Step:
    key: str
    title: str
    body: str
    target: Callable[[], QWidget | tuple[QWidget, QRect] | None]
    show_arrow: bool = False


class TutorialController(QObject):
    """Keep tutorial state separate from all normal application state."""

    def __init__(self, host) -> None:
        super().__init__(host)
        self._host = host
        self._mode: str | None = None
        self._step_index = 0
        self._steps: list[_Step] = []
        self._saved_lane_count: int | None = None
        self._saved_detection_mode: str | None = None
        self._panel = _TutorialPanel(host)
        self._highlight = _TutorialHighlight(host)
        self._table_drag_hint = _TutorialTableDragHint(host)
        self._panel._exit_btn.clicked.connect(self.stop)
        self._panel._back_btn.clicked.connect(self.previous)
        self._panel._next_btn.clicked.connect(self.next)
        self._host.results_panel._table.itemSelectionChanged.connect(
            self._on_results_selection_changed
        )
        self._follow_timer = QTimer(self)
        self._follow_timer.setInterval(120)
        self._follow_timer.timeout.connect(self._position_overlays)

    @property
    def active(self) -> bool:
        return self._mode is not None

    @property
    def mode(self) -> str | None:
        return self._mode

    @property
    def current_step_key(self) -> str | None:
        if not self.active or not self._steps:
            return None
        return self._steps[self._step_index].key

    def start(self, mode: str) -> None:
        if mode not in {"densitometry", "wb_figure"}:
            raise ValueError(f"Unknown tutorial mode: {mode}")
        if self.active:
            self.stop()
        self._saved_lane_count = self._host.param_panel.get_lane_count()
        self._saved_detection_mode = self._host.param_panel.get_mode()
        self._mode = mode
        self._step_index = 0

        if mode == "densitometry":
            self._host.param_panel.set_mode("manual")
            # Each side of the bundled examples is one three-sample replicate.
            self._host.param_panel.lane_count.setValue(3)
            self._host._show_home_page()
        else:
            self._host._on_wb_plot_mode()
            if self._host._figure_mode_window is not None:
                self._host._figure_mode_window.set_tutorial_mode(True)

        self._steps = self._build_steps(mode)
        self._panel.show()
        self._follow_timer.start()
        self._show_current_step()

    def stop(self) -> None:
        if not self.active:
            return
        if self._saved_detection_mode is not None:
            self._host.param_panel.set_mode(self._saved_detection_mode)
        if self._saved_lane_count is not None:
            self._host.param_panel.lane_count.setValue(self._saved_lane_count)
        if self._host._figure_mode_window is not None:
            self._host._figure_mode_window.set_tutorial_mode(False)
        self._mode = None
        self._steps = []
        self._step_index = 0
        self._saved_lane_count = None
        self._saved_detection_mode = None
        self._follow_timer.stop()
        self._panel.hide()
        if isValid(self._highlight):
            self._highlight.hide()
        self._table_drag_hint.clear()
        self._clear_roi_hints()
        self._host._restore_initial_home_after_tutorial()

    def handle_import_request(self) -> bool:
        """Return True only when tutorial mode intentionally owns Import."""
        if not self.active:
            return False
        if self._host._load_tutorial_images():
            self.notify_images_imported()
        return True

    def next(self) -> None:
        if not self.active:
            return
        if self._step_index >= len(self._steps) - 1:
            self.stop()
            return
        self._step_index += 1
        self._show_current_step()

    def previous(self) -> None:
        if not self.active or self._step_index <= 0:
            return
        self._step_index -= 1
        self._show_current_step()

    def refresh_language(self) -> None:
        if self.active:
            self._steps = self._build_steps(str(self._mode))
            self._show_current_step()

    def notify_images_imported(self) -> None:
        self._advance_from("import_images")

    def notify_roi_changed(self) -> None:
        key = self.current_step_key or ""
        if key in {"target_roi", "loading_roi"} or key.endswith("_lane_roi"):
            self.next()

    def notify_band_roi_changed(self) -> None:
        if (self.current_step_key or "").endswith("_band_roi"):
            self.next()

    def notify_analysis_started(self) -> None:
        if (self.current_step_key or "").startswith("analyze_"):
            self.next()

    def notify_column_setup_opened(self) -> None:
        self._advance_from("create_column_table")

    def notify_column_setup_cancelled(self) -> None:
        if self.current_step_key == "confirm_column_setup":
            self.previous()

    def notify_column_table_ready(self) -> None:
        self._advance_from("confirm_column_setup")

    def notify_table_row_selected(self, row: int) -> None:
        expected_rows = {
            "select_loading_r1_row": 1,
            "select_loading_r2_row": 4,
            "select_target_r1_row": 0,
            "select_target_r2_row": 3,
        }
        if expected_rows.get(self.current_step_key) == row:
            self.next()

    def notify_autofill_completed(self) -> None:
        if (self.current_step_key or "").startswith("autofill_"):
            self.next()

    def _on_results_selection_changed(self) -> None:
        run_by_step = {
            "autofill_loading_r1": 1,
            "autofill_loading_r2": 2,
            "autofill_target_r1": 3,
            "autofill_target_r2": 4,
        }
        run_index = run_by_step.get(self.current_step_key or "")
        if run_index is None:
            self._table_drag_hint.show_enter_prompt("", False)
            return
        bounds = self._host.results_panel._run_column_ranges.get(run_index)
        if bounds is None:
            self._table_drag_hint.show_enter_prompt("", False)
            return
        selected = {
            (index.row(), index.column())
            for index in self._host.results_panel._table.selectedIndexes()
        }
        expected = {
            (1, column)
            for column in range(bounds[0], bounds[1] + 1)
        }
        self._table_drag_hint.show_enter_prompt(
            tr(
                "Then press Enter/Return on the keyboard.",
                self._host._language,
            ),
            expected.issubset(selected),
        )

    def notify_column_table_event(self, event: str) -> None:
        mapping = {
            "negative_control_requested": "select_negative_control",
            "negative_control_selected": "select_control_group",
            "figure_generated": "generate_figure",
        }
        expected = mapping.get(event)
        if expected is not None:
            self._advance_from(expected)

    def notify_wb_event(self, event: str) -> None:
        if event == "blot_frame_selected":
            workspace = self._host._figure_mode_window
            selected = None if workspace is None else workspace._active_slot_ref
            expected_slot = (
                0
                if self.current_step_key == "select_target_frame"
                else 1 if self.current_step_key == "select_loading_frame" else None
            )
            if selected is not None and selected.slot_idx == expected_slot:
                self.next()
            return
        mapping = {
            "frame_template_dialog_opened": "frame_template",
            "frame_template_applied": "confirm_frame_template",
            "condition_template_dialog_opened": "condition_template",
            "condition_template_applied": "confirm_condition_template",
            "blot_roi_applied": "apply_target_roi"
            if self.current_step_key == "apply_target_roi"
            else "apply_loading_roi",
        }
        if (
            event == "frame_template_dialog_cancelled"
            and self.current_step_key == "confirm_frame_template"
        ):
            self.previous()
            return
        if (
            event == "condition_template_dialog_cancelled"
            and self.current_step_key == "confirm_condition_template"
        ):
            self.previous()
            return
        expected = mapping.get(event)
        if expected is not None:
            self._advance_from(expected)

    def _advance_from(self, key: str) -> None:
        if self.current_step_key == key:
            self.next()

    def _show_current_step(self) -> None:
        if not self.active or not self._steps:
            return
        step = self._steps[self._step_index]
        language = self._host._language
        final_step = self._step_index == len(self._steps) - 1
        self._panel.set_content(
            progress=tr(
                "Step {current} of {total}",
                language,
                current=self._step_index + 1,
                total=len(self._steps),
            ),
            title=tr(step.title, language),
            body=tr(step.body, language),
            back_text=tr("Back", language),
            next_text=tr("Done", language) if final_step else tr("Skip step", language),
            exit_text=tr("Exit tutorial", language),
            can_go_back=self._step_index > 0,
        )
        if step.key == "target_roi" or step.key.startswith("target_"):
            self._host._select_tutorial_image("target")
        elif step.key == "loading_roi" or step.key.startswith("loading_"):
            self._host._select_tutorial_image("loading")
        self._sync_roi_hint(step.key)
        self._sync_table_drag_hint(step.key)
        self._sync_roi_enter_prompt(step.key)
        self._position_overlays()
        self._panel.raise_()

    def _clear_roi_hints(self) -> None:
        for image_panel in self._host._image_panels:
            image_panel.canvas.clear_tutorial_roi_hint()

    def _sync_roi_hint(self, step_key: str) -> None:
        self._clear_roi_hints()
        is_lane_hint = step_key.endswith("_lane_roi")
        is_band_hint = step_key.endswith("_band_roi")
        if step_key in {"target_roi", "loading_roi"}:
            is_lane_hint = True
        if not is_lane_hint and not is_band_hint:
            return
        if self._host._active_slot_index is None:
            return
        canvas = self._host.canvas
        image_size = canvas.image_scene_size()
        if image_size is None:
            return
        width = image_size.width()
        height = image_size.height()
        role = "target" if step_key.startswith("target") else "loading"
        right_group = "right" in step_key
        if step_key == "target_roi":
            # The 3-lane tutorial frame is 108 × 18 pt (6:1). Matching that
            # aspect ratio lets Manual ROI fill it without stretching pixels.
            lane_rect = (0.12, 0.48, 0.28, 0.18)
            band_rect = lane_rect
        elif step_key == "loading_roi":
            lane_rect = (0.085, 0.405, 0.336, 0.229)
            band_rect = lane_rect
        elif role == "loading":
            lane_rect = (
                (0.635, 0.40, 0.345, 0.24)
                if right_group
                else (0.085, 0.40, 0.34, 0.24)
            )
            band_rect = (
                (0.64, 0.43, 0.115, 0.17)
                if right_group
                else (0.09, 0.43, 0.105, 0.17)
            )
        else:
            lane_rect = (
                (0.565, 0.45, 0.30, 0.24)
                if right_group
                else (0.12, 0.45, 0.28, 0.24)
            )
            band_rect = (
                (0.565, 0.47, 0.115, 0.22)
                if right_group
                else (0.115, 0.47, 0.095, 0.22)
            )
        normalized = band_rect if is_band_hint else lane_rect
        x, y, w, h = normalized
        suggested = QRect(
            int(round(x * width)),
            int(round(y * height)),
            int(round(w * width)),
            int(round(h * height)),
        )
        canvas.show_tutorial_roi_hint(QRectF(suggested))

    def _sync_table_drag_hint(self, step_key: str) -> None:
        run_by_step = {
            "autofill_loading_r1": 1,
            "autofill_loading_r2": 2,
            "autofill_target_r1": 3,
            "autofill_target_r2": 4,
        }
        run_index = run_by_step.get(step_key)
        if run_index is None:
            self._table_drag_hint.clear()
            return
        table = self._host.results_panel._table
        bounds = self._host.results_panel._run_column_ranges.get(run_index)
        if bounds is None or table.rowCount() <= 1:
            self._table_drag_hint.clear()
            return
        first = table.item(1, bounds[0])
        last = table.item(1, bounds[1])
        if first is None or last is None:
            self._table_drag_hint.clear()
            return
        table.scrollToItem(last)
        rect = table.visualItemRect(first).united(table.visualItemRect(last))
        self._table_drag_hint.show_target(table.viewport(), rect)
        self._on_results_selection_changed()

    def _sync_roi_enter_prompt(self, step_key: str) -> None:
        if step_key.startswith("autofill_"):
            return
        if step_key not in {"apply_target_roi", "apply_loading_roi"}:
            self._table_drag_hint.hide_enter_prompt()
            return
        if self._host._active_slot_index is None:
            self._table_drag_hint.hide_enter_prompt()
            return
        canvas = self._host.canvas
        roi = canvas.get_roi()
        if roi is None:
            self._table_drag_hint.hide_enter_prompt()
            return
        viewport_rect = canvas.mapFromScene(roi).boundingRect()
        prompt = self._table_drag_hint._enter_prompt
        prompt_text = tr(
            "Then press Enter/Return on the keyboard.",
            self._host._language,
        )
        metrics = prompt.fontMetrics()
        prompt_height = max(32, metrics.height() + 14)
        top_left = canvas.viewport().mapToGlobal(
            QPoint(
                viewport_rect.right() + 10,
                int(viewport_rect.center().y() - prompt_height / 2.0),
            )
        )
        self._table_drag_hint.show_prompt_at_global(prompt_text, top_left)

    def _position_overlays(self) -> None:
        if not self.active:
            return
        if not isValid(self._host):
            self._follow_timer.stop()
            return
        if not isValid(self._highlight):
            self._highlight = _TutorialHighlight(self._host)
        margin = 18
        self._panel.adjustSize()
        self._table_drag_hint.sync_parent_geometry()
        target_spec = self._steps[self._step_index].target()
        if isinstance(target_spec, tuple):
            target, target_local_rect = target_spec
        else:
            target = target_spec
            target_local_rect = None if target is None else target.rect()
        target_rect = None
        target_parent_rect = None
        if target is not None and target.isVisible():
            global_top_left = target.mapToGlobal(target_local_rect.topLeft())
            top_left = self._host.mapFromGlobal(global_top_left)
            target_rect = QRect(top_left, target_local_rect.size())
            highlight_parent = target.window()
            if self._highlight.parentWidget() is not highlight_parent:
                self._highlight.hide()
                self._highlight.setParent(highlight_parent)
                self._highlight.setAttribute(
                    Qt.WidgetAttribute.WA_TransparentForMouseEvents,
                    True,
                )
            parent_top_left = highlight_parent.mapFromGlobal(global_top_left)
            target_parent_rect = QRect(parent_top_left, target_local_rect.size())

        panel_w = self._panel.width()
        panel_h = self._panel.height()
        bottom_y = max(
            margin,
            self._host.height()
            - panel_h
            - self._host.statusBar().height()
            - margin,
        )
        right_x = max(margin, self._host.width() - panel_w - margin)
        candidates = [
            QPoint(right_x, bottom_y),
            QPoint(right_x, 72),
            QPoint(margin, bottom_y),
            QPoint(margin, 72),
        ]
        panel_position = candidates[0]
        avoid_regions: list[QRect] = []
        if target_rect is not None:
            avoid_regions.append(
                target_rect.adjusted(-16, -16, 16, 16)
            )
        enter_prompt = self._table_drag_hint._enter_prompt
        if isValid(enter_prompt) and enter_prompt.isVisible():
            prompt_top_left = self._host.mapFromGlobal(
                enter_prompt.mapToGlobal(QPoint(0, 0))
            )
            avoid_regions.append(
                QRect(prompt_top_left, enter_prompt.size()).adjusted(
                    -10,
                    -10,
                    10,
                    10,
                )
            )
        if avoid_regions:
            for candidate in candidates:
                panel_rect = QRect(candidate.x(), candidate.y(), panel_w, panel_h)
                if not any(
                    panel_rect.intersects(region)
                    for region in avoid_regions
                ):
                    panel_position = candidate
                    break
        self._panel.move(panel_position)

        if target is None or not target.isVisible():
            self._highlight.hide()
            return
        self._highlight.set_target_geometry(
            target_parent_rect,
            show_arrow=self._steps[self._step_index].show_arrow,
        )
        self._highlight.show()
        self._highlight.raise_()
        self._panel.raise_()

    def _build_steps(self, mode: str) -> list[_Step]:
        host = self._host
        active_modal_button = lambda name: (
            QApplication.activeModalWidget().findChild(QPushButton, name)
            if QApplication.activeModalWidget() is not None
            else None
        )
        canvas_target = lambda: (
            host._image_panels[host._active_slot_index]
            if host._active_slot_index is not None
            else None
        )
        no_target = lambda: None

        def table_cell_target(table, row: int, first_col: int, last_col: int):
            if table is None or row >= table.rowCount():
                return None
            first = table.item(row, first_col)
            last = table.item(row, min(last_col, table.columnCount() - 1))
            if first is None or last is None:
                return table
            rect = table.visualItemRect(first).united(table.visualItemRect(last))
            return table.viewport(), rect

        def results_run_target(run_index: int):
            table = host.results_panel._table
            bounds = host.results_panel._run_column_ranges.get(run_index)
            if bounds is None:
                return table
            last = table.item(1, bounds[1])
            if last is not None:
                table.scrollToItem(last)
            return table_cell_target(table, 1, bounds[0], bounds[1])

        def column_row_target(row: int):
            table_window = host._embedded_column_table
            return table_cell_target(
                None if table_window is None else table_window._table,
                row,
                1,
                1,
            )

        def control_group_target(group_index: int):
            table_window = host._embedded_column_table
            if table_window is None or table_window._header_view is None:
                return None
            header = table_window._header_view
            section = group_index + 2
            table = table_window._table
            item = None if table is None else table.item(0, section)
            if item is not None:
                table.scrollToItem(item)
            rect = QRect(
                header.sectionViewportPosition(section),
                0,
                header.sectionSize(section),
                header.height(),
            )
            return header.viewport(), rect

        if mode == "densitometry":
            return [
                _Step(
                    "create_column_table",
                    "Create the column table first",
                    "Click Densitometry Figure Generation. The tutorial opens Column Setup directly with Samples = 3 and Replicates = 2. Confirm it before drawing any ROI.",
                    lambda: host._figure_generation_btn,
                    True,
                ),
                _Step(
                    "confirm_column_setup",
                    "Confirm the column-table dimensions",
                    "Check Samples = 3 and Replicates = 2, then click OK at the bottom of Column Setup.",
                    lambda: active_modal_button("columnSetupOkButton"),
                    True,
                ),
                _Step(
                    "import_images",
                    "Import the practice images",
                    "Click Import Images. In tutorial mode this loads the built-in Loading Control and Target Protein images automatically; no file picker opens.",
                    lambda: host._open_toolbar_btn,
                    True,
                ),
                _Step(
                    "loading_left_lane_roi",
                    "Loading Control: left lane ROI",
                    "The Loading Control image is selected. Drag a close-fitting ROI around the left group of three lanes, following the animated rectangle without leaving excess blank space.",
                    canvas_target,
                ),
                _Step(
                    "loading_left_band_roi",
                    "Loading Control: first band ROI",
                    "Draw the small band ROI around only the first band in the left group, following the animated rectangle.",
                    canvas_target,
                ),
                _Step(
                    "analyze_loading_left",
                    "Measure Loading Control replicate 1",
                    "Click Analyze to measure the first three loading-control lanes.",
                    lambda: host._analyze_toolbar_btn,
                    True,
                ),
                _Step(
                    "loading_right_lane_roi",
                    "Loading Control: right lane ROI",
                    "Now drag a close-fitting ROI around only the right group of three lanes.",
                    canvas_target,
                ),
                _Step(
                    "loading_right_band_roi",
                    "Loading Control: first band ROI",
                    "Draw the small band ROI around only the first band in the right group.",
                    canvas_target,
                ),
                _Step(
                    "analyze_loading_right",
                    "Measure Loading Control replicate 2",
                    "Click Analyze to measure the second three loading-control lanes.",
                    lambda: host._analyze_toolbar_btn,
                    True,
                ),
                _Step(
                    "target_left_lane_roi",
                    "Target Protein: left lane ROI",
                    "The Target Protein image is now selected. Drag a close-fitting ROI around its left group of three lanes.",
                    canvas_target,
                ),
                _Step(
                    "target_left_band_roi",
                    "Target Protein: first band ROI",
                    "Draw the small band ROI around only the first band in the left group.",
                    canvas_target,
                ),
                _Step(
                    "analyze_target_left",
                    "Measure Target Protein replicate 1",
                    "Click Analyze to measure the first three target-protein lanes.",
                    lambda: host._analyze_toolbar_btn,
                    True,
                ),
                _Step(
                    "target_right_lane_roi",
                    "Target Protein: right lane ROI",
                    "Drag a close-fitting ROI around only the right group of three target-protein lanes.",
                    canvas_target,
                ),
                _Step(
                    "target_right_band_roi",
                    "Target Protein: first band ROI",
                    "Draw the small band ROI around only the first band in the right group.",
                    canvas_target,
                ),
                _Step(
                    "analyze_target_right",
                    "Measure Target Protein replicate 2",
                    "Click Analyze to measure the second three target-protein lanes.",
                    lambda: host._analyze_toolbar_btn,
                    True,
                ),
                _Step(
                    "select_loading_r1_row",
                    "Choose Loading Control replicate 1",
                    "In the Column Table, click the Loading control row under Replicate 1. This row will receive Run 1.",
                    lambda: column_row_target(1),
                    True,
                ),
                _Step(
                    "autofill_loading_r1",
                    "Fill Loading Control replicate 1",
                    "In the Results Mean row, drag across the three Run 1 values as demonstrated, then press Enter/Return to fill the selected Column Table row.",
                    lambda: results_run_target(1),
                ),
                _Step(
                    "select_loading_r2_row",
                    "Choose Loading Control replicate 2",
                    "Click the Loading control row under Replicate 2. This row will receive Run 2.",
                    lambda: column_row_target(4),
                    True,
                ),
                _Step(
                    "autofill_loading_r2",
                    "Fill Loading Control replicate 2",
                    "Drag across the three Run 2 Mean values, then press Enter/Return.",
                    lambda: results_run_target(2),
                ),
                _Step(
                    "select_target_r1_row",
                    "Choose Target Band replicate 1",
                    "Click the Target band row under Replicate 1. This row will receive Run 3.",
                    lambda: column_row_target(0),
                    True,
                ),
                _Step(
                    "autofill_target_r1",
                    "Fill Target Band replicate 1",
                    "Drag across the three Run 3 Mean values, then press Enter/Return.",
                    lambda: results_run_target(3),
                ),
                _Step(
                    "select_target_r2_row",
                    "Choose Target Band replicate 2",
                    "Click the Target band row under Replicate 2. This row will receive Run 4.",
                    lambda: column_row_target(3),
                    True,
                ),
                _Step(
                    "autofill_target_r2",
                    "Fill Target Band replicate 2",
                    "Drag across the three Run 4 Mean values, then press Enter/Return. All four input rows in the 3 × 2 Column Table will now be complete.",
                    lambda: results_run_target(4),
                ),
                _Step(
                    "select_negative_control",
                    "Choose a normalization control",
                    "Click Select Negative Control above the Column Table.",
                    lambda: (
                        None
                        if host._embedded_column_table is None
                        else host._embedded_column_table._negative_btn
                    ),
                    True,
                ),
                _Step(
                    "select_control_group",
                    "Select Group A as the control",
                    "Click the Group A column header. It will become the negative-control baseline for normalization.",
                    lambda: control_group_target(0),
                    True,
                ),
                _Step(
                    "generate_figure",
                    "Generate the densitometry figure",
                    "Click Figures Generation. The completed table will be normalized to Group A and the figure preview will be generated.",
                    lambda: (
                        None
                        if host._embedded_column_table is None
                        else host._embedded_column_table._figures_btn
                    ),
                    True,
                ),
                _Step(
                    "complete",
                    "Densitometry tutorial complete",
                    "All four analysis runs have been entered, Group A was selected as the control, and the normalized figure was generated. Click Done to return to the fresh Home page.",
                    no_target,
                ),
            ]

        figure_target = lambda name: (
            host._figure_mode_window.findChild(QPushButton, name)
            if host._figure_mode_window is not None
            else None
        )

        def blot_frame_target(slot_index: int):
            workspace = host._figure_mode_window
            if workspace is None:
                return None
            canvas = workspace._canvas
            frame = next(
                (
                    item
                    for item in canvas._blot_frames.values()
                    if item.source_ref.panel_idx == 0
                    and item.source_ref.slot_idx == slot_index
                ),
                None,
            )
            if frame is None:
                return canvas
            viewport_rect = canvas.mapFromScene(
                frame.sceneBoundingRect()
            ).boundingRect()
            return canvas.viewport(), viewport_rect

        return [
            _Step(
                "import_images",
                "Import the practice images",
                "Click Import Images to load the built-in Loading Control and Target Protein WB images.",
                lambda: host._open_toolbar_btn,
                True,
            ),
            _Step(
                "frame_template",
                "Create the blot frames",
                "Click Create Blot Frame Template. Tutorial defaults are Panels = 1, Blot Frames = 2, and Lanes = 3, matching the supplied setup screenshot.",
                lambda: figure_target("step1PrimaryButton"),
                True,
            ),
            _Step(
                "confirm_frame_template",
                "Apply the tutorial frame template",
                "Check Panels = 1, Blot Frames = 2, and Lanes = 3, then click Apply Frame.",
                lambda: active_modal_button("frameTemplateApplyButton"),
                True,
            ),
            _Step(
                "condition_template",
                "Create the blot conditions",
                "Click Create Blot Condition Template. Tutorial defaults are one condition row and one evenly divided Group Level 1.",
                lambda: figure_target("step1SecondaryButton"),
                True,
            ),
            _Step(
                "confirm_condition_template",
                "Create the tutorial conditions",
                "Check Condition rows = 1 and Group Level 1 = 1 with Evenly selected, then click Create.",
                lambda: active_modal_button("conditionCreateButton"),
                True,
            ),
            _Step(
                "select_target_frame",
                "Select the first blot frame",
                "Click the first blot frame in the figure preview. The highlight marks the exact destination for the Target Protein image.",
                lambda: blot_frame_target(0),
                True,
            ),
            _Step(
                "target_roi",
                "Draw the Target Protein ROI",
                "The Target Protein image is selected. Follow the animated dashed guide and drag an ROI over the indicated three-lane group.",
                canvas_target,
            ),
            _Step(
                "apply_target_roi",
                "Fill the first blot frame",
                "With the first blot frame selected, press Enter/Return to apply the active Target Protein ROI.",
                lambda: host._figure_mode_window,
            ),
            _Step(
                "select_loading_frame",
                "Select the second blot frame",
                "Click the second blot frame in the figure preview. This is the destination for the Loading Control image.",
                lambda: blot_frame_target(1),
                True,
            ),
            _Step(
                "loading_roi",
                "Draw the Loading Control ROI",
                "The Loading Control image is selected. Follow the animated dashed guide and drag an ROI over the matching three-lane group.",
                canvas_target,
            ),
            _Step(
                "apply_loading_roi",
                "Fill the second blot frame",
                "Press Enter/Return to apply the Loading Control ROI to the second blot frame.",
                lambda: host._figure_mode_window,
            ),
            _Step(
                "complete",
                "WB Figure tutorial complete",
                "The two example WB images now fill the two tutorial blot frames. Click Done to return to normal interaction.",
                no_target,
            ),
        ]
