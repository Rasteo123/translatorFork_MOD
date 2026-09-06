"""Дедуп dups-gt_benchmark_evaluator-55 (finding mcp-bench-cli/design/8):

benchmark/evaluator.py заново реализовывал извлечение видимого текста
(visible_text/normalize_text) и оценку токенов (свой estimate_tokens) со своими
константами, вместо переиспользования канонических
utils/html_text.py::extract_visible_text_normalized и
utils/helpers.py::estimate_gemini_tokens.

Характеризационные тесты фиксируют поведение канонических реализаций на
краевых случаях, которые как раз расходились между копиями (script/style в
"видимом" тексте, отдельный делитель для кириллицы, минимум 1 токен для
непустого текста). Тест-маршрутизация проверяет, что evaluator.py реально
вызывает канонические функции, а не свои копии.
"""

from __future__ import annotations

import gemini_translator.benchmark.evaluator as evaluator_mod
import gemini_translator.utils.helpers as helpers_mod
from gemini_translator.utils.html_text import extract_visible_text_normalized


# --- (a) характеризация канонических реализаций -----------------------------


def test_extract_visible_text_normalized_excludes_script_and_style():
    html = "<p>Keep me</p><script>var evil = 1;</script><style>.c{color:red}</style>"
    text = extract_visible_text_normalized(html)
    assert "Keep me" in text
    assert "evil" not in text
    assert "color" not in text


def test_estimate_gemini_tokens_uses_dedicated_cyrillic_divisor():
    # 10 кириллических символов должны делиться на GEMINI_CYRILLIC_CHARS_PER_TOKEN (2.2),
    # а не попадать в общий "other" делитель (2.5/2.3), как это делала старая
    # копия evaluator.py, не выделявшая кириллицу отдельно.
    text = "п" * 10
    tokens = helpers_mod.estimate_gemini_tokens(text)
    import math

    expected = max(1, math.ceil(10 / helpers_mod.GEMINI_CYRILLIC_CHARS_PER_TOKEN))
    assert tokens == expected


def test_estimate_gemini_tokens_never_zero_for_nonempty_text():
    # Старая копия evaluator.py (int(...) без ceil/без пола) отдавала 0 токенов
    # для одиночного ascii-символа; каноническая версия гарантирует минимум 1.
    assert helpers_mod.estimate_gemini_tokens("a") >= 1


# --- (b) маршрутизация: evaluator.py обязан звать канонические функции ------


def test_evaluator_uses_canonical_estimate_gemini_tokens_without_alias():
    # Алиас `estimate_gemini_tokens as estimate_tokens` убран: evaluator.py и
    # runner.py импортируют канонический хелпер под его собственным именем.
    assert evaluator_mod.estimate_gemini_tokens is helpers_mod.estimate_gemini_tokens
    assert not hasattr(evaluator_mod, "estimate_tokens")


def test_runner_imports_estimate_gemini_tokens_directly_from_helpers():
    import gemini_translator.benchmark.runner as runner_mod

    assert runner_mod.estimate_gemini_tokens is helpers_mod.estimate_gemini_tokens
    assert not hasattr(runner_mod, "estimate_tokens")


def test_evaluate_translation_routes_visible_text_through_canonical_helper(monkeypatch):
    assert hasattr(evaluator_mod, "extract_visible_text_normalized"), (
        "evaluator.py должен импортировать extract_visible_text_normalized "
        "из utils/html_text.py вместо локальной копии visible_text/normalize_text"
    )

    calls = []

    def fake_extract(value, **kwargs):
        calls.append((value, kwargs))
        return "stub visible text"

    monkeypatch.setattr(evaluator_mod, "extract_visible_text_normalized", fake_extract)

    evaluator_mod.evaluate_translation("<p>Привет мир</p>", "<p>Hello world</p>")

    assert calls, "evaluate_translation не вызвал канонический extract_visible_text_normalized"


def test_evaluate_translation_excludes_script_style_from_cjk_residue():
    # Характеризация, а не фикс бага: bs4 (>=4.9) get_text() и старая
    # evaluator.visible_text() уже по умолчанию не отдавали содержимое
    # <script>/<style> (проверено эмпирически: soup.get_text(' ') на этом же
    # HTML даёт тот же результат). Единственный сценарий регресса был бы на
    # мёртвой ветке BeautifulSoup is None (bs4 — жёсткая зависимость,
    # requirements.txt). Тест фиксирует, что канонический
    # extract_visible_text_normalized сохраняет это же поведение.
    source_html = "<p>Hello</p>"
    output_text = "<p>Hello</p><script>/* 中文 comment, ignored */</script>"

    result = evaluator_mod.evaluate_translation(source_html, output_text)

    assert result.metrics["cjk_residue_chars"] == 0
    assert "CJK residue chars" not in " ".join(result.issues)


# --- (c) major: head/title/meta исключены из "видимого текста" --------------
#
# Реальное (а не мнимое, как со script/style выше) расхождение поведения:
# extract_visible_text_normalized исключает <head>/<title>/<meta>, а старая
# evaluator.visible_text() гоняла BeautifulSoup.get_text() по всему документу
# и такой текст учитывала. Воспроизведено на фикстуре репозитория
# benchmarks/prompt_fidelity_ch21_25.json (cases[0]["reference"], поле
# <head><title>«穿越鬥羅之生死簿»第21章...</title></head>): на HEAD-версии
# evaluator.py cjk_residue_chars == 14, output_visible_chars == 11585; на
# текущей — 0 и 11565 соответственно (пересчитано напрямую поверх обеих
# версий модуля). Ниже — упрощённый, самодостаточный повтор того же случая.


def test_evaluate_translation_cjk_in_title_is_not_flagged_as_residue():
    """Пиннит осознанно принятое изменение семантики метрик бенчмарка.

    <title> с непереведённым CJK-текстом (реалистичный случай: модель
    переводит тело главы, но не трогает XHTML-заголовок в <head>) больше не
    штрафуется как "CJK residue" и не искажает output_visible_chars /
    length_ratio / reference_similarity, потому что эти теги вне
    "видимого текста" канонического extract_visible_text_normalized.
    Возврат прежнего сигнала потребовал бы параметра "исключаемые теги" в
    utils/html_text.py — файл вне периметра этого дедупа, поэтому выбор
    здесь — принять чужую семантику и явно её задокументировать (см. также
    test_evaluator_module_documents_head_title_meta_exclusion ниже).
    """
    output_text = (
        "<html><head><title>穿越鬥羅之生死簿</title></head>"
        "<body><p>Fully translated body, no CJK here.</p></body></html>"
    )

    result = evaluator_mod.evaluate_translation("<p>source</p>", output_text)

    assert result.metrics["cjk_residue_chars"] == 0
    assert "CJK residue chars" not in " ".join(result.issues)


def test_evaluator_module_documents_head_title_meta_exclusion():
    # RED до правки: evaluator.py обязан явно объяснять в комментарии рядом
    # с извлечением видимого текста, что <head>/<title>/<meta> исключены из
    # метрик и что это осознанный выбор, а не забытый побочный эффект
    # дедупа (см. major-замечание рецензента о молчаливой потере сигнала по
    # непереведённому <title>).
    import inspect

    source = inspect.getsource(evaluator_mod)
    assert "исключает <head>/<title>/<meta>" in source, (
        "evaluator.py должен явно и дословно упоминать исключение "
        "<head>/<title>/<meta> из видимого текста, чтобы выбор канонической "
        "семантики был задокументирован, а не тихо подразумевался"
    )
