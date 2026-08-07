import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("GT_DISABLE_LOCAL_MODEL_DISCOVERY", "1")

from PyQt6 import QtWidgets

from main import EventBus
from gemini_translator.ui.widgets.glossary_widget import GlossaryWidget  # noqa: F401
from gemini_translator.ui.dialogs.glossary import GlossaryManagerPage
from gemini_translator.utils.settings import SettingsManager


class AnalysisButtonsNoFlashTests(unittest.TestCase):
    """Кнопки анализа не должны становиться топ-уровневыми окнами.

    _reflow_analysis_buttons делал setParent(None) при перестройке, а
    _update_analysis_widgets затем вызывал setVisible(True) — безродительская
    кнопка на мгновение появлялась как отдельное маленькое окно при каждом
    открытии менеджера глоссария.
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.app.event_bus = EventBus()
        self.settings_file = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
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
        try:
            os.unlink(self.settings_file.name)
        except FileNotFoundError:
            pass

    def _all_analysis_buttons(self, page):
        return page.static_analysis_buttons + page.dynamic_analysis_buttons

    def _make_page(self):
        page = GlossaryManagerPage(mode="child")

        def cleanup():
            page.close()
            page.deleteLater()
            self.app.processEvents()

        self.addCleanup(cleanup)
        return page

    def test_buttons_never_become_windows_after_analysis_update(self):
        page = self._make_page()

        # run_analysis=False — без фоновой подсветки; интересующий нас путь
        # (setVisible на кнопках) вызываем напрямую.
        page.set_glossary(
            [{"original": f"term{i}", "rus": f"перевод{i}", "note": ""}
             for i in range(10)],
            run_analysis=False,
        )
        page._update_analysis_widgets()

        offenders = [
            btn.text() for btn in self._all_analysis_buttons(page)
            if btn.isWindow() or btn.parent() is None
        ]
        self.assertEqual(
            offenders, [],
            f"Кнопки без родителя становятся отдельными окнами: {offenders}")

    def test_buttons_keep_parent_after_reflow_of_empty_glossary(self):
        page = self._make_page()

        page._reflow_analysis_buttons()

        offenders = [
            btn.text() for btn in self._all_analysis_buttons(page)
            if btn.parent() is None
        ]
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
