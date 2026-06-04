import asyncio
import time
import types
import unittest

from gemini_translator.core.worker import (
    UniversalWorker,
    WORKER_IDLE_WAKE_TIMEOUT_SECONDS,
)


class WorkerIdleTimeoutTests(unittest.TestCase):
    def _worker_with_rpm_wait(self, rpm_wait):
        return types.SimpleNamespace(
            rpm_limiter=types.SimpleNamespace(
                seconds_until_next_allowed=lambda: rpm_wait
            )
        )

    def test_idle_timeout_uses_exact_rpm_wait_when_limited(self):
        # При упоре в RPM спим ровно до следующего разрешённого запроса,
        # а не будимся каждые 2 секунды впустую.
        worker = self._worker_with_rpm_wait(0.4)
        self.assertEqual(UniversalWorker._compute_idle_timeout(worker, True), 0.4)

    def test_idle_timeout_default_when_not_rpm_limited(self):
        worker = self._worker_with_rpm_wait(0.4)
        self.assertEqual(
            UniversalWorker._compute_idle_timeout(worker, False),
            WORKER_IDLE_WAKE_TIMEOUT_SECONDS,
        )

    def test_idle_timeout_default_when_rpm_wait_nonpositive(self):
        worker = self._worker_with_rpm_wait(0.0)
        self.assertEqual(
            UniversalWorker._compute_idle_timeout(worker, True),
            WORKER_IDLE_WAKE_TIMEOUT_SECONDS,
        )


class WorkerEnergyWaitTests(unittest.IsolatedAsyncioTestCase):
    async def test_idle_wait_wakes_on_notification(self):
        worker = types.SimpleNamespace(_wake_event=asyncio.Event())

        async def wake_worker():
            await asyncio.sleep(0.01)
            worker._wake_event.set()

        start = time.perf_counter()
        await asyncio.gather(
            UniversalWorker._wait_for_next_cycle(worker, set(), timeout=1.0),
            wake_worker(),
        )
        elapsed = time.perf_counter() - start

        self.assertLess(elapsed, 0.5)
        self.assertFalse(worker._wake_event.is_set())

    async def test_active_task_completion_wakes_loop(self):
        worker = types.SimpleNamespace(_wake_event=asyncio.Event())

        async def finish_quickly():
            await asyncio.sleep(0.01)

        task = asyncio.create_task(finish_quickly())

        start = time.perf_counter()
        await UniversalWorker._wait_for_next_cycle(worker, {task}, timeout=1.0)
        elapsed = time.perf_counter() - start

        self.assertLess(elapsed, 0.5)
        self.assertTrue(task.done())
