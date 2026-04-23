import json
import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.ui.widgets.glossary_widget import GlossaryWidget, sorted_glossary_entries


class GlossaryWidgetSortingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_sorted_glossary_entries_orders_by_original_and_keeps_blank_last(self):
        entries = [
            {"original": "zeta", "rus": "zeta"},
            {"original": "", "rus": "blank"},
            {"original": "Alpha", "rus": "alpha"},
            {"original": "beta", "rus": "beta"},
        ]

        sorted_entries = sorted_glossary_entries(entries)

        self.assertEqual(
            [entry["original"] for entry in sorted_entries],
            ["Alpha", "beta", "zeta", ""],
        )

    def test_set_glossary_resets_main_widget_to_first_page(self):
        widget = GlossaryWidget()
        self.addCleanup(widget.close)
        widget.items_per_page = 2
        widget.current_page = 1

        widget.set_glossary([
            {"original": "delta", "rus": "delta"},
            {"original": "Charlie", "rus": "charlie"},
            {"original": "bravo", "rus": "bravo"},
            {"original": "Alpha", "rus": "alpha"},
        ])

        self.assertEqual(widget.current_page, 0)
        self.assertEqual(widget.table.rowCount(), 2)
        self.assertEqual(widget.table.item(0, 0).text(), "Alpha")
        self.assertEqual(widget.table.item(1, 0).text(), "bravo")

    def test_save_project_glossary_writes_project_file_and_marks_state_saved(self):
        widget = GlossaryWidget()
        self.addCleanup(widget.close)

        with tempfile.TemporaryDirectory() as tmpdir:
            widget.set_project_path(tmpdir)
            widget.set_glossary([
                {"original": "delta", "rus": "дельта"},
                {"original": "Alpha", "rus": "альфа"},
            ])

            saved = widget.save_project_glossary(notify=False)

            self.assertTrue(saved)
            self.assertFalse(widget._has_unsaved_project_changes())
            self.assertNotIn("*", widget.project_save_btn.text())

            glossary_path = os.path.join(tmpdir, "project_glossary.json")
            autosave_path = os.path.join(tmpdir, "project_glossary.autosave.json")

            with open(glossary_path, "r", encoding="utf-8") as handle:
                saved_glossary = json.load(handle)
            with open(autosave_path, "r", encoding="utf-8") as handle:
                autosave_glossary = json.load(handle)

            self.assertEqual(saved_glossary, autosave_glossary)
            self.assertEqual(
                [entry["original"] for entry in saved_glossary],
                ["Alpha", "delta"],
            )

    def test_project_view_state_restores_last_page(self):
        entries = [
            {"original": "Alpha", "rus": "a"},
            {"original": "bravo", "rus": "b"},
            {"original": "charlie", "rus": "c"},
            {"original": "delta", "rus": "d"},
            {"original": "echo", "rus": "e"},
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            widget = GlossaryWidget()
            self.addCleanup(widget.close)
            widget.set_project_path(tmpdir)
            widget.items_per_page = 2
            widget.set_glossary(entries, emit_signal=False)
            widget.current_page = 2
            widget._save_project_view_state()

            restored_widget = GlossaryWidget()
            self.addCleanup(restored_widget.close)
            restored_widget.set_project_path(tmpdir)
            restored_widget.items_per_page = 2
            restored_widget.set_glossary(entries, emit_signal=False)
            restored_widget.restore_project_view_state()

            self.assertEqual(restored_widget.current_page, 2)
            self.assertEqual(restored_widget.table.rowCount(), 1)
            self.assertEqual(restored_widget.table.item(0, 0).text(), "echo")


if __name__ == "__main__":
    unittest.main()
