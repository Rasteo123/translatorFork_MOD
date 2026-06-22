import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.ui.shell import ShellPage


class ShellPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_is_qwidget_with_default_hooks(self):
        page = ShellPage()
        self.addCleanup(page.close)
        self.assertIsInstance(page, QtWidgets.QWidget)
        self.assertEqual(page.get_page_title(), "")
        self.assertTrue(page.can_leave())
        # default hooks are callable and do nothing
        page.on_enter()
        page.on_leave()

    def test_page_title_attr_is_reported(self):
        class TitledPage(ShellPage):
            page_title = "Валидация"

        page = TitledPage()
        self.addCleanup(page.close)
        self.assertEqual(page.get_page_title(), "Валидация")

    def test_has_navigation_signals(self):
        page = ShellPage()
        self.addCleanup(page.close)
        received = []
        page.request_back.connect(lambda: received.append("back"))
        page.request_push.connect(lambda p: received.append(p))
        page.request_back.emit()
        sentinel = object()
        page.request_push.emit(sentinel)
        self.assertEqual(received, ["back", sentinel])
