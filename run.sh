#!/bin/bash
# WB Densitometry launcher for macOS

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

REQUIRED_MODULES=(
  "PySide6.QtWidgets"
  "pandas"
  "numpy"
  "PIL"
  "scipy"
  "openpyxl"
)

has_required_modules() {
  local python_bin="$1"
  "$python_bin" -c '
import importlib
import sys

mods = sys.argv[1:]
missing = []
for name in mods:
    try:
        importlib.import_module(name)
    except Exception:
        missing.append(name)
sys.exit(0 if not missing else 1)
' "${REQUIRED_MODULES[@]}" >/dev/null 2>&1
}

PYTHON_CANDIDATES=(
  "/Users/skyewang/miniforge3/bin/python3"
  "$SCRIPT_DIR/.venv/bin/python"
)

if command -v python3 >/dev/null 2>&1; then
  PYTHON_CANDIDATES+=("$(command -v python3)")
fi

SELECTED_PYTHON=""
for candidate in "${PYTHON_CANDIDATES[@]}"; do
  if [[ -x "$candidate" ]] && has_required_modules "$candidate"; then
    SELECTED_PYTHON="$candidate"
    break
  fi
done

if [[ -z "$SELECTED_PYTHON" ]]; then
  echo "No compatible Python interpreter found for WB Densitometry." >&2
  echo "Checked:" >&2
  for candidate in "${PYTHON_CANDIDATES[@]}"; do
    echo "  $candidate" >&2
  done
  echo >&2
  echo "Install the requirements into one of those interpreters, then run ./run.sh again." >&2
  echo "You can also inspect the current interpreter with: python3 check_env.py" >&2
  exit 1
fi

echo "Using Python: $SELECTED_PYTHON"
exec "$SELECTED_PYTHON" main.py
