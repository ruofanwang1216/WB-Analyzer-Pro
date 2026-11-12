"""Fixed-size ROI handling for densitometry and WB Plot.

Mixed into MainWindow (gui.main_window); methods rely on its attributes.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSizeF
from PySide6.QtWidgets import QMessageBox


class FixedRoiMixin:
    """Fixed-size ROI handling for densitometry and WB Plot."""

    def _on_add_fixed_wb_plot_roi(self) -> QSizeF | None:
        if self._image_path is None:
            QMessageBox.warning(self, "No Image", "Upload or select a WB image first.")
            return None
        had_size = self.canvas.set_fixed_roi_mode(True)
        size = self.canvas.get_fixed_roi_viewport_size()
        self._set_workspace_focus_target("image")
        if had_size and size is not None:
            self._status_bar.showMessage(
                "Fixed ROI size captured. Select its saved size, drag it on any WB image, then press Return/Enter to apply.",
                5000,
            )
            return QSizeF(size)
        else:
            self._status_bar.showMessage(
                "Fixed ROI mode armed. Draw one ROI to set its size, then click Fix ROI again to save it.",
                5000,
            )
            return None

    def _on_cancel_fixed_wb_plot_roi(self) -> None:
        for idx, panel in enumerate(self._image_panels):
            panel.canvas.cancel_fixed_roi_mode(clear_current_roi=True)
            self._slot_states[idx]["lane_rects"] = []
            self._slot_states[idx]["band_roi"] = None
        self._lane_rects.clear()
        self._band_roi = None
        self._act_analyze.setEnabled(False)
        self._status_bar.showMessage("Fixed ROI mode canceled for all WB image windows.", 3000)

    def _on_select_fixed_wb_plot_roi_size(self, size: QSizeF) -> None:
        applied = 0
        for idx, panel in enumerate(self._image_panels):
            if self._slot_states[idx].get("path") is None:
                continue
            panel.canvas.set_fixed_roi_viewport_size(size, enabled=True)
            applied += 1
        self._set_workspace_focus_target("image")
        self._status_bar.showMessage(
            f"Fixed ROI size selected for {applied} WB image window(s). Click/drag to place it.",
            4000,
        )

    def _on_add_fixed_general_roi(self) -> dict[str, object] | None:
        if self._active_slot_index is None or self._image_path is None:
            QMessageBox.warning(self, "No Image", "Upload or select an image first.")
            return None
        if self.param_panel.get_mode() != "manual":
            self._status_bar.showMessage("Fixed ROI is available in Manual mode.", 3000)
            return None

        roi = self.canvas.get_roi()
        band_roi = self.canvas.get_band_roi()
        if roi is not None:
            profile = self._build_fixed_general_roi_profile(roi, band_roi)
            self._set_workspace_focus_target("image")
            if band_roi is not None:
                self._status_bar.showMessage(
                    "Fixed lane & band ROI saved. Select it on another image to place both ROIs and analyze directly.",
                    5000,
                )
            else:
                self._status_bar.showMessage(
                    "Fixed Lane ROI saved. Select it on another image, then draw a band ROI before Analyze.",
                    5000,
                )
            return profile

        self.canvas.set_fixed_roi_mode(True)
        self._set_workspace_focus_target("image")
        self._status_bar.showMessage(
            "Fixed ROI mode armed. Draw one lane ROI to set its size, then click Fix ROI again to save it.",
            5000,
        )
        return None

    def _build_fixed_general_roi_profile(
        self,
        roi: QRectF,
        band_roi: QRectF | None,
    ) -> dict[str, object]:
        image_size = self.canvas.image_scene_size()
        image_w = max(image_size.width(), 1.0) if image_size is not None else max(roi.right(), 1.0)
        image_h = max(image_size.height(), 1.0) if image_size is not None else max(roi.bottom(), 1.0)
        profile: dict[str, object] = {
            "kind": "lane_band" if band_roi is not None else "lane",
            "lane_size": QSizeF(roi.width(), roi.height()),
            "lane_size_norm": QSizeF(roi.width() / image_w, roi.height() / image_h),
        }
        if band_roi is not None and roi.width() > 0 and roi.height() > 0:
            profile["band_relative"] = QRectF(
                (band_roi.x() - roi.x()) / roi.width(),
                (band_roi.y() - roi.y()) / roi.height(),
                band_roi.width() / roi.width(),
                band_roi.height() / roi.height(),
            )
        return profile

    def _on_cancel_fixed_general_roi(self) -> None:
        if self._active_slot_index is None:
            return
        self.canvas.cancel_fixed_roi_mode(clear_current_roi=True)
        self._lane_rects.clear()
        self._band_roi = None
        self._auto_detections = []
        self._act_analyze.setEnabled(False)
        self.param_panel.set_auto_edit_enabled(False)
        self._save_active_slot_state()
        self._status_bar.showMessage("Fixed ROI mode canceled for the active image.", 3000)

    def _on_select_fixed_general_roi_size(self, profile: dict[str, object]) -> None:
        if self._active_slot_index is None or self._image_path is None:
            self._status_bar.showMessage("Select an image before applying a fixed ROI size.", 3000)
            return
        if self.param_panel.get_mode() != "manual":
            self._status_bar.showMessage("Fixed ROI is available in Manual mode.", 3000)
            return
        lane_size = self._fixed_general_lane_size_for_active_image(profile)
        if lane_size is None:
            self._status_bar.showMessage("Could not apply the saved fixed ROI size.", 3000)
            return
        band_relative = profile.get("band_relative")
        band_profile = band_relative if isinstance(band_relative, QRectF) else None
        self.canvas.clear_roi()
        self._lane_rects.clear()
        self._band_roi = None
        self._auto_detections = []
        self._act_analyze.setEnabled(False)
        self.canvas.set_fixed_roi_profile(
            lane_size,
            band_relative=band_profile,
            enabled=True,
        )
        self._set_workspace_focus_target("image")
        if profile.get("kind") == "lane_band":
            self._status_bar.showMessage(
                "Fixed lane & band ROI selected. Click/drag on the active image to place both ROIs.",
                4000,
            )
        else:
            self._status_bar.showMessage(
                "Fixed Lane ROI selected. Click/drag on the active image to place it, press Return/Enter to lock it, then draw a band ROI.",
                4000,
            )

    def _fixed_general_lane_size_for_active_image(self, profile: dict[str, object]) -> QSizeF | None:
        image_size = self.canvas.image_scene_size()
        norm_size = profile.get("lane_size_norm")
        if image_size is not None and isinstance(norm_size, QSizeF):
            width = norm_size.width() * image_size.width()
            height = norm_size.height() * image_size.height()
            if width > 0 and height > 0:
                return QSizeF(width, height)
        lane_size = profile.get("lane_size")
        if isinstance(lane_size, QSizeF):
            return QSizeF(lane_size)
        return None
