import re
from collections import Counter, defaultdict
from PyQt6 import QtWidgets, QtCore, QtGui
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, 
    QTableWidget, QTableWidgetItem, QHeaderView, QCheckBox, 
    QSpinBox, QGroupBox, QMessageBox, QAbstractItemView, QApplication
)
from PyQt6.QtCore import Qt

class GroupAnalysisDialog(QDialog):
    """
    Диалог для группировки терминов по часто встречающимся словам.
    Работает строго со СПИСКАМИ индексов, чтобы сохранить дубликаты.
    """
    def __init__(self, full_glossary, parent=None):
        super().__init__(parent)
        # Сохраняем ссылку на исходный список. Порядок в нем важен для индексов.
        self.full_glossary = full_glossary
        self.parent_manager = parent
        
        # Карта: слово -> список индексов записей в full_glossary
        self.word_to_indices_map = defaultdict(list)
        self.word_counts = Counter()
        
        self.setWindowTitle("Анализ групп терминов (сохранение дубликатов)")
        self.setMinimumSize(950, 650) # Чуть расширил для нового контрола
        
        self._init_ui()
        self._run_analysis()
        self._apply_filters() 

    def _init_ui(self):
        layout = QVBoxLayout(self)
        
        # --- Верхняя панель фильтров ---
        filter_group = QGroupBox("Настройки фильтрации")
        filter_layout = QHBoxLayout(filter_group)
        
        # 1. Фильтр длины (НОВОЕ)
        filter_layout.addWidget(QLabel("Мин. длина слова:"))
        self.len_spin = QSpinBox()
        self.len_spin.setRange(1, 20)
        self.len_spin.setValue(3) # По умолчанию 3, но можно поставить 2 для "го"
        self.len_spin.setToolTip("Минимальное количество букв, чтобы считать слово группой.")
        filter_layout.addWidget(self.len_spin)
        
        # Разделитель для красоты
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.Shape.VLine)
        line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        filter_layout.addWidget(line)

        # 2. Фильтр частоты
        filter_layout.addWidget(QLabel("Частота от:"))
        self.min_spin = QSpinBox()
        self.min_spin.setRange(1, 99999)
        self.min_spin.setValue(3)
        self.min_spin.setToolTip("Слова, встречающиеся реже этого числа, будут скрыты.")
        filter_layout.addWidget(self.min_spin)
        
        filter_layout.addWidget(QLabel("до:"))
        self.max_spin = QSpinBox()
        self.max_spin.setRange(1, 99999)
        
        # --- Динамический расчет верхнего порога ---
        norm_value = int(len(self.full_glossary) * 0.3)
        default_max = max(10, norm_value)
        
        self.max_spin.setValue(default_max)
        self.max_spin.setToolTip("Слова, встречающиеся чаще этого числа, будут скрыты (отсечение 'мусорных' слов).")
        filter_layout.addWidget(self.max_spin)
        
        self.refresh_btn = QPushButton("Обновить список")
        # ИЗМЕНЕНИЕ: Теперь вызываем _on_full_refresh, так как изменение длины требует перепарсинга
        self.refresh_btn.clicked.connect(self._on_full_refresh)
        filter_layout.addWidget(self.refresh_btn)
        filter_layout.addStretch()
        
        layout.addWidget(filter_group)
        
        # --- Таблица результатов ---
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Выбрать", "Слово (Маркер)", "Записей", "Примеры терминов"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents) 
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents) 
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents) 
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)          
        
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self.table)
        
        # --- Нижняя панель действий ---
        bottom_layout = QHBoxLayout()
        
        self.info_label = QLabel("Отметьте галочками группы для обработки.")
        self.info_label.setStyleSheet("color: grey;")
        
        self.open_editor_btn = QPushButton("Открыть редактор для выбранных")
        self.open_editor_btn.setStyleSheet("font-weight: bold; font-size: 11pt; padding: 6px;")
        self.open_editor_btn.clicked.connect(self._open_child_editor)
        
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.reject)
        
        bottom_layout.addWidget(self.info_label)
        bottom_layout.addStretch()
        bottom_layout.addWidget(self.open_editor_btn)
        bottom_layout.addWidget(close_btn)
        
        layout.addLayout(bottom_layout)

    def _on_full_refresh(self):
        """Полный пересчет (если изменилась длина) и перерисовка."""
        self.table.setRowCount(0)
        self._run_analysis()
        self._apply_filters()

    def _run_analysis(self):
        """Парсит глоссарий и строит индекс по словам."""
        self.word_to_indices_map.clear()
        self.word_counts.clear()
        
        # ИЗМЕНЕНИЕ: Получаем мин. длину из UI
        min_word_len = self.len_spin.value()
        
        splitter = re.compile(r"[\w'-]+")
        ignored_words = {'для', 'или', 'под', 'над', 'без', 'при', 'про', 'как', 'the', 'and', 'for', 'with', 'of', 'in'}
        
        # Используем enumerate для получения стабильных индексов
        for i, entry in enumerate(self.full_glossary):
            # Используем get с дефолтом, чтобы не упасть на пустых записях
            original = str(entry.get('original', ''))
            rus = str(entry.get('rus', ''))
            note = str(entry.get('note', ''))
            
            # Анализируем все поля
            text_to_analyze = f"{original} {rus} {note}".lower()
            words = splitter.findall(text_to_analyze)
            
            unique_words_in_entry = set()
            for w in words:
                # ИЗМЕНЕНИЕ: Используем динамическую переменную вместо хардкода '3'
                if len(w) < min_word_len or w in ignored_words: 
                    continue
                unique_words_in_entry.add(w)
            
            for w in unique_words_in_entry:
                self.word_counts[w] += 1
                self.word_to_indices_map[w].append(i)

    def _apply_filters(self):
        """Фильтрует данные и заполняет таблицу."""
        min_val = self.min_spin.value()
        max_val = self.max_spin.value()
        
        filtered_items = []
        for word, count in self.word_counts.items():
            if min_val <= count <= max_val:
                filtered_items.append((word, count))
        
        filtered_items.sort(key=lambda x: x[1], reverse=True)
        
        self.table.setRowCount(0)
        self.table.setRowCount(len(filtered_items))
        
        for i, (word, count) in enumerate(filtered_items):
            # Чекбокс
            chk_item = QTableWidgetItem()
            chk_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk_item.setCheckState(Qt.CheckState.Unchecked)
            chk_item.setData(Qt.ItemDataRole.UserRole, word)
            self.table.setItem(i, 0, chk_item)
            
            self.table.setItem(i, 1, QTableWidgetItem(word))
            self.table.setItem(i, 2, QTableWidgetItem(str(count)))
            
            # --- ИЗМЕНЕНИЕ: Формирование понятных примеров ---
            indices = self.word_to_indices_map[word]
            examples_list = []
            
            # Берем первые 4 примера для наглядности
            for idx in indices[:4]:
                entry = self.full_glossary[idx]
                trans = str(entry.get('rus', '')).strip()
                note = str(entry.get('note', '')).strip()
                
                display_text = trans
                
                # Логика отображения: Перевод > [Примечание] > Оригинал
                if not display_text:
                    if note:
                        display_text = f"[{note}]"
                    else:
                        display_text = str(entry.get('original', '???'))
                
                # Если перевод есть, но он короткий, добавляем кусочек примечания для контекста
                elif note and len(display_text) < 20:
                    short_note = (note[:10] + '..') if len(note) > 10 else note
                    display_text += f" ({short_note})"
                
                examples_list.append(display_text)

            examples_str = "; ".join(examples_list)
            if len(indices) > 4: examples_str += "; ..."
            
            ex_item = QTableWidgetItem(examples_str)
            ex_item.setToolTip(examples_str) # Полный текст при наведении
            self.table.setItem(i, 3, ex_item)

    def _open_child_editor(self):
        """Собирает термины по индексам и открывает дочерний менеджер."""
        selected_words = []
        for i in range(self.table.rowCount()):
            item = self.table.item(i, 0)
            if item.checkState() == Qt.CheckState.Checked:
                word = item.data(Qt.ItemDataRole.UserRole)
                selected_words.append(word)
        
        if not selected_words:
            QMessageBox.warning(self, "Ничего не выбрано", "Выберите хотя бы одну группу галочкой.")
            return
            
        # --- Сбор уникальных индексов строк ---
        # Используем set ТОЛЬКО для индексов, чтобы не открывать одну и ту же строку дважды,
        # если она попала в несколько выбранных групп (например, "Огненный Меч" попал и в "Огненный", и в "Меч").
        target_indices_set = set()
        for word in selected_words:
            # word_to_indices_map хранит СПИСКИ индексов, дубликаты строк тут уже учтены
            target_indices_set.update(self.word_to_indices_map[word])
            
        if not target_indices_set:
             QMessageBox.warning(self, "Ошибка", "Не найдено записей для выбранных групп.")
             return

        # Сортируем индексы для корректного извлечения
        sorted_indices = sorted(list(target_indices_set))
        
        # Извлекаем полные записи (словари) из исходного списка по индексам.
        # Если в исходном списке были дубликаты на разных строках, они попадут сюда как разные объекты.
        subset_glossary = [self.full_glossary[i] for i in sorted_indices]
        
        from gemini_translator.ui.dialogs.glossary import MainWindow as GlossaryManager
        
        # Создаем дочернее окно поверх текущего
        child_manager = GlossaryManager(parent=self, mode='child')
        title_part = ", ".join(selected_words[:3])
        if len(selected_words) > 3: title_part += "..."
        child_manager.setWindowTitle(f"Редактор групп ({len(subset_glossary)} записей): {title_part}")
        
        # Передаем данные. В режиме 'child' используется изолированная БД, так что
        # родительские данные в безопасности до момента применения.
        child_manager.set_glossary(subset_glossary, run_analysis=True)
        
        # Окно открывается модально поверх текущего.
        result = child_manager.exec()
        
        if result == QDialog.DialogCode.Accepted:
            # Получаем измененный СПИСОК (включая новые, удаленные и дубликаты)
            modified_subset = child_manager.get_glossary()
            
            # Запускаем применение с задержкой для обновления UI
            # Передаем sorted_indices, чтобы знать, какие строки удалять из родителя
            QtCore.QTimer.singleShot(50, lambda: self._apply_changes_to_parent(modified_subset, sorted_indices))
        
    def _apply_changes_to_parent(self, modified_subset, original_indices):
        """
        Сливает изменения обратно в родителя, сохраняя целостность остальных данных.
        """
        if not self.parent_manager: return
        
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            # 1. Получаем текущее состояние родителя
            parent_glossary = self.parent_manager.get_glossary()
            
            # Проверка безопасности
            if len(parent_glossary) != len(self.full_glossary):
                QApplication.restoreOverrideCursor()
                QMessageBox.critical(self, "Ошибка синхронизации", 
                                     "Данные родительского окна изменились в фоне. "
                                     "Применение изменений отменено во избежание потери данных.")
                return

            # 2. Формируем новый список
            # Превращаем индексы в множество для мгновенного поиска (O(1))
            indices_to_remove = set(original_indices)
            new_parent_glossary = []
            
            # Проходим по родителю и оставляем только те строки, которые НЕ редактировались
            for i, entry in enumerate(parent_glossary):
                if i not in indices_to_remove:
                    new_parent_glossary.append(entry)
            
            # 3. Добавляем результат редактирования (список!) в конец
            # Если в modified_subset были дубликаты, они добавятся как есть.
            new_parent_glossary.extend(modified_subset)
            
            # 4. Применяем изменения в менеджере (с записью в Undo/Redo)
            self.parent_manager.add_history('wholesale', {
                'action_name': "Групповая правка", 
                'description': f"Обработана группа ({len(original_indices)} -> {len(modified_subset)} записей)", 
                'old_state': parent_glossary
            })
            
            self.parent_manager.set_glossary(new_parent_glossary, run_analysis=True)
            
            # 5. Обновляем данные в текущем диалоге, чтобы можно было продолжить работу
            self.full_glossary = new_parent_glossary
            self._run_analysis()
            self._apply_filters()
            
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Ошибка", f"Не удалось применить изменения:\n{e}")
            return
        finally:
            QApplication.restoreOverrideCursor()
        
        QMessageBox.information(self, "Готово", "Изменения успешно применены.")