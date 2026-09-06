# -*- coding: utf-8 -*-
"""Тесты для pcluster-65: тело _pixmap (построение ключа кэша по состоянию
+ вызов PixmapCache.get_or_render с лямбдой) было продублировано дословно
(с точностью до имени параметра kind/action) между
GlossaryActionDelegate._pixmap (gemini_translator/ui/dialogs/glossary_dialogs/
action_delegate.py) и ReorderArrowDelegate._pixmap
(gemini_translator/ui/widgets/chapter_list_widget.py).

Кластер-06 уже вынес сам словарь-кэш в PixmapCache.get_or_render (см.
tests/test_dedup_cluster_06_pixmap_cache.py) — но обвязка "table.devicePixelRatioF()
-> ключ (state, hovered, pressed, enabled, round(dpr*100)) -> get_or_render(key,
lambda: render_template(...))" вокруг него оставалась своя в каждом делегате.

Каноническая реализация этой обвязки: PixmapCache.pixmap(table, state, hovered,
pressed, enabled, render_template) в gemini_translator.ui.widgets.delegate_utils
(тот же класс, что и раньше — новый модуль не создаётся). _pixmap() каждого
делегата становится тонкой однострочной обёрткой над ней. _render_template
(сам рендер виджета) остаётся разным у делегатов и не трогается — базовый
класс/наследование НЕ используются: action_delegate.py обязан лениво
импортировать всё из gemini_translator.ui.widgets внутри __init__ (см.
комментарий там же и в delegate_utils.py) — импорт на уровне модуля через
....ui.widgets тянет ui/widgets/__init__.py -> glossary_widget.py -> glossary.py
-> обратно action_delegate.py, который в этот момент ещё не определил свои
имена (самоцикл импорта модуля). Наследование от класса в ui/widgets/delegates.py
потребовало бы именно такого импорта на уровне модуля, поэтому вместо базового
класса используется композиция с передачей self._render_template как параметра.

(a) Характеризационные тесты фиксируют контракт PixmapCache.pixmap() напрямую.
(b) Маршрутизационные тесты патчат PixmapCache.pixmap и проверяют, что
    _pixmap() каждого делегата реально идёт через неё. До рефакторинга у
    каждого делегата собственное построение ключа + прямой вызов
    get_or_render, метода pixmap() не существует — тест падает (RED). После
    рефакторинга оба делегата используют PixmapCache.pixmap() (GREEN).
"""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gemini_translator.ui.dialogs.glossary_dialogs.action_delegate import (
    GlossaryActionDelegate,
)
from gemini_translator.ui.widgets.chapter_list_widget import ReorderArrowDelegate
from gemini_translator.ui.widgets.delegate_utils import PixmapCache


class _FakeTable:
    """Минимальная замена QTableWidget: делегату/кэшу нужен только DPI."""

    def __init__(self, dpr=1.0):
        self._dpr = dpr

    def devicePixelRatioF(self):
        return self._dpr


def _new_glossary_delegate():
    delegate = GlossaryActionDelegate.__new__(GlossaryActionDelegate)
    delegate._table = _FakeTable()
    delegate._pixmap_cache = PixmapCache()
    delegate._icons = {}
    return delegate


def _new_reorder_delegate():
    delegate = ReorderArrowDelegate.__new__(ReorderArrowDelegate)
    delegate._table = _FakeTable()
    delegate._pixmap_cache = PixmapCache()
    return delegate


def _stub_render_template(calls):
    def _render(*args):
        calls.append(args)
        return object()

    return _render


# ─── (a) Характеризационные тесты контракта PixmapCache.pixmap() ────────────

class PixmapCachePixmapMethodTests(unittest.TestCase):
    def test_cache_hit_returns_same_object_without_rerender(self):
        cache = PixmapCache()
        table = _FakeTable()
        calls = []
        render = _stub_render_template(calls)

        first = cache.pixmap(table, 'gen', False, False, True, render)
        second = cache.pixmap(table, 'gen', False, False, True, render)

        self.assertIs(first, second)
        self.assertEqual(len(calls), 1)

    def test_different_state_produces_separate_cache_entry(self):
        cache = PixmapCache()
        table = _FakeTable()
        calls = []
        render = _stub_render_template(calls)

        normal = cache.pixmap(table, 'gen', False, False, True, render)
        hovered = cache.pixmap(table, 'gen', True, False, True, render)
        pressed = cache.pixmap(table, 'gen', False, True, True, render)
        disabled = cache.pixmap(table, 'gen', False, False, False, render)
        other_state = cache.pixmap(table, 'delete', False, False, True, render)

        self.assertIsNot(normal, hovered)
        self.assertIsNot(normal, pressed)
        self.assertIsNot(normal, disabled)
        self.assertIsNot(normal, other_state)
        self.assertEqual(len(calls), 5)

    def test_different_dpr_produces_separate_cache_entry(self):
        cache = PixmapCache()
        calls = []
        render = _stub_render_template(calls)

        low_dpi = cache.pixmap(_FakeTable(1.0), 'gen', False, False, True, render)
        high_dpi = cache.pixmap(_FakeTable(2.0), 'gen', False, False, True, render)

        self.assertIsNot(low_dpi, high_dpi)
        self.assertEqual(len(calls), 2)

    def test_render_template_called_with_state_hovered_pressed_enabled_dpr(self):
        cache = PixmapCache()
        table = _FakeTable(1.5)
        calls = []
        render = _stub_render_template(calls)

        cache.pixmap(table, 'delete', True, False, True, render)

        self.assertEqual(calls, [('delete', True, False, True, 1.5)])

    def test_invalidate_forces_rerender(self):
        cache = PixmapCache()
        table = _FakeTable()
        calls = []
        render = _stub_render_template(calls)

        first = cache.pixmap(table, 'gen', False, False, True, render)
        cache.invalidate()
        second = cache.pixmap(table, 'gen', False, False, True, render)

        self.assertIsNot(first, second)
        self.assertEqual(len(calls), 2)


# ─── (b) Маршрутизационные тесты: _pixmap() каждого делегата идёт через
#         канонический PixmapCache.pixmap() ────────────────────────────────

class PixmapRenderRoutingTests(unittest.TestCase):
    def test_glossary_action_delegate_pixmap_routes_through_shared_helper(self):
        delegate = _new_glossary_delegate()
        sentinel = object()
        with patch(
            "gemini_translator.ui.widgets.delegate_utils.PixmapCache.pixmap",
            return_value=sentinel,
        ) as mocked:
            result = delegate._pixmap('gen', True, False, True)

        mocked.assert_called_once()
        self.assertIs(result, sentinel)
        # render_template, переданный в общий хелпер — это ровно связанный
        # self._render_template делегата (а не отдельная копия обвязки).
        args, kwargs = mocked.call_args
        passed_render_template = args[-1] if args else kwargs.get("render_template")
        self.assertEqual(passed_render_template, delegate._render_template)

    def test_reorder_arrow_delegate_pixmap_routes_through_shared_helper(self):
        delegate = _new_reorder_delegate()
        sentinel = object()
        with patch(
            "gemini_translator.ui.widgets.delegate_utils.PixmapCache.pixmap",
            return_value=sentinel,
        ) as mocked:
            result = delegate._pixmap('up', True, False, True)

        mocked.assert_called_once()
        self.assertIs(result, sentinel)
        args, kwargs = mocked.call_args
        passed_render_template = args[-1] if args else kwargs.get("render_template")
        self.assertEqual(passed_render_template, delegate._render_template)


if __name__ == "__main__":
    unittest.main()
