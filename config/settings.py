from pathlib import Path

APP_NAME = "WB Analyzer Pro"
VERSION = "0.1.0"

PROJECT_FOLDER_NAME = "WB_AllInOne"
DEFAULT_PROJECT_DIR = Path.home() / "Documents" / PROJECT_FOLDER_NAME
DEFAULT_CONFIG_PATH = DEFAULT_PROJECT_DIR / "config.json"

SUPPORTED_IMAGE_EXTS = {".tif", ".tiff", ".png", ".jpg", ".jpeg"}

# ── Morandi color palette ──────────────────────────────────────────────────────
COLOR_BG           = "#EAF0F4"   # app background
COLOR_SURFACE      = "#F5F8FA"   # canvas, input fields, table rows
COLOR_TOOLBAR      = "#E2ECF2"   # toolbar, title bar, status bar
COLOR_SIDEBAR      = "#EBF1F6"   # param panel background
COLOR_BORDER       = "#C8D8E4"   # all borders
COLOR_BORDER_LIGHT = "#D8E6EE"   # subtle inner borders

COLOR_TEXT         = "#35393D"   # primary text
COLOR_TEXT_MUTED   = "#6E8494"   # secondary text, labels
COLOR_TEXT_HINT    = "#A0B4C0"   # placeholder, column headers

COLOR_ACCENT       = "#8AB4A0"   # sage green — buttons, ROI overlays, badges
COLOR_ACCENT_LIGHT = "#D4EDE4"   # badge backgrounds, hint block numbers
COLOR_ACCENT_TEXT  = "#2A5E48"   # text on accent-light backgrounds
COLOR_ACCENT_DARK  = "#6A9E88"   # button border, hover state

COLOR_BAND_ROI     = "#8AAEC4"   # band ROI — blue, distinct from lane ROI

COLOR_RESULTS_HD   = "#DCE9F2"   # results header + table header background
COLOR_ROW_ALT      = "#EFF4F8"   # alternating table row
