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


@dataclass
class _OverlayEntry:
    dialog: QtWidgets.QDialog
    previous_focus: Optional[QtWidgets.QWidget]
    callback: Optional[Callable[[int], None]]
    #: Размер, который диалог хотел до вставки в карточку: layout стека
    #: перезаписывает size(), поэтому его нужно снять заранее.
    wanted_size: QtCore.QSize = None


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
        # Желаемый размер снимается ДО вставки в стек карточки: layout
        # немедленно перезапишет size() текущим размером карточки.
        if isinstance(dialog, QtWidgets.QMessageBox):
            # Крупные системные иконки (?, !) в карточках выглядят
            # чужеродно — убираем; нативные диалоги их сохраняют.
            dialog.setIcon(QtWidgets.QMessageBox.Icon.NoIcon)
            # sizeHint у QMessageBox ненадёжен: перенос строк занижает
            # ширину (обрезка), а его showEvent-эвристики раздувают окно
            # пустотой. Считаем размер сами.
            wanted_size = self._message_box_size(dialog)
        elif dialog.testAttribute(QtCore.Qt.WidgetAttribute.WA_Resized):
            wanted_size = QtCore.QSize(dialog.size())
        else:
            wanted_size = QtCore.QSize(dialog.sizeHint())
        entry = _OverlayEntry(
            dialog=dialog,
            previous_focus=QtWidgets.QApplication.focusWidget(),
            callback=on_finished,
            wanted_size=wanted_size,
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
        self._reveal_after_morph(dialog)

    # -- сборка карточки ------------------------------------------------

    def _build_card(self) -> None:
        card = QtWidgets.QFrame(self)
        card.setObjectName("overlayCard")
        # Фон карточки — тем же токеном темы, что и QDialog: иначе фон
        # встроенного диалога выделяется прямоугольником другого оттенка.
        try:
            from gemini_translator.ui import theme_manager
            background = theme_manager.color("window_bg")
            border = theme_manager.color("border")
        except Exception:
            background = "palette(window)"
            border = "palette(mid)"
        card.setStyleSheet(
            "#overlayCard {"
            f" background-color: {background};"
            f" border: 1px solid {border};"
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
        stack = QtWidgets.QStackedWidget()
        layout.addWidget(stack)
        self._card = card
        self._card_stack = stack

    def _card_rect_for(self, dialog: QtWidgets.QDialog) -> QtCore.QRect:
        wanted = None
        for entry in self._entries:
            if entry.dialog is dialog:
                wanted = entry.wanted_size
                break
        if wanted is None:
            if isinstance(dialog, QtWidgets.QMessageBox):
                wanted = self._message_box_size(dialog)
            elif dialog.testAttribute(QtCore.Qt.WidgetAttribute.WA_Resized):
                wanted = dialog.size()
            else:
                wanted = dialog.sizeHint()
        return self._card_rect_from(dialog, QtCore.QSize(wanted))

    def _card_rect_from(
        self, dialog: QtWidgets.QDialog, wanted: QtCore.QSize
    ) -> QtCore.QRect:
        minimum = dialog.minimumSizeHint().expandedTo(dialog.minimumSize())
        wanted = wanted.expandedTo(minimum)
        wanted += QtCore.QSize(2 * CARD_PADDING, 2 * CARD_PADDING)
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

    def _reveal_after_morph(self, dialog: QtWidgets.QDialog) -> None:
        """Показывает содержимое после того, как карточка приняла размер.

        Пока карточка морфится, диалог скрыт — тяжёлая первая отрисовка
        (списки глав и т.п.) не дёргает анимацию. Затем фейдом проявляется
        СНИМОК диалога (дёшево на каждом кадре, в отличие от
        QGraphicsOpacityEffect на живом виджете), и только по завершении
        показывается сам диалог — его showEvent-цепочки загрузки стартуют,
        когда никакие анимации уже не идут.
        """
        if self._content_fade_ms <= 0 and self._morph_ms <= 0:
            dialog.show()
            self._focus_dialog(dialog)
            self._refit_card(dialog)
            return
        dialog.hide()

        def _show_dialog() -> None:
            try:
                if self._card_stack is None or (
                    self._card_stack.currentWidget() is not dialog
                ):
                    return
                dialog.show()
                self._focus_dialog(dialog)
                self._refit_card(dialog)
            except RuntimeError:
                pass  # диалог уже уничтожен

        def _reveal() -> None:
            try:
                # Диалог мог закрыться или быть перекрыт следующим.
                if self._card_stack is None or (
                    self._card_stack.currentWidget() is not dialog
                ):
                    return
            except RuntimeError:
                return  # диалог уже уничтожен
            fade_ms = self._content_fade_ms
            if fade_ms <= 0:
                _show_dialog()
                return
            # Скрытые виджеты не размещаются layout-ом — размер задаём сами.
            area = self._card_stack.contentsRect().size()
            if area.isValid() and not area.isEmpty():
                dialog.resize(area)
            try:
                pixmap = dialog.grab()
            except RuntimeError:
                return
            snapshot = QtWidgets.QLabel(self._card)
            snapshot.setAttribute(
                QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
            )
            snapshot.setPixmap(pixmap)
            snapshot.setGeometry(self._card_stack.geometry())
            effect = QtWidgets.QGraphicsOpacityEffect(snapshot)
            effect.setOpacity(0.0)
            snapshot.setGraphicsEffect(effect)
            finished = {"done": False}

            def _finish() -> None:
                if finished["done"]:
                    return
                finished["done"] = True
                _show_dialog()
                snapshot.deleteLater()

            animation = QtCore.QPropertyAnimation(effect, b"opacity", snapshot)
            animation.setDuration(fade_ms)
            animation.setStartValue(0.0)
            animation.setEndValue(1.0)
            animation.finished.connect(_finish)
            # Страховка: диалог показывается, даже если finished не придёт.
            QtCore.QTimer.singleShot(fade_ms + 250, _finish)
            snapshot.show()
            snapshot.raise_()
            animation.start()

        QtCore.QTimer.singleShot(self._morph_ms, _reveal)

    @staticmethod
    def _message_box_size(box: QtWidgets.QMessageBox) -> QtCore.QSize:
        """Детерминированный размер message box-а: 420-640 по ширине,
        высота — через heightForWidth (учитывает перенос строк)."""
        box.ensurePolished()
        layout = box.layout()
        if layout is None:
            return QtCore.QSize(box.sizeHint())
        natural = layout.totalSizeHint()
        width = min(max(natural.width(), 420), 640)
        height = layout.totalHeightForWidth(width)
        if height <= 0:
            height = natural.height()
        return QtCore.QSize(width, height)

    def refit_to(self, dialog: QtWidgets.QDialog, size: QtCore.QSize) -> None:
        """Перецеливает карточку под новый желаемый размер диалога."""
        for entry in self._entries:
            if entry.dialog is dialog:
                entry.wanted_size = QtCore.QSize(size)
                if (
                    self._card is not None
                    and self._card_stack is not None
                    and self._card_stack.currentWidget() is dialog
                ):
                    self._animate_card_to(
                        self._card_rect_from(dialog, entry.wanted_size)
                    )
                break

    def _refit_card(self, dialog: QtWidgets.QDialog) -> None:
        """Подгоняет карточку и диалог друг к другу после показа.

        Обычные «ленивые» диалоги (узнают размер в showEvent) требуют
        повторной подгонки карточки. QMessageBox наоборот: его showEvent
        назначает себе фиксированный размер по своим эвристикам — снимаем
        ограничения, чтобы бокс заполнял карточку ровно, без пустот
        и обрезок.
        """
        if self._card is None:
            return
        if isinstance(dialog, QtWidgets.QMessageBox):
            dialog.setMinimumSize(0, 0)
            dialog.setMaximumSize(16777215, 16777215)
            dialog.updateGeometry()
            return
        desired = getattr(dialog, "desired_size", None)
        if desired is not None:
            # Диалог сам знает свой правильный размер (панель сообщений) —
            # live-замер тут вернул бы layout-присвоенный размер карточки.
            live = QtCore.QSize(desired)
        elif dialog.testAttribute(QtCore.Qt.WidgetAttribute.WA_Resized):
            live = QtCore.QSize(dialog.size())
        else:
            live = QtCore.QSize(dialog.sizeHint())
        for entry in self._entries:
            if entry.dialog is dialog:
                entry.wanted_size = QtCore.QSize(live)
                break
        target = self._card_rect_from(dialog, live)
        if target != self._card.geometry():
            self._animate_card_to(target)

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
            self._animate_card_to(self._card_rect_for(top.dialog))
            self._reveal_after_morph(top.dialog)
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


class _MessageBoxPanel(QtWidgets.QDialog):
    """Лёгкое зеркало QMessageBox для показа в карточке.

    Сырой QMessageBox в карточке неуправляем: он перефиксирует свой размер
    на каждом LayoutRequest по экранным эвристикам (то обрезка, то пустоты),
    а его системные иконки выглядят чужеродно. Панель рисует текст и кнопки
    сама; клики уходят в НАСТОЯЩИЕ кнопки бокса, так что результат exec и
    ``clickedButton()`` у вызывающего кода полностью сохраняются.
    """

    MIN_WIDTH = 420
    MAX_WIDTH = 640
    DETAILS_HEIGHT = 220

    def __init__(self, box: QtWidgets.QMessageBox):
        super().__init__()
        self._box = box
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        text_label = QtWidgets.QLabel(box.text())
        text_label.setWordWrap(True)
        text_label.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        text_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(text_label)

        informative = box.informativeText()
        if informative:
            info_label = QtWidgets.QLabel(informative)
            info_label.setWordWrap(True)
            info_label.setTextInteractionFlags(
                QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
            )
            layout.addWidget(info_label)

        details = box.detailedText()
        if details:
            # Панель показывает подробности сама; очистка detailedText
            # заставляет QMessageBox убрать СВОЮ кнопку «Show Details…»
            # (иначе она задваивалась бы в ряду зеркальных кнопок).
            box.setDetailedText("")
            self._details_view = QtWidgets.QPlainTextEdit(details)
            self._details_view.setReadOnly(True)
            self._details_view.setFixedHeight(self.DETAILS_HEIGHT)
            self._details_view.hide()
            toggle = QtWidgets.QPushButton("Показать подробности…")
            toggle.setCheckable(True)

            def _toggle(checked: bool) -> None:
                self._details_view.setVisible(checked)
                toggle.setText(
                    "Скрыть подробности" if checked else "Показать подробности…"
                )
                self._resize_to_content()
                host = find_overlay_host(self)
                if host is not None:
                    host.refit_to(self, self.desired_size)

            toggle.toggled.connect(_toggle)
            details_row = QtWidgets.QHBoxLayout()
            details_row.addWidget(toggle)
            details_row.addStretch(1)
            layout.addLayout(details_row)
            layout.addWidget(self._details_view)
        else:
            self._details_view = None

        buttons_row = QtWidgets.QHBoxLayout()
        buttons_row.addStretch(1)
        default = box.defaultButton()
        for real_button in box.buttons():
            mirror = QtWidgets.QPushButton(real_button.text())
            mirror.clicked.connect(
                lambda _=False, b=real_button: b.click()
            )
            if default is not None and real_button is default:
                mirror.setDefault(True)
            buttons_row.addWidget(mirror)
        layout.addLayout(buttons_row)

        # Результат настоящего бокса — это и результат панели.
        box.finished.connect(self.done)
        self._resize_to_content()

    def _resize_to_content(self) -> None:
        self.ensurePolished()
        layout = self.layout()
        natural = layout.totalSizeHint()
        width = min(max(natural.width(), self.MIN_WIDTH), self.MAX_WIDTH)
        height = layout.totalHeightForWidth(width)
        if height <= 0:
            height = natural.height()
        self.desired_size = QtCore.QSize(width, height)
        self.resize(width, height)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Финальные шрифты темы применяются при полише на показе —
        # пересчитываем высоту, иначе текст подрезается снизу.
        self._resize_to_content()
        host = find_overlay_host(self)
        if host is not None:
            host.refit_to(self, self.desired_size)

    def reject(self) -> None:
        # Esc уходит в escape-кнопку бокса — тот же путь, что у нативного.
        escape = self._box.escapeButton()
        if escape is not None:
            escape.click()
        else:
            self._box.reject()


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

        # Бокс отцепляется от родителя: фон на время карточки отключён
        # (setEnabled(False)), и унаследованный disabled делал бы кнопки
        # бокса некликабельными — click() из панели молча игнорировался бы.
        box.setParent(None)
        # Показываем зеркальную панель; сам бокс остаётся скрытым носителем
        # кнопок и результата (clickedButton() у вызывающего кода работает).
        host.present(_MessageBoxPanel(box), _done)
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