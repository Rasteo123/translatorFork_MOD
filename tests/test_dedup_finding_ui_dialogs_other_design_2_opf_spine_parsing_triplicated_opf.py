# -*- coding: utf-8 -*-
"""
Характеризационные + маршрутизационные тесты для дедупа находки
finding-ui-dialogs-other_design_2-opf-spine-parsing-triplicated.

Поиск content.opf (container.xml -> rootfile, фолбэк на *.opf) был
реализован трижды независимо:
  - gemini_translator/ui/dialogs/rulate_export.py (SimpleEpubReader._find_opf_path)
  - gemini_translator/ui/dialogs/chapter_splitter.py (_find_opf_path)
  - gemini_translator/utils/epub_tools.py (_get_spine_order_from_zip, инлайн)

Канон: gemini_translator.utils.epub_tools.find_opf_path(zip_file) —
container.xml-first (с проверкой непустого full-path), фолбэк на
регистронезависимый поиск *.opf по всему архиву, FileNotFoundError если
ничего не найдено. Это поведение двух из трёх копий (rulate_export.py,
chapter_splitter.py) и оно спецификационно корректнее прежней логики
epub_tools._get_spine_order_from_zip, которая доверяла container.xml
только когда в архиве больше одного файла с расширением .opf.

Волна 2 (по замечаниям ревьюера, устраняем blocker/major + доступные minor):
находка называется «поиск content.opf И порядок spine» — первая волна
дедуплицировала только поиск OPF-пути. Здесь дополнительно дедуплицируется
и вторая половина: разбор manifest+spine и построение упорядоченного списка
html-файлов, которые были реализованы независимо в
epub_tools._get_spine_order_from_zip и в
rulate_export.SimpleEpubReader (_parse_opf/get_ordered_html_files). Канон —
gemini_translator.utils.epub_tools.parse_opf_package +
gemini_translator.utils.epub_tools.read_spine_html_order: unquote(href),
posixpath (не os.path.join), нормализация '..'/'.', фолбэк на совпадение
basename — то есть поведение, «строго лучше обеих копий» (см. отчёт
ревьюера). chapter_splitter.split_epub_file сознательно не переведён на
этот канон: ему нужны сами XML-элементы manifest/spine для перезаписи OPF
при разбиении, это другая задача, не разбор порядка глав для чтения.

Дополнительно закрыты minor-замечания:
  - find_opf_path теперь unquote-ит full-path из container.xml, если
    закодированный путь отсутствует в архиве, а раскодированный — есть;
  - фолбэк на несколько *.opf без валидного container.xml логирует
    предупреждение и детерминированно выбирает путь с наименьшей глубиной
    вложенности (ближе к корню архива), а не первый по porядку namelist();
  - добавлены характеризационные тесты на get_epub_chapter_order,
    фиксирующие оба режима ('spine' для обычного EPUB и осознанную
    деградацию до 'filename', когда container.xml объявляет недостижимый
    full-path при единственном валидном *.opf).
"""
import zipfile

import pytest

from gemini_translator.utils.epub_tools import (
    find_opf_path,
    get_epub_chapter_order,
    parse_opf_package,
    read_spine_html_order,
)


CONTAINER_XML = (
    '<?xml version="1.0"?>'
    '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
    '<rootfiles><rootfile full-path="{path}" '
    'media-type="application/oebps-package+xml"/></rootfiles>'
    "</container>"
)


def _make_epub(tmp_path, name, entries):
    epub_path = tmp_path / name
    with zipfile.ZipFile(epub_path, "w") as zf:
        for arcname, content in entries.items():
            zf.writestr(arcname, content)
    return epub_path


# ---------------------------------------------------------------------------
# Характеризация канонической find_opf_path
# ---------------------------------------------------------------------------


def test_find_opf_path_prefers_container_xml_rootfile_over_stray_opf(tmp_path):
    """container.xml — источник истины, даже если в архиве есть посторонний
    .opf-файл (например, резервная копия), который не является rootfile."""
    epub_path = _make_epub(
        tmp_path,
        "book.epub",
        {
            "META-INF/container.xml": CONTAINER_XML.format(path="OEBPS/package.opf"),
            "OEBPS/package.opf": "<package/>",
            "backup/old_package.opf": "<package/>",
        },
    )
    with zipfile.ZipFile(epub_path, "r") as zf:
        assert find_opf_path(zf) == "OEBPS/package.opf"


def test_find_opf_path_falls_back_to_case_insensitive_opf_glob_without_container(tmp_path):
    epub_path = _make_epub(
        tmp_path,
        "book.epub",
        {
            "OEBPS/CONTENT.OPF": "<package/>",
            "OEBPS/chapter1.xhtml": "<html/>",
        },
    )
    with zipfile.ZipFile(epub_path, "r") as zf:
        assert find_opf_path(zf) == "OEBPS/CONTENT.OPF"


def test_find_opf_path_falls_back_to_glob_when_container_xml_is_malformed(tmp_path):
    epub_path = _make_epub(
        tmp_path,
        "book.epub",
        {
            "META-INF/container.xml": "not xml at all <<<",
            "OEBPS/content.opf": "<package/>",
        },
    )
    with zipfile.ZipFile(epub_path, "r") as zf:
        assert find_opf_path(zf) == "OEBPS/content.opf"


def test_find_opf_path_falls_back_when_rootfile_has_no_full_path(tmp_path):
    """rootfile без атрибута full-path не должен приводить к None — нужно
    продолжить искать *.opf (баг обеих копий-дубликатов)."""
    broken_container = (
        '<?xml version="1.0"?>'
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        "<rootfiles><rootfile media-type=\"application/oebps-package+xml\"/></rootfiles>"
        "</container>"
    )
    epub_path = _make_epub(
        tmp_path,
        "book.epub",
        {
            "META-INF/container.xml": broken_container,
            "OEBPS/content.opf": "<package/>",
        },
    )
    with zipfile.ZipFile(epub_path, "r") as zf:
        assert find_opf_path(zf) == "OEBPS/content.opf"


def test_find_opf_path_raises_file_not_found_when_nothing_matches(tmp_path):
    epub_path = _make_epub(
        tmp_path,
        "book.epub",
        {"OEBPS/chapter1.xhtml": "<html/>"},
    )
    with zipfile.ZipFile(epub_path, "r") as zf:
        with pytest.raises(FileNotFoundError):
            find_opf_path(zf)


# ---------------------------------------------------------------------------
# Маршрутизация: каждое бывшее место вызова должно идти через каноническую
# find_opf_path, а не через собственную копию.
# ---------------------------------------------------------------------------


def test_simple_epub_reader_routes_through_canonical_find_opf_path(tmp_path, monkeypatch):
    from gemini_translator.ui.dialogs import rulate_export

    epub_path = _make_epub(
        tmp_path,
        "book.epub",
        {
            "META-INF/container.xml": CONTAINER_XML.format(path="OEBPS/content.opf"),
            "OEBPS/content.opf": (
                '<package xmlns="http://www.idpf.org/2007/opf">'
                '<manifest><item id="c1" href="chapter1.xhtml" '
                'media-type="application/xhtml+xml"/></manifest>'
                '<spine><itemref idref="c1"/></spine>'
                "</package>"
            ),
            "OEBPS/chapter1.xhtml": "<html><body><h1>Глава 1</h1></body></html>",
        },
    )

    calls = []
    original = rulate_export.find_opf_path

    def spy(zip_file):
        calls.append(zip_file)
        return original(zip_file)

    # До рефакторинга у rulate_export нет своего атрибута find_opf_path (а
    # есть только приватный метод SimpleEpubReader._find_opf_path) -> setattr
    # упадёт с AttributeError, что и требуется как RED-состояние.
    monkeypatch.setattr(rulate_export, "find_opf_path", spy)

    reader = rulate_export.SimpleEpubReader(str(epub_path))
    try:
        assert reader.opf_path == "OEBPS/content.opf"
        assert len(calls) == 1
    finally:
        reader.close()


def test_split_epub_file_routes_through_canonical_find_opf_path(tmp_path, monkeypatch):
    from gemini_translator.ui.dialogs import chapter_splitter

    long_body = "<p>" + ("слово " * 400) + "</p>"
    epub_path = _make_epub(
        tmp_path,
        "book.epub",
        {
            "META-INF/container.xml": CONTAINER_XML.format(path="OEBPS/content.opf"),
            "OEBPS/content.opf": (
                '<package xmlns="http://www.idpf.org/2007/opf">'
                "<manifest>"
                '<item id="c1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>'
                "</manifest>"
                '<spine><itemref idref="c1"/></spine>'
                "</package>"
            ),
            "OEBPS/chapter1.xhtml": f"<html><body><h1>Глава</h1>{long_body}</body></html>",
        },
    )
    output_path = tmp_path / "out.epub"

    calls = []
    original = chapter_splitter.find_opf_path

    def spy(zip_file):
        calls.append(zip_file)
        return original(zip_file)

    # До рефакторинга chapter_splitter не импортирует find_opf_path (у него
    # своя приватная функция _find_opf_path) -> setattr падает AttributeError.
    monkeypatch.setattr(chapter_splitter, "find_opf_path", spy)

    settings = chapter_splitter.SplitSettings(split_threshold=50, target_size=100, min_part_size=20)
    stats = chapter_splitter.split_epub_file(str(epub_path), str(output_path), settings)

    assert len(calls) == 1
    assert stats.split_chapters == 1


# ---------------------------------------------------------------------------
# Волна 2: дедуп второй половины находки — разбор manifest+spine и порядок
# html-файлов (parse_opf_package / read_spine_html_order).
# ---------------------------------------------------------------------------


def _make_opf(manifest_items, spine_idrefs):
    manifest_xml = "".join(
        f'<item id="{item_id}" href="{href}" media-type="application/xhtml+xml"/>'
        for item_id, href in manifest_items
    )
    spine_xml = "".join(f'<itemref idref="{idref}"/>' for idref in spine_idrefs)
    return (
        '<package xmlns="http://www.idpf.org/2007/opf">'
        f"<manifest>{manifest_xml}</manifest>"
        f"<spine>{spine_xml}</spine>"
        "</package>"
    )


def test_read_spine_html_order_unquotes_normalizes_dotdot_and_falls_back_to_basename(tmp_path):
    """Канон должен уметь то, что умели ОБЕ копии-дубликата одновременно:
    unquote(href) (умел rulate_export), нормализацию '..' через posixpath
    (умел rulate_export) и при этом не падать на архивах, где итоговый путь
    не совпал буквально — фолбэк по basename (умел только rulate_export,
    epub_tools раньше вообще не резолвил такие href)."""
    epub_path = _make_epub(
        tmp_path,
        "book.epub",
        {
            "META-INF/container.xml": CONTAINER_XML.format(path="OEBPS/content.opf"),
            "OEBPS/content.opf": _make_opf(
                [
                    ("c1", "text/My%20Chapter.xhtml"),
                    ("c2", "../wrong/chapter2.xhtml"),
                ],
                ["c1", "c2"],
            ),
            "OEBPS/text/My Chapter.xhtml": "<html><body>1</body></html>",
            "OEBPS/chapter2.xhtml": "<html><body>2</body></html>",
        },
    )
    with zipfile.ZipFile(epub_path, "r") as zf:
        assert read_spine_html_order(zf) == [
            "OEBPS/text/My Chapter.xhtml",
            "OEBPS/chapter2.xhtml",
        ]


def test_simple_epub_reader_routes_spine_order_through_canonical_epub_tools_function(tmp_path, monkeypatch):
    """SimpleEpubReader.get_ordered_html_files не должен разбирать
    manifest/spine самостоятельно — вызов обязан идти через
    epub_tools.read_spine_html_order (импортированный в модуль
    rulate_export по имени, как и find_opf_path)."""
    from gemini_translator.ui.dialogs import rulate_export

    epub_path = _make_epub(
        tmp_path,
        "book.epub",
        {
            "META-INF/container.xml": CONTAINER_XML.format(path="OEBPS/content.opf"),
            "OEBPS/content.opf": _make_opf(
                [
                    ("c1", "text/My%20Chapter.xhtml"),
                    ("c2", "../wrong/chapter2.xhtml"),
                ],
                ["c1", "c2"],
            ),
            "OEBPS/text/My Chapter.xhtml": "<html><body>1</body></html>",
            "OEBPS/chapter2.xhtml": "<html><body>2</body></html>",
        },
    )

    calls = []
    original = rulate_export.read_spine_html_order

    def spy(zf):
        calls.append(zf)
        return original(zf)

    # До волны 2 у модуля rulate_export нет имени read_spine_html_order
    # (manifest/spine разбирались приватным SimpleEpubReader._parse_opf) ->
    # setattr падает AttributeError, что и требуется как RED-состояние.
    monkeypatch.setattr(rulate_export, "read_spine_html_order", spy)

    reader = rulate_export.SimpleEpubReader(str(epub_path))
    try:
        files = reader.get_ordered_html_files()
    finally:
        reader.close()

    assert len(calls) == 1
    assert files == ["OEBPS/text/My Chapter.xhtml", "OEBPS/chapter2.xhtml"]


def test_epub_tools_and_rulate_export_agree_on_spine_order_for_same_epub(tmp_path):
    """Прямое доказательство отсутствия дублирования: два бывших места
    вызова (epub_tools.get_epub_chapter_order и
    rulate_export.SimpleEpubReader) дают идентичный порядок на одном и том
    же EPUB, потому что оба идут через один и тот же parse_opf_package."""
    from gemini_translator.ui.dialogs import rulate_export

    epub_path = _make_epub(
        tmp_path,
        "book.epub",
        {
            "META-INF/container.xml": CONTAINER_XML.format(path="OEBPS/content.opf"),
            "OEBPS/content.opf": _make_opf(
                [("c1", "chapter1.xhtml"), ("c2", "chapter2.xhtml")],
                ["c1", "c2"],
            ),
            "OEBPS/chapter1.xhtml": "<html><body>1</body></html>",
            "OEBPS/chapter2.xhtml": "<html><body>2</body></html>",
        },
    )

    reader = rulate_export.SimpleEpubReader(str(epub_path))
    try:
        via_rulate = reader.get_ordered_html_files()
    finally:
        reader.close()

    via_epub_tools = get_epub_chapter_order(str(epub_path))

    assert via_rulate == ["OEBPS/chapter1.xhtml", "OEBPS/chapter2.xhtml"]
    assert via_epub_tools == via_rulate


def test_parse_opf_package_returns_path_dir_manifest_and_spine(tmp_path):
    epub_path = _make_epub(
        tmp_path,
        "book.epub",
        {
            "META-INF/container.xml": CONTAINER_XML.format(path="OEBPS/content.opf"),
            "OEBPS/content.opf": _make_opf(
                [("c1", "chapter1.xhtml")],
                ["c1"],
            ),
            "OEBPS/chapter1.xhtml": "<html/>",
        },
    )
    with zipfile.ZipFile(epub_path, "r") as zf:
        opf_path, opf_dir, manifest, spine_idrefs = parse_opf_package(zf)

    assert opf_path == "OEBPS/content.opf"
    assert opf_dir == "OEBPS"
    assert manifest == {"c1": "chapter1.xhtml"}
    assert spine_idrefs == ["c1"]


# ---------------------------------------------------------------------------
# Minor: характеризация get_epub_chapter_order на смене приоритета
# container.xml (было: доверяем container.xml только при >1 файла *.opf).
# ---------------------------------------------------------------------------


def test_get_epub_chapter_order_reports_spine_for_ordinary_single_opf_epub(tmp_path):
    epub_path = _make_epub(
        tmp_path,
        "book.epub",
        {
            "META-INF/container.xml": CONTAINER_XML.format(path="OEBPS/content.opf"),
            "OEBPS/content.opf": _make_opf(
                [("c1", "chapter1.xhtml")],
                ["c1"],
            ),
            "OEBPS/chapter1.xhtml": "<html><body>1</body></html>",
        },
    )
    assert get_epub_chapter_order(str(epub_path), return_method=True) == (
        ["OEBPS/chapter1.xhtml"],
        "spine",
    )


def test_get_epub_chapter_order_degrades_to_filename_when_container_rootfile_is_unreachable(tmp_path):
    """Осознанная деградация после смены логики на container.xml-first:
    единственный валидный *.opf есть, но container.xml (источник истины)
    указывает на несуществующий путь -> spine не читается, откат на
    сортировку по имени файла, а не молчаливый успех через старый
    «единственный .opf» фолбэк."""
    epub_path = _make_epub(
        tmp_path,
        "book.epub",
        {
            "META-INF/container.xml": CONTAINER_XML.format(path="OEBPS/missing.opf"),
            "OEBPS/content.opf": _make_opf(
                [("c1", "chapter1.xhtml")],
                ["c1"],
            ),
            "OEBPS/chapter1.xhtml": "<html><body>1</body></html>",
        },
    )
    assert get_epub_chapter_order(str(epub_path), return_method=True) == (
        ["OEBPS/chapter1.xhtml"],
        "filename",
    )


# ---------------------------------------------------------------------------
# Minor: unquote full-path из container.xml.
# ---------------------------------------------------------------------------


def test_find_opf_path_unquotes_percent_encoded_full_path_from_container_xml(tmp_path):
    epub_path = _make_epub(
        tmp_path,
        "book.epub",
        {
            "META-INF/container.xml": CONTAINER_XML.format(path="OEBPS/My%20Book.opf"),
            "OEBPS/My Book.opf": "<package/>",
        },
    )
    with zipfile.ZipFile(epub_path, "r") as zf:
        assert find_opf_path(zf) == "OEBPS/My Book.opf"


def test_find_opf_path_keeps_raw_full_path_when_it_matches_directly(tmp_path):
    """Если файл в архиве реально называется с '%20' в имени (redkiy, no
    percent-encoding интерпретации не нужно) — сырое имя должно иметь
    приоритет над unquote-версией."""
    epub_path = _make_epub(
        tmp_path,
        "book.epub",
        {
            "META-INF/container.xml": CONTAINER_XML.format(path="OEBPS/My%20Book.opf"),
            "OEBPS/My%20Book.opf": "<package/>",
        },
    )
    with zipfile.ZipFile(epub_path, "r") as zf:
        assert find_opf_path(zf) == "OEBPS/My%20Book.opf"


# ---------------------------------------------------------------------------
# Minor: несколько *.opf без валидного container.xml -> предупреждение +
# детерминированный выбор (кратчайшая глубина пути), а не "первый в
# namelist()".
# ---------------------------------------------------------------------------


def test_find_opf_path_warns_and_picks_shallowest_path_for_multiple_opf_candidates(tmp_path, capsys):
    epub_path = _make_epub(
        tmp_path,
        "book.epub",
        {
            "backup/nested/old_package.opf": "<package/>",
            "content.opf": "<package/>",
        },
    )
    with zipfile.ZipFile(epub_path, "r") as zf:
        result = find_opf_path(zf)

    assert result == "content.opf"
    captured = capsys.readouterr()
    assert "[WARN]" in captured.out
    assert "content.opf" in captured.out
    assert "backup/nested/old_package.opf" in captured.out
