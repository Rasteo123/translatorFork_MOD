# -*- coding: utf-8 -*-
"""
Тест-маршрутизация для cluster-41: подсказка заголовка главы в
EpubHtmlSelectorDialog._scan_chapter_titles_batch должна извлекаться через
каноническую _extract_first_epub_heading_text_regex из epub_tools.py, а не
через отдельную самодельную копию (_extract_h1_title).

До рефакторинга: в модуле epub.py нет имени
`_extract_first_epub_heading_text_regex`, поэтому mock.patch.object ниже
падает с AttributeError — тест RED.
После рефакторинга: имя импортировано и вызывается напрямую из
`_scan_chapter_titles_batch` — тест GREEN.
"""
import os
import tempfile
import unittest
import zipfile
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

import gemini_translator.ui.dialogs.epub as epub_module
from gemini_translator.ui.dialogs.epub import EpubHtmlSelectorDialog
from gemini_translator.utils.epub_tools import _extract_first_epub_heading_text_regex


class ScanChapterTitlesRoutesThroughCanonicalHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _make_dialog(self, epub_path, chapters):
        dialog = EpubHtmlSelectorDialog.__new__(EpubHtmlSelectorDialog)
        dialog.virtual_epub_path = epub_path
        dialog.all_chapters = chapters
        dialog._chapter_title_cache = {}
        dialog._title_scan_index = 0
        # Обходим Qt C++-инициализацию (не нужна для этого юнит-теста):
        # _refresh_tooltips_for_paths делает getattr(self, 'list_widget', None),
        # но сам объект QDialog без __init__ бросает RuntimeError на любой
        # доступ к атрибуту — кладём значение прямо в __dict__.
        dialog.__dict__['list_widget'] = None
        return dialog

    def test_scan_batch_calls_canonical_regex_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            epub_path = os.path.join(tmp, "book.epub")
            with zipfile.ZipFile(epub_path, "w") as zf:
                zf.writestr("ch1.xhtml", "<html><body><h1>Глава 1</h1></body></html>")
            dialog = self._make_dialog(epub_path, ["ch1.xhtml"])

            calls = []

            def spy(html_content, *args, **kwargs):
                calls.append(html_content)
                return _extract_first_epub_heading_text_regex(html_content, *args, **kwargs)

            with mock.patch.object(epub_module, "_extract_first_epub_heading_text_regex", spy):
                dialog._scan_chapter_titles_batch()

            self.assertEqual(len(calls), 1, "заголовок должен извлекаться через каноническую функцию")
            self.assertEqual(dialog._chapter_title_cache.get("ch1.xhtml"), "Глава 1")


if __name__ == "__main__":
    unittest.main()
