# -*- coding: utf-8 -*-
"""Import non-EPUB documents into the existing EPUB translation pipeline."""

from __future__ import annotations

from dataclasses import dataclass
import html as html_lib
import os
from pathlib import Path
import re
from typing import Any

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt

from .epub_tools import EpubCreator


SUPPORTED_DOCUMENT_EXTENSIONS = {
    ".docx",
    ".txt",
    ".md",
    ".markdown",
    ".html",
    ".htm",
    ".xhtml",
    ".pdf",
}

DOCUMENT_INPUT_FILTER = (
    "Книги и документы (*.epub *.docx *.txt *.md *.markdown *.html *.htm *.xhtml *.pdf);;"
    "EPUB (*.epub);;"
    "DOCX (*.docx);;"
    "Текст и Markdown (*.txt *.md *.markdown);;"
    "HTML (*.html *.htm *.xhtml);;"
    "PDF (*.pdf);;"
    "Все файлы (*)"
)


class DocumentImportError(RuntimeError):
    pass


@dataclass
class DocumentChapter:
    title: str
    html: str
    source_label: str = ""

    @property
    def visible_size(self) -> int:
        return len(strip_html_to_text(self.html))


@dataclass
class DocumentImportResult:
    title: str
    source_format: str
    chapters: list[DocumentChapter]
    warnings: list[str]


def is_convertible_document(path: str | os.PathLike) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_DOCUMENT_EXTENSIONS


def strip_html_to_text(html_text: str) -> str:
    try:
        from bs4 import BeautifulSoup

        return BeautifulSoup(str(html_text or ""), "html.parser").get_text(" ", strip=True)
    except Exception:
        return re.sub(r"<[^>]+>", " ", str(html_text or "")).strip()


def _escape(value: Any) -> str:
    return html_lib.escape(str(value or ""), quote=True)


def _safe_title(value: str, fallback: str = "Глава") -> str:
    title = re.sub(r"\s+", " ", str(value or "")).strip()
    return title[:180] or fallback


def _safe_filename_stem(value: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    stem = stem.strip("._")
    return stem[:90] or "document"


def _unique_output_path(output_dir: str | os.PathLike, base_name: str) -> Path:
    folder = Path(output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    safe_name = _safe_filename_stem(base_name)
    candidate = folder / f"{safe_name}_imported.epub"
    if not candidate.exists():
        return candidate
    index = 2
    while True:
        candidate = folder / f"{safe_name}_imported_{index}.epub"
        if not candidate.exists():
            return candidate
        index += 1


def _wrap_xhtml(title: str, body_html: str) -> str:
    return f"""<?xml version='1.0' encoding='utf-8'?>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
  <title>{_escape(title)}</title>
  <meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>
</head>
<body>
{body_html}
</body>
</html>"""


def _read_text_with_fallbacks(path: Path) -> str:
    errors = []
    for encoding in ("utf-8-sig", "utf-8", "cp1251", "utf-16"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError as exc:
            errors.append(f"{encoding}: {exc}")
    raise DocumentImportError(f"Не удалось определить кодировку файла {path.name}: {'; '.join(errors[:2])}")


PLAIN_HEADING_RE = re.compile(
    r"^\s*(?:"
    r"(?:глава|chapter|том|часть|book|part)\s+[\w\dIVXLCDMivxlcdmА-Яа-я一二三四五六七八九十百千万零]+"
    r"|第\s*[\d零一二三四五六七八九十百千万两]+\s*[章节回卷]"
    r"|\d{1,4}[\.)]\s+\S+"
    r")",
    re.IGNORECASE,
)


def _looks_like_plain_heading(line: str) -> bool:
    text = str(line or "").strip()
    if not text or len(text) > 160:
        return False
    return bool(PLAIN_HEADING_RE.match(text))


def _paragraphs_from_plain_text(text: str) -> str:
    paragraphs = []
    buffer: list[str] = []

    def flush():
        if buffer:
            paragraphs.append(f"<p>{_escape(' '.join(buffer))}</p>")
            buffer.clear()

    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            flush()
            continue
        buffer.append(line)
    flush()
    return "\n".join(paragraphs)


def _plain_text_to_chapters(text: str, title: str) -> list[DocumentChapter]:
    lines = str(text or "").splitlines()
    headings = [(index, line.strip()) for index, line in enumerate(lines) if _looks_like_plain_heading(line)]
    if len(headings) < 2:
        html = _paragraphs_from_plain_text(text)
        return [DocumentChapter(title=_safe_title(title), html=html)]

    chapters: list[DocumentChapter] = []
    if headings[0][0] > 0:
        preface = "\n".join(lines[: headings[0][0]])
        if preface.strip():
            chapters.append(DocumentChapter("Начало / Предисловие", _paragraphs_from_plain_text(preface)))

    for pos, (start_index, heading) in enumerate(headings):
        end_index = headings[pos + 1][0] if pos + 1 < len(headings) else len(lines)
        content = "\n".join(lines[start_index + 1 : end_index])
        body = f"<h1>{_escape(heading)}</h1>\n{_paragraphs_from_plain_text(content)}"
        chapters.append(DocumentChapter(title=_safe_title(heading), html=body))
    return [chapter for chapter in chapters if chapter.html.strip()]


def _inline_markdown(text: str) -> str:
    escaped = _escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", escaped)
    return escaped


def _markdown_lines_to_html(lines: list[str]) -> str:
    parts: list[str] = []
    list_items: list[str] = []
    in_code = False
    code_lines: list[str] = []

    def flush_list():
        if list_items:
            parts.append("<ul>\n" + "\n".join(list_items) + "\n</ul>")
            list_items.clear()

    def flush_code():
        if code_lines:
            parts.append("<pre><code>" + _escape("\n".join(code_lines)) + "</code></pre>")
            code_lines.clear()

    for raw_line in lines:
        line = raw_line.rstrip("\n")
        stripped = line.strip()

        if stripped.startswith("```"):
            if in_code:
                flush_code()
                in_code = False
            else:
                flush_list()
                in_code = True
            continue

        if in_code:
            code_lines.append(line)
            continue

        if not stripped:
            flush_list()
            continue

        heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading:
            flush_list()
            level = min(len(heading.group(1)), 6)
            parts.append(f"<h{level}>{_inline_markdown(heading.group(2).strip())}</h{level}>")
            continue

        bullet = re.match(r"^[-*+]\s+(.+)$", stripped)
        if bullet:
            list_items.append(f"<li>{_inline_markdown(bullet.group(1).strip())}</li>")
            continue

        if stripped.startswith(">"):
            flush_list()
            parts.append(f"<blockquote>{_inline_markdown(stripped.lstrip('>').strip())}</blockquote>")
            continue

        flush_list()
        parts.append(f"<p>{_inline_markdown(stripped)}</p>")

    flush_list()
    flush_code()
    return "\n".join(parts)


def _markdown_to_chapters(text: str, title: str) -> list[DocumentChapter]:
    lines = str(text or "").splitlines()
    headings = [
        (index, match.group(2).strip())
        for index, line in enumerate(lines)
        if (match := re.match(r"^(#{1,2})\s+(.+)$", line.strip()))
    ]
    if not headings:
        return [DocumentChapter(_safe_title(title), _markdown_lines_to_html(lines))]

    chapters: list[DocumentChapter] = []
    if headings[0][0] > 0:
        prefix = lines[: headings[0][0]]
        if "\n".join(prefix).strip():
            chapters.append(DocumentChapter("Начало / Предисловие", _markdown_lines_to_html(prefix)))

    for pos, (start_index, heading) in enumerate(headings):
        end_index = headings[pos + 1][0] if pos + 1 < len(headings) else len(lines)
        body = _markdown_lines_to_html(lines[start_index:end_index])
        chapters.append(DocumentChapter(_safe_title(heading), body))
    return [chapter for chapter in chapters if chapter.html.strip()]


def _html_to_chapters(text: str, title: str) -> list[DocumentChapter]:
    try:
        from bs4 import BeautifulSoup, NavigableString, Tag
    except Exception as exc:
        raise DocumentImportError("Для импорта HTML нужна зависимость beautifulsoup4.") from exc

    soup = BeautifulSoup(str(text or ""), "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()

    doc_title = ""
    if soup.title:
        doc_title = soup.title.get_text(" ", strip=True)
    body = soup.body or soup

    children = list(body.children)
    chapters: list[DocumentChapter] = []
    current_title = _safe_title(doc_title or title)
    current_parts: list[str] = []

    for child in children:
        if isinstance(child, NavigableString):
            text_value = str(child).strip()
            if text_value:
                current_parts.append(f"<p>{_escape(text_value)}</p>")
            continue
        if not isinstance(child, Tag):
            continue
        tag_name = str(child.name or "").lower()
        if tag_name in {"h1", "h2"} and child.get_text(" ", strip=True):
            if current_parts:
                chapters.append(DocumentChapter(current_title, "\n".join(current_parts)))
                current_parts = []
            current_title = _safe_title(child.get_text(" ", strip=True), current_title)
        current_parts.append(str(child))

    if current_parts:
        chapters.append(DocumentChapter(current_title, "\n".join(current_parts)))
    if not chapters:
        chapters = [DocumentChapter(_safe_title(title), f"<p>{_escape(strip_html_to_text(text))}</p>")]
    return chapters


def _docx_heading_level(style_name: str) -> int | None:
    normalized = str(style_name or "").strip().lower()
    if not normalized:
        return None
    if normalized in {"title", "название"}:
        return 1
    if normalized.startswith("heading") or normalized.startswith("заголовок"):
        match = re.search(r"(\d+)", normalized)
        return min(int(match.group(1)), 6) if match else 1
    return None


def _docx_run_to_html(run) -> str:
    text = _escape(getattr(run, "text", "")).replace("\t", "&#8195;").replace("\n", "<br/>")
    if not text:
        return ""
    if getattr(run, "bold", False):
        text = f"<strong>{text}</strong>"
    if getattr(run, "italic", False):
        text = f"<em>{text}</em>"
    if getattr(run, "underline", False):
        text = f"<u>{text}</u>"
    return text


def _docx_paragraph_to_html(paragraph) -> tuple[int | None, str, str] | None:
    text = str(getattr(paragraph, "text", "") or "").strip()
    if not text:
        return None
    style_name = getattr(getattr(paragraph, "style", None), "name", "")
    level = _docx_heading_level(style_name)
    tag = f"h{level}" if level else "p"
    run_html = "".join(_docx_run_to_html(run) for run in getattr(paragraph, "runs", [])).strip()
    if not run_html:
        run_html = _escape(text)
    return level, f"<{tag}>{run_html}</{tag}>", text


def _docx_table_to_html(table) -> str:
    rows = []
    for row in table.rows:
        cells = []
        for cell in row.cells:
            paragraphs = [
                _escape(paragraph.text.strip())
                for paragraph in cell.paragraphs
                if paragraph.text.strip()
            ]
            cells.append("<td>" + "<br/>".join(paragraphs) + "</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return "<table>\n" + "\n".join(rows) + "\n</table>" if rows else ""


def _iter_docx_blocks(document):
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for child in document.element.body.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, document)
        elif isinstance(child, CT_Tbl):
            yield Table(child, document)


def _docx_to_chapters(path: Path) -> tuple[list[DocumentChapter], list[str]]:
    try:
        from docx import Document
    except Exception as exc:
        raise DocumentImportError("Для импорта DOCX нужна зависимость python-docx.") from exc

    document = Document(str(path))
    chapters: list[DocumentChapter] = []
    current_title = _safe_title(path.stem)
    current_parts: list[str] = []
    warnings = ["DOCX импортирует текст, таблицы и базовое форматирование; изображения пока не переносятся."]

    for block in _iter_docx_blocks(document):
        if hasattr(block, "runs"):
            converted = _docx_paragraph_to_html(block)
            if converted is None:
                continue
            level, html, heading_text = converted
            if level and level <= 2:
                if current_parts:
                    chapters.append(DocumentChapter(current_title, "\n".join(current_parts)))
                    current_parts = []
                current_title = _safe_title(heading_text, current_title)
            current_parts.append(html)
        else:
            table_html = _docx_table_to_html(block)
            if table_html:
                current_parts.append(table_html)

    if current_parts:
        chapters.append(DocumentChapter(current_title, "\n".join(current_parts)))
    if not chapters:
        raise DocumentImportError(f"DOCX не содержит извлекаемого текста: {path.name}")
    return chapters, warnings


def _pdf_to_chapters(path: Path) -> tuple[list[DocumentChapter], list[str]]:
    try:
        from pypdf import PdfReader
    except Exception as exc:
        raise DocumentImportError("Для импорта PDF нужна зависимость pypdf.") from exc

    reader = PdfReader(str(path))
    chapters: list[DocumentChapter] = []
    for index, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ""
        if text.strip():
            chapters.append(
                DocumentChapter(
                    title=f"Страница {index}",
                    html=_paragraphs_from_plain_text(text),
                    source_label=f"page:{index}",
                )
            )
    if not chapters:
        raise DocumentImportError("PDF не содержит извлекаемого текста. Для сканов нужен OCR, он пока не встроен.")
    return chapters, ["PDF импортирует только извлекаемый текст. Сканированные страницы без OCR будут пустыми."]


def extract_document_chapters(path: str | os.PathLike) -> DocumentImportResult:
    source_path = Path(path)
    if not source_path.exists():
        raise DocumentImportError(f"Файл не найден: {source_path}")
    suffix = source_path.suffix.lower()
    title = _safe_title(source_path.stem)

    if suffix == ".docx":
        chapters, warnings = _docx_to_chapters(source_path)
        return DocumentImportResult(title=title, source_format="docx", chapters=chapters, warnings=warnings)

    if suffix in {".txt"}:
        text = _read_text_with_fallbacks(source_path)
        return DocumentImportResult(title=title, source_format="txt", chapters=_plain_text_to_chapters(text, title), warnings=[])

    if suffix in {".md", ".markdown"}:
        text = _read_text_with_fallbacks(source_path)
        return DocumentImportResult(title=title, source_format="markdown", chapters=_markdown_to_chapters(text, title), warnings=[])

    if suffix in {".html", ".htm", ".xhtml"}:
        text = _read_text_with_fallbacks(source_path)
        return DocumentImportResult(title=title, source_format="html", chapters=_html_to_chapters(text, title), warnings=[])

    if suffix == ".pdf":
        chapters, warnings = _pdf_to_chapters(source_path)
        return DocumentImportResult(title=title, source_format="pdf", chapters=chapters, warnings=warnings)

    raise DocumentImportError(f"Формат не поддерживается: {suffix or source_path.name}")


def create_epub_from_import(
    result: DocumentImportResult,
    source_path: str | os.PathLike,
    output_dir: str | os.PathLike,
    *,
    title: str | None = None,
    author: str | None = None,
    chapters: list[DocumentChapter] | None = None,
) -> Path:
    source = Path(source_path)
    selected_chapters = chapters if chapters is not None else result.chapters
    selected_chapters = [chapter for chapter in selected_chapters if strip_html_to_text(chapter.html)]
    if not selected_chapters:
        raise DocumentImportError("Нет глав для создания EPUB.")

    book_title = _safe_title(title or result.title or source.stem)
    book_author = author or f"Импортировано из {result.source_format.upper()}"
    output_path = _unique_output_path(output_dir, source.stem)
    creator = EpubCreator(title=book_title, author=book_author)

    for index, chapter in enumerate(selected_chapters, 1):
        chapter_title = _safe_title(chapter.title, f"Глава {index}")
        creator.add_chapter(
            f"chapter_{index:04}.xhtml",
            _wrap_xhtml(chapter_title, chapter.html),
            chapter_title,
        )

    creator.create_epub(str(output_path))
    return output_path


class DocumentImportDialog(QtWidgets.QDialog):
    """Small import wizard for non-EPUB source documents."""

    def __init__(self, source_path: str, output_dir: str, parent=None):
        super().__init__(parent)
        self.source_path = Path(source_path)
        self.output_dir = Path(output_dir)
        self.generated_epub_path: str | None = None
        self.result: DocumentImportResult | None = None
        self.chapters: list[DocumentChapter] = []

        self.setWindowTitle("Импорт документа в EPUB")
        self.resize(980, 700)
        self._build_ui()
        self._load_source()

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(10)

        self.summary_label = QtWidgets.QLabel()
        self.summary_label.setWordWrap(True)
        root.addWidget(self.summary_label)

        form = QtWidgets.QGridLayout()
        self.title_edit = QtWidgets.QLineEdit()
        self.author_edit = QtWidgets.QLineEdit()
        form.addWidget(QtWidgets.QLabel("Название EPUB:"), 0, 0)
        form.addWidget(self.title_edit, 0, 1)
        form.addWidget(QtWidgets.QLabel("Автор:"), 1, 0)
        form.addWidget(self.author_edit, 1, 1)
        root.addLayout(form)

        splitter = QtWidgets.QSplitter(Qt.Orientation.Horizontal)
        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["✓", "Название главы", "Символов"])
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.currentCellChanged.connect(self._update_preview)
        left_layout.addWidget(self.table)

        actions = QtWidgets.QHBoxLayout()
        select_all_btn = QtWidgets.QPushButton("Все")
        select_all_btn.clicked.connect(lambda: self._set_all_checked(True))
        select_none_btn = QtWidgets.QPushButton("Нет")
        select_none_btn.clicked.connect(lambda: self._set_all_checked(False))
        merge_btn = QtWidgets.QPushButton("Объединить")
        merge_btn.clicked.connect(self._merge_chapters)
        delete_btn = QtWidgets.QPushButton("Удалить выбранные")
        delete_btn.clicked.connect(self._delete_selected)
        actions.addWidget(select_all_btn)
        actions.addWidget(select_none_btn)
        actions.addWidget(merge_btn)
        actions.addWidget(delete_btn)
        actions.addStretch(1)
        left_layout.addLayout(actions)
        splitter.addWidget(left)

        self.preview = QtWidgets.QTextBrowser()
        self.preview.setOpenExternalLinks(False)
        splitter.addWidget(self.preview)
        splitter.setSizes([560, 420])
        root.addWidget(splitter, 1)

        self.warning_view = QtWidgets.QPlainTextEdit()
        self.warning_view.setReadOnly(True)
        self.warning_view.setMaximumHeight(80)
        self.warning_view.setVisible(False)
        root.addWidget(self.warning_view)

        bottom = QtWidgets.QHBoxLayout()
        bottom.addStretch(1)
        create_btn = QtWidgets.QPushButton("Создать EPUB")
        create_btn.setDefault(True)
        create_btn.clicked.connect(self._create_epub)
        cancel_btn = QtWidgets.QPushButton("Отмена")
        cancel_btn.clicked.connect(self.reject)
        bottom.addWidget(create_btn)
        bottom.addWidget(cancel_btn)
        root.addLayout(bottom)

    def _load_source(self):
        try:
            self.result = extract_document_chapters(self.source_path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Ошибка импорта", str(exc))
            QtCore.QTimer.singleShot(0, self.reject)
            return

        self.chapters = list(self.result.chapters)
        self.title_edit.setText(self.result.title)
        self.author_edit.setText(f"Импортировано из {self.result.source_format.upper()}")
        self.summary_label.setText(
            f"<b>{self.source_path.name}</b><br>"
            f"Формат: {self.result.source_format.upper()}; найдено глав: {len(self.chapters)}."
        )
        if self.result.warnings:
            self.warning_view.setVisible(True)
            self.warning_view.setPlainText("\n".join(self.result.warnings))
        self._populate_table()

    def _populate_table(self):
        self.table.setRowCount(0)
        for chapter in self.chapters:
            row = self.table.rowCount()
            self.table.insertRow(row)
            check_item = QtWidgets.QTableWidgetItem("")
            check_item.setFlags(check_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            check_item.setCheckState(Qt.CheckState.Checked)
            check_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            title_item = QtWidgets.QTableWidgetItem(chapter.title)
            size_item = QtWidgets.QTableWidgetItem(f"{chapter.visible_size:,}")
            size_item.setFlags(size_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            size_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, 0, check_item)
            self.table.setItem(row, 1, title_item)
            self.table.setItem(row, 2, size_item)
        if self.table.rowCount():
            self.table.setCurrentCell(0, 1)
            self._update_preview(0, 1, -1, -1)

    def _update_preview(self, current_row: int, *_args):
        if current_row < 0 or current_row >= len(self.chapters):
            self.preview.clear()
            return
        self.preview.setHtml(self.chapters[current_row].html)

    def _set_all_checked(self, checked: bool):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(state)

    def _delete_selected(self):
        rows = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        for row in rows:
            if 0 <= row < len(self.chapters):
                del self.chapters[row]
        self._populate_table()

    def _merge_chapters(self):
        selected = self._selected_chapters()
        if not selected:
            return
        merged_parts = []
        for chapter in selected:
            merged_parts.append(f"<h1>{_escape(chapter.title)}</h1>\n{chapter.html}")
        merged = DocumentChapter(self.title_edit.text().strip() or self.source_path.stem, "\n".join(merged_parts))
        self.chapters = [merged]
        self._populate_table()

    def _selected_chapters(self) -> list[DocumentChapter]:
        selected: list[DocumentChapter] = []
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).checkState() != Qt.CheckState.Checked:
                continue
            title_item = self.table.item(row, 1)
            chapter = self.chapters[row]
            selected.append(
                DocumentChapter(
                    title=_safe_title(title_item.text() if title_item else chapter.title),
                    html=chapter.html,
                    source_label=chapter.source_label,
                )
            )
        return selected

    def _create_epub(self):
        if self.result is None:
            return
        selected = self._selected_chapters()
        if not selected:
            QtWidgets.QMessageBox.warning(self, "Нет глав", "Выберите хотя бы одну главу.")
            return
        try:
            output_path = create_epub_from_import(
                self.result,
                self.source_path,
                self.output_dir,
                title=self.title_edit.text().strip(),
                author=self.author_edit.text().strip(),
                chapters=selected,
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Ошибка создания EPUB", str(exc))
            return
        self.generated_epub_path = str(output_path)
        self.accept()

    def get_generated_epub_path(self) -> str | None:
        return self.generated_epub_path


def convert_source_to_epub_with_dialog(source_path: str, output_dir: str, parent=None) -> str | None:
    path = Path(source_path)
    if path.suffix.lower() == ".epub":
        return str(path)
    if path.suffix.lower() == ".txt":
        from .txt_importer import TxtImportWizardDialog

        wizard = TxtImportWizardDialog(str(path), output_dir, parent)
        if wizard.exec():
            return wizard.get_generated_epub_path()
        return None
    if not is_convertible_document(path):
        QtWidgets.QMessageBox.warning(parent, "Формат не поддерживается", f"Нельзя импортировать файл: {path.name}")
        return None

    dialog = DocumentImportDialog(str(path), output_dir, parent)
    if dialog.exec():
        return dialog.get_generated_epub_path()
    return None
