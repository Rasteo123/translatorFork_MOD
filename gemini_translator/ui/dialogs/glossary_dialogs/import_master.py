
import json
import re

# --- Импорты из PyQt6 ---
from PyQt6 import QtWidgets, QtCore, QtGui
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QPushButton, QDialogButtonBox, QLabel,
    QWidget, QGroupBox, QHBoxLayout, QGridLayout, QTableWidget,
    QHeaderView, QTableWidgetItem, QMessageBox, QAbstractItemView,
    QStackedWidget, QLineEdit, QTextEdit
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor

# --- Импорты из вашего проекта ---
from gemini_translator.ui.widgets.common_widgets import NoScrollComboBox, NoScrollSpinBox
from gemini_translator.utils.text import repair_json_string
# ---------------------------------------------------------------------------
# ---  КЛАСС: Мастер импорта данных
# ---------------------------------------------------------------------------


class ImporterWizardDialog(QDialog):
    """
    Супер-гибкий "Мастер импорта", который позволяет в несколько шагов
    настроить правила разбора для сложных текстовых форматов и JSON.
    Версия 8.1: Исправлена ошибка при работе с данными из таблицы.
    """
    def __init__(self, initial_data, is_from_table=False, parent=None, multi_file_mode=False):
        super().__init__(parent)
        self.setWindowTitle("Мастер импорта данных (v8.2)")

        # --- Адаптивная геометрия окна ---
        # 1. Устанавливаем безопасный минимум, чтобы пользователь мог уменьшать окно вручную
        self.setMinimumSize(900, 600) 

        # 2. Получаем геометрию экрана
        screen_geom = self.screen().availableGeometry()

        # 3. Желаемые стандартные размеры
        target_width = 1200
        target_height = 850

        # 4. Лимиты (90% от ширины и высоты экрана)
        limit_width = int(screen_geom.width() * 0.90)
        limit_height = int(screen_geom.height() * 0.90)

        # 5. Итоговый размер: желаемый, но не превышающий лимиты
        final_width = min(target_width, limit_width)
        final_height = min(target_height, limit_height)

        self.resize(final_width, final_height)

        # 6. Центрирование окна
        self.move(
            screen_geom.center().x() - final_width // 2,
            screen_geom.center().y() - final_height // 2
        )

        # --- Состояния ---
        self.initial_data = initial_data
        self.is_from_table = is_from_table
        
        self.original_data_as_rows = []
        self.preview_data_as_rows = []
        
        self.multi_file_mode = multi_file_mode
        self.apply_rules_to_all = False
        self.parser = StandaloneFileParser()

        self.parsing_rules = []
        self.final_glossary = []
        self.mapping_combos = []

        self.init_ui()
        self.analyze_and_start()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        self.stacked_widget = QStackedWidget()
        main_layout.addWidget(self.stacked_widget)
        self.parser_widget = self._create_parser_widget()
        self.stacked_widget.addWidget(self.parser_widget)



    def analyze_and_start(self):
        """
        Анализирует входные данные и решает, как их обрабатывать.
        Использует устойчивый парсер для JSON.
        """
        if self.is_from_table:
            self.original_data_as_rows = self.initial_data
            self._prepare_for_parsing()
            return
            
        file_content = self.initial_data
        
        # Шаг 1: Используем новый устойчивый парсер
        parsed_data, error_info = self._resilient_json_parse(file_content)

        # Шаг 2: Анализируем результат парсера
        if parsed_data is not None:
            # У нас есть валидные или частично восстановленные JSON-данные.
            is_standard = False
            # Проверяем, является ли формат стандартным для программы
            if isinstance(parsed_data, list) and parsed_data and isinstance(parsed_data[0], dict):
                first_item_keys = parsed_data[0].keys()
                if 'original' in first_item_keys and 'rus' in first_item_keys:
                    is_standard = True
            elif isinstance(parsed_data, dict) and parsed_data:
                first_key = next(iter(parsed_data), None)
                if first_key and isinstance(parsed_data[first_key], dict):
                    first_item_keys = parsed_data[first_key].keys()
                    if 'rus' in first_item_keys:
                        is_standard = True

            if is_standard:
                self._handle_native_json(parsed_data)
                return

            # Если JSON валидный, но не стандартный, отправляем в Мастер
            if isinstance(parsed_data, list):
                self.original_data_as_rows = [[json.dumps(item, ensure_ascii=False)] for item in parsed_data if isinstance(item, dict)]
            elif isinstance(parsed_data, dict):
                 # Преобразуем словарь в список списков для мастера
                temp_data = []
                for k, v in parsed_data.items():
                    # Пытаемся красиво представить вложенный объект
                    if isinstance(v, dict):
                        # "original" = {"rus": "…", "note": "…"}
                        # Становится строкой: 'original={"rus": "…", "note": "…"}'
                        temp_data.append([f"{k}={json.dumps(v, ensure_ascii=False)}"])
                    else:
                        temp_data.append([f"{k}={v}"])
                self.original_data_as_rows = temp_data

            self._prepare_for_parsing()
        else:
            # Шаг 3: Если даже устойчивый парсер не справился, считаем файл обычным текстом
            self.original_data_as_rows = [[line] for line in file_content.splitlines() if line.strip()]
            self._prepare_for_parsing()
    
    def _resilient_json_parse(self, content_str):
        """
        Пытается разобрать JSON. Если не получается, показывает ошибку и
        предлагает использовать мощный ремонтный парсер.
        Возвращает (data, error_info) или (None, None) при полной неудаче.
        """
        content_str = content_str.strip()
        try:
            # Попытка №1: прочитать "как есть"
            return json.loads(content_str), None
        except json.JSONDecodeError as e:
            # Ошибка! Показываем пользователю детальную информацию.
            msg_box = QMessageBox(self)
            msg_box.setIcon(QMessageBox.Icon.Warning)
            msg_box.setWindowTitle("Ошибка в формате JSON")
            msg_box.setText(
                f"Не удалось прочитать файл как стандартный JSON.\n\n"
                f"<b>Детали ошибки:</b> {e.msg}\n"
                f"<b>Строка:</b> {e.lineno}, <b>Символ:</b> {e.colno}"
            )
            msg_box.setInformativeText(
                "Что вы хотите сделать?\n\n"
                "• <b>Попробовать починить</b>: Использовать мощный алгоритм для поиска и восстановления всех корректных записей.\n"
                "• <b>Открыть как текст</b>: Открыть файл в мастере для ручной настройки правил разбора."
            )
            
            # Изменим текст кнопки для ясности
            repair_button = msg_box.addButton("Попробовать починить", QMessageBox.ButtonRole.AcceptRole)
            text_button = msg_box.addButton("Открыть как текст", QMessageBox.ButtonRole.DestructiveRole)
            msg_box.addButton(QMessageBox.StandardButton.Cancel)
            
            msg_box.exec()
            
            clicked_button = msg_box.clickedButton()
            if clicked_button == repair_button:
                # --- ИСПОЛЬЗУЕМ "ТЯЖЕЛУЮ АРТИЛЛЕРИЮ" ---
                repaired_json_str = repair_json_string(content_str)
                
                if repaired_json_str:
                    try:
                        # Преобразуем отремонтированную строку обратно в объект Python
                        salvaged_data = json.loads(repaired_json_str)
                        
                        QMessageBox.information(self, "Ремонт завершен",
                            f"Удалось восстановить и импортировать {len(salvaged_data)} записей.\n"
                            "Файл был поврежден, но часть данных спасена."
                        )
                        # Возвращаем спасенные данные
                        return salvaged_data, None
                    except json.JSONDecodeError:
                        # Этого не должно случиться, если repair_json_string работает правильно,
                        # но лучше перестраховаться.
                        QMessageBox.warning(self, "Ошибка после ремонта",
                            "Не удалось обработать восстановленные данные. Файл будет открыт как текст."
                        )
                        return None, None
                else:
                    # Ремонт не дал результатов
                    QMessageBox.warning(self, "Ремонт не удался",
                        "Не удалось найти ни одной корректной записи в файле. Открываем как обычный текст."
                    )
                    return None, None

            elif clicked_button == text_button:
                # Пользователь выбрал открыть как текст
                return None, None
                
            else: # Пользователь нажал "Отмена"
                self.reject() # Закрываем диалог
                return None, "cancelled"
                
        return None, None
    
    def _prepare_for_parsing(self):
        self.preview_data_as_rows = self.original_data_as_rows[:50]
        self.stacked_widget.setCurrentWidget(self.parser_widget)
        self._update_preview()

    def _handle_native_json(self, data):
        self.final_glossary.clear()
        if isinstance(data, list):
            self.final_glossary = data
        else:
            for term, term_data in data.items():
                self.final_glossary.append({
                    "original": term,
                    "rus": term_data.get("rus", ""),
                    "note": term_data.get("note", "")
                })
        info_page = QWidget()
        layout = QVBoxLayout(info_page)
        label = QLabel("Обнаружен полный JSON-формат программы.\nДанные будут импортированы напрямую, без настройки.")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview = QTextEdit()
        preview.setReadOnly(True)
        preview.setText(json.dumps(data, indent=2, ensure_ascii=False))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Импортировать")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(label)
        layout.addWidget(preview)
        layout.addWidget(buttons)
        self.stacked_widget.insertWidget(0, info_page)
        self.stacked_widget.setCurrentIndex(0)

    def _create_parser_widget(self):
        widget = QWidget()
        main_layout = QVBoxLayout(widget)
        controls_group = QGroupBox("Шаг 1: Инструменты для обработки строк и столбцов")
        controls_layout = QHBoxLayout(controls_group)

        split_group = QGroupBox("Разделить столбец по разделителю")
        split_layout = QGridLayout(split_group)
        self.split_col_spinbox = NoScrollSpinBox(); self.split_col_spinbox.setMinimum(1)
        self.split_delimiter_combo = NoScrollComboBox()
        self.split_delimiter_combo.addItems(['=', '-', '@@', '@', 'Табуляция', 'Другой…'])
        self.split_custom_delimiter_edit = QLineEdit(); self.split_custom_delimiter_edit.setVisible(False)
        self.split_delimiter_combo.currentTextChanged.connect(
            lambda text: self.split_custom_delimiter_edit.setVisible(text == 'Другой…'))
        split_button = QPushButton("Применить разделение"); split_button.clicked.connect(self._add_split_rule)
        split_layout.addWidget(QLabel("Столбец:"), 0, 0); split_layout.addWidget(self.split_col_spinbox, 0, 1)
        split_layout.addWidget(QLabel("Разделитель:"), 1, 0); split_layout.addWidget(self.split_delimiter_combo, 1, 1)
        split_layout.addWidget(self.split_custom_delimiter_edit, 2, 1)
        split_layout.addWidget(split_button, 3, 0, 1, 2)
        controls_layout.addWidget(split_group)
        
        extract_group = QGroupBox("Извлечь текст по окантовке")
        extract_layout = QGridLayout(extract_group)
        self.extract_col_spinbox = NoScrollSpinBox(); self.extract_col_spinbox.setMinimum(1)
        self.extract_start_edit = QLineEdit("("); self.extract_end_edit = QLineEdit(")")
        self.extract_direction_combo = NoScrollComboBox(); self.extract_direction_combo.addItems(["в новый столбец справа", "в новый столбец слева"])
        extract_button = QPushButton("Применить извлечение"); extract_button.clicked.connect(self._add_extract_rule)
        extract_layout.addWidget(QLabel("Столбец:"), 0, 0); extract_layout.addWidget(self.extract_col_spinbox, 0, 1)
        extract_layout.addWidget(QLabel("Начало:"), 1, 0); extract_layout.addWidget(self.extract_start_edit, 1, 1)
        extract_layout.addWidget(QLabel("Конец:"), 2, 0); extract_layout.addWidget(self.extract_end_edit, 2, 1)
        extract_layout.addWidget(QLabel("Перенести:"), 3, 0); extract_layout.addWidget(self.extract_direction_combo, 3, 1)
        extract_layout.addWidget(extract_button, 4, 0, 1, 2)
        controls_layout.addWidget(extract_group)

        insert_group = QGroupBox("Вставить пустой столбец")
        insert_layout = QGridLayout(insert_group)
        self.insert_col_spinbox = NoScrollSpinBox(); self.insert_col_spinbox.setMinimum(1)
        insert_button = QPushButton("Вставить"); insert_button.clicked.connect(self._add_insert_empty_rule)
        insert_layout.addWidget(QLabel("Перед столбцом:"), 0, 0)
        insert_layout.addWidget(self.insert_col_spinbox, 0, 1)
        insert_layout.addWidget(insert_button, 1, 0, 1, 2)
        controls_layout.addWidget(insert_group)
        
        reset_button = QPushButton("Сбросить все правила"); reset_button.clicked.connect(self._reset_rules)
        controls_layout.addWidget(reset_button, alignment=Qt.AlignmentFlag.AlignBottom)
        main_layout.addWidget(controls_group)

        preview_group = QGroupBox("Шаг 2: Предпросмотр результата")
        preview_layout = QVBoxLayout(preview_group)
        self.preview_table = QTableWidget(); self.preview_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        preview_layout.addWidget(self.preview_table, 1)
        main_layout.addWidget(preview_group, 1)

        self.mapping_group = QGroupBox("Шаг 3: Назначение столбцов (какой столбец чем является)")
        self.mapping_layout = QHBoxLayout(self.mapping_group)
        main_layout.addWidget(self.mapping_group)
        
        buttons = QDialogButtonBox()
        import_button = buttons.addButton("Импортировать", QDialogButtonBox.ButtonRole.AcceptRole)
        cancel_button = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        import_button.clicked.connect(self.process_and_accept)
        cancel_button.clicked.connect(self.reject)
        
        if self.multi_file_mode:
            self.apply_all_button = buttons.addButton("Применить ко всем и настроить", QDialogButtonBox.ButtonRole.ActionRole)
            self.apply_all_button.clicked.connect(self._process_and_apply_to_all)

        main_layout.addWidget(buttons)
        return widget

    def _add_split_rule(self):
        source_col = self.split_col_spinbox.value() - 1
        delimiter_text = self.split_delimiter_combo.currentText()
        delimiter = self.split_custom_delimiter_edit.text() if delimiter_text == 'Другой…' else ('\t' if delimiter_text == 'Табуляция' else delimiter_text)
        if not delimiter:
            QMessageBox.warning(self, "Ошибка", "Необходимо указать разделитель.")
            return
        self.parsing_rules.append({'type': 'split', 'source_col': source_col, 'delimiter': delimiter})
        self._update_preview()

    def _add_extract_rule(self):
        source_col = self.extract_col_spinbox.value() - 1
        start, end = self.extract_start_edit.text(), self.extract_end_edit.text()
        direction = 'right' if 'справа' in self.extract_direction_combo.currentText() else 'left'
        if not start or not end:
            QMessageBox.warning(self, "Ошибка", "Необходимо указать начало и конец окантовки.")
            return
        self.parsing_rules.append({'type': 'extract', 'source_col': source_col, 'start': start, 'end': end, 'direction': direction})
        self._update_preview()
        
    def _add_insert_empty_rule(self):
        target_col = self.insert_col_spinbox.value() - 1
        self.parsing_rules.append({'type': 'insert_empty', 'target_col': target_col})
        self._update_preview()
        
    def _reset_rules(self):
        self.parsing_rules.clear()
        self._update_preview()

    def _update_preview(self):
        parsed_data = self.parser._run_parsing_pipeline(self.preview_data_as_rows, self.parsing_rules)
        self._populate_table(self.preview_table, parsed_data)
        num_columns = self.preview_table.columnCount() if self.preview_table.columnCount() > 0 else 1
        self.split_col_spinbox.setMaximum(num_columns)
        self.extract_col_spinbox.setMaximum(num_columns)
        self.insert_col_spinbox.setMaximum(num_columns + 1)
        self._update_mapping_controls()

    def _populate_table(self, table, data):
        table.clear()
        if not data:
            table.setColumnCount(0)
            return
        num_columns = max(len(row) for row in data) if data else 0
        table.setColumnCount(num_columns)
        table.setHorizontalHeaderLabels([f"Столбец {i+1}" for i in range(num_columns)])
        table.setRowCount(len(data))
        for row_idx, row_data in enumerate(data):
            for col_idx, cell_data in enumerate(row_data):
                table.setItem(row_idx, col_idx, QTableWidgetItem(cell_data))
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

    def _update_mapping_controls(self):
        while self.mapping_layout.count():
            child = self.mapping_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self.mapping_combos.clear()

        num_columns = self.preview_table.columnCount()
        if num_columns == 0:
            self.mapping_group.setVisible(False)
            return
        
        self.mapping_group.setVisible(True)
        options = ["Игнорировать", "Оригинал", "Перевод", "Примечание"]
        defaults = ["Оригинал", "Перевод", "Примечание"]

        for i in range(num_columns):
            col_widget = QWidget()
            col_layout = QVBoxLayout(col_widget)
            col_layout.setContentsMargins(2,2,2,2)
            
            label = QLabel(f"<b>Столбец {i+1}</b>")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            
            combo = NoScrollComboBox()
            combo.addItems(options)
            
            if i < len(defaults):
                index = combo.findText(defaults[i])
                if index != -1:
                    combo.setCurrentIndex(index)

            self.mapping_combos.append(combo)
            col_layout.addWidget(label)
            col_layout.addWidget(combo)
            self.mapping_layout.addWidget(col_widget)

    def get_parsing_rules(self):
        return self.parsing_rules

    def get_column_mapping(self):
        return [combo.currentText() for combo in self.mapping_combos]

    def _process_and_apply_to_all(self):
        self.apply_rules_to_all = True
        self.process_and_accept()

    def process_and_accept(self):
        mapping = self.get_column_mapping()
        
        unique_mappings = {m for m in mapping if m != "Игнорировать"}
        if len(unique_mappings) != len([m for m in mapping if m != "Игнорировать"]):
            QMessageBox.warning(self, "Ошибка назначения", "Одно и то же назначение (например, 'Оригинал') выбрано для нескольких столбцов. Пожалуйста, исправьте.")
            self.apply_rules_to_all = False
            return
        
        self.final_glossary = self.parser.run_on_pre_split_data(self.original_data_as_rows, self.parsing_rules, mapping)
        
        if not self.final_glossary:
            if not self.apply_rules_to_all:
                QMessageBox.warning(self, "Нет данных", "Не удалось извлечь ни одной записи. Проверьте правила и назначение столбцов.")
            self.accept()
            return
            
        self.accept()
    
    def get_glossary(self):
        return self.final_glossary



class MultiImportManagerDialog(QDialog):
    """
    Новое диалоговое окно для управления импортом из нескольких файлов.
    Версия 3.0: Автоматически обрабатывает стандартные JSON-файлы.
    """
    def __init__(self, paths_to_configure, pre_processed_data, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Диспетчер импорта нескольких файлов (v3.0)")
        self.setMinimumSize(800, 400)

        # --- ИЗМЕНЕНИЕ: Принимаем данные в новом формате ---
        self.paths_to_configure = set(paths_to_configure)
        # В self.processed_data будут храниться все обработанные данные (и начальные, и настроенные вручную)
        self.processed_data = pre_processed_data.copy()
        
        # Общий список путей для отображения в таблице
        self.paths = paths_to_configure + list(pre_processed_data.keys())
        
        self.parser = StandaloneFileParser()

        main_layout = QVBoxLayout(self)
        info_label = QLabel(
            "Стандартные файлы импортированы автоматически.\n"
            "Для остальных файлов можно настроить свои правила импорта."
        )
        main_layout.addWidget(info_label)

        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Файл", "Статус", "Действия"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        main_layout.addWidget(self.table)

        self.button_box = QDialogButtonBox()
        self.finish_button = self.button_box.addButton("Завершить импорт", QDialogButtonBox.ButtonRole.AcceptRole)
        cancel_button = self.button_box.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.finish_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)
        main_layout.addWidget(self.button_box)

        self.populate_table()
        self.update_finish_button_state()

    def populate_table(self):
        self.table.setRowCount(len(self.paths))
        for i, path in enumerate(self.paths):
            file_name = QtCore.QFileInfo(path).fileName()
            self.table.setItem(i, 0, QTableWidgetItem(file_name))

            # --- ИЗМЕНЕНИЕ: Разная логика для стандартных и не-стандартных файлов ---
            if path in self.paths_to_configure:
                # Этот файл нужно настроить вручную
                status_item = QTableWidgetItem("Ожидает настройки")
                status_item.setForeground(QColor("#E67E22")) # Оранжевый
                self.table.setItem(i, 1, status_item)
                
                buttons_widget = QWidget()
                buttons_layout = QHBoxLayout(buttons_widget)
                buttons_layout.setContentsMargins(4, 2, 4, 2)
                configure_button = QPushButton("Настроить…")
                # Связываем кнопку с путем и номером строки
                configure_button.clicked.connect(lambda ch, p=path, r=i: self.configure_file(p, r))
                buttons_layout.addWidget(configure_button)
                self.table.setCellWidget(i, 2, buttons_widget)
            else:
                # Этот файл уже был обработан
                result_count = len(self.processed_data.get(path, []))
                status_item = QTableWidgetItem(f"Готово ({result_count} записей)")
                status_item.setForeground(QColor("#2ECC71")) # Зеленый
                self.table.setItem(i, 1, status_item)
                
                # Ячейка действий пуста, т.к. настраивать нечего
                self.table.setCellWidget(i, 2, QWidget())

    def configure_file(self, path, row):
        try:
            with open(path, 'r', encoding='utf-8') as f: content = f.read()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка чтения", f"Не удалось прочитать файл:\n{path}\n\n{e}")
            self.update_status(row, "Ошибка чтения", QColor("red"))
            self.paths_to_configure.discard(path)
            self.update_finish_button_state()
            return

        if not content.strip():
            self.processed_data[path] = []
            self.update_status(row, "Файл пуст", QColor("grey"))
            self.paths_to_configure.discard(path)
            self.update_finish_button_state()
            return
            
        wizard = ImporterWizardDialog(content, self, multi_file_mode=True)
        if wizard.exec() == QDialog.DialogCode.Accepted:
            result = wizard.get_glossary()
            self.processed_data[path] = result
            self.update_status(row, f"Настроен ({len(result)} записей)", QColor("#2ECC71"))
            self.paths_to_configure.discard(path)
            
            if wizard.apply_rules_to_all:
                parsing_rules = wizard.get_parsing_rules()
                column_mapping = wizard.get_column_mapping()
                self._apply_rules_to_pending_files(parsing_rules, column_mapping, exclude_path=path)
        
        self.update_finish_button_state()

    def _apply_rules_to_pending_files(self, parsing_rules, column_mapping, exclude_path):
        """Проходит по всем файлам, которые все еще ожидают настройки, и применяет к ним правила."""
        paths_to_process = self.paths_to_configure.copy()
        
        for path in paths_to_process:
            if path == exclude_path:
                continue
                
            row_index = self.paths.index(path)

            try:
                with open(path, 'r', encoding='utf-8') as f: content = f.read()
                if not content.strip():
                    self.processed_data[path] = []
                    self.update_status(row_index, "Файл пуст (авто)", QColor("grey"))
                else:
                    result = self.parser.run(content, parsing_rules, column_mapping)
                    self.processed_data[path] = result
                    self.update_status(row_index, f"Настроен ({len(result)} записей, авто)", QColor("#3498DB"))
            
            except Exception:
                self.update_status(row_index, "Ошибка авто-применения", QColor("red"))
            
            self.paths_to_configure.discard(path)

    def update_status(self, row, text, color):
        status_item = QTableWidgetItem(text)
        status_item.setForeground(color)
        self.table.setItem(row, 1, status_item)
        widget = self.table.cellWidget(row, 2)
        if widget:
            widget.setEnabled(False)

    def update_finish_button_state(self):
        all_configured = not self.paths_to_configure
        self.finish_button.setEnabled(all_configured)

    def get_all_imported_entries(self):
        all_entries = []
        for path in self.paths:
            if path in self.processed_data:
                all_entries.extend(self.processed_data[path])
        return all_entries, len(self.processed_data)


class StandaloneFileParser:
    """
    Служебный класс, который инкапсулирует всю логику парсинга.
    Он не имеет интерфейса и может быть использован в любом месте программы.
    Версия 2.0: Исправлена ошибка с обработкой данных из таблицы.
    """
    def run_on_pre_split_data(self, pre_split_rows, parsing_rules, column_mapping):
        """
        Основной метод для данных, которые уже разделены на строки и столбцы.
        """
        parsed_rows = self._run_parsing_pipeline(pre_split_rows, parsing_rules)
        final_glossary = self._apply_column_mapping(parsed_rows, column_mapping)
        return final_glossary

    def run(self, file_content, parsing_rules, column_mapping):
        """
        Основной метод для сырого текстового контента из файла.
        """
        if not file_content:
            return []
        # Преобразуем текст в структуру "список списков"
        initial_rows = [[line] for line in file_content.splitlines() if line.strip()]
        return self.run_on_pre_split_data(initial_rows, parsing_rules, column_mapping)

    def _run_parsing_pipeline(self, initial_rows, parsing_rules):
        import re
        processed_data = []
        for row in initial_rows:
            # <<< ИСПРАВЛЕНИЕ ЗДЕСЬ >>>
            # Создаем КОПИЮ строки для обработки, а не вкладываем ее в еще один список.
            row_data = row[:] 
            
            for rule in parsing_rules:
                new_row_data = row_data[:]
                source_col = rule.get('source_col', -1)
                
                if rule['type'] == 'insert_empty':
                    target_col = rule.get('target_col', 0)
                    new_row_data.insert(target_col, "")
                
                elif source_col >= len(row_data) or source_col < 0:
                    continue

                elif rule['type'] == 'split':
                    # Добавляем проверку, что ячейка - это строка
                    if isinstance(row_data[source_col], str):
                        parts = row_data[source_col].split(rule['delimiter'])
                        new_row_data[source_col] = parts[0]
                        for i, part in enumerate(parts[1:]):
                            new_row_data.insert(source_col + 1 + i, part)

                elif rule['type'] == 'extract':
                    text = row_data[source_col]
                    # Добавляем проверку, что ячейка - это строка
                    if isinstance(text, str):
                        start_esc, end_esc = re.escape(rule['start']), re.escape(rule['end'])
                        match = re.search(f"{start_esc}(.*?){end_esc}", text)
                        if match:
                            extracted = match.group(1)
                            cleaned = text[:match.start()] + text[match.end():]
                            new_row_data[source_col] = cleaned
                            insert_pos = source_col + 1 if rule['direction'] == 'right' else source_col
                            new_row_data.insert(insert_pos, extracted)
                
                row_data = new_row_data

            # Теперь `cell` гарантированно будет строкой или приводимым к ней типом
            final_row = [str(cell).strip() for cell in row_data]
            processed_data.append(final_row)
        return processed_data

    def _apply_column_mapping(self, parsed_rows, column_mapping):
        final_glossary = []
        try:
            original_col = column_mapping.index("Оригинал") if "Оригинал" in column_mapping else -1
            translation_col = column_mapping.index("Перевод") if "Перевод" in column_mapping else -1
            note_col = column_mapping.index("Примечание") if "Примечание" in column_mapping else -1
        except ValueError:
            return []

        for row_data in parsed_rows:
            def get_data(col_index):
                if col_index != -1 and col_index < len(row_data):
                    return row_data[col_index]
                return ""

            term = get_data(original_col)
            rus = get_data(translation_col)
            note = get_data(note_col)
            
            if term or rus or note:
                final_glossary.append({
                    "original": term, "rus": rus, "note": note
                })
        
        return final_glossary

