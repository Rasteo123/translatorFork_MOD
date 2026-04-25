import unittest

from gemini_translator.ui.dialogs.validation import TranslationValidatorDialog
from gemini_translator.ui.dialogs.validation_dialogs.untranslated_fixer_dialog import (
    UNTRANSLATED_PROMPT_GUARDRAILS_MARKER,
    build_effective_untranslated_prompt,
)


class _SettingsStub:
    def __init__(self, prompt_text):
        self.prompt_text = prompt_text

    def get_last_untranslated_prompt_text(self):
        return self.prompt_text


class _AutoPromptHarness:
    _get_auto_untranslated_prompt_text = TranslationValidatorDialog._get_auto_untranslated_prompt_text

    def __init__(self, prompt_text):
        self.settings_manager = _SettingsStub(prompt_text)


class UntranslatedPromptTests(unittest.TestCase):
    def test_effective_prompt_appends_guardrails_to_legacy_prompt(self):
        prompt = build_effective_untranslated_prompt("Translate only the untranslated fragments.")

        self.assertIn("Translate only the untranslated fragments.", prompt)
        self.assertIn(UNTRANSLATED_PROMPT_GUARDRAILS_MARKER, prompt)
        self.assertIn("CJK-недоперевод", prompt)
        self.assertIn("все внешние ссылки", prompt)
        self.assertIn("рекламные", prompt)

    def test_effective_prompt_does_not_duplicate_guardrails(self):
        prompt = build_effective_untranslated_prompt(
            f"Base prompt\n\n{UNTRANSLATED_PROMPT_GUARDRAILS_MARKER}\nExisting rules."
        )

        self.assertEqual(prompt.count(UNTRANSLATED_PROMPT_GUARDRAILS_MARKER), 1)

    def test_auto_untranslated_prompt_wraps_saved_legacy_prompt(self):
        harness = _AutoPromptHarness("Translate only the untranslated fragments.")

        prompt = harness._get_auto_untranslated_prompt_text()

        self.assertIn(UNTRANSLATED_PROMPT_GUARDRAILS_MARKER, prompt)
        self.assertIn("http", prompt)
        self.assertIn("boosty", prompt)


if __name__ == "__main__":
    unittest.main()
