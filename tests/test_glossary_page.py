import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets  # noqa: F401

# glossary.py has a pre-existing circular import with glossary_widget.py.
# Importing GlossaryWidget first (which causes glossary.py to be loaded from
# that direction) resolves the cycle before we try the other direction.
from gemini_translator.ui.widgets.glossary_widget import GlossaryWidget  # noqa: F401

from gemini_translator.ui.pages.glossary_page import GlossaryManagerPage
from gemini_translator.ui.shell import ShellPage


class GlossaryManagerPageContractTests(unittest.TestCase):
    def test_is_shell_page_subclass(self):
        self.assertTrue(issubclass(GlossaryManagerPage, ShellPage))

    def test_page_title(self):
        self.assertEqual(GlossaryManagerPage.page_title, "Менеджер глоссариев")

    def test_has_result_signal(self):
        self.assertTrue(hasattr(GlossaryManagerPage, "result_ready"))

    def test_can_leave_vetoes_when_unsaved_changes(self):
        from unittest.mock import patch
        from PyQt6.QtWidgets import QMessageBox

        class _Stub:
            def _has_unsaved_glossary_changes(self):
                return True
        # call the unbound method against a stub; patch the modal to "No"
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.assertFalse(GlossaryManagerPage.can_leave(_Stub()))
