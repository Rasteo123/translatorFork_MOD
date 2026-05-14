import os
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore

from gemini_translator.core.task_manager import TaskDBWorker


class TaskDBWorkerLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])

    def test_finished_signal_is_emitted_after_thread_stops_running(self):
        running_states_at_finished = []
        worker = TaskDBWorker(lambda: time.sleep(0.02))

        def on_finished():
            running_states_at_finished.append(worker.isRunning())
            self.app.quit()

        worker.finished.connect(on_finished, QtCore.Qt.ConnectionType.DirectConnection)
        worker.start()
        QtCore.QTimer.singleShot(2000, self.app.quit)
        self.app.exec()

        self.assertTrue(worker.wait(1000))
        self.assertEqual(running_states_at_finished, [False])


if __name__ == "__main__":
    unittest.main()
