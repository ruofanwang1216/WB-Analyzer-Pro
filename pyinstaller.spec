# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller build spec for WB Analyzer Pro (Windows).
#
# Build (on Windows, inside a venv with requirements.txt + pyinstaller installed):
#   pyinstaller pyinstaller.spec --noconfirm
#
# Output: dist/WBAnalyzerPro/WBAnalyzerPro.exe (+ supporting files)

import sys
from pathlib import Path

block_cipher = None
project_root = Path(SPECPATH)

# Bundle the vendored WB-TIFF-exporter helper modules (used by the optional
# "upload/convert" feature in gui/main_window.py). These are plain .py files
# copied in as data so the frozen app can add them to sys.path at runtime -
# see MainWindow._resolve_exporter_root().
datas = []
vendor_dir = project_root / "vendor" / "wb_tiff_exporter"
if vendor_dir.exists():
    datas.append((str(vendor_dir), "vendor/wb_tiff_exporter"))

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
    icon=None,
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
