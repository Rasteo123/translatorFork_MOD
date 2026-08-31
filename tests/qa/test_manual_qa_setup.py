"""Checking a book translated yesterday must be possible, and must say when it is not."""

from __future__ import annotations

import pytest

from gemini_translator.qa.assembly import (
    first_green_key,
    resolve_manual_qa_model,
)
from gemini_translator.qa.settings import QaSettings


_PROVIDERS = {
    "gemini": {
        "models": {
            "Gemini 3.7 Flash": {"id": "gemini-3.7-flash"},
            "Gemini 3.6 Flash": {"id": "gemini-3.6-flash"},
        }
    },
    "nvidia": {"models": {"Llama 3.3": {"id": "meta/llama-3.3-70b-instruct"}}},
    "local": {"models": {}},
}


class _SettingsManager:
    def __init__(self, last_model: str = "", keys=(), blocked=()) -> None:
        self.last_model = last_model
        self.keys = list(keys)
        self.blocked = set(blocked)

    def get_last_settings(self):
        return {"model": self.last_model}

    def load_key_statuses(self):
        return [dict(item) for item in self.keys]

    def is_key_limit_active(self, key_info, model_id):
        return (key_info.get("key"), model_id) in self.blocked


@pytest.fixture(autouse=True)
def providers(monkeypatch):
    import gemini_translator.api.config as api_config

    monkeypatch.setattr(api_config, "api_providers_view", lambda: _PROVIDERS)
    return _PROVIDERS


def _keys(*pairs):
    return [{"key": key, "provider": provider} for key, provider in pairs]


def test_an_explicitly_chosen_qa_model_always_wins():
    """Явный выбор пользователя не переспрашивается ни у какой эвристики."""
    settings = QaSettings(
        correction_model_mode="custom",
        correction_provider="openai",
        correction_model="gpt-qa",
    )

    assert resolve_manual_qa_model(_SettingsManager(), settings) == ("openai", "gpt-qa")


def test_the_last_translated_model_is_the_next_best_answer():
    """Проверять книгу разумнее той же моделью, которой её переводили."""
    manager = _SettingsManager(last_model="Gemini 3.6 Flash")

    assert resolve_manual_qa_model(manager, QaSettings()) == (
        "gemini",
        "gemini-3.6-flash",
    )


def test_a_model_saved_by_its_identifier_is_also_found():
    """В настройках может лежать и id, и отображаемое имя."""
    manager = _SettingsManager(last_model="gemini-3.7-flash")

    assert resolve_manual_qa_model(manager, QaSettings()) == (
        "gemini",
        "gemini-3.7-flash",
    )


def test_a_retired_model_falls_back_to_a_provider_the_user_works_with():
    """Настоящий случай: сохранённая модель исчезла из реестра за год."""
    manager = _SettingsManager(
        last_model="Gemini 2.5 Flash Preview",
        keys=_keys(
            ("nv-1", "nvidia"),
            ("g-1", "gemini"),
            ("g-2", "gemini"),
            ("g-3", "gemini"),
        ),
    )

    provider, model = resolve_manual_qa_model(manager, QaSettings())

    # Three Gemini keys against one NVIDIA key: the book is checked with the
    # provider the user evidently works with, not with the first row.
    assert (provider, model) == ("gemini", "gemini-3.7-flash")


def test_a_provider_without_models_is_never_chosen():
    manager = _SettingsManager(keys=_keys(("l-1", "local"), ("l-2", "local")))

    assert resolve_manual_qa_model(manager, QaSettings()) == ("", "")


def test_no_keys_at_all_is_an_honest_empty_answer():
    """Пустой ответ — сигнал сказать пользователю, а не начать невозможный проход."""
    assert resolve_manual_qa_model(_SettingsManager(), QaSettings()) == ("", "")


def test_unreadable_settings_never_raise():
    class _Broken:
        def get_last_settings(self):
            raise OSError("settings are locked")

        def load_key_statuses(self):
            raise OSError("settings are locked")

    assert resolve_manual_qa_model(_Broken(), QaSettings()) == ("", "")


# --- the key that runs it ---------------------------------------------------


def test_the_first_healthy_key_of_the_provider_is_used():
    manager = _SettingsManager(
        keys=_keys(("nv-1", "nvidia"), ("g-1", "gemini"), ("g-2", "gemini")),
        blocked=[("g-1", "gemini-3.7-flash")],
    )

    assert first_green_key(manager, "gemini", "gemini-3.7-flash") == "g-2"


def test_a_provider_with_only_exhausted_keys_offers_none():
    manager = _SettingsManager(
        keys=_keys(("g-1", "gemini")), blocked=[("g-1", "gemini-3.7-flash")]
    )

    assert first_green_key(manager, "gemini", "gemini-3.7-flash") == ""
    assert first_green_key(manager, "", "gemini-3.7-flash") == ""
    assert first_green_key(None, "gemini", "gemini-3.7-flash") == ""


def test_an_unreadable_key_status_does_not_hide_the_key():
    class _Partial(_SettingsManager):
        def is_key_limit_active(self, key_info, model_id):
            raise OSError("status is unreadable")

    manager = _Partial(keys=_keys(("g-1", "gemini")))

    assert first_green_key(manager, "gemini", "gemini-3.7-flash") == "g-1"
