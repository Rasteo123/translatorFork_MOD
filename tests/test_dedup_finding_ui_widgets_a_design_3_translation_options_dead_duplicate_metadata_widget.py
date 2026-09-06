"""Tests for dedup finding:
finding-ui-widgets-a_design_3-translation-options-dead-duplicate-metadata

- TranslationOptionsWidget._build_epub_analysis_metadata was a byte-for-byte
  copy of the module-level _build_epub_analysis_metadata but was never called
  -> must be removed entirely.
- TranslationOptionsWidget._build_analysis_signature re-implemented the
  module-level build_chapter_analysis_signature instead of delegating to it
  -> must delegate.
"""

import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.ui.widgets import translation_options_widget as tow_module
from gemini_translator.ui.widgets.translation_options_widget import (
    TranslationOptionsWidget,
    _build_epub_analysis_metadata,
    build_chapter_analysis_signature,
)


class ModuleLevelMetadataCharacterizationTests(unittest.TestCase):
    """Characterization tests for the canonical module-level implementations."""

    def test_build_epub_analysis_metadata_returns_none_for_missing_file(self):
        self.assertIsNone(_build_epub_analysis_metadata("/no/such/path.epub"))

    def test_build_epub_analysis_metadata_returns_none_for_bad_zip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bad_path = Path(temp_dir) / "not_a_zip.epub"
            bad_path.write_bytes(b"not a real zip file")
            self.assertIsNone(_build_epub_analysis_metadata(str(bad_path)))

    def test_build_epub_analysis_metadata_computes_expected_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = Path(temp_dir) / "book.epub"
            with zipfile.ZipFile(epub_path, "w") as archive:
                archive.writestr("Text/ch1.xhtml", "<html>one</html>")
                archive.writestr("Text/ch2.html", "<html>two two</html>")
                archive.writestr("mimetype", "application/epub+zip")

            metadata = _build_epub_analysis_metadata(str(epub_path))

            self.assertIsNotNone(metadata)
            self.assertEqual(metadata["epub_name"], "book.epub")
            self.assertEqual(metadata["epub_size"], os.stat(epub_path).st_size)
            with zipfile.ZipFile(epub_path, "r") as archive:
                expected_checksum = sum(
                    info.file_size
                    for info in archive.infolist()
                    if info.filename.lower().endswith((".html", ".xhtml", ".htm"))
                )
            self.assertEqual(metadata["content_checksum"], expected_checksum)
            self.assertIn("metric", metadata)
            self.assertIn("version", metadata)

    def test_build_chapter_analysis_signature_returns_none_for_missing_path(self):
        self.assertIsNone(build_chapter_analysis_signature(["a.html"], None))
        self.assertIsNone(build_chapter_analysis_signature(["a.html"], "/no/such/file.epub"))

    def test_build_chapter_analysis_signature_reflects_stat_and_html_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = Path(temp_dir) / "book.epub"
            epub_path.write_bytes(b"stub content")
            stat = os.stat(epub_path)

            signature = build_chapter_analysis_signature(["a.html", "b.html"], str(epub_path))

            self.assertEqual(
                signature,
                (
                    os.path.abspath(str(epub_path)),
                    stat.st_mtime_ns,
                    stat.st_size,
                    ("a.html", "b.html"),
                ),
            )


class DeadDuplicateMethodRemovedTests(unittest.TestCase):
    def test_class_no_longer_defines_dead_duplicate_method(self):
        self.assertFalse(
            hasattr(TranslationOptionsWidget, "_build_epub_analysis_metadata"),
            "TranslationOptionsWidget must not carry its own dead copy of "
            "_build_epub_analysis_metadata; the module-level function is canonical.",
        )


class AnalysisSignatureRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _create_widget(self):
        widget = TranslationOptionsWidget()
        self.addCleanup(widget.close)
        return widget

    def test_build_analysis_signature_routes_through_canonical_function(self):
        widget = self._create_widget()
        widget.html_files = ["Text/ch1.xhtml", "Text/ch2.xhtml"]
        sentinel = ("sentinel", "signature")

        with patch.object(
            tow_module, "build_chapter_analysis_signature", return_value=sentinel
        ) as mocked:
            result = widget._build_analysis_signature("/some/book.epub")

        mocked.assert_called_once_with(widget.html_files, "/some/book.epub")
        self.assertEqual(result, sentinel)

    def test_build_analysis_signature_matches_canonical_output_end_to_end(self):
        widget = self._create_widget()
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = Path(temp_dir) / "book.epub"
            epub_path.write_bytes(b"stub content")
            widget.html_files = ["Text/ch1.xhtml"]

            expected = build_chapter_analysis_signature(widget.html_files, str(epub_path))
            actual = widget._build_analysis_signature(str(epub_path))

        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
