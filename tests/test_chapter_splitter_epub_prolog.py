import xml.etree.ElementTree as ET
import zipfile

import pytest

from gemini_translator.ui.dialogs.chapter_splitter import SplitSettings, split_epub_file


XML_DECLARATION = '<?xml version="1.0" encoding="UTF-8"?>\n'
XHTML_PROLOG = XML_DECLARATION + '<!DOCTYPE html>\n'
XHTML_NS = 'http://www.w3.org/1999/xhtml'


def _write_epub(path, chapter_prolog):
    chapter = (
        f'{chapter_prolog}<html xmlns="{XHTML_NS}" lang="ru" xml:lang="ru">'
        '<head><title>Глава</title></head><body><h1>Глава</h1>'
        '<p>Первый абзац. Достаточно текста для первой части главы.</p>'
        '<p>Второй абзац. Достаточно текста для второй части главы.</p>'
        '</body></html>'
    )
    with zipfile.ZipFile(path, 'w') as book:
        book.writestr('mimetype', 'application/epub+zip')
        book.writestr('META-INF/container.xml', '''
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>''')
        book.writestr('EPUB/content.opf', '''
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">urn:uuid:5bc6412c-a5ca-42ec-acd1-ccbdf88b802d</dc:identifier>
    <dc:title>Тестовая книга</dc:title>
    <dc:language>ru</dc:language>
    <meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>''')
        book.writestr('EPUB/nav.xhtml', f'''
<html xmlns="{XHTML_NS}" xmlns:epub="http://www.idpf.org/2007/ops" lang="ru" xml:lang="ru">
  <head><title>Оглавление</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter.xhtml">Глава</a></li></ol></nav></body>
</html>''')
        book.writestr('EPUB/chapter.xhtml', chapter)


@pytest.mark.parametrize('chapter_prolog', [
    pytest.param('', id='no-prolog'),
    pytest.param(XHTML_PROLOG, id='standard-prolog'),
    pytest.param(XHTML_PROLOG * 2, id='previously-duplicated-prolog'),
])
def test_split_epub_writes_well_formed_chapter_prologs(tmp_path, chapter_prolog):
    source = tmp_path / 'source.epub'
    output = tmp_path / 'split.epub'
    _write_epub(source, chapter_prolog)

    stats = split_epub_file(source, output, SplitSettings(split_threshold=80, target_size=80, min_part_size=30))

    assert stats.split_chapters == 1
    assert stats.output_chapters == 2
    with zipfile.ZipFile(output) as book:
        for chapter_path in ('EPUB/chapter.xhtml', 'EPUB/chapter_part2.xhtml'):
            content = book.read(chapter_path)
            assert ET.fromstring(content).tag == f'{{{XHTML_NS}}}html'
            assert content.count(b'<?xml ') == bool(chapter_prolog)
            assert content.count(b'<!DOCTYPE html>') == bool(chapter_prolog)
