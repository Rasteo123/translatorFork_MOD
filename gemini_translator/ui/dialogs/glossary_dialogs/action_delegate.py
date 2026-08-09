# -*- coding: utf-8 -*-
"""Делегат кнопок действий таблицы глоссария (по образцу ReorderArrowDelegate).

Раньше на каждую строку создавались два контейнера QWidget с QHBoxLayout и
2-3 QToolButton через setCellWidget — ~250мс и ~7500 живых Qt-объектов на
1500 строк. Делегат рисует кнопки pixmap'ами, отрендеренными со скрытой
шаблонной QToolButton (QSS-состояния инъецируются в QStyleOption), клики
hit-тестит страница через eventFilter на viewport по button_rects().

Состав кнопок строки хранится в данных item'а колонки (ACTIONS_ROLE) —
кортеж из kind-строк: 'gen', 'version', 'version_active', 'delete'.
"""

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QStyle

from ....ui import theme_manager

ACTIONS_ROLE = Qt.ItemDataRole.UserRole + 41

_KIND_ICONS = {
    'gen': QStyle.StandardPixmap.SP_FileDialogContentsView,
    'version': QStyle.StandardPixmap.SP_BrowserReload,
    'version_active': QStyle.StandardPixmap.SP_BrowserReload,
    'delete': QStyle.StandardPixmap.SP_TrashIcon,
}


class _ActionTemplateButton(QtWidgets.QToolButton):
    """Скрытая шаблонная кнопка: нужные биты состояния (hover/pressed)
    инъецируются в QStyleOption при отрисовке — у скрытого виджета честный
    State_MouseOver невозможен."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._add_state = QtWidgets.QStyle.StateFlag.State_None

    def set_forced_states(self, add_state):
        self._add_state = add_state

    def paintEvent(self, _event):
        painter = QtWidgets.QStylePainter(self)
        option = QtWidgets.QStyleOptionToolButton()
        self.initStyleOption(option)
        option.state |= self._add_state
        painter.drawComplexControl(QtWidgets.QStyle.ComplexControl.CC_ToolButton, option)


class GlossaryActionDelegate(QtWidgets.QStyledItemDelegate):
    SPACING = 4

    def __init__(self, table, button_size: QtCore.QSize, icon_size: QtCore.QSize,
                 tooltip_provider=None, parent=None):
        super().__init__(parent)
        self._table = table
        self._button_size = QtCore.QSize(button_size)
        self._icon_size = QtCore.QSize(icon_size)
        self._tooltip_provider = tooltip_provider
        self._template = _ActionTemplateButton(table)
        self._template.setObjectName("glossaryActionButton")
        self._template.setAutoRaise(True)
        self._template.setFixedSize(self._button_size)
        self._template.setIconSize(self._icon_size)
        self._template.hide()
        self._pixmaps = {}
        self._icons = {}
        self.hovered = (-1, -1, -1)   # (row, column, button_index)
        self.pressed = (-1, -1, -1)

    # --- Геометрия -------------------------------------------------------

    def button_rects(self, cell_rect, count):
        """Прямоугольники кнопок: по центру ячейки, зазор SPACING — та же
        геометрия, что была у QHBoxLayout(margins 4,2, spacing 4, center)."""
        if count <= 0:
            return []
        width = self._button_size.width()
        height = self._button_size.height()
        total = width * count + self.SPACING * (count - 1)
        x = cell_rect.x() + max(0, (cell_rect.width() - total) // 2)
        y = cell_rect.y() + max(0, (cell_rect.height() - height) // 2)
        return [
            QtCore.QRect(x + i * (width + self.SPACING), y, width, height)
            for i in range(count)
        ]

    @staticmethod
    def actions_for_index(index):
        actions = index.data(ACTIONS_ROLE)
        return tuple(actions) if isinstance(actions, (list, tuple)) else ()

    # --- Отрисовка -------------------------------------------------------

    def invalidate_cache(self):
        """Сброс кэша pixmap'ов и стилей (смена темы/палитры/шрифта)."""
        self._pixmaps.clear()
        self._icons.clear()

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        actions = self.actions_for_index(index)
        if not actions:
            return
        row, column = index.row(), index.column()
        enabled = bool(option.state & QtWidgets.QStyle.StateFlag.State_Enabled)
        for i, (kind, rect) in enumerate(zip(actions, self.button_rects(option.rect, len(actions)))):
            hovered = enabled and self.hovered == (row, column, i)
            pressed = enabled and self.pressed == (row, column, i)
            painter.drawPixmap(rect.topLeft(), self._pixmap(kind, hovered, pressed, enabled))

    def _pixmap(self, kind, hovered, pressed, enabled):
        dpr = self._table.devicePixelRatioF()
        key = (kind, hovered, pressed, enabled, round(dpr * 100))
        pixmap = self._pixmaps.get(key)
        if pixmap is None:
            pixmap = self._render_template(kind, hovered, pressed, enabled, dpr)
            self._pixmaps[key] = pixmap
        return pixmap

    def _icon(self, kind):
        icon = self._icons.get(kind)
        if icon is None:
            icon = self._icons[kind] = self._table.style().standardIcon(_KIND_ICONS[kind])
        return icon

    def _stylesheet(self, kind):
        extra = ""
        if kind == 'version_active':
            extra = (
                f"background-color: {theme_manager.color('info')}; "
                f"color: {theme_manager.color('accent_text')}; "
                "font-weight: bold;"
            )
        w, h = self._button_size.width(), self._button_size.height()
        return (
            "QToolButton {"
            "background: transparent;"
            "border: 1px solid transparent;"
            "border-radius: 6px;"
            "padding: 0px;"
            f"min-width: {w}px; max-width: {w}px;"
            f"min-height: {h}px; max-height: {h}px;"
            f"{extra}"
            "}"
            "QToolButton:hover {"
            f"background-color: {theme_manager.color('accent_hover_soft')};"
            f"border-color: {theme_manager.color('border_strong')};"
            "}"
        )

    def _render_template(self, kind, hovered, pressed, enabled, dpr):
        btn = self._template
        btn.setIcon(self._icon(kind))
        btn.setStyleSheet(self._stylesheet(kind))
        btn.setEnabled(enabled)
        add_state = QtWidgets.QStyle.StateFlag.State_None
        if hovered:
            add_state |= QtWidgets.QStyle.StateFlag.State_MouseOver
        if pressed:
            add_state |= (QtWidgets.QStyle.StateFlag.State_MouseOver
                          | QtWidgets.QStyle.StateFlag.State_Sunken)
        btn.set_forced_states(add_state)
        btn.ensurePolished()
        pixmap = QtGui.QPixmap(round(btn.width() * dpr), round(btn.height() * dpr))
        pixmap.setDevicePixelRatio(dpr)
        pixmap.fill(Qt.GlobalColor.transparent)
        btn.render(pixmap, flags=QtWidgets.QWidget.RenderFlag.DrawChildren)
        return pixmap

    # --- Тултипы ---------------------------------------------------------

    def helpEvent(self, event, view, option, index):
        actions = self.actions_for_index(index)
        if actions and self._tooltip_provider is not None:
            pos = event.pos()
            for i, rect in enumerate(self.button_rects(option.rect, len(actions))):
                if rect.contains(pos):
                    text = self._tooltip_provider(index.row(), actions[i])
                    if text:
                        QtWidgets.QToolTip.showText(event.globalPos(), text, view)
                        return True
            QtWidgets.QToolTip.hideText()
        return super().helpEvent(event, view, option, index)
