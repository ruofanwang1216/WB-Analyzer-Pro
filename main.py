from __future__ import annotations
import sys

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from config.settings import APP_NAME, VERSION
from utils.logger import get_logger

log = get_logger(__name__)


def main() -> None:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(VERSION)
    app.setOrganizationName("WB-Lab")

    app.setStyleSheet("""
        QMainWindow, QWidget {
            background-color: #EAF0F4;
            color: #35393D;
            font-size: 13px;
        }
        QToolBar {
            background-color: #E2ECF2;
            border-bottom: 1px solid #C8D8E4;
            spacing: 4px;
            padding: 4px 8px;
        }
        QToolBar QToolButton {
            background-color: #F5F8FA;
            border: 1px solid #C8D8E4;
            border-radius: 6px;
            padding: 4px 10px;
            color: #35393D;
        }
        QToolBar QToolButton:hover {
            background-color: #D4EDE4;
            border-color: #8AB4A0;
        }
        QToolBar QToolButton[text="Analyze"] {
            background-color: #8AB4A0;
            border-color: #6A9E88;
            color: #F5F8FA;
            font-weight: bold;
        }
        QToolBar QToolButton[text="Analyze"]:hover {
            background-color: #6A9E88;
        }
        QStatusBar {
            background-color: #E2ECF2;
            color: #A0B4C0;
            border-top: 1px solid #C8D8E4;
            font-size: 11px;
        }
        QDockWidget {
            background-color: #F5F8FA;
            border-top: 1px solid #C8D8E4;
        }
        QDockWidget::title {
            background-color: #DCE9F2;
            padding: 4px 10px;
            font-size: 12px;
            color: #35393D;
            border-bottom: 1px solid #C8D8E4;
        }
        QSplitter::handle {
            background-color: #C8D8E4;
            width: 1px;
        }
    """)

    # Import here so Qt app exists before any widget is constructed
    from gui.main_window import MainWindow

    log.info("Starting %s v%s", APP_NAME, VERSION)
    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
