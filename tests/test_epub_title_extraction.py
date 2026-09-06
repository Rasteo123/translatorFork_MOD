import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

# cluster-41 dedup: EpubHtmlSelectorDialog._extract_h1_title дублировал
# gemini_translator.utils.epub_tools._extract_first_epub_heading_text_regex
# и был удалён; подсказка заголовка теперь строится напрямую канонической
# функцией (см. tests/test_dedup_cluster_41_characterization.py и
# tests/test_dedup_cluster_41_routing.py для полного покрытия расхождений
# между копиями и маршрутизации вызова).
from gemini_translator.utils.epub_tools import _extract_first_epub_heading_text_regex


class ExtractH1TitleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _extract(self, html):
        return _extract_first_epub_heading_text_regex(html)

    def test_plain_title(self):
        self.assertEqual(self._extract("<html><body><h1>Глава 1</h1></body></html>"),
                         "Глава 1")

    def test_nested_tags_and_attributes(self):
        html = '<h1 class="chapter"><span>Глава</span> <em>2</em>: Начало</h1>'
        # Каноническая функция заменяет внутренние теги на пробел (а не на ""),
        # чтобы не склеивать соседний текст без разделителя — отсюда пробел
        # перед ":" по сравнению со старой копией _extract_h1_title.
        self.assertEqual(self._extract(html), "Глава 2 : Начало")

    def test_entities_and_br(self):
        html = "<h1>Глава&nbsp;3<br/>Продолжение &amp; конец</h1>"
        self.assertEqual(self._extract(html), "Глава 3 Продолжение & конец")

    def test_multiline_and_spaced_closing_tag(self):
        html = "<h1>\n  Глава 4\n</h1 >"
        self.assertEqual(self._extract(html), "Глава 4")

    def test_no_h1_returns_empty(self):
        self.assertEqual(self._extract("<html><body><p>текст</p></body></html>"), "")
        self.assertEqual(self._extract(""), "")
        self.assertEqual(self._extract(None), "")


if __name__ == "__main__":
    unittest.main()
