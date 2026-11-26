"""Tests for the User Tutorial video window."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from gui import video_tutorial as vt


def _app():
    return QApplication.instance() or QApplication([])


def _mouse(kind, pos):
    button = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else Qt.MouseButton.LeftButton
    return QMouseEvent(kind, pos, pos, button, button, Qt.KeyboardModifier.NoModifier)


def test_video_file_is_bundled():
    assert vt.video_path().exists()


def test_steps_match_the_video():
    assert len(vt.STEP_TITLES) == len(vt.STEP_STARTS) == 10
    assert list(vt.STEP_STARTS) == sorted(vt.STEP_STARTS)
    assert vt.step_at_time(0.0) == -1
    assert vt.step_at_time(vt.STEP_STARTS[5] + 0.1) == 5
    assert vt.step_at_time(69.0) == 9


def test_clicking_a_step_emits_its_index_and_hover_shows_hand():
    _app()
    surface = vt._VideoSurface()
    surface.resize(960, 540)
    clicked, background = [], []
    surface.stepClicked.connect(clicked.append)
    surface.backgroundClicked.connect(lambda: background.append(True))
    centre = surface.step_widget_rect(8).center()

    # Steps are inert while the list is not on screen (title and outro).
    surface.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, centre))
    assert clicked == [] and background == [True]

    surface.set_steps_enabled(True)
    surface.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, centre))
    assert surface.cursor().shape() == Qt.CursorShape.PointingHandCursor
    surface.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, centre))
    assert clicked == [8]
    surface.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, QPointF(700, 300)))
    assert clicked == [8] and len(background) == 2


def test_jump_to_step_seeks_the_player():
    _app()
    window = vt.VideoTutorialWindow()
    seen = []
    window.player.setPosition = seen.append  # type: ignore[method-assign]
    window.jump_to_step(3)
    window.jump_to_step(99)
    assert seen == [int(vt.STEP_STARTS[3] * 1000) + vt._SEEK_LEAD_MS]
    window.close()


def test_toolbar_button_opens_the_video_tutorial():
    _app()
    from gui.main_window import MainWindow

    window = MainWindow()
    assert window._tutorial_btn.text() == "User Tutorial"
    window._tutorial_btn.click()
    assert window._video_tutorial_window is not None
    assert window._video_tutorial_window.isVisible()
    window._video_tutorial_window.close()
    window.close()
