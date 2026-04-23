import io
import os
import tempfile
import unittest
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gemini_reader_v3 import (
    BookManager,
    READER_BOOK_FILE_FILTER,
    _is_supported_reader_book_path,
)

try:
    from docx import Document
except ImportError:
    Document = None


def _build_docx_bytes(paragraphs):
    document = Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


class ReaderBookFormatsTests(unittest.TestCase):
    def test_supported_book_filter_covers_uploader_formats(self):
        self.assertIn("*.zip", READER_BOOK_FILE_FILTER)
        self.assertIn("*.md", READER_BOOK_FILE_FILTER)
        self.assertTrue(_is_supported_reader_book_path("book.epub"))
        self.assertTrue(_is_supported_reader_book_path("book.zip"))
        self.assertTrue(_is_supported_reader_book_path("book.txt"))
        self.assertTrue(_is_supported_reader_book_path("book.md"))
        self.assertTrue(_is_supported_reader_book_path("book.html"))
        self.assertTrue(_is_supported_reader_book_path("book.htm"))
        self.assertFalse(_is_supported_reader_book_path("book.docx"))

    def test_book_manager_imports_marked_text_books(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            book_path = os.path.join(temp_dir, "sample.txt")
            with open(book_path, "w", encoding="utf-8") as file_obj:
                file_obj.write(
                    "# [Глава 1]\n"
                    "Первая строка главы.\n"
                    "\n"
                    "Второй абзац.\n"
                    "# [Глава 2]\n"
                    "Финальная глава.\n"
                )

            manager = BookManager(book_path, base_dir=os.path.join(temp_dir, "books"))

            self.assertEqual(len(manager.chapters), 2)
            self.assertEqual(manager.chapters[0].title, "Глава 1")
            self.assertEqual(manager.chapters[0].raw_text, "Первая строка главы.\nВторой абзац.")
            self.assertEqual(manager.chapters[1].raw_text, "Финальная глава.")

    def test_book_manager_imports_html_books_split_by_headings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            book_path = os.path.join(temp_dir, "sample.html")
            with open(book_path, "w", encoding="utf-8") as file_obj:
                file_obj.write(
                    "<html><head><title>Demo</title></head><body>"
                    "<h1>Глава 1</h1><p>Первый текст.</p><div>Еще текст.</div>"
                    "<h2>Глава 2</h2><p>Второй текст.</p>"
                    "</body></html>"
                )

            manager = BookManager(book_path, base_dir=os.path.join(temp_dir, "books"))

            self.assertEqual(len(manager.chapters), 2)
            self.assertEqual(manager.chapters[0].title, "Глава 1")
            self.assertEqual(manager.chapters[0].raw_text, "Первый текст.\nЕще текст.")
            self.assertEqual(manager.chapters[1].title, "Глава 2")
            self.assertEqual(manager.chapters[1].raw_text, "Второй текст.")

    def test_book_manager_chapter_status_snapshot_reads_markers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            book_path = os.path.join(temp_dir, "sample.txt")
            with open(book_path, "w", encoding="utf-8") as file_obj:
                file_obj.write("# [One]\nText one.\n# [Two]\nText two.\n")

            manager = BookManager(book_path, base_dir=os.path.join(temp_dir, "books"))
            with open(os.path.join(manager.book_dir, "Ch1.done"), "w", encoding="utf-8") as file_obj:
                file_obj.write("done")
            with open(os.path.join(manager.book_dir, "Ch2.skip"), "w", encoding="utf-8") as file_obj:
                file_obj.write("skip")
            with open(os.path.join(manager.book_dir, "Ch2.tts.txt"), "w", encoding="utf-8") as file_obj:
                file_obj.write("script")
            with open(os.path.join(manager.book_dir, "Chapter2.done"), "w", encoding="utf-8") as file_obj:
                file_obj.write("ignored")

            snapshot = manager.chapter_status_snapshot()

            self.assertEqual(snapshot["done"], {0})
            self.assertEqual(snapshot["skipped"], {1})
            self.assertEqual(snapshot["scripts"], {1})

    @unittest.skipIf(Document is None, "python-docx is not installed")
    def test_book_manager_imports_zip_docx_books_in_natural_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            book_path = os.path.join(temp_dir, "sample.zip")
            with zipfile.ZipFile(book_path, "w") as archive:
                archive.writestr("10.docx", _build_docx_bytes(["Десятая глава."]))
                archive.writestr("2.docx", _build_docx_bytes(["Вторая глава."]))

            manager = BookManager(book_path, base_dir=os.path.join(temp_dir, "books"))

            self.assertEqual(len(manager.chapters), 2)
            self.assertEqual(manager.chapters[0].title, "2")
            self.assertEqual(manager.chapters[0].raw_text, "Вторая глава.")
            self.assertEqual(manager.chapters[1].title, "10")
            self.assertEqual(manager.chapters[1].raw_text, "Десятая глава.")


if __name__ == "__main__":
    unittest.main()
