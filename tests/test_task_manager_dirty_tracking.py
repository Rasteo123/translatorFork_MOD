import types
import unittest
from threading import Lock


class _TimerStub:
    def __init__(self):
        self.start_calls = 0
    def start(self):
        self.start_calls += 1


class _SignalStub:
    def __init__(self):
        self.emit_calls = 0
    def emit(self):
        self.emit_calls += 1


class DirtyTrackingStateTests(unittest.TestCase):
    def test_status_group_order_includes_db_and_ui_aliases(self):
        from gemini_translator.core.task_manager import STATUS_GROUP_ORDER
        self.assertEqual(STATUS_GROUP_ORDER["in_progress"], 1)
        self.assertEqual(STATUS_GROUP_ORDER["pending"], 2)
        self.assertEqual(STATUS_GROUP_ORDER["held"], 3)
        # DB statuses and their UI aliases share the same group:
        self.assertEqual(STATUS_GROUP_ORDER["completed"], STATUS_GROUP_ORDER["success"])
        self.assertEqual(STATUS_GROUP_ORDER["failed"], STATUS_GROUP_ORDER["error"])
        self.assertEqual(STATUS_GROUP_ORDER["completed"], 4)
        self.assertEqual(STATUS_GROUP_ORDER["failed"], 5)


if __name__ == "__main__":
    unittest.main()
