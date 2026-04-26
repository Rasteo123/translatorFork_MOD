from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import traceback
from typing import Any

from PyQt6 import QtCore, QtGui, QtWidgets

from ...api import config as api_config
from ...benchmark.runner import BenchmarkRunner
from ...utils.document_importer import DOCUMENT_INPUT_FILTER, extract_document_chapters


class BenchmarkRunWorker(QtCore.QThread):
    progress_changed = QtCore.pyqtSignal(dict)
    finished_ok = QtCore.pyqtSignal(dict)
    failed = QtCore.pyqtSignal(str)

    def __init__(
        self,
        *,
        config_path: str,
        output_dir: str | None,
        prompt_only: bool,
        save_prompts: bool,
        filters: dict[str, set[str]],
        limit: int | None,
        api_keys_by_provider: dict[str, list[str]],
        parent=None,
    ):
        super().__init__(parent)
        self.config_path = config_path
        self.output_dir = output_dir
        self.prompt_only = prompt_only
        self.save_prompts = save_prompts
        self.filters = filters
        self.limit = limit
        self.api_keys_by_provider = api_keys_by_provider

    def run(self):
        try:
            runner = BenchmarkRunner(
                self.config_path,
                output_dir=self.output_dir or None,
                prompt_only=self.prompt_only,
                save_prompts=self.save_prompts,
                filters=self.filters,
                limit=self.limit,
                api_keys_by_provider=self.api_keys_by_provider,
                progress_callback=self.progress_changed.emit,
            )
            report = runner.run()
            self.finished_ok.emit(report)
        except Exception:
            self.failed.emit(traceback.format_exc())


class PromptBenchmarkDialog(QtWidgets.QDialog):
    """PyQt interface for editing and running prompt/model benchmark configs."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Бенчмарк промптов и моделей")
        self.setMinimumSize(1180, 780)
        self.worker: BenchmarkRunWorker | None = None
        self.current_report: dict | None = None
        self.current_output_dir: Path | None = None
        self.settings_manager = self._resolve_settings_manager()
        self.config_data: dict[str, Any] = self._new_config_data()
        self._current_case_row = -1
        self._current_prompt_row = -1
        self._current_model_row = -1
        self._loading_case = False
        self._loading_prompt = False
        self._loading_model = False
        self._build_ui()
        self._load_ui_state()
        self._populate_from_config()
        config_path = self.config_edit.text().strip()
        if config_path and Path(config_path).exists():
            self._load_config_from_path(config_path, show_errors=False)
        self._update_buttons(False)

    def _resolve_settings_manager(self):
        app = QtWidgets.QApplication.instance()
        getter = getattr(app, "get_settings_manager", None)
        if callable(getter):
            try:
                return getter()
            except Exception:
                return None
        return getattr(app, "settings_manager", None)

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(10)

        config_group = QtWidgets.QGroupBox("Конфигурация")
        config_layout = QtWidgets.QGridLayout(config_group)
        self.config_edit = QtWidgets.QLineEdit()
        self.config_edit.setPlaceholderText("benchmarks\\prompt_benchmark.example.json")
        self.browse_config_btn = QtWidgets.QPushButton("Выбрать...")
        self.browse_config_btn.clicked.connect(self._browse_config)
        self.load_config_btn = QtWidgets.QPushButton("Загрузить")
        self.load_config_btn.clicked.connect(self._load_items)
        self.new_config_btn = QtWidgets.QPushButton("Новый")
        self.new_config_btn.clicked.connect(self._new_config)
        self.save_config_btn = QtWidgets.QPushButton("Сохранить")
        self.save_config_btn.clicked.connect(self._save_config)
        self.save_config_as_btn = QtWidgets.QPushButton("Сохранить как...")
        self.save_config_as_btn.clicked.connect(lambda: self._save_config(save_as=True))

        config_layout.addWidget(QtWidgets.QLabel("JSON:"), 0, 0)
        config_layout.addWidget(self.config_edit, 0, 1)
        config_layout.addWidget(self.browse_config_btn, 0, 2)
        config_layout.addWidget(self.load_config_btn, 0, 3)
        config_layout.addWidget(self.new_config_btn, 0, 4)
        config_layout.addWidget(self.save_config_btn, 0, 5)
        config_layout.addWidget(self.save_config_as_btn, 0, 6)

        self.name_edit = QtWidgets.QLineEdit()
        self.output_edit = QtWidgets.QLineEdit()
        self.output_edit.setPlaceholderText("Пусто: будет создана папка в benchmark_results")
        self.browse_output_btn = QtWidgets.QPushButton("Папка...")
        self.browse_output_btn.clicked.connect(self._browse_output)
        config_layout.addWidget(QtWidgets.QLabel("Название:"), 1, 0)
        config_layout.addWidget(self.name_edit, 1, 1, 1, 2)
        config_layout.addWidget(QtWidgets.QLabel("Вывод:"), 1, 3)
        config_layout.addWidget(self.output_edit, 1, 4, 1, 2)
        config_layout.addWidget(self.browse_output_btn, 1, 6)
        root.addWidget(config_group)

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(self._build_run_tab(), "Запуск")
        self.tabs.addTab(self._build_cases_tab(), "Cases")
        self.tabs.addTab(self._build_prompts_tab(), "Prompts")
        self.tabs.addTab(self._build_models_tab(), "Models")
        self.tabs.addTab(self._build_json_tab(), "JSON")
        root.addWidget(self.tabs, 1)

        bottom = QtWidgets.QHBoxLayout()
        self.status_label = QtWidgets.QLabel("Готово.")
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setMaximumWidth(180)
        bottom.addWidget(self.status_label, 1)
        bottom.addWidget(self.progress_bar)

        self.open_summary_btn = QtWidgets.QPushButton("Открыть summary.md")
        self.open_summary_btn.clicked.connect(self._open_summary)
        self.open_json_btn = QtWidgets.QPushButton("Открыть results.json")
        self.open_json_btn.clicked.connect(self._open_results_json)
        self.open_folder_btn = QtWidgets.QPushButton("Открыть папку")
        self.open_folder_btn.clicked.connect(self._open_output_folder)
        self.start_btn = QtWidgets.QPushButton("Запустить")
        self.start_btn.setObjectName("primaryActionButton")
        self.start_btn.clicked.connect(self._start_run)
        self.close_btn = QtWidgets.QPushButton("Закрыть")
        self.close_btn.clicked.connect(self.close)
        bottom.addWidget(self.open_summary_btn)
        bottom.addWidget(self.open_json_btn)
        bottom.addWidget(self.open_folder_btn)
        bottom.addWidget(self.start_btn)
        bottom.addWidget(self.close_btn)
        root.addLayout(bottom)

    def _build_run_tab(self):
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setSpacing(10)

        matrix_group = QtWidgets.QGroupBox("Матрица запуска")
        matrix_layout = QtWidgets.QHBoxLayout(matrix_group)
        self.run_cases_list = QtWidgets.QListWidget()
        self.run_prompts_list = QtWidgets.QListWidget()
        self.run_models_list = QtWidgets.QListWidget()
        matrix_layout.addWidget(self._checklist_panel("Cases", self.run_cases_list))
        matrix_layout.addWidget(self._checklist_panel("Prompts", self.run_prompts_list))
        matrix_layout.addWidget(self._checklist_panel("Models", self.run_models_list))
        layout.addWidget(matrix_group, 2)

        options_group = QtWidgets.QGroupBox("Параметры запуска")
        options_layout = QtWidgets.QGridLayout(options_group)
        self.prompt_only_check = QtWidgets.QCheckBox("Только собрать промпты, без API-запросов")
        self.prompt_only_check.setChecked(True)
        self.save_prompts_check = QtWidgets.QCheckBox("Сохранять собранные промпты")
        self.save_prompts_check.setChecked(True)
        self.limit_spin = QtWidgets.QSpinBox()
        self.limit_spin.setRange(0, 100000)
        self.limit_spin.setSpecialValueText("без лимита")
        self.limit_spin.setValue(0)
        options_layout.addWidget(self.prompt_only_check, 0, 0, 1, 2)
        options_layout.addWidget(self.save_prompts_check, 0, 2, 1, 2)
        options_layout.addWidget(QtWidgets.QLabel("Лимит запусков:"), 0, 4)
        options_layout.addWidget(self.limit_spin, 0, 5)
        layout.addWidget(options_group)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.summary_table = QtWidgets.QTableWidget(0, 8)
        self.summary_table.setHorizontalHeaderLabels(
            ["Prompt", "Model", "Runs", "OK", "Errors", "Avg score", "Avg latency", "Avg input"]
        )
        self.summary_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.summary_table.verticalHeader().setVisible(False)
        self.summary_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.summary_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        splitter.addWidget(self.summary_table)

        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        self.log_view.setFont(QtGui.QFont("Consolas", 9))
        splitter.addWidget(self.log_view)
        splitter.setSizes([260, 170])
        layout.addWidget(splitter, 3)
        return widget

    def _build_cases_tab(self):
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)

        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        self.case_list = QtWidgets.QListWidget()
        self.case_list.currentRowChanged.connect(self._on_case_row_changed)
        left_layout.addWidget(self.case_list, 1)
        case_buttons = QtWidgets.QGridLayout()
        self.add_case_btn = QtWidgets.QPushButton("Добавить")
        self.add_case_btn.clicked.connect(self._add_case)
        self.duplicate_case_btn = QtWidgets.QPushButton("Дублировать")
        self.duplicate_case_btn.clicked.connect(self._duplicate_case)
        self.delete_case_btn = QtWidgets.QPushButton("Удалить")
        self.delete_case_btn.clicked.connect(self._delete_case)
        self.apply_case_btn = QtWidgets.QPushButton("Применить")
        self.apply_case_btn.clicked.connect(self._apply_case_form)
        case_buttons.addWidget(self.add_case_btn, 0, 0)
        case_buttons.addWidget(self.duplicate_case_btn, 0, 1)
        case_buttons.addWidget(self.delete_case_btn, 1, 0)
        case_buttons.addWidget(self.apply_case_btn, 1, 1)
        left_layout.addLayout(case_buttons)
        splitter.addWidget(left)

        form = QtWidgets.QWidget()
        form_layout = QtWidgets.QVBoxLayout(form)
        form_layout.setSpacing(8)

        id_row = QtWidgets.QHBoxLayout()
        self.case_id_edit = QtWidgets.QLineEdit()
        id_row.addWidget(QtWidgets.QLabel("ID:"))
        id_row.addWidget(self.case_id_edit, 1)
        form_layout.addLayout(id_row)

        source_row = QtWidgets.QHBoxLayout()
        self.case_source_path_edit = QtWidgets.QLineEdit()
        self.case_source_path_edit.setPlaceholderText("source_path, если текст хранится в файле")
        browse_source_btn = QtWidgets.QPushButton("Файл...")
        browse_source_btn.clicked.connect(lambda: self._browse_text_path(self.case_source_path_edit))
        load_source_btn = QtWidgets.QPushButton("Прочитать")
        load_source_btn.clicked.connect(lambda: self._load_text_file_into(self.case_source_path_edit, self.case_source_edit))
        source_row.addWidget(QtWidgets.QLabel("Исходник файл:"))
        source_row.addWidget(self.case_source_path_edit, 1)
        source_row.addWidget(browse_source_btn)
        source_row.addWidget(load_source_btn)
        form_layout.addLayout(source_row)

        self.case_source_edit = self._text_edit("Вставьте source_html/source.")
        form_layout.addWidget(QtWidgets.QLabel("Исходный фрагмент:"))
        form_layout.addWidget(self.case_source_edit, 2)

        reference_row = QtWidgets.QHBoxLayout()
        self.case_reference_path_edit = QtWidgets.QLineEdit()
        self.case_reference_path_edit.setPlaceholderText("reference_path, если эталон хранится в файле")
        browse_reference_btn = QtWidgets.QPushButton("Файл...")
        browse_reference_btn.clicked.connect(lambda: self._browse_text_path(self.case_reference_path_edit))
        load_reference_btn = QtWidgets.QPushButton("Прочитать")
        load_reference_btn.clicked.connect(
            lambda: self._load_text_file_into(self.case_reference_path_edit, self.case_reference_edit)
        )
        reference_row.addWidget(QtWidgets.QLabel("Эталон файл:"))
        reference_row.addWidget(self.case_reference_path_edit, 1)
        reference_row.addWidget(browse_reference_btn)
        reference_row.addWidget(load_reference_btn)
        form_layout.addLayout(reference_row)

        self.case_reference_edit = self._text_edit("Необязательный reference.")
        form_layout.addWidget(QtWidgets.QLabel("Эталонный перевод:"))
        form_layout.addWidget(self.case_reference_edit, 1)

        glossary_group = QtWidgets.QGroupBox("Глоссарий case")
        glossary_layout = QtWidgets.QVBoxLayout(glossary_group)
        self.case_glossary_table = QtWidgets.QTableWidget(0, 3)
        self.case_glossary_table.setHorizontalHeaderLabels(["Оригинал", "Перевод", "Заметка"])
        self.case_glossary_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.case_glossary_table.verticalHeader().setVisible(False)
        glossary_layout.addWidget(self.case_glossary_table)
        glossary_buttons = QtWidgets.QHBoxLayout()
        add_glossary_btn = QtWidgets.QPushButton("Добавить термин")
        add_glossary_btn.clicked.connect(self._add_glossary_row)
        remove_glossary_btn = QtWidgets.QPushButton("Удалить термин")
        remove_glossary_btn.clicked.connect(self._remove_glossary_row)
        glossary_buttons.addWidget(add_glossary_btn)
        glossary_buttons.addWidget(remove_glossary_btn)
        glossary_buttons.addStretch(1)
        glossary_layout.addLayout(glossary_buttons)
        form_layout.addWidget(glossary_group, 1)

        checks_group = QtWidgets.QGroupBox("Проверки")
        checks_layout = QtWidgets.QGridLayout(checks_group)
        self.case_required_edit = self._small_text_edit("Одна обязательная строка на строку.")
        self.case_forbidden_edit = self._small_text_edit("Одна запрещённая строка на строку.")
        self.case_placeholders_edit = self._small_text_edit("Плейсхолдеры, которые нельзя потерять.")
        checks_layout.addWidget(QtWidgets.QLabel("Должно быть:"), 0, 0)
        checks_layout.addWidget(self.case_required_edit, 1, 0)
        checks_layout.addWidget(QtWidgets.QLabel("Не должно быть:"), 0, 1)
        checks_layout.addWidget(self.case_forbidden_edit, 1, 1)
        checks_layout.addWidget(QtWidgets.QLabel("Плейсхолдеры:"), 0, 2)
        checks_layout.addWidget(self.case_placeholders_edit, 1, 2)

        self.case_preserve_html_check = QtWidgets.QCheckBox("Сохранять HTML-теги")
        self.case_preserve_html_check.setChecked(True)
        self.case_allow_cjk_check = QtWidgets.QCheckBox("Разрешить CJK в результате")
        self.case_glossary_required_check = QtWidgets.QCheckBox("Требовать термины глоссария")
        self.case_glossary_required_check.setChecked(True)
        self.case_expect_json_check = QtWidgets.QCheckBox("Ожидать валидный JSON")
        self.case_min_similarity_spin = QtWidgets.QDoubleSpinBox()
        self.case_min_similarity_spin.setRange(0.0, 1.0)
        self.case_min_similarity_spin.setSingleStep(0.05)
        self.case_min_similarity_spin.setDecimals(2)
        self.case_min_similarity_spin.setSpecialValueText("не проверять")
        self.case_min_length_spin = QtWidgets.QDoubleSpinBox()
        self.case_min_length_spin.setRange(0.0, 99.0)
        self.case_min_length_spin.setSingleStep(0.05)
        self.case_min_length_spin.setDecimals(2)
        self.case_min_length_spin.setValue(0.25)
        self.case_max_length_spin = QtWidgets.QDoubleSpinBox()
        self.case_max_length_spin.setRange(0.0, 99.0)
        self.case_max_length_spin.setSingleStep(0.1)
        self.case_max_length_spin.setDecimals(2)
        self.case_max_length_spin.setValue(4.0)

        checks_layout.addWidget(self.case_preserve_html_check, 2, 0)
        checks_layout.addWidget(self.case_allow_cjk_check, 2, 1)
        checks_layout.addWidget(self.case_glossary_required_check, 2, 2)
        checks_layout.addWidget(self.case_expect_json_check, 3, 0)
        checks_layout.addWidget(QtWidgets.QLabel("Min similarity:"), 3, 1)
        checks_layout.addWidget(self.case_min_similarity_spin, 3, 2)
        checks_layout.addWidget(QtWidgets.QLabel("Min length ratio:"), 4, 0)
        checks_layout.addWidget(self.case_min_length_spin, 4, 1)
        checks_layout.addWidget(QtWidgets.QLabel("Max length ratio:"), 4, 2)
        checks_layout.addWidget(self.case_max_length_spin, 4, 3)
        form_layout.addWidget(checks_group)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form)
        splitter.addWidget(scroll)
        splitter.setSizes([260, 900])
        return splitter

    def _build_prompts_tab(self):
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)

        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        self.prompt_list = QtWidgets.QListWidget()
        self.prompt_list.currentRowChanged.connect(self._on_prompt_row_changed)
        left_layout.addWidget(self.prompt_list, 1)
        prompt_buttons = QtWidgets.QGridLayout()
        self.add_prompt_btn = QtWidgets.QPushButton("Добавить")
        self.add_prompt_btn.clicked.connect(self._add_prompt)
        self.duplicate_prompt_btn = QtWidgets.QPushButton("Дублировать")
        self.duplicate_prompt_btn.clicked.connect(self._duplicate_prompt)
        self.delete_prompt_btn = QtWidgets.QPushButton("Удалить")
        self.delete_prompt_btn.clicked.connect(self._delete_prompt)
        self.apply_prompt_btn = QtWidgets.QPushButton("Применить")
        self.apply_prompt_btn.clicked.connect(self._apply_prompt_form)
        prompt_buttons.addWidget(self.add_prompt_btn, 0, 0)
        prompt_buttons.addWidget(self.duplicate_prompt_btn, 0, 1)
        prompt_buttons.addWidget(self.delete_prompt_btn, 1, 0)
        prompt_buttons.addWidget(self.apply_prompt_btn, 1, 1)
        left_layout.addLayout(prompt_buttons)
        splitter.addWidget(left)

        form = QtWidgets.QWidget()
        form_layout = QtWidgets.QVBoxLayout(form)
        top_grid = QtWidgets.QGridLayout()
        self.prompt_id_edit = QtWidgets.QLineEdit()
        self.prompt_mode_combo = QtWidgets.QComboBox()
        self.prompt_mode_combo.addItems(["project", "raw"])
        self.prompt_builtin_combo = QtWidgets.QComboBox()
        self.prompt_builtin_combo.addItem("Встроенный default", "default")
        self.prompt_builtin_combo.addItem("Встроенный sequential", "sequential")
        self.prompt_builtin_combo.addItem("Свой шаблон", "")
        self.prompt_builtin_combo.currentIndexChanged.connect(self._on_prompt_builtin_changed)
        self.prompt_use_system_check = QtWidgets.QCheckBox("Использовать system instruction")
        self.prompt_use_system_check.setChecked(True)
        self.prompt_sequential_check = QtWidgets.QCheckBox("Sequential mode")
        top_grid.addWidget(QtWidgets.QLabel("ID:"), 0, 0)
        top_grid.addWidget(self.prompt_id_edit, 0, 1)
        top_grid.addWidget(QtWidgets.QLabel("Mode:"), 0, 2)
        top_grid.addWidget(self.prompt_mode_combo, 0, 3)
        top_grid.addWidget(QtWidgets.QLabel("Шаблон:"), 1, 0)
        top_grid.addWidget(self.prompt_builtin_combo, 1, 1)
        top_grid.addWidget(self.prompt_use_system_check, 1, 2)
        top_grid.addWidget(self.prompt_sequential_check, 1, 3)
        form_layout.addLayout(top_grid)

        path_row = QtWidgets.QHBoxLayout()
        self.prompt_path_edit = QtWidgets.QLineEdit()
        self.prompt_path_edit.setPlaceholderText("path, если шаблон хранится в файле")
        browse_prompt_path_btn = QtWidgets.QPushButton("Файл...")
        browse_prompt_path_btn.clicked.connect(lambda: self._browse_text_path(self.prompt_path_edit))
        load_prompt_path_btn = QtWidgets.QPushButton("Прочитать")
        load_prompt_path_btn.clicked.connect(lambda: self._load_text_file_into(self.prompt_path_edit, self.prompt_template_edit))
        path_row.addWidget(QtWidgets.QLabel("Шаблон файл:"))
        path_row.addWidget(self.prompt_path_edit, 1)
        path_row.addWidget(browse_prompt_path_btn)
        path_row.addWidget(load_prompt_path_btn)
        form_layout.addLayout(path_row)

        self.prompt_template_edit = self._text_edit(
            "Для raw доступны {text}, {glossary}, {format_examples}, {previous_chapter_reference}."
        )
        form_layout.addWidget(QtWidgets.QLabel("Текст промпта:"))
        form_layout.addWidget(self.prompt_template_edit, 3)

        system_path_row = QtWidgets.QHBoxLayout()
        self.prompt_system_path_edit = QtWidgets.QLineEdit()
        self.prompt_system_path_edit.setPlaceholderText("system_instruction_path, если инструкция хранится в файле")
        browse_system_path_btn = QtWidgets.QPushButton("Файл...")
        browse_system_path_btn.clicked.connect(lambda: self._browse_text_path(self.prompt_system_path_edit))
        load_system_path_btn = QtWidgets.QPushButton("Прочитать")
        load_system_path_btn.clicked.connect(
            lambda: self._load_text_file_into(self.prompt_system_path_edit, self.prompt_system_edit)
        )
        system_path_row.addWidget(QtWidgets.QLabel("System файл:"))
        system_path_row.addWidget(self.prompt_system_path_edit, 1)
        system_path_row.addWidget(browse_system_path_btn)
        system_path_row.addWidget(load_system_path_btn)
        form_layout.addLayout(system_path_row)

        self.prompt_system_edit = self._small_text_edit("System instruction для этого prompt.")
        self.prompt_system_edit.setMinimumHeight(90)
        form_layout.addWidget(QtWidgets.QLabel("System instruction:"))
        form_layout.addWidget(self.prompt_system_edit, 1)

        splitter.addWidget(left)
        splitter.addWidget(form)
        splitter.setSizes([260, 900])
        return splitter

    def _build_models_tab(self):
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)

        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        self.model_list = QtWidgets.QListWidget()
        self.model_list.currentRowChanged.connect(self._on_model_row_changed)
        left_layout.addWidget(self.model_list, 1)
        model_buttons = QtWidgets.QGridLayout()
        self.add_model_btn = QtWidgets.QPushButton("Добавить")
        self.add_model_btn.clicked.connect(self._add_model)
        self.duplicate_model_btn = QtWidgets.QPushButton("Дублировать")
        self.duplicate_model_btn.clicked.connect(self._duplicate_model)
        self.delete_model_btn = QtWidgets.QPushButton("Удалить")
        self.delete_model_btn.clicked.connect(self._delete_model)
        self.apply_model_btn = QtWidgets.QPushButton("Применить")
        self.apply_model_btn.clicked.connect(self._apply_model_form)
        model_buttons.addWidget(self.add_model_btn, 0, 0)
        model_buttons.addWidget(self.duplicate_model_btn, 0, 1)
        model_buttons.addWidget(self.delete_model_btn, 1, 0)
        model_buttons.addWidget(self.apply_model_btn, 1, 1)
        left_layout.addLayout(model_buttons)
        splitter.addWidget(left)

        form = QtWidgets.QWidget()
        form_layout = QtWidgets.QVBoxLayout(form)
        grid = QtWidgets.QGridLayout()
        self.model_id_edit = QtWidgets.QLineEdit()
        self.model_provider_combo = QtWidgets.QComboBox()
        self._populate_provider_combo()
        self.model_provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        self.model_name_combo = QtWidgets.QComboBox()
        self.model_name_combo.setEditable(True)
        self.model_name_combo.currentTextChanged.connect(self._update_model_id_hint)
        self.generate_model_id_btn = QtWidgets.QPushButton("Сгенерировать ID")
        self.generate_model_id_btn.clicked.connect(self._generate_model_id)
        self.model_base_url_edit = QtWidgets.QLineEdit()
        self.model_base_url_edit.setPlaceholderText("Для local/OpenAI-compatible, например http://127.0.0.1:11434/v1/chat/completions")
        self.model_api_env_edit = QtWidgets.QLineEdit()
        self.model_api_env_edit.setPlaceholderText("Например GEMINI_API_KEY. Можно оставить пустым для сохранённых ключей.")
        self.model_use_stream_check = QtWidgets.QCheckBox("Stream")
        self.model_use_stream_check.setChecked(True)
        self.model_max_output_spin = QtWidgets.QSpinBox()
        self.model_max_output_spin.setRange(0, 1000000)
        self.model_max_output_spin.setSpecialValueText("по умолчанию")
        self.model_temperature_spin = QtWidgets.QDoubleSpinBox()
        self.model_temperature_spin.setRange(-1.0, 2.0)
        self.model_temperature_spin.setDecimals(2)
        self.model_temperature_spin.setSingleStep(0.05)
        self.model_temperature_spin.setSpecialValueText("по умолчанию")
        self.model_temperature_spin.setValue(-1.0)
        self.model_thinking_check = QtWidgets.QCheckBox("Thinking")
        self.model_thinking_level_combo = QtWidgets.QComboBox()
        self.model_thinking_level_combo.setEditable(True)
        self.model_thinking_level_combo.addItems(["", "minimal", "low", "medium", "high"])
        self.model_debug_check = QtWidgets.QCheckBox("Debug")

        grid.addWidget(QtWidgets.QLabel("Benchmark ID:"), 0, 0)
        grid.addWidget(self.model_id_edit, 0, 1)
        grid.addWidget(self.generate_model_id_btn, 0, 2)
        grid.addWidget(QtWidgets.QLabel("Провайдер:"), 1, 0)
        grid.addWidget(self.model_provider_combo, 1, 1, 1, 2)
        grid.addWidget(QtWidgets.QLabel("Модель:"), 2, 0)
        grid.addWidget(self.model_name_combo, 2, 1, 1, 2)
        grid.addWidget(QtWidgets.QLabel("Base URL:"), 3, 0)
        grid.addWidget(self.model_base_url_edit, 3, 1, 1, 2)
        grid.addWidget(QtWidgets.QLabel("API key env:"), 4, 0)
        grid.addWidget(self.model_api_env_edit, 4, 1, 1, 2)
        grid.addWidget(self.model_use_stream_check, 5, 0)
        grid.addWidget(QtWidgets.QLabel("Max output:"), 5, 1)
        grid.addWidget(self.model_max_output_spin, 5, 2)
        grid.addWidget(QtWidgets.QLabel("Temperature:"), 6, 0)
        grid.addWidget(self.model_temperature_spin, 6, 1)
        grid.addWidget(self.model_thinking_check, 7, 0)
        grid.addWidget(QtWidgets.QLabel("Thinking level:"), 7, 1)
        grid.addWidget(self.model_thinking_level_combo, 7, 2)
        grid.addWidget(self.model_debug_check, 8, 0)
        form_layout.addLayout(grid)
        form_layout.addStretch(1)
        splitter.addWidget(left)
        splitter.addWidget(form)
        splitter.setSizes([260, 900])
        return splitter

    def _build_json_tab(self):
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        controls = QtWidgets.QHBoxLayout()
        refresh_btn = QtWidgets.QPushButton("Обновить предпросмотр")
        refresh_btn.clicked.connect(self._refresh_json_preview)
        controls.addWidget(refresh_btn)
        controls.addStretch(1)
        layout.addLayout(controls)
        self.items_view = QtWidgets.QPlainTextEdit()
        self.items_view.setReadOnly(True)
        self.items_view.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        self.items_view.setFont(QtGui.QFont("Consolas", 9))
        layout.addWidget(self.items_view, 1)
        return widget

    def _checklist_panel(self, title: str, list_widget: QtWidgets.QListWidget):
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        title_row = QtWidgets.QHBoxLayout()
        title_row.addWidget(QtWidgets.QLabel(title))
        all_btn = QtWidgets.QPushButton("Все")
        all_btn.clicked.connect(lambda: self._set_all_checked(list_widget, True))
        none_btn = QtWidgets.QPushButton("Нет")
        none_btn.clicked.connect(lambda: self._set_all_checked(list_widget, False))
        title_row.addWidget(all_btn)
        title_row.addWidget(none_btn)
        layout.addLayout(title_row)
        list_widget.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        layout.addWidget(list_widget, 1)
        return panel

    def _text_edit(self, placeholder: str = ""):
        edit = QtWidgets.QPlainTextEdit()
        edit.setPlaceholderText(placeholder)
        edit.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.WidgetWidth)
        edit.setFont(QtGui.QFont("Consolas", 9))
        return edit

    def _small_text_edit(self, placeholder: str = ""):
        edit = self._text_edit(placeholder)
        edit.setMinimumHeight(72)
        return edit

    def _default_config_path(self) -> str:
        candidate = Path.cwd() / "benchmarks" / "prompt_benchmark.example.json"
        return str(candidate) if candidate.exists() else ""

    def _new_config_data(self) -> dict[str, Any]:
        return {
            "name": "prompt-model-benchmark",
            "output_dir": "benchmark_results",
            "defaults": {
                "use_system_instruction": True,
                "system_instruction": "You are a professional literary translator into Russian.",
                "prompt_mode": "project",
            },
            "prompts": [
                {
                    "id": "project-default",
                    "builtin": "default",
                    "mode": "project",
                }
            ],
            "models": [
                {
                    "id": "local-openai-compatible",
                    "provider": "local",
                    "model": "local-model",
                    "base_url": "http://127.0.0.1:11434/v1/chat/completions",
                    "use_stream": False,
                }
            ],
            "cases": [
                {
                    "id": "sample-case",
                    "source_html": "<p>青云宗的弟子低声说：“师尊来了。”</p><!-- MEDIA_0 -->",
                    "reference": "<p>Ученик Секты Лазурного Облака тихо сказал: «Наставник пришёл».</p><!-- MEDIA_0 -->",
                    "glossary": [
                        {"original": "青云宗", "rus": "Секта Лазурного Облака"},
                        {"original": "师尊", "rus": "наставник"},
                    ],
                    "checks": {
                        "required": ["Секта Лазурного Облака"],
                        "forbidden": ["青云宗"],
                        "placeholders": ["<!-- MEDIA_0 -->"],
                        "preserve_html_tags": True,
                        "min_similarity": 0.25,
                    },
                }
            ],
        }

    def _normalize_config(self, data: dict[str, Any]) -> dict[str, Any]:
        config = deepcopy(data) if isinstance(data, dict) else {}
        config.setdefault("name", "prompt-model-benchmark")
        config.setdefault("output_dir", "benchmark_results")
        config.setdefault("defaults", {})
        for section in ("cases", "prompts", "models"):
            if not isinstance(config.get(section), list):
                config[section] = []
        if not config["cases"]:
            config["cases"] = deepcopy(self._new_config_data()["cases"])
        if not config["prompts"]:
            config["prompts"] = deepcopy(self._new_config_data()["prompts"])
        if not config["models"]:
            config["models"] = deepcopy(self._new_config_data()["models"])
        return config

    def _load_ui_state(self):
        saved = {}
        if self.settings_manager is not None:
            try:
                saved = self.settings_manager.load_settings().get("prompt_benchmark_ui", {}) or {}
            except Exception:
                saved = {}
        self.config_edit.setText(str(saved.get("config_path") or self._default_config_path()))
        self.output_edit.setText(str(saved.get("output_dir") or ""))
        self.prompt_only_check.setChecked(bool(saved.get("prompt_only", True)))
        self.save_prompts_check.setChecked(bool(saved.get("save_prompts", True)))
        self.limit_spin.setValue(int(saved.get("limit", 0) or 0))
        self.tabs.setCurrentIndex(int(saved.get("tab", 0) or 0))

    def _save_ui_state(self):
        if self.settings_manager is None:
            return
        try:
            self.settings_manager.save_ui_state(
                {
                    "prompt_benchmark_ui": {
                        "config_path": self.config_edit.text().strip(),
                        "output_dir": self.output_edit.text().strip(),
                        "prompt_only": self.prompt_only_check.isChecked(),
                        "save_prompts": self.save_prompts_check.isChecked(),
                        "limit": self.limit_spin.value(),
                        "tab": self.tabs.currentIndex(),
                    }
                }
            )
        except Exception:
            pass

    def _populate_from_config(self):
        self.name_edit.setText(str(self.config_data.get("name") or ""))
        if not self.output_edit.text().strip():
            self.output_edit.setText(str(self.config_data.get("output_dir") or ""))
        self._refresh_editor_lists()
        self._refresh_run_lists()
        self._refresh_json_preview()

    def _load_config_from_path(self, config_path: str, show_errors: bool = True) -> bool:
        try:
            path = Path(config_path)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.config_data = self._normalize_config(data)
            self.config_edit.setText(str(path))
            self.output_edit.setText(str(self.config_data.get("output_dir") or ""))
            self._populate_from_config()
            self._append_log(f"Загружен конфиг: {path}")
            return True
        except Exception as exc:
            if show_errors:
                QtWidgets.QMessageBox.warning(self, "Ошибка конфига", f"{type(exc).__name__}: {exc}")
            return False

    def _load_items(self):
        config_path = self.config_edit.text().strip()
        if not config_path:
            QtWidgets.QMessageBox.information(self, "Нет файла", "Выберите benchmark JSON.")
            return
        if not Path(config_path).exists():
            QtWidgets.QMessageBox.warning(self, "Файл не найден", config_path)
            return
        self._load_config_from_path(config_path)

    def _browse_config(self):
        start = self.config_edit.text().strip() or str(Path.cwd() / "benchmarks")
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Выберите benchmark JSON",
            start,
            "Benchmark JSON (*.json);;All files (*)",
        )
        if path:
            self._load_config_from_path(path)

    def _browse_output(self):
        start = self.output_edit.text().strip() or str(Path.cwd() / "benchmark_results")
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Выберите папку отчёта", start)
        if folder:
            self.output_edit.setText(folder)

    def _browse_text_path(self, target_edit: QtWidgets.QLineEdit):
        start = target_edit.text().strip() or str(Path.cwd())
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Выберите файл фрагмента",
            start,
            DOCUMENT_INPUT_FILTER,
        )
        if path:
            target_edit.setText(path)

    def _load_text_file_into(self, path_edit: QtWidgets.QLineEdit, text_edit: QtWidgets.QPlainTextEdit):
        path_text = path_edit.text().strip()
        if not path_text:
            QtWidgets.QMessageBox.information(self, "Нет файла", "Сначала выберите путь к файлу.")
            return
        try:
            path = Path(path_text)
            if path.suffix.lower() in {".docx", ".md", ".markdown", ".html", ".htm", ".xhtml", ".pdf"}:
                result = extract_document_chapters(path)
                text = "\n\n".join(chapter.html for chapter in result.chapters)
            else:
                text = path.read_text(encoding="utf-8")
            text_edit.setPlainText(text)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Не удалось прочитать файл", f"{type(exc).__name__}: {exc}")

    def _new_config(self):
        answer = QtWidgets.QMessageBox.question(
            self,
            "Новый конфиг",
            "Заменить текущий состав новым шаблоном?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self.config_data = self._new_config_data()
        self.config_edit.clear()
        self.output_edit.clear()
        self._populate_from_config()
        self._append_log("Создан новый benchmark-конфиг.")

    def _save_config(self, save_as: bool = False):
        path = self._write_config_file(save_as=save_as)
        if path:
            self.status_label.setText(f"Конфиг сохранён: {path.name}")

    def _write_config_file(self, save_as: bool = False) -> Path | None:
        self._sync_config_from_forms()
        path_text = self.config_edit.text().strip()
        if save_as or not path_text:
            start = path_text or str(Path.cwd() / "benchmarks" / "prompt_benchmark.json")
            path_text, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                "Сохранить benchmark JSON",
                start,
                "Benchmark JSON (*.json);;All files (*)",
            )
            if not path_text:
                return None
        path = Path(path_text)
        if not path.suffix:
            path = path.with_suffix(".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.config_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.config_edit.setText(str(path))
        self._refresh_json_preview()
        self._append_log(f"Конфиг сохранён: {path}")
        return path

    def _ensure_config_file_for_run(self) -> Path | None:
        path_text = self.config_edit.text().strip()
        if not path_text:
            path = Path.cwd() / "benchmarks" / "prompt_benchmark_ui.json"
            self.config_edit.setText(str(path))
        return self._write_config_file(save_as=False)

    def _sync_config_from_forms(self):
        self._save_case_form(self._current_case_row)
        self._save_prompt_form(self._current_prompt_row)
        self._save_model_form(self._current_model_row)
        name = self.name_edit.text().strip()
        self.config_data["name"] = name or "prompt-model-benchmark"
        output_dir = self.output_edit.text().strip()
        if output_dir:
            self.config_data["output_dir"] = output_dir
        else:
            self.config_data.pop("output_dir", None)
        self._refresh_run_lists()

    def _refresh_editor_lists(self):
        self._refresh_case_list()
        self._refresh_prompt_list()
        self._refresh_model_list()

    def _refresh_case_list(self, select_row: int | None = None):
        row = self.case_list.currentRow() if select_row is None else select_row
        self.case_list.blockSignals(True)
        self.case_list.clear()
        for item in self.config_data.get("cases", []):
            self.case_list.addItem(str(item.get("id") or "<без id>"))
        self.case_list.blockSignals(False)
        if self.case_list.count():
            row = min(max(row, 0), self.case_list.count() - 1)
            self.case_list.setCurrentRow(row)
            self._current_case_row = row
            self._load_case_form(row)
        else:
            self._current_case_row = -1
            self._clear_case_form()

    def _refresh_prompt_list(self, select_row: int | None = None):
        row = self.prompt_list.currentRow() if select_row is None else select_row
        self.prompt_list.blockSignals(True)
        self.prompt_list.clear()
        for item in self.config_data.get("prompts", []):
            self.prompt_list.addItem(str(item.get("id") or "<без id>"))
        self.prompt_list.blockSignals(False)
        if self.prompt_list.count():
            row = min(max(row, 0), self.prompt_list.count() - 1)
            self.prompt_list.setCurrentRow(row)
            self._current_prompt_row = row
            self._load_prompt_form(row)
        else:
            self._current_prompt_row = -1
            self._clear_prompt_form()

    def _refresh_model_list(self, select_row: int | None = None):
        row = self.model_list.currentRow() if select_row is None else select_row
        self.model_list.blockSignals(True)
        self.model_list.clear()
        for item in self.config_data.get("models", []):
            self.model_list.addItem(str(item.get("id") or "<без id>"))
        self.model_list.blockSignals(False)
        if self.model_list.count():
            row = min(max(row, 0), self.model_list.count() - 1)
            self.model_list.setCurrentRow(row)
            self._current_model_row = row
            self._load_model_form(row)
        else:
            self._current_model_row = -1
            self._clear_model_form()

    def _refresh_run_lists(self):
        self._set_checklist_items(
            self.run_cases_list,
            [str(item.get("id") or "") for item in self.config_data.get("cases", []) if str(item.get("id") or "")],
        )
        self._set_checklist_items(
            self.run_prompts_list,
            [str(item.get("id") or "") for item in self.config_data.get("prompts", []) if str(item.get("id") or "")],
        )
        self._set_checklist_items(
            self.run_models_list,
            [str(item.get("id") or "") for item in self.config_data.get("models", []) if str(item.get("id") or "")],
        )

    def _set_checklist_items(self, list_widget: QtWidgets.QListWidget, ids: list[str]):
        had_items = list_widget.count() > 0
        checked = {
            list_widget.item(index).text()
            for index in range(list_widget.count())
            if list_widget.item(index).checkState() == QtCore.Qt.CheckState.Checked
        }
        all_were_checked = had_items and len(checked) == list_widget.count()
        list_widget.blockSignals(True)
        list_widget.clear()
        for item_id in ids:
            item = QtWidgets.QListWidgetItem(item_id)
            item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                QtCore.Qt.CheckState.Checked
                if (not had_items or all_were_checked or item_id in checked)
                else QtCore.Qt.CheckState.Unchecked
            )
            list_widget.addItem(item)
        list_widget.blockSignals(False)

    def _set_all_checked(self, list_widget: QtWidgets.QListWidget, checked: bool):
        state = QtCore.Qt.CheckState.Checked if checked else QtCore.Qt.CheckState.Unchecked
        for index in range(list_widget.count()):
            list_widget.item(index).setCheckState(state)

    def _selected_ids(self, list_widget: QtWidgets.QListWidget) -> list[str]:
        return [
            list_widget.item(index).text()
            for index in range(list_widget.count())
            if list_widget.item(index).checkState() == QtCore.Qt.CheckState.Checked
        ]

    def _filters(self) -> dict[str, set[str]] | None:
        sections = [
            ("cases", self.run_cases_list, "case"),
            ("prompts", self.run_prompts_list, "prompt"),
            ("models", self.run_models_list, "model"),
        ]
        filters: dict[str, set[str]] = {}
        for section, widget, label in sections:
            total = widget.count()
            selected = self._selected_ids(widget)
            if total == 0:
                QtWidgets.QMessageBox.warning(self, "Пустой состав", f"В конфиге нет {label}.")
                return None
            if not selected:
                QtWidgets.QMessageBox.warning(self, "Ничего не выбрано", f"Выберите хотя бы один {label} для запуска.")
                return None
            if len(selected) < total:
                filters[section] = set(selected)
        return filters

    def _on_case_row_changed(self, row: int):
        if self._loading_case:
            return
        self._save_case_form(self._current_case_row)
        self._current_case_row = row
        self._load_case_form(row)
        self._refresh_run_lists()

    def _on_prompt_row_changed(self, row: int):
        if self._loading_prompt:
            return
        self._save_prompt_form(self._current_prompt_row)
        self._current_prompt_row = row
        self._load_prompt_form(row)
        self._refresh_run_lists()

    def _on_model_row_changed(self, row: int):
        if self._loading_model:
            return
        self._save_model_form(self._current_model_row)
        self._current_model_row = row
        self._load_model_form(row)
        self._refresh_run_lists()

    def _unique_id(self, section: str, base: str) -> str:
        existing = {str(item.get("id") or "") for item in self.config_data.get(section, [])}
        candidate = base
        suffix = 2
        while candidate in existing:
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    def _add_case(self):
        self._save_case_form(self._current_case_row)
        self.config_data["cases"].append(
            {
                "id": self._unique_id("cases", "case"),
                "source_html": "",
                "checks": {"preserve_html_tags": True, "glossary_required": True},
            }
        )
        self._refresh_case_list(len(self.config_data["cases"]) - 1)
        self._refresh_run_lists()

    def _duplicate_case(self):
        row = self.case_list.currentRow()
        if row < 0:
            return
        self._save_case_form(row)
        item = deepcopy(self.config_data["cases"][row])
        item["id"] = self._unique_id("cases", f"{item.get('id') or 'case'}-copy")
        self.config_data["cases"].insert(row + 1, item)
        self._refresh_case_list(row + 1)
        self._refresh_run_lists()

    def _delete_case(self):
        row = self.case_list.currentRow()
        if row < 0 or len(self.config_data.get("cases", [])) <= 1:
            return
        del self.config_data["cases"][row]
        self._refresh_case_list(min(row, len(self.config_data["cases"]) - 1))
        self._refresh_run_lists()

    def _apply_case_form(self):
        self._save_case_form(self._current_case_row)
        self._refresh_case_list(self._current_case_row)
        self._refresh_run_lists()
        self._refresh_json_preview()

    def _load_case_form(self, row: int):
        self._loading_case = True
        try:
            if row < 0 or row >= len(self.config_data.get("cases", [])):
                self._clear_case_form()
                return
            item = self.config_data["cases"][row]
            checks = item.get("checks") if isinstance(item.get("checks"), dict) else {}
            self.case_id_edit.setText(str(item.get("id") or ""))
            self.case_source_path_edit.setText(str(item.get("source_path") or ""))
            self.case_source_edit.setPlainText(str(item.get("source_html") or item.get("source") or item.get("text") or ""))
            self.case_reference_path_edit.setText(str(item.get("reference_path") or ""))
            self.case_reference_edit.setPlainText(str(item.get("reference") or item.get("reference_html") or ""))
            self._populate_glossary_table(item.get("glossary") or [])
            self.case_required_edit.setPlainText("\n".join(str(value) for value in self._as_list(checks.get("required"))))
            self.case_forbidden_edit.setPlainText("\n".join(str(value) for value in self._as_list(checks.get("forbidden"))))
            self.case_placeholders_edit.setPlainText("\n".join(str(value) for value in self._as_list(checks.get("placeholders"))))
            self.case_preserve_html_check.setChecked(bool(checks.get("preserve_html_tags", True)))
            self.case_allow_cjk_check.setChecked(bool(checks.get("allow_cjk", False)))
            self.case_glossary_required_check.setChecked(bool(checks.get("glossary_required", True)))
            self.case_expect_json_check.setChecked(bool(checks.get("expect_json", False)))
            self.case_min_similarity_spin.setValue(float(checks.get("min_similarity", 0.0) or 0.0))
            self.case_min_length_spin.setValue(float(checks.get("min_length_ratio", 0.25) or 0.25))
            self.case_max_length_spin.setValue(float(checks.get("max_length_ratio", 4.0) or 4.0))
        finally:
            self._loading_case = False

    def _clear_case_form(self):
        self.case_id_edit.clear()
        self.case_source_path_edit.clear()
        self.case_source_edit.clear()
        self.case_reference_path_edit.clear()
        self.case_reference_edit.clear()
        self.case_glossary_table.setRowCount(0)
        self.case_required_edit.clear()
        self.case_forbidden_edit.clear()
        self.case_placeholders_edit.clear()
        self.case_preserve_html_check.setChecked(True)
        self.case_allow_cjk_check.setChecked(False)
        self.case_glossary_required_check.setChecked(True)
        self.case_expect_json_check.setChecked(False)
        self.case_min_similarity_spin.setValue(0.0)
        self.case_min_length_spin.setValue(0.25)
        self.case_max_length_spin.setValue(4.0)

    def _save_case_form(self, row: int):
        if self._loading_case or row < 0 or row >= len(self.config_data.get("cases", [])):
            return
        item: dict[str, Any] = {"id": self.case_id_edit.text().strip() or self._unique_id("cases", "case")}
        source = self.case_source_edit.toPlainText().strip()
        source_path = self.case_source_path_edit.text().strip()
        if source:
            item["source_html"] = source
        elif source_path:
            item["source_path"] = source_path
        reference = self.case_reference_edit.toPlainText().strip()
        reference_path = self.case_reference_path_edit.text().strip()
        if reference:
            item["reference"] = reference
        elif reference_path:
            item["reference_path"] = reference_path
        glossary = self._glossary_from_table()
        if glossary:
            item["glossary"] = glossary
        checks: dict[str, Any] = {
            "preserve_html_tags": self.case_preserve_html_check.isChecked(),
            "allow_cjk": self.case_allow_cjk_check.isChecked(),
            "glossary_required": self.case_glossary_required_check.isChecked(),
            "expect_json": self.case_expect_json_check.isChecked(),
            "min_length_ratio": self.case_min_length_spin.value(),
            "max_length_ratio": self.case_max_length_spin.value(),
        }
        required = self._lines(self.case_required_edit.toPlainText())
        forbidden = self._lines(self.case_forbidden_edit.toPlainText())
        placeholders = self._lines(self.case_placeholders_edit.toPlainText())
        if required:
            checks["required"] = required
        if forbidden:
            checks["forbidden"] = forbidden
        if placeholders:
            checks["placeholders"] = placeholders
        if self.case_min_similarity_spin.value() > 0:
            checks["min_similarity"] = self.case_min_similarity_spin.value()
        item["checks"] = checks
        self.config_data["cases"][row] = item
        current = self.case_list.item(row)
        if current:
            current.setText(item["id"])

    def _populate_glossary_table(self, glossary: list[dict[str, Any]]):
        self.case_glossary_table.setRowCount(0)
        for entry in glossary:
            row = self.case_glossary_table.rowCount()
            self.case_glossary_table.insertRow(row)
            self.case_glossary_table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(entry.get("original") or entry.get("source") or "")))
            self.case_glossary_table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(entry.get("rus") or entry.get("translation") or entry.get("target") or "")))
            self.case_glossary_table.setItem(row, 2, QtWidgets.QTableWidgetItem(str(entry.get("note") or "")))

    def _add_glossary_row(self):
        row = self.case_glossary_table.rowCount()
        self.case_glossary_table.insertRow(row)
        for col in range(3):
            self.case_glossary_table.setItem(row, col, QtWidgets.QTableWidgetItem(""))

    def _remove_glossary_row(self):
        rows = sorted({index.row() for index in self.case_glossary_table.selectedIndexes()}, reverse=True)
        if not rows and self.case_glossary_table.currentRow() >= 0:
            rows = [self.case_glossary_table.currentRow()]
        for row in rows:
            self.case_glossary_table.removeRow(row)

    def _glossary_from_table(self) -> list[dict[str, str]]:
        entries = []
        for row in range(self.case_glossary_table.rowCount()):
            original = self._table_text(self.case_glossary_table, row, 0)
            rus = self._table_text(self.case_glossary_table, row, 1)
            note = self._table_text(self.case_glossary_table, row, 2)
            if not original and not rus and not note:
                continue
            entry = {"original": original, "rus": rus}
            if note:
                entry["note"] = note
            entries.append(entry)
        return entries

    def _add_prompt(self):
        self._save_prompt_form(self._current_prompt_row)
        self.config_data["prompts"].append(
            {
                "id": self._unique_id("prompts", "prompt"),
                "mode": "raw",
                "template": "Translate into literary Russian. Preserve HTML and placeholders. Return only HTML.\n\nGlossary:\n{glossary}\n\nSource:\n{text}",
            }
        )
        self._refresh_prompt_list(len(self.config_data["prompts"]) - 1)
        self._refresh_run_lists()

    def _duplicate_prompt(self):
        row = self.prompt_list.currentRow()
        if row < 0:
            return
        self._save_prompt_form(row)
        item = deepcopy(self.config_data["prompts"][row])
        item["id"] = self._unique_id("prompts", f"{item.get('id') or 'prompt'}-copy")
        self.config_data["prompts"].insert(row + 1, item)
        self._refresh_prompt_list(row + 1)
        self._refresh_run_lists()

    def _delete_prompt(self):
        row = self.prompt_list.currentRow()
        if row < 0 or len(self.config_data.get("prompts", [])) <= 1:
            return
        del self.config_data["prompts"][row]
        self._refresh_prompt_list(min(row, len(self.config_data["prompts"]) - 1))
        self._refresh_run_lists()

    def _apply_prompt_form(self):
        self._save_prompt_form(self._current_prompt_row)
        self._refresh_prompt_list(self._current_prompt_row)
        self._refresh_run_lists()
        self._refresh_json_preview()

    def _load_prompt_form(self, row: int):
        self._loading_prompt = True
        try:
            if row < 0 or row >= len(self.config_data.get("prompts", [])):
                self._clear_prompt_form()
                return
            item = self.config_data["prompts"][row]
            self.prompt_id_edit.setText(str(item.get("id") or ""))
            self.prompt_mode_combo.setCurrentText(str(item.get("mode") or "project"))
            builtin = str(item.get("builtin") or "")
            builtin_index = self.prompt_builtin_combo.findData(builtin)
            self.prompt_builtin_combo.setCurrentIndex(builtin_index if builtin_index >= 0 else 2)
            self.prompt_path_edit.setText(str(item.get("path") or ""))
            self.prompt_template_edit.setPlainText(str(item.get("template") or item.get("text") or item.get("prompt") or ""))
            self.prompt_system_path_edit.setText(str(item.get("system_instruction_path") or ""))
            self.prompt_system_edit.setPlainText(str(item.get("system_instruction") or ""))
            self.prompt_use_system_check.setChecked(bool(item.get("use_system_instruction", True)))
            self.prompt_sequential_check.setChecked(bool(item.get("sequential_mode", False)))
            self._on_prompt_builtin_changed()
        finally:
            self._loading_prompt = False

    def _clear_prompt_form(self):
        self.prompt_id_edit.clear()
        self.prompt_mode_combo.setCurrentText("project")
        self.prompt_builtin_combo.setCurrentIndex(0)
        self.prompt_path_edit.clear()
        self.prompt_template_edit.clear()
        self.prompt_system_path_edit.clear()
        self.prompt_system_edit.clear()
        self.prompt_use_system_check.setChecked(True)
        self.prompt_sequential_check.setChecked(False)
        self._on_prompt_builtin_changed()

    def _save_prompt_form(self, row: int):
        if self._loading_prompt or row < 0 or row >= len(self.config_data.get("prompts", [])):
            return
        item: dict[str, Any] = {
            "id": self.prompt_id_edit.text().strip() or self._unique_id("prompts", "prompt"),
            "mode": self.prompt_mode_combo.currentText().strip() or "project",
        }
        builtin = str(self.prompt_builtin_combo.currentData() or "")
        template = self.prompt_template_edit.toPlainText().strip()
        path = self.prompt_path_edit.text().strip()
        if builtin:
            item["builtin"] = builtin
        elif template:
            item["template"] = template
        elif path:
            item["path"] = path
        else:
            item["template"] = ""
        system = self.prompt_system_edit.toPlainText().strip()
        system_path = self.prompt_system_path_edit.text().strip()
        if system:
            item["system_instruction"] = system
        elif system_path:
            item["system_instruction_path"] = system_path
        item["use_system_instruction"] = self.prompt_use_system_check.isChecked()
        if self.prompt_sequential_check.isChecked():
            item["sequential_mode"] = True
        self.config_data["prompts"][row] = item
        current = self.prompt_list.item(row)
        if current:
            current.setText(item["id"])

    def _on_prompt_builtin_changed(self):
        custom = not bool(self.prompt_builtin_combo.currentData())
        self.prompt_template_edit.setEnabled(custom)
        self.prompt_path_edit.setEnabled(custom)

    def _populate_provider_combo(self):
        self.model_provider_combo.clear()
        providers = api_config.api_providers()
        for provider_id, provider in sorted(providers.items(), key=lambda item: str(item[1].get("display_name") or item[0])):
            display = str(provider.get("display_name") or provider_id)
            self.model_provider_combo.addItem(f"{display} ({provider_id})", provider_id)

    def _on_provider_changed(self):
        self._populate_model_combo()
        self._update_model_id_hint()

    def _populate_model_combo(self, selected_model: str | None = None):
        provider_id = str(self.model_provider_combo.currentData() or "")
        provider = api_config.ensure_dynamic_provider_models(provider_id) or {}
        models = provider.get("models", {}) if isinstance(provider, dict) else {}
        self.model_name_combo.blockSignals(True)
        self.model_name_combo.clear()
        for model_name in sorted(models.keys()):
            model_id = str(models.get(model_name, {}).get("id") or model_name)
            self.model_name_combo.addItem(model_name, model_id)
        if selected_model:
            index = self.model_name_combo.findText(selected_model)
            if index < 0:
                self.model_name_combo.addItem(selected_model, selected_model)
                index = self.model_name_combo.findText(selected_model)
            self.model_name_combo.setCurrentIndex(index)
        elif self.model_name_combo.count():
            self.model_name_combo.setCurrentIndex(0)
        self.model_name_combo.blockSignals(False)

    def _add_model(self):
        self._save_model_form(self._current_model_row)
        first_provider = str(self.model_provider_combo.itemData(0) or "gemini")
        self.config_data["models"].append(
            {
                "id": self._unique_id("models", f"{first_provider}-model"),
                "provider": first_provider,
                "model": "",
                "use_stream": True,
            }
        )
        self._refresh_model_list(len(self.config_data["models"]) - 1)
        self._refresh_run_lists()

    def _duplicate_model(self):
        row = self.model_list.currentRow()
        if row < 0:
            return
        self._save_model_form(row)
        item = deepcopy(self.config_data["models"][row])
        item["id"] = self._unique_id("models", f"{item.get('id') or 'model'}-copy")
        self.config_data["models"].insert(row + 1, item)
        self._refresh_model_list(row + 1)
        self._refresh_run_lists()

    def _delete_model(self):
        row = self.model_list.currentRow()
        if row < 0 or len(self.config_data.get("models", [])) <= 1:
            return
        del self.config_data["models"][row]
        self._refresh_model_list(min(row, len(self.config_data["models"]) - 1))
        self._refresh_run_lists()

    def _apply_model_form(self):
        self._save_model_form(self._current_model_row)
        self._refresh_model_list(self._current_model_row)
        self._refresh_run_lists()
        self._refresh_json_preview()

    def _load_model_form(self, row: int):
        self._loading_model = True
        try:
            if row < 0 or row >= len(self.config_data.get("models", [])):
                self._clear_model_form()
                return
            item = self.config_data["models"][row]
            self.model_id_edit.setText(str(item.get("id") or ""))
            provider_id = str(item.get("provider") or "")
            provider_index = self.model_provider_combo.findData(provider_id)
            if provider_index >= 0:
                self.model_provider_combo.setCurrentIndex(provider_index)
            selected_model = str(item.get("model") or item.get("model_name") or item.get("model_id") or "")
            self._populate_model_combo(selected_model)
            self.model_base_url_edit.setText(str(item.get("base_url") or ""))
            self.model_api_env_edit.setText(str(item.get("api_key_env") or ""))
            self.model_use_stream_check.setChecked(bool(item.get("use_stream", True)))
            self.model_max_output_spin.setValue(int(item.get("max_output_tokens", 0) or 0))
            temperature = item.get("temperature")
            self.model_temperature_spin.setValue(float(temperature) if temperature is not None else -1.0)
            self.model_thinking_check.setChecked(bool(item.get("thinking_enabled", False)))
            self.model_thinking_level_combo.setCurrentText(str(item.get("thinking_level") or ""))
            self.model_debug_check.setChecked(bool(item.get("debug", False)))
        finally:
            self._loading_model = False

    def _clear_model_form(self):
        self.model_id_edit.clear()
        if self.model_provider_combo.count():
            self.model_provider_combo.setCurrentIndex(0)
        self._populate_model_combo()
        self.model_base_url_edit.clear()
        self.model_api_env_edit.clear()
        self.model_use_stream_check.setChecked(True)
        self.model_max_output_spin.setValue(0)
        self.model_temperature_spin.setValue(-1.0)
        self.model_thinking_check.setChecked(False)
        self.model_thinking_level_combo.setCurrentText("")
        self.model_debug_check.setChecked(False)

    def _save_model_form(self, row: int):
        if self._loading_model or row < 0 or row >= len(self.config_data.get("models", [])):
            return
        provider_id = str(self.model_provider_combo.currentData() or "").strip()
        model_name = self.model_name_combo.currentText().strip()
        item: dict[str, Any] = {
            "id": self.model_id_edit.text().strip() or self._model_id_from_fields(),
            "provider": provider_id,
            "use_stream": self.model_use_stream_check.isChecked(),
        }
        if model_name:
            item["model"] = model_name
        base_url = self.model_base_url_edit.text().strip()
        if base_url:
            item["base_url"] = base_url
        api_env = self.model_api_env_edit.text().strip()
        if api_env:
            item["api_key_env"] = api_env
        if self.model_max_output_spin.value() > 0:
            item["max_output_tokens"] = self.model_max_output_spin.value()
        if self.model_temperature_spin.value() >= 0:
            item["temperature"] = self.model_temperature_spin.value()
        if self.model_thinking_check.isChecked():
            item["thinking_enabled"] = True
            level = self.model_thinking_level_combo.currentText().strip()
            if level:
                item["thinking_level"] = level
        if self.model_debug_check.isChecked():
            item["debug"] = True
        self.config_data["models"][row] = item
        current = self.model_list.item(row)
        if current:
            current.setText(item["id"])

    def _model_id_from_fields(self) -> str:
        provider = str(self.model_provider_combo.currentData() or "model").strip() or "model"
        model = self.model_name_combo.currentText().strip() or "default"
        base = "".join(ch if ch.isalnum() else "-" for ch in f"{provider}-{model}".lower()).strip("-")
        while "--" in base:
            base = base.replace("--", "-")
        return self._unique_id("models", base or "model")

    def _generate_model_id(self):
        self.model_id_edit.setText(self._model_id_from_fields())

    def _update_model_id_hint(self):
        if not self.model_id_edit.text().strip():
            self.model_id_edit.setPlaceholderText(self._model_id_from_fields())

    def _refresh_json_preview(self):
        self._sync_preview_only()
        self.items_view.setPlainText(json.dumps(self.config_data, ensure_ascii=False, indent=2))

    def _sync_preview_only(self):
        self._save_case_form(self._current_case_row)
        self._save_prompt_form(self._current_prompt_row)
        self._save_model_form(self._current_model_row)
        self.config_data["name"] = self.name_edit.text().strip() or "prompt-model-benchmark"
        output_dir = self.output_edit.text().strip()
        if output_dir:
            self.config_data["output_dir"] = output_dir

    def _collect_saved_keys(self) -> dict[str, list[str]]:
        if self.settings_manager is None:
            return {}
        keys_by_provider: dict[str, list[str]] = {}
        try:
            statuses = self.settings_manager.load_key_statuses()
        except Exception:
            statuses = []
        for key_info in statuses or []:
            if not isinstance(key_info, dict):
                continue
            provider = str(key_info.get("provider") or "gemini").strip() or "gemini"
            key = str(key_info.get("key") or "").strip()
            if key:
                keys_by_provider.setdefault(provider, []).append(key)

        try:
            full_session = self.settings_manager.load_full_session_settings()
            active = full_session.get("active_keys_by_provider") if isinstance(full_session, dict) else None
        except Exception:
            active = None
        if isinstance(active, dict):
            for provider, keys in active.items():
                provider_id = str(provider or "").strip()
                if not provider_id:
                    continue
                active_keys = [str(key).strip() for key in (keys or []) if str(key).strip()]
                if active_keys:
                    keys_by_provider[provider_id] = active_keys
        return keys_by_provider

    def _start_run(self):
        self._sync_config_from_forms()
        filters = self._filters()
        if filters is None:
            return
        validation_error = self._validate_config_for_run()
        if validation_error:
            QtWidgets.QMessageBox.warning(self, "Нельзя запустить", validation_error)
            return
        config_path = self._ensure_config_file_for_run()
        if config_path is None:
            return

        self._save_ui_state()
        self.log_view.clear()
        self.summary_table.setRowCount(0)
        self.current_report = None
        self.current_output_dir = None
        self._update_buttons(True)
        self.progress_bar.setRange(0, 0)
        self.status_label.setText("Бенчмарк выполняется...")
        self._append_log("Запуск benchmark runner.")

        self.worker = BenchmarkRunWorker(
            config_path=str(config_path),
            output_dir=self.output_edit.text().strip() or None,
            prompt_only=self.prompt_only_check.isChecked(),
            save_prompts=self.save_prompts_check.isChecked(),
            filters=filters,
            limit=self.limit_spin.value() or None,
            api_keys_by_provider=self._collect_saved_keys(),
            parent=self,
        )
        self.worker.progress_changed.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_finished)
        self.worker.failed.connect(self._on_failed)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.start()

    def _validate_config_for_run(self) -> str:
        for case in self.config_data.get("cases", []):
            if not str(case.get("id") or "").strip():
                return "У одного из cases пустой ID."
            if not str(case.get("source_html") or case.get("source") or case.get("text") or case.get("source_path") or "").strip():
                return f"В case `{case.get('id')}` нет исходного текста или source_path."
        for prompt in self.config_data.get("prompts", []):
            if not str(prompt.get("id") or "").strip():
                return "У одного из prompts пустой ID."
            if not prompt.get("builtin") and not str(prompt.get("template") or prompt.get("path") or prompt.get("text") or prompt.get("prompt") or "").strip():
                return f"В prompt `{prompt.get('id')}` нет шаблона."
        for model in self.config_data.get("models", []):
            if not str(model.get("id") or "").strip():
                return "У одной из models пустой ID."
            if not str(model.get("provider") or "").strip():
                return f"В model `{model.get('id')}` не выбран провайдер."
            if not str(model.get("model") or model.get("model_id") or model.get("model_name") or "").strip():
                return f"В model `{model.get('id')}` не выбрана модель."
        return ""

    def _on_progress(self, payload: dict):
        event = payload.get("event")
        if event == "start_run":
            self._append_log(
                f"RUN {payload.get('case_id')} / {payload.get('prompt_id')} / {payload.get('model_id')}"
            )
        elif event == "finish_run":
            status = payload.get("status")
            score = payload.get("score")
            suffix = f", score={score}" if score is not None else ""
            self._append_log(
                f"DONE {payload.get('case_id')} / {payload.get('prompt_id')} / {payload.get('model_id')}: {status}{suffix}"
            )
            if payload.get("error"):
                self._append_log(str(payload.get("error")))
        elif event == "complete":
            self._append_log(f"Отчёты записаны: {payload.get('output_dir')}")

    def _on_finished(self, report: dict):
        self.current_report = report
        output_dir = report.get("output_dir")
        self.current_output_dir = Path(output_dir) if output_dir else None
        self.worker = None
        self._populate_summary(report.get("summary") or [])
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1)
        self.status_label.setText(f"Готово: {len(report.get('results') or [])} запусков.")
        self._update_buttons(False)
        self._append_log("Бенчмарк завершён.")

    def _on_failed(self, traceback_text: str):
        self.worker = None
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.status_label.setText("Ошибка.")
        self._update_buttons(False)
        self._append_log(traceback_text)
        QtWidgets.QMessageBox.warning(self, "Ошибка бенчмарка", traceback_text[-4000:])

    def _populate_summary(self, summary: list[dict]):
        self.summary_table.setRowCount(0)
        for item in summary:
            row = self.summary_table.rowCount()
            self.summary_table.insertRow(row)
            values = [
                item.get("prompt_id", ""),
                item.get("model_id", ""),
                item.get("runs", ""),
                item.get("ok", ""),
                item.get("errors", ""),
                item.get("avg_score", ""),
                item.get("avg_latency_ms", ""),
                item.get("avg_prompt_tokens", ""),
            ]
            for col, value in enumerate(values):
                table_item = QtWidgets.QTableWidgetItem("" if value is None else str(value))
                if col >= 2:
                    table_item.setTextAlignment(
                        QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
                    )
                self.summary_table.setItem(row, col, table_item)

    def _append_log(self, text: str):
        self.log_view.appendPlainText(str(text))

    def _update_buttons(self, running: bool):
        controls = [
            self.start_btn,
            self.config_edit,
            self.output_edit,
            self.name_edit,
            self.browse_config_btn,
            self.load_config_btn,
            self.new_config_btn,
            self.save_config_btn,
            self.save_config_as_btn,
            self.browse_output_btn,
            self.prompt_only_check,
            self.save_prompts_check,
            self.limit_spin,
            self.tabs,
        ]
        for control in controls:
            control.setEnabled(not running)
        has_output = self.current_output_dir is not None
        self.open_summary_btn.setEnabled((not running) and has_output)
        self.open_json_btn.setEnabled((not running) and has_output)
        self.open_folder_btn.setEnabled((not running) and has_output)

    def _open_path(self, path: Path):
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))

    def _open_output_folder(self):
        if self.current_output_dir:
            self._open_path(self.current_output_dir)

    def _open_summary(self):
        if self.current_output_dir:
            self._open_path(self.current_output_dir / "summary.md")

    def _open_results_json(self):
        if self.current_output_dir:
            self._open_path(self.current_output_dir / "results.json")

    def _lines(self, text: str) -> list[str]:
        return [line.strip() for line in str(text or "").splitlines() if line.strip()]

    def _as_list(self, value) -> list:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        return [value]

    def _table_text(self, table: QtWidgets.QTableWidget, row: int, col: int) -> str:
        item = table.item(row, col)
        return item.text().strip() if item is not None else ""

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            QtWidgets.QMessageBox.information(
                self,
                "Бенчмарк выполняется",
                "Дождитесь завершения текущего запуска.",
            )
            event.ignore()
            return
        self._save_ui_state()
        super().closeEvent(event)
