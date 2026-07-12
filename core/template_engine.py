"""core/template_engine.py — Template definitions, user templates, and project factory.

Each template provides:
  * Structural defaults (panel count, blot count, lane count, condition table)
  * A full set of GlobalLayout overrides (spacing, column widths, font sizes, …)

User templates are saved to USER_TEMPLATES_DIR (~/.wb_analyzer/templates/) and
loaded at startup.  They preserve layout settings, panel/blot structure, labels,
and overlay annotations — but NOT source image paths or ROIs.

TemplateEngine.build_project() merges overrides into the base GlobalLayout
and returns a FigureProject with empty BlotSlot stubs ready for the user
to fill in images and ROIs.

No Qt imports.  No shared state with the densitometry pipeline.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from core.figure_project import (
    BlotSlot, ConditionTable, FigureProject, GlobalLayout, Panel,
)

# Directory where user-created templates are stored
USER_TEMPLATES_DIR: Path = Path.home() / ".wb_analyzer" / "templates"
HIDDEN_BUILTIN_TEMPLATES_PATH: Path = USER_TEMPLATES_DIR / "_hidden_builtin_templates.json"


# ── Template definition ───────────────────────────────────────────────────────

@dataclass
class TemplateDefinition:
    id: str
    display_name: str
    description: str
    default_panel_count: int
    default_blot_count: int    # blots per panel
    default_lane_count: int
    has_condition_table: bool
    # Keys must match GlobalLayout field names exactly.
    # Values override the base GlobalLayout defaults for this template.
    layout_overrides: dict = field(default_factory=dict)
    # True for templates created by the user (stored on disk)
    is_user_template: bool = False


# ── Built-in template registry ────────────────────────────────────────────────

TEMPLATES: dict[str, TemplateDefinition] = {

    "normal_wb": TemplateDefinition(
        id="normal_wb",
        display_name="Normal WB",
        description=(
            "Standard Western blot — one panel, stacked blot strips, "
            "MW labels on the left, antibody labels on the right."
        ),
        default_panel_count=1,
        default_blot_count=3,
        default_lane_count=4,
        has_condition_table=False,
        layout_overrides={},      # use all GlobalLayout defaults
    ),

    "ip_coip": TemplateDefinition(
        id="ip_coip",
        display_name="IP / Co-IP",
        description=(
            "Immunoprecipitation figure with paired Input and IP blots. "
            "Wider MW column for heavier molecular-weight markers; "
            "increased blot gap to visually separate Input from IP rows."
        ),
        default_panel_count=1,
        default_blot_count=2,    # Input row + IP row
        default_lane_count=4,
        has_condition_table=False,
        layout_overrides={
            "mw_col_width_pt": 96.0,
            "blot_gap_pt": 5.0,
            "label_col_width_pt": 120.0,
            "blot_height_pt": 20.0,
        },
    ),

    "dose_response": TemplateDefinition(
        id="dose_response",
        display_name="Dose-Response WB",
        description=(
            "Blots with a dose/treatment condition matrix rendered above the lanes. "
            "More lanes by default; taller condition-table rows for readability."
        ),
        default_panel_count=1,
        default_blot_count=2,
        default_lane_count=6,
        has_condition_table=True,
        layout_overrides={
            "condition_table_row_height_pt": 17.0,
            "condition_table_font_size_pt": 13.0,
            "title_spacing_pt": 18.0,
            "canvas_width_pt": 456.0,
        },
    ),

    "multi_panel": TemplateDefinition(
        id="multi_panel",
        display_name="Multi-Panel (A / B / C)",
        description=(
            "Three independent panels (A, B, C) with separate blot sets and titles. "
            "Larger inter-panel gap and bolder panel-letter font for publication figures."
        ),
        default_panel_count=3,
        default_blot_count=2,
        default_lane_count=4,
        has_condition_table=False,
        layout_overrides={
            "inter_panel_gap_pt": 36.0,
            "panel_letter_font_size_pt": 20.0,
            "panel_letter_offset_x_pt": -12.0,
            "panel_padding_pt": 14.0,
        },
    ),
}


# ── Engine ────────────────────────────────────────────────────────────────────

class TemplateEngine:
    """Factory that builds a FigureProject from a template and user parameters."""

    _PANEL_LETTERS = "ABCDEFGHIJ"
    # In-memory cache of user templates loaded from disk
    _user_templates: dict[str, TemplateDefinition] = {}
    _hidden_builtin_templates: set[str] = set()

    # ── User template lifecycle ───────────────────────────────────────────

    @classmethod
    def load_user_templates(cls) -> None:
        """Scan USER_TEMPLATES_DIR and load all valid user template JSON files."""
        cls._user_templates.clear()
        cls._hidden_builtin_templates.clear()
        if HIDDEN_BUILTIN_TEMPLATES_PATH.exists():
            try:
                raw = json.loads(HIDDEN_BUILTIN_TEMPLATES_PATH.read_text(encoding="utf-8"))
                cls._hidden_builtin_templates = {
                    str(tid) for tid in raw.get("hidden_builtin_templates", [])
                    if str(tid) in TEMPLATES
                }
            except Exception:
                cls._hidden_builtin_templates.clear()
        if not USER_TEMPLATES_DIR.exists():
            return
        for path in sorted(USER_TEMPLATES_DIR.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("format") != "wb_user_template":
                    continue
                tmpl_id = str(data["id"])
                panels = data.get("panels", [])
                max_blots = max((len(p.get("blots", [])) for p in panels), default=1)
                cls._user_templates[tmpl_id] = TemplateDefinition(
                    id=tmpl_id,
                    display_name=str(data.get("name", tmpl_id)),
                    description=f"User template: {data.get('name', tmpl_id)}",
                    default_panel_count=len(panels) or 1,
                    default_blot_count=max_blots,
                    default_lane_count=int(data.get("lane_count", 4)),
                    has_condition_table=bool(data.get("show_condition_table", False)),
                    layout_overrides={},
                    is_user_template=True,
                )
            except Exception:
                pass

    @classmethod
    def save_user_template(
        cls,
        name: str,
        project: FigureProject,
        overlay_items_data: list[dict],
        canvas_state: dict | None = None,
        text_style_overrides: dict[tuple, dict] | None = None,
    ) -> TemplateDefinition:
        """Serialize the current figure project (without images) as a user template.

        Returns the new TemplateDefinition that was added to the in-memory registry.
        """
        USER_TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "template"
        tmpl_id = f"user_{slug}_{int(time.time())}"
        data = cls._build_user_template_payload(
            tmpl_id,
            name,
            project,
            overlay_items_data,
            canvas_state=canvas_state,
            text_style_overrides=text_style_overrides,
            created=time.strftime("%Y-%m-%d"),
        )

        (USER_TEMPLATES_DIR / f"{tmpl_id}.json").write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )

        tmpl = cls._definition_from_user_template_payload(data)
        cls._user_templates[tmpl_id] = tmpl
        return tmpl

    @classmethod
    def update_user_template(
        cls,
        template_id: str,
        project: FigureProject,
        overlay_items_data: list[dict],
        canvas_state: dict | None = None,
        text_style_overrides: dict[tuple, dict] | None = None,
    ) -> TemplateDefinition:
        """Replace a user template's saved contents without creating a new id."""
        if template_id in TEMPLATES:
            raise ValueError("Built-in templates cannot be overwritten.")

        path = USER_TEMPLATES_DIR / f"{template_id}.json"
        if not path.exists():
            raise KeyError(f"Unknown template: {template_id!r}")

        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing.get("format") != "wb_user_template":
            raise ValueError(f"Not a user template: {template_id!r}")

        name = str(existing.get("name", template_id))
        data = cls._build_user_template_payload(
            template_id,
            name,
            project,
            overlay_items_data,
            canvas_state=canvas_state,
            text_style_overrides=text_style_overrides,
            created=str(existing.get("created", time.strftime("%Y-%m-%d"))),
        )
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        tmpl = cls._definition_from_user_template_payload(data)
        cls._user_templates[template_id] = tmpl
        return tmpl

    @classmethod
    def _build_user_template_payload(
        cls,
        tmpl_id: str,
        name: str,
        project: FigureProject,
        overlay_items_data: list[dict],
        canvas_state: dict | None = None,
        text_style_overrides: dict[tuple, dict] | None = None,
        created: str | None = None,
    ) -> dict:
        gl_data = asdict(project.global_layout)

        panels_data: list[dict] = []
        for panel in project.panels:
            blots_data = [
                {
                    "label": slot.label,
                    "mw_marker": slot.mw_marker,
                    "lane_count": slot.lane_count,
                    "display_width_pt": slot.display_width_pt,
                    "display_height_pt": slot.display_height_pt,
                    "image_transform": slot.image_transform,
                }
                for slot in panel.blot_slots
            ]
            ct_data = None
            if panel.condition_table is not None:
                ct_data = {
                    "headers": panel.condition_table.headers,
                    "rows": panel.condition_table.rows,
                }
            panels_data.append({
                "panel_letter": panel.panel_letter,
                "title": panel.title,
                "blots": blots_data,
                "condition_table": ct_data,
            })

        lane_count = 4
        if project.panels and project.panels[0].blot_slots:
            lane_count = project.panels[0].blot_slots[0].lane_count

        data = {
            "format": "wb_user_template",
            "version": 2,
            "id": tmpl_id,
            "name": name,
            "created": created or time.strftime("%Y-%m-%d"),
            "global_layout": gl_data,
            "panels": panels_data,
            "lane_count": lane_count,
            "show_condition_table": project.global_layout.show_condition_table,
            "overlay_items": overlay_items_data,
            "canvas_state": canvas_state or {},
            "text_style_overrides": cls._encode_text_style_overrides(
                text_style_overrides or {}
            ),
        }
        return data

    @staticmethod
    def _definition_from_user_template_payload(data: dict) -> TemplateDefinition:
        panels_data = data.get("panels", [])
        name = str(data.get("name", data.get("id", "template")))
        return TemplateDefinition(
            id=str(data["id"]),
            display_name=name,
            description=f"User template: {name}",
            default_panel_count=len(panels_data),
            default_blot_count=max((len(p["blots"]) for p in panels_data), default=1),
            default_lane_count=int(data.get("lane_count", 4)),
            has_condition_table=data["show_condition_table"],
            layout_overrides={},
            is_user_template=True,
        )

    @staticmethod
    def _encode_text_style_overrides(styles: dict[tuple, dict]) -> list[dict]:
        return [
            {"key": list(key), "style": dict(style)}
            for key, style in styles.items()
        ]

    @staticmethod
    def _decode_text_style_overrides(data: list[dict]) -> dict[tuple, dict]:
        result: dict[tuple, dict] = {}
        for entry in data:
            key = tuple(entry.get("key", []))
            style = entry.get("style", {})
            if key and isinstance(style, dict):
                result[key] = dict(style)
        return result

    @staticmethod
    def restore_user_template_format_state(template_id: str) -> dict:
        """Restore non-model formatting captured with a user template."""
        path = USER_TEMPLATES_DIR / f"{template_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        return {
            "canvas_state": data.get("canvas_state", {}),
            "text_style_overrides": TemplateEngine._decode_text_style_overrides(
                data.get("text_style_overrides", [])
            ),
        }

    @classmethod
    def delete_user_template(cls, template_id: str) -> None:
        """Remove a user template from disk and from the in-memory cache."""
        path = USER_TEMPLATES_DIR / f"{template_id}.json"
        if path.exists():
            path.unlink()
        cls._user_templates.pop(template_id, None)

    @classmethod
    def rename_user_template(cls, template_id: str, name: str) -> TemplateDefinition:
        """Rename a user template without changing its stable template id."""
        name = name.strip()
        if not name:
            raise ValueError("Template name cannot be empty.")
        if template_id in TEMPLATES:
            raise ValueError("Built-in templates cannot be renamed.")

        path = USER_TEMPLATES_DIR / f"{template_id}.json"
        if not path.exists():
            raise KeyError(f"Unknown template: {template_id!r}")

        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("format") != "wb_user_template":
            raise ValueError(f"Not a user template: {template_id!r}")
        data["name"] = name
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        tmpl = cls._user_templates.get(template_id)
        if tmpl is None:
            cls.load_user_templates()
            tmpl = cls._user_templates.get(template_id)
        if tmpl is None:
            raise KeyError(f"Unknown template: {template_id!r}")

        tmpl.display_name = name
        tmpl.description = f"User template: {name}"
        return tmpl

    @classmethod
    def hide_builtin_template(cls, template_id: str) -> None:
        """Hide a built-in template from the Step 1 template list."""
        if template_id not in TEMPLATES:
            return
        cls._hidden_builtin_templates.add(template_id)
        USER_TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
        HIDDEN_BUILTIN_TEMPLATES_PATH.write_text(
            json.dumps(
                {"hidden_builtin_templates": sorted(cls._hidden_builtin_templates)},
                indent=2,
            ),
            encoding="utf-8",
        )

    @staticmethod
    def restore_user_project(template_id: str) -> tuple[FigureProject, list[dict]]:
        """Restore a FigureProject from a user template.

        Returns (project, overlay_items_data).  Image paths and ROIs are NOT
        restored — the user must re-apply their WB images after loading.
        """
        path = USER_TEMPLATES_DIR / f"{template_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))

        gl_raw = data.get("global_layout", {})
        base = GlobalLayout()
        gl = GlobalLayout(**{k: v for k, v in gl_raw.items() if hasattr(base, k)})

        panels: list[Panel] = []
        for pd in data.get("panels", []):
            blots: list[BlotSlot] = []
            for bd in pd.get("blots", []):
                slot = BlotSlot(
                    label=str(bd.get("label", "IB: Protein")),
                    mw_marker=str(bd.get("mw_marker", "50 kDa")),
                    source_image_path="",
                    bounding_box=None,
                    lane_count=int(bd.get("lane_count", 4)),
                )
                slot.reset_equal_lanes()
                if bd.get("display_width_pt") is not None:
                    slot.display_width_pt = float(bd["display_width_pt"])
                if bd.get("display_height_pt") is not None:
                    slot.display_height_pt = float(bd["display_height_pt"])
                if isinstance(bd.get("image_transform"), dict):
                    slot.image_transform = dict(bd["image_transform"])
                blots.append(slot)

            ct = None
            ct_data = pd.get("condition_table")
            if ct_data:
                ct = ConditionTable(
                    headers=ct_data.get("headers", []),
                    rows=ct_data.get("rows", []),
                )

            panels.append(Panel(
                panel_letter=str(pd.get("panel_letter", "A")),
                title=str(pd.get("title", "")),
                blot_slots=blots,
                condition_table=ct,
            ))

        project = FigureProject(
            template_type=template_id,
            global_layout=gl,
            panels=panels,
        )
        return project, data.get("overlay_items", [])

    # ── Template access ───────────────────────────────────────────────────

    @staticmethod
    def get_template(template_id: str) -> TemplateDefinition:
        if template_id in TEMPLATES:
            return TEMPLATES[template_id]
        if template_id in TemplateEngine._user_templates:
            return TemplateEngine._user_templates[template_id]
        raise KeyError(f"Unknown template: {template_id!r}")

    @staticmethod
    def all_templates() -> list[TemplateDefinition]:
        builtins = [
            tmpl for tid, tmpl in TEMPLATES.items()
            if tid not in TemplateEngine._hidden_builtin_templates
        ]
        return builtins + list(TemplateEngine._user_templates.values())

    @staticmethod
    def is_builtin(template_id: str) -> bool:
        return template_id in TEMPLATES

    # ── Project factory ───────────────────────────────────────────────────

    @staticmethod
    def build_project(
        template_id: str,
        panel_count: int | None = None,
        blot_count: int | None = None,
        lane_count: int | None = None,
    ) -> FigureProject:
        """Return a new FigureProject with empty BlotSlot stubs.

        For user templates the structural data is restored from disk; the
        panel_count / blot_count / lane_count overrides are ignored so the
        saved layout is reproduced faithfully (images are still empty).
        """
        if template_id in TemplateEngine._user_templates:
            project, _ = TemplateEngine.restore_user_project(template_id)
            return project

        tmpl = TEMPLATES[template_id]
        n_panels = panel_count if panel_count is not None else tmpl.default_panel_count
        n_blots  = blot_count  if blot_count  is not None else tmpl.default_blot_count
        n_lanes  = lane_count  if lane_count  is not None else tmpl.default_lane_count

        # Build GlobalLayout: start from base defaults, apply template overrides
        gl = GlobalLayout()
        for key, value in tmpl.layout_overrides.items():
            if hasattr(gl, key):
                setattr(gl, key, value)
        gl.show_condition_table = tmpl.has_condition_table

        # Build panels
        panels: list[Panel] = []
        letters = TemplateEngine._PANEL_LETTERS
        for pi in range(n_panels):
            slots = TemplateEngine._make_slots(tmpl, pi, n_blots, n_lanes)
            ct = (
                TemplateEngine.make_condition_table(
                    "group_dose" if tmpl.id == "dose_response" else "vector_matrix",
                    n_lanes,
                )
                if tmpl.has_condition_table
                else None
            )
            panels.append(Panel(
                panel_letter=letters[pi] if pi < len(letters) else str(pi + 1),
                title="",
                blot_slots=slots,
                condition_table=ct,
            ))

        return FigureProject(
            template_type=template_id,
            global_layout=gl,
            panels=panels,
        )

    # ── Private helpers ───────────────────────────────────────────────────

    @staticmethod
    def _make_slots(
        tmpl: TemplateDefinition,
        panel_idx: int,
        n_blots: int,
        n_lanes: int,
    ) -> list[BlotSlot]:
        slots = []
        for bi in range(n_blots):
            label, mw = TemplateEngine._default_slot_labels(tmpl.id, bi)
            slot = BlotSlot(
                label=label,
                mw_marker=mw,
                source_image_path="",
                bounding_box=None,
                lane_count=n_lanes,
                lane_rois=[],
            )
            slot.reset_equal_lanes()
            slots.append(slot)
        return slots

    @staticmethod
    def _default_slot_labels(template_id: str, slot_index: int) -> tuple[str, str]:
        """Return (label, mw_marker) defaults for a given template and slot index."""
        if template_id == "ip_coip":
            pairs = [("IB: Target (Input)", "50 kDa"),
                     ("IB: Target (IP)", "50 kDa"),
                     ("IB: Loading (Input)", "37 kDa")]
            if slot_index < len(pairs):
                return pairs[slot_index]
        mw_defaults = ["50 kDa", "37 kDa", "25 kDa", "75 kDa", "100 kDa"]
        label = f"IB: Protein {slot_index + 1}"
        mw = mw_defaults[slot_index % len(mw_defaults)]
        return label, mw

    @staticmethod
    def make_condition_table(style: str, n_lanes: int) -> ConditionTable | None:
        if style == "none":
            return None
        headers = [f"Lane {i + 1}" for i in range(n_lanes)]

        def values(items: list[str]) -> list[str]:
            if not items:
                items = ["—"]
            padded = (items + [""] * n_lanes)[:n_lanes]
            return padded

        if style == "vector_matrix":
            rows = [
                ["__span__", "HEK293T"],
                ["pcDNA vector"] + values(["+", "+", "+", "+", "-", "-"]),
                ["pcDNA-RSS*"] + values(["-", "-", "-", "-", "+", "-"]),
                ["pcDNA-C1"] + values(["-", "-", "-", "-", "-", "+"]),
                ["pcDNA-SMYD3"] + values(["-", "+", "-", "+", "+", "+"]),
                ["pcDNA-MAP3K2"] + values(["-", "-", "+", "+", "+", "+"]),
            ]
        elif style == "group_dose":
            rows = [
                ["__span__", "HEK293T MAP3K2"],
                ["Group"] + values(["pcDNA-C1", "pcDNA-C1", "pcDNA-C1", "RSS"]),
                ["Dose"] + values(["0.25", "0.5", "1", "1", "µg DNA/mL"]),
            ]
        elif style == "ip_input":
            rows = [
                ["__span__", "HeLa RNF113A"],
                ["pcDNA-C1"] + values(["-", "+"]),
                ["Section"] + values(["IP: HA", "Inputs"]),
            ]
        else:
            rows = [["Treatment"] + values(["—"] * n_lanes)]

        return ConditionTable(headers=headers, rows=rows)
