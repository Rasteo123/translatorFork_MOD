# -*- coding: utf-8 -*-
"""RulateExportPage — EPUB → Rulate Markdown converter as an embeddable ShellPage."""

import re

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from html import unescape
from pathlib import Path

from gemini_translator.ui.shell import ShellPage
from gemini_translator.ui.dialogs.rulate_export import EPUBConverterThread, SimpleEpubReader


class RulateExportPage(ShellPage):
    page_title = "EPUB → Rulate Markdown"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.converter_thread = None
        self.selected_epub_path = None
        self.original_titles = []
        self.custom_titles = {}
        self.volume_rules = {}
        self._build_ui()

    def can_leave(self) -> bool:
        if self.converter_thread and self.converter_thread.isRunning():
            QMessageBox.warning(
                self, "Подождите",
                "Сначала дождитесь завершения конвертации.",
            )
            return False
        return True

    def _build_ui(self):
        main_layout = QVBoxLayout(self)

        top_panel = QGroupBox("Исходный файл")
        top_layout = QHBoxLayout()
        self.file_label = QLabel("Файл не выбран")
        self.file_label.setStyleSheet("font-weight: bold;")
        select_button = QPushButton("Выбрать EPUB")
        select_button.clicked.connect(self.select_file)
        top_layout.addWidget(self.file_label, 1)
        top_layout.addWidget(select_button)
        top_panel.setLayout(top_layout)
        main_layout.addWidget(top_panel)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("Список глав (двойной клик для переименования):"))

        self.chapters_list_widget = QListWidget()
        self.chapters_list_widget.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.chapters_list_widget.itemDoubleClicked.connect(self.manual_rename_chapter)
        left_layout.addWidget(self.chapters_list_widget)

        sel_btn_layout = QHBoxLayout()
        sel_all_btn = QPushButton("Все")
        sel_all_btn.clicked.connect(self.select_all_chapters)
        sel_none_btn = QPushButton("Сброс")
        sel_none_btn.clicked.connect(self.deselect_all_chapters)
        sel_btn_layout.addWidget(sel_all_btn)
        sel_btn_layout.addWidget(sel_none_btn)
        left_layout.addLayout(sel_btn_layout)

        hint_label = QLabel("Shift+ЛКМ: диапазон, Ctrl+ЛКМ: точечный выбор")
        hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint_label.setStyleSheet("color: #7aa2f7; font-size: 11px;")
        left_layout.addWidget(hint_label)

        splitter.addWidget(left_widget)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        tabs = QTabWidget()

        rename_tab = QWidget()
        rename_layout = QVBoxLayout(rename_tab)
        rename_info = QLabel(
            "1. Выделите главы слева.\n"
            "2. Нажмите 'Получить список'.\n"
            "3. Отправьте список в AI.\n"
            "4. Вставьте ответ и нажмите 'Применить'."
        )
        rename_info.setStyleSheet("color: #9ece6a; font-style: italic;")
        rename_layout.addWidget(rename_info)

        self.check_context = QCheckBox("Добавлять первые 3 строки текста для контекста")
        rename_layout.addWidget(self.check_context)

        self.rename_editor = QPlainTextEdit()
        self.rename_editor.setPlaceholderText("[ID] Новое название главы...")
        self.rename_editor.setFont(QtGui.QFont("Courier New", 10))
        rename_layout.addWidget(self.rename_editor)

        ren_btn_layout = QHBoxLayout()
        get_list_btn = QPushButton("1. Получить список для AI")
        get_list_btn.clicked.connect(self.generate_rename_list)
        apply_ren_btn = QPushButton("2. Применить изменения")
        apply_ren_btn.setStyleSheet(
            "background-color: #7aa2f7; color: #1a1b26; font-weight: bold;"
        )
        apply_ren_btn.clicked.connect(self.apply_renames)
        ren_btn_layout.addWidget(get_list_btn)
        ren_btn_layout.addWidget(apply_ren_btn)
        rename_layout.addLayout(ren_btn_layout)
        tabs.addTab(rename_tab, "Редактор имен")

        payment_tab = QWidget()
        pay_layout = QVBoxLayout(payment_tab)
        self.radio_all_paid = QRadioButton("Все главы платные (1)")
        self.radio_all_paid.setChecked(True)
        self.radio_all_free = QRadioButton("Все главы бесплатные (0)")
        self.radio_paid_from = QRadioButton("Платные начиная с:")
        pay_layout.addWidget(self.radio_all_paid)
        pay_layout.addWidget(self.radio_all_free)

        paid_from_layout = QHBoxLayout()
        paid_from_layout.addWidget(self.radio_paid_from)
        self.chapter_combo_payment = QComboBox()
        self.chapter_combo_payment.setEnabled(False)
        paid_from_layout.addWidget(self.chapter_combo_payment, 1)
        pay_layout.addLayout(paid_from_layout)
        self.radio_paid_from.toggled.connect(self.chapter_combo_payment.setEnabled)
        pay_layout.addStretch()
        tabs.addTab(payment_tab, "Платность")

        volume_tab = QWidget()
        vol_layout = QVBoxLayout(volume_tab)

        vol_input_layout = QHBoxLayout()
        self.chapter_combo_volume = QComboBox()
        vol_input_layout.addWidget(QLabel("С главы:"))
        vol_input_layout.addWidget(self.chapter_combo_volume, 1)

        self.volume_name_input = QLineEdit()
        self.volume_name_input.setPlaceholderText("Название тома")
        vol_input_layout.addWidget(self.volume_name_input, 1)

        add_vol_btn = QPushButton("Add")
        add_vol_btn.setFixedWidth(40)
        add_vol_btn.clicked.connect(self.add_volume_rule)
        vol_input_layout.addWidget(add_vol_btn)
        vol_layout.addLayout(vol_input_layout)

        self.volume_table = QTableWidget()
        self.volume_table.setColumnCount(2)
        self.volume_table.setHorizontalHeaderLabels(["С главы", "Том"])
        self.volume_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.volume_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        vol_layout.addWidget(self.volume_table)

        del_vol_btn = QPushButton("Удалить выбранное правило")
        del_vol_btn.clicked.connect(self.delete_volume_rule)
        vol_layout.addWidget(del_vol_btn)
        tabs.addTab(volume_tab, "Тома")

        export_tab = QWidget()
        export_layout = QVBoxLayout(export_tab)

        export_group = QGroupBox("Разбиение на файлы")
        export_group_layout = QVBoxLayout(export_group)
        self.check_split = QCheckBox("Разбивать результат на части")
        export_group_layout.addWidget(self.check_split)

        split_row = QHBoxLayout()
        split_row.addWidget(QLabel("Кол-во глав в файле:"))
        self.spin_chunk_size = QSpinBox()
        self.spin_chunk_size.setRange(1, 9999)
        self.spin_chunk_size.setValue(50)
        self.spin_chunk_size.setEnabled(False)
        split_row.addWidget(self.spin_chunk_size)
        export_group_layout.addLayout(split_row)
        self.check_split.toggled.connect(self.spin_chunk_size.setEnabled)

        export_layout.addWidget(export_group)
        export_layout.addStretch()
        tabs.addTab(export_tab, "Экспорт")

        right_layout.addWidget(tabs)

        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        main_layout.addWidget(splitter, 1)

        self.convert_button = QPushButton("КОНВЕРТИРОВАТЬ ВЫБРАННОЕ")
        self.convert_button.setMinimumHeight(45)
        self.convert_button.setStyleSheet(
            "font-weight: bold; background-color: #7aa2f7; color: #1a1b26;"
        )
        self.convert_button.clicked.connect(self.start_conversion)
        self.convert_button.setEnabled(False)
        action_row = QHBoxLayout()
        action_row.addWidget(self.convert_button, 1)
        main_layout.addLayout(action_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        main_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("Ожидание...")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("color: #bb9af7;")
        main_layout.addWidget(self.status_label)

        main_layout.addWidget(QLabel("Предпросмотр результата:"))
        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setMaximumHeight(160)
        self.preview_text.setFont(QtGui.QFont("Courier New", 10))
        main_layout.addWidget(self.preview_text)

    def select_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Выберите EPUB", "", "EPUB (*.epub)")
        if not file_path:
            return

        self.selected_epub_path = file_path
        self.file_label.setText(f"Файл: {Path(file_path).name}")
        self.convert_button.setEnabled(False)
        self.chapters_list_widget.clear()
        self.preview_text.clear()
        self.status_label.setText("Анализ структуры...")
        self.volume_rules = {}
        self.custom_titles = {}
        self.update_volume_table()

        self.converter_thread = EPUBConverterThread(file_path)
        self.converter_thread.chapters_loaded.connect(self.on_chapters_loaded)
        self.converter_thread.error.connect(self.on_error)
        self.converter_thread.load_chapters_only()

    def on_chapters_loaded(self, chapter_titles):
        self.original_titles = chapter_titles
        self.custom_titles = {}

        self.refresh_chapters_list()
        self.select_all_chapters()

        combo_items = [f"{i + 1}. {title}" for i, title in enumerate(chapter_titles)]
        self.chapter_combo_payment.clear()
        self.chapter_combo_payment.addItems(combo_items)
        self.chapter_combo_volume.clear()
        self.chapter_combo_volume.addItems(combo_items)

        self.volume_rules = {}
        self.update_volume_table()
        self.convert_button.setEnabled(True)
        self.status_label.setText(f"Глав: {len(chapter_titles)}")

    def refresh_chapters_list(self):
        current_row = self.chapters_list_widget.currentRow()
        selected_rows = [
            self.chapters_list_widget.row(item) for item in self.chapters_list_widget.selectedItems()
        ]

        self.chapters_list_widget.clear()
        for i, original_title in enumerate(self.original_titles):
            display_title = self.custom_titles.get(i, original_title)
            self.chapters_list_widget.addItem(f"{i + 1}. {display_title}")

        for row in selected_rows:
            if row < self.chapters_list_widget.count():
                self.chapters_list_widget.item(row).setSelected(True)

        if 0 <= current_row < self.chapters_list_widget.count():
            self.chapters_list_widget.scrollToItem(self.chapters_list_widget.item(current_row))

    def manual_rename_chapter(self, item):
        row = self.chapters_list_widget.row(item)
        if row < 0:
            return

        current_name = self.custom_titles.get(row, self.original_titles[row])
        new_name, ok = QInputDialog.getText(
            self,
            "Переименование",
            "Новое название главы:",
            text=current_name,
        )
        if ok and new_name:
            self.custom_titles[row] = new_name.strip()
            self.refresh_chapters_list()
            self.update_volume_table()

    def generate_rename_list(self):
        selected_indices = sorted(index.row() for index in self.chapters_list_widget.selectedIndexes())
        if not selected_indices:
            QMessageBox.warning(self, "Внимание", "Выберите главы слева.")
            return

        include_context = self.check_context.isChecked()
        reader = None
        html_files = []

        if include_context:
            if not self.selected_epub_path:
                return
            QtWidgets.QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                reader = SimpleEpubReader(self.selected_epub_path)
                html_files = reader.get_ordered_html_files()
            except Exception as exc:
                QtWidgets.QApplication.restoreOverrideCursor()
                QMessageBox.critical(self, "Ошибка чтения", f"Не удалось прочитать структуру:\n{exc}")
                return

        text_output = ""
        for idx in selected_indices:
            current_name = self.custom_titles.get(idx, self.original_titles[idx])
            text_output += f"[{idx}] {current_name}\n"

            if include_context and idx < len(html_files):
                try:
                    raw_content = reader.read_file(html_files[idx])
                    clean_content = re.sub(
                        r"<h[1-6][^>]*>.*?</h[1-6]>",
                        "",
                        raw_content,
                        flags=re.IGNORECASE | re.DOTALL,
                    )
                    clean_content = re.sub(
                        r"<title[^>]*>.*?</title>",
                        "",
                        clean_content,
                        flags=re.IGNORECASE | re.DOTALL,
                    )
                    clean_content = re.sub(r"</(p|div|br)>", "\n", clean_content)
                    clean_content = re.sub(r"<[^>]+>", "", clean_content)
                    clean_content = unescape(clean_content)

                    lines = []
                    for line in clean_content.split("\n"):
                        stripped_line = line.strip()
                        if stripped_line and "xml version" not in stripped_line.lower():
                            lines.append(stripped_line)

                    for context_line in lines[:3]:
                        text_output += f">>> {context_line[:150]}\n"
                    text_output += "\n"
                except Exception:
                    text_output += ">>> (Ошибка чтения контекста)\n"

        if include_context and reader:
            reader.close()
            QtWidgets.QApplication.restoreOverrideCursor()

        self.rename_editor.setPlainText(text_output)

    def apply_renames(self):
        text = self.rename_editor.toPlainText()
        lines = text.strip().split("\n")
        pattern = re.compile(r"\[(\d+)\]\s*(.*)")
        applied_count = 0
        error_lines = 0

        for line in lines:
            line = line.strip()
            if not line or line.startswith(">>>"):
                continue

            match = pattern.search(line)
            if not match:
                error_lines += 1
                continue

            try:
                idx = int(match.group(1))
                raw_title = match.group(2).strip()
                new_title = re.sub(r"^[:\-\.]\s*", "", raw_title)

                if 0 <= idx < len(self.original_titles):
                    self.custom_titles[idx] = new_title
                    applied_count += 1
                else:
                    error_lines += 1
            except ValueError:
                error_lines += 1

        self.refresh_chapters_list()
        self.update_volume_table()

        if applied_count > 0:
            message = f"Успешно обновлено глав: {applied_count}"
            if error_lines > 0:
                message += f"\n(Не распознано строк: {error_lines})"
            QMessageBox.information(self, "Готово", message)
            return

        QMessageBox.warning(
            self,
            "Ошибка",
            "Ничего не изменилось.\nУбедитесь, что строки имеют формат [123] Название.",
        )

    def select_all_chapters(self):
        for i in range(self.chapters_list_widget.count()):
            self.chapters_list_widget.item(i).setSelected(True)

    def deselect_all_chapters(self):
        self.chapters_list_widget.clearSelection()

    def add_volume_rule(self):
        if not self.original_titles:
            return
        idx = self.chapter_combo_volume.currentIndex()
        name = self.volume_name_input.text().strip()
        if not name:
            return
        self.volume_rules[idx] = name
        self.update_volume_table()
        self.volume_name_input.clear()

    def delete_volume_rule(self):
        row = self.volume_table.currentRow()
        if row < 0:
            return
        sorted_keys = sorted(self.volume_rules.keys())
        del self.volume_rules[sorted_keys[row]]
        self.update_volume_table()

    def update_volume_table(self):
        self.volume_table.setRowCount(0)
        sorted_keys = sorted(self.volume_rules.keys())
        self.volume_table.setRowCount(len(sorted_keys))

        for row, idx in enumerate(sorted_keys):
            act_title = self.custom_titles.get(idx, self.original_titles[idx]) if idx < len(self.original_titles) else "?"

            item_chapter = QTableWidgetItem(f"{idx + 1}. {act_title}")
            item_chapter.setFlags(item_chapter.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
            item_volume = QTableWidgetItem(self.volume_rules[idx])
            item_volume.setFlags(item_volume.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)

            self.volume_table.setItem(row, 0, item_chapter)
            self.volume_table.setItem(row, 1, item_volume)

    def start_conversion(self):
        if not self.selected_epub_path:
            return

        selected_indices = sorted(index.row() for index in self.chapters_list_widget.selectedIndexes())
        if not selected_indices:
            QMessageBox.warning(self, "Ошибка", "Выберите хотя бы одну главу.")
            return

        if self.radio_all_free.isChecked():
            mode, paid_from = "all_free", 0
        elif self.radio_all_paid.isChecked():
            mode, paid_from = "all_paid", 0
        else:
            mode, paid_from = "paid_from", self.chapter_combo_payment.currentIndex()

        chunk_size = self.spin_chunk_size.value() if self.check_split.isChecked() else 0

        self.convert_button.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.status_label.setText("Конвертация...")

        self.converter_thread = EPUBConverterThread(
            self.selected_epub_path,
            mode,
            paid_from,
            self.volume_rules,
            selected_indices,
            chunk_size,
            self.custom_titles,
        )
        self.converter_thread.progress.connect(self.progress_bar.setValue)
        self.converter_thread.status.connect(self.status_label.setText)
        self.converter_thread.finished_conversion.connect(self.on_conversion_finished)
        self.converter_thread.error.connect(self.on_error)
        self.converter_thread.start()

    def on_conversion_finished(self, path, content):
        self.progress_bar.setVisible(False)
        self.convert_button.setEnabled(True)
        self.preview_text.setPlainText(content[:1000])
        self.status_label.setText("Готово")

        if "_part" in str(path) and "..." in str(path):
            QMessageBox.information(
                self,
                "Успех",
                f"Файлы сохранены рядом с исходником:\n{path}",
            )
            return

        QMessageBox.information(self, "Успех", f"Сохранено:\n{path}")

    def on_error(self, msg):
        self.progress_bar.setVisible(False)
        self.convert_button.setEnabled(True)
        self.status_label.setText("Ошибка")
        QMessageBox.critical(self, "Ошибка", msg)
