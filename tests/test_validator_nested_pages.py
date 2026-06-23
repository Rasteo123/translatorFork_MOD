import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets

from gemini_translator.ui.dialogs import consistency_checker as consistency_module
from gemini_translator.ui.dialogs import setup as setup_dialog_module
from gemini_translator.ui.dialogs import validation as validation_module
from gemini_translator.ui.dialogs.setup import InitialSetupPage
from gemini_translator.ui.dialogs.validation import TranslationValidatorPage
from gemini_translator.ui.shell import ShellPage


class _LabelStub:
    def __init__(self):
        self.text = ""

    def setText(self, text):
        self.text = text


class _ButtonStub:
    def __init__(self):
        self.enabled = False

    def setEnabled(self, value):
        self.enabled = bool(value)


class _ValidatorHarness(ShellPage):
    def __init__(self):
        super().__init__()
        self.lbl_status = _LabelStub()
        self.btn_save_changes = _ButtonStub()
        self.results_data = {0: {"translated_html": "old", "internal_html_path": "chapter.xhtml"}}
        self._fixer_filter_state = {"source_filter": "all"}
        self._fixer_data_fingerprint = "old"
        self.marked_rows = []
        self.reapplied = 0
        self.comparison_updates = 0
        self.stats_updates = 0
        self.recalculated_rows = []
        self.applied_changes = []
        self.navigated = []
        self.retried = []

    def _ensure_row_translated_html_loaded(self, row):
        return self.results_data[row]["translated_html"]

    def _mark_row_changed_by_ai_repair(self, row):
        self.marked_rows.append(row)

    def reapply_filters(self):
        self.reapplied += 1

    def update_comparison_view(self):
        self.comparison_updates += 1

    def _recalculate_untranslated_words_for_rows(self, rows):
        self.recalculated_rows.append(list(rows))

    def _recalc_untranslated_stats_ui(self):
        self.stats_updates += 1

    def _apply_untranslated_fixer_changes(self, changes, soup_cache, save_immediately=False, show_feedback=True):
        self.applied_changes.append((changes, soup_cache, save_immediately, show_feedback))
        return {"replacements": len(changes)}

    def navigate_to_problem_chapter(self, payload):
        self.navigated.append(payload)

    def mark_chapters_for_retry(self, chapter_paths):
        self.retried.append(chapter_paths)


class _FakeAIRepairReviewPage(ShellPage):
    result_ready = QtCore.pyqtSignal(bool)

    def __init__(self, candidates, parent=None):
        super().__init__(parent)
        self.candidates = candidates
        self.html_by_row = {0: "new"}

    def selected_html_by_row(self):
        return dict(self.html_by_row)


class _FakeUntranslatedFixerPage(ShellPage):
    result_ready = QtCore.pyqtSignal(bool)
    navigate_to_chapter_requested = QtCore.pyqtSignal(dict)
    mark_chapters_for_retry_requested = QtCore.pyqtSignal(list)

    def __init__(self, data, parent=None, initial_source_filter="all"):
        super().__init__(parent)
        self.data = data
        self.initial_source_filter = initial_source_filter
        self.restored = None
        self.filter_state = {"source_filter": "user"}
        self.glossary_updates = False
        self.changes = [{"row_idx": 0, "new_context": "fixed"}]
        self.save_immediately = True

    def restore_filter_state(self, saved_state, restore_selection=False):
        self.restored = (saved_state, restore_selection)

    def save_filter_state(self):
        return dict(self.filter_state)

    def has_glossary_updates(self):
        return self.glossary_updates

    def get_changes(self):
        return list(self.changes)

    def should_save_immediately(self):
        return self.save_immediately


class _FakeConsistencyPage(ShellPage):
    def __init__(self, chapters, settings_manager, parent=None, project_manager=None):
        super().__init__(parent)
        self.chapters = chapters
        self.settings_manager = settings_manager
        self.project_manager = project_manager
        self.chunk_stats_updated = False

    def _update_chunk_stats(self):
        self.chunk_stats_updated = True


class _InitialSetupHarness(ShellPage):
    def __init__(self):
        super().__init__()
        self.project_manager = object()
        self.settings_manager = object()


class ValidatorNestedPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.validator = _ValidatorHarness()
        self.addCleanup(self.validator.close)
        self.pushed_pages = []
        self.validator.request_push.connect(self.pushed_pages.append)

    def test_ai_repair_review_is_pushed_and_applies_accepted_result(self):
        candidates = [{"row": 0, "original_html": "old", "segments": [], "changes": []}]
        with (
            patch.object(validation_module, "AIRepairReviewDialog", side_effect=AssertionError("modal path used")),
            patch.object(validation_module, "AIRepairReviewPage", _FakeAIRepairReviewPage, create=True),
            patch.object(validation_module.QMessageBox, "information"),
        ):
            TranslationValidatorPage._push_ai_repair_review_page(self.validator, candidates, True, 0, [])

        self.assertEqual(len(self.pushed_pages), 1)
        page = self.pushed_pages[0]
        self.assertIsInstance(page, _FakeAIRepairReviewPage)

        back_requests = []
        page.request_back.connect(lambda: back_requests.append(True))
        with patch.object(validation_module.QMessageBox, "information"):
            page.result_ready.emit(True)

        self.assertEqual(self.validator.results_data[0]["translated_html"], "new")
        self.assertEqual(self.validator.marked_rows, [0])
        self.assertTrue(self.validator.btn_save_changes.enabled)
        self.assertEqual(self.validator._fixer_data_fingerprint, None)
        self.assertEqual(self.validator.reapplied, 1)
        self.assertEqual(self.validator.comparison_updates, 1)
        self.assertEqual(back_requests, [True])

    def test_untranslated_fixer_is_pushed_and_applies_accepted_result(self):
        data = [{"row_idx": 0, "context": "old"}]
        soup_cache = {0: object()}
        with (
            patch.object(validation_module, "UntranslatedFixerDialog", side_effect=AssertionError("modal path used")),
            patch.object(validation_module, "UntranslatedFixerPage", _FakeUntranslatedFixerPage, create=True),
            patch.object(validation_module.QMessageBox, "information"),
        ):
            TranslationValidatorPage._push_untranslated_fixer_page(
                self.validator,
                data,
                soup_cache,
                effective_source_filter="system",
                saved_state={"source_filter": "system"},
                new_fp="new-fp",
            )

        self.assertEqual(len(self.pushed_pages), 1)
        page = self.pushed_pages[0]
        self.assertIsInstance(page, _FakeUntranslatedFixerPage)
        self.assertEqual(page.data, data)
        self.assertEqual(page.initial_source_filter, "system")
        self.assertEqual(page.restored, ({"source_filter": "system"}, False))
        self.assertEqual(self.validator._fixer_data_fingerprint, "new-fp")

        back_requests = []
        page.request_back.connect(lambda: back_requests.append(True))
        with patch.object(validation_module.QMessageBox, "information"):
            page.result_ready.emit(True)

        self.assertEqual(self.validator._fixer_filter_state, {"source_filter": "user"})
        self.assertEqual(self.validator.applied_changes, [(page.changes, soup_cache, True, True)])
        self.assertEqual(self.validator._fixer_data_fingerprint, None)
        self.assertEqual(back_requests, [True])

    def test_validator_consistency_checker_is_pushed_as_page(self):
        self.validator.settings_manager = object()
        self.validator.project_manager = object()
        chapters = [{"name": "Chapter 1", "content": "text", "path": "chapter.xhtml"}]

        with patch.object(consistency_module, "ConsistencyValidatorPage", _FakeConsistencyPage, create=True):
            TranslationValidatorPage._push_consistency_checker_page(self.validator, chapters)

        self.assertEqual(len(self.pushed_pages), 1)
        page = self.pushed_pages[0]
        self.assertIsInstance(page, _FakeConsistencyPage)
        self.assertEqual(page.chapters, chapters)
        self.assertIs(page.settings_manager, self.validator.settings_manager)
        self.assertIs(page.project_manager, self.validator.project_manager)
        self.assertTrue(page.chunk_stats_updated)

    def test_session_consistency_checker_is_pushed_as_page(self):
        session = _InitialSetupHarness()
        self.addCleanup(session.close)
        pushed_pages = []
        session.request_push.connect(pushed_pages.append)

        chapters = [{"name": "Chapter 1", "content": "text", "path": "chapter.xhtml"}]
        with (
            patch.object(setup_dialog_module, "load_project_chapters_for_consistency", return_value=chapters),
            patch.object(consistency_module, "ConsistencyValidatorDialog", side_effect=AssertionError("modal path used")),
            patch.object(consistency_module, "ConsistencyValidatorPage", _FakeConsistencyPage, create=True),
        ):
            InitialSetupPage.open_ai_consistency_checker(session)

        self.assertEqual(len(pushed_pages), 1)
        page = pushed_pages[0]
        self.assertIsInstance(page, _FakeConsistencyPage)
        self.assertEqual(page.chapters, chapters)
        self.assertIs(page.settings_manager, session.settings_manager)
        self.assertIs(page.project_manager, session.project_manager)
        self.assertTrue(page.chunk_stats_updated)


if __name__ == "__main__":
    unittest.main()
