import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.ui.pages.home_page import HomePage
from gemini_translator.ui.shell import ShellPage


class HomePageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _home(self):
        home = HomePage()
        self.addCleanup(home.close)
        return home

    def test_is_shell_page(self):
        self.assertIsInstance(self._home(), ShellPage)

    def test_exposes_all_expected_tool_ids(self):
        home = self._home()
        expected = {
            "translator", "validator", "glossary", "rulate_export",
            "chapter_splitter", "gemini_reader", "ranobelib_uploader",
            "qidian_rulate_creator", "prompt_benchmark",
        }
        self.assertEqual(set(home.tool_buttons.keys()), expected)

    def test_clicking_button_emits_tool_selected(self):
        home = self._home()
        received = []
        home.tool_selected.connect(received.append)
        home.tool_buttons["validator"].click()
        home.tool_buttons["translator"].click()
        self.assertEqual(received, ["validator", "translator"])

    def test_home_page_title_is_empty(self):
        # Home intentionally shows no nav-bar title and no Back button.
        self.assertEqual(self._home().get_page_title(), "")
