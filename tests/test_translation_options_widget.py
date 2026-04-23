import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.api import config as api_config
from gemini_translator.ui.widgets.translation_options_widget import TranslationOptionsWidget


class TranslationOptionsWidgetTaskSizeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _create_widget(self):
        widget = TranslationOptionsWidget()
        self.addCleanup(widget.close)
        widget.html_files = ["Text/ch1.xhtml"]
        widget.chapter_compositions = {
            "Text/ch1.xhtml": {
                "code_size": 0,
                "text_size": 10000,
                "total_size": 10000,
                "is_cjk": False,
            }
        }
        return widget

    def test_model_recommendation_updates_task_size_until_user_sets_it(self):
        widget = self._create_widget()

        with patch.object(api_config, "all_models", return_value={"test-model": {"max_output_tokens": 200000}}):
            widget.update_recommendations_from_model("test-model")

        self.assertNotEqual(widget.task_size_spin.value(), 10000)
        self.assertFalse(widget.is_task_size_user_defined())
        self.assertFalse(widget.get_settings()["task_size_limit_user_defined"])

    def test_saved_task_size_is_not_overwritten_by_model_recommendation(self):
        widget = self._create_widget()
        widget.set_settings({"task_size_limit": 15404})

        with patch.object(api_config, "all_models", return_value={"test-model": {"max_output_tokens": 200000}}):
            widget.update_recommendations_from_model("test-model")

        self.assertEqual(widget.task_size_spin.value(), 15404)
        self.assertTrue(widget.is_task_size_user_defined())

    def test_auto_task_size_flag_allows_later_model_recommendation(self):
        widget = self._create_widget()
        widget.set_settings({"task_size_limit": 15404, "task_size_limit_user_defined": False})

        with patch.object(api_config, "all_models", return_value={"test-model": {"max_output_tokens": 200000}}):
            widget.update_recommendations_from_model("test-model")

        self.assertNotEqual(widget.task_size_spin.value(), 15404)
        self.assertFalse(widget.is_task_size_user_defined())

    def test_manual_task_size_is_not_overwritten_by_model_recommendation(self):
        widget = self._create_widget()
        widget.task_size_spin.setValue(15404)

        with patch.object(api_config, "all_models", return_value={"test-model": {"max_output_tokens": 200000}}):
            widget.update_recommendations_from_model("test-model")

        self.assertEqual(widget.task_size_spin.value(), 15404)
        self.assertTrue(widget.is_task_size_user_defined())


if __name__ == "__main__":
    unittest.main()
