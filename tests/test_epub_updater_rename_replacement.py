"""EpubUpdater: переименованные главы должны обновлять ссылки во всём архиве за один проход.

Регрессия аудита perf:memory-churn/1: замена имён делалась вложенным циклом
«для каждого файла архива × для каждого переименования» — O(глав² × размер главы).
На книге в 1000 глав экспорт блокировал интерфейс на 12 секунд.
"""
import os
import time
import zipfile

import pytest

from gemini_translator.utils.epub_tools import EpubUpdater


def _chapter(name: str, next_name: str, title: str, filler: str = "") -> str:
    return (
        "<?xml version='1.0' encoding='utf-8'?>\n"
        "<html xmlns='http://www.w3.org/1999/xhtml'><head><title>"
        f"{title}</title></head><body><h1>{title}</h1><p>Текст главы.</p>{filler}"
        f"<p><a href='{next_name}'>Следующая</a></p></body></html>"
    )


def _write_epub(path: str, chapters: list[tuple[str, str]]) -> None:
    names = [name for name, _ in chapters]
    manifest = "".join(
        f"<item id='c{i}' href='{name}' media-type='application/xhtml+xml'/>" for i, name in enumerate(names)
    )
    spine = "".join(f"<itemref idref='c{i}'/>" for i in range(len(names)))
    opf = (
        "<?xml version='1.0'?><package xmlns='http://www.idpf.org/2007/opf' version='2.0'>"
        f"<manifest>{manifest}<item id='ncx' href='toc.ncx' media-type='application/x-dtbncx+xml'/></manifest>"
        f"<spine toc='ncx'>{spine}</spine></package>"
    )
    nav_points = "".join(
        f"<navPoint id='n{i}' playOrder='{i + 1}'><navLabel><text>Глава {i + 1}</text></navLabel>"
        f"<content src='{name}'/></navPoint>"
        for i, name in enumerate(names)
    )
    ncx = (
        "<?xml version='1.0' encoding='utf-8'?><ncx xmlns='http://www.daisy.org/z3986/2005/ncx/' version='2005-1'>"
        f"<navMap>{nav_points}</navMap></ncx>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            "<?xml version='1.0'?><container version='1.0' xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>"
            "<rootfiles><rootfile full-path='OEBPS/content.opf' media-type='application/oebps-package+xml'/></rootfiles></container>",
        )
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/toc.ncx", ncx)
        for name, body in chapters:
            archive.writestr(f"OEBPS/{name}", body)


def test_links_manifest_and_toc_follow_renamed_chapters(tmp_path):
    source = os.path.join(tmp_path, "book.epub")
    _write_epub(
        source,
        [
            ("ch1.xhtml", _chapter("ch1.xhtml", "ch2.xhtml", "Глава 1")),
            ("ch2.xhtml", _chapter("ch2.xhtml", "ch3.xhtml", "Глава 2")),
            ("ch3.xhtml", _chapter("ch3.xhtml", "ch1.xhtml", "Глава 3")),
        ],
    )
    new_ch1 = os.path.join(tmp_path, "new_ch1.xhtml")
    new_ch2 = os.path.join(tmp_path, "ch2_v2.xhtml")
    with open(new_ch1, "w", encoding="utf-8") as handle:
        handle.write(_chapter("new_ch1.xhtml", "ch2_v2.xhtml", "Новая первая глава"))
    with open(new_ch2, "w", encoding="utf-8") as handle:
        handle.write(_chapter("ch2_v2.xhtml", "ch3.xhtml", "Новая вторая глава"))

    updater = EpubUpdater(source)
    updater.add_replacement("OEBPS/ch1.xhtml", new_ch1)
    updater.add_replacement("OEBPS/ch2.xhtml", new_ch2)
    output = os.path.join(tmp_path, "out.epub")
    updater.update_and_save(output)

    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        assert "OEBPS/new_ch1.xhtml" in names and "OEBPS/ch2_v2.xhtml" in names
        assert "OEBPS/ch1.xhtml" not in names and "OEBPS/ch2.xhtml" not in names
        ch3 = archive.read("OEBPS/ch3.xhtml").decode("utf-8")
        assert "href='new_ch1.xhtml'" in ch3 and "ch1.xhtml'" not in ch3.replace("new_ch1.xhtml", "")
        opf = archive.read("OEBPS/content.opf").decode("utf-8")
        assert "href='new_ch1.xhtml'" in opf and "href='ch2_v2.xhtml'" in opf
        assert "href='ch1.xhtml'" not in opf and "href='ch2.xhtml'" not in opf
        ncx = archive.read("OEBPS/toc.ncx").decode("utf-8")
        assert "new_ch1.xhtml" in ncx and "ch2_v2.xhtml" in ncx
        assert "Новая первая глава" in ncx and "Новая вторая глава" in ncx
        assert "Глава 3" in ncx


@pytest.mark.performance
def test_renaming_every_chapter_of_a_big_book_is_not_quadratic(tmp_path):
    count = 1000
    filler = ("<p>" + ("Довольно длинный абзац перевода, чтобы глава весила как настоящая. " * 60) + "</p>") * 10
    chapters = [
        (f"ch{i:04d}.xhtml", _chapter(f"ch{i:04d}.xhtml", f"ch{(i + 1) % count:04d}.xhtml", f"Глава {i}", filler))
        for i in range(count)
    ]
    source = os.path.join(tmp_path, "big.epub")
    _write_epub(source, chapters)

    updater = EpubUpdater(source)
    for i in range(count):
        replacement = os.path.join(tmp_path, f"tr{i:04d}.xhtml")
        with open(replacement, "w", encoding="utf-8") as handle:
            handle.write(_chapter(f"tr{i:04d}.xhtml", f"tr{(i + 1) % count:04d}.xhtml", f"Перевод {i}"))
        updater.add_replacement(f"OEBPS/ch{i:04d}.xhtml", replacement)

    output = os.path.join(tmp_path, "big_out.epub")
    started = time.perf_counter()
    updater.update_and_save(output)
    elapsed = time.perf_counter() - started
    assert elapsed < 3.0, f"экспорт {count} переименованных глав занял {elapsed:.1f} с"
    with zipfile.ZipFile(output) as archive:
        opf = archive.read("OEBPS/content.opf").decode("utf-8")
        assert "href='tr0999.xhtml'" in opf and "href='ch0999.xhtml'" not in opf
