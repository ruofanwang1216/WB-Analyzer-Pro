# 04-20-2026 — C1 pERK/pMEK Dose-Response Template

## Source

Replicated from **Slide 4** of `C1 effect to pMEK&pERK.pptx`:
*"March 23 2026 – rerun 0319 HEK293T C1 effect to pERK & pMEK Trial 2"*.

## File

- `user_c1_perk_pmek_dose_response_1776698071.json`

The filename matches the internal template `id`, which is what
`core/template_engine.py::restore_user_project()` uses to locate the file.
The `name` field shown in the WB-analyzer UI is
**"C1 pERK/pMEK Dose-Response (7 lanes, 8 blots)"**.

## Installation

Copy the JSON file into the WB-analyzer user-templates directory, which is
hard-coded in `core/template_engine.py` as `~/.wb_analyzer/templates/`:

```bash
mkdir -p ~/.wb_analyzer/templates
cp templates_presets/user_c1_perk_pmek_dose_response_1776698071.json \
   ~/.wb_analyzer/templates/
```

Restart WB-analyzer. The template will appear in the Step 1 template list
under "C1 pERK/pMEK Dose-Response (7 lanes, 8 blots)".

## Structure

- **Panels:** 1
- **Blots per panel:** 8 (MAP3K2me2, MAP3K2, pMEK, MEK, pERK, ERK, SMYD3, Vinculin)
- **Lanes:** 7 (WT, Mut, 1000, 600, 700, 800, 900)
- **Condition table (5 rows):**
  1. Span header: `pcDNA-C1 (ng)`
  2. Column headers row: `WT | Mut | 1000 | 600 | 700 | 800 | 900`
  3. `pcDNA-SMYD3`: `- | + | + | + | + | + | +`
  4. `pcDNA-MAP3K2`: `- | + | + | + | + | + | +`
  5. `pcDNA-C1`: `- | - | + | + | + | + | +`

## Known deviation from the source slide

`core/layout_engine.py` currently renders `__span__` rows as a single cell
that spans the **full blot width** (not a subset of columns). On Slide 4,
the `pcDNA-C1 (ng)` header visually covers only columns 3–7. The template
uses a full-width span, which is the closest representation available
without modifying the rendering engine. If you want the span limited to
the dose columns, that requires extending the `__span__` row schema in
`layout_engine.py` (e.g., `["__span__", start_col, end_col, text]`).

## MW markers

The blot `mw_marker` values are reasonable literature defaults; feel free
to edit per blot inside the UI after loading the template:

- MAP3K2me2 / MAP3K2 — 70 kDa
- pMEK / MEK — 45 kDa
- pERK / ERK — 42/44 kDa
- SMYD3 — 49 kDa
- Vinculin — 117 kDa
