import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("GT_DISABLE_LOCAL_MODEL_DISCOVERY", "1")

from PyQt6 import QtCore, QtWidgets

from main import EventBus
from gemini_translator.ui.themes import LIGHT_DEFAULT_THEME_COLORS, build_stylesheet
from gemini_translator.ui.widgets.glossary_widget import GlossaryWidget  # noqa: F401
from gemini_translator.ui.dialogs.glossary import GlossaryManagerPage
from gemini_translator.utils.settings import SettingsManager


class GlossaryManagerTableLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.app.setStyleSheet(build_stylesheet(LIGHT_DEFAULT_THEME_COLORS))
        self.app.event_bus = EventBus()
        self.settings_file = tempfile.NamedTemporaryFile(
            suffix=".json",
            delete=False,
        )
        self.settings_file.close()
        self.settings = SettingsManager(
            event_bus=self.app.event_bus,
            config_file=self.settings_file.name,
        )
        self.app.settings_manager = self.settings
        self.app.get_settings_manager = lambda: self.settings
        self.app.global_version = ""

    def tearDown(self):
        self.settings.flush()
        self.app.setStyleSheet("")
        try:
            os.unlink(self.settings_file.name)
        except FileNotFoundError:
            pass

    def test_action_cell_widgets_have_room_under_global_theme(self):
        page = GlossaryManagerPage(mode="child")
        self.addCleanup(page.close)
        page.associated_project_path = tempfile.gettempdir()
        page.associated_epub_path = os.path.join(tempfile.gettempdir(), "book.epub")
        page.set_glossary(
            [
                {
                    "original": "Alpha",
                    "rus": "Альфа",
                    "note": "Персонаж; длинное примечание",
                }
            ],
            run_analysis=False,
        )

        page.resize(1800, 1100)
        page.show()
        self.app.processEvents()

        for column in (3, 4):
            cell_widget = page.table.cellWidget(0, column)
            self.assertIsNotNone(cell_widget)
            self.assertEqual(cell_widget.findChildren(QtWidgets.QPushButton), [])
            self.assertGreaterEqual(
                cell_widget.width(),
                cell_widget.minimumSizeHint().width(),
            )
            tool_buttons = cell_widget.findChildren(QtWidgets.QToolButton)
            self.assertGreaterEqual(len(tool_buttons), 1)
            for button in tool_buttons:
                self.assertGreaterEqual(
                    button.width(),
                    page.TABLE_ACTION_BUTTON_SIZE.width(),
                )
                self.assertLessEqual(button.height(), cell_widget.height())

    def test_manager_loads_glossary_as_single_vertical_list(self):
        page = GlossaryManagerPage(mode="child")
        self.addCleanup(page.close)
        page.set_glossary(
            [
                {
                    "original": f"Term {index:03d}",
                    "rus": f"Термин {index:03d}",
                    "note": "",
                }
                for index in range(125)
            ],
            run_analysis=False,
        )

        self.assertEqual(page.table.rowCount(), 125)
        self.assertEqual(page.total_pages, 1)
        self.assertEqual(page.page_info_label.text(), "Всего: 125")
        self.assertFalse(page.first_page_button.isVisible())
        self.assertFalse(page.next_page_button.isVisible())
        self.assertEqual(
            page.table.horizontalScrollBarPolicy(),
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
        )


if __name__ == "__main__":
    unittest.main()
