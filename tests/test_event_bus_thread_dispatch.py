import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets

from main import EventBus


class _ThreadBoundReceiver(QtCore.QObject):
    received = QtCore.pyqtSignal()

    def __init__(self):
        super().__init__()
        self.received_thread = None
        self.received_event = None

    @QtCore.pyqtSlot(dict)
    def on_event(self, event):
        self.received_thread = QtCore.QThread.currentThread()
        self.received_event = event
        self.received.emit()


class EventBusThreadDispatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_topic_dispatch_queues_qobject_callbacks_to_receiver_thread(self):
        bus = EventBus()
        receiver = _ThreadBoundReceiver()
        worker_thread = QtCore.QThread()
        self.addCleanup(worker_thread.wait)
        self.addCleanup(worker_thread.quit)
        self.addCleanup(receiver.deleteLater)
        self.addCleanup(bus.deleteLater)

        receiver.moveToThread(worker_thread)
        worker_thread.start()
        bus.subscribe("start_session_requested", receiver.on_event)

        loop = QtCore.QEventLoop()
        receiver.received.connect(loop.quit)
        QtCore.QTimer.singleShot(1000, loop.quit)

        bus.event_posted.emit({"event": "start_session_requested", "data": {"settings": {}}})
        if receiver.received_event is None:
            loop.exec()

        self.assertEqual(receiver.received_event["event"], "start_session_requested")
        self.assertIs(receiver.received_thread, worker_thread)


if __name__ == "__main__":
    unittest.main()
