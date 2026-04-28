from zipfile import ZipFile

from merge_html_chapters import main


def test_merge_html_chapters_outputs_sorted_html_and_epub(tmp_path):
    (tmp_path / "0002_2_Chapter_2_Test_translated_gemini.html").write_text(
        "<html><body><h1>Chapter 2</h1><p>Second text.</p></body></html>",
        encoding="utf-8",
    )
    (tmp_path / "0001_1_Chapter_1_Test_translated_gemini.html").write_text(
        "<html><body><h1>Chapter 1</h1><p>First text.</p></body></html>",
        encoding="utf-8",
    )

    html_path = tmp_path / "combined.html"
    epub_path = tmp_path / "combined.epub"
    exit_code = main(
        [
            str(tmp_path),
            "--pattern",
            "*_translated_gemini.html",
            "--title",
            "Test Book",
            "--out",
            str(html_path),
            "--epub",
            str(epub_path),
        ]
    )

    assert exit_code == 0
    combined_html = html_path.read_text(encoding="utf-8")
    assert combined_html.find("Chapter 1") < combined_html.find("Chapter 2")
    assert "First text." in combined_html
    assert "Second text." in combined_html

    with ZipFile(epub_path) as epub:
        assert epub.namelist()[0] == "mimetype"
        combined_xhtml = epub.read("OEBPS/text/combined.xhtml").decode("utf-8")

    assert combined_xhtml.find("Chapter 1") < combined_xhtml.find("Chapter 2")
    assert "First text." in combined_xhtml
    assert "Second text." in combined_xhtml
