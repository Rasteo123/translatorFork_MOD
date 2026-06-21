import os
import tempfile
import unittest
import zipfile

from bs4 import BeautifulSoup

from gemini_translator.core.epub_deep_cleanup_helpers import (
    EpubDeepCssAnalyzer,
    clean_deep_html_content,
    clean_opf_file,
    find_opf_file,
    get_default_deep_cleanup_tag_rules,
    normalize_deep_cleanup_tag_rules,
    resolve_opf_href,
)
from gemini_translator.ui.dialogs.epub import (
    EpubDeepCleanupThread,
)


def _build_deep_cleanup_epub(epub_path):
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
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="styles" href="styles.css" media-type="text/css"/>
    <item id="font" href="fonts/book.woff" media-type="font/woff"/>
    <item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="ch1"/>
  </spine>
</package>
""",
        )
        epub.writestr(
            "OEBPS/nav.xhtml",
            """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><nav><ol>
  <li><a href="ch1.xhtml">Chapter One</a></li>
</ol></nav></body></html>
""",
        )
        epub.writestr(
            "OEBPS/styles.css",
            ".italic { font-style: italic; } .bold { font-weight: 700; }",
        )
        epub.writestr("OEBPS/fonts/book.woff", b"fontdata")
        epub.writestr(
            "OEBPS/ch1.xhtml",
            """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><link rel="stylesheet" href="styles.css"/></head>
  <body>
    <p>Hello <span class="italic">world</span> and <span class="bold">accent</span>.</p>
    <p>Keep <em>meaning</em> and <b>bold</b>; unwrap <a href="https://example.com/very/long/url">link text</a>.</p>
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><text>1</text></svg>
  </body>
</html>
""",
        )


class EpubDeepCleanupTests(unittest.TestCase):
    def test_tag_rules_are_normalized_without_qt_settings(self):
        rules = normalize_deep_cleanup_tag_rules({
            " A ": {"action": "UNWRAP", "preserve": True},
            "svg": ("remove", False),
            "bad": ("unsupported", False),
            42: ("remove", False),
        })

        self.assertEqual(rules["a"], ("unwrap", True))
        self.assertEqual(rules["svg"], ("remove", False))
        self.assertEqual(rules["bad"], ("keep", False))
        self.assertNotIn(42, rules)

    def test_css_analyzer_applies_semantic_formatting(self):
        analyzer = EpubDeepCssAnalyzer()
        styles = analyzer.parse_css_content(
            ".italic { font-style: italic; } "
            ".bold { font-weight: 700; } "
            ".center { text-align: center; }"
        )
        soup = BeautifulSoup(
            '<p><span class="italic">world</span><span class="bold center">accent</span></p>',
            "html.parser",
        )

        for element in soup.find_all(True):
            classes = element.get("class", [])
            applied_tags = set()
            for selector_key in classes:
                if selector_key in styles:
                    analyzer.apply_styles_to_element(element, styles[selector_key], soup, applied_tags)

        self.assertIn("<em>world</em>", str(soup))
        self.assertIn("<strong>accent</strong>", str(soup))
        self.assertIn('style="text-align: center;"', str(soup))

    def test_clean_deep_html_content_removes_noise_and_preserves_semantics(self):
        analyzer = EpubDeepCssAnalyzer()
        css_styles = analyzer.parse_css_content(".italic { font-style: italic; }")
        cleaned = clean_deep_html_content(
            """<?xml version="1.0" encoding="utf-8"?>
<html><head><meta name="viewport" content="x"/><script>bad()</script></head>
<body><!--note--><p data-junk="1">Hello <span class="italic">world</span></p><a href="x">link</a></body></html>
""",
            {
                "remove_css": True,
                "apply_css_styles": True,
            },
            get_default_deep_cleanup_tag_rules(),
            css_styles,
            analyzer,
        )

        self.assertTrue(cleaned.startswith('<?xml version="1.0" encoding="utf-8"?>'))
        self.assertIn("<em>world</em>", cleaned)
        self.assertIn("link", cleaned)
        self.assertNotIn("<a ", cleaned)
        self.assertNotIn("data-junk", cleaned)
        self.assertNotIn("<script", cleaned)
        self.assertNotIn("<!--note-->", cleaned)
        self.assertNotIn("viewport", cleaned)

    def test_clean_opf_file_removes_deleted_manifest_items_and_spine_refs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            os.makedirs(os.path.join(temp_dir, "META-INF"))
            os.makedirs(os.path.join(temp_dir, "OEBPS", "fonts"))
            container_path = os.path.join(temp_dir, "META-INF", "container.xml")
            opf_path = os.path.join(temp_dir, "OEBPS", "content.opf")

            with open(container_path, "w", encoding="utf-8") as fh:
                fh.write(
                    """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""
                )
            with open(opf_path, "w", encoding="utf-8") as fh:
                fh.write(
                    """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="styles" href="styles.css" media-type="text/css"/>
    <item id="font" href="fonts/book.woff" media-type="font/woff"/>
    <item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="styles"/>
    <itemref idref="ch1"/>
  </spine>
</package>
"""
                )

            self.assertEqual(find_opf_file(temp_dir), opf_path)
            self.assertEqual(resolve_opf_href("OEBPS/Text", "../styles/main.css"), "OEBPS/styles/main.css")

            removed_ids = clean_opf_file(
                temp_dir,
                opf_path,
                {"remove_css": True, "remove_nav": True, "remove_fonts": True},
                removed_css_files={"OEBPS/styles.css"},
                removed_nav_files=set(),
                removed_font_files={"OEBPS/fonts/book.woff"},
            )

            with open(opf_path, "r", encoding="utf-8") as fh:
                cleaned_opf = fh.read()
            self.assertEqual(removed_ids, {"nav", "styles", "font"})
            self.assertNotIn('href="nav.xhtml"', cleaned_opf)
            self.assertNotIn('href="styles.css"', cleaned_opf)
            self.assertNotIn('href="fonts/book.woff"', cleaned_opf)
            self.assertNotIn('idref="styles"', cleaned_opf)
            self.assertIn('href="ch1.xhtml"', cleaned_opf)
            self.assertIn('idref="ch1"', cleaned_opf)

    def test_recommended_cleanup_profile_preserves_text_and_removes_noise(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            _build_deep_cleanup_epub(epub_path)

            options = {
                "remove_css": True,
                "remove_nav": False,
                "remove_fonts": True,
                "apply_css_styles": True,
                "tag_rules": get_default_deep_cleanup_tag_rules(),
            }
            result = {}
            thread = EpubDeepCleanupThread(epub_path, options)
            thread.finished_cleanup.connect(
                lambda output_path, message: result.update(path=output_path, message=message)
            )

            thread.run()

            self.assertEqual(result.get("path"), epub_path, result.get("message"))
            with zipfile.ZipFile(epub_path, "r") as epub:
                names = set(epub.namelist())
                self.assertIn("OEBPS/nav.xhtml", names)
                self.assertNotIn("OEBPS/styles.css", names)
                self.assertNotIn("OEBPS/fonts/book.woff", names)

                opf = epub.read("OEBPS/content.opf").decode("utf-8", "ignore")
                self.assertIn('properties="nav"', opf)
                self.assertNotIn('href="styles.css"', opf)
                self.assertNotIn('href="fonts/book.woff"', opf)

                chapter = epub.read("OEBPS/ch1.xhtml").decode("utf-8", "ignore")
                self.assertIn("<em>world</em>", chapter)
                self.assertIn("<strong>accent</strong>", chapter)
                self.assertEqual(chapter.count("world"), 1)
                self.assertIn("<em>meaning</em>", chapter)
                self.assertIn("<strong>bold</strong>", chapter)
                self.assertNotIn("<a ", chapter)
                self.assertIn("link text", chapter)
                self.assertNotIn("<svg", chapter)


if __name__ == "__main__":
    unittest.main()
