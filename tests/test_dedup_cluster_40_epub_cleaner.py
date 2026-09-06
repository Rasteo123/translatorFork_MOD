# -*- coding: utf-8 -*-
"""
Характеризационные тесты + тест-маршрутизация для cluster-40: replace_in_tag/
remove_attr/flush_buffer были продублированы между EpubCleanupThread.run()
(gemini_translator/ui/dialogs/epub.py) и EpubCleaner (gemini_translator/utils/
epub_cleaner.py). Класс EpubCleaner нигде не был подключён (0 импортов),
живой была только инлайновая копия в epub.py.

Канон: gemini_translator.utils.epub_cleaner.EpubCleaner.apply_fixes(). Часть
"а" ниже характеризует его поведение на всех типах фиксов (num_mismatch,
attr, orphans, br, force_renumber_sequential) — это тот код, который раньше
был продублирован дословно внутри EpubCleanupThread.run().

Часть "б" — тест-маршрутизация: EpubCleanupThread.run() должен звать
EpubCleaner, а не свою собственную копию. До рефакторинга в модуле epub.py
нет имени `EpubCleaner`, поэтому mock.patch.object ниже падает с
AttributeError — тест RED. После рефакторинга имя импортировано и
используется в run() — тест GREEN.
"""
import os
import tempfile
import unittest
import zipfile
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.utils.epub_cleaner import EpubCleaner, clean_epub


def _make_epub(path, files):
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)


def _read_epub(path):
    with zipfile.ZipFile(path, "r") as zf:
        return {name: zf.read(name).decode("utf-8") for name in zf.namelist()}


class EpubCleanerCharacterizationTests(unittest.TestCase):
    """(a) Поведение канонической EpubCleaner на каждом типе фикса."""

    def test_num_mismatch_replaces_fragment_in_header_and_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            epub_path = os.path.join(tmp, "book.epub")
            _make_epub(epub_path, {
                "ch1.xhtml": (
                    "<html><head><title>Chapter 5</title></head>"
                    "<body><h1>Chapter 5</h1><p>Text</p></body></html>"
                ),
            })
            cleaner = EpubCleaner(epub_path)
            processed = cleaner.apply_fixes([{
                "type": "num_mismatch",
                "file": "ch1.xhtml",
                "old_fragment": "5",
                "new_number": "7",
            }])

            self.assertEqual(processed, 1)
            content = _read_epub(epub_path)["ch1.xhtml"]
            self.assertIn("<h1>Chapter 7</h1>", content)
            self.assertIn("<title>Chapter 7</title>", content)
            self.assertNotIn("Chapter 5", content)

    def test_attr_fix_strips_matching_attribute_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            epub_path = os.path.join(tmp, "book.epub")
            _make_epub(epub_path, {
                "ch1.xhtml": (
                    '<html><body><img src="a.png" onerror="alert(1)">'
                    '<img src="b.png" onerror="safe()"></body></html>'
                ),
            })
            cleaner = EpubCleaner(epub_path)
            cleaner.apply_fixes([{
                "type": "attr",
                "tag": "img",
                "attr": "onerror",
                "value": "alert(1)",
            }])

            content = _read_epub(epub_path)["ch1.xhtml"]
            # Атрибут с совпавшим значением вырезан...
            self.assertIn('<img src="a.png">', content)
            # ...а с другим значением - остаётся нетронутым.
            self.assertIn('onerror="safe()"', content)

    def test_orphans_fix_wraps_loose_body_text_in_paragraph(self):
        with tempfile.TemporaryDirectory() as tmp:
            epub_path = os.path.join(tmp, "book.epub")
            _make_epub(epub_path, {
                "ch1.xhtml": (
                    "<html><body><h1>Title</h1>Loose orphan text"
                    "<p>Existing paragraph</p></body></html>"
                ),
            })
            cleaner = EpubCleaner(epub_path)
            cleaner.apply_fixes([{"type": "orphans"}])

            content = _read_epub(epub_path)["ch1.xhtml"]
            self.assertIn("<p>Loose orphan text</p>", content)
            self.assertIn("<p>Existing paragraph</p>", content)
            self.assertIn("<h1>Title</h1>", content)

    def test_br_fix_splits_paragraph_on_line_break(self):
        with tempfile.TemporaryDirectory() as tmp:
            epub_path = os.path.join(tmp, "book.epub")
            _make_epub(epub_path, {
                "ch1.xhtml": "<html><body><p>Line1<br>Line2</p></body></html>",
            })
            cleaner = EpubCleaner(epub_path)
            cleaner.apply_fixes([{"type": "br"}])

            content = _read_epub(epub_path)["ch1.xhtml"]
            self.assertNotIn("<br", content)
            self.assertIn("<p>Line1</p>", content)
            self.assertIn("<p>Line2</p>", content)

    def test_force_renumber_sequential_updates_headers_in_filename_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            epub_path = os.path.join(tmp, "book.epub")
            _make_epub(epub_path, {
                "ch2.xhtml": "<html><body><h1>Chapter 9</h1></body></html>",
                "ch1.xhtml": "<html><body><h1>Chapter 9</h1></body></html>",
            })
            processed = clean_epub(epub_path, [{"type": "force_renumber_sequential"}])

            self.assertEqual(processed, 2)
            content = _read_epub(epub_path)
            self.assertIn("<h1>Chapter 1</h1>", content["ch1.xhtml"])
            self.assertIn("<h1>Chapter 2</h1>", content["ch2.xhtml"])


class EpubCleanupThreadRoutesThroughEpubCleanerTests(unittest.TestCase):
    """(b) Тест-маршрутизация: run() обязан идти через каноническую EpubCleaner."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_run_delegates_to_canonical_epub_cleaner(self):
        import gemini_translator.ui.dialogs.epub as epub_module
        from gemini_translator.ui.dialogs.epub import EpubCleanupThread

        with tempfile.TemporaryDirectory() as tmp:
            epub_path = os.path.join(tmp, "book.epub")
            _make_epub(epub_path, {
                "ch1.xhtml": (
                    "<html><head><title>Chapter 5</title></head>"
                    "<body><h1>Chapter 5</h1></body></html>"
                ),
            })
            fixes = [{
                "type": "num_mismatch",
                "file": "ch1.xhtml",
                "old_fragment": "5",
                "new_number": "7",
            }]

            calls = []
            real_cls = EpubCleaner

            class SpyEpubCleaner(real_cls):
                def __init__(self, epub_path_arg):
                    calls.append(epub_path_arg)
                    super().__init__(epub_path_arg)

            thread = EpubCleanupThread(epub_path, fixes)
            results = []
            thread.finished_cleanup.connect(lambda path, msg: results.append((path, msg)))

            with mock.patch.object(epub_module, "EpubCleaner", SpyEpubCleaner):
                thread.run()

            self.assertEqual(calls, [epub_path],
                              "EpubCleanupThread.run() должен создавать EpubCleaner(virtual_epub_path)")
            self.assertEqual(len(results), 1)
            result_path, result_msg = results[0]
            self.assertEqual(result_path, epub_path)
            self.assertIn("Обработано файлов: 1", result_msg)

            content = _read_epub(epub_path)["ch1.xhtml"]
            self.assertIn("<h1>Chapter 7</h1>", content)


if __name__ == "__main__":
    unittest.main()
