# -*- coding: utf-8 -*-

from gemini_translator.api import config as api_config

HYBRID_NAME = "Точный перевод (приоритет верности)"


class TestBuiltinTranslationPromptVariants:
    def test_hybrid_prompt_is_offered_as_preset(self):
        variants = api_config.builtin_translation_prompt_variants()
        assert HYBRID_NAME in variants

    def test_hybrid_preset_keeps_placeholders(self):
        text = api_config.builtin_translation_prompt_variants()[HYBRID_NAME]
        for placeholder in ("{glossary}", "{text}", "{format_examples}"):
            assert text.count(placeholder) == 1, f"плейсхолдер {placeholder} потерян"

    def test_existing_variants_are_preserved(self):
        variants = api_config.builtin_translation_prompt_variants()
        assert "Базовый перевод" in variants
        assert "сокращённый" in variants

    def test_values_are_plain_strings(self):
        # PresetWidget кладет значение прямо в userData комбобокса.
        for name, text in api_config.builtin_translation_prompt_variants().items():
            assert isinstance(name, str)
            assert isinstance(text, str) and text.strip()

    def test_default_prompt_button_still_returns_standard_prompt(self):
        # Кнопка «Загрузить стандартный промпт» не должна отдавать гибрид.
        standard = api_config.default_prompt()
        assert "RUSSIAN LITERARY ADAPTATION" in standard
        assert standard != api_config.builtin_translation_prompt_variants()[HYBRID_NAME]

    def test_missing_file_does_not_break_variants(self, monkeypatch):
        class MissingPath:
            @staticmethod
            def exists():
                return False

        monkeypatch.setattr(api_config, "_HYBRID_TRANSLATION_PROMPT_FILE", MissingPath)
        variants = api_config.builtin_translation_prompt_variants()
        assert HYBRID_NAME not in variants
        assert "Базовый перевод" in variants
