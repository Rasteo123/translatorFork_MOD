import os
import tempfile
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.ui.dialogs.misc import EnhancedProjectHistoryDialog
from gemini_translator.ui.widgets.overlay_tab_widget import install_tab_fade


class ProjectScanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_scan_finds_glossary_only_projects(self):
        root = tempfile.mkdtemp()
        translated = os.path.join(root, "translated_project")
        glossary_only = os.path.join(root, "glossary_only_project")
        empty = os.path.join(root, "not_a_project")
        os.makedirs(translated)
        os.makedirs(glossary_only)
        os.makedirs(empty)
        open(os.path.join(translated, "translation_map.json"), "w").write("{}")
        open(os.path.join(glossary_only, "project_glossary.json"), "w").write("[]")

        sm = Mock()
        sm.get_last_projects_root_folder.return_value = root
        dialog = EnhancedProjectHistoryDialog([], sm)
        self.addCleanup(dialog.deleteLater)
        folders, warning = dialog._scan_projects_root_folder()
        names = {os.path.basename(f) for f in folders}
        self.assertIn("translated_project", names)
        # Проект с одним лишь глоссарием (перевод ещё не начат) тоже виден.
        self.assertIn("glossary_only_project", names)
        self.assertNotIn("not_a_project", names)
        self.assertEqual(warning, "")


class TabFadeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_switch_fades_in_new_tab(self):
        from PyQt6 import QtTest

        tabs = QtWidgets.QTabWidget()
        install_tab_fade(tabs)
        first = QtWidgets.QLabel("один")
        second = QtWidgets.QLabel("два")
        tabs.addTab(first, "1")
        tabs.addTab(second, "2")
        tabs.show()
        self.addCleanup(tabs.hide)
        for _ in range(3):
            self.app.processEvents()
        tabs.setCurrentIndex(1)
        # Сразу после переключения контент проявляется через эффект…
        self.assertIsNotNone(second.graphicsEffect())
        QtTest.QTest.qWait(600)
        # …а после завершения фейда эффект гарантированно снят.
        self.assertIsNone(second.graphicsEffect())


if __name__ == "__main__":
    unittest.main()
