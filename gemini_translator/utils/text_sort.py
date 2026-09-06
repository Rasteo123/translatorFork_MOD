# -*- coding: utf-8 -*-
"""Естественная («человеческая») сортировка строк.

Канонический дом для ``natural_sort_key`` — раньше алгоритм был
продублирован дословно в ``ranobelib/utils.py`` (мёртвый код, без единого
вызывающего) и в ``gemini_reader_v3.py`` (под именем
``_reader_natural_sort_key``, единственное реальное использование —
сортировка файлов .docx внутри ZIP-архива при импорте).
"""

from __future__ import annotations

import re

_SPLIT_RE = re.compile(r"(\d+)")


def natural_sort_key(value: str) -> list:
    """Ключ для естественной сортировки: 'Ch2' < 'Ch10'.

    Строка разбивается на чередующиеся текстовые и числовые токены;
    числовые токены сравниваются как ``int``, текстовые — регистронезависимо.
    """
    return [int(token) if token.isdigit() else token.lower() for token in _SPLIT_RE.split(value)]
