import unittest

from bs4 import BeautifulSoup

from gemini_translator.ui.dialogs.epub import (
    analyze_duplicate_findings,
    extract_duplicate_review_blocks,
)


def _build_chapter_info(index, path, html):
    soup = BeautifulSoup(html, "html.parser")
    return {
        "index": index,
        "path": path,
        "name": path.split("/")[-1],
        "blocks": extract_duplicate_review_blocks(soup),
    }


class EpubDuplicateCleanupTests(unittest.TestCase):
    def test_repeated_end_markers_are_reported_for_chapter_tails(self):
        chapter_infos = [
            _build_chapter_info(
                0,
                "Text/ch1.xhtml",
                """
                <html><body>
                    <h1>Chapter 1</h1>
                    <p>First chapter text.</p>
                    <p>End chapter</p>
                </body></html>
                """,
            ),
            _build_chapter_info(
                1,
                "Text/ch2.xhtml",
                """
                <html><body>
                    <h1>Chapter 2</h1>
                    <p>Second chapter text.</p>
                    <p>End chapter</p>
                </body></html>
                """,
            ),
            _build_chapter_info(
                2,
                "Text/ch3.xhtml",
                """
                <html><body>
                    <h1>Chapter 3</h1>
                    <p>Third chapter text.</p>
                    <p>Unique ending</p>
                </body></html>
                """,
            ),
        ]

        analysis = analyze_duplicate_findings(chapter_infos)
        tail_findings = analysis["boundary_findings"]

        self.assertEqual(len(tail_findings), 2)
        self.assertEqual(
            {finding["chapter_path"] for finding in tail_findings},
            {"Text/ch1.xhtml", "Text/ch2.xhtml"},
        )
        self.assertEqual(
            {finding["text"] for finding in tail_findings},
            {"End chapter"},
        )


if __name__ == "__main__":
    unittest.main()
