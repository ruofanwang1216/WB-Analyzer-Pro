# WB Densitometry — Setup & Test

## Status

**Core modules**: Ready (no external GUI deps)
- `core/band_detector.py` — band detection algorithm (numpy/scipy)
- `core/measure.py` — pure-Python densitometry measurement
- `config/settings.py` — app constants
- `utils/logger.py` — app logging

**GUI modules**: Ready (require PySide6)
- `gui/main_window.py` — main app window
- `gui/image_canvas.py` — image view + ROI drawing
- `gui/param_panel.py` — parameter controls
- `gui/results_panel.py` — results table + export

**Preferred entry point**: `./run.sh`

## Quick Start

1. **Install dependencies**
   ```bash
   cd WB-analyzer
   python3 -m pip install -r requirements.txt
   ```

   Or launch directly with the recommended wrapper:
   ```bash
   ./run.sh
   ```
   `run.sh` checks several local Python interpreters and starts the app with
   the first one that already has the required packages installed.

2. **Verify environment**
   ```bash
   python3 check_env.py
   ```

3. **Run full app**
   ```bash
   ./run.sh
   ```

## Architecture notes

- Analysis is pure Python: numpy + scipy + Pillow — no external tools needed.
- `run.sh` is the safest launcher because it validates dependency availability before opening the GUI.
- Tests can run independently of the GUI framework.
