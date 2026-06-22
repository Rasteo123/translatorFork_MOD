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
from PyQt6.QtGui import QAction
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
    QToolBar,
    QVBoxLayout,
    QWidget,
)
from .menu_utils import prompt_return_to_menu, return_to_main_menu
from ...utils.epub_tools import extract_first_epub_heading_text, normalize_epub_chapter_heading_to_h1


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
            return normalize_epub_chapter_heading_to_h1(f.read().decode("utf-8", errors="ignore"))

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
        return extract_first_epub_heading_text(html_content, include_title=True) or None

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
        self._returning_to_main_menu = False

        # Lazy import breaks the page<->wrapper import cycle.
        from gemini_translator.ui.pages.rulate_export_page import RulateExportPage

        self.page = RulateExportPage(self)
        self.setCentralWidget(self.page)

        toolbar = QToolBar()
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        act_menu = QAction("В меню", self)
        act_menu.triggered.connect(self._return_to_menu)
        toolbar.addAction(act_menu)

    @property
    def converter_thread(self):
        return self.page.converter_thread

    def _return_to_menu(self):
        if self.page.converter_thread and self.page.converter_thread.isRunning():
            QMessageBox.warning(self, "Подождите", "Сначала дождитесь завершения конвертации.")
            return
        self._returning_to_main_menu = True
        self.close()

    def closeEvent(self, event):
        if self.page.converter_thread and self.page.converter_thread.isRunning():
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
