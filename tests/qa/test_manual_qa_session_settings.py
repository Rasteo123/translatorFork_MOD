"""A check must ask the model the same way wherever it was started from.

Reported from a live book: every one of 634 chapters came back unchecked with
«ModelNotFoundError: Thinking level MINIMAL is not supported for this model»,
while the same model checked chapters happily during translation.  The
difference was the settings: a session hands QA its own, and the quality window
handed it the proxy and nothing else, so the handler fell back to the minimum
level named in the model config.
"""

from __future__ import annotations

import pytest

from gemini_translator.qa.assembly import manual_session_settings


class _Settings:
    def __init__(self, payload=None, error=None) -> None:
        self.payload = payload
        self.error = error

    def load_settings(self):
        if self.error is not None:
            raise self.error
        return self.payload


_PROXY = {"enabled": True, "type": "SOCKS5", "host": "127.0.0.1", "port": 8080}


def test_the_thinking_the_user_chose_reaches_the_check():
    """HIGH стоит в настройках — значит, проверка спрашивает так же, как перевод."""
    settings = manual_session_settings(
        _Settings({"thinking_enabled": True, "thinking_level": "HIGH", "model": "x"}),
        _PROXY,
    )

    assert settings["thinking_enabled"] is True
    assert settings["thinking_level"] == "HIGH"
    assert settings["proxy_settings"] == _PROXY


def test_a_missing_budget_is_not_a_budget_of_none():
    """None ушёл бы в сравнение с минимумом модели и уронил бы запрос."""
    settings = manual_session_settings(
        _Settings({"thinking_enabled": True, "thinking_level": "HIGH", "thinking_budget": None}),
        _PROXY,
    )

    assert "thinking_budget" not in settings


def test_a_budget_of_zero_is_a_choice_and_survives():
    settings = manual_session_settings(
        _Settings({"thinking_enabled": False, "thinking_budget": 0}), _PROXY
    )

    assert settings["thinking_enabled"] is False
    assert settings["thinking_budget"] == 0


def test_nothing_but_the_thinking_is_borrowed():
    """У проверки своя температура; настройки перевода её не касаются."""
    settings = manual_session_settings(
        _Settings(
            {
                "thinking_enabled": True,
                "thinking_level": "HIGH",
                "temperature": 1.4,
                "model": "Gemini 3.0 Flash Preview",
                "api_keys_with_status": ["secret"],
            }
        ),
        _PROXY,
    )

    assert set(settings) == {"proxy_settings", "thinking_enabled", "thinking_level"}


@pytest.mark.parametrize(
    "broken",
    [
        _Settings(None),
        _Settings("не словарь"),
        _Settings(error=OSError("файл настроек занят")),
    ],
)
def test_unreadable_settings_never_stop_a_check(broken):
    """Без настроек проверка всё ещё возможна — молча, но возможна."""
    settings = manual_session_settings(broken, _PROXY)

    assert settings == {"proxy_settings": _PROXY}


def test_the_quality_window_sends_them():
    """Ради этого всё и делалось: окно передаёт настройки, а не один прокси."""
    import inspect

    from gemini_translator.ui.dialogs import validation

    source = inspect.getsource(
        validation.TranslationValidatorPage._build_manual_quality_coordinator
    )

    assert "manual_session_settings(" in source
    assert 'session_settings={"proxy_settings": proxy_settings}' not in source
