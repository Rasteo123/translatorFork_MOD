# -*- coding: utf-8 -*-
"""Animated main-window resizing driven by shell navigation.

Кроссплатформенно: анимируется только размер окна (Wayland не разрешает
программно перемещать окна), позиция не трогается.
"""
from __future__ import annotations

from PyQt6 import QtCore


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
            size = self._animation.endValue()
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

    # -- анимация -------------------------------------------------------

    def _animate_to(self, target: QtCore.QSize) -> None:
        if self._duration <= 0:
            self._window.resize(target)
            self._stack.set_size_hint_override(None)
            return
        # Минимум стека заморожен с stack_about_to_change, иначе Qt мгновенно
        # распахнёт окно до минимума новой страницы вместо плавного роста;
        # заморозка снимается по завершении анимации.
        self._stack.set_size_hint_override(QtCore.QSize(0, 0))
        animation = QtCore.QPropertyAnimation(self._window, b"size", self)
        animation.setDuration(self._duration)
        animation.setStartValue(self._window.size())
        animation.setEndValue(target)
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
