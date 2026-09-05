"""Регрессия для ui-widgets-a/bugs/4-glossary-nonatomic-write-defea.

_write_glossary_json писал прямо в целевой файл (open(..., "w") усекает его
немедленно), поэтому крах/исключение посреди json.dump оставлял на диске
битый JSON. load_project_glossary при этом грузил project_glossary.json
раньше автокопии без try/except, так что JSONDecodeError вылетал наружу до
попытки восстановления — вызывающий код (setup.py) реагировал на исключение
полной очисткой глоссария, хотя рядом могла лежать целая автокопия.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.ui.widgets import glossary_widget as glossary_widget_module
from gemini_translator.ui.widgets.glossary_widget import GlossaryWidget


class GlossaryAtomicWriteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_write_glossary_json_does_not_destroy_existing_file_on_failure(self):
        """Крах посреди записи не должен уничтожать уже сохранённые данные."""
        widget = GlossaryWidget()
        self.addCleanup(widget.close)

        with tempfile.TemporaryDirectory() as tmpdir:
            target_path = os.path.join(tmpdir, "project_glossary.json")
            original_entries = [{"original": "Alpha", "rus": "альфа"}]
            with open(target_path, "w", encoding="utf-8") as handle:
                json.dump(original_entries, handle, ensure_ascii=False)

            with patch.object(
                glossary_widget_module.json,
                "dump",
                side_effect=RuntimeError("диск переполнен на середине записи"),
            ):
                with self.assertRaises(RuntimeError):
                    widget._write_glossary_json(target_path, [{"original": "beta", "rus": "бета"}])

            # Целевой файл не должен быть усечён/повреждён неудачной записью.
            with open(target_path, "r", encoding="utf-8") as handle:
                surviving_data = json.load(handle)
            self.assertEqual(surviving_data, original_entries)

            # Временный файл не должен оставаться мусором в директории проекта.
            leftovers = [name for name in os.listdir(tmpdir) if name != "project_glossary.json"]
            self.assertEqual(leftovers, [])

    def test_load_project_glossary_survives_corrupted_project_file(self):
        """Битый project_glossary.json не должен ронять load_project_glossary."""
        widget = GlossaryWidget()
        self.addCleanup(widget.close)

        with tempfile.TemporaryDirectory() as tmpdir:
            widget.set_project_path(tmpdir)

            project_path = os.path.join(tmpdir, "project_glossary.json")
            autosave_path = os.path.join(tmpdir, "project_glossary.autosave.json")

            # Усечённый JSON — как будто процесс упал ровно во время записи.
            with open(project_path, "w", encoding="utf-8") as handle:
                handle.write('[{"original": "Alpha", "rus": "альф')

            autosave_entries = [{"original": "Alpha", "rus": "альфа", "note": "", "timestamp": 1.0}]
            with open(autosave_path, "w", encoding="utf-8") as handle:
                json.dump(autosave_entries, handle, ensure_ascii=False)

            with (
                patch.object(glossary_widget_module.QMessageBox, "exec", return_value=None),
                patch.object(
                    glossary_widget_module.QMessageBox,
                    "clickedButton",
                    lambda self: self.defaultButton(),
                ),
            ):
                # До фикса здесь вылетает json.decoder.JSONDecodeError.
                project_data, restored_from_autosave = widget.load_project_glossary()

            self.assertTrue(restored_from_autosave)
            self.assertEqual(
                [entry["original"] for entry in widget.get_glossary()],
                ["Alpha"],
            )

    def test_load_project_glossary_survives_corrupted_autosave_file(self):
        """Битая автокопия не должна ронять загрузку целого project_glossary.json."""
        widget = GlossaryWidget()
        self.addCleanup(widget.close)

        with tempfile.TemporaryDirectory() as tmpdir:
            widget.set_project_path(tmpdir)

            project_path = os.path.join(tmpdir, "project_glossary.json")
            autosave_path = os.path.join(tmpdir, "project_glossary.autosave.json")

            project_entries = [{"original": "Beta", "rus": "бета", "note": "", "timestamp": 1.0}]
            with open(project_path, "w", encoding="utf-8") as handle:
                json.dump(project_entries, handle, ensure_ascii=False)

            # Пустой (0 байт) файл автокопии — как в сценарии D из репро.
            with open(autosave_path, "w", encoding="utf-8"):
                pass

            project_data, restored_from_autosave = widget.load_project_glossary()

            self.assertFalse(restored_from_autosave)
            self.assertEqual(
                [entry["original"] for entry in widget.get_glossary()],
                ["Beta"],
            )


if __name__ == "__main__":
    unittest.main()
