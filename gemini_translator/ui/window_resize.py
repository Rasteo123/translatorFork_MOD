# -*- coding: utf-8 -*-
"""Animated main-window resizing driven by shell navigation.

Кроссплатформенно: анимируется только размер окна (Wayland не разрешает
программно перемещать окна), позиция не трогается.
"""
from __future__ import annotations

from PyQt6 import QtCore, QtGui


class WindowResizeController(QtCore.QObject):
    """Smoothly resizes the shell window to each page's preferred size.

    Приоритет целевого размера:

    1. размер, который окно имело на этой странице в прошлый раз в текущей
       сессии (снимок делается в момент ухода со страницы, поэтому ручные
       изменения размера пользователем «прилипают» к странице);
    2. ``preferred_window_size`` страницы;
    3. без изменений (окно лишь дорастает до минимума страницы).

    Цель ограничивается минимумом страницы снизу и доступной областью
    экрана сверху. Ничего не делает, пока окно развёрнуто, свёрнуто,
    в полноэкранном режиме или ещё не показано.
    """

    ANIMATION_MS = 200

    def __init__(self, window, navigation, stack, duration_ms: int | None = None):
        super().__init__(window)
        self._window = window
        self._navigation = navigation
        self._stack = stack
        self._duration = self.ANIMATION_MS if duration_ms is None else duration_ms
        self._remembered: dict[str, QtCore.QSize] = {}
        self._animation: QtCore.QPropertyAnimation | None = None
        # Wayland не разрешает программно двигать окна — там анимируется
        # только размер (якорь — левый верхний угол), на остальных
        # платформах окно растёт из своего центра.
        platform = QtGui.QGuiApplication.platformName().lower()
        self._can_move = "wayland" not in platform
        navigation.stack_about_to_change.connect(self._snapshot_current_page_size)
        navigation.stack_changed.connect(self._on_stack_changed)

    def set_duration(self, duration_ms: int) -> None:
        self._duration = max(0, int(duration_ms))

    # -- снимок размера уходящей страницы ------------------------------

    def _snapshot_current_page_size(self) -> None:
        # Заморозить минимум до смены страницы: иначе Qt синхронно
        # распахнёт окно под минимум новой страницы ещё внутри push(),
        # и анимировать будет уже нечего.
        self._stack.set_size_hint_override(QtCore.QSize(0, 0))
        page = self._navigation.current_page()
        if page is None or not self._window.isVisible() or not self._window_is_normal():
            return
        if self._animation is not None:
            # Быстрый переход посреди анимации: за страницей закрепляется
            # размер, в котором она бы устоялась, а не промежуточный кадр.
            end = self._animation.endValue()
            size = end.size() if isinstance(end, QtCore.QRect) else end
        else:
            size = self._window.size()
        self._remembered[self._page_key(page)] = QtCore.QSize(size)

    # -- реакция на навигацию ------------------------------------------

    def _on_stack_changed(self) -> None:
        self._stop_animation()
        page = self._navigation.current_page()
        if page is None or not self._window.isVisible() or not self._window_is_normal():
            self._stack.set_size_hint_override(None)
            return
        target = self._target_size(page)
        if target is None:
            self._stack.set_size_hint_override(None)
            return
        self._animate_to(target)

    def _window_is_normal(self) -> bool:
        w = self._window
        return not (w.isMaximized() or w.isFullScreen() or w.isMinimized())

    @staticmethod
    def _page_key(page) -> str:
        return type(page).__qualname__

    def _window_minimum(self) -> QtCore.QSize:
        """Минимум окна для текущей страницы, не полагаясь на кэш Qt.

        ``window.minimumSizeHint()`` в момент ``stack_changed`` ещё отдаёт
        значение до перехода, поэтому минимум собирается вручную: реальный
        хинт стека плюс текущая дельта на хром окна (nav bar, поля).
        """
        stack_min = self._stack.real_minimum_size_hint()
        delta_w = max(0, self._window.width() - self._stack.width())
        delta_h = max(0, self._window.height() - self._stack.height())
        return QtCore.QSize(stack_min.width() + delta_w, stack_min.height() + delta_h)

    def _target_size(self, page) -> QtCore.QSize | None:
        target = self._remembered.get(self._page_key(page))
        if target is None:
            preferred = getattr(page, "preferred_window_size", None)
            if preferred:
                target = QtCore.QSize(*preferred)
        minimum = self._window_minimum()
        if target is None:
            current = self._window.size()
            if (
                current.width() >= minimum.width()
                and current.height() >= minimum.height()
            ):
                return None
            target = QtCore.QSize(current)
        target = target.expandedTo(minimum)
        screen = self._window.screen()
        if screen is not None:
            target = target.boundedTo(screen.availableGeometry().size())
        if target == self._window.size():
            return None
        return QtCore.QSize(target)

    def _target_rect(self, size: QtCore.QSize) -> QtCore.QRect:
        """Геометрия окна для нового размера: рост из центра, в экране.

        Центр остаётся на месте; если край упирается в границу экрана,
        окно прижимается к ней, и центр смещается соответственно.
        """
        current = self._window.geometry()
        rect = QtCore.QRect(QtCore.QPoint(0, 0), size)
        rect.moveCenter(current.center())
        screen = self._window.screen()
        if screen is not None:
            avail = screen.availableGeometry()
            # Рамка окна (заголовок) тоже должна остаться на экране:
            # ужимаем доступную область на поля рамки вокруг клиентской.
            frame = self._window.frameGeometry()
            avail = avail.adjusted(
                current.left() - frame.left(),
                current.top() - frame.top(),
                current.right() - frame.right(),
                current.bottom() - frame.bottom(),
            )
            if rect.right() > avail.right():
                rect.moveRight(avail.right())
            if rect.bottom() > avail.bottom():
                rect.moveBottom(avail.bottom())
            if rect.left() < avail.left():
                rect.moveLeft(avail.left())
            if rect.top() < avail.top():
                rect.moveTop(avail.top())
        return rect

    # -- анимация -------------------------------------------------------

    def _animate_to(self, target: QtCore.QSize) -> None:
        if not self._can_move:
            self._animate_property(b"size", self._window.size(), target)
            return
        rect = self._target_rect(target)
        self._animate_property(b"geometry", self._window.geometry(), rect)

    def _animate_property(self, prop: bytes, start, end) -> None:
        if self._duration <= 0:
            if prop == b"geometry":
                self._window.setGeometry(end)
            else:
                self._window.resize(end)
            self._stack.set_size_hint_override(None)
            return
        # Минимум стека заморожен с stack_about_to_change, иначе Qt мгновенно
        # распахнёт окно до минимума новой страницы вместо плавного роста;
        # заморозка снимается по завершении анимации.
        self._stack.set_size_hint_override(QtCore.QSize(0, 0))
        animation = QtCore.QPropertyAnimation(self._window, prop, self)
        animation.setDuration(self._duration)
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
        animation.finished.connect(self._on_animation_finished)
        self._animation = animation
        animation.start()

    def _on_animation_finished(self) -> None:
        self._animation = None
        self._stack.set_size_hint_override(None)

    def _stop_animation(self) -> None:
        animation = self._animation
        self._animation = None
        if animation is not None:
            animation.finished.disconnect(self._on_animation_finished)
            animation.stop()
            animation.deleteLater()
