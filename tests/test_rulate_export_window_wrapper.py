import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.ui.dialogs.rulate_export import RulateMarkdownExportWindow
from gemini_translator.ui.pages.rulate_export_page import RulateExportPage


class RulateExportWrapperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _window(self):
        win = RulateMarkdownExportWindow()

        def _safe_close():
            # Reset any fake thread so closeEvent does not open a blocking dialog.
            win.page.converter_thread = None
            with patch("gemini_translator.ui.dialogs.rulate_export.prompt_return_to_menu", return_value="cancel"):
                win.close()

        self.addCleanup(_safe_close)
        return win

    def test_is_qmainwindow_hosting_the_page(self):
        win = self._window()
        self.assertIsInstance(win, QtWidgets.QMainWindow)
        self.assertIsInstance(win.page, RulateExportPage)
        self.assertIs(win.centralWidget(), win.page)

    def test_window_title_preserved(self):
        self.assertEqual(self._window().windowTitle(), "EPUB -> Rulate Markdown")

    def test_return_to_menu_blocked_while_converting(self):
        win = self._window()

        class _FakeThread:
            def isRunning(self):
                return True

        win.page.converter_thread = _FakeThread()
        win._returning_to_main_menu = False
        with patch("gemini_translator.ui.dialogs.rulate_export.QMessageBox.warning"):
            win._return_to_menu()
        self.assertFalse(win._returning_to_main_menu)
