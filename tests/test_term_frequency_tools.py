import tempfile
import unittest
import zipfile
from pathlib import Path

from gemini_translator.utils.language_tools import GlossaryRegexService
from gemini_translator.utils.term_frequency_tools import GlossaryFrequencyWorker


def _write_epub(path, chapters):
    with zipfile.ZipFile(path, "w") as epub:
        for name, payload in chapters.items():
            epub.writestr(name, payload)


def _run_frequency_worker(epub_path, glossary):
    payloads = []
    errors = []
    worker = GlossaryFrequencyWorker(str(epub_path), glossary)
    worker.analysis_finished.connect(payloads.append)
    worker.error_occurred.connect(errors.append)

    worker.run()

    assert errors == []
    assert len(payloads) == 1
    return payloads[0]


class TermFrequencyToolsTests(unittest.TestCase):
    def test_frequency_worker_counts_single_occurrence_in_utf16_epub(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = Path(temp_dir) / "book.epub"
            chapter = (
                '<?xml version="1.0" encoding="utf-16"?>\n'
                '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                "<p>РедкийТермин встречается только здесь.</p>"
                "</body></html>"
            ).encode("utf-16")
            _write_epub(epub_path, {"OEBPS/ch1.xhtml": chapter})

            payload = _run_frequency_worker(
                epub_path,
                [{"original": "РедкийТермин", "rus": "", "note": ""}],
            )

        self.assertEqual(payload["terms"]["РедкийТермин"]["count"], 1)
        self.assertEqual(payload["terms"]["РедкийТермин"]["files"], ["OEBPS/ch1.xhtml"])

    def test_regex_service_counts_unicode_normalized_alpha_term_once(self):
        service = GlossaryRegexService({"Café Noir": {}})

        counts = service.count_matches("Cafe\u0301 Noir appears once.")

        self.assertEqual(counts["Café Noir"], 1)

    def test_regex_service_counts_dash_variants_as_same_alpha_term_once(self):
        service = GlossaryRegexService({"Silver-Eyed Witch": {}})

        counts = service.count_matches("The Silver\u2011Eyed Witch appears once.")

        self.assertEqual(counts["Silver-Eyed Witch"], 1)

    def test_frequency_worker_counts_alpha_suffix_forms_without_separate_glossary_terms(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = Path(temp_dir) / "book.epub"
            chapter = (
                '<?xml version="1.0" encoding="utf-8"?>\n'
                '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                "<p>Rune masters opened a school of Rune mastery.</p>"
                "<p>The Traditionalists argued over the traditionalist's vault.</p>"
                "</body></html>"
            ).encode("utf-8")
            _write_epub(epub_path, {"OEBPS/ch1.xhtml": chapter})

            payload = _run_frequency_worker(
                epub_path,
                [
                    {"original": "Rune master", "rus": "", "note": ""},
                    {"original": "Traditionalist", "rus": "", "note": ""},
                ],
            )

        self.assertEqual(payload["terms"]["Rune master"]["count"], 2)
        self.assertEqual(payload["terms"]["Traditionalist"]["count"], 2)

    def test_frequency_worker_does_not_double_count_explicit_alpha_variant_terms(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = Path(temp_dir) / "book.epub"
            chapter = (
                '<?xml version="1.0" encoding="utf-8"?>\n'
                '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                "<p>Rune masters arrived.</p>"
                "</body></html>"
            ).encode("utf-8")
            _write_epub(epub_path, {"OEBPS/ch1.xhtml": chapter})

            payload = _run_frequency_worker(
                epub_path,
                [
                    {"original": "Rune master", "rus": "", "note": ""},
                    {"original": "Rune masters", "rus": "", "note": ""},
                ],
            )

        self.assertEqual(payload["terms"]["Rune master"]["count"], 1)
        self.assertEqual(payload["terms"]["Rune masters"]["count"], 1)


if __name__ == "__main__":
    unittest.main()
