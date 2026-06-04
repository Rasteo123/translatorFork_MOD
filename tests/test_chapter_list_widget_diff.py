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


if __name__ == "__main__":
    unittest.main()
