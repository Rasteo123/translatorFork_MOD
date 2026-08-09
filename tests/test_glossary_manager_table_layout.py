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

        # Кнопки действий рисуются делегатом (без per-row виджетов):
        # проверяем, что под глобальной темой все кнопки помещаются в ячейку.
        from gemini_translator.ui.dialogs.glossary_dialogs.action_delegate import (
            GlossaryActionDelegate,
        )

        for column in (3, 4):
            self.assertIsNone(page.table.cellWidget(0, column))
            index = page.table.model().index(0, column)
            actions = GlossaryActionDelegate.actions_for_index(index)
            self.assertGreaterEqual(len(actions), 1)
            cell_rect = page.table.visualRect(index)
            rects = page._action_delegate.button_rects(cell_rect, len(actions))
            for rect in rects:
                self.assertGreaterEqual(rect.width(), page.TABLE_ACTION_BUTTON_SIZE.width())
                self.assertLessEqual(rect.right(), cell_rect.right())
                self.assertGreaterEqual(rect.left(), cell_rect.left())
        # У версии с привязанным проектом должна быть кнопка версий
        col3_actions = GlossaryActionDelegate.actions_for_index(page.table.model().index(0, 3))
        self.assertTrue(
            {'version', 'version_active'} & set(col3_actions),
            f"ожидалась кнопка версий, получено: {col3_actions}",
        )

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

    def test_analysis_buttons_survive_repeated_reflow(self):
        page = GlossaryManagerPage(mode="child")
        self.addCleanup(page.close)
        page.set_glossary(
            [
                {
                    "original": "Alpha",
                    "rus": "Альфа",
                    "note": "",
                }
            ],
            run_analysis=False,
        )

        page._reflow_analysis_buttons()
        page._reflow_analysis_buttons()

        layout_widgets = [
            page.analysis_layout.itemAt(index).widget()
            for index in range(page.analysis_layout.count())
        ]

        self.assertIn(page.analyze_button, layout_widgets)
        self.assertIn(page.ai_correction_button, layout_widgets)
        self.assertIn(page.group_analysis_button, layout_widgets)
        self.assertIn(page.freq_analysis_button, layout_widgets)


if __name__ == "__main__":
    unittest.main()
