import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.ui.shell import NavigationController, ShellPage


class RecordingPage(ShellPage):
    """Page that records on_enter/on_leave calls and can veto leaving."""

    def __init__(self, title="", can_leave_value=True):
        super().__init__()
        self.page_title = title
        self._can_leave_value = can_leave_value
        self.events = []

    def on_enter(self):
        self.events.append("enter")

    def on_leave(self):
        self.events.append("leave")

    def can_leave(self):
        return self._can_leave_value


class NavigationControllerPushTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.stack = QtWidgets.QStackedWidget()
        self.addCleanup(self.stack.deleteLater)
        self.nav = NavigationController(self.stack)
        self.changes = []
        self.nav.stack_changed.connect(lambda: self.changes.append(self.nav.depth))

    def test_set_home_makes_depth_one_and_current(self):
        home = RecordingPage("home")
        self.nav.set_home(home)
        self.assertEqual(self.nav.depth, 1)
        self.assertIs(self.nav.current_page(), home)
        self.assertIs(self.stack.currentWidget(), home)
        self.assertEqual(home.events, ["enter"])
        self.assertEqual(self.changes, [1])

    def test_push_adds_page_and_switches_current(self):
        home = RecordingPage("home")
        child = RecordingPage("child")
        self.nav.set_home(home)
        self.nav.push(child)
        self.assertEqual(self.nav.depth, 2)
        self.assertIs(self.nav.current_page(), child)
        self.assertIs(self.stack.currentWidget(), child)
        # on_leave fires only on pop (removal), NOT when a page is merely covered.
        self.assertEqual(home.events, ["enter"])
        self.assertEqual(child.events, ["enter"])
        self.assertEqual(self.changes, [1, 2])

    def test_page_request_push_is_routed_through_controller(self):
        home = RecordingPage("home")
        child = RecordingPage("child")
        grandchild = RecordingPage("grandchild")
        self.nav.set_home(home)
        self.nav.push(child)
        child.request_push.emit(grandchild)
        self.assertEqual(self.nav.depth, 3)
        self.assertIs(self.nav.current_page(), grandchild)

    def test_push_before_set_home_raises(self):
        with self.assertRaises(RuntimeError):
            self.nav.push(RecordingPage("orphan"))

    def test_set_home_twice_raises(self):
        self.nav.set_home(RecordingPage("home"))
        with self.assertRaises(RuntimeError):
            self.nav.set_home(RecordingPage("again"))


class NavigationControllerPopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.stack = QtWidgets.QStackedWidget()
        self.addCleanup(self.stack.deleteLater)
        self.nav = NavigationController(self.stack)

    def test_pop_returns_to_previous_page(self):
        home = RecordingPage("home")
        child = RecordingPage("child")
        self.nav.set_home(home)
        self.nav.push(child)
        result = self.nav.pop()
        self.assertTrue(result)
        self.assertEqual(self.nav.depth, 1)
        self.assertIs(self.nav.current_page(), home)
        self.assertIs(self.stack.currentWidget(), home)
        self.assertEqual(self.stack.indexOf(child), -1)
        # The popped child is removed → gets on_leave. on_leave fires only on pop.
        self.assertEqual(child.events, ["enter", "leave"])
        # home was never popped/removed while covered, so no on_leave; on_enter fires again on return.
        self.assertEqual(home.events, ["enter", "enter"])

    def test_cannot_pop_home_page(self):
        home = RecordingPage("home")
        self.nav.set_home(home)
        self.assertFalse(self.nav.pop())
        self.assertEqual(self.nav.depth, 1)

    def test_can_leave_veto_blocks_pop(self):
        home = RecordingPage("home")
        sticky = RecordingPage("sticky", can_leave_value=False)
        self.nav.set_home(home)
        self.nav.push(sticky)
        self.assertFalse(self.nav.pop())
        self.assertEqual(self.nav.depth, 2)
        self.assertIs(self.nav.current_page(), sticky)

    def test_reset_to_home_pops_everything(self):
        home = RecordingPage("home")
        self.nav.set_home(home)
        self.nav.push(RecordingPage("a"))
        self.nav.push(RecordingPage("b"))
        self.nav.reset_to_home()
        self.assertEqual(self.nav.depth, 1)
        self.assertIs(self.nav.current_page(), home)

    def test_popped_page_signals_are_disconnected(self):
        home = RecordingPage("home")
        child_a = RecordingPage("a")
        child_b = RecordingPage("b")
        self.nav.set_home(home)
        self.nav.push(child_a)
        self.nav.push(child_b)
        self.nav.pop()  # pops child_b; child_a is now current
        # Stale signals from the popped page must not drive navigation.
        child_b.request_back.emit()
        child_b.request_push.emit(RecordingPage("ghost"))
        self.assertEqual(self.nav.depth, 2)
        self.assertIs(self.nav.current_page(), child_a)

    def test_reset_to_home_stops_at_veto(self):
        home = RecordingPage("home")
        a = RecordingPage("a")
        b = RecordingPage("b", can_leave_value=False)
        c = RecordingPage("c")
        self.nav.set_home(home)
        self.nav.push(a)
        self.nav.push(b)
        self.nav.push(c)
        self.nav.reset_to_home()
        # c pops; b vetoes -> stop. Remaining: home, a, b.
        self.assertEqual(self.nav.depth, 3)
        self.assertIs(self.nav.current_page(), b)
