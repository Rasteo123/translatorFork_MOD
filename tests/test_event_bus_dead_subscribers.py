import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets, sip

from main import EventBus


class _Subscriber(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.received = []

    def on_event(self, event):
        self.received.append(event)


class EventBusDeadSubscriberTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_dead_widget_subscription_is_removed_on_dispatch(self):
        bus = EventBus()
        widget = _Subscriber()
        bus.subscribe("log_message", widget.on_event)
        bus.subscribe("task_state_changed", widget.on_event)

        sip.delete(widget)

        bus.emit_event({"event": "log_message", "data": {"message": "x"}})

        self.assertNotIn("log_message", bus._topic_subscribers)
        # Мёртвый подписчик снимается со ВСЕХ топиков, не только с текущего.
        self.assertNotIn("task_state_changed", bus._topic_subscribers)

    def test_live_widget_still_receives_and_stays_subscribed(self):
        bus = EventBus()
        widget = _Subscriber()
        bus.subscribe("log_message", widget.on_event)

        event = {"event": "log_message", "data": {"message": "y"}}
        bus.emit_event(event)

        self.assertEqual(widget.received, [event])
        self.assertIn("log_message", bus._topic_subscribers)

    def test_dead_subscriber_does_not_block_live_ones(self):
        bus = EventBus()
        dead = _Subscriber()
        live = _Subscriber()
        bus.subscribe("log_message", dead.on_event)
        bus.subscribe("log_message", live.on_event)

        sip.delete(dead)

        event = {"event": "log_message", "data": {"message": "z"}}
        bus.emit_event(event)

        self.assertEqual(live.received, [event])
        self.assertEqual(bus._topic_subscribers["log_message"], [live.on_event])

    def test_non_qobject_callbacks_unaffected(self):
        bus = EventBus()
        events = []
        bus.subscribe("log_message", events.append)

        event = {"event": "log_message", "data": {}}
        bus.emit_event(event)

        self.assertEqual(events, [event])


if __name__ == "__main__":
    unittest.main()
