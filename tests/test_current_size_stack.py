import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets

from gemini_translator.ui.shell import CurrentSizeStack


class CurrentSizeStackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _stack(self):
        stack = CurrentSizeStack()
        self.addCleanup(stack.deleteLater)
        small = QtWidgets.QWidget()
        small.setMinimumSize(100, 80)
        big = QtWidgets.QWidget()
        big.setMinimumSize(900, 700)
        stack.addWidget(small)
        stack.addWidget(big)
        return stack

    def test_minimum_hint_follows_current_page_only(self):
        stack = self._stack()
        stack.setCurrentIndex(0)
        self.assertEqual(stack.minimumSizeHint(), QtCore.QSize(100, 80))
        stack.setCurrentIndex(1)
        self.assertEqual(stack.minimumSizeHint(), QtCore.QSize(900, 700))
        # Возврат на маленькую страницу: большая скрытая страница не должна
        # удерживать минимум.
        stack.setCurrentIndex(0)
        self.assertEqual(stack.minimumSizeHint(), QtCore.QSize(100, 80))

    def test_size_hint_follows_current_page(self):
        stack = self._stack()
        stack.setCurrentIndex(0)
        self.assertEqual(stack.sizeHint(), stack.widget(0).sizeHint())
        stack.setCurrentIndex(1)
        self.assertEqual(stack.sizeHint(), stack.widget(1).sizeHint())

    def test_override_freezes_minimum_hint(self):
        stack = self._stack()
        stack.setCurrentIndex(1)
        stack.set_size_hint_override(QtCore.QSize(0, 0))
        self.assertEqual(stack.minimumSizeHint(), QtCore.QSize(0, 0))
        stack.set_size_hint_override(None)
        self.assertEqual(stack.minimumSizeHint(), QtCore.QSize(900, 700))

    def test_real_min_hint_covers_layout_needs_but_is_not_enforced(self):
        # Страница с setMinimumSize меньше реальных потребностей layout-а:
        # цель анимации (real_minimum_size_hint) покрывает контент, но
        # жёсткий минимум остаётся явным — иначе окно дёргается при
        # подгрузке контента и не сжимается вручную.
        stack = CurrentSizeStack()
        self.addCleanup(stack.deleteLater)
        page = QtWidgets.QWidget()
        page.setMinimumSize(100, 80)
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        content = QtWidgets.QWidget()
        content.setFixedSize(400, 300)
        layout.addWidget(content)
        stack.addWidget(page)
        stack.setCurrentIndex(0)
        real = stack.real_minimum_size_hint()
        self.assertGreaterEqual(real.width(), 400)
        self.assertGreaterEqual(real.height(), 300)
        self.assertEqual(stack.minimumSizeHint(), QtCore.QSize(100, 80))

    def test_real_min_hint_accounts_for_scroll_area_content_width(self):
        # Контент в QScrollArea почти не даёт минимума наружу, но окно
        # должно дорастать до его ширины (высота — на прокрутке).
        stack = CurrentSizeStack()
        self.addCleanup(stack.deleteLater)
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        area = QtWidgets.QScrollArea()
        area.setWidgetResizable(True)
        inner = QtWidgets.QWidget()
        inner_layout = QtWidgets.QVBoxLayout(inner)
        wide = QtWidgets.QWidget()
        wide.setFixedSize(1300, 200)
        inner_layout.addWidget(wide)
        area.setWidget(inner)
        layout.addWidget(area)
        stack.addWidget(page)
        stack.setCurrentIndex(0)
        real = stack.real_minimum_size_hint()
        self.assertGreaterEqual(real.width(), 1300)
        # Жёсткий минимум по-прежнему не форсируется содержимым.
        self.assertLess(stack.minimumSizeHint().width(), 1300)

    def test_window_can_shrink_after_leaving_big_page(self):
        window = QtWidgets.QMainWindow()
        self.addCleanup(window.close)
        stack = self._stack()
        window.setCentralWidget(stack)
        window.show()
        stack.setCurrentIndex(1)
        QtWidgets.QApplication.processEvents()
        stack.setCurrentIndex(0)
        QtWidgets.QApplication.processEvents()
        window.resize(300, 200)
        QtWidgets.QApplication.processEvents()
        self.assertLessEqual(window.width(), 300)
        self.assertLessEqual(window.height(), 200)


if __name__ == "__main__":
    unittest.main()
