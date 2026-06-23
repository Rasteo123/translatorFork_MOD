import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtWidgets import QTextEdit

from gemini_translator.ui.widgets.log_widget import (
    CATCHUP_FLUSH_BATCH_SIZE,
    CATCHUP_FLUSH_INTERVAL_MS,
    LOG_FLUSH_INTERVAL_MS,
    MAX_LOG_FLUSH_BATCH_SIZE,
    LogWidget,
)


class LogWidgetBatchingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_append_message_queues_until_flush(self):
        widget = LogWidget(event_bus=None)
        try:
            widget.append_message({"message": "[INFO] one"})

            self.assertEqual(len(widget._pending_log_data), 1)
            self.assertNotIn("one", widget.log_view.toPlainText())

            widget._flush_pending_messages()

            self.assertEqual(widget._pending_log_data, [])
            self.assertIn("one", widget.log_view.toPlainText())
        finally:
            widget.close()

    def test_flush_inserts_multiple_messages_as_one_batch(self):
        widget = LogWidget(event_bus=None)
        inserted_batches = []
        widget._insert_html_batch = inserted_batches.append

        try:
            widget.append_message({"message": "[INFO] first"})
            widget.append_message({"message": "[WARN] second"})

            widget._flush_pending_messages()

            self.assertEqual(len(inserted_batches), 1)
            self.assertIn("first", inserted_batches[0])
            self.assertIn("second", inserted_batches[0])
        finally:
            widget.close()

    def test_large_backlog_drains_in_small_fast_chunks(self):
        # After being away from the tab, a big backlog must stream in smoothly:
        # small chunks at a fast cadence, not one jerky 300-line batch.
        widget = LogWidget(event_bus=None)
        inserted_batches = []
        scheduled_delays = []
        widget._insert_html_batch = inserted_batches.append
        widget._schedule_log_flush = scheduled_delays.append

        try:
            for index in range(100):
                widget._queue_log_message({"message": f"[INFO] item {index}"})

            widget._flush_pending_messages()

            self.assertEqual(len(inserted_batches), 1)
            self.assertEqual(len(widget._pending_log_data), 100 - CATCHUP_FLUSH_BATCH_SIZE)
            self.assertEqual(scheduled_delays, [CATCHUP_FLUSH_INTERVAL_MS])
        finally:
            widget.close()

    def test_catchup_returns_to_calm_cadence_for_final_small_chunk(self):
        # Once the backlog shrinks to one smooth chunk or less, drain the rest in
        # one go and go back to the calm 1s cadence (no perpetual fast flushing).
        widget = LogWidget(event_bus=None)
        scheduled_delays = []
        widget._insert_html_batch = lambda *_: None
        widget._schedule_log_flush = scheduled_delays.append

        try:
            for index in range(CATCHUP_FLUSH_BATCH_SIZE + 5):
                widget._queue_log_message({"message": f"[INFO] item {index}"})

            widget._flush_pending_messages()  # drains 25, leaves 5

            self.assertEqual(len(widget._pending_log_data), 5)
            self.assertEqual(scheduled_delays, [CATCHUP_FLUSH_INTERVAL_MS])

            widget._flush_pending_messages()  # 5 <= chunk → drain all, no reschedule

            self.assertEqual(widget._pending_log_data, [])
            self.assertEqual(scheduled_delays, [CATCHUP_FLUSH_INTERVAL_MS])
        finally:
            widget.close()

    def test_small_live_backlog_flushes_in_one_calm_batch(self):
        # The normal live trickle (<= one chunk) never enters fast mode.
        widget = LogWidget(event_bus=None)
        inserted_batches = []
        scheduled_delays = []
        widget._insert_html_batch = inserted_batches.append
        widget._schedule_log_flush = scheduled_delays.append

        try:
            for index in range(5):
                widget._queue_log_message({"message": f"[INFO] item {index}"})

            widget._flush_pending_messages()

            self.assertEqual(len(inserted_batches), 1)
            self.assertEqual(widget._pending_log_data, [])
            self.assertEqual(scheduled_delays, [])  # nothing left → no reschedule
        finally:
            widget.close()

    def test_show_event_with_large_backlog_starts_fast_catchup(self):
        widget = LogWidget(event_bus=None)
        try:
            for index in range(100):
                widget._queue_log_message({"message": f"[INFO] item {index}"})

            scheduled_delays = []
            widget._schedule_log_flush = scheduled_delays.append
            widget.showEvent(QtGui.QShowEvent())

            self.assertEqual(scheduled_delays, [CATCHUP_FLUSH_INTERVAL_MS])
        finally:
            widget._log_flush_timer.stop()
            widget.close()

    def test_hidden_widget_defers_flush_timer_until_shown(self):
        widget = LogWidget(event_bus=None)
        try:
            self.assertFalse(widget.isVisible())

            widget.append_message({"message": "[INFO] hidden"})

            self.assertEqual(len(widget._pending_log_data), 1)
            self.assertFalse(widget._log_flush_timer.isActive())

            scheduled_delays = []
            widget._schedule_log_flush = scheduled_delays.append

            widget.showEvent(QtGui.QShowEvent())

            self.assertEqual(scheduled_delays, [LOG_FLUSH_INTERVAL_MS])
        finally:
            widget._log_flush_timer.stop()
            widget.close()

    def test_normal_flush_interval_is_throttled_for_text_layout(self):
        self.assertGreaterEqual(LOG_FLUSH_INTERVAL_MS, 750)

    def test_log_view_uses_low_energy_rendering_settings(self):
        widget = LogWidget(event_bus=None)
        try:
            self.assertEqual(
                widget._log_flush_timer.timerType(),
                QtCore.Qt.TimerType.CoarseTimer,
            )
            self.assertFalse(widget.log_view.isUndoRedoEnabled())
            self.assertEqual(
                widget.log_view.lineWrapMode(),
                QTextEdit.LineWrapMode.NoWrap,
            )
        finally:
            widget.close()


if __name__ == "__main__":
    unittest.main()
