import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.ui.widgets.ancestor_utils import find_ancestor_by_class_name


class FindAncestorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_finds_ancestor_by_new_or_old_name(self):
        class InitialSetupPage(QtWidgets.QWidget):
            pass

        root = InitialSetupPage()
        self.addCleanup(root.close)
        mid = QtWidgets.QWidget(root)
        leaf = QtWidgets.QWidget(mid)
        self.assertIs(find_ancestor_by_class_name(leaf, "InitialSetupDialog", "InitialSetupPage"), root)

    def test_returns_none_when_absent(self):
        leaf = QtWidgets.QWidget()
        self.addCleanup(leaf.close)
        self.assertIsNone(find_ancestor_by_class_name(leaf, "InitialSetupDialog", "InitialSetupPage"))
