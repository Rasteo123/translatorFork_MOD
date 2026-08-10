import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets

from gemini_translator.ui.shell import MainShell, ShellPage


class SmallPage(ShellPage):
    page_title = "small"

    def __init__(self):
        super().__init__()
        self.setMinimumSize(200, 150)


class PreferredPage(ShellPage):
    page_title = "preferred"
    preferred_window_size = (500, 400)

    def __init__(self):
        super().__init__()
        self.setMinimumSize(300, 240)


class HugePreferredPage(ShellPage):
    page_title = "huge"
    preferred_window_size = (5000, 4000)


def _drain(app):
    for _ in range(5):
        app.processEvents()


class WindowResizeControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _shell(self):
        shell = MainShell()
        shell.resize_controller.set_duration(0)
        # closeEvent видимого окна показывает модальный вопрос «Выйти?» —
        # прячем окно до close, чтобы не заблокировать offscreen-прогон.
        self.addCleanup(shell.close)
        self.addCleanup(shell.hide)
        shell.resize(320, 260)
        shell.show()
        _drain(self.app)
        return shell

    def test_push_animates_to_preferred_size(self):
        shell = self._shell()
        shell.set_home(SmallPage())
        _drain(self.app)
        shell.navigation.push(PreferredPage())
        _drain(self.app)
        self.assertEqual(shell.size(), QtCore.QSize(500, 400))

    def test_pop_shrinks_back_to_remembered_size(self):
        shell = self._shell()
        shell.set_home(SmallPage())
        _drain(self.app)
        home_size = QtCore.QSize(shell.size())
        shell.navigation.push(PreferredPage())
        _drain(self.app)
        shell.navigation.pop()
        _drain(self.app)
        self.assertEqual(shell.size(), home_size)

    def test_preferred_size_clamped_to_screen(self):
        shell = self._shell()
        shell.set_home(SmallPage())
        _drain(self.app)
        shell.navigation.push(HugePreferredPage())
        _drain(self.app)
        available = shell.screen().availableGeometry().size()
        self.assertLessEqual(shell.width(), available.width())
        self.assertLessEqual(shell.height(), available.height())

    def test_manual_resize_remembered_per_page(self):
        shell = self._shell()
        shell.set_home(SmallPage())
        _drain(self.app)
        shell.navigation.push(PreferredPage())
        _drain(self.app)
        # Пользователь вручную меняет размер на странице с preferred-размером.
        shell.resize(640, 420)
        _drain(self.app)
        shell.navigation.pop()
        _drain(self.app)
        shell.navigation.push(PreferredPage())
        _drain(self.app)
        self.assertEqual(shell.size(), QtCore.QSize(640, 420))

    def test_no_change_when_page_has_no_preference_and_min_fits(self):
        shell = self._shell()
        shell.set_home(SmallPage())
        _drain(self.app)
        before = QtCore.QSize(shell.size())
        shell.navigation.push(SmallPage())
        _drain(self.app)
        self.assertEqual(shell.size(), before)

    def test_real_animation_reaches_target_and_unfreezes_stack(self):
        from PyQt6 import QtTest

        shell = self._shell()
        shell.resize_controller.set_duration(50)
        shell.set_home(SmallPage())
        _drain(self.app)
        shell.navigation.push(PreferredPage())
        QtTest.QTest.qWait(400)
        self.assertEqual(shell.size(), QtCore.QSize(500, 400))
        # Заморозка минимума снята: хинт снова отражает текущую страницу.
        self.assertEqual(
            shell._stack.minimumSizeHint().width(),
            shell.navigation.current_page().minimumSize().width(),
        )

    def test_grows_to_cover_minimum_of_new_page(self):
        shell = self._shell()
        shell.set_home(SmallPage())
        _drain(self.app)

        class BigMinPage(ShellPage):
            page_title = "bigmin"

            def __init__(self):
                super().__init__()
                self.setMinimumSize(600, 500)

        shell.navigation.push(BigMinPage())
        _drain(self.app)
        self.assertGreaterEqual(shell.width(), 600)
        self.assertGreaterEqual(shell.height(), 500)

    def test_resize_grows_from_window_center(self):
        shell = self._shell()
        avail = shell.screen().availableGeometry()
        # Ставим окно так, чтобы рост 320x260 -> 500x400 не упирался в края.
        shell.move(avail.center().x() - 160, avail.center().y() - 130)
        shell.set_home(SmallPage())
        _drain(self.app)
        old_center = shell.geometry().center()
        shell.navigation.push(PreferredPage())
        _drain(self.app)
        new_center = shell.geometry().center()
        self.assertLessEqual(abs(new_center.x() - old_center.x()), 1)
        self.assertLessEqual(abs(new_center.y() - old_center.y()), 1)

    def test_growth_stops_at_screen_edge(self):
        shell = self._shell()
        avail = shell.screen().availableGeometry()
        # Окно прижато к правому краю: рост должен упереться в границу,
        # а центр — сместиться влево, не вылезая за экран.
        shell.move(avail.right() - shell.width() - 5, avail.top() + 50)
        shell.set_home(SmallPage())
        _drain(self.app)
        shell.navigation.push(PreferredPage())
        _drain(self.app)
        geo = shell.geometry()
        self.assertLessEqual(geo.right(), avail.right())
        self.assertGreaterEqual(geo.left(), avail.left())
        self.assertLessEqual(geo.bottom(), avail.bottom())
        self.assertGreaterEqual(geo.top(), avail.top())
        self.assertEqual(geo.size(), QtCore.QSize(500, 400))

    def test_growth_to_minimum_is_animated_not_instant(self):
        from PyQt6 import QtTest

        shell = self._shell()
        shell.resize_controller.set_duration(100)
        shell.set_home(SmallPage())
        _drain(self.app)
        start_width = shell.width()

        class BigMinPage(ShellPage):
            page_title = "bigmin-anim"

            def __init__(self):
                super().__init__()
                self.setMinimumSize(700, 500)

        shell.navigation.push(BigMinPage())
        # Сразу после push окно ещё не должно прыгнуть к минимуму —
        # рост происходит анимацией, а не мгновенным снапом Qt.
        self.assertLess(shell.width(), 700)
        self.assertGreaterEqual(shell.width(), start_width)
        QtTest.QTest.qWait(500)
        self.assertGreaterEqual(shell.width(), 700)
        self.assertGreaterEqual(shell.height(), 500)


if __name__ == "__main__":
    unittest.main()
