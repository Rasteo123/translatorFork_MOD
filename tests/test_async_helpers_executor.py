import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from gemini_translator.utils.async_helpers import run_sync


class RunSyncExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_given_executor(self):
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="given")
        try:
            def work():
                return threading.current_thread().name
            name = await run_sync(work, executor=pool, timeout=5)
            self.assertTrue(name.startswith("given"))
        finally:
            pool.shutdown(wait=False)


if __name__ == "__main__":
    unittest.main()
