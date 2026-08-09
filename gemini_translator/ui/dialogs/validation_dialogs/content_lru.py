# -*- coding: utf-8 -*-
"""Ограниченный кэш содержимого глав для окна валидации.

Dict-подобный интерфейс (in / [] / clear), чтобы встать на место прежних
безразмерных словарей original_content_cache / validated_content_cache.
Хранит последние max_entries глав; при переполнении вытесняет давно не
использованные. Ёмкость в единицах глав: ~24 главы × ~100КБ ≈ пара мегабайт
вместо полной книги в двух копиях.
"""

from collections import OrderedDict


class ContentLru:
    def __init__(self, max_entries: int = 24):
        self._max_entries = max(1, int(max_entries))
        self._entries = OrderedDict()

    def __contains__(self, key) -> bool:
        if key in self._entries:
            self._entries.move_to_end(key)
            return True
        return False

    def __getitem__(self, key):
        value = self._entries[key]
        self._entries.move_to_end(key)
        return value

    def __setitem__(self, key, value) -> None:
        self._entries[key] = value
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def __len__(self) -> int:
        return len(self._entries)

    def pop(self, key, default=None):
        return self._entries.pop(key, default)

    def clear(self) -> None:
        self._entries.clear()
