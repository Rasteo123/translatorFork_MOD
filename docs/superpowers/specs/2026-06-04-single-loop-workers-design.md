# Single-loop async workers — design

- **Date:** 2026-06-04
- **Status:** Design approved; implementation plan pending
- **Branch:** `claude/mystifying-carson-ea41e7`

## Problem

macOS flags the app with high "Energy Impact" during active translation. A live
profile of the running process (`sample` of PID 76770, our worktree build)
showed:

- CPU is low: of 8245 samples, real Python execution (`_PyEval_EvalFrameDefault`)
  ≈ 459; everything else is waiting (`__psynch_cvwait` 66k, `kevent` 57k,
  `mach_msg` 16k). Hourly average ≈ 5% CPU, instantaneous < 1%.
- **20 threads.** Real work, when it happens, is regex text processing + JSON
  stream parsing — normal translation work, no busy-loop.

Conclusion: the energy flag is driven by **idle wake-ups across ~20 threads**,
not CPU. macOS Energy Impact weights wake-ups heavily.

Root cause in code:

- `translation_engine.py:659` — `ThreadPoolExecutor(max_workers=len(api_keys))`:
  one OS thread per API key.
- `base.py:55` `get_worker_loop()` — a **separate asyncio event loop per
  thread** (`_thread_local.loop`).
- aiohttp/asyncio also spawn `SimpleQueue` helper threads (DNS, etc.).

So thread/loop/wake-up count scales with the number of API keys. The workers are
**already async** (`worker.run` at `worker.py:390` drives a per-thread loop);
each just runs in its own thread via `executor.submit(worker.run)`
(`translation_engine.py:865`).

## Goal & non-negotiable constraint

**Goal:** collapse the per-key threads + event loops onto a single shared
asyncio loop, cutting wake-ups, **without reducing translation concurrency**.

**Non-negotiable constraint (the reason this design is shaped the way it is):**
the traffic Google (and every other provider) sees per API key must remain
**identical** — same request rate, same per-key concurrency, same per-key
connection pool. Google rate-limits / flags keys on the *requests* (key, source
IP, rate, concurrent connections), never on client-side threads. Because of the
GIL the current N threads already do not run Python in parallel — the real
concurrency is the async network I/O — so N coroutines on one loop produce the
same network concurrency. We must not become burstier and must not share TCP
connections across keys.

Precise guarantee (avoid over-claiming "same timing"): what is preserved is the
**per-key request-rate ceiling and connection footprint** — set by the per-key
RPM limiter, the per-key brigade cap, and the per-key session. The limiter is
time-based, so any client-side delay can only **space requests further apart,
never make them burstier** — the provider-safety property holds in one
direction. What can shift is *response-processing latency*: handlers do
synchronous CPU work inside their coroutine (JSON decode + buffer accumulation in
the Gemini stream parser, `gemini.py:166`). On a single shared loop such a
section runs without yielding, briefly blocking *all* keys (head-of-line
blocking), whereas the threaded model let the GIL interleave every few ms. For
small chunks this is microseconds; for large streamed responses it could add
latency. Mitigations: keep per-chunk work small / add `await asyncio.sleep(0)`
yield points if a hot parser proves heavy; and a **multi-key streaming
regression/perf test** guards throughput (see Testing).

This is preserved by keeping, per key:
1. the per-key RPM limiter (already per-worker = per-key);
2. the same per-key in-flight concurrency cap (brigade);
3. a **dedicated aiohttp session/connector per key** (see §Per-key session).

## Decisions (from brainstorming)

- **Approach A**: keep `UniversalWorker` per key; change only the execution
  substrate (thread → coroutine on a shared loop). Rejected: B (request-pool,
  too much behavioral change → footprint risk) and C (shared loop over existing
  threads, doesn't remove threads).
- **No feature flag**: full replacement of the per-thread model. Rollback via
  git/branch.
- **Provider scope**: classification is by the handler's `is_async` flag
  (`config/api_providers.json`), not by guesswork. **All `is_async: true`
  handlers run as coroutines on the shared loop** — this includes the
  Playwright-based browser / WorkAscii ChatGPT-Web handlers, which are
  async-native (`await async_playwright().start()`,
  `await asyncio.create_subprocess_exec(...)`) and already dispatch through
  `_async_executor` (`base.py:310`). **Only `is_async: false` handlers** (today:
  `local` Ollama/LM Studio) use the executor path
  (`_sync_executor_wrapper` → `run_sync`). The Perplexity *server* (`curl_cffi`,
  its own process via `server_manager`) is not a worker handler and is out of
  scope.

  > Correction vs. the scope question asked during brainstorming: Playwright
  > ChatGPT-Web/WorkAscii were wrongly called "sync/subprocess" there. They are
  > async-native and stay on the shared loop. The decision ("sync → executor,
  > async → loop") is unchanged; only the example was wrong.

## Architecture

### New component: `AsyncWorkerRuntime`

A single background thread running one `asyncio` loop (`run_forever`), plus two
**bounded** `ThreadPoolExecutor`s (not one per key): a small **default** pool for
DNS/misc offload and a **dedicated** pool for `is_async: false` sync-handler
offload (see §Synchronous handler offload for sizing/rationale).

- `start()` — spin up the thread + loop, and call
  `loop.set_default_executor(self.default_executor)` so aiohttp's `getaddrinfo`
  and bare `run_in_executor(None, …)` use the small DNS pool (see P2a/P3). The
  dedicated sync-handler pool is passed explicitly to `run_sync`.
- `loop` — explicit accessor for the runtime loop, used to bind per-worker
  sessions and to schedule work. Deliberately separate from `get_worker_loop()`
  so the consistency engine is not affected (see P1b).
- `spawn(coro) -> concurrent.futures.Future` — schedule a coroutine on the loop
  from the GUI/engine thread via `run_coroutine_threadsafe`.
- `stop()` — cancel outstanding tasks, stop the loop, join the thread, shut down
  both executors.

`TranslationEngine` owns the runtime: creates it on session start, stops it on
session end. It replaces the per-key `ThreadPoolExecutor`.

### Worker (minimal change)

`UniversalWorker.run` (currently a sync wrapper that grabs `get_worker_loop()`)
splits into:

- `run_async()` — the existing async loop logic (harvest/seed/exit/wait — steps
  1–5, `_wait_for_next_cycle`, the P2 `_compute_idle_timeout`) **unchanged**, now
  scheduled directly on the shared loop.
- `self._worker_loop` = the shared loop; `self._wake_event = asyncio.Event()`
  bound to the shared loop. `notify()` already does
  `loop.call_soon_threadsafe(wake_event.set)` — carries over verbatim.

**Setup must become async.** Today `setup_client()` (e.g. `gemini.py:26`) calls
`_proactive_session_init()`, which does `loop.run_until_complete(...)`
(`base.py:83`). That only works because, in the current model, setup runs in the
worker's own thread *before* its loop starts. On the already-running shared loop
`run_until_complete` raises `RuntimeError: loop already running`. Fix: drop the
proactive sync init; session creation becomes async — either lazy on the first
`await self._get_or_create_session_internal()` (which `call_api` already does,
e.g. `gemini.py:30`) or an explicit `await handler.async_setup()` the worker
coroutine awaits before its main loop. Trivial non-loop setup (assigning
`api_key`, URLs) stays synchronous.

### Per-key session ownership (the footprint guarantee)

Today `base.py:_get_or_create_session_internal` caches the session in
`_thread_local.session` — per-thread = per-key only because there is one thread
per key. On a single thread that would collapse to **one shared session for all
keys**, violating the constraint.

Change: each `UniversalWorker` owns its **own** `ClientSession` + `TCPConnector`
(its own proxy + SSL context), created when its coroutine starts and closed in a
`finally`. The handler uses the worker's session rather than a thread-local one.

The session is created inside an `async` context and bound to
`asyncio.get_running_loop()` (not `get_worker_loop()`). This kills two birds:
creation is naturally async (no `run_until_complete`, see Worker §) and the
session binds to whatever loop is actually running it — the shared runtime loop
for workers, or the consistency engine's own loop in its context — so there is
no cross-loop binding and no need to globally repurpose `get_worker_loop()`.

Result: one connector pool per key → TCP connections per key are isolated
exactly as today. The RPM limiter is already per-worker/per-key; the brigade cap
is unchanged. Per key, the request-rate ceiling and connection pools are
preserved; client-side latency may shift but cannot create bursts (see the
constraint section).

**Session reset on network error must move per-worker too.** Today
`_force_session_reset()` (`base.py:274`) deletes `_thread_local.session` on
aiohttp/`NetworkError` (`base.py:445`) so the next request rebuilds a clean
connection — this is the recovery path for `ServerDisconnected` &c. With
per-worker sessions there is no thread-local to reset, and resetting must target
**only the current worker's** session (close its connector, clear its handle);
the worker's next request lazily recreates it. The reset must not touch any other
worker's session.

### Synchronous handler offload (`is_async: false`)

Only `is_async: false` handlers (today: `local` Ollama/LM Studio) are
synchronous. They already route through `_sync_executor_wrapper` → `run_sync`
(`base.py:386`), which offloads to a thread so the loop stays responsive. Offload
threads scale with concurrent sync calls (few), not with the number of keys.

**Executor wiring — two bounded pools (must be explicit).** `run_sync` calls
`loop.run_in_executor(None, …)` (`async_helpers.py:55`), i.e. the *default*
executor. The genuinely-blocking sync handler (`local.py:83`, Ollama/LM Studio)
can run for many seconds; aiohttp's `getaddrinfo` (DNS) also offloads to an
executor and is short + latency-sensitive. Putting both in one small pool lets a
long `local` call starve DNS for every other key; one big pool re-bloats threads
and undoes the energy goal. So:

- **Default executor** (DNS / misc): small, fixed, bounded — e.g. 4 threads —
  set via `loop.set_default_executor(...)`. DNS is infrequent after warm-up
  (keep-alive reuse), so this stays tiny.
- **Dedicated sync-handler executor**: used only for `is_async: false` offload.
  `run_sync` gains an explicit `executor` argument and `_sync_executor_wrapper`
  passes this pool; size it to the count of concurrent `is_async: false` workers
  (usually 0–2). A long `local` call therefore cannot block DNS.

Net thread count stays ≈ `1 (loop) + ~4 (DNS) + few (sync handlers)` instead of
per-key. Tests pin: (a) `run_in_executor(None, …)` lands on the default pool;
(b) sync-handler offload lands on the dedicated pool; (c) a long fake sync call
does not delay a concurrent DNS-path offload.

### Cancellation & graceful shutdown

A worker spawns **child tasks** per chunk via `asyncio.create_task(...)`
(`worker.py:501`) into `active_tasks`. Today orphans are swept because each
worker owns its loop and the `finally` does `loop.close()` (`worker.py:438`). On
the **shared** loop that no longer holds: the loop is not closed per worker, and
cancelling the outer `run_async` task raises `CancelledError` inside the while
loop — *past* the step-5 `gather` (`worker.py:521`) — so child tasks would be
orphaned on the shared loop and could finish an API call after the session is
closed. So the contract becomes explicit:

- **`run_async()` owns its children.** It wraps the main loop so that on
  `CancelledError` (or any exit) a `finally` **cancels every task in
  `active_tasks`, `await`s them with `gather(return_exceptions=True)`, then
  closes the worker's own session** (`await`, not `run_until_complete` —
  `worker.py:437` currently uses the latter, which can't run on the shared loop).
  No child task outlives its parent or its session.
- Single worker hard stop: existing flags (`is_cancelled` / `is_shutting_down` +
  `wake_event.set()` via `notify()`) **plus** `task.cancel()` on the outer task;
  the `finally` above guarantees clean child teardown.
- Session end: cancel all outer worker tasks → `gather(return_exceptions=True)`
  (each drains its own children + closes its session) → `runtime.stop()` (stop
  loop, join thread, shut down both executors).
- Test: cancelling the outer worker task cancels its in-flight child API task and
  leaves **no pending tasks** on the loop, and the session is closed exactly once.

### GUI events (unchanged)

Workers run on the single loop thread and emit via the EventBus (queued
cross-thread connections to the GUI thread) — one source thread instead of N.
The earlier topic-subscription migration and P2/P3 work all still apply.

### Ramp-up & pacing (preserved)

`ramp_up_timer` (GUI thread) still launches workers gradually; only
`executor.submit(worker.run)` becomes `runtime.spawn(worker.run_async())` on the
same schedule. Gradual start avoids a burst of requests to providers at session
start (reinforces the footprint constraint). `session_monitor_timer` (P3)
unchanged.

### Error handling

- One worker crashing must not kill the loop or other workers: each task is
  wrapped, exceptions logged (existing `try/except` in `run()` preserved).
- Loop thread: `run_forever` guarded; on a fatal error, end the session cleanly.
- Offload errors propagate into the coroutine as normal exceptions → existing
  handler error logic (`NetworkError`, etc.).

## Testing strategy (TDD)

- `AsyncWorkerRuntime`: start/stop lifecycle, `spawn` returns a result,
  `run_in_executor` works, `stop()` joins the thread cleanly.
- **Footprint-guard test**: each worker creates its **own** session/connector
  (not shared) and its own per-key RPM limiter — encodes the Google-safety
  constraint so a future change cannot silently break it.
- Cancellation closes the worker's session; one worker raising does not stop the
  others.
- Existing tests stay green: `test_worker_energy_wait` (P2),
  `test_*_topic_subscriptions`, etc.
- Regression: runtime with 2–3 fake workers (fake handler returning canned text,
  no network) — all tasks processed, per-key RPM pacing respected, events
  emitted, clean shutdown.
- **Async setup (P1a)**: worker setup creates its session without
  `run_until_complete` on the running loop (would raise) — assert setup succeeds
  while the shared loop is running.
- **Executor split (P2a/P3)**: `run_in_executor(None, …)` lands on the small
  default (DNS) pool; sync-handler offload lands on the dedicated pool; a long
  fake sync call does not delay a concurrent default-pool offload (no DNS
  starvation) — assert via thread identity / pool size / timing.
- **Per-worker session reset on network error (P1)**: an aiohttp/`NetworkError`
  closes and clears **only** the current worker's session/connector and the next
  request recreates it; other workers' sessions are untouched.
- **Consistency-engine isolation (P1b)**: with the runtime loop running,
  `consistency_engine._run_handler_awaitable()` still works (its temp-loop path
  is not broken by the shared loop) — assert no cross-loop error.
- **Multi-key streaming perf/regression (P2c)**: N fake workers streaming
  chunked responses concurrently complete within an expected bound — guards
  against head-of-line blocking from synchronous per-chunk parsing on the shared
  loop.

## Known touch points (handle in the plan)

- **Do NOT globally repurpose `get_worker_loop()`.**
  `consistency_engine._run_handler_awaitable()` (`consistency_engine.py:1450`)
  calls `get_worker_loop()` and, if that loop is already running, spins a
  **temporary** loop to run the awaitable. If `get_worker_loop()` returned the
  shared runtime loop, the consistency engine would run handler coroutines /
  sessions bound to the runtime loop on a *different* temp loop → cross-loop
  errors. So the worker runtime exposes its loop via an **explicit** handle
  (e.g. `runtime.loop`); workers run on it directly (their
  `asyncio.get_running_loop()` is the runtime loop). `get_worker_loop()` and the
  consistency engine keep their current behavior, untouched.
- `base.py` `_thread_local` **session** caching moves to per-worker ownership
  (sessions bound to `asyncio.get_running_loop()`, per §Per-key session). The
  `_thread_local.loop` machinery is no longer used to drive workers.
- `_force_session_reset()` (`base.py:274`), called on aiohttp/`NetworkError`
  (`base.py:445`), must reset the **current worker's** session instead of
  `_thread_local.session` (per §Per-key session). This is the recovery path for
  `ServerDisconnected`; it must close + clear only that worker's connector.
- `TaskDBWorker` (2 QThreads for the DB) is **out of scope** — not worker loops.

## Out of scope

- Sharing one connector across keys (would change footprint — rejected).
- Reworking the worker abstraction (Approach B).
- Touching the TaskDBWorker DB threads or the Qt/Cocoa infrastructure threads.

## Success criteria / expectations

**Worker threads no longer scale with the number of API keys.** Instead of one
thread + one event loop per key, the worker-side thread count is bounded and
roughly constant: 1 runtime loop thread + a small DNS/default pool (~4) + the
sync-handler pool (~0–2). (The Qt/Cocoa infra threads and the 2 `TaskDBWorker`
threads are out of scope and unchanged.) Idle wake-ups drop materially because
the per-key event loops — each waking independently on its stream — collapse onto
one loop.

This should yield a meaningful reduction in macOS Energy Impact during
translation — not zero (the GUI loop + active network streaming + parsing still
use CPU and wake-ups), but enough to drop out of the "significant energy" list at
moderate concurrency. Critically: **no change in per-key request behavior**, so
no new risk of provider key blocking.

## Rollback

No feature flag. Revert the branch / git history to restore the per-thread
model.
