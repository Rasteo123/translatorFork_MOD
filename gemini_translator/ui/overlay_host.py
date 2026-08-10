# -*- coding: utf-8 -*-
"""In-window modal presentation: dialogs as cards over a dimmed shell.

``OverlayHost`` живёт внутри :class:`MainShell` и показывает обычные
``QDialog`` как карточки без системной рамки поверх затемнённого и
отключённого интерфейса — в духе листов App Store / Claude Desktop —
не создавая отдельных окон ОС.

Точка входа — :func:`present_dialog`: если в окне есть хост, диалог
встраивается в карточку; иначе показывается обычным window-modal
``QDialog.open()``. Результат всегда доставляется колбэком, вложенный
цикл событий ``exec()`` не используется.

Модальность обеспечивает сам хост: на время показа фоновый контент
получает ``setEnabled(False)``, что блокирует и мышь, и Tab-фокус,
и виджетные шорткаты. ``accept``/``reject``/``finished`` у QDialog
работают и когда он встроен как child-виджет.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from PyQt6 import QtCore, QtGui, QtWidgets

#: Затемнение фона под карточками.
DIM_COLOR = QtGui.QColor(0, 0, 0, 110)
#: Карточка не занимает больше этой доли окна.
CARD_MAX_FRACTION = 0.9
#: Внутренний отступ карточки вокруг встроенного диалога.
CARD_PADDING = 12


@dataclass
class _OverlayEntry:
    card: QtWidgets.QFrame
    dialog: QtWidgets.QDialog
    previous_focus: Optional[QtWidgets.QWidget]
    callback: Optional[Callable[[int], None]]


class OverlayHost(QtWidgets.QWidget):
    """Hosts stacked modal cards over the window content.

    Хост закрывает всё окно, пока показан; пустой — скрыт. Сам рисует
    затемнение и глотает события мыши, чтобы фон был недоступен даже
    там, где нет карточки.
    """

    def __init__(self, window: QtWidgets.QWidget, blocked: QtWidgets.QWidget):
        super().__init__(window)
        # ``blocked`` отключается на время показа карточек: это и есть
        # модальность (клики, фокус, шорткаты).
        self._blocked = blocked
        self._entries: list[_OverlayEntry] = []
        window.installEventFilter(self)
        self.hide()

    # -- публичный API --------------------------------------------------

    def present(
        self,
        dialog: QtWidgets.QDialog,
        on_finished: Optional[Callable[[int], None]] = None,
    ) -> None:
        """Показывает ``dialog`` карточкой поверх затемнённого окна.

        Диалог перевоспитывается в child-виджет карточки; его
        ``finished(int)`` закрывает карточку и вызывает ``on_finished``.
        Карточка владеет диалогом и удаляет его после закрытия.
        """
        entry = _OverlayEntry(
            card=self._build_card(dialog),
            dialog=dialog,
            previous_focus=QtWidgets.QApplication.focusWidget(),
            callback=on_finished,
        )
        if self._entries:
            # Нижняя карточка блокируется, пока открыта новая.
            self._entries[-1].card.setEnabled(False)
        self._entries.append(entry)
        dialog.finished.connect(
            lambda result, e=entry: self._dismiss(e, int(result))
        )
        if len(self._entries) == 1:
            self._blocked.setEnabled(False)
            self._sync_geometry()
            self.show()
            self.raise_()
        self._layout_entry(entry)
        entry.card.show()
        dialog.show()
        self._focus_dialog(dialog)

    # -- сборка карточки ------------------------------------------------

    def _build_card(self, dialog: QtWidgets.QDialog) -> QtWidgets.QFrame:
        card = QtWidgets.QFrame(self)
        card.setObjectName("overlayCard")
        card.setStyleSheet(
            "#overlayCard {"
            " background-color: palette(window);"
            " border: 1px solid palette(mid);"
            " border-radius: 12px;"
            "}"
        )
        shadow = QtWidgets.QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(40)
        shadow.setOffset(0, 8)
        shadow.setColor(QtGui.QColor(0, 0, 0, 90))
        card.setGraphicsEffect(shadow)
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(
            CARD_PADDING, CARD_PADDING, CARD_PADDING, CARD_PADDING
        )
        # addWidget перевоспитывает диалог: оконные флаги сбрасываются,
        # он становится обычным child-виджетом.
        layout.addWidget(dialog)
        return card

    def _layout_entry(self, entry: _OverlayEntry) -> None:
        dialog = entry.dialog
        if dialog.testAttribute(QtCore.Qt.WidgetAttribute.WA_Resized):
            wanted = dialog.size()
        else:
            wanted = dialog.sizeHint()
        minimum = dialog.minimumSizeHint().expandedTo(dialog.minimumSize())
        wanted = wanted.expandedTo(minimum)
        padding = QtCore.QSize(2 * CARD_PADDING, 2 * CARD_PADDING)
        wanted += padding
        limit = QtCore.QSize(
            int(self.width() * CARD_MAX_FRACTION),
            int(self.height() * CARD_MAX_FRACTION),
        )
        size = wanted.boundedTo(limit)
        rect = QtCore.QRect(QtCore.QPoint(0, 0), size)
        rect.moveCenter(self.rect().center())
        entry.card.setGeometry(rect)

    def _focus_dialog(self, dialog: QtWidgets.QDialog) -> None:
        focus = dialog.focusWidget()
        if focus is not None:
            focus.setFocus(QtCore.Qt.FocusReason.ActiveWindowFocusReason)
            return
        # Первому фокусируемому потомку — фокус, чтобы Esc/Enter
        # обрабатывались диалогом сразу.
        if not dialog.focusNextChild():
            dialog.setFocus(QtCore.Qt.FocusReason.ActiveWindowFocusReason)

    # -- закрытие -------------------------------------------------------

    def _dismiss(self, entry: _OverlayEntry, result: int) -> None:
        if entry not in self._entries:
            return
        self._entries.remove(entry)
        entry.card.hide()
        entry.card.setParent(None)
        entry.card.deleteLater()  # вместе с встроенным диалогом
        if self._entries:
            top = self._entries[-1]
            top.card.setEnabled(True)
            self._focus_dialog(top.dialog)
        else:
            self.hide()
            self._blocked.setEnabled(True)
            previous = entry.previous_focus
            if previous is not None and previous.isVisible():
                previous.setFocus(QtCore.Qt.FocusReason.ActiveWindowFocusReason)
        if entry.callback is not None:
            entry.callback(result)

    # -- геометрия и модальность ----------------------------------------

    def eventFilter(self, obj, event):
        if obj is self.parentWidget() and event.type() == QtCore.QEvent.Type.Resize:
            if self._entries:
                self._sync_geometry()
                for entry in self._entries:
                    self._layout_entry(entry)
        return super().eventFilter(obj, event)

    def _sync_geometry(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())

    def paintEvent(self, event) -> None:
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), DIM_COLOR)

    # Хост «глотает» взаимодействие вне карточек.
    def mousePressEvent(self, event) -> None:
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:
        event.accept()

    def wheelEvent(self, event) -> None:
        event.accept()


def find_overlay_host(widget: Optional[QtWidgets.QWidget]) -> Optional[OverlayHost]:
    """Возвращает overlay-хост окна, в котором лежит ``widget`` (или None)."""
    if widget is None:
        return None
    window = widget.window()
    host = getattr(window, "overlay_host", None)
    return host if isinstance(host, OverlayHost) else None


def present_dialog(
    context: Optional[QtWidgets.QWidget],
    dialog: QtWidgets.QDialog,
    on_finished: Optional[Callable[[int], None]] = None,
) -> None:
    """Показывает ``dialog`` встроенной карточкой, если окно умеет, иначе
    обычным window-modal диалогом. Никогда не блокирует: результат
    (``QDialog.DialogCode``) приходит в ``on_finished``.
    """
    host = find_overlay_host(context)
    if host is not None:
        host.present(dialog, on_finished)
        return
    if on_finished is not None:
        dialog.finished.connect(lambda result: on_finished(int(result)))
    dialog.finished.connect(dialog.deleteLater)
    dialog.open()
