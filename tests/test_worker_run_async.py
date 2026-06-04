import asyncio
import types
import unittest


class WorkerRunAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_drains_children_and_closes_session(self):
        from gemini_translator.core.worker import UniversalWorker

        closed = {"session": False}
        child_started = asyncio.Event()
        child_cancelled = {"hit": False}

        async def fake_child():
            child_started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                child_cancelled["hit"] = True
                raise

        async def fake_cleanup():
            closed["session"] = True

        teardown = {"cancel_called": False}

        worker = types.SimpleNamespace(
            provider_config={"needs_warmup": False},   # real attr the code reads
            use_warmup=False,
            active_tasks=set(),
            _setup_sync=lambda: None,                  # success = no raise
            _perform_warmup=lambda: asyncio.sleep(0),
            cancel=lambda: teardown.__setitem__("cancel_called", True),
            api_handler_instance=types.SimpleNamespace(
                _close_thread_session_internal=fake_cleanup,
            ),
        )

        async def fake_processing_loop():
            worker.active_tasks = {asyncio.create_task(fake_child())}
            await child_started.wait()
            await asyncio.sleep(60)                     # park until cancelled

        worker._async_processing_loop = fake_processing_loop

        task = asyncio.create_task(UniversalWorker.run_async(worker))
        await asyncio.wait_for(child_started.wait(), timeout=2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertTrue(child_cancelled["hit"], "child task must be cancelled")
        self.assertTrue(closed["session"], "session cleanup hook must run")
        self.assertTrue(teardown["cancel_called"],
                        "cancel() must run for EventBus unsubscribe + rescue parity")
        self.assertIsNone(worker._worker_loop)
        self.assertIsNone(worker._wake_event)


if __name__ == "__main__":
    unittest.main()
