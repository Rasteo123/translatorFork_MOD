"""Общая инфраструктура для QSyntaxHighlighter в текстовых редакторах приложения.

Раньше здесь было два независимых класса, решавших одну и ту же задачу —
подсветку HTML-разметки в QTextEdit — с разными алгоритмами и палитрами:
``validation.HtmlHighlighter`` (тёмная палитра, каждое правило гонится по
всему блоку текста независимо от остальных) и
``chapter_editor.HtmlSyntaxHighlighter`` (светлая палитра, сначала находится
тег целиком, и только внутри него ищутся атрибуты/строки — точнее, меньше
ложных срабатываний). Модуль объединяет их в одну каноническую реализацию.

``RuleBasedSyntaxHighlighter`` — базовый класс с плоским списком правил
(``self.highlightingRules``: пары ``QRegularExpression``/``QTextCharFormat``),
применяемых по всему блоку независимо друг от друга. От него наследует
``PunctuationHighlighter`` (validation.py) — эта часть поведения не менялась.

``HtmlSyntaxHighlighter`` — каноническая подсветка HTML, использует более
точный алгоритм из бывшего chapter_editor.HtmlSyntaxHighlighter (тег целиком,
затем атрибуты/строки внутри него) и параметризуется палитрой цветов, чтобы
validation.py и chapter_editor.py могли сохранить свой прежний внешний вид.
DOCTYPE-подсветка (была только в validation.HtmlHighlighter) перенесена в
канон и включена по умолчанию для обеих палитр.

Точный алгоритм жертвует диагностикой БИТОЙ разметки: старый
validation.HtmlHighlighter гонял ``</?\\w+`` и ``[<>]`` по всему блоку
независимо от остального, поэтому подсвечивал одиночные незакрытые скобки,
незакрытые теги и теги, разорванные переводом строки — то, ради чего рядом
с окном валидации живёт find_stray_angle_bracket_snippets. Опциональный флаг
``highlight_partial_markup`` (выключен по умолчанию, включён в validation.py)
восстанавливает именно эту диагностику: после прохода по целым тегам он
дополнительно красит tag_format всё, что попало под ``</?\\w+``/``[<>]`` и не
покрыто уже найденным тегом/комментарием/DOCTYPE.
"""

from __future__ import annotations

import re

from PyQt6.QtCore import QRegularExpression
from PyQt6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat


class RuleBasedSyntaxHighlighter(QSyntaxHighlighter):
    """QSyntaxHighlighter с плоским списком независимых правил.

    Подклассы заполняют ``self.highlightingRules`` в ``__init__`` парами
    ``(QRegularExpression, QTextCharFormat)``; в ``highlightBlock`` каждое
    правило применяется по всему тексту блока независимо от остальных
    (правила не знают друг о друге и могут пересекаться).
    """

    def __init__(self, document=None):
        super().__init__(document)
        self.highlightingRules: list[tuple[QRegularExpression, QTextCharFormat]] = []

    def highlightBlock(self, text: str) -> None:
        for pattern, fmt in self.highlightingRules:
            iterator = pattern.globalMatch(text)
            while iterator.hasNext():
                match = iterator.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), fmt)


# Палитра из бывшего validation.HtmlHighlighter (тёмная тема).
HTML_PALETTE_DARK: dict[str, str] = {
    "tag": "#569CD6",
    "attribute": "#9CDCFE",
    "string": "#CE9178",
    "comment": "#6A9955",
    "doctype": "#4EC9B0",
}

# Палитра из бывшего chapter_editor.HtmlSyntaxHighlighter (светлая тема).
# DOCTYPE в исходной реализации не подсвечивался — при объединении берём
# тон, гармонирующий с остальной палитрой (совпадает с цветом тега).
HTML_PALETTE_LIGHT: dict[str, str] = {
    "tag": "#0f5c7a",
    "attribute": "#7b3fb7",
    "string": "#b54708",
    "comment": "#687076",
    "doctype": "#0f5c7a",
}


class HtmlSyntaxHighlighter(RuleBasedSyntaxHighlighter):
    """Каноническая подсветка HTML-разметки в QTextEdit.

    Алгоритм (из бывшего chapter_editor.HtmlSyntaxHighlighter, выбран как
    более точный): сначала помечаются комментарии и DOCTYPE по всему блоку,
    затем находится каждый тег целиком, и только внутри найденного тега
    ищутся атрибуты и строковые значения — то есть "attr=value" вне тега
    подсветить нельзя, в отличие от прежнего validation.HtmlHighlighter,
    гонявшего атрибутный и строковый паттерны по всему блоку независимо.

    ``highlight_partial_markup`` (по умолчанию выключен) восстанавливает
    одну конкретную часть старого поведения validation.HtmlHighlighter —
    диагностику битой разметки: одиночные "<"/">" вне тегов, незакрытые
    теги, теги, разорванные переводом строки. Включается только для
    диапазонов, не покрытых уже найденным тегом/комментарием/DOCTYPE, и
    красит их тем же tag_format — атрибуты и строки вне целого тега при
    этом по-прежнему не подсвечиваются (это самостоятельный, осознанный
    фикс прежнего бага, не связанный с этим флагом).
    """

    TAG_PATTERN = re.compile(r"</?[A-Za-z0-9:_-]+(?:\s+[^>]*?)?>")
    ATTRIBUTE_PATTERN = re.compile(r"\b[A-Za-z_:][-A-Za-z0-9_:.]*(?=\=)")
    STRING_PATTERN = re.compile(r"\"[^\"]*\"|'[^']*'")
    COMMENT_PATTERN = re.compile(r"<!--.*?-->")
    DOCTYPE_PATTERN = re.compile(r"<!DOCTYPE[^>]+>", re.IGNORECASE)
    # Правила старого validation.HtmlHighlighter для диагностики битой
    # разметки — начало тега без гарантированного закрытия и одиночная
    # угловая скобка сама по себе.
    PARTIAL_TAG_START_PATTERN = re.compile(r"</?\w+")
    PARTIAL_BRACKET_PATTERN = re.compile(r"[<>]")

    def __init__(
        self,
        document=None,
        palette: dict[str, str] | None = None,
        highlight_partial_markup: bool = False,
    ):
        super().__init__(document)
        palette = palette or HTML_PALETTE_DARK
        self.highlight_partial_markup = highlight_partial_markup

        self.tag_format = QTextCharFormat()
        self.tag_format.setForeground(QColor(palette["tag"]))
        self.tag_format.setFontWeight(QFont.Weight.Bold)

        self.attr_format = QTextCharFormat()
        self.attr_format.setForeground(QColor(palette["attribute"]))

        self.string_format = QTextCharFormat()
        self.string_format.setForeground(QColor(palette["string"]))

        self.comment_format = QTextCharFormat()
        self.comment_format.setForeground(QColor(palette["comment"]))
        self.comment_format.setFontItalic(True)

        self.doctype_format = QTextCharFormat()
        self.doctype_format.setForeground(QColor(palette["doctype"]))

    def highlightBlock(self, text: str) -> None:
        # Контракт базового класса: highlightingRules подклассов (если
        # когда-нибудь появятся) тоже применяются. Список пуст по
        # умолчанию, так что вызов ничего не стоит.
        super().highlightBlock(text)

        covered_ranges: list[tuple[int, int]] = []

        for match in self.COMMENT_PATTERN.finditer(text):
            self.setFormat(match.start(), match.end() - match.start(), self.comment_format)
            covered_ranges.append((match.start(), match.end()))

        for match in self.DOCTYPE_PATTERN.finditer(text):
            self.setFormat(match.start(), match.end() - match.start(), self.doctype_format)
            covered_ranges.append((match.start(), match.end()))

        for match in self.TAG_PATTERN.finditer(text):
            self.setFormat(match.start(), match.end() - match.start(), self.tag_format)
            covered_ranges.append((match.start(), match.end()))

            inner_text = match.group(0)
            inner_offset = match.start()
            for attr_match in self.ATTRIBUTE_PATTERN.finditer(inner_text):
                self.setFormat(
                    inner_offset + attr_match.start(),
                    attr_match.end() - attr_match.start(),
                    self.attr_format,
                )
            for string_match in self.STRING_PATTERN.finditer(inner_text):
                self.setFormat(
                    inner_offset + string_match.start(),
                    string_match.end() - string_match.start(),
                    self.string_format,
                )

        if self.highlight_partial_markup:
            self._highlight_partial_markup(text, covered_ranges)

    def _highlight_partial_markup(
        self, text: str, covered_ranges: list[tuple[int, int]]
    ) -> None:
        for pattern in (self.PARTIAL_TAG_START_PATTERN, self.PARTIAL_BRACKET_PATTERN):
            for match in pattern.finditer(text):
                start, end = match.start(), match.end()
                if any(start < c_end and end > c_start for c_start, c_end in covered_ranges):
                    continue
                self.setFormat(start, end - start, self.tag_format)
