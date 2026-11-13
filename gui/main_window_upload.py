"""Import, Image Lab conversion and TIFF export for the main window.

Mixed into MainWindow (gui.main_window); methods rely on its attributes.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QThread
from PySide6.QtWidgets import QFileDialog, QListWidgetItem, QMessageBox

from gui.image_panel_widgets import _UploadedFileListRow
from gui.workers import UploadConversionWorker
from utils.i18n import tr
from utils.logger import get_logger

log = get_logger(__name__)


class UploadMixin:
    """Import, Image Lab conversion and TIFF export for the main window."""

    def _ensure_direct_exporter(self):
        if self._direct_exporter is not None:
            return self._direct_exporter

        exporter_root = self._resolve_exporter_root()
        if exporter_root is None:
            raise FileNotFoundError(
                "WB-TIFF-exporter module not found (checked bundled vendor copy "
                "and sibling WB-TIFF-exporter folder)"
            )
        if str(exporter_root) not in sys.path:
            sys.path.insert(0, str(exporter_root))

        from direct_exporter import DirectExporter
        from scn_parser import SUPPORTED_EXTENSIONS as SUPPORTED_DOC_EXTENSIONS

        self._supported_upload_exts = set(SUPPORTED_DOC_EXTENSIONS)
        self._direct_exporter = DirectExporter()
        return self._direct_exporter

    @staticmethod
    def _resolve_exporter_root() -> Path | None:
        """Locate the direct_exporter/scn_parser/tiff_writer module folder.

        Checks, in order: a PyInstaller-bundled copy (packaged builds), a
        vendored copy shipped inside this repo, then the original sibling
        "WB-TIFF-exporter" project folder used during local development.
        """
        candidates: list[Path] = []
        if getattr(sys, "frozen", False):
            meipass = getattr(sys, "_MEIPASS", "")
            if meipass:
                candidates.append(Path(meipass) / "vendor" / "wb_tiff_exporter")
        candidates.append(Path(__file__).resolve().parents[1] / "vendor" / "wb_tiff_exporter")
        candidates.append(Path(__file__).resolve().parents[2] / "WB-TIFF-exporter")

        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    @staticmethod
    def _ensure_unique_path(path: Path) -> Path:
        if not path.exists():
            return path
        counter = 2
        while True:
            candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
            if not candidate.exists():
                return candidate
            counter += 1

    def _refresh_uploaded_files_list(self) -> None:
        self._files_list.clear()
        for source_key, entry in sorted(
            self._converted_documents.items(),
            key=lambda item: item[1]["source"].name.lower(),
        ):
            name = entry["source"].name
            if not entry["display_tiff"]:
                name = f"{name} (conversion failed)"
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, source_key)
            item.setSizeHint(QSize(0, 32))
            item.setToolTip(str(entry["source"]))
            self._files_list.addItem(item)
            row = _UploadedFileListRow(source_key, name, str(entry["source"]))
            row.open_requested.connect(self._open_uploaded_file_entry)
            row.remove_requested.connect(self._remove_uploaded_file_entry)
            self._files_list.setItemWidget(item, row)
        self._export_all_tiffs_btn.setEnabled(bool(self._converted_documents))

    def _open_uploaded_file_entry(self, source_key: str) -> None:
        entry = self._converted_documents.get(source_key)
        if not entry:
            return

        display_tiff: Path | None = entry.get("display_tiff")
        if display_tiff is None:
            QMessageBox.warning(
                self,
                "No TIFF",
                f"No TIFF was generated for {entry['source'].name}.",
            )
            return
        self._load_converted_tiff_into_viewer(display_tiff, clear_results_on_first=False)

    def _remove_uploaded_file_entry(self, source_key: str) -> None:
        entry = self._converted_documents.pop(source_key, None)
        if not entry:
            return
        self._refresh_uploaded_files_list()
        self._status_bar.showMessage(
            f"Removed {entry['source'].name} from Uploaded Files.",
            3000,
        )

    def _load_converted_tiff_into_viewer(
        self,
        image_path: Path,
        *,
        clear_results_on_first: bool = False,
    ) -> bool:
        normalized_path = str(image_path.expanduser().resolve())
        for idx, state in enumerate(self._slot_states):
            if state["path"] and Path(state["path"]).expanduser().resolve() == Path(normalized_path):
                self._set_active_slot_from_interaction(idx)
                return True

        loaded = self._loaded_slot_indices()
        if len(loaded) >= len(self._image_panels):
            QMessageBox.information(
                self,
                "Maximum Images Reached",
                f"You can open a maximum of {len(self._image_panels)} images. Please remove one before adding another.",
            )
            return False

        target_slot = next(
            (idx for idx, state in enumerate(self._slot_states) if state["path"] is None),
            None,
        )
        if target_slot is None:
            return False
        try:
            log.info("Starting TIFF load in viewer slot %d: %s", target_slot + 1, image_path)
            if self._active_slot_index is not None:
                self._save_active_slot_state()

            self._image_panels[target_slot].canvas.load_image(normalized_path)
            self._image_panels[target_slot].set_filename(Path(normalized_path).name)
            self._slot_states[target_slot]["path"] = normalized_path
            self._slot_states[target_slot]["lane_rects"] = []
            self._slot_states[target_slot]["band_roi"] = None
            self._slot_states[target_slot]["auto_detections"] = []
            self._slot_states[target_slot]["image_operation_history"] = []
            self._slot_states[target_slot]["selected"] = len(loaded) == 0

            if clear_results_on_first and len(loaded) == 0:
                self.results_panel.clear()

            self._current_mode = self.param_panel.get_mode()
            self.param_panel.set_auto_edit_enabled(False)
            self.canvas.set_auto_edit_mode(False)
            self._refresh_image_panel_layout()
            self._refresh_detection_actions()

            self._status_bar.showMessage(
                f"Loading {image_path.name} — preparing preview…"
            )
            return True
        except Exception as e:
            log.exception("Image load error for %s", image_path)
            QMessageBox.critical(self, "Image Load Error", str(e))
            return False

    def _on_image_preview_ready_for_slot(self, slot_index: int, image_path: str) -> None:
        slot_path = self._slot_states[slot_index].get("path")
        if not slot_path or Path(slot_path).expanduser().resolve() != Path(image_path):
            return
        self._status_bar.showMessage(
            f"Preview ready: {Path(image_path).name} — loading full-resolution quantitative data…"
        )

    def _on_image_load_finished_for_slot(self, slot_index: int, image_path: str) -> None:
        slot_path = self._slot_states[slot_index].get("path")
        if not slot_path or Path(slot_path).expanduser().resolve() != Path(image_path):
            return
        self._refresh_image_panel_layout()
        self._refresh_detection_actions()
        loaded = self._loaded_slot_indices()
        if len(loaded) > 1:
            message = (
                f"Loaded: {Path(image_path).name} — viewer showing {len(loaded)} images. "
                "Click an image to work on it."
            )
        else:
            message = f"Loaded: {Path(image_path).name} — draw a ROI to continue."
        self._status_bar.showMessage(message)
        log.info(
            "TIFF load finished in viewer slot %d (%s): %s",
            slot_index + 1,
            "memory-mapped" if self._image_panels[slot_index].canvas.is_memory_mapped() else "decoded",
            image_path,
        )
        if (
            self._tutorial_controller.active
            and self._tutorial_controller.current_step_key == "import_images"
            and self._tutorial_images_ready()
        ):
            self._select_tutorial_image("loading")
            self._status_bar.showMessage(
                tr(
                    "Loaded the built-in Loading Control and Target Protein tutorial images.",
                    self._language,
                ),
                5000,
            )
            self._tutorial_controller.notify_images_imported()

    def _on_image_load_failed_for_slot(
        self,
        slot_index: int,
        image_path: str,
        message: str,
    ) -> None:
        slot = self._slot_states[slot_index]
        slot_path = slot.get("path")
        if not slot_path or Path(slot_path).expanduser().resolve() != Path(image_path):
            return
        if self._active_slot_index == slot_index:
            self._active_slot_index = None
            self._image_path = None
            self._lane_rects.clear()
            self._band_roi = None
            self._auto_detections = []
        slot.update({
            "path": None,
            "selected": False,
            "lane_rects": [],
            "band_roi": None,
            "auto_detections": [],
            "image_operation_history": [],
        })
        self._image_panels[slot_index].canvas.clear_image()
        self._image_panels[slot_index].set_filename("")
        self._refresh_image_panel_layout()
        self._refresh_detection_actions()
        self._status_bar.showMessage("Image load failed — see error dialog.")
        log.error("Image load error for %s: %s", image_path, message)
        QMessageBox.critical(self, "Image Load Error", message)

    def _upload_files(self) -> None:
        if self._tutorial_controller.handle_import_request():
            return
        if self._upload_thread is not None and self._upload_thread.isRunning():
            self._status_bar.showMessage(
                tr("An upload is already in progress — please wait.", self._language)
            )
            return

        doc_exts = set(self._supported_upload_exts)
        tiff_exts = set(self._direct_tiff_exts)
        exts = " ".join(f"*{ext}" for ext in sorted(doc_exts | tiff_exts))
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            tr("Import Images", self._language),
            str(self._persistence.default_open_dir()),
            f"Image Lab docs + TIFF ({exts});;All files (*)",
        )
        if not paths:
            return
        self._import_paths(paths)

    def _import_paths(self, paths: list[str]) -> None:
        """Import files chosen in the dialog or dropped onto the window."""
        if self._upload_thread is not None and self._upload_thread.isRunning():
            self._status_bar.showMessage(
                tr("An upload is already in progress — please wait.", self._language)
            )
            return
        if not paths:
            return

        converter_error: Exception | None = None
        try:
            self._ensure_direct_exporter()
        except Exception as e:
            converter_error = e

        doc_exts = set(self._supported_upload_exts)
        tiff_exts = set(self._direct_tiff_exts)
        selected_paths = [Path(p).expanduser().resolve() for p in paths]
        if selected_paths:
            self._persistence.remember_open_dir(selected_paths[0].parent)
        docs = [p for p in selected_paths if p.suffix.lower() in doc_exts]
        direct_tiffs = [p for p in selected_paths if p.suffix.lower() in tiff_exts]
        unsupported = [p for p in selected_paths if p.suffix.lower() not in (doc_exts | tiff_exts)]

        if docs and self._direct_exporter is None:
            QMessageBox.critical(
                self,
                "Conversion Error",
                f"Image Lab document conversion is unavailable:\n\n{converter_error}",
            )
            docs = []
        elif docs:
            self._start_upload_conversion(
                docs=docs,
                selected_paths=selected_paths,
                direct_tiffs=direct_tiffs,
                unsupported=unsupported,
            )
            return

        self._complete_upload(
            results=[],
            selected_paths=selected_paths,
            direct_tiffs=direct_tiffs,
            unsupported=unsupported,
        )

    def _start_upload_conversion(
        self,
        *,
        docs: list[Path],
        selected_paths: list[Path],
        direct_tiffs: list[Path],
        unsupported: list[Path],
    ) -> None:
        """Start document conversion in a worker thread and keep the UI responsive."""
        self._pending_upload = {
            "selected_paths": list(selected_paths),
            "direct_tiffs": list(direct_tiffs),
            "unsupported": list(unsupported),
        }
        self._act_open.setEnabled(False)
        self._status_bar.showMessage(
            f"Uploading {len(selected_paths)} file(s) — converting Image Lab documents…"
        )
        log.info(
            "Upload started: %d selected, %d document(s), %d direct TIFF(s)",
            len(selected_paths),
            len(docs),
            len(direct_tiffs),
        )

        thread = QThread(self)
        worker = UploadConversionWorker(
            self._direct_exporter,
            docs,
            self._conversion_cache_dir,
        )
        self._upload_thread = thread
        self._upload_worker = worker
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.progress.connect(self._on_upload_progress)
        worker.finished.connect(self._on_upload_conversion_done)
        worker.error.connect(self._on_upload_conversion_error)
        worker.finished.connect(worker.deleteLater)
        worker.error.connect(worker.deleteLater)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(self._on_upload_thread_finished)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _on_upload_progress(self, message: str) -> None:
        log.info("[Upload/Convert] %s", message)
        if message.startswith("Parsing file:"):
            self._status_bar.showMessage(f"Uploading — {message}")

    def _on_upload_conversion_done(self, results: list) -> None:
        pending = self._pending_upload
        self._pending_upload = None
        if pending is None:
            log.error("Upload conversion finished without pending upload context")
            return
        log.info("Upload conversion finished; updating the file list and viewers")
        try:
            self._complete_upload(
                results=results,
                selected_paths=pending["selected_paths"],
                direct_tiffs=pending["direct_tiffs"],
                unsupported=pending["unsupported"],
            )
        except Exception as exc:
            log.exception("Upload post-processing failed")
            self._status_bar.showMessage("Upload post-processing failed — see error dialog.")
            QMessageBox.critical(self, "Upload Error", str(exc))

    def _on_upload_conversion_error(self, message: str) -> None:
        pending = self._pending_upload
        self._pending_upload = None
        log.error("Upload conversion failed: %s", message)
        self._status_bar.showMessage("Upload failed — see error dialog.")
        QMessageBox.critical(self, "Conversion Error", message)
        if pending is None or not pending["direct_tiffs"]:
            return
        # TIFFs picked in the same dialog need no conversion; a failed
        # Image Lab document must not silently discard them.
        try:
            self._complete_upload(
                results=[],
                selected_paths=pending["direct_tiffs"],
                direct_tiffs=pending["direct_tiffs"],
                unsupported=pending["unsupported"],
            )
        except Exception as exc:
            log.exception("Upload post-processing failed")
            self._status_bar.showMessage("Upload post-processing failed — see error dialog.")
            QMessageBox.critical(self, "Upload Error", str(exc))

    def _on_upload_thread_finished(self) -> None:
        self._act_open.setEnabled(True)
        self._upload_worker = None
        self._upload_thread = None

    def _complete_upload(
        self,
        *,
        results: list,
        selected_paths: list[Path],
        direct_tiffs: list[Path],
        unsupported: list[Path],
    ) -> None:
        """Apply completed conversion results on the main Qt thread."""
        log.info("Upload post-processing started")

        display_tiff_by_source: dict[str, Path] = {}
        failed_sources: list[str] = []
        for result in results:
            source_key = str(result.source)
            display_tiff = result.exported_files[0] if result.exported_files else None
            self._converted_documents[source_key] = {
                "source": result.source,
                "tiffs": list(result.exported_files),
                "display_tiff": display_tiff,
            }
            if display_tiff:
                display_tiff_by_source[source_key] = display_tiff
            if not result.exported_files:
                failed_sources.append(result.source.name)

        for tiff_path in direct_tiffs:
            source_key = str(tiff_path)
            self._converted_documents[source_key] = {
                "source": tiff_path,
                "tiffs": [tiff_path],
                "display_tiff": tiff_path,
            }
            display_tiff_by_source[source_key] = tiff_path

        log.info("Refreshing Uploaded Files list")
        self._refresh_uploaded_files_list()
        log.info("Uploaded Files list refreshed with %d item(s)", len(self._converted_documents))

        if failed_sources:
            QMessageBox.warning(
                self,
                "Conversion Warning",
                "Some files could not be converted:\n\n" + "\n".join(failed_sources),
            )
        if unsupported:
            skipped = "\n".join(path.name for path in unsupported[:8])
            if len(unsupported) > 8:
                skipped += f"\n... and {len(unsupported) - 8} more"
            QMessageBox.information(
                self,
                "Unsupported Files Skipped",
                f"Only Image Lab documents and TIFF files are supported.\n\nSkipped:\n{skipped}",
            )

        selected_display_tiffs: list[Path] = []
        seen_display_tiffs: set[str] = set()
        for selected in selected_paths:
            candidate = display_tiff_by_source.get(str(selected))
            if candidate is not None:
                key = str(candidate.expanduser().resolve())
                if key not in seen_display_tiffs:
                    seen_display_tiffs.add(key)
                    selected_display_tiffs.append(candidate)

        if (
            selected_display_tiffs
            and self._home_mode_active
            and not self._tutorial_controller.active
        ):
            # Importing from Home goes straight to the Quantify workspace so
            # the lane settings and Analyze are on screen without a detour.
            self._on_figure_generation_clicked()

        if selected_display_tiffs:
            existing_loaded = {
                str(Path(state["path"]).expanduser().resolve())
                for state in self._slot_states
                if state["path"]
            }
            was_empty = not existing_loaded
            loaded_new = 0
            deferred_tiffs: list[Path] = []
            for display_index, display_tiff in enumerate(selected_display_tiffs):
                key = str(display_tiff.expanduser().resolve())
                if key in existing_loaded:
                    self._load_converted_tiff_into_viewer(display_tiff, clear_results_on_first=False)
                    continue
                if self._available_image_slot_count() <= 0:
                    deferred_tiffs.extend(selected_display_tiffs[display_index:])
                    break
                if self._load_converted_tiff_into_viewer(
                    display_tiff,
                    clear_results_on_first=was_empty and loaded_new == 0,
                ):
                    existing_loaded.add(key)
                    loaded_new += 1

            if deferred_tiffs:
                if self._files_panel_collapsed:
                    self._set_files_panel_collapsed(False)
                self._status_bar.showMessage(
                    f"Upload complete. {len(deferred_tiffs)} TIFF(s) are ready in Uploaded Files, "
                    f"but all {len(self._image_panels)} image windows are full."
                )
                QMessageBox.information(
                    self,
                    "Upload Complete — Image Windows Full",
                    f"The upload completed successfully, but all {len(self._image_panels)} image windows are already in use.\n\n"
                    f"{len(deferred_tiffs)} new TIFF file(s) are ready in Uploaded Files. "
                    "Remove an image from a window, then click the file there to open it.",
                )
                log.info(
                    "Upload complete: %d TIFF(s) deferred because all %d viewers are full",
                    len(deferred_tiffs),
                    len(self._image_panels),
                )
            else:
                self._status_bar.showMessage(
                    f"Upload complete — opened {loaded_new} new image(s); "
                    f"{len(selected_display_tiffs)} selected TIFF(s) are ready."
                )
        else:
            self._status_bar.showMessage(
                f"Uploaded {len(selected_paths)} file(s). TIFF images are ready in the left file list."
            )
        log.info("Upload post-processing finished")

    def _on_uploaded_file_clicked(self, item: QListWidgetItem) -> None:
        source_key = item.data(Qt.ItemDataRole.UserRole)
        self._open_uploaded_file_entry(str(source_key))

    def _export_all_tiffs(self) -> None:
        all_tiffs: list[Path] = []
        seen: set[str] = set()
        for entry in self._converted_documents.values():
            for tiff_path in entry.get("tiffs", []):
                key = str(Path(tiff_path).expanduser().resolve())
                if key in seen:
                    continue
                seen.add(key)
                all_tiffs.append(Path(tiff_path))
        if not all_tiffs:
            QMessageBox.information(self, "Export All TIFFs", "No TIFF files to export.")
            return

        dest_dir = QFileDialog.getExistingDirectory(
            self,
            "Export All TIFFs",
            str(self._persistence.default_tiff_export_dir()),
        )
        if not dest_dir:
            return

        destination_root = Path(dest_dir).expanduser().resolve()
        copied = 0
        try:
            destination_root.mkdir(parents=True, exist_ok=True)
            self._persistence.remember_tiff_export_dir(destination_root)
            for tiff_path in all_tiffs:
                target = self._ensure_unique_path(destination_root / tiff_path.name)
                shutil.copy2(tiff_path, target)
                copied += 1
        except OSError as exc:
            log.error("TIFF export failed after %d file(s): %s", copied, exc)
            QMessageBox.critical(
                self,
                "Export Error",
                f"Exported {copied} of {len(all_tiffs)} TIFF file(s) before an error occurred:\n\n{exc}",
            )
            return
        QMessageBox.information(
            self,
            "Export Complete",
            f"Exported {copied} TIFF file(s) to:\n{destination_root}",
        )
