import zipfile

from bs4 import BeautifulSoup

from gemini_translator.utils.epub_tools import (
    EpubUpdater,
    calculate_potential_output_size,
    extract_epub_heading_text,
    extract_first_epub_heading_text,
    extract_number_from_path,
    get_chapter_fingerprint,
    get_epub_chapter_order,
    normalize_epub_chapter_heading_to_h1,
)


SEQUENCE = "\u7b2c175\u7ae0"
CHAPTER_NAME = "\u5c0f\u59e8\u5230\u8bbf"
EXPECTED_TITLE = f"{SEQUENCE} {CHAPTER_NAME}"
SPLIT_HEAD = (
    '<h2 class="head"><span class="chapter-sequence-number">'
    f"{SEQUENCE}</span><br />{CHAPTER_NAME}</h2>"
)
MULTI_H2_HEAD = "<h2>Chapter 42</h2><h2>Review: Hidden plot</h2>"
EXPECTED_MULTI_H2_TITLE = "Chapter 42 Review: Hidden plot"


def _chapter_html(title_html=SPLIT_HEAD):
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml">'
        "<head><title>Old title</title></head>"
        f"<body>{title_html}<p>Body text.</p></body>"
        "</html>"
    )


def test_split_epub_heading_text_keeps_separator():
    soup = BeautifulSoup(_chapter_html(), "html.parser")

    assert extract_epub_heading_text(soup.h2) == EXPECTED_TITLE
    assert extract_first_epub_heading_text(_chapter_html()) == EXPECTED_TITLE


def test_split_epub_heading_is_normalized_to_plain_h1():
    normalized = normalize_epub_chapter_heading_to_h1(_chapter_html())

    assert SPLIT_HEAD not in normalized
    assert f"<h1>{EXPECTED_TITLE}</h1>" in normalized
    assert "chapter-sequence-number" not in normalized


def test_leading_multi_h2_heading_is_normalized_to_plain_h1():
    normalized = normalize_epub_chapter_heading_to_h1(_chapter_html(MULTI_H2_HEAD))

    assert MULTI_H2_HEAD not in normalized
    assert f"<h1>{EXPECTED_MULTI_H2_TITLE}</h1>" in normalized
    assert "<h2>" not in normalized


def test_chapter_fingerprint_keeps_split_heading_separator(tmp_path):
    epub_path = tmp_path / "book.epub"
    with zipfile.ZipFile(epub_path, "w") as archive:
        archive.writestr("OEBPS/ch175.xhtml", _chapter_html())

    with zipfile.ZipFile(epub_path, "r") as archive:
        fingerprint = get_chapter_fingerprint(archive, "OEBPS/ch175.xhtml")

    assert fingerprint["h1"] == EXPECTED_TITLE


def test_epub_updater_uses_split_heading_for_toc_titles(tmp_path):
    source_epub = tmp_path / "source.epub"
    new_chapter = tmp_path / "ch175_translated.html"
    output_epub = tmp_path / "output.epub"

    new_chapter.write_text(_chapter_html(), encoding="utf-8")
    with zipfile.ZipFile(source_epub, "w") as archive:
        archive.writestr("OEBPS/ch175.xhtml", _chapter_html("<h1>Old title</h1>"))
        archive.writestr(
            "OEBPS/nav.xhtml",
            '<html><body><nav><ol><li><a href="ch175.xhtml">Old title</a></li></ol></nav></body></html>',
        )
        archive.writestr(
            "OEBPS/toc.ncx",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<ncx><navMap><navPoint><navLabel><text>Old title</text></navLabel>'
                '<content src="ch175.xhtml"/></navPoint></navMap></ncx>'
            ),
        )

    updater = EpubUpdater(str(source_epub))
    updater.add_replacement("OEBPS/ch175.xhtml", str(new_chapter))
    updater.update_and_save(str(output_epub))

    with zipfile.ZipFile(output_epub, "r") as archive:
        nav = archive.read("OEBPS/nav.xhtml").decode("utf-8")
        ncx = archive.read("OEBPS/toc.ncx").decode("utf-8")
        rebuilt_chapter = archive.read("OEBPS/ch175_translated.html").decode("utf-8")

    assert EXPECTED_TITLE in nav
    assert EXPECTED_TITLE in ncx
    assert f"<h1>{EXPECTED_TITLE}</h1>" in rebuilt_chapter
    assert f"<title>{EXPECTED_TITLE}</title>" in rebuilt_chapter


def test_extract_number_from_qt_item_uses_user_role_path():
    class FakeItem:
        def data(self, role):
            assert role.name == "UserRole"
            return "OEBPS/chapter-175.xhtml"

    assert extract_number_from_path(FakeItem()) == 175


def test_potential_output_size_parses_visible_html_text(monkeypatch):
    from gemini_translator.utils.epub_tools import api_config

    monkeypatch.setattr(api_config, "ALPHABETIC_EXPANSION_FACTOR", 2)

    assert calculate_potential_output_size("<p>Hello</p>", is_cjk=False) == (17, 7)


def test_epub_order_rejects_xml_entity_declarations(tmp_path):
    epub_path = tmp_path / "malicious.epub"
    with zipfile.ZipFile(epub_path, "w") as archive:
        archive.writestr(
            "META-INF/container.xml",
            (
                '<?xml version="1.0"?>'
                '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                '<rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles>'
                '</container>'
            ),
        )
        archive.writestr(
            "OEBPS/content.opf",
            (
                '<?xml version="1.0"?>'
                '<!DOCTYPE package [<!ENTITY payload "chapter.xhtml">]>'
                '<package xmlns="http://www.idpf.org/2007/opf">'
                '<manifest><item id="chapter" href="&payload;"/></manifest>'
                '<spine><itemref idref="chapter"/></spine>'
                '</package>'
            ),
        )
        archive.writestr("OEBPS/chapter.xhtml", "<html><body>safe</body></html>")

    assert get_epub_chapter_order(epub_path, return_method=True) == ([], "error")
