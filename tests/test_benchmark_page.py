import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("GT_DISABLE_LOCAL_MODEL_DISCOVERY", "1")

from PyQt6 import QtWidgets  # noqa: F401
from PyQt6.QtWidgets import QMessageBox

from gemini_translator.ui.pages.benchmark_page import PromptBenchmarkPage
from gemini_translator.ui.shell import ShellPage


class PromptBenchmarkPageContractTests(unittest.TestCase):
    """Contract + lifecycle-guard tests that do NOT construct the page.

    Constructing ``PromptBenchmarkPage`` builds the model combos, which block on
    local-model discovery in a headless environment without a settings manager;
    the verbatim migration is covered by review and the page works in the real
    app. So the guards are exercised via the unbound methods against stubs.
    """

    def test_is_shell_page_subclass(self):
        self.assertTrue(issubclass(PromptBenchmarkPage, ShellPage))

    def test_page_title(self):
        self.assertEqual(PromptBenchmarkPage.page_title, "Бенчмарк промптов и моделей")

    def test_can_leave_true_when_idle(self):
        class _Stub:
            worker = None

        self.assertTrue(PromptBenchmarkPage.can_leave(_Stub()))

    def test_can_leave_blocks_while_worker_running(self):
        class _Worker:
            def isRunning(self):
                return True

        class _Stub:
            worker = _Worker()

        with patch.object(QMessageBox, "warning"):
            self.assertFalse(PromptBenchmarkPage.can_leave(_Stub()))

    def test_on_leave_saves_ui_state(self):
        calls = []

        class _Stub:
            def _save_ui_state(self):
                calls.append(True)

        PromptBenchmarkPage.on_leave(_Stub())
        self.assertEqual(calls, [True])
