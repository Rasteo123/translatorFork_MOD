import os
import sys
import zipfile

from docx import Document


TESTS_DIR = os.path.dirname(__file__)
PROJECT_ROOT = os.path.dirname(TESTS_DIR)
RANOBELIB_DIR = os.path.join(PROJECT_ROOT, "ranobelib")

if RANOBELIB_DIR not in sys.path:
    sys.path.insert(0, RANOBELIB_DIR)

from parsers import FileParser
from models import ChapterData
from workers import RulateDownloadWorker


def _write_epub(path, chapter_bodies):
    manifest_items = []
    spine_items = []
    files = {}
    for index, body in enumerate(chapter_bodies, start=1):
        item_id = f"chapter{index}"
        href = f"Text/ch{index}.xhtml"
        manifest_items.append(
            f'<item id="{item_id}" href="{href}" media-type="application/xhtml+xml"/>'
        )
        spine_items.append(f'<itemref idref="{item_id}"/>')
        files[f"OEBPS/{href}"] = body

    opf = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="2.0">'
        "<manifest>"
        + "".join(manifest_items)
        + "</manifest><spine>"
        + "".join(spine_items)
        + "</spine></package>"
    )

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("OEBPS/content.opf", opf)
        for name, content in files.items():
            archive.writestr(name, content)


def test_parse_epub_reads_body_text_split_by_br(tmp_path):
    long_para = " ".join(["regular paragraph text"] * 20)
    direct_text = "<br/>".join(
        [
            "direct body text one " * 10,
            "direct body text two " * 10,
            "direct body text three " * 10,
        ]
    )
    epub_path = tmp_path / "book.epub"
    _write_epub(
        epub_path,
        [
            (
                '<html><body><h1>Chapter 1. P tags</h1>'
                f"<p>{long_para}</p></body></html>"
            ),
            (
                '<html><body><h1>Chapter 2. BR tags</h1>'
                f"<br/>{direct_text}</body></html>"
            ),
        ],
    )

    chapters = FileParser.parse_epub(str(epub_path), "1")

    assert len(chapters) == 2
    assert chapters[0].number == 1.0
    assert chapters[1].number == 2.0
    assert "direct body text one" in chapters[1].content
    assert chapters[1].content.count("<p>") == 3


def test_parse_zip_docx_keeps_russian_chapter_parts(tmp_path):
    zip_path = tmp_path / "rulate.zip"
    titles = [
        "Глава 30 Король Валид становится королём зубрёжки. Часть 1",
        "Глава 30 Король Валид становится королём зубрёжки. Часть 2",
        "Глава 30 Король Валид становится королём зубрёжки. Часть 3",
    ]

    with zipfile.ZipFile(zip_path, "w") as archive:
        for index, title in enumerate(titles, start=1):
            docx_path = tmp_path / f"chapter_{index}.docx"
            doc = Document()
            doc.add_paragraph(f"Text for {title}")
            doc.save(docx_path)
            archive.write(docx_path, f"{title}.docx")

    chapters = FileParser.parse_zip_docx(str(zip_path), "1")

    assert [chapter.number for chapter in chapters] == [30.1, 30.2, 30.3]
    assert chapters[0].title == "Король Валид становится королём зубрёжки"


def test_rulate_worker_applies_full_site_titles_to_downloaded_chapters():
    infos = [
        {
            "id": "101",
            "title": "Глава 31 Очень длинное необрезанное название главы с сайта Rulate",
            "number": 31.0,
        },
        {
            "id": "102",
            "title": "Глава 32 Второе длинное необрезанное название главы с сайта Rulate",
            "number": 32.0,
        },
    ]
    chapters = [
        ChapterData("1", 31.0, "Очень длинное необрезанное...", "text 31"),
        ChapterData("1", 32.0, "Второе длинное необрезанное...", "text 32"),
    ]
    worker = RulateDownloadWorker(
        "https://tl.rulate.ru/book/1",
        "1",
        chapter_ids=["101", "102"],
        chapter_infos=infos,
    )

    worker._apply_chapter_infos(chapters, infos)

    assert chapters[0].title == "Очень длинное необрезанное название главы с сайта Rulate"
    assert chapters[1].title == "Второе длинное необрезанное название главы с сайта Rulate"
