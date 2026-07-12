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

1. **Open Image** — TIFF, PNG, or JPG
2. **Set lanes** — choose the number of lanes
3. **Draw ROIs** — draw a lane ROI, then draw a band ROI
4. **Analyze** — app measures the selected band ROIs
5. **Export** — save only the outputs you explicitly request (CSV/Excel/TIFF export actions)

## Project Folder And Persistence

```
~/Documents/WB_AllInOne/
└── config.json            # lightweight app/debug/UI state
```

Analysis results are kept in memory during a session and are only written to disk
when you explicitly export.

## Notes

- ROI selection uses the manual lane-and-band workflow
- The app no longer auto-saves per-run analysis artifacts
