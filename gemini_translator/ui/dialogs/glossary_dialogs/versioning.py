# gemini_translator/ui/dialogs/glossary_dialogs/versioning.py

import os
import json
import zipfile
from PyQt6 import QtWidgets, QtCore, QtGui
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, 
    QListWidget, QListWidgetItem, QGroupBox, QTextEdit, 
    QDialogButtonBox, QWidget, QSplitter, QCheckBox, QLineEdit,
    QMessageBox, QAbstractItemView, QFrame
)
from PyQt6.QtCore import Qt

# Импортируем утилиту для правильной сортировки глав
from gemini_translator.utils.epub_tools import get_epub_chapter_order, extract_number_from_path

class ChapterSelectorWidget(QWidget):
    """
    Виджет для выбора глав из EPUB.
    Поддерживает множественный выбор, диапазоны и подсветку конфликтов.
    """
    # Цвета для индикации
    OCCUPIED_COLOR = QtGui.QColor(255, 140, 0, 40)   # Оранжевый (занято в другом правиле)
    CONFLICT_COLOR = QtGui.QColor(255, 50, 50, 80)   # Красный (конфликт: выбрано занятое)

    def __init__(self, epub_path, initial_selection=None, occupied_chapters=None, parent=None):
        super().__init__(parent)
        self.epub_path = epub_path
        self.initial_selection = set(initial_selection) if initial_selection else set()
        self.occupied_chapters = set(occupied_chapters) if occupied_chapters else set()
        self.all_chapters = []
        self._init_ui()
        self._load_chapters()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Фильтр/Поиск
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Фильтр по названию файла...")
        self.search_input.textChanged.connect(self._filter_list)
        layout.addWidget(self.search_input)

        # Список
        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list_widget.itemChanged.connect(self._on_item_changed) # Следим за изменениями для подсветки
        layout.addWidget(self.list_widget)
        
        # Инструменты выделения (Группировка 1: Работа с выделением курсором)
        hl_group = QHBoxLayout()
        btn_check_hl = QPushButton("☑ Выделенные")
        btn_check_hl.setToolTip("Поставить галочки на строках, выделенных синим курсором")
        btn_check_hl.clicked.connect(lambda: self._modify_selection('check_highlighted'))
        
        btn_uncheck_hl = QPushButton("☐ Выделенные")
        btn_uncheck_hl.setToolTip("Снять галочки со строк, выделенных синим курсором")
        btn_uncheck_hl.clicked.connect(lambda: self._modify_selection('uncheck_highlighted'))
        
        hl_group.addWidget(btn_check_hl)
        hl_group.addWidget(btn_uncheck_hl)
        layout.addLayout(hl_group)

        # Инструменты выделения (Группировка 2: Массовые операции от текущей строки)
        range_group = QHBoxLayout()
        btn_before = QPushButton("☑ Всё до текущей")
        btn_before.setToolTip("Отметить все главы выше текущей выделенной строки (включительно)")
        btn_before.clicked.connect(lambda: self._modify_selection('check_before'))
        
        btn_after = QPushButton("☑ Всё после текущей")
        btn_after.setToolTip("Отметить все главы ниже текущей выделенной строки (включительно)")
        btn_after.clicked.connect(lambda: self._modify_selection('check_after'))
        
        range_group.addWidget(btn_before)
        range_group.addWidget(btn_after)
        layout.addLayout(range_group)

        # Глобальные операции
        global_group = QHBoxLayout()
        btn_all = QPushButton("Выделить всё")
        btn_all.clicked.connect(self._select_all)
        btn_none = QPushButton("Сброс")
        btn_none.clicked.connect(self._deselect_all)
        global_group.addWidget(btn_all)
        global_group.addWidget(btn_none)
        layout.addLayout(global_group)

    def _load_chapters(self):
        if not self.epub_path or not os.path.exists(self.epub_path):
            self.list_widget.addItem("EPUB файл не найден")
            self.setEnabled(False)
            return

        try:
            self.list_widget.blockSignals(True) # Блокируем сигналы при загрузке
            self.all_chapters = get_epub_chapter_order(self.epub_path)
            
            for chapter_path in self.all_chapters:
                item = QListWidgetItem(os.path.basename(chapter_path))
                item.setData(Qt.ItemDataRole.UserRole, chapter_path)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                
                # Восстанавливаем состояние "Checked"
                if chapter_path in self.initial_selection:
                    item.setCheckState(Qt.CheckState.Checked)
                else:
                    item.setCheckState(Qt.CheckState.Unchecked)
                
                self._update_item_color(item, chapter_path)
                self.list_widget.addItem(item)
                
            self.list_widget.blockSignals(False)
                
        except Exception as e:
            self.list_widget.addItem(f"Ошибка чтения EPUB: {e}")

    def _update_item_color(self, item, path):
        """Обновляет цвет фона в зависимости от статуса (Занято / Конфликт)."""
        is_occupied = path in self.occupied_chapters
        is_checked = (item.checkState() == Qt.CheckState.Checked)

        if is_occupied:
            if is_checked:
                # КОНФЛИКТ: Глава занята, но мы её выбрали
                item.setBackground(self.CONFLICT_COLOR)
                item.setToolTip("КОНФЛИКТ: Эта глава уже используется в другой версии!")
            else:
                # ЗАНЯТО: Глава занята, но не выбрана (просто информативно)
                item.setBackground(self.OCCUPIED_COLOR)
                item.setToolTip("Эта глава используется в другом правиле")
        else:
            # Свободная глава
            item.setBackground(QtGui.QBrush()) # Сброс цвета
            item.setToolTip("")

    def _on_item_changed(self, item):
        """Реакция на изменение чекбокса."""
        path = item.data(Qt.ItemDataRole.UserRole)
        self._update_item_color(item, path)

    def _filter_list(self, text):
        text = text.lower()
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            item.setHidden(text not in item.text().lower())

    def _select_all(self):
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if not item.isHidden():
                item.setCheckState(Qt.CheckState.Checked)

    def _deselect_all(self):
        for i in range(self.list_widget.count()):
            self.list_widget.item(i).setCheckState(Qt.CheckState.Unchecked)

    def _modify_selection(self, mode):
        """Универсальный метод для кнопок управления выделением."""
        count = self.list_widget.count()
        current_row = self.list_widget.currentRow()
        
        # Получаем список выделенных (синих) элементов для режимов highlight
        selected_items = self.list_widget.selectedItems()

        for i in range(count):
            item = self.list_widget.item(i)
            if item.isHidden(): continue

            should_process = False
            
            if mode == 'check_highlighted':
                if item in selected_items:
                    item.setCheckState(Qt.CheckState.Checked)
            
            elif mode == 'uncheck_highlighted':
                if item in selected_items:
                    item.setCheckState(Qt.CheckState.Unchecked)

            elif mode == 'check_before':
                # Если строка выбрана, работаем от неё. Если нет - игнор или от начала?
                # Логика: от 0 до current_row включительно
                if current_row >= 0 and i <= current_row:
                    item.setCheckState(Qt.CheckState.Checked)

            elif mode == 'check_after':
                # Логика: от current_row до конца
                if current_row >= 0 and i >= current_row:
                    item.setCheckState(Qt.CheckState.Checked)

    def get_selected_chapters(self):
        selected = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                selected.append(item.data(Qt.ItemDataRole.UserRole))
        return selected

class VersionEditDialog(QDialog):
    """Диалог редактирования одной версии (правила)."""
    def __init__(self, parent_term, base_data, current_rule_data=None, epub_path=None, occupied_scopes=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Настройка версии для '{parent_term}'")
        self.resize(1000, 650)
        
        self.base_data = base_data
        self.current_rule = current_rule_data or {}
        self.epub_path = epub_path
        # Сет глав, занятых другими правилами (для подсветки конфликтов)
        self.occupied_scopes = occupied_scopes 
        
        self._init_ui()
        self._populate_fields()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        
        # --- Левая часть: Выбор глав ---
        left_group = QGroupBox("1. Где применять (Scope)")
        left_layout = QVBoxLayout(left_group)
        
        initial_files = self.current_rule.get('scope', [])
        self.chapter_selector = ChapterSelectorWidget(
            self.epub_path, 
            initial_selection=initial_files,
            occupied_chapters=self.occupied_scopes, # Передаем занятые главы
            parent=self
        )
        left_layout.addWidget(self.chapter_selector)
        
        # Легенда цветов
        legend_layout = QHBoxLayout()
        lbl_conflict = QLabel("🟥 Конфликт")
        lbl_conflict.setStyleSheet("color: #FF5555; font-weight: bold;")
        lbl_occupied = QLabel("🟧 Занято другими")
        lbl_occupied.setStyleSheet("color: orange;")
        legend_layout.addWidget(lbl_occupied)
        legend_layout.addWidget(lbl_conflict)
        legend_layout.addStretch()
        left_layout.addLayout(legend_layout)
        
        layout.addWidget(left_group, 4) # stretch factor 4
        
        # --- Правая часть: Данные ---
        right_group = QGroupBox("2. Данные версии (Override)")
        right_layout = QVBoxLayout(right_group)
        
        # Базовые данные (Только для чтения и копирования)
        right_layout.addWidget(QLabel("<b>Базовый перевод:</b>"))
        self.base_rus_view = QLineEdit(self.base_data.get('rus', ''))
        self.base_rus_view.setReadOnly(True)
        self.base_rus_view.setStyleSheet("color: gray; background-color: #2b2b2b;")
        right_layout.addWidget(self.base_rus_view)
        
        right_layout.addWidget(QLabel("<b>Базовое примечание:</b>"))
        self.base_note_view = QTextEdit()
        self.base_note_view.setPlainText(self.base_data.get('note', ''))
        self.base_note_view.setReadOnly(True)
        self.base_note_view.setMaximumHeight(80)
        self.base_note_view.setStyleSheet("color: gray; background-color: #2b2b2b;")
        right_layout.addWidget(self.base_note_view)
        
        # Разделитель
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        right_layout.addWidget(line)
        
        # Поля редактирования
        right_layout.addWidget(QLabel("<b>Новый перевод (Оставьте пустым для базового):</b>"))
        self.rus_edit = QLineEdit()
        self.rus_edit.setPlaceholderText("Введите перевод для этой версии...")
        right_layout.addWidget(self.rus_edit)
        
        right_layout.addWidget(QLabel("<b>Новое примечание (Override):</b>"))
        self.note_edit = QTextEdit()
        self.note_edit.setPlaceholderText("Инструкции для ИИ специально для этих глав...")
        right_layout.addWidget(self.note_edit)
        
        right_layout.addStretch()
        
        # Кастомные кнопки
        btn_layout = QHBoxLayout()
        btn_save = QPushButton("Сохранить версию")
        btn_save.setMinimumHeight(35)
        btn_save.clicked.connect(self.accept)
        
        btn_cancel = QPushButton("Отмена")
        btn_cancel.setMinimumHeight(35)
        btn_cancel.clicked.connect(self.reject)
        
        btn_layout.addWidget(btn_save)
        btn_layout.addWidget(btn_cancel)
        right_layout.addLayout(btn_layout)
        
        layout.addWidget(right_group, 6) # stretch factor 6

    def _populate_fields(self):
        override = self.current_rule.get('override', {})
        self.rus_edit.setText(override.get('rus', ''))
        self.note_edit.setPlainText(override.get('note', ''))

    def get_data(self):
        files = self.chapter_selector.get_selected_chapters()
        override = {}
        
        rus = self.rus_edit.text().strip()
        if rus: override['rus'] = rus
            
        note = self.note_edit.toPlainText().strip()
        if note: override['note'] = note
            
        return {
            "scope": files,
            "override": override
        }
        
class TermVersioningDialog(QDialog):
    """
    Главный диалог управления версиями для конкретного термина.
    """
    def __init__(self, term, base_data, project_manager, epub_path, parent=None):
        super().__init__(parent)
        self.term = term
        self.base_data = base_data
        self.project_manager = project_manager
        self.epub_path = epub_path
        
        self.versions_file = os.path.join(project_manager.project_folder, "glossary_versions.json")
        self.all_versions_data = self._load_all_versions()
        
        # Данные конкретно для этого термина (список правил)
        self.term_rules = self.all_versions_data.get(term, [])

        self.setWindowTitle(f"Версии термина: {term}")
        self.resize(700, 500)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        
        # Информация о базовом термине
        base_info = QGroupBox("Базовое состояние (Основной глоссарий)")
        base_layout = QVBoxLayout(base_info)
        base_lbl = QLabel(
            f"<b>Перевод:</b> {self.base_data.get('rus', '-')}<br>"
            f"<b>Note:</b> {self.base_data.get('note', '-')}"
        )
        base_lbl.setWordWrap(True)
        base_layout.addWidget(base_lbl)
        layout.addWidget(base_info)
        
        # Список версий
        layout.addWidget(QLabel("<b>Активные переопределения (Версии):</b>"))
        self.list_widget = QListWidget()
        self.list_widget.setAlternatingRowColors(True)
        self.list_widget.itemDoubleClicked.connect(self._edit_rule)
        layout.addWidget(self.list_widget)
        
        self._refresh_list()
        
        # Кнопки действий
        action_layout = QHBoxLayout()
        add_btn = QPushButton("➕ Добавить новую версию")
        add_btn.setMinimumHeight(30)
        add_btn.clicked.connect(self._add_rule)
        
        edit_btn = QPushButton("✎ Редактировать")
        edit_btn.setMinimumHeight(30)
        edit_btn.clicked.connect(self._edit_selected)
        
        del_btn = QPushButton("🗑️ Удалить")
        del_btn.setMinimumHeight(30)
        del_btn.clicked.connect(self._delete_selected)
        
        action_layout.addWidget(add_btn)
        action_layout.addWidget(edit_btn)
        action_layout.addWidget(del_btn)
        layout.addLayout(action_layout)
        
        # Разделитель и кнопка закрытия
        layout.addStretch()
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)

        close_btn = QPushButton("Закрыть окно")
        close_btn.setMinimumHeight(35)
        # Мы сохраняем данные сразу при добавлении/удалении версий, поэтому просто закрываем
        close_btn.clicked.connect(self.accept) 
        layout.addWidget(close_btn)

    def _refresh_list(self):
        self.list_widget.clear()
        for i, rule in enumerate(self.term_rules):
            files_count = len(rule.get('scope', []))
            override = rule.get('override', {})
            
            desc_parts = []
            if 'rus' in override: desc_parts.append(f"Перевод: {override['rus']}")
            if 'note' in override: desc_parts.append(f"Note: {override['note']}")
            
            text = f"Версия #{i+1} [Глав: {files_count}] -> " + " | ".join(desc_parts)
            
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, i)
            self.list_widget.addItem(item)

    def _load_all_versions(self):
        if os.path.exists(self.versions_file):
            try:
                with open(self.versions_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"[Versioning] Error loading file: {e}")
        return {}

    def _save_all_versions(self):
        if self.term_rules:
            self.all_versions_data[self.term] = self.term_rules
        else:
            if self.term in self.all_versions_data:
                del self.all_versions_data[self.term]
        
        try:
            with open(self.versions_file, 'w', encoding='utf-8') as f:
                json.dump(self.all_versions_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить версии: {e}")

    def _get_occupied_scopes(self, exclude_idx=None):
        """Собирает все главы, которые уже заняты другими версиями этого термина."""
        occupied = set()
        for i, rule in enumerate(self.term_rules):
            if i == exclude_idx:
                continue
            occupied.update(rule.get('scope', []))
        return occupied

    def _add_rule(self):
        # При создании новой версии все существующие считаются занятыми
        occupied = self._get_occupied_scopes(exclude_idx=None)
        
        dlg = VersionEditDialog(
            self.term, 
            self.base_data, 
            epub_path=self.epub_path, 
            occupied_scopes=occupied,
            parent=self
        )
        
        if dlg.exec():
            new_rule = dlg.get_data()
            if not new_rule['scope']:
                QMessageBox.warning(self, "Внимание", "Не выбраны главы для применения версии!")
                return
            if not new_rule['override']:
                QMessageBox.warning(self, "Внимание", "Не указаны данные для переопределения!")
                return
                
            self.term_rules.append(new_rule)
            self._save_all_versions()
            self._refresh_list()

    def _edit_selected(self):
        item = self.list_widget.currentItem()
        if not item: return
        self._edit_rule(item)

    def _edit_rule(self, item):
        idx = item.data(Qt.ItemDataRole.UserRole)
        rule = self.term_rules[idx]
        
        # Исключаем текущее правило из списка "занятых", чтобы они не светились оранжевым
        occupied = self._get_occupied_scopes(exclude_idx=idx)
        
        dlg = VersionEditDialog(
            self.term, 
            self.base_data, 
            current_rule_data=rule, 
            epub_path=self.epub_path, 
            occupied_scopes=occupied,
            parent=self
        )
        
        if dlg.exec():
            new_rule = dlg.get_data()
            if not new_rule['scope']:
                QMessageBox.warning(self, "Внимание", "Не выбраны главы!")
                return
            
            self.term_rules[idx] = new_rule
            self._save_all_versions()
            self._refresh_list()

    def _delete_selected(self):
        row = self.list_widget.currentRow()
        if row < 0: return
        
        if QMessageBox.question(self, "Удаление", "Удалить эту версию?") == QMessageBox.StandardButton.Yes:
            self.term_rules.pop(row)
            self._save_all_versions()
            self._refresh_list()