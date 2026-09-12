"""Пользовательские модели проверяются на загрузке, а не в середине перевода.

`api_providers.json` — наш собственный артефакт, его стережёт отдельный тест
ниже. А `custom_provider_models` вводит человек, и до сих пор строка в поле
`rpm` проходила нормализацию насквозь и доезжала до воркера, где падала через
несколько часов работы.
"""

import json
import unittest

from gemini_translator.api import config as api_config
from gemini_translator.api.model_config_schema import (
    KNOWN_NUMERIC_FIELDS,
    sanitize_model_config,
    validate_model_config,
)


class ModelConfigSchemaTests(unittest.TestCase):
    def test_valid_config_passes_through_with_unknown_keys_intact(self):
        cleaned, error = validate_model_config({
            "id": "some-model",
            "rpm": 5,
            "max_output_tokens": 8192,
            "needs_chunking": True,
            # Ключи конкретного провайдера схеме неизвестны и трогать их нельзя.
            "deepseek_thinking": "enabled",
            "thinkingLevel": ["high", "max"],
        })

        self.assertIsNone(error)
        self.assertEqual(cleaned["id"], "some-model")
        self.assertEqual(cleaned["rpm"], 5)
        self.assertEqual(cleaned["deepseek_thinking"], "enabled")
        self.assertEqual(cleaned["thinkingLevel"], ["high", "max"])

    def test_numeric_strings_are_coerced(self):
        """Диалог настроек отдаёт числа строками — это не ошибка пользователя."""
        cleaned, error = validate_model_config({"id": "m", "rpm": "5", "tpm": "250000"})

        self.assertIsNone(error)
        self.assertEqual(cleaned["rpm"], 5)
        self.assertEqual(cleaned["tpm"], 250000)

    def test_unparsable_number_is_reported_and_names_the_field(self):
        cleaned, error = validate_model_config({"id": "m", "rpm": "пять"})

        self.assertIsNone(cleaned)
        self.assertIn("rpm", error)

    def test_negative_limits_are_rejected(self):
        for field in ("rpm", "max_output_tokens", "max_concurrent_requests"):
            with self.subTest(field=field):
                cleaned, error = validate_model_config({"id": "m", field: -1})
                self.assertIsNone(cleaned, f"{field}: отрицательное значение должно отклоняться")
                self.assertIn(field, error)

    def test_missing_id_is_reported(self):
        cleaned, error = validate_model_config({"rpm": 5})
        self.assertIsNone(cleaned)
        self.assertIn("id", error)


class CustomProviderModelsValidationTests(unittest.TestCase):
    def setUp(self):
        api_config.initialize_configs()
        self.addCleanup(api_config.set_custom_provider_models, {})

    def test_good_custom_model_reaches_the_runtime_view(self):
        api_config.set_custom_provider_models({
            "gemini": {"Моя модель": {"id": "my-model", "rpm": "7"}}
        })

        models = api_config.api_providers_view()["gemini"]["models"]
        self.assertIn("Моя модель", models)
        self.assertEqual(models["Моя модель"]["rpm"], 7)
        self.assertTrue(models["Моя модель"]["user_defined"])
        self.assertEqual(api_config.custom_model_validation_errors(), [])

    def test_broken_field_is_dropped_while_the_model_survives(self):
        """Модель не должна исчезать: это выглядит как потеря данных.

        Негодным считается отдельное поле — оно убирается, и к модели
        применится значение провайдера по умолчанию.
        """
        api_config.set_custom_provider_models({
            "gemini": {
                "Хорошая": {"id": "good-model", "rpm": 5},
                "Полубитая": {"id": "bad-model", "rpm": "пять", "max_output_tokens": 4096},
            }
        })

        models = api_config.api_providers_view()["gemini"]["models"]
        self.assertIn("Хорошая", models)
        self.assertIn("Полубитая", models, "модель обязана остаться в списке")
        self.assertNotIn("rpm", models["Полубитая"], "негодное поле должно быть убрано")
        self.assertEqual(
            models["Полубитая"]["max_output_tokens"], 4096,
            "исправные поля той же модели должны уцелеть",
        )

        errors = api_config.custom_model_validation_errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["provider"], "gemini")
        self.assertEqual(errors[0]["model"], "Полубитая")
        self.assertIn("rpm", errors[0]["error"])

    def test_errors_are_cleared_on_the_next_successful_load(self):
        api_config.set_custom_provider_models({"gemini": {"Битая": {"id": "x", "rpm": "нет"}}})
        self.assertEqual(len(api_config.custom_model_validation_errors()), 1)

        api_config.set_custom_provider_models({"gemini": {"Целая": {"id": "y", "rpm": 3}}})
        self.assertEqual(api_config.custom_model_validation_errors(), [])

    def test_settings_full_of_junk_still_load(self):
        """Приложение обязано стартовать, что бы ни лежало в настройках."""
        api_config.set_custom_provider_models({
            "gemini": {"Битая": {"id": "x", "max_output_tokens": "много", "rpm": -5}},
            "deepseek": {"Целая": {"id": "y"}},
        })

        providers = api_config.api_providers_view()
        self.assertIn("Целая", providers["deepseek"]["models"])
        broken = providers["gemini"]["models"]["Битая"]
        self.assertNotIn("max_output_tokens", broken)
        self.assertNotIn("rpm", broken)
        self.assertEqual(broken["id"], "x")
        self.assertEqual(len(api_config.custom_model_validation_errors()), 2)


class ShippedProvidersFileTests(unittest.TestCase):
    """Гейт на наш собственный config/api_providers.json.

    Проверять его в рантайме незачем — это артефакт репозитория, а не ввод
    пользователя. Но опечатка в нём должна падать здесь, а не у человека.
    """

    def test_every_shipped_model_satisfies_the_schema(self):
        with open(api_config._PROVIDERS_FILE, encoding="utf-8") as handle:
            providers = json.load(handle)

        failures = []
        checked = 0
        for provider_id, provider in providers.items():
            for display_name, model in (provider.get("models") or {}).items():
                checked += 1
                _cleaned, error = validate_model_config(model)
                if error:
                    failures.append(f"{provider_id}/{display_name}: {error}")

        self.assertGreater(checked, 100, "конфигурация внезапно опустела — проверять нечего")
        self.assertEqual(failures, [], "\n".join(failures))

    def test_numeric_fields_stay_numeric_across_the_whole_file(self):
        """Поле, где встречаются и число и строка, схема поймать не сможет."""
        with open(api_config._PROVIDERS_FILE, encoding="utf-8") as handle:
            providers = json.load(handle)

        seen = {}
        for provider in providers.values():
            for model in (provider.get("models") or {}).values():
                for field in KNOWN_NUMERIC_FIELDS:
                    if field in model:
                        seen.setdefault(field, set()).add(type(model[field]).__name__)

        mixed = {field: sorted(types) for field, types in seen.items() if len(types) > 1}
        self.assertEqual(mixed, {}, f"числовые поля с разнотипными значениями: {mixed}")



class SanitizeModelConfigTests(unittest.TestCase):
    """`sanitize_model_config` — путь загрузки: чинит, но никогда не выбрасывает."""

    def test_keeps_everything_usable_and_names_what_it_removed(self):
        cleaned, errors = sanitize_model_config({
            "id": "m",
            "rpm": "пять",
            "tpm": 250000,
            "deepseek_thinking": "enabled",
        })

        self.assertEqual(cleaned["id"], "m")
        self.assertEqual(cleaned["tpm"], 250000)
        self.assertEqual(cleaned["deepseek_thinking"], "enabled")
        self.assertNotIn("rpm", cleaned)
        self.assertEqual(len(errors), 1)
        self.assertIn("rpm", errors[0])

    def test_reports_every_bad_field_separately(self):
        cleaned, errors = sanitize_model_config({
            "id": "m", "rpm": "нет", "tpm": "тоже нет", "context_length": -1,
        })

        self.assertEqual(cleaned, {"id": "m"})
        self.assertEqual(len(errors), 3)

    def test_a_clean_config_is_returned_untouched_without_errors(self):
        cleaned, errors = sanitize_model_config({"id": "m", "rpm": 5})
        self.assertEqual(cleaned, {"id": "m", "rpm": 5})
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
