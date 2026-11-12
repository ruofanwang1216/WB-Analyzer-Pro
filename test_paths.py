"""Tests for the unified data folder and one-time legacy migration."""
from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import paths
from config.settings import VERSION


class _FakeHome(unittest.TestCase):
    platform = "darwin"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "home"
        self.home.mkdir()
        patchers = [
            patch.object(Path, "home", return_value=self.home),
            patch.object(paths.sys, "platform", self.platform),
            patch.dict(
                paths.os.environ,
                {"APPDATA": str(self.home / "AppData" / "Roaming")},
            ),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def _write_legacy_blot_file(self) -> Path:
        legacy = self.home / ".wb_analyzer" / "blot_files"
        assets = legacy / "blot_a_assets"
        assets.mkdir(parents=True)
        (assets / "image_1.tif").write_bytes(b"tiff-bytes")
        (assets / "preview_panel_1_blot_1.png").write_bytes(b"png-bytes")
        outside = str(self.home / "Desktop" / "raw.tif")
        payload = {
            "format": "wb_blot_file",
            "id": "blot_a",
            "name": "实验 A",
            "project": {
                "panels": [{
                    "blot_slots": [{
                        "source_image_path": str(assets / "image_1.tif"),
                        "saved_preview_path": str(
                            assets / "preview_panel_1_blot_1.png"
                        ),
                        "label": "IB: Actin",
                    }],
                }],
            },
            "canvas_state": {
                "overlay_items": [{"type": "blot", "image_path": outside}],
            },
        }
        (legacy / "blot_a.json").write_text(json.dumps(payload), encoding="utf-8")
        return legacy


class MacMigrationTests(_FakeHome):
    def test_data_folder_is_unchanged_on_macos(self) -> None:
        self.assertEqual(
            paths.app_data_dir(),
            self.home / "Library" / "Application Support" / "WB Analyzer Pro",
        )

    def test_blot_files_and_templates_are_copied_with_paths_rebased(self) -> None:
        legacy_blots = self._write_legacy_blot_file()
        legacy_templates = self.home / ".wb_analyzer" / "templates"
        legacy_templates.mkdir(parents=True)
        (legacy_templates / "user_t_1.json").write_text(
            json.dumps({"format": "wb_user_template", "id": "user_t_1"}),
            encoding="utf-8",
        )

        notes = paths.migrate_legacy_data()

        self.assertEqual(len(notes), 2)
        self.assertTrue((paths.templates_dir() / "user_t_1.json").is_file())
        migrated = json.loads(
            (paths.blot_files_dir() / "blot_a.json").read_text(encoding="utf-8")
        )
        slot = migrated["project"]["panels"][0]["blot_slots"][0]
        for key in ("source_image_path", "saved_preview_path"):
            migrated_path = Path(slot[key])
            self.assertIn(paths.blot_files_dir(), migrated_path.parents)
            self.assertTrue(migrated_path.is_file())
        self.assertEqual(migrated["name"], "实验 A")
        self.assertEqual(slot["label"], "IB: Actin")
        # Paths outside the old folder are the user's own files: untouched.
        self.assertEqual(
            migrated["canvas_state"]["overlay_items"][0]["image_path"],
            str(self.home / "Desktop" / "raw.tif"),
        )
        # The old data is copied, never moved.
        self.assertTrue((legacy_blots / "blot_a.json").is_file())
        self.assertTrue((legacy_blots / "blot_a_assets" / "image_1.tif").is_file())

    def test_migration_runs_once_and_never_overwrites(self) -> None:
        legacy_templates = self.home / ".wb_analyzer" / "templates"
        legacy_templates.mkdir(parents=True)
        (legacy_templates / "user_t_1.json").write_text("{}", encoding="utf-8")
        paths.templates_dir().mkdir(parents=True)
        (paths.templates_dir() / "user_t_1.json").write_text(
            '{"kept": true}', encoding="utf-8"
        )

        self.assertEqual(paths.migrate_legacy_data(), [])
        self.assertEqual(
            (paths.templates_dir() / "user_t_1.json").read_text(encoding="utf-8"),
            '{"kept": true}',
        )

        # A template deleted after migration must not come back.
        (legacy_templates / "user_t_2.json").write_text("{}", encoding="utf-8")
        self.assertEqual(paths.migrate_legacy_data(), [])
        self.assertFalse((paths.templates_dir() / "user_t_2.json").exists())

    def test_fresh_install_has_nothing_to_migrate(self) -> None:
        self.assertEqual(paths.migrate_legacy_data(), [])
        self.assertTrue(paths.app_data_dir().is_dir())


class WindowsMigrationTests(_FakeHome):
    platform = "win32"

    def test_settings_move_out_of_the_macos_style_folder(self) -> None:
        old_dir = self.home / "Library" / "Application Support" / "WB Analyzer Pro"
        old_dir.mkdir(parents=True)
        (old_dir / "config.json").write_text(
            json.dumps({"ui": {"language": "zh_CN"}}), encoding="utf-8"
        )

        self.assertEqual(
            paths.app_data_dir(),
            self.home / "AppData" / "Roaming" / "WB Analyzer Pro",
        )
        paths.migrate_legacy_data()

        config = json.loads(
            (paths.app_data_dir() / "config.json").read_text(encoding="utf-8")
        )
        self.assertEqual(config["ui"]["language"], "zh_CN")
        self.assertEqual(paths.log_dir(), paths.app_data_dir() / "logs")


class SavedBlotFileMigrationTests(_FakeHome):
    """A Blot File written by the real save code survives the move."""

    def test_migrated_blot_file_opens_with_identical_pixels(self) -> None:
        import shutil

        import numpy as np
        from PIL import Image
        from PySide6.QtWidgets import QApplication

        import gui.figure_mode_window as figure_mode_module
        from core.figure_project import ImageBBox
        from gui.figure_mode_window import FigureModeWindow

        app = QApplication.instance() or QApplication([])
        legacy_dir = paths.legacy_user_data_dir() / "blot_files"
        source_path = self.home / "source.tif"
        pixels = (
            np.arange(20 * 30, dtype=np.uint32).reshape(20, 30) * 101
        ).clip(0, 65535).astype(np.uint16)
        Image.fromarray(pixels).save(source_path)
        blot_key = (0, 0, None, None, None, "blot")

        with patch.object(figure_mode_module, "USER_BLOT_FILES_DIR", legacy_dir):
            window = FigureModeWindow()
            window._on_apply_template()
            slot = window._project.panels[0].blot_slots[0]
            slot.source_image_path = str(source_path)
            slot.bounding_box = ImageBBox(2.0, 3.0, 18.0, 9.0)
            slot.reset_equal_lanes()
            window._recompute_and_refresh(fit_view=False)
            app.processEvents()
            before = window._canvas.blot_preview_image(blot_key)
            window._write_blot_file("blot_old", "Old Blot")

        paths.migrate_legacy_data()
        # Prove the migrated copy does not depend on the old folder.
        shutil.rmtree(paths.legacy_user_data_dir())
        source_path.unlink()

        with patch.object(
            figure_mode_module, "USER_BLOT_FILES_DIR", paths.blot_files_dir()
        ):
            restored = FigureModeWindow()
            restored._load_blot_file("blot_old")
            restored_slot = restored._project.panels[0].blot_slots[0]
            for value in (
                restored_slot.source_image_path,
                restored_slot.saved_preview_path,
            ):
                self.assertIn(paths.blot_files_dir(), Path(value).parents)
                self.assertTrue(Path(value).is_file())
            self.assertEqual(restored._canvas.blot_preview_image(blot_key), before)


class SingleVersionSourceTests(unittest.TestCase):
    def test_build_files_do_not_hardcode_the_version(self) -> None:
        root = Path(__file__).resolve().parent
        for name in ("pyinstaller.spec", "installer.iss"):
            text = (root / name).read_text(encoding="utf-8")
            self.assertNotIn(VERSION, text, name)
        spec = (root / "pyinstaller.spec").read_text(encoding="utf-8")
        pattern = re.search(r"r'(\^VERSION[^']+)'", spec).group(1)
        settings = (root / "config" / "settings.py").read_text(encoding="utf-8")
        self.assertEqual(
            re.search(pattern, settings, re.MULTILINE).group(1), VERSION
        )


if __name__ == "__main__":
    unittest.main()
