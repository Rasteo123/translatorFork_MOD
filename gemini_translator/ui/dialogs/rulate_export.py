# -*- coding: utf-8 -*-

import os
import re
import zipfile
import xml.etree.ElementTree as ET
from html import unescape
from pathlib import Path
from urllib.parse import unquote

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import QThread, Qt, pyqtSignal
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
    QMainWindow,
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
from .menu_utils import prompt_return_to_menu, return_to_main_menu


class SimpleEpubReader:
    def __init__(self, filepath):
        self.filepath = filepath
        self.zf = zipfile.ZipFile(filepath, "r")
        self.opf_path = self._find_opf_path()
        self.opf_dir = os.path.dirname(self.opf_path)
        self.spine_ids = []
        self.manifest = {}
        self._parse_opf()

    def _find_opf_path(self):
        try:
            with self.zf.open("META-INF/container.xml") as f:
                tree = ET.parse(f)
                root = tree.getroot()
                for elem in root.iter():
                    if elem.tag.endswith("rootfile"):
                        return elem.get("full-path")
        except Exception:
            pass

        for name in self.zf.namelist():
            if name.endswith(".opf"):
                return name
        raise Exception("Не найден OPF-файл (структура книги повреждена или нестандартна)")

    def _parse_opf(self):
        with self.zf.open(self.opf_path) as f:
            tree = ET.parse(f)
            root = tree.getroot()

            for elem in root.iter():
                if elem.tag.endswith("manifest"):
                    for item in elem:
                        if item.tag.endswith("item"):
                            res_id = item.get("id")
                            href = item.get("href")
                            if res_id and href:
                                self.manifest[res_id] = unquote(href)

            for elem in root.iter():
                if elem.tag.endswith("spine"):
                    for itemref in elem:
                        if itemref.tag.endswith("itemref"):
                            idref = itemref.get("idref")
                            if idref:
                                self.spine_ids.append(idref)

    def get_ordered_html_files(self):
        ordered_files = []

        for spine_id in self.spine_ids:
            if spine_id not in self.manifest:
                continue

            href = self.manifest[spine_id]
            full_path = f"{self.opf_dir}/{href}" if self.opf_dir else href
            full_path = full_path.replace("\\", "/")

            parts = full_path.split("/")
            normalized_parts = []
            for part in parts:
                if part == "..":
                    if normalized_parts:
                        normalized_parts.pop()
                elif part != ".":
                    normalized_parts.append(part)
            clean_path = "/".join(normalized_parts)

            if clean_path in self.zf.namelist():
                ordered_files.append(clean_path)
                continue

            basename = os.path.basename(clean_path)
            for name in self.zf.namelist():
                if name.endswith(basename):
                    ordered_files.append(name)
                    break

        return ordered_files

    def read_file(self, filename):
        with self.zf.open(filename) as f:
            return f.read().decode("utf-8", errors="ignore")

    def close(self):
        self.zf.close()


class EPUBConverterThread(QThread):
    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    finished_conversion = pyqtSignal(str, str)
    error = pyqtSignal(str)
    chapters_loaded = pyqtSignal(list)

    def __init__(
        self,
        epub_path,
        payment_mode="all_paid",
        paid_from_index=0,
        volume_rules=None,
        selected_indices=None,
        chunk_size=0,
        overridden_titles=None,
    ):
        super().__init__()
        self.epub_path = epub_path
        self.payment_mode = payment_mode
        self.paid_from_index = paid_from_index
        self.volume_rules = volume_rules if volume_rules else {}
        self.selected_indices = set(selected_indices) if selected_indices is not None else None
        self.chunk_size = chunk_size
        self.overridden_titles = overridden_titles if overridden_titles else {}

    def load_chapters_only(self):
        reader = None
        try:
            reader = SimpleEpubReader(self.epub_path)
            files = reader.get_ordered_html_files()
            chapter_titles = []

            for index, filename in enumerate(files):
                try:
                    content = reader.read_file(filename)
                    title = self._extract_title_from_html(content)
                    if not title:
                        title = f"Глава {index + 1}"
                    chapter_titles.append(title)
                except Exception:
                    chapter_titles.append(f"Глава {index + 1} (ошибка)")

            self.chapters_loaded.emit(chapter_titles)
        except Exception as exc:
            self.error.emit(f"Ошибка чтения EPUB: {exc}")
        finally:
            if reader:
                reader.close()

    def run(self):
        reader = None
        try:
            self.status.emit("Распаковка EPUB...")
            reader = SimpleEpubReader(self.epub_path)
            all_files = reader.get_ordered_html_files()

            total_chapters = len(all_files)
            total_to_process = len(self.selected_indices) if self.selected_indices else total_chapters
            processed_count = 0
            sorted_vol_indices = sorted(self.volume_rules.keys())

            base_output_path = Path(self.epub_path).with_suffix("")
            current_text_buffer = ""
            chunk_counter = 1
            chapters_in_current_chunk = 0
            first_file_content = ""
            final_path = ""

            for idx, filename in enumerate(all_files):
                if self.selected_indices is not None and idx not in self.selected_indices:
                    continue

                processed_count += 1
                if total_to_process > 0:
                    self.progress.emit(int(processed_count / total_to_process * 100))

                try:
                    html_content = reader.read_file(filename)
                    html_content = re.sub(r"<\?xml[^>]*\?>", "", html_content, flags=re.IGNORECASE)
                    html_content = re.sub(r"<!DOCTYPE[^>]*>", "", html_content, flags=re.IGNORECASE)

                    if idx in self.overridden_titles:
                        chapter_title = self.overridden_titles[idx]
                    else:
                        chapter_title = self._extract_title_from_html(html_content)
                        if not chapter_title:
                            chapter_title = f"Глава {idx + 1}"

                    chapter_title = chapter_title.replace(":|:", " ").strip()
                    html_cleaned = self._remove_headers_from_html(html_content)
                    text_content = self._html_to_plain_text(html_cleaned)
                    text_content = re.sub(r"\n{3,}", "\n\n", text_content).strip()

                    payment_status = self._get_payment_status(idx)
                    current_volume = ""
                    for vol_idx in sorted_vol_indices:
                        if idx >= vol_idx:
                            current_volume = self.volume_rules[vol_idx]
                        else:
                            break

                    header_line = f" # [{chapter_title} :|: :|: {payment_status} :|: {current_volume}]"
                    chapter_full_text = f"{header_line}\n{text_content}\n"

                    if self.chunk_size > 0:
                        current_text_buffer += chapter_full_text
                        chapters_in_current_chunk += 1
                        if chapters_in_current_chunk >= self.chunk_size:
                            self._save_chunk(base_output_path, chunk_counter, current_text_buffer)
                            if chunk_counter == 1:
                                first_file_content = current_text_buffer
                            chunk_counter += 1
                            chapters_in_current_chunk = 0
                            current_text_buffer = ""
                    else:
                        current_text_buffer += chapter_full_text
                except Exception as exc:
                    print(f"Error converting chapter {idx}: {exc}")
                    continue

            if current_text_buffer:
                current_text_buffer = self._clean_xml_artifacts(current_text_buffer)
                if self.chunk_size > 0:
                    self._save_chunk(base_output_path, chunk_counter, current_text_buffer)
                    if chunk_counter == 1:
                        first_file_content = current_text_buffer
                    final_path = f"{base_output_path}_part1...{chunk_counter}.md"
                else:
                    output_path = Path(self.epub_path).with_suffix(".md")
                    with open(output_path, "w", encoding="utf-8") as f:
                        f.write(current_text_buffer)
                    final_path = str(output_path)
                    first_file_content = current_text_buffer

            self.status.emit("Конвертация завершена")
            self.finished_conversion.emit(final_path, first_file_content)
        except Exception as exc:
            self.error.emit(f"Критическая ошибка: {exc}")
        finally:
            if reader:
                reader.close()

    def _save_chunk(self, base_path, counter, content):
        content = self._clean_xml_artifacts(content)
        out_name = f"{base_path}_part{counter}.md"
        with open(out_name, "w", encoding="utf-8") as f:
            f.write(content)

    def _get_payment_status(self, chapter_index):
        if self.payment_mode == "all_free":
            return "0"
        if self.payment_mode == "all_paid":
            return "1"
        if self.payment_mode == "paid_from":
            return "1" if chapter_index >= self.paid_from_index else "0"
        return "1"

    def _extract_title_from_html(self, html_content):
        patterns = [
            r"<h1[^>]*>(.*?)</h1>",
            r"<h2[^>]*>(.*?)</h2>",
            r"<h3[^>]*>(.*?)</h3>",
            r"<title[^>]*>(.*?)</title>",
        ]
        for pattern in patterns:
            match = re.search(pattern, html_content, re.IGNORECASE | re.DOTALL)
            if not match:
                continue
            raw_title = match.group(1)
            title = re.sub(r"<[^>]+>", " ", raw_title)
            title = unescape(title.strip())
            title = re.sub(r"\s+", " ", title)
            return title
        return None

    def _remove_headers_from_html(self, html_content):
        html_content = re.sub(
            r"<title[^>]*>.*?</title>",
            "",
            html_content,
            flags=re.IGNORECASE | re.DOTALL,
        )
        html_content = re.sub(
            r"<h[1-3][^>]*>.*?</h[1-3]>",
            "",
            html_content,
            count=1,
            flags=re.IGNORECASE | re.DOTALL,
        )
        return html_content

    def _html_to_plain_text(self, html_content):
        text = html_content
        text = re.sub(r"</(p|div|h[1-6]|li|blockquote)>", "\n\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<hr\s*/?>", "\n***\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = unescape(text)

        lines = []
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped:
                lines.append(stripped)

        return "\n".join(lines)

    def _clean_xml_artifacts(self, content):
        content = re.sub(r"xml version='[^']+' encoding='[^']+'?", "", content)
        content = re.sub(r'xmlns="[^"]+"', "", content)
        return content


class RulateMarkdownExportWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("EPUB -> Rulate Markdown")
        self.setMinimumSize(1100, 750)

        self.converter_thread = None
        self.selected_epub_path = None
        self.original_titles = []
        self.custom_titles = {}
        self.volume_rules = {}
        self._returning_to_main_menu = False

        self.init_ui()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

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
        self.menu_button = QPushButton("В меню")
        self.menu_button.clicked.connect(self._return_to_menu)
        action_row.addWidget(self.menu_button)
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

    def _return_to_menu(self):
        if self.converter_thread and self.converter_thread.isRunning():
            QMessageBox.warning(self, "Подождите", "Сначала дождитесь завершения конвертации.")
            return
        self._returning_to_main_menu = True
        self.close()

    def closeEvent(self, event):
        if self.converter_thread and self.converter_thread.isRunning():
            QMessageBox.warning(self, "Подождите", "Сначала дождитесь завершения конвертации.")
            event.ignore()
            return

        if self._returning_to_main_menu:
            return_to_main_menu()
            event.accept()
            return

        action = prompt_return_to_menu(self)
        if action == "cancel":
            event.ignore()
            return
        if action == "menu":
            return_to_main_menu()
        event.accept()
