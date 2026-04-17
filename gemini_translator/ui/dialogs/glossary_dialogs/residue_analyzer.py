# gemini_translator/ui/dialogs/glossary_dialogs/residue_analyzer.py

import re
from collections import defaultdict
from PyQt6 import QtWidgets, QtCore, QtGui
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QPushButton, QDialogButtonBox, QLabel, QWidget, 
    QHBoxLayout, QTableWidget, QHeaderView, QTableWidgetItem, QListWidget, QTextEdit,
    QListWidgetItem, QSplitter, QAbstractItemView, QGroupBox, QLineEdit, QGridLayout
)
from PyQt6.QtCore import Qt, pyqtSignal

from .custom_widgets import ExpandingTextEditDelegate, ExpandingTextEdit
from ....ui.widgets.preset_widget import PresetWidget
from ....api import config as api_config

NO_RUS_PATTERN = re.compile(r'[^а-яА-ЯёЁ\s\d!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~]+')



class ResidueAnalyzerDialog(QDialog):
    """
    Диалог для анализа и исправления "перекрестного загрязнения" и "неизвестных остатков".
    Версия 11.0: Финальная унификация UI.
    """
    create_new_term_requested = pyqtSignal(dict)

    def __init__(self, residue_map, original_glossary_list, settings_manager, parent=None):
        super().__init__(parent)
        # --- Сохраняем полный, нефильтрованный результат ---
        self.full_residue_map = residue_map 
        self.original_glossary_list = original_glossary_list
        self.settings_manager = settings_manager
        
        self.patch_list = []
        self.view_mode = 'fragment_to_term'
        # --- Состояние режима анализа ---
        self.analysis_mode = 'all' # 'all' или 'translation_only'

        # --- Эти словари теперь будут содержать отфильтрованные данные ---
        self.residue_map = {}
        self.inverted_residue_map = defaultdict(lambda: {'fragments': set(), 'entries': []})

        self.setWindowTitle("Анализатор остаточных фрагментов")
        self.setMinimumSize(1200, 750)

        self._init_ui()
        # ---  Первый запуск теперь тоже проходит через фильтр ---
        self._apply_all_filters_and_update_view()

    def _get_entry_id(self, entry):
        if not isinstance(entry, dict):
            return str(entry)
        return tuple(entry.get(k, '') for k in ['original', 'rus', 'note'])

    def _apply_all_filters_and_update_view(self):
        """
        Главный метод обновления. Сначала пересчитывает остатки на основе
        ТЕКУЩЕГО состояния глоссария (с учетом патча), а затем применяет фильтры.
        """
        # --- ШАГ 1: Переанализ на основе актуальных данных ---
        parent_main_window = self.parent()
        if not (parent_main_window and hasattr(parent_main_window, 'logic')): 
            return

        # Получаем самое свежее состояние глоссария (оригинал + все правки из патча)
        current_glossary_state = self.get_current_glossary_state()
        
        # Запускаем анализ на этих свежих данных, чтобы получить актуальную карту остатков
        self.full_residue_map = parent_main_window.logic.find_untranslated_residue(current_glossary_state)

        # --- ШАГ 2: Применение фильтров к актуальной карте ---
        
        # 2.1. Загружаем и применяем список исключений
        exceptions_text = self.settings_manager.get_last_word_exceptions_text() or api_config.default_word_exceptions()
        exceptions_set = {line.strip().lower() for line in exceptions_text.splitlines() if line.strip() and not line.strip().startswith('#')}
        
        temp_residue_map = {
            fragment: data for fragment, data in self.full_residue_map.items()
            if fragment not in exceptions_set
        }
        
        # 2.2. Применяем фильтр по режиму (translation_only / all)
        if self.analysis_mode == 'all':
            self.residue_map = temp_residue_map
        else: # 'translation_only'
            filtered_map = defaultdict(lambda: {'entries_with_residue': []})
            for fragment, data in temp_residue_map.items():
                entries_in_translation = [
                    entry_info for entry_info in data.get('entries_with_residue', [])
                    if entry_info.get('location') == 'rus'
                ]
                if entries_in_translation:
                    filtered_map[fragment]['entries_with_residue'] = entries_in_translation
            self.residue_map = dict(filtered_map)
        
        # --- ШАГ 3: Перестроение UI ---
        
        # 3.1. Перестраиваем инвертированную карту
        self.inverted_residue_map.clear()
        for fragment, data in self.residue_map.items():
            for entry_info in data['entries_with_residue']:
                entry = entry_info['entry']
                original_term = entry.get('original')
                if original_term:
                    self.inverted_residue_map[original_term]['entries'].append(entry)
                    self.inverted_residue_map[original_term]['fragments'].add(fragment)
        
        # 3.2. Определяем оптимальный вид и обновляем список
        if self.inverted_residue_map and (len(self.inverted_residue_map) < len(self.residue_map)):
            self.view_mode = 'term_to_fragment'
        else:
            self.view_mode = 'fragment_to_term'
            
        self._populate_left_list()

    def _init_ui(self):
        main_layout = QHBoxLayout(self); splitter = QSplitter(Qt.Orientation.Horizontal)
        left_panel = QWidget(); left_layout = QVBoxLayout(left_panel)
        self.left_label = QLabel(); left_layout.addWidget(self.left_label)
        self.left_list = QListWidget(); self.left_list.currentItemChanged.connect(self._on_item_selected)
        left_layout.addWidget(self.left_list)
        
        self.toggle_view_btn = QPushButton("Переключить вид")
        self.toggle_view_btn.clicked.connect(self._toggle_view)
        left_layout.addWidget(self.toggle_view_btn)
        
        self.toggle_analysis_mode_btn = QPushButton()
        self.toggle_analysis_mode_btn.clicked.connect(self._toggle_analysis_mode)
        self._update_analysis_mode_button_text()
        left_layout.addWidget(self.toggle_analysis_mode_btn)
        
        exceptions_btn = QPushButton("Списки исключений…")
        exceptions_btn.clicked.connect(self._open_exceptions_manager)
        left_layout.addWidget(exceptions_btn)
        
        right_panel = QWidget(); self.right_layout = QVBoxLayout(right_panel)
        self.right_panel_container = QWidget(); self.right_panel_container_layout = QVBoxLayout(self.right_panel_container)
        self.right_layout.addWidget(self.right_panel_container, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Принять изменения")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        self.right_layout.addWidget(buttons)
        splitter.addWidget(left_panel); splitter.addWidget(right_panel)
        splitter.setSizes([350, 850]); main_layout.addWidget(splitter)

    def _toggle_view(self):
        self.view_mode = 'term_to_fragment' if self.view_mode == 'fragment_to_term' else 'fragment_to_term'
        self._populate_left_list()

    def _populate_left_list(self):
        self.left_list.blockSignals(True)
        self.left_list.clear()
        
        source_map = self.residue_map if self.view_mode == 'fragment_to_term' else self.inverted_residue_map
        label_text = "<b>Найденные фрагменты:</b>" if self.view_mode == 'fragment_to_term' else "<b>Термины с фрагментами:</b>"
        self.left_label.setText(label_text)
        
        sorted_keys = sorted(source_map.keys())
        
        for key in sorted_keys:
            display_text = key
            
            # ЕСЛИ включен режим "термины -> фрагменты", key - это оригинал термина.
            # Хотим показать перевод, если он есть.
            if self.view_mode == 'term_to_fragment':
                # Берем первую попавшуюся запись для этого термина, чтобы узнать перевод
                entries = source_map[key].get('entries', [])
                if entries:
                    first_entry = entries[0]
                    trans = first_entry.get('rus', 'НЕТ ПЕРЕВОДА')
                    if trans:
                        display_text = trans
                        
            item = QListWidgetItem(display_text)
            # Сохраняем реальный ключ (фрагмент или оригинал термина) в данные
            item.setData(Qt.ItemDataRole.UserRole, key) 
            self.left_list.addItem(item)
            
        self.left_list.blockSignals(False)
        if self.left_list.count() > 0: self.left_list.setCurrentRow(0)
        else: self._clear_right_panel()

    def _on_item_selected(self, current_item, previous_item):
        if not current_item: 
            self._clear_right_panel()
        else: 
            # Берем ключ из UserRole, если есть. Если нет (старая логика), берем текст.
            key = current_item.data(Qt.ItemDataRole.UserRole)
            if key is None:
                key = current_item.text()
            self._display_details(key)
    
    def _update_analysis_mode_button_text(self):
        """Обновляет текст на кнопке переключения режима анализа."""
        if self.analysis_mode == 'all':
            self.toggle_analysis_mode_btn.setText("Учесть только переводы")
            self.toggle_analysis_mode_btn.setToolTip("Игнорировать остатки, найденные в примечаниях.")
        else:
            self.toggle_analysis_mode_btn.setText("Учитывать примечания")
            self.toggle_analysis_mode_btn.setToolTip("Искать остатки и в переводах, и в примечаниях (по умолчанию).")
    
    def _apply_filter_and_rebuild_maps(self):
        """
        Берет полный набор данных (self.full_residue_map) и фильтрует его
        в self.residue_map в соответствии с текущим self.analysis_mode.
        Затем перестраивает инвертированную карту для другого вида.
        """
        if self.analysis_mode == 'all':
            self.residue_map = self.full_residue_map
        else: # 'translation_only'
            # ИЗМЕНЕНИЕ: Регулярное выражение ищет последовательности символов,
            # которые НЕ являются кириллицей, пробелами, цифрами или пунктуацией.
            filtered_map = defaultdict(lambda: {'entries_with_residue': []})
            
            for fragment, data in self.full_residue_map.items():
                entries_in_translation = []
                for entry_info in data['entries_with_residue']:
                    entry = entry_info['entry']
                    rus = entry.get('rus', '')

                    found_words = {r.lower() for r in NO_RUS_PATTERN.findall(rus)}
                    if fragment in found_words:
                        entries_in_translation.append(entry_info)
                
                if entries_in_translation:
                    filtered_map[fragment]['entries_with_residue'] = entries_in_translation
            
            self.residue_map = dict(filtered_map)

        # Перестраиваем инвертированную карту на основе отфильтрованных данных
        self.inverted_residue_map.clear()
        for fragment, data in self.residue_map.items():
            for entry_info in data['entries_with_residue']:
                entry = entry_info['entry']
                original_term = entry.get('original')
                if original_term:
                    self.inverted_residue_map[original_term]['entries'].append(entry)
                    self.inverted_residue_map[original_term]['fragments'].add(fragment)
        
        if self.inverted_residue_map and (len(self.inverted_residue_map) < len(self.residue_map)):
            self.view_mode = 'term_to_fragment'
        else:
            self.view_mode = 'fragment_to_term'
            
    def _toggle_analysis_mode(self):
        """Переключает режим анализа и запускает полную локальную перефильтрацию."""
        self.analysis_mode = 'translation_only' if self.analysis_mode == 'all' else 'all'
        self._update_analysis_mode_button_text()
        self._apply_all_filters_and_update_view()
    
    def _display_details(self, selected_key):
        self._clear_right_panel()
        current_glossary = self.get_current_glossary_state()
        all_known_originals = {e.get('original') for e in current_glossary}

        if self.view_mode == 'fragment_to_term':
            data = self.residue_map.get(selected_key, {})
            main_entry = next((e for e in current_glossary if e.get('original') == selected_key), {'original': selected_key})
            
            is_new = selected_key not in all_known_originals
            main_title = "Неизвестный фрагмент" if is_new else "Контекст (известный термин)"
            
            sub_entries = []
            if not is_new:
                duplicates = [e for e in current_glossary if e.get('original') == selected_key and self._get_entry_id(e) != self._get_entry_id(main_entry)]
                sub_entries.extend([{'type': 'duplicate', 'data': d} for d in duplicates])
            occurrences = data.get('entries_with_residue', [])
            sub_entries.extend([{'type': 'occurrence', 'data': o['entry']} for o in occurrences])
            sub_title = f"Связанные записи ({len(sub_entries)}):"
        else: # term_to_fragment
            main_entries = [e for e in current_glossary if e.get('original') == selected_key]
            main_entry = main_entries[0] if main_entries else {}
            is_new = False
            main_title = f"Редактирование термина '{selected_key}'"
            
            sub_entries = []
            if len(main_entries) > 1: sub_entries.extend([{'type': 'duplicate', 'data': e} for e in main_entries[1:]])
            data = self.inverted_residue_map.get(selected_key, {})
            sub_entries.extend([{'type': 'fragment', 'data': f} for f in sorted(list(data.get('fragments', [])))])
            sub_title = f"Связанные записи ({len(sub_entries)}):"

        if main_entry: self.right_panel_container_layout.addWidget(self._create_editor_group(main_title, main_entry, is_new))
        if sub_entries: self.right_panel_container_layout.addWidget(self._create_sub_entries_group(sub_title, sub_entries), 1)

    def _create_editor_group(self, title, entry_data, is_new):
        editor_group = QGroupBox(title); editor_layout = QGridLayout(editor_group)
        
        # Находим актуальное состояние (с учетом уже сделанных правок)
        found_data = next((p['after'] for p in self.get_final_patch() if p.get('before') and self._get_entry_id(p['before']) == self._get_entry_id(entry_data)), entry_data)
        
        # Если запись была удалена патчем, found_data будет None. 
        # Чтобы не упасть, подставляем пустой словарь для отображения.
        current_data = found_data if found_data is not None else {}
        is_deleted = found_data is None

        self.editor_original_edit = QLineEdit(current_data.get('original', '')); self.editor_original_edit.setReadOnly(is_new)
        self.editor_translation_edit = ExpandingTextEdit(self); self.editor_translation_edit.setPlainText(current_data.get('rus', ''))
        self.editor_note_edit = ExpandingTextEdit(self); self.editor_note_edit.setPlainText(current_data.get('note', ''))
        
        if is_deleted:
            self.editor_original_edit.setEnabled(False)
            self.editor_translation_edit.setEnabled(False)
            self.editor_note_edit.setEnabled(False)
            self.editor_original_edit.setPlaceholderText("ТЕРМИН УДАЛЕН")

        for editor in [self.editor_original_edit, self.editor_translation_edit, self.editor_note_edit]:
            # ВАЖНО: *args в начале лямбды "проглатывает" текст, который посылает QLineEdit.
            # Без этого entry_data заменялась бы на строку текста, ломая структуру патча.
            editor.textChanged.connect(lambda *args, ed=entry_data, o_edit=self.editor_original_edit, t_edit=self.editor_translation_edit, n_edit=self.editor_note_edit, new=is_new: self._on_editor_item_changed(ed, o_edit, t_edit, n_edit, new))
        
        editor_layout.addWidget(QLabel("Оригинал:"), 0, 0); editor_layout.addWidget(self.editor_original_edit, 0, 1)
        editor_layout.addWidget(QLabel("Перевод:"), 1, 0, Qt.AlignmentFlag.AlignTop); editor_layout.addWidget(self.editor_translation_edit, 1, 1)
        editor_layout.addWidget(QLabel("Примечание:"), 2, 0, Qt.AlignmentFlag.AlignTop); editor_layout.addWidget(self.editor_note_edit, 2, 1)
        
        action_btn = QPushButton("➕" if is_new else "🗑️"); action_btn.setToolTip("Создать новый термин" if is_new else "Удалить этот термин (и все его дубликаты)")
        
        # Если уже удалено, кнопку удаления отключаем
        if is_deleted and not is_new:
            action_btn.setEnabled(False)

        action_btn.clicked.connect(lambda: (self._create_new_term_locally(entry_data, self.editor_original_edit, self.editor_translation_edit, self.editor_note_edit) if is_new else self._delete_entry(entry_data)))
        editor_layout.addWidget(action_btn, 0, 2, 3, 1)
        return editor_group


    def _create_sub_entries_group(self, title, sub_entries):
        group = QGroupBox(title); layout = QVBoxLayout(group)
        table = QTableWidget(); table.setColumnCount(5)
        table.setHorizontalHeaderLabels(["Тип", "Оригинал/Фрагмент", "Перевод", "Примечание", "Действия"])
        delegate = ExpandingTextEditDelegate(table); header = table.horizontalHeader()
        for i in [1,2,3]: header.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        
        table.itemChanged.connect(self._on_sub_table_item_changed)
        
        current_glossary = self.get_current_glossary_state()
        all_originals_map = {e['original']: e for e in current_glossary if e.get('original')}
        
        table.setRowCount(len(sub_entries))
        table.blockSignals(True)
        for i, item_info in enumerate(sub_entries):
            item_type, item_data = item_info['type'], item_info['data']
            
            type_map = {
                'duplicate': "Дубликат", 'occurrence': "Вхождение", 'fragment': "Фрагмент"
            }
            display_type_text = type_map.get(item_type, item_type.capitalize())
            type_item = QTableWidgetItem(display_type_text)
            type_item.setFlags(type_item.flags() & ~Qt.ItemFlag.ItemIsEditable)

            if item_type == 'duplicate': type_item.setForeground(QtGui.QColor("#F39C12"))
            elif item_type == 'occurrence': type_item.setForeground(QtGui.QColor("#2ECC71"))
            elif item_type == 'fragment': type_item.setForeground(QtGui.QColor("#3498DB"))
            table.setItem(i, 0, type_item)

            actions_widget = QWidget(); actions_layout = QHBoxLayout(actions_widget); actions_layout.setContentsMargins(0,0,0,0)

            if item_type in ['duplicate', 'occurrence']:
                entry = item_data
                type_item.setData(Qt.ItemDataRole.UserRole, entry)
                
                found_data = next((p['after'] for p in self.get_final_patch() if p.get('before') and self._get_entry_id(p['before']) == self._get_entry_id(entry)), entry)
                # ЗАЩИТА: Если удалено (None), используем пустой словарь
                current_data = found_data if found_data is not None else {}
                
                table.setItem(i, 1, QTableWidgetItem(current_data.get('original', '')))
                table.setItem(i, 2, QTableWidgetItem(current_data.get('rus', '')))
                table.setItem(i, 3, QTableWidgetItem(current_data.get('note', '')))
                
                # Если элемент удален, блокируем кнопки и ячейки
                if found_data is None:
                    for c in range(1, 4):
                         if table.item(i, c): table.item(i, c).setFlags(Qt.ItemFlag.NoItemFlags)

                delete_btn = QPushButton("🗑️"); delete_btn.setToolTip(f"Удалить термин '{entry.get('original')}'"); delete_btn.clicked.connect(lambda ch, e=entry: self._delete_entry(e))
                if found_data is None: delete_btn.setEnabled(False)
                actions_layout.addWidget(delete_btn)

            elif item_type == 'fragment':
                fragment = item_data
                frag_item = QTableWidgetItem(fragment)
                frag_item.setFlags(frag_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(i, 1, frag_item)
                
                match_entry = all_originals_map.get(fragment)
                if match_entry and not self._is_entry_deleted(match_entry):
                    found_data = next((p['after'] for p in self.get_final_patch() if p.get('before') and self._get_entry_id(p['before']) == self._get_entry_id(match_entry)), match_entry)
                    current_data = found_data if found_data is not None else {}
                    
                    type_item.setData(Qt.ItemDataRole.UserRole, match_entry)
                    table.setItem(i, 2, QTableWidgetItem(current_data.get('rus', '')))
                    table.setItem(i, 3, QTableWidgetItem(current_data.get('note', '')))
                    delete_btn = QPushButton("🗑️"); delete_btn.setToolTip(f"Удалить термин '{fragment}'"); delete_btn.clicked.connect(lambda ch, e=match_entry: self._delete_entry(e))
                    actions_layout.addWidget(delete_btn)
                else:
                    for col_idx in [2, 3]:
                        read_only_item = QTableWidgetItem("")
                        read_only_item.setFlags(read_only_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                        table.setItem(i, col_idx, read_only_item)
                    create_btn = QPushButton("➕"); create_btn.setToolTip(f"Создать новый термин '{fragment}'"); create_btn.clicked.connect(lambda ch, f=fragment: self._create_new_term_locally({'original': f}, QLineEdit(f), QLineEdit(), QLineEdit()))
                    actions_layout.addWidget(create_btn)
            table.setCellWidget(i, 4, actions_widget)

        table.blockSignals(False)
        table.resizeRowsToContents(); layout.addWidget(table); return group
        
        
    def _on_sub_table_item_changed(self, item):
        """Обрабатывает ручное редактирование ячеек в таблице связанных записей."""
        table = item.tableWidget()
        if not table or table.signalsBlocked():
            return
            
        row = item.row()
        # Получаем исходные данные записи, сохраненные при создании строки
        original_entry_data = table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        
        # Если данных нет (например, это строка с фрагментом без записи), выходим
        if not original_entry_data or not isinstance(original_entry_data, dict):
            return

        # Собираем новое состояние из всех ячеек строки
        after_state = {
            'original': table.item(row, 1).text(),
            'rus': table.item(row, 2).text(),
            'note': table.item(row, 3).text(),
        }
        
        self._add_or_update_patch(original_entry_data, after_state)
    
    def _on_editor_item_changed(self, original_entry_data, original_edit, translation_edit, note_edit, is_new):
        after_state = {'original': original_edit.text(), 'rus': translation_edit.toPlainText(), 'note': note_edit.toPlainText()}
        
        # ИЗМЕНЕНИЕ: Создаем правильный тип патча в зависимости от того, новый это термин или существующий
        if is_new:
            # Для нового термина 'before' всегда None - это операция "добавления"
            self._add_or_update_patch(None, after_state)
        else:
            # Для существующего термина указываем 'before' - это операция "обновления"
            self._add_or_update_patch(original_entry_data, after_state)

    def _add_or_update_patch(self, before_state, after_state):
        # ЗАЩИТА: before_state ОБЯЗАН быть словарем (или None). 
        # Если пришла строка или мусор - считаем, что это "Новая" запись (before=None),
        # иначе GlossaryDialog упадет при попытке прочитать before['original'].
        if before_state is not None and not isinstance(before_state, dict):
            before_state = None

        key = self._get_entry_id(before_state) if before_state and 'original' in before_state else ('new', after_state['original'])
        
        for i, change in enumerate(self.patch_list):
            if change.get('before') and self._get_entry_id(change['before']) == key:
                self.patch_list[i]['after'] = after_state; return
            if not change.get('before') and change.get('after') and ('new', change.get('after')['original']) == key:
                 self.patch_list[i]['after'] = after_state; return
        self.patch_list.append({'before': before_state, 'after': after_state})

    def _delete_entry(self, entry_to_delete):
        all_to_delete = [e for e in self.get_current_glossary_state() if e.get('original') == entry_to_delete.get('original')]
        for entry in all_to_delete:
            if not self._is_entry_deleted(entry):
                self.patch_list.append({'before': entry, 'after': None})
        
        # ИЗМЕНЕНИЕ: Немедленно перерисовываем правую панель, чтобы показать результат
        if current_item := self.left_list.currentItem():
            self._display_details(current_item.text())

    def _create_new_term_locally(self, original_data, original_edit, translation_edit, note_edit):
        new_entry_data = {
            "original": original_edit.text(),
            "rus": translation_edit.toPlainText() if isinstance(translation_edit, QTextEdit) else translation_edit.text(),
            "note": note_edit.toPlainText() if isinstance(note_edit, QTextEdit) else note_edit.text()
        }
        if not new_entry_data['original']: return
        self._add_or_update_patch(None, new_entry_data)
        
        # ИЗМЕНЕНИЕ: Немедленно перерисовываем правую панель, чтобы показать результат
        if current_item := self.left_list.currentItem():
            self._display_details(current_item.text())

    def _clear_right_panel(self):
        layout = self.right_panel_container_layout
        while layout.count():
            child = layout.takeAt(0)
            if child.widget(): child.widget().deleteLater()

    def get_current_glossary_state(self):
        current_map = {self._get_entry_id(e): e for e in self.original_glossary_list}
        final_patch = self.get_final_patch()
        for change in final_patch:
            before, after = change['before'], change['after']
            if before and not after:
                if (key := self._get_entry_id(before)) in current_map: del current_map[key]
            elif not before and after:
                current_map[self._get_entry_id(after)] = after
            elif before and after:
                if (before_key := self._get_entry_id(before)) in current_map: del current_map[before_key]
                current_map[self._get_entry_id(after)] = after
        return list(current_map.values())

    def get_final_patch(self):
        final_patch_map = {}
        for change in self.patch_list:
            before, after = change['before'], change['after']
            key = self._get_entry_id(before) if before else ('new', after.get('original'))
            if key in final_patch_map: final_patch_map[key]['after'] = after
            else: final_patch_map[key] = change
        return list(final_patch_map.values())
        
    def _is_entry_deleted(self, entry):
        entry_id = self._get_entry_id(entry)
        for change in self.get_final_patch():
            if change.get('before') and not change.get('after') and self._get_entry_id(change['before']) == entry_id:
                return True
        return False
    
    def _open_exceptions_manager(self):
        dialog = QDialog(self); dialog.setWindowTitle("Менеджер списков слов-исключений"); dialog.setMinimumSize(700, 500)
        layout = QVBoxLayout(dialog)
        exceptions_widget = PresetWidget(
            parent=dialog, preset_name="Список исключений",
            default_prompt_func=api_config.default_word_exceptions,
            load_presets_func=self.settings_manager.load_word_exceptions_presets,
            save_presets_func=self.settings_manager.save_word_exceptions_presets,
            get_last_text_func=self.settings_manager.get_last_word_exceptions_text
        )
        exceptions_widget.load_last_session_state()
        layout.addWidget(exceptions_widget)
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.button(QDialogButtonBox.StandardButton.Ok).setText("Принять и перефильтровать")
        button_box.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        button_box.accepted.connect(dialog.accept); button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            exceptions_widget.save_last_session_state()
            self.settings_manager.save_last_word_exceptions_text(exceptions_widget.get_prompt())
            self._apply_all_filters_and_update_view()
