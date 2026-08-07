"""Отложенный показ модальных "Пожалуйста, подождите" диалогов.

Быстрые фоновые операции закрываются раньше, чем пользователь успевает
прочитать сообщение — маленькое окно лишь мигает и раздражает. Показываем
диалог только если операция действительно затянулась.
"""
from PyQt6 import QtCore


DEFAULT_SHOW_DELAY_MS = 400


def show_when_slow(dialog, delay_ms: int = DEFAULT_SHOW_DELAY_MS) -> QtCore.QTimer:
    """Показывает dialog через delay_ms, если к этому моменту он ещё не закрыт.

    Диалог должен закрываться через accept()/reject()/done() — сигнал
    finished отменяет отложенный показ. Возвращает таймер (для тестов).
    """
    timer = QtCore.QTimer(dialog)
    timer.setSingleShot(True)
    timer.setInterval(delay_ms)
    timer.timeout.connect(dialog.show)
    dialog.finished.connect(timer.stop)
    timer.start()
    return timer
