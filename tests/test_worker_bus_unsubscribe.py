import os
import types
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.core.worker import UniversalWorker


class _TopicBus:
    """Минимальная шина с topic-подписками (как EventBus в main.py)."""

    def __init__(self):
        self.subscribers = {}

    def subscribe(self, event_name, callback):
        self.subscribers.setdefault(event_name, []).append(callback)

    def unsubscribe(self, event_name, callback):
        callbacks = self.subscribers.get(event_name, [])
        if callback in callbacks:
            callbacks.remove(callback)

    def total_subscriptions(self):
        return sum(len(cbs) for cbs in self.subscribers.values())


def _make_worker_shell(bus):
    """UniversalWorker без тяжёлой инициализации: только связь с шиной,
    методы подключения/отключения и run() — ровно то, что нужно, чтобы
    проверить жизненный цикл подписки."""
    worker = UniversalWorker.__new__(UniversalWorker)
    worker.bus = bus
    worker._uses_topic_subscription = False
    worker._worker_loop = None
    worker._wake_event = None
    worker._event_topics = (
        'stop_session_requested',
        'graceful_shutdown_requested',
        'cancel_graceful_shutdown',
        'tasks_added',
    )
    worker.worker_id = "test-worker-0001"
    worker.is_cancelled = False
    worker.is_shutting_down = False
    return worker


class WorkerBusUnsubscribeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_run_unsubscribes_from_bus_even_on_failure(self):
        """Воркеры пересоздаются при каждой ротации ключа; если run()
        завершился, а подписка осталась, шина навсегда удерживает весь
        объект воркера (глоссарий, промпт, api handler) — утечка памяти."""
        bus = _TopicBus()
        worker = _make_worker_shell(bus)
        worker._connect_to_bus()
        self.assertEqual(bus.total_subscriptions(), 4)

        def broken_setup(self):
            raise RuntimeError("setup failed in test")

        worker._setup_sync = types.MethodType(broken_setup, worker)
        worker._post_event = lambda *args, **kwargs: None

        worker.run()

        self.assertEqual(
            bus.total_subscriptions(), 0,
            "run() завершился, но воркер остался подписан на шину событий")

    def test_run_async_unsubscribes_when_teardown_is_cancelled(self):
        """Движок останавливает сессию отменой корутин run_async. Если во
        время финальной очистки снова прилетает CancelledError (закрытие
        aiohttp-сессии прервано остановкой рантайма), воркер обязан всё
        равно отписаться от шины — иначе он утекает на каждой остановке."""
        import asyncio
        from types import SimpleNamespace

        bus = _TopicBus()
        worker = _make_worker_shell(bus)
        worker._connect_to_bus()

        worker._setup_sync = types.MethodType(lambda self: None, worker)
        worker.provider_config = {}
        worker.use_warmup = False
        worker.active_tasks = []
        worker._post_event = lambda *args, **kwargs: None
        worker.task_manager = SimpleNamespace(
            rescue_task_by_worker_id=lambda worker_id: None)

        async def cancelled_close():
            raise asyncio.CancelledError()

        worker.api_handler_instance = SimpleNamespace(
            _close_thread_session_internal=cancelled_close)

        async def wait_forever(self):
            await asyncio.Event().wait()

        worker._async_processing_loop = types.MethodType(wait_forever, worker)

        async def scenario():
            task = asyncio.ensure_future(worker.run_async())
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(scenario())

        self.assertEqual(
            bus.total_subscriptions(), 0,
            "run_async отменён, но воркер остался подписан на шину событий")

    def test_cancel_still_unsubscribes(self):
        bus = _TopicBus()
        worker = _make_worker_shell(bus)
        worker._connect_to_bus()

        worker._disconnect_from_bus()

        self.assertEqual(bus.total_subscriptions(), 0)


if __name__ == "__main__":
    unittest.main()
