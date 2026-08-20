# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller build spec for WB Analyzer Pro. Shared across Windows and macOS -
# the platform-specific bits (the macOS .app BUNDLE step) are gated on
# sys.platform so the same spec file works in both CI jobs.
#
# Build (inside a venv with requirements.txt + pyinstaller installed):
#   pyinstaller pyinstaller.spec --noconfirm
#
# Output:
#   Windows: dist/WBAnalyzerPro/WBAnalyzerPro.exe (+ supporting files)
#   macOS:   dist/WBAnalyzerPro.app

import sys
from pathlib import Path

block_cipher = None
project_root = Path(SPECPATH)
app_icon = project_root / "assets" / "WBAnalyzerPro.icns"

# Bundle the vendored WB-TIFF-exporter helper modules (used by the optional
# "upload/convert" feature in gui/main_window.py). These are plain .py files
# copied in as data so the frozen app can add them to sys.path at runtime -
# see MainWindow._resolve_exporter_root().
datas = []
vendor_dir = project_root / "vendor" / "wb_tiff_exporter"
if vendor_dir.exists():
    datas.append((str(vendor_dir), "vendor/wb_tiff_exporter"))
tutorial_assets_dir = project_root / "assets" / "tutorial"
if tutorial_assets_dir.exists():
    datas.append((str(tutorial_assets_dir), "assets/tutorial"))

# Saved blot files and user-created templates are private data stored outside
# the project under ~/.wb_analyzer.  They must never be bundled into a
# distributable installer.  The built-in 1-panel / 3-blot / 4-lane template is
# defined in source code and remains available without copying user data.
private_saved_data = {
    (Path.home() / ".wb_analyzer" / "blot_files").resolve(),
    (Path.home() / ".wb_analyzer" / "templates").resolve(),
}
for source, _destination in datas:
    source_path = Path(source).resolve()
    if any(source_path == private_dir or private_dir in source_path.parents
           for private_dir in private_saved_data):
        raise RuntimeError(
            "Private saved blot files/templates must not be included in release builds"
        )

hiddenimports = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "pandas",
    "numpy",
    "scipy",
    "scipy.ndimage",
    "PIL",
    "PIL.Image",
    "openpyxl",
    "pptx",
]

a = Analysis(
    ["main.py"],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib",
        "tkinter",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtNetwork",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
    ],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WBAnalyzerPro",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(app_icon) if app_icon.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="WBAnalyzerPro",
)

# macOS only: wrap the COLLECT output into a proper .app bundle. PyInstaller
# ignores this on Windows/Linux builds, so the same spec file is safe there.
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="WBAnalyzerPro.app",
        icon=str(app_icon) if app_icon.exists() else None,
        bundle_identifier="com.wblab.wbanalyzerpro",
        info_plist={
            "CFBundleName": "WB Analyzer Pro",
            "CFBundleDisplayName": "WB Analyzer Pro",
            "CFBundleShortVersionString": "0.3.1",
            "NSHighResolutionCapable": True,
        },
    )
