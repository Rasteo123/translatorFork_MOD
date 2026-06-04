# Energy reduction for active translation sessions — design

**Status:** Draft, awaiting user approval
**Date:** 2026-06-04
**Scope:** Reduce CPU/energy of the PyQt6 translator app during an active translation session, without removing features or redesigning the UI.

## Context

Prior energy work (commits `feat(energy): reduce translation wakeups`, `feat(runtime): add shared async worker runtime`, `Reduce hidden log rendering energy usage`) cut idle and worker-loop wakeups. macOS still flags the process during **active** sessions: bursts of `main.py` CPU at 70%+ correlate with Qt timers re-driving the task table redraw and the SQLite-backed `_get_ui_state_list_background` cache rebuild on every state change.

What is already in place (do NOT redo):
- `_redraw_timer` (35ms, `setSingleShot`) coalesces `task_state_changed` → redraw.
- `_snapshot_save_timer` (15s, `setSingleShot`) coalesces SQLite snapshot autosaves.
- `_pending_log_data` batches log inserts at 250ms; hidden log skips rendering.
- `_populate_row` gates `setText` of the status cell on text diff.

What remains expensive during active sessions:
- `_selective_update` (chapter_list_widget.py:769) iterates every row and `_update_row_status` (881) unconditionally calls `setText` / `setForeground` / `setToolTip` — no diff gating on that branch.
- `_get_ui_state_list_background` (task_manager.py:1243) does a full `SELECT ... FROM tasks` plus JSON-parse plus a Python-level full-list diff on every notification, however small the actual change.
- Existing `_is_updating_cache` guard silently drops requests that arrive while a worker is mid-flight, with no follow-up cycle.

## Goal

Cut the per-event UI and DB cost during active translation. Preserve all visible behaviour. No layout, no widget removal, no API surface breakage.

## Approach

Ship in **two independently testable phases**. Each phase has its own merge candidate so we can stop after phase 1 if it already pays for itself.

### Phase 1 — UI-side gating (low risk, fast win)

**`gemini_translator/ui/widgets/chapter_list_widget.py`**

- `_update_row_status` (881): before any setter, read current values from the items:
  - `status_item.text()` vs new `display_text`
  - `status_item.foreground().color().name()` vs new `color_hex`
  - **`item_task.foreground().color().name()` vs new `color_hex`** (903-904 sets brush on *both* items; comparing only one risks leaving the task-cell colour stale)
  - `status_item.toolTip()` vs the new tooltip string
  - If all four match: `return` without constructing `QBrush` or calling setters.
- `_populate_row` (706): the text-diff at line 744 stays. Add equivalent diff gates around `item_task.setToolTip(tooltip_text)` (729) and `item_task.setData(UserRole, task_tuple_for_ui_role)` (730) — both should compare against the current value before assigning.

**`gemini_translator/ui/widgets/task_management_widget.py`**

- New private helper `_apply_active_session_redraw_tuning(active: bool)`:
  - `active=True`: `_redraw_timer.setInterval(150)` and `_redraw_timer.setTimerType(Qt.TimerType.CoarseTimer)`. `CoarseTimer` aims for 5% accuracy, appropriate for ~100ms intervals; `VeryCoarseTimer` rounds to whole seconds and is reserved for second-scale health-check timers (e.g. the session_monitor that already uses it per `translator-energy-optimization` memory).
  - `active=False`: `_redraw_timer.setInterval(35)` and `_redraw_timer.setTimerType(Qt.TimerType.PreciseTimer)`.
- Call from `set_session_mode(is_session_active)` (184).
- Initial `_redraw_timer` setup in `__init__` (51–54) stays unchanged (default = inactive = 35ms precise).
- **Filter combo handler** (87): replace the bare `self.category_filter_combo.currentTextChanged.connect(self.redraw_ui)` with a small handler `_on_filter_changed(_text)` that explicitly resets `self._pending_changed_ids = None` and `self._pending_ui_state = None` (forces fresh fetch from `task_manager.get_ui_state_list()`) before calling `redraw_ui()`. Without this, a partial `task_state_changed` arriving just before the user changes the filter would leave a stale `changed_ids` set, and `_selective_update` would touch only those rows in a list that the filter has just re-shaped.

Phase 1 changes no APIs and no event payloads. Worst case for diff gates: equality check costs a few comparisons per row but skips a `QBrush` allocation, a `setText`, a `setForeground` and a `setToolTip` per unchanged row.

### Phase 2 — TaskManager dirty-tracking

**`gemini_translator/core/task_manager.py`**

New state:
- `self._dirty_task_ids: set[str] = set()`
- `self._structural_dirty: bool = True` (initial = True so the first cache build is always full)
- `self._dirty_state_lock = threading.Lock()` — narrow, non-reentrant, dedicated to dirty-set+flag snapshot/reset. **Separate from the existing `_chancellor_lock` (PatientLock/RLock)** to avoid deadlock with DB-path acquires.
- `self._sort_keys: dict[str, tuple[int, int]] = {}` — per-task `(priority, sequence)`. **Status group is derived from the cache entry's `status` field at sort time** via a `STATUS_GROUP_ORDER` constant (mirroring the SQL `CASE status WHEN 'in_progress' THEN 1 WHEN 'pending' THEN 2 WHEN 'held' THEN 3 WHEN 'completed' THEN 4 WHEN 'failed' THEN 5 ELSE 6 END`). The Python sort key for merge is `(STATUS_GROUP_ORDER.get(status, 6), -priority, sequence)` — three components, exactly matching the SQL `ORDER BY` at line 1253. Storing only `(priority, sequence)` is correct because the cache entry already carries `status`.

New public methods (thread-safe, callable from worker threads):
- `notify_task_dirty(task_id: str)`: under lock, `self._dirty_task_ids.add(task_id)`; release lock; **emit `self._ui_update_requested`** (existing `pyqtSignal` at line 92, queued to main thread).
- `notify_structural_change()`: under lock, `self._structural_dirty = True`; release lock; **emit `self._ui_update_requested`**.

These methods **never call `_update_timer.start()` directly** — `QTimer` is owned by the main (Qt GUI) thread, and starting it from a worker thread is undefined behaviour. The existing queued-signal indirection (line 130) is what keeps `_safe_request_ui_update` safe today; the new APIs follow the same pattern.

`_notify_ui_of_change(self)` (1205) stays as the `@pyqtSlot()` connected to `_ui_update_requested`, runs in main thread, calls `self._update_timer.start()`. No semantic change.

Backward-compatibility:
- `_safe_request_ui_update()` (1212) becomes a thin alias for `notify_structural_change()` — same emit behaviour as today, but it now also flips `_structural_dirty=True` so the next worker run does a full fetch. All ~25 unmigrated callsites land here. **Worst case after phase 2 with zero migration = current behaviour (always full fetch).**

`_trigger_cache_update` (1219), runs in main thread:
- Under `_dirty_state_lock`, snapshot `(ids_copy, structural_flag)` and reset both. Release lock. Pass the snapshot tuple to the worker as a constructor arg. **Keep a reference on `self` (e.g. `self._in_flight_snapshot`) so that `_on_cache_updated` can recover it on worker failure** (see below). Worker NEVER touches `self._dirty_task_ids` directly.

`_get_ui_state_list_background(snapshot)` (1243), runs in worker thread:
- If `snapshot.structural` or cache empty: existing full-SQL path. SELECT now includes `priority, sequence`. While running it, rebuild a *local* `sort_keys_full` dict; the main thread will assign it to `self._sort_keys` after worker completes (workers don't mutate main-thread-owned state). Result replaces cache entirely.
- Else (partial path):
  - SQL: `SELECT task_id, payload, status, priority, sequence FROM tasks WHERE task_id IN (?, ?, ...)`. Error counts query gets the same IN-list filter.
  - Build a dict `task_id → new_entry` for those rows. Build a `sort_keys_delta` dict for those ids.
  - Merge: copy `self._ui_state_list_cache`, replace entries by task_id, re-sort using `STATUS_GROUP_ORDER` + per-task `(priority, sequence)`.
  - Defensive: if the partial SELECT returned fewer rows than requested ids, **do NOT mutate state from the worker thread**. Instead return a result dict with `needs_structural_retry=True` and no merged cache; main thread handles the escalation.
- Threshold: if `len(snapshot.ids) > 0.5 * max(len(cache), 1)` at entry, switch to full path. Constant `PARTIAL_REFRESH_THRESHOLD = 0.5`.
- All worker exceptions are caught (existing pattern at 1274) and returned as `{'error': repr(e)}` rather than `None`.

`_on_cache_updated(worker)` (1233), runs in main thread:
- Read `worker.result`. Three cases:
  - **Success (full)**: replace `_ui_state_list_cache` with new list; assign `sort_keys_full` to `self._sort_keys`; `changed_ids = None`.
  - **Success (partial)**: assign the merged list to `_ui_state_list_cache`; merge `sort_keys_delta` into `self._sort_keys`; `changed_ids = {tid for tid in snapshot.ids if new_entry_for(tid) != prev_entry_for(tid)}` (entry comparison uses the full 3-tuple).
  - **`needs_structural_retry=True` or error/None result**: do NOT replace cache. Under `_dirty_state_lock`, set `_structural_dirty = True` AND re-add the snapshot's ids back to `_dirty_task_ids` (snapshot is recoverable via `self._in_flight_snapshot`). Skip emit. Restart `_update_timer`. The next pass will fetch a fresh full list. This covers worker crashes, missing-row escalation, and DB transient errors.
- On success, emit `task_state_changed` with `data={'full_state': self._ui_state_list_cache, 'changed_ids': changed_ids}`.
- Clear `self._in_flight_snapshot` and `_is_updating_cache`.
- **Re-entry fix:** before final return, check `_dirty_task_ids` / `_structural_dirty` under lock. If non-empty, `self._update_timer.start()` so the next batch is not dropped. (Also fixes the existing silent-drop bug at line 1221.)

**`gemini_translator/ui/widgets/task_management_widget.py`**

- `_on_task_state_changed` (225): pull `changed_ids` from `event_data['data']`, stash on `self._pending_changed_ids` (new attr, default `None`). `_do_redraw` reads and clears it, passes through to `update_list`.

**`gemini_translator/ui/widgets/chapter_list_widget.py`**

- `update_list(tasks_data, changed_ids=None)`: signature gains `changed_ids`. Existing structural-match branch (608) forwards it to `_selective_update`. The `_surgical_update` / `_full_redraw` branches ignore it (those paths inherently rewrite everything).
- `_selective_update(tasks_data, changed_ids=None)`: when `changed_ids is not None`, iterate `enumerate(tasks_data)` and call `_update_row_status` only for rows whose `task_id` is in the set. Row order is guaranteed equal on this branch (the caller checked `current_task_ids == new_task_ids`).

### Callsite audit (part of phase 2 implementation)

Today there are ~25 callers of `_safe_request_ui_update` in `task_manager.py` plus 1 in `chunk_assembler.py`. All currently go through `_notify_ui_of_change`. The audit produces a table with three columns: file:line, change category, mapped API. Categories:

- **structural** (add / remove / reorder / batch import / queue load / clear): leave on `_notify_ui_of_change` alias.
- **single-task transition** (status change for one known task, error counter bump, completion): migrate to `notify_task_dirty(task_id)`.
- **unclear / shared paths**: leave on `_notify_ui_of_change`. The energy budget is "as good as today plus phase-1 gains" in this bucket, which is acceptable.

The audit table is committed in the same PR as the phase-2 code changes; uncertain entries default to structural.

## Edge cases

1. **Worker busy when notify arrives** — dirty-set accumulates under lock; `_on_cache_updated` re-checks and restarts the timer. Side effect: fixes the silent-drop bug where notifications arriving during `_is_updating_cache=True` were lost.
2. **Partial SELECT returns fewer rows than requested ids** — the missing ids represent a delete that did not go through `notify_structural_change`. Worker returns `needs_structural_retry=True`; main thread re-adds ids to dirty set and sets `_structural_dirty=True`. No state mutated from worker thread.
3. **Worker exception or returns None** — handled identically to case 2: snapshot ids restored to dirty set, structural flag set, timer restarted. No silent data loss.
4. **Wrong-bucket migration** (structural change misrouted to `notify_task_dirty`) — cache diverges from DB. Mitigation: audit table + conservative default + integration test that exercises add/delete via the real API and verifies cache equals a fresh full read.
5. **Threshold flip** — when `len(dirty_set) > 0.5 * len(cache)`, partial path becomes more expensive than full; switch to full. Single constant, easy to tune.
6. **Filter combobox change while partial event pending** — `_on_filter_changed` handler explicitly clears `_pending_changed_ids` and `_pending_ui_state` before calling `redraw_ui()`, forcing a fresh full fetch (see Phase 1 filter handler change).
7. **Other `task_state_changed` subscribers** — `setup.py` snapshot autosave reads `is_session_active` only; `changed_ids` is invisible to it. Forward-compatible.
8. **Notify from worker thread while main thread is in `_trigger_cache_update`** — `notify_task_dirty` acquires `_dirty_state_lock`, blocks briefly until main thread releases its snapshot-and-reset critical section, then adds the id and emits the queued signal. No deadlock (lock never held across blocking calls; signal emit doesn't acquire lock).
9. **Re-entrance during structural+partial overlap** — if a notify_structural arrives while a partial worker is in flight, the structural flag is set on the next snapshot; partial result still merges into cache correctly; the next pass does full and overrides. No corruption.

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|-----------|
| Callsite misclassified as partial | medium | conservative default (all unmigrated callsites route through `_safe_request_ui_update` → structural) + integration test that exercises add/delete and verifies cache parity with a fresh full read |
| Cross-thread `_update_timer.start()` from worker | medium (if API misused) | `notify_*` methods only emit `_ui_update_requested`; `_update_timer.start()` only ever in `_notify_ui_of_change` slot (main thread) |
| `_dirty_state_lock` deadlock with `_chancellor_lock` | low | separate `threading.Lock`; scope is snapshot + reset only; never held across worker call, signal emit, or `_chancellor_lock` acquire |
| Dirty ids lost on worker failure | low | `_on_cache_updated` restores `self._in_flight_snapshot` ids to `_dirty_task_ids` and sets `_structural_dirty=True` on any non-success result |
| Stale `_pending_changed_ids` after filter change | low | dedicated `_on_filter_changed` handler clears it before `redraw_ui()` |
| Partial SQL slower than full for tiny caches | low | `PARTIAL_REFRESH_THRESHOLD = 0.5` short-circuits |
| Test environment quirks with QTimer / threading.Lock | medium | reuse patterns from `tests/test_session_timer_coalescing.py`, per `translator-test-env-deps` memory |

## Testing

New test files, style matches `tests/test_rpm_limiter.py` / `tests/test_worker_energy_wait.py` / `tests/test_session_timer_coalescing.py`:

**Phase 1 — `tests/test_chapter_list_widget_diff.py`:**
- `test_update_row_status_noop_when_unchanged` — spies on `setText`/`setForeground`/`setToolTip`, zero calls when inputs identical.
- `test_update_row_status_applies_when_color_changes` — diff does not over-skip.
- `test_update_row_status_compares_both_item_foregrounds` — `item_task` colour drift triggers a real update even if `status_item` colour matches.
- `test_populate_row_skips_tooltip_when_same`.

**Phase 1 — `tests/test_task_management_widget_redraw_tuning.py`:**
- `test_set_session_mode_active_uses_150ms_coarse_timer` — asserts both interval AND `Qt.TimerType.CoarseTimer` (explicitly NOT `VeryCoarseTimer`).
- `test_set_session_mode_inactive_restores_35ms_precise`.
- `test_on_filter_changed_clears_pending_changed_ids` — combo change forces full path even if a partial event arrived just before.

**Phase 2 — `tests/test_task_manager_dirty_tracking.py`:**
- `test_first_run_is_full_fetch` — initial `_structural_dirty=True` forces full.
- `test_notify_task_dirty_runs_partial_query` — single dirty id → SELECT uses IN clause with that id only.
- `test_notify_structural_overrides_dirty_set` — both flags set → full path wins.
- `test_partial_merge_resorts_cache_to_match_sql_order` — merge + Python sort (3-component key incl. status group) matches full-fetch order.
- `test_changed_ids_excludes_unchanged_entries` — dirty id whose row data did not change is absent from emitted `changed_ids`.
- `test_dirty_during_worker_triggers_followup` — re-entry retry covers the silent-drop bug.
- `test_missing_id_in_partial_returns_needs_structural_retry` — worker returns the flag, does not mutate state (edge case 2).
- `test_main_thread_handles_structural_retry` — on `needs_structural_retry`, main thread restores snapshot ids to dirty set and sets `_structural_dirty=True`.
- `test_worker_failure_restores_snapshot_and_sets_structural` — worker returns error / None: snapshot ids re-added, structural flag set, timer restarted, no emit (edge case 3).
- `test_threshold_falls_back_to_full_when_dirty_set_too_large` — edge case 5.
- `test_notify_task_dirty_from_worker_thread_does_not_start_timer_directly` — call `notify_task_dirty` from a `threading.Thread`, assert `_update_timer` is untouched until the queued slot fires in main thread (uses `QSignalSpy` / `processEvents`).

**Phase 2 — extend `tests/test_chapter_list_widget_diff.py`:**
- `test_selective_update_with_changed_ids_skips_other_rows`.

**Manual verification:** macOS Activity Monitor on `main.py` during a real active session with realistic file (RPS near limit). Three measurements: baseline (current `main`), phase 1, phase 2. If phase 1 already lands the win, phase 2 is reconsidered before merge.

## Out of scope

- EventBus topic migration for on-demand dialogs (`translator-energy-optimization` memory marks this as deliberately deferred).
- Snapshot autosave changes (current 15s debounce is adequate).
- Log widget changes (250ms batch + hidden skip already in place).
- Any visible-behaviour change (interval bumps stay inside human-perceptual debounce limits during active sessions only).
