# -*- coding: utf-8 -*-
"""
Регрессионный тест для находки mcp-bench-cli/bugs/3-untranslated-fix-truncation-da.

Дефект: если текстовый узел с недопереведённым словом длиннее --max-context-chars,
_collect_untranslated_fix_items всё равно создавала группу для автозамены, где
цель замены (target_object) — ВЕСЬ узел, а отправляемый в модель контекст —
урезанный (обрезанный до max_context_chars символов) текст. При применении правки
(_apply_untranslated_fix_changes) весь узел заменялся переводом ТОЛЬКО урезанного
куска, и хвост исходного текста главы безвозвратно терялся из сохранённого файла.

Фикс: при превышении лимита автозамена такого вхождения не выполняется (вхождение
пропускается), исходный текст узла остаётся нетронутым. Слово по-прежнему попадает
в scan_issues (сканирование не привязано к лимиту), чтобы не потерять видимость
проблемы, но группа для автоматического перевода/замены не создаётся.
"""

from gemini_translator.cli import _collect_untranslated_fix_items


def _make_record(text_body: str, chapter="OEBPS/ch1.xhtml", file_path="/tmp/ch1_validated.html"):
    html = f"<html><body><p>{text_body}</p></body></html>"
    return {"chapter": chapter, "file": file_path, "translated_html": html}


def test_oversized_node_is_not_grouped_for_autofix_default_limit():
    """Узел длиннее дефолтного --max-context-chars=2000 не должен уходить в автозамену."""
    # Один текстовый узел без внутренней разметки, длиннее 2000 символов, с
    # недопереведённым латинским словом внутри кириллического текста и меткой-
    # якорем в конце, чтобы легко проверить, что хвост не потерялся бы при замене.
    body = "б" * 1000 + " Warehouse " + "в" * 2000 + " ЯКОРЬ_ХВОСТА_КОНЦА"
    assert len(body) > 2000

    record = _make_record(body)
    data_items, soup_cache, scan_issues = _collect_untranslated_fix_items(
        [record], word_exceptions=set()
    )

    # Проблема по-прежнему должна быть видна пользователю в отчёте сканирования...
    assert scan_issues
    assert "Warehouse" in scan_issues[0]["untranslated_words"]

    # ...но автозамена для этого (слишком длинного) узла создаваться не должна —
    # иначе при применении правки был бы потерян хвост исходного текста узла.
    assert data_items == []


def test_oversized_node_not_grouped_with_lowered_limit():
    """Тот же дефект тривиально воспроизводится при пониженном --max-context-chars."""
    body = "б" * 200 + " Warehouse " + "в" * 400 + " ЯКОРЬ_ХВОСТА"
    record = _make_record(body)

    data_items, soup_cache, scan_issues = _collect_untranslated_fix_items(
        [record], word_exceptions=set(), max_context_chars=500
    )

    assert scan_issues
    assert data_items == []


def test_short_node_over_limit_container_still_grouped_normally():
    """Контроль: обычный короткий узел с недопереведённым словом по-прежнему обрабатывается.

    Здесь контекст самого блока (<p>) превышает лимит, но отдельный текстовый узел
    с термином укладывается в лимит после сужения до самого узла — такой случай
    не является дефектным и должен продолжать формировать группу для автозамены.
    """
    body = "Перевод Warehouse остался."
    record = _make_record(body)

    data_items, soup_cache, scan_issues = _collect_untranslated_fix_items(
        [record], word_exceptions=set()
    )

    assert len(data_items) == 1
    assert "Warehouse" in data_items[0]["context"]
    assert data_items[0]["occurrences"]
