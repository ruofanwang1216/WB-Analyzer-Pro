"""User Tutorial window: the quick-start video with clickable steps.

The video shows a numbered step list on its left side. Those steps are
clickable here: a click jumps playback to that step. Left alone, the video
simply plays on a loop.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QImage, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from utils.i18n import tr

# Geometry and timing of assets/tutorial/quick_start_guide.mp4. Update these
# together with the video whenever it is re-rendered.
VIDEO_SIZE = QSize(1920, 1080)
STEP_TITLES = (
    "Import",
    "Straighten",
    "Adjust display",
    "Box the bands",
    "Analyze",
    "Loading control",
    "Replicates",
    "Plot",
    "Build the figure",
    "Export",
)
# Second at which each step starts in the video.
STEP_STARTS = (4.5, 8.2, 11.0, 15.2, 21.8, 26.0, 32.8, 39.2, 42.8, 61.0)
# The step list is on screen between these two times.
STEPS_VISIBLE = (4.5, 67.0)
# Step rows in video pixels: x, first row y, width, row height, row pitch.
_STEP_X, _STEP_Y0, _STEP_W, _STEP_H, _STEP_PITCH = 44.0, 156.0, 370.0, 58.0, 64.0
# Land slightly inside the step so its caption and highlight are already shown.
_SEEK_LEAD_MS = 250


def video_path() -> Path:
    if getattr(sys, "frozen", False):
        root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    else:
        root = Path(__file__).resolve().parents[1]
    return root / "assets" / "tutorial" / "quick_start_guide.mp4"


def step_rect(index: int) -> QRectF:
    """Rectangle of step ``index`` in video pixel coordinates."""
    return QRectF(_STEP_X, _STEP_Y0 + index * _STEP_PITCH, _STEP_W, _STEP_H)


def step_at_time(seconds: float) -> int:
    """Index of the step playing at ``seconds``, or -1 before the first one."""
    current = -1
    for index, start in enumerate(STEP_STARTS):
        if seconds >= start:
            current = index
    return current


def _format_time(ms: int) -> str:
    total = max(0, int(ms // 1000))
    return f"{total // 60}:{total % 60:02d}"


class _VideoSurface(QWidget):
    """Paints video frames and turns the step list in them into buttons."""

    stepClicked = Signal(int)
    backgroundClicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._image = QImage()
        self._hover = -1
        self._steps_enabled = False
        self.setMouseTracking(True)
        self.setMinimumSize(640, 360)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt name)
        return QSize(1120, 630)

    def set_frame(self, frame) -> None:
        image = frame.toImage() if frame is not None and frame.isValid() else QImage()
        if image.isNull():
            return
        self._image = image
        self.update()

    def set_steps_enabled(self, enabled: bool) -> None:
        if enabled == self._steps_enabled:
            return
        self._steps_enabled = enabled
        if not enabled:
            self._set_hover(-1)

    # ── geometry ────────────────────────────────────────────────────────────
    def video_rect(self) -> QRectF:
        """Where the video is drawn inside the widget (letterboxed)."""
        vw, vh = VIDEO_SIZE.width(), VIDEO_SIZE.height()
        scale = min(self.width() / vw, self.height() / vh)
        w, h = vw * scale, vh * scale
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def step_widget_rect(self, index: int) -> QRectF:
        target = self.video_rect()
        scale = target.width() / VIDEO_SIZE.width()
        rect = step_rect(index)
        return QRectF(
            target.x() + rect.x() * scale,
            target.y() + rect.y() * scale,
            rect.width() * scale,
            rect.height() * scale,
        )

    def step_at(self, pos: QPointF) -> int:
        if not self._steps_enabled:
            return -1
        for index in range(len(STEP_TITLES)):
            # Include the gap below each row so the list has no dead stripes.
            rect = self.step_widget_rect(index)
            pitch = rect.height() * _STEP_PITCH / _STEP_H
            if QRectF(rect.x(), rect.y(), rect.width(), pitch).contains(pos):
                return index
        return -1

    # ── events ──────────────────────────────────────────────────────────────
    def _set_hover(self, index: int) -> None:
        if index == self._hover:
            return
        self._hover = index
        if index >= 0:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.unsetCursor()
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self._set_hover(self.step_at(event.position()))

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._set_hover(-1)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        index = self.step_at(event.position())
        if index >= 0:
            self.stepClicked.emit(index)
        else:
            self.backgroundClicked.emit()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#141B1D"))
        if self._image.isNull():
            return
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawImage(self.video_rect(), self._image)
        if self._hover >= 0:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = self.step_widget_rect(self._hover)
            radius = rect.height() * 14 / _STEP_H
            painter.setPen(QColor(246, 241, 231, 150))
            painter.setBrush(QColor(246, 241, 231, 30))
            painter.drawRoundedRect(rect, radius, radius)


class VideoTutorialWindow(QDialog):
    """Plays the quick-start video; clicking a step in it jumps there."""

    practiceRequested = Signal()

    def __init__(self, parent=None, *, language: str = "en") -> None:
        super().__init__(parent)
        self._language = language
        self._dragging = False
        self.setModal(False)
        self.setObjectName("videoTutorialWindow")
        self.setStyleSheet(
            "QDialog#videoTutorialWindow { background:#141B1D; }"
            "QLabel { color:#C9D6D5; font-size:12px; }"
            "QPushButton { background:#243033; color:#F6F1E7; border:1px solid #3A4A4D;"
            " border-radius:8px; padding:5px 12px; font-size:12px; }"
            "QPushButton:hover { background:#2E3D40; }"
            "QSlider::groove:horizontal { height:4px; background:#3A4A4D; border-radius:2px; }"
            "QSlider::sub-page:horizontal { background:#C4472D; border-radius:2px; }"
            "QSlider::handle:horizontal { width:12px; margin:-5px 0; border-radius:6px;"
            " background:#F6F1E7; }"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._surface = _VideoSurface(self)
        root.addWidget(self._surface, 1)

        self._message = QLabel(self._surface)
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message.setWordWrap(True)
        self._message.setStyleSheet("color:#F6F1E7; font-size:14px;")
        self._message.hide()

        bar = QHBoxLayout()
        bar.setContentsMargins(14, 8, 14, 10)
        bar.setSpacing(10)
        self._play_btn = QPushButton()
        self._play_btn.setFixedWidth(40)
        self._play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        bar.addWidget(self._play_btn)
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 0)
        self._slider.setCursor(Qt.CursorShape.PointingHandCursor)
        bar.addWidget(self._slider, 1)
        self._time_label = QLabel("0:00 / 0:00")
        bar.addWidget(self._time_label)
        self._mute_btn = QPushButton()
        self._mute_btn.setFixedWidth(40)
        self._mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        bar.addWidget(self._mute_btn)
        self._practice_btn = QPushButton()
        self._practice_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._practice_btn.clicked.connect(self._on_practice)
        bar.addWidget(self._practice_btn)
        root.addSpacing(8)
        root.addLayout(bar)

        # Imported here so the rest of the app never depends on multimedia.
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink

        self._QMediaPlayer = QMediaPlayer
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._audio.setVolume(0.6)
        self._sink = QVideoSink(self)
        self._player.setAudioOutput(self._audio)
        self._player.setVideoSink(self._sink)
        self._player.setLoops(QMediaPlayer.Loops.Infinite)

        self._sink.videoFrameChanged.connect(self._surface.set_frame)
        self._player.positionChanged.connect(self._on_position)
        self._player.durationChanged.connect(self._on_duration)
        self._player.playbackStateChanged.connect(lambda _s: self._refresh_icons())
        self._player.errorOccurred.connect(self._on_error)
        self._surface.stepClicked.connect(self.jump_to_step)
        self._surface.backgroundClicked.connect(self.toggle_play)
        self._play_btn.clicked.connect(self.toggle_play)
        self._mute_btn.clicked.connect(self._toggle_mute)
        self._slider.sliderPressed.connect(self._on_slider_pressed)
        self._slider.sliderReleased.connect(self._on_slider_released)
        self._slider.sliderMoved.connect(self._on_slider_moved)
        self._slider.actionTriggered.connect(self._on_slider_action)
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, activated=self.toggle_play)

        self.set_language(language)
        self._refresh_icons()
        self.resize(1120, 700)

        path = video_path()
        if path.exists():
            self._player.setSource(QUrl.fromLocalFile(str(path)))
        else:
            self._show_message(
                tr("The tutorial video file is missing from this installation.", language)
            )

    # ── public API ──────────────────────────────────────────────────────────
    @property
    def player(self):
        return self._player

    @property
    def surface(self) -> _VideoSurface:
        return self._surface

    def set_language(self, language: str) -> None:
        self._language = language
        self.setWindowTitle(tr("User Tutorial", language))
        self._practice_btn.setText(tr("Hands-on practice", language))
        self._practice_btn.setToolTip(
            tr("Close the video and practise step by step with two built-in images.", language)
        )
        self._play_btn.setToolTip(tr("Play / Pause", language))
        self._mute_btn.setToolTip(tr("Mute / Unmute", language))

    def start(self) -> None:
        """Show the window and play from the beginning."""
        self.show()
        self.raise_()
        self.activateWindow()
        self._player.setPosition(0)
        self._player.play()

    def jump_to_step(self, index: int) -> None:
        if not 0 <= index < len(STEP_STARTS):
            return
        self._player.setPosition(int(STEP_STARTS[index] * 1000) + _SEEK_LEAD_MS)
        self._player.play()

    def toggle_play(self) -> None:
        if self._player.playbackState() == self._QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    # ── internals ───────────────────────────────────────────────────────────
    def _on_practice(self) -> None:
        self.close()
        self.practiceRequested.emit()

    def _toggle_mute(self) -> None:
        self._audio.setMuted(not self._audio.isMuted())
        self._refresh_icons()

    def _refresh_icons(self) -> None:
        style = self.style()
        playing = (
            self._player.playbackState() == self._QMediaPlayer.PlaybackState.PlayingState
        )
        self._play_btn.setIcon(
            style.standardIcon(
                QStyle.StandardPixmap.SP_MediaPause
                if playing
                else QStyle.StandardPixmap.SP_MediaPlay
            )
        )
        self._mute_btn.setIcon(
            style.standardIcon(
                QStyle.StandardPixmap.SP_MediaVolumeMuted
                if self._audio.isMuted()
                else QStyle.StandardPixmap.SP_MediaVolume
            )
        )

    def _on_duration(self, duration: int) -> None:
        self._slider.setRange(0, max(0, int(duration)))
        self._update_time(self._player.position())

    def _on_position(self, position: int) -> None:
        if not self._dragging:
            self._slider.setValue(int(position))
        self._update_time(position)
        seconds = position / 1000.0
        self._surface.set_steps_enabled(
            STEPS_VISIBLE[0] <= seconds < STEPS_VISIBLE[1]
        )

    def _update_time(self, position: int) -> None:
        self._time_label.setText(
            f"{_format_time(position)} / {_format_time(self._player.duration())}"
        )

    def _on_slider_pressed(self) -> None:
        self._dragging = True

    def _on_slider_moved(self, value: int) -> None:
        self._player.setPosition(int(value))

    def _on_slider_released(self) -> None:
        self._dragging = False
        self._player.setPosition(int(self._slider.value()))

    def _on_slider_action(self, _action: int) -> None:
        # Clicks on the groove and keyboard steps (not handle drags).
        if not self._dragging:
            self._player.setPosition(int(self._slider.sliderPosition()))

    def _on_error(self, _error, message: str = "") -> None:
        text = tr("The tutorial video could not be played.", self._language)
        if message:
            text = f"{text}\n{message}"
        self._show_message(text)

    def _show_message(self, text: str) -> None:
        self._message.setText(text)
        self._message.setGeometry(self._surface.rect())
        self._message.show()
        self._message.raise_()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._message.setGeometry(self._surface.rect())

    def closeEvent(self, event) -> None:  # noqa: N802
        self._player.stop()
        super().closeEvent(event)

    def reject(self) -> None:
        self._player.stop()
        super().reject()
