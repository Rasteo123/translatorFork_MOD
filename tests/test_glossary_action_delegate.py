"""Делегат кнопок действий таблицы глоссария: без per-row виджетов.

Раньше на строку создавались 2 контейнера + 2-3 QToolButton (setCellWidget) —
~250мс и ~7500 Qt-объектов на 1500 строк. Контракт делегата:
- в колонках 3/4 нет cellWidget, состав кнопок лежит в ACTIONS_ROLE;
- клик по кнопке вызывает прежний обработчик и «съедается» (не меняет
  выделение), клик мимо кнопок ведёт себя как обычный клик по ячейке;
- тултипы совпадают с прежними текстами кнопок.
"""

import os
import sqlite3
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets
from PyQt6.QtTest import QTest

import gemini_translator.ui.dialogs.validation  # порядок импорта (циркулярный)
from gemini_translator.api import config as api_config
from gemini_translator.core.task_manager import ChapterQueueManager
from gemini_translator.utils.settings import SettingsManager
from gemini_translator.utils.glossary_tools import ContextManager
from gemini_translator.ui.dialogs.glossary import GlossaryManagerPage, PYMORPHY_AVAILABLE
from gemini_translator.ui.dialogs.glossary_dialogs.action_delegate import (
    ACTIONS_ROLE,
    GlossaryActionDelegate,
)


class _Bus(QtCore.QObject):
    event_posted = QtCore.pyqtSignal(dict)

    def subscribe(self, *a, **k): pass

    def unsubscribe(self, *a, **k): pass


def _prepare_app():
    """Атрибуты app выставляются БЕЗУСЛОВНО (конвенция тестов проекта):
    соседние модули оставляют в app свои моки/закрытые соединения."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.event_bus = _Bus()
    app.global_version = "test"
    app.main_db_connection = sqlite3.connect(
        api_config.SHARED_DB_URI, uri=True, check_same_thread=False
    )
    app.main_db_connection.row_factory = sqlite3.Row
    app.task_manager = ChapterQueueManager(event_bus=app.event_bus)
    app.settings_manager = SettingsManager(config_dir=tempfile.mkdtemp())
    app.get_settings_manager = lambda: app.settings_manager
    app.context_manager = ContextManager(tempfile.mkdtemp())
    return app


class ButtonRectsTests(unittest.TestCase):
    def test_rects_centered_with_spacing(self):
        rect = QtCore.QRect(100, 50, 100, 36)
        size = QtCore.QSize(24, 22)

        class _T:
            def devicePixelRatioF(self): return 1.0
            def style(self): return None

        delegate = GlossaryActionDelegate.__new__(GlossaryActionDelegate)
        delegate._button_size = size
        rects = delegate.button_rects(rect, 2)
        self.assertEqual(len(rects), 2)
        total = 24 * 2 + GlossaryActionDelegate.SPACING
        self.assertEqual(rects[0].x(), 100 + (100 - total) // 2)
        self.assertEqual(rects[1].x(), rects[0].x() + 24 + GlossaryActionDelegate.SPACING)
        self.assertEqual(rects[0].y(), 50 + (36 - 22) // 2)
        self.assertEqual(delegate.button_rects(rect, 0), [])


class GlossaryActionDelegateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _prepare_app()

    def _drain_deferred_deletes(self):
        # Отложенные удаления страниц не должны «взрываться» внутри
        # app.exec() соседних тестов.
        self.app.processEvents()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def _make_page(self):
        page = GlossaryManagerPage(mode='standalone', project_path=tempfile.mkdtemp())
        self.addCleanup(self._drain_deferred_deletes)
        self.addCleanup(page.deleteLater)
        page.set_glossary(
            [
                {"original": "Ли Цинь", "rus": "Li Qin", "note": ""},
                {"original": "секта звёзд", "rus": "star sect", "note": ""},
            ],
            run_analysis=False,
        )
        page.resize(1000, 600)
        page.show()
        self.app.processEvents()
        return page

    def _button_center(self, page, row, column, button_index):
        index = page.table.model().index(row, column)
        actions = GlossaryActionDelegate.actions_for_index(index)
        cell = page.table.visualRect(index)
        rects = page._action_delegate.button_rects(cell, len(actions))
        return rects[button_index].center()

    def _click(self, page, pos):
        QTest.mouseClick(
            page.table.viewport(),
            QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier,
            pos,
        )

    def test_action_columns_have_roles_and_no_widgets(self):
        page = self._make_page()
        self.assertIsNone(page.table.cellWidget(0, 3))
        self.assertIsNone(page.table.cellWidget(0, 4))
        self.assertEqual(
            GlossaryActionDelegate.actions_for_index(page.table.model().index(0, 4)),
            ('delete',),
        )
        col3 = GlossaryActionDelegate.actions_for_index(page.table.model().index(0, 3))
        if PYMORPHY_AVAILABLE:
            self.assertIn('gen', col3)
        # версии без привязанного проекта не показываются (как раньше)
        self.assertNotIn('version', col3)
        self.assertNotIn('version_active', col3)

    def test_click_delete_calls_handler_with_db_id(self):
        page = self._make_page()
        removed = []
        page._remove_single_term_by_id = lambda db_id: removed.append(db_id)

        self._click(page, self._button_center(page, 1, 4, 0))

        expected_id = page.table.item(1, 0).data(page.DB_ID_ROLE)
        self.assertEqual(removed, [expected_id])

    @unittest.skipUnless(PYMORPHY_AVAILABLE, "pymorphy не установлен")
    def test_click_gen_calls_note_handler_for_row(self):
        page = self._make_page()
        calls = []
        page._on_generate_note_in_main_table_clicked = lambda row: calls.append(row)

        self._click(page, self._button_center(page, 0, 3, 0))

        self.assertEqual(calls, [0])

    def test_click_beside_buttons_does_not_trigger_handlers(self):
        page = self._make_page()
        removed = []
        page._remove_single_term_by_id = lambda db_id: removed.append(db_id)

        index = page.table.model().index(0, 4)
        cell = page.table.visualRect(index)
        corner = QtCore.QPoint(cell.x() + 1, cell.y() + 1)  # мимо кнопки
        self._click(page, corner)

        self.assertEqual(removed, [])

    def test_tooltips_match_old_button_texts(self):
        page = self._make_page()
        self.assertEqual(page._row_action_tooltip(0, 'gen'), "Сгенерировать примечание")
        self.assertEqual(
            page._row_action_tooltip(0, 'version'),
            "Создать версии (переопределения для глав)",
        )
        self.assertEqual(
            page._row_action_tooltip(0, 'version_active'),
            "Управление версиями (ЕСТЬ АКТИВНЫЕ ПРАВИЛА)",
        )
        self.assertEqual(
            page._row_action_tooltip(0, 'delete'), "Удалить термин 'Ли Цинь'"
        )


if __name__ == "__main__":
    unittest.main()
