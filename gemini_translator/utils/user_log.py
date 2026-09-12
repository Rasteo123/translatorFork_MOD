# gemini_translator/utils/user_log.py
# -*- coding: utf-8 -*-

"""Канал «код → пользователь» поверх stdlib logging.

У приложения не было способа сообщить что-то человеку помимо шины событий.
Модуль без ссылки на шину — `os_patch`, `api/config`, всё, что работает до
создания окна, — писал `print()`, а при запуске GUI консоли не существует.
Так молча терялись предупреждение PatientLock о зависшем замке и причины
отклонённых полей конфигурации: код честно сообщал о проблеме в пустоту.

Здесь ничего нового не изобретается. `ServerManager` давно мостит logging в
окно лога через свой `GuiLogHandler`; этот модуль делает то же самое, но один
раз и для корневого логгера, чтобы любому модулю хватало обычного
`logging.getLogger(__name__).warning(...)`.

Два решения, которые стоит объяснить.

WARNING и выше. Окно лога читает человек, а не отладчик: INFO из библиотек
залил бы его и обесценил. Кому нужна подробность — пишет в свой логгер на
уровне ниже, в окно она не попадёт.

Буфер до появления шины. Оба случая, ради которых канал и заводится,
происходят на старте: конструктор `SettingsManager` отклоняет модель раньше,
чем существует окно. Без буфера сообщение снова потерялось бы — только уже
внутри новой красивой системы.
"""

import logging
from collections import deque

BACKLOG_LIMIT = 200

_HANDLER = None


class UserNoticeHandler(logging.Handler):
    """Отправляет запись в окно лога; до появления шины держит её у себя."""

    def __init__(self, level=logging.WARNING):
        super().__init__(level)
        self._bus = None
        self._backlog = deque(maxlen=BACKLOG_LIMIT)

    def attach_bus(self, bus):
        """Подключает шину и отдаёт ей всё, что накопилось до её появления."""
        self._bus = bus
        pending = list(self._backlog)
        self._backlog.clear()
        for message in pending:
            self._send(message)

    def detach_bus(self):
        self._bus = None

    def _send(self, message):
        self._bus.event_posted.emit({
            "event": "log_message",
            "data": {"message": message},
        })

    @staticmethod
    def _discover_bus():
        """Ищет шину там же, где её ищет SettingsManager, — в QApplication.

        Так канал не требует отдельного шага подключения в main.py: первое же
        предупреждение после создания шины находит её само, а всё, что накопилось
        раньше, уезжает следом.
        """
        try:
            from PyQt6 import QtWidgets
        except Exception:  # noqa: BLE001 - консольный запуск без Qt
            return None
        application = QtWidgets.QApplication.instance()
        return getattr(application, "event_bus", None) if application else None

    def emit(self, record):
        # Обработчик логов не имеет права бросать: исключение отсюда прилетит
        # в случайное место программы, которое всего лишь хотело предупредить.
        try:
            message = self.format(record)
        except Exception:  # noqa: BLE001 - форматирование чужой записи
            return

        if self._bus is None:
            discovered = self._discover_bus()
            if discovered is None:
                self._backlog.append(message)
                return
            self._backlog.append(message)
            self.attach_bus(discovered)
            return

        try:
            self._send(message)
        except RuntimeError:
            # При выходе Qt-объект шины умирает раньше последних логов.
            self._bus = None
        except Exception:  # noqa: BLE001 - шина не должна ронять вызывающего
            pass


def install(level=logging.WARNING):
    """Ставит обработчик на корневой логгер. Повторный вызов ничего не делает."""
    global _HANDLER
    if _HANDLER is not None:
        return _HANDLER

    _HANDLER = UserNoticeHandler(level)
    logging.getLogger().addHandler(_HANDLER)
    return _HANDLER


def attach_event_bus(bus):
    """Подключает шину к каналу; накопленное уезжает в окно немедленно."""
    handler = install()
    handler.attach_bus(bus)
    return handler
