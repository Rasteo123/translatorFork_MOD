"""Тесты для дедупликации кластера cluster-50 (_as_list -> utils.helpers.as_list).

(а) Характеризационные тесты канонической реализации ``as_list``.
(б) Тест-маршрутизация: каждая бывшая копия должна вызывать каноническую
    функцию (проверяется через monkeypatch по имени модуля).
"""

from __future__ import annotations

import gemini_translator.benchmark.evaluator as evaluator_module
import gemini_translator.mcp.commands as commands_module
import gemini_translator.utils.helpers as helpers_module


# ---------------------------------------------------------------------------
# (a) Характеризация канонической реализации
# ---------------------------------------------------------------------------


def test_as_list_none_returns_empty_list():
    assert helpers_module.as_list(None) == []


def test_as_list_passthrough_for_list():
    value = [1, 2, 3]
    result = helpers_module.as_list(value)
    assert result == [1, 2, 3]
    assert result is value  # копии evaluator.py/commands.py возвращали тот же объект


def test_as_list_unwraps_tuple():
    assert helpers_module.as_list((1, 2, 3)) == [1, 2, 3]


def test_as_list_wraps_scalar_string():
    assert helpers_module.as_list("chapter-1") == ["chapter-1"]


def test_as_list_wraps_scalar_other_types():
    assert helpers_module.as_list(42) == [42]
    assert helpers_module.as_list({"a": 1}) == [{"a": 1}]


def test_as_list_set_default_wraps_as_single_scalar():
    # Поведение evaluator.py/benchmark_page.py (без явной обработки set):
    # набор целиком уходит в default-ветку `[value]`.
    value = {1, 2, 3}
    result = helpers_module.as_list(value)
    assert result == [value]


def test_as_list_set_sort_sets_true_returns_sorted_list():
    # Поведение mcp/commands.py: sorted(value) для детерминизма CLI-аргументов.
    assert helpers_module.as_list({3, 1, 2}, sort_sets=True) == [1, 2, 3]


# ---------------------------------------------------------------------------
# (б) Тест-маршрутизация: бывшие места вызова должны идти через as_list
# ---------------------------------------------------------------------------


def test_evaluator_evaluate_translation_routes_through_canonical_as_list(monkeypatch):
    calls = []

    def fake_as_list(value, *, sort_sets=False):
        calls.append(value)
        return [] if value is None else list(value) if isinstance(value, list) else [value]

    monkeypatch.setattr(evaluator_module, "as_list", fake_as_list, raising=False)

    evaluator_module.evaluate_translation(
        "<p>текст слово</p>",
        "текст слово",
        checks={"required": ["слово"], "forbidden": ["плохое"], "placeholders": []},
    )

    assert calls, "evaluate_translation должен вызывать as_list через evaluator_module.as_list"


def test_mcp_commands_append_options_routes_through_canonical_as_list(monkeypatch):
    calls = []

    def fake_as_list(value, *, sort_sets=False):
        calls.append(value)
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, tuple):
            return list(value)
        if isinstance(value, set):
            return sorted(value) if sort_sets else [value]
        return [value]

    monkeypatch.setattr(commands_module, "as_list", fake_as_list, raising=False)

    args = {"chapter": ["1", "2"]}
    argv: list[str] = []
    commands_module._append_options(
        argv, args, (commands_module.OptionSpec("chapter", "--chapter", "repeated"),)
    )

    assert calls, "_append_options должен вызывать as_list через commands_module.as_list"
    assert argv == ["--chapter", "1", "--chapter", "2"]


def test_benchmark_page_as_list_removed_in_favor_of_canonical():
    # Метод-копия self._as_list должен быть удалён из класса страницы бенчмарка
    # после рефакторинга — используется каноническая as_list из utils.helpers.
    import gemini_translator.ui.pages.benchmark_page as benchmark_page_module

    assert not hasattr(benchmark_page_module.PromptBenchmarkPage, "_as_list"), (
        "BenchmarkPage._as_list должен быть удалён; используйте as_list из "
        "gemini_translator.utils.helpers"
    )
    assert hasattr(benchmark_page_module, "as_list"), (
        "benchmark_page.py должен импортировать каноническую as_list"
    )
