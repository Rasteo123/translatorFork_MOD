import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.api import config as api_config
from gemini_translator.utils.epub_tools import TASK_SIZE_UNIT_CHARS, TASK_SIZE_UNIT_TOKENS
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
        self.assertEqual(widget.get_settings()["task_size_unit"], TASK_SIZE_UNIT_TOKENS)

    def test_task_size_unit_switch_round_trips(self):
        widget = self._create_widget()

        widget.set_settings({"task_size_unit": TASK_SIZE_UNIT_CHARS})

        self.assertTrue(widget.task_size_chars_checkbox.isChecked())
        self.assertEqual(widget.task_size_unit(), TASK_SIZE_UNIT_CHARS)
        self.assertEqual(widget.get_settings()["task_size_unit"], TASK_SIZE_UNIT_CHARS)

    def test_character_mode_uses_character_sizes_for_batch_preview(self):
        widget = self._create_widget()
        widget.html_files = ["Text/ch1.xhtml", "Text/ch2.xhtml"]
        widget.chapter_compositions = {
            "Text/ch1.xhtml": {
                "code_size": 0,
                "text_size": 2000,
                "total_size": 2000,
                "total_chars": 7000,
                "is_cjk": False,
            },
            "Text/ch2.xhtml": {
                "code_size": 0,
                "text_size": 2000,
                "total_size": 2000,
                "total_chars": 7000,
                "is_cjk": False,
            },
        }
        widget._update_batching_availability()
        widget.batch_checkbox.setChecked(True)
        widget.task_size_spin.setValue(10_000)

        self.assertIn("~1", widget.info_label.text())

        widget.task_size_chars_checkbox.setChecked(True)

        self.assertIn("~2", widget.info_label.text())
        self.assertEqual(
            widget.chapter_sizes_for_current_unit(),
            {"Text/ch1.xhtml": 7000, "Text/ch2.xhtml": 7000},
        )

    def test_batch_preview_uses_same_packing_with_and_without_chunking(self):
        widget = self._create_widget()
        widget.html_files = [
            "Text/ch1.xhtml",
            "Text/ch2.xhtml",
            "Text/ch3.xhtml",
            "Text/ch4.xhtml",
        ]
        widget.chapter_compositions = {
            "Text/ch1.xhtml": {"total_size": 600, "is_cjk": False},
            "Text/ch2.xhtml": {"total_size": 600, "is_cjk": False},
            "Text/ch3.xhtml": {"total_size": 400, "is_cjk": False},
            "Text/ch4.xhtml": {"total_size": 400, "is_cjk": False},
        }
        widget._update_batching_availability()
        widget.task_size_spin.setValue(1000)
        widget.batch_checkbox.setChecked(True)

        self.assertIn("~2", widget.info_label.text())

        widget.chunking_checkbox.setChecked(True)

        self.assertIn("~2", widget.info_label.text())

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

    def test_text_edit_marks_task_size_as_user_defined_before_value_commit(self):
        widget = self._create_widget()
        widget.task_size_spin.lineEdit().textEdited.emit("15404")

        with patch.object(api_config, "all_models", return_value={"test-model": {"max_output_tokens": 200000}}):
            widget.update_recommendations_from_model("test-model")

        self.assertEqual(widget.task_size_spin.value(), 10000)
        self.assertTrue(widget.is_task_size_user_defined())

    def test_sequential_mode_preserves_batching_choice(self):
        widget = self._create_widget()
        widget.html_files = ["Text/ch1.xhtml", "Text/ch2.xhtml"]
        widget._update_batching_availability()

        widget.batch_checkbox.setChecked(True)
        widget.sequential_checkbox.setChecked(True)
        widget.sequential_splits_spin.setValue(2)

        settings = widget.get_settings()
        self.assertTrue(settings["sequential_translation"])
        self.assertEqual(settings["sequential_translation_splits"], 2)
        self.assertTrue(settings["use_batching"])
        self.assertFalse(settings["chunking"])
        self.assertTrue(widget.batch_checkbox.isEnabled())
        self.assertTrue(widget.sequential_splits_spin.isEnabled())

    def test_loading_sequential_settings_keeps_existing_split_mode(self):
        widget = self._create_widget()
        widget.html_files = ["Text/ch1.xhtml", "Text/ch2.xhtml"]
        widget._update_batching_availability()

        widget.set_settings({
            "use_batching": False,
            "chunking": True,
            "sequential_translation": True,
            "sequential_translation_splits": 3,
        })

        settings = widget.get_settings()
        self.assertTrue(settings["sequential_translation"])
        self.assertEqual(settings["sequential_translation_splits"], 3)
        self.assertFalse(settings["use_batching"])
        self.assertTrue(settings["chunking"])

    def test_batch_and_chunking_can_be_enabled_together(self):
        widget = self._create_widget()
        widget.html_files = ["Text/ch1.xhtml", "Text/ch2.xhtml"]
        widget._update_batching_availability()

        widget.batch_checkbox.setChecked(True)
        widget.chunking_checkbox.setChecked(True)

        settings = widget.get_settings()
        self.assertTrue(settings["use_batching"])
        self.assertTrue(settings["chunking"])

    def test_orchestration_settings_round_trip(self):
        widget = self._create_widget()

        widget.set_settings({
            "parallel_providers_enabled": True,
            "parallel_provider_list": "openrouter:Claude, local:DeepSeek",
            "parallel_provider_strategy": "best_score",
            "multi_pass_enabled": True,
            "multi_pass_count": 4,
            "multi_pass_strategy": "first_success",
        })

        settings = widget.get_settings()
        self.assertTrue(settings["parallel_providers_enabled"])
        self.assertEqual(settings["parallel_provider_list"], "openrouter:Claude, local:DeepSeek")
        self.assertEqual(settings["parallel_provider_strategy"], "best_score")
        self.assertTrue(settings["multi_pass_enabled"])
        self.assertTrue(settings["multi_pass_chapter_translation"])
        self.assertEqual(settings["multi_pass_count"], 4)
        self.assertEqual(settings["multi_pass_strategy"], "first_success")
        self.assertTrue(widget.parallel_providers_edit.isEnabled())
        self.assertTrue(widget.multi_pass_count_spin.isEnabled())


if __name__ == "__main__":
    unittest.main()
