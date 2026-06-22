# -*- coding: utf-8 -*-
"""ChapterSplitterPage — Chapter Splitter tool as an embeddable ShellPage."""

import os
from pathlib import Path

from PyQt6 import QtGui
from PyQt6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from gemini_translator.ui.shell import ShellPage
from gemini_translator.ui.dialogs.chapter_splitter import ChapterSplitterThread, SplitSettings


class ChapterSplitterPage(ShellPage):
    page_title = "Chapter Splitter"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = None
        self._build_ui()

    def can_leave(self) -> bool:
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "Подождите", "Сначала дождитесь завершения обработки.")
            return False
        return True

    def _build_ui(self):
        main_layout = QVBoxLayout(self)

        files_group = QGroupBox("Файлы")
        files_layout = QFormLayout(files_group)

        input_row = QHBoxLayout()
        self.input_edit = QLineEdit()
        input_browse = QPushButton("Обзор...")
        input_browse.clicked.connect(self._choose_input)
        input_row.addWidget(self.input_edit, 1)
        input_row.addWidget(input_browse)
        files_layout.addRow("Вход:", input_row)

        output_row = QHBoxLayout()
        self.output_edit = QLineEdit()
        output_browse = QPushButton("Обзор...")
        output_browse.clicked.connect(self._choose_output)
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(output_browse)
        files_layout.addRow("Выход:", output_row)
        main_layout.addWidget(files_group)

        params_group = QGroupBox("Параметры разбиения (в символах)")
        params_layout = QHBoxLayout(params_group)

        self.threshold_spin = self._create_spinbox(100, 1_000_000, 14000)
        self.target_spin = self._create_spinbox(100, 1_000_000, 8000)
        self.min_size_spin = self._create_spinbox(100, 1_000_000, 6000)

        params_layout.addWidget(self._wrap_control("Порог деления >=", self.threshold_spin))
        params_layout.addWidget(self._wrap_control("Целевой размер", self.target_spin))
        params_layout.addWidget(self._wrap_control("Мин. размер части", self.min_size_spin))
        main_layout.addWidget(params_group)

        self.process_button = QPushButton("▶ Обработать")
        self.process_button.setMinimumHeight(44)
        self.process_button.setStyleSheet(
            "font-weight: bold; background-color: #4f79a7; color: white;"
        )
        self.process_button.clicked.connect(self._start_processing)
        action_row = QHBoxLayout()
        action_row.addWidget(self.process_button, 1)
        main_layout.addLayout(action_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        main_layout.addWidget(self.progress_bar)

        main_layout.addWidget(QLabel("Лог:"))
        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setFont(QtGui.QFont("Courier New", 10))
        main_layout.addWidget(self.log_output, 1)

    def _create_spinbox(self, minimum, maximum, value):
        spinbox = QSpinBox()
        spinbox.setRange(minimum, maximum)
        spinbox.setValue(value)
        return spinbox

    def _wrap_control(self, label, widget):
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel(label))
        layout.addWidget(widget)
        return wrapper

    def _choose_input(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите входной файл",
            "",
            "Supported Files (*.epub *.md);;EPUB (*.epub);;Markdown (*.md)",
        )
        if not file_path:
            return

        self.input_edit.setText(file_path)
        input_path = Path(file_path)
        self.output_edit.setText(str(input_path.with_name(f"{input_path.stem}_split{input_path.suffix}")))

    def _choose_output(self):
        input_path = self.input_edit.text().strip()
        suffix = Path(input_path).suffix.lower() if input_path else ".epub"
        filter_value = "EPUB (*.epub)" if suffix == ".epub" else "Markdown (*.md)"
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить как",
            self.output_edit.text().strip(),
            filter_value,
        )
        if file_path:
            self.output_edit.setText(file_path)

    def _start_processing(self):
        input_path = self.input_edit.text().strip()
        output_path = self.output_edit.text().strip()

        if not input_path or not output_path:
            QMessageBox.warning(self, "Ошибка", "Укажите входной и выходной файлы.")
            return

        if not os.path.exists(input_path):
            QMessageBox.warning(self, "Ошибка", "Входной файл не найден.")
            return

        if Path(input_path).suffix.lower() != Path(output_path).suffix.lower():
            QMessageBox.warning(
                self,
                "Ошибка",
                "Расширение выходного файла должно совпадать с входным (.epub или .md).",
            )
            return

        if os.path.abspath(input_path) == os.path.abspath(output_path):
            QMessageBox.warning(self, "Ошибка", "Выходной файл должен отличаться от входного.")
            return

        settings = SplitSettings(
            split_threshold=self.threshold_spin.value(),
            target_size=self.target_spin.value(),
            min_part_size=self.min_size_spin.value(),
        )

        self.log_output.clear()
        self.progress_bar.setValue(0)
        self.process_button.setEnabled(False)

        self.worker = ChapterSplitterThread(input_path, output_path, settings)
        self.worker.progress.connect(self.progress_bar.setValue)
        self.worker.log_message.connect(self._append_log)
        self.worker.finished_processing.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _append_log(self, message):
        self.log_output.appendPlainText(message)

    def _on_finished(self, result):
        self.process_button.setEnabled(True)
        self.progress_bar.setValue(100)
        self._append_log("")
        self._append_log("Итог:")
        self._append_log(f"Глав разбито: {result['split_chapters']}")
        self._append_log(f"Глав без изменений: {result['unchanged_chapters']}")
        self._append_log(f"Глав в результате: {result['output_chapters']}")
        self._append_log("")
        self._append_log(f"Готово! Результат: {result['output_path']}")
        QMessageBox.information(self, "Готово", f"Сохранено:\n{result['output_path']}")

    def _on_error(self, error_text):
        self.process_button.setEnabled(True)
        self.progress_bar.setValue(0)
        self._append_log(error_text)
        QMessageBox.critical(self, "Ошибка", error_text)
