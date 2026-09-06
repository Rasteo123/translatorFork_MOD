# -*- coding: utf-8 -*-
"""
Характеризационные тесты + тест-маршрутизация для cluster-39: EpubAnalysisThread.run()
(gemini_translator/ui/dialogs/epub.py) дословно дублировал логику EpubAnalyzer.analyze()
(gemini_translator/utils/epub_analyzer.py) — идентичные regex-паттерны (RE_TAG_OPENER/
RE_ATTRIBUTES/RE_BR/RE_H1/RE_TITLE), идентичные пороги (ANALYSIS_THRESHOLD=0.90,
MIN_TAG_COUNT_FOR_ANALYSIS=5/1) и идентичные имена переменных (stats, br_files_count,
orphaned_text_count, num_mismatches). Класс EpubAnalyzer нигде не был подключён (0
импортов вне собственного файла), живой была только инлайновая копия в epub.py.

Найденное расхождение (divergence) между копиями:
  - EpubAnalyzer._check_numbering() оборачивает вызов recognize_number() для КАЖДОЙ
    культуры в свой try/except и продолжает со следующей культурой при ошибке.
  - Старая копия в EpubAnalysisThread.run() вызывала recognize_number() без try/except
    для конкретной культуры: исключение в одной культуре прерывало весь анализ главы
    (br/orphans/attrs для неё тоже терялись), потому что перехватывалось только
    внешним `except Exception: continue` вокруг всей главы.
  behavior_choice: канон — поведение EpubAnalyzer (более отказоустойчивое: сбой одной
  культуры не должен терять анализ остальной главы). Тест
  test_numbering_recognizer_failure_in_one_culture_does_not_abort_chapter_analysis
  характеризует именно это.

  - Старая копия определяла доступность BS4 через `'bs4' in sys.modules` (полагаясь на
    то, что bs4 уже импортирован где-то в процессе), тогда как EpubAnalyzer использует
    собственный BS4_AVAILABLE, выставленный честным try/except ImportError на уровне
    модуля. behavior_choice: канон — BS4_AVAILABLE модуля epub_analyzer (тот же подход,
    что уже применён для EpubCleaner/EpubCleanupThread в cluster-40).

Канон: gemini_translator.utils.epub_analyzer.EpubAnalyzer.analyze().

Часть "а" ниже характеризует поведение канона на каждом виде проблемы (br, orphans,
attr-порог, num_mismatch с изолированным сбоем одной культуры) — это код, который
раньше был продублирован дословно внутри EpubAnalysisThread.run().

Часть "б" — тест-маршрутизация: EpubAnalysisThread.run() должен звать EpubAnalyzer,
а не свою собственную копию. До рефакторинга в модуле epub.py нет имени
`EpubAnalyzer`, поэтому mock.patch.object ниже падает с AttributeError — тест RED.
После рефакторинга имя импортировано и используется в run() — тест GREEN.
"""
import os
import tempfile
import unittest
import zipfile
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.utils.epub_analyzer import EpubAnalyzer, analyze_epub


def _make_epub(path, files):
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)


class EpubAnalyzerCharacterizationTests(unittest.TestCase):
    """(a) Поведение канонической EpubAnalyzer на каждом виде проблемы."""

    def test_br_tags_are_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            epub_path = os.path.join(tmp, "book.epub")
            _make_epub(epub_path, {
                "ch1.xhtml": "<html><body><p>Line1<br>Line2</p></body></html>",
                "ch2.xhtml": "<html><body><p>No break here</p></body></html>",
            })
            issues = analyze_epub(epub_path)
            br_issue = next((i for i in issues if i["type"] == "br"), None)
            self.assertIsNotNone(br_issue)
            self.assertEqual(br_issue["count"], 1)

    def test_orphaned_text_outside_paragraph_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            epub_path = os.path.join(tmp, "book.epub")
            _make_epub(epub_path, {
                "ch1.xhtml": (
                    "<html><body><h1>Title</h1>Loose orphan text"
                    "<p>Existing paragraph</p></body></html>"
                ),
            })
            issues = analyze_epub(epub_path)
            orphan_issue = next((i for i in issues if i["type"] == "orphans"), None)
            self.assertIsNotNone(orphan_issue)
            self.assertEqual(orphan_issue["count"], 1)

    def test_attribute_present_on_at_least_90_percent_of_tags_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            epub_path = os.path.join(tmp, "book.epub")
            # 5 <p> тегов (минимум для анализа), 5 из 5 несут одинаковый junk-class.
            paragraphs = "".join(
                f'<p class="junk">Text {i}</p>' for i in range(5)
            )
            _make_epub(epub_path, {
                "ch1.xhtml": f"<html><body>{paragraphs}</body></html>",
            })
            issues = analyze_epub(epub_path)
            attr_issue = next((i for i in issues if i["type"] == "attr"), None)
            self.assertIsNotNone(attr_issue)
            self.assertEqual(attr_issue["tag"], "p")
            self.assertEqual(attr_issue["attr"], "class")
            self.assertEqual(attr_issue["value"], "junk")

    def test_attribute_below_threshold_or_below_min_count_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            epub_path = os.path.join(tmp, "book.epub")
            # Только 2 <p> тега — меньше MIN_TAG_COUNT_FOR_ANALYSIS (5).
            _make_epub(epub_path, {
                "ch1.xhtml": (
                    '<html><body><p class="junk">A</p>'
                    '<p class="junk">B</p></body></html>'
                ),
            })
            issues = analyze_epub(epub_path)
            attr_issue = next((i for i in issues if i["type"] == "attr"), None)
            self.assertIsNone(attr_issue)

    def test_numbering_recognizer_failure_in_one_culture_does_not_abort_chapter_analysis(self):
        """
        Расхождение с прежней копией: сбой recognize_number() для ОДНОЙ культуры
        не должен терять весь анализ главы (br/attrs всё равно должны быть собраны).
        Канон (EpubAnalyzer) оборачивает вызов на каждую культуру в свой try/except.
        """
        import gemini_translator.utils.epub_analyzer as analyzer_module

        class FakeCulture:
            English = "en"
            Chinese = "zh"
            Japanese = "ja"

        def fake_recognize_number(text, culture):
            if culture == FakeCulture.English:
                raise RuntimeError("simulated recognizer failure for English culture")
            return []

        with tempfile.TemporaryDirectory() as tmp:
            epub_path = os.path.join(tmp, "book.epub")
            _make_epub(epub_path, {
                # Имя файла содержит ровно одну группу цифр -> target_number = 5.
                "05.xhtml": "<html><body><h1>Chapter Five</h1><p>Text<br>More</p></body></html>",
            })

            with mock.patch.object(analyzer_module, "RECOGNIZERS_AVAILABLE", True), \
                 mock.patch.object(analyzer_module, "Culture", FakeCulture, create=True), \
                 mock.patch.object(analyzer_module, "recognize_number", fake_recognize_number, create=True):
                analyzer = EpubAnalyzer(epub_path)
                issues = analyzer.analyze()

            # Несмотря на сбой английской культуры, br всё равно должен быть найден -
            # значит анализ главы не был прерван исключением одной культуры.
            br_issue = next((i for i in issues if i["type"] == "br"), None)
            self.assertIsNotNone(
                br_issue,
                "Сбой recognize_number() в одной культуре не должен прерывать "
                "остальной анализ главы (br/attrs) - это и есть выбранное поведение канона",
            )


class EpubAnalysisThreadRoutesThroughEpubAnalyzerTests(unittest.TestCase):
    """(b) Тест-маршрутизация: run() обязан идти через каноническую EpubAnalyzer."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_run_delegates_to_canonical_epub_analyzer(self):
        import gemini_translator.ui.dialogs.epub as epub_module
        from gemini_translator.ui.dialogs.epub import EpubAnalysisThread

        with tempfile.TemporaryDirectory() as tmp:
            epub_path = os.path.join(tmp, "book.epub")
            _make_epub(epub_path, {
                "ch1.xhtml": "<html><body><p>Line1<br>Line2</p></body></html>",
            })
            chapters = ["ch1.xhtml"]

            calls = []
            real_cls = EpubAnalyzer

            class SpyEpubAnalyzer(real_cls):
                def __init__(self, epub_path_arg, chapters_list_arg=None):
                    calls.append((epub_path_arg, chapters_list_arg))
                    super().__init__(epub_path_arg, chapters_list_arg)

            thread = EpubAnalysisThread(epub_path, chapters)
            results = []
            thread.analysis_finished.connect(lambda issues: results.append(issues))

            with mock.patch.object(epub_module, "EpubAnalyzer", SpyEpubAnalyzer):
                thread.run()

            self.assertEqual(
                calls, [(epub_path, chapters)],
                "EpubAnalysisThread.run() должен создавать "
                "EpubAnalyzer(virtual_epub_path, chapters_list)",
            )
            self.assertEqual(len(results), 1)
            issues = results[0]
            br_issue = next((i for i in issues if i["type"] == "br"), None)
            self.assertIsNotNone(br_issue)
            self.assertEqual(br_issue["count"], 1)


if __name__ == "__main__":
    unittest.main()
