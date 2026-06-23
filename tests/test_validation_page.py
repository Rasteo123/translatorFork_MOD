import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest.mock import patch

from PyQt6 import QtWidgets

from gemini_translator.ui.pages.validation_page import TranslationValidatorPage
from gemini_translator.ui.shell import ShellPage


class TranslationValidatorPageContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app.global_version = ""

    def test_is_shell_page_subclass(self):
        self.assertTrue(issubclass(TranslationValidatorPage, ShellPage))

    def test_page_title(self):
        self.assertEqual(TranslationValidatorPage.page_title, "Валидация перевода")

    def test_can_leave_vetoes_while_analysis_running(self):
        from unittest.mock import patch
        from PyQt6.QtWidgets import QMessageBox

        class _Thread:
            def isRunning(self): return True
        class _Stub:
            analysis_thread = _Thread()
        # call the unbound method against a stub; patch the modal to "No"
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.assertFalse(TranslationValidatorPage.can_leave(_Stub()))

    def test_validator_keeps_legacy_single_screen_layout(self):
        with patch.object(TranslationValidatorPage, "_perform_initial_cjk_scan"):
            page = TranslationValidatorPage(
                "/tmp/nonexistent-translations",
                "/tmp/nonexistent-book.epub",
                project_manager=None,
            )
        self.addCleanup(page.deleteLater)

        self.assertFalse(hasattr(page, "main_tabs"))
        self.assertIs(page.table_results.parentWidget(), page.results_widget)
        self.assertIs(page.view_translated.parentWidget(), page.comparison_splitter)
        self.assertFalse(page.findChildren(QtWidgets.QTabWidget))

    def test_comparison_editors_fit_inside_available_shell_height(self):
        with patch.object(TranslationValidatorPage, "_perform_initial_cjk_scan"):
            page = TranslationValidatorPage(
                "/tmp/nonexistent-translations",
                "/tmp/nonexistent-book.epub",
                project_manager=None,
            )
        self.addCleanup(page.deleteLater)

        page.resize(1900, 980)
        page.show()
        self.app.processEvents()

        self.assertLessEqual(page.minimumSizeHint().height(), 980)
        for editor in (page.view_original, page.view_translated):
            editor_bottom = editor.geometry().y() + editor.geometry().height()
            self.assertLessEqual(editor_bottom, page.comparison_splitter.height())

    def test_validator_content_scrolls_when_shell_height_is_tight(self):
        with patch.object(TranslationValidatorPage, "_perform_initial_cjk_scan"):
            page = TranslationValidatorPage(
                "/tmp/nonexistent-translations",
                "/tmp/nonexistent-book.epub",
                project_manager=None,
            )
        self.addCleanup(page.deleteLater)

        self.assertIsInstance(page.content_scroll_area, QtWidgets.QScrollArea)
        self.assertTrue(page.content_scroll_area.widgetResizable())

        page.resize(1180, 620)
        page.show()
        self.app.processEvents()

        self.assertGreater(page.content_scroll_area.verticalScrollBar().maximum(), 0)
