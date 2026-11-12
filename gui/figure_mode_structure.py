"""Applying templates and resizing the panel/blot/lane structure.

Mixed into FigureModeWindow (gui.figure_mode_window); methods rely on its attributes.
"""
from __future__ import annotations

import copy

from PySide6.QtWidgets import QMessageBox

from core.figure_project import BlotSlot, ConditionTable, Panel
from core.layout_engine import LayoutResult, pt_to_scene
from core.template_engine import TemplateEngine


class FigureStructureMixin:
    """Applying templates and resizing the panel/blot/lane structure."""

    def _confirm_save_current_template_before_apply(self) -> bool:
        if self._project is None or self._layout_result is None:
            return True
        reply = QMessageBox.question(
            self,
            "Save Current Figure Template?",
            "Do you want to save the current Figure Template before applying a new layout?",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Cancel:
            return False
        if reply == QMessageBox.StandardButton.Yes:
            return self._on_save_template()
        return True

    def _on_apply_template(self) -> bool:
        tid, table_style = self._current_template_selection()
        if not self._confirm_save_current_template_before_apply():
            return False
        tmpl = TemplateEngine.get_template(tid)
        self._active_template_id = tid
        self._active_table_style = table_style
        self._text_style_overrides.clear()
        self._canvas._hidden_text_keys.clear()

        if not TemplateEngine.is_builtin(tid):
            # User template: restore saved project + overlay items
            try:
                project, overlay_data = TemplateEngine.restore_user_project(tid)
                format_state = TemplateEngine.restore_user_template_format_state(tid)
            except Exception as exc:
                QMessageBox.critical(
                    self,
                    "Load Figure Template",
                    f"Failed to load the Figure Template:\n{exc}",
                )
                return False
            self._project = project
            self._text_style_overrides = dict(
                format_state.get("text_style_overrides", {})
            )
            self._active_table_style = "vector_matrix" if project.global_layout.show_condition_table else "none"
            for spin, val in [
                (self._panels_spin, len(project.panels)),
                (self._blots_spin,  len(project.panels[0].blot_slots) if project.panels else 1),
                (self._lanes_spin,  project.panels[0].blot_slots[0].lane_count
                 if project.panels and project.panels[0].blot_slots else 4),
            ]:
                spin.blockSignals(True)
                spin.setValue(val)
                spin.blockSignals(False)
            canvas_state = format_state.get("canvas_state") or {}
            if canvas_state:
                self._canvas.restore_state_snapshot(canvas_state, repopulate_scene=False)
            elif overlay_data:
                self._canvas._restore_overlay_from_data(overlay_data)
            self._rebuild_step4()
            self._recompute_and_refresh()
            return True

        # Built-in template
        for spin, val in [
            (self._panels_spin, tmpl.default_panel_count),
            (self._blots_spin,  tmpl.default_blot_count),
            (self._lanes_spin,  tmpl.default_lane_count),
        ]:
            spin.blockSignals(True)
            spin.setValue(val)
            spin.blockSignals(False)

        self._project = TemplateEngine.build_project(tid)
        for panel in self._project.panels:
            panel.condition_table = TemplateEngine.make_condition_table(
                table_style,
                self._lanes_spin.value(),
            )
        self._project.global_layout.show_condition_table = table_style != "none"

        self._rebuild_step4()
        self._recompute_and_refresh()
        return True

    def _on_apply_structure(self) -> None:
        if not self._confirm_save_current_template_before_apply():
            return
        n_panels = self._panels_spin.value()
        n_blots  = self._blots_spin.value()
        n_lanes  = self._lanes_spin.value()
        old_panel_count = len(self._project.panels) if self._project is not None else 0
        old_layout = self._layout_engine.compute(self._project) if self._project is not None else None
        old_overlay_data = self._canvas.overlay_items_as_json_data()

        if self._project is None:
            tid, table_style = self._current_template_selection()
            self._active_template_id = tid
            self._active_table_style = table_style
            self._project = TemplateEngine.build_project(tid, n_panels, n_blots, n_lanes)
        else:
            table_style = self._active_table_style

        self._resize_current_project(n_panels, n_blots, n_lanes, table_style)
        self._apply_created_frame_defaults()
        if n_panels > old_panel_count and old_overlay_data and old_layout is not None:
            self._duplicate_first_panel_overlays_for_new_panels(
                old_overlay_data,
                old_layout,
                old_panel_count,
                n_panels,
            )
        self._rebuild_step4()
        self._recompute_and_refresh(fit_view=False)
        self._canvas.fit_frame_content_to_view()

    def _apply_created_frame_defaults(self) -> None:
        """Apply the labeling rules specific to a newly created frame."""
        if self._project is None:
            return
        layout = self._project.global_layout
        layout.show_mw_labels = False
        layout.panel_layout = (
            "horizontal" if len(self._project.panels) > 1 else "vertical"
        )
        layout.share_ib_labels = len(self._project.panels) > 1
        for panel in self._project.panels:
            for slot_index, slot in enumerate(panel.blot_slots):
                slot.label = f"IB: Protein {slot_index + 1}"
                slot.mw_marker = ""

    @staticmethod
    def _panel_anchor_scene_point(
        layout: LayoutResult,
        panel_idx: int,
    ) -> tuple[float, float] | None:
        panel_items = [
            item
            for item in layout.items
            if (
                item.kind == "blot"
                and item.source_ref is not None
                and item.source_ref.panel_idx == panel_idx
            )
        ]
        if not panel_items:
            panel_items = [
                item
                for item in layout.items
                if (
                    item.source_ref is not None
                    and item.source_ref.panel_idx == panel_idx
                )
            ]
        if not panel_items:
            return None
        return (
            pt_to_scene(min(item.x_pt for item in panel_items)),
            pt_to_scene(min(item.y_pt for item in panel_items)),
        )

    @staticmethod
    def _overlay_item_center_x(item_data: dict) -> float:
        x = float(item_data.get("x", 0.0))
        if item_data.get("type") == "line":
            return x + (
                float(item_data.get("x1", 0.0))
                + float(item_data.get("x2", 0.0))
            ) / 2.0
        return x + float(item_data.get("width", 0.0)) / 2.0

    @staticmethod
    def _overlay_item_center_y(item_data: dict) -> float:
        y = float(item_data.get("y", 0.0))
        if item_data.get("type") == "line":
            return y + (
                float(item_data.get("y1", 0.0))
                + float(item_data.get("y2", 0.0))
            ) / 2.0
        return y + float(item_data.get("height", 0.0)) / 2.0

    @classmethod
    def _first_panel_overlay_data(
        cls,
        overlay_data: list[dict],
        old_layout: LayoutResult,
        old_panel_count: int,
    ) -> list[dict]:
        if old_panel_count <= 1:
            return [dict(item) for item in overlay_data]

        first_anchor = cls._panel_anchor_scene_point(old_layout, 0)
        second_anchor = cls._panel_anchor_scene_point(old_layout, 1)
        if first_anchor is None or second_anchor is None:
            return [dict(item) for item in overlay_data]
        delta_x = abs(second_anchor[0] - first_anchor[0])
        delta_y = abs(second_anchor[1] - first_anchor[1])
        horizontal = delta_x > delta_y
        upper = (
            (first_anchor[0] + second_anchor[0]) / 2.0
            if horizontal
            else (first_anchor[1] + second_anchor[1]) / 2.0
        )
        return [
            dict(item)
            for item in overlay_data
            if (
                cls._overlay_item_center_x(item)
                if horizontal
                else cls._overlay_item_center_y(item)
            ) < upper
        ]

    @staticmethod
    def _shift_overlay_item(
        item_data: dict,
        delta_x: float,
        delta_y: float,
    ) -> dict:
        shifted = dict(item_data)
        shifted["x"] = float(shifted.get("x", 0.0)) + delta_x
        shifted["y"] = float(shifted.get("y", 0.0)) + delta_y
        return shifted

    def _duplicate_first_panel_overlays_for_new_panels(
        self,
        old_overlay_data: list[dict],
        old_layout: LayoutResult,
        old_panel_count: int,
        new_panel_count: int,
    ) -> None:
        if self._project is None:
            return
        first_panel_overlays = self._first_panel_overlay_data(
            old_overlay_data,
            old_layout,
            old_panel_count,
        )
        if not first_panel_overlays:
            return

        old_first_anchor = self._panel_anchor_scene_point(old_layout, 0)
        if old_first_anchor is None:
            return
        new_layout = self._layout_engine.compute(self._project)
        combined = [dict(item) for item in old_overlay_data]
        for panel_idx in range(old_panel_count, new_panel_count):
            target_anchor = self._panel_anchor_scene_point(
                new_layout,
                panel_idx,
            )
            if target_anchor is None:
                continue
            delta_x = target_anchor[0] - old_first_anchor[0]
            delta_y = target_anchor[1] - old_first_anchor[1]
            combined.extend(
                self._shift_overlay_item(item, delta_x, delta_y)
                for item in first_panel_overlays
            )
        self._canvas._restore_overlay_from_data(combined)

    def _resize_current_project(
        self,
        n_panels: int,
        n_blots: int,
        n_lanes: int,
        table_style: str,
    ) -> None:
        if self._project is None:
            return
        template_id = self._active_template_id or self._project.template_type or "normal_wb"
        skeleton_id = template_id if TemplateEngine.is_builtin(template_id) else "normal_wb"
        skeleton = TemplateEngine.build_project(skeleton_id, n_panels, n_blots, n_lanes)
        old_panels = list(self._project.panels)
        resized_panels: list[Panel] = []

        for pi in range(n_panels):
            if pi < len(old_panels):
                panel = old_panels[pi]
            else:
                blueprint = old_panels[0] if old_panels else skeleton.panels[pi]
                panel = self._clone_panel_structure(
                    blueprint,
                    skeleton.panels[pi],
                    n_blots,
                    n_lanes,
                    table_style,
                )
            if not panel.panel_letter:
                panel.panel_letter = skeleton.panels[pi].panel_letter

            old_slots = list(panel.blot_slots)
            resized_slots: list[BlotSlot] = []
            for si in range(n_blots):
                if si < len(old_slots):
                    slot = old_slots[si]
                else:
                    slot = skeleton.panels[pi].blot_slots[si]
                if slot.lane_count != max(1, n_lanes):
                    slot.lane_crops = []
                slot.lane_count = max(1, n_lanes)
                if slot.bounding_box is not None or slot.lane_rois:
                    slot.reset_equal_lanes()
                resized_slots.append(slot)
            panel.blot_slots = resized_slots
            panel.condition_table = self._resize_condition_table(
                panel.condition_table,
                n_lanes,
                table_style,
            )
            resized_panels.append(panel)

        self._project.panels = resized_panels
        self._project.global_layout.show_condition_table = table_style != "none"

    def _clone_panel_structure(
        self,
        source_panel: Panel,
        skeleton_panel: Panel,
        n_blots: int,
        n_lanes: int,
        table_style: str,
    ) -> Panel:
        panel = copy.deepcopy(source_panel)
        panel.panel_letter = skeleton_panel.panel_letter
        source_slots = list(panel.blot_slots)
        cloned_slots: list[BlotSlot] = []
        for si in range(n_blots):
            if si < len(source_slots):
                slot = source_slots[si]
            elif si < len(skeleton_panel.blot_slots):
                slot = copy.deepcopy(skeleton_panel.blot_slots[si])
            else:
                slot = copy.deepcopy(source_slots[-1]) if source_slots else copy.deepcopy(skeleton_panel.blot_slots[-1])
            slot.source_image_path = ""
            slot.bounding_box = None
            slot.image_transform = None
            slot.geometry_transform = None
            slot.lane_crops = []
            slot.preserve_image_aspect = False
            slot.saved_preview_path = ""
            slot.lane_count = max(1, n_lanes)
            slot.reset_equal_lanes()
            cloned_slots.append(slot)
        panel.blot_slots = cloned_slots
        panel.condition_table = self._resize_condition_table(
            copy.deepcopy(source_panel.condition_table),
            n_lanes,
            table_style,
        )
        return panel

    def _resize_condition_table(
        self,
        table: ConditionTable | None,
        n_lanes: int,
        table_style: str,
    ) -> ConditionTable | None:
        if table_style == "none":
            return None
        if table is None:
            return TemplateEngine.make_condition_table(table_style, n_lanes)

        headers = (list(table.headers) + [f"Lane {i + 1}" for i in range(len(table.headers), n_lanes)])[:n_lanes]
        rows: list[list[str]] = []
        for row in table.rows:
            if row and row[0] == "__span__":
                rows.append(list(row[:2]))
                continue
            if row and row[0] in {
                "__cross_groups__",
                "__cross_groups_level__",
                "__cross_group_space__",
            }:
                rows.append(list(row))
                continue
            if row and row[0] in {"__groups__", "__groups_level__"}:
                group_data_start = 2 if row[0] == "__groups_level__" else 1
                resized_group_row = list(row[:group_data_start])
                for data_index in range(group_data_start, len(row) - 1, 2):
                    try:
                        start_text, end_text = row[data_index].split("-", 1)
                        start = max(1, min(n_lanes, int(start_text)))
                        end = max(start, min(n_lanes, int(end_text)))
                    except (AttributeError, TypeError, ValueError):
                        continue
                    resized_group_row.extend([
                        f"{start}-{end}",
                        row[data_index + 1],
                    ])
                rows.append(resized_group_row)
                continue
            label = row[0] if row else ""
            values = list(row[1:])
            values = (values + [""] * n_lanes)[:n_lanes]
            rows.append([label] + values)
        return ConditionTable(headers=headers, rows=rows)
