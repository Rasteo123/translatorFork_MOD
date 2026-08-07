import asyncio
import threading
import unittest

from gemini_translator.core.async_worker_runtime import AsyncWorkerRuntime


class AsyncWorkerRuntimeTests(unittest.TestCase):
    def test_spawn_runs_coroutine_and_returns_result(self):
        rt = AsyncWorkerRuntime()
        rt.start()
        try:
            async def work():
                await asyncio.sleep(0)
                return 42
            fut = rt.spawn(work())
            self.assertEqual(fut.result(timeout=5), 42)
        finally:
            rt.stop()

    def test_loop_runs_on_its_own_thread(self):
        rt = AsyncWorkerRuntime()
        rt.start()
        try:
            async def who():
                return threading.get_ident()
            loop_thread = rt.spawn(who()).result(timeout=5)
            self.assertNotEqual(loop_thread, threading.get_ident())
        finally:
            rt.stop()

    def test_stop_is_idempotent_and_joins(self):
        rt = AsyncWorkerRuntime()
        rt.start()
        rt.stop()
        rt.stop()  # must not raise
        self.assertFalse(rt._thread.is_alive())

    def test_stop_workers_cancels_running_tasks(self):
        import time
        rt = AsyncWorkerRuntime()
        rt.start()
        try:
            cancelled = {"hit": False}

            async def long_task():
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:
                    cancelled["hit"] = True
                    raise

            rt.spawn(long_task())
            deadline = time.time() + 2
            while not rt._tasks and time.time() < deadline:
                time.sleep(0.01)
            rt.stop_workers()
            self.assertTrue(cancelled["hit"])
        finally:
            rt.stop()

    def test_stop_workers_cancels_nested_untracked_tasks(self):
        rt = AsyncWorkerRuntime()
        rt.start()
        child_started = threading.Event()
        child_cancelled = threading.Event()
        child_holder = {}

        async def child_request():
            child_started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                child_cancelled.set()
                raise

        async def worker():
            child = asyncio.create_task(child_request())
            child_holder["task"] = child
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                # A worker may finish its own cancellation before an in-flight
                # child request has unwound. The runtime still owns that child.
                return

        rt.spawn(worker())
        self.assertTrue(child_started.wait(timeout=2))
        try:
            rt.stop_workers()
            self.assertTrue(child_cancelled.wait(timeout=1))
        finally:
            child = child_holder.get("task")
            if child is not None and not child.done() and rt.loop is not None:
                rt.loop.call_soon_threadsafe(child.cancel)
                child_cancelled.wait(timeout=1)
            rt.stop()


if __name__ == "__main__":
    unittest.main()
