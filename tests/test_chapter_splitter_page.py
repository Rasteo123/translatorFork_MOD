import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.ui.pages.chapter_splitter_page import ChapterSplitterPage
from gemini_translator.ui.shell import ShellPage


class ChapterSplitterPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _page(self):
        page = ChapterSplitterPage()
        self.addCleanup(page.close)
        return page

    def test_is_shell_page_with_title(self):
        page = self._page()
        self.assertIsInstance(page, ShellPage)
        self.assertEqual(page.get_page_title(), "Chapter Splitter")

    def test_core_widgets_exist(self):
        page = self._page()
        for attr in (
            "input_edit",
            "output_edit",
            "threshold_spin",
            "target_spin",
            "min_size_spin",
            "process_button",
            "progress_bar",
            "log_output",
        ):
            self.assertTrue(hasattr(page, attr), f"missing widget: {attr}")

    def test_has_no_menu_button(self):
        page = self._page()
        self.assertFalse(hasattr(page, "menu_button"))

    def test_can_leave_true_when_idle(self):
        page = self._page()
        self.assertTrue(page.can_leave())

    def test_can_leave_false_while_processing(self):
        page = self._page()

        class _FakeWorker:
            def isRunning(self):
                return True

        page.worker = _FakeWorker()
        with patch("gemini_translator.ui.pages.chapter_splitter_page.QMessageBox.warning"):
            self.assertFalse(page.can_leave())
