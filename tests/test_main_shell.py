import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.ui.shell import MainShell, ShellPage
from gemini_translator.ui.pages.home_page import HomePage


class TitledPage(ShellPage):
    def __init__(self, title):
        super().__init__()
        self.page_title = title


class MainShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _shell(self):
        shell = MainShell()
        self.addCleanup(shell.close)
        return shell

    def test_is_qmainwindow(self):
        shell = self._shell()
        self.assertIsInstance(shell, QtWidgets.QMainWindow)

    def test_back_button_hidden_on_home_visible_after_push(self):
        shell = self._shell()
        shell.set_home(TitledPage("home"))
        self.assertTrue(shell._back_button.isHidden())
        shell.navigation.push(TitledPage("child"))
        self.assertFalse(shell._back_button.isHidden())

    def test_title_label_follows_current_page(self):
        shell = self._shell()
        shell.set_home(TitledPage("home"))
        self.assertEqual(shell._title_label.text(), "home")
        shell.navigation.push(TitledPage("Валидация"))
        self.assertEqual(shell._title_label.text(), "Валидация")

    def test_back_button_click_pops(self):
        shell = self._shell()
        shell.set_home(TitledPage("home"))
        shell.navigation.push(TitledPage("child"))
        shell._back_button.click()
        self.assertEqual(shell.navigation.depth, 1)
        self.assertTrue(shell._back_button.isHidden())


class ShellHomeIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_selecting_tool_pushes_page_and_back_returns_home(self):
        shell = MainShell()
        self.addCleanup(shell.close)
        home = HomePage()
        shell.set_home(home)

        def open_tool(tool_id):
            page = TitledPage(f"tool:{tool_id}")
            shell.navigation.push(page)

        home.tool_selected.connect(open_tool)

        home.tool_buttons["validator"].click()
        self.assertEqual(shell.navigation.depth, 2)
        self.assertEqual(shell._title_label.text(), "tool:validator")
        self.assertFalse(shell._back_button.isHidden())

        shell._back_button.click()
        self.assertEqual(shell.navigation.depth, 1)
        self.assertTrue(shell._back_button.isHidden())
