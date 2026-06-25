import os
import tempfile
import unittest
import zipfile

from gemini_translator.utils.epub_tools import (
    CHAPTER_SIZE_CACHE_METRIC,
    CHAPTER_SIZE_CACHE_VERSION,
    TASK_SIZE_UNIT_CHARS,
    estimate_epub_chapter_input_size,
    estimate_epub_chapter_input_tokens,
    get_epub_chapter_sizes_with_cache,
)
from gemini_translator.utils.helpers import estimate_gemini_tokens


class _ProjectManagerStub:
    def __init__(self, cache=None):
        self.cache = cache
        self.saved_cache = None

    def load_size_cache(self):
        return self.cache

    def save_size_cache(self, cache_data):
        self.saved_cache = cache_data
        self.cache = cache_data


class GeminiTokenAnalysisTests(unittest.TestCase):
    def _write_epub(self, html_by_name):
        fd, epub_path = tempfile.mkstemp(suffix=".epub")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(epub_path) and os.remove(epub_path))
        with zipfile.ZipFile(epub_path, "w") as archive:
            for name, content in html_by_name.items():
                archive.writestr(name, content)
        return epub_path

    def test_estimate_gemini_tokens_uses_language_weights(self):
        self.assertEqual(estimate_gemini_tokens("a" * 400), 100)
        self.assertEqual(estimate_gemini_tokens("я" * 22), 10)
        self.assertEqual(estimate_gemini_tokens("你" * 15), 10)

    def test_epub_input_size_can_use_legacy_character_unit(self):
        content = "<html><body><p>" + ("Hello " * 20) + "</p></body></html>"

        self.assertEqual(
            estimate_epub_chapter_input_size(content),
            estimate_epub_chapter_input_tokens(content),
        )
        self.assertEqual(
            estimate_epub_chapter_input_size(content, TASK_SIZE_UNIT_CHARS),
            len(content),
        )

    def test_epub_chapter_size_cache_stores_gemini_input_tokens(self):
        content = "<html><body><p>" + ("Hello world. " * 20) + "</p></body></html>"
        epub_path = self._write_epub({"Text/ch1.xhtml": content})
        project_manager = _ProjectManagerStub()

        sizes, cache_hit = get_epub_chapter_sizes_with_cache(
            project_manager,
            epub_path,
            return_cache_status=True,
        )

        self.assertFalse(cache_hit)
        self.assertEqual(
            sizes["Text/ch1.xhtml"],
            estimate_epub_chapter_input_tokens(content),
        )
        metadata = project_manager.saved_cache["metadata"]
        self.assertEqual(metadata["metric"], CHAPTER_SIZE_CACHE_METRIC)
        self.assertEqual(metadata["version"], CHAPTER_SIZE_CACHE_VERSION)

    def test_old_matching_cache_recalculates_without_breaking_project_identity(self):
        content = "<html><body><p>" + ("Привет мир. " * 20) + "</p></body></html>"
        epub_path = self._write_epub({"Text/ch1.xhtml": content})
        epub_size = os.path.getsize(epub_path)
        with zipfile.ZipFile(epub_path, "r") as archive:
            checksum = sum(info.file_size for info in archive.infolist())
        old_cache = {
            "metadata": {
                "epub_name": os.path.basename(epub_path),
                "epub_size": epub_size,
                "content_checksum": checksum,
            },
            "sizes": {"Text/ch1.xhtml": len(content)},
        }
        project_manager = _ProjectManagerStub(old_cache)

        sizes, identity_valid = get_epub_chapter_sizes_with_cache(
            project_manager,
            epub_path,
            return_cache_status=True,
        )

        self.assertTrue(identity_valid)
        self.assertEqual(
            sizes["Text/ch1.xhtml"],
            estimate_epub_chapter_input_tokens(content),
        )
        self.assertEqual(
            project_manager.saved_cache["metadata"]["metric"],
            CHAPTER_SIZE_CACHE_METRIC,
        )


if __name__ == "__main__":
    unittest.main()
