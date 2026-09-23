# -*- coding: utf-8 -*-

import os
import re
import zipfile
import xml.etree.ElementTree as ET

from html import unescape
from pathlib import Path

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
from ...utils.epub_tools import (
    extract_first_epub_heading_text,
    find_opf_path,
    normalize_epub_chapter_heading_to_h1,
    read_spine_html_order,
)


# Блоки системных окон (см. utils/system_windows.py) уходят на Rulate как
# сырой HTML: загрузчик сайта пропускает инлайн-стили, но оборачивает каждую
# строку файла в <p>, поэтому блок обязан остаться одной строкой. Атрибут
# data-sys-orig хранит исходные абзацы для снятия оформления и сайту не нужен.
# Сборка EPUB пересобирает главы через BeautifulSoup, а он пишет значение
# с двойными кавычками внутри в одинарных кавычках.
SYSTEM_BLOCK_RE = re.compile(r'<div\b[^>]*\bdata-sys="[^"]*"[^>]*>.*?</div>', re.IGNORECASE | re.DOTALL)
SYSTEM_BLOCK_ORIG_RE = re.compile(r"""\s*\bdata-sys-orig=(?:"[^"]*"|'[^']*')""", re.IGNORECASE)
_SYSTEM_BLOCK_PLACEHOLDER = "\x00SYSBLOCK{index}\x00"
# Загрузчик Rulate прогоняет файл через Markdown. Строка «[Метка]: 80 (обожание)»
# для него — определение ссылки: она пропадает со страницы. Обратная косая черта
# перед скобкой делает её обычным текстом.
_REFERENCE_DEFINITION_RE = re.compile(
    r"""^\[[^\]\n]+\]:[ \t]*<?\S+?>?(?:[ \t]+(?:"[^"]*"|'[^']*'|\([^)]*\)))?[ \t]*$"""
)


def _protect_from_markdown(line: str) -> str:
    """Строка текста главы, которую Markdown сайта покажет как есть.

    ``<`` уходит сущностью, чтобы «<Shadow>» не приняли за тег, а строки вида
    определения ссылки получают ``\\`` перед скобкой.
    """
    line = line.replace("<", "&lt;")
    if _REFERENCE_DEFINITION_RE.match(line):
        line = "\\" + line
    return line


class SimpleEpubReader:
    def __init__(self, filepath):
        self.filepath = filepath
        self.zf = zipfile.ZipFile(filepath, "r")
        try:
            self.opf_path = find_opf_path(self.zf)
            self.opf_dir = os.path.dirname(self.opf_path)
        except Exception:
            # Если поиск OPF не удался (битый/нестандартный EPUB), self.zf
            # уже открыт, но объект не будет присвоен переменной вызывающего
            # кода — без явного close() дескриптор .epub утекает до
            # ближайшего цикла сборщика мусора.
            self.zf.close()
            raise

    def get_ordered_html_files(self):
        return read_spine_html_order(self.zf)

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
        system_blocks = []

        def keep_system_block(match):
            block = SYSTEM_BLOCK_ORIG_RE.sub("", match.group(0))
            block = re.sub(r"\s*\n\s*", " ", block).strip()
            system_blocks.append(block)
            return "\n\n" + _SYSTEM_BLOCK_PLACEHOLDER.format(index=len(system_blocks) - 1) + "\n\n"

        text = SYSTEM_BLOCK_RE.sub(keep_system_block, text)
        text = re.sub(r"<(script|style|head)[^>]*>.*?</\1>", "", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"</(p|div|h[1-6]|li|blockquote)>", "\n\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<hr\s*/?>", "\n***\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = unescape(text)

        lines = []
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped:
                is_block = stripped.startswith("\x00SYSBLOCK")
                lines.append(stripped if is_block else _protect_from_markdown(stripped))

        result = "\n".join(lines)
        for index, block in enumerate(system_blocks):
            result = result.replace(_SYSTEM_BLOCK_PLACEHOLDER.format(index=index), block)
        return result

    def _clean_xml_artifacts(self, content):
        content = re.sub(r"xml version='[^']+' encoding='[^']+'?", "", content)
        content = re.sub(r'xmlns="[^"]+"', "", content)
        return content
