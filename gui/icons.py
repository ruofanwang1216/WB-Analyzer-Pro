"""Painted toolbar icons for the main window."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPainterPath, QPen, QPixmap

from gui import theming


def _folder_open_icon(size: int = 22) -> QIcon:
    """Return a compact outline folder icon matching the Import Images control."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(size / 24.0, size / 24.0)
    pen = QPen(theming.color("#29483B"), 1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    folder_back = QPainterPath()
    folder_back.moveTo(3.0, 19.0)
    folder_back.lineTo(3.0, 6.0)
    folder_back.cubicTo(3.0, 4.7, 3.7, 4.0, 5.0, 4.0)
    folder_back.lineTo(9.0, 4.0)
    folder_back.lineTo(11.2, 7.0)
    folder_back.lineTo(19.0, 7.0)
    folder_back.cubicTo(20.3, 7.0, 21.0, 7.7, 21.0, 9.0)
    folder_back.lineTo(21.0, 10.5)
    painter.drawPath(folder_back)

    folder_front = QPainterPath()
    folder_front.moveTo(3.2, 18.8)
    folder_front.lineTo(5.7, 11.8)
    folder_front.cubicTo(6.1, 10.7, 6.8, 10.2, 8.0, 10.2)
    folder_front.lineTo(20.5, 10.2)
    folder_front.cubicTo(21.8, 10.2, 22.2, 11.0, 21.8, 12.2)
    folder_front.lineTo(19.4, 19.0)
    folder_front.cubicTo(19.0, 20.2, 18.2, 20.7, 17.0, 20.7)
    folder_front.lineTo(5.0, 20.7)
    folder_front.cubicTo(3.7, 20.7, 2.8, 20.0, 3.2, 18.8)
    painter.drawPath(folder_front)
    painter.end()
    return QIcon(pixmap)


def _zoom_icon(*, zoom_in: bool, size: int = 18) -> QIcon:
    """Return a compact magnifier icon for an image-panel zoom control."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(size / 18.0, size / 18.0)
    pen = QPen(theming.color("#405967"), 1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(QRectF(2.5, 2.0, 9.5, 9.5))
    painter.drawLine(QPointF(10.2, 10.0), QPointF(15.2, 15.0))
    painter.drawLine(QPointF(5.0, 6.75), QPointF(9.5, 6.75))
    if zoom_in:
        painter.drawLine(QPointF(7.25, 4.5), QPointF(7.25, 9.0))
    painter.end()
    return QIcon(pixmap)


def _image_operation_icon(operation: str, size: int = 18) -> QIcon:
    """Draw compact, font-independent arrows for the image operation menu."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(size / 18.0, size / 18.0)
    pen = QPen(theming.color("#26322D"), 1.65)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    if operation == "flip-horizontal":
        painter.drawLine(QPointF(3.0, 9.0), QPointF(15.0, 9.0))
        painter.drawLine(QPointF(3.0, 9.0), QPointF(6.0, 6.0))
        painter.drawLine(QPointF(3.0, 9.0), QPointF(6.0, 12.0))
        painter.drawLine(QPointF(15.0, 9.0), QPointF(12.0, 6.0))
        painter.drawLine(QPointF(15.0, 9.0), QPointF(12.0, 12.0))
    elif operation == "flip-vertical":
        painter.drawLine(QPointF(9.0, 3.0), QPointF(9.0, 15.0))
        painter.drawLine(QPointF(9.0, 3.0), QPointF(6.0, 6.0))
        painter.drawLine(QPointF(9.0, 3.0), QPointF(12.0, 6.0))
        painter.drawLine(QPointF(9.0, 15.0), QPointF(6.0, 12.0))
        painter.drawLine(QPointF(9.0, 15.0), QPointF(12.0, 12.0))
    elif operation == "auto-straighten":
        # A tilted band above the level band it becomes.
        painter.drawLine(QPointF(3.0, 7.5), QPointF(15.0, 3.5))
        painter.drawLine(QPointF(3.0, 13.5), QPointF(15.0, 13.5))
        painter.drawLine(QPointF(9.0, 7.6), QPointF(9.0, 10.6))
        painter.drawLine(QPointF(9.0, 10.6), QPointF(7.4, 9.0))
        painter.drawLine(QPointF(9.0, 10.6), QPointF(10.6, 9.0))
    elif operation == "custom-rotate":
        # One continuous, two-headed curved arrow mirrors the reference icon
        # while remaining crisp on systems without the corresponding glyph.
        curve = QPainterPath(QPointF(3.0, 12.5))
        curve.cubicTo(4.0, 5.0, 14.0, 5.0, 15.0, 12.5)
        painter.drawPath(curve)
        painter.drawLine(QPointF(3.0, 12.5), QPointF(3.2, 8.7))
        painter.drawLine(QPointF(3.0, 12.5), QPointF(6.7, 11.7))
        painter.drawLine(QPointF(15.0, 12.5), QPointF(14.8, 8.7))
        painter.drawLine(QPointF(15.0, 12.5), QPointF(11.3, 11.7))
    elif operation == "undo":
        path = QPainterPath(QPointF(5.0, 6.0))
        path.cubicTo(13.5, 4.0, 15.0, 8.2, 14.0, 12.0)
        painter.drawPath(path)
        painter.drawLine(QPointF(5.0, 6.0), QPointF(8.2, 3.8))
        painter.drawLine(QPointF(5.0, 6.0), QPointF(7.7, 8.7))

    painter.end()
    return QIcon(pixmap)


def _crisp_icon(size: int, draw) -> QIcon:
    """Paint *draw* (in a 0..size box) at 1x, 2x and 3x so it stays sharp.

    Qt picks the pixmap that matches the screen, so Retina and scaled macOS
    displays never enlarge a low-resolution bitmap.
    """
    icon = QIcon()
    for ratio in (1, 2, 3):
        pixmap = QPixmap(size * ratio, size * ratio)
        pixmap.setDevicePixelRatio(float(ratio))
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        draw(painter)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def _theme_toggle_icon(*, dark: bool, size: int = 16) -> QIcon:
    """Sun while the dark theme is on, moon while the light theme is on."""
    import math

    color = theming.color("#3B4A43")

    def draw(painter: QPainter) -> None:
        painter.scale(size / 16.0, size / 16.0)
        if dark:
            pen = QPen(color, 1.3)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(5.4, 5.4, 5.2, 5.2))
            for step in range(8):
                angle = math.radians(step * 45.0)
                dx, dy = math.cos(angle), math.sin(angle)
                painter.drawLine(
                    QPointF(8.0 + dx * 4.6, 8.0 + dy * 4.6),
                    QPointF(8.0 + dx * 6.4, 8.0 + dy * 6.4),
                )
        else:
            # A filled crescent: a disc with an offset disc cut away.
            disc = QPainterPath()
            disc.addEllipse(QRectF(2.0, 2.0, 12.0, 12.0))
            cut = QPainterPath()
            cut.addEllipse(QRectF(5.6, -0.6, 11.0, 11.0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawPath(disc.subtracted(cut))

    return _crisp_icon(size, draw)


def _panel_arrow_icon(*, direction: str, size: int = 10) -> QIcon:
    """A small filled triangle pointing left, right, up or down."""
    color = theming.color("#3B4A43")

    def draw(painter: QPainter) -> None:
        painter.scale(size / 10.0, size / 10.0)
        tri = QPainterPath()
        if direction == "left":
            tri.moveTo(7.2, 1.6)
            tri.lineTo(7.2, 8.4)
            tri.lineTo(2.4, 5.0)
        elif direction == "up":
            tri.moveTo(1.6, 7.2)
            tri.lineTo(8.4, 7.2)
            tri.lineTo(5.0, 2.4)
        elif direction == "down":
            tri.moveTo(1.6, 2.8)
            tri.lineTo(8.4, 2.8)
            tri.lineTo(5.0, 7.6)
        else:
            tri.moveTo(2.8, 1.6)
            tri.lineTo(2.8, 8.4)
            tri.lineTo(7.6, 5.0)
        tri.closeSubpath()
        pen = QPen(color, 0.9)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(color)
        painter.drawPath(tri)

    return _crisp_icon(size, draw)
