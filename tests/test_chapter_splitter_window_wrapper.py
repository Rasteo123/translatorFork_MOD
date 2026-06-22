import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.ui.dialogs.chapter_splitter import ChapterSplitterWindow
from gemini_translator.ui.pages.chapter_splitter_page import ChapterSplitterPage


class ChapterSplitterWrapperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _window(self):
        win = ChapterSplitterWindow()

        def _safe_close():
            # Reset any fake worker so closeEvent cannot pop a blocking dialog.
            win.page.worker = None
            with patch(
                "gemini_translator.ui.dialogs.chapter_splitter.prompt_return_to_menu",
                return_value="cancel",
            ):
                win.close()

        self.addCleanup(_safe_close)
        return win

    def test_is_qmainwindow_hosting_the_page(self):
        win = self._window()
        self.assertIsInstance(win, QtWidgets.QMainWindow)
        self.assertIsInstance(win.page, ChapterSplitterPage)
        self.assertIs(win.centralWidget(), win.page)

    def test_window_title_preserved(self):
        self.assertEqual(self._window().windowTitle(), "Chapter Splitter")

    def test_return_to_menu_blocked_while_processing(self):
        win = self._window()

        class _FakeWorker:
            def isRunning(self):
                return True

        win.page.worker = _FakeWorker()
        win._returning_to_main_menu = False
        with patch("gemini_translator.ui.dialogs.chapter_splitter.QMessageBox.warning"):
            win._return_to_menu()
        self.assertFalse(win._returning_to_main_menu)
