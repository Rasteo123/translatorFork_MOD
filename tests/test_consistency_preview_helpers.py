import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PyQt6 import QtWidgets
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QTextOption
from PyQt6.QtWidgets import QMessageBox, QTableWidget, QTableWidgetItem, QTextEdit

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
    _problem_key = ConsistencyValidatorDialog._problem_key
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
    _build_session_payload = ConsistencyValidatorDialog._build_session_payload
    _save_session = ConsistencyValidatorDialog._save_session

    def __init__(self, session_file):
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
            get_completed_chunk_keys=lambda: {"analysis": ["chunk-key-1"]},
            get_session_signature=lambda: "signature-1",
            get_request_response_trace=lambda: [{"phase": "analysis", "prompt": "p", "response": "r"}],
        )
        self.selected_chapter_ids = set()
        self.resolved_problem_keys = {
            self._problem_key({"id": "p1", "chapter": "chapter.xhtml"})
        }


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

        problem_key = harness._problem_key(harness.current_problem)
        self.assertTrue(harness.apply_btn.enabled)
        self.assertEqual(harness.corrected_text.toPlainText(), "<p>Hello world</p>\n")
        self.assertEqual(
            harness.fix_previews[problem_key],
            ("<p>Hello world</p>\n", "<p>Hello world</p>\n"),
        )
        self.assertNotIn("p1", harness.fix_previews)
        self.assertTrue(any("Ручное исправление" in entry for entry in harness.logs))

    def test_manual_preview_edits_are_kept_when_selection_changes(self):
        harness = _ManualFixHarness()
        harness.start_manual_fix()

        harness.corrected_text.setPlainText("<p>Hello fixed world</p>")
        harness._sync_corrected_preview_content()

        problem_key = harness._problem_key(harness.current_problem)
        self.assertEqual(
            harness.fix_previews[problem_key],
            ("<p>Hello world</p>\n", "<p>Hello fixed world</p>"),
        )

    def test_manual_previews_with_same_id_use_distinct_problem_keys(self):
        harness = _ManualFixHarness()
        first_key = harness._problem_key(harness.current_problem)
        harness.start_manual_fix()

        harness.current_problem = {
            "id": "p1",
            "chapter": "chapter.xhtml",
            "type": "typo",
            "quote": "Hello",
            "description": "Different issue",
            "suggestion": "Hi",
        }
        second_key = harness._problem_key(harness.current_problem)
        harness.start_manual_fix()

        self.assertNotEqual(first_key, second_key)
        self.assertIn(first_key, harness.fix_previews)
        self.assertIn(second_key, harness.fix_previews)

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

    def test_same_id_in_different_problem_type_is_not_resolved_together(self):
        harness = _ResolvedProblemHarness()
        typo_problem = {
            "id": "2",
            "chapter": "chapter.xhtml",
            "type": "typo",
            "quote": "teh",
            "description": "typo",
            "suggestion": "the",
        }
        gender_problem = {
            "id": "2",
            "chapter": "chapter.xhtml",
            "type": "gender_mismatch",
            "quote": "она сказал",
            "description": "gender",
            "suggestion": "она сказала",
        }
        harness.problems_table.item(0, 1).setData(Qt.ItemDataRole.UserRole, typo_problem)
        harness.problems_table.setRowCount(2)
        check_item = QTableWidgetItem()
        check_item.setCheckState(Qt.CheckState.Checked)
        harness.problems_table.setItem(1, 0, check_item)
        id_item = QTableWidgetItem("2")
        id_item.setData(Qt.ItemDataRole.UserRole, gender_problem)
        harness.problems_table.setItem(1, 1, id_item)
        for col in range(2, 8):
            harness.problems_table.setItem(1, col, QTableWidgetItem(""))

        harness._mark_problem_resolved(typo_problem, row=0)

        self.assertTrue(harness._is_problem_resolved(typo_problem))
        self.assertFalse(harness._is_problem_resolved(gender_problem))
        self.assertEqual(
            harness.problems_table.item(1, 0).checkState(),
            Qt.CheckState.Checked,
        )
        self.assertEqual(harness._count_visible_checked_problems(), (1, 1))

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
        self.assertEqual(payload["completed_chunks"], {"analysis": ["chunk-key-1"]})
        self.assertEqual(payload["session_signature"], "signature-1")
        self.assertEqual(
            payload["request_response_trace"],
            [{"phase": "analysis", "prompt": "p", "response": "r"}],
        )

    def test_save_custom_prompts_merges_existing_json_and_keeps_unknown_keys(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            prompts_file = Path(temp_dir) / "consistency_prompts.json"
            prompts_file.write_text(
                json.dumps(
                    {
                        "consistency_analysis": ["old"],
                        "fast_proofread_3_1_analysis": ["keep fast"],
                        "source_reference": {"intro": ["keep source"]},
                        "unknown_section": {"keep": True},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            editor = QTextEdit()
            editor.setPlainText("new analysis\nsecond line")
            harness = SimpleNamespace(prompts_editors={"consistency_analysis": editor})

            with patch(
                "gemini_translator.ui.dialogs.consistency_checker.api_config.get_resource_path",
                return_value=prompts_file,
            ), patch.object(QMessageBox, "information") as information, patch.object(
                QMessageBox,
                "critical",
            ) as critical:
                ConsistencyValidatorDialog._save_custom_prompts(harness)

            payload = json.loads(prompts_file.read_text(encoding="utf-8"))
            leftovers = list(Path(temp_dir).glob(".consistency_prompts.json.*.tmp"))

        self.assertTrue(information.called)
        self.assertFalse(critical.called)
        self.assertEqual(leftovers, [])
        self.assertEqual(payload["consistency_analysis"], ["new analysis", "second line"])
        self.assertEqual(payload["fast_proofread_3_1_analysis"], ["keep fast"])
        self.assertEqual(payload["source_reference"], {"intro": ["keep source"]})
        self.assertEqual(payload["unknown_section"], {"keep": True})


if __name__ == "__main__":
    unittest.main()
