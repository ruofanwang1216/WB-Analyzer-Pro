"""Shared pytest setup for the Qt test suite."""
from __future__ import annotations

import gc


def pytest_sessionfinish(session, exitstatus) -> None:
    """Tear Qt down in a defined order before the interpreter exits.

    Tests leave top-level windows alive. Letting Python's shutdown destroy
    them in arbitrary order after the QApplication intermittently crashed or
    hung the process once every test had already passed.
    """
    try:
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return
    app = QApplication.instance()
    if app is None:
        return
    for widget in QApplication.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    gc.collect()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
