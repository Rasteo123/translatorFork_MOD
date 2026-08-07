import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

import gemini_translator.ui.dialogs.epub as epub_module
from gemini_translator.ui.dialogs.epub import EpubHtmlSelectorDialog


class ExtractH1TitleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _extract(self, html):
        return EpubHtmlSelectorDialog._extract_h1_title(html)

    def test_plain_title(self):
        self.assertEqual(self._extract("<html><body><h1>Глава 1</h1></body></html>"),
                         "Глава 1")

    def test_nested_tags_and_attributes(self):
        html = '<h1 class="chapter"><span>Глава</span> <em>2</em>: Начало</h1>'
        self.assertEqual(self._extract(html), "Глава 2: Начало")

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

    def test_does_not_parse_whole_document_with_beautifulsoup(self):
        """Полный BS4-парсинг каждой главы стоил ~3 мс × число глав (секунды
        на большой книге) только ради подсказки — заголовок должен извлекаться
        регулярным выражением."""
        def _boom(*args, **kwargs):
            raise AssertionError("BeautifulSoup must not be used for h1 titles")

        with mock.patch.object(epub_module, "BeautifulSoup", _boom):
            self.assertEqual(
                self._extract("<html><body><h1>Глава 5</h1></body></html>"),
                "Глава 5")


if __name__ == "__main__":
    unittest.main()
