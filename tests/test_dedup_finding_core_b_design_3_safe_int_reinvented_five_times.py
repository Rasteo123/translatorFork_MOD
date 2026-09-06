# -*- coding: utf-8 -*-
"""Тесты dedup finding-core-b/design/3: safe_int переизобретён 5 раз.

Пять мест независимо реализовывали «безопасный int() с дефолтом»:

- gemini_translator/core/consistency_engine.py
  ``_ConsistencyMockWorker._safe_int`` (staticmethod) — клампит к minimum.
- gemini_translator/core/worker_helpers/provider_orchestrator.py
  ``_safe_int`` — клампит к minimum (по умолчанию 0) и опционально к maximum.
- gemini_translator/qa/handler_factory.py ``_safe_int`` — клампит к minimum
  (обязательный позиционный параметр).
- gemini_translator/ui/dialogs/setup.py ``_safe_int`` (локальная функция
  внутри ``_resolve_auto_translation_options``) — БЕЗ клампа, default только
  при ошибке парсинга.
- ranobelib/api_upload.py ``_safe_int`` — БЕЗ клампа, аналогично setup.py.

Поведение разошлось намеренно по смыслу поля (workspace_index/timeout/
max_log_mb действительно не могут быть меньше 1/60/1 и клампятся; team_id/
batch_token_limit/chapter number — просто числа, отрицательные и нулевые
значения там осмысленны и не клампались).

Каноническая реализация: gemini_translator/utils/helpers.py:safe_int(value,
default=0, minimum=None, maximum=None). minimum/maximum по умолчанию None —
это НЕ клампает (сохраняет поведение setup.py/api_upload.py), а вызовы,
которым нужен кламп (consistency_engine, provider_orchestrator,
handler_factory), передают minimum (и, где нужно, maximum) явно — поведение
всех пяти бывших мест вызова не меняется.

Важный краевой случай, который сохраняет каноническая реализация: если
value не парсится, default тоже подставляется под клампы (так вели себя
все три клампающие копии — они клампили результат ПОСЛЕ подстановки
default, а не только успешно распарсенное значение).

(a) Характеризационные тесты фиксируют поведение канонической функции на
    граничных случаях, которые различали копии.
(b) Маршрутизационные тесты подменяют ``safe_int`` в модуле, откуда его
    реально читает каждое бывшее место вызова, и проверяют, что вызов идёт
    через неё — обязаны падать до рефакторинга (каждое место держало свою
    копию) и проходить после.
(c) Тесты на отсутствие мёртвых копий после рефакторинга.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys

from gemini_translator.utils.helpers import safe_int

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RANOBELIB_DIR = os.path.join(_REPO_ROOT, "ranobelib")
if _RANOBELIB_DIR not in sys.path:
    sys.path.insert(0, _RANOBELIB_DIR)

import api_upload as ranobelib_api_upload  # noqa: E402  (path insert above)


# ─── (a) Характеризация канонической safe_int ──────────────────────────────


def test_parses_valid_value():
    assert safe_int("42", 0) == 42
    assert safe_int(7, 0) == 7


def test_invalid_value_returns_default():
    assert safe_int("not-a-number", 5) == 5
    assert safe_int(None, 3) == 3


def test_default_arg_is_zero_matching_setup_and_api_upload_signature():
    # setup.py/_safe_int(value, default=0) и ranobelib/_safe_int(value,
    # default=0) вызывались с одним позиционным аргументом.
    assert safe_int("bad") == 0


def test_no_clamp_by_default_matches_setup_and_api_upload_behavior():
    # Ни setup.py, ни ranobelib/api_upload.py никогда не клампили —
    # отрицательные и нулевые значения должны проходить как есть, пока
    # minimum не передан явно.
    assert safe_int(-5, 0) == -5
    assert safe_int(0, 0) == 0


def test_minimum_clamps_up_matches_orchestrator_consistency_handler_factory():
    assert safe_int(0, 0, minimum=1) == 1
    assert safe_int(-100, 0, minimum=60) == 60
    assert safe_int(5, 0, minimum=1) == 5


def test_invalid_value_with_minimum_clamps_default_too():
    # Все три клампающие копии клампили default ПОСЛЕ подстановки, а не
    # только успешно распарсенное значение.
    assert safe_int("garbage", default=0, minimum=1) == 1


def test_maximum_clamps_down_matches_orchestrator_behavior():
    assert safe_int(100, 8, minimum=1, maximum=32) == 32
    assert safe_int(5, 8, minimum=1, maximum=32) == 5


# ─── (b) Маршрутизация: бывшие места вызова используют каноническую safe_int


def test_consistency_engine_routes_through_canonical_safe_int(monkeypatch):
    from gemini_translator.core import consistency_engine as ce_module

    calls = []

    def fake_safe_int(value, default, minimum=None, maximum=None):
        calls.append((value, default, minimum, maximum))
        return default

    monkeypatch.setattr(ce_module, "safe_int", fake_safe_int)

    worker = ce_module._ConsistencyMockWorker(None)
    worker.configure(
        provider_config={},
        model_config={},
        config={"workascii_refresh_every_requests": 7},
        api_key_value="key",
        default_model_name="model",
    )

    assert calls, (
        "_ConsistencyMockWorker.configure больше не вызывает safe_int — "
        "маршрутизация к канонической реализации сломана"
    )


def test_provider_orchestrator_routes_through_canonical_safe_int(monkeypatch):
    from gemini_translator.core.worker_helpers import provider_orchestrator as po_module

    calls = []

    def fake_safe_int(value, default, minimum=None, maximum=None):
        calls.append((value, default, minimum, maximum))
        return default

    monkeypatch.setattr(po_module, "safe_int", fake_safe_int)

    class _Worker:
        pass

    po_module._normalize_pass_specs(_Worker())

    assert calls, (
        "_normalize_pass_specs больше не вызывает safe_int — маршрутизация "
        "к канонической реализации сломана"
    )


def test_handler_factory_routes_through_canonical_safe_int(monkeypatch):
    from gemini_translator.qa import handler_factory as hf_module

    calls = []

    def fake_safe_int(value, default, minimum=None, maximum=None):
        calls.append((value, default, minimum, maximum))
        return default

    monkeypatch.setattr(hf_module, "safe_int", fake_safe_int)

    hf_module.QaHandlerWorker(
        settings_manager=None,
        provider_config={},
        model_config={},
        api_key="key",
        session_settings={},
    )

    assert calls, (
        "QaHandlerWorker.__init__ больше не вызывает safe_int — "
        "маршрутизация к канонической реализации сломана"
    )


def test_setup_dialog_routes_through_canonical_safe_int(monkeypatch):
    from gemini_translator.ui.dialogs import setup as setup_module

    calls = []

    def fake_safe_int(value, default=0, minimum=None, maximum=None):
        calls.append((value, default, minimum, maximum))
        return default

    monkeypatch.setattr(setup_module, "safe_int", fake_safe_int)

    class _TranslationOptionsWidgetStub:
        def get_settings(self):
            return {}

    class _Harness:
        _resolve_auto_translation_options = (
            setup_module.InitialSetupDialog._resolve_auto_translation_options
        )

        def __init__(self):
            self.translation_options_widget = _TranslationOptionsWidgetStub()

        def _estimate_auto_task_size_limit(self, token_limit):
            return int(token_limit), "profile"

    harness = _Harness()
    harness._resolve_auto_translation_options(
        {"batch_token_limit_override": 100, "batch_chapter_limit_override": 3}
    )

    assert calls, (
        "_resolve_auto_translation_options больше не вызывает safe_int — "
        "маршрутизация к канонической реализации сломана"
    )


def test_ranobelib_api_upload_routes_through_canonical_safe_int(monkeypatch):
    calls = []

    def fake_safe_int(value, default=0, minimum=None, maximum=None):
        calls.append((value, default, minimum, maximum))
        return default

    monkeypatch.setattr(ranobelib_api_upload, "safe_int", fake_safe_int)

    chapters = [
        {
            "volume": "1",
            "number": "1",
            "branches": [{"branch_id": "9", "teams": [{"id": "5"}]}],
        }
    ]

    ranobelib_api_upload._get_latest_chapter_config(chapters, "1")

    assert calls, (
        "_get_latest_chapter_config больше не вызывает safe_int — "
        "маршрутизация к канонической реализации сломана"
    )


# ─── (c) Мёртвых копий не осталось ─────────────────────────────────────────


def test_no_leftover_safe_int_copies():
    from gemini_translator.core import consistency_engine as ce_module
    from gemini_translator.core.worker_helpers import provider_orchestrator as po_module
    from gemini_translator.qa import handler_factory as hf_module
    from gemini_translator.ui.dialogs import setup as setup_module
    import inspect

    assert not hasattr(ce_module._ConsistencyMockWorker, "_safe_int")
    assert not hasattr(po_module, "_safe_int")
    assert not hasattr(hf_module, "_safe_int")
    assert not hasattr(ranobelib_api_upload, "_safe_int")

    source = inspect.getsource(
        setup_module.InitialSetupDialog._resolve_auto_translation_options
    )
    assert "def _safe_int" not in source, (
        "gemini_translator/ui/dialogs/setup.py всё ещё содержит локальную "
        "копию _safe_int внутри _resolve_auto_translation_options"
    )
