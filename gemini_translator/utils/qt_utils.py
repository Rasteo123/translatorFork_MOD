# -*- coding: utf-8 -*-
"""Общие мелкие хелперы для работы с Qt/PyQt6.

Канонический дом для проверок вида «жив ли ещё C++-объект Qt» — раньше
эта проверка была продублирована дословно в нескольких модулях
(``gemini_translator/ui/pages/qidian_creator_page.py`` и
``ranobelib/main_window.py``).
"""

from __future__ import annotations

from PyQt6 import sip


def qt_object_is_alive(obj) -> bool:
    """Вернуть True, если ``obj`` не None и его C++-объект Qt ещё не удалён.

    ``sip.isdeleted`` бросает TypeError для объектов, которые вообще не
    являются обёрнутыми sip-объектами (например, обычный Python-объект) —
    в этом случае считаем объект «живым», как и обе исходные копии.
    """
    if obj is None:
        return False
    try:
        return not sip.isdeleted(obj)
    except TypeError:
        return True
