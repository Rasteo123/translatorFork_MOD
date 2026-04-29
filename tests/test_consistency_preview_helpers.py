import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest

from PyQt6 import QtWidgets
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QTextOption
from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem, QTextEdit

from gemini_translator.ui.dialogs.consistency_checker import (
    build_changed_line_format,
    configure_wrapped_text_edit,
    ConsistencyValidatorDialog,
)


class _ButtonStub:
    def __init__(self, enabled=False):
        self.enabled = bool(enabled)

    def setEnabled(self, value):
        self.enabled = bool(value)


class _ManualFixHarness:
    _sanitize_preview_text = ConsistencyValidatorDialog._sanitize_preview_text
    _set_corrected_preview_plain_text = ConsistencyValidatorDialog._set_corrected_preview_plain_text
    _render_line_diff_preview = ConsistencyValidatorDialog._render_line_diff_preview
    _show_diff = ConsistencyValidatorDialog._show_diff
    _focus_corrected_preview_on_quote = ConsistencyValidatorDialog._focus_corrected_preview_on_quote
    _get_corrected_preview_content = ConsistencyValidatorDialog._get_corrected_preview_content
    _sync_corrected_preview_content = ConsistencyValidatorDialog._sync_corrected_preview_content
    start_manual_fix = ConsistencyValidatorDialog.start_manual_fix

    def __init__(self):
        self.current_problem = {"id": "p1", "quote": "world"}
        self.current_chapter = {
            "name": "chapter.xhtml",
            "path": "chapter.xhtml",
            "content": "<p>Hello world</p>\n",
        }
        self.corrected_text = QTextEdit()
        self.apply_btn = _ButtonStub(False)
        self.fix_previews = {}
        self.single_fix_trace_file = None
        self.logs = []

    def _is_thread_running(self, thread_attr):
        return False

    def _log(self, message):
        self.logs.append(message)


class _ResolvedProblemHarness:
    _problem_key = ConsistencyValidatorDialog._problem_key
    _is_problem_resolved = ConsistencyValidatorDialog._is_problem_resolved
    _mark_problem_resolved = ConsistencyValidatorDialog._mark_problem_resolved
    _count_visible_checked_problems = ConsistencyValidatorDialog._count_visible_checked_problems
    _iter_visible_problem_rows = ConsistencyValidatorDialog._iter_visible_problem_rows
    _problem_for_row = ConsistencyValidatorDialog._problem_for_row
    _set_check_state_silently = ConsistencyValidatorDialog._set_check_state_silently

    def __init__(self):
        self.resolved_problem_keys = set()
        self.batch_fix_updates = 0
        self.problems_table = QTableWidget(1, 8)
        check_item = QTableWidgetItem()
        check_item.setCheckState(Qt.CheckState.Checked)
        self.problems_table.setItem(0, 0, check_item)
        id_item = QTableWidgetItem("p1")
        id_item.setData(Qt.ItemDataRole.UserRole, {"id": "p1", "chapter": "chapter.xhtml"})
        self.problems_table.setItem(0, 1, id_item)
        for col in range(2, 8):
            self.problems_table.setItem(0, col, QTableWidgetItem(""))

    def _update_batch_fix_button_state(self):
        self.batch_fix_updates += 1


class _SessionSaveHarness:
    _problem_key = ConsistencyValidatorDialog._problem_key
    _is_problem_resolved = ConsistencyValidatorDialog._is_problem_resolved
    _save_session = ConsistencyValidatorDialog._save_session

    def __init__(self, session_file):
        self.resolved_problem_keys = {"id:p1"}
        self.session_file = Path(session_file)
        self.engine = SimpleNamespace(
            chapter_problems_map={
                "chapter.xhtml": [
                    {"id": "p1", "chapter": "chapter.xhtml"},
                    {"id": "p2", "chapter": "chapter.xhtml"},
                ]
            },
            glossary_session=SimpleNamespace(
                processed_chapters=[],
                to_dict=lambda: {"characters": [], "terms": [], "plots": []},
            ),
        )
        self.selected_chapter_ids = set()


class ConsistencyPreviewHelpersTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_configure_wrapped_text_edit_enables_word_wrap(self):
        editor = QTextEdit()

        configure_wrapped_text_edit(editor, read_only=True)

        self.assertTrue(editor.isReadOnly())
        self.assertEqual(editor.lineWrapMode(), QTextEdit.LineWrapMode.WidgetWidth)
        self.assertEqual(
            editor.wordWrapMode(),
            QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere,
        )

    def test_build_changed_line_format_uses_readable_foreground(self):
        changed_format = build_changed_line_format()

        self.assertEqual(changed_format.background().color().name(), "#c8e6c9")
        self.assertEqual(changed_format.foreground().color().name(), "#0f2411")

    def test_manual_fix_starts_editable_preview_without_ai_result(self):
        harness = _ManualFixHarness()

        harness.start_manual_fix()

        self.assertTrue(harness.apply_btn.enabled)
        self.assertEqual(harness.corrected_text.toPlainText(), "<p>Hello world</p>\n")
        self.assertEqual(
            harness.fix_previews["p1"],
            ("<p>Hello world</p>\n", "<p>Hello world</p>\n"),
        )
        self.assertTrue(any("Ручное исправление" in entry for entry in harness.logs))

    def test_manual_preview_edits_are_kept_when_selection_changes(self):
        harness = _ManualFixHarness()
        harness.start_manual_fix()

        harness.corrected_text.setPlainText("<p>Hello fixed world</p>")
        harness._sync_corrected_preview_content()

        self.assertEqual(
            harness.fix_previews["p1"],
            ("<p>Hello world</p>\n", "<p>Hello fixed world</p>"),
        )

    def test_resolved_problem_is_unchecked_and_not_counted_for_batch_fix(self):
        harness = _ResolvedProblemHarness()
        problem = {"id": "p1", "chapter": "chapter.xhtml"}

        harness._mark_problem_resolved(problem, row=0)

        self.assertTrue(harness._is_problem_resolved(problem))
        self.assertEqual(
            harness.problems_table.item(0, 0).checkState(),
            Qt.CheckState.Unchecked,
        )
        self.assertEqual(harness._count_visible_checked_problems(), (0, 0))
        self.assertEqual(harness.batch_fix_updates, 1)

    def test_incomplete_problem_row_is_ignored_during_batch_count(self):
        harness = _ResolvedProblemHarness()
        harness.problems_table.setRowCount(2)
        check_item = QTableWidgetItem()
        check_item.setCheckState(Qt.CheckState.Checked)
        harness.problems_table.setItem(1, 0, check_item)

        self.assertEqual(harness._count_visible_checked_problems(), (1, 1))

    def test_session_save_skips_resolved_problems(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session_file = Path(temp_dir) / "consistency_session.json"
            harness = _SessionSaveHarness(session_file)

            harness._save_session()

            with open(session_file, "r", encoding="utf-8") as fh:
                payload = json.load(fh)

        self.assertEqual(
            payload["problems"],
            [{"id": "p2", "chapter": "chapter.xhtml"}],
        )


if __name__ == "__main__":
    unittest.main()
