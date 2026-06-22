import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets  # noqa: F401

from gemini_translator.ui.pages.validation_page import TranslationValidatorPage
from gemini_translator.ui.shell import ShellPage


class TranslationValidatorPageContractTests(unittest.TestCase):
    def test_is_shell_page_subclass(self):
        self.assertTrue(issubclass(TranslationValidatorPage, ShellPage))

    def test_page_title(self):
        self.assertEqual(TranslationValidatorPage.page_title, "Валидация перевода")

    def test_can_leave_vetoes_while_analysis_running(self):
        from unittest.mock import patch
        from PyQt6.QtWidgets import QMessageBox

        class _Thread:
            def isRunning(self): return True
        class _Stub:
            analysis_thread = _Thread()
        # call the unbound method against a stub; patch the modal to "No"
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.assertFalse(TranslationValidatorPage.can_leave(_Stub()))
