# -*- coding: utf-8 -*-

# ---------------------------------------------------------------------------
# glossary.py (Professional Interactive Version 8 - Bug Fix)
# ---------------------------------------------------------------------------

import sys
import re
import json
import os
import time
import copy
from collections import defaultdict, Counter
import functools
import itertools
import sqlite3 # <-- Новый импорт
import uuid    # <-- Новый импорт
# --- Импорты из PyQt6 ---
from PyQt6 import QtWidgets, QtGui, QtCore
from PyQt6.QtCore import Qt, pyqtSlot, QTimer
from PyQt6.QtGui import QFont, QColor
from PyQt6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QPushButton, QDialogButtonBox, QLabel,
    QTextEdit, QTableWidget, QTableWidgetItem, QHeaderView, QLineEdit,
    QFileDialog, QMessageBox, QWidget, QHBoxLayout, QComboBox,
    QSplitter, QStyle, QGroupBox, QAbstractItemView, QGridLayout
)

# --- Импорты из модулей проекта ---

# Диалоги, вынесенные в отдельные файлы
from .glossary_dialogs.ai_correction import CorrectionSessionDialog
from .glossary_dialogs.core_term_dialog import CoreTermAnalyzerDialog
from .glossary_dialogs.versioning import TermVersioningDialog
from .glossary_dialogs.residue_analyzer import ResidueAnalyzerDialog
from .glossary_dialogs.conflict_resolvers import (
    DirectConflictResolverDialog,
    ReverseConflictResolverDialog,
    ComplexOverlapResolverDialog
)
from .glossary_dialogs.group_analyzer import GroupAnalysisDialog
from .glossary_dialogs.term_frequency_analyzer import TermFrequencyAnalyzerDialog
from .glossary_dialogs.import_master import (
    ImporterWizardDialog,
    MultiImportManagerDialog
)
# Кастомные виджеты
from .glossary_dialogs.custom_widgets import ExpandingTextEditDelegate

# Утилиты и API
from ...api import config as api_config
from ...utils.settings import SettingsManager
from ...utils.language_tools import (
    LanguageDetector, ChineseTextProcessor, GlossaryLogic
)


# --- Универсальный импорт Pymorphy с проверкой версии ---
PYMORPHY_AVAILABLE = False
morph_analyzer = None
PYMORPHY_RECOMMENDATION = ""

try:
    import pymorphy3
    morph_analyzer = pymorphy3.MorphAnalyzer(lang='ru')
    PYMORPHY_AVAILABLE = True
    print("INFO: Используется библиотека pymorphy3.")
except Exception:
    try:
        import pymorphy2
        morph_analyzer = pymorphy2.MorphAnalyzer()
        PYMORPHY_AVAILABLE = True
        print("INFO: Используется библиотека pymorphy2 (рекомендуется обновиться до pymorphy3).")
    except Exception:
        PYMORPHY_AVAILABLE = False
        if sys.version_info >= (3, 7):
            PYMORPHY_RECOMMENDATION = (
                "<b>Внимание:</b> Pymorphy не найдена. Функционал ограничен.<br>"
                "Для вашей версии Python рекомендуется установить <b>pymorphy3</b>:<br>"
                "<code>pip install pymorphy3 pymorphy3-dicts-ru</code>"
            )
        else:
            PYMORPHY_RECOMMENDATION = (
                "<b>Внимание:</b> Pymorphy не найдена. Функционал ограничен.<br>"
                "Для вашей версии Python рекомендуется установить <b>pymorphy2</b>:<br>"
                "<code>pip install pymorphy2</code>"
            )


class GlossaryStartupDialog(QDialog):
    """Диалог выбора способа запуска Менеджера Глоссариев."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Запуск Менеджера Глоссариев")
        self.setMinimumWidth(400)
        self.project_path = None
        
        app = QtWidgets.QApplication.instance()
        self.settings_manager = app.get_settings_manager()

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(QLabel("<h3>Выберите режим работы:</h3>"))

        # Кнопка 1: Из истории
        history_btn = QPushButton("📂 Открыть глоссарий проекта (из истории)")
        history_btn.setMinimumHeight(40)
        history_btn.clicked.connect(self.load_from_history)
        layout.addWidget(history_btn)

        # Кнопка 2: Пустой/Вручную
        manual_btn = QPushButton("📝 Новый / Открыть файл вручную")
        manual_btn.setMinimumHeight(40)
        manual_btn.clicked.connect(self.start_empty)
        layout.addWidget(manual_btn)
        
        layout.addStretch()

        cancel_btn = QPushButton("Отмена")
        cancel_btn.clicked.connect(self.reject)
        layout.addWidget(cancel_btn, 0, Qt.AlignmentFlag.AlignRight)

    def load_from_history(self):
        history = self.settings_manager.load_project_history()
        if not history:
            QMessageBox.information(self, "История пуста", "Нет сохраненных проектов.")
            return
            
        from gemini_translator.ui.dialogs.misc import ProjectHistoryDialog
        dialog = ProjectHistoryDialog(history, self.settings_manager, self)
        
        if dialog.exec():
            project = dialog.get_selected_project()
            if project:
                path = project.get("output_folder")
                if path and os.path.isdir(path):
                    self.project_path = path
                    self.accept()
                else:
                    QMessageBox.warning(self, "Ошибка", f"Папка проекта не найдена:\n{path}")

    def start_empty(self):
        self.project_path = None # None означает "без проекта"
        self.accept()


class GlossaryFilterDialog(QDialog):
    """Диалог настройки фильтрации/поиска (Расширенный)."""
    def __init__(self, current_text="", current_regex=False, current_cols=None, 
                 whole_word=False, match_all=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Поиск и фильтрация")
        self.resize(500, 380) # Чуть выше, так как опций больше
        
        if current_cols is None:
            current_cols = {'original', 'rus', 'note'}
            
        layout = QVBoxLayout(self)
        
        splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(splitter)
        
        # --- Верхняя часть: Ввод ---
        input_widget = QWidget()
        input_layout = QVBoxLayout(input_widget)
        input_layout.setContentsMargins(0, 0, 0, 0)
        input_layout.addWidget(QLabel("Текст для поиска (разбивается по пробелам):"))
        
        self.text_input = QtWidgets.QPlainTextEdit()
        self.text_input.setPlaceholderText("Введите слова через пробел...")
        self.text_input.setPlainText(current_text)
        input_layout.addWidget(self.text_input)
        splitter.addWidget(input_widget)
        
        # --- Нижняя часть: Настройки ---
        options_widget = QWidget()
        options_layout = QVBoxLayout(options_widget)
        options_layout.setContentsMargins(0, 10, 0, 0)
        
        # 1. Группа "Умный поиск"
        self.smart_group = QGroupBox("Параметры слов")
        smart_layout = QVBoxLayout(self.smart_group)
        
        self.cb_whole_word = QtWidgets.QCheckBox("Только слово целиком")
        self.cb_whole_word.setChecked(whole_word)
        self.cb_whole_word.setToolTip("Искать точное совпадение слова (границы слова)")
        
        # Логика объединения
        logic_layout = QHBoxLayout()
        self.rb_any = QtWidgets.QRadioButton("ИЛИ: Найти любое из слов")
        self.rb_all = QtWidgets.QRadioButton("И: Найти все слова (в любых полях)")
        
        if match_all:
            self.rb_all.setChecked(True)
        else:
            self.rb_any.setChecked(True)
            
        logic_layout.addWidget(self.rb_any)
        logic_layout.addWidget(self.rb_all)
        logic_layout.addStretch()
        
        smart_layout.addWidget(self.cb_whole_word)
        smart_layout.addLayout(logic_layout)
        options_layout.addWidget(self.smart_group)

        # 2. Где искать
        col_layout = QHBoxLayout()
        col_layout.addWidget(QLabel("Поля:"))
        self.cb_orig = QtWidgets.QCheckBox("Оригинал")
        self.cb_orig.setChecked('original' in current_cols)
        self.cb_trans = QtWidgets.QCheckBox("Перевод")
        self.cb_trans.setChecked('rus' in current_cols)
        self.cb_note = QtWidgets.QCheckBox("Примечание")
        self.cb_note.setChecked('note' in current_cols)
        
        col_layout.addWidget(self.cb_orig)
        col_layout.addWidget(self.cb_trans)
        col_layout.addWidget(self.cb_note)
        col_layout.addStretch()
        options_layout.addLayout(col_layout)

        # 3. Режим Regex (перекрывает умный поиск)
        self.check_regex = QtWidgets.QCheckBox("Режим сырого Regex (отключает параметры слов)")
        self.check_regex.setStyleSheet("color: grey;")
        self.check_regex.setChecked(current_regex)
        self.check_regex.toggled.connect(self._on_regex_toggled)
        options_layout.addWidget(self.check_regex)
        
        splitter.addWidget(options_widget)
        
        # Кнопки
        btn_layout = QHBoxLayout()
        reset_btn = QPushButton("Сбросить")
        reset_btn.clicked.connect(self.reset_filter)
        
        cancel_btn = QPushButton("Отмена")
        cancel_btn.clicked.connect(self.reject)
        
        apply_btn = QPushButton("Применить")
        apply_btn.setDefault(True)
        apply_btn.clicked.connect(self.accept)
        
        btn_layout.addWidget(reset_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(apply_btn)
        
        layout.addLayout(btn_layout)
        splitter.setSizes([100, 180])
        
        self._on_regex_toggled(current_regex) # Init state

    def _on_regex_toggled(self, checked):
        self.smart_group.setEnabled(not checked)
        if checked:
            self.smart_group.setTitle("Параметры слов (Отключено в режиме Regex)")
        else:
            self.smart_group.setTitle("Параметры слов")

    def reset_filter(self):
        self.text_input.clear()
        self.check_regex.setChecked(False)
        self.cb_whole_word.setChecked(False)
        self.rb_any.setChecked(True)
        self.cb_orig.setChecked(True)
        self.cb_trans.setChecked(True)
        self.cb_note.setChecked(True)
        self.accept()

    def get_filter_state(self):
        cols = set()
        if self.cb_orig.isChecked(): cols.add('original')
        if self.cb_trans.isChecked(): cols.add('rus')
        if self.cb_note.isChecked(): cols.add('note')
        
        return {
            'text': self.text_input.toPlainText().strip(),
            'is_regex': self.check_regex.isChecked(),
            'columns': cols,
            'whole_word': self.cb_whole_word.isChecked(),
            'match_all': self.rb_all.isChecked()
        }


class GlossarySortDialog(QDialog):
    """Диалог расширенной настройки сортировки."""
    def __init__(self, current_col_idx, current_order, current_criterion, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройка сортировки")
        self.resize(400, 320)
        self.result_state = None
        layout = QVBoxLayout(self)
        
        col_group = QGroupBox("1. Что сортируем?")
        col_layout = QVBoxLayout(col_group)
        self.col_combo = QComboBox()
        # Добавлена "Дата добавления" (индекс 3)
        self.col_combo.addItems(["Оригинальный термин", "Перевод", "Примечание", "Дата создания (скрытое поле)"])
        self.col_combo.setCurrentIndex(current_col_idx if current_col_idx in [0, 1, 2, 3] else 0)
        col_layout.addWidget(self.col_combo)
        layout.addWidget(col_group)
        
        crit_group = QGroupBox("2. Критерий")
        crit_layout = QVBoxLayout(crit_group)
        self.radio_value = QtWidgets.QRadioButton("По значению (алфавит/время)")
        self.radio_length = QtWidgets.QRadioButton("По длине текста")
        if current_criterion == 'length': self.radio_length.setChecked(True)
        else: self.radio_value.setChecked(True)
        crit_layout.addWidget(self.radio_value); crit_layout.addWidget(self.radio_length)
        layout.addWidget(crit_group)
        
        dir_group = QGroupBox("3. Направление")
        dir_layout = QVBoxLayout(dir_group)
        self.radio_asc = QtWidgets.QRadioButton("По возрастанию (А-Я, Старые -> Новые)")
        self.radio_desc = QtWidgets.QRadioButton("По убыванию (Я-А, Новые -> Старые)")
        if current_order == Qt.SortOrder.AscendingOrder: self.radio_asc.setChecked(True)
        else: self.radio_desc.setChecked(True)
        dir_layout.addWidget(self.radio_asc); dir_layout.addWidget(self.radio_desc)
        layout.addWidget(dir_group)
        
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        
        # Устанавливаем русский текст для кнопок
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Применить")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        
        # Подключаем сигналы
        btns.accepted.connect(self.accept_selection)
        btns.rejected.connect(self.reject)
        
        layout.addWidget(btns)

    def accept_selection(self):
        col_idx = self.col_combo.currentIndex()
        criterion = 'length' if self.radio_length.isChecked() else 'value'
        # Если выбрана дата, принудительно ставим критерий 'value'
        if col_idx == 3: criterion = 'value'
        order = Qt.SortOrder.AscendingOrder if self.radio_asc.isChecked() else Qt.SortOrder.DescendingOrder
        self.result_state = (col_idx, order, criterion)
        self.accept()
        
    def get_state(self):
        return self.result_state

# ---------------------------------------------------------------------------
# --- Основное окно приложения
# ---------------------------------------------------------------------------

class MainWindow(QDialog):
    ConflictTypeRole = Qt.ItemDataRole.UserRole + 1
    DB_ID_ROLE = Qt.ItemDataRole.UserRole + 2 

    def __init__(self, parent=None, mode='standalone', project_path=None):
        super().__init__(parent)
        
        app = QtWidgets.QApplication.instance()
        self.version = ""
        if app and app.global_version:
            self.version = app.global_version
        self.setWindowTitle(f"Менеджер Глоссариев {self.version}")
        # --- Геометрия окна ---
        available_geometry = self.screen().availableGeometry()
        
        height = min(int(available_geometry.height() * 0.75), 650)
        width = min(int(available_geometry.width() * 0.65), 1000)
        self.setMinimumSize(width, height)
       
       
        height = max(int(available_geometry.height() * 0.75), 650)
        width = max(int(available_geometry.width() * 0.65), 1000)
        
        self.resize(width, height)
        self.move(
            available_geometry.center().x() - self.width() // 2,
            available_geometry.center().y() - self.height() // 2
        )
        
        self.setWindowFlags(
            self.windowFlags() | 
            Qt.WindowType.WindowMinimizeButtonHint | 
            Qt.WindowType.WindowMaximizeButtonHint | 
            Qt.WindowType.WindowCloseButtonHint
        )
        
        self.launch_mode = mode
        self.associated_project_path = project_path # Сохраняем путь к проекту
        
        
        self.associated_epub_path = None
        if self.associated_project_path:
            # Пытаемся найти EPUB через историю проектов (SettingsManager)
            # Это нужно, так как project_manager знает только папку, но не исходный файл
            app = QtWidgets.QApplication.instance()
            settings = app.get_settings_manager()
            history = settings.load_project_history()
            
            # Нормализуем пути для сравнения
            norm_proj_path = os.path.normpath(self.associated_project_path)
            
            for proj in history:
                if os.path.normpath(proj.get('output_folder', '')) == norm_proj_path:
                    self.associated_epub_path = proj.get('epub_path')
                    break
        
        
        # --- ИЗОЛЯЦИЯ БАЗЫ ДАННЫХ ---
        if self.launch_mode == 'child':
            # Для дочернего окна создаем УНИКАЛЬНУЮ изолированную базу.
            # Это предотвращает перезапись данных родительского окна.
            unique_id = uuid.uuid4().hex
            self.db_uri = f"file:child_session_{unique_id}?mode=memory&cache=shared"
            
            # Создаем "якорное" подключение, чтобы база жила, пока живет это окно
            self._child_db_anchor = sqlite3.connect(self.db_uri, uri=True, check_same_thread=False)
            # Инициализируем схему в этой новой пустой базе
            self._init_db(self._child_db_anchor)
            
        else:
            # Для основного режима используем глобальную общую базу
            self.db_uri = api_config.SHARED_DB_URI
            
            # Убеждаемся, что схема существует (используем глобальный якорь приложения)
            app = QtWidgets.QApplication.instance()
            if hasattr(app, 'main_db_connection'):
                self._init_db(app.main_db_connection)
            else:
                # Фолбэк на случай запуска вне main.py (для тестов)
                self._child_db_anchor = sqlite3.connect(self.db_uri, uri=True, check_same_thread=False)
                self._init_db(self._child_db_anchor)

        # Остальная инициализация...
        app = QtWidgets.QApplication.instance()
        self.settings_manager = app.get_settings_manager()
        self.logic = GlossaryLogic()
        self.history = []
        
        
        self.direct_conflicts = {}
        self.reverse_issues = {}
        self.overlap_groups = {}
        self.inverted_overlaps = {}
        self.untranslated_residue = {}
        self.conflicting_term_keys = set()
        self.is_analysis_dirty = False
        self.core_term_candidates = set() 
        self.conflict_map = defaultdict(set) 
        self._highlight_timer = None 
        self.term_to_conflict_keys_map = defaultdict(lambda: defaultdict(set))
        self.chinese_processor = ChineseTextProcessor()
        self._saved_glossary_snapshot = []
        self._saved_to_project_in_session = False
        self._dialog_result_closing = False

        # --- Состояние пагинации ---
        self.items_per_page = 100
        self.current_page = 0
        self.total_items = 0
        
        # --- Состояние сортировки ---
        self.sort_column_index = 0 
        self.sort_order = Qt.SortOrder.AscendingOrder
        self.sort_criterion = 'value'
        
        self.filter_state = {
            'text': "",
            'is_regex': False,
            'columns': {'original', 'rus', 'note'},
            'whole_word': False,
            'match_all': False # False = OR (Any), True = AND (All)
        }
        
        self.init_ui()
        
        # Если передан путь к проекту, сначала проверяем бекап
        restored_from_backup = False
        if self.associated_project_path and self.launch_mode != 'child':
            restored_from_backup = self._check_and_restore_backup()
            
        if not restored_from_backup and self.associated_project_path and self.launch_mode == 'standalone':
            self._try_load_project_glossary(self.associated_project_path)

        self._restore_project_view_state()
        self._load_current_page()
        self._update_project_save_controls()
    
    def _get_db_conn(self) -> sqlite3.Connection:
        """
        Возвращает подключение к ПРАВИЛЬНОЙ базе данных (изолированной или общей)
        в зависимости от режима запуска, с поддержкой REGEXP.
        """
        conn = sqlite3.connect(self.db_uri, uri=True, check_same_thread=False, timeout=15.0)
        conn.row_factory = sqlite3.Row
        
        # Регистрируем функцию REGEXP для поддержки регулярных выражений в SQL
        def regexp_func(expr, item):
            if not item: return False
            try:
                return re.search(expr, str(item), re.IGNORECASE) is not None
            except Exception:
                return False
                
        conn.create_function("REGEXP", 2, regexp_func)
        return conn

    def _init_db(self, conn: sqlite3.Connection):
        """Создает таблицу для хранения состояния редактора глоссария."""
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS glossary_editor_state (
                    id TEXT PRIMARY KEY,
                    sequence INTEGER NOT NULL,
                    original TEXT,
                    rus TEXT,
                    note TEXT,
                    timestamp REAL
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_glossary_sequence ON glossary_editor_state (sequence ASC);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_glossary_timestamp ON glossary_editor_state (timestamp DESC);")
    
    def init_ui(self):
        main_layout = QVBoxLayout(self)
        
        self.reflow_timer = QTimer(self)
        self.reflow_timer.setSingleShot(True)
        self.reflow_timer.setInterval(50)
        self.reflow_timer.timeout.connect(self._reflow_analysis_buttons)

        if not PYMORPHY_AVAILABLE and PYMORPHY_RECOMMENDATION:
            pymorphy_warning_label = QLabel(PYMORPHY_RECOMMENDATION)
            pymorphy_warning_label.setStyleSheet("background-color: #fff3cd; color: #856404; border: 1px solid #ffeeba; padding: 8px; border-radius: 4px;")
            pymorphy_warning_label.setOpenExternalLinks(True)
            pymorphy_warning_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            main_layout.addWidget(pymorphy_warning_label)

        
        
        
        
        
        
        top_controls = QHBoxLayout()
        
        # Кнопки "Открыть" и "Сохранить" показываем ТОЛЬКО в обычном режиме
        if self.launch_mode != 'child':
            load_widget = QWidget(); load_layout = QVBoxLayout(load_widget); load_layout.setContentsMargins(0,0,0,0)
            load_button = QPushButton("Открыть глоссарии…"); load_button.clicked.connect(self.load_files)
            load_hint_label = QLabel("Поддерживает JSON (простой/полный) и TXT"); load_hint_label.setStyleSheet("color: grey; font-size: 9pt;")
            load_layout.addWidget(load_button); load_layout.addWidget(load_hint_label)
            top_controls.addWidget(load_widget)
        
        self.undo_button = QPushButton("Отменить последнее действие"); self.undo_button.clicked.connect(self.undo_last_action)
        top_controls.addWidget(self.undo_button)

        if self.launch_mode != 'child' and self.associated_project_path:
            self.project_save_button = QPushButton("Сохранить в проект")
            self.project_save_button.clicked.connect(self._save_project_glossary)
            top_controls.addWidget(self.project_save_button)

        if self.launch_mode != 'child':
            self.save_button = QPushButton("Сохранить глоссарий как…"); self.save_button.clicked.connect(self.save_glossary)
            top_controls.addWidget(self.save_button)
        
        # Кнопка выхода/применения
        if self.launch_mode == 'dialog':
            self.apply_button = QPushButton("Применить и закрыть"); self.apply_button.clicked.connect(self.accept)
            top_controls.addWidget(self.apply_button)
        elif self.launch_mode == 'child':
            self.apply_button = QPushButton("✅ ПРИМЕНИТЬ ИЗМЕНЕНИЯ"); 
            self.apply_button.setStyleSheet("background-color: #2ECC71; color: white; font-weight: bold; padding: 6px;")
            self.apply_button.clicked.connect(self.accept) # accept закроет окно и вернет результат вызывающему
            top_controls.addWidget(self.apply_button)
        
        add_term_button = QPushButton("➕ Добавить термин"); add_term_button.clicked.connect(self._add_new_term)
        top_controls.addWidget(add_term_button)
        
        main_layout.addLayout(top_controls)
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        analysis_group = QGroupBox("Анализ и действия")
        self.analysis_layout = QGridLayout(analysis_group)
        
        # --- НАЧАЛО ИЗМЕНЕНИЯ: Разделение кнопок ---
        self.analyze_button = QPushButton("🔄 Анализ"); self.analyze_button.setToolTip("Запустить полный анализ глоссария")
        self.analyze_button.clicked.connect(lambda: self.analyze_and_update_ui(structural_patch={'type': 'run_full_analysis'}))
        self.ai_correction_button = QPushButton("🤖 Исправить с AI…"); self.ai_correction_button.setToolTip("Использовать AI для автоматического исправления")
        self.ai_correction_button.clicked.connect(self._start_ai_correction_session)
        self.fix_with_importer_button = QPushButton("⚙️ Исправить с Мастером импорта"); self.fix_with_importer_button.setVisible(False)
        self.fix_with_importer_button.clicked.connect(self.run_importer_on_current_data)
        
        self.static_analysis_buttons = [self.analyze_button, self.ai_correction_button, self.fix_with_importer_button]
        if PYMORPHY_AVAILABLE:
            self.generate_all_notes_button = QPushButton("📝 Сгенерировать примечания…"); self.generate_all_notes_button.clicked.connect(self._generate_notes_for_all)
            self.static_analysis_buttons.append(self.generate_all_notes_button)

        # --- НОВАЯ КНОПКА: ГРУППОВОЙ АНАЛИЗ ---
        self.group_analysis_button = QPushButton("📂 Анализ групп")
        self.group_analysis_button.setToolTip("Найти группы терминов по общим словам (например, 'локация', 'меч') и редактировать их отдельно.")
        self.group_analysis_button.clicked.connect(self.open_group_analysis)
        self.static_analysis_buttons.append(self.group_analysis_button)
        # --------------------------------------
        
        
        
        self.direct_conflict_button = QPushButton("Прямые конфликты"); self.direct_conflict_button.clicked.connect(self.resolve_direct_conflicts)
        self.reverse_conflict_button = QPushButton("Обратные конфликты"); self.reverse_conflict_button.clicked.connect(self.resolve_reverse_conflicts)
        self.overlap_button = QPushButton("Наложения"); self.overlap_button.clicked.connect(self.resolve_overlaps)
        self.core_term_button = QPushButton("Общие паттерны"); self.core_term_button.setToolTip("Анализ терминов с общими частями."); self.core_term_button.clicked.connect(self.resolve_core_terms)
        self.residue_button = QPushButton("Непереведенные остатки"); self.residue_button.setToolTip("Найти латиницу/CJK в переводе."); self.residue_button.clicked.connect(self.resolve_untranslated_residue)

        
        
        
        self.freq_analysis_button = QPushButton("📊 Частотный анализ")
        self.freq_analysis_button.setToolTip("Просканировать книгу и найти термины, которые не используются или встречаются слишком часто (мусор).")
        self.freq_analysis_button.clicked.connect(self.open_frequency_analyzer)

        
        self.dynamic_analysis_buttons = [
            self.direct_conflict_button, self.reverse_conflict_button, self.overlap_button, 
            self.core_term_button, self.residue_button
        ]
        
        self.dynamic_analysis_buttons.append(self.freq_analysis_button) # Добавляем в список для авто-reflow
        
        self.status_label = QLabel("Загрузите файлы для начала анализа.")
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---

        self._reflow_analysis_buttons()
        main_layout.addWidget(analysis_group)
        
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableWidget(columnCount=5)
        self.table.setHorizontalHeaderLabels(["Ориг. термин", "Перевод", "Примечание", "", ""])
        
        header = self.table.horizontalHeader()
        
        # --- ИСПРАВЛЕНИЕ 1: Отключаем встроенную сортировку Qt, включаем только индикатор ---
        self.table.setSortingEnabled(False) 
        header.setSortIndicatorShown(True) 
        
        # Подключаем НАШ слот для клика по заголовку
        header.sectionClicked.connect(self._on_header_clicked)
        
        for i in range(3): header.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
        for i in range(3, 5): header.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        
        self.table.verticalHeader().setDefaultSectionSize(30)
        # self.table.setSortingEnabled(True)  <-- ЭТУ СТРОКУ УДАЛИТЬ ИЛИ ЗАКОММЕНТИРОВАТЬ
        
        self.table.itemChanged.connect(self.on_main_table_item_changed)
        delegate = ExpandingTextEditDelegate(self.table); self.table.setItemDelegate(delegate)
        splitter.addWidget(self.table)
        
        history_widget = QWidget(); history_layout = QVBoxLayout(history_widget)
        history_layout.addWidget(QLabel("<b>История изменений:</b>")); self.history_table = QTableWidget(columnCount=2)
        self.history_table.setHorizontalHeaderLabels(["Действие", "Описание"]); self.history_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        history_layout.addWidget(self.history_table); splitter.addWidget(history_widget)
        splitter.setSizes([600, 200]); main_layout.addWidget(splitter)

        pagination_widget = QWidget(); pagination_layout = QHBoxLayout(pagination_widget); pagination_layout.setContentsMargins(0, 5, 0, 0)
        
        
        
        # --- КНОПКА ПОИСКА (СЛЕВА) ---
        self.search_button = QPushButton("🔍 Поиск / Фильтр")
        self.search_button.clicked.connect(self._open_filter_dialog)
        pagination_layout.addWidget(self.search_button)

        # --- НОВАЯ КНОПКА: РЕДАКТИРОВАТЬ ФИЛЬТР КАК ГРУППУ ---
        self.open_filtered_group_btn = QPushButton("📝 Ред. группу")
        self.open_filtered_group_btn.setToolTip("Открыть текущие результаты поиска в отдельном окне для массовой правки")
        self.open_filtered_group_btn.clicked.connect(self.open_filtered_as_group)
        self.open_filtered_group_btn.setVisible(False) 
        self.open_filtered_group_btn.setStyleSheet(
            "background-color: rgba(26, 188, 156, 0.15); "  # Полупрозрачный фон (Teal)
            "color: #1abc9c; "                               # Яркий текст
            "font-weight: bold; "
            "border: 1px solid rgba(26, 188, 156, 0.5); "    # Тонкая рамка
            "border-radius: 4px; padding: 4px 8px;"
        )
        pagination_layout.addWidget(self.open_filtered_group_btn)

        pagination_layout.addStretch() # Распорка слева от пагинации
        
        self.first_page_button = QPushButton("<< В начало"); self.first_page_button.clicked.connect(self._go_to_first_page)
        self.prev_page_button = QPushButton("< Назад"); self.prev_page_button.clicked.connect(self._go_to_prev_page)
        self.page_info_label = QLabel("Страница 1 / 1"); self.page_info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.next_page_button = QPushButton("Вперед >"); self.next_page_button.clicked.connect(self._go_to_next_page)
        self.last_page_button = QPushButton("В конец >>"); self.last_page_button.clicked.connect(self._go_to_last_page)
        
        pagination_layout.addWidget(self.first_page_button); pagination_layout.addWidget(self.prev_page_button)
        pagination_layout.addWidget(self.page_info_label); pagination_layout.addWidget(self.next_page_button); pagination_layout.addWidget(self.last_page_button); 
        
        pagination_layout.addStretch() # Распорка справа от пагинации
        
        # --- КНОПКА СОРТИРОВКИ (СПРАВА) ---
        self.sort_button = QPushButton("⇅ Сортировка")
        self.sort_button.clicked.connect(self._open_sort_dialog)
        pagination_layout.addWidget(self.sort_button)
        # ----------------------------------------
        
        main_layout.addWidget(pagination_widget)
        
        self._update_analysis_widgets()
        
    def add_history(self, action_type, data):
        data['old_analysis_state'] = self._get_analysis_snapshot()
        if action_type == 'atomic':
            if data['change_type'] == 'add':
                data['added_id'] = data['entry']['id']
        self.history.append({'type': action_type, 'data': data})
        self.history_table.insertRow(0)
        self.history_table.setItem(0, 0, QTableWidgetItem(data.get('action_name', action_type)))
        self.history_table.setItem(0, 1, QTableWidgetItem(data.get('description', '')))
        self.undo_button.setEnabled(True)
        self._save_auto_backup()
        self._update_project_save_controls()

    def _snapshot_glossary_state(self, glossary_data=None) -> list:
        if glossary_data is None:
            glossary_data = self.get_glossary()

        snapshot = []
        for entry in glossary_data or []:
            snapshot.append({
                'original': entry.get('original', ''),
                'rus': entry.get('rus') or entry.get('translation') or '',
                'note': entry.get('note', ''),
                'timestamp': entry.get('timestamp'),
            })
        return snapshot

    def _has_unsaved_glossary_changes(self) -> bool:
        return self._snapshot_glossary_state() != self._saved_glossary_snapshot

    def mark_current_state_as_saved(self, saved_to_project: bool = False):
        self._saved_glossary_snapshot = self._snapshot_glossary_state()
        self._saved_to_project_in_session = saved_to_project
        self._update_project_save_controls()

    def is_current_state_saved_to_project(self) -> bool:
        return bool(
            self.associated_project_path and
            self._saved_to_project_in_session and
            not self._has_unsaved_glossary_changes()
        )

    def _update_project_save_controls(self):
        if not hasattr(self, 'project_save_button'):
            return

        is_dirty = self._has_unsaved_glossary_changes()
        self.project_save_button.setEnabled(
            bool(self.associated_project_path) and (is_dirty or not self._is_glossary_empty())
        )
        self.project_save_button.setText(
            "Сохранить в проект*" if is_dirty else "Сохранить в проект"
        )

    def _sync_saved_project_state_to_parent(self, glossary_data: list):
        if self.launch_mode != 'dialog':
            return

        parent_widget = self.parent()
        if parent_widget and hasattr(parent_widget, 'set_glossary'):
            copied_glossary = [item.copy() for item in glossary_data]
            try:
                parent_widget.set_glossary(copied_glossary, emit_signal=False)
            except TypeError:
                parent_widget.set_glossary(copied_glossary)

            parent_dialog = parent_widget.parent()
            while parent_dialog and parent_dialog.__class__.__name__ != 'InitialSetupDialog':
                parent_dialog = parent_dialog.parent()

            if parent_dialog and hasattr(parent_dialog, 'mark_project_glossary_as_saved'):
                parent_dialog.mark_project_glossary_as_saved(copied_glossary)

    def _save_project_glossary(self, checked=False, notify: bool = True) -> bool:
        del checked
        if not self.associated_project_path:
            return False

        self.table.setCurrentItem(None)
        QApplication.processEvents()
        glossary_to_save = self.get_glossary()
        project_glossary_path = os.path.join(self.associated_project_path, "project_glossary.json")

        try:
            with open(project_glossary_path, 'w', encoding='utf-8') as f:
                json.dump(glossary_to_save, f, ensure_ascii=False, indent=2, sort_keys=True)

            self.mark_current_state_as_saved(saved_to_project=True)
            self._sync_saved_project_state_to_parent(glossary_to_save)
            self.status_label.setText(
                f"Глоссарий сохранён в проект: {os.path.basename(self.associated_project_path)}"
            )
            if notify:
                QMessageBox.information(
                    self,
                    "Успех",
                    "Глоссарий сохранён в project_glossary.json.",
                )
            return True
        except Exception as e:
            QMessageBox.critical(
                self,
                "Ошибка",
                f"Не удалось сохранить project_glossary.json:\n{e}",
            )
            return False
    
    def _try_load_project_glossary(self, folder_path):
        """Пытается загрузить project_glossary.json из указанной папки."""
        file_path = os.path.join(folder_path, "project_glossary.json")
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.set_glossary(data, run_analysis=True)
                self.mark_current_state_as_saved(saved_to_project=True)
                self.status_label.setText(f"Загружен глоссарий проекта: {os.path.basename(folder_path)}")
            except Exception as e:
                QMessageBox.warning(self, "Ошибка загрузки", f"Не удалось прочитать файл проекта:\n{e}")
        else:
            # Если файла нет, просто ничего не делаем (начинаем с чистого листа)
            self.status_label.setText(f"Новый глоссарий для проекта: {os.path.basename(folder_path)}")
            
    def undo_last_action(self):
        if not self.history: return
        self.table.setCurrentItem(None)
        last_action = self.history.pop()
        action_type, data = last_action['type'], last_action['data']
        
        conn = self._get_db_conn()
        with conn:
            if action_type == 'atomic':
                change_type = data['change_type']
                if change_type == 'edit':
                    old_entry = data['old_entry']
                    conn.execute("UPDATE glossary_editor_state SET original=?, rus=?, note=? WHERE id=?",
                                 (old_entry['original'], old_entry['rus'], old_entry['note'], old_entry['id']))
                elif change_type == 'delete':
                    conn.executemany("INSERT OR REPLACE INTO glossary_editor_state (id, sequence, original, rus, note) VALUES (?, ?, ?, ?, ?)",
                                     [(e['id'], e['sequence'], e['original'], e['rus'], e['note']) for e in data['entries']])
                elif change_type == 'add':
                    conn.execute("DELETE FROM glossary_editor_state WHERE id=?", (data['added_id'],))
            
            elif action_type == 'wholesale':
                conn.execute("DELETE FROM glossary_editor_state")
                old_state = data['old_state']
                if old_state:
                    # При восстановлении wholesale-состояния, генерируем новые ID, чтобы избежать коллизий
                    conn.executemany("INSERT INTO glossary_editor_state (id, sequence, original, rus, note) VALUES (?, ?, ?, ?, ?)",
                                     [(str(uuid.uuid4()), i, e['original'], e['rus'], e['note']) for i, e in enumerate(old_state)])

        # --- КЛЮЧЕВОЕ ИЗМЕНЕНИЕ: Вместо всех sync/update вызываем одну функцию ---
        self._load_current_page()
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---
        
        self._restore_analysis_snapshot(data['old_analysis_state'])
        # _apply_all_highlights() уже вызывается внутри _load_current_page(), но повторный вызов не повредит, если анализ восстановил подсветку
        self._apply_all_highlights()
        self._update_analysis_widgets()

        self.history_table.removeRow(0)
        self.undo_button.setEnabled(len(self.history) > 0)
        self._save_auto_backup()
        self._update_project_save_controls()

    
    def _create_wait_dialog(self, message):
        dialog = QDialog(self); dialog.setWindowTitle("Пожалуйста, подождите")
        dialog.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.CustomizeWindowHint); dialog.setModal(True)
        layout = QVBoxLayout(dialog); label = QLabel(message); label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label); dialog.setMinimumWidth(250)
        return dialog
    
    def _close_wait_dialog(self):
        if hasattr(self, 'wait_dialog') and self.wait_dialog:
            self.wait_dialog.close(); self.wait_dialog = None
            
    @pyqtSlot(dict)
    def _on_create_new_term_requested(self, new_entry_data):
        """Слот, который принимает сигнал от диалога и создает новый термин."""
        self._add_new_term(new_entry_data=new_entry_data)
        QMessageBox.information(self, "Термин создан", 
            f"Новый термин '{new_entry_data.get('original')}' добавлен в конец списка и готов к редактированию.")
    
    def _add_new_term(self, new_entry_data=None):
        """
        "DB-driven" добавление. Вставляет термин в БД, затем переходит
        на нужную страницу и инициирует редактирование.
        """
        self.table.setCurrentItem(None)
        
        if new_entry_data and isinstance(new_entry_data, dict):
            original = new_entry_data.get('original', 'новый_термин')
            rus = new_entry_data.get('rus', '')
            note = new_entry_data.get('note', '')
        else:
            original = 'новый_термин'
            rus = ''
            note = ''
        
        new_id = str(uuid.uuid4())
        current_ts = time.time() # Фиксируем время создания
        final_entry_data = {
            'id': new_id, 
            'original': original, 
            'rus': rus, 
            'note': note,
            'timestamp': current_ts
        }

        conn = self._get_db_conn()
        with conn:
            cursor = conn.execute("SELECT MAX(sequence) FROM glossary_editor_state")
            max_seq = cursor.fetchone()[0]
            new_seq = 0 if max_seq is None else max_seq + 1
            final_entry_data['sequence'] = new_seq
            
            conn.execute("""
                INSERT INTO glossary_editor_state (id, sequence, original, rus, note, timestamp) 
                VALUES (?, ?, ?, ?, ?, ?)
            """, (new_id, new_seq, original, rus, note, current_ts))

        self.add_history('atomic', {
            'change_type': 'add',
            'action_name': 'Добавление',
            'description': f"Добавлен новый термин '{original}'",
            'entry': final_entry_data
        })
        self.is_analysis_dirty = True
        self._update_analysis_widgets()
        if self.launch_mode != 'child':
            self.save_button.setEnabled(True)
        target_page = self._find_page_for_id(new_id)
        self.current_page = target_page
        self._load_current_page()
        
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.data(self.DB_ID_ROLE) == new_id:
                self.table.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
                self.table.editItem(item)
                break
    
    def open_frequency_analyzer(self):
        """Открывает диалог частотного анализа и применяет полученный патч."""
        if self._is_glossary_empty():
            QMessageBox.warning(self, "Нет данных", "Глоссарий пуст.")
            return
            
        epub_path = None
        # Попытка найти путь (из родителя или проекта)
        parent = self.parent()
        if parent and hasattr(parent, 'selected_file') and parent.selected_file:
            epub_path = parent.selected_file
        if not epub_path and self.associated_project_path:
             history = self.settings_manager.load_project_history()
             for proj in history:
                 if proj.get('output_folder') == self.associated_project_path:
                     candidate = proj.get('epub_path')
                     if candidate and os.path.exists(candidate):
                         epub_path = candidate
                         break
        
        current_glossary = self.get_glossary()
        
        dialog = TermFrequencyAnalyzerDialog(current_glossary, epub_path, self)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # Получаем унифицированный патч (удаления + обновления)
            patch_list = dialog.get_patch()
            
            if patch_list:
                self._apply_patch_and_log_history(
                    patch_list, 
                    "Частотный анализ", 
                    current_glossary
                )
    
    def _add_table_row(self, entry_data: dict):
        """Быстро добавляет одну строку в конец таблицы."""
        self.table.blockSignals(True)
        row_count = self.table.rowCount()
        self.table.insertRow(row_count)
        self._populate_table_row(row_count, entry_data)
        self.table.scrollToBottom()
        self.table.blockSignals(False)
    
    def _remove_table_row_by_id(self, db_id: str):
        """Быстро находит и удаляет строку по ее ID из БД."""
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.data(self.DB_ID_ROLE) == db_id:
                self.table.removeRow(row)
                return
    
    def _update_table_row_from_data(self, entry_data: dict):
        """Быстро находит строку по ID и обновляет ее данные."""
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.data(self.DB_ID_ROLE) == entry_data['id']:
                self._populate_table_row(row, entry_data)
                return

    def _populate_table_row(self, row, entry_data: dict):
        """Заполняет одну строку таблицы данными. Внутренний метод."""
        items = [
            QTableWidgetItem(str(entry_data.get(k, '')))
            for k in ['original', 'rus', 'note']
        ]
        items[0].setData(self.DB_ID_ROLE, entry_data['id'])
        for col, item in enumerate(items):
            self.table.setItem(row, col, item)
        self._create_row_buttons(row, entry_data)


    def _update_analysis_widgets(self):
        if self._is_glossary_empty():
            # Скрываем ВСЕ кнопки
            for btn in self.static_analysis_buttons + self.dynamic_analysis_buttons: 
                btn.setVisible(False)
            self.status_label.setText("Загрузите файлы для начала анализа.")
        else:
            # Показываем все СТАТИЧЕСКИЕ кнопки
            for btn in self.static_analysis_buttons:
                btn.setVisible(True)

            # Обновляем видимость ДИНАМИЧЕСКИХ кнопок
            is_visible = lambda btn, collection: btn.setVisible(bool(collection)) and (not collection or btn.setText(f"{btn.text().split('(')[0].strip()} ({len(collection)})"))
            is_visible(self.direct_conflict_button, self.direct_conflicts)
            is_visible(self.reverse_conflict_button, self.reverse_issues)
            is_visible(self.overlap_button, self.overlap_groups)
            is_visible(self.core_term_button, self.core_term_candidates)
            is_visible(self.residue_button, self.untranslated_residue)
            self.freq_analysis_button.setVisible(True)
            
            status_parts = [ f"{name}: {len(data)}" for name, data in [("Прямых", self.direct_conflicts),("Обратных/Связей", self.reverse_issues),("Наложений", self.overlap_groups),("Остатков", self.untranslated_residue)] if data]
            base_status_text = "Проблем не найдено." if not status_parts else "<b>Найдено:</b> " + ", ".join(status_parts)
            
            if self.is_analysis_dirty:
                self.status_label.setText(f"{base_status_text} <b style='color: #E67E22;'>(!)</b>")
                self.status_label.setToolTip("Данные изменены. Рекомендуется перезапустить анализ.")
                self.analyze_button.setStyleSheet("font-weight: bold; border-color: #E67E22;")
            else:
                self.status_label.setText(base_status_text)
                self.status_label.setToolTip("")
                self.status_label.setStyleSheet("color: green;" if not status_parts else "")
                self.analyze_button.setStyleSheet("")
        
        # --- НАЧАЛО НОВОГО БЛОКА: Проверка на пустые столбцы ---
        if not self._is_glossary_empty():
            current_glossary = self.get_glossary()
            # Проверяем, что все значения в одном из ключевых столбцов - пустые
            all_originals_empty = all(not entry.get('original', '').strip() for entry in current_glossary)
            all_translations_empty = all(not entry.get('rus', '').strip() for entry in current_glossary)
            
            # Кнопка видима, если хотя бы один из столбцов полностью пуст
            self.fix_with_importer_button.setVisible(all_originals_empty or all_translations_empty)
        else:
            self.fix_with_importer_button.setVisible(False)
        # --- КОНЕЦ НОВОГО БЛОКА ---

        self.reflow_timer.start()


    def resolve_core_terms(self):
        self.table.setCurrentItem(None)
        current_glossary = self.get_glossary()
        
        # Проверяем, есть ли вообще что анализировать.
        # self.core_term_candidates - это set, который заполняется при основном анализе
        if not self.core_term_candidates:
            QMessageBox.information(self, "Нет данных", "Предварительный анализ не нашел паттернов для дальнейшей работы.")
            return

        dialog = CoreTermAnalyzerDialog(current_glossary, self.logic, self.core_term_candidates, PYMORPHY_AVAILABLE, self)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            patch_list = dialog.get_patch()
            self._apply_patch_and_log_history(patch_list, "Анализ по паттернам", current_glossary)
    
    
    def _apply_row_highlight(self, row):
        """
        БЫСТРО. Применяет или сбрасывает подсветку для ОДНОЙ строки.
        """
        item = self.table.item(row, 0)
        if not item: return
    
        # Получаем сохраненные типы конфликтов для этой строки
        conflict_types = item.data(self.ConflictTypeRole) or set()
        
        color = QColor("transparent")
        if conflict_types:
            # Можно задать разные цвета для разных типов, но пока используем один
            color = QColor(255, 243, 205, 120) # Полупрозрачный желтый
    
        for col in range(3): # Подсвечиваем только ячейки с данными
            cell_item = self.table.item(row, col)
            if cell_item:
                cell_item.setBackground(color)
    
    def _apply_all_highlights(self):
        """
        Запускает новый, прогрессивный процесс подсветки всей таблицы.
        """
        # Если уже идет другой процесс подсветки, останавливаем его
        if hasattr(self, '_highlight_timer') and self._highlight_timer:
            self._highlight_timer.stop()
            self._highlight_timer = None
    
        self._highlight_row_index = 0  # Начинаем с первой строки
        self._highlight_timer = QtCore.QTimer(self)
        self._highlight_timer.setSingleShot(True) # Таймер сработает один раз
        self._highlight_timer.timeout.connect(self._apply_highlights_chunk)
        self._highlight_timer.start(0) # Запускаем немедленно
# ---------------------------------------------------------------------------
    # --- НОВЫЕ МЕТОДЫ: СИСТЕМА ПАГИНАЦИИ И ЗАГРУЗКИ ДАННЫХ ---
    # ---------------------------------------------------------------------------
    
    @property
    def total_pages(self) -> int:
        """Вычисляет общее количество страниц."""
        if self.total_items == 0:
            return 1
        return (self.total_items + self.items_per_page - 1) // self.items_per_page

    def _get_sort_clause(self) -> str:
        """Возвращает строку для SQL-запроса ORDER BY."""
        column_map = {0: 'original', 1: 'rus', 2: 'note', 3: 'timestamp'}
        column_name = column_map.get(self.sort_column_index, 'sequence')
        
        if column_name == 'timestamp':
            sort_expression = "timestamp"
        elif self.sort_criterion == 'length':
            sort_expression = f"LENGTH({column_name})"
        else:
            sort_expression = f"{column_name} COLLATE NOCASE"

        order_str = "ASC" if self.sort_order == Qt.SortOrder.AscendingOrder else "DESC"
        return f"ORDER BY {sort_expression} {order_str}, id ASC"

    
    
    def _open_filter_dialog(self):
        """Открывает диалог настройки фильтра."""
        dialog = GlossaryFilterDialog(
            current_text=self.filter_state['text'],
            current_regex=self.filter_state['is_regex'],
            current_cols=self.filter_state['columns'],
            whole_word=self.filter_state.get('whole_word', False),
            match_all=self.filter_state.get('match_all', False),
            parent=self
        )
        
        if dialog.exec():
            self.filter_state = dialog.get_filter_state()
            
            # Меняем стиль кнопки
            if self.filter_state['text']:
                self.search_button.setStyleSheet(
                    "background-color: rgba(255, 215, 0, 0.15); " 
                    "border: 1px solid rgba(255, 215, 0, 0.5); "
                    "border-radius: 4px;"
                )
                mode_str = "Regex" if self.filter_state['is_regex'] else ("AND" if self.filter_state['match_all'] else "OR")
                self.search_button.setText(f"🔍 [{mode_str}] {self.filter_state['text'][:10]}...")
            else:
                self.search_button.setStyleSheet("")
                self.search_button.setText("🔍 Поиск / Фильтр")
                
            self.current_page = 0
            self._load_current_page()

    def _get_filter_sql(self):
        """
        Генерирует WHERE-часть SQL запроса.
        Поддерживает: Raw Regex, Whole Word, Split words (AND/OR logic), Cross-column search.
        """
        txt = self.filter_state['text']
        if not txt:
            return "", []
            
        cols = list(self.filter_state['columns']) # set -> list для стабильности порядка
        if not cols: return "WHERE 0", []
        
        is_regex = self.filter_state['is_regex']
        whole_word = self.filter_state.get('whole_word', False)
        match_all = self.filter_state.get('match_all', False) # True=AND, False=OR
        
        # --- Сценарий 1: Сырой Regex ---
        if is_regex:
            # Просто ищем этот regex в любом из выбранных полей
            conditions = [f"{col} REGEXP ?" for col in cols]
            clause = "WHERE (" + " OR ".join(conditions) + ")"
            params = [txt] * len(cols)
            return clause, params

        # --- Сценарий 2: Умный поиск (Split + Logic) ---
        import re
        # Разбиваем ввод на слова, фильтруем пустые
        words = [w.strip() for w in txt.split() if w.strip()]
        if not words: return "", []

        # Экранируем спецсимволы, чтобы они не ломали REGEXP SQLite
        escaped_words = [re.escape(w) for w in words]
        
        # Если "Целое слово" - оборачиваем в границы
        if whole_word:
            final_patterns = [f"\\b{w}\\b" for w in escaped_words]
        else:
            final_patterns = escaped_words # Просто частичное совпадение (как LIKE %...%)
            
        # SQL Конструктор
        # Для SQLite REGEXP мы передаем паттерн как параметр.
        
        if match_all:
            # ЛОГИКА "И" (AND): 
            # Каждое слово должно найтись ХОТЯ БЫ В ОДНОМ из выбранных полей.
            # (col1~w1 OR col2~w1) AND (col1~w2 OR col2~w2) ...
            
            main_conditions = []
            all_params = []
            
            for pattern in final_patterns:
                # Группа для одного слова: (orig REGEXP pat OR trans REGEXP pat ...)
                word_conditions = [f"{col} REGEXP ?" for col in cols]
                main_conditions.append(f"({' OR '.join(word_conditions)})")
                all_params.extend([pattern] * len(cols))
            
            clause = "WHERE " + " AND ".join(main_conditions)
            return clause, all_params
            
        else:
            # ЛОГИКА "ИЛИ" (OR):
            # Любое слово в любом поле.
            # Эффективнее всего собрать один большой Regex: (w1|w2|w3)
            # и искать его в полях.
            
            combined_pattern = "|".join(final_patterns)
            
            conditions = [f"{col} REGEXP ?" for col in cols]
            clause = "WHERE (" + " OR ".join(conditions) + ")"
            params = [combined_pattern] * len(cols)
            return clause, params
    
    
    def open_filtered_as_group(self):
        """
        Открывает текущие отфильтрованные записи в отдельном окне редактора.
        Работает как 'Групповой анализ', но для произвольного фильтра.
        """
        # 1. Получаем SQL для текущего фильтра
        filter_clause, filter_params = self._get_filter_sql()
        if not filter_clause:
            return # На всякий случай, хотя кнопка должна быть скрыта

        conn = self._get_db_conn()
        filtered_entries = []
        
        with conn:
            # Выбираем ВСЕ записи, попадающие под фильтр (без LIMIT/OFFSET), сортируем по sequence для порядка
            query = f"SELECT * FROM glossary_editor_state {filter_clause} ORDER BY sequence ASC"
            cursor = conn.execute(query, filter_params)
            filtered_entries = [dict(row) for row in cursor.fetchall()]

        if not filtered_entries:
            return

        # 2. Открываем дочерний редактор
        # Используем self.__class__, чтобы создать экземпляр того же класса (MainWindow)
        child_manager = self.__class__(parent=self, mode='child')
        filter_text = self.filter_state.get('text', '')
        child_manager.setWindowTitle(f"Редактор группы [{filter_text}] ({len(filtered_entries)} записей)")
        
        # Передаем данные. set_glossary создаст новые ID в изолированной базе ребенка
        child_manager.set_glossary(filtered_entries, run_analysis=True)
        
        result = child_manager.exec()
        
        # 3. Если пользователь сохранил изменения (нажал 'Применить')
        if result == QDialog.DialogCode.Accepted:
            new_group_data = child_manager.get_glossary()
            
            # Формируем патч для истории и применения
            # Стратегия: Удалить ВСЕ старые отфильтрованные записи, Вставить ВСЕ новые из редактора.
            # Это надежнее, чем пытаться сопоставить ID, так как в дочернем окне ID были пересозданы.
            patch_list = []
            
            # Удаление старых
            for old_entry in filtered_entries:
                patch_list.append({'before': old_entry, 'after': None})
                
            # Добавление новых
            for new_entry in new_group_data:
                patch_list.append({'before': None, 'after': new_entry})
            
            if patch_list:
                self._apply_patch_and_log_history(
                    patch_list, 
                    action_name="Правка по фильтру", 
                    old_state_for_history=self.get_glossary() # Снимок всего глоссария до правки
                )
                QMessageBox.information(self, "Успех", "Изменения группы применены.")
    
    def _load_current_page(self):
        """
        Загружает данные для текущей страницы с учетом активного ФИЛЬТРА.
        """
        print(f"DEBUG: Loading page {self.current_page + 1}...")
        self.table.blockSignals(True)
        
        filter_clause, filter_params = self._get_filter_sql()
        
        conn = self._get_db_conn()
        with conn:
            # 1. Считаем общее количество записей, попадающих под фильтр
            count_query = f"SELECT COUNT(id) FROM glossary_editor_state {filter_clause}"
            cursor = conn.execute(count_query, filter_params)
            self.total_items = cursor.fetchone()[0]

        # Коррекция текущей страницы
        if self.total_items > 0 and self.current_page >= self.total_pages:
            self.current_page = max(0, self.total_pages - 1)
        elif self.total_items == 0:
            self.current_page = 0
        
        self.table.clearContents()
        self.table.setRowCount(0)

        # Если фильтр ничего не нашел или база пуста
        if self.total_items == 0:
            if not self.filter_state['text']:
                self._full_redraw_from_db() # Показываем плейсхолдеры только если фильтр пуст
            else:
                # Если фильтр активен, но ничего не найдено - просто пустая таблица
                pass
            self._update_pagination_controls()
            self.table.blockSignals(False)
            return

        offset = self.current_page * self.items_per_page
        sort_clause = self._get_sort_clause()
        
        with conn:
            # 2. Выбираем данные с учетом фильтра, сортировки и пагинации
            # ВАЖНО: LIMIT/OFFSET идут после ORDER BY
            query = f"SELECT * FROM glossary_editor_state {filter_clause} {sort_clause} LIMIT ? OFFSET ?"
            # Параметры: сначала от фильтра, потом лимит и оффсет
            all_params = filter_params + [self.items_per_page, offset]
            
            cursor = conn.execute(query, all_params)
            page_data = [dict(row) for row in cursor.fetchall()]

        self.table.setRowCount(len(page_data))
        for i, row_data in enumerate(page_data):
            self._populate_table_row(i, row_data)
        
        self.table.blockSignals(False)
        self._update_pagination_controls()
        self._save_project_view_state()
        self._apply_all_highlights()
        self.table.resizeColumnToContents(3)
        self.table.resizeColumnToContents(4)

        # --- Логика видимости кнопки группового редактирования ---
        # Показываем кнопку только если введен текст фильтра и есть результаты
        has_text_filter = bool(self.filter_state.get('text', '').strip())
        can_show_group_edit = has_text_filter and self.total_items > 0
        
        self.open_filtered_group_btn.setVisible(can_show_group_edit)
        if can_show_group_edit:
            self.open_filtered_group_btn.setText(f"📝 Ред. эти {self.total_items}")



    def _update_pagination_controls(self):
        """Обновляет состояние кнопок и текста навигации."""
        total_pg = self.total_pages
        current_pg = self.current_page + 1
        
        self.page_info_label.setText(f"Страница {current_pg} / {total_pg}")
        
        is_not_first = self.current_page > 0
        self.first_page_button.setEnabled(is_not_first)
        self.prev_page_button.setEnabled(is_not_first)
        
        is_not_last = self.current_page < total_pg - 1
        self.next_page_button.setEnabled(is_not_last)
        self.last_page_button.setEnabled(is_not_last)

    def _go_to_first_page(self):
        self.table.setCurrentItem(None)
        self.current_page = 0
        self._load_current_page()

    def _go_to_prev_page(self):
        self.table.setCurrentItem(None)
        self.current_page = max(0, self.current_page - 1)
        self._load_current_page()

    def _go_to_next_page(self):
        self.table.setCurrentItem(None)
        self.current_page = min(self.total_pages - 1, self.current_page + 1)
        self._load_current_page()

    def _go_to_last_page(self):
        self.table.setCurrentItem(None)
        if self.total_pages > 0:
            self.current_page = self.total_pages - 1
            self._load_current_page()

    def _find_page_for_id(self, db_id: str) -> int:
        """Находит номер страницы для ID с учетом сортировки И ФИЛЬТРА."""
        conn = self._get_db_conn()
        
        filter_clause, filter_params = self._get_filter_sql()
        
        with conn:
            # 1. Проверяем, попадает ли этот ID вообще в выборку фильтра
            check_query = f"SELECT 1 FROM glossary_editor_state {filter_clause} AND id=?"
            # SQLite не поддерживает WHERE ... AND WHERE, нужно умно добавить ID в условие
            if filter_clause:
                check_query = f"SELECT 1 FROM glossary_editor_state {filter_clause} AND id=?"
            else:
                check_query = "SELECT 1 FROM glossary_editor_state WHERE id=?"
                
            cursor = conn.execute(check_query, filter_params + [db_id])
            if not cursor.fetchone():
                return self.current_page # Элемент скрыт фильтром, остаемся где были

            # 2. Получаем сам элемент для определения его значения сортировки
            cursor = conn.execute("SELECT * FROM glossary_editor_state WHERE id=?", (db_id,))
            target_item = cursor.fetchone()
            if not target_item: return 0

            column_map = {0: 'original', 1: 'rus', 2: 'note'}
            sort_column_name = column_map.get(self.sort_column_index, 'sequence')
            sort_value = target_item[sort_column_name]

            order_op = '<' if self.sort_order == Qt.SortOrder.AscendingOrder else '>'
            
            # 3. Считаем ранг среди ОТФИЛЬТРОВАННЫХ записей
            # Нужно добавить условие сортировки к условиям фильтра
            if self.sort_criterion == 'length':
                # Если сортируем по длине, то и сравнивать надо длину
                sort_expr = f"LENGTH({sort_column_name})"
                target_val = len(str(sort_value)) # Сравниваем с длиной искомого значения
            else:
                sort_expr = sort_column_name
                target_val = sort_value

            # 3. Считаем ранг
            base_where = filter_clause if filter_clause else "WHERE 1=1"
            
            rank_query = f"SELECT COUNT(id) FROM glossary_editor_state {base_where} AND {sort_expr} {order_op} ?"
            cursor = conn.execute(rank_query, filter_params + [target_val])
            rank = cursor.fetchone()[0]
            
            return rank // self.items_per_page
            
    def _apply_highlights_chunk(self):
        CHUNK_SIZE = 100
        rows_processed = 0
        
        self.table.blockSignals(True)
        
        # Получаем актуальные данные один раз перед циклом
        current_glossary = self.get_glossary()
        
        while self._highlight_row_index < self.table.rowCount() and rows_processed < CHUNK_SIZE:
            row = self._highlight_row_index
            item = self.table.item(row, 0)
    
            if item:
                # Вместо real_index используем db_id для поиска в списке
                db_id = item.data(self.DB_ID_ROLE)
                # Находим термин по его оригинальному тексту, так как ID в списке нет
                original_text = item.text()
                
                # Ищем термин в списке по оригинальному тексту
                term_data = next((e for e in current_glossary if e.get('original') == original_text), None)
                if term_data:
                    term = term_data.get('original', '')
                    conflict_types = self.conflict_map.get(term, set())
                    item.setData(self.ConflictTypeRole, conflict_types)
                    self._apply_row_highlight(row)
            
            self._highlight_row_index += 1
            rows_processed += 1
        
        self.table.blockSignals(False)
    
        if self._highlight_row_index < self.table.rowCount():
            self._highlight_timer.start(1)
        else:
            self._highlight_timer = None
            print("Progressive highlighting finished.")
    
    def _reset_analysis_state(self):
        """
        Полностью сбрасывает все результаты анализа.
        """
        self.direct_conflicts.clear()
        self.reverse_issues.clear()
        self.overlap_groups.clear()
        self.inverted_overlaps.clear()
        self.conflicting_term_keys.clear()
        self.is_analysis_dirty = True
        self._update_analysis_ui()
    
    def _invalidate_analysis_for_terms(self, affected_terms: set):
        """
        МГНОВЕННО. Точечно удаляет термины из результатов анализа, используя обратный индекс.
        Никаких циклов.
        """
        if not affected_terms: return
    
        for term in affected_terms:
            if term not in self.term_to_conflict_keys_map: continue
            
            conflict_keys = self.term_to_conflict_keys_map[term]
    
            for key in conflict_keys.get('direct_conflicts', set()):
                if key in self.direct_conflicts: del self.direct_conflicts[key]
            
            for key in conflict_keys.get('reverse_issues', set()):
                if key in self.reverse_issues: del self.reverse_issues[key]
            
            for key in conflict_keys.get('overlap_groups', set()):
                if key in self.overlap_groups: del self.overlap_groups[key]
            
            # Удаляем сам термин из `conflict_map` и обратного индекса
            if term in self.conflict_map: del self.conflict_map[term]
            del self.term_to_conflict_keys_map[term]
        
        self.conflicting_term_keys -= affected_terms
        self.is_analysis_dirty = True
        self._update_analysis_widgets()
    

    def _update_analysis_ui(self):
        """
        Обновляет только UI анализа (кнопки, статус, подсветка)
        на основе текущего состояния данных анализа. НЕ запускает сам анализ.
        """
        is_visible = lambda btn, collection: btn.setVisible(bool(collection)) and btn.setText(f"{btn.text().split('(')[0].strip()} ({len(collection)})")
    
        # Обновляем видимость кнопок
        is_visible(self.direct_conflict_button, self.direct_conflicts)
        is_visible(self.reverse_conflict_button, self.reverse_issues)
        is_visible(self.overlap_button, self.overlap_groups)
    
        # Обновляем статусную строку
        status_parts = [f"{name}: {len(data)}" for name, data in [("Прямых", self.direct_conflicts), ("Обратных/Связей", self.reverse_issues), ("Наложений", self.overlap_groups)] if data]
        
        base_status_text = ""
        if status_parts:
            base_status_text = "<b>Найдено:</b> " + ", ".join(status_parts)
        else:
            base_status_text = "Проблем не найдено."
    
        # Добавляем предупреждение, если данные устарели
        if self.is_analysis_dirty:
            self.status_label.setText(f"{base_status_text} <b style='color: #E67E22;'> (Данные могут быть неактуальны, рекомендуется перезапуск анализа)</b>")
            self.analyze_button.setStyleSheet("font-weight: bold; border-color: #E67E22;")
        else:
            self.status_label.setText(base_status_text)
            self.status_label.setStyleSheet("color: green;" if not status_parts else "")
            self.analyze_button.setStyleSheet("")
    
        # --- КЛЮЧЕВОЕ ИЗМЕНЕНИЕ: Заменяем полный redraw на целевое обновление подсветки ---
        self._apply_all_highlights()
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---
    
    
    def keyPressEvent(self, event: QtGui.QKeyEvent):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.table.hasFocus():
            self._remove_selected_terms()
        else:
            super().keyPressEvent(event)
    
    def _generate_notes_for_all(self):
        self.table.setCurrentItem(None)
        if not PYMORPHY_AVAILABLE: return QMessageBox.warning(self, "Функция недоступна", "Для использования этой функции необходима библиотека Pymorphy.")
        if self._is_glossary_empty(): return QMessageBox.information(self, "Нет данных", "Глоссарий пуст. Нечего обрабатывать.")
        scope = self._ask_generate_all_scope()
        if not scope: return
        self.generation_scope = scope
        self.wait_dialog = self._create_wait_dialog("Идет генерация примечаний…\nЭто может занять некоторое время.")
        self.wait_dialog.show(); QApplication.processEvents()
        QtCore.QTimer.singleShot(0, self._do_generate_notes_work)
    
    def _ask_generate_all_scope(self):
        dialog = QDialog(self); dialog.setWindowTitle("Выберите режим генерации")
        layout = QVBoxLayout(dialog); layout.addWidget(QLabel("Сгенерировать примечания для каких терминов?"))
        self.choice = None
        def set_choice(choice): self.choice = choice; dialog.accept()
        btn_all = QPushButton("Для ВСЕХ терминов"); btn_all.clicked.connect(lambda: set_choice('all'))
        btn_empty = QPushButton("Только для терминов с ПУСТЫМИ примечаниями"); btn_empty.clicked.connect(lambda: set_choice('empty'))
        
        # --- НАЧАЛО ИЗМЕНЕНИЙ ---
        button_box = QDialogButtonBox()
        cancel_button = button_box.addButton("Отмена", QDialogButtonBox.ButtonRole.RejectRole)
        button_box.rejected.connect(dialog.reject)
        # --- КОНЕЦ ИЗМЕНЕНИЙ ---
        
        layout.addWidget(btn_all); layout.addWidget(btn_empty); layout.addWidget(button_box)
        dialog.exec()
        return self.choice
    
    
    def _get_analysis_snapshot(self):
        """Создает и возвращает 'слепок' текущего состояния анализа."""
        return {
            'direct_conflicts': copy.deepcopy(self.direct_conflicts),
            'reverse_issues': copy.deepcopy(self.reverse_issues),
            'overlap_groups': copy.deepcopy(self.overlap_groups),
            'inverted_overlaps': copy.deepcopy(self.inverted_overlaps),
            'conflicting_term_keys': self.conflicting_term_keys.copy(),
            'conflict_map': copy.deepcopy(self.conflict_map),
            'term_to_conflict_keys_map': copy.deepcopy(self.term_to_conflict_keys_map),
            'is_analysis_dirty': self.is_analysis_dirty,
        }

    def _restore_analysis_snapshot(self, snapshot):
        """Восстанавливает состояние анализа из 'слепка'."""
        self.direct_conflicts = snapshot.get('direct_conflicts', {})
        self.reverse_issues = snapshot.get('reverse_issues', {})
        self.overlap_groups = snapshot.get('overlap_groups', {})
        self.inverted_overlaps = snapshot.get('inverted_overlaps', {})
        self.conflicting_term_keys = snapshot.get('conflicting_term_keys', set())
        self.conflict_map = snapshot.get('conflict_map', defaultdict(set))
        self.term_to_conflict_keys_map = snapshot.get('term_to_conflict_keys_map', defaultdict(lambda: defaultdict(set)))
        self.is_analysis_dirty = snapshot.get('is_analysis_dirty', True)
    
    def _start_ai_correction_session(self):
        """
        Создает, настраивает и показывает диалог AI-коррекции.
        Версия 3.0: Убрана ошибочная предварительная проверка. Диалог открывается всегда.
        """
        current_glossary = self.get_glossary() # Захватываем состояние "до" для истории


        self.correction_dialog = CorrectionSessionDialog(self.settings_manager, self)
        
        self.correction_dialog.correction_accepted.connect(
            lambda patch_list: self._apply_patch_and_log_history(patch_list, "AI-коррекция", current_glossary)
        )
        
        self.correction_dialog.exec()
        self.correction_dialog = None
    
    
    def _on_generate_note_in_main_table_clicked(self, row):
        if not PYMORPHY_AVAILABLE:
            return

        item = self.table.item(row, 0)
        if not item: return
        db_id = item.data(self.DB_ID_ROLE)
        if db_id is None: return

        translation_text = self.table.item(row, 1).text()
        note_text = self._generate_note_logic(translation_text)
        if not note_text: return

        conn = self._get_db_conn()
        with conn:
            cursor = conn.execute("SELECT * FROM glossary_editor_state WHERE id=?", (db_id,))
            old_row_data = cursor.fetchone()
        
        if not old_row_data: return
        old_entry = dict(old_row_data)
        
        if old_entry['note'] != note_text:
            with conn:
                conn.execute("UPDATE glossary_editor_state SET note=? WHERE id=?", (note_text, db_id))
            
            self.add_history('atomic', {
                'change_type': 'edit',
                'old_entry': old_entry,
                'action_name': 'Генерация примечания',
                'description': f"Сгенерировано примечание для '{old_entry['original']}'"
            })
            
            self.table.blockSignals(True)
            self.table.item(row, 2).setText(note_text)
            self.table.resizeRowToContents(row)
            self.table.blockSignals(False)
            
            self.is_analysis_dirty = True
            self._update_analysis_widgets()
    

    class VirtualTag:
        def __init__(self, grammemes_set):
            self.grammemes = grammemes_set
            self.case = next((g for g in grammemes_set if g in {'nomn', 'gent', 'datv', 'accs', 'ablt', 'loct'}), None)
            self.number = next((g for g in grammemes_set if g in {'sing', 'plur'}), None)
            self.gender = next((g for g in grammemes_set if g in {'masc', 'femn', 'neut'}), None)
            self.POS = next((g for g in grammemes_set if g.isupper()), None)
            self.animacy = next((g for g in grammemes_set if g in {'anim', 'inan'}), None)
        def __contains__(self, grammeme): return grammeme in self.grammemes
        def __str__(self): return ",".join(sorted(list(self.grammemes)))

    class VirtualParse:
        def __init__(self, word, tag_object, score):
            self.word = word
            self.tag = tag_object
            self.score = score
            self.methods_stack = (('Virtual', word, None),)
    
    def _convert_to_virtual_parses(self, word, original_parses):
        """
        Преобразует теги от Pymorphy в VirtualTag.
        Для "гибких" (неизменяемых/неизвестных) слов создает ОДНУ
        "абсолютную" flex-частицу без определенной части речи (POS=None).
        """
        
       # --- Отсечение неоднозначности ---
        if word.lower() in self.AMBIGUOUS_SERVICE_WORDS:
            # Ищем, есть ли среди гипотез служебная часть речи
            service_parses = [p for p in original_parses if p.tag.POS in {'PREP', 'CONJ', 'PRCL'}]
            if service_parses:
                # Если нашли, то считаем, что это единственно верный вариант.
                # Все остальные гипотезы (существительное "из" и т.д.) отбрасываются.
                original_parses = service_parses

        
        try:
            fixed_parse = next(p for p in original_parses if ('Fixd' in p.tag and 'NOUN' in p.tag) or 'UNKN' in p.tag)
            
            # --- НАЧАЛО ИЗМЕНЕНИЙ: Полностью очищаем POS ---
            # Удаляем все возможные теги частей речи, включая UNKN
            base_grammemes = set(g for g in fixed_parse.tag.grammemes if not g.isupper())
            base_grammemes.add('flex')

            new_tag = self.VirtualTag(base_grammemes)
            new_parse = self.VirtualParse(word=word, tag_object=new_tag, score=fixed_parse.score)
            
            return [new_parse]
            # --- КОНЕЦ ИЗМЕНЕНИЙ ---

        except StopIteration:
            virtual_parses = []
            for p_parse in original_parses:
                new_tag = self.VirtualTag(p_parse.tag.grammemes)
                virtual_parses.append(self.VirtualParse(word=word, tag_object=new_tag, score=p_parse.score))
            return virtual_parses
            
    PREPOSITION_MATRIX = {
        # Ключ: текст предлога.
        # Значение: словарь {требуемый падеж: балл}.
        'для':     {'gent': 12},
        'без':     {'gent': 12},
        'у':       {'gent': 11},
        'из':      {'gent': 11},
        'с':       {'gent': 11, 'accs': 10, 'ablt': 11}, # "с горы" (gent), "размером с дом" (accs), "с другом" (ablt)
        'в':       {'accs': 11, 'loct': 11}, # "в дом" (accs), "в доме" (loct)
        'на':      {'accs': 11, 'loct': 11}, # "на стол" (accs), "на столе" (loct)
        'о':       {'accs': 10, 'loct': 12},
        'про':     {'accs': 11},
        'к':       {'datv': 12, 'nomn': -1000, 'gent': -1000, 'accs': -1000, 'ablt': -1000, 'loct': -1000},
        'по':      {'datv': 11, 'accs': 10, 'loct': 10},
        'за':      {'accs': 11, 'ablt': 11},
        'над':     {'ablt': 12},
        'под':     {'accs': 11, 'ablt': 11},
        'перед':   {'ablt': 12},
        'при':     {'loct': 11},
    }
    
    AMBIGUOUS_SERVICE_WORDS = {'и', 'а', 'но', 'да', 'с', 'к', 'у', 'в', 'на', 'о', 'об', 'из', 'за', 'под', 'над', 'по', 'про', 'же', 'бы', 'ли'}
    
    CASE_GOVERNMENT_MATRIX = {
        # Ключ: падеж ГЛАВНОГО слова.
        # Значение: Словарь {падеж ЗАВИСИМОГО слова: балл}.
        'nomn': {'nomn': 7.0, 'gent': 10.0, 'datv': 8.0, 'accs': 3.0, 'ablt': 9.0, 'loct': 0.0},
        'gent': {'nomn': 0.0, 'gent': 2.0,  'datv': 0.0, 'accs': 0.0, 'ablt': 0.0, 'loct': 0.0},
        'datv': {'nomn': 0.0, 'gent': 7.0,  'datv': 0.0, 'accs': 0.0, 'ablt': 0.0, 'loct': 0.0},
        'accs': {'nomn': 0.0, 'gent': 9.0,  'datv': 0.0, 'accs': 0.0, 'ablt': 8.0, 'loct': 0.0},
        'ablt': {'nomn': 0.0, 'gent': 8.0,  'datv': 0.0, 'accs': 0.0, 'ablt': 0.0, 'loct': 0.0},
        'loct': {'nomn': 0.0, 'gent': 8.0,  'datv': 0.0, 'accs': 0.0, 'ablt': 0.0, 'loct': 0.0},
    }
    
    POS_GOVERNMENT_MATRIX = {
        # Ключ: Часть речи ГЛАВНОГО слова.
        # Значение: Словарь {падеж ЗАВИСИМОГО слова: балл}.
        'NOUN': { 'nomn': 1.0, 'gent': 4.0, 'datv': 3.0, 'accs': 1.0, 'ablt': 3.0, 'loct': 0.0 },
        'VERB': { 'nomn': 8.0, 'gent': 2.0, 'datv': 4.0, 'accs': 6.0, 'ablt': 4.0, 'loct': 1.0 },
        'INFN': { 'nomn':-5.0, 'gent': 2.0, 'datv': 4.0, 'accs': 6.0, 'ablt': 4.0, 'loct': 1.0 },
        'ADJF': { 'nomn':-10.0,'gent': 5.0, 'datv': 3.0, 'accs':-5.0, 'ablt': 2.0, 'loct':-5.0 },
        'PRTF': { 'nomn':-10.0,'gent': 5.0, 'datv': 3.0, 'accs': 6.0, 'ablt': 5.0, 'loct':-5.0 },
        'ADVB': { 'nomn':-15.0,'gent': 4.0, 'datv':-10.0,'accs':-10.0,'ablt':-10.0,'loct':-15.0},
        'NUMR': { 'nomn':-10.0,'gent': 2.0, 'datv':-10.0,'accs':-10.0,'ablt':-10.0,'loct':-15.0},
        'PRCL': { 'nomn':-20.0,'gent':-20.0,'datv':-20.0,'accs':-20.0,'ablt':-20.0,'loct':-20.0},
    }
    
    INTERNAL_COHERENCE_MATRIX = {
        #               NOUN    ADJF    VERB    INFN    PRTF    NUMR    ADVB    PRCL
        'NOUN': {   'NOUN': 4.0, 'ADJF': 5.0, 'VERB':-10.0, 'INFN':-10.0, 'PRTF': 20.0, 'NUMR': 5.0, 'ADVB':-5.0, 'PRCL':-5.0   },
        'ADJF': {   'NOUN': 20.0,'ADJF': 5.0, 'VERB':-15.0, 'INFN':-15.0, 'PRTF': 10.0, 'NUMR': 5.0, 'ADVB':-10.0,'PRCL':-10.0  },
        'VERB': {   'NOUN':-10.0,'ADJF':-15.0,'VERB': 2.0,  'INFN': 15.0, 'PRTF':-10.0, 'NUMR':-15.0, 'ADVB': 25.0, 'PRCL': 15.0  },
        'INFN': {   'NOUN':-10.0,'ADJF':-15.0,'VERB': 15.0, 'INFN': 2.0,  'PRTF':-10.0, 'NUMR':-15.0, 'ADVB': 25.0, 'PRCL': 15.0  },
        'PRTF': {   'NOUN': 20.0,'ADJF': 10.0,'VERB':-15.0, 'INFN':-15.0, 'PRTF': 5.0,  'NUMR': 5.0, 'ADVB':-10.0,'PRCL':-5.0   },
        'NUMR': {   'NOUN': 15.0,'ADJF': 5.0, 'VERB':-15.0, 'INFN':-15.0, 'PRTF': 5.0,  'NUMR': 5.0, 'ADVB':-10.0,'PRCL':-10.0  },
        'ADVB': {   'NOUN':-5.0, 'ADJF': 15.0,'VERB': 25.0, 'INFN': 25.0, 'PRTF': 15.0, 'NUMR':-10.0, 'ADVB': 10.0, 'PRCL': 8.0   },
        'PRCL': {   'NOUN':-5.0, 'ADJF':-5.0, 'VERB': 15.0, 'INFN': 15.0, 'PRTF':-5.0,  'NUMR':-10.0, 'ADVB': 8.0,  'PRCL':-5.0   },
    }
    
    PUNCTUATION_MATRIX = {
        # Ключ: знак препинания. Значение: "квадратная" матрица {POS_до: {POS_после: балл}}
        ',': {
            'NOUN': {'NOUN': 12.0, 'NPRO': -5.0,  'ADJF': 14.0, 'PRTF': 14.0, 'VERB': -5.0,  'NUMR': -5.0},
            'NPRO': {'NOUN': -5.0,  'NPRO': 12.0, 'ADJF': 14.0, 'PRTF': 14.0, 'VERB': -5.0,  'NUMR': -5.0},
            'ADJF': {'NOUN': 13.0, 'NPRO': 13.0, 'ADJF': 12.0, 'PRTF': 11.0, 'VERB': -5.0,  'NUMR': -5.0},
            'PRTF': {'NOUN': 13.0, 'NPRO': 13.0, 'ADJF': 11.0, 'PRTF': 12.0, 'VERB': -5.0,  'NUMR': -5.0},
            'VERB': {'NOUN': -5.0,  'NPRO': -5.0,  'ADJF': -5.0,  'PRTF': -5.0,  'VERB': 12.0, 'NUMR': -5.0},
            'NUMR': {'NOUN': -5.0,  'NPRO': -5.0,  'ADJF': -5.0,  'PRTF': -5.0,  'VERB': -5.0,  'NUMR': 12.0}, # Однородные числительные
        },
        
        ';': {
            # Точка с запятой обычно разделяет более крупные, независимые блоки.
            # Поэтому связи здесь слабее, чем у запятой.
            'NOUN': {'NOUN': 8.0, 'NPRO': -5.0, 'ADJF': 5.0,  'PRTF': 5.0,  'VERB': -5.0, 'NUMR': -5.0},
            'NPRO': {'NOUN': -5.0, 'NPRO': 8.0, 'ADJF': 5.0,  'PRTF': 5.0,  'VERB': -5.0, 'NUMR': -5.0},
            'ADJF': {'NOUN': 4.0,  'NPRO': 4.0,  'ADJF': 8.0,  'PRTF': 7.0,  'VERB': -5.0, 'NUMR': -5.0},
            'PRTF': {'NOUN': 4.0,  'NPRO': 4.0,  'ADJF': 7.0,  'PRTF': 8.0,  'VERB': -5.0, 'NUMR': -5.0},
            'VERB': {'NOUN': -5.0, 'NPRO': -5.0, 'ADJF': -5.0, 'PRTF': -5.0, 'VERB': 8.0,  'NUMR': -5.0},
            'NUMR': {'NOUN': -5.0, 'NPRO': -5.0, 'ADJF': -5.0, 'PRTF': -5.0, 'VERB': -5.0, 'NUMR': 8.0},
        },
        
        ':': {
            # Двоеточие вводит пояснение. Связь несимметрична.
            'NOUN': {'NOUN': 10.0, 'NPRO': 10.0, 'ADJF': 10.0, 'PRTF': 10.0, 'VERB': 10.0, 'NUMR': 10.0}, # Пояснение к существительному
            # остальные строки в основном будут с низкими баллами
            'NPRO': {'NOUN': -5.0, 'NPRO': -5.0, 'ADJF': -5.0, 'PRTF': -5.0, 'VERB': -5.0, 'NUMR': -5.0},
            'ADJF': {'NOUN': -5.0, 'NPRO': -5.0, 'ADJF': -5.0, 'PRTF': -5.0, 'VERB': -5.0, 'NUMR': -5.0},
            'PRTF': {'NOUN': -5.0, 'NPRO': -5.0, 'ADJF': -5.0, 'PRTF': -5.0, 'VERB': -5.0, 'NUMR': -5.0},
            'VERB': {'NOUN': -5.0, 'NPRO': -5.0, 'ADJF': -5.0, 'PRTF': -5.0, 'VERB': -5.0, 'NUMR': -5.0},
            'NUMR': {'NOUN': -5.0, 'NPRO': -5.0, 'ADJF': -5.0, 'PRTF': -5.0, 'VERB': -5.0, 'NUMR': -5.0},
        }
    }
    
    def _get_pos_priority(self, parse):
        tag = parse.tag
        if 'NOUN' in tag: pos_prio = 1.0
        elif 'NPRO' in tag: pos_prio = 2.0
        elif 'PRTF' in tag: pos_prio = 3.0
        elif 'ADJF' in tag: pos_prio = 4.0
        elif 'NUMR' in tag: pos_prio = 5.0
        else: pos_prio = 10.0
        if 'NOUN' in tag and ('Subx' in tag or 'Anum' in tag): pos_prio += 0.5
        return pos_prio

    def _calculate_nuclear_energy(self, atom):
        """
        ЯДЕРНАЯ ФИЗИКА (ДВУХФАЗНАЯ ВЕРСИЯ).
        Фаза 1: Определяет POS для всех flex-частиц.
        Фаза 2: Согласовывает падежи/числа внутри атома.
        """
        total_energy = 0.0
        resolved_atom = list(atom)
        
        # --- Фаза 1: Определение POS для всех flex-частиц в атоме ---
        for i, p in enumerate(resolved_atom):
            if 'flex' in p.tag and p.tag.POS is None:
                best_pos_score = -float('inf')
                best_pos = 'NOUN' 
                POS_HYPOTHESES = ['NOUN', 'ADJF'] 

                has_neighbors = (len(resolved_atom) > 1)
                
                if has_neighbors:
                    for pos_hypo in POS_HYPOTHESES:
                        current_score = 0
                        if i > 0:
                            prev_pos = resolved_atom[i-1].tag.POS or 'NOUN'
                            current_score += self.INTERNAL_COHERENCE_MATRIX.get(prev_pos, {}).get(pos_hypo, -1.0)
                        if i < len(resolved_atom) - 1:
                            next_pos = resolved_atom[i+1].tag.POS or 'NOUN'
                            current_score += self.INTERNAL_COHERENCE_MATRIX.get(pos_hypo, {}).get(next_pos, -1.0)
                        
                        if current_score > best_pos_score:
                            best_pos_score = current_score
                            best_pos = pos_hypo
                
                resolved_atom[i] = self._inject_pos(p, best_pos)

        # --- Фаза 2, Согласование ---
        # Проходим несколько раз, чтобы изменения "растеклись" по атому
        for _ in range(len(resolved_atom)):
            # Проход слева направо
            for i in range(len(resolved_atom) - 1):
                p1, p2 = resolved_atom[i], resolved_atom[i+1]
                # Если p1 стабилен, а p2 - нет, p2 наследует
                if p1.tag.case is not None and p2.tag.case is None:
                    resolved_atom[i+1] = self._create_resolved_parse(p2, p1.tag.case, p1.tag.number)
            
            # Проход справа налево
            for i in range(len(resolved_atom) - 1, 0, -1):
                p1, p2 = resolved_atom[i-1], resolved_atom[i]
                # Если p2 стабилен, а p1 - нет, p1 наследует
                if p2.tag.case is not None and p1.tag.case is None:
                    resolved_atom[i-1] = self._create_resolved_parse(p1, p2.tag.case, p2.tag.number)
        # --- ---

        # --- Финальный подсчет энергии когерентности ---
        for i in range(len(resolved_atom) - 1):
            prev_p, curr_p = resolved_atom[i], resolved_atom[i + 1]
            prev_pos = prev_p.tag.POS or 'NOUN'
            curr_pos = curr_p.tag.POS or 'NOUN'
            bond_energy = self.INTERNAL_COHERENCE_MATRIX.get(prev_pos, {}).get(curr_pos, -1.0)
            total_energy += bond_energy
        quanting = sum(p.score for p in resolved_atom)
        total_energy += quanting
        if resolved_atom and resolved_atom[0].tag.case == 'nomn' and 'flex' not in resolved_atom[0].tag:
            total_energy += 2.0
            
        return total_energy, resolved_atom
    
    def _create_resolved_parse(self, original_flex_parse, new_case, new_number='sing'):
        """Вспомогательный метод для создания 'схлопнувшейся' частицы."""
        base_grammemes = set(original_flex_parse.tag.grammemes) - {'flex'}
        
        # --- Фильтруем None значения ---
        # Создаем новый набор, добавляя new_case и new_number, только если они не None.
        new_grammemes = base_grammemes | {g for g in (new_case, new_number) if g is not None}

        
        new_tag = self.VirtualTag(new_grammemes)
        return self.VirtualParse(word=original_flex_parse.word, tag_object=new_tag, score=original_flex_parse.score)
        
    def _calculate_molecular_chemistry(self, main_atom, dep_atom, interaction_zone):
        """
        МОЛЕКУЛЯРНАЯ ХИМИЯ (ФИНАЛЬНАЯ ПОЛНАЯ ВЕРСИЯ).
        Симметрично измеряет энергию связи A<->B.
        Если один из атомов - неразрешенный flex-одиночка, определяет
        для него POS и падеж на основе связи и ПЕРЕЗАПИСЫВАЕТ его.
        """
        score1, res_main1, res_dep1 = self._calculate_chemistry_directional(main_atom, dep_atom, interaction_zone)
        score2, res_main2, res_dep2 = self._calculate_chemistry_directional(dep_atom, main_atom, [])

        if score1 >= score2:
            return score1, res_main1, res_dep1
        else:
            return score2, res_dep2, res_main2
    
    def _calculate_chemistry_directional(self, main_atom, dep_atom, interaction_zone):
        """Вспомогательный метод: считает ОДНОНАПРАВЛЕННУЮ связь и разрешает flex-одиночек."""
        total_link_score = 0
        
        resolved_main_atom = list(main_atom)
        resolved_dep_atom = list(dep_atom)
        
        main_p = resolved_main_atom[-1]
        dep_p = resolved_dep_atom[0]
        
        # --- Разрешаем POS для flex-одиночек, если "Ядерщик" не справился ---
        if len(resolved_main_atom) == 1 and 'flex' in main_p.tag and main_p.tag.POS is None:
            # Логика определения POS для главного...
            best_pos = 'NOUN' # fallback
            # (эта логика упрощена, т.к. Ядерщик уже должен был дать POS. Это запасной вариант)
            resolved_main_atom[0] = self._inject_pos(main_p, best_pos)
            main_p = resolved_main_atom[0]

        if len(resolved_dep_atom) == 1 and 'flex' in dep_p.tag and dep_p.tag.POS is None:
             # Логика определения POS для зависимого...
             best_pos = 'NOUN' # fallback
             resolved_dep_atom[0] = self._inject_pos(dep_p, best_pos)
             dep_p = resolved_dep_atom[0]
        
        main_tag = main_p.tag
        dep_tag = dep_p.tag

        # --- Шаг 1: Влияние предлогов ---
        preposition_case = None
        for carrier_atom in interaction_zone:
            carrier_parse = carrier_atom[0]
            carrier_word = carrier_parse.word.lower()
            if 'PREP' in carrier_parse.tag and carrier_word in self.PREPOSITION_MATRIX:
                rules = self.PREPOSITION_MATRIX[carrier_word]
                if rules:
                    best_case, prep_score = max(rules.items(), key=lambda item: item[1])
                    preposition_case = best_case
                    total_link_score += prep_score

        # --- Шаг 2: Определяем и схлапываем падеж зависимой частицы ---
        dep_case = dep_tag.case
        pos_score = 0
        if preposition_case:
            dep_case = preposition_case
        elif 'flex' in dep_tag and dep_case is None:
             main_pos_for_lookup = main_tag.POS or 'NOUN'
             pos_rules = self.POS_GOVERNMENT_MATRIX.get(main_pos_for_lookup, {})
             if pos_rules:
                 best_case, score = max(pos_rules.items(), key=lambda item: item[1])
                 dep_case = best_case
                 pos_score = score
        
        if dep_case is not None and dep_case != dep_tag.case:
             resolved_dep_atom[0] = self._create_resolved_parse(dep_p, dep_case, main_tag.number or 'sing')

        # --- Шаг 3: Считаем итоговую энергию ---
        total_link_score += pos_score

        if not preposition_case:
            main_case = main_tag.case or 'nomn'
            final_dep_case = resolved_dep_atom[0].tag.case or 'gent'
            case_rules = self.CASE_GOVERNMENT_MATRIX.get(main_case, {})
            total_link_score += case_rules.get(final_dep_case, 0.0)

        return total_link_score, resolved_main_atom, resolved_dep_atom
    
    
    # Вспомогательный метод, который нужен для _calculate_molecular_chemistry
    def _inject_pos(self, parse_obj, pos_str):
        if not pos_str or ('flex' not in parse_obj.tag and parse_obj.tag.POS == pos_str):
            return parse_obj
        
        new_grammemes = set(g for g in parse_obj.tag.grammemes if not g.isupper())
        new_grammemes.add(pos_str)
        new_tag = self.VirtualTag(new_grammemes)
        return self.VirtualParse(word=parse_obj.word, tag_object=new_tag, score=parse_obj.score)
    
    # --- ФУНКЦИЯ ДЛЯ ЧИСТОТЫ ---
    def _is_force_carrier(self, atom_parses):
        """
        Проверяет, является ли "атом" "переносчиком взаимодействия" (предлог, союз, пунктуация).
        """
        if len(atom_parses) == 1:
            p = atom_parses[0]
            tag = p.tag

            # Критерий 1: Это известная служебная часть речи?
            if 'PREP' in tag or 'CONJ' in tag or 'PRCL' in tag or 'INTJ' in tag:
                return True

            # Критерий 2: Это НАША "виртуальная" частица, которую мы создали для пунктуации?
            # Мы узнаем ее по "свидетельству о рождении" (methods_stack).
            if isinstance(p, self.VirtualParse) and p.tag.POS == 'PUNCT':
                return True
                
        return False
    
    def open_group_analysis(self):
        """Открывает диалог анализа групп по ключевым словам."""
        if self._is_glossary_empty():
            QMessageBox.warning(self, "Нет данных", "Глоссарий пуст.")
            return
            
        current_glossary = self.get_glossary()
        dialog = GroupAnalysisDialog(current_glossary, parent=self)
        dialog.exec() # Диалог модальный, он сам управляет процессом
    
    def _calculate_molecule_energy(self, molecule):
        """
        ДИРИЖЕР (ФИНАЛЬНАЯ ВЕРСИЯ).
        Правильно использует Ядерщик, а затем Химик, который
        может разрешать flex-одиночек.
        """
        if not molecule: return 0.0, []

        total_energy = 0.0
        resolved_molecule = []

        # Шаг 1: Стабилизируем все атомы внутри
        for atom in molecule:
            nuclear_energy, resolved_atom = self._calculate_nuclear_energy(atom)
            total_energy += nuclear_energy
            resolved_molecule.append(resolved_atom)

        # Шаг 2: Разрешаем связи между атомами
        i = 0
        while i < len(resolved_molecule):
            if self._is_force_carrier(resolved_molecule[i]):
                i += 1
                continue
            
            next_matter_idx = i + 1
            while next_matter_idx < len(resolved_molecule) and self._is_force_carrier(resolved_molecule[next_matter_idx]):
                next_matter_idx += 1

            if next_matter_idx < len(resolved_molecule):
                main_atom = resolved_molecule[i]
                dep_atom = resolved_molecule[next_matter_idx]
                interaction_zone = resolved_molecule[i+1 : next_matter_idx]
                
                # Принимаем 3 значения, так как Химик может разрешать атомы
                link_score, resolved_main_atom, resolved_dep_atom = self._calculate_molecular_chemistry(
                    main_atom, dep_atom, interaction_zone
                )
                total_energy += link_score
                
                # Обновляем атомы в нашей молекуле
                resolved_molecule[i] = resolved_main_atom
                resolved_molecule[next_matter_idx] = resolved_dep_atom

            i = next_matter_idx
            
        return total_energy, resolved_molecule


    def _crystallize(self, parse_combination):
        """
        КРИСТАЛЛИЗАТОР (ФИНАЛЬНАЯ ВЕРСИЯ).
        Группирует частицы в атомы по правилам согласования.
        Немедленно разрешает падеж/число для flex-частиц при слиянии.
        Не определяет POS.
        """
        def build_molecules_recursively(index, current_molecule):
            if index >= len(parse_combination):
                yield current_molecule
                return

            current_p = parse_combination[index]
            
            if current_molecule:
                last_atom = current_molecule[-1]
                prev_p = last_atom[-1]
                
                is_prev_flex = 'flex' in prev_p.tag
                is_curr_flex = 'flex' in current_p.tag
                
                can_merge = (not self._is_force_carrier([prev_p]) and 
                             not self._is_force_carrier([current_p]) and
                             (prev_p.tag.number == current_p.tag.number or is_prev_flex or is_curr_flex) and
                             (prev_p.tag.gender == current_p.tag.gender or prev_p.tag.gender is None or prev_p.tag.gender is None) and
                             (prev_p.tag.case == current_p.tag.case or is_prev_flex or is_curr_flex))

                if can_merge:
                    resolved_p = current_p
                    # Если текущая частица flex, она наследует граммемы от соседа
                    if is_curr_flex and not is_prev_flex:
                        resolved_p = self._create_resolved_parse(current_p, prev_p.tag.case, prev_p.tag.number)
                    
                    merged_molecule = current_molecule[:-1] + [last_atom + [resolved_p]]
                    yield from build_molecules_recursively(index + 1, merged_molecule)

            new_atom_molecule = current_molecule + [[current_p]]
            yield from build_molecules_recursively(index + 1, new_atom_molecule)

        if not parse_combination: return []
        return list(build_molecules_recursively(0, []))

    def _generate_note_logic(self, translation_text, debug=False, return_raw_parse=False):
        if not PYMORPHY_AVAILABLE or not translation_text: 
            if return_raw_parse: return None, None, None
            return ""
        
        tokens = re.findall(r"[\w'-]+|[,;:.]", translation_text)
        if not tokens: 
            if return_raw_parse: return None, None, None
            return ""
        if len(tokens) == 1 and tokens[0] not in ",;:": 
            if return_raw_parse:
                # Для одного слова главный разбор - это просто первый разбор
                parsed = self._convert_to_virtual_parses(tokens[0], morph_analyzer.parse(tokens[0]))[0]
                head_data = {'word': tokens[0], 'parses': [parsed]}
                return None, head_data, parsed
            return self._generate_single_word_note(tokens[0])
        
        all_parses_data = []
        for i, token in enumerate(tokens):
            if token in ",;:.":
                # Пунктуация сразу становится виртуальной
                parse = self.VirtualParse(word=token, tag_object=self.VirtualTag({'PUNCT'}), score=1.0)
                all_parses_data.append({'index': i, 'word': token, 'parses': [parse]})
            else:
                # 1. Получаем "сырые" разборы от Pymorphy
                raw_parses = morph_analyzer.parse(token)
                # 2. Немедленно конвертируем их в наши виртуальные объекты
                virtual_parses = self._convert_to_virtual_parses(token, raw_parses)
                all_parses_data.append({'index': i, 'word': token, 'parses': virtual_parses})

        
        all_parse_combinations = list(itertools.product(*(word['parses'] for word in all_parses_data)))

        molecules_with_energy = []
        # --- НАЧАЛО ВЛОЖЕННОЙ СТРУКТУРЫ ---
        for combo in all_parse_combinations:
            # 1. Получаем список молекул ТОЛЬКО для ТЕКУЩЕГО combo
            possible_molecules = self._crystallize(combo)
            
            # 2. Немедленно обрабатываем эти молекулы, ПОКА мы еще помним правильный combo
            for molecule in possible_molecules:
                energy, resolved_molecule = self._calculate_molecule_energy(molecule)
                
                # Здесь 'combo' - это именно та комбинация, которая породила 'molecule'. Связь не потеряна.
                molecules_with_energy.append((energy, molecule, combo, resolved_molecule))
        # --- КОНЕЦ ВЛОЖЕННОЙ СТРУКТУРЫ ---


        if not molecules_with_energy: 
            if return_raw_parse: return None, None, None
            return "Не удалось проанализировать фразу."
        
        # Находим победителя по энергии
        best_energy, best_molecule, best_combo, best_resolved_molecule = max(molecules_with_energy, key=lambda item: item[0])
        
        # --- блок вывода ---
        if debug: 
            print("\n" + "="*70); print("--- ДЕБАГ: АНАЛИЗ ВСЕХ ВОЗМОЖНЫХ РЕАЛЬНОСТЕЙ ---"); print("="*70)
            LIMIT = 20
            sorted_molecules = sorted(molecules_with_energy, key=lambda item: item[0], reverse=True)
            
            for i, (energy, original_molecule, original_combo, resolved_molecule) in enumerate(sorted_molecules):
                if i >= LIMIT: print(f"\n... и еще {len(sorted_molecules) - LIMIT} состояний ..."); break
                
                # Показываем исходную комбинацию, чтобы видеть, с чего все началось
                combo_str = " + ".join([f"{p.word}({p.tag.POS or '?'},{p.tag.case or '?'})" for p in original_combo])
                
                # Показываем, как кристаллизатор сгруппировал атомы
                mol_str = " + ".join([f"[{' '.join(p.word for p in atom)}]" for atom in original_molecule])
                
                # А вот здесь показываем ФИНАЛЬНЫЙ, "схлопнувшийся" результат!
                resolved_str_parts = []
                for atom in resolved_molecule:
                    atom_parts = []
                    for p in atom:
                        atom_parts.append(f"{p.word}({p.tag.POS or '?'},{p.tag.case or '?'})")
                    resolved_str_parts.append(f"[{' '.join(atom_parts)}]")
                resolved_str = " + ".join(resolved_str_parts)

                print(f"\n{i+1}. Комбинация ('газ'): {combo_str}")
                print(f"    ↳ Кристалл (структура): {mol_str}")
                print(f"    ↳ Кристалл (финал):     {resolved_str:<40} | Энергия: {energy:.4f}")
            
            print("="*70)
            
            # В выводе победителя тоже используем resolved_molecule
            winner_combo_str = " + ".join([f"{p.word}({p.tag.POS or '?'},{p.tag.case or '?'})" for p in best_combo])
            winner_resolved_str = " + ".join([f"[{' '.join(f'{p.word}({p.tag.POS},{p.tag.case})' for p in atom)}]" for atom in best_resolved_molecule])
            
            print(f"--- ПОБЕДИТЕЛЬ ---")
            print(f"Исходная комбинация: {winner_combo_str}")
            print(f"Финальная структура: {winner_resolved_str}")
            print(f"Финальная энергия: {best_energy:.4f}")
            print("="*70 + "\n")



        # Шаг 3: Анализ победителя
        nomn_atoms = [atom for atom in best_resolved_molecule if not self._is_force_carrier(atom) and atom[0].tag.case == 'nomn']
        matter_atoms = [atom for atom in best_resolved_molecule if not self._is_force_carrier(atom)]
        if not matter_atoms: 
            if return_raw_parse: return None, None, None
            return "Фраза состоит только из служебных слов."

        main_atom = None
        if not nomn_atoms:
            main_atom = min(matter_atoms, key=lambda atom: min(self._get_pos_priority(p) for p in atom))
        else:
            main_atom = min(nomn_atoms, key=lambda atom: min(self._get_pos_priority(p) for p in atom))

        best_candidate_parse = min(main_atom, key=lambda p: self._get_pos_priority(p))
        
        # Попытка 1: быстрая, по слову
        head_data = next((d for d in all_parses_data if d['word'] == best_candidate_parse.word), None)
        
        # Если первая попытка провалилась, пробуем вторую, более надежную
        if not head_data:
            try:
                head_data = next(d for d in all_parses_data if best_candidate_parse in d['parses'])
            except StopIteration:
                head_data = None # Обе попытки провалились
        
        # Теперь, когда мы испробовали ВСЕ, проверяем результат
        if not head_data:
            if return_raw_parse: 
                return None, None, None # Вот правильное место для выхода
            return "Ошибка анализа: не найдено главное слово."
        
        if return_raw_parse:
            return all_parses_data, head_data, best_candidate_parse
        
        return self._format_final_note(all_parses_data, head_data, best_candidate_parse)


    
    def _generate_single_word_note(self, word):
        # Ваша версия этой функции
        parsed = morph_analyzer.parse(word)[0]
        tag = parsed.tag; features = []
        if 'masc' in tag: features.append("муж. род")
        elif 'femn' in tag: features.append("жен. род")
        elif 'neut' in tag: features.append("ср. род")
        elif 'ms-f' in tag: features.append("общий род")
        if 'NOUN' in tag:
            if 'anim' in tag: features.append("одуш.")
            elif 'inan' in tag: features.append("неодуш.")
        if 'Fixd' in tag or 'UNKN' in tag:
            if 'Abbr' in tag: return "Несклоняемая аббревиатура."
            note = f"Несклоняемое слово ({', '.join(features)})." if features else "Несклоняемое слово."
            return note
        else:
            if 'Pltm' in tag: features.append("только мн. число")
            change_type = "спрягается" if 'VERB' in tag or 'INFN' in tag else "склоняется"
            return f"{change_type.capitalize()} ({', '.join(features)})."
    
    def _format_final_note(self, all_parses_data, best_candidate_data, main_word_parse):
        main_word_str = best_candidate_data['word']
        tag = main_word_parse.tag
        
        main_features = []
        if 'masc' in tag: main_features.append('муж. род')
        elif 'femn' in tag: main_features.append('жен. род')
        elif 'neut' in tag: main_features.append('ср. род')
        if 'sing' in tag or 'Sgtm' in tag: main_features.append('ед. число')
        elif 'plur' in tag or 'Pltm' in tag: main_features.append('мн. число')
        if 'NOUN' in tag:
            if 'anim' in tag: main_features.append('одуш.')
            elif 'inan' in tag: main_features.append('неодуш.')

        features_str = ""
        # Создаем строку с характеристиками только если они есть
        if main_features:
            features_str = f" ({', '.join(main_features)})"
        
        # Собираем основную информацию, добавляя точку в конце
        main_word_info = f"Главное слово: «{main_word_str}»{features_str}."
        # ------------------------------------
        
        uninflected_words = [
            f"«{d['word']}»" for d in all_parses_data 
            if d['word'] != main_word_str and 
            d.get('parses') and ('Fixd' in d['parses'][0].tag or 'UNKN' in d['parses'][0].tag or 'ADVB' in d['parses'][0].tag)
        ]
        
        other_info = ""
        if uninflected_words:
            other_info = f"Неизменяемые слова: {', '.join(uninflected_words)}."
            
        return " ".join(filter(None, [main_word_info, other_info])).strip()
        
    def set_glossary(self, glossary_data: list, run_analysis: bool = True):
        import time
        self.history.clear()
        self.history_table.setRowCount(0)
        conn = self._get_db_conn()
        current_now = time.time()
        
        with conn:
            conn.execute("DELETE FROM glossary_editor_state")
            if glossary_data:
                to_insert = []
                for i, e in enumerate(glossary_data):
                    rus_val = e.get('rus') or e.get('translation') or ''
                    # ПРАВИЛО: Сохраняем существующий таймстамп или создаем новый для новых строк
                    ts_val = e.get('timestamp') or current_now
                    to_insert.append((
                        str(uuid.uuid4()), 
                        i, 
                        e.get('original', ''), 
                        rus_val, 
                        e.get('note', ''),
                        ts_val
                    ))
                conn.executemany("INSERT INTO glossary_editor_state (id, sequence, original, rus, note, timestamp) VALUES (?, ?, ?, ?, ?, ?)", to_insert)
        
        self.current_page = 0 
        self._load_current_page()
        if self.launch_mode != 'child':
            self.save_button.setEnabled(bool(glossary_data))
        self._update_project_save_controls()
        if run_analysis:
            self.add_history('wholesale', {'action_name': "Начальная загрузка", 'description': f"Загружено {len(glossary_data)} терминов.", 'old_state': []})
            self.is_analysis_dirty = True
            self._run_full_analysis()
        else:
            self._update_analysis_widgets()

    def get_glossary(self) -> list:
        conn = self._get_db_conn()
        with conn:
            # Обязательно выбираем timestamp для сохранения в файл
            cursor = conn.execute("SELECT original, rus, note, timestamp FROM glossary_editor_state ORDER BY sequence ASC")
            return [dict(row) for row in cursor.fetchall()]
    
    def _remove_selected_terms(self):
        selected_indexes = self.table.selectionModel().selectedIndexes()
        if not selected_indexes: return
        
        db_ids_to_delete = sorted(list({self.table.item(idx.row(), 0).data(self.DB_ID_ROLE) for idx in selected_indexes if self.table.item(idx.row(), 0)}))
        if not db_ids_to_delete: return

        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Подтверждение удаления")
        msg_box.setText(f"Вы уверены, что хотите удалить {len(db_ids_to_delete)} выделенных строк(у)?")
        msg_box.setIcon(QMessageBox.Icon.Question)
        yes_button = msg_box.addButton("Да, удалить", QMessageBox.ButtonRole.YesRole)
        no_button = msg_box.addButton("Нет", QMessageBox.ButtonRole.NoRole)
        msg_box.exec()
        
        if msg_box.clickedButton() == yes_button:
            conn = self._get_db_conn()
            deleted_entries, terms_to_invalidate = [], set()
            
            with conn:
                placeholders = ','.join('?' for _ in db_ids_to_delete)
                # Сначала получаем данные для истории
                cursor = conn.execute(f"SELECT * FROM glossary_editor_state WHERE id IN ({placeholders})", db_ids_to_delete)
                for row_data in cursor.fetchall():
                    entry_dict = dict(row_data)
                    deleted_entries.append(entry_dict)
                    terms_to_invalidate.add(entry_dict['original'])
                # Затем удаляем
                conn.execute(f"DELETE FROM glossary_editor_state WHERE id IN ({placeholders})", db_ids_to_delete)

            if deleted_entries:
                if len(deleted_entries) == 1:
                    action_name, description = "Удаление", f"Удален термин: {deleted_entries[0].get('original', '[пустой]')}"
                else:
                    action_name, description = "Массовое удаление", f"Удалено строк: {len(deleted_entries)}"
                self.add_history('atomic', {'change_type': 'delete', 'entries': deleted_entries, 'action_name': action_name, 'description': description})
                
                self._invalidate_analysis_for_terms(terms_to_invalidate)
                self._update_analysis_widgets()
            
            # Просто перезагружаем текущую страницу. _load_current_page сама скорректирует номер, если страница опустеет.
            self._load_current_page()
    
    
    @pyqtSlot(int)
    def _on_header_clicked(self, logical_index):
        """
        Ручная обработка клика по заголовку.
        Переключает сортировку в БД, не трогая локальные виджеты.
        """
        # Игнорируем колонки с кнопками (3 и 4)
        if logical_index > 2:
            return

        # Логика переключения:
        # Если кликнули по той же колонке -> меняем порядок (ASC/DESC).
        # Если по новой -> ставим ASC.
        if logical_index == self.sort_column_index:
            self.sort_order = Qt.SortOrder.DescendingOrder if self.sort_order == Qt.SortOrder.AscendingOrder else Qt.SortOrder.AscendingOrder
        else:
            self.sort_column_index = logical_index
            self.sort_order = Qt.SortOrder.AscendingOrder
            self.sort_criterion = 'value' # Сбрасываем критерий "по длине" на обычный при клике

        # Обновляем визуальную стрелочку в заголовке
        self.table.horizontalHeader().setSortIndicator(self.sort_column_index, self.sort_order)

        # Сбрасываем "спец-стиль" кнопки, так как мы вернулись к обычной сортировке
        self.sort_button.setText("⇅ Сортировка")
        self.sort_button.setStyleSheet("")

        # Перезагружаем данные (это вызовет SQL запрос с новым ORDER BY)
        self.current_page = 0
        self._commit_current_visual_order_to_db()
        self._load_current_page()
    
    def _open_sort_dialog(self):
        """Открывает диалог настройки сортировки."""
        dialog = GlossarySortDialog(
            self.sort_column_index, 
            self.sort_order, 
            self.sort_criterion, 
            parent=self
        )
        # Добавляем опцию в комбобокс диалога на лету, если его класс позволяет, 
        # но проще обновить сам класс GlossarySortDialog ниже.
        
        if dialog.exec():
            col_idx, order, criterion = dialog.get_state()
            self.sort_column_index = col_idx
            self.sort_order = order
            self.sort_criterion = criterion
            
            # Визуальный индикатор (только для текстовых колонок)
            if col_idx < 3:
                self.table.horizontalHeader().setSortIndicator(self.sort_column_index, self.sort_order)
            
            if criterion == 'length':
                self.sort_button.setText("📏 Сортировка: Длина")
            elif col_idx == 3:
                self.sort_button.setText("📅 Сортировка: Дата")
            else:
                self.sort_button.setText("⇅ Сортировка")
            
            self.current_page = 0
            self._commit_current_visual_order_to_db()
            self._load_current_page()
            
    def _commit_current_visual_order_to_db(self):
        """
        Перестраивает поле 'sequence' в БД, чтобы оно соответствовало
        текущему визуальному порядку, заданному сортировкой.
        Это сохраняет порядок сортировки между сессиями.
        """
        print("DEBUG: Committing visual sort order to 'sequence' column in DB...")
        sort_clause = self._get_sort_clause()
        conn = self._get_db_conn()
        
        with conn:
            # 1. Получаем все ID в новом отсортированном порядке
            cursor = conn.execute(f"SELECT id FROM glossary_editor_state {sort_clause}")
            all_sorted_ids = [row['id'] for row in cursor.fetchall()]
            
            # 2. Готовим данные для массового обновления
            update_data = [(i, db_id) for i, db_id in enumerate(all_sorted_ids)]
            
            # 3. Обновляем sequence одним запросом
            conn.executemany("UPDATE glossary_editor_state SET sequence=? WHERE id=?", update_data)
        print("DEBUG: Commit finished.")
            
            
    def _full_redraw_from_db(self):
        """Отображает плейсхолдеры, если база данных пуста."""
        print("DEBUG: DB is empty, showing placeholders.")
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.clearContents()
        
        data_to_show = [
            ('Пример: Son Goku', 'Пример: Сон Гоку', 'Это пример. Загрузите свой файл.'),
            ('Пример: Kamehameha', 'Пример: Камехамеха', 'Нажмите "Открыть глоссарии…".')
        ]
        self.table.setRowCount(len(data_to_show))
        for i, row_data in enumerate(data_to_show):
            original, rus, note = row_data
            items = [QTableWidgetItem(str(val)) for val in [original, rus, note]]
            for col, item in enumerate(items):
                font = QFont(); font.setItalic(True)
                item.setFont(font); item.setForeground(Qt.GlobalColor.gray)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(i, col, item)
        
        self.table.setSortingEnabled(True)
        self.table.blockSignals(False)
        self.undo_button.setEnabled(len(self.history) > 0)
        if self.launch_mode != 'child':
            self.save_button.setEnabled(False) # Нельзя сохранить плейсхолдеры
        
        self._update_project_save_controls()
    
    def on_main_table_item_changed(self, item: QTableWidgetItem):
        if not self.table.signalsBlocked():
            self._update_db_from_item(item)
    
    def _update_db_from_item(self, item: QTableWidgetItem):
        row, col = item.row(), item.column()
        id_item = self.table.item(row, 0)
        if not id_item: return
        db_id = id_item.data(self.DB_ID_ROLE)
        if db_id is None: return

        conn = self._get_db_conn()
        with conn:
            cursor = conn.execute("SELECT * FROM glossary_editor_state WHERE id=?", (db_id,))
            old_row_data = cursor.fetchone()
            if not old_row_data: return
        old_entry = dict(old_row_data)

        keys = ['original', 'rus', 'note']
        if col >= len(keys): return
        key_to_change = keys[col]
        new_value = item.text().strip()
        
        if old_entry.get(key_to_change) != new_value:
            new_entry = old_entry.copy()
            new_entry[key_to_change] = new_value

            with conn:
                # Обновляем только текстовое поле, timestamp не трогаем
                conn.execute(f"UPDATE glossary_editor_state SET {key_to_change}=? WHERE id=?", (new_value, db_id))
            
            self.add_history('atomic', {
                'change_type': 'edit', 'old_entry': old_entry, 'action_name': 'Правка',
                'description': f"Изменено поле '{key_to_change}' для '{old_entry['original']}'"
            })
            
            self._run_full_analysis(changed_entries=[{'before': old_entry, 'after': new_entry}])
            self.is_analysis_dirty = True
            self._update_analysis_widgets()
            
            # Обновляем высоту строки
            QtCore.QTimer.singleShot(0, lambda: self.table.resizeRowToContents(row))

    def _finish_generating_notes(self):
        try:
            if not hasattr(self, 'new_state_from_work'): return
            changed_terms = self.changed_terms_from_work
            if changed_terms:
                old_state = self.get_glossary()
                history_data = {
                    'action_name': "Массовая генерация",
                    'description': f"Сгенерировано/обновлено {len(changed_terms)} примечаний.",
                    'old_state': old_state
                }
                self.add_history('wholesale', history_data)
                self.set_glossary(self.new_state_from_work)
            else: 
                QMessageBox.information(self, "Нет изменений", "Не найдено терминов для генерации примечаний.")
        finally: 
            self._close_wait_dialog()
    
    def _deduplicate_and_merge(self, entries):
        # --- ШАГ 0: Нормализация входных данных (rus/translation) ---
        normalized_entries = []
        for d in entries:
            new_d = d.copy()
            # Если нет rus, ищем translation
            if not new_d.get('rus') and new_d.get('translation'):
                new_d['rus'] = new_d['translation']
            
            # Гарантируем строковые значения
            new_d['original'] = str(new_d.get('original', '') or '')
            new_d['rus'] = str(new_d.get('rus', '') or '')
            new_d['note'] = str(new_d.get('note', '') or '')
            
            if any([new_d['original'].strip(), new_d['rus'].strip(), new_d['note'].strip()]):
                normalized_entries.append(new_d)
        # -------------------------------------------------------------

        unique_entries = list({tuple(d[k].strip() for k in ['original', 'rus', 'note']): d for d in normalized_entries}.values())
        term_map = defaultdict(list)
        for entry in unique_entries:
            original = entry.get('original', '').strip()
            if original: term_map[original].append(entry)
        
        merged_by_original = []
        for term, group in term_map.items():
            if len(group) > 1:
                non_empty_translations = {e.get('rus', '').strip() for e in group if e.get('rus', '').strip()}
                non_empty_notes = {e.get('note', '').strip() for e in group if e.get('note', '').strip()}
                if len(non_empty_translations) <= 1 and len(non_empty_notes) <= 1:
                    merged_by_original.append({'original': term, 'rus': next(iter(non_empty_translations), ""), 'note': next(iter(non_empty_notes), "")})
                else: merged_by_original.extend(group)
            else: merged_by_original.extend(group)
        
        orphans = [e for e in unique_entries if not e.get('original', '').strip()]
        unlinked_orphans, complete_by_trans = [], defaultdict(list)
        for entry in merged_by_original:
            if t:=entry.get('rus', '').strip(): complete_by_trans[t].append(entry)

        for orphan in orphans:
            orphan_trans, orphan_note = orphan.get('rus', '').strip(), orphan.get('note', '').strip()
            if not orphan_trans or not orphan_note:
                unlinked_orphans.append(orphan); continue
            
            candidates = complete_by_trans.get(orphan_trans, [])
            perfect_matches = [c for c in candidates if not c.get('note', '').strip()]
            
            if len(candidates) == 1 and len(perfect_matches) == 1:
                perfect_matches[0]['note'] = orphan_note
            else: unlinked_orphans.append(orphan)
        
        return merged_by_original + unlinked_orphans

    def _process_imported_data(self, imported_entries, file_count, mode='replace'):
        # 1. Сначала чистим сами импортируемые данные от внутренних дубликатов
        processed_entries = self._deduplicate_and_merge(imported_entries)
        if not processed_entries: 
            QMessageBox.information(self, "Нет данных для импорта", "После обработки в файлах не осталось уникальных данных.")
            return
    
        self.wait_dialog = self._create_wait_dialog("Идет обработка терминов…"); 
        self.wait_dialog.show(); 
        QApplication.processEvents()
        
        def do_work():
            try:
                old_state = self.get_glossary()
                new_state = []
                action_name = "Импорт"
                desc = ""

                if mode == 'replace':
                    # ПОЛНАЯ ЗАМЕНА
                    new_state = processed_entries
                    desc = f"Замена. Загружено {len(new_state)} терминов."
                    action_name = "Импорт (Замена)"

                elif mode == 'merge':
                    # СЛИЯНИЕ (Умное обновление)
                    # Объединяем и прогоняем через дедупликатор, который склеит дубликаты
                    combined = old_state + processed_entries
                    new_state = self._deduplicate_and_merge(combined)
                    diff = len(new_state) - len(old_state)
                    desc = f"Слияние. Итого: {len(new_state)}. Изменение размера: {diff:+d}."
                    action_name = "Импорт (Слияние)"

                elif mode == 'accumulate':
                    # НАКОПЛЕНИЕ (Все подряд)
                    # Просто добавляем новые записи в конец, даже если такие оригиналы уже есть
                    new_state = old_state + processed_entries
                    desc = f"Накопление. Добавлено {len(processed_entries)} записей (возможны дубликаты)."
                    action_name = "Импорт (Накопление)"

                elif mode == 'supplement':
                    # ДОПОЛНЕНИЕ (Только новые)
                    # Добавляем только те, чьих оригиналов еще нет в базе
                    existing_originals = {e.get('original') for e in old_state if e.get('original')}
                    
                    unique_new = [
                        e for e in processed_entries 
                        if e.get('original') not in existing_originals
                    ]
                    
                    new_state = old_state + unique_new
                    skipped = len(processed_entries) - len(unique_new)
                    desc = f"Дополнение. Добавлено {len(unique_new)} новых. Пропущено {skipped} существующих."
                    action_name = "Импорт (Дополнение)"
                
                self.add_history('wholesale', {
                    'action_name': action_name,
                    'description': desc,
                    'old_state': old_state,
                })
                self.set_glossary(new_state)
            finally: 
                self._close_wait_dialog()
        QtCore.QTimer.singleShot(0, do_work)



    def save_glossary(self):
        if self._is_glossary_empty(): return
        self.table.setCurrentItem(None)
        dialog = QDialog(self); dialog.setWindowTitle("Выберите формат сохранения"); layout = QVBoxLayout(dialog)
        self.save_format_choice = None
        def set_fmt(fmt): self.save_format_choice = fmt; dialog.accept()
        
        actions = {"Проектный файл JSON (все данные, […] )": "full_json_project", "Словарь JSON (для перевода, {…} )": "full_json_dictionary", "Простой JSON (Оригинал -> Перевод)": "simple_json", "Контекст TXT (Перевод - Примечание)": "context_txt", "Простой TXT (Оригинал = Перевод)": "simple_txt"}
        for text, fmt in actions.items():
            btn = QPushButton(text); btn.clicked.connect(lambda ch, f=fmt: set_fmt(f)); layout.addWidget(btn)
        if not dialog.exec(): return
        
        filters = {"full_json_project": "JSON Project File (*.json)", "full_json_dictionary": "JSON Dictionary (*.json)", "simple_json": "Simple JSON Glossary (*.json)", "context_txt": "Text File (*.txt)", "simple_txt": "Text File (*.txt)"}
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить глоссарий", f"glossary.{'txt' if 'txt' in self.save_format_choice else 'json'}", filters[self.save_format_choice])
        if not path: return
        
        try:
            glossary_to_save = self.get_glossary()
            with open(path, 'w', encoding='utf-8') as f:
                if self.save_format_choice == "full_json_project":
                    json.dump(glossary_to_save, f, ensure_ascii=False, indent=2)
                elif self.save_format_choice == "full_json_dictionary":
                    json.dump({item['original']: {'rus': item.get('rus', ''), 'note': item.get('note', '')} for item in glossary_to_save if item.get('original')}, f, ensure_ascii=False, indent=2, sort_keys=True)
                elif self.save_format_choice == "simple_json": 
                    json.dump({item['original']: item.get('rus', '') for item in glossary_to_save if item.get('original')}, f, ensure_ascii=False, indent=2, sort_keys=True)
                elif self.save_format_choice == "context_txt": 
                    f.write("\n".join(sorted([f"{item.get('rus', '')} - {item.get('note', '')}" for item in glossary_to_save if item.get('rus') and item.get('note')])))
                elif self.save_format_choice == "simple_txt": 
                    f.write("\n".join(sorted([f"{item['original']} = {item.get('rus', '')}" for item in glossary_to_save if item.get('original') and item.get('rus')])))
            QMessageBox.information(self, "Успех", "Глоссарий успешно сохранен.")
        except Exception as e: QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить файл: {e}")
    
    
    def run_importer_on_current_data(self):
        self.table.setCurrentItem(None)
        current_glossary = self.get_glossary()
        if not current_glossary:
            return QMessageBox.warning(self, "Нет данных", "В глоссарии нет данных для обработки.")
        
        table_data = [[entry.get(k, '') for k in ['original', 'rus', 'note']] for entry in current_glossary]
            
        wizard = ImporterWizardDialog(initial_data=table_data, is_from_table=True, parent=self)
        
        if wizard.exec() == QDialog.DialogCode.Accepted:
            new_glossary = wizard.get_glossary()
            if new_glossary:
                self.add_history('wholesale', {'action_name': "Мастер импорта", 'description': f"Данные пересобраны ({len(new_glossary)} записей).", 'old_state': current_glossary})
                self.set_glossary(new_glossary)
    

    def analyze_and_update_ui(self, structural_patch=None):
        # Этот метод больше не управляет перерисовкой. Он только запускает анализ.
        patch_type = structural_patch.get('type') if structural_patch else None
        if patch_type == 'run_full_analysis':
            self._run_full_analysis(force=True)
        else:
            # Для атомарных изменений (add/delete) просто обновляем виджеты анализа
            self._apply_all_highlights()
            self._update_analysis_widgets()
    

    def _run_full_analysis(self, force=False, changed_entries: list[dict] | None = None):
        """
        Умный анализатор v4.0 (Истинная инкрементальность).
        """
        if not force and not self.is_analysis_dirty and not changed_entries:
            return
            
        current_glossary = self.get_glossary()
        if not current_glossary:
            self.is_analysis_dirty = False
            self.direct_conflicts, self.reverse_issues, self.overlap_groups, self.core_term_candidates = {}, {}, {}, {}
            self._update_analysis_widgets()
            return

        # --- ИНКРЕМЕНТАЛЬНОЕ ОБНОВЛЕНИЕ ---
        if changed_entries and not force:
            print(f"DEBUG: Running TRUE incremental analysis for {len(changed_entries)} entries.")
            
            originals_before = {entry['before'].get('original', '') for entry in changed_entries if entry.get('before')}
            originals_after = {entry['after'].get('original', '') for entry in changed_entries if entry.get('after')}
            affected_originals = originals_before.union(originals_after)

            # --- Проактивная чистка ---
            auto_patch, resolved_terms = self._find_and_resolve_simple_conflicts_for_terms(affected_originals, current_glossary)
            if auto_patch:
                print(f"DEBUG: Proactively auto-resolving {len(resolved_terms)} simple note conflicts...")
                # Используем старое состояние для истории, т.к. это первое действие
                self._apply_patch_and_log_history(auto_patch, "Авто-разрешение (примечания)", current_glossary)
                
                # ВАЖНО: Обновляем и глоссарий, и список затронутых терминов
                current_glossary = self.get_glossary()
                affected_originals -= resolved_terms
                # Также нужно обновить `originals_before`, чтобы `_invalidate_analysis_for_terms` не работал с уже удаленными данными
                originals_before -= resolved_terms

            # --- Продолжение вашей инкрементальной логики ---
            translations_before = {entry['before'].get('rus', '') for entry in changed_entries if entry.get('before')}
            translations_after = {entry['after'].get('rus', '') for entry in changed_entries if entry.get('after')}
            
            originals_structurally_changed = any(
                (e.get('before') or {}).get('original') != (e.get('after') or {}).get('original')
                for e in changed_entries
            )

            self._invalidate_analysis_for_terms(originals_before)

            # Пересчитываем конфликты для ОСТАВШИХСЯ затронутых терминов
            for original in affected_originals:
                if not original: continue
                related = [e for e in current_glossary if e.get('original') == original]
                if len({(e.get('rus', ''), e.get('note', '')) for e in related}) > 1:
                    self.direct_conflicts[original] = [{'rus': e.get('rus', ''), 'note': e.get('note', '')} for e in related]

            affected_translations = translations_before.union(translations_after)
            for trans in affected_translations:
                if not trans: continue
                related = [e for e in current_glossary if e.get('rus') == trans]
                if len([e for e in related if e.get('original')]) > 1 or ([e for e in related if e.get('original')] and [e for e in related if not e.get('original')]):
                    self.reverse_issues[trans] = {'complete': [e for e in related if e.get('original')], 'orphans': [e for e in related if not e.get('original')]}
                elif trans in self.reverse_issues:
                    del self.reverse_issues[trans]

            if originals_structurally_changed:
                print("DEBUG: Structural changes detected, incrementally updating overlaps and patterns...")
                all_originals_set = {e.get('original', '') for e in current_glossary if e.get('original')}
                for term in affected_originals:
                    if not term: continue
                    parents = [other for other in all_originals_set if term != other and term in other]
                    if parents: self.overlap_groups[term] = parents
                    else: self.overlap_groups.pop(term, None) # Удаляем, если больше нет
                    children = [other for other in all_originals_set if term != other and other in term]
                    if children: self.inverted_overlaps[term] = children
                    else: self.inverted_overlaps.pop(term, None) # Удаляем, если больше нет

                self.core_term_candidates = self.logic.analyze_patterns_for_ui(current_glossary, min_group_size=2)

        # --- ПОЛНЫЙ АНАЛИЗ ---
        else:
            print("DEBUG: Running full, structural analysis on DB data…")
            self.add_history('wholesale', {'action_name': "Анализ", 'description': "Выполнен полный анализ глоссария.", 'old_state': current_glossary})
            _, self.direct_conflicts = self.logic.find_direct_conflicts(current_glossary)
            self.reverse_issues = self.logic.find_reverse_issues(current_glossary)
            
            # --- ИНТЕГРАЦИЯ В ПОЛНЫЙ БЛОК ---
            # Собираем все термины, у которых есть и прямой, и обратный конфликт
            terms_to_check = set(self.direct_conflicts.keys()).intersection(
                term for issue in self.reverse_issues.values() for entry in issue.get('complete', []) if (term := entry.get('original'))
            )
            auto_patch, resolved_terms = self._find_and_resolve_simple_conflicts_for_terms(terms_to_check, current_glossary)
            
            if auto_patch:
                print(f"DEBUG: Auto-resolving {len(resolved_terms)} simple note conflicts during full scan...")
                self._apply_patch_and_log_history(auto_patch, "Авто-разрешение (примечания)", current_glossary)
                
                current_glossary = self.get_glossary()
                for term in resolved_terms:
                    if term in self.direct_conflicts: del self.direct_conflicts[term]
                self.reverse_issues = self.logic.find_reverse_issues(current_glossary)
            # --- КОНЕЦ ИНТЕГРАЦИИ ---
            
            # Остальная часть полного анализа
            self.overlap_groups, self.inverted_overlaps = self.logic.find_overlap_groups(current_glossary)
            self.core_term_candidates = self.logic.analyze_patterns_for_ui(current_glossary, min_group_size=2)

        # --- ОБЩИЙ ФИНАЛЬНЫЙ ШАГ ---
        self.untranslated_residue = self.logic.find_untranslated_residue(current_glossary)
        self._rebuild_conflict_maps()
        self.is_analysis_dirty = False
        self._apply_all_highlights()
        self._update_analysis_widgets()
    
    def _rebuild_conflict_maps(self):
        """Перестраивает карты `conflict_map` и `term_to_conflict_keys_map` с нуля."""
        self.conflict_map.clear()
        self.term_to_conflict_keys_map.clear()
    
        for term in self.direct_conflicts:
            self.conflict_map[term].add('direct')
            self.term_to_conflict_keys_map[term]['direct_conflicts'].add(term)
        for trans, issue_data in self.reverse_issues.items():
            for entry in issue_data.get('complete', []):
                if original := entry.get('original'):
                    self.conflict_map[original].add('reverse')
                    self.term_to_conflict_keys_map[original]['reverse_issues'].add(trans)
        for term, sub_terms in self.overlap_groups.items():
            self.conflict_map[term].add('overlap')
            self.term_to_conflict_keys_map[term]['overlap_groups'].add(term)
            for sub_term in sub_terms:
                self.term_to_conflict_keys_map[sub_term]['inverted_overlaps'].add(term)
                
        self.conflicting_term_keys = set(self.conflict_map.keys())
    
    def _create_row_buttons(self, row, item_dict):
        if not isinstance(item_dict, dict): return
        
        # Общий размер для всех кнопок
        BTN_SIZE = QtCore.QSize(30, 26) 

        # ---------------------------------------------------------
        # КОЛОНКА 3: Генерация примечаний + Версионирование
        # ---------------------------------------------------------
        col3_widget = QWidget()
        # ВАЖНО: Запрещаем виджету сжиматься меньше содержимого
        col3_widget.setSizePolicy(QtWidgets.QSizePolicy.Policy.Minimum, QtWidgets.QSizePolicy.Policy.Minimum)
        
        col3_layout = QHBoxLayout(col3_widget)
        col3_layout.setContentsMargins(4, 2, 4, 2)
        col3_layout.setSpacing(4)
        col3_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 1. Кнопка Pymorphy (Генерация)
        if PYMORPHY_AVAILABLE:
            gen_btn = QPushButton("📝")
            gen_btn.setToolTip("Сгенерировать примечание")
            gen_btn.setFixedSize(BTN_SIZE)
            # Фиксируем размер кнопки
            gen_btn.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Fixed)
            gen_btn.clicked.connect(lambda ch, r=row: self._on_generate_note_in_main_table_clicked(r))
            col3_layout.addWidget(gen_btn)
        
        # 2. Кнопка Версии
        if self.associated_project_path and self.associated_epub_path:
            version_btn = QPushButton("🔀")
            version_btn.setFixedSize(BTN_SIZE)
            version_btn.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Fixed)
            
            term = item_dict.get('original', '')
            has_versions = self._check_if_term_has_versions(term)
            
            if has_versions:
                version_btn.setStyleSheet("background-color: #9B59B6; color: white; font-weight: bold;")
                version_btn.setToolTip("Управление версиями (ЕСТЬ АКТИВНЫЕ ПРАВИЛА)")
            else:
                version_btn.setToolTip("Создать версии (переопределения для глав)")
                
            version_btn.clicked.connect(lambda ch, d=item_dict: self._open_versioning_dialog(d))
            col3_layout.addWidget(version_btn)

        self.table.setCellWidget(row, 3, col3_widget)

        # ---------------------------------------------------------
        # КОЛОНКА 4: Удаление
        # ---------------------------------------------------------
        col4_widget = QWidget()
        col4_widget.setSizePolicy(QtWidgets.QSizePolicy.Policy.Minimum, QtWidgets.QSizePolicy.Policy.Minimum)
        
        col4_layout = QHBoxLayout(col4_widget)
        col4_layout.setContentsMargins(4, 2, 4, 2)
        col4_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        delete_btn = QPushButton(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon), "")
        delete_btn.setToolTip(f"Удалить термин '{item_dict.get('original','')}'")
        delete_btn.setFixedSize(BTN_SIZE)
        delete_btn.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Fixed)
        
        db_id = item_dict.get('id')
        if db_id:
            delete_btn.clicked.connect(lambda ch, current_id=db_id: self._remove_single_term_by_id(current_id))
        
        col4_layout.addWidget(delete_btn)
        
        self.table.setCellWidget(row, 4, col4_widget)
        
    def _check_if_term_has_versions(self, term):
        """Быстрая проверка наличия версий в файле JSON без полной загрузки."""
        if not self.associated_project_path: return False
        v_file = os.path.join(self.associated_project_path, "glossary_versions.json")
        if not os.path.exists(v_file): return False
        
        # Для оптимизации можно кэшировать этот файл при старте диалога,
        # но для UI кнопок проще читать (файл обычно небольшой).
        try:
            with open(v_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return term in data
        except:
            return False

    def _open_versioning_dialog(self, item_dict):
        """Открывает диалог версионирования."""
        term = item_dict.get('original')
        if not term: return

        # Создаем временный project_manager для передачи путей
        # (или используем существующий, если он есть в MainWindow)
        from gemini_translator.utils.project_manager import TranslationProjectManager
        pm = TranslationProjectManager(self.associated_project_path)

        dlg = TermVersioningDialog(
            term=term,
            base_data={'rus': item_dict.get('rus', ''), 'note': item_dict.get('note', '')},
            project_manager=pm,
            epub_path=self.associated_epub_path,
            parent=self
        )
        
        dlg.exec()
        
        # После закрытия диалога нужно обновить кнопку (вдруг версии появились/исчезли)
        # Для простоты можно перезагрузить текущую страницу таблицы
        self._load_current_page()


    def _remove_single_term_by_id(self, db_id: str):
        """Находит строку по ID, выделяет ее и вызывает основной обработчик удаления."""
        if not db_id: return
        self.table.clearSelection()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.data(self.DB_ID_ROLE) == db_id:
                self.table.selectRow(row)
                # Вызываем наш надежный метод для удаления выделенных строк
                self._remove_selected_terms()
                return
    
    def _is_glossary_empty(self) -> bool:
        """Быстро проверяет, пуста ли таблица глоссария."""
        conn = self._get_db_conn()
        with conn:
            cursor = conn.execute("SELECT 1 FROM glossary_editor_state LIMIT 1")
            return cursor.fetchone() is None
        
    def _try_parse_standard_json_file(self, path):
        try:
            with open(path, 'r', encoding='utf-8') as f: content = f.read()
            if not content.strip(): return []
            
            data = None
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                stripped_content = content.strip()
                repaired_content = None
                if stripped_content.startswith('{') and stripped_content.endswith('}'):
                    repaired_content = '[' + stripped_content[1:-1] + ']'
                elif stripped_content.startswith('[') and stripped_content.endswith(']'):
                    repaired_content = '{' + stripped_content[1:-1] + '}'
                if repaired_content:
                    try:
                        data = json.loads(repaired_content)
                    except json.JSONDecodeError:
                        data = None

            if data is None:
                return None

            # Проверяем, является ли распознанный JSON стандартным
            # Случай 1: Список словарей
            if isinstance(data, list) and data and isinstance(data[0], dict):
                first_item_keys = data[0].keys()
                # ФОЛБЭК: Принимаем и 'rus', и 'translation'
                has_trans = 'rus' in first_item_keys or 'translation' in first_item_keys
                if 'original' in first_item_keys and has_trans:
                    return data
            
            # Случай 2: Словарь словарей
            elif isinstance(data, dict) and data:
                first_key = next(iter(data), None)
                if first_key and isinstance(data[first_key], dict):
                    first_item_keys = data[first_key].keys()
                    # ФОЛБЭК: Принимаем и 'rus', и 'translation'
                    has_trans = 'rus' in first_item_keys or 'translation' in first_item_keys
                    if has_trans:
                        return [{'original': term, **term_data} for term, term_data in data.items()]
            
            return None
        except (Exception):
            return None
    
    def load_files(self):
        self.table.setCurrentItem(None)
        paths, _ = QFileDialog.getOpenFileNames(self, "Выберите файлы глоссариев", "", 'Все поддерживаемые форматы (*.json *.txt);;JSON файлы (*.json);;Текстовые файлы (*.txt)')
        if not paths: return
        
        imported_entries, files_processed_count = [], 0
        
        # --- Блок чтения файлов (оставлен без изменений, сокращен для краткости в патче) ---
        if len(paths) == 1:
            try:
                with open(paths[0], 'r', encoding='utf-8') as f: content = f.read()
                if content.strip():
                    wizard = ImporterWizardDialog(initial_data=content, parent=self)
                    if wizard.exec() == QDialog.DialogCode.Accepted and (newly_imported := wizard.get_glossary()):
                        imported_entries.extend(newly_imported); files_processed_count = 1
                else: files_processed_count = 1; QMessageBox.information(self, "Файл пуст", f"Выбранный файл пуст:\n{paths[0]}")
            except Exception as e: QMessageBox.critical(self, "Ошибка чтения файла", f"Не удалось прочитать или обработать файл:\n{paths[0]}\n\nОшибка: {e}")
        else:
            pre_processed, to_configure = {}, []
            for path in paths:
                if (data := self._try_parse_standard_json_file(path)) is not None: pre_processed[path] = data
                else: to_configure.append(path)
            if not to_configure:
                for data in pre_processed.values(): imported_entries.extend(data)
                files_processed_count = len(pre_processed)
                QMessageBox.information(self, "Автоматический импорт", f"Все {files_processed_count} файлов были в стандартном формате.")
            else:
                manager = MultiImportManagerDialog(to_configure, pre_processed, self)
                if manager.exec() == QDialog.DialogCode.Accepted:
                    imported_entries, files_processed_count = manager.get_all_imported_entries()
        # ----------------------------------------------------------------------------------
        
        if imported_entries:
            if self._is_glossary_empty(): 
                self._process_imported_data(imported_entries, files_processed_count, 'replace')
            else:
                # --- НОВЫЙ ДИАЛОГ ВЫБОРА РЕЖИМА ---
                dialog = QDialog(self)
                dialog.setWindowTitle("Способ импорта")
                dialog.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint)
                layout = QVBoxLayout(dialog)
                
                layout.addWidget(QLabel("<h3>Выберите режим объединения:</h3>"))
                
                # Функция-фабрика для кнопок
                def add_option(title, desc, mode_key):
                    btn = QPushButton(title)
                    btn.setToolTip(desc)
                    lbl = QLabel(f"<small style='color:grey'>{desc}</small>")
                    lbl.setWordWrap(True)
                    
                    vbox = QVBoxLayout()
                    vbox.setSpacing(2)
                    vbox.addWidget(btn)
                    vbox.addWidget(lbl)
                    
                    layout.addLayout(vbox)
                    layout.addSpacing(5)
                    
                    btn.clicked.connect(lambda: dialog.done(100 + list(modes.keys()).index(mode_key)))
                    return btn

                modes = {
                    'merge': ("🔄 Слияние (Умное)", "Обновит существующие термины и добавит новые. Конфликты объединяются."),
                    'supplement': ("➕ Дополнение (Только новые)", "Добавит ТОЛЬКО те термины, которых еще нет. Старые не тронет."),
                    'accumulate': ("📚 Накопление (Все подряд)", "Просто добавит всё в конец списка. Могут появиться дубликаты."),
                    'replace': ("🗑️ Замена (Стереть всё)", "Удалит текущий глоссарий и загрузит новый файл.")
                }

                for key, (tit, desc) in modes.items():
                    add_option(tit, desc, key)

                cancel_btn = QPushButton("Отмена")
                cancel_btn.clicked.connect(dialog.reject)
                layout.addWidget(cancel_btn)
                
                result_code = dialog.exec()
                
                # Коды возврата: 100=merge, 101=supplement, 102=accumulate, 103=replace
                if result_code >= 100:
                    mode_keys = list(modes.keys())
                    selected_mode = mode_keys[result_code - 100]
                    self._process_imported_data(imported_entries, files_processed_count, selected_mode)

        elif files_processed_count > 0: 
            QMessageBox.information(self, "Нет данных", "Импорт завершен, но не удалось извлечь ни одной записи.")
    
    
    
    def _do_generate_notes_work(self):
        self.new_state_from_work, self.changed_terms_from_work = None, set()
        try:
            current_glossary = self.get_glossary()
            new_state = copy.deepcopy(current_glossary)
            changed_terms = {e['original'] for e in new_state if (self.generation_scope == 'all' or not e.get('note','').strip()) and e.get('rus','').strip() and (new_note := self._generate_note_logic(e['rus'])) and e.get('note','') != new_note and e.update({'note': new_note}) is None}
            self.new_state_from_work = new_state
            self.changed_terms_from_work = changed_terms
        finally: QtCore.QTimer.singleShot(0, self._finish_generating_notes)
    
    def resolve_direct_conflicts(self):
        self.table.setCurrentItem(None)
        if not self.direct_conflicts: return
        current_glossary = self.get_glossary()
        dlg = DirectConflictResolverDialog(self.direct_conflicts, self, morph=morph_analyzer)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            resolved = dlg.resolved_glossary
            if not resolved: return
            
            # --- НАЧАЛО ИСПРАВЛЕНИЯ: Полностью новая логика создания патча ---
            patch_list = []
            
            # Итерируемся по каждому разрешенному конфликту
            for term, new_data in resolved.items():
                
                # 1. Находим ВСЕ старые записи, которые соответствовали этому термину
                old_entries_for_term = [e for e in current_glossary if e.get('original') == term]
                
                # 2. Помечаем КАЖДУЮ из них на удаление
                for old_entry in old_entries_for_term:
                    patch_list.append({'before': old_entry, 'after': None})
                
                # 3. Создаем одну новую, итоговую запись и помечаем ее на добавление
                new_resolved_entry = {'original': term, **new_data}
                patch_list.append({'before': None, 'after': new_resolved_entry})

            # --- КОНЕЦ ИСПРАВЛЕНИЯ ---
            
            self._apply_patch_and_log_history(patch_list, "Прямые конфликты", current_glossary)
    
    def _find_and_resolve_simple_conflicts_for_terms(self, terms_to_check: set, current_glossary: list) -> tuple[list, set]:
        """
        УНИВЕРСАЛЬНЫЙ МЕТОД. Целенаправленно ищет и разрешает простые конфликты
        только для переданного набора терминов.
        Возвращает патч для применения и набор разрешенных терминов.
        """
        if not terms_to_check:
            return [], set()

        patch_list = []
        resolved_terms = set()

        for term in terms_to_check:
            # 1. Находим все записи, относящиеся к проверяемому термину
            conflicting_entries = [e for e in current_glossary if e.get('original') == term]
            
            # 2. Проверяем, есть ли конфликт (больше одной записи)
            if len(conflicting_entries) <= 1:
                continue

            # 3. Убеждаемся, что это ПРОСТОЙ конфликт (все переводы одинаковы)
            if len({e.get('rus', '') for e in conflicting_entries}) == 1:
                # Находим примечание с максимальной длиной
                winner_entry = max(conflicting_entries, key=lambda e: len(e.get('note', '')))
                
                final_entry = {
                    'original': winner_entry.get('original'),
                    'rus': winner_entry.get('rus'),
                    'note': winner_entry.get('note')
                }

                # Создаем патч: удалить все старые, добавить одну новую
                for old_entry in conflicting_entries:
                    patch_list.append({'before': old_entry, 'after': None})
                patch_list.append({'before': None, 'after': final_entry})
                
                resolved_terms.add(term)
        
        return patch_list, resolved_terms
        
    def resolve_reverse_conflicts(self):
        self.table.setCurrentItem(None)
        if not self.reverse_issues: return
        current_glossary = self.get_glossary()
        dlg = ReverseConflictResolverDialog(self.reverse_issues, current_glossary, self, morph=morph_analyzer)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            patch_list = dlg.get_patch()
            self._apply_patch_and_log_history(patch_list, "Обратные конфликты", current_glossary)

    def resolve_overlaps(self):
        self.table.setCurrentItem(None)
        if not self.overlap_groups: return
        current_glossary = self.get_glossary()
        glossary_for_dialog = {e['original']: {k: e.get(k, '') for k in ['rus', 'note']} for e in current_glossary if e.get('original')}
        dlg = ComplexOverlapResolverDialog(self.overlap_groups, self.inverted_overlaps, glossary_for_dialog, PYMORPHY_AVAILABLE, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            patch_list = dlg.get_patch()
            self._apply_patch_and_log_history(patch_list, "Наложения", current_glossary)
    
    def resolve_untranslated_residue(self):
        """Открывает диалог для исправления непереведенных остатков."""
        self.table.setCurrentItem(None)
        if not self.untranslated_residue:
            return
    
        current_glossary = self.get_glossary()
        dialog = ResidueAnalyzerDialog(self.untranslated_residue, current_glossary, self.settings_manager, self)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            patch_list = dialog.get_final_patch()
            self._apply_patch_and_log_history(patch_list, "Анализ остатков", current_glossary)


    def _apply_patch(self, patch_list: list):
        """Атомарно применяет список изменений (патч) к состоянию глоссария в БД."""
        if not patch_list: return

        conn = self._get_db_conn()
        current_now = time.time()
        with conn:
            for change in patch_list:
                before, after = change.get('before'), change.get('after')
                
                if before and not after: # Удаление
                    conn.execute("DELETE FROM glossary_editor_state WHERE original=? AND rus=? AND note=?",
                                 (before['original'], before['rus'], before['note']))
                
                elif not before and after: # Добавление новой записи
                    cursor = conn.execute("SELECT MAX(sequence) FROM glossary_editor_state")
                    max_seq = cursor.fetchone()[0]
                    new_seq = 0 if max_seq is None else max_seq + 1
                    
                    # Если в патче нет таймстампа, считаем это созданием сейчас
                    ts = after.get('timestamp') or current_now
                    
                    conn.execute("""
                        INSERT INTO glossary_editor_state (id, sequence, original, rus, note, timestamp) 
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (str(uuid.uuid4()), new_seq, after['original'], after['rus'], after['note'], ts))
                
                elif before and after: # Обновление существующей записи
                    # При обновлении НЕ меняем timestamp в БД, чтобы сохранить дату создания
                    conn.execute("""
                        UPDATE glossary_editor_state 
                        SET original=?, rus=?, note=? 
                        WHERE original=? AND rus=? AND note=?
                    """, (after['original'], after['rus'], after['note'], 
                          before['original'], before['rus'], before['note']))

        self._load_current_page()
    
    def _apply_patch_and_log_history(self, patch_list: list, action_name: str, old_state_for_history: list):
        """
        Универсальный обработчик. Принимает патч, логирует, применяет к БД
        и запускает инкрементальный анализ.
        """
        if not patch_list:
            return

        # Создаем описание для истории
        deletions = sum(1 for p in patch_list if p.get('before') and not p.get('after'))
        additions = sum(1 for p in patch_list if not p.get('before') and p.get('after'))
        updates = len(patch_list) - deletions - additions
        
        desc_parts = []
        if updates: desc_parts.append(f"Изменено: {updates}")
        if additions: desc_parts.append(f"Добавлено: {additions}")
        if deletions: desc_parts.append(f"Удалено: {deletions}")
        description = f"Применены изменения ({', '.join(desc_parts)})."

        # СНАЧАЛА Применяем (чтобы бекап внутри add_history сохранил уже новые данные)
        self._apply_patch(patch_list)

        # ЗАТЕМ Логируем
        self.add_history('wholesale', {
            'action_name': action_name,
            'description': description,
            'old_state': old_state_for_history
        })
        
        # Анализируем
        changed_entries_for_analysis = [
            {'before': p.get('before'), 'after': p.get('after')} for p in patch_list
        ]
        self._run_full_analysis(changed_entries=changed_entries_for_analysis)
        
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reflow_timer.start()

    def _reflow_analysis_buttons(self):
        visible_buttons = [btn for btn in self.static_analysis_buttons + self.dynamic_analysis_buttons if btn.isVisible()]
        
        while self.analysis_layout.count():
            item = self.analysis_layout.takeAt(0)
            if widget := item.widget():
                widget.setParent(None)

        if not visible_buttons and not self._is_glossary_empty():
            self.analysis_layout.addWidget(self.status_label, 0, 0)
            return

        spacing = self.analysis_layout.spacing()
        buttons_width = sum(btn.sizeHint().width() for btn in visible_buttons) + (len(visible_buttons) - 1) * spacing
        label_metrics = QtGui.QFontMetrics(self.status_label.font())
        label_width = label_metrics.horizontalAdvance(self.status_label.text()) + 15
        available_width = self.analysis_layout.parentWidget().width() - 20

        num_rows = 1
        if buttons_width + label_width > available_width:
            num_rows = 2
            if len(visible_buttons) > 1:
                import math
                split_point = math.ceil(len(visible_buttons) / 2)
                row1_width = sum(btn.sizeHint().width() for btn in visible_buttons[:split_point]) + (split_point - 1) * spacing
                row2_width = sum(btn.sizeHint().width() for btn in visible_buttons[split_point:]) + (len(visible_buttons) - split_point - 1) * spacing
                if max(row1_width, row2_width + label_width) > available_width:
                    num_rows = 3
        
        if num_rows == 1:
            for i, btn in enumerate(visible_buttons): self.analysis_layout.addWidget(btn, 0, i)
            self.analysis_layout.addWidget(self.status_label, 0, len(visible_buttons))
            self.analysis_layout.setColumnStretch(len(visible_buttons) + 1, 1)
        elif num_rows == 2:
            import math
            split_point = math.ceil(len(visible_buttons) / 2)
            for i, btn in enumerate(visible_buttons[:split_point]): self.analysis_layout.addWidget(btn, 0, i)
            for i, btn in enumerate(visible_buttons[split_point:]): self.analysis_layout.addWidget(btn, 1, i)
            self.analysis_layout.addWidget(self.status_label, 1, len(visible_buttons[split_point:]))
            self.analysis_layout.setColumnStretch(max(split_point, len(visible_buttons[split_point:]) + 1), 1)
        else: # num_rows == 3
            import math
            split_point1 = math.ceil(len(visible_buttons) / 3)
            split_point2 = split_point1 + math.ceil((len(visible_buttons) - split_point1) / 2)
            for i, btn in enumerate(visible_buttons[:split_point1]): self.analysis_layout.addWidget(btn, 0, i)
            for i, btn in enumerate(visible_buttons[split_point1:split_point2]): self.analysis_layout.addWidget(btn, 1, i)
            for i, btn in enumerate(visible_buttons[split_point2:]): self.analysis_layout.addWidget(btn, 2, i)
            self.analysis_layout.addWidget(self.status_label, 2, len(visible_buttons[split_point2:]))
            self.analysis_layout.setColumnStretch(max(split_point1, len(visible_buttons[split_point1:split_point2]), len(visible_buttons[split_point2:]) + 1), 1)

    # ---------------------------------------------------------------------------
    # --- СИСТЕМА АВТОБЕКАПА (AUTO-BACKUP) ---
    # ---------------------------------------------------------------------------
    
    def _glossary_state_path(self):
        if self.launch_mode == 'child' or not self.associated_project_path:
            return None
        return os.path.join(self.associated_project_path, "project_glossary_state.json")

    def _save_project_view_state(self):
        state_path = self._glossary_state_path()
        if not state_path:
            return

        state = {}
        try:
            if os.path.exists(state_path):
                with open(state_path, 'r', encoding='utf-8') as f:
                    loaded_state = json.load(f)
                    if isinstance(loaded_state, dict):
                        state = loaded_state
        except Exception:
            state = {}

        state['current_page'] = int(self.current_page)
        try:
            with open(state_path, 'w', encoding='utf-8') as f:
                json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
        except Exception as e:
            print(f"Failed to persist glossary page state: {e}")

    def _restore_project_view_state(self):
        state_path = self._glossary_state_path()
        if not state_path or not os.path.exists(state_path):
            return

        try:
            with open(state_path, 'r', encoding='utf-8') as f:
                state = json.load(f)
            target_page = int((state or {}).get('current_page', 0))
        except Exception:
            return

        self.current_page = max(0, target_page)

    def _save_auto_backup(self):
        """Создает SQLite-дамп текущего состояния глоссария в папке проекта."""
        if self.launch_mode == 'child' or not self.associated_project_path:
            return
            
        backup_path = os.path.join(self.associated_project_path, "glossary_backup.db")
        try:
            source_conn = self._get_db_conn()
            dest_conn = sqlite3.connect(backup_path)
            with source_conn:
                source_conn.backup(dest_conn)
            dest_conn.close()
        except Exception as e:
            print(f"Auto-backup failed: {e}")

    def _check_and_restore_backup(self) -> bool:
        """Проверяет наличие бекапа и предлагает восстановить данные."""
        if self.launch_mode == 'child' or not self.associated_project_path:
            return False
            
        backup_path = os.path.join(self.associated_project_path, "glossary_backup.db")
        if os.path.exists(backup_path):
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("Восстановление глоссария")
            msg_box.setText("Обнаружена резервная копия глоссария с прошлого сеанса (glossary_backup.db).\n\nВы хотите восстановить несохраненные изменения из неё?")
            msg_box.setIcon(QMessageBox.Icon.Question)
            
            btn_yes = msg_box.addButton("Да, восстановить", QMessageBox.ButtonRole.YesRole)
            btn_no = msg_box.addButton("Нет, пропустить", QMessageBox.ButtonRole.NoRole)
            
            msg_box.exec()
            
            if msg_box.clickedButton() == btn_yes:
                try:
                    source_conn = sqlite3.connect(backup_path)
                    dest_conn = self._get_db_conn()
                    with dest_conn:
                        source_conn.backup(dest_conn)
                    source_conn.close()
                    
                    self.status_label.setText("Восстановлено из резервной копии.")
                    self.is_analysis_dirty = True
                    self._run_full_analysis(force=True)
                    self._update_project_save_controls()
                    return True
                except Exception as e:
                    QMessageBox.warning(self, "Ошибка", f"Не удалось восстановить бекап:\n{e}")
        return False

    def _ask_delete_backup(self):
        """Спрашивает пользователя, нужно ли удалить бекап при выходе."""
        if hasattr(self, '_backup_asked') and self._backup_asked:
            return
        self._backup_asked = True
        
        if self.launch_mode != 'child' and self.associated_project_path:
            backup_path = os.path.join(self.associated_project_path, "glossary_backup.db")
            if os.path.exists(backup_path):
                msg_box = QMessageBox(self)
                msg_box.setWindowTitle("Удаление резервной копии")
                msg_box.setText("Удалить временный файл резервной копии (glossary_backup.db)?\n\n(Рекомендуется нажать 'Да', если вы уже сохранили глоссарий, чтобы не засорять папку проекта)")
                msg_box.setIcon(QMessageBox.Icon.Question)
                
                btn_yes = msg_box.addButton("Да, удалить", QMessageBox.ButtonRole.YesRole)
                btn_no = msg_box.addButton("Нет, оставить", QMessageBox.ButtonRole.NoRole)
                
                msg_box.exec()
                
                if msg_box.clickedButton() == btn_yes:
                    try:
                        os.remove(backup_path)
                    except Exception as e:
                        print(f"Failed to remove backup: {e}")

    def _close_via_result(self, accepted: bool):
        self._dialog_result_closing = True
        try:
            if accepted:
                super().accept()
            else:
                super().reject()
        finally:
            self._dialog_result_closing = False

    def accept(self):
        if self.launch_mode == 'dialog' and self.associated_project_path and self._has_unsaved_glossary_changes():
            if not self._save_project_glossary(notify=False):
                return
        self._close_via_result(True)

    def reject(self):
        if self.launch_mode != 'dialog' or not self._has_unsaved_glossary_changes():
            self._close_via_result(False)
            return

        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Несохранённые изменения")
        msg_box.setIcon(QMessageBox.Icon.Question)
        msg_box.setText("Изменения в менеджере глоссария ещё не применены.")

        save_btn = None
        if self.associated_project_path:
            msg_box.setInformativeText("Сохранить их в project_glossary.json перед закрытием?")
            save_btn = msg_box.addButton("Сохранить в проект", QMessageBox.ButtonRole.AcceptRole)
            msg_box.setDefaultButton(save_btn)
        else:
            msg_box.setInformativeText("Применить изменения в текущее окно перед закрытием?")

        apply_btn = msg_box.addButton("Применить без сохранения", QMessageBox.ButtonRole.ActionRole)
        discard_btn = msg_box.addButton("Закрыть без сохранения", QMessageBox.ButtonRole.DestructiveRole)
        cancel_btn = msg_box.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
        if save_btn is None:
            msg_box.setDefaultButton(apply_btn)

        msg_box.exec()
        clicked = msg_box.clickedButton()

        if save_btn is not None and clicked == save_btn:
            if self._save_project_glossary(notify=False):
                self._close_via_result(True)
            return

        if clicked == apply_btn:
            self._close_via_result(True)
            return

        if clicked == discard_btn:
            self._close_via_result(False)
            return

        if clicked == cancel_btn:
            return

    def closeEvent(self, event):
        """Обработка закрытия с выбором: Выход или Меню."""
        if self.launch_mode != 'standalone' and not self._dialog_result_closing:
            event.ignore()
            self.reject()
            return

        if self.launch_mode == 'standalone':
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("Завершение работы")
            msg_box.setText("Вы хотите закрыть приложение или вернуться в главное меню?")
            msg_box.setIcon(QMessageBox.Icon.Question)
            
            btn_menu = msg_box.addButton("Вернуться в меню", QMessageBox.ButtonRole.ActionRole)
            btn_exit = msg_box.addButton("Выйти из программы", QMessageBox.ButtonRole.DestructiveRole)
            btn_cancel = msg_box.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
            
            msg_box.exec()
            clicked = msg_box.clickedButton()
            
            if clicked == btn_cancel:
                event.ignore()
                return
            elif clicked == btn_menu:
                self._ask_delete_backup()
                # Спецкод для main.py
                QApplication.exit(2000) 
                event.accept()
            else:
                self._ask_delete_backup()
                # Обычный выход (код 0)
                event.accept()
        else:
            self._ask_delete_backup()
            # Для диалогового режима просто закрываемся
            event.accept()

    def done(self, r):
        """Перехватывает закрытие окна через accept/reject в диалоговом режиме."""
        self._ask_delete_backup()
        super().done(r)
