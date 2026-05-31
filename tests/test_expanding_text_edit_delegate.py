import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets

from gemini_translator.ui.dialogs.glossary_dialogs.custom_widgets import (
    ExpandingTextEditDelegate,
)


class ExpandingTextEditDelegateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_size_hint_handles_invalid_index_during_table_reset(self):
        delegate = ExpandingTextEditDelegate()
        option = QtWidgets.QStyleOptionViewItem()
        invalid_index = QtCore.QModelIndex()

        size = delegate.sizeHint(option, invalid_index)

        self.assertIsInstance(size, QtCore.QSize)


if __name__ == "__main__":
    unittest.main()
