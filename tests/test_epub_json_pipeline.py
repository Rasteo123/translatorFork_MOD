import copy
import os
import tempfile
import unittest
import zipfile

from bs4 import BeautifulSoup

from gemini_translator.core.worker_helpers.taskers.base_processor import BaseTaskProcessor
from gemini_translator.utils.epub_json import (
    apply_transport_payload,
    apply_translation_payload,
    build_html_document_model,
    build_transport_payload,
    build_translation_payload,
    epub_to_json_model,
    estimate_translation_noise,
    json_model_to_epub,
    render_document_html,
)


CHAPTER_ONE = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head>
    <title>Chapter One</title>
    <link rel="stylesheet" type="text/css" href="styles.css"/>
  </head>
  <body class="chapter">
    <h1 id="ch1">Chapter 1</h1>
    <p class="lead">Hello <em>world</em> and <a href="#note-1" epub:type="noteref">1</a>.</p>
    <div class="scene">Nested <strong>text</strong> with an <img src="images/pic.jpg" alt="Scene image"/> inline image.</div>
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><text>1</text></svg>
    <!-- keep me -->
    <aside id="note-1" epub:type="footnote"><p>Footnote content.</p></aside>
  </body>
</html>
"""

CHAPTER_TWO = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head>
    <title>Chapter Two</title>
  </head>
  <body>
    <h2>Chapter 2</h2>
    <p>A second chapter with <span class="caps">mixed</span> inline markup.</p>
  </body>
</html>
"""


def _build_test_epub(epub_path):
    with zipfile.ZipFile(epub_path, "w") as epub:
        mimetype_info = zipfile.ZipInfo("mimetype")
        mimetype_info.compress_type = zipfile.ZIP_STORED
        epub.writestr(mimetype_info, "application/epub+zip")
        epub.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
""",
        )
        epub.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">urn:uuid:test-book</dc:identifier>
    <dc:title>JSON Pipeline Test</dc:title>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="styles" href="styles.css" media-type="text/css"/>
    <item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="ch2" href="ch2.xhtml" media-type="application/xhtml+xml"/>
    <item id="img" href="images/pic.jpg" media-type="image/jpeg"/>
  </manifest>
  <spine>
    <itemref idref="ch1"/>
    <itemref idref="ch2"/>
  </spine>
</package>
""",
        )
        epub.writestr(
            "OEBPS/nav.xhtml",
            """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Nav</title></head>
  <body>
    <nav epub:type="toc">
      <ol>
        <li><a href="ch1.xhtml">Chapter One</a></li>
        <li><a href="ch2.xhtml">Chapter Two</a></li>
      </ol>
    </nav>
  </body>
</html>
""",
        )
        epub.writestr("OEBPS/styles.css", "body { font-family: serif; }")
        epub.writestr("OEBPS/ch1.xhtml", CHAPTER_ONE)
        epub.writestr("OEBPS/ch2.xhtml", CHAPTER_TWO)
        epub.writestr("OEBPS/images/pic.jpg", b"\xff\xd8\xff\xdbJPEGDATA")


def _iter_text_fragments(blocks):
    for block in blocks:
        for attr_item in block.get("attrs_text", []):
            yield attr_item
        yield from _iter_inline_fragments(block.get("inlines", []))


def _iter_inline_fragments(inlines):
    for inline in inlines:
        if inline.get("type") == "text":
            yield inline
        for attr_item in inline.get("attrs_text", []):
            yield attr_item
        yield from _iter_inline_fragments(inline.get("children", []))


class _DummyWorker:
    pass


class EpubJsonPipelineTests(unittest.TestCase):
    def test_json_pipeline_selector_is_opt_in(self):
        processor = BaseTaskProcessor(_DummyWorker())
        self.assertFalse(processor._should_use_json_epub_pipeline())

        worker = _DummyWorker()
        worker.use_json_epub_pipeline = True
        processor = BaseTaskProcessor(worker)
        self.assertTrue(processor._should_use_json_epub_pipeline())

    def test_epub_json_roundtrip_preserves_entries_and_visible_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_epub = os.path.join(temp_dir, "source.epub")
            rebuilt_epub = os.path.join(temp_dir, "rebuilt.epub")
            _build_test_epub(source_epub)

            book_model = epub_to_json_model(source_epub)
            json_model_to_epub(book_model, rebuilt_epub)

            with zipfile.ZipFile(source_epub, "r") as source_zip, zipfile.ZipFile(rebuilt_epub, "r") as rebuilt_zip:
                self.assertEqual(source_zip.namelist(), rebuilt_zip.namelist())
                self.assertEqual(
                    source_zip.getinfo("mimetype").compress_type,
                    rebuilt_zip.getinfo("mimetype").compress_type,
                )
                self.assertEqual(
                    source_zip.read("OEBPS/images/pic.jpg"),
                    rebuilt_zip.read("OEBPS/images/pic.jpg"),
                )

                source_ch1 = source_zip.read("OEBPS/ch1.xhtml").decode("utf-8", "ignore")
                rebuilt_ch1 = rebuilt_zip.read("OEBPS/ch1.xhtml").decode("utf-8", "ignore")
                self.assertEqual(
                    BeautifulSoup(source_ch1, "html.parser").get_text(" ", strip=True),
                    BeautifulSoup(rebuilt_ch1, "html.parser").get_text(" ", strip=True),
                )
                self.assertIn('href="#note-1"', rebuilt_ch1)
                self.assertIn('alt="Scene image"', rebuilt_ch1)
                self.assertIn("<svg", rebuilt_ch1)

    def test_epub_json_roundtrip_preserves_utf16_xhtml_encoding(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_epub = os.path.join(temp_dir, "source_utf16.epub")
            rebuilt_epub = os.path.join(temp_dir, "rebuilt_utf16.epub")

            chapter_utf16 = (
                """<?xml version="1.0" encoding="utf-16"?>\n"""
                """<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Привет мир</p></body></html>"""
            ).encode("utf-16")

            with zipfile.ZipFile(source_epub, "w") as epub:
                mimetype_info = zipfile.ZipInfo("mimetype")
                mimetype_info.compress_type = zipfile.ZIP_STORED
                epub.writestr(mimetype_info, "application/epub+zip")
                epub.writestr(
                    "META-INF/container.xml",
                    """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
""",
                )
                epub.writestr(
                    "OEBPS/content.opf",
                    """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <manifest>
    <item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="ch1"/>
  </spine>
</package>
""",
                )
                epub.writestr("OEBPS/ch1.xhtml", chapter_utf16)

            book_model = epub_to_json_model(source_epub)
            json_model_to_epub(book_model, rebuilt_epub)

            with zipfile.ZipFile(rebuilt_epub, "r") as rebuilt_zip:
                rebuilt_chapter = rebuilt_zip.read("OEBPS/ch1.xhtml")

            self.assertTrue(
                rebuilt_chapter.startswith((b"\xff\xfe", b"\xfe\xff")),
                "Round-trip должен сохранить UTF-16 BOM для XHTML-главы.",
            )
            rebuilt_text = rebuilt_chapter.decode("utf-16")
            self.assertIn("Привет мир", rebuilt_text)
            self.assertIn('encoding="utf-16"', rebuilt_text)

    def test_translation_payload_applies_text_and_attr_translation(self):
        document_model = build_html_document_model(CHAPTER_ONE, document_id="OEBPS/ch1.xhtml")
        source_payload = build_translation_payload(document_model)
        translated_payload = copy.deepcopy(source_payload)

        for fragment in _iter_text_fragments(translated_payload["blocks"]):
            value = fragment.get("text") if "text" in fragment else fragment.get("value")
            if value == "Hello ":
                fragment["text"] = "Привет "
            if value == "Scene image":
                fragment["value"] = "Иллюстрация сцены"

        updated_document = apply_translation_payload(
            copy.deepcopy(document_model),
            translated_payload,
            source_payload=source_payload,
        )
        updated_html = render_document_html(updated_document)

        self.assertIn("Привет", updated_html)
        self.assertIn("<em>world</em>", updated_html)
        self.assertIn('href="#note-1"', updated_html)
        self.assertIn('alt="Иллюстрация сцены"', updated_html)

    def test_transport_payload_applies_text_and_attr_translation(self):
        document_model = build_html_document_model(CHAPTER_ONE, document_id="OEBPS/ch1.xhtml")
        source_payload = build_translation_payload(document_model)
        translated_transport = build_transport_payload(source_payload)

        for block in translated_transport["b"]:
            for inline in block.get("c", []):
                if inline.get("x") == "Hello ":
                    inline["x"] = "Bonjour "
                if inline.get("t") == "img" and inline.get("a"):
                    inline["a"][0] = "Image de scene"

        updated_document = apply_transport_payload(
            copy.deepcopy(document_model),
            translated_transport,
            source_payload=source_payload,
        )
        updated_html = render_document_html(updated_document)

        self.assertIn("Bonjour", updated_html)
        self.assertIn("<em>world</em>", updated_html)
        self.assertIn('href="#note-1"', updated_html)
        self.assertIn('alt="Image de scene"', updated_html)

    def test_json_payload_reduces_noise_for_realistic_fragment(self):
        document_model = build_html_document_model(CHAPTER_ONE, document_id="OEBPS/ch1.xhtml")
        payload = build_translation_payload(document_model)
        noise_report = estimate_translation_noise(CHAPTER_ONE, payload)

        self.assertGreater(noise_report["html_markup_chars"], 0)
        self.assertTrue(noise_report["json_is_less_noisy"])


if __name__ == "__main__":
    unittest.main()
