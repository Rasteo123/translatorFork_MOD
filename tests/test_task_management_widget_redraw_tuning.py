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

    def test_coalesced_partial_events_union_changed_ids(self):
        widget = self._make_widget()
        # First partial event — fresh window (timer inactive), so it sets the set.
        widget._on_task_state_changed({
            "event": "task_state_changed",
            "data": {"full_state": [("s1",)], "changed_ids": ["a"]},
        })
        self.assertTrue(widget._redraw_timer.isActive())
        self.assertEqual(widget._pending_changed_ids, {"a"})
        # Second partial event within the SAME window — must UNION, not overwrite.
        widget._on_task_state_changed({
            "event": "task_state_changed",
            "data": {"full_state": [("s2",)], "changed_ids": ["b"]},
        })
        self.assertEqual(widget._pending_changed_ids, {"a", "b"},
                         "Coalesced partial events must union changed_ids, not overwrite")

    def test_full_event_overrides_pending_partials_in_window(self):
        widget = self._make_widget()
        widget._on_task_state_changed({
            "event": "task_state_changed",
            "data": {"full_state": [], "changed_ids": ["a"]},
        })
        # A full (None) event in the same window must override the accumulated set.
        widget._on_task_state_changed({
            "event": "task_state_changed",
            "data": {"full_state": [], "changed_ids": None},
        })
        self.assertIsNone(widget._pending_changed_ids,
                          "A full-refresh event must override pending partials within the window")

    def test_partial_after_full_in_window_stays_full(self):
        widget = self._make_widget()
        widget._on_task_state_changed({
            "event": "task_state_changed",
            "data": {"full_state": [], "changed_ids": None},
        })
        # Once a full refresh is pending, a later partial must NOT downgrade it.
        widget._on_task_state_changed({
            "event": "task_state_changed",
            "data": {"full_state": [], "changed_ids": ["a"]},
        })
        self.assertIsNone(widget._pending_changed_ids,
                          "Once a full refresh is pending, a later partial must not downgrade it")


if __name__ == "__main__":
    unittest.main()
