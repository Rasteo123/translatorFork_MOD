import os
import importlib.util
from pathlib import Path
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.ui.shell import MainShell
from gemini_translator.ui.pages.home_page import HomePage


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_app_main_module():
    spec = importlib.util.spec_from_file_location(
        "translatorfork_root_main_for_tests",
        PROJECT_ROOT / "main.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class OpenToolInShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _shell(self):
        main = _load_app_main_module()
        shell = MainShell()
        shell._external_windows = []
        shell.set_home(HomePage())
        self.addCleanup(shell.close)
        return shell, main

    def test_push_benchmark_routes_without_constructing_real_page(self):
        # Constructing the real PromptBenchmarkPage blocks on local-model
        # discovery in headless tests, so verify the 'prompt_benchmark' route
        # by patching the page class with a lightweight stand-in.
        from unittest.mock import patch

        from gemini_translator.ui.shell import ShellPage

        class _FakeBenchmarkPage(ShellPage):
            page_title = "fake-benchmark"

        shell, main = self._shell()
        with patch(
            "gemini_translator.ui.pages.benchmark_page.PromptBenchmarkPage",
            _FakeBenchmarkPage,
        ):
            main.open_tool_in_shell(shell, "prompt_benchmark")
        self.assertEqual(shell.navigation.depth, 2)
        self.assertIsInstance(shell.navigation.current_page(), _FakeBenchmarkPage)

    def test_push_chapter_splitter_page(self):
        from gemini_translator.ui.pages.chapter_splitter_page import ChapterSplitterPage
        shell, main = self._shell()
        main.open_tool_in_shell(shell, "chapter_splitter")
        self.assertEqual(shell.navigation.depth, 2)
        self.assertIsInstance(shell.navigation.current_page(), ChapterSplitterPage)

    def test_gemini_reader_is_embedded_in_shell_navigation(self):
        from unittest.mock import patch

        fake_window = self._fake_external_window("Fake Reader")
        shell, main = self._shell()

        with patch.object(main, "launch_gemini_reader", return_value=(fake_window, False)):
            main.open_tool_in_shell(shell, "gemini_reader")

        self._assert_external_window_embedded(shell, fake_window, "Fake Reader")

    def test_ranobelib_uploader_is_embedded_in_shell_navigation(self):
        from unittest.mock import patch

        fake_window = self._fake_external_window("Fake RanobeLib")
        shell, main = self._shell()

        with patch.object(main, "launch_ranobelib_uploader", return_value=(fake_window, False)):
            main.open_tool_in_shell(shell, "ranobelib_uploader")

        self._assert_external_window_embedded(shell, fake_window, "Fake RanobeLib")

    def test_embedded_external_window_return_to_menu_pops_to_home(self):
        from unittest.mock import patch

        fake_window = self._fake_external_window("Fake Reader")
        shell, main = self._shell()

        with patch.object(main, "launch_gemini_reader", return_value=(fake_window, False)):
            main.open_tool_in_shell(shell, "gemini_reader")

        fake_window.return_to_menu_handler()

        self.assertEqual(shell.navigation.depth, 1)
        self.assertIsInstance(shell.navigation.current_page(), HomePage)

    def test_unknown_tool_is_noop(self):
        shell, main = self._shell()
        main.open_tool_in_shell(shell, "does_not_exist")
        self.assertEqual(shell.navigation.depth, 1)

    def _fake_external_window(self, title):
        class FakeExternalWindow(QtWidgets.QMainWindow):
            def __init__(self):
                super().__init__()
                self.return_to_menu_handler = None
                self.setWindowTitle(title)

            def set_return_to_menu_handler(self, handler):
                self.return_to_menu_handler = handler

        return FakeExternalWindow()

    def _assert_external_window_embedded(self, shell, fake_window, title):
        self.assertEqual(shell.navigation.depth, 2)
        page = shell.navigation.current_page()
        self.assertIs(getattr(page, "embedded_window", None), fake_window)
        self.assertEqual(page.get_page_title(), title)
        self.assertIs(fake_window.parent(), page)
        self.assertFalse(fake_window.isWindow())
        self.assertTrue(callable(fake_window.return_to_menu_handler))
        self.assertEqual(shell._external_windows, [])
