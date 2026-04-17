# gemini_translator/ui/widgets/chapter_list_widget.py

import os
from PyQt6 import QtWidgets, QtCore, QtGui
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QGridLayout, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QHBoxLayout, QStyle, QMessageBox, QDialog,
    QLabel, QListWidget, QDialogButtonBox
)
from PyQt6.QtCore import pyqtSignal, Qt
import uuid # Добавляем импорт

try:
    import Levenshtein
    LEVENSHTEIN_AVAILABLE = True
except ImportError:
    LEVENSHTEIN_AVAILABLE = False


class BatchChapterOrderDialog(QDialog):
    """Небольшой редактор порядка глав внутри одного пакета."""

    def __init__(self, chapters: list[str], preview_callback=None, parent=None):
        super().__init__(parent)
        self._preview_callback = preview_callback
        self.setWindowTitle("Порядок глав в пакете")
        self.setMinimumSize(620, 420)

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Перетаскивайте главы мышью или используйте кнопки справа, "
            "чтобы изменить порядок обработки внутри пакета."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        content_layout = QHBoxLayout()

        self.list_widget = QListWidget(self)
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list_widget.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list_widget.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list_widget.setAlternatingRowColors(True)
        self.list_widget.itemDoubleClicked.connect(self._preview_item)
        for chapter in chapters:
            item = QtWidgets.QListWidgetItem(os.path.basename(str(chapter)))
            item.setData(Qt.ItemDataRole.UserRole, str(chapter))
            item.setToolTip(str(chapter))
            self.list_widget.addItem(item)
        content_layout.addWidget(self.list_widget, 1)

        buttons_layout = QVBoxLayout()
        btn_top = QPushButton("⏫ Вверх")
        btn_top.clicked.connect(lambda: self._move_selected_to_edge(move_to_top=True))
        buttons_layout.addWidget(btn_top)

        btn_up = QPushButton("🔼 Выше")
        btn_up.clicked.connect(lambda: self._move_selected_by_step(-1))
        buttons_layout.addWidget(btn_up)

        btn_down = QPushButton("🔽 Ниже")
        btn_down.clicked.connect(lambda: self._move_selected_by_step(1))
        buttons_layout.addWidget(btn_down)

        btn_bottom = QPushButton("⏬ Вниз")
        btn_bottom.clicked.connect(lambda: self._move_selected_to_edge(move_to_top=False))
        buttons_layout.addWidget(btn_bottom)
        buttons_layout.addStretch()

        content_layout.addLayout(buttons_layout)
        layout.addLayout(content_layout, 1)

        dialog_buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self
        )
        dialog_buttons.accepted.connect(self.accept)
        dialog_buttons.rejected.connect(self.reject)
        layout.addWidget(dialog_buttons)

    def _selected_rows(self) -> list[int]:
        return sorted({self.list_widget.row(item) for item in self.list_widget.selectedItems()})

    def _reselect_rows(self, rows: list[int]):
        self.list_widget.clearSelection()
        for row in rows:
            item = self.list_widget.item(row)
            if item:
                item.setSelected(True)
        if rows:
            self.list_widget.setCurrentRow(rows[0])

    def _move_selected_by_step(self, step: int):
        rows = self._selected_rows()
        if not rows:
            return

        if step < 0:
            for row in rows:
                if row == 0 or (row - 1) in rows:
                    continue
                item = self.list_widget.takeItem(row)
                self.list_widget.insertItem(row - 1, item)
            self._reselect_rows([max(0, row - 1) for row in rows])
            return

        original_rows = rows[:]
        for row in reversed(rows):
            if row >= self.list_widget.count() - 1 or (row + 1) in rows:
                continue
            item = self.list_widget.takeItem(row)
            self.list_widget.insertItem(row + 1, item)
        self._reselect_rows([min(self.list_widget.count() - 1, row + 1) for row in original_rows])

    def _move_selected_to_edge(self, move_to_top: bool):
        rows = self._selected_rows()
        if not rows:
            return

        items = []
        for offset, row in enumerate(rows):
            items.append(self.list_widget.takeItem(row - offset))

        if move_to_top:
            for index, item in enumerate(items):
                self.list_widget.insertItem(index, item)
            self._reselect_rows(list(range(len(items))))
            return

        start_row = self.list_widget.count()
        for item in items:
            self.list_widget.addItem(item)
        self._reselect_rows(list(range(start_row, self.list_widget.count())))

    def get_chapters(self) -> list[str]:
        chapters = []
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            chapters.append(str(item.data(Qt.ItemDataRole.UserRole)))
        return chapters

    def _preview_item(self, item):
        if not item or not callable(self._preview_callback):
            return
        self._preview_callback(str(item.data(Qt.ItemDataRole.UserRole)))
    
class ChapterListWidget(QWidget):
    """
    Виджет для отображения списка глав/заданий для перевода и управления этим списком.
    """
    # Сигналы для родительского окна
    clear_list_requested = pyqtSignal()
    remove_selected_requested = pyqtSignal(list)
    duplicate_requested = pyqtSignal(list)
    filter_untranslated_requested = pyqtSignal()
    select_failed_requested = pyqtSignal()
    reorder_requested = pyqtSignal(str, list)
    copy_originals_requested = pyqtSignal()
    reanimate_requested = pyqtSignal(list)
    split_batch_requested = pyqtSignal(list)
    batch_chapters_reorder_requested = pyqtSignal(object, list)
    chapter_preview_requested = pyqtSignal(str, str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_session_active = False
        self._init_ui()
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    
    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
    
        buttons_panel = QWidget()
        buttons_grid = QGridLayout(buttons_panel)
        buttons_grid.setSpacing(5)
        buttons_grid.setContentsMargins(0, 5, 0, 0)
    
        
        list_actions_layout = QHBoxLayout()
        list_actions_layout.setSpacing(5)
        
        self.btn_duplicate = QPushButton("➕ Дублировать")
        self.btn_duplicate.setToolTip("Создать копии выделенных задач в списке")
        self.btn_duplicate.clicked.connect(self._emit_duplicate_request)
    
        self.btn_remove = QPushButton("🗑️ Удалить")
        self.btn_remove.setToolTip("Удалить выделенные задачи из списка")
        self.btn_remove.clicked.connect(self._on_remove_selected)
    
        self.btn_copy_originals = QPushButton("📋 Скопировать оригиналы")
        self.btn_copy_originals.setToolTip("Копирует оригинальное содержимое выбранных глав как перевод (для ручной доработки).")
        self.btn_copy_originals.clicked.connect(self._emit_copy_originals_request)
    
        self.btn_reanimate = QPushButton("🩹 Реанимировать")
        self.btn_reanimate.setToolTip("Для 'проваленных' задач: сбросить ошибки и вернуть в очередь.\nДля остальных: сбросить историю ошибок.")
        self.btn_reanimate.clicked.connect(self._emit_reanimate_request)

        self.btn_split_batch = QPushButton("🪓 Разбить пакет")
        self.btn_split_batch.setToolTip(
            "Разбить выбранные пакеты на отдельные главы.\n"
            "Работает и во время активного перевода.\n"
            "Пакеты со статусом ошибки будут возвращены в общую очередь."
        )
        self.btn_split_batch.clicked.connect(self._emit_split_batch_request)

        self.btn_edit_batch_order = QPushButton("↕ Главы в пакете")
        self.btn_edit_batch_order.setToolTip(
            "Изменить порядок глав внутри выбранного пакета.\n"
            "Работает для одного выбранного пакета, который еще не взят в работу."
        )
        self.btn_edit_batch_order.clicked.connect(self._open_batch_reorder_dialog)
    
        list_actions_layout.addWidget(self.btn_duplicate)
        list_actions_layout.addWidget(self.btn_remove)
        list_actions_layout.addWidget(self.btn_copy_originals)
        list_actions_layout.addWidget(self.btn_reanimate)
        list_actions_layout.addWidget(self.btn_split_batch)
        list_actions_layout.addWidget(self.btn_edit_batch_order)
        
        buttons_grid.addLayout(list_actions_layout, 0, 1)
    
        reorder_layout = QHBoxLayout()
        reorder_layout.setSpacing(5)
        
        btn_move_top = QPushButton("⏫ Наверх")
        btn_move_top.setToolTip("Переместить выделенные задачи в самый верх списка")
        btn_move_top.clicked.connect(lambda: self._emit_reorder_request('top'))
        
        btn_move_up = QPushButton("🔼 Вверх")
        btn_move_up.setToolTip("Переместить выделенные задачи на одну позицию вверх")
        btn_move_up.clicked.connect(lambda: self._emit_reorder_request('up'))
        
        btn_move_down = QPushButton("🔽 Вниз")
        btn_move_down.setToolTip("Переместить выделенные задачи на одну позицию вниз")
        btn_move_down.clicked.connect(lambda: self._emit_reorder_request('down'))
        
        btn_move_bottom = QPushButton("⏬ Подниз")
        btn_move_bottom.setToolTip("Переместить выделенные задачи в самый низ списка")
        btn_move_bottom.clicked.connect(lambda: self._emit_reorder_request('bottom'))
        
        reorder_layout.addWidget(btn_move_top)
        reorder_layout.addWidget(btn_move_up)
        reorder_layout.addWidget(btn_move_down)
        reorder_layout.addWidget(btn_move_bottom)
        
        buttons_grid.addLayout(reorder_layout, 0, 4)
    
        buttons_grid.setColumnStretch(3, 1)
        main_layout.addWidget(buttons_panel)
    
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Задача", "Статус", "Порядок"])
        
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    
        self.table.itemSelectionChanged.connect(self._on_selection_changed_for_buttons)
        self.table.itemDoubleClicked.connect(self._handle_item_double_click)
    
        
        main_layout.addWidget(self.table, 1)
            
    def _create_reorder_cell_widget(self, row):
        """
        Создает виджет с "умными" кнопками, которые определяют свою строку
        в момент нажатия.
        """
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(4)
        
        btn_up = QPushButton(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowUp), "")
        btn_up.setFixedSize(24, 24)
        # --- ИЗМЕНЕНИЕ: Используем 'self' для поиска строки ---
        btn_up.clicked.connect(lambda: self._emit_reorder_from_button('up', self.sender()))
        
        btn_down = QPushButton(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown), "")
        btn_down.setFixedSize(24, 24)
        # --- ИЗМЕНЕНИЕ: Используем 'self' для поиска строки ---
        btn_down.clicked.connect(lambda: self._emit_reorder_from_button('down', self.sender()))
    
        layout.addWidget(btn_up)
        layout.addWidget(btn_down)
        return widget

    def _emit_reorder_from_button(self, action, button_widget):
        """
        Запускает атомарную операцию перемещения: блокирует UI, обновляет данные,
        анимирует скролл и разблокирует UI после завершения.
        """
        # === ШАГ 1: Немедленная блокировка ===
        # Если таблица уже заблокирована, игнорируем повторные клики.
        if not self.table.isEnabled():
            return
            
        self.table.setEnabled(False) # Делаем таблицу неактивной

        for row in range(self.table.rowCount()):
            if self.table.cellWidget(row, 2) == button_widget.parent():
                item = self.table.item(row, 0)
                if item and item.data(QtCore.Qt.ItemDataRole.UserRole):
                    task_id = item.data(QtCore.Qt.ItemDataRole.UserRole)[0]
                    
                    scrollbar = self.table.verticalScrollBar()
                    initial_scroll_value = scrollbar.value()
                    
                    scroll_delta_in_rows = -1 if action == 'up' else 1

                    # === ШАГ 2: Запуск обновления данных ===
                    self.reorder_requested.emit(action, [task_id])

                    # === ШАГ 3: Планирование анимации и разблокировки ===
                    # Мы ждем, пока UI обновится, и ТОЛЬКО ПОТОМ запускаем анимацию.
                    QtCore.QTimer.singleShot(
                        0, lambda: self._compensate_scroll(initial_scroll_value, scroll_delta_in_rows)
                    )
                    
                break
    
    def _compensate_scroll(self, initial_value, delta_in_rows):
        """
        Применяет смещение к скроллбару и разблокирует таблицу ПОСЛЕ завершения анимации.
        """
        scrollbar = self.table.verticalScrollBar()
        
        if hasattr(self, 'scroll_animation') and self.scroll_animation.state() == QtCore.QPropertyAnimation.State.Running:
            self.scroll_animation.stop()

        target_value = initial_value + delta_in_rows
        target_value = max(scrollbar.minimum(), min(target_value, scrollbar.maximum()))
        
        self.scroll_animation = QtCore.QPropertyAnimation(scrollbar, b"value")
        self.scroll_animation.setDuration(120) 
        self.scroll_animation.setStartValue(scrollbar.value())
        self.scroll_animation.setEndValue(target_value)
        self.scroll_animation.setEasingCurve(QtCore.QEasingCurve.Type.InOutQuad)
        
        # === ШАГ 4: Разблокировка после анимации ===
        # Мы подключаем разблокировку к сигналу finished() анимации.
        # Если анимация уже завершена, старый коннект удалится при создании новой.
        try:
            self.scroll_animation.finished.disconnect()
        except (TypeError, RuntimeError):
            pass # Если коннекта не было, ничего страшного
            
        self.scroll_animation.finished.connect(lambda: self.table.setEnabled(True))
        
        self.scroll_animation.start()
        
        # На случай, если анимация не запустится (start_value == end_value)
        if scrollbar.value() == target_value:
             self.table.setEnabled(True)
    
    def _on_selection_changed_for_buttons(self):
        """Обновляет состояние кнопок, зависящих от выделения."""
        has_selection = self.table.selectionModel().hasSelection()
        _, has_chunks = self._get_selected_ids_and_check_chunks()
        has_batches = self._selection_has_batch_tasks()
        single_batch_task = self._get_single_selected_batch_task()
        is_session_active = self._is_session_active
    
        can_duplicate = has_selection and not has_chunks
        self.btn_duplicate.setEnabled(can_duplicate)
    
        can_remove = not is_session_active and has_selection and not has_chunks
        self.btn_remove.setEnabled(can_remove)
        
        can_copy = not is_session_active and has_selection
        self.btn_copy_originals.setEnabled(can_copy)
        
        # Кнопка реанимации работает всегда, если что-то выделено
        self.btn_reanimate.setEnabled(has_selection)
        self.btn_split_batch.setEnabled(has_selection and has_batches and not has_chunks)
        self.btn_edit_batch_order.setEnabled(single_batch_task is not None and not has_chunks)
    
    def _get_selected_ids_and_check_chunks(self):
        """Вспомогательный метод для получения выделения и проверки на чанки."""
        selected_rows = self.table.selectionModel().selectedRows()
        selected_ids = []
        has_chunks = False
        
        # Получаем ID в порядке их отображения в таблице
        indices = sorted([index.row() for index in selected_rows])
        for i in indices:
            item = self.table.item(i, 0)
            # --- ИСПРАВЛЕНИЕ: Убедимся, что UserRole содержит кортеж ---
            if item and item.data(QtCore.Qt.ItemDataRole.UserRole) and isinstance(item.data(QtCore.Qt.ItemDataRole.UserRole), tuple):
                task_tuple_with_uuid = item.data(QtCore.Qt.ItemDataRole.UserRole)
                task_id, task_payload = task_tuple_with_uuid[0], task_tuple_with_uuid[1] # Распаковываем
                selected_ids.append(task_id)
                if task_payload and task_payload[0] == 'epub_chunk':
                    has_chunks = True
        return selected_ids, has_chunks

    def _extract_task_tuple_from_item(self, item):
        if not item:
            return None
        task_tuple_with_uuid = item.data(QtCore.Qt.ItemDataRole.UserRole)
        if not isinstance(task_tuple_with_uuid, tuple) or len(task_tuple_with_uuid) < 2:
            return None
        return task_tuple_with_uuid

    def _extract_task_id_from_row(self, row):
        task_tuple_with_uuid = self._extract_task_tuple_from_item(self.table.item(row, 0))
        if not task_tuple_with_uuid:
            return None
        return task_tuple_with_uuid[0]

    def _get_single_selected_batch_task(self):
        selected_rows = self.table.selectionModel().selectedRows()
        if len(selected_rows) != 1:
            return None

        row = selected_rows[0].row()
        item = self.table.item(row, 0)
        task_tuple_with_uuid = self._extract_task_tuple_from_item(item)
        if not task_tuple_with_uuid:
            return None

        task_id, task_payload = task_tuple_with_uuid
        if not task_payload or task_payload[0] != 'epub_batch':
            return None

        status = item.data(Qt.ItemDataRole.UserRole + 1)
        return {
            'task_id': task_id,
            'task_payload': task_payload,
            'status': status,
        }

    def _normalize_tasks_data(self, tasks_data):
        normalized_items = []
        for item in tasks_data or []:
            if not isinstance(item, tuple) or len(item) < 3:
                continue
            task_tuple_with_uuid, status, details = item
            if not isinstance(task_tuple_with_uuid, tuple) or len(task_tuple_with_uuid) < 2:
                continue
            normalized_items.append((task_tuple_with_uuid[:2], status, details if isinstance(details, dict) else {}))
        return normalized_items
    
    def _emit_reorder_request(self, action):
        """Собирает ID выделенных задач и испускает сигнал."""
        selected_ids, _ = self._get_selected_ids_and_check_chunks()
        if selected_ids:
            self.reorder_requested.emit(action, selected_ids)

    def _open_batch_reorder_dialog(self):
        batch_task = self._get_single_selected_batch_task()
        if not batch_task:
            return

        if batch_task['status'] not in ('pending', 'held', 'failed'):
            QMessageBox.information(
                self,
                "Пакет недоступен",
                "Менять порядок глав можно только у пакетов в ожидании, заморозке или ошибке."
            )
            return

        chapters = list(batch_task['task_payload'][2]) if len(batch_task['task_payload']) > 2 else []
        if len(chapters) < 2:
            QMessageBox.information(
                self,
                "Нечего перемещать",
                "В этом пакете меньше двух глав, порядок менять не требуется."
            )
            return

        dialog = BatchChapterOrderDialog(
            chapters,
            preview_callback=lambda chapter_path, epub_path=batch_task['task_payload'][1]: self.chapter_preview_requested.emit(str(epub_path), str(chapter_path)),
            parent=self
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        new_order = dialog.get_chapters()
        if new_order == chapters:
            return

        self.batch_chapters_reorder_requested.emit(batch_task['task_id'], new_order)

    def _emit_preview_request_for_payload(self, task_payload):
        if not task_payload:
            return

        task_type = task_payload[0]
        if task_type not in ('epub', 'epub_chunk'):
            return

        epub_path = str(task_payload[1]) if len(task_payload) > 1 else ""
        chapter_path = str(task_payload[2]) if len(task_payload) > 2 else ""
        if chapter_path:
            self.chapter_preview_requested.emit(epub_path, chapter_path)

    def _handle_item_double_click(self, item):
        task_tuple_with_uuid = self._extract_task_tuple_from_item(item if isinstance(item, QTableWidgetItem) else None)
        if not task_tuple_with_uuid:
            return

        task_payload = task_tuple_with_uuid[1]
        if not task_payload:
            return

        if task_payload[0] == 'epub_batch':
            self._open_batch_reorder_dialog()
            return

        self._emit_preview_request_for_payload(task_payload)
    
    def _emit_duplicate_request(self):
        selected_ids, has_chunks = self._get_selected_ids_and_check_chunks()
        if selected_ids and not has_chunks:
            self.duplicate_requested.emit(selected_ids)
        elif has_chunks:
            QMessageBox.warning(self.parent(), "Действие запрещено", "Дублирование отдельных частей (чанков) главы не поддерживается.")
    
    
    def _on_remove_selected(self):
        selected_ids, has_chunks = self._get_selected_ids_and_check_chunks()
        if selected_ids and not has_chunks:
            self.remove_selected_requested.emit(selected_ids)
        elif has_chunks:
            QMessageBox.warning(self.parent(), "Действие запрещено", "Удаление отдельных частей (чанков) главы не поддерживается.")
    
    def _emit_copy_originals_request(self):
        selected_ids, _ = self._get_selected_ids_and_check_chunks()
        if selected_ids:
            self.copy_originals_requested.emit()
    
    def _emit_reanimate_request(self):
        selected_ids, _ = self._get_selected_ids_and_check_chunks()
        if selected_ids:
            self.reanimate_requested.emit(selected_ids)

    def _emit_split_batch_request(self):
        selected_ids, has_chunks = self._get_selected_ids_and_check_chunks()
        if not selected_ids:
            return
        if has_chunks:
            QtWidgets.QMessageBox.warning(
                self.parent(),
                "Действие запрещено",
                "Нельзя разбивать выбор, если в нем есть отдельные чанки главы."
            )
            return
        if not self._selection_has_batch_tasks():
            return
        self.split_batch_requested.emit(selected_ids)
    
    
    def _restore_selection(self, ids_to_select):
        """Находит строки с указанными ID и выделяет их."""
        if not ids_to_select:
            return
    
        selection = QtCore.QItemSelection()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.data(QtCore.Qt.ItemDataRole.UserRole):
                task_id = item.data(QtCore.Qt.ItemDataRole.UserRole)[0]
                if task_id in ids_to_select:
                    start_index = self.table.model().index(row, 0)
                    end_index = self.table.model().index(row, self.table.columnCount() - 1)
                    selection.select(start_index, end_index)
        
        if not selection.isEmpty():
            QtCore.QTimer.singleShot(0, lambda: self.table.selectionModel().select(
                selection, QtCore.QItemSelectionModel.SelectionFlag.Select | QtCore.QItemSelectionModel.SelectionFlag.Rows
            ))
        
    # ----------------------------------------------------
    # Публичные методы (API виджета)
    # ----------------------------------------------------

    def update_list(self, tasks_data):
        """
        "Умная" перерисовка с усиленной блокировкой сигналов для предотвращения
        артефактов выделения при быстрых операциях.
        """
        # Шаг 1: Запоминаем текущее выделение до начала всех операций.
        tasks_data = self._normalize_tasks_data(tasks_data)
        selected_ids_before_update, _ = self._get_selected_ids_and_check_chunks()
        # Шаг 2: Полностью блокируем сигналы таблицы, чтобы она не реагировала на мышь.
        self.table.blockSignals(True)
        try:
            # === НАЧАЛО БЛОКА ИЗМЕНЕНИЙ ===
            # Временно отключаем возможность выбора ячеек, чтобы предотвратить
            # любые побочные эффекты от событий мыши во время перерисовки.
            original_selection_mode = self.table.selectionMode()
            self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
            # === КОНЕЦ БЛОКА ИЗМЕНЕНИЙ ===
            # --- Далее идет существующая логика "умного" обновления ---
            new_task_ids = [item[0][0] for item in tasks_data]
            current_task_ids = [self.table.item(row, 0).data(QtCore.Qt.ItemDataRole.UserRole)[0] 
                                for row in range(self.table.rowCount()) if self.table.item(row, 0)]
            if current_task_ids == new_task_ids:
                self._selective_update(tasks_data)
            else:
                # Определяем, нужна ли полная перерисовка или "хирургическое" вмешательство
                # (здесь можно оставить вашу логику с Levenshtein или порогом)
                self._surgical_update(tasks_data)
        finally:
            # Шаг 3: Восстанавливаем все в обратном порядке в блоке finally,
            # чтобы это выполнилось даже в случае ошибки.
            
            # === НАЧАЛО БЛОКА ИЗМЕНЕНИЙ ===
            # Возвращаем исходный режим выделения
            self.table.setSelectionMode(original_selection_mode)
            # === КОНЕЦ БЛОКА ИЗМЕНЕНИЙ ===

            # Сначала разблокируем сигналы
            self.table.blockSignals(False) 
            
            # И только потом, когда таблица снова "жива", восстанавливаем выделение.
            # Оборачиваем в QTimer.singleShot, чтобы дать Qt один цикл на "осознание"
            # изменений перед применением выделения. Это повышает надежность.
            QtCore.QTimer.singleShot(0, lambda: self._restore_selection(selected_ids_before_update))
    
    def _surgical_update(self, tasks_data):
        """Применяет точечные изменения к таблице, чтобы она соответствовала tasks_data."""
        self.table.blockSignals(True)
    
        new_data_map = {item[0][0]: item for item in tasks_data}
        new_task_ids = [item[0][0] for item in tasks_data]
    
        old_ids = [self.table.item(row, 0).data(QtCore.Qt.ItemDataRole.UserRole)[0] 
                   for row in range(self.table.rowCount()) if self.table.item(row, 0) and self.table.item(row, 0).data(QtCore.Qt.ItemDataRole.UserRole)]
    
        n, m = len(old_ids), len(new_task_ids)
        dp = [[0] * (m + 1) for _ in range(n + 1)]
        for i in range(n):
            for j in range(m):
                if old_ids[i] == new_task_ids[j]:
                    dp[i+1][j+1] = dp[i][j] + 1
                else:
                    dp[i+1][j+1] = max(dp[i+1][j], dp[i][j+1])
    
        i, j = n, m
        ops = []
        while i > 0 or j > 0:
            if i > 0 and j > 0 and old_ids[i-1] == new_task_ids[j-1]:
                ops.append(('keep', i-1, j-1))
                i -= 1; j -= 1
            elif j > 0 and (i == 0 or dp[i][j-1] >= dp[i-1][j]):
                ops.append(('insert', -1, j-1))
                j -= 1
            elif i > 0 and (j == 0 or dp[i][j-1] < dp[i-1][j]):
                ops.append(('delete', i-1, -1))
                i -= 1
        
        ops.reverse()
        
        # --- ЭТАП СОРТИРОВКИ (TRIAGE) ---
        # Подсчитываем количество структурных изменений (вставки и удаления).
        # Обновления (keep) дешевые, их не считаем критичными.
        structural_changes = sum(1 for op in ops if op[0] != 'keep')
        total_items = len(new_task_ids)
        # Эвристика: Если структурных изменений более 20% от общего объема данных,
        # или если самих изменений просто слишком много (например, > 300),
        # то точечная хирургия становится дороже полной перерисовки.
        # Коэффициенты 0.2 (20%) и 300 можно подстроить под конкретное железо.
        is_chaos_too_high = (total_items > 0 and (structural_changes / total_items) > 0.2) or structural_changes > 300

        if is_chaos_too_high:
            # Отменяем блокировку, так как full_redraw управляет ей сам 
            self.table.blockSignals(False) 
            self._full_redraw(tasks_data)
            return

        # --- ХИРУРГИЧЕСКОЕ ВМЕШАТЕЛЬСТВО ---
        current_row = 0
        for op, old_idx, new_idx in ops:
            if op == 'delete':
                self.table.removeRow(current_row)
            elif op == 'insert':
                task_item_data = new_data_map[new_task_ids[new_idx]]
                self.table.insertRow(current_row)
                self._populate_row(current_row, task_item_data)
                current_row += 1
            elif op == 'keep':
                task_item_data = new_data_map[new_task_ids[new_idx]]
                # Оптимизация: обновляем данные, только если они реально изменились
                # (предполагается, что _populate_row внутри достаточно умён, 
                # или update_only=True делает минимум работы)
                self._populate_row(current_row, task_item_data, update_only=True)
                current_row += 1
        
        self.table.blockSignals(False)
    
    def _populate_row(self, row, task_item_data, update_only=False):
        """Заполняет или обновляет одну строку таблицы."""
        task_tuple_with_uuid, status, details = task_item_data
        task_id, task_payload = task_tuple_with_uuid

        # --- Логика для UserRole остается ---
        app = QtWidgets.QApplication.instance()
        session_id_for_ui = app.engine.session_id if app.engine and app.engine.session_id else "no_session"
        payload_list_for_ui_role = list(task_payload) if task_payload else []
        if len(payload_list_for_ui_role) > 1 and hasattr(payload_list_for_ui_role[1], 'getvalue'):
            payload_list_for_ui_role[1] = session_id_for_ui
        task_tuple_for_ui_role = (task_id, tuple(payload_list_for_ui_role))
        
        display_text, tooltip_text = self._get_display_texts(task_payload)

        # --- ОБНОВЛЕНИЕ/СОЗДАНИЕ ЯЧЕЙКИ ЗАДАЧИ (СТОЛБЕЦ 0) ---
        item_task = self.table.item(row, 0)
        if not item_task: # Создаем, только если не существует
            item_task = QTableWidgetItem(display_text)
            self.table.setItem(row, 0, item_task)
        elif item_task.text() != display_text: # Обновляем, только если текст изменился
            item_task.setText(display_text)
        
        item_task.setToolTip(tooltip_text)
        item_task.setData(QtCore.Qt.ItemDataRole.UserRole, task_tuple_for_ui_role)
        item_task.setData(Qt.ItemDataRole.UserRole + 1, status)

        # --- ОБНОВЛЕНИЕ/СОЗДАНИЕ ЯЧЕЙКИ СТАТУСА (СТОЛБЕЦ 1) ---
        status_item = self.table.item(row, 1)
        if not status_item: # Создаем, только если не существует
            status_item = QTableWidgetItem()
            self.table.setItem(row, 1, status_item)

        # --- ГЛАВНОЕ ИЗМЕНЕНИЕ: "УМНАЯ" ПРОВЕРКА СТАТУСА ---
        # 1. Получаем текст, который ДОЛЖЕН БЫТЬ
        new_display_text, _ = self._get_status_display_info(status, details, task_payload)
        
        # 2. Сравниваем с тем, что ЕСТЬ СЕЙЧАС
        if status_item.text() != new_display_text:
            # 3. И только если они отличаются, вызываем "тяжелую" перерисовку
            self._update_row_status(row, status, details)
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---
    
        if not update_only:
            # Виджет с кнопками создаем только при полной вставке строки
            self.table.setCellWidget(row, 2, self._create_reorder_cell_widget(row))
    
    def _full_redraw(self, tasks_data):
        """Выполняет полную перерисовку таблицы с нуля, используя _populate_row."""
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        if not tasks_data:
            self.table.blockSignals(False)
            return
    
        self.table.setRowCount(len(tasks_data))
        for i, task_item in enumerate(tasks_data):
            self._populate_row(i, task_item)
            
    
        self.table.blockSignals(False)


    def _selective_update(self, tasks_data):
        """Выполняет точечное обновление статусов в существующей таблице."""
        self.table.blockSignals(True)
        for i, (task_tuple_with_uuid, status, details) in enumerate(tasks_data):
            self._update_row_status(i, status, details)
        self.table.blockSignals(False)
    
    def _selection_has_batch_tasks(self):
        selected_rows = self.table.selectionModel().selectedRows()
        for index in selected_rows:
            task_tuple_with_uuid = self._extract_task_tuple_from_item(self.table.item(index.row(), 0))
            if not task_tuple_with_uuid:
                continue
            task_payload = task_tuple_with_uuid[1]
            if task_payload and task_payload[0] == 'epub_batch':
                return True
        return False

    def _get_display_texts(self, task_payload):
        """Вспомогательный метод для генерации текста ячейки."""
        display_text = "Потерянная задача..."
        tooltip_text = ""
        if not task_payload:
            return display_text, tooltip_text

        task_type = task_payload[0]
        
        try:
            # --- Пакеты (Glossary и EPUB) ---
            if task_type in ("glossary_batch_task", "epub_batch"):
                content_data = task_payload[2] if len(task_payload) > 2 else []
                
                prefix = "✨ Пакет глоссария" if task_type == "glossary_batch_task" else "📦 Пакет"
                display_text = f"{prefix} из {len(content_data)} глав"
                if content_data:
                    display_text += f" (начиная с '{os.path.basename(str(content_data[0]))}')"
                
                tooltip_header = "Главы для генерации глоссария:\n" if task_type == "glossary_batch_task" else "Содержимое пакета:\n"
                tooltip_text = tooltip_header + "\n".join(map(str, content_data))

            # --- Одиночная глава EPUB ---
            elif task_type == "epub":
                epub_path = str(task_payload[1]) if len(task_payload) > 1 else "???"
                chapter_path = str(task_payload[2]) if len(task_payload) > 2 else "???"
                
                original_filename = os.path.basename(epub_path)
                display_text = f"📄 HTML: {os.path.basename(chapter_path)}"
                tooltip_text = f"EPUB: {original_filename}\nHTML: {chapter_path}"

            # --- Часть главы (чанк) EPUB ---
            elif task_type == "epub_chunk":
                epub_path = str(task_payload[1]) if len(task_payload) > 1 else "???"
                chapter_path = str(task_payload[2]) if len(task_payload) > 2 else "???"
                chunk_index = task_payload[4] if len(task_payload) > 4 else -1
                total_chunks = task_payload[5] if len(task_payload) > 5 else -1
                
                original_filename = os.path.basename(epub_path)
                display_text = f"쪼 ЧАНК {chunk_index + 1}/{total_chunks} из '{os.path.basename(chapter_path)}'"
                tooltip_text = f"EPUB: {original_filename}\nHTML: {chapter_path}"

            # --- Прямой перевод текста (НОВЫЙ БЛОК) ---
            elif task_type == "raw_text_translation":
                # task_payload[3] - это title
                title = task_payload[3] if len(task_payload) > 3 and task_payload[3] else "Прямой перевод"
                # task_payload[2] - это сам текст
                text_content = task_payload[2] if len(task_payload) > 2 else ""
                
                display_text = f"✨ {title}"
                # Показываем первые 100 символов в подсказке
                tooltip_text = text_content[:100] + ('...' if len(text_content) > 100 else '')

            # --- Обработчик для всех остальных, неизвестных типов ---
            else:
                display_text = f"Задача типа: '{task_type}'"
                tooltip_text = str(task_payload)

        except (IndexError, TypeError) as e:
            # Защитный блок на случай, если payload придет поврежденным
            display_text = f"Ошибка отображения задачи ({task_type})"
            tooltip_text = f"Некорректный payload: {task_payload}\nОшибка: {e}"
            
        return display_text, tooltip_text
    
    def _get_status_display_info(self, status, details, task_payload):
        """Возвращает (текст_статуса, цвет_hex) для заданного состояния."""
        is_glossary_task = task_payload and task_payload[0] == 'glossary_batch_task'
        
        final_status_key = status
        if status == 'success' and is_glossary_task:
            final_status_key = 'glossary_success'
        elif status == 'error':
            error_types = details.get('errors', {}).keys()
            if 'CONTENT_FILTER' in error_types: final_status_key = 'error_filter'
            elif 'NETWORK' in error_types: final_status_key = 'error_network'
            elif 'VALIDATION' in error_types: final_status_key = 'error_validation'
            else: final_status_key = 'error'

        status_map = {
            'success': ("✅ Успешно", "#2ECC71"),
            'glossary_success': ("✅ Сгенерировано", "#1ABC9C"),
            'error': ("❌ Ошибка", "#E74C3C"),
            'error_filter': ("🛡️ Фильтр", "#9B59B6"),
            'error_network': ("🔌 Сеть лежит!", "#E67E22"),
            'error_validation': ("📋 Невалидно!", "#F39C12"),
            'in_progress': ("🔄 В работе…", "#3498DB"),
            'pending': ("⏳ Ожидание…", self.palette().color(QtGui.QPalette.ColorRole.Text).name()),
            'held': ("స్త Заморожено", "#7F8C8D"),
            'completion': ("✍️ До-генерация…", "#F39C12")
        }
        
        return status_map.get(final_status_key, (f"❓ {final_status_key}", "#FFFFFF"))
        
    def _update_row_status(self, row, status, details={}):
        status_item = self.table.item(row, 1)
        item_task = self.table.item(row, 0)
        if not status_item or not item_task: return

        # 1. Получаем payload, чтобы передать его в "мозг"
        task_tuple = item_task.data(QtCore.Qt.ItemDataRole.UserRole)
        task_payload = task_tuple[1] if task_tuple and len(task_tuple) > 1 else None
        
        # 2. Обращаемся к "мозгу" за инструкциями
        display_text, color_hex = self._get_status_display_info(status, details, task_payload)
        
        # 3. Просто выполняем инструкции
        status_item.setText(display_text)
        
        error_tooltip = ""
        if status.startswith('error'):
            error_counts = details.get('errors', {})
            error_lines = [f"- {err_type}: {count} раз" for err_type, count in error_counts.items()]
            error_tooltip = "\n\nИстория ошибок:\n" + "\n".join(error_lines)
        
        status_item.setToolTip(f"Статус: {display_text}{error_tooltip}")
        brush = QtGui.QBrush(QtGui.QColor(color_hex))
        item_task.setForeground(brush)
        status_item.setForeground(brush)
    
    def set_retry_button_visible(self, visible):
        """Управляет видимостью кнопки 'Выбрать ошибочные'."""
        self.retry_failed_btn.setVisible(visible)
    
    def set_copy_originals_visible(self, visible: bool):
        """Управляет видимостью кнопки 'Скопировать оригиналы'."""
        self.btn_copy_originals.setVisible(visible)
        
    def set_session_mode(self, is_session_active):
        """Переключает режим виджета для активной сессии."""
        self._is_session_active = is_session_active
        
        self.btn_copy_originals.setEnabled(not is_session_active)
    
        # Принудительно вызываем обновление кнопок, так как режим сессии изменился
        self._on_selection_changed_for_buttons()
            
    def closeEvent(self, event):
        """Отписываемся от шины при закрытии/уничтожении виджета."""
        if self.bus:
            try:
                self.bus.event_posted.disconnect(self.on_event)
            except (TypeError, RuntimeError):
                pass
        super().closeEvent(event)
