"""pcluster-53: strip_html_to_text/get_chapter_fingerprint переизобретают
extract_visible_text.

Канонический хелпер: gemini_translator.utils.html_text.extract_visible_text_normalized
(extract_visible_text() + схлопывание пробелов + опц. усечение).

Характеризационные тесты фиксируют его поведение (в т.ч. случай, где старые
копии расходились с extract_visible_text: script/style не исключались, а
get_chapter_fingerprint терял пробел между соседними тегами). Тесты
маршрутизации патчат канонический символ и проверяют, что каждое бывшее
место дублирования действительно вызывает его.
"""

import os
import unittest
import zipfile
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gemini_translator.utils import html_text
from gemini_translator.utils.document_importer import strip_html_to_text
from gemini_translator.utils.epub_tools import get_chapter_fingerprint
from gemini_translator.ui.dialogs.validation import (
    TranslationValidatorDialog,
    _line_review_visible_text,
    _normalize_problem_term_text,
)


class ExtractVisibleTextNormalizedCharacterizationTests(unittest.TestCase):
    def test_empty_and_falsy_values_return_empty_string(self):
        for value in (None, "", 0, False):
            with self.subTest(value=value):
                self.assertEqual(html_text.extract_visible_text_normalized(value), "")

    def test_collapses_internal_whitespace(self):
        self.assertEqual(
            html_text.extract_visible_text_normalized("<p>Hello   \n\n  world</p>"),
            "Hello world",
        )

    def test_excludes_script_and_style_like_canonical_extractor(self):
        html = '<script>var s = "secret";</script><style>.a{}</style><p>Видимый</p>'
        self.assertEqual(html_text.extract_visible_text_normalized(html), "Видимый")

    def test_inserts_separator_between_adjacent_block_tags(self):
        # Расхождение get_chapter_fingerprint: soup.get_text() без separator
        # схлопывал бы это в "HelloWorld".
        self.assertEqual(
            html_text.extract_visible_text_normalized("<p>Hello</p><p>World</p>"),
            "Hello World",
        )

    def test_limit_truncates_and_strips_trailing_whitespace(self):
        result = html_text.extract_visible_text_normalized("abcdefgh ij", limit=8)
        self.assertEqual(result, "abcdefgh")
        self.assertLessEqual(len(result), 8)

    def test_from_html_false_skips_html_parsing(self):
        # Уже готовый превью-текст не должен повторно парситься как HTML.
        result = html_text.extract_visible_text_normalized(
            "<not a real tag>", from_html=False
        )
        self.assertEqual(result, "<not a real tag>")


class GetChapterFingerprintLengthRegressionTests(unittest.TestCase):
    def test_length_counts_separator_between_adjacent_tags(self, tmp_path=None):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            epub_path = os.path.join(tmp, "book.epub")
            html = (
                '<html><body><h1>T</h1><p>Hello</p><p>World</p></body></html>'
            )
            with zipfile.ZipFile(epub_path, "w") as archive:
                archive.writestr("OEBPS/ch1.xhtml", html)

            with zipfile.ZipFile(epub_path, "r") as archive:
                fingerprint = get_chapter_fingerprint(archive, "OEBPS/ch1.xhtml")

        # "T Hello World" (13) а не "THelloWorld" (11) — заголовок и абзацы
        # разделены пробелами, как и даёт extract_visible_text.
        self.assertEqual(fingerprint["length"], len("T Hello World"))


class RoutingThroughCanonicalHelperTests(unittest.TestCase):
    """Каждое бывшее место дублирования обязано звать canonical helper."""

    def test_document_importer_strip_html_to_text_routes_through_canonical(self):
        with patch(
            "gemini_translator.utils.document_importer.extract_visible_text_normalized",
            return_value="ROUTED",
        ) as mocked:
            result = strip_html_to_text("<p>x</p>")
        mocked.assert_called_once()
        self.assertEqual(result, "ROUTED")

    def test_epub_tools_get_chapter_fingerprint_routes_through_canonical(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            epub_path = os.path.join(tmp, "book.epub")
            with zipfile.ZipFile(epub_path, "w") as archive:
                archive.writestr("OEBPS/ch1.xhtml", "<p>x</p>")

            with zipfile.ZipFile(epub_path, "r") as archive:
                with patch(
                    "gemini_translator.utils.epub_tools.extract_visible_text_normalized",
                    return_value="ROUTED",
                ) as mocked:
                    fingerprint = get_chapter_fingerprint(archive, "OEBPS/ch1.xhtml")

        mocked.assert_called_once()
        self.assertEqual(fingerprint["length"], len("ROUTED"))

    def test_validation_normalize_problem_term_text_routes_through_canonical(self):
        with patch(
            "gemini_translator.ui.dialogs.validation.extract_visible_text_normalized",
            return_value="ROUTED",
        ) as mocked:
            result = _normalize_problem_term_text("<p>x</p>")
        mocked.assert_called_once()
        self.assertEqual(result, "ROUTED")

    def test_validation_line_review_visible_text_routes_through_canonical(self):
        with patch(
            "gemini_translator.ui.dialogs.validation.extract_visible_text_normalized",
            return_value="ROUTED",
        ) as mocked:
            result = _line_review_visible_text("<p>x</p>")
        mocked.assert_called_once()
        self.assertEqual(result, "ROUTED")

    def test_validator_normalize_navigation_search_text_routes_through_canonical(self):
        with patch(
            "gemini_translator.ui.dialogs.validation.extract_visible_text_normalized",
            return_value="ROUTED",
        ) as mocked:
            result = TranslationValidatorDialog._normalize_navigation_search_text(
                "<p>x</p>", html_to_text=True, limit=50
            )
        mocked.assert_called_once_with("<p>x</p>", from_html=True, limit=50)
        self.assertEqual(result, "ROUTED")


if __name__ == "__main__":
    unittest.main()
