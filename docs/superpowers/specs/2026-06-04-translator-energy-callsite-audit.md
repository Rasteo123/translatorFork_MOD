# Callsite audit — `_safe_request_ui_update` → dirty-tracking API

Date: 2026-06-04

Result of Task 17: classification of every `_safe_request_ui_update()` call site
and the migration decision per site.

After Task 8, `_safe_request_ui_update()` is a backward-compat alias that routes
to `notify_structural_change()`. This forces a full SQL refresh for every event.
To realise the partial-fetch savings from Tasks 10–12, single-known-task
transitions are migrated to `notify_task_dirty(task_id)`. Structural and
ambiguous callers stay on `_safe_request_ui_update` — worst case is today's
behaviour (full fetch), so the migration is opt-in and conservative.

Line numbers below are against `task_manager.py` / `chunk_assembler.py` at the
post-migration state of this task (branch `feat/energy-active-session-dirty-tracking`).

## Summary

- Total callsites inspected: 31 (30 in `task_manager.py` + 1 in `chunk_assembler.py`)
  - The `task_manager.py` total counts the `get_next_task` method twice because
    it has two distinct callsites on different branches (one migrated, one kept).
- Migrated to `notify_task_dirty(task_id)`: 9
- Kept on `_safe_request_ui_update` (structural / unclear): 22
  - 21 in `task_manager.py` + 1 in `chunk_assembler.py`.
  - One of the 21 is the `get_next_task` no-eligible-task fallback branch
    (line 566), which has no task id in scope and is therefore left on the alias.

## Table

| file:line | method / context | category | action |
|-----------|------------------|----------|--------|
| task_manager.py:369 | `_handle_session_finished_background()` — bulk UPDATE on every `in_progress` task back to `pending` after a session finishes | structural | kept on `_safe_request_ui_update` |
| task_manager.py:400 | `rescue_task_by_worker_id(worker_id)` — bulk UPDATE on all `in_progress` tasks owned by the worker, with new sequence (ordering change) | structural | kept on `_safe_request_ui_update` |
| task_manager.py:513 | `add_priority_tasks(tasks, parent_history)` — bulk INSERT of new task rows | structural | kept on `_safe_request_ui_update` |
| task_manager.py:538 | `promote_held_task(task_id, new_payload)` — single task transition `held` → `pending` via `update_task`; `task_id` is the param in scope | single-task | migrated to `self.notify_task_dirty(task_id)` |
| task_manager.py:553 | `add_pending_tasks(tasks)` — bulk INSERT of new task rows | structural | kept on `_safe_request_ui_update` |
| task_manager.py:564 | `get_next_task(worker_id)` — single task transition `pending` → `in_progress`; `task_for_work` is `(uuid, payload)` in scope | single-task | migrated to `self.notify_task_dirty(task_for_work[0])` |
| task_manager.py:566 | `get_next_task(worker_id)` — no-eligible-task branch (`update_task` returned `None`); nothing changed and no task id in scope | unclear | kept on `_safe_request_ui_update` (fallback preserves original behaviour) |
| task_manager.py:734 | `replace_batch_with_results(...)` — replaces a batch task with multiple completed-chapter rows (inserts + delete/update) | structural | kept on `_safe_request_ui_update` |
| task_manager.py:758 | `replace_chunks_with_chapter(chunk_task_ids, …)` — deletes N chunk task rows, inserts one new chapter task row | structural | kept on `_safe_request_ui_update` |
| task_manager.py:636 | `task_done(worker_id, task_info, success_payload)` — single task transition to `completed` via `update_task(task_info[0], …)` | single-task | migrated to `self.notify_task_dirty(task_info[0])` |
| task_manager.py:688 | `task_done_with_content(worker_id, task_info, …)` — single task transition to `completed` via `update_task(task_info[0], …)` | single-task | migrated to `self.notify_task_dirty(task_info[0])` |
| task_manager.py:772 | `task_failed_permanently(worker_id, task_info)` — single task transition to `failed` via `update_task(task_info[0], …)` | single-task | migrated to `self.notify_task_dirty(task_info[0])` |
| task_manager.py:795 | `task_requeued(worker_id, task_info)` — single task transition to `pending` with a new sequence (front-of-queue). Ordering changes but only for one task | single-task | migrated to `self.notify_task_dirty(task_info[0])` |
| task_manager.py:887 | `task_requeued_for_retry(worker_id, task_info)` — single task transition to `pending` (priority 1) for retry | single-task | migrated to `self.notify_task_dirty(task_info[0])` |
| task_manager.py:975 | `split_in_progress_batch_into_chapters(task_info, …)` — deletes/updates one batch task, inserts many chapter tasks | structural | kept on `_safe_request_ui_update` |
| task_manager.py:999 | `remove_tasks(task_ids: list)` — bulk DELETE of task rows | structural | kept on `_safe_request_ui_update` |
| task_manager.py:1197 | `split_batches_into_chapters(task_ids)` — bulk DELETE of batch rows and bulk INSERT of resulting chapter rows | structural | kept on `_safe_request_ui_update` |
| task_manager.py:1203 | `clear_all_queues()` — `DELETE FROM tasks` | structural | kept on `_safe_request_ui_update` |
| task_manager.py:1510 | `record_failure(task_info, error_type)` — inserts a row in `task_errors` for a single task; `task_info[0]` is the task id | single-task | migrated to `self.notify_task_dirty(task_info[0])` |
| task_manager.py:1546 | `release_held_tasks()` — bulk UPDATE of every `held` task to `pending` | structural | kept on `_safe_request_ui_update` |
| task_manager.py:1559 | `hold_all_pending_tasks()` — bulk UPDATE of every `pending` task to `held` | structural | kept on `_safe_request_ui_update` |
| task_manager.py:1594 | `reanimate_tasks(task_ids)` — bulk reanimation; multiple rows change status and sequence | structural | kept on `_safe_request_ui_update` |
| task_manager.py:1656 | `reorder_tasks(action, task_ids)` — explicit bulk reordering, sequence column rewrite | structural | kept on `_safe_request_ui_update` |
| task_manager.py:1708 | `reorder_batch_chapters(task_id, chapters)` — single task: rewrites the payload (chapter order) of one `epub_batch` row; `task_id` in scope | single-task | migrated to `self.notify_task_dirty(task_id)` |
| task_manager.py:1760 | `duplicate_tasks(task_ids)` — bulk INSERT of duplicates plus sequence shift on existing rows | structural | kept on `_safe_request_ui_update` |
| task_manager.py:1777 | `update_many(task_ids, new_status, new_priority)` — bulk UPDATE across N tasks (status / priority change) | structural | kept on `_safe_request_ui_update` |
| task_manager.py:1845 | `hold_all_except_first()` — bulk UPDATE of every `pending` task except the first to `held` | structural | kept on `_safe_request_ui_update` |
| task_manager.py:1925 | `set_pending_tasks(tasks_payloads, initial_history)` — `DELETE FROM tasks` then bulk INSERT; full queue replacement | structural | kept on `_safe_request_ui_update` |
| task_manager.py:1994 | `set_pending_task_chains(task_chains, initial_history)` — `DELETE FROM tasks` then bulk INSERT; full queue replacement | structural | kept on `_safe_request_ui_update` |
| task_manager.py:2359 | `load_queue_snapshot(snapshot_path, current_epub_path)` — full snapshot restore from disk into memory; all rows replaced | structural | kept on `_safe_request_ui_update` |
| chunk_assembler.py:299 | `_assemble_chapter_from_db` → `_requeue_chunks_missing_results` — bulk UPDATE of N missing-result chunk tasks back to `pending` with new priority | structural | kept on `_safe_request_ui_update` |

## Notes

- **`task_requeued` (line 795)** is borderline: the task's `sequence` is reset to
  `MIN(sequence) - 1` to push the task to the front of the queue. Strictly that
  is an ordering change, but the change is confined to a *single* task id, so
  the partial-fetch path can still merge the single row and re-sort by
  `(STATUS_GROUP_ORDER, -priority, sequence)` correctly (the partial path in
  `_fetch_partial_ui_state` re-sorts the entire cache using `_sort_keys`).
  Classified as **single-task** for that reason.

- **`task_requeued_for_retry` (line 887)** also changes `priority=1`; same
  reasoning as `task_requeued` — single-row change, partial path re-sorts using
  the updated sort key.

- **`reorder_batch_chapters` (line 1708)** rewrites the `payload` (chapter order
  inside one `epub_batch`) of a single task. The status / priority / sequence
  columns do not change, so the only thing the UI must re-fetch is that one
  row's display name (computed from payload). Classified as **single-task**.

- **`record_failure` (line 1510)** is the canonical example from the plan: it
  inserts a row in `task_errors` for one task. The UI shows an error counter
  per task derived from `task_errors`, so only that one task's UI entry
  changes. Classified as **single-task**.

- **`get_next_task` (line 564 / 566)** transitions one task `pending` →
  `in_progress` on the success branch (migrated, line 564). Its status group
  changes (group 2 → group 1) so its row reorders, but that is a single-row
  change the partial path handles via the re-sort using `_sort_keys`. The
  `else` branch (line 566) fires when no task was eligible — nothing changed and
  there is no task id in scope, so it stays on `_safe_request_ui_update`
  (a harmless full refresh, matching the original behaviour).

- **`replace_batch_with_results` (line 734)** and
  **`split_in_progress_batch_into_chapters` (line 975)** could in principle be
  migrated by passing the *set* of affected task ids (the deleted/updated batch
  + the new completed-chapter rows). That would exceed the 1-line-per-site
  discipline of this task (the task ids of the freshly-inserted rows are not all
  in scope at the callsite), so both are left as structural.

- **`chunk_assembler.py:299`**: the surrounding `_requeue_chunks_missing_results`
  helper updates N tasks (a subset of `task_ids`) with `status='pending'`,
  `priority=1`, `worker_id=NULL`. Because N may be > 1 and the ordering
  re-sorts, this is structural in the strict sense. The callsite only has a
  boolean `recovered` flag in scope, not the affected ids, so migration would
  require more than a 1-line change. Left as is.
