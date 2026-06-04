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
  - `status_item.toolTip()` vs the new tooltip string
  - If all three match: `return` without constructing `QBrush` or calling setters.
- `_populate_row` (706): the text-diff at line 744 stays. Add equivalent diff gates around `item_task.setToolTip(tooltip_text)` (729) and `item_task.setData(UserRole, task_tuple_for_ui_role)` (730) — both should compare against the current value before assigning.

**`gemini_translator/ui/widgets/task_management_widget.py`**

- New private helper `_apply_active_session_redraw_tuning(active: bool)`:
  - `active=True`: `_redraw_timer.setInterval(150)` and `_redraw_timer.setTimerType(Qt.TimerType.VeryCoarseTimer)`.
  - `active=False`: `_redraw_timer.setInterval(35)` and `_redraw_timer.setTimerType(Qt.TimerType.PreciseTimer)`.
- Call from `set_session_mode(is_session_active)` (184).
- Initial `_redraw_timer` setup in `__init__` (51–54) stays unchanged (default = inactive = 35ms precise).

Phase 1 changes no APIs and no event payloads. Worst case for diff gates: equality check costs a few comparisons per row but skips a `QBrush` allocation, a `setText`, a `setForeground` and a `setToolTip` per unchanged row.

### Phase 2 — TaskManager dirty-tracking

**`gemini_translator/core/task_manager.py`**

New state:
- `self._dirty_task_ids: set[str] = set()`
- `self._structural_dirty: bool = True` (initial = True so the first cache build is always full)
- `self._dirty_state_lock = threading.Lock()` — narrow, non-reentrant, dedicated to dirty-set+flag snapshot/reset. **Separate from the existing `_chancellor_lock` (PatientLock/RLock)** to avoid deadlock with DB-path acquires.
- `self._sort_keys: dict[str, tuple[int, int]] = {}` — per-task `(priority, sequence)` so the merge step can resort without re-querying the entire table

New public methods:
- `notify_task_dirty(task_id: str)`: under lock, `self._dirty_task_ids.add(task_id)`; then `self._update_timer.start()`.
- `notify_structural_change()`: under lock, `self._structural_dirty = True`; then `self._update_timer.start()`.

Backward-compatibility:
- `_notify_ui_of_change(self)` (1205) becomes a thin alias for `notify_structural_change()`. Every existing call site goes through this path unchanged. **Worst case after phase 2 with zero migration = current behaviour.**

`_trigger_cache_update` (1219):
- Under lock, snapshot `(ids_copy, structural_flag)` and reset both. Release lock. Pass snapshot to the worker (do NOT touch `self._dirty_task_ids` from the worker thread).

`_get_ui_state_list_background(snapshot)` (1243):
- If `snapshot.structural` or cache empty: existing full-SQL path. While running it, also rebuild `self._sort_keys` from the same SQL (add `priority, sequence` to the SELECT). Result replaces cache entirely.
- Else (partial path):
  - SQL: `SELECT task_id, payload, status, priority, sequence FROM tasks WHERE task_id IN (?, ?, ...)`. Error counts query gets the same IN-list filter.
  - Build a dict `task_id → new_entry` for those rows. Update `self._sort_keys` for those ids.
  - Merge: copy `self._ui_state_list_cache`, replace entries by task_id, re-sort with the same Python key as the SQL ORDER BY (status-group order, then `-priority`, then `sequence`).
- Threshold: if `len(snapshot.ids) > 0.5 * max(len(cache), 1)` at entry, switch to full path. Constant `PARTIAL_REFRESH_THRESHOLD = 0.5`.
- Defensive: if the partial SELECT returns fewer rows than requested ids, escalate by setting `_structural_dirty = True` and re-running full on the next pass. Emit `changed_ids=None` for this batch.

`_on_cache_updated` (1233):
- Compute `changed_ids: set | None`:
  - structural path → `None`
  - partial path → `{tid for tid in snapshot.ids if cache_entry_for(tid) != prev_entry_for(tid)}` (entry comparison uses the full 3-tuple)
- Emit `task_state_changed` with `data={'full_state': self._ui_state_list_cache, 'changed_ids': changed_ids}`.
- **Re-entry fix:** before clearing `_is_updating_cache`, peek `_dirty_task_ids` / `_structural_dirty` under lock. If non-empty, `self._update_timer.start()` so the next batch is not dropped.

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
2. **Partial SELECT returns fewer rows than requested ids** — the missing ids represent a delete that did not go through `notify_structural_change`. Defensive escalation: set `_structural_dirty=True`, emit `changed_ids=None` for this batch.
3. **Wrong-bucket migration** (structural change misrouted to `notify_task_dirty`) — cache diverges from DB. Mitigation: audit table + conservative default + integration test that exercises add/delete via the real API and verifies cache equals a fresh full read.
4. **Threshold flip** — when `len(dirty_set) > 0.5 * len(cache)`, partial path becomes more expensive than full; switch to full. Single constant, easy to tune.
5. **Filter combobox change** — `category_filter_combo.currentTextChanged` triggers `redraw_ui()` with `_pending_changed_ids=None`, so the full path runs. Correct by construction.
6. **Other `task_state_changed` subscribers** — `setup.py` snapshot autosave reads `is_session_active` only; `changed_ids` is invisible to it. Forward-compatible.
7. **Re-entrance during structural+partial overlap** — if a notify_structural arrives while a partial worker is in flight, the structural flag is set on the next snapshot; partial result still merges into cache correctly; the next pass does full and overrides. No corruption.

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|-----------|
| Callsite misclassified as partial | medium | conservative default + integration test |
| `_dirty_state_lock` deadlock with `_chancellor_lock` | low | separate lock; scope is snapshot + reset only; never held across worker call or `_chancellor_lock` acquire |
| Partial SQL slower than full for tiny caches | low | `PARTIAL_REFRESH_THRESHOLD = 0.5` short-circuits |
| Test environment quirks with QTimer/QMutex | medium | reuse patterns from `tests/test_session_timer_coalescing.py`, per `translator-test-env-deps` memory |

## Testing

New test files, style matches `tests/test_rpm_limiter.py` / `tests/test_worker_energy_wait.py` / `tests/test_session_timer_coalescing.py`:

**Phase 1 — `tests/test_chapter_list_widget_diff.py`:**
- `test_update_row_status_noop_when_unchanged` — spies on `setText`/`setForeground`/`setToolTip`, zero calls when inputs identical.
- `test_update_row_status_applies_when_color_changes` — diff does not over-skip.
- `test_populate_row_skips_tooltip_when_same`.

**Phase 1 — `tests/test_task_management_widget_redraw_tuning.py`:**
- `test_set_session_mode_active_uses_150ms_coarse_timer`.
- `test_set_session_mode_inactive_restores_35ms_precise`.

**Phase 2 — `tests/test_task_manager_dirty_tracking.py`:**
- `test_first_run_is_full_fetch` — initial `_structural_dirty=True` forces full.
- `test_notify_task_dirty_runs_partial_query` — single dirty id → SELECT uses IN clause with that id only.
- `test_notify_structural_overrides_dirty_set` — both flags set → full path wins.
- `test_partial_merge_resorts_cache_to_match_sql_order` — merge + Python sort matches full-fetch order.
- `test_changed_ids_excludes_unchanged_entries` — dirty id whose row data did not change is absent from emitted `changed_ids`.
- `test_dirty_during_worker_triggers_followup` — re-entry retry covers the silent-drop bug.
- `test_missing_id_in_partial_falls_back_to_structural` — defensive (edge case 2).
- `test_threshold_falls_back_to_full_when_dirty_set_too_large` — edge case 4.

**Phase 2 — extend `tests/test_chapter_list_widget_diff.py`:**
- `test_selective_update_with_changed_ids_skips_other_rows`.

**Manual verification:** macOS Activity Monitor on `main.py` during a real active session with realistic file (RPS near limit). Three measurements: baseline (current `main`), phase 1, phase 2. If phase 1 already lands the win, phase 2 is reconsidered before merge.

## Out of scope

- EventBus topic migration for on-demand dialogs (`translator-energy-optimization` memory marks this as deliberately deferred).
- Snapshot autosave changes (current 15s debounce is adequate).
- Log widget changes (250ms batch + hidden skip already in place).
- Any visible-behaviour change (interval bumps stay inside human-perceptual debounce limits during active sessions only).
