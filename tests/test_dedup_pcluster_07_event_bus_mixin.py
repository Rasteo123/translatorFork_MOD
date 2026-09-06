import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.core.event_bus_mixin import EventBusMixin
from gemini_translator.core.chunk_assembler import ChunkAssembler
from gemini_translator.core.translation_engine import TranslationEngine
from gemini_translator.core.worker import UniversalWorker
from gemini_translator.ui.widgets.key_management_widget import KeyManagementWidget
from gemini_translator.ui.widgets.model_settings_widget import ModelSettingsWidget


class _TopicBus:
    """Минимальная шина с topic-подписками (как EventBus в main.py)."""

    def __init__(self):
        self.subscribers = {}

    def subscribe(self, event_name, callback):
        self.subscribers.setdefault(event_name, []).append(callback)

    def unsubscribe(self, event_name, callback):
        callbacks = self.subscribers.get(event_name, [])
        if callback in callbacks:
            callbacks.remove(callback)

    def total_subscriptions(self):
        return sum(len(cbs) for cbs in self.subscribers.values())


class _Signal:
    """Минимальный аналог pyqtSignal для broadcast-шины."""

    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def disconnect(self, slot):
        if slot not in self._slots:
            raise TypeError("disconnect() failed between 'event_posted' and 'on_event'")
        self._slots.remove(slot)


class _BroadcastBus:
    def __init__(self):
        self.event_posted = _Signal()


class _Consumer(EventBusMixin):
    """Голый потребитель миксина — без Qt/QObject, как UniversalWorker."""

    def __init__(self, bus, topics=('topic_a', 'topic_b')):
        self.bus = bus
        self._event_topics = topics
        self._uses_topic_subscription = False

    def on_event(self, event):
        pass


class EventBusMixinCharacterizationTests(unittest.TestCase):
    """Характеризационные тесты канонического поведения EventBusMixin.

    Канон — версия ChunkAssembler: idempotent _disconnect_from_bus через
    флаг _bus_connected (единственная из 5 копий, где это было защищено).
    """

    def test_connect_with_topic_bus_subscribes_all_topics(self):
        bus = _TopicBus()
        consumer = _Consumer(bus)
        consumer._connect_to_bus()

        self.assertEqual(bus.total_subscriptions(), 2)
        self.assertTrue(consumer._uses_topic_subscription)
        self.assertTrue(consumer._bus_connected)

    def test_connect_with_broadcast_bus_connects_signal(self):
        bus = _BroadcastBus()
        consumer = _Consumer(bus)
        consumer._connect_to_bus()

        self.assertIn(consumer.on_event, bus.event_posted._slots)
        self.assertFalse(consumer._uses_topic_subscription)
        self.assertTrue(consumer._bus_connected)

    def test_disconnect_unsubscribes_all_topics(self):
        bus = _TopicBus()
        consumer = _Consumer(bus)
        consumer._connect_to_bus()
        consumer._disconnect_from_bus()

        self.assertEqual(bus.total_subscriptions(), 0)
        self.assertFalse(consumer._bus_connected)
        self.assertFalse(consumer._uses_topic_subscription)

    def test_disconnect_is_idempotent(self):
        """Повторный disconnect (двойной cleanup()/closeEvent) — no-op.

        Раньше это было защищено только в ChunkAssembler; остальные 4 копии
        при повторном вызове попытались бы отписаться от уже отписанной
        шины. Канон распространяет идемпотентность на все 5 потребителей.
        """
        bus = _TopicBus()
        consumer = _Consumer(bus)
        consumer._connect_to_bus()
        consumer._disconnect_from_bus()
        self.assertEqual(bus.total_subscriptions(), 0)

        # Второй disconnect не должен падать и не должен трогать шину повторно.
        consumer._disconnect_from_bus()
        self.assertEqual(bus.total_subscriptions(), 0)

    def test_disconnect_without_prior_connect_is_noop(self):
        bus = _TopicBus()
        consumer = _Consumer(bus)
        consumer._disconnect_from_bus()
        self.assertEqual(bus.total_subscriptions(), 0)

    def test_disconnect_swallows_signal_disconnect_errors(self):
        bus = _BroadcastBus()
        consumer = _Consumer(bus)
        consumer._connect_to_bus()
        bus.event_posted._slots.clear()  # эмулируем "уже отключено" на уровне Qt

        consumer._disconnect_from_bus()  # не должен бросить TypeError

        self.assertFalse(consumer._bus_connected)

    def test_missing_event_topics_defaults_safely(self):
        """UniversalWorker создаётся через __new__ в тестах без всех атрибутов
        — disconnect не должен падать, если _event_topics не был выставлен."""
        bus = _TopicBus()
        consumer = EventBusMixin.__new__(_Consumer)
        consumer.bus = bus
        consumer._bus_connected = True
        consumer._uses_topic_subscription = True
        consumer._disconnect_from_bus()  # не должен упасть без _event_topics


class EventBusMixinRoutingTests(unittest.TestCase):
    """Тест-маршрутизация: каждое из 5 бывших мест дублирования обязано
    использовать ИМЕННО канонический EventBusMixin._connect_to_bus /
    _disconnect_from_bus, а не свою локальную копию.

    До рефакторинга падает (у каждого класса своя копия метода);
    после рефакторинга проходит (классы наследуют метод из EventBusMixin).
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_all_five_former_copies_route_through_canonical_mixin(self):
        classes = (
            ChunkAssembler,
            TranslationEngine,
            UniversalWorker,
            KeyManagementWidget,
            ModelSettingsWidget,
        )
        for cls in classes:
            with self.subTest(cls=cls.__name__):
                self.assertIs(
                    cls._connect_to_bus,
                    EventBusMixin._connect_to_bus,
                    f"{cls.__name__}._connect_to_bus не унаследован от EventBusMixin "
                    "(осталась локальная копия-дубликат)",
                )
                self.assertIs(
                    cls._disconnect_from_bus,
                    EventBusMixin._disconnect_from_bus,
                    f"{cls.__name__}._disconnect_from_bus не унаследован от EventBusMixin "
                    "(осталась локальная копия-дубликат)",
                )


if __name__ == "__main__":
    unittest.main()
