#!/usr/bin/env python3
"""Diagnostic: check dependencies and basic imports."""
import sys

print("Python executable:", sys.executable)
print("Python version:", sys.version)
print()

deps = [
    ("PySide6", "PySide6.QtWidgets"),
    ("pandas", "pandas"),
    ("numpy", "numpy"),
    ("Pillow", "PIL"),
    ("scipy", "scipy"),
    ("openpyxl", "openpyxl"),
]

all_ok = True
for name, module in deps:
    try:
        __import__(module)
        print(f"✓ {name}")
    except ModuleNotFoundError as e:
        print(f"✗ {name}: {e}")
        all_ok = False

print()

if all_ok:
    print("Checking app imports...")
    try:
        from config.settings import APP_NAME, VERSION
        print(f"✓ Config module: {APP_NAME} v{VERSION}")
        print(f"✓ Logger module")
        print()
        print("All imports OK — ready to run './run.sh'")
    except Exception as e:
        print(f"✗ Import error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
else:
    print()
    print("Missing dependencies for this interpreter.")
    print("Preferred launcher:")
    print("  ./run.sh")
    print()
    print("If you want to fix this exact interpreter, run:")
    print(f"  {sys.executable} -m pip install -r requirements.txt")
    sys.exit(1)
