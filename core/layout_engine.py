"""core/layout_engine.py — Coordinate conversion and layout computation.

THIS IS THE SOLE FILE PERMITTED TO PERFORM ARITHMETIC BETWEEN COORDINATE
SPACES.  All other modules must call the helpers defined here.

Coordinate spaces (see figure_project.py for definitions):
  IMAGE_PX  →  PT  :  px_to_pt(px, source_dpi)
  PT        →  PX  :  pt_to_px(pt, target_dpi)
  PT        →  EMU :  pt_to_emu(pt)          (for PPTX)
  REL       →  PT  :  rel_to_pt(rel, ref_pt) (lane-width conversion)
  PT        → SCENE:  pt_to_scene(pt)        (multiply by SCREEN_SCALE)

No Qt imports — coordinates are plain floats.  Qt-side rendering is done in
gui/figure_canvas.py and gui/export_engine.py using these helpers.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from core.figure_project import FigureProject, GlobalLayout, SourceRef


# ── Coordinate-system constants ───────────────────────────────────────────────

SCREEN_DPI: float = 96.0      # logical screen resolution
EXPORT_DPI: float = 300.0     # PDF/image export resolution
EMU_PER_PT: int = 12700       # PPTX English Metric Units per typographic point

# SCREEN_SCALE converts PT → scene units used by QGraphicsScene.
# 1.5 gives a comfortable on-screen size for a 6.5-inch canvas
# (468 pt × 1.5 = 702 scene-px wide) without being too large.
SCREEN_SCALE: float = 1.5

# WB Plot figure lanes should retain a stable visual width.  The blot strip and
# condition-table columns grow or shrink with lane count instead of stretching a
# small number of lanes across the full canvas width.
DEFAULT_LANE_WIDTH_PT: float = 36.0


# ── Conversion helpers (all cross-space arithmetic lives here) ────────────────

def pt_to_px(pt: float, dpi: float = SCREEN_DPI) -> float:
    """PT → absolute pixels at the given DPI."""
    return pt * dpi / 72.0


def px_to_pt(px: float, dpi: float = SCREEN_DPI) -> float:
    """Absolute pixels → PT at the given DPI."""
    return px * 72.0 / dpi


def pt_to_emu(pt: float) -> int:
    """PT → PPTX English Metric Units (integer)."""
    return int(pt * EMU_PER_PT)


def emu_to_pt(emu: float) -> float:
    """PPTX English Metric Units → PT."""
    return float(emu) / EMU_PER_PT


def rel_to_pt(rel: float, ref_pt: float) -> float:
    """REL (0–1) → PT given a reference dimension in PT.
    Used to convert LaneROI.x_offset / .width to blot-relative PT coords.
    """
    return rel * ref_pt


def pt_to_scene(pt: float) -> float:
    """PT → QGraphicsScene units (SCREEN_SCALE × pt)."""
    return pt * SCREEN_SCALE


def scene_to_pt(scene: float) -> float:
    """QGraphicsScene units → PT."""
    return scene / SCREEN_SCALE


# ── Layout data types ─────────────────────────────────────────────────────────

@dataclass
class LayoutItem:
    """One renderable element.  All positional fields are in PT.

    source_ref is None for non-editable items (blot images, separator lines,
    lane dividers).  Every editable item MUST carry a populated SourceRef so
    that FigureProject.apply_edit() can write back the correct field without
    relying on render order or text matching.
    """
    kind: Literal[
        "blot", "label", "mw", "line", "panel_letter",
        "table_cell", "title", "divider",
    ]
    # Position and size in PT
    x_pt: float
    y_pt: float
    w_pt: float
    h_pt: float

    # Text content (empty for blot/line/divider)
    text: str = ""

    # Image data (only for kind == "blot")
    image_path: str | None = None
    # Crop in IMAGE_PX coordinates; None means use full image
    image_crop_px: dict | None = None   # {"x", "y", "w", "h"}
    image_lane_crops_px: list[dict] | None = None
    # Optional display transform captured from the source WB image viewer.
    image_transform: dict | None = None

    # Typography
    font_family: str = "Arial"
    font_size_pt: float = 7.0
    bold: bool = False
    italic: bool = False
    underline: bool = False
    align: str = "left"    # "left" | "center" | "right"
    rotation: float = 0.0

    # Line styling (only for kind == "line")
    line_color: str = "#AAAAAA"
    line_width_pt: float = 0.5333333333

    # Rendering order (higher = drawn on top)
    z_order: int = 0

    # Back-reference to the data model field this item represents.
    # None for structural / non-editable items.
    source_ref: SourceRef | None = None
    editable: bool = False


@dataclass
class LayoutResult:
    items: list[LayoutItem] = field(default_factory=list)
    canvas_width_pt: float = 468.0
    canvas_height_pt: float = 200.0


# ── Layout engine ─────────────────────────────────────────────────────────────

class LayoutEngine:
    """Computes a flat list of LayoutItems from a FigureProject.

    All positions are in PT.  The same LayoutResult is used by:
      • FigureCanvas   (screen rendering — converts PT → scene via pt_to_scene)
      • PDFExporter    (export — converts PT → px at EXPORT_DPI via pt_to_px)
      • PPTXExporter   (export — converts PT → EMU via pt_to_emu)
    """

    @staticmethod
    def _slot_blot_width_pt(slot, fallback_width_pt: float) -> float:
        if slot.display_width_pt is not None:
            return max(40.0, float(slot.display_width_pt))
        lane_count = max(1, int(getattr(slot, "lane_count", 1) or 1))
        return max(40.0, lane_count * DEFAULT_LANE_WIDTH_PT)

    @staticmethod
    def _condition_lane_cells(panel, lane_count: int) -> list[tuple[float, float]]:
        """Return REL cells whose centres follow an auto-fitted blot's lanes."""
        lane_count = max(1, int(lane_count))
        for slot in panel.blot_slots:
            lane_rois = getattr(slot, "lane_rois", [])
            if (
                len(lane_rois) != lane_count
            ):
                continue
            cells = [
                (
                    max(0.0, min(1.0, float(lane.x_offset))),
                    max(0.0, min(1.0, float(lane.width))),
                )
                for lane in lane_rois
            ]
            equal_width = 1.0 / lane_count
            if all(
                abs(left - index * equal_width) <= 1e-6
                and abs(width - equal_width) <= 1e-6
                for index, (left, width) in enumerate(cells)
            ):
                return [
                    (index * equal_width, equal_width)
                    for index in range(lane_count)
                ]
            centers = [left + width / 2.0 for left, width in cells]
            if all(
                right > left
                for left, right in zip(centers, centers[1:])
            ):
                return cells
        width = 1.0 / lane_count
        return [(index * width, width) for index in range(lane_count)]

    def compute(self, project: FigureProject) -> LayoutResult:
        if (
            project.global_layout.panel_layout == "horizontal"
            and len(project.panels) > 1
        ):
            return self._compute_horizontal(project)
        return self._compute_vertical(project)

    def _compute_horizontal(self, project: FigureProject) -> LayoutResult:
        """Lay panels out in one compact row with an optional shared IB column."""
        gl = project.global_layout
        gap_pt = max(gl.blot_gap_pt, gl.inter_panel_gap_pt * 0.25)
        combined_items: list[LayoutItem] = []
        prepared_panels: list[tuple[int, LayoutResult, list[LayoutItem]]] = []
        blot_tops: list[float] = []

        for panel_index, panel in enumerate(project.panels):
            render_shared_labels = (
                gl.show_ib_labels
                and gl.share_ib_labels
                and panel_index == len(project.panels) - 1
            )
            panel_gl = replace(
                gl,
                panel_layout="vertical",
                show_ib_labels=(
                    render_shared_labels
                    if gl.share_ib_labels
                    else gl.show_ib_labels
                ),
                share_ib_labels=False,
            )
            panel_project = FigureProject(
                template_type=project.template_type,
                global_layout=panel_gl,
                panels=[panel],
                metadata=project.metadata,
            )
            panel_layout = self._compute_vertical(panel_project)
            visible_items = [
                item
                for item in panel_layout.items
                if not (
                    panel_index > 0
                    and gl.share_ib_labels
                    and item.kind == "table_cell"
                    and item.source_ref is not None
                    and item.source_ref.field == "condition_cell"
                    and item.source_ref.table_col == 0
                )
            ]
            prepared_panels.append(
                (panel_index, panel_layout, visible_items)
            )
            panel_blots = [
                item for item in visible_items if item.kind == "blot"
            ]
            if panel_blots:
                blot_tops.append(min(item.y_pt for item in panel_blots))

        shared_blot_top = max(blot_tops, default=gl.panel_padding_pt)
        next_blot_x: float | None = None

        for panel_index, panel_layout, visible_items in prepared_panels:
            if not visible_items:
                continue

            panel_blots = [
                item for item in visible_items if item.kind == "blot"
            ]
            if panel_blots:
                blot_left = min(item.x_pt for item in panel_blots)
                blot_right = max(
                    item.x_pt + item.w_pt for item in panel_blots
                )
                visible_left = min(
                    item.x_pt
                    for item in visible_items
                    if item.kind != "label"
                )
                if next_blot_x is None:
                    desired_blot_left = (
                        gl.panel_padding_pt
                        + max(0.0, blot_left - visible_left)
                    )
                else:
                    desired_blot_left = next_blot_x
                delta_x = desired_blot_left - blot_left
                next_blot_x = (
                    desired_blot_left
                    + (blot_right - blot_left)
                    + gap_pt
                )
                panel_blot_top = min(item.y_pt for item in panel_blots)
                delta_y = shared_blot_top - panel_blot_top
            else:
                structural_left = min(
                    item.x_pt for item in visible_items
                )
                structural_right = max(
                    item.x_pt + item.w_pt for item in visible_items
                )
                desired_left = (
                    next_blot_x
                    if next_blot_x is not None
                    else gl.panel_padding_pt
                )
                delta_x = desired_left - structural_left
                next_blot_x = (
                    desired_left
                    + (structural_right - structural_left)
                    + gap_pt
                )
                delta_y = 0.0

            for item in visible_items:
                source_ref = item.source_ref
                item_text = item.text
                if source_ref is not None:
                    source_panel_index = panel_index
                    if item.kind == "label" and gl.share_ib_labels:
                        source_panel_index = 0
                        slot_index = source_ref.slot_idx
                        if (
                            slot_index is not None
                            and project.panels
                            and slot_index < len(project.panels[0].blot_slots)
                        ):
                            item_text = project.panels[0].blot_slots[
                                slot_index
                            ].label
                    source_ref = replace(
                        source_ref,
                        panel_idx=source_panel_index,
                    )
                combined_items.append(
                    replace(
                        item,
                        x_pt=item.x_pt + delta_x,
                        y_pt=item.y_pt + delta_y,
                        text=item_text,
                        source_ref=source_ref,
                    )
                )

        combined_items.extend(
            self._cross_panel_condition_items(project, combined_items)
        )

        canvas_width_pt = gl.canvas_width_pt
        canvas_height_pt = gl.panel_padding_pt * 2.0
        if combined_items:
            canvas_width_pt = max(
                canvas_width_pt,
                max(item.x_pt + item.w_pt for item in combined_items)
                + gl.panel_padding_pt,
            )
            canvas_height_pt = max(
                canvas_height_pt,
                max(item.y_pt + item.h_pt for item in combined_items)
                + gl.panel_padding_pt,
            )
        return LayoutResult(
            items=combined_items,
            canvas_width_pt=canvas_width_pt,
            canvas_height_pt=canvas_height_pt,
        )

    @staticmethod
    def _cross_panel_condition_items(
        project: FigureProject,
        combined_items: list[LayoutItem],
    ) -> list[LayoutItem]:
        """Render shared group rows after horizontal panel positions are known."""
        gl = project.global_layout
        result: list[LayoutItem] = []

        def lane_cell(panel_index: int, lane_number: int) -> LayoutItem | None:
            candidates = [
                item for item in combined_items
                if (
                    item.kind == "table_cell"
                    and item.source_ref is not None
                    and item.source_ref.panel_idx == panel_index
                    and item.source_ref.field == "condition_cell"
                    and item.source_ref.table_col == lane_number
                    and item.source_ref.table_row is not None
                )
            ]
            return min(
                candidates,
                key=lambda item: item.source_ref.table_row,
                default=None,
            )

        for owner_panel_index, panel in enumerate(project.panels):
            table = panel.condition_table
            if table is None:
                continue
            owner_cells = [
                item for item in combined_items
                if (
                    item.kind == "table_cell"
                    and item.source_ref is not None
                    and item.source_ref.panel_idx == owner_panel_index
                    and item.source_ref.field == "condition_cell"
                    and item.source_ref.table_row is not None
                    and item.source_ref.table_col is not None
                    and item.source_ref.table_col > 0
                )
            ]
            if not owner_cells:
                continue
            base_y = min(
                item.y_pt
                - item.source_ref.table_row * gl.condition_table_row_height_pt
                for item in owner_cells
            )
            for row_index, row in enumerate(table.rows):
                if not row or row[0] not in {
                    "__cross_groups__", "__cross_groups_level__"
                }:
                    continue
                data_start = 2 if row[0] == "__cross_groups_level__" else 1
                row_y = base_y + row_index * gl.condition_table_row_height_pt
                for data_index in range(data_start, len(row) - 1, 2):
                    try:
                        start_text, end_text = row[data_index].split("-", 1)
                        start_panel_text, start_lane_text = start_text.split(":", 1)
                        end_panel_text, end_lane_text = end_text.split(":", 1)
                        start_panel = int(start_panel_text)
                        start_lane = int(start_lane_text)
                        end_panel = int(end_panel_text)
                        end_lane = int(end_lane_text)
                    except (AttributeError, TypeError, ValueError):
                        continue
                    first_cell = lane_cell(start_panel, start_lane)
                    last_cell = lane_cell(end_panel, end_lane)
                    if first_cell is None or last_cell is None:
                        continue
                    group_x = first_cell.x_pt
                    group_w = last_cell.x_pt + last_cell.w_pt - group_x
                    if group_w <= 0:
                        continue
                    title_col = data_index + 1
                    title_w = max(group_w, 52.0)
                    title_x = group_x - (title_w - group_w) / 2.0
                    line_inset = max(5.0, min(10.0, group_w * 0.10))
                    source_ref = SourceRef(
                        panel_idx=owner_panel_index,
                        table_row=row_index,
                        table_col=title_col,
                        field="condition_cell",
                    )
                    result.append(LayoutItem(
                        kind="table_cell",
                        x_pt=title_x,
                        y_pt=row_y - 3.0,
                        w_pt=title_w,
                        h_pt=gl.condition_table_row_height_pt,
                        text=row[title_col],
                        font_family=gl.font_family,
                        font_size_pt=12.0,
                        bold=True,
                        align="center",
                        z_order=7,
                        source_ref=source_ref,
                        editable=True,
                    ))
                    result.append(LayoutItem(
                        kind="line",
                        x_pt=group_x + line_inset,
                        y_pt=row_y + gl.condition_table_row_height_pt - 4.0,
                        w_pt=max(1.0, group_w - 2.0 * line_inset),
                        h_pt=0.0,
                        line_color="#111111",
                        line_width_pt=1.0,
                        z_order=6,
                        source_ref=SourceRef(
                            panel_idx=owner_panel_index,
                            table_row=row_index,
                            table_col=title_col,
                            field="condition_line",
                        ),
                        editable=True,
                    ))
        return result

    def _compute_vertical(self, project: FigureProject) -> LayoutResult:
        gl = project.global_layout
        items: list[LayoutItem] = []

        # ── Derived column geometry (all in PT) ──────────────────────────
        blot_origin_x = gl.mw_col_width_pt + gl.panel_padding_pt
        # Blot strips occupy whatever remains after MW + IB label columns
        blot_width_pt = (
            gl.canvas_width_pt
            - blot_origin_x
            - gl.label_col_width_pt
            - gl.panel_padding_pt
        )
        blot_width_pt = max(blot_width_pt, 60.0)   # never collapse to nothing

        current_y = gl.panel_padding_pt

        for pi, panel in enumerate(project.panels):
            panel_blot_width_pt = max(
                (
                    self._slot_blot_width_pt(slot, blot_width_pt)
                    for slot in panel.blot_slots
                ),
                default=blot_width_pt,
            )
            if panel.condition_table is not None:
                panel_blot_width_pt = max(
                    panel_blot_width_pt,
                    len(panel.condition_table.headers) * DEFAULT_LANE_WIDTH_PT,
                )

            # ── Panel title ──────────────────────────────────────────────
            if panel.title:
                items.append(LayoutItem(
                    kind="title",
                    x_pt=blot_origin_x,
                    y_pt=current_y,
                    w_pt=panel_blot_width_pt,
                    h_pt=gl.title_spacing_pt,
                    text=panel.title,
                    font_family=gl.font_family,
                    font_size_pt=gl.title_font_size_pt,
                    bold=True,
                    z_order=5,
                    source_ref=SourceRef(panel_idx=pi, field="title"),
                    editable=True,
                ))
                current_y += gl.title_spacing_pt

            # ── Condition table ──────────────────────────────────────────
            if (panel.condition_table is not None
                    and gl.show_condition_table):
                ct = panel.condition_table
                condition_cells = self._condition_lane_cells(
                    panel,
                    len(ct.headers),
                )
                for ri, row in enumerate(ct.rows):
                    row_y = current_y + ri * gl.condition_table_row_height_pt
                    if row and row[0] in {
                        "__cross_groups__",
                        "__cross_groups_level__",
                        "__cross_group_space__",
                    }:
                        continue
                    if row and row[0] in {"__groups__", "__groups_level__"}:
                        group_data_start = (
                            2 if row[0] == "__groups_level__" else 1
                        )
                        for group_data_index in range(
                            group_data_start, len(row) - 1, 2
                        ):
                            try:
                                start_text, end_text = row[group_data_index].split("-", 1)
                                start_lane = max(1, int(start_text))
                                end_lane = min(len(ct.headers), int(end_text))
                            except (TypeError, ValueError):
                                continue
                            if end_lane < start_lane:
                                continue
                            group_left, _first_width = condition_cells[start_lane - 1]
                            last_left, last_width = condition_cells[end_lane - 1]
                            group_x = blot_origin_x + group_left * panel_blot_width_pt
                            group_w = (
                                last_left + last_width - group_left
                            ) * panel_blot_width_pt
                            title_col = group_data_index + 1
                            line_inset = max(5.0, min(10.0, group_w * 0.10))
                            title_w = max(group_w, 52.0)
                            title_x = group_x - (title_w - group_w) / 2.0
                            items.append(LayoutItem(
                                kind="table_cell",
                                x_pt=title_x,
                                y_pt=row_y - 3.0,
                                w_pt=title_w,
                                h_pt=gl.condition_table_row_height_pt,
                                text=row[title_col],
                                font_family=gl.font_family,
                                font_size_pt=12.0,
                                bold=True,
                                align="center",
                                z_order=6,
                                source_ref=SourceRef(
                                    panel_idx=pi,
                                    table_row=ri,
                                    table_col=title_col,
                                    field="condition_cell",
                                ),
                                editable=True,
                            ))
                            items.append(LayoutItem(
                                kind="line",
                                x_pt=group_x + line_inset,
                                y_pt=(
                                    row_y
                                    + gl.condition_table_row_height_pt
                                    - 4.0
                                ),
                                w_pt=max(1.0, group_w - 2.0 * line_inset),
                                h_pt=0.0,
                                line_color="#111111",
                                line_width_pt=1.0,
                                z_order=5,
                                source_ref=SourceRef(
                                    panel_idx=pi,
                                    table_row=ri,
                                    table_col=title_col,
                                    field="condition_line",
                                ),
                                editable=True,
                            ))
                        continue
                    if row and row[0] == "__span__":
                        items.append(LayoutItem(
                            kind="table_cell",
                            x_pt=blot_origin_x,
                            y_pt=row_y,
                            w_pt=panel_blot_width_pt,
                            h_pt=gl.condition_table_row_height_pt,
                            text=row[1] if len(row) > 1 else "",
                            font_family=gl.font_family,
                            font_size_pt=gl.condition_table_font_size_pt,
                            bold=True,
                            align="center",
                            z_order=6,
                            source_ref=SourceRef(
                                panel_idx=pi,
                                table_row=ri,
                                table_col=1,
                                field="condition_cell",
                            ),
                            editable=True,
                        ))
                        continue

                    label = row[0] if row else ""
                    values = row[1:] if len(row) > 1 else [""] * max(len(ct.headers), 1)
                    n_cols = max(len(values), 1)
                    row_cells = (
                        condition_cells
                        if len(condition_cells) == n_cols
                        else self._condition_lane_cells(panel, n_cols)
                    )
                    items.append(LayoutItem(
                        kind="table_cell",
                        x_pt=0.0,
                        y_pt=row_y,
                        w_pt=max(10.0, blot_origin_x - 3.0),
                        h_pt=gl.condition_table_row_height_pt,
                        text=label,
                        font_family=gl.font_family,
                        font_size_pt=gl.condition_table_font_size_pt,
                        bold=True,
                        align="right",
                        z_order=6,
                        source_ref=SourceRef(
                            panel_idx=pi,
                            table_row=ri,
                            table_col=0,
                            field="condition_cell",
                        ),
                        editable=True,
                    ))
                    for ci, cell_text in enumerate(values):
                        cell_left, cell_width = row_cells[ci]
                        items.append(LayoutItem(
                            kind="table_cell",
                            x_pt=blot_origin_x + cell_left * panel_blot_width_pt,
                            y_pt=row_y,
                            w_pt=cell_width * panel_blot_width_pt,
                            h_pt=gl.condition_table_row_height_pt,
                            text=cell_text,
                            font_family=gl.font_family,
                            font_size_pt=gl.condition_table_font_size_pt,
                            align="center",
                            z_order=4,
                            source_ref=SourceRef(
                                panel_idx=pi,
                                table_row=ri,
                                table_col=ci + 1,
                                field="condition_cell",
                            ),
                            editable=True,
                        ))
                n_rows = len(ct.rows)
                current_y += n_rows * gl.condition_table_row_height_pt + 3.0

            # ── Blot strips ──────────────────────────────────────────────
            for si, slot in enumerate(panel.blot_slots):
                slot_y = current_y
                slot_blot_width_pt = self._slot_blot_width_pt(slot, blot_width_pt)
                slot_blot_height_pt = max(10.0, slot.display_height_pt or gl.blot_height_pt)

                # MW label (left column)
                if gl.show_mw_labels:
                    items.append(LayoutItem(
                        kind="mw",
                        x_pt=0.0,
                        y_pt=slot_y,
                        w_pt=gl.mw_col_width_pt,
                        h_pt=slot_blot_height_pt,
                        text=slot.mw_marker,
                        font_family=gl.font_family,
                        font_size_pt=gl.mw_font_size_pt,
                        align="right",
                        z_order=5,
                        source_ref=SourceRef(
                            panel_idx=pi, slot_idx=si, field="mw_marker"
                        ),
                        editable=True,
                    ))

                # Blot image / placeholder
                saved_preview = (
                    slot.saved_preview_path
                    if slot.saved_preview_path
                    and Path(slot.saved_preview_path).exists()
                    else ""
                )
                crop = (
                    None
                    if saved_preview
                    else slot.bounding_box.to_dict() if slot.bounding_box else None
                )
                items.append(LayoutItem(
                    kind="blot",
                    x_pt=blot_origin_x,
                    y_pt=slot_y,
                    w_pt=slot_blot_width_pt,
                    h_pt=slot_blot_height_pt,
                    image_path=saved_preview or slot.source_image_path or None,
                    image_crop_px=crop,
                    image_lane_crops_px=(
                        None
                        if saved_preview
                        else [
                            lane_crop.to_dict() for lane_crop in slot.lane_crops
                        ] or None
                    ),
                    image_transform=(
                        {
                            "low": 0,
                            "high": 65535,
                            "gamma": 1.0,
                            "inverted": False,
                        }
                        if saved_preview
                        else slot.image_transform
                    ),
                    z_order=1,
                    source_ref=SourceRef(panel_idx=pi, slot_idx=si, field="blot"),
                    editable=False,
                ))

                # Lane dividers (optional overlay)
                if gl.show_lane_dividers and slot.lane_rois:
                    for lr in slot.lane_rois[1:]:   # skip left edge
                        div_x = blot_origin_x + rel_to_pt(lr.x_offset, slot_blot_width_pt)
                        items.append(LayoutItem(
                            kind="divider",
                            x_pt=div_x,
                            y_pt=slot_y,
                            w_pt=0.4,
                            h_pt=slot_blot_height_pt,
                            z_order=2,
                            source_ref=None,
                            editable=False,
                        ))

                # IB / antibody label (right column)
                if gl.show_ib_labels:
                    items.append(LayoutItem(
                        kind="label",
                        x_pt=blot_origin_x + slot_blot_width_pt + gl.ib_label_gap_pt,
                        y_pt=slot_y,
                        w_pt=gl.label_col_width_pt,
                        h_pt=slot_blot_height_pt,
                        text=slot.label,
                        font_family=gl.font_family,
                        font_size_pt=gl.label_font_size_pt,
                        italic=True,
                        align="left",
                        z_order=5,
                        source_ref=SourceRef(
                            panel_idx=pi, slot_idx=si, field="label"
                        ),
                        editable=True,
                    ))

                current_y += slot_blot_height_pt
                if si < len(panel.blot_slots) - 1:
                    current_y += gl.blot_gap_pt

            if pi < len(project.panels) - 1:
                current_y += gl.inter_panel_gap_pt * 0.25

        current_y += gl.panel_padding_pt
        text_kinds = {"label", "mw", "title", "panel_letter", "table_cell"}
        items = [
            item for item in items
            if not (item.kind in text_kinds and not item.text.strip())
        ]
        canvas_width = gl.canvas_width_pt
        if items:
            canvas_width = max(
                canvas_width,
                max(item.x_pt + item.w_pt for item in items) + gl.panel_padding_pt,
            )

        return LayoutResult(
            items=items,
            canvas_width_pt=canvas_width,
            canvas_height_pt=current_y,
        )
