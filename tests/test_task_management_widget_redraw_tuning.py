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


if __name__ == "__main__":
    unittest.main()
