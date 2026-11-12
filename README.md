# WB Analyzer Pro

Desktop app for Western blot band densitometry. Pure-Python analysis engine — no external tools required.

## Requirements

- macOS (primary target)
- Python 3.10+

## Setup

```bash
cd WB-analyzer
./run.sh
```

`run.sh` is the recommended launcher. It automatically selects a Python
interpreter that already has the required GUI and analysis dependencies.
It first checks the project-local `.venv`, then known local Python installs,
and launches the app with the first compatible interpreter it finds.

If you want to prepare a specific interpreter manually:

```bash
python3 -m pip install -r requirements.txt
python3 check_env.py
```

## Workflow

1. **Import Images** — TIFF files or Bio-Rad Image Lab documents
2. **Set lanes** — choose the number of lanes
3. **Draw ROIs** — draw a lane ROI, then draw a band ROI
4. **Analyze** — app measures the selected band ROIs
5. **Export** — save only the outputs you explicitly request (CSV/Excel/TIFF export actions)

## Project Folder And Persistence

Everything the app saves lives in one per-user folder:

```
<data folder>/
├── config.json            # lightweight app/debug/UI state
├── templates/             # your saved figure templates
└── blot_files/            # your saved Blot Files and their images
```

| Platform | Data folder | Logs |
| --- | --- | --- |
| macOS | `~/Library/Application Support/WB Analyzer Pro` | `~/Library/Logs/WB Analyzer Pro` |
| Windows | `%APPDATA%\WB Analyzer Pro` | `<data folder>\logs` |

Versions up to 0.3.1 kept templates and Blot Files in `~/.wb_analyzer`. On
first launch the app copies them into the data folder; the old folder is left
untouched and can be deleted once you have confirmed everything is there.

Analysis results are kept in memory during a session and are only written to disk
when you explicitly export.

## Notes

- ROI selection uses the manual lane-and-band workflow
- The app no longer auto-saves per-run analysis artifacts
