import re
from collections import Counter, defaultdict

# --- Импорты из PyQt6 ---
from PyQt6 import QtCore, QtGui
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QPushButton, QDialogButtonBox, QLabel,
    QWidget, QGroupBox, QHBoxLayout, QGridLayout, QTableWidget, QHeaderView,
    QTableWidgetItem, QMessageBox, QListWidget, QListWidgetItem, QSplitter,
    QComboBox, QLineEdit, QButtonGroup, QStackedWidget, QStyle
)
from PyQt6.QtCore import Qt

# --- Импорты из вашего проекта ---
# Импортируем виджеты из их нового местоположения
from .custom_widgets import ExpandingTextEditDelegate

# --- Аннотация типа для избежания циклического импорта ---
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from gemini_translator.ui.dialogs.glossary import MainWindow, GlossaryLogic


class CoreTermAnalyzerDialog(QDialog):
    """
    Супер-диалог для разрешения наложений с двумя режимами:
    1. Общий вид (свободная навигация по списку).
    2. Пошаговый режим "Визард" (проход по нерешенным проблемам).
    """
    def __init__(self, original_glossary_list, logic, analysis_results, pymorphy_available, parent=None): # <-- ИЗМЕНЕНЫ АРГУМЕНТЫ
        super().__init__(parent)
        self.original_glossary_list = original_glossary_list
        self.logic = logic
        self.parent_window = parent
        self.pymorphy_available = pymorphy_available
        # --- Состояния ---
        # --- НАЧАЛО ИЗМЕНЕНИЙ: Принимаем готовые данные ---
        self.initial_analysis_results = analysis_results # <--- Принимаем результаты
        self.analysis_data = {} # Будет заполнено после обработки
        # --- КОНЕЦ ИЗМЕНЕНИЙ ---
        self.pending_changes = {}
        self.deleted_terms = set()
        self.current_lcs_tuple = None
    
        self.setWindowTitle("Анализатор Паттернов v4.1 (Гига-глоссарий)")
        self.setMinimumSize(1300, 850)
        
        # --- НАЧАЛО ИЗМЕНЕНИЙ: Ленивая загрузка UI ---
        self._is_loaded = False
        self.init_lazy_ui()
        # --- КОНЕЦ ИЗМЕНЕНИЙ ---
    
    def init_lazy_ui(self):
        """Создает базовый UI с заглушкой 'Загрузка…'."""
        main_layout = QVBoxLayout(self)
        self.loading_label = QLabel("<h2>Подготовка данных анализатора…</h2>")
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(self.loading_label, 1)
        
        self.main_content_widget = QWidget()
        self.main_content_widget.setVisible(False)
        main_layout.addWidget(self.main_content_widget, 1)
        
        # Кнопки OK/Cancel теперь тоже часть основного виджета
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Принять изменения")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept_changes)
        buttons.rejected.connect(self.reject)
        main_layout.addWidget(buttons)
    
    def _async_prepare_data_and_populate(self):
        """
        Выполняет всю тяжелую работу по подготовке данных и созданию
        полноценного UI, а затем подменяет заглушку.
        """
        # --- Шаг 1: Подготовка данных (быстрая операция) ---
        self._prepare_analysis_data()
        
        if not self.analysis_data:
            self.loading_label.setVisible(False)
            QMessageBox.information(self, "Анализ завершен", "Не найдено значимых паттернов для анализа.")
            self.reject() # Закрываем диалог, если нечего показывать
            return

        # --- Шаг 2: Создание "тяжелого" UI ---
        content_layout = QHBoxLayout(self.main_content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        left_panel = self._create_left_panel()
        
        # Панель редактора теперь создается здесь
        self.editor_panel = QWidget()
        self.editor_panel_layout = self._create_editor_panel_layout()
        self.editor_panel.setLayout(self.editor_panel_layout)
        
        splitter.addWidget(left_panel)
        splitter.addWidget(self.editor_panel)
        splitter.setSizes([350, 950])
        content_layout.addWidget(splitter)

        # --- Шаг 3: Заполнение UI данными ---
        self._populate_left_list()
        if self.left_list.count() > 0:
            # Выбираем первый элемент, чтобы правая панель не была пустой
            first_item = self.left_list.item(0)
            widget = self.left_list.itemWidget(first_item)
            button = widget.findChild(QPushButton)
            if button:
                button.click()

        # --- Шаг 4: Подмена виджетов ---
        self.loading_label.setVisible(False)
        self.main_content_widget.setVisible(True)
    

    def _create_left_panel(self):
        """Создает левую панель со списком ПАТТЕРНОВ."""
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.addWidget(QLabel("<b>Общие паттерны:</b>"))
        
        self.left_list = QListWidget()
        # ВАЖНО: Мы больше не используем currentItemChanged, т.к. на виджете будут кнопки
        left_layout.addWidget(self.left_list)
        return left_panel
    
    def _create_right_panel(self):
        """Создает правую панель с переключателем состояний (до/после анализа)."""
        right_panel = QWidget()
        self.right_layout = QVBoxLayout(right_panel)
        self.right_layout.setContentsMargins(0, 0, 0, 0)
    
        self.right_stack = QStackedWidget()
        
        # Состояние 0: Приглашение к анализу
        pre_analysis_widget = QWidget()
        pre_analysis_layout = QVBoxLayout(pre_analysis_widget)
        pre_analysis_layout.addStretch(1)
        info_label = QLabel(
            "Этот инструмент находит термины, состоящие из очень популярных частей.\n"
            "Они могут быть как 'ключевой сутью' вашего глоссария, так и 'шумом'.\n\n"
            "Нажмите кнопку ниже, чтобы начать анализ."
        )
        info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info_label.setWordWrap(True)
        self.start_analysis_button = QPushButton("🚀 Начать анализ")
        self.start_analysis_button.clicked.connect(self._run_analysis)
        pre_analysis_layout.addWidget(info_label)
        pre_analysis_layout.addWidget(self.start_analysis_button, 0, Qt.AlignmentFlag.AlignHCenter)
        pre_analysis_layout.addStretch(1)
        
        # Состояние 1: Панель редактирования (пока пустая, будет заполняться)
        self.editor_panel = QWidget()
    
        self.right_stack.addWidget(pre_analysis_widget)
        self.right_stack.addWidget(self.editor_panel)
        
        self.right_layout.addWidget(self.right_stack)
    
        # Основные кнопки OK/Cancel
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Принять изменения")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept_changes)
        buttons.rejected.connect(self.reject)
        self.right_layout.addWidget(buttons)
        
        return right_panel
    
    def _prepare_analysis_data(self):
        """
        Подготовка данных V3.0 (Thin Client):
        Мы полностью доверяем данным из initial_analysis_results (они уже прошли Smart Consolidation).
        Просто конвертируем их во внутренний формат UI.
        """
        glossary_map = {e.get('original', ''): e for e in self.original_glossary_list}
        
        self.analysis_data = {}
        self.member_to_patterns_map = defaultdict(set)
        
        # initial_analysis_results: { "Realized String": {members_set} }
        for pattern_str, members in self.initial_analysis_results.items():
            # Используем кортеж как внутренний ключ (для совместимости с остальным кодом UI)
            # Разбиваем уже готовую "реализованную форму"
            lcs_tuple = tuple(pattern_str.split())
            
            # Проверяем, является ли сам паттерн термином
            pattern_entry = glossary_map.get(pattern_str)
            
            self.analysis_data[lcs_tuple] = {
                'members': members,
                'pattern_exists_as_term': pattern_entry is not None,
                'pattern_translation': pattern_entry.get('rus', '') if pattern_entry else '',
                'realized_form': pattern_str # <-- БЕРЕМ ГОТОВОЕ, НЕ ВЫЧИСЛЯЕМ
            }
            
            # Заполняем карту обратного поиска
            for member in members:
                self.member_to_patterns_map[member].add(lcs_tuple)
    
    def _create_editor_panel_layout(self):
        editor_layout = QVBoxLayout() # Не устанавливаем родителя сразу
        editor_layout.setContentsMargins(4, 4, 4, 4)
        editor_layout.setSpacing(10)

        # --- 1. Верхний блок (Паттерн) ---
        self.pattern_editor_group = QGroupBox()
        editor_layout.addWidget(self.pattern_editor_group, 0) # stretch = 0
        
        # --- НОВАЯ, КОМПАКТНАЯ ВЕРСТКА РЕДАКТОРА ПАТТЕРНА ---
        pattern_layout = QGridLayout(self.pattern_editor_group)
        self.pattern_original_edit = QLineEdit()
        self.pattern_translation_edit = QLineEdit()
        self.pattern_note_edit = QLineEdit()
        
        # Подключаем сигналы для сохранения изменений
        for editor in [self.pattern_original_edit, self.pattern_translation_edit, self.pattern_note_edit]:
            editor.textChanged.connect(self._on_pattern_editor_item_changed)

        pattern_layout.addWidget(QLabel("Оригинал (Паттерн):"), 0, 0)
        pattern_layout.addWidget(self.pattern_original_edit, 0, 1)
        pattern_layout.addWidget(QLabel("Перевод:"), 1, 0)
        pattern_layout.addWidget(self.pattern_translation_edit, 1, 1)
        pattern_layout.addWidget(QLabel("Примечание:"), 2, 0)
        pattern_layout.addWidget(self.pattern_note_edit, 2, 1)
        
        self.pattern_action_button = QPushButton() # Кнопка без текста, будем менять иконку
        self.pattern_action_button.setFixedSize(28, 28)
        pattern_layout.addWidget(self.pattern_action_button, 0, 2, 3, 1)

        # --- 2. Фильтры (без изменений, stretch=0) ---
        self.filter_group = QGroupBox("Вторичные фильтры:")
        self.filter_chips_layout = QHBoxLayout(self.filter_group)
        self.filter_chips_layout.setSpacing(10)
        editor_layout.addWidget(self.filter_group, 0)

        # --- 3. Regex (без изменений, stretch=0) ---
        self.mass_edit_group = QGroupBox("Массовое редактирование")
        mass_edit_layout = QGridLayout(self.mass_edit_group)
        top_row_layout = QHBoxLayout()
        self.re_column_combo = QComboBox(); self.re_column_combo.addItems(["Перевод", "Примечание", "Оригинал"])
        re_apply_btn = QPushButton("Применить Regex"); re_apply_btn.clicked.connect(self._apply_mass_edit)
        top_row_layout.addWidget(QLabel("Поле:")); top_row_layout.addWidget(self.re_column_combo)
        top_row_layout.addWidget(re_apply_btn); top_row_layout.addStretch()
        mass_edit_layout.addLayout(top_row_layout, 0, 0, 1, 2)
        self.re_find_edit = QLineEdit(); self.re_find_edit.setPlaceholderText("Найти...")
        self.re_replace_edit = QLineEdit(); self.re_replace_edit.setPlaceholderText("Заменить на...")
        mass_edit_layout.addWidget(QLabel("Найти:"), 1, 0); mass_edit_layout.addWidget(self.re_find_edit, 1, 1)
        mass_edit_layout.addWidget(QLabel("Заменить:"), 2, 0); mass_edit_layout.addWidget(self.re_replace_edit, 2, 1)
        editor_layout.addWidget(self.mass_edit_group, 0)

        # --- 4. Нижняя таблица (Термины) ---
        self.members_table_group = QGroupBox("Термины, соответствующие паттерну:")
        table_layout = QVBoxLayout(self.members_table_group)
        table_layout.setContentsMargins(2, 6, 2, 2) # Было: (2, 2, 2, 2)
        
        self.members_table = QTableWidget()
        self.members_table.setItemDelegate(ExpandingTextEditDelegate(self.members_table))
        self.members_table.setColumnCount(4)
        self.members_table.setHorizontalHeaderLabels(["Оригинал", "Перевод", "Примечание", "Действия"])
        header = self.members_table.horizontalHeader()
        for i in range(3): header.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.members_table.itemChanged.connect(self._on_sub_table_item_changed)
        
        table_layout.addWidget(self.members_table, 1) # stretch = 1 внутри группы
        
        # ВАЖНО: Группа с таблицей получает stretch = 1
        editor_layout.addWidget(self.members_table_group, 1) 

        return editor_layout
    def _populate_left_list(self):
        """Заполняет левый список кастомными виджетами для каждого паттерна."""
        self.left_list.clear()
        
        sorted_patterns = sorted(
            self.analysis_data.items(), 
            key=lambda item: (len(item[1]['members']), len(item[0])), 
            reverse=True
        )
        
        for lcs_tuple, data in sorted_patterns:
            list_item = QListWidgetItem(self.left_list)
            # Виджет теперь создается новым, более простым методом
            widget = self._create_pattern_widget(lcs_tuple, data)
            list_item.setSizeHint(widget.sizeHint())
            self.left_list.addItem(list_item)
            self.left_list.setItemWidget(list_item, widget)
    
    def _create_pattern_widget(self, lcs_tuple, data):
        """
        Создает кастомный виджет для одного элемента в левом списке.
        """
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(1)
        
        pattern_str = " ".join(lcs_tuple)
        count = len(data['members'])
        
        main_button = QPushButton(f"'{pattern_str}' ({count} терм.)")
        main_button.setStyleSheet("text-align: left; font-weight: bold; border: none; padding: 2px;")
        main_button.setFlat(True)
        main_button.clicked.connect(lambda ch, t=lcs_tuple: self._display_group_for_editing(t))
        layout.addWidget(main_button)
        
        if data['pattern_exists_as_term'] and data['pattern_translation']:
            translation_label = QLabel(f"→ {data['pattern_translation']}")
            translation_label.setStyleSheet("padding-left: 15px; color: grey; border: none;")
            layout.addWidget(translation_label)
            
        widget.setLayout(layout)
        return widget
    
    def _save_new_pattern_as_term(self):
        """Сохраняет данные из 'Редактора Паттерна' как новый термин."""
        pattern_str = self.pattern_original_edit.text()
        rus = self.pattern_translation_edit.toPlainText().strip()
        note = self.pattern_note_edit.toPlainText().strip()
    
        if not rus:
            QMessageBox.warning(self, "Пустой перевод", "Поле 'Перевод' не может быть пустым.")
            return
    
        # Добавляем в pending_changes
        self.pending_changes[pattern_str] = (pattern_str, {'rus': rus, 'note': note})
        
        # Обновляем состояние в analysis_data, чтобы UI отреагировал
        self.analysis_data[self.current_lcs_tuple]['pattern_exists_as_term'] = True
        self.analysis_data[self.current_lcs_tuple]['pattern_translation'] = rus
        
        # Перерисовываем UI, чтобы кнопка исчезла, а поля стали обычными редакторами
        self._display_group_for_editing(self.current_lcs_tuple)
        self._populate_left_list() # Обновляем левый список, чтобы там тоже появился перевод
        
        QMessageBox.information(self, "Готово", f"Термин '{pattern_str}' будет добавлен при применении изменений.")
    
    
    def _apply_mass_edit(self):
        """Применяет find/replace с regex к видимым строкам в таблице."""
        find_re = self.re_find_edit.text()
        replace_with = self.re_replace_edit.text()
        column_name = self.re_column_combo.currentText()
        
        if not find_re:
            QMessageBox.warning(self, "Пустое поле", "Поле 'Найти' не может быть пустым.")
            return
    
        column_map = {"Оригинал": 0, "Перевод": 1, "Примечание": 2}
        target_col = column_map[column_name]
    
        try:
            regex = re.compile(find_re)
        except re.error as e:
            QMessageBox.warning(self, "Ошибка Regex", f"Некорректное регулярное выражение:\n{e}")
            return
            
        changes_count = 0
        # Итерируем только по видимым строкам
        for row in range(self.members_table.rowCount()):
            if not self.members_table.isRowHidden(row):
                item = self.members_table.item(row, target_col)
                if item:
                    original_text = item.text()
                    new_text = regex.sub(replace_with, original_text)
                    if original_text != new_text:
                        item.setText(new_text) # Это вызовет on_item_changed и сохранит правку
                        changes_count += 1
        
        QMessageBox.information(self, "Готово", f"Выполнено замен: {changes_count}.")
    
    def _on_pattern_selected(self, current_item: QListWidgetItem, previous_item: QListWidgetItem):
        """Слот, вызываемый при выборе ПАТТЕРНА в левом списке."""
        if not current_item:
            return
            
        lcs_tuple = current_item.data(Qt.ItemDataRole.UserRole)
        if lcs_tuple != self.current_lcs_tuple:
            self.current_lcs_tuple = lcs_tuple
            self._display_group_for_editing(lcs_tuple)

    def _display_group_for_editing(self, lcs_tuple):
        """
        Отображает редактор для выбранного паттерна.
        Генерирует умные фильтры, скрывая бесполезные родительские паттерны.
        """
        self.current_lcs_tuple = lcs_tuple
        pattern_data = self.analysis_data[lcs_tuple]
        realized_pattern_str = pattern_data['realized_form']
    
        # --- 1. Заполнение полей редактора ---
        for editor in [self.pattern_original_edit, self.pattern_translation_edit, self.pattern_note_edit]:
            editor.blockSignals(True)

        current_data = self.pending_changes.get(lcs_tuple, {})
        display_original = current_data.get('original', realized_pattern_str)
        self.pattern_original_edit.setText(display_original)
    
        rus = current_data.get('rus', pattern_data.get('pattern_translation', ''))
        self.pattern_translation_edit.setText(rus)

        is_existing_term = pattern_data['pattern_exists_as_term']
        note = '' 

        if 'note' in current_data:
            note = current_data['note']
        elif is_existing_term:
            note_source_term = current_data.get('original', realized_pattern_str)
            # Ищем примечание в исходном глоссарии
            note = next((e.get('note', '') for e in self.original_glossary_list if e.get('original') == note_source_term), '')
        
        self.pattern_note_edit.setText(note)
        
        # Настройка кнопки действия (Удалить / Создать)
        try: self.pattern_action_button.clicked.disconnect() 
        except TypeError: pass

        if is_existing_term:
            self.pattern_action_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon))
            self.pattern_action_button.setToolTip("Удалить этот термин (сам паттерн останется)")
            self.pattern_action_button.clicked.connect(lambda: self._delete_member_term(self.pattern_original_edit.text()))
        else:
            self.pattern_action_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton))
            self.pattern_action_button.setToolTip("Сохранить этот паттерн как новый термин")
            self.pattern_action_button.clicked.connect(self._save_pattern_as_term)

        self.pattern_editor_group.setTitle("Паттерн (является термином):" if is_existing_term else "Паттерн (не является термином):")
        
        style = ""; tooltip = ""
        if is_existing_term and not rus:
            style = "background-color: #fff3cd;"; tooltip = "Внимание: этот термин существует, но у него пустой перевод!"
        self.pattern_translation_edit.setStyleSheet(style)
        self.pattern_translation_edit.setToolTip(tooltip)
        
        for editor in [self.pattern_original_edit, self.pattern_translation_edit, self.pattern_note_edit]:
            editor.blockSignals(False)

        # --- 2. Генерация Умных Фильтров ---
        # Очистка старых кнопок
        while self.filter_chips_layout.count():
            child = self.filter_chips_layout.takeAt(0)
            if child.widget(): child.widget().deleteLater()
        self.filter_buttons = []
        
        members = pattern_data['members']
        
        # Считаем, в какие ЕЩЕ паттерны входят термины этой группы
        secondary_pattern_counter = Counter()
        for term in members:
            patterns_for_this_member = self.member_to_patterns_map.get(term, set())
            secondary_pattern_counter.update(patterns_for_this_member)
    
        potential_filters = []
        for pattern, count in secondary_pattern_counter.most_common(20):
            # 1. Исключаем саму себя
            if pattern == self.current_lcs_tuple:
                continue
            
            # 2. Исключаем единичные совпадения (шум)
            if count <= 1:
                continue
                
            # 3. КРИТИЧЕСКИЙ ФИЛЬТР: Исключаем "Родителей"
            # Если паттерн-кандидат присутствует во ВСЕХ терминах текущей группы,
            # значит, текущая группа является его подмножеством.
            # Фильтровать по нему бессмысленно — он ничего не скроет.
            if count == len(members):
                continue
                
            potential_filters.append(pattern)
    
        # Умная дедупликация вложенных фильтров (оставляем самый длинный/специфичный)
        final_filters = []
        sorted_potential = sorted(potential_filters, key=len, reverse=True)
        
        for p_outer in sorted_potential:
            is_subsumed = False
            for p_final in final_filters:
                # Проверяем вхождение: если p_outer часть p_final и они фильтруют одних и тех же
                if len(p_outer) < len(p_final):
                    str_outer = " ".join(p_outer)
                    str_final = " ".join(p_final)
                    
                    if str_outer in str_final:
                        # Проверяем, совпадают ли наборы фильтруемых элементов
                        group_outer = {m for m in members if p_outer in self.member_to_patterns_map.get(m, set())}
                        group_final = {m for m in members if p_final in self.member_to_patterns_map.get(m, set())}
                        if group_outer == group_final: 
                            is_subsumed = True
                            break
            if not is_subsumed: final_filters.append(p_outer)
    
        # Создаем кнопки
        self.filter_button_group = QButtonGroup(self); self.filter_button_group.setExclusive(True)
    
        for pattern_tuple in final_filters[:7]: 
            count = secondary_pattern_counter[pattern_tuple]
            
            # --- ИЗМЕНЕНИЕ: Берем готовую форму из данных, вместо вычисления ---
            # Так как pattern_tuple взято из ключей self.analysis_data (через map),
            # оно гарантированно там есть.
            realized_sub_str = self.analysis_data[pattern_tuple]['realized_form']
            
            btn = QPushButton(f"'{realized_sub_str}' ({count})")
            btn.setCheckable(True)
            btn.setProperty("lcs_pattern", pattern_tuple)
            btn.setProperty("lcs_string", realized_sub_str) 
            
            btn.toggled.connect(self._apply_table_filter)
            self.filter_buttons.append(btn); self.filter_button_group.addButton(btn)
            self.filter_chips_layout.addWidget(btn)
        
        reset_btn = QPushButton("Сброс"); reset_btn.clicked.connect(self._reset_table_filter)
        self.filter_chips_layout.addStretch(); self.filter_chips_layout.addWidget(reset_btn)
    
        # --- 3. Заполнение таблицы терминов ---
        self.members_table.blockSignals(True)
        self.members_table.setRowCount(0)
        
        # Сортировка: сначала короткие, потом по алфавиту
        sorted_members = sorted(list(members), key=lambda t: (len(t), t))
        self.members_table.setRowCount(len(sorted_members))
        delete_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon)
        
        for row, member_term in enumerate(sorted_members):
            # Получаем данные (с учетом pending changes)
            original_member_data = next((e for e in self.original_glossary_list if e.get('original') == member_term), {})
            current_member_term, current_member_data = self.pending_changes.get(member_term, (member_term, original_member_data.copy()))
            
            # Колонка 0: Оригинал
            term_item = QTableWidgetItem(current_member_term)
            term_item.setData(Qt.ItemDataRole.UserRole, member_term) # ID неизменен
            self.members_table.setItem(row, 0, term_item)
            
            # Колонка 1: Перевод
            self.members_table.setItem(row, 1, QTableWidgetItem(current_member_data.get('rus', '')))
            
            # Колонка 2: Примечание
            self.members_table.setItem(row, 2, QTableWidgetItem(current_member_data.get('note', '')))
            
            # Колонка 3: Действия
            actions_widget = QWidget(); actions_layout = QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(0, 0, 0, 0); actions_layout.setSpacing(2)
            
            if self.pymorphy_available:
                gen_btn = QPushButton("📝"); gen_btn.setToolTip("Сгенерировать примечание")
                gen_btn.setFixedSize(24, 24); gen_btn.clicked.connect(lambda ch, r=row: self._generate_note_for_member(r))
                actions_layout.addWidget(gen_btn)
                
            delete_btn = QPushButton(delete_icon, ""); delete_btn.setToolTip(f"Удалить термин '{current_member_term}'")
            delete_btn.clicked.connect(lambda ch, term_id=member_term: self._delete_member_term(term_id))
            actions_layout.addWidget(delete_btn)
            
            self.members_table.setCellWidget(row, 3, actions_widget)
    
        self.members_table.resizeRowsToContents()
        self.members_table.blockSignals(False)   
    def _apply_table_filter(self, checked):
        """
        Фильтрует таблицу по под-паттерну.
        - Ищет существующий термин, используя правильную строковую форму.
        - Если не находит, предлагает создать термин.
        """
        if not checked:
            if not self.filter_button_group.checkedButton():
                self._reset_table_filter()
            return
    
        active_button = self.filter_button_group.checkedButton()
        if not active_button: return
    
        filter_pattern_tuple = active_button.property("lcs_pattern")
        filter_pattern_str = active_button.property("lcs_string") 
        
        if not filter_pattern_tuple or not filter_pattern_str: return

        # --- ШАГ 1: Очистка предыдущего "призрака" ---
        if self.members_table.rowCount() > 0:
            first_item = self.members_table.item(0, 0)
            if first_item and first_item.data(Qt.ItemDataRole.UserRole + 1) == "ghost":
                self.members_table.removeRow(0)

        # Сброс подсветки
        default_brush = QtGui.QBrush(Qt.BrushStyle.NoBrush)
        for r in range(self.members_table.rowCount()):
            for c in range(3):
                it = self.members_table.item(r, c)
                if it: it.setBackground(default_brush)

        # --- ШАГ 2: Поиск существующего ---
        found_row_index = -1
        for row in range(self.members_table.rowCount()):
            term_item = self.members_table.item(row, 0)
            if term_item:
                original_id = term_item.data(Qt.ItemDataRole.UserRole)
                if original_id == filter_pattern_str:
                    found_row_index = row
                    break
        
        # --- ШАГ 3: Применение фильтра ---
        for row in range(self.members_table.rowCount()):
            term_item = self.members_table.item(row, 0)
            if not term_item: continue
            
            original_term_id = term_item.data(Qt.ItemDataRole.UserRole)
            patterns_for_this_term = self.member_to_patterns_map.get(original_term_id, set())
            
            is_visible = filter_pattern_tuple in patterns_for_this_term
            self.members_table.setRowHidden(row, not is_visible)

        # --- ШАГ 4: Обработка "Героя" ---
        highlight_color = QtGui.QColor(85, 170, 255, 50)

        if found_row_index != -1:
            # СЦЕНАРИЙ А: Перемещаем существующий вверх
            if found_row_index != 0:
                self.members_table.insertRow(0)
                for col in range(self.members_table.columnCount()):
                    item = self.members_table.takeItem(found_row_index + 1, col)
                    if item: self.members_table.setItem(0, col, item)
                
                self.members_table.removeRow(found_row_index + 1)
                
                # Восстанавливаем кнопки
                term_id = self.members_table.item(0, 0).data(Qt.ItemDataRole.UserRole)
                self._create_standard_action_widget(0, term_id) # Используем хелпер или код ниже

            self.members_table.setRowHidden(0, False)
            for c in range(3):
                it = self.members_table.item(0, c)
                if it: it.setBackground(highlight_color)

        else:
            # СЦЕНАРИЙ Б: Создаем "Призрака"
            self.members_table.insertRow(0)
            
            item_pat = QTableWidgetItem(filter_pattern_str)
            # Призрак пока НЕ редактируемый, чтобы случайно не сбить ID до создания
            item_pat.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            item_pat.setData(Qt.ItemDataRole.UserRole, filter_pattern_str)
            item_pat.setData(Qt.ItemDataRole.UserRole + 1, "ghost")
            item_pat.setBackground(highlight_color)
            self.members_table.setItem(0, 0, item_pat)
            
            for c in [1, 2]:
                empty_item = QTableWidgetItem("")
                # Ячейки данных пока тоже заблочим, пока не нажмет плюс
                empty_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                empty_item.setBackground(highlight_color)
                self.members_table.setItem(0, c, empty_item)

            btn_widget = QWidget(); btn_layout = QHBoxLayout(btn_widget); btn_layout.setContentsMargins(0,0,0,0)
            add_btn = QPushButton("➕ Добавить как термин")
            add_btn.setStyleSheet("text-align: left; padding-left: 5px; font-weight: bold;")
            # ВАЖНО: Передаем и строку и кортеж паттерна
            add_btn.clicked.connect(lambda: self._quick_add_pattern_term(filter_pattern_str, filter_pattern_tuple))
            btn_layout.addWidget(add_btn)
            self.members_table.setCellWidget(0, 3, btn_widget)
    
    def _create_standard_action_widget(self, row, term_id):
        """Вспомогательный метод для создания кнопок действий в строке."""
        actions_widget = QWidget(); actions_layout = QHBoxLayout(actions_widget)
        actions_layout.setContentsMargins(0,0,0,0); actions_layout.setSpacing(2)
        if self.pymorphy_available:
            gen_btn = QPushButton("📝"); gen_btn.setFixedSize(24, 24)
            gen_btn.clicked.connect(lambda ch, r=row: self._generate_note_for_member(r))
            actions_layout.addWidget(gen_btn)
        del_btn = QPushButton(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon), "")
        del_btn.clicked.connect(lambda ch, t=term_id: self._delete_member_term(t))
        actions_layout.addWidget(del_btn)
        self.members_table.setCellWidget(row, 3, actions_widget)

    def _on_pattern_editor_item_changed(self):
        """Сохраняет изменения из 'Редактора Паттерна' (QLineEdit)."""
        if self.current_lcs_tuple is None: return
        self.pending_changes[self.current_lcs_tuple] = {
            'original': self.pattern_original_edit.text().strip(),
            'rus': self.pattern_translation_edit.text().strip(),
            'note': self.pattern_note_edit.text().strip()
        }
    
    def _quick_add_pattern_term(self, pattern_str, pattern_tuple):
        """
        Превращает "Призрачную" строку (row 0) в реальный редактируемый термин.
        Обновляет данные и UI мгновенно.
        """
        # 1. Добавляем в патч изменений (создаем термин)
        # Пустой перевод и примечание по умолчанию
        self.pending_changes[pattern_str] = (pattern_str, {'rus': '', 'note': ''})
        
        # 2. Обновляем локальные карты связей, чтобы фильтры признали этот термин
        # Добавляем этот термин как участника текущей группы паттерна
        if pattern_tuple not in self.analysis_data:
             # Если вдруг такой группы нет (редко), создаем
             self.analysis_data[pattern_tuple] = {'members': set(), 'pattern_exists_as_term': True, 'realized_form': pattern_str}
        
        self.analysis_data[pattern_tuple]['members'].add(pattern_str)
        self.member_to_patterns_map[pattern_str].add(pattern_tuple)

        # 3. Трансформация UI (Row 0) IN-PLACE
        row = 0
        
        # А. Снимаем метку "ghost"
        term_item = self.members_table.item(row, 0)
        term_item.setData(Qt.ItemDataRole.UserRole + 1, None) # Remove ghost flag
        
        # Б. Делаем ячейки редактируемыми
        for col in [0, 1, 2]:
            item = self.members_table.item(row, col)
            if item:
                # Включаем флаг Editable
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable)
        
        # В. Заменяем кнопку "Добавить" на стандартные кнопки "Удалить/Генерировать"
        # Для удаления передаем pattern_str как ID
        self._create_standard_action_widget(row, pattern_str)
        
        # 4. Фокус на перевод для немедленного ввода
        translation_item = self.members_table.item(row, 1)
        if translation_item:
            self.members_table.editItem(translation_item)
            
    def _save_pattern_as_term(self):
        """Обрабатывает нажатие кнопки 'Сохранить'."""
        self._on_pattern_editor_item_changed() # Убедимся, что данные сохранены
        
        saved_data = self.pending_changes.get(self.current_lcs_tuple)
        if not saved_data:
            QMessageBox.warning(self, "Нет данных", "Нечего сохранять. Введите перевод."); return
    
        pattern_str = saved_data.get('original')
        rus = saved_data.get('rus')
    
        if not pattern_str or not rus:
            QMessageBox.warning(self, "Пустые поля", "Поля 'Оригинал' и 'Перевод' не могут быть пустыми."); return
    
        self.analysis_data[self.current_lcs_tuple]['pattern_exists_as_term'] = True
        self.analysis_data[self.current_lcs_tuple]['pattern_translation'] = rus
        self.analysis_data[self.current_lcs_tuple]['realized_form'] = pattern_str
    
        self._display_group_for_editing(self.current_lcs_tuple)
        self._populate_left_list()
        
        QMessageBox.information(self, "Готово", f"Термин '{pattern_str}' будет добавлен/обновлен при применении изменений.")
        
    
    
    def _reset_table_filter(self):
        """Сбрасывает фильтры, удаляет призрачные строки и убирает подсветку."""
        self.members_table.blockSignals(True)
        
        # 1. Удаляем призрачную строку, если есть
        if self.members_table.rowCount() > 0:
            first_item = self.members_table.item(0, 0)
            if first_item and first_item.data(Qt.ItemDataRole.UserRole + 1) == "ghost":
                self.members_table.removeRow(0)

        # 2. Сбрасываем кнопки
        for btn in self.filter_buttons:
            btn.blockSignals(True)
            btn.setChecked(False)
            btn.blockSignals(False)
        
        # 3. Сбрасываем скрытие строк и подсветку
        default_brush = QtGui.QBrush(Qt.BrushStyle.NoBrush)
        for row in range(self.members_table.rowCount()):
            self.members_table.setRowHidden(row, False)
            for c in range(3):
                it = self.members_table.item(row, c)
                if it: it.setBackground(default_brush)

        self.members_table.blockSignals(False)
    
    def _on_sub_table_item_changed(self, item: QTableWidgetItem):
        """Автоматически сохраняет изменения из таблицы 'соседей'."""
        row, col = item.row(), item.column()
        # Нас интересуют только столбцы с данными (0, 1, 2)
        if col not in [0, 1, 2]: return 
        
        # Идентификатор (оригинальный ключ) хранится в UserRole столбца 0
        id_item = self.members_table.item(row, 0)
        if not id_item: return
        original_term_id = id_item.data(Qt.ItemDataRole.UserRole)
        
        # Получаем текущее состояние изменений для этого термина или его оригинал
        current_term, current_data = self.pending_changes.get(
            original_term_id,
            (original_term_id, next((e for e in self.original_glossary_list if e.get('original') == original_term_id), {}).copy())
        )
    
        if col == 0: current_term = item.text()
        elif col == 1: current_data['rus'] = item.text()
        elif col == 2: current_data['note'] = item.text()
        
        self.pending_changes[original_term_id] = (current_term, current_data)
    
    def accept_changes(self):
        """Вызывается при нажатии 'Принять изменения'."""
        # Здесь можно добавить логику подтверждения, если нужно
        self.accept()


    def get_patch(self):
        """
        Собирает финальный патч изменений для применения в MainWindow.
        Версия 4.0: Учитывает два разных формата данных в pending_changes.
        """
        patch_list = []
        original_map = {e.get('original'): e for e in self.original_glossary_list}

        # --- Шаг 1: Обрабатываем изменения и добавления ---
        for key, value in self.pending_changes.items():
            if isinstance(key, tuple):
                # --- Случай 1: Изменение/создание самого паттерна ---
                # key = lcs_tuple, value = {'original': ..., 'rus': ..., 'note': ...}
                pattern_data_dict = value
                new_term_str = pattern_data_dict.get('original')
                if not new_term_str: continue

                # Находим, как паттерн выглядел до редактирования
                analysis_info = self.analysis_data.get(key, {})
                original_term_str = analysis_info.get('realized_form')
                
                before_state = original_map.get(original_term_str)
                after_state = {'original': new_term_str, **pattern_data_dict}
                
                # Если термин существовал и был переименован - это update.
                # Если его не было - это addition.
                if before_state and original_term_str != new_term_str:
                    # Это особый случай: удаление старого и добавление нового.
                    # Моделируем как два изменения для чистоты патча.
                    patch_list.append({'before': before_state, 'after': None})
                    patch_list.append({'before': None, 'after': after_state})
                else:
                     # Это либо чистое добавление, либо обновление существующего
                     patch_list.append({'before': before_state, 'after': after_state})

            else:
                # --- Случай 2: Изменение термина-участника ---
                # key = original_term_string, value = (new_term_string, new_data_dict)
                orig_term = key
                new_term, new_data = value
                
                before_state = original_map.get(orig_term)
                
                # Создаем after_state, сохраняя доп. поля, если они были
                after_state = (before_state or {}).copy()
                after_state.update(new_data)
                after_state['original'] = new_term

                patch_list.append({'before': before_state, 'after': after_state})
        
        # --- Шаг 2: Обрабатываем удаления ---
        for term_to_delete in self.deleted_terms:
            # Проверяем, не был ли этот термин уже обработан как часть изменения
            is_already_handled = False
            for change in patch_list:
                if change.get('before') and change['before'].get('original') == term_to_delete:
                    is_already_handled = True
                    break
            
            if not is_already_handled:
                before_state = original_map.get(term_to_delete)
                if before_state:
                    patch_list.append({'before': before_state, 'after': None})
        
        # --- Шаг 3: Финальная очистка и дедупликация патча ---
        # (На случай сложных переименований, чтобы не было конфликтов)
        final_patch_map = {}
        for change in patch_list:
            # Используем ID на основе состояния "до" для уникальности
            before = change.get('before')
            key = tuple(before.items()) if before else ('new', change['after']['original'])
            
            if key in final_patch_map:
                # Если уже есть изменение для этого элемента, просто обновляем его конечное состояние
                final_patch_map[key]['after'] = change['after']
            else:
                final_patch_map[key] = change

        return list(final_patch_map.values())


    def _generate_note_for_member(self, row):
        """Генерирует примечание для термина в таблице 'соседей'."""
        main_window = self.parent_window
        if main_window and main_window.__class__.__name__ == 'MainWindow':
            if not self.pymorphy_available: 
                return
        
        translation_item = self.members_table.item(row, 1)
        note_item = self.members_table.item(row, 2)
        if not translation_item or not note_item: return
    
        note_text = main_window._generate_note_logic(translation_item.text())
        if note_text:
            note_item.setText(note_text) # Это автоматически вызовет _on_sub_table_item_changed
            self.members_table.resizeRowToContents(row)
    

    def _delete_member_term(self, original_term_id):
        """
        ФИНАЛЬНАЯ ВЕРСИЯ. Мгновенно удаляет термин из "живых" данных анализа
        и оперативно обновляет весь связанный UI (счетчики в левом списке и фильтрах).
        """
        # --- НАЧАЛО ИСПРАВЛЕНИЯ: Ручное создание QMessageBox для локализации кнопок ---
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Подтверждение удаления")
        msg_box.setText(f"Вы уверены, что хотите удалить термин '{original_term_id}'?")
        msg_box.setIcon(QMessageBox.Icon.Question)
        yes_button = msg_box.addButton("Да, удалить", QMessageBox.ButtonRole.YesRole)
        msg_box.addButton("Нет", QMessageBox.ButtonRole.NoRole)
        msg_box.exec()
        
        if msg_box.clickedButton() == yes_button:
        # --- КОНЕЦ ИСПРАВЛЕНИЯ ---
            # 1. Помечаем на удаление для финального сохранения (как и раньше)
            self.deleted_terms.add(original_term_id)
            if original_term_id in self.pending_changes:
                del self.pending_changes[original_term_id]
            
            # 2. Находим все паттерны, в которые входил этот термин
            affected_patterns = self.member_to_patterns_map.get(original_term_id, set())
    
            # 3. "Хирургически" удаляем термин из "живых" данных анализа
            for pattern_tuple in affected_patterns:
                if pattern_tuple in self.analysis_data:
                    # Удаляем из множества участников
                    self.analysis_data[pattern_tuple]['members'].discard(original_term_id)
                    # Обновляем счетчик в левом списке
                    self._update_left_list_item_by_tuple(pattern_tuple)
    
            # 4. Перерисовываем правую панель. Так как self.analysis_data уже обновлен,
            # таблица и счетчики на кнопках-фильтрах перерисуются с правильными данными.
            self._display_group_for_editing(self.current_lcs_tuple)
                
    def _update_left_list_item_by_tuple(self, pattern_tuple):
        """
        Находит элемент в левом списке по его кортежу-ключу и обновляет
        его виджет (в частности, счетчик терминов).
        """
        for i in range(self.left_list.count()):
            item = self.left_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == pattern_tuple:
                # Нашли нужный элемент
                widget = self.left_list.itemWidget(item)
                if widget:
                    # Находим кнопку внутри виджета
                    button = widget.findChild(QPushButton)
                    if button:
                        # Обновляем счетчик
                        data = self.analysis_data[pattern_tuple]
                        pattern_str = " ".join(pattern_tuple)
                        new_count = len(data['members'])
                        button.setText(f"'{pattern_str}' ({new_count} терм.)")
                break # Прерываем цикл, так как элемент найден   

    def showEvent(self, event):
        """Переопределяем showEvent для асинхронной загрузки."""
        super().showEvent(event)
        if not self._is_loaded:
            self._is_loaded = True
            # Запускаем тяжелую работу после того, как окно уже показалось
            QtCore.QTimer.singleShot(50, self._async_prepare_data_and_populate)
