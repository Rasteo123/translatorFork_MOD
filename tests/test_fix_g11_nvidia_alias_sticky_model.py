"""
Регрессионный тест для находки api/bugs/3-nvidia-alias-sticky-model.

NvidiaApiHandler._switch_to_next_model_id мутирует worker.model_id (а не только
локальное состояние хендлера), а _model_id_candidates/_model_id_index строятся
один раз в setup_client на весь жизненный цикл воркера (много глав/задач).
Поэтому 404-фолбэк на alias, случившийся на одной главе, залипает на все
последующие главы этой сессии воркера: воркер больше никогда не пробует
primary_model_id заново, даже если проблема была временной, и RPD-счётчик
(settings_manager.increment_request_count) начинает вестись под другим
model_id для тех же запросов.

Харнесс: боевой NvidiaApiHandler (реальный setup_client + реальный call_api)
на минимальном worker-заглушке (SimpleNamespace) + фейковая aiohttp-сессия,
которая отвечает 404 на primary-модель и 200 (стрим) на alias. Сети и GUI нет.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import asyncio
import unittest
from types import SimpleNamespace

from gemini_translator.api.handlers.nvidia import NvidiaApiHandler

PRIMARY_ID = "meta/llama-3.3-70b-instruct"
ALIAS_ID = "meta/llama-3.3-70b-instruct:latest"


class FakeResponse:
    def __init__(self, status=200, body_text="", stream_lines=None):
        self.status = status
        self._body = body_text
        self._lines = stream_lines or []

    async def text(self):
        return self._body

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
    """Отдаёт 404 для primary-модели и успешный стрим для alias.

    Пишет каждую запрошенную модель в общий список calls, чтобы тест мог
    проверить, с какой модели хендлер начинает КАЖДЫЙ новый вызов call_api.
    """

    def __init__(self, calls):
        self.calls = calls

    def post(self, *args, **kwargs):
        model = kwargs["json"]["model"]
        self.calls.append(model)
        if model == PRIMARY_ID:
            return FakeCtx(FakeResponse(status=404, body_text='{"error":"not found"}'))
        return FakeCtx(
            FakeResponse(
                status=200,
                stream_lines=[
                    'data: {"choices":[{"delta":{"content":"перевод"},"finish_reason":"stop"}]}',
                    "data: [DONE]",
                ],
            )
        )


def _make_handler():
    request_counts = []
    worker = SimpleNamespace(
        provider_config={"is_async": True, "base_timeout": 600},
        model_config={"id": PRIMARY_ID, "alternate_ids": [ALIAS_ID]},
        provider_config_extra=None,
        prompt_builder=SimpleNamespace(system_instruction="sys"),
        temperature=0.7,
        temperature_override_enabled=False,
        thinking_enabled=False,
        api_key=None,
        model_id=None,
        _post_event=lambda *a, **k: None,
        settings_manager=SimpleNamespace(
            increment_request_count=lambda key, model_id: request_counts.append((key, model_id)),
            decrement_request_count=lambda *a, **k: None,
        ),
    )
    handler = NvidiaApiHandler(worker)

    # Реальный setup_client -- как это делает core/worker.py::_setup_sync
    # РОВНО ОДИН РАЗ за жизненный цикл воркера, до основного цикла обработки
    # многих глав.
    client_override = SimpleNamespace(api_key="nv-1234")
    assert handler.setup_client(client_override=client_override, proxy_settings=None) is True

    calls = []
    session = FakeSession(calls)

    async def _get_session():
        return session

    handler._get_or_create_session_internal = _get_session
    return handler, calls, request_counts


class TestNvidiaAliasNotStickyAcrossCalls(unittest.TestCase):
    def test_alias_fallback_does_not_persist_to_next_call_api_invocation(self):
        handler, calls, _ = _make_handler()
        self.assertEqual(handler.worker.model_id, PRIMARY_ID)

        # Глава 3: primary отдаёт 404, хендлер переключается на alias и
        # получает успешный перевод в рамках этого же вызова call_api.
        text_ch3 = asyncio.run(handler.call_api("текст главы 3", "[LOG]"))
        self.assertEqual(text_ch3, "перевод")
        self.assertEqual(calls, [PRIMARY_ID, ALIAS_ID])

        calls.clear()

        # Глава 4: НОВЫЙ вызов call_api на том же хендлере/воркере (как в
        # реальном основном цикле воркера). Он обязан снова начать с
        # primary_model_id, а не молча продолжать с alias, залипшего с
        # предыдущей главы.
        text_ch4 = asyncio.run(handler.call_api("текст главы 4", "[LOG]"))
        self.assertEqual(text_ch4, "перевод")
        self.assertEqual(
            calls[0],
            PRIMARY_ID,
            "call_api должен заново пробовать primary_model_id в начале каждой новой задачи, "
            f"а не продолжать с залипшего alias; фактические запросы модели: {calls}",
        )

    def test_worker_model_id_reset_to_primary_before_each_call(self):
        handler, calls, _ = _make_handler()

        asyncio.run(handler.call_api("текст главы 3", "[LOG]"))
        # После фолбэка на 404 модель на воркере -- alias (это ожидаемо и
        # нужно для payload/RPD-учёта текущего запроса).
        self.assertEqual(handler.worker.model_id, ALIAS_ID)

        # Но воркер -- общий на много глав: _setup_sync вызывается один раз,
        # поэтому НАЧАЛО следующей задачи должно вернуть worker.model_id к
        # primary, иначе главы 4-50 тихо переводятся уже другой моделью.
        calls.clear()
        asyncio.run(handler.call_api("текст главы 4", "[LOG]"))
        self.assertEqual(calls[0], PRIMARY_ID)


if __name__ == "__main__":
    unittest.main()
