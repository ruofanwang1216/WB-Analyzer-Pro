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

from dataclasses import dataclass, field
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

    def compute(self, project: FigureProject) -> LayoutResult:
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
                for ri, row in enumerate(ct.rows):
                    row_y = current_y + ri * gl.condition_table_row_height_pt
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
                    col_w = panel_blot_width_pt / n_cols
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
                        items.append(LayoutItem(
                            kind="table_cell",
                            x_pt=blot_origin_x + ci * col_w,
                            y_pt=row_y,
                            w_pt=col_w,
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
                crop = slot.bounding_box.to_dict() if slot.bounding_box else None
                items.append(LayoutItem(
                    kind="blot",
                    x_pt=blot_origin_x,
                    y_pt=slot_y,
                    w_pt=slot_blot_width_pt,
                    h_pt=slot_blot_height_pt,
                    image_path=slot.source_image_path or None,
                    image_crop_px=crop,
                    image_transform=slot.image_transform,
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
                        x_pt=blot_origin_x + slot_blot_width_pt + gl.panel_padding_pt,
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
