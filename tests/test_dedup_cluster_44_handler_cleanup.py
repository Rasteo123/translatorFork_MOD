"""Характеризационные и маршрутизационные тесты для cluster-44.

_cleanup_handler был продублирован:
  - async-версия в gemini_translator/core/worker_helpers/provider_orchestrator.py
    (глотала исключения очистки БЕЗ логирования);
  - sync-метод ConsistencyEngine._cleanup_handler в
    gemini_translator/core/consistency_engine.py (логировал обе точки отказа
    через logger.warning).

Канонической выбрана поведение consistency_engine (с логированием), вынесенное
в gemini_translator/core/handler_cleanup.cleanup_provider_handler — общая
async-корутина с раздельным logger.warning для двух точек отказа. Оба места
вызова должны идти через неё.
"""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.core.worker_helpers import provider_orchestrator as orchestrator
from gemini_translator.core.consistency_engine import ConsistencyEngine


class _SettingsStub:
    def is_key_limit_active(self, key_info, model_id):
        return False

    def load_proxy_settings(self):
        return None

    def increment_request_count(self, key_to_update, model_id):
        return True

    def decrement_request_count(self, key_to_update, model_id):
        return True


# ---------------------------------------------------------------------------
# (а) Характеризационные тесты на поведение канонической реализации.
# ---------------------------------------------------------------------------
class CleanupProviderHandlerCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def _import_canonical(self):
        from gemini_translator.core.handler_cleanup import cleanup_provider_handler

        return cleanup_provider_handler

    async def test_noop_when_close_method_missing(self):
        cleanup_provider_handler = self._import_canonical()
        handler = SimpleNamespace()  # no _close_thread_session_internal
        # Не должно кидать исключений.
        await cleanup_provider_handler(handler)

    async def test_noop_when_close_method_not_callable(self):
        cleanup_provider_handler = self._import_canonical()
        handler = SimpleNamespace(_close_thread_session_internal="not-callable")
        await cleanup_provider_handler(handler)

    async def test_awaits_awaitable_result_of_cleanup(self):
        cleanup_provider_handler = self._import_canonical()
        calls = []

        async def _async_close():
            calls.append("closed")

        handler = SimpleNamespace(_close_thread_session_internal=lambda: _async_close())
        await cleanup_provider_handler(handler)
        self.assertEqual(calls, ["closed"])

    async def test_plain_sync_return_is_not_awaited(self):
        cleanup_provider_handler = self._import_canonical()
        calls = []

        def _sync_close():
            calls.append("closed")
            return None

        handler = SimpleNamespace(_close_thread_session_internal=_sync_close)
        await cleanup_provider_handler(handler)
        self.assertEqual(calls, ["closed"])

    async def test_logs_warning_when_cleanup_call_raises(self):
        cleanup_provider_handler = self._import_canonical()

        def _raising_close():
            raise RuntimeError("boom-sync")

        handler = SimpleNamespace(_close_thread_session_internal=_raising_close)

        with self.assertLogs(
            "gemini_translator.core.handler_cleanup", level="WARNING"
        ) as captured:
            await cleanup_provider_handler(handler)

        self.assertTrue(
            any("boom-sync" in message for message in captured.output),
            captured.output,
        )

    async def test_logs_warning_when_awaited_result_raises(self):
        cleanup_provider_handler = self._import_canonical()

        async def _raising_async_close():
            raise RuntimeError("boom-async")

        handler = SimpleNamespace(
            _close_thread_session_internal=lambda: _raising_async_close()
        )

        with self.assertLogs(
            "gemini_translator.core.handler_cleanup", level="WARNING"
        ) as captured:
            await cleanup_provider_handler(handler)

        self.assertTrue(
            any("boom-async" in message for message in captured.output),
            captured.output,
        )


# ---------------------------------------------------------------------------
# (б) Тест-маршрутизация: провайдерный (async) путь.
# ---------------------------------------------------------------------------
class ProviderOrchestratorRoutesThroughCanonicalCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_attempt_finally_calls_canonical_cleanup(self):
        close_calls = []

        class _HandlerWithClose:
            def __init__(self, _worker):
                pass

            def setup_client(self, _client, proxy_settings=None):
                return True

            def execute_api_call(self, _prompt, _log_prefix, **_kwargs):
                raise RuntimeError("attempt failed")

            def _close_thread_session_internal(self):
                close_calls.append("closed")

        attempt = orchestrator.ProviderAttempt(
            provider_id="primary",
            model_name="Primary",
            model_config={"id": "primary-model"},
            api_key="primary-key",
            label="primary",
        )
        worker = SimpleNamespace(
            worker_id="worker-1",
            temperature=None,
            temperature_override_enabled=True,
            proxy_settings=None,
        )

        canonical_calls = []
        real_cleanup = orchestrator.cleanup_provider_handler

        async def spy_cleanup(handler, *args, **kwargs):
            canonical_calls.append(handler)
            return await real_cleanup(handler, *args, **kwargs)

        with patch.object(orchestrator, "_provider_info", return_value={"handler_class": "_HandlerWithClose"}), \
             patch.object(orchestrator, "get_api_handler_class", return_value=_HandlerWithClose), \
             patch.object(orchestrator, "cleanup_provider_handler", side_effect=spy_cleanup):
            await orchestrator._run_attempt(worker, attempt, "prompt", "[Test]", {})

        self.assertEqual(len(canonical_calls), 1)
        self.assertEqual(close_calls, ["closed"])


# ---------------------------------------------------------------------------
# (б) Тест-маршрутизация: sync-путь ConsistencyEngine.
# ---------------------------------------------------------------------------
class ConsistencyEngineRoutesThroughCanonicalCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_cleanup_handler_calls_canonical_coroutine(self):
        engine = ConsistencyEngine(_SettingsStub())
        close_calls = []

        class _Handler:
            def _close_thread_session_internal(self):
                close_calls.append("closed")

        handler = _Handler()

        canonical_calls = []
        from gemini_translator.core import consistency_engine as ce_module

        real_cleanup = ce_module.cleanup_provider_handler

        async def spy_cleanup(h, *args, **kwargs):
            canonical_calls.append(h)
            return await real_cleanup(h, *args, **kwargs)

        with patch.object(ce_module, "cleanup_provider_handler", side_effect=spy_cleanup):
            engine._cleanup_handler(handler)

        self.assertEqual(len(canonical_calls), 1)
        self.assertEqual(close_calls, ["closed"])

    def test_cleanup_handler_calls_canonical_coroutine_with_async_close(self):
        # Боевые реализации (api/base.py, handlers/local.py, browser.py,
        # workascii_chatgpt.py) объявляют _close_thread_session_internal как
        # `async def`, поэтому sync-путь ConsistencyEngine должен реально
        # дожидаться такой корутины через _run_handler_awaitable, а не только
        # обычный sync-возврат (см. previous_review issues[2]).
        engine = ConsistencyEngine(_SettingsStub())
        close_calls = []

        class _AsyncHandler:
            def _close_thread_session_internal(self):
                async def _close():
                    close_calls.append("closed")

                return _close()

        engine._cleanup_handler(_AsyncHandler())

        self.assertEqual(close_calls, ["closed"])

    def test_cleanup_handler_does_not_raise_when_run_handler_awaitable_fails(self):
        # previous_review issues[0] (major): раньше тело _cleanup_handler
        # ловило исключения на обеих точках отказа и никогда не бросало
        # наружу. После выноса в cleanup_provider_handler try/except вокруг
        # self._run_handler_awaitable(...) потерялся — ошибки самого
        # _run_handler_awaitable (get_worker_loop/new_event_loop/
        # run_until_complete) стали пробрасываться наружу из _cleanup_handler,
        # что рвёт вызывающий код (close_session_resources,
        # _get_or_create_cached_handler, _invalidate_cached_handler).
        engine = ConsistencyEngine(_SettingsStub())

        class _Handler:
            def _close_thread_session_internal(self):
                pass

        with patch.object(
            engine,
            "_run_handler_awaitable",
            side_effect=RuntimeError("loop already running"),
        ), self.assertLogs(
            "gemini_translator.core.consistency_engine", level="WARNING"
        ) as captured:
            engine._cleanup_handler(_Handler())  # не должно бросать

        self.assertTrue(
            any("loop already running" in message for message in captured.output),
            captured.output,
        )

    def test_cleanup_handler_skips_event_loop_when_nothing_to_close(self):
        # previous_review issues[1] (minor): раньше хендлер без
        # _close_thread_session_internal возвращался мгновенно и loop'а не
        # касался. Проверка callable() ушла внутрь корутины, поэтому сейчас
        # sync-путь заходит в _run_handler_awaitable (get_worker_loop() и
        # т.д.) даже когда чистить нечего.
        engine = ConsistencyEngine(_SettingsStub())

        with patch.object(engine, "_run_handler_awaitable") as mock_run:
            engine._cleanup_handler(SimpleNamespace())  # нет метода очистки

        mock_run.assert_not_called()

    def test_cleanup_handler_logs_warning_on_sync_failure_via_canonical(self):
        # consistency_engine передаёт в канон свой собственный logger, поэтому
        # запись логируется под именем "consistency_engine" (как и до
        # рефакторинга) — это сохраняет прежнюю атрибуцию логов для этого
        # вызывающего кода, при этом сама логика находится в handler_cleanup.
        engine = ConsistencyEngine(_SettingsStub())

        class _RaisingHandler:
            def _close_thread_session_internal(self):
                raise RuntimeError("consistency-boom")

        with self.assertLogs(
            "gemini_translator.core.consistency_engine", level="WARNING"
        ) as captured:
            engine._cleanup_handler(_RaisingHandler())

        self.assertTrue(
            any("consistency-boom" in message for message in captured.output),
            captured.output,
        )


if __name__ == "__main__":
    unittest.main()
