from gemini_translator.api import config as api_config
from gemini_translator.utils.active_keys import (
    owned_active_keys,
    sanitize_active_keys_by_provider,
    saved_keys_by_provider,
)


# Форма повреждённых настроек из отчёта пользователя: каждый набор держит
# ключи провайдера, выбранного перед ним.
KEY_STATUSES = [
    {"provider": "gemini", "key": "gemini-1"},
    {"provider": "gemini", "key": "gemini-2"},
    {"provider": "deepseek", "key": "deepseek-1"},
    {"provider": "nvidia", "key": "nvapi-1"},
    {"provider": "nvidia", "key": "nvapi-2"},
]


def setup_module():
    api_config.initialize_configs()


def test_saved_keys_are_grouped_by_their_provider_in_stored_order():
    statuses = KEY_STATUSES + [
        {"provider": "gemini", "key": "gemini-1"},
        {"provider": "gemini", "key": "  "},
        {"key": "no-provider"},
        "not-a-dict",
    ]

    assert saved_keys_by_provider(statuses) == {
        "gemini": ["gemini-1", "gemini-2"],
        "deepseek": ["deepseek-1"],
        "nvidia": ["nvapi-1", "nvapi-2"],
    }


def test_owned_active_keys_keeps_only_saved_keys_in_active_order():
    active = ["gemini-2", "stale-key", "deepseek-1", "gemini-1", "gemini-2"]

    assert owned_active_keys(active, ["gemini-1", "gemini-2"]) == ["gemini-2", "gemini-1"]


def test_sanitize_drops_keys_saved_under_another_provider():
    active = {
        "gemini": ["gemini-1", "stale-gemini"],
        "deepseek": ["gemini-1", "gemini-2", "stale-gemini"],
        "openrouter": ["nvapi-1", "nvapi-2"],
        "perplexytiApiMOD": ["deepseek-1"],
    }

    assert sanitize_active_keys_by_provider(active, KEY_STATUSES) == {
        "gemini": ["gemini-1"],
        "deepseek": [],
        "openrouter": [],
        "perplexytiApiMOD": [],
    }


def test_sanitize_keeps_session_placeholder_of_provider_without_api_key():
    placeholder = api_config.provider_placeholder_api_key("local")

    sanitized = sanitize_active_keys_by_provider({"local": [placeholder]}, KEY_STATUSES)

    assert sanitized == {"local": [placeholder]}


def test_sanitize_accepts_sets_and_skips_malformed_entries():
    active = {
        "gemini": {"gemini-2"},
        "": ["gemini-1"],
        "deepseek": None,
    }

    assert sanitize_active_keys_by_provider(active, KEY_STATUSES) == {"gemini": ["gemini-2"]}
