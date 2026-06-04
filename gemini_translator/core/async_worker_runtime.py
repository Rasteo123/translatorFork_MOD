import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor, Future


class AsyncWorkerRuntime:
    """One background thread + one asyncio loop hosting all HTTP/in-process
    worker coroutines, plus two bounded pools: a small default pool (DNS/misc,
    set as the loop default executor) and a dedicated pool for synchronous
    handler offload (passed explicitly to run_sync)."""

    def __init__(self, dns_pool_size: int = 4, sync_pool_size: int = 4):
        self.loop: asyncio.AbstractEventLoop | None = None
        self.default_executor = ThreadPoolExecutor(
            max_workers=dns_pool_size, thread_name_prefix="rt-dns"
        )
        self.sync_executor = ThreadPoolExecutor(
            max_workers=sync_pool_size, thread_name_prefix="rt-sync"
        )
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stopped = False
        self._tasks: set[asyncio.Task] = set()  # mutated only on the loop thread

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="async-worker-runtime", daemon=True
        )
        self._thread.start()
        self._ready.wait(timeout=5)

    def _run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.set_default_executor(self.default_executor)
        self._ready.set()
        try:
            self.loop.run_forever()
        finally:
            self.loop.close()

    def spawn(self, coro) -> Future:
        """Schedule a coroutine on the loop from another thread; track its task."""
        return asyncio.run_coroutine_threadsafe(self._tracked(coro), self.loop)

    async def _tracked(self, coro):
        task = asyncio.current_task()
        self._tasks.add(task)
        try:
            return await coro
        finally:
            self._tasks.discard(task)

    async def _cancel_all(self):
        tasks = [t for t in self._tasks if t is not asyncio.current_task()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def stop_workers(self, timeout: float = 10):
        """Cancel + drain all spawned worker tasks ON the loop (a
        concurrent.futures.Future.cancel() cannot stop a running coroutine)."""
        if self.loop is None or not self.loop.is_running():
            return
        fut = asyncio.run_coroutine_threadsafe(self._cancel_all(), self.loop)
        try:
            fut.result(timeout=timeout)
        except Exception:
            pass

    def stop(self):
        if self._stopped:
            return
        self._stopped = True
        self.stop_workers()                      # drain tasks before stopping loop
        if self.loop is not None and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=10)
        self.default_executor.shutdown(wait=False)
        self.sync_executor.shutdown(wait=False)
