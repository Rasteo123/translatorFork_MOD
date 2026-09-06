# -*- coding: utf-8 -*-
"""
Тесты для устранения находки dups-gt_utils_glossary_tools-40:
    Извлечение prefix/body/suffix + чанкинг главы продублировано между
    TaskPreparer._prepare_chunk_payloads и инлайн-блоком внутри
    TaskPreparer._prepare_individual_and_chunked_tasks.

(а) Характеризационные тесты фиксируют поведение канонической реализации
    (_prepare_chunk_payloads) на граничных случаях: <body> с атрибутами,
    отсутствие <body>.
(б) Тест-маршрутизация проверяет, что _prepare_individual_and_chunked_tasks
    действительно вызывает self._prepare_chunk_payloads (с уже открытым
    epub_zip) вместо собственной инлайн-копии логики извлечения/чанкинга.
    До рефакторинга этот тест падает: код в _prepare_individual_and_chunked_tasks
    не обращается к _prepare_chunk_payloads вовсе.
"""
import os
import tempfile
import unittest
import zipfile
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gemini_translator.utils.glossary_tools import TaskPreparer


def _make_epub(chapters: dict) -> str:
    with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as epub_file:
        epub_path = epub_file.name
    with zipfile.ZipFile(epub_path, "w") as epub_zip:
        for name, content in chapters.items():
            epub_zip.writestr(name, content)
    return epub_path


class PrepareChunkPayloadsCharacterizationTests(unittest.TestCase):
    """(а) Характеризация канонической реализации извлечения prefix/body/suffix."""

    def setUp(self):
        self._cleanup_paths = []

    def tearDown(self):
        for path in self._cleanup_paths:
            if os.path.exists(path):
                os.remove(path)

    def _preparer_for(self, epub_path, chapter_file, content, task_size_limit=10):
        settings = {
            "file_path": epub_path,
            "use_batching": False,
            "chunking": True,
            "sequential_translation": False,
            "task_size_limit": task_size_limit,
        }
        return TaskPreparer(settings, {chapter_file: len(content)})

    def test_extracts_prefix_and_suffix_around_body_with_attributes(self):
        long_body_text = "one two three. " * 800
        content = (
            '<html><head><title>T</title></head>'
            '<body class="chapter" id="c1">' + long_body_text + '</body></html>'
        )
        epub_path = _make_epub({"Text/ch1.xhtml": content})
        self._cleanup_paths.append(epub_path)

        preparer = self._preparer_for(epub_path, "Text/ch1.xhtml", content)
        with patch.object(TaskPreparer, "_chunk_target_chars_for_token_limit", return_value=500):
            payloads = preparer._prepare_chunk_payloads("Text/ch1.xhtml")

        self.assertGreater(len(payloads), 1, "ожидалось более одного чанка")
        self.assertTrue(all(p[0] == "epub_chunk" for p in payloads))
        prefix = payloads[0][6]
        suffix = payloads[0][7]
        self.assertTrue(
            content.lower().startswith(prefix.lower())
        )
        self.assertTrue(prefix.endswith(">"))
        self.assertIn('<body class="chapter" id="c1">'.lower(), prefix.lower())
        self.assertEqual(suffix, "</body></html>")

    def test_no_body_tag_treats_whole_content_as_body(self):
        long_text = "word " * 2000
        content = "<div>" + long_text + "</div>"
        epub_path = _make_epub({"Text/ch1.xhtml": content})
        self._cleanup_paths.append(epub_path)

        preparer = self._preparer_for(epub_path, "Text/ch1.xhtml", content)
        with patch.object(TaskPreparer, "_chunk_target_chars_for_token_limit", return_value=500):
            payloads = preparer._prepare_chunk_payloads("Text/ch1.xhtml")

        self.assertGreater(len(payloads), 1)
        for payload in payloads:
            self.assertEqual(payload[6], "")  # prefix
            self.assertEqual(payload[7], "")  # suffix

    def test_single_chunk_collapses_to_plain_epub_payload(self):
        content = "<html><body><p>short</p></body></html>"
        epub_path = _make_epub({"Text/ch1.xhtml": content})
        self._cleanup_paths.append(epub_path)

        preparer = self._preparer_for(epub_path, "Text/ch1.xhtml", content, task_size_limit=10_000)
        payloads = preparer._prepare_chunk_payloads("Text/ch1.xhtml")

        self.assertEqual(payloads, [("epub", epub_path, "Text/ch1.xhtml")])

    def test_accepts_already_open_epub_zip_handle(self):
        """_prepare_chunk_payloads должен уметь переиспользовать уже открытый ZipFile,
        а не только открывать свой собственный (см. suggested_fix находки)."""
        long_body_text = "one two three. " * 800
        content = "<html><body>" + long_body_text + "</body></html>"
        epub_path = _make_epub({"Text/ch1.xhtml": content})
        self._cleanup_paths.append(epub_path)

        preparer = self._preparer_for(epub_path, "Text/ch1.xhtml", content)
        with patch.object(TaskPreparer, "_chunk_target_chars_for_token_limit", return_value=500):
            with open(epub_path, "rb") as epub_file, zipfile.ZipFile(epub_file, "r") as epub_zip:
                payloads_shared = preparer._prepare_chunk_payloads("Text/ch1.xhtml", epub_zip=epub_zip)
            payloads_own = preparer._prepare_chunk_payloads("Text/ch1.xhtml")

        self.assertEqual(payloads_shared, payloads_own)
        self.assertGreater(len(payloads_shared), 1)


class PrepareIndividualAndChunkedTasksRoutingTests(unittest.TestCase):
    """(б) Тест-маршрутизация: _prepare_individual_and_chunked_tasks обязан идти
    через self._prepare_chunk_payloads, а не через собственную инлайн-копию."""

    def setUp(self):
        self._cleanup_paths = []

    def tearDown(self):
        for path in self._cleanup_paths:
            if os.path.exists(path):
                os.remove(path)

    def test_routes_large_chapters_through_prepare_chunk_payloads(self):
        long_body = "<html><body>" + ("one two three. " * 800) + "</body></html>"
        epub_path = _make_epub({
            "Text/ch1.xhtml": "<html><body><p>small</p></body></html>",
            "Text/ch2.xhtml": long_body,
        })
        self._cleanup_paths.append(epub_path)

        settings = {
            "file_path": epub_path,
            "use_batching": False,
            "chunking": True,
            "sequential_translation": False,
            "task_size_limit": 800,
        }
        preparer = TaskPreparer(settings, {
            "Text/ch1.xhtml": 300,
            "Text/ch2.xhtml": len(long_body),
        })

        sentinel_payloads = [("epub_chunk", epub_path, "Text/ch2.xhtml", "chunk-a", 0, 2, "PRE", "SUF")]
        with patch.object(
            TaskPreparer, "_prepare_chunk_payloads", return_value=sentinel_payloads
        ) as mock_chunk_payloads:
            final_payloads = preparer._prepare_individual_and_chunked_tasks(
                ["Text/ch1.xhtml", "Text/ch2.xhtml"]
            )

        mock_chunk_payloads.assert_called_once()
        call_args, call_kwargs = mock_chunk_payloads.call_args
        self.assertEqual(call_args[0], "Text/ch2.xhtml")
        # Общий (уже открытый) ZipFile должен передаваться, чтобы не открывать
        # архив заново на каждую главу.
        epub_zip_arg = call_kwargs.get("epub_zip") or (call_args[1] if len(call_args) > 1 else None)
        self.assertIsInstance(epub_zip_arg, zipfile.ZipFile)

        self.assertEqual(
            final_payloads,
            [("epub", epub_path, "Text/ch1.xhtml")] + sentinel_payloads,
        )

    def test_end_to_end_output_matches_direct_prepare_chunk_payloads(self):
        """Без моков: результат _prepare_individual_and_chunked_tasks для главы,
        нуждающейся в чанкинге, должен буквально совпадать с тем, что вернул бы
        отдельный вызов _prepare_chunk_payloads для той же главы."""
        long_body = "<html><body>" + ("one two three. " * 800) + "</body></html>"
        epub_path = _make_epub({"Text/ch1.xhtml": long_body})
        self._cleanup_paths.append(epub_path)

        settings = {
            "file_path": epub_path,
            "use_batching": False,
            "chunking": True,
            "sequential_translation": False,
            "task_size_limit": 800,
        }
        preparer = TaskPreparer(settings, {"Text/ch1.xhtml": len(long_body)})

        expected = preparer._prepare_chunk_payloads("Text/ch1.xhtml")
        actual = preparer._prepare_individual_and_chunked_tasks(["Text/ch1.xhtml"])

        self.assertEqual(actual, expected)
        self.assertGreater(len(actual), 1)


if __name__ == "__main__":
    unittest.main()
