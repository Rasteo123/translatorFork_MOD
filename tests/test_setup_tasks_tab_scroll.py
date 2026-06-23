import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets

from gemini_translator.ui.dialogs import setup as setup_dialog
from gemini_translator.ui.widgets.translation_options_widget import TranslationOptionsWidget


class SetupTasksTabScrollTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_tasks_tab_is_scrollable_and_keeps_task_list_room(self):
        task_widget = QtWidgets.QWidget()
        options_widget = QtWidgets.QWidget()

        scroll_area, splitter = setup_dialog._create_tasks_tab_scroll_area(
            task_widget,
            options_widget,
        )
        self.addCleanup(scroll_area.close)

        self.assertIsInstance(scroll_area, QtWidgets.QScrollArea)
        self.assertTrue(scroll_area.widgetResizable())
        self.assertEqual(scroll_area.frameShape(), QtWidgets.QFrame.Shape.NoFrame)
        self.assertEqual(
            scroll_area.verticalScrollBarPolicy(),
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded,
        )
        self.assertEqual(
            scroll_area.horizontalScrollBarPolicy(),
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
        )
        self.assertIs(scroll_area.widget().layout().itemAt(0).widget(), splitter)
        self.assertGreaterEqual(task_widget.minimumHeight(), setup_dialog.TASK_LIST_MIN_HEIGHT)
        self.assertGreaterEqual(
            options_widget.minimumHeight(),
            setup_dialog.TASK_OPTIONS_MIN_HEIGHT,
        )
        self.assertGreaterEqual(splitter.minimumHeight(), setup_dialog.TASKS_TAB_MIN_HEIGHT)

        scroll_area.resize(900, 520)
        scroll_area.show()
        self.app.processEvents()
        self.assertGreater(scroll_area.verticalScrollBar().maximum(), 0)

    def test_tasks_options_minimum_keeps_orchestration_controls_visible(self):
        task_widget = QtWidgets.QWidget()
        options_widget = TranslationOptionsWidget()

        scroll_area, _splitter = setup_dialog._create_tasks_tab_scroll_area(
            task_widget,
            options_widget,
        )
        self.addCleanup(scroll_area.close)

        self.assertGreaterEqual(
            options_widget.minimumHeight(),
            options_widget.minimumSizeHint().height(),
        )


if __name__ == "__main__":
    unittest.main()
