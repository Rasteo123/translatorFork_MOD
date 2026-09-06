# -*- coding: utf-8 -*-
"""Общий кэш pixmap-рендеров кнопок для делегатов таблиц (cluster-06, pcluster-65).

GlossaryActionDelegate (gemini_translator/ui/dialogs/glossary_dialogs/
action_delegate.py) и ReorderArrowDelegate (gemini_translator/ui/widgets/
chapter_list_widget.py) рисуют кнопки как pixmap, отрендеренный со скрытой
шаблонной кнопки, и кэшируют результат по кортежу состояния (тип кнопки,
hover, pressed, enabled, DPI), чтобы не рендерить кнопку заново на каждый
paint(). Сама пара "словарь get/set" была у обоих делегатов идентичной
посимвольно — она вынесена сюда как объект-помощник (композиция, не
базовый класс — GlossaryActionDelegate использует его через отложенный
импорт внутри __init__, см. комментарий в action_delegate.py).

pcluster-65: обвязка вокруг get_or_render — сборка ключа кэша
(state, hovered, pressed, enabled, round(dpr*100)) и вызов
get_or_render(key, lambda: render_template(...)) — тоже была продублирована
дословно в _pixmap() обоих делегатов. Вынесена как pixmap(): делегат зовёт
её из своего _pixmap(), передавая self._table, состояние и свой
self._render_template. Наследование (общий базовый класс-делегат) не
используется по той же причине, что и выше: базовый класс в
gemini_translator/ui/widgets/ пришлось бы импортировать в action_delegate.py
на уровне модуля (класс должен знать своих предков при определении), а это
воссоздаёт самоцикл импорта ui/widgets/__init__.py -> glossary_widget.py ->
glossary.py -> action_delegate.py, который и заставил делать ленивый импорт
PixmapCache в __init__. Композиция с передачей render_template параметром
достигает той же цели без этого риска.

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

    def pixmap(self, table, state, hovered, pressed, enabled, render_template):
        """Кэшированный pixmap кнопки по состоянию (pcluster-65).

        Строит ключ кэша из ``state`` (тип/направление кнопки), ``hovered``,
        ``pressed``, ``enabled`` и DPI таблицы (``table.devicePixelRatioF()``,
        округлённый до сотых) и при промахе зовёт
        ``render_template(state, hovered, pressed, enabled, dpr)`` — это и
        есть общая обвязка, которая раньше была продублирована посимвольно в
        ``_pixmap()`` GlossaryActionDelegate и ReorderArrowDelegate. Сам
        ``render_template`` (что именно рисуется) остаётся у каждого
        делегата своим — сюда передаётся как обычный колбэк (``self._render_template``
        делегата), без наследования.
        """
        dpr = table.devicePixelRatioF()
        key = (state, hovered, pressed, enabled, round(dpr * 100))
        return self.get_or_render(
            key, lambda: render_template(state, hovered, pressed, enabled, dpr)
        )
