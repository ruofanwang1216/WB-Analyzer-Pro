#!/bin/bash
# Build a standalone macOS .app + .dmg installer for WB Analyzer Pro, locally,
# with no GitHub/network dependency. Copy the resulting .dmg to a USB drive
# and hand it to anyone with a Mac -- no Python or dependencies required on
# their end, everything is bundled into the .app.
#
# The actual PyInstaller build output is written to a temp folder outside
# ~/Documents on purpose: if ~/Documents is synced by iCloud Drive, the sync
# daemon keeps re-attaching Finder/resource-fork metadata to files while
# they're being written, which makes `codesign` fail with "resource fork,
# Finder information, or similar detritus not allowed". Building outside the
# synced folder avoids that entirely. Only the final .dmg is copied back.
#
# Usage:
#   cd ~/Documents/WB-analyzer
#   ./build_macos_local.sh
#
# Output: dist_installer/WBAnalyzerPro-Setup-macOS.dmg

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

PYTHON_CANDIDATES=(
  "/Users/skyewang/miniforge3/bin/python3"
  "$SCRIPT_DIR/.venv/bin/python"
)
if command -v python3 >/dev/null 2>&1; then
  PYTHON_CANDIDATES+=("$(command -v python3)")
fi

SELECTED_PYTHON=""
for candidate in "${PYTHON_CANDIDATES[@]}"; do
  if [[ -x "$candidate" ]]; then
    SELECTED_PYTHON="$candidate"
    break
  fi
done

if [[ -z "$SELECTED_PYTHON" ]]; then
  echo "No Python interpreter found. Install Python 3.10+ first." >&2
  exit 1
fi

echo "Using Python: $SELECTED_PYTHON"

echo "==> Installing/verifying dependencies (requirements.txt + pyinstaller)..."
"$SELECTED_PYTHON" -m pip install -q -r requirements.txt
"$SELECTED_PYTHON" -m pip install -q pyinstaller

# Build outside ~/Documents (see note above) so iCloud sync can't interfere.
BUILD_ROOT="/tmp/wb-analyzer-pro-build"
echo "==> Using build directory outside iCloud sync: $BUILD_ROOT"
rm -rf "$BUILD_ROOT"
mkdir -p "$BUILD_ROOT"

echo "==> Cleaning previous local build output..."
rm -rf build dist dist_installer dmg_staging

echo "==> Running PyInstaller (writing output to $BUILD_ROOT)..."
"$SELECTED_PYTHON" -m PyInstaller pyinstaller.spec --noconfirm \
  --distpath "$BUILD_ROOT/dist" \
  --workpath "$BUILD_ROOT/build"

APP_PATH="$BUILD_ROOT/dist/WBAnalyzerPro.app"
if [[ ! -d "$APP_PATH" ]]; then
  echo "Build did not produce $APP_PATH - check the PyInstaller output above." >&2
  exit 1
fi

echo "==> Cleaning Finder/AppleDouble detritus that blocks codesigning..."
dot_clean -m "$APP_PATH" 2>/dev/null || true
find "$APP_PATH" -name '.DS_Store' -delete
find "$APP_PATH" -name '._*' -delete
xattr -cr "$APP_PATH"

echo "==> Ad-hoc signing the app bundle (required for it to launch on Apple Silicon Macs)..."
if ! codesign --force --deep --sign - "$APP_PATH"; then
  echo "First codesign attempt failed, retrying file-by-file (slower but more thorough)..." >&2
  find "$APP_PATH" -type f \( -perm -u+x -o -name "*.dylib" -o -name "*.so" \) -print0 \
    | xargs -0 -I{} xattr -c "{}" 2>/dev/null || true
  codesign --force --deep --sign - "$APP_PATH"
fi
codesign --verify --deep --strict "$APP_PATH" && echo "   signature OK"

echo "==> Building .dmg installer (in $BUILD_ROOT)..."
mkdir -p "$BUILD_ROOT/dmg_staging"
cp -R "$APP_PATH" "$BUILD_ROOT/dmg_staging/"
ln -s /Applications "$BUILD_ROOT/dmg_staging/Applications"
hdiutil create -volname "WB Analyzer Pro" \
  -srcfolder "$BUILD_ROOT/dmg_staging" \
  -ov -format UDZO \
  "$BUILD_ROOT/WBAnalyzerPro-Setup-macOS.dmg"

echo "==> Copying finished installer back into the project folder..."
mkdir -p dist_installer
cp "$BUILD_ROOT/WBAnalyzerPro-Setup-macOS.dmg" dist_installer/

echo
echo "Done. Installer at: dist_installer/WBAnalyzerPro-Setup-macOS.dmg"
echo "Copy this one file to a USB drive to share it."
