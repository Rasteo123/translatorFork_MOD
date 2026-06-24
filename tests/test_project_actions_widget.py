import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.ui.widgets.project_actions_widget import ProjectActionsWidget


class ProjectActionsWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_project_action_buttons_keep_standard_size_policy(self):
        widget = ProjectActionsWidget()
        self.addCleanup(widget.deleteLater)

        buttons = (widget.build_epub_btn, widget.sync_project_btn, widget.projects_btn)

        self.assertTrue(all(type(button) is QtWidgets.QPushButton for button in buttons))
        self.assertTrue(all(button.minimumHeight() == 0 for button in buttons))

    def test_text_area_gets_extra_card_space(self):
        widget = ProjectActionsWidget()
        self.addCleanup(widget.deleteLater)

        card_layout = widget.card.layout()

        self.assertEqual(card_layout.stretch(0), 1)


if __name__ == "__main__":
    unittest.main()
