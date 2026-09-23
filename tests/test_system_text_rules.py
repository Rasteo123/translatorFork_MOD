"""Флажок «Системный текст LitRPG»: правила оформления окон системы в промпте перевода."""

import unittest
from unittest.mock import patch

from gemini_translator.api import config as api_config
from gemini_translator.core.worker_helpers.prompt_builder import PromptBuilder

RULES = "## SYSTEM AND INTERFACE TEXT (LitRPG)\nRULES"


def _build(template, *, sequential, rules_on):
    builder = PromptBuilder(
        custom_prompt=template,
        context_manager=None,
        use_system_instruction=False,
        sequential_mode=sequential,
        system_text_rules=rules_on,
    )
    with patch.object(api_config, "default_sequential_prompt", return_value=template), patch.object(
        api_config, "internal_prompts", return_value={"translation_output_examples": {"en": ["EXAMPLE"]}}
    ), patch.object(api_config, "system_text_rules", return_value=RULES):
        prompt, _, _ = builder._build_with_placeholders("<p>Text</p>", "", "")
    return prompt


class SystemTextRulesPromptTests(unittest.TestCase):
    def test_rules_stay_out_while_the_checkbox_is_off(self):
        prompt = _build("A {format_examples}\n{system_text_rules}\nB {text}", sequential=True, rules_on=False)

        self.assertNotIn("RULES", prompt)
        self.assertNotIn("{system_text_rules}", prompt)

    def test_sequential_prompt_gets_the_rules_in_their_own_place(self):
        prompt = _build("A {format_examples}\n{system_text_rules}\nB {text}", sequential=True, rules_on=True)

        self.assertEqual(prompt.count("RULES"), 1)
        self.assertLess(prompt.index("EXAMPLE"), prompt.index("RULES"))
        self.assertLess(prompt.index("RULES"), prompt.index("B <p>Text</p>"))

    def test_prompt_with_examples_gets_the_rules_before_them(self):
        prompt = _build("A <ex>{format_examples}</ex> B {text}", sequential=False, rules_on=True)

        self.assertEqual(prompt.count("RULES"), 1)
        self.assertLess(prompt.index("RULES"), prompt.index("EXAMPLE"))

    def test_prompt_without_examples_gets_the_rules_at_the_end(self):
        # safe_format дописывает значение без места в шаблоне блоком в XML-тегах.
        prompt = _build("A {text}", sequential=False, rules_on=True)

        self.assertTrue(prompt.rstrip().endswith("</system_text_rules>"))
        self.assertLess(prompt.index("<p>Text</p>"), prompt.index("RULES"))


class BuiltinSystemTextRulesTests(unittest.TestCase):
    def test_rules_file_keeps_brackets_and_shows_examples(self):
        rules = api_config.system_text_rules()

        self.assertIn("【", rules)
        self.assertIn("Src:", rules)
        self.assertIn("not interface text", rules)
        self.assertNotIn("—", rules)

    def test_sequential_prompt_has_a_place_for_the_rules(self):
        self.assertIn("{system_text_rules}", api_config.default_sequential_prompt())


if __name__ == "__main__":
    unittest.main()
