import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets  # noqa: F401

from gemini_translator.ui.pages.setup_page import InitialSetupPage
from gemini_translator.ui.shell import ShellPage


class InitialSetupPageContractTests(unittest.TestCase):
    def test_is_shell_page_subclass(self):
        self.assertTrue(issubclass(InitialSetupPage, ShellPage))

    def test_page_title(self):
        self.assertEqual(InitialSetupPage.page_title, "Переводчик EPUB")

    def test_on_enter_defers_session_sync_until_lazy_ui_is_loaded(self):
        class _LazyPageHarness:
            _full_ui_loaded = False

            def _check_and_sync_active_session(self):
                raise AssertionError("session sync must wait for the full UI")

        InitialSetupPage.on_enter(_LazyPageHarness())

    def test_on_enter_syncs_after_lazy_ui_is_loaded(self):
        class _LoadedPageHarness:
            _full_ui_loaded = True

            def __init__(self):
                self.sync_count = 0

            def _check_and_sync_active_session(self):
                self.sync_count += 1

        harness = _LoadedPageHarness()
        InitialSetupPage.on_enter(harness)
        self.assertEqual(harness.sync_count, 1)
