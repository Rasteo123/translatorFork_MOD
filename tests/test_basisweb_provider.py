from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gemini_translator.api import config as api_config
from gemini_translator.api.errors import ValidationFailedError
from gemini_translator.api.handlers.local import LocalApiHandler


def _handler(name, *, level=None, enabled=True):
    provider = api_config.api_providers()["basisweb"]
    worker = SimpleNamespace(
        provider_config=provider,
        model_config=provider["models"][name],
        thinking_enabled=enabled,
        thinking_level=level,
        temperature_override_enabled=False,
        prompt_builder=SimpleNamespace(system_instruction=None),
        _post_event=lambda *args: None,
    )
    handler = LocalApiHandler(worker)
    handler.setup_client(SimpleNamespace(api_key="test-key"))
    return handler


def _call(handler):
    response = SimpleNamespace(
        status_code=200,
        json=lambda: {"choices": [{"message": {"content": "Перевод"}, "finish_reason": "stop"}]},
    )
    with patch("requests.Session.post", return_value=response) as post:
        assert handler.call_api("prompt", "test", use_stream=False) == "Перевод"
    return post.call_args.kwargs["json"]


@pytest.mark.parametrize("level", ["low", "medium", "high", "xhigh", "max", "ultra"])
def test_new_sol_sends_supported_reasoning_levels(level):
    payload = _call(_handler("GPT 6.1 Sol (Basisweb)", level=level.upper()))
    assert payload["model"] == "gpt-6.1-sol"
    assert payload["reasoning_effort"] == level


def test_new_terra_rejects_unsupported_max_before_sending_request():
    handler = _handler("GPT 6.1 Terra (Basisweb)", level="MAX")
    with patch("requests.Session.post", side_effect=AssertionError("unexpected request")):
        with pytest.raises(ValidationFailedError, match="max"):
            handler.call_api("prompt", "test", use_stream=False)


def test_new_glm_uses_its_advertised_default():
    assert _call(_handler("GLM 5.3 Flash (Basisweb)"))["reasoning_effort"] == "max"


def test_non_reasoning_model_ignores_global_thinking_level():
    payload = _call(_handler("Gemini 3.1 Flash Lite (Basisweb)", level="HIGH"))
    assert payload["model"] == "gemini-3.1-flash-lite"
    assert "reasoning_effort" not in payload


def test_disabled_thinking_does_not_override_provider_default():
    assert "reasoning_effort" not in _call(_handler("GPT 6.1 Sol (Basisweb)", enabled=False))


def test_existing_luna_name_and_low_default_remain_usable():
    payload = _call(_handler("GPT 6 Luna (Basisweb)"))
    assert payload["model"] == "gpt-6-luna"
    assert payload["reasoning_effort"] == "low"
