import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets  # noqa: F401  (ensures a QApplication-capable env import)

from gemini_translator.ui.pages.qidian_creator_page import QidianCreatorPage
from gemini_translator.ui.dialogs.qidian_rulate_creator import _split_csv
from gemini_translator.ui.shell import ShellPage


class QidianCreatorPageContractTests(unittest.TestCase):
    def test_is_shell_page_subclass(self):
        self.assertTrue(issubclass(QidianCreatorPage, ShellPage))

    def test_page_title(self):
        self.assertEqual(QidianCreatorPage.page_title, "Qidian → Rulate")

    def test_split_csv_dedupes_and_strips(self):
        self.assertEqual(_split_csv("a, b ,a\nc"), ["a", "b", "c"])
        self.assertEqual(_split_csv(""), [])
