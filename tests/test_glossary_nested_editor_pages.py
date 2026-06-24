import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets

# Import through GlossaryWidget first to keep the existing glossary module
# circular import settled in the same way as the app/tests already do.
from gemini_translator.ui.widgets.glossary_widget import GlossaryWidget  # noqa: F401

from gemini_translator.ui.dialogs import glossary as glossary_module
from gemini_translator.ui.dialogs.glossary_dialogs.ai_correction import CorrectionSessionPage
from gemini_translator.ui.dialogs.glossary import GlossaryManagerPage
from gemini_translator.ui.shell import ShellPage


class _TableStub:
    def setCurrentItem(self, item):
        self.current_item = item


class _CheckStub:
    def __init__(self, checked=False):
        self._checked = checked

    def isChecked(self):
        return self._checked


class _GlossaryManagerHarness(ShellPage):
    def __init__(self):
        super().__init__()
        self.settings_manager = object()
        self.table = _TableStub()
        self.logic = object()
        self.associated_project_path = None
        self.core_term_candidates = {"pattern"}
        self.untranslated_residue = {"Fragment": {"entries_with_residue": []}}
        self.direct_conflicts = {}
        self.reverse_issues = {"альфа": {"complete": [], "orphans": []}}
        self.overlap_groups = {"alpha": ["alpha prime"]}
        self.inverted_overlaps = {"alpha prime": ["alpha"]}
        self.applied = []
        self.glossary = [{"original": "alpha", "rus": "альфа", "note": ""}]

    def get_glossary(self):
        return [entry.copy() for entry in self.glossary]

    def _is_glossary_empty(self):
        return False

    def _apply_patch_and_log_history(self, patch_list, source, old_glossary):
        self.applied.append((patch_list, source, old_glossary))


def _init_shell_glossary_owner(self):
    ShellPage.__init__(self)
    self.direct_conflicts = {}
    self.reverse_issues = {}
    self.overlap_groups = {}
    self.inverted_overlaps = {}
    self.associated_project_path = None
    self.associated_epub_path = None
    self.logic = object()


_ShellGlossaryOwner = type(
    "GlossaryManagerPage",
    (ShellPage,),
    {
        "__init__": _init_shell_glossary_owner,
        "get_glossary": lambda self: [
            {"original": "alpha", "rus": "альфа", "note": ""},
            {"original": "beta", "rus": "бета", "note": "note"},
        ],
    },
)


class _FakeCorrectionSessionPage(ShellPage):
    correction_accepted = QtCore.pyqtSignal(list)
    result_ready = QtCore.pyqtSignal(bool)

    def __init__(self, settings_manager=None, parent=None):
        super().__init__(parent)
        self.settings_manager = settings_manager


class _FakeCoreTermAnalyzerPage(ShellPage):
    result_ready = QtCore.pyqtSignal(bool)

    def __init__(self, original_glossary_list, logic, analysis_results, pymorphy_available, parent=None):
        super().__init__(parent)
        self.original_glossary_list = original_glossary_list
        self.logic = logic
        self.analysis_results = analysis_results
        self.pymorphy_available = pymorphy_available
        self.patch = [{"before": {"original": "alpha"}, "after": {"original": "beta"}}]

    def get_patch(self):
        return list(self.patch)


class _FakeResidueAnalyzerPage(ShellPage):
    result_ready = QtCore.pyqtSignal(bool)

    def __init__(self, residue_map, original_glossary_list, settings_manager, parent=None):
        super().__init__(parent)
        self.residue_map = residue_map
        self.original_glossary_list = original_glossary_list
        self.settings_manager = settings_manager
        self.patch = [{"before": {"original": "alpha"}, "after": {"original": "gamma"}}]

    def get_final_patch(self):
        return list(self.patch)


class _FakeTermFrequencyAnalyzerPage(ShellPage):
    result_ready = QtCore.pyqtSignal(bool)

    def __init__(self, glossary_data, epub_path=None, parent=None):
        super().__init__(parent)
        self.glossary_data = glossary_data
        self.epub_path = epub_path
        self.patch = [{"before": {"original": "alpha"}, "after": {"original": "alpha", "rus": "альфа!"}}]

    def get_patch(self):
        return list(self.patch)


class _FakeReverseConflictResolverPage(ShellPage):
    result_ready = QtCore.pyqtSignal(bool)

    def __init__(self, reverse_issues, current_glossary, parent=None, morph=None):
        super().__init__(parent)
        self.reverse_issues = reverse_issues
        self.current_glossary = current_glossary
        self.morph = morph
        self.patch = [{"before": {"original": "alpha"}, "after": {"original": "alpha", "note": "reverse"}}]

    def get_patch(self):
        return list(self.patch)


class _FakeComplexOverlapResolverPage(ShellPage):
    result_ready = QtCore.pyqtSignal(bool)

    def __init__(self, overlap_groups, inverted_groups, original_glossary, pymorphy_available, parent=None):
        super().__init__(parent)
        self.overlap_groups = overlap_groups
        self.inverted_groups = inverted_groups
        self.original_glossary = original_glossary
        self.pymorphy_available = pymorphy_available
        self.patch = [{"before": {"original": "alpha"}, "after": {"original": "alpha", "note": "overlap"}}]

    def get_patch(self):
        return list(self.patch)


class _FakeGroupAnalysisPage(ShellPage):
    result_ready = QtCore.pyqtSignal(bool)

    def __init__(self, full_glossary, parent=None):
        super().__init__(parent)
        self.full_glossary = full_glossary


class GlossaryNestedEditorPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.manager = _GlossaryManagerHarness()
        self.addCleanup(self.manager.close)
        self.pushed_pages = []
        self.manager.request_push.connect(self.pushed_pages.append)

    def _assert_result_pops(self, page):
        back_requests = []
        page.request_back.connect(lambda: back_requests.append(True))
        page.result_ready.emit(True)
        self.assertEqual(back_requests, [True])

    def test_ai_correction_session_stays_open_after_applying_patch_result(self):
        with (
            patch.object(glossary_module, "CorrectionSessionDialog", side_effect=AssertionError("modal path used")),
            patch.object(glossary_module, "CorrectionSessionPage", _FakeCorrectionSessionPage, create=True),
        ):
            GlossaryManagerPage._start_ai_correction_session(self.manager)

        self.assertEqual(len(self.pushed_pages), 1)
        page = self.pushed_pages[0]
        self.assertIsInstance(page, _FakeCorrectionSessionPage)
        self.assertIs(page.settings_manager, self.manager.settings_manager)

        back_requests = []
        page.request_back.connect(lambda: back_requests.append(True))
        patch_list = [{"before": None, "after": {"original": "beta"}}]
        page.correction_accepted.emit(patch_list)

        self.assertEqual(self.manager.applied, [(patch_list, "AI-коррекция", self.manager.glossary)])
        self.assertEqual(back_requests, [])

        page.result_ready.emit(False)

        self.assertEqual(back_requests, [True])

    def test_ai_correction_session_uses_original_glossary_owner_after_shell_reparent(self):
        self.app.engine = SimpleNamespace(task_manager=None)
        owner = _ShellGlossaryOwner()
        self.addCleanup(owner.close)
        page = CorrectionSessionPage(object(), owner)
        stack = QtWidgets.QStackedWidget()
        self.addCleanup(stack.close)
        stack.addWidget(page)

        page.cb_context = _CheckStub(True)
        page.cb_notes = _CheckStub(False)
        page.cb_direct = _CheckStub(False)
        page.cb_reverse = _CheckStub(False)
        page.cb_overlaps = _CheckStub(False)
        page.cb_frequency_filter = _CheckStub(False)

        data_for_ai, *_ = page._get_data_and_estimate_tokens()

        self.assertIsNotNone(data_for_ai)
        self.assertIn("alpha", data_for_ai)

    def test_core_term_analyzer_is_pushed_and_applies_accepted_patch(self):
        with (
            patch.object(glossary_module, "CoreTermAnalyzerDialog", side_effect=AssertionError("modal path used")),
            patch.object(glossary_module, "CoreTermAnalyzerPage", _FakeCoreTermAnalyzerPage, create=True),
        ):
            GlossaryManagerPage.resolve_core_terms(self.manager)

        self.assertEqual(len(self.pushed_pages), 1)
        page = self.pushed_pages[0]
        self.assertIsInstance(page, _FakeCoreTermAnalyzerPage)
        self.assertEqual(page.original_glossary_list, self.manager.glossary)
        self.assertIs(page.logic, self.manager.logic)
        self.assertEqual(page.analysis_results, self.manager.core_term_candidates)

        self._assert_result_pops(page)
        self.assertEqual(self.manager.applied, [(page.patch, "Анализ по паттернам", self.manager.glossary)])

    def test_residue_analyzer_is_pushed_and_applies_accepted_patch(self):
        with (
            patch.object(glossary_module, "ResidueAnalyzerDialog", side_effect=AssertionError("modal path used")),
            patch.object(glossary_module, "ResidueAnalyzerPage", _FakeResidueAnalyzerPage, create=True),
        ):
            GlossaryManagerPage.resolve_untranslated_residue(self.manager)

        self.assertEqual(len(self.pushed_pages), 1)
        page = self.pushed_pages[0]
        self.assertIsInstance(page, _FakeResidueAnalyzerPage)
        self.assertEqual(page.residue_map, self.manager.untranslated_residue)
        self.assertEqual(page.original_glossary_list, self.manager.glossary)
        self.assertIs(page.settings_manager, self.manager.settings_manager)

        self._assert_result_pops(page)
        self.assertEqual(self.manager.applied, [(page.patch, "Анализ остатков", self.manager.glossary)])

    def test_frequency_analyzer_is_pushed_and_applies_accepted_patch(self):
        with (
            patch.object(glossary_module, "TermFrequencyAnalyzerDialog", side_effect=AssertionError("modal path used")),
            patch.object(glossary_module, "TermFrequencyAnalyzerPage", _FakeTermFrequencyAnalyzerPage, create=True),
        ):
            GlossaryManagerPage.open_frequency_analyzer(self.manager)

        self.assertEqual(len(self.pushed_pages), 1)
        page = self.pushed_pages[0]
        self.assertIsInstance(page, _FakeTermFrequencyAnalyzerPage)
        self.assertEqual(page.glossary_data, self.manager.glossary)
        self.assertIsNone(page.epub_path)

        self._assert_result_pops(page)
        self.assertEqual(self.manager.applied, [(page.patch, "Частотный анализ", self.manager.glossary)])

    def test_reverse_conflict_resolver_is_pushed_and_applies_accepted_patch(self):
        with (
            patch.object(glossary_module, "ReverseConflictResolverDialog", side_effect=AssertionError("modal path used")),
            patch.object(glossary_module, "ReverseConflictResolverPage", _FakeReverseConflictResolverPage, create=True),
        ):
            GlossaryManagerPage.resolve_reverse_conflicts(self.manager)

        self.assertEqual(len(self.pushed_pages), 1)
        page = self.pushed_pages[0]
        self.assertIsInstance(page, _FakeReverseConflictResolverPage)
        self.assertEqual(page.reverse_issues, self.manager.reverse_issues)
        self.assertEqual(page.current_glossary, self.manager.glossary)

        self._assert_result_pops(page)
        self.assertEqual(self.manager.applied, [(page.patch, "Обратные конфликты", self.manager.glossary)])

    def test_overlap_resolver_is_pushed_and_applies_accepted_patch(self):
        with (
            patch.object(glossary_module, "ComplexOverlapResolverDialog", side_effect=AssertionError("modal path used")),
            patch.object(glossary_module, "ComplexOverlapResolverPage", _FakeComplexOverlapResolverPage, create=True),
        ):
            GlossaryManagerPage.resolve_overlaps(self.manager)

        self.assertEqual(len(self.pushed_pages), 1)
        page = self.pushed_pages[0]
        self.assertIsInstance(page, _FakeComplexOverlapResolverPage)
        self.assertEqual(page.overlap_groups, self.manager.overlap_groups)
        self.assertEqual(page.inverted_groups, self.manager.inverted_overlaps)
        self.assertEqual(page.original_glossary, {"alpha": {"rus": "альфа", "note": ""}})

        self._assert_result_pops(page)
        self.assertEqual(self.manager.applied, [(page.patch, "Наложения", self.manager.glossary)])

    def test_group_analysis_is_pushed_as_page(self):
        with (
            patch.object(glossary_module, "GroupAnalysisDialog", side_effect=AssertionError("modal path used")),
            patch.object(glossary_module, "GroupAnalysisPage", _FakeGroupAnalysisPage, create=True),
        ):
            GlossaryManagerPage.open_group_analysis(self.manager)

        self.assertEqual(len(self.pushed_pages), 1)
        page = self.pushed_pages[0]
        self.assertIsInstance(page, _FakeGroupAnalysisPage)
        self.assertEqual(page.full_glossary, self.manager.glossary)


if __name__ == "__main__":
    unittest.main()
