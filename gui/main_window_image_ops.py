"""Rotate, flip and undo of the active image.

Mixed into MainWindow (gui.main_window); methods rely on its attributes.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from core.image_transform import (
    GeometryTransform,
    geometry_transform_from_dict,
    geometry_transform_to_dict,
    image_transform_to_dict,
)
from utils.logger import get_logger

log = get_logger(__name__)


class ImageOperationsMixin:
    """Rotate, flip and undo of the active image."""

    def _refresh_rotation_controls(self) -> None:
        has_active = self._active_slot_index is not None and self._image_path is not None
        self.param_panel.set_rotation_controls_enabled(has_active)
        if not has_active:
            self.param_panel.set_rotation_mode_active(False)
            self.param_panel.set_rotation_angle(0.0)
            return
        canvas = self.canvas
        self.param_panel.set_rotation_mode_active(canvas.is_rotation_mode())
        self.param_panel.set_rotation_angle(canvas.get_rotation_angle())

    def _on_rotation_angle_changed_for_slot(self, slot_index: int, angle: float) -> None:
        if self._active_slot_index != slot_index:
            return
        self.param_panel.set_rotation_angle(angle)

    def _on_rotation_mode_changed_for_slot(self, slot_index: int, enabled: bool) -> None:
        if self._active_slot_index != slot_index:
            return
        self.param_panel.set_rotation_mode_active(enabled)
        if not enabled:
            self.param_panel.set_rotation_angle(0.0)

    def _on_custom_rotate_requested(self) -> None:
        if self._active_slot_index is None or self._image_path is None:
            QMessageBox.information(self, "Select Image", "Please select an image first.")
            return

        if not self.canvas.enter_rotation_mode():
            QMessageBox.warning(self, "Rotation", "No image is loaded in the selected window.")
            return

        self.canvas.setFocus()
        self._act_analyze.setEnabled(False)
        self.param_panel.set_rotation_mode_active(True)
        self.param_panel.set_rotation_angle(self.canvas.get_rotation_angle())
        self._refresh_detection_actions()
        self._status_bar.showMessage(
            "Custom rotation mode enabled — drag to align the red crosshair, then press Return/Enter to apply."
        )

    def _on_cancel_rotate_requested(self) -> None:
        if self._active_slot_index is None or self._image_path is None:
            return
        self.canvas.cancel_rotation_mode()
        self._refresh_detection_actions()
        self._refresh_rotation_controls()
        self._status_bar.showMessage("Custom rotation canceled.")

    def _image_operation_snapshot(self) -> dict | None:
        if self._active_slot_index is None or self._image_path is None:
            return None
        return {
            "filename": self._image_panels[self._active_slot_index].filename_label.text(),
            "display_transform": image_transform_to_dict(
                self.canvas.get_image_transform_params()
            ),
            "geometry_transform": geometry_transform_to_dict(
                self.canvas.get_geometry_transform()
            ),
        }

    def _commit_image_operation(
        self,
        geometry_transform: GeometryTransform,
        snapshot: dict,
        status: str,
    ) -> None:
        if self._active_slot_index is None:
            return
        slot_index = self._active_slot_index
        panel_canvas = self._image_panels[slot_index].canvas
        view_transform = panel_canvas.transform()
        panel_canvas.set_geometry_transform(geometry_transform)
        panel_canvas.setTransform(view_transform)
        if panel_canvas._pixmap_item is not None:
            panel_canvas.centerOn(panel_canvas._pixmap_item)
        panel_canvas.cancel_rotation_mode()
        panel_canvas.clear_roi()

        slot = self._slot_states[slot_index]
        slot.setdefault("image_operation_history", []).append(snapshot)
        slot["lane_rects"] = []
        slot["band_roi"] = None
        slot["auto_detections"] = []
        self._lane_rects.clear()
        self._band_roi = None
        self._auto_detections = []
        self.param_panel.set_auto_edit_enabled(False)
        self.canvas.set_auto_edit_mode(False)
        self._refresh_detection_actions()
        self._refresh_rotation_controls()
        self._status_bar.showMessage(status)

    def _on_flip_image_requested(self, *, vertical: bool) -> None:
        if self._active_slot_index is None or self._image_path is None:
            QMessageBox.information(self, "Select Image", "Please select an image first.")
            return
        if self.canvas.is_rotation_mode():
            self.canvas.cancel_rotation_mode()
        snapshot = self._image_operation_snapshot()
        if snapshot is None:
            return
        direction = "vertical" if vertical else "horizontal"
        try:
            geometry_transform = self.canvas.get_geometry_transform().flipped_in_presentation(
                vertical=vertical,
            )
            self._commit_image_operation(
                geometry_transform,
                snapshot,
                f"Image flipped {direction}. Use Rotate → Undo Image Operation to revert.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Image Flip Error", f"Failed to flip image:\n{exc}")
            log.error("Image flip failed: %s", exc)

    def _on_undo_image_operation_requested(self) -> None:
        if self._active_slot_index is None or self._image_path is None:
            return
        if self.canvas.is_rotation_mode():
            self.canvas.cancel_rotation_mode()
            self._refresh_detection_actions()
            self._refresh_rotation_controls()
            self._status_bar.showMessage("Custom rotation canceled.")
            return
        slot = self._slot_states[self._active_slot_index]
        history = slot.setdefault("image_operation_history", [])
        if not history:
            self._status_bar.showMessage("No image operation to undo.", 3000)
            return
        snapshot = history.pop()
        previous_geometry = geometry_transform_from_dict(
            snapshot.get("geometry_transform")
        )
        try:
            view_transform = self.canvas.transform()
            self.canvas.set_geometry_transform(previous_geometry)
            self.canvas.setTransform(view_transform)
            if self.canvas._pixmap_item is not None:
                self.canvas.centerOn(self.canvas._pixmap_item)
            self.canvas.clear_roi()
        except Exception as exc:
            history.append(snapshot)
            QMessageBox.critical(self, "Undo Image Operation", f"Failed to restore image:\n{exc}")
            log.error("Image operation undo failed: %s", exc)
            return

        slot["lane_rects"] = []
        slot["band_roi"] = None
        slot["auto_detections"] = []
        self._lane_rects.clear()
        self._band_roi = None
        self._auto_detections = []
        self._image_panels[self._active_slot_index].set_filename(
            str(snapshot.get("filename") or Path(self._image_path).name)
        )
        self.param_panel.set_auto_edit_enabled(False)
        self.canvas.set_auto_edit_mode(False)
        self._refresh_detection_actions()
        self._refresh_rotation_controls()
        self._status_bar.showMessage("Undid the last image operation.")

    def _on_rotate_requested(self) -> None:
        if self._active_slot_index is None or self._image_path is None:
            QMessageBox.information(self, "Select Image", "Please select an image first.")
            return
        if not self.canvas.is_rotation_mode():
            QMessageBox.information(self, "Custom Rotate", "Click Custom Rotate first.")
            return

        angle_deg = self.canvas.get_rotation_angle()
        max_deskew_angle = 45.0
        if abs(angle_deg) > max_deskew_angle:
            clamped = max(-max_deskew_angle, min(max_deskew_angle, angle_deg))
            QMessageBox.warning(
                self,
                "Rotation Angle Clamped",
                f"The selected angle ({angle_deg:+.2f}°) is outside the deskew range.\n"
                f"It will be clamped to {clamped:+.2f}°.",
            )
            angle_deg = clamped
        if abs(angle_deg) < 0.01:
            self.canvas.cancel_rotation_mode()
            self._refresh_detection_actions()
            self._refresh_rotation_controls()
            self._status_bar.showMessage("Rotation skipped (angle is near zero).")
            return

        snapshot = self._image_operation_snapshot()
        if snapshot is None:
            return

        try:
            # The crosshair is an alignment reference: once it follows the
            # bands, returning that reference to horizontal must rotate the
            # image by the opposite angle.
            applied_rotation = -float(angle_deg)
            log.info(
                "Custom rotate apply: displayed_angle=%+.3f deg, rotation_to_apply=%+.3f deg",
                float(self.canvas.get_rotation_angle()),
                applied_rotation,
            )
            geometry_transform = self.canvas.get_geometry_transform().rotated(
                applied_rotation
            )
            self._commit_image_operation(
                geometry_transform,
                snapshot,
                f"Rotation applied ({angle_deg:+.2f}° reference). Use Rotate → Undo Image Operation to revert.",
            )
        except Exception as e:
            QMessageBox.critical(self, "Rotation Error", f"Failed to rotate image:\n{e}")
            log.error("Rotation failed for %s: %s", self._image_path, e)
