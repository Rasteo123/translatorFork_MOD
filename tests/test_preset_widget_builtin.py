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


class PresetWidgetOverridePromptTests(unittest.TestCase):
    """Промпт последовательного перевода подменяет выбранный — виджет
    должен это показывать."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.settings_manager = _SettingsManagerStub()
        cls.app.get_settings_manager = lambda: cls.settings_manager

    def _make_widget(self):
        return PresetWidget(
            preset_name="Промпт",
            override_prompt_func=lambda: "SEQUENTIAL {previous_chapter_reference}",
            override_prompt_title="Промпт последовательного перевода",
            override_prompt_notice="Этот промпт модель не получит.",
        )

    def test_notice_follows_override_state(self):
        widget = self._make_widget()
        self.assertTrue(widget.override_notice.isHidden())

        widget.set_override_active(True)
        self.assertFalse(widget.override_notice.isHidden())
        self.assertEqual(widget.override_notice.text(), "Этот промпт модель не получит.")

        widget.set_override_active(False)
        self.assertTrue(widget.override_notice.isHidden())

    def test_widget_without_override_has_no_view_button_and_ignores_state(self):
        widget = PresetWidget()
        self.assertIsNone(widget.override_view_btn)

        widget.set_override_active(True)
        self.assertTrue(widget.override_notice.isHidden())

    def test_view_button_shows_override_prompt_read_only(self):
        from unittest import mock

        widget = self._make_widget()
        shown = []

        def fake_exec(_context, dialog):
            shown.append(dialog)
            return 0

        with mock.patch(
            "gemini_translator.ui.widgets.preset_widget.exec_dialog", fake_exec
        ):
            widget.override_view_btn.click()

        self.assertEqual(len(shown), 1)
        dialog = shown[0]
        self.assertEqual(dialog.windowTitle(), "Промпт последовательного перевода")
        from PyQt6.QtWidgets import QPlainTextEdit

        view = dialog.findChild(QPlainTextEdit)
        self.assertTrue(view.isReadOnly())
        self.assertEqual(view.toPlainText(), "SEQUENTIAL {previous_chapter_reference}")

    def test_view_button_stays_enabled_during_session(self):
        widget = self._make_widget()
        widget.set_session_mode(True)

        self.assertTrue(widget.override_view_btn.isEnabled())
        self.assertFalse(widget.save_as_btn.isEnabled())


if __name__ == "__main__":
    unittest.main()
