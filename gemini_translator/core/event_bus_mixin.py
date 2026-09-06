# -*- coding: utf-8 -*-
"""Общий миксин подписки/отписки от EventBus.

Устраняет дубликат ``_connect_to_bus``/``_disconnect_from_bus`` (pcluster-07),
скопированный побайтово (для пары ``ChunkAssembler``/``KeyManagementWidget``/
``ModelSettingsWidget``) или почти побайтово (``UniversalWorker``,
``TranslationEngine``) в 5 местах ядра и UI.

Канон поведения — версия ``ChunkAssembler``: она единственная была защищена
от повторного вызова ``_disconnect_from_bus`` флагом ``_bus_connected``.
Остальные 4 копии такой защиты не имели (двойной ``cleanup()``/``closeEvent``
привёл бы там к попытке повторно отписаться от уже отписанной шины). Это
осознанное изменение поведения: идемпотентность распространяется на все
5 потребителей мискина.

``_post_event`` в миксин намеренно НЕ вынесен — состав полей словаря события
у ``UniversalWorker`` (добавляет ``worker_key``, ``source`` в формате
``worker_<id>``) и ``TranslationEngine`` (``source='TranslationEngine'``)
различается осознанно, объединение потребовало бы флагов-переключателей
поведения ради нескольких строк общего кода.

Миксин не предполагает ``QObject``/``QWidget`` — ``UniversalWorker``
обычный python-класс. Класс-потребитель обязан перед первым вызовом
``_connect_to_bus()`` задать:

- ``self.bus`` — объект шины событий (topic-API: ``subscribe``/``unsubscribe``;
  либо broadcast-API: сигнал Qt ``event_posted``);
- ``self._event_topics`` — кортеж имён топиков для topic-API;
- ``self.on_event`` — обработчик входящих событий.
"""


class EventBusMixin:
    """Подписка/отписка на EventBus с защитой от двойного disconnect."""

    def _connect_to_bus(self):
        if hasattr(self.bus, "subscribe"):
            for topic in self._event_topics:
                self.bus.subscribe(topic, self.on_event)
            self._uses_topic_subscription = True
        elif hasattr(self.bus, "event_posted"):
            self.bus.event_posted.connect(self.on_event)
            self._uses_topic_subscription = False
        else:
            self._uses_topic_subscription = False
        self._bus_connected = True

    def _disconnect_from_bus(self):
        if not getattr(self, "_bus_connected", False):
            return

        try:
            if getattr(self, "_uses_topic_subscription", False) and hasattr(self.bus, "unsubscribe"):
                for topic in getattr(self, "_event_topics", ()):
                    self.bus.unsubscribe(topic, self.on_event)
            elif hasattr(self.bus, "event_posted"):
                self.bus.event_posted.disconnect(self.on_event)
        except (TypeError, RuntimeError, ValueError):
            pass
        finally:
            self._bus_connected = False
            self._uses_topic_subscription = False
