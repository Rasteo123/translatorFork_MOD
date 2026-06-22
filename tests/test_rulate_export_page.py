import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.ui.pages.rulate_export_page import RulateExportPage
from gemini_translator.ui.shell import ShellPage


class RulateExportPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _page(self):
        page = RulateExportPage()
        self.addCleanup(page.close)
        return page

    def test_is_shell_page_with_title(self):
        page = self._page()
        self.assertIsInstance(page, ShellPage)
        self.assertEqual(page.get_page_title(), "EPUB → Rulate Markdown")

    def test_core_widgets_exist(self):
        page = self._page()
        for attr in (
            "file_label", "chapters_list_widget", "rename_editor",
            "volume_table", "check_split", "spin_chunk_size",
            "convert_button", "progress_bar", "status_label", "preview_text",
        ):
            self.assertTrue(hasattr(page, attr), f"missing widget: {attr}")

    def test_has_no_in_layout_menu_button(self):
        page = self._page()
        self.assertFalse(hasattr(page, "menu_button"))

    def test_can_leave_true_when_idle(self):
        page = self._page()
        self.assertTrue(page.can_leave())

    def test_can_leave_false_while_converting(self):
        page = self._page()

        class _FakeThread:
            def isRunning(self):
                return True

        page.converter_thread = _FakeThread()
        with patch("gemini_translator.ui.pages.rulate_export_page.QMessageBox.warning"):
            self.assertFalse(page.can_leave())
