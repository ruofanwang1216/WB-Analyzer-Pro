"""Centralized app path and config persistence helpers."""
from __future__ import annotations

import json
import os
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

from config.settings import APP_NAME, VERSION, PROJECT_FOLDER_NAME
from utils.logger import get_logger

log = get_logger(__name__)


class AppPersistence:
    """
    Owns lightweight app persistence:
    - stable project folder under ~/Documents/WB_AllInOne
    - minimal config.json for app/debug/UI state
    - remembered defaults for user-triggered exports
    """

    def __init__(self, project_dir: Path | None = None) -> None:
        env_override = os.environ.get("WB_PROJECT_DIR", "").strip()
        root = project_dir or (Path(env_override).expanduser() if env_override else (Path.home() / "Documents" / PROJECT_FOLDER_NAME))
        self._project_dir = root.expanduser().resolve()
        self._config_path = self._project_dir / "config.json"
        try:
            self._ensure_project_dir()
        except OSError as exc:
            fallback_root = (Path.cwd() / PROJECT_FOLDER_NAME).resolve()
            log.warning(
                "Cannot access project folder %s (%s). Falling back to %s",
                self._project_dir,
                exc,
                fallback_root,
            )
            self._project_dir = fallback_root
            self._config_path = self._project_dir / "config.json"
            self._ensure_project_dir()
        self._ensure_config_exists()

    @property
    def project_dir(self) -> Path:
        return self._project_dir

    @property
    def config_path(self) -> Path:
        return self._config_path

    def _ensure_project_dir(self) -> None:
        self._project_dir.mkdir(parents=True, exist_ok=True)

    def _default_config(self) -> dict[str, Any]:
        return {
            "app": {
                "name": APP_NAME,
                "version": VERSION,
            },
            "paths": {
                "project_dir": str(self._project_dir),
                "config_path": str(self._config_path),
                "last_open_dir": str(self._project_dir),
                "last_results_export_dir": str(self._project_dir),
                "last_tiff_export_dir": str(self._project_dir),
            },
            "ui": {
                "mode": "manual",
                "files_panel_collapsed": False,
            },
            "debug": {
                "last_analysis": None,
            },
        }

    def _ensure_config_exists(self) -> None:
        if self._config_path.exists():
            return
        self.write_config(self._default_config())

    def read_config(self) -> dict[str, Any]:
        if not self._config_path.exists():
            return self._default_config()
        try:
            payload = json.loads(self._config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Failed reading config at %s: %s", self._config_path, exc)
            return self._default_config()
        if not isinstance(payload, dict):
            return self._default_config()
        return payload

    def write_config(self, payload: dict[str, Any]) -> None:
        self._ensure_project_dir()
        self._config_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def update_config(self, *, paths: dict[str, Any] | None = None, ui: dict[str, Any] | None = None, debug: dict[str, Any] | None = None) -> None:
        data = self.read_config()
        data.setdefault("app", {})
        data["app"]["name"] = APP_NAME
        data["app"]["version"] = VERSION

        data.setdefault("paths", {})
        data["paths"]["project_dir"] = str(self._project_dir)
        data["paths"]["config_path"] = str(self._config_path)
        if paths:
            data["paths"].update(paths)

        data.setdefault("ui", {})
        if ui:
            data["ui"].update(ui)

        data.setdefault("debug", {})
        if debug:
            data["debug"].update(debug)

        self.write_config(data)

    def default_open_dir(self) -> Path:
        return self._path_from_config("last_open_dir")

    def default_results_export_dir(self) -> Path:
        return self._path_from_config("last_results_export_dir")

    def default_tiff_export_dir(self) -> Path:
        return self._path_from_config("last_tiff_export_dir")

    def remember_open_dir(self, directory: Path) -> None:
        self.update_config(paths={"last_open_dir": str(directory.expanduser().resolve())})

    def remember_results_export_dir(self, directory: Path) -> None:
        self.update_config(paths={"last_results_export_dir": str(directory.expanduser().resolve())})

    def remember_tiff_export_dir(self, directory: Path) -> None:
        self.update_config(paths={"last_tiff_export_dir": str(directory.expanduser().resolve())})

    def remember_ui_state(self, *, mode: str | None = None, files_panel_collapsed: bool | None = None) -> None:
        ui_payload: dict[str, Any] = {}
        if mode is not None:
            ui_payload["mode"] = mode
        if files_panel_collapsed is not None:
            ui_payload["files_panel_collapsed"] = bool(files_panel_collapsed)
        if ui_payload:
            self.update_config(ui=ui_payload)

    def remember_analysis_debug(self, *, image_path: str, mode: str, lane_count: int, band_count: int) -> None:
        self.update_config(
            debug={
                "last_analysis": {
                    "timestamp_utc": datetime.now(UTC).isoformat(),
                    "image_path": image_path,
                    "mode": mode,
                    "lane_count": int(lane_count),
                    "band_count": int(band_count),
                }
            }
        )

    def _path_from_config(self, key: str) -> Path:
        payload = self.read_config()
        raw = payload.get("paths", {}).get(key)
        if isinstance(raw, str) and raw.strip():
            candidate = Path(raw).expanduser()
            if candidate.exists() and candidate.is_dir():
                return candidate
        return self._project_dir
