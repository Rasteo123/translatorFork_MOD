# -*- coding: utf-8 -*-
"""In-window modal presentation: dialogs as cards over a dimmed shell.

``OverlayHost`` живёт внутри :class:`MainShell` и показывает обычные
``QDialog`` как карточки без системной рамки поверх затемнённого и
отключённого интерфейса — в духе листов App Store / Claude Desktop —
не создавая отдельных окон ОС.

Карточка одна: вложенные и последовательные диалоги не громоздятся
стопкой и не мигают затемнением, а «морфятся» — содержимое карточки
подменяется, геометрия плавно анимируется под новый диалог, затемнение
появляется и уходит плавным фейдом.

Точка входа — :func:`present_dialog` (колбэк) или :func:`exec_dialog`
(синхронная drop-in замена ``exec()``). Без хоста поведение нативное.

Модальность обеспечивает сам хост: на время показа фоновый контент
получает ``setEnabled(False)``, что блокирует и мышь, и Tab-фокус,
и виджетные шорткаты. ``accept``/``reject``/``finished`` у QDialog
работают и когда он встроен как child-виджет.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from PyQt6 import QtCore, QtGui, QtWidgets

#: Затемнение фона под карточкой (альфа — при полностью проявленном фоне).
DIM_COLOR = QtGui.QColor(0, 0, 0, 110)
#: Карточка не занимает больше этой доли окна.
CARD_MAX_FRACTION = 0.9
#: Внутренний отступ карточки вокруг встроенного диалога.
CARD_PADDING = 12
#: Анимация геометрии карточки (морфинг между диалогами).
MORPH_MS = 180
#: Фейд затемнения при появлении/уходе.
FADE_MS = 150
#: Пауза перед закрытием пустого оверлея: если за это время открывается
#: следующий диалог, затемнение не мигает, а карточка морфится дальше.
CLOSE_LINGER_MS = 120
#: Проявление содержимого карточки после того, как она приняла размер.
CONTENT_FADE_MS = 120

#: Надписи кнопок, означающие «этим диалог можно закрыть»: таким карточкам
#: не нужен собственный крестик.
_CLOSE_BUTTON_WORDS = {
    "закрыть", "отмена", "отменить", "выход", "готово", "принять",
    "пропустить", "ok", "оk", "cancel", "close", "done", "apply",
}


def _dialog_offers_close(dialog: QtWidgets.QDialog) -> bool:
    """Есть ли у диалога собственный способ закрыться (кнопки)."""
    if dialog.findChild(QtWidgets.QDialogButtonBox) is not None:
        return True
    for button in dialog.findChildren(QtWidgets.QAbstractButton):
        text = button.text().replace("&", "").strip().strip("…. ").lower()
        if not text:
            continue
        # Отсекаем эмодзи-префиксы вида «🚀 Собрать EPUB».
        words = {w for w in text.split() if w.isalpha()}
        if words & _CLOSE_BUTTON_WORDS or text in _CLOSE_BUTTON_WORDS:
            return True
    return False


@dataclass
class _OverlayEntry:
    dialog: QtWidgets.QDialog
    previous_focus: Optional[QtWidgets.QWidget]
    callback: Optional[Callable[[int], None]]


class OverlayHost(QtWidgets.QWidget):
    """Hosts modal dialogs in a single morphing card over the window."""

    def __init__(self, window: QtWidgets.QWidget, blocked: QtWidgets.QWidget):
        super().__init__(window)
        # ``blocked`` отключается на время показа карточки: это и есть
        # модальность (клики, фокус, шорткаты).
        self._blocked = blocked
        self._entries: list[_OverlayEntry] = []
        self._card: Optional[QtWidgets.QFrame] = None
        self._card_stack: Optional[QtWidgets.QStackedWidget] = None
        self._dim = 0.0
        self._dim_animation: Optional[QtCore.QVariantAnimation] = None
        self._geometry_animation: Optional[QtCore.QPropertyAnimation] = None
        self._close_timer: Optional[QtCore.QTimer] = None
        self._restore_focus_to: Optional[QtWidgets.QWidget] = None
        self._morph_ms = MORPH_MS
        self._fade_ms = FADE_MS
        self._linger_ms = CLOSE_LINGER_MS
        self._content_fade_ms = CONTENT_FADE_MS
        self._card_header: Optional[QtWidgets.QWidget] = None
        window.installEventFilter(self)
        self.hide()

    def set_animation_durations(
        self,
        morph_ms: int | None = None,
        fade_ms: int | None = None,
        linger_ms: int | None = None,
        content_fade_ms: int | None = None,
    ) -> None:
        if morph_ms is not None:
            self._morph_ms = max(0, int(morph_ms))
        if fade_ms is not None:
            self._fade_ms = max(0, int(fade_ms))
        if linger_ms is not None:
            self._linger_ms = max(0, int(linger_ms))
        if content_fade_ms is not None:
            self._content_fade_ms = max(0, int(content_fade_ms))

    # -- публичный API --------------------------------------------------

    def present(
        self,
        dialog: QtWidgets.QDialog,
        on_finished: Optional[Callable[[int], None]] = None,
    ) -> None:
        """Показывает ``dialog`` в карточке поверх затемнённого окна.

        Если карточка уже показана (вложенный или следующий по цепочке
        диалог), её содержимое подменяется, а геометрия плавно анимируется
        под новый диалог. Карточка владеет диалогом и удаляет его после
        закрытия; ``finished(int)`` доставляет результат в ``on_finished``.
        """
        self._cancel_pending_close()
        entry = _OverlayEntry(
            dialog=dialog,
            previous_focus=QtWidgets.QApplication.focusWidget(),
            callback=on_finished,
        )
        appearing = not self.isVisible()
        if self._card is None:
            self._build_card()
        card_was_hidden = not self._card.isVisible()
        self._entries.append(entry)
        dialog.finished.connect(
            lambda result, e=entry: self._dismiss(e, int(result))
        )
        self._card_stack.addWidget(dialog)
        self._card_stack.setCurrentWidget(dialog)
        if appearing:
            self._blocked.setEnabled(False)
            self._sync_geometry()
            self.show()
            self.raise_()
            self._fade_dim_to(1.0)
        self._sync_card_header(dialog)
        target = self._card_rect_for(dialog)
        if appearing or card_was_hidden:
            # Первое появление: лёгкий «вырост» карточки из чуть меньшего
            # прямоугольника вместо мгновенного появления.
            start = QtCore.QRect(target)
            shrink_w = max(1, int(target.width() * 0.04))
            shrink_h = max(1, int(target.height() * 0.04))
            start.adjust(shrink_w, shrink_h, -shrink_w, -shrink_h)
            self._card.setGeometry(start)
        self._card.show()
        self._animate_card_to(target)
        dialog.show()
        self._prepare_content_fade(dialog)
        self._focus_dialog(dialog)

    # -- сборка карточки ------------------------------------------------

    def _build_card(self) -> None:
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
        layout.setSpacing(6)
        # Крестик для диалогов без собственных кнопок закрытия — иначе
        # карточку без рамки было бы нечем закрыть (кроме Esc).
        header = QtWidgets.QWidget()
        header_layout = QtWidgets.QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.addStretch(1)
        close_button = QtWidgets.QToolButton(header)
        close_button.setObjectName("overlayCardClose")
        close_button.setText("✕")
        close_button.setAutoRaise(True)
        close_button.setToolTip("Закрыть (Esc)")
        close_button.clicked.connect(self._reject_top_dialog)
        header_layout.addWidget(close_button)
        layout.addWidget(header)
        header.hide()
        stack = QtWidgets.QStackedWidget()
        layout.addWidget(stack)
        self._card = card
        self._card_stack = stack
        self._card_header = header

    def _reject_top_dialog(self) -> None:
        if self._entries:
            self._entries[-1].dialog.reject()

    def _sync_card_header(self, dialog: QtWidgets.QDialog) -> None:
        if self._card_header is not None:
            self._card_header.setVisible(not _dialog_offers_close(dialog))

    def _card_rect_for(self, dialog: QtWidgets.QDialog) -> QtCore.QRect:
        if dialog.testAttribute(QtCore.Qt.WidgetAttribute.WA_Resized):
            wanted = dialog.size()
        else:
            wanted = dialog.sizeHint()
        minimum = dialog.minimumSizeHint().expandedTo(dialog.minimumSize())
        wanted = wanted.expandedTo(minimum)
        wanted += QtCore.QSize(2 * CARD_PADDING, 2 * CARD_PADDING)
        if not _dialog_offers_close(dialog) and self._card_header is not None:
            spacing = self._card.layout().spacing() if self._card else 6
            wanted.setHeight(
                wanted.height() + self._card_header.sizeHint().height() + spacing
            )
        limit = QtCore.QSize(
            int(self.width() * CARD_MAX_FRACTION),
            int(self.height() * CARD_MAX_FRACTION),
        )
        size = wanted.boundedTo(limit)
        rect = QtCore.QRect(QtCore.QPoint(0, 0), size)
        rect.moveCenter(self.rect().center())
        return rect

    def _animate_card_to(self, rect: QtCore.QRect) -> None:
        if self._geometry_animation is not None:
            self._geometry_animation.stop()
            self._geometry_animation.deleteLater()
            self._geometry_animation = None
        if self._morph_ms <= 0 or not self.isVisible():
            self._card.setGeometry(rect)
            return
        animation = QtCore.QPropertyAnimation(self._card, b"geometry", self)
        animation.setDuration(self._morph_ms)
        animation.setStartValue(self._card.geometry())
        animation.setEndValue(rect)
        animation.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
        self._geometry_animation = animation
        animation.start()

    def _prepare_content_fade(self, dialog: QtWidgets.QDialog) -> None:
        """Содержимое проявляется после того, как карточка приняла размер."""
        if self._content_fade_ms <= 0:
            return
        effect = QtWidgets.QGraphicsOpacityEffect(dialog)
        effect.setOpacity(0.0)
        dialog.setGraphicsEffect(effect)

        def _start() -> None:
            try:
                if not dialog.isVisible() or dialog.graphicsEffect() is not effect:
                    return
            except RuntimeError:
                return  # диалог уже уничтожен
            animation = QtCore.QPropertyAnimation(effect, b"opacity", dialog)
            animation.setDuration(self._content_fade_ms)
            animation.setStartValue(0.0)
            animation.setEndValue(1.0)

            def _cleanup() -> None:
                try:
                    if dialog.graphicsEffect() is effect:
                        dialog.setGraphicsEffect(None)
                except RuntimeError:
                    pass
            animation.finished.connect(_cleanup)
            animation.start()

        QtCore.QTimer.singleShot(self._morph_ms, _start)

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
        dialog = entry.dialog
        if self._card_stack is not None:
            self._card_stack.removeWidget(dialog)
        dialog.hide()
        dialog.setParent(None)
        dialog.deleteLater()
        if self._entries:
            top = self._entries[-1]
            self._card_stack.setCurrentWidget(top.dialog)
            self._sync_card_header(top.dialog)
            self._animate_card_to(self._card_rect_for(top.dialog))
            self._prepare_content_fade(top.dialog)
            self._focus_dialog(top.dialog)
        else:
            self._begin_close(entry.previous_focus)
        if entry.callback is not None:
            entry.callback(result)

    def _begin_close(self, focus_target: Optional[QtWidgets.QWidget]) -> None:
        """Прячет карточку и, чуть подождав, гасит затемнение.

        Пауза даёт следующему диалогу цепочки открыться без мигания
        оверлея: present() внутри окна ожидания отменяет закрытие.
        """
        self._restore_focus_to = focus_target
        if self._card is not None:
            self._card.hide()
        if self._close_timer is None:
            timer = QtCore.QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._fade_out)
            self._close_timer = timer
        self._close_timer.start(self._linger_ms)

    def _cancel_pending_close(self) -> None:
        if self._close_timer is not None:
            self._close_timer.stop()
        if self.isVisible() and self._dim < 1.0:
            # Отменённый фейд-аут: затемнение возвращается.
            self._fade_dim_to(1.0)

    def _fade_out(self) -> None:
        if self._entries:
            return
        self._fade_dim_to(0.0, on_done=self._finish_close)

    def _finish_close(self) -> None:
        if self._entries:
            return
        self.hide()
        self._blocked.setEnabled(True)
        if self._card is not None:
            self._card.deleteLater()
            self._card = None
            self._card_stack = None
        target = self._restore_focus_to
        self._restore_focus_to = None
        if target is not None:
            try:
                if target.isVisible():
                    target.setFocus(
                        QtCore.Qt.FocusReason.ActiveWindowFocusReason
                    )
            except RuntimeError:
                pass  # виджет уже уничтожен

    # -- затемнение -----------------------------------------------------

    def _fade_dim_to(
        self, value: float, on_done: Optional[Callable[[], None]] = None
    ) -> None:
        if self._dim_animation is not None:
            self._dim_animation.stop()
            self._dim_animation.deleteLater()
            self._dim_animation = None
        if self._fade_ms <= 0 or not self.isVisible():
            self._dim = value
            self.update()
            if on_done is not None:
                on_done()
            return
        animation = QtCore.QVariantAnimation(self)
        animation.setDuration(self._fade_ms)
        animation.setStartValue(float(self._dim))
        animation.setEndValue(float(value))
        animation.valueChanged.connect(self._set_dim)
        if on_done is not None:
            animation.finished.connect(on_done)
        self._dim_animation = animation
        animation.start()

    def _set_dim(self, value) -> None:
        self._dim = float(value)
        self.update()

    # -- геометрия и модальность ----------------------------------------

    def eventFilter(self, obj, event):
        if obj is self.parentWidget() and event.type() == QtCore.QEvent.Type.Resize:
            if self.isVisible():
                self._sync_geometry()
                if self._entries and self._card is not None:
                    self._card.setGeometry(
                        self._card_rect_for(self._entries[-1].dialog)
                    )
        return super().eventFilter(obj, event)

    def _sync_geometry(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())

    def paintEvent(self, event) -> None:
        if self._dim <= 0.0:
            return
        color = QtGui.QColor(DIM_COLOR)
        color.setAlpha(int(DIM_COLOR.alpha() * self._dim))
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), color)

    # Хост «глотает» взаимодействие вне карточки.
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
    # Контекст бывает и не-виджетом (тестовые harness-объекты, None) —
    # тогда хоста нет и работает нативный fallback.
    if not isinstance(widget, QtWidgets.QWidget):
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
    host = _usable_host(context)
    if host is not None:
        host.present(dialog, on_finished)
        return
    if on_finished is not None:
        dialog.finished.connect(lambda result: on_finished(int(result)))
    dialog.finished.connect(dialog.deleteLater)
    dialog.open()


def _usable_host(context: Optional[QtWidgets.QWidget]) -> Optional[OverlayHost]:
    """Хост, в котором карточку реально видно (окно показано на экране)."""
    host = find_overlay_host(context)
    if host is None:
        return None
    window = host.parentWidget()
    if window is None or not window.isVisible():
        return None
    return host


def _message_box_host(
    parent: Optional[QtWidgets.QWidget],
) -> Optional[OverlayHost]:
    """Хост для message box-ов: окно родителя, иначе активное окно.

    Боксы нередко создаются виджетами, ещё не вставленными в шелл
    (например, при конструировании страницы) — тогда родительская цепочка
    не доходит до окна с хостом, хотя пользователь смотрит на шелл.
    """
    host = _usable_host(parent)
    if host is not None:
        return host
    return _usable_host(QtWidgets.QApplication.activeWindow())


def exec_dialog(
    context: Optional[QtWidgets.QWidget], dialog: QtWidgets.QDialog
) -> int:
    """Синхронная drop-in замена ``dialog.exec()`` с показом в overlay.

    Возвращает тот же код результата, что и ``exec()``; без хоста падает
    обратно на нативный ``dialog.exec()``. Крутит вложенный QEventLoop —
    семантика блокировки та же, что у нативного ``exec()``, поэтому
    существующий последовательный код точек вызова не меняется.
    """
    host = _usable_host(context)
    if host is None:
        return dialog.exec()
    loop = QtCore.QEventLoop()
    codes: list[int] = []

    def _done(code: int) -> None:
        codes.append(code)
        loop.quit()

    host.present(dialog, _done)
    loop.exec()
    return codes[0] if codes else int(QtWidgets.QDialog.DialogCode.Rejected)


_message_boxes_patched = False


def install_message_box_overlay() -> None:
    """Глобально направляет QMessageBox в overlay-карточки.

    Патчит ``QMessageBox.exec`` (ловит и все ``msg_box.exec()`` по месту,
    и storm-protected ``critical`` из os_patch, который строит бокс и
    зовёт ``exec``) и статики ``information``/``warning``/``question``.
    ``critical`` не трогаем — им владеет os_patch. Без хоста (нет шелла,
    окно скрыто) поведение остаётся нативным. Идемпотентно.
    """
    global _message_boxes_patched
    if _message_boxes_patched:
        return
    _message_boxes_patched = True

    QMessageBox = QtWidgets.QMessageBox
    native_exec = QMessageBox.exec

    def overlay_exec(box: QMessageBox) -> int:
        host = _message_box_host(box.parentWidget())
        if host is None or box.isVisible():
            return native_exec(box)
        loop = QtCore.QEventLoop()
        codes: list[int] = []

        def _done(code: int) -> None:
            codes.append(code)
            loop.quit()

        host.present(box, _done)
        loop.exec()
        # finished(int) несёт тот же код, что вернул бы нативный exec():
        # QMessageBox зовёт done(execReturnCode(кнопка)) и для стандартных,
        # и для кастомных кнопок.
        return codes[0] if codes else int(QMessageBox.StandardButton.NoButton)

    QMessageBox.exec = overlay_exec

    def _make_static(icon, native, default_buttons):
        def runner(
            parent,
            title,
            text,
            buttons=default_buttons,
            defaultButton=QMessageBox.StandardButton.NoButton,
        ):
            if _message_box_host(parent) is None:
                return native(parent, title, text, buttons, defaultButton)
            box = QMessageBox(icon, title, text, buttons, parent)
            box.setDefaultButton(defaultButton)
            return QMessageBox.StandardButton(box.exec())

        return runner

    QMessageBox.information = _make_static(
        QMessageBox.Icon.Information,
        QMessageBox.information,
        QMessageBox.StandardButton.Ok,
    )
    QMessageBox.warning = _make_static(
        QMessageBox.Icon.Warning,
        QMessageBox.warning,
        QMessageBox.StandardButton.Ok,
    )
    QMessageBox.question = _make_static(
        QMessageBox.Icon.Question,
        QMessageBox.question,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    )