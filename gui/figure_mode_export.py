"""PDF, TIFF and PPTX export actions.

Mixed into FigureModeWindow (gui.figure_mode_window); methods rely on its attributes.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from core.export_engine import PPTX_AVAILABLE, PDFExporter, PPTXExporter, TIFFExporter
from core.layout_engine import LayoutResult


class FigureExportMixin:
    """PDF, TIFF and PPTX export actions."""

    def _layout_with_annotations(self) -> LayoutResult:
        """Return LayoutResult with overlay annotations appended for export."""
        base = self._layout_result
        self._apply_text_style_overrides_to_layout()
        hidden = self._canvas.hidden_text_keys()
        base_items = [
            item for item in base.items
            if item.source_ref is None or item.source_ref.key() not in hidden
        ]
        base_items = self._canvas.adjusted_layout_items_for_export(base_items)
        extra = self._canvas.overlay_as_layout_items()
        if not extra:
            return LayoutResult(
                items=base_items,
                canvas_width_pt=base.canvas_width_pt,
                canvas_height_pt=base.canvas_height_pt,
            )
        return LayoutResult(
            items=base_items + extra,
            canvas_width_pt=base.canvas_width_pt,
            canvas_height_pt=base.canvas_height_pt,
        )

    def _on_export_pdf(self) -> None:
        if not self._check_export_ready():
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export PDF", str(Path.home() / "wb_figure.pdf"),
            "PDF (*.pdf)"
        )
        if not path:
            return
        try:
            layout = self._layout_with_annotations()
            snapshot = self._canvas.render_page_image(scale=4.0)
            PDFExporter().export_image(
                snapshot,
                layout.canvas_width_pt,
                layout.canvas_height_pt,
                path,
            )
            QMessageBox.information(self, "Export PDF", f"Saved to:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export PDF", f"Export failed:\n{exc}")

    def _on_export_tiff(self) -> None:
        if not self._check_export_ready():
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export TIFF",
            str(Path.home() / "wb_figure.tiff"),
            "TIFF Image (*.tif *.tiff)",
        )
        if not path:
            return
        if Path(path).suffix.lower() not in {".tif", ".tiff"}:
            path = f"{path}.tiff"
        try:
            snapshot = self._canvas.render_page_image(
                scale=TIFFExporter.render_scale_for_dpi(),
                include_overflow=True,
            )
            TIFFExporter().export_image(snapshot, path)
            QMessageBox.information(self, "Export TIFF", f"Saved to:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export TIFF", f"Export failed:\n{exc}")

    def _on_export_pptx(self) -> None:
        if not PPTX_AVAILABLE:
            QMessageBox.warning(
                self,
                "PPTX Unavailable",
                "python-pptx is not installed.\n\nRun:  pip install python-pptx",
            )
            return
        if not self._check_export_ready():
            return

        # Ask user how to save
        dlg = QDialog(self)
        dlg.setWindowTitle("Export PPTX")
        dlg.setMinimumWidth(340)
        vl = QVBoxLayout(dlg)
        vl.setSpacing(12)
        vl.addWidget(QLabel("How would you like to save the slide?"))
        btn_new = QPushButton("Save as a new file")
        btn_new.setStyleSheet("QPushButton { padding: 8px 16px; text-align: left; }")
        btn_append = QPushButton("Save as a slide in an existing file")
        btn_append.setStyleSheet("QPushButton { padding: 8px 16px; text-align: left; }")
        btn_cancel = QPushButton("Cancel")
        vl.addWidget(btn_new)
        vl.addWidget(btn_append)
        vl.addWidget(btn_cancel)
        choice = [None]

        def _pick(val):
            choice[0] = val
            dlg.accept()

        btn_new.clicked.connect(lambda: _pick("new"))
        btn_append.clicked.connect(lambda: _pick("append"))
        btn_cancel.clicked.connect(dlg.reject)
        self._retranslate_widget_tree(dlg)
        if dlg.exec() != QDialog.DialogCode.Accepted or choice[0] is None:
            return

        layout = self._layout_with_annotations()
        if choice[0] == "new":
            path, _ = QFileDialog.getSaveFileName(
                self, "Export PPTX", str(Path.home() / "wb_figure.pptx"),
                "PowerPoint (*.pptx)"
            )
            if not path:
                return
            try:
                PPTXExporter().export(layout, path)
                QMessageBox.information(self, "Export PPTX", f"Saved to:\n{path}")
            except Exception as exc:
                QMessageBox.critical(self, "Export PPTX", f"Export failed:\n{exc}")
        else:
            existing, _ = QFileDialog.getOpenFileName(
                self, "Select Existing PPTX", str(Path.home()),
                "PowerPoint (*.pptx)"
            )
            if not existing:
                return
            try:
                PPTXExporter().export_append_slide(layout, existing)
                QMessageBox.information(self, "Export PPTX", f"Slide appended to:\n{existing}")
            except Exception as exc:
                QMessageBox.critical(self, "Export PPTX", f"Export failed:\n{exc}")

    def _check_export_ready(self) -> bool:
        if self._layout_result is None or not self._layout_result.items:
            QMessageBox.warning(
                self, "Nothing to Export",
                "Complete Step 1: Choose Layout before exporting the Figure.",
            )
            return False
        return True
