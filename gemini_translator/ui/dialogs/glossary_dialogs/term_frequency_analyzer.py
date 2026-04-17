# gemini_translator/ui/dialogs/glossary_dialogs/term_frequency_analyzer.py

import os
from PyQt6 import QtWidgets, QtCore
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, 
    QTableWidget, QTableWidgetItem, QHeaderView, QWidget, QGroupBox, 
    QProgressBar, QMessageBox, QDialogButtonBox, QTabWidget, QAbstractItemView
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QBrush

# Импорты проекта
from gemini_translator.utils.project_manager import TranslationProjectManager
from gemini_translator.utils.term_frequency_tools import (
    GlossaryFrequencyWorker,
    get_term_frequency_map,
    is_term_frequency_payload_valid,
)
from gemini_translator.ui.widgets.common_widgets import NoScrollSpinBox
from .custom_widgets import ExpandingTextEditDelegate

class TermFrequencyAnalyzerDialog(QDialog):
    def __init__(self, glossary_data, epub_path=None, parent=None):
        super().__init__(parent)
        self.glossary_data = glossary_data
        self.glossary_map = {
            e.get('original', '').strip(): e 
            for e in glossary_data if e.get('original', '').strip()
        }
        self.epub_path = epub_path
        self.worker = None
        self.frequency_payload = {}
        self.project_manager = None

        parent_window = self.parent()
        project_path = getattr(parent_window, 'associated_project_path', None)
        if project_path:
            self.project_manager = TranslationProjectManager(project_path)
        
        # Данные анализа
        self.raw_distribution = {}
        self.term_counts = {}
        
        # Данные для списков
        self.freq_items_all = []
        self.rare_items_all = []
        
        # Пагинация для "Частых"
        self.freq_page = 0
        self.ITEMS_PER_PAGE = 100
        
        # Изменения
        self.terms_to_delete = set()
        self.pending_updates = {} # {original: {'rus': ..., 'note': ...}}
        
        self.setWindowTitle("Глобальный частотный анализ")
        self.resize(1200, 800)
        
        self._init_ui()
        QtCore.QTimer.singleShot(100, self._start_analysis_flow)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        
        # 1. Прогресс
        self.progress_group = QGroupBox("Сканирование книги")
        prog_layout = QVBoxLayout(self.progress_group)
        self.progress_label = QLabel("Подготовка...")
        self.progress_bar = QProgressBar()
        prog_layout.addWidget(self.progress_label)
        prog_layout.addWidget(self.progress_bar)
        self.progress_group.setVisible(False)
        layout.addWidget(self.progress_group)
        
        # 2. Контент
        self.content_widget = QWidget()
        content_layout = QVBoxLayout(self.content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        
        self.tabs = QTabWidget()
        
        # --- Вкладка 1: Редкие (Кандидаты на удаление) ---
        self.rare_tab = QWidget(); rare_layout = QVBoxLayout(self.rare_tab)
        rare_settings = QHBoxLayout()
        rare_settings.addWidget(QLabel("Показать термины, найденные в ≤"))
        self.threshold_spin = NoScrollSpinBox()
        self.threshold_spin.setRange(0, 999999); self.threshold_spin.setValue(1); self.threshold_spin.setSuffix(" вхождений")
        self.threshold_spin.valueChanged.connect(self._refresh_tables)
        rare_settings.addWidget(self.threshold_spin); rare_settings.addStretch()
        
        self.rare_table = self._create_table(editable=False)
        # --- ИСПРАВЛЕНИЕ: Подключаем сигнал изменений для таблицы редких терминов ---
        self.rare_table.itemChanged.connect(self._on_item_changed)
        # ---------------------------------------------------------------------------

        rare_buttons = QHBoxLayout()
        btn_sel_rare = QPushButton("Выделить всё на этой вкладке")
        btn_sel_rare.clicked.connect(lambda: self._toggle_table_checks(self.rare_table, True))
        btn_desel_rare = QPushButton("Снять выделение")
        btn_desel_rare.clicked.connect(lambda: self._toggle_table_checks(self.rare_table, False))
        rare_buttons.addWidget(btn_sel_rare); rare_buttons.addWidget(btn_desel_rare); rare_buttons.addStretch()
        
        rare_layout.addLayout(rare_settings); rare_layout.addWidget(self.rare_table); rare_layout.addLayout(rare_buttons)
        
        # --- Вкладка 2: Частые (Редактирование) ---
        self.freq_tab = QWidget(); freq_layout = QVBoxLayout(self.freq_tab)
        
        freq_label = QLabel("Самые частые термины по числу вхождений (Топ-500). Доступно редактирование перевода и примечаний.")
        freq_label.setStyleSheet("color: grey;")
        
        self.freq_table = self._create_table(editable=True)
        # Подключаем сигнал изменения для сохранения правок (для частых)
        self.freq_table.itemChanged.connect(self._on_freq_item_changed)
        
        # Панель пагинации
        pagination_layout = QHBoxLayout()
        self.btn_prev = QPushButton("<< Назад")
        self.btn_prev.clicked.connect(self._prev_page)
        self.lbl_page = QLabel("Страница 1")
        self.btn_next = QPushButton("Вперед >>")
        self.btn_next.clicked.connect(self._next_page)
        
        pagination_layout.addWidget(self.btn_prev)
        pagination_layout.addWidget(self.lbl_page)
        pagination_layout.addWidget(self.btn_next)
        pagination_layout.addStretch()
        
        freq_buttons = QHBoxLayout()
        btn_desel_freq = QPushButton("Снять выделение (удаление)")
        btn_desel_freq.clicked.connect(lambda: self._toggle_table_checks(self.freq_table, False))
        freq_buttons.addWidget(btn_desel_freq); freq_buttons.addStretch()
        
        freq_layout.addWidget(freq_label)
        freq_layout.addWidget(self.freq_table)
        freq_layout.addLayout(pagination_layout)
        freq_layout.addLayout(freq_buttons)
        
        self.tabs.addTab(self.rare_tab, "👻 Кандидаты на удаление (Редкие)")
        self.tabs.addTab(self.freq_tab, "🔥 Самые частые (Редактирование)")
        content_layout.addWidget(self.tabs)
        
        layout.addWidget(self.content_widget)
        self.content_widget.setVisible(False)
        
        # 3. Кнопки
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Применить изменения")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _create_table(self, editable=False):
        table = QTableWidget()
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels(["Оригинал", "Перевод", "Примечание", "Частота"])
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents) # Оригинал
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)          # Перевод
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)          # Примечание
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents) # Частота
        
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        
        # Стилизация
        table.setStyleSheet("""
            QTableWidget {
                background-color: #2c313c; 
                alternate-background-color: #353b48;
                color: #f0f0f0; 
                border: 1px solid #4d5666;
            }
            QTableWidget::item:selected {
                background-color: #4a6984; 
            }
        """)
        table.setAlternatingRowColors(True)

        if editable:
            # Подключаем умный делегат для расширения строк при редактировании
            delegate = ExpandingTextEditDelegate(table)
            table.setItemDelegateForColumn(1, delegate)
            table.setItemDelegateForColumn(2, delegate)
        
        # Сигнал itemChanged подключим позже или в init, чтобы не двоился
        return table

    def _load_cached_payload(self):
        if not self.project_manager:
            return {}

        payload = self.project_manager.load_term_frequency_cache()
        if is_term_frequency_payload_valid(payload, self.glossary_data, self.epub_path):
            return payload
        return {}

    def _apply_frequency_payload(self, payload):
        self.frequency_payload = payload or {}
        frequency_map = get_term_frequency_map(self.frequency_payload)

        self.raw_distribution = {
            term: set(stats.get('files', []))
            for term, stats in frequency_map.items()
            if isinstance(stats, dict)
        }
        self.term_counts = {
            term: int(stats.get('count', 0) or 0)
            for term, stats in frequency_map.items()
            if isinstance(stats, dict)
        }

        if self.term_counts:
            self.threshold_spin.setMaximum(max(self.term_counts.values()))

        self.progress_group.setVisible(False)
        self.content_widget.setVisible(True)
        self._refresh_tables()

    def _start_analysis_flow(self):
        if not self.epub_path or not os.path.exists(self.epub_path):
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, "Выберите EPUB для анализа", "", "EPUB Files (*.epub)"
            )
            if path:
                self.epub_path = path
            else:
                self.reject()
                return

        cached_payload = self._load_cached_payload()
        if cached_payload:
            self._apply_frequency_payload(cached_payload)
            return

        self.progress_group.setVisible(True)
        self.worker = GlossaryFrequencyWorker(self.epub_path, self.glossary_data, self)
        self.worker.progress_update.connect(self._update_progress)
        self.worker.analysis_finished.connect(self._on_analysis_finished)
        self.worker.error_occurred.connect(self._on_error)
        self.worker.start()

    def _update_progress(self, current, total, filename):
        self.progress_bar.setMaximum(max(1, total))
        self.progress_bar.setValue(current)
        self.progress_label.setText(f"Сканирование: {filename} ({current}/{total})")

    def _on_error(self, msg):
        self.progress_group.setVisible(False)
        QtWidgets.QMessageBox.critical(self, "Ошибка", f"Ошибка анализа: {msg}")
        self.reject()

    def _on_analysis_finished(self, payload):
        if self.project_manager and payload:
            self.project_manager.save_term_frequency_cache(payload)
        self._apply_frequency_payload(payload)

    def _refresh_tables(self):
        threshold = self.threshold_spin.value()
        
        sorted_by_count_asc = sorted(self.term_counts.items(), key=lambda x: x[1])
        sorted_by_count_desc = sorted(self.term_counts.items(), key=lambda x: x[1], reverse=True)
        
        # 1. Редкие (все, что ниже порога)
        self.rare_items_all = [t for t in sorted_by_count_asc if t[1] <= threshold]
        
        # 2. Частые (Топ-500, исключая те, что попали в редкие)
        # Увеличил лимит, так как теперь есть пагинация
        self.freq_items_all = [t for t in sorted_by_count_desc[:500] if t[1] > threshold]
        
        # Заполняем таблицу редких (без пагинации, их обычно просто удаляют)
        self._populate_rare_table()
        
        # Заполняем таблицу частых (с пагинацией)
        self.freq_page = 0
        self._update_freq_table_view()
        
        self.tabs.setTabText(0, f"👻 Кандидаты ({len(self.rare_items_all)})")
        self.tabs.setTabText(1, f"🔥 Частые ({len(self.freq_items_all)})")

    def _populate_rare_table(self):
        table = self.rare_table
        items = self.rare_items_all
        
        table.blockSignals(True)
        table.setRowCount(0)
        table.setRowCount(len(items))
        table.setSortingEnabled(False)
        
        for i, (term, count) in enumerate(items):
            self._create_row(table, i, term, count, editable=False)
            
        table.setSortingEnabled(True)
        table.blockSignals(False)

    def _update_freq_table_view(self):
        """Обновляет таблицу частых терминов для текущей страницы."""
        start = self.freq_page * self.ITEMS_PER_PAGE
        end = start + self.ITEMS_PER_PAGE
        page_items = self.freq_items_all[start:end]
        
        total_pages = (len(self.freq_items_all) + self.ITEMS_PER_PAGE - 1) // self.ITEMS_PER_PAGE or 1
        self.lbl_page.setText(f"Страница {self.freq_page + 1} из {total_pages}")
        self.btn_prev.setEnabled(self.freq_page > 0)
        self.btn_next.setEnabled(self.freq_page < total_pages - 1)
        
        table = self.freq_table
        table.blockSignals(True)
        table.setRowCount(0)
        table.setRowCount(len(page_items))
        table.setSortingEnabled(False)
        
        for i, (term, count) in enumerate(page_items):
            self._create_row(table, i, term, count, editable=True)
            
        table.setSortingEnabled(True)
        table.blockSignals(False)

    def _create_row(self, table, row, term, count, editable):
        entry = self.glossary_map.get(term, {})
        
        # Проверяем, есть ли незакомиченные изменения для этого термина
        current_trans = self.pending_updates.get(term, {}).get('rus', entry.get('rus', ''))
        current_note = self.pending_updates.get(term, {}).get('note', entry.get('note', ''))
        
        # Колонка 0: Оригинал + Чекбокс
        orig_item = QTableWidgetItem(term)
        orig_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        
        is_checked = term in self.terms_to_delete
        orig_item.setCheckState(Qt.CheckState.Checked if is_checked else Qt.CheckState.Unchecked)
        orig_item.setData(Qt.ItemDataRole.UserRole, term)
        table.setItem(row, 0, orig_item)
        
        # Колонка 1: Перевод
        trans_item = QTableWidgetItem(current_trans)
        if editable:
            trans_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsEditable)
        else:
            trans_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        table.setItem(row, 1, trans_item)
        
        # Колонка 2: Примечание
        note_item = QTableWidgetItem(current_note)
        if editable:
            note_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsEditable)
        else:
            note_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        table.setItem(row, 2, note_item)
        
        # Колонка 3: Частота
        count_item = QTableWidgetItem(str(count))
        count_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        count_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        
        files = sorted(list(self.raw_distribution.get(term, set())))
        if files:
            tooltip = "Найден в:\n" + "\n".join(files[:15])
            if len(files) > 15: tooltip += f"\n... и еще {len(files)-15}"
            count_item.setToolTip(tooltip)
        else:
            count_item.setToolTip("Не найден ни в одной главе")
        table.setItem(row, 3, count_item)
        
        # Красим, если отмечен на удаление
        if is_checked:
            self._color_row(table, row, True)

    def _on_item_changed(self, item):
        """Обработчик чекбокса в первой колонке редких терминов."""
        if item.column() == 0:
            term = item.data(Qt.ItemDataRole.UserRole)
            is_checked = (item.checkState() == Qt.CheckState.Checked)
            
            if is_checked:
                self.terms_to_delete.add(term)
            else:
                self.terms_to_delete.discard(term)
            
            table = item.tableWidget()
            # Используем блокировку, чтобы не вызвать рекурсию
            table.blockSignals(True)
            self._color_row(table, item.row(), is_checked)
            table.blockSignals(False)
            
    def _on_freq_item_changed(self, item):
        """
        Обработчик изменений в таблице частых терминов.
        Обрабатывает и чекбоксы (удаление), и редактирование текста.
        """
        row = item.row()
        col = item.column()
        table = self.freq_table
        
        # Получаем термин из первой колонки
        orig_item = table.item(row, 0)
        term = orig_item.data(Qt.ItemDataRole.UserRole)
        
        # 1. Чекбокс (удаление)
        if col == 0:
            is_checked = (item.checkState() == Qt.CheckState.Checked)
            if is_checked:
                self.terms_to_delete.add(term)
                # Если удаляем, то правки можно стереть (опционально)
            else:
                self.terms_to_delete.discard(term)
            
            table.blockSignals(True)
            self._color_row(table, row, is_checked)
            table.blockSignals(False)
            
        # 2. Редактирование перевода (col 1) или примечания (col 2)
        elif col in [1, 2]:
            new_text = item.text().strip()
            
            # Инициализируем запись в pending_updates, если нет
            if term not in self.pending_updates:
                original_entry = self.glossary_map.get(term, {})
                self.pending_updates[term] = {
                    'rus': original_entry.get('rus', ''),
                    'note': original_entry.get('note', '')
                }
            
            if col == 1:
                self.pending_updates[term]['rus'] = new_text
            elif col == 2:
                self.pending_updates[term]['note'] = new_text

    def _color_row(self, table, row, is_checked):
        color = QColor(231, 76, 60, 25) if is_checked else QColor(0, 0, 0, 0)
        brush = QBrush(color)
        for i in range(table.columnCount()):
            it = table.item(row, i)
            if it: it.setBackground(brush)

    def _prev_page(self):
        if self.freq_page > 0:
            self.freq_page -= 1
            self._update_freq_table_view()

    def _next_page(self):
        total_pages = (len(self.freq_items_all) + self.ITEMS_PER_PAGE - 1) // self.ITEMS_PER_PAGE
        if self.freq_page < total_pages - 1:
            self.freq_page += 1
            self._update_freq_table_view()

    def _toggle_table_checks(self, table, state):
        check_state = Qt.CheckState.Checked if state else Qt.CheckState.Unchecked
        table.blockSignals(True) # Отключаем сигналы, чтобы не дергать логику на каждой строке
        
        for i in range(table.rowCount()):
            item = table.item(i, 0)
            item.setCheckState(check_state)
            
            term = item.data(Qt.ItemDataRole.UserRole)
            if state:
                self.terms_to_delete.add(term)
            else:
                self.terms_to_delete.discard(term)
                
            self._color_row(table, i, state)

        table.blockSignals(False)

    def get_patch(self):
        """
        Генерирует список изменений (Patch) для применения в GlossaryWidget.
        Удаление имеет приоритет над редактированием.
        """
        patch_list = []
        
        # 1. Сначала обрабатываем удаления
        for term in self.terms_to_delete:
            old_entry = self.glossary_map.get(term)
            if old_entry:
                patch_list.append({'before': old_entry, 'after': None})
        
        # 2. Затем обрабатываем обновления (только если термин не удален)
        for term, new_data in self.pending_updates.items():
            if term in self.terms_to_delete:
                continue
            
            old_entry = self.glossary_map.get(term)
            if not old_entry: continue
            
            # Проверяем, изменилось ли что-то реально
            if (new_data['rus'] != old_entry.get('rus', '') or 
                new_data['note'] != old_entry.get('note', '')):
                
                new_entry = old_entry.copy()
                new_entry.update(new_data)
                patch_list.append({'before': old_entry, 'after': new_entry})
                
        return patch_list

    def reject(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(2000)
        super().reject()

    def accept(self):
        """Подтверждение действий."""
        terms_del_count = len(self.terms_to_delete)
        # Считаем реальные обновления (исключая удаленные)
        updates_count = sum(1 for t in self.pending_updates if t not in self.terms_to_delete)
        
        if terms_del_count == 0 and updates_count == 0:
            QMessageBox.information(self, "Нет изменений", "Вы ничего не изменили.")
            return

        msg = QMessageBox(self)
        msg.setWindowTitle("Подтверждение")
        msg.setIcon(QMessageBox.Icon.Question)
        
        msg.setText("Применить следующие изменения?")
        details = []
        if terms_del_count: details.append(f"• Удалить терминов: {terms_del_count}")
        if updates_count: details.append(f"• Обновить терминов: {updates_count}")
        
        msg.setInformativeText("\n".join(details))
        
        btn_yes = msg.addButton("Да, применить", QMessageBox.ButtonRole.YesRole)
        btn_no = msg.addButton("Нет, вернуться", QMessageBox.ButtonRole.NoRole)
        msg.setDefaultButton(btn_yes)
        
        msg.exec()
        
        if msg.clickedButton() == btn_yes:
            super().accept()
