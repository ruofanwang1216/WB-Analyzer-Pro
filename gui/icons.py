"""Painted toolbar icons for the main window."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap


def _folder_open_icon(size: int = 22) -> QIcon:
    """Return a compact outline folder icon matching the Import Images control."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(size / 24.0, size / 24.0)
    pen = QPen(QColor("#29483B"), 1.8)
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
    pen = QPen(QColor("#405967"), 1.6)
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
    pen = QPen(QColor("#26322D"), 1.65)
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
