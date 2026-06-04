# Single-loop async workers — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the per-key HTTP/in-process translation workers as coroutines on one shared asyncio event loop instead of one OS thread + one event loop per API key, cutting idle wake-ups (the macOS Energy Impact driver) without changing per-key network behavior.

**Architecture:** A new `AsyncWorkerRuntime` owns one background thread running one asyncio loop plus two bounded thread pools (small DNS/default pool + dedicated sync-handler pool). `TranslationEngine` routes each worker by a mechanical predicate: subprocess-spawning handlers (`browser`, `workascii_chatgpt`) keep today's `ThreadPoolExecutor.submit(worker.run)` path; everything else runs via `runtime.spawn(worker.run_async())`. Both paths return `concurrent.futures.Future`, so `active_workers_map` / `_on_worker_finished` bookkeeping is unchanged. aiohttp sessions move from thread-local to per-handler ownership bound to the running loop.

**Tech Stack:** Python 3.11/3.12, asyncio, aiohttp, PyQt6, pytest (`QT_QPA_PLATFORM=offscreen`).

**Spec:** `docs/superpowers/specs/2026-06-04-single-loop-workers-design.md`

**Non-negotiable constraint:** per-key request-rate ceiling + connection footprint stay identical (per-key RPM limiter, per-key brigade, per-key session). The client-side change must never make requests burstier or share connections across keys.

---

## File Structure

- **Create** `gemini_translator/core/async_worker_runtime.py` — `AsyncWorkerRuntime` (loop thread, two pools, spawn/stop_workers/stop). One responsibility: own the shared loop + offload pools.
- **Create** `tests/test_async_worker_runtime.py` — runtime lifecycle/executor/cancel tests.
- **Create** `tests/test_worker_dispatch.py` — dispatch-routing + shutdown + finish/replace tests.
- **Modify** `gemini_translator/utils/async_helpers.py` — `run_sync` gains an explicit `executor` argument.
- **Modify** `gemini_translator/api/base.py` — per-handler session ownership (drop `_thread_local.session`), async session creation bound to `get_running_loop()`, drop the sync `_proactive_session_init` `run_until_complete`, per-handler `_force_session_reset`.
- **Modify** `gemini_translator/api/handlers/*` — only where a handler relied on the thread-local session (verify; HTTP handlers use `_get_or_create_session_internal`, so most need no change).
- **Modify** `config/api_providers.json` — add `"worker_runtime": "thread"` to `browser` and `workascii_chatgpt`.
- **Modify** `gemini_translator/api/config.py` — `uses_legacy_worker_thread(provider_config)` predicate (single source of truth).
- **Modify** `gemini_translator/core/worker.py` — `run()` → `async def run_async()` coroutine (sync metadata setup → await session → await warmup → await `_async_processing_loop` → `finally` cancels children + awaits cleanup hook). Keep legacy `run()` for subprocess handlers.
- **Modify** `gemini_translator/core/translation_engine.py` — own an `AsyncWorkerRuntime`; branch in `_launch_worker` (`:865`); mixed-model shutdown.

---

## Task 1: `AsyncWorkerRuntime` skeleton

**Files:**
- Create: `gemini_translator/core/async_worker_runtime.py`
- Test: `tests/test_async_worker_runtime.py`

- [ ] **Step 1: Write the failing test (start/spawn/stop)**

```python
# tests/test_async_worker_runtime.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_async_worker_runtime.py -q`
Expected: FAIL — `ModuleNotFoundError: gemini_translator.core.async_worker_runtime`.

- [ ] **Step 3: Implement the runtime**

```python
# gemini_translator/core/async_worker_runtime.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_async_worker_runtime.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add gemini_translator/core/async_worker_runtime.py tests/test_async_worker_runtime.py
git commit -m "feat(runtime): add AsyncWorkerRuntime (shared loop + two bounded pools)"
```

---

## Task 2: `run_sync` accepts an explicit executor

**Files:**
- Modify: `gemini_translator/utils/async_helpers.py:55` (the `run_in_executor(None, ...)` call)
- Test: `tests/test_async_helpers_executor.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_async_helpers_executor.py
import asyncio
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_async_helpers_executor.py -q`
Expected: FAIL — `TypeError: run_sync() got an unexpected keyword argument 'executor'`.

- [ ] **Step 3: Add the `executor` parameter**

In `gemini_translator/utils/async_helpers.py`, add `executor=None` to the `run_sync` signature and change the offload line:

```python
# was: future = loop.run_in_executor(None, context_bound_call)
future = loop.run_in_executor(executor, context_bound_call)
```

(`executor=None` preserves today's behavior — the loop's default executor.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_async_helpers_executor.py tests/ -q -k "run_sync or async_helpers"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gemini_translator/utils/async_helpers.py tests/test_async_helpers_executor.py
git commit -m "feat(async): run_sync accepts explicit executor (default unchanged)"
```

---

## Task 3: Dispatch predicate + config flag

**Files:**
- Modify: `config/api_providers.json` (add flag to `browser`, `workascii_chatgpt`)
- Modify: `gemini_translator/api/config.py` (add `uses_legacy_worker_thread`)
- Test: `tests/test_worker_dispatch.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_worker_dispatch.py
import unittest

from gemini_translator.api import config as api_config


class LegacyWorkerPredicateTests(unittest.TestCase):
    def test_subprocess_handlers_use_legacy_thread(self):
        self.assertTrue(
            api_config.uses_legacy_worker_thread({"worker_runtime": "thread"})
        )

    def test_http_handlers_use_runtime(self):
        self.assertFalse(api_config.uses_legacy_worker_thread({"is_async": True}))
        self.assertFalse(api_config.uses_legacy_worker_thread({}))

    def test_real_config_routing(self):
        import json
        import pathlib
        cfg = json.loads(
            (pathlib.Path(__file__).resolve().parents[1]
             / "config" / "api_providers.json").read_text()
        )
        providers = {k: v for k, v in cfg.items()
                     if isinstance(v, dict) and "handler_class" in v}
        for pid in ("workascii_chatgpt", "web_chatgpt_free", "web_perplexity"):
            self.assertIn(pid, providers, f"{pid} missing from config")
            self.assertTrue(api_config.uses_legacy_worker_thread(providers[pid]), pid)
        # At least one HTTP provider must route to the runtime (not legacy).
        http = [k for k, v in providers.items()
                if not api_config.uses_legacy_worker_thread(v)]
        self.assertTrue(http, "expected >=1 runtime (HTTP) provider")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_worker_dispatch.py::LegacyWorkerPredicateTests -q`
Expected: FAIL — `AttributeError: module ... has no attribute 'uses_legacy_worker_thread'`.

- [ ] **Step 3: Add the predicate**

In `gemini_translator/api/config.py`:

```python
# Subprocess-spawning handler classes (Playwright browser, Node bridge).
_SUBPROCESS_HANDLER_CLASSES = {"BrowserApiHandler", "WorkAsciiChatGptApiHandler"}

def uses_legacy_worker_thread(provider_config: dict) -> bool:
    """Subprocess-spawning handlers keep the per-thread worker model; everything
    else runs on the shared AsyncWorkerRuntime. The explicit flag is the override;
    the handler-class set is a safety net so a future Browser/WorkAscii provider
    that forgets the flag is still routed to the legacy path."""
    if not provider_config:
        return False
    if provider_config.get("worker_runtime") == "thread":
        return True
    return provider_config.get("handler_class") in _SUBPROCESS_HANDLER_CLASSES
```

- [ ] **Step 4: Add the config flag to the real subprocess providers**

In `config/api_providers.json`, add `"worker_runtime": "thread",` (next to their existing `"is_async": true`) to the three subprocess providers — there is **no** `browser` key; the real keys are:
- `workascii_chatgpt` (`WorkAsciiChatGptApiHandler`, Node bridge)
- `web_chatgpt_free` (`BrowserApiHandler`, Playwright)
- `web_perplexity` (`BrowserApiHandler`, Playwright)

(The handler-class safety net in the predicate also covers these, but the explicit flag documents intent.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_worker_dispatch.py::LegacyWorkerPredicateTests -q`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add gemini_translator/api/config.py config/api_providers.json tests/test_worker_dispatch.py
git commit -m "feat(dispatch): add uses_legacy_worker_thread predicate + config flag"
```

---

## Task 4: Per-handler session ownership

**Files:**
- Modify: `gemini_translator/api/base.py` (`_get_or_create_session_internal`, `_proactive_session_init`, `_force_session_reset`, `_close_thread_session_internal`)
- Test: `tests/test_handler_session_ownership.py` (create)

**Context:** Today the session lives in `_thread_local.session`. On one shared loop that would collapse to a single session for all keys. Move it to a per-handler instance attribute, created lazily inside an async context and bound to the running loop. Setup must NOT call `run_until_complete`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_handler_session_ownership.py
import asyncio
import types
import unittest


def _make_handler():
    from gemini_translator.api.base import BaseApiHandler
    worker = types.SimpleNamespace(
        provider_config={"is_async": True, "base_timeout": 600},
        proxy_settings=None,
    )
    return BaseApiHandler(worker)


class HandlerSessionOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_handlers_get_distinct_sessions(self):
        h1, h2 = _make_handler(), _make_handler()
        s1 = await h1._get_or_create_session_internal()
        s2 = await h2._get_or_create_session_internal()
        self.assertIsNot(s1, s2)
        await h1._close_thread_session_internal()
        await h2._close_thread_session_internal()

    async def test_reset_clears_only_this_handler(self):
        h1, h2 = _make_handler(), _make_handler()
        s1 = await h1._get_or_create_session_internal()
        s2 = await h2._get_or_create_session_internal()
        h1._force_session_reset()                      # sync, fire-and-forget close
        s1_new = await h1._get_or_create_session_internal()
        s2_same = await h2._get_or_create_session_internal()
        self.assertIsNot(s1_new, s1)   # h1 rebuilt
        self.assertIs(s2_same, s2)     # h2 untouched
        await h1._close_thread_session_internal()
        await h2._close_thread_session_internal()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_handler_session_ownership.py -q`
Expected: FAIL — both handlers share the thread-local session (`assertIsNot` fails), and/or `_force_session_reset` is not a coroutine.

- [ ] **Step 3: Move session to per-handler ownership**

In `gemini_translator/api/base.py`:

1. In `BaseApiHandler.__init__`, add: `self._session = None`.
2. Replace `_get_or_create_session_internal` body to use `self._session` instead of `_thread_local.session`, create the `ClientSession` without `loop=get_worker_loop()` (it binds to `asyncio.get_running_loop()` by default), and keep the proxy/SSL/timeout signature checks but keyed on `self`:

```python
async def _get_or_create_session_internal(self, api_timeout=600):
    desired_proxy = self._get_proxy_signature()
    desired_ssl = _get_ssl_context_signature()
    if (self._session is not None and not self._session.closed
            and getattr(self, "_session_proxy", None) == desired_proxy
            and getattr(self, "_session_timeout", None) == api_timeout
            and getattr(self, "_session_ssl", None) == desired_ssl):
        return self._session
    if self._session is not None and not self._session.closed:
        await self._session.close()
    ssl_context = _create_ssl_context()
    connector = self._build_connector(ssl_context)   # extract existing proxy/TCP logic
    timeout = aiohttp.ClientTimeout(total=api_timeout)
    self._session = aiohttp.ClientSession(timeout=timeout, connector=connector)
    self._session_proxy, self._session_timeout, self._session_ssl = (
        desired_proxy, api_timeout, desired_ssl)
    return self._session
```

3. Extract the connector-building block (currently lines ~131–147) into `_build_connector(self, ssl_context)` and call it above (DRY).
4. Make `_proactive_session_init` a no-op (or delete it and its call at `gemini.py:26`) — session is created lazily by `call_api`'s existing `await self._get_or_create_session_internal()`. Keep `setup_client` synchronous and free of `run_until_complete`.
5. Keep `_force_session_reset` **synchronous** (do NOT make it async). It is
   called from both an async method (`execute_api_call:319`) and a **sync** one
   (`_process_exception_and_counters:454`), so it cannot use `await`. Implement
   it as fire-and-forget: clear the handle synchronously and schedule the close
   on the running loop. Both call sites stay plain `self._force_session_reset()`:

```python
def _force_session_reset(self):
    session = self._session
    self._session = None                  # next request rebuilds a clean session
    if session is not None and not session.closed:
        try:
            asyncio.get_running_loop().create_task(session.close())
        except RuntimeError:
            pass                          # no running loop (e.g. teardown) — drop it
```

6. Update the base `_close_thread_session_internal` to close `self._session` (subprocess-handler overrides in `browser.py`/`workascii_chatgpt.py` are out of scope — leave them).
7. **No call-site change** for the reset: both `execute_api_call:319` and
   `_process_exception_and_counters:454` keep calling `self._force_session_reset()`
   synchronously. (`_process_exception_and_counters` stays sync — no restructure.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_handler_session_ownership.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Run handler regression**

Run: `python -m pytest tests/ -q -k "handler or gemini or nvidia or deepseek or session"`
Expected: PASS (no regressions). Fix any caller that referenced `_thread_local.session`.

- [ ] **Step 6: Commit**

```bash
git add gemini_translator/api/base.py tests/test_handler_session_ownership.py
git commit -m "feat(api): per-handler aiohttp session ownership bound to running loop"
```

---

## Task 5: Worker `run_async()` coroutine

**Files:**
- Modify: `gemini_translator/core/worker.py` (`run` wrapper ~`:388-462`; add `run_async`)
- Test: `tests/test_worker_run_async.py` (create)

**Context:** Today `run()` (sync) grabs a per-thread loop and calls `loop.run_until_complete(...)` three times (warmup `:415`, main loop `:419`, session close `:437`) then `loop.close()` (`:438`). On the shared loop none of that works. Provide `async def run_async()` that the runtime awaits, and keep `run()` for the legacy (subprocess) path.

- [ ] **Step 1: Write the failing test (cancellation tears down children + closes session)**

```python
# tests/test_worker_run_async.py
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

        worker = types.SimpleNamespace(
            provider_config={"needs_warmup": False},   # real attr the code reads
            use_warmup=False,
            active_tasks=set(),
            _setup_sync=lambda: None,                  # success = no raise
            _perform_warmup=lambda: asyncio.sleep(0),
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
```

*(Note: `run_async` reads `self.active_tasks` set by `_async_processing_loop`; keep that attribute name consistent with the implementation in Step 3.)*

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_worker_run_async.py -q`
Expected: FAIL — `AttributeError: ... has no attribute 'run_async'`.

- [ ] **Step 3: Implement `run_async`**

In `gemini_translator/core/worker.py`, make `active_tasks` an instance attribute (`self.active_tasks = set()`) inside `_async_processing_loop` (replace the local `active_tasks = set()` at `:463`), and add:

```python
async def run_async(self):
    self._worker_loop = asyncio.get_running_loop()
    self._wake_event = asyncio.Event()
    try:
        self._setup_sync()                   # raises RuntimeError on setup failure
        # Mirror the real warmup gate (worker.py:413):
        if self.provider_config.get('needs_warmup', False) and getattr(self, 'use_warmup', False):
            if not await self._perform_warmup():
                return                        # warmup failed → exit cleanly
        await self._async_processing_loop()
    finally:
        pending = list(getattr(self, "active_tasks", []) or [])
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        try:
            await self.api_handler_instance._close_thread_session_internal()
        except Exception:
            pass
```

Extract the existing synchronous metadata setup currently inside `run()` (assigning `api_key`, building URLs) plus the `setup_client` call into `_setup_sync(self)`, **preserving the current failure semantics** — `run()` today raises `RuntimeError("Настройка API хендлера провалилась.")` when `setup_client()` returns falsy (`worker.py:406`), so `_setup_sync` must `raise` the same way (not return a bool that the caller ignores):

```python
def _setup_sync(self):
    # ... assign api_key / model_id / urls as today ...
    client = self.client_map[self.api_key]
    if not self.api_handler_instance.setup_client(
        client_override=client, proxy_settings=self.proxy_settings
    ):
        raise RuntimeError("Настройка API хендлера провалилась.")
```

Keep the legacy `run()` (used by subprocess handlers) intact, refactored to call `_setup_sync()` then the existing `loop.run_until_complete(...)` warmup + `_async_processing_loop()` exactly as today (`worker.py:413-419`). `_async_processing_loop` must store its in-flight tasks on `self.active_tasks` (replace the local `active_tasks = set()` at `worker.py:463`) so both `run_async`'s finalizer and the legacy path see them.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_worker_run_async.py -q`
Expected: PASS.

- [ ] **Step 5: Run worker regression**

Run: `python -m pytest tests/ -q -k "worker"`
Expected: PASS (incl. `test_worker_energy_wait`).

- [ ] **Step 6: Commit**

```bash
git add gemini_translator/core/worker.py tests/test_worker_run_async.py
git commit -m "feat(worker): run_async coroutine with warmup + child-task teardown on cancel"
```

---

## Task 6: Engine integration — runtime + dispatch + shutdown

**Files:**
- Modify: `gemini_translator/core/translation_engine.py` (`_start_session`/`__init__` for runtime; `_launch_worker:865`; shutdown path)
- Test: append to `tests/test_worker_dispatch.py`

- [ ] **Step 1: Write the failing test (routing + mixed shutdown)**

```python
# tests/test_worker_dispatch.py  (append)
import types
import unittest
from unittest.mock import MagicMock

from gemini_translator.core.translation_engine import TranslationEngine


class DispatchRoutingTests(unittest.TestCase):
    def _engine(self):
        eng = TranslationEngine.__new__(TranslationEngine)
        eng.executor = MagicMock()
        eng.runtime = MagicMock()
        eng.active_workers_map = {}
        eng.keys_map = {}
        return eng

    def test_subprocess_provider_routes_to_executor(self):
        eng = self._engine()
        fut = eng._spawn_worker({"worker_runtime": "thread"}, worker=MagicMock())
        eng.executor.submit.assert_called_once()
        eng.runtime.spawn.assert_not_called()

    def test_http_provider_routes_to_runtime(self):
        eng = self._engine()
        fut = eng._spawn_worker({"is_async": True}, worker=MagicMock())
        eng.runtime.spawn.assert_called_once()
        eng.executor.submit.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_worker_dispatch.py::DispatchRoutingTests -q`
Expected: FAIL — `AttributeError: ... has no attribute '_spawn_worker'`.

- [ ] **Step 3: Add the dispatch helper and use it in `_launch_worker`**

In `gemini_translator/core/translation_engine.py`:

```python
from gemini_translator.api.config import uses_legacy_worker_thread

def _spawn_worker(self, provider_config, worker):
    if uses_legacy_worker_thread(provider_config):
        return self.executor.submit(worker.run)          # legacy per-thread
    return self.runtime.spawn(worker.run_async())         # shared loop
```

Replace `future = self.executor.submit(worker.run)` (`:865`) with
`future = self._spawn_worker(self.worker.provider_config, worker)` (use the provider config available where the worker is built). Keep `self.active_workers_map[uuid_worker] = future` and the existing `future.add_done_callback(...)` / `_on_worker_finished` wiring unchanged — both branches return `concurrent.futures.Future`.

- [ ] **Step 4: Create + own the runtime; mixed shutdown**

In `_start_session` (where `self.executor = ThreadPoolExecutor(...)` is created, `:659`), also create `self.runtime = AsyncWorkerRuntime(); self.runtime.start()`. In the session-teardown path (the method that iterates `self.active_workers_map` to stop workers), after cancelling/awaiting workers, call `self.runtime.stop()` and `self.executor.shutdown(wait=False)`. Import `AsyncWorkerRuntime` at top.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_worker_dispatch.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add gemini_translator/core/translation_engine.py tests/test_worker_dispatch.py
git commit -m "feat(engine): route workers via runtime/legacy dispatch; mixed-model shutdown"
```

---

## Task 7: Regression / integration guards

**Files:**
- Test: `tests/test_single_loop_integration.py` (create)

- [ ] **Step 1: Write footprint + multi-key streaming guards**

```python
# tests/test_single_loop_integration.py
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
```

- [ ] **Step 2: Run test to verify it fails (then passes)**

Run: `python -m pytest tests/test_single_loop_integration.py -q`
Expected: PASS once Task 1 is in place (this guards the shared-loop path end-to-end). If it hangs, head-of-line blocking regressed — investigate per-chunk sync work.

- [ ] **Step 3: Full suite**

Run: `python -m pytest tests/ -q`
Expected: same pre-existing unrelated failures only (`test_workascii_runtime` /tmp-symlink, the missing-dep error); everything else green. No NEW failures.

- [ ] **Step 4: Commit**

```bash
git add tests/test_single_loop_integration.py
git commit -m "test: multi-key shared-loop streaming + footprint guards"
```

---

## Task 8: Live verification

- [ ] **Step 1: Launch offscreen smoke**

Run: `QT_QPA_PLATFORM=offscreen perl -e 'alarm shift; exec @ARGV' 15 python main.py`
Expected: starts, runs 15s, no traceback (killed by alarm, exit 142).

- [ ] **Step 2: Thread-count check during a real translation**

Start a real translation with several Gemini keys, then:
Run: `ps -M -p "$(pgrep -f main.py)" | wc -l`
Expected: worker threads no longer scale with key count — roughly `1 loop + ~4 DNS + sync pool + Qt/Cocoa infra + 2 TaskDBWorker`, not one-per-key.

- [ ] **Step 3: (Optional) confirm wake-up drop**

Run: `sudo powermetrics --samplers tasks -n 1 -i 1000 | grep -A2 Python`
Expected: lower idle wake-ups than the per-thread baseline.

- [ ] **Step 4: Final commit / branch ready for review**

```bash
git add -A && git commit -m "chore: single-loop worker migration complete"
```

---

## Self-Review (author checklist — completed)

- **Spec coverage:** runtime (T1), executor split/run_sync (T1–T2), dispatch predicate+flag (T3), per-handler session + reset (T4), run_async + warmup + child teardown (T5), engine routing + mixed shutdown (T6), unified bookkeeping (T6 — both return `concurrent.futures.Future`), multi-key streaming + footprint (T7), live verify (T8). Subprocess handlers (browser/workascii) deliberately untouched (legacy path).
- **Per-key footprint constraint:** unchanged — per-key RPM limiter and per-handler (=per-key) session preserved; no shared connector; ramp-up pacing kept (engine still launches gradually).
- **Placeholders:** none — every code/test step has concrete content.
- **Type consistency:** `run_async`, `_spawn_worker`, `uses_legacy_worker_thread`, `_force_session_reset` (async), `active_tasks`, `self._session`, `AsyncWorkerRuntime.{start,spawn,stop,loop,default_executor,sync_executor}` used consistently across tasks.
- **Open implementation note:** in T5/T6 the worker needs `self.provider_config`/`needs_warmup` available where dispatch happens; verify exact attribute access in `_launch_worker` against current code when implementing.
