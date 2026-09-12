"""Единая политика повторов при перегрузке сервера (500/502/503).

Три хендлера — DeepSeek, NVIDIA и OpenModel — несли одну и ту же политику
скопированной построчно: `max_retries = 3`, `wait_time = 15.0 * (retry_count + 1)`.
Политика переехала в BaseApiHandler, стала настраиваемой из provider_config и
перестала спать перед отказом: последняя пауза никого не дожидалась, потому что
после неё цикл сразу заканчивался.
"""

import unittest
from types import SimpleNamespace
from unittest import mock

from gemini_translator.api.base import BaseApiHandler
from gemini_translator.api.errors import NetworkError
from gemini_translator.api.handlers.deepseek import DeepseekApiHandler
from gemini_translator.api.handlers.nvidia import NvidiaApiHandler
from gemini_translator.api.handlers.openmodel import OpenModelApiHandler


class _Probe(BaseApiHandler):
    """Минимальный хендлер: нужен только доступ к политике повторов."""

    def call_api(self, *args, **kwargs):  # pragma: no cover - не вызывается
        raise NotImplementedError


def _make_probe(**provider_config):
    worker = SimpleNamespace(
        provider_config={"is_async": True, **provider_config},
        model_config={},
        _post_event=lambda *a, **k: None,
    )
    return _Probe(worker)


class ServerOverloadPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_while_attempts_remain_and_backs_off_linearly(self):
        handler = _make_probe()
        slept = []

        async def fake_sleep(delay):
            slept.append(delay)

        with mock.patch("asyncio.sleep", fake_sleep):
            first = await handler._retry_after_server_overload(503, 1, "Тест")
            second = await handler._retry_after_server_overload(500, 2, "Тест")

        self.assertTrue(first)
        self.assertTrue(second)
        self.assertEqual(slept, [15.0, 30.0])

    async def test_last_allowed_attempt_does_not_sleep_before_giving_up(self):
        """Сон перед отказом никого не дожидается — попыток больше не будет."""
        handler = _make_probe()
        slept = []

        async def fake_sleep(delay):
            slept.append(delay)

        with mock.patch("asyncio.sleep", fake_sleep):
            retry = await handler._retry_after_server_overload(503, 3, "Тест")

        self.assertFalse(retry)
        self.assertEqual(slept, [])

    async def test_status_outside_the_overload_set_is_not_retried(self):
        handler = _make_probe()
        slept = []

        async def fake_sleep(delay):
            slept.append(delay)

        with mock.patch("asyncio.sleep", fake_sleep):
            for status in (400, 401, 404, 429):
                self.assertFalse(
                    await handler._retry_after_server_overload(status, 1, "Тест"),
                    f"статус {status} не должен считаться перегрузкой",
                )
        self.assertEqual(slept, [])

    async def test_provider_config_overrides_attempts_and_delay(self):
        handler = _make_probe(
            server_overload_retries=2,
            server_overload_retry_delay_seconds=0.5,
        )
        slept = []

        async def fake_sleep(delay):
            slept.append(delay)

        with mock.patch("asyncio.sleep", fake_sleep):
            self.assertTrue(await handler._retry_after_server_overload(503, 1, "Тест"))
            self.assertFalse(await handler._retry_after_server_overload(503, 2, "Тест"))

        self.assertEqual(slept, [0.5])

    async def test_broken_config_falls_back_to_defaults(self):
        handler = _make_probe(
            server_overload_retries="три",
            server_overload_retry_delay_seconds="долго",
        )
        slept = []

        async def fake_sleep(delay):
            slept.append(delay)

        with mock.patch("asyncio.sleep", fake_sleep):
            self.assertTrue(await handler._retry_after_server_overload(503, 1, "Тест"))

        self.assertEqual(slept, [15.0])


class _FakeResponse:
    def __init__(self, status, text="перегрузка", payload=None):
        self.status = status
        self._text = text
        self._payload = payload

    async def text(self):
        return self._text

    async def json(self, **kwargs):
        # NVIDIA зовёт response.json(content_type=None)
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class _FakeSession:
    def __init__(self, statuses, payload):
        self._queue = list(statuses)
        self._payload = payload
        self.requests = 0

    def post(self, *args, **kwargs):
        self.requests += 1
        status = self._queue.pop(0)
        if status == 200:
            return _FakeResponse(200, payload=self._payload)
        return _FakeResponse(status)


_OPENAI_PAYLOAD = {"choices": [{"message": {"content": "перевод"}, "finish_reason": "stop"}]}
# OpenModel говорит на диалекте Anthropic: content верхним уровнем.
_ANTHROPIC_PAYLOAD = {"content": [{"type": "text", "text": "перевод"}], "stop_reason": "end_turn"}

HANDLERS = [
    ("DeepSeek", DeepseekApiHandler, "https://api.deepseek.com/chat/completions", _OPENAI_PAYLOAD),
    ("NVIDIA", NvidiaApiHandler, "https://integrate.api.nvidia.com/v1/chat/completions", _OPENAI_PAYLOAD),
    ("OpenModel", OpenModelApiHandler, "https://openmodel.example/v1/messages", _ANTHROPIC_PAYLOAD),
]


def _make_handler(handler_cls, base_url, statuses, payload=_OPENAI_PAYLOAD):
    worker = SimpleNamespace(
        provider_config={"is_async": True, "base_url": base_url},
        model_config={"id": "test-model", "max_output_tokens": 4096},
        api_key="SECRET",
        model_id="test-model",
        prompt_builder=SimpleNamespace(system_instruction=None),
        temperature_override_enabled=False,
        temperature=None,
        _post_event=lambda *a, **k: None,
    )
    handler = handler_cls(worker)
    handler.base_url = base_url
    # setup_client() в тесте не вызывается, а OpenModel читает это поле в call_api.
    if hasattr(handler_cls, "DEFAULT_ANTHROPIC_VERSION"):
        handler.anthropic_version = handler_cls.DEFAULT_ANTHROPIC_VERSION
    session = _FakeSession(statuses, payload)

    async def _session():
        return session

    handler._get_or_create_session_internal = _session
    return handler, session


class OverloadRetryInHandlersTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, handler, statuses):
        slept = []

        async def fake_sleep(delay):
            slept.append(delay)

        with mock.patch("asyncio.sleep", fake_sleep):
            try:
                result = await handler.call_api("prompt", "[log]", use_stream=False)
            except Exception as error:  # noqa: BLE001 - тест разбирает исключение сам
                return None, error, slept
        return result, None, slept

    async def test_overload_then_success_returns_the_answer(self):
        for name, cls, url, payload in HANDLERS:
            with self.subTest(handler=name):
                handler, session = _make_handler(cls, url, [503, 500, 200], payload)
                result, error, slept = await self._run(handler, [503, 500, 200])
                self.assertIsNone(error, f"{name}: неожиданная ошибка {error!r}")
                self.assertEqual(result, "перевод")
                self.assertEqual(session.requests, 3)
                self.assertEqual(slept, [15.0, 30.0])

    async def test_exhausted_attempts_fail_without_a_pointless_final_sleep(self):
        for name, cls, url, payload in HANDLERS:
            with self.subTest(handler=name):
                handler, session = _make_handler(cls, url, [503, 503, 503], payload)
                result, error, slept = await self._run(handler, [503, 503, 503])
                self.assertIsNone(result)
                self.assertIsInstance(error, NetworkError)
                self.assertEqual(session.requests, 3, f"{name}: должно быть ровно 3 запроса")
                self.assertEqual(
                    slept, [15.0, 30.0],
                    f"{name}: перед отказом спать не нужно, попыток больше нет",
                )


if __name__ == "__main__":
    unittest.main()
