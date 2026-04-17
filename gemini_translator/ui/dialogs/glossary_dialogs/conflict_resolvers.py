from collections import defaultdict

# --- Импорты из PyQt6 ---
from PyQt6 import QtWidgets, QtCore, QtGui
from PyQt6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QPushButton, QDialogButtonBox, QLabel,
    QWidget, QGroupBox, QCheckBox, QHBoxLayout, QGridLayout, QTableWidget,
    QHeaderView, QTableWidgetItem, QMessageBox, QAbstractItemView, QListWidget,
    QListWidgetItem, QSplitter, QStackedWidget, QLineEdit, QStyle
)
from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor

# --- Импорты из вашего проекта ---
from gemini_translator.ui.widgets.common_widgets import NoScrollComboBox
from .custom_widgets import ExpandingTextEditDelegate, SmartTextEdit

# --- Аннотация типа для избежания циклического импорта ---
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ..glossary import MainWindow

# --- Глобальные переменные, которые нужны этому диалогу ---
# Предполагаем, что они будут переданы из главного файла
PYMORPHY_AVAILABLE = False # Как заглушка


def _get_checked_color(widget):
    """Возвращает цвет для выделения, смешанный с базовым фоном виджета."""
    base_color = widget.palette().color(QtGui.QPalette.ColorRole.Base)
    tint_color = QtGui.QColor(46, 204, 113)
    factor = 0.15

    r = int(base_color.red() * (1 - factor) + tint_color.red() * factor)
    g = int(base_color.green() * (1 - factor) + tint_color.green() * factor)
    b = int(base_color.blue() * (1 - factor) + tint_color.blue() * factor)

    return QtGui.QColor(r, g, b)


class ComplexOverlapResolverDialog(QDialog):
    """
    Супер-диалог для разрешения наложений с двумя режимами:
    1. Общий вид (свободная навигация по списку).
    2. Пошаговый режим "Визард" (проход по нерешенным проблемам).
    """
    def __init__(self, overlap_groups, inverted_groups, original_glossary, pymorphy_available, parent=None):
        super().__init__(parent)
        self.overlap_groups = overlap_groups
        self.inverted_groups = inverted_groups
        self.original_glossary = original_glossary
        self.pending_changes = {}
        self.deleted_terms = set()
        self.checked_terms = set()
        self.pymorphy_available = pymorphy_available 
        # --- Состояние для пошагового режима ---
        self.wizard_mode_active = False
        self.wizard_terms = []
        self.wizard_current_index = -1
        
        self.view_mode = 'short_to_long'
        self.show_translations_mode = True  # <--- НОВЫЙ ФЛАГ: По умолчанию показываем переводы
        
        self.setWindowTitle("Шаг 3: Комплексное разрешение наложений")
        self.setMinimumSize(1200, 800)
        self.init_ui()

    def init_ui(self):
        main_layout = QHBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # --- Левая панель (список) ---
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        self.left_label = QLabel()
        left_layout.addWidget(self.left_label)
        self.left_list = QListWidget()
        self.left_list.currentItemChanged.connect(self.on_group_changed)
        left_layout.addWidget(self.left_list)
        
        # --- НОВАЯ КНОПКА В ЛЕВОЙ ПАНЕЛИ ---
        self.toggle_display_btn = QPushButton()
        self.toggle_display_btn.clicked.connect(self.toggle_term_display)
        self._update_display_btn_text() # Установить текст
        left_layout.addWidget(self.toggle_display_btn)
        
        # --- Правая панель (редактор) ---
        right_panel = QWidget()
        self.right_layout = QVBoxLayout(right_panel)
        
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([300, 900])
        main_layout.addWidget(splitter)
        
        # --- Верхняя панель управления ---
        top_controls = QHBoxLayout()
        self.toggle_view_btn = QPushButton("Переключить группировку")
        self.toggle_view_btn.clicked.connect(self.toggle_view)
        
        # Виджеты для обычного режима
        self.normal_mode_widget = QWidget()
        normal_layout = QHBoxLayout(self.normal_mode_widget)
        normal_layout.setContentsMargins(0,0,0,0)
        self.start_wizard_button = QPushButton("▶ Начать пошаговое разрешение")
        self.start_wizard_button.clicked.connect(self.start_wizard_mode)
        self.checked_checkbox = QCheckBox("Проверено")
        self.checked_checkbox.toggled.connect(self.on_checked_changed)
        normal_layout.addWidget(self.start_wizard_button)
        normal_layout.addStretch()
        normal_layout.addWidget(self.checked_checkbox)

        # Виджеты для пошагового режима
        self.wizard_mode_widget = QWidget()
        wizard_layout = QHBoxLayout(self.wizard_mode_widget)
        wizard_layout.setContentsMargins(0,0,0,0)
        self.wizard_prev_button = QPushButton("< Назад")
        self.wizard_prev_button.clicked.connect(self.wizard_go_prev)
        self.wizard_progress_label = QLabel("Шаг X из Y")
        self.wizard_next_button = QPushButton("Далее >")
        self.wizard_next_button.clicked.connect(self.wizard_go_next)
        finish_wizard_button = QPushButton("Завершить пошаговый режим")
        finish_wizard_button.clicked.connect(self.end_wizard_mode)
        wizard_layout.addWidget(self.wizard_prev_button)
        wizard_layout.addWidget(self.wizard_progress_label)
        wizard_layout.addWidget(self.wizard_next_button)
        wizard_layout.addStretch()
        wizard_layout.addWidget(finish_wizard_button)
        
        self.top_controls_stack = QStackedWidget()
        self.top_controls_stack.addWidget(self.normal_mode_widget)
        self.top_controls_stack.addWidget(self.wizard_mode_widget)

        top_controls.addWidget(self.toggle_view_btn)
        top_controls.addWidget(self.top_controls_stack, 1)

        # --- Основные кнопки OK/Cancel ---
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Принять изменения")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept_changes)
        buttons.rejected.connect(self.reject)
        
        self.right_panel_container = QWidget()
        self.right_panel_container_layout = QVBoxLayout(self.right_panel_container)
        
        self.right_layout.addLayout(top_controls)
        self.right_layout.addWidget(self.right_panel_container, 1) # Добавляем контейнер с растяжением
        self.right_layout.addWidget(buttons)
        
        self.populate_left_list()
        self.end_wizard_mode()


    @pyqtSlot(object, str, str)
    def _on_data_committed(self, identifier, field_name, new_text):
        """
        Принимает сигнал от SmartTextEdit и атомарно обновляет
        состояние изменений (pending_changes).
        """
        original_term = identifier # Идентификатор - это исходный термин
        
        # Получаем текущее состояние изменений для этого термина или его оригинал
        current_term, current_data = self.pending_changes.get(
            original_term,
            (original_term, self.original_glossary.get(original_term, {}).copy())
        )

        # Обновляем либо имя термина, либо данные в словаре
        if field_name == "original_term":
            current_term = new_text.strip()
        else:
            current_data[field_name] = new_text.strip()
        
        # Сохраняем обновленные данные в pending_changes
        self.pending_changes[original_term] = (current_term, current_data)
    
    def toggle_term_display(self):
        """Переключает режим отображения в левом списке (Оригиналы <-> Переводы)."""
        if self.wizard_mode_active: return
        self.show_translations_mode = not self.show_translations_mode
        self._update_display_btn_text()
        
        # Сохраняем ID текущего элемента
        current_id = None
        if self.left_list.currentItem():
            current_id = self.left_list.currentItem().data(Qt.ItemDataRole.UserRole)
            
        self.populate_left_list()
        
        # Восстанавливаем выбор
        if current_id:
            for i in range(self.left_list.count()):
                item = self.left_list.item(i)
                if item.data(Qt.ItemDataRole.UserRole) == current_id:
                    self.left_list.setCurrentItem(item)
                    break

    def _update_display_btn_text(self):
        if self.show_translations_mode:
            self.toggle_display_btn.setText("Показать оригиналы")
        else:
            self.toggle_display_btn.setText("Показать переводы")

    def populate_left_list(self):
        self.left_list.blockSignals(True)
        self.left_list.clear()
        source = self.overlap_groups
        label_text = "<b>Проблемные короткие термины:</b>"
        if self.view_mode == 'long_to_short':
            source = self.inverted_groups
            label_text = "<b>Проблемные длинные термины:</b>"
        self.left_label.setText(label_text)
        
        sorted_keys = sorted(source.keys())
        
        for term_original in sorted_keys:
            display_text = term_original
            
            # Если включен режим переводов, пытаемся найти перевод
            if self.show_translations_mode:
                data = self.original_glossary.get(term_original, {})
                rus = data.get('rus', 'НЕТ ПЕРЕВОДА')
                if rus:
                    display_text = rus
            
            item = QListWidgetItem(display_text)
            # ВАЖНО: Сохраняем оригинальный ID термина в UserRole
            item.setData(Qt.ItemDataRole.UserRole, term_original) 
            
            if term_original in self.checked_terms:
                item.setBackground(_get_checked_color(self.left_list))
            self.left_list.addItem(item)
            
        self.left_list.blockSignals(False)
        if self.left_list.count() > 0:
            self.left_list.setCurrentRow(0)
        else:
            self.on_group_changed(None, None)






    def start_wizard_mode(self):
        # Собираем только непроверенные термины для визарда
        self.wizard_terms = []
        for i in range(self.left_list.count()):
            item = self.left_list.item(i)
            # ИЗМЕНЕНИЕ: Сравниваем ID
            term_id = item.data(Qt.ItemDataRole.UserRole)
            if term_id not in self.checked_terms:
                self.wizard_terms.append(term_id)
        
        if not self.wizard_terms:
            QMessageBox.information(self, "Все готово", "Все конфликты в этом списке уже помечены как проверенные.")
            return
            
        self.wizard_mode_active = True
        self.wizard_current_index = 0
        
        self.left_list.setEnabled(False) 
        self.top_controls_stack.setCurrentWidget(self.wizard_mode_widget)
        
        self._show_wizard_step()

    def end_wizard_mode(self):
        self.wizard_mode_active = False
        self.wizard_terms = []
        self.wizard_current_index = -1
        
        self.left_list.setEnabled(True) # Разблокируем список
        self.top_controls_stack.setCurrentWidget(self.normal_mode_widget)

    def wizard_go_next(self):
        # Сохраняем и помечаем текущий как проверенный
        self.checked_checkbox.setChecked(True)
        
        if self.wizard_current_index < len(self.wizard_terms) - 1:
            self.wizard_current_index += 1
            self._show_wizard_step()
        else:
            QMessageBox.information(self, "Завершено", "Вы просмотрели все оставшиеся конфликты.")
            self.end_wizard_mode()

    def wizard_go_prev(self):
        # Просто переходим назад, ПРЕДВАРИТЕЛЬНО СОХРАНИВ ИЗМЕНЕНИЯ
        if self.wizard_current_index > 0:
            self.wizard_current_index -= 1
            self._show_wizard_step()

    def _show_wizard_step(self):
        if not self.wizard_mode_active or not self.wizard_terms:
            return

        self.wizard_progress_label.setText(f"Шаг {self.wizard_current_index + 1} из {len(self.wizard_terms)}")
        self.wizard_prev_button.setEnabled(self.wizard_current_index > 0)
        self.wizard_next_button.setText("Далее >" if self.wizard_current_index < len(self.wizard_terms) - 1 else "Завершить")

        term_to_show = self.wizard_terms[self.wizard_current_index]
        
        # ИЗМЕНЕНИЕ: Ищем по UserRole
        for i in range(self.left_list.count()):
            item = self.left_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == term_to_show:
                self.left_list.setCurrentItem(item)
                break


    def toggle_view(self):
        if self.wizard_mode_active: return
        self.view_mode = 'long_to_short' if self.view_mode == 'short_to_long' else 'short_to_long'
        self.populate_left_list()

    def _get_current_value(self, original_term):
        if original_term in self.deleted_terms: return None, None
        term, data = self.pending_changes.get(original_term, (original_term, self.original_glossary.get(original_term, {})))
        return term, data
    
    # --- ИЗМЕНЕНИЕ: Метод `_create_note_widget` больше не нужен и удален ---

    def _on_generate_note_for_main_term_clicked(self):
        main_window = self.parent()
        if not main_window: return
        note_text = main_window._generate_note_logic(self.main_trans_edit.text())
        if note_text:
            self.main_note_edit.setText(note_text)

    # --- ИЗМЕНЕНИЕ: Метод обновлен для работы с QTableWidgetItem ---
    def _on_generate_note_in_sub_table_clicked(self, row):
        main_window = self.parent()
        if not main_window: return
        
        translation_item = self.sub_terms_table.item(row, 1)
        if not translation_item: return

        note_text = main_window._generate_note_logic(translation_item.text())
        if note_text:
            note_item = self.sub_terms_table.item(row, 2)
            if note_item:
                note_item.setText(note_text)
                self.sub_terms_table.resizeRowToContents(row)
    
    # --- ИЗМЕНЕНИЕ: Логика сохранения обновлена для чтения из QTableWidgetItem ---
    def _save_current_changes(self):
        if not hasattr(self, 'current_term') or not self.current_term: return
        orig_term = self.current_term
        if orig_term not in self.deleted_terms and hasattr(self, 'main_term_edit'):
            new_term = self.main_term_edit.text().strip()
            new_trans = self.main_trans_edit.text().strip()
            new_note = self.main_note_edit.text().strip()
            
            original_data = self.original_glossary.get(orig_term, {})
            if (orig_term != new_term or 
                original_data.get('rus', '') != new_trans or
                original_data.get('note', '') != new_note):
                self.pending_changes[orig_term] = (new_term, {"rus": new_trans, "note": new_note})
            elif orig_term in self.pending_changes:
                del self.pending_changes[orig_term]
        
        if hasattr(self, 'sub_terms_table'):
            for i in range(self.sub_terms_table.rowCount()):
                sub_orig_term_item = self.sub_terms_table.item(i, 0)
                if not sub_orig_term_item: continue
                sub_orig_term = sub_orig_term_item.data(Qt.ItemDataRole.UserRole)
                if sub_orig_term in self.deleted_terms: continue
                
                sub_new_term = self.sub_terms_table.item(i, 0).text().strip()
                sub_new_trans = self.sub_terms_table.item(i, 1).text().strip()
                sub_new_note = self.sub_terms_table.item(i, 2).text().strip()

                sub_original_data = self.original_glossary.get(sub_orig_term, {})
                if (sub_orig_term != sub_new_term or 
                    sub_original_data.get('rus', '') != sub_new_trans or
                    sub_original_data.get('note', '') != sub_new_note):
                    self.pending_changes[sub_orig_term] = (sub_new_term, {"rus": sub_new_trans, "note": sub_new_note})
                elif sub_orig_term in self.pending_changes:
                    del self.pending_changes[sub_orig_term]

    def on_group_changed(self, current, previous):
        self.checked_checkbox.blockSignals(True)
        if current:
            # Получаем ID термина из данных, а не из текста (т.к. текст может быть переводом)
            term_id = current.data(Qt.ItemDataRole.UserRole)
            if term_id is None: # Fallback на всякий случай
                term_id = current.text()
            
            self.checked_checkbox.setChecked(term_id in self.checked_terms)
            self._display_group(current) # Передаем сам item, внутри разберемся
        else:
            self._display_group(None)
        self.checked_checkbox.blockSignals(False)

    def on_checked_changed(self, is_checked):
        item = self.left_list.currentItem()
        if not item: return
        
        # ИЗМЕНЕНИЕ: Получаем ID
        term = item.data(Qt.ItemDataRole.UserRole)
        if term is None: term = item.text()
        
        if is_checked:
            self.checked_terms.add(term)
            item.setBackground(_get_checked_color(self.left_list))
        else:
            self.checked_terms.discard(term)
            item.setBackground(self.left_list.palette().color(QtGui.QPalette.ColorRole.Base))
    
    def _display_group(self, current_item: QListWidgetItem):
        # --- Очищаем layout ---
        while self.right_panel_container_layout.count():
            child = self.right_panel_container_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        self.current_term = None
        if current_item:
            # ИЗМЕНЕНИЕ: Берем ID из UserRole
            self.current_term = current_item.data(Qt.ItemDataRole.UserRole)
            if self.current_term is None: self.current_term = current_item.text()
            
            term_val, data_val = self._get_current_value(self.current_term)

            if data_val:
                main_group = QGroupBox("Редактирование основного термина:")
                main_layout = QGridLayout(main_group)
                
                self.main_term_edit = SmartTextEdit(self.current_term, "original_term", term_val, self)
                self.main_trans_edit = SmartTextEdit(self.current_term, "rus", data_val.get('rus', ''), self)
                self.main_note_edit = SmartTextEdit(self.current_term, "note", data_val.get('note', ''), self)

                self.main_term_edit.data_committed.connect(self._on_data_committed)
                self.main_trans_edit.data_committed.connect(self._on_data_committed)
                self.main_note_edit.data_committed.connect(self._on_data_committed)

                main_layout.addWidget(QLabel("Термин:"), 0, 0)
                main_layout.addWidget(self.main_term_edit, 0, 1)
                main_layout.addWidget(QLabel("Перевод:"), 1, 0)
                main_layout.addWidget(self.main_trans_edit, 1, 1)
               
                note_label_widget = QWidget()
                note_label_layout = QHBoxLayout(note_label_widget)
                note_label_layout.setContentsMargins(0,0,0,0)
                note_label_layout.addWidget(QLabel("Примечание:"))
                note_label_layout.addStretch()
                
                if self.pymorphy_available:
                    gen_note_btn = QPushButton("📝")
                    gen_note_btn.setToolTip("Сгенерировать примечание")
                    gen_note_btn.setFixedSize(24, 24)
                    gen_note_btn.clicked.connect(self._on_generate_note_for_main_term_clicked)
                    note_label_layout.addWidget(gen_note_btn)

                main_layout.addWidget(note_label_widget, 2, 0)
                main_layout.addWidget(self.main_note_edit, 2, 1)
                
                delete_btn = QPushButton(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon), "")
                delete_btn.setToolTip("Удалить этот основной термин")
                delete_btn.clicked.connect(self.delete_main_term)
                main_layout.addWidget(delete_btn, 0, 2, 3, 1)
                self.right_panel_container_layout.addWidget(main_group)

            sub_terms_source = self.overlap_groups if self.view_mode == 'short_to_long' else self.inverted_groups
            sub_label_text = "Найден в следующих терминах:" if self.view_mode == 'short_to_long' else "Включает в себя следующие термины:"
            
            sub_group = QGroupBox(sub_label_text)
            sub_layout = QVBoxLayout(sub_group)
            
            self.sub_terms_table = QTableWidget()
            self.sub_terms_table.itemChanged.connect(self._on_sub_table_item_changed)
            delegate = ExpandingTextEditDelegate(self.sub_terms_table)
            self.sub_terms_table.setItemDelegate(delegate)

            self.sub_terms_table.setColumnCount(4)
            self.sub_terms_table.setHorizontalHeaderLabels(["Термин", "Перевод", "Примечание", "Действия"])
            header = self.sub_terms_table.horizontalHeader()
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
            
            visible_sub_terms = [t for t in sub_terms_source.get(self.current_term, []) if t not in self.deleted_terms]
            self.sub_terms_table.setRowCount(len(visible_sub_terms))
            delete_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon)
            drill_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView)

            for i, sub_term_orig in enumerate(visible_sub_terms):
                term_val, data_val = self._get_current_value(sub_term_orig)
                
                term_item = QTableWidgetItem(term_val)
                term_item.setData(Qt.ItemDataRole.UserRole, sub_term_orig)
                self.sub_terms_table.setItem(i, 0, term_item)
                self.sub_terms_table.setItem(i, 1, QTableWidgetItem(data_val.get('rus', '')))
                self.sub_terms_table.setItem(i, 2, QTableWidgetItem(data_val.get('note', '')))
                
                actions_widget = QWidget()
                actions_layout = QHBoxLayout(actions_widget)
                actions_layout.setContentsMargins(0,0,0,0)
                actions_layout.setSpacing(2)

                if self.pymorphy_available:
                    gen_btn = QPushButton("📝")
                    gen_btn.setToolTip("Сгенерировать примечание")
                    gen_btn.setFixedSize(24, 24)
                    gen_btn.clicked.connect(lambda checked=False, r=i: self._on_generate_note_in_sub_table_clicked(r))
                    actions_layout.addWidget(gen_btn)

                drill_btn = QPushButton(drill_icon, "")
                drill_btn.setToolTip("Перейти к этому термину")
                drill_btn.clicked.connect(lambda checked, t=sub_term_orig: self.drill_down(t))
                drill_btn.setDisabled(self.wizard_mode_active)
                actions_layout.addWidget(drill_btn)
                
                delete_btn = QPushButton(delete_icon, "")
                delete_btn.setToolTip("Удалить этот термин")
                delete_btn.clicked.connect(lambda checked, t=sub_term_orig: self.delete_sub_term(t))
                actions_layout.addWidget(delete_btn)
                
                self.sub_terms_table.setCellWidget(i, 3, actions_widget)
            
            sub_layout.addWidget(self.sub_terms_table, 1)
            self.sub_terms_table.resizeRowsToContents()
            
            # Добавляем группу с таблицей в основной layout контейнера с растяжением
            self.right_panel_container_layout.addWidget(sub_group, 1)
        else:
             self.right_panel_container_layout.addStretch(1)
    
    def _on_sub_table_item_changed(self, item: QTableWidgetItem):
        """Автоматически сохраняет изменения из таблицы под-терминов."""
        row, col = item.row(), item.column()
        if col not in [0, 1, 2]: return # Интересуют столбцы 0, 1, 2
        
        # Идентификатор хранится в столбце 0
        id_item = self.sub_terms_table.item(row, 0)
        if not id_item: return
        original_term_id = id_item.data(Qt.ItemDataRole.UserRole)
        
        current_term, current_data = self.pending_changes.get(
            original_term_id,
            (original_term_id, self.original_glossary.get(original_term_id, {}).copy())
        )

        if col == 0: current_term = item.text()
        elif col == 1: current_data['rus'] = item.text()
        elif col == 2: current_data['note'] = item.text()
        
        self.pending_changes[original_term_id] = (current_term, current_data)
    
    def delete_main_term(self):
        if self.current_term:
            self.deleted_terms.add(self.current_term)
            if self.current_term in self.pending_changes: del self.pending_changes[self.current_term]
            if self.wizard_mode_active: self.end_wizard_mode()
            self.populate_left_list()

    def delete_sub_term(self, term_to_delete):
        self.deleted_terms.add(term_to_delete)
        if term_to_delete in self.pending_changes: del self.pending_changes[term_to_delete]
        self._display_group(self.left_list.currentItem())

    def drill_down(self, term_to_find):
        if self.wizard_mode_active: return

        current_source = self.overlap_groups if self.view_mode == 'short_to_long' else self.inverted_groups
        if term_to_find not in current_source: self.toggle_view()
        
        # ИЗМЕНЕНИЕ: Ищем элемент перебором данных, так как текст может быть переводом
        found_item = None
        for i in range(self.left_list.count()):
            item = self.left_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == term_to_find:
                found_item = item
                break
        
        if found_item:
            self.left_list.setCurrentItem(found_item)

    def accept_changes(self):
        self.accept()

    def get_patch(self):
        """Возвращает список изменений (патч) для применения в MainWindow."""
        patch_list = []
    
        # Обрабатываем измененные записи
        for orig_term, (new_term, new_data) in self.pending_changes.items():
            if orig_term not in self.deleted_terms:
                before_state = {'original': orig_term, **self.original_glossary.get(orig_term, {})}
                after_state = {'original': new_term, **new_data}
                if before_state != after_state:
                    patch_list.append({'before': before_state, 'after': after_state})
    
        # Обрабатываем удаленные записи
        for term in self.deleted_terms:
            if term not in self.pending_changes: # Если термин был изменен, а потом удален, он уже обработан
                before_state = {'original': term, **self.original_glossary.get(term, {})}
                patch_list.append({'before': before_state, 'after': None})
    
        return patch_list


class ReverseConflictResolverDialog(QDialog):
    """
    Супер-диалог, который решает и обратные конфликты, и связывает "сирот".
    Версия 2.2 с пошаговым режимом "Визард".
    """
    def __init__(self, reverse_issues, original_glossary, parent=None, morph=None):
        super().__init__(parent)
        self.reverse_issues = reverse_issues
        self.original_glossary_list = original_glossary
        self.morph = morph
        
        self.entry_map = { self._get_entry_id(e): e for e in self.original_glossary_list }
        
        self.pending_changes = {}
        self.deleted_entries = set()
        self.checked_items = set() # Для отметки проверенных
        
        # --- Состояние для пошагового режима ---
        self.wizard_mode_active = False
        self.wizard_items = []
        self.wizard_current_index = -1

        self.setWindowTitle("Шаг 2: Обратные конфликты и связывание")
        self.setMinimumSize(1200, 800)
        self.init_ui()

    def _get_entry_id(self, entry):
        return tuple(entry.get(k, '') for k in ['original', 'rus', 'note'])

    def init_ui(self):
        main_layout = QHBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.addWidget(QLabel("<b>Проблемные переводы:</b>"))
        self.translations_list = QListWidget()
        self.translations_list.currentItemChanged.connect(self.on_group_changed)
        self.translations_list.itemClicked.connect(self.on_item_clicked)
        left_layout.addWidget(self.translations_list)
        
        right_panel = QWidget()
        self.right_layout = QVBoxLayout(right_panel)
        
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([300, 900])
        main_layout.addWidget(splitter)
        
        top_controls_layout = QHBoxLayout()
        
        self.normal_mode_widget = QWidget()
        normal_layout = QHBoxLayout(self.normal_mode_widget)
        normal_layout.setContentsMargins(0,0,0,0)
        self.start_wizard_button = QPushButton("▶ Начать пошаговое разрешение")
        self.start_wizard_button.clicked.connect(self.start_wizard_mode)
        self.checked_checkbox = QCheckBox("Проверено")
        self.checked_checkbox.toggled.connect(self.on_checked_changed)
        normal_layout.addWidget(self.start_wizard_button)
        normal_layout.addStretch()
        normal_layout.addWidget(self.checked_checkbox)

        self.wizard_mode_widget = QWidget()
        wizard_layout = QHBoxLayout(self.wizard_mode_widget)
        wizard_layout.setContentsMargins(0,0,0,0)
        self.wizard_prev_button = QPushButton("< Назад")
        self.wizard_prev_button.clicked.connect(self.wizard_go_prev)
        self.wizard_progress_label = QLabel("Шаг X из Y")
        self.wizard_next_button = QPushButton("Далее >")
        self.wizard_next_button.clicked.connect(self.wizard_go_next)
        finish_wizard_button = QPushButton("Завершить пошаговый режим")
        finish_wizard_button.clicked.connect(self.end_wizard_mode)
        wizard_layout.addWidget(self.wizard_prev_button)
        wizard_layout.addWidget(self.wizard_progress_label)
        wizard_layout.addWidget(self.wizard_next_button)
        wizard_layout.addStretch()
        wizard_layout.addWidget(finish_wizard_button)

        self.top_controls_stack = QStackedWidget()
        self.top_controls_stack.addWidget(self.normal_mode_widget)
        self.top_controls_stack.addWidget(self.wizard_mode_widget)
        top_controls_layout.addWidget(self.top_controls_stack, 1)
        self.right_layout.addLayout(top_controls_layout)
        
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Принять изменения")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept_changes)
        buttons.rejected.connect(self.reject)
        
        # --- ИСПРАВЛЕНИЕ: Создаем постоянный контейнер и его layout ---
        self.right_panel_container = QWidget()
        self.right_panel_container_layout = QVBoxLayout(self.right_panel_container)
        self.right_layout.addWidget(self.right_panel_container, 1) # Добавляем контейнер с растяжением
        self.right_layout.addWidget(buttons)
        
        self.populate_list()
        self.end_wizard_mode()

    def start_wizard_mode(self):
        self.wizard_items = [self.translations_list.item(i).text() for i in range(self.translations_list.count()) if self.translations_list.item(i).text() not in self.checked_items]
        
        if not self.wizard_items:
            QMessageBox.information(self, "Все готово", "Все конфликты в этом списке уже помечены как проверенные.")
            return
            
        self.wizard_mode_active = True
        self.wizard_current_index = 0
        self.translations_list.setEnabled(False)
        self.top_controls_stack.setCurrentWidget(self.wizard_mode_widget)
        self._show_wizard_step()

    def end_wizard_mode(self):
        self.wizard_mode_active = False
        self.wizard_items = []
        self.wizard_current_index = -1
        self.translations_list.setEnabled(True)
        self.top_controls_stack.setCurrentWidget(self.normal_mode_widget)

    def wizard_go_next(self):
        self.checked_checkbox.setChecked(True)
        
        if self.wizard_current_index < len(self.wizard_items) - 1:
            self.wizard_current_index += 1
            self._show_wizard_step()
        else:
            QMessageBox.information(self, "Завершено", "Вы просмотрели все оставшиеся конфликты.")
            self.end_wizard_mode()
    
    def wizard_go_prev(self):
        # Просто переходим назад, ПРЕДВАРИТЕЛЬНО СОХРАНИВ ИЗМЕНЕНИЯ
        if self.wizard_current_index > 0:
            self.wizard_current_index -= 1
            self._show_wizard_step()

    def _show_wizard_step(self):
        if not self.wizard_mode_active or not self.wizard_items: return
        self.wizard_progress_label.setText(f"Шаг {self.wizard_current_index + 1} из {len(self.wizard_items)}")
        self.wizard_prev_button.setEnabled(self.wizard_current_index > 0)
        self.wizard_next_button.setText("Далее >" if self.wizard_current_index < len(self.wizard_items) - 1 else "Завершить")

        term_to_show = self.wizard_items[self.wizard_current_index]
        items = self.translations_list.findItems(term_to_show, Qt.MatchFlag.MatchExactly)
        if items: self.translations_list.setCurrentItem(items[0])

    def _on_table_item_changed(self, item: QTableWidgetItem):
        """Автоматически сохраняет изменения из таблицы."""
        row, col = item.row(), item.column()
        # ИЗМЕНЕНИЕ: Нам нужны столбцы 0, 1, 2 (были 1, 2, 3)
        if col not in [0, 1, 2]: return
        
        # ИЗМЕНЕНИЕ: ID теперь в столбце 0
        original_item = self.complete_table.item(row, 0)
        if not original_item: return
        
        original_id_tuple = original_item.data(Qt.ItemDataRole.UserRole)
        
        current_data = self.pending_changes.get(original_id_tuple, self.entry_map.get(original_id_tuple, {})).copy()
        
        # ИЗМЕНЕНИЕ: Смещаем проверку колонок
        if col == 0: current_data['original'] = item.text()
        elif col == 1: current_data['rus'] = item.text()
        elif col == 2: current_data['note'] = item.text()
        
        self.pending_changes[original_id_tuple] = current_data

    def populate_list(self):
        self.translations_list.clear()
        for key in sorted(self.reverse_issues.keys()):
            item = QListWidgetItem(key)
            if key in self.checked_items:
                item.setBackground(_get_checked_color(self.translations_list))
            self.translations_list.addItem(item)
        if self.translations_list.count() > 0:
            self.translations_list.setCurrentRow(0)

    def on_checked_changed(self, is_checked):
        item = self.translations_list.currentItem()
        if not item: return
        key = item.text()
        if is_checked:
            self.checked_items.add(key)
            item.setBackground(_get_checked_color(self.translations_list))
        else:
            self.checked_items.discard(key)
            # Сбрасываем на базовый цвет палитры, а не на белый
            item.setBackground(self.translations_list.palette().color(QtGui.QPalette.ColorRole.Base))
    
    def on_item_clicked(self, item):
        """Обновляет состояние галочки при клике на элемент."""
        self.checked_checkbox.setChecked(item.text() in self.checked_items)

    def _save_current_changes(self):
        if not hasattr(self, 'complete_table') or not self.complete_table: return
        
        for i in range(self.complete_table.rowCount()):
            # ИЗМЕНЕНИЕ: ID теперь в столбце 0
            original_item = self.complete_table.item(i, 0)
            if not original_item: continue
            
            original_id_tuple = original_item.data(Qt.ItemDataRole.UserRole)
            if original_id_tuple in self.deleted_entries: continue
            
            new_data = {
                # ИЗМЕНЕНИЕ: Считываем данные из колонок 0, 1, 2
                "original": self.complete_table.item(i, 0).text(),
                "rus": self.complete_table.item(i, 1).text(),
                "note": self.complete_table.item(i, 2).text()
            }
            
            original_entry = self.entry_map[original_id_tuple]
            last_known_data = self.pending_changes.get(original_id_tuple, original_entry)

            if new_data['original'] != last_known_data.get('original', '') or \
               new_data['rus'] != last_known_data.get('rus', '') or \
               new_data['note'] != last_known_data.get('note', ''):
                updated_entry = last_known_data.copy()
                updated_entry.update(new_data)
                self.pending_changes[original_id_tuple] = updated_entry
            elif original_id_tuple in self.pending_changes:
                if self.pending_changes[original_id_tuple] == original_entry:
                    del self.pending_changes[original_id_tuple]

    def on_group_changed(self, current, previous):
        
        self.checked_checkbox.blockSignals(True)
        if current:
            self.checked_checkbox.setChecked(current.text() in self.checked_items)
        self.checked_checkbox.blockSignals(False)
        
        self._display_group(current)
    
    def _display_group(self, current_item: QListWidgetItem):
        # --- ИСПРАВЛЕНИЕ: Очищаем layout вместо замены виджета ---
        while self.right_panel_container_layout.count():
            child = self.right_panel_container_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        
        self.complete_table = None

        if current_item:
            rus = current_item.text()
            issue_data = self.reverse_issues[rus]
            
            complete_group = QGroupBox(f"Термины с переводом '{rus}'")
            complete_layout = QVBoxLayout(complete_group)
            self.complete_table = self._create_complete_table(issue_data['complete'])
            complete_layout.addWidget(self.complete_table, 1) # Таблица внутри группы должна растягиваться
            
            has_orphans = bool(issue_data.get('orphans'))
            
            # Если есть "сироты", то основная таблица не растягивается,
            # а если их нет, то она занимает все место.
            stretch_factor_for_complete_group = 0 if has_orphans else 1
            self.right_panel_container_layout.addWidget(complete_group, stretch_factor_for_complete_group)
            
            if has_orphans:
                orphan_group = QGroupBox("Записи для связывания (без оригинала)")
                orphan_layout = QVBoxLayout(orphan_group)
                orphan_table = self._create_orphan_table(issue_data['orphans'])
                orphan_layout.addWidget(orphan_table, 1) # Таблица сирот тоже растягивается внутри своей группы
                # Группа с сиротами всегда растягивается, если она есть
                self.right_panel_container_layout.addWidget(orphan_group, 1)
        else:
            # Если ничего не выбрано, добавляем растяжение, чтобы кнопки были внизу
            self.right_panel_container_layout.addStretch(1)
        
        
        
    def _create_table_widget(self, headers):
        table = QTableWidget()
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        delegate = ExpandingTextEditDelegate(table)
        # ИЗМЕНЕНИЕ: Индексы сместились, теперь делегат нужен для 0, 1 и 2 колонок
        table.setItemDelegateForColumn(0, delegate) 
        table.setItemDelegateForColumn(1, delegate) 
        table.setItemDelegateForColumn(2, delegate) 
        return table

    def _create_complete_table(self, entries):
        # ИЗМЕНЕНИЕ: Удалена пустая колонка для чекбокса из заголовков
        table = self._create_table_widget(["Оригинал", "Перевод", "Примечание", "Действия"])
        header = table.horizontalHeader()
        # ИЗМЕНЕНИЕ: Индексы сместились на -1
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch) # Был 1
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch) # Был 2
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch) # Был 3
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents) # Был 4
        
        table.itemChanged.connect(self._on_table_item_changed)
        
        visible_entries = [e for e in entries if self._get_entry_id(e) not in self.deleted_entries]
        table.setRowCount(len(visible_entries))
        
        table.blockSignals(True)
        for i, entry in enumerate(visible_entries):
            entry_id = self._get_entry_id(entry)
            current_data = self.pending_changes.get(entry_id, entry)

            # ИЗМЕНЕНИЕ: Чекбокс и его виджет полностью удалены

            original_item = QTableWidgetItem(current_data['original'])
            original_item.setData(Qt.ItemDataRole.UserRole, entry_id)
            table.setItem(i, 0, original_item) # Был столбец 1
            table.setItem(i, 1, QTableWidgetItem(current_data['rus'])) # Был столбец 2
            table.setItem(i, 2, QTableWidgetItem(current_data.get('note', ''))) # Был столбец 3
            
            actions_widget = QWidget()
            actions_layout = QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(0, 0, 0, 0); actions_layout.setSpacing(2)
            if self.morph:
                gen_btn = QPushButton("📝"); gen_btn.setToolTip("Сгенерировать примечание")
                gen_btn.setFixedSize(24, 24)
                gen_btn.clicked.connect(lambda ch, r=i: self._on_generate_note_clicked(r))
                actions_layout.addWidget(gen_btn)

            delete_btn = QPushButton(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon), "")
            delete_btn.setToolTip(f"Удалить термин '{current_data['original']}'")
            delete_btn.clicked.connect(lambda ch, eid=entry_id: self._delete_entry(eid))
            actions_layout.addWidget(delete_btn)
            table.setCellWidget(i, 3, actions_widget) # Был столбец 4
        table.blockSignals(False)
            
        table.resizeRowsToContents()
        return table

    def _create_orphan_table(self, entries):
        table = self._create_table_widget(["Примечание", "Действия"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)

        visible_entries = [e for e in entries if self._get_entry_id(e) not in self.deleted_entries]
        table.setRowCount(len(visible_entries))

        for i, entry in enumerate(visible_entries):
            table.setItem(i, 0, QTableWidgetItem(entry['note']))
            
            buttons_widget = QWidget(); buttons_layout = QHBoxLayout(buttons_widget)
            buttons_layout.setContentsMargins(2, 0, 2, 0); buttons_layout.setSpacing(4)
            
            # ИЗМЕНЕНИЕ: Вернули кнопку, но с новой подписью
            apply_selected_btn = QPushButton("Применить к выделенным")
            apply_selected_btn.setToolTip("Скопировать это примечание в строки, выделенные курсором (синим цветом) в верхней таблице.")
            apply_selected_btn.clicked.connect(lambda ch, orphan_entry=entry: self._apply_note_to_selected(orphan_entry))
            
            apply_all_btn = QPushButton("Применить ко всем")
            apply_all_btn.setToolTip("Скопировать это примечание во ВСЕ термины в верхней таблице и удалить эту строку.")
            apply_all_btn.clicked.connect(lambda ch, orphan_entry=entry: self._apply_note_to_all(orphan_entry))
            
            buttons_layout.addWidget(apply_selected_btn)
            buttons_layout.addWidget(apply_all_btn)
            table.setCellWidget(i, 1, buttons_widget)
            
        table.resizeRowsToContents()
        return table

    def _apply_note_to_selected(self, orphan_entry):
        orphan_id = self._get_entry_id(orphan_entry)
        if not self.complete_table or orphan_id in self.deleted_entries: return
        
        # ИЗМЕНЕНИЕ: Получаем индексы уникальных строк из выделенных ячеек
        selected_rows = set()
        for item in self.complete_table.selectedItems():
            selected_rows.add(item.row())
            
        if not selected_rows:
            QMessageBox.information(self, "Нет выделения", "Пожалуйста, выделите курсором (мышкой) строки в верхней таблице, куда нужно вставить это примечание.")
            return

        applied = False
        orphan_note = orphan_entry.get('note', '')

        for row in selected_rows:
            # ИЗМЕНЕНИЕ: Столбец примечания теперь имеет индекс 2 (после удаления чекбоксов)
            note_item = self.complete_table.item(row, 2)
            if note_item:
                note_item.setText(orphan_note)
                applied = True
        
        if applied:
            self.deleted_entries.add(orphan_id)
            self._display_group(self.translations_list.currentItem())

    def _apply_note_to_all(self, orphan_entry):
        orphan_id = self._get_entry_id(orphan_entry)
        if not self.complete_table or orphan_id in self.deleted_entries: return

        if self.complete_table.rowCount() == 0:
            QMessageBox.information(self, "Нет записей", "В верхней таблице нет записей для применения.")
            return

        orphan_note = orphan_entry.get('note', '')
        for i in range(self.complete_table.rowCount()):
             # --- ИЗМЕНЕНИЕ: Обновляем item напрямую ---
            note_item = self.complete_table.item(i, 3)
            if note_item:
                note_item.setText(orphan_note)

        self.deleted_entries.add(orphan_id)
        self._display_group(self.translations_list.currentItem())
            
    def _delete_entry(self, entry_id):
        self.deleted_entries.add(entry_id)
        if entry_id in self.pending_changes: del self.pending_changes[entry_id]
        if self.wizard_mode_active: self.end_wizard_mode()
        self._display_group(self.translations_list.currentItem())

    # --- ИЗМЕНЕНИЕ: Метод `_create_note_widget` больше не нужен, удаляем его ---
    # def _create_note_widget(…) -> Удалено

    def _on_generate_note_clicked(self, row):
        main_window = self.parent()
        if not main_window or not hasattr(self, 'complete_table'): return
        
        # ИЗМЕНЕНИЕ: Перевод теперь в столбце 1 (был 2)
        translation_item = self.complete_table.item(row, 1)
        rus = translation_item.text() if translation_item else ""
        note_text = main_window._generate_note_logic(rus)
        
        if note_text:
            # ИЗМЕНЕНИЕ: Примечание теперь в столбце 2 (было 3)
            note_item = self.complete_table.item(row, 2)
            if note_item:
                note_item.setText(note_text)
            
            self.complete_table.resizeRowToContents(row)

    def accept_changes(self):
        self.accept()

    def get_patch(self):
        """
        Возвращает список изменений (патч) для применения в MainWindow.
        ИСПРАВЛЕННАЯ ВЕРСИЯ: Работает с self.deleted_entries и self.entry_map.
        """
        patch_list = []
        
        # Обрабатываем измененные записи
        for original_id, new_data in self.pending_changes.items():
            if original_id not in self.deleted_entries:
                before_state = self.entry_map.get(original_id)
                if before_state and before_state != new_data:
                    patch_list.append({'before': before_state, 'after': new_data})

        # Обрабатываем удаленные записи (которые не были изменены перед удалением)
        for deleted_id in self.deleted_entries:
            if deleted_id not in self.pending_changes:
                before_state = self.entry_map.get(deleted_id)
                if before_state:
                    patch_list.append({'before': before_state, 'after': None})
        
        return patch_list


class DirectConflictResolverDialog(QDialog):
    """
    Супер-диалог для разрешения прямых конфликтов с двумя режимами:
    1. Пошаговый "Визард" с кнопками-вариантами (по умолчанию).
    2. Классический табличный режим для опытных пользователей.
    """
    def __init__(self, conflicts, parent=None, morph=None):
        super().__init__(parent)
        self.conflicts = conflicts
        self.morph = morph
        self.resolved_glossary = {}
        
        # --- Состояние для пошагового режима ---
        self.wizard_conflicts_list = list(self.conflicts.keys())
        self.wizard_current_index = 0

        self.setWindowTitle("Шаг 1: Помощник разрешения конфликтов")
        self.setMinimumSize(1000, 600)
        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        self.stacked_widget = QStackedWidget()
        
        self.wizard_widget = self._create_wizard_view()
        self.table_widget = self._create_table_view()
        self.stacked_widget.addWidget(self.wizard_widget)
        self.stacked_widget.addWidget(self.table_widget)
        
        main_layout.addWidget(self.stacked_widget)
        
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Принять изменения")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept_resolution)
        buttons.rejected.connect(self.reject)
        
        main_layout.addWidget(buttons)
        
        if self.wizard_conflicts_list:
            self._display_wizard_step()
        else:
            self.stacked_widget.setCurrentWidget(self.table_widget)

    def _create_wizard_view(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        top_bar = QHBoxLayout()
        self.wizard_progress_label = QLabel()
        
        # --- НОВАЯ КНОПКА: АВТО-СХЛОПЫВАНИЕ ---
        auto_resolve_btn = QPushButton("⚡ Авто-схлопывание")
        auto_resolve_btn.setToolTip(
            "Автоматически разрешает конфликты по частоте и наличию примечаний:\n"
            "1. Приоритет отдается вариантам с примечаниями.\n"
            "   (Если у частых вариантов нет примечаний, а у редких есть — побеждают редкие).\n"
            "2. Среди равных выбирается наиболее частый.\n"
            "3. При полном совпадении выбирается вариант с самым коротким примечанием."
        )
        auto_resolve_btn.clicked.connect(self.auto_resolve_by_frequency)
        top_bar.addWidget(auto_resolve_btn)
        # --------------------------------------

        top_bar.addStretch()
        switch_to_table_button = QPushButton("Перейти в табличный режим 🗂️")
        switch_to_table_button.clicked.connect(self.switch_to_table_view)
        
        top_bar.addWidget(self.wizard_progress_label)
        top_bar.addWidget(switch_to_table_button)
        layout.addLayout(top_bar)
        
        self.wizard_term_label = QLabel()
        self.wizard_term_label.setStyleSheet("font-size: 16pt; font-weight: bold;")
        layout.addWidget(self.wizard_term_label)
        
        options_group = QGroupBox("Выберите действие (один клик = переход к следующему)")
        self.wizard_options_layout = QVBoxLayout(options_group)
        layout.addWidget(options_group)

        custom_group = QGroupBox("Или доработайте/введите свой вариант здесь")
        custom_layout = QGridLayout(custom_group)
        self.wizard_custom_edit = QLineEdit()
        self.wizard_custom_edit.setPlaceholderText("Итоговый перевод…")
        
        self.wizard_note_edit = QLineEdit()
        self.wizard_note_edit.setToolTip(
            "Нажмите '✎' у варианта выше, чтобы загрузить его сюда для доработки.\n"
            "Нажатие на кнопку 'Сгенерировать 📝' перезапишет содержимое этого поля."
        )

        custom_apply_button = QPushButton("Применить и далее")
        custom_apply_button.clicked.connect(self._wizard_apply_custom)
        
        custom_layout.addWidget(QLabel("Перевод:"), 0, 0)
        custom_layout.addWidget(self.wizard_custom_edit, 0, 1)

        note_label_widget = QWidget()
        note_label_layout = QHBoxLayout(note_label_widget)
        note_label_layout.setContentsMargins(0,0,0,0)
        note_label_layout.addWidget(QLabel("Примечание:"))
        if self.morph:
            gen_note_btn = QPushButton("📝")
            gen_note_btn.setToolTip("Сгенерировать примечание на основе перевода в поле слева")
            gen_note_btn.setFixedSize(24, 24)
            gen_note_btn.clicked.connect(self._wizard_generate_note)
            note_label_layout.addStretch()
            note_label_layout.addWidget(gen_note_btn)

        custom_layout.addWidget(note_label_widget, 1, 0)
        custom_layout.addWidget(self.wizard_note_edit, 1, 1)
        custom_layout.addWidget(custom_apply_button, 2, 0, 1, 2)
        layout.addWidget(custom_group)
        
        layout.addStretch(1)

        nav_bar = QHBoxLayout()
        self.wizard_prev_button = QPushButton("< Назад")
        self.wizard_prev_button.clicked.connect(self._wizard_go_prev)
        self.wizard_skip_button = QPushButton("Пропустить >")
        self.wizard_skip_button.clicked.connect(self._wizard_go_next)
        nav_bar.addWidget(self.wizard_prev_button)
        nav_bar.addStretch()
        nav_bar.addWidget(self.wizard_skip_button)
        layout.addLayout(nav_bar)
        
        return widget

    def _create_table_view(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)

        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel(f"<b>Найдено {len(self.conflicts)} терминов с несколькими вариантами перевода.</b>"))
        top_bar.addStretch()
        
        # --- НОВАЯ КНОПКА: АВТО-СХЛОПЫВАНИЕ ---
        auto_resolve_btn = QPushButton("⚡ Авто-схлопывание")
        auto_resolve_btn.setToolTip(
            "Автоматически разрешает конфликты по частоте:\n"
            "1. Выбирается вариант, который встречается чаще остальных.\n"
            "   (при этом берется версия с самым коротким непустым примечанием)\n"
            "2. Если есть ничья по частоте — удаляются все менее популярные варианты."
        )
        auto_resolve_btn.clicked.connect(self.auto_resolve_by_frequency)
        top_bar.addWidget(auto_resolve_btn)
        # --------------------------------------

        switch_to_wizard_button = QPushButton("Перейти в пошаговый режим ✨")
        switch_to_wizard_button.clicked.connect(self.switch_to_wizard_view)
        top_bar.addWidget(switch_to_wizard_button)
        layout.addLayout(top_bar)

        self.table = QTableWidget()
        delegate = ExpandingTextEditDelegate(self.table)
        self.table.setItemDelegateForColumn(3, delegate)
        self.table.setItemDelegateForColumn(4, delegate)

        headers = ["Термин", "Варианты", "Выбор", "Свой вариант", "Примечание", "Действия"]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        
        layout.addWidget(self.table)
        self._populate_table()
        return widget

    def _populate_table(self):
        self.table.setRowCount(len(self.conflicts))
        delete_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon)

        for i, (term, trans_options) in enumerate(self.conflicts.items()):
            term_item = QTableWidgetItem(term)
            term_item.setFlags(term_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(i, 0, term_item)
            
            variants_text = " | ".join([opt['rus'] for opt in trans_options])
            variants_item = QTableWidgetItem(variants_text)
            variants_item.setFlags(variants_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(i, 1, variants_item)
            
            combo = NoScrollComboBox()
            combo.addItems([opt['rus'] for opt in trans_options])
            combo.addItem("[Свой вариант]")
            self.table.setCellWidget(i, 2, combo)
            
            custom_variant_item = QTableWidgetItem("")
            custom_variant_item.setFlags(custom_variant_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(i, 3, custom_variant_item)

            initial_note = trans_options[0].get('note', '')
            self.table.setItem(i, 4, QTableWidgetItem(initial_note))

            actions_widget = QWidget()
            actions_layout = QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(0,0,0,0)
            if self.morph:
                gen_note_btn = QPushButton("📝"); gen_note_btn.setFixedSize(24, 24)
                gen_note_btn.setToolTip("Сгенерировать примечание")
                gen_note_btn.clicked.connect(lambda ch, r=i: self.generate_note_for_table(r))
                actions_layout.addWidget(gen_note_btn)
            
            delete_btn = QPushButton(delete_icon, "")
            delete_btn.setToolTip("Удалить этот конфликт из списка")
            delete_btn.clicked.connect(lambda checked, row=i: self.table.removeRow(row))
            actions_layout.addWidget(delete_btn)
            self.table.setCellWidget(i, 5, actions_widget)

            combo.setProperty("options", trans_options)
            combo.setProperty("row", i)
            combo.currentIndexChanged.connect(self.on_combo_changed_for_table)

        self.table.resizeRowsToContents()
    
    def auto_resolve_by_frequency(self):
        resolved_count = 0
        reduced_count = 0
        
        # Работаем с копией ключей
        for term in list(self.conflicts.keys()):
            options = self.conflicts[term]
            if not options: continue
            
            # 1. Группируем по частоте: {число_вхождений: [варианты_перевода]}
            counts = defaultdict(int)
            for opt in options:
                counts[opt['rus']] += 1
            
            # Группировка вариантов по количеству вхождений
            # freq_map: { 5: ['перевод1'], 3: ['перевод2', 'перевод3'] }
            freq_map = defaultdict(list)
            for rus_text, count in counts.items():
                freq_map[count].append(rus_text)
            
            # Сортируем частоты от больших к меньшим
            sorted_freqs = sorted(freq_map.keys(), reverse=True)
            
            winning_translations = []
            
            # 2. Логика "Приоритет примечаний"
            # Ищем самую высокую частоту, в которой ХОТЯ БЫ ОДИН вариант имеет примечание.
            found_tier_with_notes = False
            for freq in sorted_freqs:
                candidates = freq_map[freq]
                
                # Проверяем, есть ли у кого-то из этих кандидатов примечание в исходных опциях
                has_note = False
                for opt in options:
                    if opt['rus'] in candidates and opt.get('note', '').strip():
                        has_note = True
                        break
                
                if has_note:
                    # Нашли "победный" слой (высокая частота + наличие примечаний)
                    winning_translations = candidates
                    found_tier_with_notes = True
                    break
            
            # Если ни в одном слое нет примечаний, берем просто самый частый слой
            if not found_tier_with_notes:
                winning_translations = freq_map[sorted_freqs[0]]

            # 3. Финализация выбора
            # Сценарий А: Есть один победитель в выбранной категории
            if len(winning_translations) == 1:
                winner_rus = winning_translations[0]
                candidates = [opt for opt in options if opt['rus'] == winner_rus]
                
                # Ищем кандидата с самым коротким, но НЕ пустым примечанием
                with_notes = [opt for opt in candidates if opt.get('note', '').strip()]
                
                final_opt = None
                if with_notes:
                    with_notes.sort(key=lambda x: len(x['note']))
                    final_opt = with_notes[0]
                else:
                    final_opt = candidates[0]
                
                self.resolved_glossary[term] = {
                    "rus": final_opt['rus'],
                    "note": final_opt.get('note', '')
                }
                del self.conflicts[term]
                resolved_count += 1
                
            # Сценарий Б: Ничья (несколько разных переводов выиграли)
            else:
                # Оставляем только победителей, удаляем проигравших
                new_options = [opt for opt in options if opt['rus'] in winning_translations]
                if len(new_options) < len(options):
                    self.conflicts[term] = new_options
                    reduced_count += 1

        # Обновление UI
        self._populate_table()
        
        # Обновляем список для визарда
        self.wizard_conflicts_list = list(self.conflicts.keys())
        self.wizard_current_index = 0
        if self.wizard_conflicts_list:
            self._display_wizard_step()
        else:
            # Если все решилось, обновляем UI визарда на пустое состояние
             self.wizard_term_label.setText("Все конфликты разрешены!")
             while self.wizard_options_layout.count():
                 self.wizard_options_layout.takeAt(0).widget().deleteLater()
             self.wizard_custom_edit.clear()
             self.wizard_note_edit.clear()
             self.wizard_progress_label.setText("Готово")
        
        # Обновляем заголовок таблицы (если виджет существует)
        if hasattr(self, 'table_widget') and self.table_widget.layout():
            top_bar_layout = self.table_widget.layout().itemAt(0).layout()
            if top_bar_layout and top_bar_layout.itemAt(0) and isinstance(top_bar_layout.itemAt(0).widget(), QLabel):
                 top_bar_layout.itemAt(0).widget().setText(f"<b>Найдено {len(self.conflicts)} терминов с несколькими вариантами перевода.</b>")

        QMessageBox.information(self, "Результат схлопывания", 
                                f"Автоматически разрешено конфликтов: {resolved_count}\n"
                                f"Упрощено (удалены слабые варианты): {reduced_count}\n\n"
                                f"Осталось разобрать вручную: {len(self.conflicts)}")
                                
    def switch_to_table_view(self):
        self._wizard_save_current_note()
        self.stacked_widget.setCurrentWidget(self.table_widget)

    def switch_to_wizard_view(self):
        self._save_table_changes()
        self.stacked_widget.setCurrentWidget(self.wizard_widget)
        self._display_wizard_step()

    def _display_wizard_step(self):
        if not (0 <= self.wizard_current_index < len(self.wizard_conflicts_list)):
             return

        term = self.wizard_conflicts_list[self.wizard_current_index]
        options = self.conflicts[term]

        self.wizard_progress_label.setText(f"<b>Шаг {self.wizard_current_index + 1} из {len(self.wizard_conflicts_list)}</b>")
        self.wizard_term_label.setText(term)
        self.wizard_prev_button.setEnabled(self.wizard_current_index > 0)
        is_last_step = self.wizard_current_index == len(self.wizard_conflicts_list) - 1
        self.wizard_skip_button.setText("Завершить" if is_last_step else "Пропустить >")
        
        while self.wizard_options_layout.count():
            self.wizard_options_layout.takeAt(0).widget().deleteLater()
        
        self.wizard_custom_edit.clear()
        self.wizard_note_edit.clear()
        
        for option in options:
            btn_container = QWidget()
            btn_layout = QHBoxLayout(btn_container)
            btn_layout.setContentsMargins(0,0,0,0)
            btn_layout.setSpacing(4)

            # <<< ИЗМЕНЕНИЕ 3: Примечания убраны с кнопки и перенесены в подсказку >>>
            btn_text = option['rus']
            
            # 1. Кнопка "Выбрать как есть"
            main_button = QPushButton(btn_text)
            
            tooltip_text = f"Выбрать этот вариант: '{option['rus']}'"
            if option.get('note'):
                tooltip_text += f"\nПримечание: {option['note']}"
            main_button.setToolTip(tooltip_text)
            main_button.clicked.connect(lambda ch, t=term, o=option: self._wizard_select_and_go_next(t, o))
            
            # 2. Кнопка "Выбрать + Авто-примечание" (только если есть Pymorphy)
            if self.morph:
                gen_button_text = "Выбрать + 📝 Авто-примечание"
                if option.get('note', '').strip():
                    gen_button_text += " (замена)"
                
                gen_button = QPushButton(gen_button_text)
                gen_button.setToolTip("Выбрать этот перевод, сгенерировать для него новое примечание и перейти к следующему")
                gen_button.clicked.connect(lambda ch, t=term, o=option: self._wizard_select_generate_and_go_next(t, o))

            # 3. Кнопка "Загрузить для редактирования"
            edit_button = QPushButton("✎")
            edit_button.setFixedSize(28, 28)
            edit_button.setToolTip("Загрузить этот вариант в поля ниже для ручного редактирования")
            edit_button.clicked.connect(lambda ch, o=option: self._wizard_populate_fields_for_editing(o))
            
            btn_layout.addWidget(main_button, 6)
            if self.morph:
                btn_layout.addWidget(gen_button, 4)
            btn_layout.addWidget(edit_button, 1)

            self.wizard_options_layout.addWidget(btn_container)
            # <<< КОНЕЦ ИЗМЕНЕНИЯ 3 >>>
    
    def _wizard_select_and_go_next(self, term, option):
        self.resolved_glossary[term] = {
            "rus": option['rus'],
            "note": option.get('note', '')
        }
        self._wizard_go_next(skip_save=True)

    def _wizard_select_generate_and_go_next(self, term, option):
        main_window = self.parent()
        rus = option['rus']
        note_text = ""
        if main_window:
            note_text = main_window._generate_note_logic(rus)
        
        self.resolved_glossary[term] = {
            "rus": rus,
            "note": note_text
        }
        self._wizard_go_next(skip_save=True)

    def _wizard_populate_fields_for_editing(self, option):
        self.wizard_custom_edit.setText(option['rus'])
        self.wizard_note_edit.setText(option.get('note', ''))
        self.wizard_custom_edit.setFocus()

    def _wizard_apply_custom(self):
        term = self.wizard_conflicts_list[self.wizard_current_index]
        rus = self.wizard_custom_edit.text().strip()
        if not rus:
            QMessageBox.warning(self, "Пустое поле", "Пожалуйста, введите или выберите вариант перевода.")
            return
        
        self.resolved_glossary[term] = {
            "rus": rus,
            "note": self.wizard_note_edit.text().strip()
        }
        self._wizard_go_next(skip_save=True)

    def _wizard_save_current_note(self):
        if not (0 <= self.wizard_current_index < len(self.wizard_conflicts_list)):
            return
        
        term = self.wizard_conflicts_list[self.wizard_current_index]
        
        # Получаем текст из полей ручного ввода
        custom_translation = self.wizard_custom_edit.text().strip()
        custom_note = self.wizard_note_edit.text().strip()

        # Если в поле ручного перевода есть текст, мы ОБЯЗАНЫ его сохранить.
        # Это исправляет баг, когда ручной ввод без нажатия "Применить" терялся.
        if custom_translation:
            self.resolved_glossary[term] = {
                "rus": custom_translation,
                "note": custom_note
            }
        # Если поле ручного перевода пусто, но термин уже был разрешен ранее
        # (например, пользователь нажал кнопку, а потом вернулся и стер текст),
        # мы также обновляем его примечание, если оно было введено.
        elif term in self.resolved_glossary:
             self.resolved_glossary[term]['note'] = custom_note

    def _wizard_go_next(self, skip_save=False):
        if not skip_save:
            self._wizard_save_current_note()
            
        if self.wizard_current_index < len(self.wizard_conflicts_list) - 1:
            self.wizard_current_index += 1
            self._display_wizard_step()
        else:
            QMessageBox.information(self, "Завершено", "Вы просмотрели все конфликты.")
            self.accept_resolution()

    def _wizard_go_prev(self):
        self._wizard_save_current_note()
        if self.wizard_current_index > 0:
            self.wizard_current_index -= 1
            self._display_wizard_step()

    def _wizard_generate_note(self):
        rus = self.wizard_custom_edit.text().strip()
        if not rus:
            QMessageBox.warning(self, "Нет слова", "Поле перевода пусто. Загрузите вариант или введите свой.")
            return

        main_window = self.parent()
        if main_window:
            note_text = main_window._generate_note_logic(rus)
            self.wizard_note_edit.setText(note_text)

    def on_combo_changed_for_table(self, index):
        combo = self.sender()
        row = combo.property("row")
        options = combo.property("options")
        custom_variant_item = self.table.item(row, 3)
        note_item = self.table.item(row, 4)

        is_custom = combo.currentText() == "[Свой вариант]"
        
        if is_custom:
            custom_variant_item.setFlags(custom_variant_item.flags() | Qt.ItemFlag.ItemIsEditable)
            note_item.setText("")
            self.table.editItem(custom_variant_item)
        else:
            custom_variant_item.setText("")
            custom_variant_item.setFlags(custom_variant_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if index < len(options):
                note_item.setText(options[index].get('note', ''))
        
        self.table.resizeRowToContents(row)

    def generate_note_for_table(self, row):
        combo = self.table.cellWidget(row, 2)
        rus = combo.currentText()
        if rus == "[Свой вариант]":
            rus = self.table.item(row, 3).text().strip()
            
        if not rus:
            QMessageBox.warning(self, "Нет слова", "Введите или выберите перевод для анализа.")
            return
            
        main_window = self.parent()
        if main_window:
            note_text = main_window._generate_note_logic(rus)
            self.table.item(row, 4).setText(note_text)
            self.table.resizeRowToContents(row)
    
    def _save_table_changes(self):
        for i in range(self.table.rowCount()):
            term_item = self.table.item(i, 0)
            if not term_item: continue
            term = term_item.text().strip()
            if not term: continue
            
            combo = self.table.cellWidget(i, 2)
            rus = combo.currentText()
            
            if rus == "[Свой вариант]":
                rus = self.table.item(i, 3).text().strip()
            
            if not rus:
                rus = combo.itemText(0)

            note = self.table.item(i, 4).text().strip()
            self.resolved_glossary[term] = {"rus": rus, "note": note}

    def accept_resolution(self):
        if self.stacked_widget.currentWidget() == self.table_widget:
            self._save_table_changes()
        else:
            pass
            
        self.accept()

