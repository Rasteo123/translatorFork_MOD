# -*- coding: utf-8 -*-
"""Общая числовая сортировка QTableWidgetItem через __lt__ (cluster-54).

Раньше в кодовой базе было три независимые реализации __lt__ для колонок
таблиц с числами, каждая под свой формат ячейки:
  - gemini_translator/utils/txt_importer.py: SortableTableWidgetItem —
    текст вида "1,234" / "1 234 симв.".
  - gemini_translator/ui/dialogs/validation.py: NumericTableWidgetItem —
    текст вида "150 | доп.инфо".
  - gemini_translator/ui/dialogs/validation.py: SortableChapterItem —
    вообще не парсит текст ячейки, а извлекает число из внешнего атрибута
    (internal_path) через epub_tools.extract_number_from_path, и сравнивает
    только с элементами своего же типа (иначе — откат на текстовое
    сравнение), потому что чужому key_fn нельзя скармливать чужой объект.

NumericSortItem сводит это к одному классу, параметризованному key_fn
(и опциональным require_same_type для случая SortableChapterItem), вместо
единой эвристики "угадать формат по тексту" — форматы ячеек у бывших копий
разные и смешивать их парсеры нельзя.
"""
from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtWidgets import QTableWidgetItem


def default_numeric_key(item: "QTableWidgetItem") -> float:
    """Ключ по умолчанию: текст ячейки без пробелов/запятых-разделителей
    тысяч и суффикса 'симв.' (формат бывшего SortableTableWidgetItem)."""
    return float(item.text().replace(' ', '').replace(',', '').replace('симв.', ''))


class NumericSortItem(QTableWidgetItem):
    """QTableWidgetItem с числовой сортировкой через настраиваемый key_fn.

    key_fn получает элемент (self или other) и должен вернуть float —
    по умолчанию используется default_numeric_key (парсинг self.text()).
    При ValueError/IndexError из key_fn сортировка откатывается на
    стандартное текстовое сравнение QTableWidgetItem.__lt__.

    require_same_type=True ограничивает числовое сравнение элементами
    класса same_type_as (по умолчанию — type(self)) и сразу откатывается
    на текстовое сравнение для остальных — нужно, когда key_fn читает
    объект целиком (а не только текст) и не рассчитан на посторонние типы
    ячеек в той же колонке. same_type_as стоит передавать явно (классом,
    объявляющим require_same_type=True, например SortableChapterItem), а
    не полагаться на type(self): если у such класса появится подкласс,
    сравнение по type(self) станет асимметричным — sub.__lt__(base)
    и base.__lt__(sub) будут давать разные (текст vs число) результаты.

    _key_fn/_require_same_type/_same_type_as объявлены и как атрибуты
    класса (со значениями по умолчанию), чтобы __lt__ не падал с
    AttributeError на объекте, созданном в обход __init__ (например,
    Qt-клонирование ячейки через прототип) — такой объект просто получит
    поведение по умолчанию (числовая сортировка по тексту без
    require_same_type), а не исключение.
    """

    _key_fn: Callable[["QTableWidgetItem"], float] = staticmethod(default_numeric_key)
    _require_same_type: bool = False
    _same_type_as: Optional[type] = None

    def __init__(
        self,
        *args,
        key_fn: Optional[Callable[["QTableWidgetItem"], float]] = None,
        require_same_type: bool = False,
        same_type_as: Optional[type] = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        if key_fn is not None:
            self._key_fn = key_fn
        self._require_same_type = require_same_type
        self._same_type_as = same_type_as

    def __lt__(self, other):
        if self._require_same_type and not isinstance(other, self._same_type_as or type(self)):
            return super().__lt__(other)
        try:
            return self._key_fn(self) < self._key_fn(other)
        except (ValueError, IndexError):
            return super().__lt__(other)
