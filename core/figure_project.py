"""core/figure_project.py — Data model for WB Plot Figure Generation.

Coordinate-system conventions (strict — never mix between spaces):

  IMAGE_PX  Source image pixels.
            Used in: ImageBBox, BlotSlot.bounding_box.

  REL       Unitless 0.0–1.0 relative to the owning BlotSlot bounding_box.
            Used in: LaneROI.x_offset, LaneROI.width.

  PT        Typographic points (1 pt = 1/72 inch).
            Used in: GlobalLayout fields and all LayoutItem coordinates.
            Conversion to screen pixels or EMU is performed ONLY in
            core/layout_engine.py (pt_to_px, pt_to_emu, rel_to_pt).

This module has zero Qt imports and is fully JSON-serialisable via
dataclasses.asdict().
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


# ── Stable back-reference ────────────────────────────────────────────────────

@dataclass
class SourceRef:
    """Stable identity carried by every editable LayoutItem.

    When the user edits a text item on FigureCanvas the canvas calls
    FigureProject.apply_edit(ref, new_text).  The dispatch happens entirely
    on the *field* discriminator — no render-order or text-matching required.
    """
    panel_idx: int | None = None
    slot_idx:  int | None = None
    lane_idx:  int | None = None
    table_row: int | None = None
    table_col: int | None = None
    # Discriminator for the target model field
    field: Literal[
        "label", "mw_marker", "title", "panel_letter",
        "condition_cell", "condition_line", "metadata_title", "blot", ""
    ] = ""

    def key(self) -> tuple:
        """Hashable identity used by FigureCanvas to persist fine-position
        offsets across re-renders."""
        return (
            self.panel_idx, self.slot_idx, self.lane_idx,
            self.table_row, self.table_col, self.field,
        )


# ── IMAGE_PX coordinate type ─────────────────────────────────────────────────

@dataclass
class ImageBBox:
    """Bounding box in IMAGE_PX coordinates (source image pixels).
    Never convert these values yourself — pass them to layout_engine helpers."""
    x: float
    y: float
    w: float
    h: float

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}

    @classmethod
    def from_dict(cls, d: dict) -> "ImageBBox":
        return cls(
            x=float(d["x"]), y=float(d["y"]),
            w=float(d["w"]), h=float(d["h"]),
        )


# ── REL coordinate type ───────────────────────────────────────────────────────

@dataclass
class LaneROI:
    """One lane slice within a BlotSlot.

    x_offset and width are in REL space (0.0–1.0 relative to
    BlotSlot.bounding_box.w).  Absolute PT conversion is done exclusively
    in layout_engine.py via rel_to_pt().
    """
    lane_index: int
    x_offset: float   # REL — left edge relative to blot bbox left
    width: float       # REL — lane width relative to blot bbox width


# ── Core model objects ────────────────────────────────────────────────────────

@dataclass
class BlotSlot:
    """One blot strip within a Panel."""
    label: str                     # antibody / IB label (right column)
    mw_marker: str                 # MW label (left column), e.g. "50 kDa"
    source_image_path: str         # absolute filesystem path; "" if not yet set
    bounding_box: ImageBBox | None # IMAGE_PX; None until ROI is drawn
    lane_count: int = 4
    lane_rois: list[LaneROI] = field(default_factory=list)
    # Optional equal-size source crops, one per lane. Auto-Fit uses these to
    # align signal anchors by translation while retaining original pixels.
    lane_crops: list[ImageBBox] = field(default_factory=list)
    display_width_pt: float | None = None
    display_height_pt: float | None = None
    image_transform: dict | None = None
    # Lossless display-ready crop embedded in a saved blot file. When present,
    # render/export use it so reopening cannot reinterpret TIFF polarity or
    # tone controls differently from what the user saved.
    saved_preview_path: str = ""

    # ── Convenience helpers ───────────────────────────────────────────────

    def reset_equal_lanes(self) -> None:
        """Reset lane_rois to uniform REL divisions."""
        w = 1.0 / max(self.lane_count, 1)
        self.lane_rois = [
            LaneROI(lane_index=i,
                    x_offset=round(i * w, 8),
                    width=round(w, 8))
            for i in range(self.lane_count)
        ]

    def roi_complete(self) -> bool:
        """True when both a bounding box and at least one lane ROI exist."""
        return self.bounding_box is not None and len(self.lane_rois) > 0

    def image_ready(self) -> bool:
        """True when a source image path is set and the file exists."""
        from pathlib import Path
        return bool(self.source_image_path) and Path(self.source_image_path).exists()


@dataclass
class ConditionTable:
    """Optional condition matrix rendered above blot strips."""
    headers: list[str]      # one per lane — column headers
    rows: list[list[str]]   # each row is one label per lane


@dataclass
class Panel:
    panel_letter: str       # "A", "B", "C" …
    title: str
    blot_slots: list[BlotSlot] = field(default_factory=list)
    condition_table: ConditionTable | None = None


# ── PT coordinate type — layout parameters ───────────────────────────────────

@dataclass
class GlobalLayout:
    """All measurements in PT (1 pt = 1/72 inch).

    Conversion to screen pixels or EMU happens ONLY in core/layout_engine.py.
    Never perform pt↔px arithmetic in any other module.
    """
    # Canvas / margins
    canvas_width_pt: float = 456.0
    panel_padding_pt: float = 12.0
    inter_panel_gap_pt: float = 28.0
    # User-created multi-panel frames can flow left-to-right and use the
    # first panel's antibody labels as one shared column after the last panel.
    panel_layout: Literal["vertical", "horizontal"] = "vertical"
    share_ib_labels: bool = False

    # Blot strip
    blot_height_pt: float = 18.0
    blot_gap_pt: float = 3.0

    # Column widths
    mw_col_width_pt: float = 96.0
    label_col_width_pt: float = 120.0
    ib_label_gap_pt: float = 6.0

    # Spacing
    title_spacing_pt: float = 18.0
    condition_table_row_height_pt: float = 17.0

    # Panel letter offset relative to blot origin (top-left corner of panel)
    panel_letter_offset_x_pt: float = -10.0
    panel_letter_offset_y_pt: float = 0.0

    # Typography — all font sizes in PT
    font_family: str = "Arial"
    label_font_size_pt: float = 14.0
    mw_font_size_pt: float = 13.0
    title_font_size_pt: float = 16.0
    panel_letter_font_size_pt: float = 20.0
    condition_table_font_size_pt: float = 13.0

    # Visibility toggles
    show_mw_labels: bool = True
    show_ib_labels: bool = True
    show_condition_table: bool = True
    show_lane_dividers: bool = False


# ── Top-level project ─────────────────────────────────────────────────────────

@dataclass
class FigureProject:
    template_type: str
    global_layout: GlobalLayout = field(default_factory=GlobalLayout)
    panels: list[Panel] = field(default_factory=list)
    metadata: dict = field(
        default_factory=lambda: {"title": "", "author": "", "date": ""}
    )

    # ── Convenience accessors ─────────────────────────────────────────────

    def get_slot(self, panel_idx: int, slot_idx: int) -> BlotSlot | None:
        try:
            return self.panels[panel_idx].blot_slots[slot_idx]
        except IndexError:
            return None

    # ── Single write-back entry-point ────────────────────────────────────

    def apply_edit(self, ref: SourceRef, new_text: str) -> None:
        """Write a canvas text edit back to the exact model field identified
        by *ref*.  This is the ONLY place the data model is mutated from a
        UI edit event — no other module should write to model fields directly
        in response to canvas interaction.
        """
        if ref.field == "metadata_title":
            self.metadata["title"] = new_text
            return

        if ref.panel_idx is None or ref.panel_idx >= len(self.panels):
            return
        panel = self.panels[ref.panel_idx]

        if ref.field == "panel_letter":
            panel.panel_letter = new_text

        elif ref.field == "title":
            panel.title = new_text

        elif ref.field == "label":
            if ref.slot_idx is not None and ref.slot_idx < len(panel.blot_slots):
                panel.blot_slots[ref.slot_idx].label = new_text

        elif ref.field == "mw_marker":
            if ref.slot_idx is not None and ref.slot_idx < len(panel.blot_slots):
                panel.blot_slots[ref.slot_idx].mw_marker = new_text

        elif ref.field == "condition_cell":
            ct = panel.condition_table
            if (ct is not None
                    and ref.table_row is not None
                    and ref.table_col is not None
                    and ref.table_row < len(ct.rows)
                    and ref.table_col < len(ct.rows[ref.table_row])):
                ct.rows[ref.table_row][ref.table_col] = new_text
