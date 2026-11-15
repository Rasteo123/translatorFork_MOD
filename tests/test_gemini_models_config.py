import json
from pathlib import Path


def test_latest_gemini_flash_models_are_configured():
    config_path = Path(__file__).resolve().parents[1] / "config" / "api_providers.json"
    providers = json.loads(config_path.read_text(encoding="utf-8"))
    models = providers["gemini"]["models"]

    expected = {
        "Gemini 3.8 Flash": {
            "id": "gemini-3.8-flash",
            "rpm": 5,
            "rpd": 20,
            "max_concurrent_requests": 10,
        },
        "Gemini 3.6 Flash": {
            "id": "gemini-3.6-flash",
            "rpm": 5,
            "rpd": 20,
            "max_concurrent_requests": 10,
        },
        "Gemini 3.5 Flash-Lite": {
            "id": "gemini-3.5-flash-lite",
            "rpm": 15,
            "rpd": 500,
            "max_concurrent_requests": 15,
        },
        "Gemini 3.1 Flash-Lite": {
            "id": "gemini-3.1-flash-lite",
            "rpm": 15,
            "rpd": 500,
            "max_concurrent_requests": 15,
        },
    }

    for display_name, expected_values in expected.items():
        model = models[display_name]
        for key, value in expected_values.items():
            assert model[key] == value
        assert model["tpm"] == 250000
        assert model["context_length"] == 230000
        assert model["max_output_tokens"] == 65536
        assert model["needs_chunking"] is True
        assert model["thinkingLevel"] == ["high", "medium", "low", "minimal"]
        assert model["min_thinking_budget"] == "minimal"

    obsolete_ids = {
        "gemini-3.7-flash",
        "gemini-3.5-flash",
        "gemini-3.1-flash-lite-preview",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-robotics-er-1.5-preview",
        "gemini-robotics-er-1.6-preview",
    }
    assert obsolete_ids.isdisjoint(model["id"] for model in models.values())


def test_default_gemini_model_is_available_from_both_catalogues():
    from gemini_translator.api import config

    default = config.default_model_name()
    assert default == "Gemini 3.5 Flash-Lite"
    assert config.api_providers()["gemini"]["models"][default]["id"] == "gemini-3.5-flash-lite"
    assert config._DEFAULT_API_PROVIDERS_CONFIG["gemini"]["models"][default]["id"] == "gemini-3.5-flash-lite"


def test_reader_can_select_current_free_gemini_tts_models():
    from gemini_reader_v3 import FLASH_TTS_MODELS

    config_path = Path(__file__).resolve().parents[1] / "config" / "api_providers.json"
    models = json.loads(config_path.read_text(encoding="utf-8"))["gemini"]["models"]

    for label, model_id in (
        ("Gemini 3.8 Flash TTS", "gemini-3.8-flash-tts"),
        ("Gemini 3.8 Flash-Lite TTS", "gemini-3.8-flash-lite-tts"),
    ):
        assert FLASH_TTS_MODELS[label] == models[label]["id"] == model_id
        assert models[label]["input_token_limit"] == 8192
        assert models[label]["max_output_tokens"] == 16384
