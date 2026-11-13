from config.paths import APP_FOLDER_NAME, app_data_dir

APP_NAME = "WB Analyzer Pro"
# Single source of truth for the version: pyinstaller.spec and the Windows
# installer build both read it from here.
VERSION = "0.3.1"

PROJECT_FOLDER_NAME = APP_FOLDER_NAME
# App-managed settings belong in the platform's per-user application-data
# folder (see config/paths.py), not the user's Documents folder. This avoids
# an unnecessary privacy prompt on first launch.
DEFAULT_PROJECT_DIR = app_data_dir()

# Shared by the global stylesheet (gui/theme.py) and the densitometry widgets.
# ── Colour palette (2026 UI redesign: white cards on a soft grey-green ground) ──────────────────────────────────────────────────────
COLOR_BG           = "#E7ECE9"   # app background
COLOR_SURFACE      = "#FFFFFF"   # canvas, input fields, table rows
COLOR_TOOLBAR      = "#FFFFFF"   # toolbar, title bar, status bar
COLOR_SIDEBAR      = "#FFFFFF"   # param panel background
COLOR_BORDER       = "#C9D3CE"   # all borders
COLOR_BORDER_LIGHT = "#E4E9E6"   # subtle inner borders

COLOR_TEXT         = "#1C2622"   # primary text
COLOR_TEXT_MUTED   = "#4A5A53"   # secondary text, labels
COLOR_TEXT_HINT    = "#5A6B63"   # placeholder, column headers

COLOR_ACCENT       = "#1F6B4A"   # sage green — buttons, ROI overlays, badges
COLOR_ACCENT_LIGHT = "#E3EFE8"   # badge backgrounds, hint block numbers
COLOR_ACCENT_TEXT  = "#134A32"   # text on accent-light backgrounds
COLOR_ACCENT_DARK  = "#134A32"   # button border, hover state

COLOR_BAND_ROI     = "#8AAEC4"   # band ROI — blue, distinct from lane ROI

COLOR_RESULTS_HD   = "#F3F5F4"   # results header + table header background
COLOR_ROW_ALT      = "#F7F9F8"   # alternating table row
