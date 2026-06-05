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
        # _get_status_display_info('in_progress', {}, payload) → ("🔄 В работе…", "#3498DB")
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

    def test_populate_row_skips_tooltip_when_same(self):
        from PyQt6.QtWidgets import QTableWidget
        widget = self._make_widget()
        widget.table = QTableWidget(1, 3)

        # _populate_row reads app.engine.session_id; stub so the attribute exists
        # and short-circuits to the "no_session" branch.
        app = QtWidgets.QApplication.instance()
        prev_engine = getattr(app, "engine", "__missing__")
        app.engine = None
        if prev_engine == "__missing__":
            self.addCleanup(lambda: delattr(app, "engine"))
        else:
            self.addCleanup(lambda: setattr(app, "engine", prev_engine))

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

        # _populate_row (called inside _full_redraw) reads app.engine.session_id;
        # stub so the attribute exists and short-circuits to the "no_session" branch.
        app = QtWidgets.QApplication.instance()
        prev_engine = getattr(app, "engine", "__missing__")
        app.engine = None
        if prev_engine == "__missing__":
            self.addCleanup(lambda: delattr(app, "engine"))
        else:
            self.addCleanup(lambda: setattr(app, "engine", prev_engine))

        tasks = []
        for i in range(n):
            tid = uuid.UUID(int=i + 1)
            payload = ("epub", f"/tmp/{i}.epub", f"/tmp/{i}.html")
            tasks.append(((tid, payload), "pending", {}))
        widget._full_redraw(tasks)
        return widget, tasks

    def test_selective_update_with_changed_ids_skips_other_rows(self):
        widget, tasks = self._make_widget_with_rows(3)
        update_calls = []
        orig = widget._update_row_status
        widget._update_row_status = lambda row, status, details={}: update_calls.append(row) or orig(row, status, details)

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

        changed = {str(tasks[0][0][0])}  # plain str; row id is UUID
        widget._selective_update(tasks, changed_ids=changed)

        self.assertEqual(update_calls, [0], "str(row UUID) must match the set[str] entry")

    def test_selective_update_with_none_changed_ids_updates_all_rows(self):
        widget, tasks = self._make_widget_with_rows(3)
        update_calls = []
        orig = widget._update_row_status
        widget._update_row_status = lambda row, status, details={}: update_calls.append(row) or orig(row, status, details)

        widget._selective_update(tasks, changed_ids=None)

        self.assertEqual(update_calls, [0, 1, 2], "None means update every row (backward compat)")


if __name__ == "__main__":
    unittest.main()
