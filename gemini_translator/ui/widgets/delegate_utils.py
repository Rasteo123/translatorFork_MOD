# -*- coding: utf-8 -*-
"""Общий кэш pixmap-рендеров кнопок для делегатов таблиц (cluster-06).

GlossaryActionDelegate (gemini_translator/ui/dialogs/glossary_dialogs/
action_delegate.py) и ReorderArrowDelegate (gemini_translator/ui/widgets/
chapter_list_widget.py) рисуют кнопки как pixmap, отрендеренный со скрытой
шаблонной кнопки, и кэшируют результат по кортежу состояния (тип кнопки,
hover, pressed, enabled, DPI), чтобы не рендерить кнопку заново на каждый
paint(). Сама пара "словарь get/set" была у обоих делегатов идентичной
посимвольно — она и вынесена сюда как объект-помощник (композиция, не
базовый класс — GlossaryActionDelegate использует его через отложенный
импорт внутри __init__, см. комментарий в action_delegate.py).

Что НЕ вынесено осознанно: сам _render_template (какой виджет рисуется,
QToolButton с иконкой у одного делегата против QPushButton со
стрелкой-текстом у другого) и paint() — они разошлись по дизайну и остаются
в каждом делегате своими.
"""


class PixmapCache:
    """Кэш pixmap'ов по кортежу состояния кнопки.

    Использование: держать экземпляр в делегате (``self._pixmap_cache =
    PixmapCache()``), в ``_pixmap(...)`` звать ``get_or_render(key,
    render_fn)`` вместо собственной пары "словарь.get / словарь[key] = ...",
    в ``invalidate_cache()`` — ``invalidate()``.
    """

    def __init__(self):
        self._pixmaps = {}

    def invalidate(self):
        """Сброс кэша pixmap'ов (нужно при смене темы/палитры/шрифта)."""
        self._pixmaps.clear()

    def get_or_render(self, key, render_fn):
        pixmap = self._pixmaps.get(key)
        if pixmap is None:
            pixmap = render_fn()
            self._pixmaps[key] = pixmap
        return pixmap
