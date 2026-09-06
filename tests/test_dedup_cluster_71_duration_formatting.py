# -*- coding: utf-8 -*-
"""Тесты dedup cluster-71: единая функция форматирования длительности.

Три места форматировали секунды в человекочитаемую строку независимо друг
от друга:

- gemini_translator/ui/dialogs/validation_dialogs/translation_quality_controller.py
  ``_humanize_seconds`` — округляет минуты (остаток >= 30 сек -> +1 мин),
  секунды отдельно никогда не показываются, единица "с"/"мин"/"ч" с пробелом.
- gemini_translator/ui/dialogs/setup.py ``_format_duration`` — мёртвый код,
  нигде не вызывается (проверено grep по всему репозиторию), подлежит
  удалению без замены.
- ranobelib/utils.py ``format_timedelta`` — принимает timedelta (не число
  секунд), отрицательное значение -> "—", секунды показываются рядом с
  минутами без округления, без пробела перед единицей ("1ч 01мин",
  "5мин 03сек", "5сек").

Каноническая реализация: gemini_translator/utils/text.py:format_duration —
принимает секунды и явные флаги (round_minutes, unit_spacing, seconds_unit,
dash_on_negative) под каждый из двух выживших стилей отображения.
ranobelib/utils.py:format_timedelta остаётся тонкой обёрткой, потому что её
интерфейс (timedelta, а не число секунд) действительно другой и её вызывают
2 места (ranobelib/api_upload.py, ranobelib/workers.py). Импорт канонической
функции внутри format_timedelta сделан ленивым (локальный import внутри
функции), чтобы модуль ranobelib/utils.py — лёгкий leaf-модуль, от которого
не должны тянуться lxml/bs4/requests/api-конфиг переводчика — не платил за
тяжёлые зависимости gemini_translator.utils.text на этапе своего импорта.

(a) Характеризационные тесты фиксируют поведение канонической функции на
    граничных случаях, которые раньше отличали три копии.
(b) Маршрутизационные тесты подменяют ``format_duration`` в модуле, откуда
    его реально читает каждое бывшее место вызова, и проверяют, что вызов
    действительно идёт через неё — эти тесты обязаны падать до рефакторинга
    (каждое место держало свою копию логики форматирования) и проходить
    после.
(c) Тест на лёгкость импорта ranobelib/utils.py — фиксирует, что импорт
    leaf-модуля не тянет за собой lxml/bs4/requests (регресс из предыдущей
    волны: модульный ``from gemini_translator.utils.text import
    format_duration`` в ranobelib/utils.py тянул весь этот тяжёлый стек).
"""
import os
import sys
from datetime import timedelta

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RANOBELIB_DIR = os.path.join(_REPO_ROOT, "ranobelib")

if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
# insert(0, ...) — не append: ranobelib-тесты в этом наборе (например
# test_ranobelib_epub_parser.py) сами вставляют RANOBELIB_DIR в позицию 0
# и полагаются на то, что "workers"/"models"/"parsers" резолвятся именно
# из ranobelib/, а не из одноимённых модулей qidian_rulate/. Если этот файл
# соберётся раньше них и добавит RANOBELIB_DIR через append, их
# "if RANOBELIB_DIR not in sys.path" увидит путь уже присутствующим и не
# переставит его в 0 — сломав их резолвинг. Используем тот же паттерн
# insert(0, ...), что и остальные тесты в tests/test_ranobelib_*.py и
# tests/test_dedup_cluster_00/02/57 — это ничего не меняет по сравнению с
# уже существующим соглашением по всему набору тестов. Известная общая
# проблема с "import main" (ranobelib/main.py тоже плоский) — пред-
# существующая (см. test_dedup_cluster_00_chapterdata.py:29,
# test_dedup_cluster_57_playwright_launcher.py:23) и вне cluster-71:
# main.py не входит в разрешённый список файлов этой волны.
if _RANOBELIB_DIR not in sys.path:
    sys.path.insert(0, _RANOBELIB_DIR)

from gemini_translator.utils.text import format_duration  # noqa: E402


# ─── (a) Характеризация канонической format_duration ──────────────────────


def test_round_minutes_style_matches_former_humanize_seconds():
    # Стиль _humanize_seconds: округление минут, без секунд, единица "с".
    assert format_duration(0, round_minutes=True, seconds_unit="с") == "0 с"
    assert format_duration(45, round_minutes=True, seconds_unit="с") == "45 с"
    assert format_duration(90, round_minutes=True, seconds_unit="с") == "2 мин"
    assert format_duration(100, round_minutes=True, seconds_unit="с") == "2 мин"
    assert format_duration(3700, round_minutes=True, seconds_unit="с") == "1 ч 01 мин"
    # 59 мин 30 сек округляется вверх до "60 мин" буквально (часовой блок не
    # пересчитывается) — это исходное поведение _humanize_seconds(3570).
    assert format_duration(3570, round_minutes=True, seconds_unit="с") == "60 мин"
    # Отрицательное значение зажимается в ноль, а не превращается в "—".
    assert format_duration(-5, round_minutes=True, seconds_unit="с") == "0 с"


def test_compact_style_matches_former_format_timedelta():
    # Стиль format_timedelta: без пробела перед единицей, секунды
    # показываются рядом с минутами без округления, отрицательное -> "—".
    assert format_duration(5, unit_spacing="", dash_on_negative=True) == "5сек"
    assert format_duration(0, unit_spacing="", dash_on_negative=True) == "0сек"
    assert format_duration(303, unit_spacing="", dash_on_negative=True) == "5мин 03сек"
    assert format_duration(3661, unit_spacing="", dash_on_negative=True) == "1ч 01мин"
    assert format_duration(-1, unit_spacing="", dash_on_negative=True) == "—"


# ─── (b) Маршрутизация: бывшие места вызова используют каноническую функцию ─


def test_humanize_seconds_routes_through_canonical_format_duration(monkeypatch):
    from gemini_translator.ui.dialogs.validation_dialogs import (
        translation_quality_controller as tqc,
    )

    sentinel = "__CANONICAL_FORMAT_DURATION_CALLED__"
    # raising=True (дефолт): без него тест не различает "имя не
    # импортировано в модуль" и "функция его просто не вызвала" — тут явно
    # нужен первый инвариант (модульный импорт format_duration в tqc).
    monkeypatch.setattr(tqc, "format_duration", lambda *a, **k: sentinel)

    assert tqc._humanize_seconds(90) == sentinel


def test_format_timedelta_routes_through_canonical_format_duration(monkeypatch):
    # format_timedelta импортирует format_duration лениво (локально внутри
    # функции), поэтому патчить нужно источник — gemini_translator.utils.text
    # — а не несуществующий атрибут на модуле ranobelib.utils.
    import gemini_translator.utils.text as text_module
    import utils as ranobelib_utils

    sentinel = "__CANONICAL_FORMAT_DURATION_CALLED__"
    monkeypatch.setattr(text_module, "format_duration", lambda *a, **k: sentinel)

    assert ranobelib_utils.format_timedelta(timedelta(seconds=90)) == sentinel


def test_setup_dialog_no_longer_defines_dead_format_duration_copy():
    from gemini_translator.ui.dialogs import setup as setup_module

    assert not hasattr(setup_module, "_format_duration"), (
        "gemini_translator/ui/dialogs/setup.py всё ещё содержит мёртвую копию "
        "_format_duration (нигде не вызывается, канонический источник — "
        "gemini_translator/utils/text.py:format_duration)"
    )


# ─── (c) ranobelib/utils.py остаётся лёгким leaf-модулем ──────────────────


def test_ranobelib_utils_import_does_not_pull_in_heavy_translator_stack():
    """Регресс-тест на замечание рецензента (cluster-71, major).

    Модульный ``from gemini_translator.utils.text import format_duration``
    в ranobelib/utils.py тянул на этапе импорта lxml/bs4/requests и
    gemini_translator/api/config.py — путь, до которого лёгкому
    ranobelib-модулю (нужному, например, для headless-парсинга epub) дела
    быть не должно. Пересобираем модуль в чистом подпроцессе, чтобы не
    зависеть от того, что другие тесты этого файла уже импортировали
    gemini_translator.utils.text заранее (что скрыло бы регресс).
    """
    import subprocess

    probe = (
        "import sys\n"
        f"sys.path.insert(0, {_REPO_ROOT!r})\n"
        f"sys.path.insert(0, {_RANOBELIB_DIR!r})\n"
        "import utils as ranobelib_utils\n"
        "heavy_prefixes = ('lxml', 'bs4', 'requests')\n"
        "leaked = sorted(\n"
        "    m for m in sys.modules\n"
        "    if m.split('.')[0] in heavy_prefixes\n"
        ")\n"
        "print('LEAKED=' + ','.join(leaked))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    leaked_line = next(
        line for line in result.stdout.splitlines() if line.startswith("LEAKED=")
    )
    leaked = leaked_line[len("LEAKED="):]
    assert leaked == "", (
        "import ranobelib/utils.py подтянул тяжёлые модули переводчика: "
        f"{leaked} — format_duration должен импортироваться лениво внутри "
        "format_timedelta, а не на уровне модуля"
    )
