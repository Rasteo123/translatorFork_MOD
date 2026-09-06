# -*- coding: utf-8 -*-
"""Канонический хелпер для стабильной идентификации главы.

Раньше эта функция была продублирована дословно (с точностью до имени
метода/докстроки/аннотации типа) в двух тесно связанных диалогах:
``gemini_translator/ui/dialogs/chapter_selection_dialog.py``
(``_chapter_identity``) и ``gemini_translator/ui/dialogs/consistency_checker.py``
(``_chapter_id``). Обе копии использовались для одной и той же цели —
сопоставить главу с сохранённым идентификатором выбора между
``ConsistencyValidatorDialog`` и открываемым им ``ChapterSelectionDialog``.
"""

from __future__ import annotations


def chapter_identity(chapter: dict) -> str:
    """Возвращает стабильный идентификатор главы для выбора и восстановления.

    Предпочитает ``path``, при его отсутствии — ``name``. Не-словарь или
    отсутствие обоих ключей даёт пустую строку.
    """
    if not isinstance(chapter, dict):
        return ""
    return str(chapter.get('path') or chapter.get('name') or "").strip()
