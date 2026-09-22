import unittest
from types import SimpleNamespace

from gemini_translator.api.errors import (
    ContentFilterError,
    PartialGenerationError,
    RateLimitExceededError,
    TemporaryRateLimitError,
)
from gemini_translator.api.handlers.openrouter import OpenRouterApiHandler


def _make_handler(model_config, provider_config=None):
    worker = SimpleNamespace(
        provider_config=provider_config or {"is_async": True},
        model_config=model_config,
    )
    return OpenRouterApiHandler(worker)


class OpenRouterHandlerTests(unittest.TestCase):
    def test_provider_extra_headers_are_added_to_requests(self):
        handler = _make_handler(
            {"id": "auto"},
            {
                "is_async": True,
                "extra_headers": {
                    "X-OmniRoute-No-Cache": "true",
                    "x-omniroute-no-memory": "true",
                    "x-omniroute-compression": "off",
                },
            },
        )
        handler.worker.api_key = "endpoint-key"

        headers = handler._build_request_headers()

        self.assertEqual(headers["Authorization"], "Bearer endpoint-key")
        self.assertEqual(headers["X-OmniRoute-No-Cache"], "true")
        self.assertEqual(headers["x-omniroute-no-memory"], "true")
        self.assertEqual(headers["x-omniroute-compression"], "off")

    def test_model_without_reasoning_config_leaves_payload_unchanged(self):
        handler = _make_handler({"id": "translator"})
        payload = {"model": "translator"}

        handler._apply_openai_reasoning_options(payload)

        self.assertEqual(payload, {"model": "translator"})

    def test_model_reasoning_effort_is_added_to_payload(self):
        handler = _make_handler({"id": "translator", "reasoning_effort": "high"})
        payload = {"model": "translator"}

        handler._apply_openai_reasoning_options(payload)

        self.assertEqual(payload["reasoning_effort"], "high")

    def test_provider_reasoning_effort_is_used_as_fallback(self):
        handler = _make_handler(
            {"id": "translator"},
            {"is_async": True, "default_reasoning_effort": "HIGH"},
        )
        payload = {"model": "translator"}

        handler._apply_openai_reasoning_options(payload)

        self.assertEqual(payload["reasoning_effort"], "high")

    def test_model_access_denied_error_is_detected(self):
        response_text = (
            '{"error":{"message":"model gemini-3.5-flash-extra-low is not allowed '
            'for this API key","type":"qroute_error"}}'
        )

        self.assertTrue(
            OpenRouterApiHandler._is_model_access_denied_error(403, response_text)
        )

    def test_generic_key_error_is_not_model_access_denied(self):
        response_text = '{"error":{"message":"invalid api key","type":"auth_error"}}'

        self.assertFalse(
            OpenRouterApiHandler._is_model_access_denied_error(403, response_text)
        )

    def test_model_access_denied_stream_error_retries_once_without_stream(self):
        response_text = (
            '{"error":{"message":"model gemini-3.5-flash-extra-low is not allowed '
            'for this API key","type":"qroute_error"}}'
        )

        self.assertTrue(
            OpenRouterApiHandler._should_retry_without_stream_for_model_access(
                403,
                response_text,
                use_stream=True,
                already_retried=False,
            )
        )
        self.assertFalse(
            OpenRouterApiHandler._should_retry_without_stream_for_model_access(
                403,
                response_text,
                use_stream=True,
                already_retried=True,
            )
        )
        self.assertFalse(
            OpenRouterApiHandler._should_retry_without_stream_for_model_access(
                403,
                response_text,
                use_stream=False,
                already_retried=False,
            )
        )


class _FakeResponse:
    def __init__(self, status, text="", headers=None, stream_lines=None, payload=None):
        self.status = status
        self._text = text
        self.headers = headers or {}
        self._lines = stream_lines or []
        self._payload = payload

    async def text(self):
        return self._text

    async def json(self, **kwargs):
        return self._payload

    @property
    def content(self):
        async def lines():
            for line in self._lines:
                yield line.encode("utf-8")

        return lines()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class _FakeSession:
    def __init__(self, response):
        self._response = response
        self.requests = 0

    def post(self, *args, **kwargs):
        self.requests += 1
        return self._response


def _make_calling_handler(response):
    worker = SimpleNamespace(
        provider_config={"is_async": True, "base_url": "http://127.0.0.1:20128/v1/chat/completions"},
        model_config={"id": "agy/gemini-3.8-flash-low", "max_output_tokens": 32768},
        api_key="endpoint-key",
        model_id="agy/gemini-3.8-flash-low",
        prompt_builder=SimpleNamespace(system_instruction=None),
        temperature_override_enabled=False,
        temperature=None,
        _post_event=lambda *a, **k: None,
    )
    handler = OpenRouterApiHandler(worker)
    handler.base_url = "http://127.0.0.1:20128/v1/chat/completions"
    handler.is_dynamic_local = False
    session = _FakeSession(response)

    async def _session():
        return session

    handler._get_or_create_session_internal = _session
    return handler


_OMNIROUTE_QUOTA_MESSAGE = (
    "Antigravity upstream error (429): You have exhausted your capacity on this "
    "model. Individual quota reached. Contact your administrator to enable overages."
)


class OpenRouterRateLimitTests(unittest.IsolatedAsyncioTestCase):
    """Окно квоты (5 часов у Antigravity) — это пауза, а не исчерпанный ключ.

    Раньше слово «quota» в теле любого ответа помечало ключ исчерпанным на
    сутки, и единственный ключ OmniRoute уводил сессию в тупик посреди книги.
    """

    async def test_429_with_reset_hint_pauses_for_the_hinted_time(self):
        body = (
            '{"error":{"message":"' + _OMNIROUTE_QUOTA_MESSAGE + '"},'
            '"retryAfterMs":900000}'
        )
        handler = _make_calling_handler(_FakeResponse(429, body))

        with self.assertRaises(TemporaryRateLimitError) as raised:
            await handler.call_api("prompt", "log", use_stream=False)

        self.assertEqual(raised.exception.delay_seconds, 900.0)

    async def test_429_pause_is_capped_at_one_hour(self):
        body = '{"error":{"message":"' + _OMNIROUTE_QUOTA_MESSAGE + '"},"retryAfterMs":7200000}'
        handler = _make_calling_handler(_FakeResponse(429, body))

        with self.assertRaises(TemporaryRateLimitError) as raised:
            await handler.call_api("prompt", "log", use_stream=False)

        self.assertEqual(raised.exception.delay_seconds, 3600.0)

    async def test_429_with_reset_days_away_exhausts_the_key(self):
        body = (
            '{"error":{"message":"Antigravity upstream error (429): You have exhausted '
            'your capacity on this model. Your quota will reset after 166h9m16s."}}'
        )
        handler = _make_calling_handler(_FakeResponse(429, body))

        with self.assertRaises(RateLimitExceededError) as raised:
            await handler.call_api("prompt", "log", use_stream=False)

        self.assertEqual(
            raised.exception.retry_after_seconds, 166 * 3600 + 9 * 60 + 16.0
        )
        self.assertIn("166", str(raised.exception))

    async def test_429_without_hint_keeps_the_short_default_pause(self):
        handler = _make_calling_handler(_FakeResponse(429, "Too Many Requests"))

        with self.assertRaises(TemporaryRateLimitError) as raised:
            await handler.call_api("prompt", "log", use_stream=False)

        self.assertEqual(raised.exception.delay_seconds, 20)

    async def test_429_retry_after_header_sets_the_pause(self):
        handler = _make_calling_handler(
            _FakeResponse(429, '{"error":{"message":"rate limit"}}', headers={"Retry-After": "45"})
        )

        with self.assertRaises(TemporaryRateLimitError) as raised:
            await handler.call_api("prompt", "log", use_stream=False)

        self.assertEqual(raised.exception.delay_seconds, 45.0)

    async def test_402_still_exhausts_the_key(self):
        handler = _make_calling_handler(_FakeResponse(402, '{"error":{"message":"Insufficient credits"}}'))

        with self.assertRaises(RateLimitExceededError):
            await handler.call_api("prompt", "log", use_stream=False)

    async def test_403_quota_text_still_exhausts_the_key(self):
        handler = _make_calling_handler(
            _FakeResponse(403, '{"error":{"message":"Daily quota exceeded for this key"}}')
        )

        with self.assertRaises(RateLimitExceededError):
            await handler.call_api("prompt", "log", use_stream=False)


def _stream(*chunks):
    return [f"data: {chunk}" for chunk in chunks] + ["data: [DONE]"]


class OpenRouterContentFilterTests(unittest.IsolatedAsyncioTestCase):
    """Блокировка контента у OpenAI-совместимого шлюза приходит как
    ``finish_reason: "content_filter"`` (OmniRoute сводит к нему SAFETY,
    PROHIBITED_CONTENT, RECITATION и BLOCKLIST Gemini). Раньше обработчик
    смотрел только на ``length`` и отдавал обрезанный текст как готовый
    перевод, так что резервная модель не включалась.
    """

    async def test_stream_cut_by_content_filter_is_a_content_block_with_the_tail(self):
        handler = _make_calling_handler(
            _FakeResponse(
                200,
                stream_lines=_stream(
                    '{"choices":[{"delta":{"content":"Первый абзац."},"finish_reason":null}]}',
                    '{"choices":[{"delta":{},"finish_reason":"content_filter"}]}',
                ),
            )
        )

        with self.assertRaises(PartialGenerationError) as raised:
            await handler.call_api("prompt", "log", use_stream=True)

        self.assertEqual(raised.exception.reason, "CONTENT_FILTER")
        self.assertEqual(raised.exception.partial_text, "Первый абзац.")

    async def test_stream_blocked_before_any_text_is_a_content_filter_error(self):
        handler = _make_calling_handler(
            _FakeResponse(
                200,
                stream_lines=_stream('{"choices":[{"delta":{},"finish_reason":"content_filter"}]}'),
            )
        )

        with self.assertRaises(ContentFilterError):
            await handler.call_api("prompt", "log", use_stream=True)

    async def test_full_response_cut_by_content_filter_is_a_content_block_with_the_tail(self):
        handler = _make_calling_handler(
            _FakeResponse(
                200,
                payload={
                    "choices": [
                        {"message": {"content": "Первый абзац."}, "finish_reason": "content_filter"}
                    ]
                },
            )
        )

        with self.assertRaises(PartialGenerationError) as raised:
            await handler.call_api("prompt", "log", use_stream=False)

        self.assertEqual(raised.exception.reason, "CONTENT_FILTER")
        self.assertEqual(raised.exception.partial_text, "Первый абзац.")

    async def test_full_response_blocked_before_any_text_is_a_content_filter_error(self):
        handler = _make_calling_handler(
            _FakeResponse(
                200,
                payload={"choices": [{"message": {"content": ""}, "finish_reason": "content_filter"}]},
            )
        )

        with self.assertRaises(ContentFilterError):
            await handler.call_api("prompt", "log", use_stream=False)

    async def test_finished_stream_is_still_returned_whole(self):
        handler = _make_calling_handler(
            _FakeResponse(
                200,
                stream_lines=_stream(
                    '{"choices":[{"delta":{"content":"Весь текст."},"finish_reason":null}]}',
                    '{"choices":[{"delta":{},"finish_reason":"stop"}]}',
                ),
            )
        )

        self.assertEqual(await handler.call_api("prompt", "log", use_stream=True), "Весь текст.")


if __name__ == "__main__":
    unittest.main()
