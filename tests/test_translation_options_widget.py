import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.ui.widgets.translation_options_widget import TranslationOptionsWidget


class TranslationOptionsWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _create_widget(self):
        widget = TranslationOptionsWidget()
        widget.html_files = ["chapter-1.xhtml"]
        widget.chapter_compositions = {
            "chapter-1.xhtml": {
                "code_size": 1000,
                "text_size": 9000,
                "is_cjk": False,
                "total_size": 10000,
            }
        }
        self.addCleanup(widget.close)
        return widget

    @patch("gemini_translator.ui.widgets.translation_options_widget.api_config.all_models")
    def test_recommendation_applies_when_task_size_not_overridden(self, all_models_mock):
        all_models_mock.return_value = {"Test Model": {"max_output_tokens": 40000}}
        widget = self._create_widget()

        widget.update_recommendations_from_model("Test Model")

        self.assertGreater(widget.task_size_spin.value(), 30000)

    @patch("gemini_translator.ui.widgets.translation_options_widget.api_config.all_models")
    def test_manual_task_size_survives_recommendation_refresh(self, all_models_mock):
        all_models_mock.return_value = {"Test Model": {"max_output_tokens": 40000}}
        widget = self._create_widget()

        widget.task_size_spin.setValue(15000)
        widget.update_recommendations_from_model("Test Model")

        self.assertEqual(widget.task_size_spin.value(), 15000)

    @patch("gemini_translator.ui.widgets.translation_options_widget.api_config.all_models")
    def test_saved_task_size_survives_recommendation_refresh_after_restore(self, all_models_mock):
        all_models_mock.return_value = {"Test Model": {"max_output_tokens": 40000}}
        widget = self._create_widget()

        widget.set_settings(
            {
                "use_batching": True,
                "chunking": False,
                "chunk_on_error": False,
                "task_size_limit": 15000,
            }
        )
        widget.update_recommendations_from_model("Test Model")

        self.assertEqual(widget.task_size_spin.value(), 15000)


if __name__ == "__main__":
    unittest.main()
