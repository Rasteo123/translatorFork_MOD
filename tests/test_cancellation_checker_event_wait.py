import asyncio
import threading
import time
import types
import unittest

from gemini_translator.api.base import BaseApiHandler


def _make_handler(worker):
    handler = BaseApiHandler.__new__(BaseApiHandler)
    handler.worker = worker
    return handler


class CancellationCheckerEventWaitTests(unittest.TestCase):
    def test_checker_returns_immediately_if_already_cancelled(self):
        worker = types.SimpleNamespace(is_cancelled=True, _cancel_async_event=None)

        async def scenario():
            worker._cancel_async_event = asyncio.Event()
            handler = _make_handler(worker)
            await asyncio.wait_for(handler._cancellation_checker(), timeout=1.0)

        asyncio.run(scenario())

    def test_checker_wakes_on_threadsafe_cancel_without_polling(self):
        """cancel() из чужого потока должен будить чекер через событие."""
        worker = types.SimpleNamespace(is_cancelled=False, _cancel_async_event=None)
        wake_delays = []

        async def scenario():
            loop = asyncio.get_running_loop()
            worker._cancel_async_event = asyncio.Event()
            handler = _make_handler(worker)

            def cancel_from_thread():
                time.sleep(0.05)
                worker.is_cancelled = True
                loop.call_soon_threadsafe(worker._cancel_async_event.set)

            thread = threading.Thread(target=cancel_from_thread)
            start = time.monotonic()
            thread.start()
            await asyncio.wait_for(handler._cancellation_checker(), timeout=2.0)
            wake_delays.append(time.monotonic() - start)
            thread.join()

        asyncio.run(scenario())
        # Событийное пробуждение: почти сразу после cancel (без 200мс-поллинга).
        self.assertLess(wake_delays[0], 0.5)

    def test_checker_falls_back_to_polling_without_event(self):
        """Legacy-путь run(): события нет — чекер обязан поллить и завершиться."""
        worker = types.SimpleNamespace(is_cancelled=False)

        async def scenario():
            handler = _make_handler(worker)

            async def cancel_soon():
                await asyncio.sleep(0.05)
                worker.is_cancelled = True

            await asyncio.gather(
                asyncio.wait_for(handler._cancellation_checker(), timeout=2.0),
                cancel_soon(),
            )

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
