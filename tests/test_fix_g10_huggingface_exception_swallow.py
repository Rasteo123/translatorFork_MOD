"""
Регрессионный тест для находки api/bugs/2-hf-exception-swallow.

HuggingFaceApiHandler.call_api должен пробрасывать доменные исключения
(PartialGenerationError, RateLimitExceededError, ModelNotFoundError, NetworkError и т.д.)
как есть, а не оборачивать их в обычный Exception -- иначе теряются атрибуты
(partial_text, reason, delay_seconds) и тип, необходимые downstream-коду
(base.py:_process_exception_and_counters, error_analyzer.py, emerger_tasks.py)
для правильной обработки (до-генерация хвоста, пауза/ротация ключа, backoff).

Харнесс: боевой HuggingFaceApiHandler на минимальном worker-заглушке (SimpleNamespace)
+ фейковая aiohttp-сессия (async context manager + асинхронный итератор строк стрима).
Сети нет, GUI нет, настройки пользователя не читаются.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import asyncio
import unittest
from types import SimpleNamespace

from gemini_translator.api.errors import (
    ModelNotFoundError,
    NetworkError,
    PartialGenerationError,
    RateLimitExceededError,
)
from gemini_translator.api.handlers.huggingface import HuggingFaceApiHandler


class FakeResponse:
    def __init__(self, status=200, body_text="", stream_lines=None, json_obj=None):
        self.status = status
        self._body = body_text
        self._lines = stream_lines or []
        self._json = json_obj

    async def text(self):
        return self._body

    async def json(self):
        return self._json

    @property
    def content(self):
        async def gen():
            for line in self._lines:
                yield line.encode("utf-8")

        return gen()


class FakeCtx:
    def __init__(self, resp):
        self.resp = resp

    async def __aenter__(self):
        return self.resp

    async def __aexit__(self, *args):
        return False


class FakeSession:
    def __init__(self, resp):
        self.resp = resp

    def post(self, *args, **kwargs):
        return FakeCtx(self.resp)


def make_handler(resp):
    worker = SimpleNamespace(
        provider_config={"is_async": True, "base_timeout": 600},
        model_config={"id": "google/gemma-3-27b-it:scaleway"},
        prompt_builder=SimpleNamespace(system_instruction="sys"),
        temperature=0.7,
        temperature_override_enabled=False,
        api_key="hf_secret_ABCD",
        model_id="google/gemma-3-27b-it:scaleway",
        _post_event=lambda *a, **k: None,
        settings_manager=SimpleNamespace(decrement_request_count=lambda *a, **k: None),
    )
    handler = HuggingFaceApiHandler(worker)
    handler.base_url = "https://router.huggingface.co/v1/chat/completions"

    async def _sess():
        return FakeSession(resp)

    handler._get_or_create_session_internal = _sess
    handler._force_session_reset = lambda *a, **k: None
    return handler


class TestHuggingFaceExceptionSwallow(unittest.TestCase):
    """call_api не должен терять тип доменных исключений в generic except Exception."""

    def test_stream_length_limit_raises_partial_generation_error(self):
        # Стрим обрывается по finish_reason=length -- должен дойти PartialGenerationError
        # с накопленным partial_text, а не голый Exception.
        stream = [
            'data: {"choices":[{"delta":{"content":"<p>Абзац один.</p>"}}]}',
            'data: {"choices":[{"delta":{"content":"<p>Абзац два.</p>"},"finish_reason":"length"}]}',
            'data: [DONE]',
        ]
        handler = make_handler(FakeResponse(status=200, stream_lines=stream))

        with self.assertRaises(PartialGenerationError) as ctx:
            asyncio.run(
                handler.call_api("prompt", "[LOG]", allow_incomplete=False, use_stream=True)
            )

        self.assertEqual(ctx.exception.reason, "LENGTH")
        self.assertIn("Абзац один", ctx.exception.partial_text)

    def test_json_length_limit_raises_partial_generation_error(self):
        # Та же проверка для нестримовой (JSON) ветки.
        handler = make_handler(
            FakeResponse(
                status=200,
                json_obj={
                    "choices": [
                        {"message": {"content": "<p>кусок</p>"}, "finish_reason": "length"}
                    ]
                },
            )
        )

        with self.assertRaises(PartialGenerationError) as ctx:
            asyncio.run(
                handler.call_api("prompt", "[LOG]", allow_incomplete=False, use_stream=False)
            )

        self.assertEqual(ctx.exception.reason, "LENGTH")
        self.assertEqual(ctx.exception.partial_text, "<p>кусок</p>")

    def test_http_401_raises_rate_limit_exceeded_error(self):
        handler = make_handler(FakeResponse(status=401, body_text='{"error":"invalid token"}'))

        with self.assertRaises(RateLimitExceededError):
            asyncio.run(handler.call_api("prompt", "[LOG]", use_stream=True))

    def test_http_404_raises_model_not_found_error(self):
        handler = make_handler(FakeResponse(status=404, body_text='{"error":"not found"}'))

        with self.assertRaises(ModelNotFoundError):
            asyncio.run(handler.call_api("prompt", "[LOG]", use_stream=True))

    def test_http_500_raises_network_error(self):
        handler = make_handler(FakeResponse(status=500, body_text="server exploded"))

        with self.assertRaises(NetworkError):
            asyncio.run(handler.call_api("prompt", "[LOG]", use_stream=True))

    def test_stream_disconnect_raises_partial_generation_error(self):
        # Обрыв стрима на середине -- должен дойти PartialGenerationError с partial_text,
        # а не голый Exception.
        class BrokenResponse(FakeResponse):
            @property
            def content(self):
                async def gen():
                    yield 'data: {"choices":[{"delta":{"content":"<p>половина</p>"}}]}'.encode(
                        "utf-8"
                    )
                    raise ConnectionResetError("stream died")

                return gen()

        handler = make_handler(BrokenResponse(status=200))

        with self.assertRaises(PartialGenerationError) as ctx:
            asyncio.run(handler.call_api("prompt", "[LOG]", use_stream=True))

        self.assertIn("половина", ctx.exception.partial_text)


if __name__ == "__main__":
    unittest.main()
