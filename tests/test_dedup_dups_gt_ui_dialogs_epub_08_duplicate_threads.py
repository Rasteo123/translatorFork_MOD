# -*- coding: utf-8 -*-
"""
Тесты на устранение дублирования из dups-gt_ui_dialogs_epub-08:

находка ui-dialogs-epub-consistency/design/8-epub-vs-html-duplicate-cleanup —
EpubDuplicateAnalysisThread/EpubDuplicateCleanupThread (zip в памяти) и
HtmlDuplicateAnalysisThread/HtmlDuplicateCleanupThread (файлы на диске)
дублировали одну и ту же логику построения chapter_infos, группировки
findings по tag_path и удаления тегов-повторов с сохранением h1.

До рефакторинга: в epub.py не было имён `_build_duplicate_chapter_info`,
`_group_duplicate_findings_by_tag_path`, `_remove_duplicate_findings_from_content`
— mock.patch.object ниже падал бы с AttributeError (RED).
После рефакторинга: все четыре Thread-класса вызывают эти общие функции
(GREEN).
"""
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

import gemini_translator.ui.dialogs.epub as epub_module
from gemini_translator.ui.dialogs.epub import (
    _build_duplicate_chapter_info,
    _group_duplicate_findings_by_tag_path,
    _remove_duplicate_findings_from_content,
    HtmlDuplicateAnalysisThread,
    HtmlDuplicateCleanupThread,
)


class _App:
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class GroupDuplicateFindingsCharacterizationTests(unittest.TestCase, _App):
    @classmethod
    def setUpClass(cls):
        _App.setUpClass()

    def test_groups_by_chapter_and_tag_path_prefers_tag_paths_plural(self):
        findings = [
            {"chapter_path": "ch1.xhtml", "tag_paths": [[0, 1], [0, 2]], "id": "a"},
            {"chapter_path": "ch1.xhtml", "tag_path": [0, 3], "id": "b"},
            {"chapter_path": "ch2.xhtml", "tag_paths": [[1]], "id": "c"},
            {"chapter_path": None, "tag_paths": [[0]], "id": "skip-no-chapter"},
            {"chapter_path": "ch3.xhtml", "tag_paths": [], "id": "skip-no-tags"},
        ]
        grouped = _group_duplicate_findings_by_tag_path(findings)

        self.assertEqual(set(grouped.keys()), {"ch1.xhtml", "ch2.xhtml"})
        self.assertEqual(grouped["ch1.xhtml"][(0, 1)]["id"], "a")
        self.assertEqual(grouped["ch1.xhtml"][(0, 2)]["id"], "a")
        self.assertEqual(grouped["ch1.xhtml"][(0, 3)]["id"], "b")
        self.assertEqual(grouped["ch2.xhtml"][(1,)]["id"], "c")


class RemoveDuplicateFindingsCharacterizationTests(unittest.TestCase, _App):
    @classmethod
    def setUpClass(cls):
        _App.setUpClass()

    def test_removes_tag_but_preserves_h1(self):
        content = "<html><body><h1>Заголовок</h1><p>Повтор</p><p>Оставить</p></body></html>"
        # tag_path к <h1> и к первому <p> внутри body (индексы дочерних тегов body)
        finding_map = {(0,): {"id": "h1-finding"}, (1,): {"id": "p-finding"}}

        updated, removed = _remove_duplicate_findings_from_content(content, finding_map)

        self.assertEqual(removed, 1, "должен быть удалён только <p>, а не <h1>")
        self.assertIn("<h1>Заголовок</h1>", updated)
        self.assertNotIn("Повтор", updated)
        self.assertIn("Оставить", updated)

    def test_preserves_xml_declaration_when_present(self):
        content = '<?xml version="1.0" encoding="utf-8"?>\n<html><body><p>Повтор</p></body></html>'
        finding_map = {(0,): {"id": "p-finding"}}

        updated, removed = _remove_duplicate_findings_from_content(content, finding_map)

        self.assertEqual(removed, 1)
        self.assertTrue(updated.lstrip().startswith("<?xml"))

    def test_no_removal_returns_original_content_and_zero(self):
        content = "<html><body><h1>Заголовок</h1></body></html>"
        finding_map = {(0,): {"id": "h1-only"}}

        updated, removed = _remove_duplicate_findings_from_content(content, finding_map)

        self.assertEqual(removed, 0)
        self.assertEqual(updated, content)


class BuildDuplicateChapterInfoCharacterizationTests(unittest.TestCase, _App):
    @classmethod
    def setUpClass(cls):
        _App.setUpClass()

    def test_returns_none_when_no_blocks(self):
        info = _build_duplicate_chapter_info(0, "ch1.xhtml", "<html><body></body></html>")
        self.assertIsNone(info)

    def test_returns_info_dict_with_blocks(self):
        content = "<html><body><p>Привет мир</p><p>Привет мир</p></body></html>"
        info = _build_duplicate_chapter_info(2, "dir/ch3.xhtml", content)
        self.assertIsNotNone(info)
        self.assertEqual(info["index"], 2)
        self.assertEqual(info["path"], "dir/ch3.xhtml")
        self.assertEqual(info["name"], "ch3.xhtml")
        self.assertTrue(info["blocks"])


class HtmlDuplicateThreadsRouteThroughSharedHelpersTests(unittest.TestCase, _App):
    """Тест-маршрутизация: до рефакторинга Html*Thread не вызывали общие
    функции (их не существовало в модуле), тест падал с AttributeError."""

    @classmethod
    def setUpClass(cls):
        _App.setUpClass()

    def test_html_analysis_thread_routes_through_build_chapter_info(self, ):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ch1.xhtml")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("<html><body><h1>Глава</h1></body></html>")

            thread = HtmlDuplicateAnalysisThread([path])
            calls = []

            def spy(idx, chapter_path, content):
                calls.append((idx, chapter_path))
                return _build_duplicate_chapter_info(idx, chapter_path, content)

            with mock.patch.object(epub_module, "_build_duplicate_chapter_info", spy):
                results = {}
                thread.analysis_finished.connect(lambda r: results.setdefault("r", r))
                thread.run()

            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][1], path)

    def test_html_cleanup_thread_routes_through_group_and_remove_helpers(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ch1.xhtml")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("<html><body><h1>Глава</h1><p>Повтор</p></body></html>")

            findings = [{"chapter_path": path, "tag_paths": [[1]]}]
            thread = HtmlDuplicateCleanupThread(findings)

            group_calls = []
            remove_calls = []

            def group_spy(fs):
                group_calls.append(fs)
                return _group_duplicate_findings_by_tag_path(fs)

            def remove_spy(content, finding_map):
                remove_calls.append((content, finding_map))
                return _remove_duplicate_findings_from_content(content, finding_map)

            with mock.patch.object(epub_module, "_group_duplicate_findings_by_tag_path", group_spy), \
                    mock.patch.object(epub_module, "_remove_duplicate_findings_from_content", remove_spy):
                results = {}
                thread.finished_cleanup.connect(lambda ok, msg: results.setdefault("r", (ok, msg)))
                thread.run()

            self.assertEqual(len(group_calls), 1)
            self.assertEqual(len(remove_calls), 1)
            with open(path, "r", encoding="utf-8") as fh:
                final_content = fh.read()
            self.assertNotIn("Повтор", final_content)
            self.assertIn("<h1>Глава</h1>", final_content)


if __name__ == "__main__":
    unittest.main()
