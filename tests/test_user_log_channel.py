"""Канал «код → пользователь»: предупреждение обязано дойти до окна лога.

До сих пор у приложения не было способа что-то сообщить человеку помимо шины
событий. Модуль без ссылки на шину — а это `os_patch`, `api/config` и всё, что
работает до создания окна, — писал `print()`, и сообщение уходило в консоль,
которой при запуске GUI не существует. Так молча терялись предупреждение
PatientLock о зависшем замке и причины отклонённых полей конфигурации.
"""

from __future__ import annotations

import logging

import pytest

from gemini_translator.utils import user_log


class _RecordingBus:
    """Подделка шины: у настоящей это Qt-сигнал с тем же .emit()."""

    def __init__(self):
        self.events = []

        class _Signal:
            def __init__(self, sink):
                self._sink = sink

            def emit(self, payload):
                self._sink.append(payload)

        self.event_posted = _Signal(self.events)


class _DeadBus:
    class _Signal:
        def emit(self, payload):
            raise RuntimeError("wrapped C/C++ object of type EventBus has been deleted")

    event_posted = _Signal()


@pytest.fixture
def channel(monkeypatch):
    """Канал без автопоиска шины.

    Автопоиск смотрит в QApplication, а её в общем прогоне успевает создать
    какой-нибудь другой тест — и тогда «шины ещё нет» перестаёт быть правдой.
    Тесты, которым автопоиск нужен, включают его сами.
    """
    monkeypatch.setattr(
        user_log.UserNoticeHandler, "_discover_bus", staticmethod(lambda: None)
    )
    handler = user_log.UserNoticeHandler()
    logger = logging.getLogger("тест.канал")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    logger.propagate = False
    yield handler, logger
    logger.removeHandler(handler)


def _messages(bus):
    return [event["data"]["message"] for event in bus.events]


def test_warning_reaches_the_bus(channel):
    handler, logger = channel
    bus = _RecordingBus()
    handler.attach_bus(bus)

    logger.warning("ключ отклонён")

    assert _messages(bus) == ["ключ отклонён"]
    assert bus.events[0]["event"] == "log_message"


def test_debug_and_info_stay_out_of_the_user_log(channel):
    """Окно лога — для человека, а не поток отладки."""
    handler, logger = channel
    bus = _RecordingBus()
    handler.attach_bus(bus)

    logger.debug("подробность")
    logger.info("рутина")

    assert _messages(bus) == []


def test_a_warning_raised_before_the_window_exists_is_not_lost(channel):
    """Ровно этот случай и терялся: предупреждение на старте, окна ещё нет."""
    handler, logger = channel

    logger.warning("первое, до окна")
    logger.error("второе, до окна")

    bus = _RecordingBus()
    handler.attach_bus(bus)

    assert _messages(bus) == ["первое, до окна", "второе, до окна"]


def test_the_backlog_is_delivered_once(channel):
    handler, logger = channel
    logger.warning("одно")

    first = _RecordingBus()
    handler.attach_bus(first)
    second = _RecordingBus()
    handler.attach_bus(second)

    assert _messages(first) == ["одно"]
    assert _messages(second) == [], "накопленное не должно приезжать дважды"


def test_the_backlog_is_bounded(channel):
    """Приложение без окна не должно копить память бесконечно."""
    handler, logger = channel
    for index in range(user_log.BACKLOG_LIMIT * 2):
        logger.warning("сообщение %d", index)

    bus = _RecordingBus()
    handler.attach_bus(bus)

    delivered = _messages(bus)
    assert len(delivered) == user_log.BACKLOG_LIMIT
    assert delivered[-1] == f"сообщение {user_log.BACKLOG_LIMIT * 2 - 1}", (
        "при переполнении выбрасывать надо старое, а не свежее"
    )


def test_a_destroyed_bus_never_breaks_the_caller(channel):
    """При выходе Qt-объект шины умирает раньше последних логов."""
    handler, logger = channel
    handler.attach_bus(_DeadBus())

    logger.warning("после смерти шины")  # не должно бросить


def test_install_is_idempotent():
    """Повторный вызов не должен множить сообщения в окне."""
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        first = user_log.install()
        second = user_log.install()
        assert first is second
        assert sum(isinstance(h, user_log.UserNoticeHandler) for h in root.handlers) == 1
    finally:
        root.handlers = before
        user_log._HANDLER = None


def test_the_channel_finds_the_bus_on_its_own(channel, monkeypatch):
    """Отдельный шаг подключения в main.py не нужен и не будет забыт.

    Шину ищем там же, где её ищет SettingsManager, — в QApplication. Первое же
    предупреждение после её создания находит её само и увозит накопленное.
    """
    handler, logger = channel
    bus = _RecordingBus()
    monkeypatch.setattr(user_log.UserNoticeHandler, "_discover_bus", staticmethod(lambda: None))

    logger.warning("пока шины нет")

    monkeypatch.setattr(user_log.UserNoticeHandler, "_discover_bus", staticmethod(lambda: bus))
    logger.warning("шина появилась")

    assert _messages(bus) == ["пока шины нет", "шина появилась"]


def test_discovery_survives_a_console_run_without_qt(channel, monkeypatch):
    """CLI поднимается без QApplication — предупреждение просто копится."""
    handler, logger = channel
    monkeypatch.setattr(user_log.UserNoticeHandler, "_discover_bus", staticmethod(lambda: None))

    logger.warning("консольный запуск")  # не должно бросить

    assert list(handler._backlog) == ["консольный запуск"]


def test_a_rejected_model_field_actually_reaches_the_log_window(tmp_path, monkeypatch):
    """Сквозная проверка исходного случая.

    Пользователь ввёл в настройках нечисловой лимит. Раньше причина уходила в
    `print`, то есть в несуществующую консоль, и модель просто вела себя не так,
    как человек ожидал. Теперь причина обязана доехать до окна лога.
    """
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6 import QtWidgets

    from gemini_translator.api import config as api_config

    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    bus = _RecordingBus()
    monkeypatch.setattr(application, "event_bus", bus, raising=False)

    handler = user_log.install()
    handler.detach_bus()
    handler._backlog.clear()

    api_config.initialize_configs()
    try:
        api_config.set_custom_provider_models({
            "gemini": {"Моя модель": {"id": "m", "rpm": "пять"}}
        })
    finally:
        api_config.set_custom_provider_models({})

    delivered = " | ".join(_messages(bus))
    assert "Моя модель" in delivered, f"причина не доехала до окна: {bus.events!r}"
    assert "rpm" in delivered
