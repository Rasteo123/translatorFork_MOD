"""cluster-31: HtmlHighlighter (validation.py) и HtmlSyntaxHighlighter
(chapter_editor.py) — два независимых QSyntaxHighlighter для одной и той же
подсветки HTML, объединённые в gemini_translator.ui.widgets.regex_syntax_highlighter.

(а) Характеризационные тесты канонической реализации: крайние случаи, которые
    различали копии (атрибут вне тега, DOCTYPE, комментарии).
(б) Тест-маршрутизация: подменяем каноническую HtmlSyntaxHighlighter/
    RuleBasedSyntaxHighlighter и убеждаемся, что оба бывших места вызова
    (validation.py, chapter_editor.py) действительно используют канон, а не
    собственную копию.
"""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets
from PyQt6.QtGui import QTextDocument

from gemini_translator.ui.widgets.regex_syntax_highlighter import (
    HTML_PALETTE_DARK,
    HTML_PALETTE_LIGHT,
    HtmlSyntaxHighlighter,
    RuleBasedSyntaxHighlighter,
)


def _formats_at(document: QTextDocument, block_index: int = 0):
    """Возвращает список (start, length, QTextCharFormat) для блока."""
    block = document.findBlockByNumber(block_index)
    layout = block.layout()
    return [
        (fr.start, fr.length, fr.format)
        for fr in layout.formats()
    ]


def _color_at(document: QTextDocument, pos: int, block_index: int = 0) -> str | None:
    for start, length, fmt in _formats_at(document, block_index):
        if start <= pos < start + length:
            color = fmt.foreground().color()
            return color.name()
    return None


class CanonicalHtmlSyntaxHighlighterCharacterizationTests(unittest.TestCase):
    """(а) Крайние случаи, которые раньше различали HtmlHighlighter и
    HtmlSyntaxHighlighter."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _highlight(
        self, text: str, palette=None, highlight_partial_markup: bool = False
    ) -> QTextDocument:
        document = QTextDocument()
        highlighter = HtmlSyntaxHighlighter(
            document, palette=palette, highlight_partial_markup=highlight_partial_markup
        )
        document.setPlainText(text)
        highlighter.rehighlight()
        self._keepalive = highlighter  # держим ссылку, чтобы не собрал GC
        return document

    def test_attribute_pattern_outside_a_tag_is_not_highlighted(self):
        # Это и есть зафиксированное расхождение: старый HtmlHighlighter
        # (validation.py) гонял атрибутный regex по всему блоку независимо
        # и подсвечивал "attr=value" даже вне тега; выбранный канонический
        # алгоритм ищет атрибуты только внутри уже найденного тега.
        text = 'просто текст attr=value без тега'
        document = self._highlight(text)

        attr_pos = text.index("attr")
        self.assertIsNone(_color_at(document, attr_pos))

    def test_attribute_inside_a_real_tag_is_highlighted(self):
        text = '<p class="x">текст</p>'
        document = self._highlight(text, palette=HTML_PALETTE_DARK)

        attr_pos = text.index("class")
        self.assertEqual(_color_at(document, attr_pos), HTML_PALETTE_DARK["attribute"].lower())

    def test_string_value_inside_tag_is_highlighted(self):
        text = '<p class="x">текст</p>'
        document = self._highlight(text, palette=HTML_PALETTE_DARK)

        string_pos = text.index('"x"') + 1
        self.assertEqual(_color_at(document, string_pos), HTML_PALETTE_DARK["string"].lower())

    def test_comment_is_highlighted(self):
        text = '<!-- комментарий -->текст'
        document = self._highlight(text, palette=HTML_PALETTE_DARK)

        self.assertEqual(_color_at(document, 2), HTML_PALETTE_DARK["comment"].lower())

    def test_doctype_is_highlighted_for_both_palettes(self):
        # DOCTYPE раньше умел подсвечивать только validation.HtmlHighlighter;
        # при объединении это поведение перенесено в канон и работает для
        # обеих палитр (в т.ч. светлой из chapter_editor.py).
        text = "<!DOCTYPE html>\n<html></html>"
        for palette in (HTML_PALETTE_DARK, HTML_PALETTE_LIGHT):
            document = self._highlight(text, palette=palette)
            self.assertEqual(_color_at(document, 2), palette["doctype"].lower())

    def test_default_palette_is_dark(self):
        document = self._highlight("<p>x</p>")
        tag_pos = 1
        self.assertEqual(_color_at(document, tag_pos), HTML_PALETTE_DARK["tag"].lower())

    # --- highlight_partial_markup: диагностика битой разметки (major из ревью) ---
    #
    # Старый validation.HtmlHighlighter гонял `</?\w+` и `[<>]` по всему
    # блоку независимо от TAG_PATTERN, поэтому ловил битую/незакрытую/
    # разорванную переводом строки разметку — ровно то, ради чего рядом
    # живёт find_stray_angle_bracket_snippets. Канон по умолчанию этого не
    # делает (более точный алгоритм не подсвечивает то, что не похоже на
    # тег целиком); опциональный флаг highlight_partial_markup восстанавливает
    # именно эту диагностику для validation.py, не трогая поведение по
    # умолчанию (chapter_editor.py, тест test_attribute_pattern_outside_a_tag_is_not_highlighted).

    def test_partial_markup_disabled_by_default_stray_bracket_not_highlighted(self):
        text = "текст с < и > битой разметкой"
        document = self._highlight(text, palette=HTML_PALETTE_DARK)

        self.assertIsNone(_color_at(document, text.index("<")))
        self.assertIsNone(_color_at(document, text.index(">")))

    def test_partial_markup_stray_angle_brackets_highlighted_when_enabled(self):
        text = "текст с < и > битой разметкой"
        document = self._highlight(
            text, palette=HTML_PALETTE_DARK, highlight_partial_markup=True
        )

        self.assertEqual(
            _color_at(document, text.index("<")), HTML_PALETTE_DARK["tag"].lower()
        )
        self.assertEqual(
            _color_at(document, text.index(">")), HTML_PALETTE_DARK["tag"].lower()
        )

    def test_partial_markup_unterminated_tag_highlighted_when_enabled(self):
        text = '<p>незакрытый тег <b текст'
        document = self._highlight(
            text, palette=HTML_PALETTE_DARK, highlight_partial_markup=True
        )

        b_pos = text.index("<b")
        self.assertEqual(_color_at(document, b_pos), HTML_PALETTE_DARK["tag"].lower())
        self.assertEqual(
            _color_at(document, b_pos + 1), HTML_PALETTE_DARK["tag"].lower()
        )

    def test_partial_markup_tag_broken_across_lines_highlighted_when_enabled(self):
        document = QTextDocument()
        highlighter = HtmlSyntaxHighlighter(
            document, palette=HTML_PALETTE_DARK, highlight_partial_markup=True
        )
        document.setPlainText('<p\n  class="x">')
        highlighter.rehighlight()
        self._keepalive = highlighter

        # Первый блок: "<p" — открывающая часть тега, разорванная переносом
        # строки; TAG_PATTERN не видит закрывающую ">" в этом же блоке.
        self.assertEqual(
            _color_at(document, 0, block_index=0), HTML_PALETTE_DARK["tag"].lower()
        )
        # Второй блок: "  class=\"x\">" — одинокая ">" всё ещё диагностируется
        # как часть разметки (attr/string внутри разорванного тега — нет,
        # только tag_format для одиночных скобок/начал тега, как и было
        # заявлено в предложении ревьюера).
        second_line = '  class="x">'
        self.assertEqual(
            _color_at(document, second_line.index(">"), block_index=1),
            HTML_PALETTE_DARK["tag"].lower(),
        )


class RuleBasedSyntaxHighlighterTests(unittest.TestCase):
    """PunctuationHighlighter наследует от RuleBasedSyntaxHighlighter, не
    меняя собственных правил — здесь просто проверяем сам базовый класс."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_empty_rules_list_by_default(self):
        document = QTextDocument()
        highlighter = RuleBasedSyntaxHighlighter(document)
        self.assertEqual(highlighter.highlightingRules, [])


class RoutingTests(unittest.TestCase):
    """(б) До рефакторинга у каждого места вызова была своя копия — эти
    тесты обязаны падать. После рефакторинга оба места используют канон."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app.global_version = ""

    def test_validation_page_routes_html_highlighting_through_canon(self):
        from gemini_translator.ui.dialogs import validation

        with patch(
            "gemini_translator.ui.dialogs.validation.HtmlSyntaxHighlighter",
            side_effect=HtmlSyntaxHighlighter,
            create=True,
        ) as tracked, patch.object(
            validation.TranslationValidatorPage, "_perform_initial_cjk_scan"
        ):
            page = validation.TranslationValidatorPage(
                "/tmp/nonexistent-translations-cluster31",
                "/tmp/nonexistent-book-cluster31.epub",
                project_manager=None,
            )
            self.addCleanup(page.deleteLater)
            self.addCleanup(page.close)

        self.assertGreaterEqual(
            tracked.call_count,
            2,
            "TranslationValidatorPage должен создавать HtmlSyntaxHighlighter "
            "(канон) для view_original и view_translated, а не собственный "
            "HtmlHighlighter",
        )

    def test_validation_page_enables_partial_markup_diagnostics(self):
        # major из ревью: окно валидации существует именно для диагностики
        # битой разметки (см. find_stray_angle_bracket_snippets рядом) —
        # оно обязано включать highlight_partial_markup, иначе теряет
        # часть этой диагностики по сравнению со старым HtmlHighlighter.
        from gemini_translator.ui.dialogs import validation

        with patch.object(validation.TranslationValidatorPage, "_perform_initial_cjk_scan"):
            page = validation.TranslationValidatorPage(
                "/tmp/nonexistent-translations-cluster31-partial",
                "/tmp/nonexistent-book-cluster31-partial.epub",
                project_manager=None,
            )
            self.addCleanup(page.deleteLater)
            self.addCleanup(page.close)

        self.assertTrue(page.html_highlighter_orig.highlight_partial_markup)
        self.assertTrue(page.html_highlighter_trans.highlight_partial_markup)

    def test_validation_page_punctuation_highlighter_is_rule_based(self):
        from gemini_translator.ui.dialogs.validation import PunctuationHighlighter

        self.assertTrue(issubclass(PunctuationHighlighter, RuleBasedSyntaxHighlighter))

    def test_validation_module_has_no_own_html_highlighter_copy(self):
        from gemini_translator.ui.dialogs import validation

        self.assertFalse(
            hasattr(validation, "HtmlHighlighter"),
            "Копия HtmlHighlighter должна быть удалена из validation.py",
        )

    def test_validation_html_syntax_highlighter_is_the_canonical_class(self):
        from gemini_translator.ui.dialogs import validation

        # Ключевая проверка идентичности класса: имя "HtmlSyntaxHighlighter"
        # уже существует в chapter_editor.py как своя локальная копия, так
        # что одно лишь наличие атрибута с этим именем не доказывает
        # маршрутизацию через канон — нужна проверка `is`.
        self.assertTrue(
            hasattr(validation, "HtmlSyntaxHighlighter"),
            "validation.py должен импортировать канонический HtmlSyntaxHighlighter",
        )
        self.assertIs(validation.HtmlSyntaxHighlighter, HtmlSyntaxHighlighter)

    def test_chapter_editor_html_syntax_highlighter_is_the_canonical_class(self):
        from gemini_translator.ui.dialogs import chapter_editor

        # До рефакторинга chapter_editor.HtmlSyntaxHighlighter — это СВОЯ
        # локальная копия класса с тем же именем, а не канон: patch-based
        # тест ниже не может отличить копию от канона именно из-за
        # совпадения имён, поэтому различие ловим через identity-проверку.
        self.assertIs(chapter_editor.HtmlSyntaxHighlighter, HtmlSyntaxHighlighter)

    def test_chapter_editor_routes_html_highlighting_through_canon(self):
        # До рефакторинга имя HtmlSyntaxHighlighter совпадало у канона и у
        # локальной копии в chapter_editor.py, поэтому patch на
        # "chapter_editor.HtmlSyntaxHighlighter" с call-count не был
        # дискриминирующим: он подменял и ловил вызовы ЛОКАЛЬНОЙ копии
        # точно так же, как ловил бы вызовы канона (см. ревью cluster-31).
        # Различающая проверка — сравнение типа реального экземпляра с
        # классом, импортированным напрямую из канонического модуля.
        import tempfile

        from gemini_translator.ui.dialogs import chapter_editor

        with tempfile.TemporaryDirectory() as tmp:
            translated_path = os.path.join(tmp, "chapter.html")
            with open(translated_path, "w", encoding="utf-8") as fh:
                fh.write("<p>текст</p>")

            dialog = chapter_editor.ChapterEditorDialog(
                translated_path,
                original_epub_path=None,
                original_internal_path=None,
                project_manager=None,
            )
            self.addCleanup(dialog.deleteLater)
            self.addCleanup(dialog.close)

            self.assertIs(type(dialog._translated_highlighter), HtmlSyntaxHighlighter)
            self.assertIs(type(dialog._original_highlighter), HtmlSyntaxHighlighter)

    def test_chapter_editor_uses_light_palette_to_preserve_appearance(self):
        import tempfile

        from gemini_translator.ui.dialogs import chapter_editor

        with tempfile.TemporaryDirectory() as tmp:
            translated_path = os.path.join(tmp, "chapter.html")
            with open(translated_path, "w", encoding="utf-8") as fh:
                fh.write("<p>текст</p>")

            dialog = chapter_editor.ChapterEditorDialog(
                translated_path,
                original_epub_path=None,
                original_internal_path=None,
                project_manager=None,
            )
            self.addCleanup(dialog.deleteLater)
            self.addCleanup(dialog.close)

            self.assertEqual(
                dialog._translated_highlighter.tag_format.foreground().color().name(),
                HTML_PALETTE_LIGHT["tag"].lower(),
            )


if __name__ == "__main__":
    unittest.main()
