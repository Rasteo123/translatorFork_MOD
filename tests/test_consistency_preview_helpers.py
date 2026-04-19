import unittest

from PyQt6 import QtWidgets
from PyQt6.QtGui import QTextOption
from PyQt6.QtWidgets import QTextEdit

from gemini_translator.ui.dialogs.consistency_checker import (
    build_changed_line_format,
    configure_wrapped_text_edit,
)


class ConsistencyPreviewHelpersTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_configure_wrapped_text_edit_enables_word_wrap(self):
        editor = QTextEdit()

        configure_wrapped_text_edit(editor, read_only=True)

        self.assertTrue(editor.isReadOnly())
        self.assertEqual(editor.lineWrapMode(), QTextEdit.LineWrapMode.WidgetWidth)
        self.assertEqual(
            editor.wordWrapMode(),
            QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere,
        )

    def test_build_changed_line_format_uses_readable_foreground(self):
        changed_format = build_changed_line_format()

        self.assertEqual(changed_format.background().color().name(), "#c8e6c9")
        self.assertEqual(changed_format.foreground().color().name(), "#0f2411")


if __name__ == "__main__":
    unittest.main()
