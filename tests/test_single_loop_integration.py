import asyncio
import unittest

from gemini_translator.core.async_worker_runtime import AsyncWorkerRuntime


class MultiKeyStreamingTests(unittest.TestCase):
    def test_concurrent_workers_complete_within_bound(self):
        rt = AsyncWorkerRuntime()
        rt.start()
        try:
            async def fake_worker(n):
                # simulate per-chunk parse interleaved with awaits
                total = 0
                for _ in range(50):
                    await asyncio.sleep(0)
                    total += sum(range(200))  # small sync CPU section
                return n
            futs = [rt.spawn(fake_worker(i)) for i in range(8)]
            results = [f.result(timeout=10) for f in futs]
            self.assertEqual(sorted(results), list(range(8)))
        finally:
            rt.stop()


if __name__ == "__main__":
    unittest.main()
