"""RanobeLib: «Вернуться в меню» не должен уходить в меню со скрытым окном, если
пользователь отказался прерывать активный фоновый воркер.

Регрессия находки ranobelib/bugs/3-return-to-menu-abandons-runnin (замечание
рецензента, minor, main_window.py:921): closeEvent (main_window.py) теперь умеет
отказывать в закрытии (event.ignore(), QWidget.close() возвращает False), но
_return_to_menu раньше делал self.hide(); self.close(); handler() безусловно —
при отказе получалось невидимое окно с работающим воркером и мгновенный переход
в меню поверх него.
"""
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

TESTS_DIR = os.path.dirname(__file__)
PROJECT_ROOT = os.path.dirname(TESTS_DIR)
RANOBELIB_DIR = os.path.join(PROJECT_ROOT, "ranobelib")
if RANOBELIB_DIR not in sys.path:
    sys.path.insert(0, RANOBELIB_DIR)

from PyQt6.QtWidgets import QApplication  # noqa: E402

from main_window import RanobeUploaderApp  # noqa: E402

_APP = QApplication.instance() or QApplication([])


class _DeclinedCloseHarness:
    """close() ведёт себя как реальный QWidget.close(), отказавшийся закрыться
    (closeEvent вызвал event.ignore()) — возвращает именно False."""

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
        return False

    def show(self):
        self.calls.append("show")


class _AcceptedCloseHarness(_DeclinedCloseHarness):
    """close() успешно закрывает окно и возвращает True — обычный случай."""

    def close(self):
        self.calls.append("close")
        return True


class ReturnToMenuDeclinedTests(unittest.TestCase):
    def test_declined_close_shows_window_back_and_skips_handler(self):
        handler_calls = []
        harness = _DeclinedCloseHarness(
            handler=lambda: handler_calls.append("handler")
        )

        harness._return_to_menu()

        self.assertEqual(harness.calls, ["save", "hide", "close", "show"])
        self.assertEqual(handler_calls, [])

    def test_accepted_close_still_calls_handler(self):
        handler_calls = []
        harness = _AcceptedCloseHarness(
            handler=lambda: handler_calls.append("handler")
        )

        harness._return_to_menu()

        self.assertEqual(harness.calls, ["save", "hide", "close"])
        self.assertEqual(handler_calls, ["handler"])


if __name__ == "__main__":
    unittest.main()
