import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

TESTS_DIR = os.path.dirname(__file__)
PROJECT_ROOT = os.path.dirname(TESTS_DIR)
RANOBELIB_DIR = os.path.join(PROJECT_ROOT, "ranobelib")

if RANOBELIB_DIR not in sys.path:
    sys.path.insert(0, RANOBELIB_DIR)

from main_window import RanobeUploaderApp


class _RanobeUploaderHarness:
    _return_to_menu = RanobeUploaderApp._return_to_menu

    def __init__(self, handler=None):
        self._return_to_menu_handler = handler
        self.calls = []

    def _save_settings(self):
        self.calls.append("save")

    def hide(self):
        self.calls.append("hide")

    def close(self):
        self.calls.append("close")


class RanobeUploaderReturnToMenuTests(unittest.TestCase):
    def test_return_to_menu_closes_window_before_handler(self):
        handler_calls = []

        harness = _RanobeUploaderHarness(
            handler=lambda: handler_calls.append("handler")
        )

        harness._return_to_menu()

        self.assertEqual(harness.calls, ["save", "hide", "close"])
        self.assertEqual(handler_calls, ["handler"])

    def test_return_to_menu_without_handler_just_closes_window(self):
        harness = _RanobeUploaderHarness()

        harness._return_to_menu()

        self.assertEqual(harness.calls, ["save", "close"])


if __name__ == "__main__":
    unittest.main()
