import os
import tempfile
import unittest
import zipfile

from gemini_translator.ui.dialogs.epub import (
    EpubDeepCleanupThread,
    get_default_deep_cleanup_tag_rules,
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
