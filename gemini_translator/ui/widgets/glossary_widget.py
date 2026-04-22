# gemini_translator/ui/widgets/glossary_widget.py

import json
import os

import re
import zipfile
import time

from PyQt6 import QtWidgets
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QFileDialog, QMessageBox, QDialog, QDialogButtonBox, QGroupBox,
    QRadioButton, QFrame, QSizePolicy, QAbstractItemView, QLabel, QCheckBox
)
from PyQt6.QtCore import pyqtSignal, Qt, pyqtSlot, QTimer

from ..dialogs.glossary import MainWindow as GlossaryToolWindow
from ..dialogs.glossary import ImporterWizardDialog
from ..dialogs.glossary_dialogs.custom_widgets import ExpandingTextEditDelegate
from ...utils.settings import SettingsManager
from ...api import config as api_config
from collections import defaultdict


def sorted_glossary_entries(entries: list[dict]) -> list[dict]:
    """Стабильно сортирует записи по original, оставляя пустые строки в конце."""
    return sorted(
        entries,
        key=lambda entry: (
            not str(entry.get("original", "") or "").strip(),
            str(entry.get("original", "") or "").strip().casefold(),
        ),
    )

class GlossaryWidget(QWidget):
    """
    Виджет для управления глоссарием проекта, включая таблицу и кнопки управления.
    Версия 2.0: Встроенная пагинация.
    """
    glossary_changed = pyqtSignal()

    def __init__(self, parent=None, settings_manager: SettingsManager = None):
        super().__init__(parent)
        self.settings_manager = settings_manager
        self.current_epub_path = None 
        # --- АТРИБУТЫ ДЛЯ ПАГИНАЦИИ ---
        self._full_glossary_data = []
        self.items_per_page = 100
        self.current_page = 0
        self.total_items = 0
        
        # --- КОНЕЦ АТРИБУТОВ ---

        self._init_ui()
        self.table.itemChanged.connect(self._on_item_changed)

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Оригинал", "Перевод", "Примечание (Контекст)"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        # --- НАЧАЛО ОПЕРАЦИИ ---
        # 1. Устанавливаем быструю, фиксированную высоту по умолчанию.
        self.table.verticalHeader().setDefaultSectionSize(30)
        
        # 2. Подключаем "умный" делегат, который будет расширять ячейки ТОЛЬКО при редактировании.
        delegate = ExpandingTextEditDelegate(self.table)
        self.table.setItemDelegate(delegate)
        # --- КОНЕЦ ОПЕРАЦИИ ---

        self.table.setWordWrap(True)
        main_layout.addWidget(self.table)

        # --- ПАНЕЛЬ ПАГИНАЦИИ (переехала сюда) ---
        pagination_widget = QWidget()
        pagination_layout = QHBoxLayout(pagination_widget)
        pagination_layout.setContentsMargins(0, 5, 0, 5)
        self.first_page_button = QPushButton("<< В начало")
        self.first_page_button.clicked.connect(self._go_to_first_page)
        self.prev_page_button = QPushButton("< Назад")
        self.prev_page_button.clicked.connect(self._go_to_prev_page)
        self.page_info_label = QLabel("Страница 1 / 1")
        self.page_info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.next_page_button = QPushButton("Вперед >")
        self.next_page_button.clicked.connect(self._go_to_next_page)
        self.last_page_button = QPushButton("В конец >>")
        self.last_page_button.clicked.connect(self._go_to_last_page)
        pagination_layout.addStretch()
        pagination_layout.addWidget(self.first_page_button)
        pagination_layout.addWidget(self.prev_page_button)
        pagination_layout.addWidget(self.page_info_label)
        pagination_layout.addWidget(self.next_page_button)
        pagination_layout.addWidget(self.last_page_button)
        pagination_layout.addStretch()
        main_layout.addWidget(pagination_widget)
        # --- КОНЕЦ ПАНЕЛИ ПАГИНАЦИИ ---

        bottom_panel = QWidget()
        bottom_panel_layout = QHBoxLayout(bottom_panel)
        bottom_panel_layout.setContentsMargins(0, 5, 0, 5)

        table_actions_layout = QHBoxLayout()
        self.add_row_btn = QPushButton("➕ Добавить")
        self.add_row_btn.setToolTip("Добавить новый термин в таблицу")
        self.add_row_btn.clicked.connect(self._add_row)
        self.remove_row_btn = QPushButton("➖ Удалить")
        self.remove_row_btn.setToolTip("Удалить выделенные строки")
        self.remove_row_btn.clicked.connect(self._remove_selected_rows)
        
        # --- НОВАЯ КНОПКА (скрыта по умолчанию) ---
        self.cleanup_btn = QPushButton("🧹 Пост-обработка")
        self.cleanup_btn.setToolTip("Автоматически исправить скобки и слеши в переводах")
        self.cleanup_btn.clicked.connect(self._open_cleanup_dialog)
        self.cleanup_btn.setVisible(False) 
        # ------------------------------------------

        table_actions_layout.addWidget(self.add_row_btn)
        table_actions_layout.addWidget(self.remove_row_btn)
        table_actions_layout.addWidget(self.cleanup_btn) # Добавляем в лейаут
        
        global_actions_layout = QHBoxLayout()
        self.load_btn = QPushButton("Импорт…")
        self.load_btn.setToolTip("Загрузить глоссарий из файла (.json, .txt)")
        self.load_btn.clicked.connect(self._load_from_file)
        self.manage_btn = QPushButton("🛠️ Менеджер…")
        self.manage_btn.setToolTip("Открыть менеджер для анализа и разрешения конфликтов")
        self.manage_btn.clicked.connect(self._open_manager)
        self.generate_btn = QPushButton("✨ Генерация AI…")
        self.generate_btn.setToolTip("Создать/дополнить глоссарий с помощью AI на основе выбранных глав")
        self.generate_btn.clicked.connect(self._open_ai_generation_dialog)
        global_actions_layout.addWidget(self.load_btn)
        global_actions_layout.addWidget(self.manage_btn)
        global_actions_layout.addWidget(self.generate_btn)

        bottom_panel_layout.addLayout(table_actions_layout)
        bottom_panel_layout.addStretch()
        bottom_panel_layout.addLayout(global_actions_layout)
        
        main_layout.addWidget(bottom_panel)

        
    def get_glossary(self) -> list:
        # Теперь этот метод всегда возвращает полный список
        return self._full_glossary_data

    def _sort_full_glossary_data(self):
        self._full_glossary_data = sorted_glossary_entries(self._full_glossary_data)

    def _find_entry_index(self, target_entry) -> int | None:
        for index, entry in enumerate(self._full_glossary_data):
            if entry is target_entry:
                return index
        return None

    def set_glossary(self, glossary_data, emit_signal: bool = True):
        self.table.blockSignals(True)
        entries_to_load = []
        
        # --- Нормализация данных (rus vs translation + timestamp) ---
        raw_list = []
        if isinstance(glossary_data, dict):
            raw_list = [{"original": k, **v} for k, v in glossary_data.items()]
        elif isinstance(glossary_data, list):
            raw_list = glossary_data
            
        current_now = time.time()

        for entry in raw_list:
            clean_entry = entry.copy()
            
            # Фолбэк: если нет 'rus', но есть 'translation', используем его
            if 'rus' not in clean_entry and 'translation' in clean_entry:
                clean_entry['rus'] = clean_entry['translation']
            
            # Гарантируем наличие ключей
            if 'rus' not in clean_entry: clean_entry['rus'] = ""
            if 'note' not in clean_entry: clean_entry['note'] = ""
            if 'original' not in clean_entry: clean_entry['original'] = ""
            
            # ТАЙМСТАМП: Сохраняем старый или создаем новый (для импорта из старых версий)
            if 'timestamp' not in clean_entry:
                clean_entry['timestamp'] = current_now
            
            entries_to_load.append(clean_entry)

        self._full_glossary_data = sorted_glossary_entries(entries_to_load)
        self.current_page = 0
        self._load_current_page()
        
        self.table.blockSignals(False)
        if emit_signal:
            self.glossary_changed.emit()

    def _add_row(self):
        """Создает новую запись в глоссарии с текущей меткой времени и открывает её для редактирования."""
        # Создаем запись с таймстампом (дата создания)
        new_entry = {
            "original": "", 
            "rus": "", 
            "note": "", 
            "timestamp": time.time()
        }
        
        # Вставляем в начало текущей страницы для визуального удобства
        start_index = self.current_page * self.items_per_page
        self._full_glossary_data.insert(start_index, new_entry)
        
        # Перезагружаем таблицу, чтобы увидеть новую пустую строку
        self._load_current_page()
        
        # Автоматически переходим в режим редактирования первой ячейки новой строки
        QTimer.singleShot(0, lambda: (
            self.table.selectRow(0),
            self.table.scrollToItem(self.table.item(0, 0)),
            self.table.editItem(self.table.item(0, 0))
        ))
        
        self.glossary_changed.emit()

    def _remove_selected_rows(self):
        selected_rows_on_page = sorted(list(set(index.row() for index in self.table.selectedIndexes())), reverse=True)
        if not selected_rows_on_page: return

        start_index = self.current_page * self.items_per_page
        
        self.table.blockSignals(True)
        # Удаляем из полного списка по реальным индексам
        for row_on_page in selected_rows_on_page:
            index_in_full_list = start_index + row_on_page
            if 0 <= index_in_full_list < len(self._full_glossary_data):
                del self._full_glossary_data[index_in_full_list]
        
        # Перезагружаем текущую страницу
        self._load_current_page()
        self.table.blockSignals(False)
        self.glossary_changed.emit()

    @pyqtSlot(QtWidgets.QTableWidgetItem)
    def _on_item_changed(self, item: QtWidgets.QTableWidgetItem):
        """
        Синхронизирует ИЗМЕНЕННУЮ ячейку с полным списком в памяти,
        используя сохраненный индекс для надежности при сортировке.
        """
        if self.table.signalsBlocked():
            return

        # Получаем реальный индекс данных, который мы сохранили при загрузке
        index_in_full_list = item.data(Qt.ItemDataRole.UserRole)
        
        # Если индекса нет (например, новая строка до перезагрузки) или он некорректен
        if index_in_full_list is None:
            # Fallback на старую логику только для крайних случаев
            row = item.row()
            index_in_full_list = self.current_page * self.items_per_page + row

        col = item.column()

        # Проверяем, что индекс в пределах допустимого
        if 0 <= index_in_full_list < len(self._full_glossary_data):
            entry = self._full_glossary_data[index_in_full_list]
            
            text_value = item.text().strip()
            
            if col == 0:
                entry["original"] = text_value
            elif col == 1:
                entry["rus"] = text_value
            elif col == 2:
                entry["note"] = text_value

            if col == 0:
                self._sort_full_glossary_data()
                new_index = self._find_entry_index(entry)
                if new_index is not None:
                    self.current_page = new_index // self.items_per_page
                    self._load_current_page()
                    new_row = new_index % self.items_per_page
                    if 0 <= new_row < self.table.rowCount():
                        self.table.selectRow(new_row)
                        target_item = self.table.item(new_row, 0)
                        if target_item:
                            self.table.scrollToItem(target_item)

        self.glossary_changed.emit()
        
    def commit_active_editor(self):
        """Принудительно завершает редактирование активной ячейки, сохраняя данные."""
        if self.table.state() == QAbstractItemView.State.EditingState:
            # Смена фокуса заставляет делегат сохранить данные и закрыть редактор
            self.table.setFocus() 
            # На случай, если фокус уже там, явно закрываем для текущего элемента
            current = self.table.currentItem()
            if current:
                self.table.closePersistentEditor(current)
                
    # --- МЕТОДЫ ПАГИНАЦИИ ---
    @property
    def total_pages(self) -> int:
        if self.total_items == 0: return 1
        return (self.total_items + self.items_per_page - 1) // self.items_per_page

    def _load_current_page(self):
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False) 
        self.table.setRowCount(0)
        self.total_items = len(self._full_glossary_data)
        
        if self.total_items > 0 and self.current_page >= self.total_pages:
            self.current_page = self.total_pages - 1

        start_index = self.current_page * self.items_per_page
        end_index = start_index + self.items_per_page
        
        page_data = self._full_glossary_data[start_index:end_index] # Срез ссылок на словари
        
        self.table.setRowCount(len(page_data))
        
        for i, entry in enumerate(page_data):
            real_data_index = start_index + i
            
            # Создаем элементы и привязываем к ним РЕАЛЬНЫЙ индекс в списке
            item_original = QTableWidgetItem(entry.get("original", ""))
            item_original.setData(Qt.ItemDataRole.UserRole, real_data_index)
            
            item_translation = QTableWidgetItem(entry.get("rus", ""))
            item_translation.setData(Qt.ItemDataRole.UserRole, real_data_index)
            
            item_note = QTableWidgetItem(entry.get("note", ""))
            item_note.setData(Qt.ItemDataRole.UserRole, real_data_index)

            self.table.setItem(i, 0, item_original)
            self.table.setItem(i, 1, item_translation)
            self.table.setItem(i, 2, item_note)
        
        self.table.blockSignals(False)
        self._update_pagination_controls()
        
    def set_cleanup_button_visible(self, visible: bool):
        """Управляет видимостью кнопки пост-обработки."""
        self.cleanup_btn.setVisible(visible)
    
    def set_epub_path(self, path: str):
        """Устанавливает путь к текущему EPUB файлу для инструментов анализа."""
        self.current_epub_path = path
        
    def _open_cleanup_dialog(self):
        """Открывает диалог очистки/пост-обработки."""
        if not self._full_glossary_data:
            QMessageBox.information(self, "Пусто", "Глоссарий пуст, обрабатывать нечего.")
            return

        # Передаем сохраненный путь (self.current_epub_path)
        dialog = GlossaryCleanupDialog(
            self.get_glossary(), 
            epub_path=self.current_epub_path, 
            parent=self
        )
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_data, count = dialog.process_data()
            if count > 0:
                self.set_glossary(new_data)
                QMessageBox.information(self, "Готово", f"Обработано записей: {count}")
            else:
                QMessageBox.information(self, "Результат", "Изменений не потребовалось.")
        
    def _update_pagination_controls(self):
        total_pg = self.total_pages
        current_pg = self.current_page + 1
        self.page_info_label.setText(f"Страница {current_pg} / {total_pg} (Всего: {self.total_items})")
        is_not_first = self.current_page > 0
        self.first_page_button.setEnabled(is_not_first)
        self.prev_page_button.setEnabled(is_not_first)
        is_not_last = self.current_page < total_pg - 1
        self.next_page_button.setEnabled(is_not_last)
        self.last_page_button.setEnabled(is_not_last)

    def _go_to_first_page(self):
        self.current_page = 0
        # Разрываем стек вызовов. _load_current_page будет вызван из "чистого" состояния.
        QTimer.singleShot(0, self._load_current_page)

    def _go_to_prev_page(self):
        self.current_page = max(0, self.current_page - 1)
        # Разрываем стек вызовов.
        QTimer.singleShot(0, self._load_current_page)

    def _go_to_next_page(self):
        self.current_page = min(self.total_pages - 1, self.current_page + 1)
        # Разрываем стек вызовов.
        QTimer.singleShot(0, self._load_current_page)

    def _go_to_last_page(self):
        if self.total_pages > 0:
            self.current_page = self.total_pages - 1
            # Разрываем стек вызовов.
            QTimer.singleShot(0, self._load_current_page)
        
    # --- Остальные методы (без изменений) ---
    def set_simplified_mode(self):
        self.load_btn.hide()
        self.manage_btn.hide()
        self.generate_btn.hide()
        
    def set_generation_enabled(self, enabled):
        self.generate_btn.setEnabled(enabled)
        
    def clear(self):
        self.set_glossary([])

    def _load_from_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Загрузить глоссарий из файла", "", "Все поддерживаемые (*.json *.txt);;JSON Files (*.json);;Text Files (*.txt)")
        if not file_path: return
        try:
            with open(file_path, 'r', encoding='utf-8') as f: content = f.read()
            if not content.strip(): QMessageBox.information(self, "Файл пуст", "Выбранный файл не содержит данных."); return
            wizard = ImporterWizardDialog(initial_data=content, parent=self)
            if wizard.exec() == QDialog.DialogCode.Accepted:
                newly_imported_list = wizard.get_glossary()
                if not newly_imported_list: QMessageBox.information(self, "Нет данных", "Мастер импорта не смог извлечь ни одной записи."); return
                self.set_glossary(newly_imported_list)
                QMessageBox.information(self, "Успех", f"Загружено {len(newly_imported_list)} терминов.")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка загрузки", f"Не удалось прочитать или обработать файл: {e}")
            
    def _open_manager(self):
        parent_dialog = self.parent()
        # Ищем родительское окно настроек, чтобы заблокировать его и взять путь
        while parent_dialog and parent_dialog.__class__.__name__ != 'InitialSetupDialog':
            parent_dialog = parent_dialog.parent()
        
        # --- ИЗВЛЕЧЕНИЕ ПУТИ ПРОЕКТА ---
        project_folder = None
        if parent_dialog and hasattr(parent_dialog, 'output_folder'):
            project_folder = parent_dialog.output_folder
        # -------------------------------

        if parent_dialog:
            parent_dialog.setEnabled(False)
            parent_dialog.is_blocked_by_child_dialog = True
        
        # --- ПЕРЕДАЕМ project_path В КОНСТРУКТОР ---
        manager_window = GlossaryToolWindow(self, mode='dialog', project_path=project_folder)
        
        manager_window.set_glossary(self.get_glossary())
        manager_window.mark_current_state_as_saved()
        if manager_window.exec() == QDialog.DialogCode.Accepted:
            updated_glossary = manager_window.get_glossary()
            is_project_synced = manager_window.is_current_state_saved_to_project()
            self.set_glossary(updated_glossary, emit_signal=not is_project_synced)
            if is_project_synced and parent_dialog and hasattr(parent_dialog, 'initial_glossary_state'):
                parent_dialog.initial_glossary_state = [item.copy() for item in updated_glossary]
        
        if parent_dialog:
            parent_dialog.setEnabled(True)
            parent_dialog.is_blocked_by_child_dialog = False
            parent_dialog._check_and_sync_active_session()
            
    def _open_ai_generation_dialog(self):
        from ..dialogs.glossary_dialogs.ai_generation import GenerationSessionDialog
        
        # Ищем наше главное окно InitialSetupDialog, поднимаясь по иерархии
        parent_dialog = self.parent()
        while parent_dialog and parent_dialog.__class__.__name__ != 'InitialSetupDialog':
            parent_dialog = parent_dialog.parent()
        
        if not parent_dialog:
            QMessageBox.warning(self, "Ошибка", "Не удалось найти основное окно настроек (InitialSetupDialog).")
            return

        if not parent_dialog.html_files or not parent_dialog.selected_file:
            QMessageBox.warning(self, "Нет данных", "Сначала выберите EPUB файл и главы в основном окне.")
            return
            
        dialog = GenerationSessionDialog(
            settings_manager=self.settings_manager, 
            initial_glossary=self.get_glossary(), 
            merge_mode=None, 
            html_files=parent_dialog.html_files, 
            epub_path=parent_dialog.selected_file, 
            project_manager=parent_dialog.project_manager, 
            initial_ui_settings=parent_dialog.get_settings()
        )
        dialog.generation_finished.connect(self._on_generation_dialog_finished)

        try:
            parent_dialog.setEnabled(False)
            parent_dialog.is_blocked_by_child_dialog = True
            dialog.exec()
        finally:
            parent_dialog.setEnabled(True)
            parent_dialog.is_blocked_by_child_dialog = False
            parent_dialog._check_and_sync_active_session()
            # Теперь мы вызываем метод у найденного родительского окна
            parent_dialog._prepare_and_display_tasks(clean_rebuild=True)
            if hasattr(parent_dialog, 'auto_translate_widget'):
                parent_dialog.auto_translate_widget.refresh_glossary_presets()
    
    @pyqtSlot(list, set)
    def _on_generation_dialog_finished(self, final_glossary_from_ai, updated_generated_chapters_map):
        if final_glossary_from_ai is None: return 
        glossary_before = self.get_glossary()
        before_dict = {term.get('original', '').lower(): term for term in glossary_before if term.get('original')}
        after_dict = {term.get('original', '').lower(): term for term in final_glossary_from_ai if term.get('original')}
        before_keys, after_keys = set(before_dict.keys()), set(after_dict.keys())
        added_count, deleted_count, changed_count = len(after_keys - before_keys), len(before_keys - after_keys), 0
        common_keys = before_keys & after_keys
        for key in common_keys:
            if before_dict[key] != after_dict[key]:
                changed_count += 1
        total_changes = added_count + deleted_count + changed_count
        self.set_glossary(final_glossary_from_ai)
        if total_changes == 0: return
        summary_parts = []
        if added_count > 0: summary_parts.append(f"<b>Добавлено: {added_count}</b>")
        if changed_count > 0: summary_parts.append(f"Изменено: {changed_count}")
        if deleted_count > 0: summary_parts.append(f"Удалено: {deleted_count}")
        summary_text = ", ".join(summary_parts)
        parent_dialog = self.parent()
        while parent_dialog and parent_dialog.__class__.__name__ != 'InitialSetupDialog':
            parent_dialog = parent_dialog.parent()
        project_folder = getattr(parent_dialog, 'output_folder', None) if parent_dialog else None
        msg_box = QMessageBox(self); msg_box.setWindowTitle("Генерация завершена")
        msg_box.setText(f"Обнаружены изменения в глоссарии: {summary_text}.")
        if project_folder:
            msg_box.setInformativeText("Хотите сохранить этот результат в файл 'project_glossary.json'?")
            save_btn = msg_box.addButton("Сохранить в проект", QMessageBox.ButtonRole.AcceptRole)
            discard_btn = msg_box.addButton("Не сохранять", QMessageBox.ButtonRole.RejectRole)
            msg_box.setDefaultButton(save_btn)
        else:
            msg_box.setInformativeText("Данные обновлены в таблице. Папка проекта не выбрана, поэтому автосохранение недоступно.")
            msg_box.addButton("OK", QMessageBox.ButtonRole.AcceptRole)
        msg_box.exec()
        if project_folder and msg_box.clickedButton() == save_btn:
            try:
                project_glossary_path = os.path.join(project_folder, "project_glossary.json")
                with open(project_glossary_path, 'w', encoding='utf-8') as f:
                    json.dump(self.get_glossary(), f, ensure_ascii=False, indent=2, sort_keys=True)
                if parent_dialog and hasattr(parent_dialog, 'project_manager') and parent_dialog.project_manager:
                    parent_dialog.project_manager.save_glossary_generation_map(updated_generated_chapters_map)
                # --- ИСПРАВЛЕНИЕ: Сохраняем копию состояния, чтобы разорвать ссылочную связь ---
                if parent_dialog: 
                    parent_dialog.initial_glossary_state = [item.copy() for item in self.get_glossary()]
                QMessageBox.information(self, "Успех", "Глоссарий проекта и карта сгенерированных глав успешно сохранены.")
            except Exception as e:
                QMessageBox.critical(self, "Ошибка сохранения", f"Не удалось сохранить глоссарий и/или карту глав: {e}")
        
    def set_controls_enabled(self, enabled: bool):
        self.add_row_btn.setEnabled(enabled)
        self.remove_row_btn.setEnabled(enabled)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed if enabled else QAbstractItemView.EditTrigger.NoEditTriggers)
        
        
        
class GlossaryCleanupDialog(QDialog):
    """
    Диалог пост-обработки.
    Режим: Анализ -> Выбор -> Применение.
    Идентификация записей происходит по паре (original, rus), а не по индексам.
    """
    def __init__(self, glossary_data: list, epub_path: str = None, parent=None):
        super().__init__(parent)
        self.glossary_data = glossary_data 
        self.epub_path = epub_path
        
        # Храним наборы кортежей: (original, rus)
        self.candidates_parens = set()
        self.candidates_slashes = set()
        self.candidates_headers = set()
        
        self.setWindowTitle("Мастер очистки глоссария")
        self.resize(600, 420)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # --- Описание ---
        info_box = QLabel(
            "Этот инструмент проанализирует глоссарий на наличие 'мусора' и структурных ошибок.\n"
            "Нажмите 'Анализировать', чтобы увидеть статистику."
        )
        info_box.setStyleSheet("background-color: #2b2b2b; padding: 10px; border-radius: 5px;")
        info_box.setWordWrap(True)
        layout.addWidget(info_box)

        # --- Чекбоксы (Изначально отключены) ---
        self.group = QGroupBox("Результаты анализа")
        group_layout = QVBoxLayout(self.group)

        self.check_parens = QCheckBox("Перенести скобки (...) в примечание")
        self.check_parens.setToolTip("Переносит пояснения в скобках из перевода в колонку Note.")
        self.check_parens.setEnabled(False)

        self.check_slashes = QCheckBox("Разделить варианты через слеш '/'")
        self.check_slashes.setToolTip("Оставляет первый вариант, остальные переносит в Note как синонимы.")
        self.check_slashes.setEnabled(False)

        self.check_headers = QCheckBox("Удалить термины-заголовки")
        self.check_headers.setToolTip(
            "Удаляет термины, которые встречаются только как заголовки глав (h1/title)\n"
            "и не участвуют в повествовании."
        )
        self.check_headers.setVisible(False)
        self.check_headers.setEnabled(False)

        group_layout.addWidget(self.check_parens)
        group_layout.addWidget(self.check_slashes)
        group_layout.addWidget(self.check_headers)
        layout.addWidget(self.group)

        # --- Прогресс бар ---
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        # --- Кнопки ---
        btn_layout = QHBoxLayout()
        
        self.btn_analyze = QPushButton("🔍 Анализировать")
        self.btn_analyze.clicked.connect(self._run_analysis)
        self.btn_analyze.setStyleSheet("font-weight: bold; padding: 5px;")
        
        btn_layout.addWidget(self.btn_analyze)
        btn_layout.addStretch()
        
        self.btn_apply = QPushButton("Применить")
        self.btn_apply.setEnabled(False)
        self.btn_apply.clicked.connect(self.accept)
        
        self.btn_cancel = QPushButton("Отмена")
        self.btn_cancel.clicked.connect(self.reject)

        btn_layout.addWidget(self.btn_apply)
        btn_layout.addWidget(self.btn_cancel)
        layout.addLayout(btn_layout)

    def _run_analysis(self):
        """Запускает процесс анализа."""
        self.btn_analyze.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0) 
        QtWidgets.QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        
        self.candidates_parens.clear()
        self.candidates_slashes.clear()
        self.candidates_headers.clear()

        QTimer.singleShot(100, self._perform_analysis_logic)

    def _perform_analysis_logic(self):
        try:
            # 1. Анализ форматирования
            for entry in self.glossary_data:
                orig = entry.get('original', '')
                rus = entry.get('rus', '')
                ident_key = (orig, rus) # Уникальный идентификатор записи для этого сеанса
                
                # Скобки
                if '(' in rus or '（' in rus:
                    if not ('(' in orig or ')' in orig or '（' in orig):
                        if re.search(r'(?:[\(\（](.*?)[\)\）])', rus):
                            self.candidates_parens.add(ident_key)

                # Слеши
                if '/' in rus:
                    if '/' not in orig:
                        parts = [p for p in rus.split('/') if p.strip()]
                        if len(parts) > 1:
                            self.candidates_slashes.add(ident_key)

            # 2. Анализ заголовков (если есть EPUB)
            # if self.epub_path and os.path.exists(self.epub_path):
                # epub_cache = {}
                # try:
                    # self._analyze_epub_structure(epub_cache)
                    # for entry in self.glossary_data:
                        # term = entry.get('original', '').strip()
                        # rus = entry.get('rus', '')
                        # ident_key = (entry.get('original', ''), rus)
                        
                        # if self._should_remove_as_header(term, epub_cache):
                            # self.candidates_headers.add(ident_key)
                # except Exception as e:
                    # print(f"[Analysis Error] {e}")

            # 3. Обновление UI
            self._update_checkboxes()
            self.btn_apply.setEnabled(True)

        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
            self.progress_bar.setVisible(False)
            self.btn_analyze.setEnabled(True)
            self.btn_analyze.setText("🔍 Пересканировать")

    def _update_checkboxes(self):
        n_parens = len(self.candidates_parens)
        self.check_parens.setText(f"Перенести скобки (...) в примечание (Найдено: {n_parens})")
        self.check_parens.setEnabled(n_parens > 0)
        self.check_parens.setChecked(n_parens > 0)

        n_slashes = len(self.candidates_slashes)
        self.check_slashes.setText(f"Разделить варианты через слеш '/' (Найдено: {n_slashes})")
        self.check_slashes.setEnabled(n_slashes > 0)
        self.check_slashes.setChecked(n_slashes > 0)

        n_headers = len(self.candidates_headers)
        if self.epub_path:
            self.check_headers.setText(f"Удалить термины-заголовки (Найдено: {n_headers})")
            self.check_headers.setEnabled(n_headers > 0)
            self.check_headers.setChecked(n_headers > 0)
        else:
            self.check_headers.setText("Удалить термины-заголовки (Нет EPUB файла)")
            self.check_headers.setEnabled(False)

    def process_data(self):
        """
        Генерирует финальный список.
        """
        new_list = []
        
        do_parens = self.check_parens.isChecked()
        do_slashes = self.check_slashes.isChecked()
        do_headers = self.check_headers.isChecked()
        
        stat_formatted = 0
        stat_removed = 0

        for entry in self.glossary_data:
            orig = entry.get('original', '')
            rus = entry.get('rus', '')
            ident_key = (orig, rus) # Ключ для проверки

            # 1. Проверка на удаление
            if do_headers and ident_key in self.candidates_headers:
                stat_removed += 1
                continue 

            # Создаем копию
            new_entry = entry.copy()
            modified = False
            
            # Работаем с локальными переменными для удобства
            curr_rus = new_entry.get('rus', '')
            curr_note = new_entry.get('note', '')

            # 2. Обработка скобок
            if do_parens and ident_key in self.candidates_parens:
                matches = list(re.finditer(r'\s*(?:[\(\（](.*?)[\)\）])', curr_rus))
                if matches:
                    extracted = []
                    for match in reversed(matches):
                        content = match.group(1).strip()
                        if content: extracted.insert(0, content)
                        s, e = match.span()
                        curr_rus = curr_rus[:s] + curr_rus[e:]
                    
                    curr_rus = curr_rus.strip()
                    if extracted:
                        add_note = "; ".join(extracted)
                        curr_note = f"{add_note}; {curr_note}" if curr_note else add_note
                        modified = True

            # 3. Обработка слешей
            if do_slashes and ident_key in self.candidates_slashes and '/' in curr_rus:
                parts = [p.strip() for p in curr_rus.split('/') if p.strip()]
                if len(parts) > 1:
                    curr_rus = parts[0]
                    synonyms = ", ".join([f"[{s}]" for s in parts[1:]])
                    add_note = f"(Допустимые синонимы/омонимы: {synonyms})"
                    curr_note = f"{curr_note} {add_note}" if curr_note else add_note
                    modified = True

            if modified:
                new_entry['rus'] = curr_rus
                new_entry['note'] = curr_note
                stat_formatted += 1
            
            new_list.append(new_entry)
            
        return new_list, stat_formatted + stat_removed

    # --- Методы анализа EPUB ---
    def _analyze_epub_structure(self, cache_dict):
        ignore_patterns = ['toc', 'nav', 'cover', 'style', 'css']
        with zipfile.ZipFile(self.epub_path, 'r') as zf:
            for filename in zf.namelist():
                if not filename.endswith(('.html', '.xhtml', '.htm')): continue
                if any(pat in filename.lower() for pat in ignore_patterns): continue
                try:
                    raw_content = zf.read(filename).decode('utf-8', 'ignore')
                    # H1 / Title
                    h1s = re.findall(r'<h1.*?>(.*?)</h1>', raw_content, re.IGNORECASE | re.DOTALL)
                    titles = re.findall(r'<title.*?>(.*?)</title>', raw_content, re.IGNORECASE | re.DOTALL)
                    clean_h1s = [re.sub(r'<[^>]+>', '', h).strip() for h in h1s]
                    clean_titles = [re.sub(r'<[^>]+>', '', t).strip() for t in titles]
                    
                    # Чистый текст для анализа длины
                    no_script = re.sub(r'<(script|style).*?>.*?</\1>', '', raw_content, flags=re.DOTALL | re.IGNORECASE)
                    blocks_replaced = re.sub(r'</?(p|div|br|h\d|li).*?>', '\n', no_script)
                    text_only = re.sub(r'<[^>]+>', '', blocks_replaced)
                    lines = [line.strip() for line in text_only.split('\n') if line.strip()]

                    cache_dict[filename] = {
                        'lines': lines,
                        'h1s': set(clean_h1s),
                        'titles': set(clean_titles)
                    }
                except Exception: pass

    def _should_remove_as_header(self, term, cache_dict):
        if not term or len(term) < 2: return False
        term_lower = term.lower()
        found_count = 0
        last_data = None
        
        for fname, data in cache_dict.items():
            found_in_file = False
            for line in data['lines']:
                if term_lower in line.lower():
                    found_in_file = True
                    break
            if found_in_file:
                found_count += 1
                last_data = data
            if found_count > 1: return False 

        if found_count == 0: return False
        
        # Только в 1 файле. Проверяем контекст.
        is_header = False
        for h in last_data['h1s']:
            if term_lower in h.lower(): is_header = True; break
        if not is_header:
            for t in last_data['titles']:
                if term_lower in t.lower(): is_header = True; break
        
        if not is_header: return False 

        # Проверка на нарратив
        term_len = len(term)
        for line in last_data['lines']:
            if term_lower in line.lower():
                line_len = len(line)
                diff = abs(line_len - term_len)
                if diff / term_len > 0.3: # Если строка на 30% длиннее термина
                    return False 
        
        return True
        
###
