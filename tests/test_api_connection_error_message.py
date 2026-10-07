import asyncio
from types import SimpleNamespace

import aiohttp
import pytest

from gemini_translator.api.base import BaseApiHandler
from gemini_translator.api.errors import NetworkError
from gemini_translator.api.handlers.gemini import GeminiApiHandler


def make_worker():
    return SimpleNamespace(
        provider_config={"is_async": True}, model_config={"id": "gemini-test"},
        api_key="test-key", model_id="gemini-test", is_cancelled=False,
        temperature=0.7, temperature_override_enabled=False, thinking_enabled=False,
        prompt_builder=SimpleNamespace(system_instruction=""),
        settings_manager=SimpleNamespace(decrement_request_count=lambda *_: None),
    )


def connection_error():
    key = SimpleNamespace(host="generativelanguage.googleapis.com", port=443, ssl=True)
    return aiohttp.ClientConnectorError(key, OSError(None, None))


def assert_connection_guidance(error, original):
    message = str(error)
    assert "Не удалось подключиться" in message
    assert "generativelanguage.googleapis.com:443" in message
    assert "прокси" in message.lower()
    assert "Ошибка SSL" not in message
    assert error.delay_seconds == 30
    assert error.__cause__ is original


def test_base_classifies_connector_error_with_guidance_and_original_cause():
    handler = BaseApiHandler(make_worker())
    original = connection_error()
    with pytest.raises(NetworkError) as raised:
        handler._process_exception_and_counters(original)
    assert_connection_guidance(raised.value, original)


def test_gemini_request_uses_connection_guidance_without_losing_details():
    original = connection_error()

    class RefusingSession:
        def post(self, *_args, **_kwargs):
            return self

        async def __aenter__(self):
            raise original

        async def __aexit__(self, *_args):
            return False

    handler = GeminiApiHandler(make_worker())
    handler.default_url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent"

    async def get_session():
        return RefusingSession()

    handler._get_or_create_session_internal = get_session
    with pytest.raises(NetworkError) as raised:
        asyncio.run(handler.call_api("test", "[TEST]", use_stream=False))
    assert_connection_guidance(raised.value, original)
