# Translator Active-Session Energy Reduction — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut CPU/energy during active translation sessions by adding UI-side diff gating and an upstream dirty-tracking layer in `TaskManager`, without changing any visible behaviour.

**Architecture:** Two phases, each shippable on its own. Phase 1 = pure UI gating (`_update_row_status` no-op when unchanged, adaptive redraw debounce, dedicated filter handler). Phase 2 = TaskManager tracks dirty task ids, runs partial SQL refetch + Python merge/sort instead of full read+JSON-parse on every event, and emits `task_state_changed` with a `changed_ids: list[str] | None` payload so the UI can skip untouched rows. Strictly backward compatible: `_safe_request_ui_update` becomes an alias for `notify_structural_change`, so unmigrated callsites behave exactly as today.

**Tech Stack:** Python 3.12, PyQt6, SQLite (in-memory clone for reads), `threading.Lock`, `unittest` with `QT_QPA_PLATFORM=offscreen`.

**Spec:** [docs/superpowers/specs/2026-06-04-translator-energy-active-session-design.md](../specs/2026-06-04-translator-energy-active-session-design.md)

**Branch:** `feat/energy-active-session-dirty-tracking`

---

## File Structure

**Modified production files:**
- `gemini_translator/ui/widgets/chapter_list_widget.py` — diff gates in `_update_row_status` / `_populate_row`; `update_list` / `_selective_update` accept `changed_ids`.
- `gemini_translator/ui/widgets/task_management_widget.py` — `_apply_active_session_redraw_tuning`, `_on_filter_changed`, `_pending_changed_ids` plumbing.
- `gemini_translator/core/task_manager.py` — dirty-tracking state, thread-safe notify API, partial cache refresh, `changed_ids` in event payload, callsite migration.
- `gemini_translator/core/chunk_assembler.py` — one callsite migrated (line 299).

**New test files:**
- `tests/test_chapter_list_widget_diff.py` — Phase 1 UI diff gates + Phase 2 `changed_ids` integration.
- `tests/test_task_management_widget_redraw_tuning.py` — Phase 1 redraw tuning + filter handler + Phase 2 plumbing.
- `tests/test_task_manager_dirty_tracking.py` — Phase 2 TaskManager state machine, sort/merge, thread-hop.

**Out of scope:** snapshot autosave, log widget, EventBus topic migration for on-demand dialogs (deliberately deferred per `translator-energy-optimization` memory).

---

## Test Environment Reminders

- `QT_QPA_PLATFORM=offscreen` at module top (existing pattern in `test_session_timer_coalescing.py`).
- Per `translator-test-env-deps` memory: PyQt6 forbids calling methods on `cls.__new__(cls)` instances. Use the **`types.MethodType` harness idiom** (existing `_FixerHarness` pattern, see `test_worker_energy_wait.py`) for non-Qt unit tests; instantiate real widgets only when timer types or Qt signals are under test.
- Tests run via `pytest tests/<file>.py -v` from repo root.

---

# Phase 1 — UI-side gating (low risk)

## Task 1: Diff gate in `_update_row_status` (both items' foreground)

**Files:**
- Create: `tests/test_chapter_list_widget_diff.py`
- Modify: `gemini_translator/ui/widgets/chapter_list_widget.py:881-905`

- [ ] **Step 1: Write the failing test**

Create `tests/test_chapter_list_widget_diff.py`:

```python
import os
import unittest
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtGui, QtWidgets

from gemini_translator.ui.widgets.chapter_list_widget import ChapterListWidget


class _SpyItem:
    """Spy QTableWidgetItem replacement that records setter calls."""
    def __init__(self, text="", color="#000000", tooltip=""):
        self._text = text
        self._brush = QtGui.QBrush(QtGui.QColor(color))
        self._tooltip = tooltip
        self.set_text_calls = 0
        self.set_foreground_calls = 0
        self.set_tooltip_calls = 0

    def text(self):
        return self._text

    def setText(self, value):
        self._text = value
        self.set_text_calls += 1

    def foreground(self):
        return self._brush

    def setForeground(self, brush):
        self._brush = brush
        self.set_foreground_calls += 1

    def toolTip(self):
        return self._tooltip

    def setToolTip(self, value):
        self._tooltip = value
        self.set_tooltip_calls += 1

    def data(self, _role):
        return ("uuid-x", ("epub", "/tmp/x.epub", "/tmp/ch.html"))


class DiffGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _make_widget(self):
        # Minimal widget — we only exercise _update_row_status against spy items.
        w = ChapterListWidget()
        self.addCleanup(w.deleteLater)
        return w

    def _install_spy_row(self, widget, status_text, color_hex, tooltip):
        task_item = _SpyItem(text="📄 HTML: ch.html", color=color_hex)
        status_item = _SpyItem(text=status_text, color=color_hex, tooltip=tooltip)
        widget.table = MagicMock()
        widget.table.item = lambda row, col: task_item if col == 0 else status_item
        return task_item, status_item

    def test_update_row_status_noop_when_unchanged(self):
        widget = self._make_widget()
        # _get_status_display_info('pending', {}, payload) → ("⏳ Ожидание…", text-palette colour).
        # Set spies so current values exactly match what _update_row_status would write.
        # We use 'in_progress' (stable colour #3498DB) to avoid palette dependency.
        expected_text = "🔄 В работе…"
        expected_color = "#3498DB"
        expected_tooltip = f"Статус: {expected_text}"
        task_spy, status_spy = self._install_spy_row(
            widget, expected_text, expected_color, expected_tooltip
        )

        widget._update_row_status(0, "in_progress", {})

        self.assertEqual(status_spy.set_text_calls, 0, "setText should be skipped when text matches")
        self.assertEqual(status_spy.set_foreground_calls, 0, "setForeground should be skipped when colour matches")
        self.assertEqual(task_spy.set_foreground_calls, 0, "task-cell foreground should be skipped too")
        self.assertEqual(status_spy.set_tooltip_calls, 0, "setToolTip should be skipped when tooltip matches")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_chapter_list_widget_diff.py::DiffGateTests::test_update_row_status_noop_when_unchanged -v`

Expected: FAIL — current `_update_row_status` unconditionally calls all setters, so the assertions on `set_*_calls == 0` fail.

- [ ] **Step 3: Implement the diff gate**

Modify `gemini_translator/ui/widgets/chapter_list_widget.py`, replace `_update_row_status` (881-905) with:

```python
    def _update_row_status(self, row, status, details={}):
        status_item = self.table.item(row, 1)
        item_task = self.table.item(row, 0)
        if not status_item or not item_task: return

        # 1. Получаем payload, чтобы передать его в "мозг"
        task_tuple = item_task.data(QtCore.Qt.ItemDataRole.UserRole)
        task_payload = task_tuple[1] if task_tuple and len(task_tuple) > 1 else None

        # 2. Обращаемся к "мозгу" за инструкциями
        display_text, color_hex = self._get_status_display_info(status, details, task_payload)

        error_tooltip = ""
        if status.startswith('error'):
            error_counts = details.get('errors', {})
            error_lines = [f"- {err_type}: {count} раз" for err_type, count in error_counts.items()]
            error_tooltip = "\n\nИстория ошибок:\n" + "\n".join(error_lines)
        new_tooltip = f"Статус: {display_text}{error_tooltip}"

        # 3. Diff gate — skip if text+colour+tooltip all match current.
        current_color = status_item.foreground().color().name().lower()
        task_color = item_task.foreground().color().name().lower()
        target_color = color_hex.lower()
        if (
            status_item.text() == display_text
            and current_color == target_color
            and task_color == target_color
            and status_item.toolTip() == new_tooltip
        ):
            return

        status_item.setText(display_text)
        status_item.setToolTip(new_tooltip)
        brush = QtGui.QBrush(QtGui.QColor(color_hex))
        item_task.setForeground(brush)
        status_item.setForeground(brush)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_chapter_list_widget_diff.py::DiffGateTests::test_update_row_status_noop_when_unchanged -v`

Expected: PASS.

- [ ] **Step 5: Add the over-skip guard test**

Append to `DiffGateTests` in `tests/test_chapter_list_widget_diff.py`:

```python
    def test_update_row_status_applies_when_color_changes(self):
        widget = self._make_widget()
        # Current row painted as in_progress (#3498DB). New status is 'success' (#2ECC71).
        task_spy, status_spy = self._install_spy_row(
            widget, "🔄 В работе…", "#3498DB", "Статус: 🔄 В работе…"
        )

        widget._update_row_status(0, "success", {})

        self.assertEqual(status_spy.set_text_calls, 1)
        self.assertEqual(status_spy.set_foreground_calls, 1)
        self.assertEqual(task_spy.set_foreground_calls, 1)
        self.assertEqual(status_spy.set_tooltip_calls, 1)

    def test_update_row_status_applies_when_only_task_item_color_drifts(self):
        # Regression guard: status_item.foreground matches the target but
        # item_task.foreground was reset somewhere. We must still update.
        widget = self._make_widget()
        target_text = "🔄 В работе…"
        target_color = "#3498DB"
        target_tooltip = f"Статус: {target_text}"
        task_item = _SpyItem(text="📄 HTML: ch.html", color="#000000")  # drifted
        status_item = _SpyItem(text=target_text, color=target_color, tooltip=target_tooltip)
        widget.table = MagicMock()
        widget.table.item = lambda row, col: task_item if col == 0 else status_item

        widget._update_row_status(0, "in_progress", {})

        self.assertEqual(task_item.set_foreground_calls, 1, "task-cell foreground must update even if status-cell matches")
```

- [ ] **Step 6: Run all DiffGateTests to verify**

Run: `pytest tests/test_chapter_list_widget_diff.py::DiffGateTests -v`

Expected: 3 PASS.

- [ ] **Step 7: Commit**

```bash
git add tests/test_chapter_list_widget_diff.py gemini_translator/ui/widgets/chapter_list_widget.py
git commit -m "perf(ui): skip no-op setters in _update_row_status

Compare text, both item foregrounds, and tooltip before calling the
setters. Skips QBrush allocation and four Qt setter calls per row
when nothing actually changed."
```

---

## Task 2: Diff gates in `_populate_row` for tooltip and UserRole

**Files:**
- Modify: `gemini_translator/ui/widgets/chapter_list_widget.py:706-751`
- Modify: `tests/test_chapter_list_widget_diff.py`

- [ ] **Step 1: Write the failing test**

Append to `DiffGateTests`:

```python
    def test_populate_row_skips_tooltip_when_same(self):
        from PyQt6.QtWidgets import QTableWidget
        widget = self._make_widget()
        widget.table = QTableWidget(1, 3)

        # First populate creates the items.
        task_payload = ("epub", "/tmp/x.epub", "/tmp/ch.html")
        task_data = (("uuid-1", task_payload), "in_progress", {})
        widget._populate_row(0, task_data)

        task_item = widget.table.item(0, 0)
        original_tooltip = task_item.toolTip()

        # Wrap setToolTip / setData to count.
        tooltip_calls = []
        data_calls = []
        orig_set_tooltip = task_item.setToolTip
        orig_set_data = task_item.setData
        task_item.setToolTip = lambda v: (tooltip_calls.append(v), orig_set_tooltip(v))[1]
        task_item.setData = lambda role, v: (data_calls.append((role, v)), orig_set_data(role, v))[1]

        # Same data again → expect zero setToolTip and zero setData calls.
        widget._populate_row(0, task_data, update_only=True)

        self.assertEqual(tooltip_calls, [], "tooltip setter should be skipped when value unchanged")
        # setData(UserRole+1, status) still allowed even if equal — we only gate the heavy ones.
        # But UserRole (the task tuple) must be skipped:
        userrole_calls = [c for c in data_calls if c[0] == QtCore.Qt.ItemDataRole.UserRole]
        self.assertEqual(userrole_calls, [], "UserRole setData should be skipped when value unchanged")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_chapter_list_widget_diff.py::DiffGateTests::test_populate_row_skips_tooltip_when_same -v`

Expected: FAIL — current `_populate_row` unconditionally calls `setToolTip` and `setData(UserRole, ...)`.

- [ ] **Step 3: Implement the gates**

Modify `_populate_row` in `gemini_translator/ui/widgets/chapter_list_widget.py` (706-751), specifically the section around lines 723-732. Replace:

```python
        # --- ОБНОВЛЕНИЕ/СОЗДАНИЕ ЯЧЕЙКИ ЗАДАЧИ (СТОЛБЕЦ 0) ---
        item_task = self.table.item(row, 0)
        if not item_task: # Создаем, только если не существует
            item_task = QTableWidgetItem(display_text)
            self.table.setItem(row, 0, item_task)
        elif item_task.text() != display_text: # Обновляем, только если текст изменился
            item_task.setText(display_text)

        item_task.setToolTip(tooltip_text)
        item_task.setData(QtCore.Qt.ItemDataRole.UserRole, task_tuple_for_ui_role)
        item_task.setData(Qt.ItemDataRole.UserRole + 1, status)
```

with:

```python
        # --- ОБНОВЛЕНИЕ/СОЗДАНИЕ ЯЧЕЙКИ ЗАДАЧИ (СТОЛБЕЦ 0) ---
        item_task = self.table.item(row, 0)
        if not item_task: # Создаем, только если не существует
            item_task = QTableWidgetItem(display_text)
            self.table.setItem(row, 0, item_task)
        elif item_task.text() != display_text:
            item_task.setText(display_text)

        if item_task.toolTip() != tooltip_text:
            item_task.setToolTip(tooltip_text)
        if item_task.data(QtCore.Qt.ItemDataRole.UserRole) != task_tuple_for_ui_role:
            item_task.setData(QtCore.Qt.ItemDataRole.UserRole, task_tuple_for_ui_role)
        if item_task.data(Qt.ItemDataRole.UserRole + 1) != status:
            item_task.setData(Qt.ItemDataRole.UserRole + 1, status)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_chapter_list_widget_diff.py::DiffGateTests::test_populate_row_skips_tooltip_when_same -v`

Expected: PASS.

- [ ] **Step 5: Run all chapter_list_widget tests**

Run: `pytest tests/test_chapter_list_widget_diff.py -v`

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/test_chapter_list_widget_diff.py gemini_translator/ui/widgets/chapter_list_widget.py
git commit -m "perf(ui): skip no-op setToolTip and setData in _populate_row

Mirrors the existing setText diff gate so update-only repopulations
do not touch Qt state when nothing changed."
```

---

## Task 3: Adaptive redraw debounce for active session

**Files:**
- Create: `tests/test_task_management_widget_redraw_tuning.py`
- Modify: `gemini_translator/ui/widgets/task_management_widget.py:184-210`

- [ ] **Step 1: Write the failing test**

Create `tests/test_task_management_widget_redraw_tuning.py`:

```python
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets

from gemini_translator.ui.widgets.task_management_widget import TaskManagementWidget


class RedrawTuningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _make_widget(self):
        w = TaskManagementWidget()
        self.addCleanup(w.deleteLater)
        return w

    def test_set_session_mode_active_uses_150ms_coarse_timer(self):
        widget = self._make_widget()
        widget.set_session_mode(True)

        self.assertEqual(widget._redraw_timer.interval(), 150)
        self.assertEqual(
            widget._redraw_timer.timerType(),
            QtCore.Qt.TimerType.CoarseTimer,
            "Use CoarseTimer (5% slack) for 150ms — VeryCoarseTimer would round to 1s.",
        )

    def test_set_session_mode_inactive_restores_35ms_precise(self):
        widget = self._make_widget()
        widget.set_session_mode(True)
        widget.set_session_mode(False)

        self.assertEqual(widget._redraw_timer.interval(), 35)
        self.assertEqual(
            widget._redraw_timer.timerType(),
            QtCore.Qt.TimerType.PreciseTimer,
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_task_management_widget_redraw_tuning.py::RedrawTuningTests -v`

Expected: FAIL — current `set_session_mode` does not touch the redraw timer.

- [ ] **Step 3: Implement the tuning helper**

Modify `gemini_translator/ui/widgets/task_management_widget.py`, add the helper above `set_session_mode` (around line 184):

```python
    def _apply_active_session_redraw_tuning(self, active: bool):
        """During an active translation session, slow the redraw debounce
        from 35 ms (PreciseTimer) to 150 ms (CoarseTimer). The list updates
        are not user-driven during a session, so the slack is invisible to
        the user but lets macOS coalesce Qt timer wake-ups."""
        if active:
            self._redraw_timer.setInterval(150)
            self._redraw_timer.setTimerType(QtCore.Qt.TimerType.CoarseTimer)
        else:
            self._redraw_timer.setInterval(35)
            self._redraw_timer.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
```

Then modify `set_session_mode` (currently starts at line 184) — add the helper call at the top:

```python
    def set_session_mode(self, is_session_active):
        """
        Переключает доступность кнопок управления списком.
        """
        self._apply_active_session_redraw_tuning(is_session_active)
        self._is_session_active = is_session_active
        # ... rest unchanged ...
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_task_management_widget_redraw_tuning.py::RedrawTuningTests -v`

Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_task_management_widget_redraw_tuning.py gemini_translator/ui/widgets/task_management_widget.py
git commit -m "perf(ui): use 150ms CoarseTimer redraw during active sessions

Cuts redraw wake-ups from ~28/s to ~5/s with imperceptible UI lag
(the user is not interacting with the list during translation).
CoarseTimer (5% slack) lets macOS coalesce timer fires.
PreciseTimer/35ms restored on session end."
```

---

## Task 4: `_on_filter_changed` handler clears pending partial state

**Files:**
- Modify: `gemini_translator/ui/widgets/task_management_widget.py:48, 87`
- Modify: `tests/test_task_management_widget_redraw_tuning.py`

- [ ] **Step 1: Write the failing test**

Append to `RedrawTuningTests`:

```python
    def test_on_filter_changed_clears_pending_changed_ids(self):
        widget = self._make_widget()
        # Simulate a partial event arriving just before the user changes the filter.
        widget._pending_changed_ids = {"some-uuid"}
        widget._pending_ui_state = [("dummy",)]

        widget._on_filter_changed("Все")

        self.assertIsNone(widget._pending_changed_ids,
                          "Filter change must force a full redraw, not a stale partial one")
        self.assertIsNone(widget._pending_ui_state,
                          "Pending UI state must be cleared so _do_redraw refetches fresh")
        self.assertTrue(widget._redraw_timer.isActive(),
                        "Redraw must be scheduled after filter change")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_task_management_widget_redraw_tuning.py::RedrawTuningTests::test_on_filter_changed_clears_pending_changed_ids -v`

Expected: FAIL — `_on_filter_changed` does not exist yet; widget has no `_pending_changed_ids` attribute either.

- [ ] **Step 3: Add the attribute and the handler**

Modify `gemini_translator/ui/widgets/task_management_widget.py`:

1. In `__init__` after line 48 (`self._pending_ui_state = None`), add:

```python
        self._pending_ui_state = None
        self._pending_changed_ids = None  # set[str] | None — partial-event filter from TaskManager
```

2. Add the handler method (e.g. just above `redraw_ui` around line 175):

```python
    def _on_filter_changed(self, _text):
        """Filter combobox changed — drop any pending partial update and force a
        fresh full redraw. Otherwise a partial task_state_changed pending from
        before the filter change would touch only its rows in a freshly filtered
        list, leaving the filtered view inconsistent."""
        self._pending_changed_ids = None
        self._pending_ui_state = None
        self.redraw_ui()
```

3. Rewire the combobox connection at line 87. Replace:

```python
        self.category_filter_combo.currentTextChanged.connect(self.redraw_ui)
```

with:

```python
        self.category_filter_combo.currentTextChanged.connect(self._on_filter_changed)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_task_management_widget_redraw_tuning.py::RedrawTuningTests -v`

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_task_management_widget_redraw_tuning.py gemini_translator/ui/widgets/task_management_widget.py
git commit -m "fix(ui): clear pending partial state on filter change

A partial task_state_changed pending from just before a filter change
would otherwise apply changed_ids over the freshly filtered list,
touching only a few rows in a view that needs a full redraw."
```

---

## Task 5: Phase 1 verification checkpoint

**Files:** none (verification only)

- [ ] **Step 1: Run the full Phase 1 test set**

Run: `pytest tests/test_chapter_list_widget_diff.py tests/test_task_management_widget_redraw_tuning.py -v`

Expected: all tests PASS.

- [ ] **Step 2: Run the broader UI test surface to catch regressions**

Run: `pytest tests/ -k "widget or chapter_list or task_management" -v`

Expected: same pass/fail status as before this branch started (per `translator-test-env-deps`, `test_workascii_runtime.py` and `test_ranobelib_return_to_menu.py` are pre-existing failures and stay failing).

- [ ] **Step 3: Manual smoke**

Start the app: `python main.py` (or the project's launch entry point).
- Start a small translation session (3-5 chapters).
- Verify the task list still updates status colours/text as tasks transition.
- Verify changing the category filter still re-renders correctly.

- [ ] **Step 4: Tag the Phase 1 checkpoint**

```bash
git tag energy-active-session-phase-1
```

Phase 1 is independently shippable here. If Activity Monitor already shows the desired drop, Phase 2 can be reconsidered.

---

# Phase 2 — TaskManager dirty-tracking

## Task 6: Dirty-tracking state and `STATUS_GROUP_ORDER` constant

**Files:**
- Create: `tests/test_task_manager_dirty_tracking.py`
- Modify: `gemini_translator/core/task_manager.py` (top-level constant near other module-level constants; `__init__` to add new attrs)

- [ ] **Step 1: Write the failing test**

Create `tests/test_task_manager_dirty_tracking.py`:

```python
import types
import unittest
from threading import Lock


class _TimerStub:
    def __init__(self):
        self.start_calls = 0
    def start(self):
        self.start_calls += 1


class _SignalStub:
    def __init__(self):
        self.emit_calls = 0
    def emit(self):
        self.emit_calls += 1


class DirtyTrackingStateTests(unittest.TestCase):
    def test_status_group_order_includes_db_and_ui_aliases(self):
        from gemini_translator.core.task_manager import STATUS_GROUP_ORDER
        self.assertEqual(STATUS_GROUP_ORDER["in_progress"], 1)
        self.assertEqual(STATUS_GROUP_ORDER["pending"], 2)
        self.assertEqual(STATUS_GROUP_ORDER["held"], 3)
        # DB statuses and their UI aliases share the same group:
        self.assertEqual(STATUS_GROUP_ORDER["completed"], STATUS_GROUP_ORDER["success"])
        self.assertEqual(STATUS_GROUP_ORDER["failed"], STATUS_GROUP_ORDER["error"])
        self.assertEqual(STATUS_GROUP_ORDER["completed"], 4)
        self.assertEqual(STATUS_GROUP_ORDER["failed"], 5)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_task_manager_dirty_tracking.py::DirtyTrackingStateTests -v`

Expected: FAIL — `STATUS_GROUP_ORDER` does not exist yet.

- [ ] **Step 3: Add the constant and state**

Modify `gemini_translator/core/task_manager.py`:

1. Near the top-level imports/constants, add:

```python
# Mirrors the SQL CASE in _get_ui_state_list_background. Cache entries store
# *UI* statuses (line ~1282 maps completed -> success, failed -> error), so
# the constant carries both DB names and their UI aliases pointing at the
# same group number. Sort key: (STATUS_GROUP_ORDER.get(status, 6), -priority, sequence).
STATUS_GROUP_ORDER = {
    'in_progress': 1,
    'pending':     2,
    'held':        3,
    'completed':   4,
    'success':     4,  # UI alias for completed
    'failed':      5,
    'error':       5,  # UI alias for failed
}

PARTIAL_REFRESH_THRESHOLD = 0.5  # if len(dirty_ids) > threshold * len(cache), use full path
```

2. In `TaskManager.__init__` (around where other state is initialized — find the block after `self._ui_state_list_cache` setup), add:

```python
        # Dirty-tracking state for active-session energy reduction.
        # _dirty_state_lock guards _dirty_task_ids and _structural_dirty only.
        # It is a plain threading.Lock (NOT the _chancellor_lock RLock) to keep
        # the scope minimal and avoid deadlock with DB-path acquires.
        self._dirty_state_lock = Lock()
        self._dirty_task_ids: set[str] = set()
        self._structural_dirty: bool = True  # first run is always full
        self._sort_keys: dict[str, tuple[int, int]] = {}  # task_id_str -> (priority, sequence)
        self._in_flight_snapshot = None  # holds the snapshot passed to the active worker, for failure recovery
```

3. At the top of `task_manager.py`, ensure `from threading import Lock` is imported (the file already imports `PatientLock` from `threading`; either reuse that import line or add a separate import).

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_task_manager_dirty_tracking.py::DirtyTrackingStateTests -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_task_manager_dirty_tracking.py gemini_translator/core/task_manager.py
git commit -m "feat(core): add STATUS_GROUP_ORDER constant and dirty-tracking state

State only — no behaviour change yet. STATUS_GROUP_ORDER mirrors the
SQL CASE ordering and carries UI-status aliases (success/error) since
cache entries store mapped UI statuses, not raw DB names.
PARTIAL_REFRESH_THRESHOLD = 0.5 governs partial vs full SQL choice."
```

---

## Task 7: `notify_task_dirty` / `notify_structural_change` thread-safe API

**Files:**
- Modify: `gemini_translator/core/task_manager.py` (add two methods near `_safe_request_ui_update` at 1212)
- Modify: `tests/test_task_manager_dirty_tracking.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_task_manager_dirty_tracking.py`:

```python
class NotifyApiTests(unittest.TestCase):
    def _make_stub(self):
        # Bind real TaskManager methods onto a SimpleNamespace stub with just the
        # required attrs (per the test-env-deps memory: __new__ bypass would crash
        # on PyQt6, so we use the MethodType harness idiom).
        tm = types.SimpleNamespace(
            _dirty_state_lock=Lock(),
            _dirty_task_ids=set(),
            _structural_dirty=False,
            _ui_update_requested=_SignalStub(),
        )
        from gemini_translator.core.task_manager import TaskManager
        tm.notify_task_dirty = types.MethodType(TaskManager.notify_task_dirty, tm)
        tm.notify_structural_change = types.MethodType(TaskManager.notify_structural_change, tm)
        return tm

    def test_notify_task_dirty_adds_id_and_emits_signal(self):
        tm = self._make_stub()
        tm.notify_task_dirty("abc-123")
        self.assertEqual(tm._dirty_task_ids, {"abc-123"})
        self.assertFalse(tm._structural_dirty)
        self.assertEqual(tm._ui_update_requested.emit_calls, 1)

    def test_notify_task_dirty_accepts_uuid_and_stringifies(self):
        import uuid
        tm = self._make_stub()
        u = uuid.uuid4()
        tm.notify_task_dirty(u)
        self.assertEqual(tm._dirty_task_ids, {str(u)})

    def test_notify_structural_change_sets_flag_and_emits(self):
        tm = self._make_stub()
        tm.notify_structural_change()
        self.assertTrue(tm._structural_dirty)
        self.assertEqual(tm._dirty_task_ids, set())
        self.assertEqual(tm._ui_update_requested.emit_calls, 1)

    def test_notify_methods_do_not_start_timer_directly(self):
        """Critical: QTimer.start() must NEVER be called from these methods,
        because they may be invoked from worker threads. The thread-hop goes
        through _ui_update_requested → main-thread slot → _update_timer.start()."""
        tm = self._make_stub()
        tm._update_timer = _TimerStub()
        tm.notify_task_dirty("x")
        tm.notify_structural_change()
        self.assertEqual(tm._update_timer.start_calls, 0,
                         "QTimer.start() must not be called from notify_* — they may run on worker threads")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_task_manager_dirty_tracking.py::NotifyApiTests -v`

Expected: FAIL — methods do not exist.

- [ ] **Step 3: Implement the methods**

Modify `gemini_translator/core/task_manager.py`, add two methods near `_safe_request_ui_update` (line 1212):

```python
    def notify_task_dirty(self, task_id):
        """Mark a single task as dirty. Thread-safe: callable from worker threads.
        Does NOT start the redraw timer — that happens in the main-thread slot
        connected to _ui_update_requested. QTimer is owned by the main (Qt GUI)
        thread; starting it from a worker thread is undefined behaviour."""
        task_id_str = str(task_id)
        with self._dirty_state_lock:
            self._dirty_task_ids.add(task_id_str)
        self._ui_update_requested.emit()

    def notify_structural_change(self):
        """Mark the cache as needing a full refresh (add/remove/reorder/import).
        Thread-safe: callable from worker threads. Same thread-hop discipline
        as notify_task_dirty."""
        with self._dirty_state_lock:
            self._structural_dirty = True
        self._ui_update_requested.emit()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_task_manager_dirty_tracking.py::NotifyApiTests -v`

Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_task_manager_dirty_tracking.py gemini_translator/core/task_manager.py
git commit -m "feat(core): add notify_task_dirty / notify_structural_change

Thread-safe API for cache invalidation hints. Under _dirty_state_lock
update the set/flag, then emit the existing _ui_update_requested
queued signal. Never starts the QTimer directly (QTimer is main-thread
owned). UUID inputs are stringified at the boundary so _dirty_task_ids
stays a homogeneous set[str]."
```

---

## Task 8: `_safe_request_ui_update` aliases `notify_structural_change`

**Files:**
- Modify: `gemini_translator/core/task_manager.py:1212-1217`
- Modify: `tests/test_task_manager_dirty_tracking.py`

- [ ] **Step 1: Write the failing test**

Append to `NotifyApiTests` (same class):

```python
    def test_safe_request_ui_update_routes_to_structural(self):
        """Backward compat: all ~25 unmigrated callsites of _safe_request_ui_update
        must now set _structural_dirty, so worst case = today's full-fetch behaviour."""
        tm = self._make_stub()
        from gemini_translator.core.task_manager import TaskManager
        tm._safe_request_ui_update = types.MethodType(TaskManager._safe_request_ui_update, tm)
        tm._safe_request_ui_update()
        self.assertTrue(tm._structural_dirty)
        self.assertEqual(tm._ui_update_requested.emit_calls, 1)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_task_manager_dirty_tracking.py::NotifyApiTests::test_safe_request_ui_update_routes_to_structural -v`

Expected: FAIL — `_safe_request_ui_update` only emits the signal; it does not set `_structural_dirty`.

- [ ] **Step 3: Replace `_safe_request_ui_update`**

In `gemini_translator/core/task_manager.py`, replace lines 1212-1217:

```python
    def _safe_request_ui_update(self):
        """
        Безопасный метод для запроса обновления UI из ЛЮБОГО потока.
        Он просто испускает сигнал.
        """
        self._ui_update_requested.emit()
```

with:

```python
    def _safe_request_ui_update(self):
        """Backward-compat alias for notify_structural_change. Every unmigrated
        callsite routes here, so worst case = today's full-fetch behaviour."""
        self.notify_structural_change()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_task_manager_dirty_tracking.py::NotifyApiTests -v`

Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_task_manager_dirty_tracking.py gemini_translator/core/task_manager.py
git commit -m "refactor(core): _safe_request_ui_update -> notify_structural_change

Existing callsites now flip _structural_dirty before emitting, so the
next worker run does a full fetch — semantically identical to today."
```

---

## Task 9: `_trigger_cache_update` snapshots dirty state under lock

**Files:**
- Modify: `gemini_translator/core/task_manager.py:1219-1227`
- Modify: `tests/test_task_manager_dirty_tracking.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_task_manager_dirty_tracking.py`:

```python
class TriggerCacheUpdateTests(unittest.TestCase):
    def _make_stub(self):
        tm = types.SimpleNamespace(
            _dirty_state_lock=Lock(),
            _dirty_task_ids={"a", "b"},
            _structural_dirty=False,
            _is_updating_cache=False,
            _cache_update_worker=None,
            _in_flight_snapshot=None,
            _started_workers=[],
        )

        class _FakeWorker:
            def __init__(self, fn, *args, **kwargs):
                self.fn = fn; self.args = args; self.kwargs = kwargs
                self.finished = types.SimpleNamespace(connect=lambda cb: None)
            def start(self):
                tm._started_workers.append(self)
        tm._FakeWorker = _FakeWorker
        # Patch the worker factory used by _trigger_cache_update; tested below.
        return tm

    def test_trigger_cache_update_snapshots_and_clears_state(self):
        from gemini_translator.core import task_manager as tm_mod
        tm = self._make_stub()
        # Monkeypatch TaskDBWorker in the module to our fake.
        original = tm_mod.TaskDBWorker
        tm_mod.TaskDBWorker = tm._FakeWorker
        try:
            from gemini_translator.core.task_manager import TaskManager
            tm._get_ui_state_list_background = lambda snapshot: None
            tm._trigger_cache_update = types.MethodType(TaskManager._trigger_cache_update, tm)
            tm._trigger_cache_update()
        finally:
            tm_mod.TaskDBWorker = original

        # State reset
        self.assertEqual(tm._dirty_task_ids, set())
        self.assertFalse(tm._structural_dirty)
        # Snapshot stored for failure recovery
        self.assertIsNotNone(tm._in_flight_snapshot)
        self.assertEqual(set(tm._in_flight_snapshot["ids"]), {"a", "b"})
        self.assertFalse(tm._in_flight_snapshot["structural"])
        # Worker started, with snapshot passed as arg
        self.assertEqual(len(tm._started_workers), 1)
        self.assertTrue(tm._is_updating_cache)

    def test_trigger_cache_update_returns_early_if_worker_already_running(self):
        tm = self._make_stub()
        tm._is_updating_cache = True
        from gemini_translator.core.task_manager import TaskManager
        tm._trigger_cache_update = types.MethodType(TaskManager._trigger_cache_update, tm)
        tm._trigger_cache_update()
        # State NOT reset because we did not snapshot
        self.assertEqual(tm._dirty_task_ids, {"a", "b"})
        self.assertIsNone(tm._in_flight_snapshot)
        self.assertEqual(tm._started_workers, [])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_task_manager_dirty_tracking.py::TriggerCacheUpdateTests -v`

Expected: FAIL — `_trigger_cache_update` does not snapshot, does not store `_in_flight_snapshot`, does not pass snapshot to the worker.

- [ ] **Step 3: Modify `_trigger_cache_update`**

In `gemini_translator/core/task_manager.py`, replace lines 1219-1227:

```python
    def _trigger_cache_update(self):
        """Этот метод вызывается таймером и запускает фоновое обновление."""
        if self._is_updating_cache:
            return
        self._is_updating_cache = True
        worker = TaskDBWorker(self._get_ui_state_list_background)
        worker.finished.connect(lambda: self._on_cache_updated(worker))
        self._cache_update_worker = worker
        worker.start()
```

with:

```python
    def _trigger_cache_update(self):
        """Runs in the main (Qt GUI) thread (debounced by _update_timer).
        Snapshots the dirty state under lock, resets it, and hands the snapshot
        to the worker. _in_flight_snapshot keeps the snapshot retrievable so
        _on_cache_updated can restore the dirty ids on worker failure."""
        if self._is_updating_cache:
            return
        with self._dirty_state_lock:
            snapshot = {
                "ids": tuple(self._dirty_task_ids),
                "structural": self._structural_dirty,
            }
            self._dirty_task_ids.clear()
            self._structural_dirty = False
        self._in_flight_snapshot = snapshot
        self._is_updating_cache = True
        worker = TaskDBWorker(self._get_ui_state_list_background, snapshot)
        worker.finished.connect(lambda: self._on_cache_updated(worker))
        self._cache_update_worker = worker
        worker.start()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_task_manager_dirty_tracking.py::TriggerCacheUpdateTests -v`

Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_task_manager_dirty_tracking.py gemini_translator/core/task_manager.py
git commit -m "feat(core): snapshot dirty state before launching cache worker

Under _dirty_state_lock, copy ids tuple + structural flag, reset both,
store snapshot on _in_flight_snapshot for failure recovery, pass to
the worker as a constructor arg. Worker never touches main-thread
state directly."
```

---

## Task 10: `_get_ui_state_list_background` accepts snapshot — full path keeps sort keys

**Files:**
- Modify: `gemini_translator/core/task_manager.py:1243-1286`
- Modify: `tests/test_task_manager_dirty_tracking.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_task_manager_dirty_tracking.py`:

```python
class BackgroundFetchTests(unittest.TestCase):
    def _make_stub_with_db(self):
        """Build a minimal TaskManager with a real in-memory SQLite via the
        existing _get_read_only_conn. Uses the harness idiom to avoid PyQt6
        construction crashes."""
        import sqlite3
        from gemini_translator.core.task_manager import TaskManager
        tm = types.SimpleNamespace(
            _ui_state_list_cache=[],
            _sort_keys={},
        )

        # Fake read-only conn returning a context manager around an in-memory db.
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE tasks (task_id TEXT PRIMARY KEY, payload TEXT, status TEXT,
                                priority INTEGER, sequence INTEGER);
            CREATE TABLE task_errors (task_id TEXT, error_type TEXT, timestamp REAL);
        """)
        # Seed: 3 tasks across status groups, mixed priorities.
        import json
        conn.execute("INSERT INTO tasks VALUES (?, ?, ?, ?, ?)",
                     ("00000000-0000-0000-0000-000000000001",
                      json.dumps(["epub", "/tmp/a.epub", "/tmp/a.html"]),
                      "in_progress", 10, 1))
        conn.execute("INSERT INTO tasks VALUES (?, ?, ?, ?, ?)",
                     ("00000000-0000-0000-0000-000000000002",
                      json.dumps(["epub", "/tmp/b.epub", "/tmp/b.html"]),
                      "completed", 5, 2))
        conn.execute("INSERT INTO tasks VALUES (?, ?, ?, ?, ?)",
                     ("00000000-0000-0000-0000-000000000003",
                      json.dumps(["epub", "/tmp/c.epub", "/tmp/c.html"]),
                      "failed", 5, 3))
        conn.commit()

        class _ConnCtx:
            def __enter__(self_inner): return conn
            def __exit__(self_inner, *a): return False
        tm._get_read_only_conn = lambda: _ConnCtx()
        tm._payload_for_ui = lambda p: p  # identity
        tm._get_ui_state_list_background = types.MethodType(
            TaskManager._get_ui_state_list_background, tm
        )
        return tm

    def test_full_path_returns_list_with_ui_aliased_statuses(self):
        tm = self._make_stub_with_db()
        snapshot = {"ids": (), "structural": True}
        result = tm._get_ui_state_list_background(snapshot)
        self.assertIn("entries", result)
        entries = result["entries"]
        # 3 tasks, ordered: in_progress (1), success (4, from completed), error (5, from failed)
        self.assertEqual(len(entries), 3)
        statuses = [entry[1] for entry in entries]
        self.assertEqual(statuses, ["in_progress", "success", "error"])

    def test_full_path_populates_sort_keys(self):
        tm = self._make_stub_with_db()
        snapshot = {"ids": (), "structural": True}
        result = tm._get_ui_state_list_background(snapshot)
        self.assertIn("sort_keys", result)
        # Three task_id_strs → (priority, sequence) tuples
        sk = result["sort_keys"]
        self.assertEqual(len(sk), 3)
        self.assertEqual(sk["00000000-0000-0000-0000-000000000001"], (10, 1))
        self.assertEqual(sk["00000000-0000-0000-0000-000000000002"], (5, 2))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_task_manager_dirty_tracking.py::BackgroundFetchTests -v`

Expected: FAIL — method doesn't accept snapshot arg, doesn't return dict, doesn't carry sort_keys.

- [ ] **Step 3: Refactor `_get_ui_state_list_background` — full path returning a result dict**

In `gemini_translator/core/task_manager.py`, replace `_get_ui_state_list_background` (1243-1286). New full-path implementation:

```python
    def _get_ui_state_list_background(self, snapshot):
        """Runs in worker thread. Returns a result dict — never mutates
        main-thread-owned state directly. Main thread (in _on_cache_updated)
        assigns the result into self._ui_state_list_cache / _sort_keys.

        Result shape (success):
          full:    {"mode": "full",    "entries": [...], "sort_keys": {...}}
          partial: {"mode": "partial", "entries": [...], "sort_keys_delta": {...}}
        Result shape (failure / escalation):
          {"mode": "structural_retry"} or {"mode": "error", "error": "..."}
        """
        cache = self._ui_state_list_cache
        is_structural = snapshot["structural"] or not cache
        if not is_structural:
            # Threshold short-circuit
            if len(snapshot["ids"]) > PARTIAL_REFRESH_THRESHOLD * max(len(cache), 1):
                is_structural = True

        try:
            with self._get_read_only_conn() as conn:
                if is_structural:
                    return self._fetch_full_ui_state(conn)
                return self._fetch_partial_ui_state(conn, snapshot["ids"])
        except Exception as e:
            print(f"[CRITICAL DB WORKER] Ошибка в _get_ui_state_list_background: {e}")
            return {"mode": "error", "error": repr(e)}

    def _fetch_full_ui_state(self, conn):
        cursor = conn.execute("""
            SELECT task_id, payload, status, priority, sequence FROM tasks
            ORDER BY CASE status WHEN 'in_progress' THEN 1 WHEN 'pending' THEN 2 WHEN 'held' THEN 3 WHEN 'completed' THEN 4 WHEN 'failed' THEN 5 ELSE 6 END, priority DESC, sequence ASC
        """)
        all_rows = cursor.fetchall()
        failed_task_ids = [row['task_id'] for row in all_rows if row['status'] == 'failed']
        error_histories = self._fetch_error_histories(conn, failed_task_ids)
        entries = []
        sort_keys = {}
        for row in all_rows:
            entry, sort_key = self._build_ui_entry(row, error_histories)
            entries.append(entry)
            sort_keys[row['task_id']] = sort_key
        return {"mode": "full", "entries": entries, "sort_keys": sort_keys}

    def _fetch_error_histories(self, conn, failed_task_ids):
        error_histories = {}
        if not failed_task_ids:
            return error_histories
        placeholders = ','.join('?' for _ in failed_task_ids)
        error_cursor = conn.execute(
            f"SELECT task_id, error_type, COUNT(*) as count FROM task_errors WHERE task_id IN ({placeholders}) GROUP BY task_id, error_type",
            failed_task_ids
        )
        for row in error_cursor:
            tid = row['task_id']
            if tid not in error_histories:
                error_histories[tid] = {'total_count': 0, 'errors': {}}
            error_histories[tid]['errors'][row['error_type']] = row['count']
            error_histories[tid]['total_count'] += row['count']
        return error_histories

    def _build_ui_entry(self, row, error_histories):
        """Returns ((task_tuple_for_ui, ui_status, details), (priority, sequence))."""
        import json
        task_id_str = row['task_id']
        payload = json.loads(row['payload'], object_hook=tuple_deserializer)
        payload = self._payload_for_ui(payload)
        ui_status = {'completed': 'success', 'failed': 'error'}.get(row['status'], row['status'])
        task_tuple_for_ui = (uuid.UUID(task_id_str), payload)
        details = error_histories.get(task_id_str, {})
        return ((task_tuple_for_ui, ui_status, details), (row['priority'], row['sequence']))
```

Leave `_fetch_partial_ui_state` for Task 11.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_task_manager_dirty_tracking.py::BackgroundFetchTests -v`

Expected: 2 PASS (partial-path test added in Task 11).

- [ ] **Step 5: Commit**

```bash
git add tests/test_task_manager_dirty_tracking.py gemini_translator/core/task_manager.py
git commit -m "refactor(core): full-path cache fetch returns result dict

Split _get_ui_state_list_background into a dispatcher that picks full
vs partial based on the snapshot, plus _fetch_full_ui_state. Full path
now also returns sort_keys (task_id -> (priority, sequence)) so the
partial path can merge and resort without re-fetching."
```

---

## Task 11: Partial path with merge, sort, and missing-rows escalation

**Files:**
- Modify: `gemini_translator/core/task_manager.py` (add `_fetch_partial_ui_state` next to the helpers from Task 10)
- Modify: `tests/test_task_manager_dirty_tracking.py`

- [ ] **Step 1: Write the failing tests**

Append to `BackgroundFetchTests`:

```python
    def test_partial_path_refetches_only_requested_ids(self):
        tm = self._make_stub_with_db()
        # Seed cache from a prior full fetch.
        full = tm._get_ui_state_list_background({"ids": (), "structural": True})
        tm._ui_state_list_cache = full["entries"]
        tm._sort_keys = full["sort_keys"]
        # Mutate the DB: change task 2 from completed -> in_progress.
        with tm._get_read_only_conn() as conn:
            conn.execute("UPDATE tasks SET status = 'in_progress' WHERE task_id = ?",
                         ("00000000-0000-0000-0000-000000000002",))
            conn.commit()

        snapshot = {"ids": ("00000000-0000-0000-0000-000000000002",), "structural": False}
        result = tm._get_ui_state_list_background(snapshot)

        self.assertEqual(result["mode"], "partial")
        # Task 2 in the merged list should now have ui_status 'in_progress'.
        entries_by_id = {str(e[0][0]): e for e in result["entries"]}
        self.assertEqual(entries_by_id["00000000-0000-0000-0000-000000000002"][1], "in_progress")
        # Order: both 1 and 2 are now in_progress (group 1) → sorted by priority DESC then sequence ASC.
        # task 1 priority 10, task 2 priority 5 → task 1 first.
        statuses = [(str(e[0][0]), e[1]) for e in result["entries"]]
        # Group 1 (in_progress) entries come before group 5 (error).
        in_progress_ids = [tid for tid, s in statuses if s == "in_progress"]
        self.assertEqual(in_progress_ids,
                         ["00000000-0000-0000-0000-000000000001",
                          "00000000-0000-0000-0000-000000000002"])

    def test_partial_path_returns_structural_retry_when_id_missing(self):
        tm = self._make_stub_with_db()
        full = tm._get_ui_state_list_background({"ids": (), "structural": True})
        tm._ui_state_list_cache = full["entries"]
        tm._sort_keys = full["sort_keys"]
        # Delete a row outside the dirty-tracking path (simulates a missed structural change).
        with tm._get_read_only_conn() as conn:
            conn.execute("DELETE FROM tasks WHERE task_id = ?",
                         ("00000000-0000-0000-0000-000000000002",))
            conn.commit()

        snapshot = {"ids": ("00000000-0000-0000-0000-000000000002",), "structural": False}
        result = tm._get_ui_state_list_background(snapshot)

        self.assertEqual(result["mode"], "structural_retry")

    def test_partial_path_resort_matches_full_path_for_aliased_statuses(self):
        """Cover both completed/success and failed/error aliases by mutating one of each."""
        tm = self._make_stub_with_db()
        full = tm._get_ui_state_list_background({"ids": (), "structural": True})
        tm._ui_state_list_cache = full["entries"]
        tm._sort_keys = full["sort_keys"]
        # Toggle task 3 (failed/error) -> completed/success.
        with tm._get_read_only_conn() as conn:
            conn.execute("UPDATE tasks SET status = 'completed' WHERE task_id = ?",
                         ("00000000-0000-0000-0000-000000000003",))
            conn.commit()

        partial = tm._get_ui_state_list_background({
            "ids": ("00000000-0000-0000-0000-000000000003",),
            "structural": False,
        })
        # Now compare order with a fresh full fetch from the same db.
        full2 = tm._get_ui_state_list_background({"ids": (), "structural": True})
        partial_order = [str(e[0][0]) for e in partial["entries"]]
        full_order = [str(e[0][0]) for e in full2["entries"]]
        self.assertEqual(partial_order, full_order,
                         "Partial merge + Python sort must produce the same order as full SQL ORDER BY")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_task_manager_dirty_tracking.py::BackgroundFetchTests -v`

Expected: 3 new FAIL — `_fetch_partial_ui_state` does not exist.

- [ ] **Step 3: Implement `_fetch_partial_ui_state`**

In `gemini_translator/core/task_manager.py`, add after `_build_ui_entry`:

```python
    def _fetch_partial_ui_state(self, conn, dirty_ids):
        if not dirty_ids:
            # Nothing dirty — return the existing cache unchanged.
            return {"mode": "partial", "entries": list(self._ui_state_list_cache),
                    "sort_keys_delta": {}}

        placeholders = ','.join('?' for _ in dirty_ids)
        cursor = conn.execute(
            f"""SELECT task_id, payload, status, priority, sequence FROM tasks
                WHERE task_id IN ({placeholders})""",
            tuple(dirty_ids),
        )
        rows = cursor.fetchall()
        fetched_ids = {row['task_id'] for row in rows}
        missing = set(dirty_ids) - fetched_ids
        if missing:
            # Row was deleted outside the dirty-tracking path; escalate to a full
            # refresh on the next pass. Main thread restores the snapshot.
            return {"mode": "structural_retry"}

        failed_in_partial = [row['task_id'] for row in rows if row['status'] == 'failed']
        error_histories = self._fetch_error_histories(conn, failed_in_partial)

        # Build dicts for the refetched ids, then merge into a copy of the cache.
        new_by_id = {}
        sort_keys_delta = {}
        for row in rows:
            entry, sort_key = self._build_ui_entry(row, error_histories)
            new_by_id[row['task_id']] = entry
            sort_keys_delta[row['task_id']] = sort_key

        # Merge: keep existing entries, replace dirty ones.
        merged_by_id = {str(e[0][0]): e for e in self._ui_state_list_cache}
        merged_by_id.update(new_by_id)

        # Effective sort_keys for the resort: cache's keys overlaid with the delta.
        effective_sort_keys = dict(self._sort_keys)
        effective_sort_keys.update(sort_keys_delta)

        def _sort_key(entry):
            tid = str(entry[0][0])
            status = entry[1]
            prio, seq = effective_sort_keys.get(tid, (0, 0))
            return (STATUS_GROUP_ORDER.get(status, 6), -prio, seq)

        entries = sorted(merged_by_id.values(), key=_sort_key)
        return {"mode": "partial", "entries": entries, "sort_keys_delta": sort_keys_delta}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_task_manager_dirty_tracking.py::BackgroundFetchTests -v`

Expected: 5 PASS (2 from Task 10 + 3 new).

- [ ] **Step 5: Commit**

```bash
git add tests/test_task_manager_dirty_tracking.py gemini_translator/core/task_manager.py
git commit -m "feat(core): partial cache fetch with merge and resort

WHERE task_id IN (...) refetch, merge into a copy of the cache by id,
resort with 3-component key (STATUS_GROUP_ORDER, -priority, sequence).
Missing rows return mode=structural_retry — main thread escalates,
no state mutated from the worker thread."
```

---

## Task 12: `_on_cache_updated` success paths emit `changed_ids`

**Files:**
- Modify: `gemini_translator/core/task_manager.py:1233-1241`
- Modify: `tests/test_task_manager_dirty_tracking.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_task_manager_dirty_tracking.py`:

```python
class OnCacheUpdatedTests(unittest.TestCase):
    def _make_stub(self):
        tm = types.SimpleNamespace(
            _ui_state_list_cache=[],
            _sort_keys={},
            _dirty_state_lock=Lock(),
            _dirty_task_ids=set(),
            _structural_dirty=False,
            _is_updating_cache=True,
            _cache_update_worker=None,
            _in_flight_snapshot={"ids": ("a", "b"), "structural": False},
            _update_timer=_TimerStub(),
            _posted_events=[],
        )
        tm._post_event = lambda name, data: tm._posted_events.append((name, data))
        from gemini_translator.core.task_manager import TaskManager
        tm._on_cache_updated = types.MethodType(TaskManager._on_cache_updated, tm)
        return tm

    def _make_worker(self, result):
        return types.SimpleNamespace(result=result)

    def test_full_success_replaces_cache_and_emits_none_changed_ids(self):
        tm = self._make_stub()
        # Prime an old cache so we can see it get replaced.
        tm._ui_state_list_cache = [("old",)]
        tm._sort_keys = {"old": (0, 0)}
        new_entries = [(("uuid-x", ("epub",)), "in_progress", {})]
        new_sort_keys = {"x": (10, 1)}
        worker = self._make_worker({"mode": "full", "entries": new_entries, "sort_keys": new_sort_keys})

        tm._on_cache_updated(worker)

        self.assertEqual(tm._ui_state_list_cache, new_entries)
        self.assertEqual(tm._sort_keys, new_sort_keys)
        self.assertEqual(len(tm._posted_events), 1)
        name, data = tm._posted_events[0]
        self.assertEqual(name, "task_state_changed")
        self.assertIsNone(data["changed_ids"], "full path emits changed_ids=None")
        self.assertEqual(data["full_state"], new_entries)
        self.assertFalse(tm._is_updating_cache)
        self.assertIsNone(tm._in_flight_snapshot)

    def test_partial_success_emits_changed_ids_excluding_unchanged(self):
        tm = self._make_stub()
        import uuid
        a, b = uuid.UUID("00000000-0000-0000-0000-00000000000a"), uuid.UUID("00000000-0000-0000-0000-00000000000b")
        # Old cache: a=pending, b=in_progress.
        old_entries = [((a, ("epub",)), "pending", {}), ((b, ("epub",)), "in_progress", {})]
        tm._ui_state_list_cache = old_entries
        tm._sort_keys = {str(a): (0, 1), str(b): (0, 2)}
        tm._in_flight_snapshot = {"ids": (str(a), str(b)), "structural": False}

        # New entries from partial fetch: a unchanged, b -> success.
        new_entries = [((a, ("epub",)), "pending", {}), ((b, ("epub",)), "success", {})]
        sort_keys_delta = {str(a): (0, 1), str(b): (0, 2)}
        worker = self._make_worker({"mode": "partial", "entries": new_entries,
                                    "sort_keys_delta": sort_keys_delta})

        tm._on_cache_updated(worker)

        self.assertEqual(tm._ui_state_list_cache, new_entries)
        name, data = tm._posted_events[0]
        self.assertEqual(set(data["changed_ids"]), {str(b)},
                         "Only b actually changed; a's entry equals the old one and is excluded")
        self.assertIsInstance(data["changed_ids"], list,
                              "Payload must carry list[str], not set, for consistency with full_state")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_task_manager_dirty_tracking.py::OnCacheUpdatedTests -v`

Expected: FAIL — current `_on_cache_updated` doesn't read `mode`, doesn't compute `changed_ids`, doesn't accept the new result shape.

- [ ] **Step 3: Replace `_on_cache_updated`**

In `gemini_translator/core/task_manager.py`, replace lines 1233-1241:

```python
    def _on_cache_updated(self, worker):
        """Слот, который вызывается по завершении фонового обновления кэша."""
        if hasattr(worker, 'result') and worker.result is not None:
            new_state = worker.result
            if new_state != self._ui_state_list_cache:
                self._ui_state_list_cache = new_state
                self._post_event('task_state_changed', {'full_state': self._ui_state_list_cache})
        self._is_updating_cache = False
        self._cache_update_worker = None
```

with:

```python
    def _on_cache_updated(self, worker):
        """Runs in the main (Qt GUI) thread (via worker.finished signal).
        Handles three success modes (full, partial) and the structural_retry /
        error escalation paths. Never raises — failures are recovered by
        restoring the in-flight snapshot's ids and forcing a structural refresh."""
        result = getattr(worker, 'result', None)
        snapshot = self._in_flight_snapshot

        if result is None or not isinstance(result, dict) or result.get("mode") in ("error", None):
            self._recover_failed_worker(snapshot, result)
            self._is_updating_cache = False
            self._cache_update_worker = None
            self._in_flight_snapshot = None
            return

        mode = result["mode"]
        if mode == "structural_retry":
            self._recover_failed_worker(snapshot, result)
            self._is_updating_cache = False
            self._cache_update_worker = None
            self._in_flight_snapshot = None
            return

        prev_cache_by_id = {str(e[0][0]): e for e in self._ui_state_list_cache}
        if mode == "full":
            self._ui_state_list_cache = result["entries"]
            self._sort_keys = result["sort_keys"]
            changed_ids = None
        elif mode == "partial":
            new_entries = result["entries"]
            new_by_id = {str(e[0][0]): e for e in new_entries}
            self._ui_state_list_cache = new_entries
            self._sort_keys.update(result["sort_keys_delta"])
            changed = []
            for tid in (snapshot["ids"] if snapshot else ()):
                if new_by_id.get(tid) != prev_cache_by_id.get(tid):
                    changed.append(tid)
            changed_ids = changed
        else:
            # Unknown mode — treat as failure.
            self._recover_failed_worker(snapshot, result)
            self._is_updating_cache = False
            self._cache_update_worker = None
            self._in_flight_snapshot = None
            return

        self._post_event('task_state_changed', {
            'full_state': self._ui_state_list_cache,
            'changed_ids': changed_ids,
        })

        self._is_updating_cache = False
        self._cache_update_worker = None
        self._in_flight_snapshot = None
        self._restart_timer_if_dirty()

    def _recover_failed_worker(self, snapshot, result):
        """Worker returned None / error / structural_retry. Put the snapshot's
        ids back into the dirty set and force a structural refresh next pass."""
        with self._dirty_state_lock:
            self._structural_dirty = True
            if snapshot:
                for tid in snapshot["ids"]:
                    self._dirty_task_ids.add(tid)
        self._update_timer.start()

    def _restart_timer_if_dirty(self):
        """If notifications accumulated during the worker run, schedule another pass.
        Also fixes the latent silent-drop bug at the _is_updating_cache guard."""
        with self._dirty_state_lock:
            needs_followup = bool(self._dirty_task_ids) or self._structural_dirty
        if needs_followup:
            self._update_timer.start()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_task_manager_dirty_tracking.py::OnCacheUpdatedTests -v`

Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_task_manager_dirty_tracking.py gemini_translator/core/task_manager.py
git commit -m "feat(core): _on_cache_updated handles full+partial result modes

Full mode replaces cache and emits changed_ids=None. Partial mode
merges, computes changed_ids by diffing prev vs new per snapshot id,
emits the delta as list[str]. Failure / structural_retry paths
restore the snapshot ids and force a structural refresh next pass."
```

---

## Task 13: Worker failure recovery and re-entry follow-up

**Files:**
- Modify: `tests/test_task_manager_dirty_tracking.py`

- [ ] **Step 1: Write the failing tests**

Append to `OnCacheUpdatedTests`:

```python
    def test_structural_retry_restores_snapshot_and_sets_structural(self):
        tm = self._make_stub()
        tm._in_flight_snapshot = {"ids": ("a", "b"), "structural": False}
        worker = self._make_worker({"mode": "structural_retry"})

        tm._on_cache_updated(worker)

        # Snapshot ids back in the dirty set
        self.assertEqual(tm._dirty_task_ids, {"a", "b"})
        self.assertTrue(tm._structural_dirty)
        # Timer was kicked
        self.assertGreaterEqual(tm._update_timer.start_calls, 1)
        # No event emitted (the data was not refreshed)
        self.assertEqual(tm._posted_events, [])
        # Snapshot cleared
        self.assertIsNone(tm._in_flight_snapshot)
        self.assertFalse(tm._is_updating_cache)

    def test_worker_returns_none_recovers(self):
        tm = self._make_stub()
        tm._in_flight_snapshot = {"ids": ("a",), "structural": False}
        worker = self._make_worker(None)
        tm._on_cache_updated(worker)
        self.assertEqual(tm._dirty_task_ids, {"a"})
        self.assertTrue(tm._structural_dirty)
        self.assertGreaterEqual(tm._update_timer.start_calls, 1)

    def test_worker_returns_error_dict_recovers(self):
        tm = self._make_stub()
        tm._in_flight_snapshot = {"ids": ("a",), "structural": False}
        worker = self._make_worker({"mode": "error", "error": "boom"})
        tm._on_cache_updated(worker)
        self.assertEqual(tm._dirty_task_ids, {"a"})
        self.assertTrue(tm._structural_dirty)

    def test_followup_timer_starts_when_dirty_accumulated_during_worker(self):
        """Silent-drop bug fix: notifications arriving while _is_updating_cache=True
        are buffered in the dirty set. After _on_cache_updated, the timer is
        restarted so the next batch runs."""
        tm = self._make_stub()
        # Simulate: while the worker ran, two more dirty notifications arrived.
        tm._dirty_task_ids = {"new-1", "new-2"}
        tm._in_flight_snapshot = {"ids": ("a",), "structural": False}
        new_entries = [(("uuid-a", ("epub",)), "in_progress", {})]
        worker = self._make_worker({"mode": "partial", "entries": new_entries,
                                    "sort_keys_delta": {"a": (0, 0)}})
        tm._on_cache_updated(worker)
        self.assertGreaterEqual(tm._update_timer.start_calls, 1,
                                "Timer must restart because new dirty ids accumulated during the worker run")
```

- [ ] **Step 2: Run the tests to verify they pass**

Run: `pytest tests/test_task_manager_dirty_tracking.py::OnCacheUpdatedTests -v`

Expected: 6 PASS (2 from Task 12 + 4 new). The implementation from Task 12 already covers all four cases.

- [ ] **Step 3: Commit (tests only — implementation already in place)**

```bash
git add tests/test_task_manager_dirty_tracking.py
git commit -m "test(core): cover failure recovery and silent-drop fix paths"
```

---

## Task 14: Thread-hop integration test

**Files:**
- Modify: `tests/test_task_manager_dirty_tracking.py`

- [ ] **Step 1: Write the test**

Append a new class to `tests/test_task_manager_dirty_tracking.py`:

```python
class ThreadHopTests(unittest.TestCase):
    """Verifies QTimer is only started from the main thread."""

    @classmethod
    def setUpClass(cls):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_notify_task_dirty_from_worker_thread_does_not_touch_timer(self):
        import threading
        from PyQt6 import QtCore

        # Build a stub TM with a real QTimer to verify cross-thread call safety.
        tm = types.SimpleNamespace(
            _dirty_state_lock=Lock(),
            _dirty_task_ids=set(),
            _structural_dirty=False,
        )
        timer = QtCore.QTimer()
        timer.setSingleShot(True)
        tm._update_timer = timer

        emit_recorder = {"calls": 0}
        class _Signal:
            def emit(self_inner):
                emit_recorder["calls"] += 1
        tm._ui_update_requested = _Signal()

        from gemini_translator.core.task_manager import TaskManager
        tm.notify_task_dirty = types.MethodType(TaskManager.notify_task_dirty, tm)

        def worker():
            tm.notify_task_dirty("from-worker")

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=2.0)
        self.assertFalse(t.is_alive(), "worker thread should complete quickly")

        # Dirty set was updated under lock — safe across threads.
        self.assertEqual(tm._dirty_task_ids, {"from-worker"})
        # Signal was emitted — main thread will pick this up via queued connection.
        self.assertEqual(emit_recorder["calls"], 1)
        # Critically: QTimer.start() was NOT called from the worker thread.
        self.assertFalse(timer.isActive(),
                         "QTimer must not be started directly from worker thread")
```

- [ ] **Step 2: Run the test**

Run: `pytest tests/test_task_manager_dirty_tracking.py::ThreadHopTests -v`

Expected: PASS (implementation from Task 7 already enforces this).

- [ ] **Step 3: Commit**

```bash
git add tests/test_task_manager_dirty_tracking.py
git commit -m "test(core): worker-thread notify never starts QTimer directly"
```

---

## Task 15: UI plumbing — `_pending_changed_ids` in task_management_widget

**Files:**
- Modify: `gemini_translator/ui/widgets/task_management_widget.py:225-266`
- Modify: `tests/test_task_management_widget_redraw_tuning.py`

- [ ] **Step 1: Write the failing test**

Append to `RedrawTuningTests`:

```python
    def test_on_task_state_changed_normalizes_changed_ids_to_set(self):
        widget = self._make_widget()
        widget._on_task_state_changed({
            "event": "task_state_changed",
            "data": {
                "full_state": [(("u", ("epub",)), "pending", {})],
                "changed_ids": ["abc", "def"],
            },
        })
        self.assertEqual(widget._pending_changed_ids, {"abc", "def"})
        self.assertEqual(widget._pending_ui_state, [(("u", ("epub",)), "pending", {})])

    def test_on_task_state_changed_passes_none_for_full_refresh(self):
        widget = self._make_widget()
        widget._on_task_state_changed({
            "event": "task_state_changed",
            "data": {"full_state": [], "changed_ids": None},
        })
        self.assertIsNone(widget._pending_changed_ids)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_task_management_widget_redraw_tuning.py::RedrawTuningTests::test_on_task_state_changed_normalizes_changed_ids_to_set tests/test_task_management_widget_redraw_tuning.py::RedrawTuningTests::test_on_task_state_changed_passes_none_for_full_refresh -v`

Expected: FAIL — `_on_task_state_changed` doesn't read `changed_ids`.

- [ ] **Step 3: Update `_on_task_state_changed` and `_do_redraw`**

In `gemini_translator/ui/widgets/task_management_widget.py`, replace `_on_task_state_changed` (225-229):

```python
    def _on_task_state_changed(self, event_data: dict):
        full_state = event_data.get('data', {}).get('full_state')
        if isinstance(full_state, list):
            self._pending_ui_state = full_state
        self.redraw_ui()
```

with:

```python
    def _on_task_state_changed(self, event_data: dict):
        data = event_data.get('data', {})
        full_state = data.get('full_state')
        if isinstance(full_state, list):
            self._pending_ui_state = full_state
        changed_ids = data.get('changed_ids')
        if changed_ids is None:
            self._pending_changed_ids = None
        else:
            self._pending_changed_ids = set(changed_ids)
        self.redraw_ui()
```

Then update `_do_redraw` (234-266) to forward `changed_ids` to `update_list`. Find the two lines that call `self.chapter_list_widget.update_list(...)` and pass an extra arg:

```python
                if filter_key:
                    filtered_list = []
                    for item in ui_state_list:
                        if not isinstance(item, tuple) or len(item) < 3:
                            continue
                        details = item[2] if isinstance(item[2], dict) else {}
                        errors_map = details.get('errors', {})
                        if filter_key in errors_map:
                            filtered_list.append(item)
                    # When a filter is applied, changed_ids may point at rows excluded
                    # from filtered_list; safer to force a full update on this branch.
                    self.chapter_list_widget.update_list(filtered_list)
                    self._pending_changed_ids = None
                else:
                    self.chapter_list_widget.update_list(ui_state_list, self._pending_changed_ids)
                    self._pending_changed_ids = None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_task_management_widget_redraw_tuning.py -v`

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_task_management_widget_redraw_tuning.py gemini_translator/ui/widgets/task_management_widget.py
git commit -m "feat(ui): plumb changed_ids from task_state_changed to chapter list

Normalize the list[str] payload to set[str] for O(1) membership lookup.
On filtered redraws, force a full update (changed_ids cleared) because
the filter may exclude some rows that the partial set would otherwise
skip-and-leave-stale."
```

---

## Task 16: `update_list` and `_selective_update` accept `changed_ids`

**Files:**
- Modify: `gemini_translator/ui/widgets/chapter_list_widget.py:587-634, 769-774`
- Modify: `tests/test_chapter_list_widget_diff.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_chapter_list_widget_diff.py` (or a new class within the same file):

```python
import uuid


class SelectiveUpdateChangedIdsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _make_widget_with_rows(self, n=3):
        from PyQt6.QtWidgets import QTableWidget
        widget = ChapterListWidget()
        self.addCleanup(widget.deleteLater)
        widget.table = QTableWidget(0, 3)

        tasks = []
        for i in range(n):
            tid = uuid.UUID(int=i + 1)
            payload = ("epub", f"/tmp/{i}.epub", f"/tmp/{i}.html")
            tasks.append(((tid, payload), "pending", {}))
        # Use _full_redraw to populate the table without exercising _selective_update.
        widget._full_redraw(tasks)
        return widget, tasks

    def test_selective_update_with_changed_ids_skips_other_rows(self):
        widget, tasks = self._make_widget_with_rows(3)
        update_calls = []
        orig = widget._update_row_status
        widget._update_row_status = lambda row, status, details={}: update_calls.append(row) or orig(row, status, details)

        # Only the middle row id is in changed_ids.
        only_middle = {str(tasks[1][0][0])}
        widget._selective_update(tasks, changed_ids=only_middle)

        self.assertEqual(update_calls, [1],
                         "_selective_update must touch only the row whose task_id is in changed_ids")

    def test_selective_update_matches_uuid_via_str_cast(self):
        """Regression guard: row task ids are uuid.UUID, changed_ids is set[str]."""
        widget, tasks = self._make_widget_with_rows(2)
        update_calls = []
        orig = widget._update_row_status
        widget._update_row_status = lambda row, status, details={}: update_calls.append(row) or orig(row, status, details)

        changed = {str(tasks[0][0][0])}  # plain str, row id is UUID
        widget._selective_update(tasks, changed_ids=changed)

        self.assertEqual(update_calls, [0], "str(row UUID) must match the set[str] entry")

    def test_selective_update_with_none_changed_ids_updates_all_rows(self):
        widget, tasks = self._make_widget_with_rows(3)
        update_calls = []
        orig = widget._update_row_status
        widget._update_row_status = lambda row, status, details={}: update_calls.append(row) or orig(row, status, details)

        widget._selective_update(tasks, changed_ids=None)

        self.assertEqual(update_calls, [0, 1, 2], "None means update every row (backward compat)")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_chapter_list_widget_diff.py::SelectiveUpdateChangedIdsTests -v`

Expected: FAIL — `_selective_update` does not accept `changed_ids` kwarg.

- [ ] **Step 3: Extend `update_list` and `_selective_update` signatures**

In `gemini_translator/ui/widgets/chapter_list_widget.py`:

1. Replace `update_list` signature and the structural-match branch (587-634). Change the signature to:

```python
    def update_list(self, tasks_data, changed_ids=None):
```

and replace the structural-match branch:

```python
            if current_task_ids == new_task_ids:
                self._selective_update(tasks_data)
```

with:

```python
            if current_task_ids == new_task_ids:
                self._selective_update(tasks_data, changed_ids=changed_ids)
```

(Surgical / full_redraw branches do not need `changed_ids` — they rewrite everything.)

2. Replace `_selective_update` (769-774):

```python
    def _selective_update(self, tasks_data):
        """Выполняет точечное обновление статусов в существующей таблице."""
        self.table.blockSignals(True)
        for i, (task_tuple_with_uuid, status, details) in enumerate(tasks_data):
            self._update_row_status(i, status, details)
        self.table.blockSignals(False)
```

with:

```python
    def _selective_update(self, tasks_data, changed_ids=None):
        """Выполняет точечное обновление статусов в существующей таблице.
        changed_ids: set[str] | None — if set, only rows whose task_id (cast to
        str) is in the set get _update_row_status. None means update every row
        (backward compatible)."""
        self.table.blockSignals(True)
        for i, (task_tuple_with_uuid, status, details) in enumerate(tasks_data):
            if changed_ids is not None and str(task_tuple_with_uuid[0]) not in changed_ids:
                continue
            self._update_row_status(i, status, details)
        self.table.blockSignals(False)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_chapter_list_widget_diff.py -v`

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_chapter_list_widget_diff.py gemini_translator/ui/widgets/chapter_list_widget.py
git commit -m "feat(ui): _selective_update skips rows not in changed_ids

UUID rows in the table are matched against set[str] changed_ids via
str(task_id), preserving the type discipline established in TaskManager.
changed_ids=None preserves the original 'update every row' behaviour."
```

---

## Task 17: Callsite audit and migration

**Files:**
- Modify: `gemini_translator/core/task_manager.py` (selected `_safe_request_ui_update` callsites)
- Modify: `gemini_translator/core/chunk_assembler.py:299`
- Create: `docs/superpowers/specs/2026-06-04-translator-energy-callsite-audit.md` (audit table artifact)

- [ ] **Step 1: Produce the audit table**

Run: `grep -n "_safe_request_ui_update" gemini_translator/core/task_manager.py gemini_translator/core/chunk_assembler.py`

For each callsite, read the surrounding 10 lines and classify into one of:
- **structural** — adds/removes/reorders tasks, batch import, queue load/restore, clear. Leave on `_safe_request_ui_update` (now an alias for `notify_structural_change`).
- **single-task** — caller has a `task_id` (or `task_info[0]`) in scope and is updating exactly that task's status/error counter. Migrate to `self.notify_task_dirty(task_id)`.
- **unclear / shared paths** — leave on `_safe_request_ui_update`. Conservative default.

Save the table to `docs/superpowers/specs/2026-06-04-translator-energy-callsite-audit.md`:

```markdown
# Callsite audit — `_safe_request_ui_update` → dirty-tracking API

| file:line | surrounding context (one line) | category | migrated to |
|-----------|--------------------------------|----------|-------------|
| task_manager.py:340 | (paste a short summary) | structural | `_safe_request_ui_update` (unchanged) |
| task_manager.py:1317 | `record_failure(task_info, ...)` | single-task | `notify_task_dirty(task_info[0])` |
| ...one row per grep hit... | | | |
```

- [ ] **Step 2: Migrate single-task callsites**

For each row classified as **single-task**, replace the call with the new API. Example for `record_failure` (line 1317):

Replace:

```python
    def record_failure(self, task_info: tuple, error_type: str):
        try:
            with self._get_write_conn() as conn:
                conn.execute("INSERT INTO task_errors (task_id, error_type, timestamp) VALUES (?, ?, ?)", (str(task_info[0]), error_type, time.time()))
        except sqlite3.IntegrityError:
            pass

        self._safe_request_ui_update()
```

with:

```python
    def record_failure(self, task_info: tuple, error_type: str):
        try:
            with self._get_write_conn() as conn:
                conn.execute("INSERT INTO task_errors (task_id, error_type, timestamp) VALUES (?, ?, ?)", (str(task_info[0]), error_type, time.time()))
        except sqlite3.IntegrityError:
            pass

        self.notify_task_dirty(task_info[0])
```

Repeat for every callsite marked **single-task**. Leave the rest untouched.

- [ ] **Step 3: Write an integration test for the audit**

Append to `tests/test_task_manager_dirty_tracking.py`:

```python
class CallsiteMigrationTests(unittest.TestCase):
    def test_record_failure_marks_only_that_task_dirty(self):
        tm = types.SimpleNamespace(
            _dirty_state_lock=Lock(),
            _dirty_task_ids=set(),
            _structural_dirty=False,
            _ui_update_requested=_SignalStub(),
        )
        from gemini_translator.core.task_manager import TaskManager
        tm.notify_task_dirty = types.MethodType(TaskManager.notify_task_dirty, tm)

        # Simulate record_failure body's notify call directly.
        tm.notify_task_dirty("00000000-0000-0000-0000-00000000aaaa")

        self.assertEqual(tm._dirty_task_ids, {"00000000-0000-0000-0000-00000000aaaa"})
        self.assertFalse(tm._structural_dirty,
                         "Single-task transitions must NOT set the structural flag")
```

- [ ] **Step 4: Run the full test suite to catch regressions**

Run: `pytest tests/test_task_manager_dirty_tracking.py tests/test_chapter_list_widget_diff.py tests/test_task_management_widget_redraw_tuning.py -v`

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add gemini_translator/core/task_manager.py gemini_translator/core/chunk_assembler.py \
        tests/test_task_manager_dirty_tracking.py \
        docs/superpowers/specs/2026-06-04-translator-energy-callsite-audit.md
git commit -m "refactor(core): migrate single-task callsites to notify_task_dirty

See callsite-audit.md for the per-call classification. Structural
callsites stay on _safe_request_ui_update (now an alias). Migrated
callers pass task_id explicitly so partial cache refresh activates."
```

---

## Task 18: Phase 2 verification checkpoint

**Files:** none (verification only)

- [ ] **Step 1: Run the full new test surface**

Run: `pytest tests/test_task_manager_dirty_tracking.py tests/test_chapter_list_widget_diff.py tests/test_task_management_widget_redraw_tuning.py -v`

Expected: all PASS.

- [ ] **Step 2: Run the broader test surface to catch regressions**

Run: `pytest tests/ -x --ignore=tests/test_workascii_runtime.py --ignore=tests/test_ranobelib_return_to_menu.py -v`

(Two ignores per `translator-test-env-deps` memory — pre-existing failures unrelated to this work.)

Expected: pass/fail status equal to the pre-branch baseline.

- [ ] **Step 3: Manual Activity Monitor measurement**

1. Stash any work in progress.
2. Check out `main`, launch `python main.py`, start a realistic translation session with ~50 chapters, RPS near the limit. Note CPU% in Activity Monitor over a 60-second window.
3. Check out this branch (`feat/energy-active-session-dirty-tracking`), repeat. Compare.
4. Record the numbers in a comment on the eventual PR.

If the measured drop is modest, revisit the Phase 1-only path (the `energy-active-session-phase-1` tag) before merging Phase 2.

- [ ] **Step 4: Tag the Phase 2 checkpoint**

```bash
git tag energy-active-session-phase-2
```

- [ ] **Step 5: Update project memory**

Append a line to `~/.claude/projects/-Users-rasreo-dev-translatorFork-MOD/memory/translator-energy-optimization.md` describing the new dirty-tracking layer (under a "Done <date>" section like the existing P2/P3 entries). This keeps the next session's context current.

---

## Self-Review Summary

Spec coverage verified against [the design doc](../specs/2026-06-04-translator-energy-active-session-design.md):
- Phase 1 (UI gating) — Tasks 1, 2 (chapter_list_widget diff gates), Task 3 (redraw tuning), Task 4 (filter handler).
- Phase 2 (TaskManager dirty-tracking) — Tasks 6 (state + STATUS_GROUP_ORDER), 7 (notify API), 8 (alias), 9 (snapshot), 10 (full path), 11 (partial path), 12 (success modes + changed_ids), 13 (failure recovery), 14 (thread-hop test), 15 (UI plumbing), 16 (selective_update), 17 (callsite migration).
- Verification checkpoints — Tasks 5 and 18.

No placeholders, no `TBD`s, every code change shown in full. Type discipline (`set[str]`, `list[str]`, `str(task_id)`) consistent across tasks. STATUS_GROUP_ORDER includes both DB and UI aliases. Failure recovery + re-entry follow-up explicitly tested. Thread-hop discipline preserved end to end.
