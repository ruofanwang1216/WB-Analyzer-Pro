"""Where WB Analyzer Pro keeps its files, and migration from older layouts.

Everything the app writes lives under one per-user data folder:

    <data dir>/config.json     lightweight app/UI state
    <data dir>/templates/      user figure templates
    <data dir>/blot_files/     saved Blot Files and their image assets

The data folder follows each platform's convention.  Logs sit beside it
(macOS: ``~/Library/Logs``; elsewhere: ``<data dir>/logs``).

This module imports only the standard library so that it can be used before
logging or Qt are set up, and from the PyInstaller spec.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

APP_FOLDER_NAME = "WB Analyzer Pro"
_MIGRATION_MARKER = ".legacy_migration_done"


def app_data_dir() -> Path:
    """Return the per-user folder for settings, templates and Blot Files."""
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / APP_FOLDER_NAME
    if sys.platform == "win32":
        roaming = os.environ.get("APPDATA", "").strip()
        base = Path(roaming) if roaming else home / "AppData" / "Roaming"
        return base / APP_FOLDER_NAME
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(xdg) if xdg else home / ".local" / "share"
    return base / APP_FOLDER_NAME


def log_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / APP_FOLDER_NAME
    return app_data_dir() / "logs"


def templates_dir() -> Path:
    return app_data_dir() / "templates"


def blot_files_dir() -> Path:
    return app_data_dir() / "blot_files"


# ── Locations used by releases up to 0.3.1 ───────────────────────────────────

def legacy_user_data_dir() -> Path:
    """Old home of templates/ and blot_files/ on every platform."""
    return Path.home() / ".wb_analyzer"


def legacy_config_dir() -> Path:
    """Old settings folder; the macOS path was also used on Windows/Linux."""
    return Path.home() / "Library" / "Application Support" / APP_FOLDER_NAME


# ── Migration ────────────────────────────────────────────────────────────────

def _path_prefixes(root: Path) -> list[str]:
    candidates = {os.path.abspath(root)}
    try:
        candidates.add(str(root.resolve()))
    except OSError:
        pass
    return sorted(candidates, key=len, reverse=True)


def rebase_paths(value: Any, old_root: Path, new_root: Path) -> Any:
    """Return *value* with absolute paths under *old_root* moved to *new_root*.

    Walks nested dicts/lists as produced by ``json.loads``.  Strings that are
    not absolute paths inside *old_root* are returned unchanged.
    """
    prefixes = [os.path.normcase(prefix) for prefix in _path_prefixes(old_root)]

    def rebase(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: rebase(child) for key, child in item.items()}
        if isinstance(item, list):
            return [rebase(child) for child in item]
        if not isinstance(item, str) or not item or not os.path.isabs(item):
            return item
        normalized = os.path.normcase(os.path.abspath(item))
        for prefix in prefixes:
            if normalized.startswith(prefix + os.sep):
                relative = os.path.abspath(item)[len(prefix) + 1:]
                return str(new_root / relative)
        return item

    return rebase(value)


def _copy_entry(source: Path, target: Path, old_root: Path, new_root: Path) -> None:
    """Copy one file or folder into place atomically, rebasing JSON paths."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        staging = Path(tempfile.mkdtemp(prefix=".migrating-", dir=str(target.parent)))
        try:
            staged = staging / source.name
            shutil.copytree(source, staged)
            os.replace(staged, target)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return

    fd, staged_name = tempfile.mkstemp(prefix=".migrating-", dir=str(target.parent))
    os.close(fd)
    staged_file = Path(staged_name)
    try:
        shutil.copy2(source, staged_file)
        if source.suffix.lower() == ".json":
            try:
                data = json.loads(source.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = None
            if data is not None:
                rebased = rebase_paths(data, old_root, new_root)
                if rebased != data:
                    staged_file.write_text(
                        json.dumps(rebased, indent=2), encoding="utf-8"
                    )
        os.replace(staged_file, target)
    finally:
        staged_file.unlink(missing_ok=True)


def _merge_folder(source: Path, target: Path) -> tuple[int, list[str]]:
    """Copy entries of *source* that *target* lacks.  Never overwrites."""
    copied = 0
    problems: list[str] = []
    if not source.is_dir():
        return copied, problems
    for entry in sorted(source.iterdir()):
        destination = target / entry.name
        if destination.exists():
            continue
        try:
            _copy_entry(entry, destination, source, target)
            copied += 1
        except OSError as exc:
            problems.append(f"{entry}: {exc}")
    return copied, problems


def migrate_legacy_data() -> list[str]:
    """Bring data from pre-0.3.2 locations into :func:`app_data_dir`, once.

    Old files are copied, never moved or deleted, so an older app version
    still finds its data and nothing is lost if a copy is interrupted.
    Returns human-readable notes for the log; it never raises.
    """
    notes: list[str] = []
    data_dir = app_data_dir()
    marker = data_dir / _MIGRATION_MARKER
    try:
        if marker.exists():
            return notes

        problems: list[str] = []
        legacy_root = legacy_user_data_dir()
        for name, target in (
            ("templates", templates_dir()),
            ("blot_files", blot_files_dir()),
        ):
            copied, failed = _merge_folder(legacy_root / name, target)
            problems.extend(failed)
            if copied:
                notes.append(
                    f"Copied {copied} item(s) from {legacy_root / name} to {target}"
                )

        old_config_dir = legacy_config_dir()
        if os.path.abspath(old_config_dir) != os.path.abspath(data_dir):
            old_config = old_config_dir / "config.json"
            new_config = data_dir / "config.json"
            if old_config.is_file() and not new_config.exists():
                try:
                    _copy_entry(old_config, new_config, old_config_dir, data_dir)
                    notes.append(f"Copied settings from {old_config} to {new_config}")
                except OSError as exc:
                    problems.append(f"{old_config}: {exc}")

        if problems:
            # Leave the marker unwritten so the next launch retries what failed.
            notes.extend(f"Could not migrate {problem}" for problem in problems)
            return notes

        data_dir.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            "Data from earlier versions has been copied into this folder.\n",
            encoding="utf-8",
        )
    except OSError as exc:
        notes.append(f"Legacy data migration skipped: {exc}")
    return notes
