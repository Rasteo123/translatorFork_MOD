# -*- coding: utf-8 -*-
"""
Тесты для finding-utils-io_design_5-calculate-potential-output-siz.

calculate_potential_output_size была продублирована:
  - gemini_translator/utils/epub_tools.py  -- каноническая, живая версия
    (возвращает кортеж (total, tags_len), коэффициенты из api_config,
    используется ui/dialogs/setup.py и покрыта
    tests/test_epub_heading_extraction.py);
  - gemini_translator/utils/helpers.py -- мёртвая копия с другой сигнатурой
    (возвращает одно int-значение, коэффициенты 2.8/1.25/1.8 зашиты в код),
    на неё нет ни одного вызывающего места во всём репозитории.

behavior_choice: канон -- версия epub_tools.py (уже единственная, что
реально импортируется и вызывается). Версия helpers.py удаляется целиком,
поведение вызывающего кода (setup.py) не меняется, т.к. он и раньше не
использовал helpers-версию.

Особенность этого кластера: в отличие от типичного дедупа, у мёртвой копии
нет НИ ОДНОГО вызывающего места, которое рефакторинг мог бы "переключить" на
канон -- переключать нечего, есть только сама копия для удаления. Поэтому
"тест-маршрутизация" здесь выражена как:
  1) факт отсутствия функции в helpers.py после рефакторинга (до рефакторинга
     -- RED, т.к. функция там ещё есть);
  2) статическая проверка, что единственный реальный импортёр
     (ui/dialogs/setup.py) берёт имя из epub_tools, а не из helpers.
Дополнительно -- характеризационные тесты канонической реализации на кейсах,
которые как раз различали копии (CJK-коэффициент из api_config, кортеж как
тип возврата, поведение при ошибке парсинга).
"""

import ast
import pathlib

SETUP_PY = (
    pathlib.Path(__file__).resolve().parents[1]
    / "gemini_translator"
    / "ui"
    / "dialogs"
    / "setup.py"
)


def _imports_of(name, tree):
    """Возвращает список модулей (node.module), из которых импортируется name."""
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.name == name:
                    modules.append(node.module)
    return modules


# ---------------------------------------------------------------------------
# 1) Дубликат в helpers.py должен быть удалён (RED до рефакторинга).
# ---------------------------------------------------------------------------

def test_helpers_no_longer_defines_duplicate_calculate_potential_output_size():
    from gemini_translator.utils import helpers

    assert not hasattr(helpers, "calculate_potential_output_size"), (
        "helpers.py всё ещё содержит мёртвую копию calculate_potential_output_size; "
        "каноническая версия живёт в gemini_translator/utils/epub_tools.py"
    )


# ---------------------------------------------------------------------------
# 2) "Маршрутизация": единственный реальный импортёр функции (setup.py)
#    обязан брать имя из epub_tools, а не из helpers.
# ---------------------------------------------------------------------------

def test_setup_dialog_imports_calculate_potential_output_size_only_from_epub_tools():
    tree = ast.parse(SETUP_PY.read_text(encoding="utf-8"))

    from_epub_tools = _imports_of("calculate_potential_output_size", tree)

    assert from_epub_tools == ["utils.epub_tools"], (
        f"ожидался ровно один импорт calculate_potential_output_size, из "
        f"utils.epub_tools, получено: {from_epub_tools!r}"
    )


# ---------------------------------------------------------------------------
# 3) Характеризационные тесты канонической реализации (epub_tools) --
#    кейсы, которые различали копии: CJK-коэффициент из api_config,
#    возврат кортежа, фолбэк при ошибке парсинга.
# ---------------------------------------------------------------------------

def test_calculate_potential_output_size_uses_cjk_factor_from_api_config(monkeypatch):
    from gemini_translator.utils import epub_tools as et

    monkeypatch.setattr(et.api_config, "CJK_EXPANSION_FACTOR", 3)

    total, tags = et.calculate_potential_output_size("<p>你好</p>", is_cjk=True)

    # "<p>你好</p>" -> 9 символов всего, видимый текст "你好" -- 2 символа.
    assert tags == 7
    assert total == 7 + 2 * 3


def test_calculate_potential_output_size_uses_alphabetic_factor_from_api_config(monkeypatch):
    from gemini_translator.utils import epub_tools as et

    monkeypatch.setattr(et.api_config, "ALPHABETIC_EXPANSION_FACTOR", 2)

    result = et.calculate_potential_output_size("<p>Hello</p>", is_cjk=False)

    assert result == (17, 7)


def test_calculate_potential_output_size_returns_tuple_not_int():
    from gemini_translator.utils import epub_tools as et

    result = et.calculate_potential_output_size("<p>Hi</p>", is_cjk=False)

    assert isinstance(result, tuple)
    assert len(result) == 2


def test_calculate_potential_output_size_falls_back_on_parse_error(monkeypatch):
    from gemini_translator.utils import epub_tools as et

    class BoomBeautifulSoup:
        def __init__(self, *args, **kwargs):
            raise ValueError("boom")

    monkeypatch.setattr("bs4.BeautifulSoup", BoomBeautifulSoup)

    html = "hello world"
    result = et.calculate_potential_output_size(html, is_cjk=False)

    assert result == (len(html) * 2, 0)
