"""Automatic lane/band detection for the densitometry (quantification) workflow.

Mixed into MainWindow (gui.main_window); methods rely on its attributes.

Flow: the user draws one large ROI and sets the lane count, then clicks
"Auto-detect lanes".  Detected boxes are shown as editable items (drag to
move/resize, Delete to remove, drag on empty space to add) and Analyze
measures every remaining box.
"""
from __future__ import annotations

import numpy as np
from PIL import Image
from PySide6.QtCore import QRectF
from PySide6.QtWidgets import QMessageBox

from utils.i18n import LANG_ZH_CN
from utils.logger import get_logger

log = get_logger(__name__)


def _presentation_pixels(raw: np.ndarray, transform) -> np.ndarray:
    """Native-depth pixels resampled into presentation (canvas) geometry."""
    value = transform.sanitized()
    pixels = np.asarray(raw)
    if pixels.ndim == 3:
        rgb = pixels[:, :, :3].astype(np.float32)
        pixels = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]
    if value.is_identity():
        return pixels
    height, width = pixels.shape
    matrix, output_size = value.affine(width, height)
    inverse = np.linalg.inv(matrix)
    coefficients = tuple(float(v) for v in (*inverse[0], *inverse[1]))
    source = np.ascontiguousarray(pixels, dtype=np.float32)
    border = np.concatenate([source[0], source[-1], source[:, 0], source[:, -1]])
    image = Image.fromarray(source, mode="F").transform(
        output_size,
        Image.Transform.AFFINE,
        coefficients,
        resample=Image.Resampling.BILINEAR,
        fillcolor=float(np.median(border)),
    )
    return np.asarray(image, dtype=np.float32)


class AutoLaneMixin:
    """Auto lane/band detection inside the densitometry lane ROI."""

    def _quant_auto_msg(self, en: str, zh: str) -> str:
        return zh if getattr(self, "_language", None) == LANG_ZH_CN else en

    def _quant_auto_active(self) -> bool:
        return (
            not self._is_wb_plot_workspace_active()
            and self.canvas.is_quant_auto_review()
        )

    def _leave_quant_auto_review(self) -> None:
        self.param_panel.set_adjacent_band_option(False)
        if self.canvas.is_quant_auto_review():
            self.canvas.set_quant_auto_review(False)

    def _wb_plot_lane_fit_detections(
        self, search_roi, expected_lane_count: int | None
    ) -> tuple[list[dict], dict] | None:
        """Lane-fitting detection for WB Plot Auto-Fit, or None to use the old detector.

        Needs the lane count from the figure layout and a rough ROI.  Uses the
        same native-depth detector as densitometry auto-detect, so figure
        crops get the same lane and band placement.
        """
        from core.lane_auto_detect import detect_lanes_in_roi

        if not expected_lane_count or not isinstance(search_roi, QRectF):
            return None
        raw = self.canvas.current_analysis_pixels()
        if raw is None:
            return None
        try:
            pixels = _presentation_pixels(raw, self.canvas.get_geometry_transform())
            result = detect_lanes_in_roi(
                pixels,
                (search_roi.x(), search_roi.y(), search_roi.width(), search_roi.height()),
                int(expected_lane_count),
            )
        except Exception:
            log.exception("WB Plot lane-fit detection failed; using the guided detector")
            return None
        detections = [
            {
                "lane_index": lane["lane_index"],
                "lane_rect": QRectF(*lane["lane_rect"]),
                "bands": [
                    {
                        "band_rect": QRectF(*band["band_rect"]),
                        "band_index": band["band_index"],
                        "row_index": band["row_index"],
                    }
                    for band in lane["bands"]
                ],
            }
            for lane in result["lanes"]
        ]
        if not detections or not any(lane["bands"] for lane in detections):
            return None
        metadata = {
            "failure_stage": None,
            "message": result.get("message", ""),
            "mode": "lane_fit",
            "retained_lanes": len(detections),
            "retained_bands": sum(len(lane["bands"]) for lane in detections),
            "detected_polarity": "light_on_dark" if result.get("light_on_dark", True) else "dark_on_light",
        }
        return detections, metadata

    def _run_quant_auto_lane_detect(self) -> None:
        """Detect lanes + bands inside the drawn lane ROI and show editable boxes."""
        from core.lane_auto_detect import detect_lanes_in_roi

        if self._is_wb_plot_workspace_active():
            return
        title = self._quant_auto_msg("Auto-detect lanes", "自动识别泳道")
        if self._active_slot_index is None or self._image_path is None:
            QMessageBox.information(
                self, title,
                self._quant_auto_msg("Upload or select an image first.", "请先上传或选中一张图像。"),
            )
            return
        roi = self.canvas.get_roi()
        if roi is None and self.param_panel.adjacent_band_option_available():
            # The ROI was cleared while the adjacent-band choice was still
            # showing; retire the choice quietly instead of raising a dialog.
            self.param_panel.set_adjacent_band_option(False)
            return
        if roi is None:
            QMessageBox.information(
                self, title,
                self._quant_auto_msg(
                    "Draw one large ROI around all the bands first, set the lane count, "
                    "then click Auto-detect lanes.",
                    "请先在图上框一个包含所有条带的大区域，填好泳道数，再点“自动识别泳道”。",
                ),
            )
            return
        raw = self.canvas.current_analysis_pixels()
        if raw is None:
            return
        lane_count = self.param_panel.get_lane_count()
        try:
            pixels = _presentation_pixels(raw, self.canvas.get_geometry_transform())
            include_adjacent = self.param_panel.include_adjacent_band()
            result = detect_lanes_in_roi(
                pixels,
                (roi.x(), roi.y(), roi.width(), roi.height()),
                lane_count,
                include_adjacent=include_adjacent,
            )
        except Exception as exc:  # detection must never take the window down
            log.exception("Quantification auto lane detection failed")
            QMessageBox.warning(
                self, title,
                self._quant_auto_msg(
                    f"Automatic detection failed:\n{exc}\n\nYou can still draw the band ROI manually.",
                    f"自动识别失败：\n{exc}\n\n仍然可以手动框选条带 ROI。",
                ),
            )
            return

        detections = [
            {
                "lane_index": lane["lane_index"],
                "lane_rect": QRectF(*lane["lane_rect"]),
                "bands": [
                    {
                        "band_rect": QRectF(*band["band_rect"]),
                        "band_index": band["band_index"],
                        "row_index": band["row_index"],
                    }
                    for band in lane["bands"]
                ],
            }
            for lane in result["lanes"]
        ]
        total_bands = sum(len(lane["bands"]) for lane in detections)
        if not detections or total_bands == 0:
            self._status_bar.showMessage(
                self._quant_auto_msg(
                    "No bands found in the ROI — adjust the ROI, or draw the band ROI manually.",
                    "在框选区域里没有找到条带——请调整区域，或手动框选条带 ROI。",
                ),
                6000,
            )
            return

        self._quant_auto_light_on_dark = bool(result.get("light_on_dark", True))
        self._band_roi = None
        self._lane_rects = [QRectF(lane["lane_rect"]) for lane in detections]
        self._auto_detections = self._clone_auto_detections(detections)
        self.canvas.set_auto_detect_overlays(self._auto_detections, quiet_lanes=True)
        self.canvas.set_quant_auto_review(True)
        self.canvas.setFocus()
        self._save_active_slot_state()
        self._act_analyze.setEnabled(True)
        rows = len(result.get("rows", []))
        adjacent_rows = result.get("adjacent_rows", [])
        self.param_panel.set_adjacent_band_option(bool(adjacent_rows))
        if adjacent_rows:
            # The choice changes what is measured, so say which way it is set.
            if result.get("include_adjacent"):
                self._status_bar.showMessage(
                    self._quant_auto_msg(
                        f"Found {len(detections)} lane(s) × {rows} band row(s). A faint band next to the "
                        "main band is INCLUDED in the boxes — untick “Include adjacent band” to measure "
                        "the main band only.",
                        f"识别到 {len(detections)} 条泳道 × {rows} 排条带。主条带旁边的淡条带已包含在框内——"
                        "取消勾选“包含相邻条带”可只测主条带。",
                    )
                )
            else:
                self._status_bar.showMessage(
                    self._quant_auto_msg(
                        f"Found {len(detections)} lane(s) × {rows} band row(s). A faint band sits right next "
                        "to the main band: boxes cover the MAIN band only — tick “Include adjacent band” "
                        "to measure both together.",
                        f"识别到 {len(detections)} 条泳道 × {rows} 排条带。主条带旁边紧挨着一条淡条带：目前的框"
                        "只包住主条带——勾选“包含相邻条带”可把两条一起测。",
                    )
                )
            return
        self._status_bar.showMessage(
            self._quant_auto_msg(
                f"Found {len(detections)} lane(s) × {rows} band row(s). Drag boxes to adjust, "
                "Delete removes the selected box, drag on empty space adds one — then Analyze.",
                f"识别到 {len(detections)} 条泳道 × {rows} 排条带。拖动框可调整，Delete 删除选中的框，"
                "在空白处拖拽可补画，确认后点“定量分析”。",
            )
        )

    def _on_quant_auto_rois_edited(self, detections: list[dict]) -> None:
        """Boxes were moved / resized / added / deleted during review."""
        self._auto_detections = self._clone_auto_detections(detections)
        self.canvas.refresh_auto_band_labels()
        self._save_active_slot_state()
        total_bands = sum(len(lane["bands"]) for lane in self._auto_detections)
        self._act_analyze.setEnabled(total_bands > 0)
        self._status_bar.showMessage(
            self._quant_auto_msg(
                f"{total_bands} band box(es) selected — click Analyze to measure.",
                f"当前共 {total_bands} 个条带框——点“定量分析”进行测量。",
            )
        )

    def _quant_auto_band_rois(self) -> list[dict]:
        """Band boxes in the same order as the #N labels on the canvas (row by row)."""
        rois: list[dict] = []
        row = 0
        previous: QRectF | None = None
        lane_indices = [lane for _rect, lane in self.canvas.ordered_auto_band_rects()]
        light_on_dark = bool(getattr(self, "_quant_auto_light_on_dark", True))
        for (rect, strips), lane_index in zip(
            self.canvas.auto_band_background_strips(), lane_indices
        ):
            if (
                previous is None
                or rect.center().x() <= previous.center().x()
                or rect.center().y() > previous.bottom() + previous.height()
                or rect.center().y() < previous.top() - previous.height()
            ):
                row += 1
            previous = rect
            rois.append(
                {
                    "x": rect.x(),
                    "y": rect.y(),
                    "width": rect.width(),
                    "height": rect.height(),
                    "lane": int(lane_index) if lane_index is not None else len(rois) + 1,
                    "band": row,
                    # Same "#N" as the label drawn above the box on the image.
                    "band_label": f"#{len(rois) + 1}",
                    # Local background: thin strips right above / below the box,
                    # already mapped to raw-image space for the measurement.
                    "background_rois": [
                        self.canvas.map_canvas_roi_to_raw(strip) for strip in strips
                    ],
                    "light_on_dark": light_on_dark,
                }
            )
        return rois

    def _run_quant_auto_analysis(self) -> None:
        band_rois = self._quant_auto_band_rois()
        if not band_rois:
            QMessageBox.warning(
                self, "No Band ROI",
                self._quant_auto_msg(
                    "There are no band boxes to measure.", "没有可测量的条带框。"
                ),
            )
            return
        self._persistence.remember_analysis_debug(
            image_path=self._image_path,
            mode="auto_lanes",
            lane_count=len(self._auto_detections),
            band_count=len(band_rois),
        )
        raw_band_rois = self.canvas.map_canvas_rois_to_raw(band_rois)
        self._start_measurement_worker(self._image_path, raw_band_rois)
