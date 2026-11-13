from __future__ import annotations
import sys

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from config.paths import migrate_legacy_data
from config.settings import APP_NAME, VERSION
from gui.theme import APP_STYLESHEET
from utils.logger import get_logger

log = get_logger(__name__)


def main() -> None:
    # Must run before anything reads settings, templates or Blot Files.
    for note in migrate_legacy_data():
        log.info("%s", note)

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(VERSION)
    app.setOrganizationName("WB-Lab")

    app.setStyleSheet(APP_STYLESHEET)

    # Import here so Qt app exists before any widget is constructed
    from gui.main_window import MainWindow

    log.info("Starting %s v%s", APP_NAME, VERSION)
    window = MainWindow()
    # Remember dragged panel dividers between launches (off in tests).
    window._persist_layout_on_close = True
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
