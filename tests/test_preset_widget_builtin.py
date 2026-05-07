import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from gemini_translator.ui.widgets.preset_widget import PresetWidget


class _SettingsManagerStub:
    def load_named_prompts(self):
        return {}

    def save_named_prompts(self, payload):
        self.saved_prompts = dict(payload)
        return True

    def get_custom_prompt(self):
        return ""

    def get_last_prompt_preset_name(self):
        return None


class PresetWidgetBuiltinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.settings_manager = _SettingsManagerStub()
        cls.app.get_settings_manager = lambda: cls.settings_manager

    def test_switching_builtin_to_new_preserves_new_prompt_label(self):
        widget = PresetWidget(
            builtin_presets_func=lambda: {"Basic": "basic prompt text"},
        )
        widget.load_last_session_state()

        widget.prompt_combo.setCurrentText("Basic")
        self.assertEqual(widget.get_prompt(), "basic prompt text")

        widget.prompt_combo.setCurrentIndex(0)

        self.assertEqual(widget.get_current_preset_name(), None)
        self.assertEqual(widget.prompt_combo.currentText(), "[Новый Пресет]")
        self.assertEqual(widget.prompt_combo.itemText(0), "[Новый Пресет]")
        self.assertEqual(widget.get_prompt(), "")
        self.assertNotEqual(widget.prompt_combo.findText("Basic"), -1)

    def test_new_prompt_label_is_restored_if_button_state_refresh_runs(self):
        widget = PresetWidget(
            preset_name="Промпт",
            builtin_presets_func=lambda: {"Basic": "basic prompt text"},
        )
        widget.load_last_session_state()
        widget.prompt_combo.setItemText(0, "")

        widget._update_button_states()

        self.assertEqual(widget.prompt_combo.itemText(0), "[Новый Промпт]")


if __name__ == "__main__":
    unittest.main()
