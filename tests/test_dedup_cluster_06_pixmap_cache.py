# -*- coding: utf-8 -*-
"""Тесты для cluster-06: кэш pixmap-рендера кнопки был скопирован дословно
(с точностью до имени параметра) между GlossaryActionDelegate._pixmap
(gemini_translator/ui/dialogs/glossary_dialogs/action_delegate.py) и
ReorderArrowDelegate._pixmap (gemini_translator/ui/widgets/chapter_list_widget.py).

Каноническая реализация: PixmapCache.get_or_render в новом модуле
gemini_translator.ui.widgets.delegate_utils. Оба делегата держат экземпляр
PixmapCache (композиция, self._pixmap_cache) и зовут
self._pixmap_cache.get_or_render(key, render_fn) вместо собственной пары
"self._pixmaps.get(key) / self._pixmaps[key] = ...". Композиция, а не общий
базовый класс: GlossaryActionDelegate импортирует PixmapCache ЛЕНИВО внутри
__init__ (см. комментарий в action_delegate.py) — импорт на уровне модуля
через ....ui.widgets тянет ui/widgets/__init__.py -> glossary_widget.py ->
glossary.py -> обратно action_delegate.py, который в этот момент ещё не
определил свои имена (самоцикл импорта модуля).

_render_template у делегатов остаётся разным (QToolButton с иконкой против
QPushButton со стрелкой-текстом) — рефакторинг НЕ трогает это поведение,
поэтому кэш подменяется через _render_template, а не через реальный рендер
виджета (не нужен QApplication).

(a) Характеризационные тесты фиксируют контракт кэша (общий для обеих копий
    до рефакторинга): попадание по ключу не вызывает повторный рендер и
    возвращает тот же объект pixmap; разный ключ состояния -> новый рендер;
    invalidate_cache() сбрасывает кэш.
(b) Маршрутизационные тесты патчат
    gemini_translator.ui.widgets.delegate_utils.PixmapCache.get_or_render
    и проверяют, что _pixmap() каждого делегата реально идёт через неё.
    До рефакторинга у каждого делегата собственный словарь _pixmaps и модуля
    delegate_utils не существует — тест падает (RED). После рефакторинга оба
    делегата используют PixmapCache и патч перехватывает вызов (GREEN).
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
    """Минимальная замена QTableWidget: делегату нужен только DPI."""

    def devicePixelRatioF(self):
        return 1.0


def _new_glossary_delegate():
    delegate = GlossaryActionDelegate.__new__(GlossaryActionDelegate)
    delegate._table = _FakeTable()
    delegate._pixmap_cache = PixmapCache()
    delegate._icons = {}  # invalidate_cache() тоже чистит кэш иконок
    return delegate


def _new_reorder_delegate():
    delegate = ReorderArrowDelegate.__new__(ReorderArrowDelegate)
    delegate._table = _FakeTable()
    delegate._pixmap_cache = PixmapCache()
    return delegate


def _stub_render_template(calls):
    """render_fn-заглушка: считает вызовы, каждый раз возвращает новый sentinel."""

    def _render(*args):
        calls.append(args)
        return object()

    return _render


# ─── (a) Характеризационные тесты контракта кэша ─────────────────────────────

class GlossaryActionDelegatePixmapCacheTests(unittest.TestCase):
    def test_cache_hit_returns_same_object_without_rerender(self):
        delegate = _new_glossary_delegate()
        calls = []
        delegate._render_template = _stub_render_template(calls)

        first = delegate._pixmap('gen', False, False, True)
        second = delegate._pixmap('gen', False, False, True)

        self.assertIs(first, second)
        self.assertEqual(len(calls), 1)

    def test_different_state_produces_separate_cache_entry(self):
        delegate = _new_glossary_delegate()
        calls = []
        delegate._render_template = _stub_render_template(calls)

        normal = delegate._pixmap('gen', False, False, True)
        hovered = delegate._pixmap('gen', True, False, True)
        disabled = delegate._pixmap('gen', False, False, False)
        other_kind = delegate._pixmap('delete', False, False, True)

        self.assertIsNot(normal, hovered)
        self.assertIsNot(normal, disabled)
        self.assertIsNot(normal, other_kind)
        self.assertEqual(len(calls), 4)

    def test_invalidate_cache_forces_rerender(self):
        delegate = _new_glossary_delegate()
        calls = []
        delegate._render_template = _stub_render_template(calls)

        first = delegate._pixmap('gen', False, False, True)
        delegate.invalidate_cache()
        second = delegate._pixmap('gen', False, False, True)

        self.assertIsNot(first, second)
        self.assertEqual(len(calls), 2)


class ReorderArrowDelegatePixmapCacheTests(unittest.TestCase):
    def test_cache_hit_returns_same_object_without_rerender(self):
        delegate = _new_reorder_delegate()
        calls = []
        delegate._render_template = _stub_render_template(calls)

        first = delegate._pixmap('up', False, False, True)
        second = delegate._pixmap('up', False, False, True)

        self.assertIs(first, second)
        self.assertEqual(len(calls), 1)

    def test_different_state_produces_separate_cache_entry(self):
        delegate = _new_reorder_delegate()
        calls = []
        delegate._render_template = _stub_render_template(calls)

        up = delegate._pixmap('up', False, False, True)
        down = delegate._pixmap('down', False, False, True)
        pressed = delegate._pixmap('up', False, True, True)
        disabled = delegate._pixmap('up', False, False, False)

        self.assertIsNot(up, down)
        self.assertIsNot(up, pressed)
        self.assertIsNot(up, disabled)
        self.assertEqual(len(calls), 4)

    def test_invalidate_cache_forces_rerender(self):
        delegate = _new_reorder_delegate()
        calls = []
        delegate._render_template = _stub_render_template(calls)

        first = delegate._pixmap('up', False, False, True)
        delegate.invalidate_cache()
        second = delegate._pixmap('up', False, False, True)

        self.assertIsNot(first, second)
        self.assertEqual(len(calls), 2)


# ─── (b) Маршрутизационные тесты: вызовы идут через общий кэш ────────────────

class PixmapCacheRoutingTests(unittest.TestCase):
    def test_glossary_action_delegate_routes_through_shared_cache(self):
        delegate = _new_glossary_delegate()
        sentinel = object()
        with patch(
            "gemini_translator.ui.widgets.delegate_utils.PixmapCache.get_or_render",
            return_value=sentinel,
        ) as mocked:
            result = delegate._pixmap('gen', True, False, True)

        mocked.assert_called_once()
        self.assertIs(result, sentinel)

    def test_reorder_arrow_delegate_routes_through_shared_cache(self):
        delegate = _new_reorder_delegate()
        sentinel = object()
        with patch(
            "gemini_translator.ui.widgets.delegate_utils.PixmapCache.get_or_render",
            return_value=sentinel,
        ) as mocked:
            result = delegate._pixmap('up', True, False, True)

        mocked.assert_called_once()
        self.assertIs(result, sentinel)


if __name__ == "__main__":
    unittest.main()
