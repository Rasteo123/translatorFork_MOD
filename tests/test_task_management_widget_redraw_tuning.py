import os
import types
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtGui, QtWidgets

from gemini_translator.ui.widgets.task_management_widget import TaskManagementWidget


class RedrawTuningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _make_widget(self):
        w = TaskManagementWidget()
        self.addCleanup(w.close)
        return w

    def test_set_session_mode_active_uses_500ms_coarse_timer(self):
        widget = self._make_widget()
        widget.set_session_mode(True)

        self.assertEqual(widget._redraw_timer.interval(), 500)
        self.assertEqual(
            widget._redraw_timer.timerType(),
            QtCore.Qt.TimerType.CoarseTimer,
            "Use CoarseTimer so active-session table redraws can be coalesced by macOS.",
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

    def test_do_redraw_skips_retry_visibility_scan_during_active_session(self):
        widget = self._make_widget()
        widget.isVisible = lambda: True  # exercise the visible redraw path
        widget._is_session_active = True
        widget._pending_ui_state = []

        update_calls = []
        retry_scan_calls = []
        widget.chapter_list_widget = types.SimpleNamespace(
            update_list=lambda *args: update_calls.append(args)
        )
        widget.check_and_update_retry_button_visibility = lambda: retry_scan_calls.append(True)

        app = QtWidgets.QApplication.instance()
        previous_engine = getattr(app, "engine", "__missing__")
        app.engine = types.SimpleNamespace(
            task_manager=types.SimpleNamespace(get_ui_state_list=lambda: [])
        )
        if previous_engine == "__missing__":
            self.addCleanup(lambda: delattr(app, "engine"))
        else:
            self.addCleanup(lambda: setattr(app, "engine", previous_engine))

        widget._do_redraw()

        self.assertEqual(len(update_calls), 1)
        self.assertEqual(retry_scan_calls, [],
                         "Retry-button visibility scan is idle/end-of-session UI work")

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

    def test_do_redraw_deferred_when_tab_hidden(self):
        widget = self._make_widget()  # never shown → not visible
        self.assertFalse(widget.isVisible())
        rebuilds = []
        widget.chapter_list_widget = types.SimpleNamespace(
            update_list=lambda *args, **kwargs: rebuilds.append((args, kwargs))
        )
        widget._pending_ui_state = [(("u", ("epub",)), "pending", {})]

        widget._do_redraw()

        self.assertEqual(rebuilds, [],
                         "Hidden task tab must not rebuild the table (setCellWidget churn).")
        self.assertTrue(widget._redraw_pending)
        self.assertIsNotNone(widget._pending_ui_state,
                             "Pending state must be preserved for the deferred redraw.")

    def test_show_event_runs_deferred_full_redraw(self):
        widget = self._make_widget()
        widget._redraw_pending = True
        widget._pending_changed_ids = {"x"}
        redraw_calls = []
        widget.redraw_ui = lambda: redraw_calls.append(True)

        widget.showEvent(QtGui.QShowEvent())

        self.assertFalse(widget._redraw_pending)
        self.assertIsNone(widget._pending_changed_ids,
                          "A redraw deferred while hidden must rebuild fully, not selectively.")
        self.assertEqual(redraw_calls, [True])

    def test_show_event_noop_when_nothing_pending(self):
        widget = self._make_widget()
        widget._redraw_pending = False
        redraw_calls = []
        widget.redraw_ui = lambda: redraw_calls.append(True)

        widget.showEvent(QtGui.QShowEvent())

        self.assertEqual(redraw_calls, [],
                         "No redraw on show if nothing changed while hidden.")


if __name__ == "__main__":
    unittest.main()
