"""Регресс на находку utils-io/bugs/1-docimp-utf16-misdecode.

_read_text_with_fallbacks перебирал кодировки в порядке
("utf-8-sig", "utf-8", "cp1251", "utf-16"): кириллический текст, сохранённый
в UTF-16 (с BOM или без), почти всегда успешно, но неверно декодируется как
"utf-8-sig" или "cp1251" раньше, чем очередь доходит до "utf-16" — файл
превращается в мусор из управляющих символов без единого исключения.
"""

from __future__ import annotations

from gemini_translator.utils.document_importer import (
    _read_text_with_fallbacks,
    extract_document_chapters,
)

ORIGINAL_TEXT = "# Глава один\n\nПривет, это тестовый текст главы номер один.\n\nВторой абзац."


def test_read_text_with_fallbacks_decodes_utf16_le_without_bom(tmp_path):
    path = tmp_path / "book.md"
    path.write_bytes(ORIGINAL_TEXT.encode("utf-16-le"))

    decoded = _read_text_with_fallbacks(path)

    assert decoded == ORIGINAL_TEXT


def test_read_text_with_fallbacks_decodes_utf16_with_bom(tmp_path):
    path = tmp_path / "book.md"
    # То, что даёт «Юникод» в блокноте Windows: BOM + UTF-16 LE.
    path.write_bytes(ORIGINAL_TEXT.encode("utf-16"))

    decoded = _read_text_with_fallbacks(path)

    assert decoded == ORIGINAL_TEXT


def test_markdown_import_survives_utf16_encoded_source(tmp_path):
    md_path = tmp_path / "book.md"
    md_path.write_bytes(ORIGINAL_TEXT.encode("utf-16"))

    result = extract_document_chapters(md_path)

    assert result.source_format == "markdown"
    assert result.chapters, "документ должен дать хотя бы одну главу"
    combined_html = "".join(chapter.html for chapter in result.chapters)
    assert "Привет, это тестовый текст" in combined_html
    assert "\x00" not in combined_html


def test_read_text_with_fallbacks_still_decodes_short_cp1251_text(tmp_path):
    """Регресс замечания рецензента: UnicodeDammit без ограничений на семейство
    кодировок ломает cp1251, для которого перебор изначально и писался — короткий
    текст 'Привет, мир!' в cp1251 эвристика статистически принимает за cp1125
    ('╧ЁштхҐ, ьшЁ!'), хотя раньше он читался корректно.
    """
    path = tmp_path / "book.md"
    text = "Привет, мир!\n"
    path.write_bytes(text.encode("cp1251"))

    decoded = _read_text_with_fallbacks(path)

    assert decoded == text


def test_read_text_with_fallbacks_still_decodes_mixed_cp1251_text(tmp_path):
    """Тот же регресс на более длинном смешанном тексте (латиница + кириллица +
    типографские кавычки/тире) — рецензент указал, что UnicodeDammit ошибочно
    определяет его как windows-1250.
    """
    path = tmp_path / "book.md"
    text = "Chapter 1\n\nHello world — тире и «кавычки».\n"
    path.write_bytes(text.encode("cp1251"))

    decoded = _read_text_with_fallbacks(path)

    assert decoded == text
