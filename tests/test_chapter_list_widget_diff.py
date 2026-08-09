import os
import unittest
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtGui, QtWidgets

from PyQt6.QtTest import QTest

from gemini_translator.ui.themes import LIGHT_DEFAULT_THEME_COLORS, build_stylesheet
from gemini_translator.ui.widgets.chapter_list_widget import (
    REORDER_BUTTON_SIZE,
    ChapterListWidget,
    ReorderArrowDelegate,
)


class _SpyItem:
    """Spy QTableWidgetItem replacement that records setter calls."""
    def __init__(self, text="", color="#000000", tooltip=""):
        self._text = text
        self._brush = QtGui.QBrush(QtGui.QColor(color))
        self._tooltip = tooltip
        self.set_text_calls = 0
        self.set_foreground_calls = 0
        self.set_tooltip_calls = 0

    def text(self):
        return self._text

    def setText(self, value):
        self._text = value
        self.set_text_calls += 1

    def foreground(self):
        return self._brush

    def setForeground(self, brush):
        self._brush = brush
        self.set_foreground_calls += 1

    def toolTip(self):
        return self._tooltip

    def setToolTip(self, value):
        self._tooltip = value
        self.set_tooltip_calls += 1

    def data(self, _role):
        return ("uuid-x", ("epub", "/tmp/x.epub", "/tmp/ch.html"))


class DiffGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _make_widget(self):
        # Minimal widget — we only exercise _update_row_status against spy items.
        w = ChapterListWidget()
        self.addCleanup(w.close)
        return w

    def _install_spy_row(self, widget, status_text, color_hex, tooltip):
        task_item = _SpyItem(text="📄 HTML: ch.html", color=color_hex)
        status_item = _SpyItem(text=status_text, color=color_hex, tooltip=tooltip)
        widget.table = MagicMock()
        widget.table.item = lambda row, col: task_item if col == 0 else status_item
        return task_item, status_item

    def test_update_row_status_noop_when_unchanged(self):
        widget = self._make_widget()
        # _get_status_display_info('in_progress', {}, payload) → ("🔄 В работе…", "#3498DB")
        expected_text = "🔄 В работе…"
        expected_color = "#3498DB"
        expected_tooltip = f"Статус: {expected_text}"
        task_spy, status_spy = self._install_spy_row(
            widget, expected_text, expected_color, expected_tooltip
        )

        widget._update_row_status(0, "in_progress", {})

        self.assertEqual(status_spy.set_text_calls, 0, "setText should be skipped when text matches")
        self.assertEqual(status_spy.set_foreground_calls, 0, "setForeground should be skipped when colour matches")
        self.assertEqual(task_spy.set_foreground_calls, 0, "task-cell foreground should be skipped too")
        self.assertEqual(status_spy.set_tooltip_calls, 0, "setToolTip should be skipped when tooltip matches")

    def test_update_row_status_applies_when_color_changes(self):
        widget = self._make_widget()
        # Current row painted as in_progress (#3498DB). New status is 'success' (#2ECC71).
        task_spy, status_spy = self._install_spy_row(
            widget, "🔄 В работе…", "#3498DB", "Статус: 🔄 В работе…"
        )

        widget._update_row_status(0, "success", {})

        self.assertEqual(status_spy.set_text_calls, 1)
        self.assertEqual(status_spy.set_foreground_calls, 1)
        self.assertEqual(task_spy.set_foreground_calls, 1)
        self.assertEqual(status_spy.set_tooltip_calls, 1)

    def test_update_row_status_applies_when_only_task_item_color_drifts(self):
        # Regression guard: status_item.foreground matches the target but
        # item_task.foreground was reset somewhere. We must still update.
        widget = self._make_widget()
        target_text = "🔄 В работе…"
        target_color = "#3498DB"
        target_tooltip = f"Статус: {target_text}"
        task_item = _SpyItem(text="📄 HTML: ch.html", color="#000000")  # drifted
        status_item = _SpyItem(text=target_text, color=target_color, tooltip=target_tooltip)
        widget.table = MagicMock()
        widget.table.item = lambda row, col: task_item if col == 0 else status_item

        widget._update_row_status(0, "in_progress", {})

        self.assertEqual(task_item.set_foreground_calls, 1, "task-cell foreground must update even if status-cell matches")

    def test_populate_row_skips_tooltip_when_same(self):
        from PyQt6.QtWidgets import QTableWidget
        widget = self._make_widget()
        widget.table = QTableWidget(1, 3)

        # _populate_row reads app.engine.session_id; stub so the attribute exists
        # and short-circuits to the "no_session" branch.
        app = QtWidgets.QApplication.instance()
        prev_engine = getattr(app, "engine", "__missing__")
        app.engine = None
        if prev_engine == "__missing__":
            self.addCleanup(lambda: delattr(app, "engine"))
        else:
            self.addCleanup(lambda: setattr(app, "engine", prev_engine))

        # First populate creates the items.
        task_payload = ("epub", "/tmp/x.epub", "/tmp/ch.html")
        task_data = (("uuid-1", task_payload), "in_progress", {})
        widget._populate_row(0, task_data)

        task_item = widget.table.item(0, 0)
        original_tooltip = task_item.toolTip()

        # Wrap setToolTip / setData to count.
        tooltip_calls = []
        data_calls = []
        orig_set_tooltip = task_item.setToolTip
        orig_set_data = task_item.setData
        task_item.setToolTip = lambda v: (tooltip_calls.append(v), orig_set_tooltip(v))[1]
        task_item.setData = lambda role, v: (data_calls.append((role, v)), orig_set_data(role, v))[1]

        # Same data again → expect zero setToolTip and zero setData calls.
        widget._populate_row(0, task_data)

        self.assertEqual(tooltip_calls, [], "tooltip setter should be skipped when value unchanged")
        # setData(UserRole+1, status) still allowed even if equal — we only gate the heavy ones.
        # But UserRole (the task tuple) must be skipped:
        userrole_calls = [c for c in data_calls if c[0] == QtCore.Qt.ItemDataRole.UserRole]
        self.assertEqual(userrole_calls, [], "UserRole setData should be skipped when value unchanged")

    def test_reorder_column_and_rows_fit_arrow_buttons(self):
        widget = self._make_widget()
        header = widget.table.horizontalHeader()

        self.assertGreaterEqual(widget.table.verticalHeader().defaultSectionSize(), 36)
        self.assertGreaterEqual(widget.table.verticalHeader().minimumSectionSize(), 36)
        self.assertEqual(header.sectionResizeMode(1), QtWidgets.QHeaderView.ResizeMode.Fixed)
        self.assertLessEqual(widget.table.columnWidth(1), 150)
        self.assertEqual(header.sectionResizeMode(2), QtWidgets.QHeaderView.ResizeMode.Fixed)
        self.assertGreaterEqual(widget.table.columnWidth(2), 96)

        cell = QtCore.QRect(0, 0, widget.table.columnWidth(2),
                            widget.table.verticalHeader().defaultSectionSize())
        up_rect, down_rect = ReorderArrowDelegate.arrow_rects(cell)

        for rect in (up_rect, down_rect):
            self.assertEqual(rect.size(), QtCore.QSize(REORDER_BUTTON_SIZE, REORDER_BUTTON_SIZE))
            self.assertTrue(cell.contains(rect), "стрелка должна помещаться в ячейку")
        self.assertFalse(up_rect.intersects(down_rect))

    def test_reorder_arrows_render_under_light_theme(self):
        """Делегат должен рендерить стрелки со стилем QPushButton#reorderButton."""
        app = QtWidgets.QApplication.instance()
        previous_stylesheet = app.styleSheet()
        app.setStyleSheet(build_stylesheet(LIGHT_DEFAULT_THEME_COLORS))
        self.addCleanup(lambda: app.setStyleSheet(previous_stylesheet))

        previous_engine = getattr(app, "engine", "__missing__")
        app.engine = None
        if previous_engine == "__missing__":
            self.addCleanup(lambda: delattr(app, "engine"))
        else:
            self.addCleanup(lambda: setattr(app, "engine", previous_engine))

        widget = self._make_widget()
        widget.resize(1200, 600)
        widget._full_redraw([
            ((QtCore.QUuid.createUuid(), ("epub", "/tmp/book.epub", "/tmp/chapter.xhtml")), "pending", {})
        ])
        widget.show()
        app.processEvents()

        delegate = widget.table.itemDelegateForColumn(2)
        self.assertIsInstance(delegate, ReorderArrowDelegate)
        self.assertIsNone(widget.table.cellWidget(0, 2), "виджеты в колонке не создаются")

        def image_bytes(pixmap):
            self.assertFalse(pixmap.isNull())
            image = pixmap.toImage()
            center = image.pixelColor(image.width() // 2, image.height() // 2)
            self.assertGreater(center.alpha(), 0, "в центре должна быть видимая кнопка")
            return bytes(image.constBits().asarray(image.sizeInBytes()))

        normal = image_bytes(delegate._pixmap('up', hovered=False, pressed=False, enabled=True))
        hovered = image_bytes(delegate._pixmap('up', hovered=True, pressed=False, enabled=True))
        # Тема задаёт QPushButton#reorderButton:hover — hover обязан выглядеть
        # иначе (WA_UnderMouse для скрытого шаблона не срабатывает, поэтому
        # состояния инъецируются принудительно; этот тест ловит регрессию).
        self.assertNotEqual(normal, hovered, "hover-состояние должно рендериться отлично от обычного")

    def test_display_text_includes_chapter_char_count_when_enabled(self):
        widget = self._make_widget()
        widget.set_chapter_char_counts({"/tmp/chapter.xhtml": 12345})
        widget.set_show_chapter_char_count(True)

        display_text, tooltip = widget._get_display_texts(("epub", "/tmp/book.epub", "/tmp/chapter.xhtml"))

        self.assertIn("12 345 симв.", display_text)
        self.assertIn("Размер главы: 12 345 симв.", tooltip)

    def test_batch_display_text_sums_chapter_char_counts_when_enabled(self):
        widget = self._make_widget()
        widget.set_chapter_char_counts({"/tmp/a.xhtml": 1000, "/tmp/b.xhtml": 2500})
        widget.set_show_chapter_char_count(True)

        display_text, tooltip = widget._get_display_texts(
            ("epub_batch", "/tmp/book.epub", ["/tmp/a.xhtml", "/tmp/b.xhtml"])
        )

        self.assertIn("3 500 симв.", display_text)
        self.assertIn("Размер пакета: 3 500 симв.", tooltip)

    def test_display_text_omits_chapter_char_count_when_disabled(self):
        widget = self._make_widget()
        widget.set_chapter_char_counts({"/tmp/chapter.xhtml": 12345})
        widget.set_show_chapter_char_count(False)

        display_text, tooltip = widget._get_display_texts(("epub", "/tmp/book.epub", "/tmp/chapter.xhtml"))

        self.assertNotIn("симв.", display_text)
        self.assertNotIn("Размер главы", tooltip)



import uuid


class FullRedrawRowReuseTests(unittest.TestCase):
    """_full_redraw на большом списке — главный источник подвисаний GUI:
    сброс setRowCount(0) уничтожал и пересоздавал 3 виджета (▲▼) на строку.
    Эти тесты закрепляют переиспользование строк и виджетов."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _stub_engine(self):
        app = QtWidgets.QApplication.instance()
        prev_engine = getattr(app, "engine", "__missing__")
        app.engine = None
        if prev_engine == "__missing__":
            self.addCleanup(lambda: delattr(app, "engine"))
        else:
            self.addCleanup(lambda: setattr(app, "engine", prev_engine))

    def _make_tasks(self, n, status="pending", details=None):
        tasks = []
        for i in range(n):
            tid = uuid.UUID(int=i + 1)
            payload = ("epub", f"/tmp/{i}.epub", f"/tmp/{i}.html")
            tasks.append(((tid, payload), status, dict(details or {})))
        return tasks

    def _make_widget_with_rows(self, n=3):
        widget = ChapterListWidget()
        self.addCleanup(widget.close)
        self._stub_engine()
        tasks = self._make_tasks(n)
        widget._full_redraw(tasks)
        return widget, tasks

    def test_full_redraw_reuses_row_items_and_creates_no_widgets(self):
        widget, tasks = self._make_widget_with_rows(4)
        items_before = [widget.table.item(r, 0) for r in range(4)]
        self.assertTrue(all(item is not None for item in items_before))

        rotated = tasks[1:] + tasks[:1]
        widget._full_redraw(rotated)

        items_after = [widget.table.item(r, 0) for r in range(4)]
        self.assertEqual(
            items_before, items_after,
            "QTableWidgetItem должны переиспользоваться при полной перерисовке"
        )
        for row in range(4):
            self.assertIsNone(
                widget.table.cellWidget(row, 2),
                "кнопки ▲▼ рисует делегат — per-row виджетов быть не должно"
            )

    def test_full_redraw_reused_rows_show_new_tasks(self):
        widget, tasks = self._make_widget_with_rows(3)

        rotated = tasks[1:] + tasks[:1]
        widget._full_redraw(rotated)

        self.assertEqual(widget.table.rowCount(), 3)
        for row, (task_tuple, _status, _details) in enumerate(rotated):
            item = widget.table.item(row, 0)
            expected_text, _ = widget._get_display_texts(task_tuple[1])
            self.assertEqual(item.text(), expected_text)
            self.assertEqual(item.data(QtCore.Qt.ItemDataRole.UserRole)[0], task_tuple[0])

    def test_full_redraw_handles_shrink_and_grow(self):
        widget, tasks = self._make_widget_with_rows(3)

        widget._full_redraw(tasks[:1])
        self.assertEqual(widget.table.rowCount(), 1)
        self.assertIsNotNone(widget.table.item(0, 0))

        grown = self._make_tasks(5)
        widget._full_redraw(grown)
        self.assertEqual(widget.table.rowCount(), 5)
        for row in range(5):
            self.assertIsNotNone(widget.table.item(row, 0), f"row {row}: нет ячейки задачи")
            self.assertIsNotNone(widget.table.item(row, 1), f"row {row}: нет ячейки статуса")

    def test_full_redraw_to_empty_clears_table(self):
        widget, _tasks = self._make_widget_with_rows(2)
        widget._full_redraw([])
        self.assertEqual(widget.table.rowCount(), 0)

    def test_populate_row_update_only_refreshes_tooltip_on_same_status_text(self):
        """Текст статуса '❌ Ошибка' не меняется, а история ошибок в tooltip —
        меняется. Текстовый гейт в _populate_row такое пропускал."""
        widget = ChapterListWidget()
        self.addCleanup(widget.close)
        self._stub_engine()

        task = self._make_tasks(1, status="error", details={"errors": {"NETWORK": 1}})[0]
        widget._full_redraw([task])
        self.assertIn("NETWORK: 1", widget.table.item(0, 1).toolTip())

        task_tuple, _status, _details = task
        widget._populate_row(0, (task_tuple, "error", {"errors": {"NETWORK": 2}}))
        self.assertIn(
            "NETWORK: 2", widget.table.item(0, 1).toolTip(),
            "tooltip с историей ошибок должен обновляться и при неизменном тексте статуса"
        )


class ReorderArrowInteractionTests(unittest.TestCase):
    """Клики по рисованным стрелкам должны вести себя как старые QPushButton:
    попадание — reorder_requested без смены выделения, промах — обычный клик."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _make_widget(self, n=3):
        widget = ChapterListWidget()
        self.addCleanup(widget.close)

        app = QtWidgets.QApplication.instance()
        prev_engine = getattr(app, "engine", "__missing__")
        app.engine = None
        if prev_engine == "__missing__":
            self.addCleanup(lambda: delattr(app, "engine"))
        else:
            self.addCleanup(lambda: setattr(app, "engine", prev_engine))

        tasks = []
        for i in range(n):
            tid = uuid.UUID(int=i + 1)
            payload = ("epub", f"/tmp/{i}.epub", f"/tmp/{i}.html")
            tasks.append(((tid, payload), "pending", {}))

        widget.resize(900, 600)
        widget._full_redraw(tasks)
        widget.show()
        app.processEvents()
        return widget, tasks

    def _arrow_center(self, widget, row, action):
        index = widget.table.model().index(row, 2)
        cell = widget.table.visualRect(index)
        up_rect, down_rect = ReorderArrowDelegate.arrow_rects(cell)
        return (up_rect if action == 'up' else down_rect).center()

    def _click(self, widget, pos):
        QTest.mouseClick(
            widget.table.viewport(),
            QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier,
            pos,
        )

    def test_click_up_arrow_emits_reorder_for_that_row(self):
        widget, tasks = self._make_widget()
        received = []
        widget.reorder_requested.connect(lambda action, ids: received.append((action, ids)))

        self._click(widget, self._arrow_center(widget, 1, 'up'))

        self.assertEqual(received, [('up', [tasks[1][0][0]])])

    def test_click_down_arrow_emits_reorder_for_that_row(self):
        widget, tasks = self._make_widget()
        received = []
        widget.reorder_requested.connect(lambda action, ids: received.append((action, ids)))

        self._click(widget, self._arrow_center(widget, 0, 'down'))

        self.assertEqual(received, [('down', [tasks[0][0][0]])])

    def test_arrow_click_does_not_change_selection(self):
        widget, _tasks = self._make_widget()
        widget.table.selectRow(0)
        selection_before = {i.row() for i in widget.table.selectionModel().selectedRows()}
        self.assertEqual(selection_before, {0})

        self._click(widget, self._arrow_center(widget, 2, 'up'))

        selection_after = {i.row() for i in widget.table.selectionModel().selectedRows()}
        self.assertEqual(
            selection_after, {0},
            "клик по стрелке должен съедаться, не трогая выделение (как QPushButton раньше)"
        )

    def test_click_beside_arrows_selects_row_without_reorder(self):
        widget, _tasks = self._make_widget()
        received = []
        widget.reorder_requested.connect(lambda action, ids: received.append((action, ids)))

        index = widget.table.model().index(1, 2)
        cell = widget.table.visualRect(index)
        pos = QtCore.QPoint(cell.left() + 2, cell.center().y())  # мимо стрелок
        up_rect, down_rect = ReorderArrowDelegate.arrow_rects(cell)
        self.assertFalse(up_rect.contains(pos) or down_rect.contains(pos))

        self._click(widget, pos)

        self.assertEqual(received, [], "промах мимо стрелок не должен запускать перемещение")
        selection = {i.row() for i in widget.table.selectionModel().selectedRows()}
        self.assertEqual(selection, {1}, "обычный клик по ячейке выделяет строку")

    def test_reorder_click_locks_table_until_animation(self):
        widget, _tasks = self._make_widget()
        self.assertTrue(widget.table.isEnabled())

        self._click(widget, self._arrow_center(widget, 1, 'down'))

        self.assertFalse(
            widget.table.isEnabled(),
            "на время перестановки таблица блокируется (защита от даблкликов)"
        )

    def test_hover_tracking_updates_delegate_state(self):
        widget, _tasks = self._make_widget()
        delegate = widget.table.itemDelegateForColumn(2)

        widget._update_arrow_hover(self._arrow_center(widget, 0, 'down'))
        self.assertEqual(delegate.hovered, (0, 'down'))

        widget._update_arrow_hover(QtCore.QPoint(5, 5))  # колонка «Задача»
        self.assertEqual(delegate.hovered, (-1, None))


class SelectiveUpdateChangedIdsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _make_widget_with_rows(self, n=3):
        from PyQt6.QtWidgets import QTableWidget
        widget = ChapterListWidget()
        self.addCleanup(widget.close)
        widget.table = QTableWidget(0, 3)

        # _populate_row (called inside _full_redraw) reads app.engine.session_id;
        # stub so the attribute exists and short-circuits to the "no_session" branch.
        app = QtWidgets.QApplication.instance()
        prev_engine = getattr(app, "engine", "__missing__")
        app.engine = None
        if prev_engine == "__missing__":
            self.addCleanup(lambda: delattr(app, "engine"))
        else:
            self.addCleanup(lambda: setattr(app, "engine", prev_engine))

        tasks = []
        for i in range(n):
            tid = uuid.UUID(int=i + 1)
            payload = ("epub", f"/tmp/{i}.epub", f"/tmp/{i}.html")
            tasks.append(((tid, payload), "pending", {}))
        widget._full_redraw(tasks)
        return widget, tasks

    def test_selective_update_with_changed_ids_skips_other_rows(self):
        widget, tasks = self._make_widget_with_rows(3)
        update_calls = []
        orig = widget._update_row_status
        widget._update_row_status = lambda row, status, details={}: update_calls.append(row) or orig(row, status, details)

        only_middle = {str(tasks[1][0][0])}
        widget._selective_update(tasks, changed_ids=only_middle)

        self.assertEqual(update_calls, [1],
                         "_selective_update must touch only the row whose task_id is in changed_ids")

    def test_selective_update_matches_uuid_via_str_cast(self):
        """Regression guard: row task ids are uuid.UUID, changed_ids is set[str]."""
        widget, tasks = self._make_widget_with_rows(2)
        update_calls = []
        orig = widget._update_row_status
        widget._update_row_status = lambda row, status, details={}: update_calls.append(row) or orig(row, status, details)

        changed = {str(tasks[0][0][0])}  # plain str; row id is UUID
        widget._selective_update(tasks, changed_ids=changed)

        self.assertEqual(update_calls, [0], "str(row UUID) must match the set[str] entry")

    def test_selective_update_with_none_changed_ids_updates_all_rows(self):
        widget, tasks = self._make_widget_with_rows(3)
        update_calls = []
        orig = widget._update_row_status
        widget._update_row_status = lambda row, status, details={}: update_calls.append(row) or orig(row, status, details)

        widget._selective_update(tasks, changed_ids=None)

        self.assertEqual(update_calls, [0, 1, 2], "None means update every row (backward compat)")


if __name__ == "__main__":
    unittest.main()
