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
same network concurrency and the same per-key timing. We must not become
burstier and must not share TCP connections across keys.

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
- **Provider scope**: HTTP-API providers (gemini, deepseek, nvidia, openrouter,
  huggingface) run as coroutines on the shared loop. Synchronous / subprocess
  handlers (Perplexity `curl_cffi`, Playwright ChatGPT-Web/WorkAscii) run via
  `run_in_executor` so they never block the loop.

## Architecture

### New component: `AsyncWorkerRuntime`

A single background thread running one `asyncio` loop (`run_forever`), plus one
**bounded** `ThreadPoolExecutor` used only to offload synchronous handler work
(not one thread per key).

- `start()` — spin up the thread + loop.
- `spawn(coro) -> concurrent.futures.Future` — schedule a coroutine on the loop
  from the GUI/engine thread via `run_coroutine_threadsafe`.
- `stop()` — cancel outstanding tasks, stop the loop, join the thread, shut down
  the executor.

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

Fast synchronous setup currently in `run()` (e.g. `setup_client`) runs inline if
trivial, otherwise via `run_in_executor`.

### Per-key session ownership (the footprint guarantee)

Today `base.py:_get_or_create_session_internal` caches the session in
`_thread_local.session` — per-thread = per-key only because there is one thread
per key. On a single thread that would collapse to **one shared session for all
keys**, violating the constraint.

Change: each `UniversalWorker` owns its **own** `ClientSession` + `TCPConnector`
(its own proxy + SSL context), created when its coroutine starts and closed in a
`finally`. The handler uses the worker's session rather than a thread-local one.

Result: one connector pool per key → TCP connections per key are isolated
exactly as today. The RPM limiter is already per-worker/per-key; the brigade cap
is unchanged. Network footprint per key is byte-for-byte and tick-for-tick the
same.

### Sync / subprocess handler offload

Perplexity (`curl_cffi`, sync) and Playwright handlers wrap blocking calls in
`await loop.run_in_executor(runtime.executor, fn, …)`. The loop stays
responsive; offload threads scale with the number of concurrent sync calls (few),
not with the number of keys.

### Cancellation & graceful shutdown

- Single worker: existing flag mechanism (`is_cancelled` / `is_shutting_down` +
  `wake_event.set()` via `notify()`) is kept; hard stop also cancels the asyncio
  Task (`task.cancel()`).
- Session end: cancel all worker tasks → `gather(return_exceptions=True)` → each
  worker closes its own session in `finally` → `runtime.stop()` (stop loop, join
  thread, shut down executor).

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

## Known touch points (handle in the plan)

- `consistency_engine.py` also calls `get_worker_loop()` and in places creates a
  `new_event_loop()` — verify it does not conflict with the shared loop.
- `base.py` `_thread_local` session caching moves to per-worker ownership.
  `get_worker_loop()` is repurposed to return the shared runtime loop, so
  existing callers (handlers, `consistency_engine`, `base.py`) transparently use
  it instead of creating per-thread loops.
- `TaskDBWorker` (2 QThreads for the DB) is **out of scope** — not worker loops.

## Out of scope

- Sharing one connector across keys (would change footprint — rejected).
- Reworking the worker abstraction (Approach B).
- Touching the TaskDBWorker DB threads or the Qt/Cocoa infrastructure threads.

## Success criteria / expectations

~20 threads → ~2–3; a large drop in idle wake-ups → a meaningful reduction in
macOS Energy Impact during translation. Not zero: the GUI loop + active network
streaming + parsing still use CPU and wake-ups. Expectation: drop out of the
"significant energy" list at moderate concurrency, and — critically — **no
change in per-key request behavior**, so no new risk of provider key blocking.

## Rollback

No feature flag. Revert the branch / git history to restore the per-thread
model.
