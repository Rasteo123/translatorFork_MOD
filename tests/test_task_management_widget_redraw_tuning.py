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
