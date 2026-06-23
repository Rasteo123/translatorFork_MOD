import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets

from gemini_translator.ui.shell import ShellPage
from gemini_translator.ui.dialogs.glossary_dialogs import ai_generation as ai_generation_module
from gemini_translator.ui.widgets import glossary_widget as glossary_widget_module
from gemini_translator.ui.widgets.glossary_widget import GlossaryWidget


class _AutoTranslateWidgetStub:
    def __init__(self):
        self.refresh_count = 0

    def refresh_glossary_presets(self):
        self.refresh_count += 1


class InitialSetupPage(ShellPage):
    def __init__(self):
        super().__init__()
        self.output_folder = None
        self.html_files = ["chapter.xhtml"]
        self.selected_file = "/tmp/book.epub"
        self.project_manager = object()
        self.sync_count = 0
        self.prepare_count = 0
        self.auto_translate_widget = _AutoTranslateWidgetStub()

    def get_settings(self):
        return {"provider": "test-provider"}

    def _check_and_sync_active_session(self):
        self.sync_count += 1

    def _prepare_and_display_tasks(self, clean_rebuild=False):
        if clean_rebuild:
            self.prepare_count += 1


class FakeGlossaryManagerPage(ShellPage):
    result_ready = QtCore.pyqtSignal(bool)

    def __init__(self, parent=None, mode="standalone", project_path=None):
        super().__init__(parent)
        self.mode = mode
        self.project_path = project_path
        self.glossary = []
        self.saved_marks = []
        self.saved_to_project = False

    def set_glossary(self, glossary):
        self.glossary = [entry.copy() for entry in glossary]

    def get_glossary(self):
        return [entry.copy() for entry in self.glossary]

    def mark_current_state_as_saved(self, saved_to_project=False):
        self.saved_marks.append(saved_to_project)

    def is_current_state_saved_to_project(self):
        return self.saved_to_project


class FakeGenerationSessionPage(ShellPage):
    generation_finished = QtCore.pyqtSignal(list, set)
    result_ready = QtCore.pyqtSignal(bool)

    def __init__(
        self,
        settings_manager,
        initial_glossary,
        merge_mode,
        html_files,
        epub_path,
        project_manager,
        initial_ui_settings,
        parent=None,
    ):
        super().__init__(parent)
        self.settings_manager = settings_manager
        self.initial_glossary = [entry.copy() for entry in initial_glossary]
        self.merge_mode = merge_mode
        self.html_files = list(html_files)
        self.epub_path = epub_path
        self.project_manager = project_manager
        self.initial_ui_settings = dict(initial_ui_settings)


class GlossaryWidgetManagerNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.session = InitialSetupPage()
        self.addCleanup(self.session.close)
        self.widget = GlossaryWidget(self.session)
        self.addCleanup(self.widget.close)
        self.widget.set_glossary([{"original": "alpha", "rus": "альфа", "note": "", "timestamp": 123.0}])

    def test_session_manager_is_pushed_as_page_and_applies_accepted_result(self):
        pushed_pages = []
        self.session.request_push.connect(pushed_pages.append)

        with (
            patch.object(glossary_widget_module, "GlossaryToolWindow", side_effect=AssertionError("modal path used")),
            patch.object(glossary_widget_module, "GlossaryManagerPage", FakeGlossaryManagerPage, create=True),
        ):
            self.widget._open_manager()

        self.assertEqual(len(pushed_pages), 1)
        page = pushed_pages[0]
        self.assertIsInstance(page, FakeGlossaryManagerPage)
        self.assertEqual(page.mode, "dialog")
        self.assertIsNone(page.project_path)
        self.assertEqual(page.glossary, [{"original": "alpha", "rus": "альфа", "note": "", "timestamp": 123.0}])
        self.assertEqual(page.saved_marks, [False])
        self.assertTrue(self.session.isEnabled())

        back_requests = []
        page.request_back.connect(lambda: back_requests.append(True))
        page.glossary = [{"original": "beta", "rus": "бета", "note": "", "timestamp": 456.0}]
        page.result_ready.emit(True)

        self.assertEqual(self.widget.get_glossary(), [{"original": "beta", "rus": "бета", "note": "", "timestamp": 456.0}])
        self.assertEqual(back_requests, [True])

    def test_ai_generation_is_pushed_as_page_and_applies_result(self):
        pushed_pages = []
        self.session.request_push.connect(pushed_pages.append)

        with (
            patch.object(ai_generation_module, "GenerationSessionDialog", side_effect=AssertionError("modal path used")),
            patch.object(ai_generation_module, "GenerationSessionPage", FakeGenerationSessionPage, create=True),
            patch.object(glossary_widget_module.QMessageBox, "information"),
        ):
            self.widget._open_ai_generation_dialog()

        self.assertEqual(len(pushed_pages), 1)
        page = pushed_pages[0]
        self.assertIsInstance(page, FakeGenerationSessionPage)
        self.assertEqual(page.initial_glossary, [{"original": "alpha", "rus": "альфа", "note": "", "timestamp": 123.0}])
        self.assertEqual(page.html_files, ["chapter.xhtml"])
        self.assertEqual(page.epub_path, "/tmp/book.epub")
        self.assertEqual(page.initial_ui_settings, {"provider": "test-provider"})
        self.assertTrue(self.session.isEnabled())

        back_requests = []
        page.request_back.connect(lambda: back_requests.append(True))
        with patch.object(glossary_widget_module.QMessageBox, "exec", return_value=None):
            page.generation_finished.emit(
                [{"original": "beta", "rus": "бета", "note": "", "timestamp": 456.0}],
                {"chapter.xhtml"},
            )
        page.result_ready.emit(True)

        self.assertEqual(self.widget.get_glossary(), [{"original": "beta", "rus": "бета", "note": "", "timestamp": 456.0}])
        self.assertEqual(back_requests, [True])
        self.assertEqual(self.session.sync_count, 1)
        self.assertEqual(self.session.prepare_count, 1)
        self.assertEqual(self.session.auto_translate_widget.refresh_count, 1)
