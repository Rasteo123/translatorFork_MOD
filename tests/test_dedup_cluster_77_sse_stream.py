"""
Дедуп cluster-77: цикл разбора OpenAI-совместимого SSE-стрима был переписан
почти дословно в deepseek.py, huggingface.py, nvidia.py и openrouter.py.

(a) Характеризационные тесты на каноническую реализацию
    gemini_translator.api.handlers._sse_stream.parse_openai_compatible_sse_stream --
    краевые случаи, которые различали копии (DONE, пустые строки, битый JSON,
    finish_reason, обрыв соединения с/без накопленного текста, capture_raw).

(b) Тесты-маршрутизация: подменяем каноническую функцию monkeypatch'ем и
    проверяем, что каждый бывший "местный" SSE-цикл (deepseek/huggingface/
    nvidia/openrouter) на самом деле идёт через неё. До рефакторинга у каждого
    хендлера была своя копия цикла -- эти тесты обязаны падать (RED) до
    вынесения в общий модуль и проходить (GREEN) после.

Харнесс -- по образцу tests/test_fix_g10_huggingface_exception_swallow.py:
боевой хендлер на SimpleNamespace-воркере + фейковая aiohttp-сессия.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import asyncio
import unittest
from types import SimpleNamespace

from gemini_translator.api.handlers._sse_stream import (
    SSEStreamInterrupted,
    parse_openai_compatible_sse_stream,
)
from gemini_translator.api.handlers import deepseek as deepseek_module
from gemini_translator.api.handlers import huggingface as huggingface_module
from gemini_translator.api.handlers import nvidia as nvidia_module
from gemini_translator.api.handlers import openrouter as openrouter_module
from gemini_translator.api.handlers.deepseek import DeepseekApiHandler
from gemini_translator.api.handlers.huggingface import HuggingFaceApiHandler
from gemini_translator.api.handlers.nvidia import NvidiaApiHandler
from gemini_translator.api.handlers.openrouter import OpenRouterApiHandler


class FakeResponse:
    def __init__(self, status=200, body_text="", stream_lines=None, json_obj=None):
        self.status = status
        self._body = body_text
        self._lines = stream_lines or []
        self._json = json_obj

    async def text(self):
        return self._body

    async def json(self, content_type=None):
        return self._json

    @property
    def content(self):
        async def gen():
            for line in self._lines:
                yield line.encode("utf-8")

        return gen()


class BrokenStreamResponse(FakeResponse):
    """Отдаёт часть строк и обрывается посреди чтения."""

    def __init__(self, lines_before_break, **kwargs):
        super().__init__(**kwargs)
        self._lines_before_break = lines_before_break

    @property
    def content(self):
        async def gen():
            for line in self._lines_before_break:
                yield line.encode("utf-8")
            raise ConnectionResetError("stream died")

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


def _wire_handler(handler, resp):
    handler.base_url = "https://example.invalid/v1/chat/completions"

    async def _sess():
        return FakeSession(resp)

    handler._get_or_create_session_internal = _sess
    handler._force_session_reset = lambda *a, **k: None
    return handler


def _make_deepseek(resp):
    worker = SimpleNamespace(
        provider_config={"is_async": True},
        model_config={"id": "deepseek-chat"},
        prompt_builder=SimpleNamespace(system_instruction="sys"),
        temperature=0.7,
        temperature_override_enabled=False,
        thinking_enabled=False,
        thinking_level=None,
        api_key="ds_secret",
        model_id="deepseek-chat",
        _post_event=lambda *a, **k: None,
        settings_manager=SimpleNamespace(decrement_request_count=lambda *a, **k: None),
    )
    return _wire_handler(DeepseekApiHandler(worker), resp)


def _make_huggingface(resp):
    worker = SimpleNamespace(
        provider_config={"is_async": True, "base_timeout": 600},
        model_config={"id": "google/gemma-3-27b-it:scaleway"},
        prompt_builder=SimpleNamespace(system_instruction="sys"),
        temperature=0.7,
        temperature_override_enabled=False,
        api_key="hf_secret",
        model_id="google/gemma-3-27b-it:scaleway",
        _post_event=lambda *a, **k: None,
        settings_manager=SimpleNamespace(decrement_request_count=lambda *a, **k: None),
    )
    return _wire_handler(HuggingFaceApiHandler(worker), resp)


def _make_nvidia(resp):
    worker = SimpleNamespace(
        provider_config={"is_async": True},
        model_config={"id": "meta/llama-4-scout"},
        prompt_builder=SimpleNamespace(system_instruction="sys"),
        temperature=0.7,
        temperature_override_enabled=False,
        thinking_enabled=False,
        thinking_level=None,
        api_key="nv_secret",
        model_id="meta/llama-4-scout",
        _post_event=lambda *a, **k: None,
        settings_manager=SimpleNamespace(decrement_request_count=lambda *a, **k: None),
    )
    handler = _wire_handler(NvidiaApiHandler(worker), resp)
    handler._reset_model_id_to_primary = lambda: None
    return handler


def _make_openrouter(resp):
    worker = SimpleNamespace(
        provider_config={"is_async": True},
        model_config={"id": "deepseek/deepseek-chat-v3-0324:free"},
        prompt_builder=SimpleNamespace(system_instruction="sys"),
        temperature=0.7,
        temperature_override_enabled=False,
        api_key="or_secret",
        model_id="deepseek/deepseek-chat-v3-0324:free",
        _post_event=lambda *a, **k: None,
        settings_manager=SimpleNamespace(decrement_request_count=lambda *a, **k: None),
    )
    handler = _wire_handler(OpenRouterApiHandler(worker), resp)
    handler.is_dynamic_local = False
    return handler


STREAM_LINES = [
    'data: {"choices":[{"delta":{"content":"Привет"}}]}',
    'data: {"choices":[{"delta":{"content":", мир"},"finish_reason":"stop"}]}',
    'data: [DONE]',
]


class ParseOpenAICompatibleSSEStreamCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """(а) Краевые случаи канонической реализации разбора SSE."""

    async def test_collects_text_and_finish_reason_across_chunks(self):
        response = FakeResponse(stream_lines=STREAM_LINES)

        text, finish_reason, raw = await parse_openai_compatible_sse_stream(response)

        self.assertEqual(text, "Привет, мир")
        self.assertEqual(finish_reason, "stop")
        self.assertIsNone(raw)

    async def test_capture_raw_records_decoded_lines_in_order(self):
        response = FakeResponse(stream_lines=STREAM_LINES)

        _, _, raw = await parse_openai_compatible_sse_stream(response, capture_raw=True)

        self.assertEqual(raw, STREAM_LINES)

    async def test_skips_empty_lines_and_done_marker(self):
        response = FakeResponse(stream_lines=["", "  ", "data: [DONE]"])

        text, finish_reason, _ = await parse_openai_compatible_sse_stream(response)

        self.assertEqual(text, "")
        self.assertIsNone(finish_reason)

    async def test_ignores_lines_without_data_prefix(self):
        response = FakeResponse(stream_lines=["event: ping", ": comment"])

        text, finish_reason, _ = await parse_openai_compatible_sse_stream(response)

        self.assertEqual(text, "")
        self.assertIsNone(finish_reason)

    async def test_skips_invalid_json_and_continues(self):
        response = FakeResponse(
            stream_lines=[
                "data: {not valid json",
                'data: {"choices":[{"delta":{"content":"ok"}}]}',
            ]
        )

        text, _, _ = await parse_openai_compatible_sse_stream(response)

        self.assertEqual(text, "ok")

    async def test_missing_choices_key_does_not_crash(self):
        response = FakeResponse(stream_lines=['data: {"unrelated": true}'])

        text, finish_reason, _ = await parse_openai_compatible_sse_stream(response)

        self.assertEqual(text, "")
        self.assertIsNone(finish_reason)

    async def test_empty_choices_list_does_not_crash(self):
        response = FakeResponse(stream_lines=['data: {"choices": []}'])

        text, finish_reason, _ = await parse_openai_compatible_sse_stream(response)

        self.assertEqual(text, "")
        self.assertIsNone(finish_reason)

    async def test_interruption_with_partial_text_raises_sse_stream_interrupted(self):
        response = BrokenStreamResponse(
            lines_before_break=['data: {"choices":[{"delta":{"content":"половина"}}]}']
        )

        with self.assertRaises(SSEStreamInterrupted) as ctx:
            await parse_openai_compatible_sse_stream(response)

        self.assertEqual(ctx.exception.partial_text, "половина")
        self.assertIsInstance(ctx.exception.original_error, ConnectionResetError)

    async def test_interruption_without_partial_text_reraises_original_error(self):
        response = BrokenStreamResponse(lines_before_break=[])

        with self.assertRaises(ConnectionResetError):
            await parse_openai_compatible_sse_stream(response)


class SSERoutingTests(unittest.TestCase):
    """(б) Каждый бывший локальный SSE-цикл обязан идти через каноническую функцию.

    До рефакторинга -- падает (у хендлера своя копия цикла, patch не перехватывает
    ничего значимого / текст не совпадёт с фейковым возвратом). После рефакторинга --
    проходит.
    """

    def _assert_routes_through_canonical(self, make_handler_fn, handler_module):
        fake_text = "МАРКЕР_ИЗ_КАНОНИЧЕСКОЙ_ФУНКЦИИ"
        calls = []

        async def fake_parse(response, capture_raw=False):
            calls.append((response, capture_raw))
            return fake_text, None, None

        response = FakeResponse(stream_lines=STREAM_LINES)
        handler = make_handler_fn(response)

        original = handler_module.parse_openai_compatible_sse_stream
        handler_module.parse_openai_compatible_sse_stream = fake_parse
        try:
            result = asyncio.run(
                handler.call_api("prompt", "[LOG]", allow_incomplete=True, use_stream=True)
            )
        finally:
            handler_module.parse_openai_compatible_sse_stream = original

        self.assertEqual(len(calls), 1, "хендлер должен вызвать каноническую функцию ровно один раз")
        self.assertEqual(result, fake_text)

    def test_deepseek_routes_stream_parsing_through_canonical_function(self):
        self._assert_routes_through_canonical(_make_deepseek, deepseek_module)

    def test_huggingface_routes_stream_parsing_through_canonical_function(self):
        self._assert_routes_through_canonical(_make_huggingface, huggingface_module)

    def test_nvidia_routes_stream_parsing_through_canonical_function(self):
        self._assert_routes_through_canonical(_make_nvidia, nvidia_module)

    def test_openrouter_routes_stream_parsing_through_canonical_function(self):
        self._assert_routes_through_canonical(_make_openrouter, openrouter_module)


class NvidiaDebugTracingParityTests(unittest.TestCase):
    """Разошедшееся поведение: NVIDIA -- единственный хендлер без debug-трассы.

    Фикс: NVIDIA должен вести себя как остальные -- писать raw_stream_lines
    в _debug_record_response, когда включена трассировка (debug=True), и звать
    _debug_record_request перед циклом ретраев, как deepseek/huggingface/openmodel.
    """

    def test_stream_branch_records_debug_response_when_debug_enabled(self):
        response = FakeResponse(stream_lines=STREAM_LINES)
        handler = _make_nvidia(response)

        recorded_responses = []
        recorded_requests = []
        handler._debug_record_response = lambda *a, **k: recorded_responses.append((a, k))
        handler._debug_record_request = lambda *a, **k: recorded_requests.append((a, k))

        asyncio.run(handler.call_api("prompt", "[LOG]", use_stream=True, debug=True))

        self.assertTrue(recorded_requests, "NVIDIA должен трассировать исходный запрос")
        self.assertTrue(recorded_responses, "NVIDIA должен трассировать ответ стрима при debug=True")


if __name__ == "__main__":
    unittest.main()
