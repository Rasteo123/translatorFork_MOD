# -*- coding: utf-8 -*-
"""
Характеризационные тесты для cluster-41: _extract_h1_title (epub.py) дублировал
_extract_first_epub_heading_text_regex (epub_tools.py).

Кейсы ниже — экстремальные случаи, которые РАЗЛИЧАЛИ две копии до рефакторинга:
- test_h2_heading_is_found: старая копия ловила только <h1>, каноническая —
  h1/h2/h3 (divergence #1 из cluster-41.json).
- test_nested_tags_glue_is_avoided_with_space: старая копия склеивала соседние
  текстовые фрагменты без разделителя (`<span>1</span>Title` -> "1Title"),
  каноническая вставляет пробел (divergence #2).
- test_closing_tag_with_trailing_whitespace: реальный баг канонической
  реализации, обнаруженный при переносе — regex требовал точное "</h1>" и не
  находил валидный (HTML5-корректный) "</h1 >" с пробелом перед ">". Старая
  копия эту форму поддерживала. Исправлено в epub_tools.py вместе с этим
  рефакторингом (behavior_choice: сохранить поддержку пробела перед ">").
"""
import unittest

from gemini_translator.utils.epub_tools import _extract_first_epub_heading_text_regex


class ExtractFirstEpubHeadingTextRegexCharacterizationTests(unittest.TestCase):
    def _extract(self, html):
        return _extract_first_epub_heading_text_regex(html)

    def test_plain_title(self):
        self.assertEqual(
            self._extract("<html><body><h1>Глава 1</h1></body></html>"),
            "Глава 1",
        )

    def test_h2_heading_is_found(self):
        # Divergence #1: старая копия _extract_h1_title искала ТОЛЬКО h1 и
        # вернула бы "" для этой главы; каноническая функция матчит h2/h3.
        self.assertEqual(
            self._extract("<html><body><h2>Глава 2</h2></body></html>"),
            "Глава 2",
        )

    def test_h3_heading_is_found(self):
        self.assertEqual(
            self._extract("<html><body><h3>Глава 3</h3></body></html>"),
            "Глава 3",
        )

    def test_nested_tags_glue_is_avoided_with_space(self):
        # Divergence #2: внутренние теги заменяются на пробел, а не на "",
        # чтобы "<span>1</span>Название" не склеилось в "1Название".
        html = '<h1 class="chapter"><span>1</span>Название</h1>'
        self.assertEqual(self._extract(html), "1 Название")

    def test_entities_and_br(self):
        html = "<h1>Глава&nbsp;3<br/>Продолжение &amp; конец</h1>"
        self.assertEqual(self._extract(html), "Глава 3 Продолжение & конец")

    def test_closing_tag_with_trailing_whitespace(self):
        # Реальный баг канонической реализации (не был учтён в исходном
        # cluster-41.json divergence): "</h1 >" — валидный HTML5 закрывающий
        # тег с пробелом перед ">" — раньше не матчился regex'ом и функция
        # тихо возвращала "". Старая копия _extract_h1_title такой синтаксис
        # поддерживала явно (`</h1\s*>`). Исправлено в epub_tools.py.
        html = "<h1>\n  Глава 4\n</h1 >"
        self.assertEqual(self._extract(html), "Глава 4")

    def test_no_heading_returns_empty(self):
        self.assertEqual(self._extract("<html><body><p>текст</p></body></html>"), "")
        self.assertEqual(self._extract(""), "")
        self.assertEqual(self._extract(None), "")

    def test_multiline_and_spaced_closing_tag_with_attributes(self):
        html = '<h1 id="c4">\n  Глава 4\n</h1 >'
        self.assertEqual(self._extract(html), "Глава 4")


if __name__ == "__main__":
    unittest.main()
