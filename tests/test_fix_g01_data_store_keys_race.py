"""
Тест для root-entry/bugs/4-eventbus-datastore-unlocked-it.

EventBus._data_store — общий для всех экземпляров словарь, который
set_data/pop_data/get_data мутируют под self._lock (main.py), но ядро
(worker.py, task_manager.py, translation_engine.py) итерирует
`bus._data_store.keys()` напрямую, без блокировки. Конкурентная мутация
словаря во время такой итерации бросает
`RuntimeError: dictionary changed size during iteration`.

Эти внешние файлы вне области правки (группа g01 = main.py), поэтому
проверяем, что `.keys()` самого _data_store стал потокобезопасным сам по
себе — тогда все внешние вызовы `bus._data_store.keys()` становятся
безопасными без единой правки в worker.py/task_manager.py/translation_engine.py.
"""
import os
import threading
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from main import EventBus


class DataStoreKeysRaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_concurrent_set_pop_and_keys_iteration_does_not_raise(self):
        bus = EventBus()
        # Реальный набор ключей проекта: 2-4 штуки одновременно.
        keys = [
            "current_active_session",
            "cli_session_active",
            "managed_session_active_1",
            "managed_session_active_2",
        ]

        stop = threading.Event()
        errors = []

        def mutator():
            i = 0
            while not stop.is_set():
                key = keys[i % len(keys)]
                bus.set_data(key, i)
                bus.pop_data(key, None)
                i += 1

        def reader():
            # Дословно повторяет паттерн вызовов из ядра:
            # `for key in self.bus._data_store.keys(): ...`
            while not stop.is_set():
                try:
                    for _key in bus._data_store.keys():
                        pass
                    [k for k in bus._data_store.keys() if k.startswith("managed_")]
                except RuntimeError as exc:
                    errors.append(exc)
                    return

        mutators = [threading.Thread(target=mutator) for _ in range(4)]
        readers = [threading.Thread(target=reader) for _ in range(4)]

        for t in mutators + readers:
            t.start()

        # Достаточно окна гонки, чтобы старый bare dict почти всегда падал.
        stop.wait(1.0)
        stop.set()
        for t in mutators + readers:
            t.join(timeout=5)

        self.assertEqual(
            errors,
            [],
            "Итерация bus._data_store.keys() не должна падать при "
            "конкурентных set_data/pop_data из других потоков",
        )

    def test_concurrent_items_values_copy_do_not_raise_or_deadlock(self):
        """Defense-in-depth (review round 2, minor): .keys() — не единственная
        форма чтения. `.items()`, `.values()`, `.copy()` по-прежнему брали бы
        'живой' dict и падали бы точно так же, если бы _KeysSnapshotDict
        защищал только .keys().

        Проверяем не только отсутствие RuntimeError, но и то, что все потоки
        реально завершаются (thread.is_alive() после join): при первой
        попытке реализовать это защитой заодно и __iter__ обнаружился
        реальный deadlock в .copy() (self._snapshot_lock не рекурсивный, а
        CPython для dict-подкласса с переопределённым __iter__ реализует
        dict.copy() через повторный вызов keys() изнутри) — тест, который
        лишь проверяет `errors == []` и не проверяет, что потоки закончили
        работу, такой deadlock не поймает: реальный код зависнет навсегда,
        и это единственный способ увидеть его на этом эвристическом тесте.

        Примечание: голый конструктор `dict(bus._data_store)` и
        `for k in bus._data_store:` сюда намеренно не включены — __iter__
        осознанно не переопределён (см. докстринг _KeysSnapshotDict в
        main.py): его переопределение как раз и ломает быстрый atomic-путь
        dict.copy()/dict(x) в CPython. Внешний код
        (worker.py/task_manager.py/translation_engine.py) такие вызовы не
        использует — используется только .keys()."""
        bus = EventBus()
        keys = [
            "current_active_session",
            "cli_session_active",
            "managed_session_active_1",
            "managed_session_active_2",
        ]

        stop = threading.Event()
        errors = []

        def mutator():
            i = 0
            while not stop.is_set():
                key = keys[i % len(keys)]
                bus.set_data(key, i)
                bus.pop_data(key, None)
                i += 1

        def reader():
            while not stop.is_set():
                try:
                    list(bus._data_store.items())
                    list(bus._data_store.values())
                    bus._data_store.copy()
                except RuntimeError as exc:
                    errors.append(exc)
                    return

        mutators = [threading.Thread(target=mutator) for _ in range(4)]
        readers = [threading.Thread(target=reader) for _ in range(4)]

        for t in mutators + readers:
            t.start()

        stop.wait(1.0)
        stop.set()
        for t in mutators + readers:
            t.join(timeout=5)

        stuck = [t.name for t in mutators + readers if t.is_alive()]
        self.assertEqual(
            stuck,
            [],
            "Поток(и) не завершились за отведённый таймаут — похоже на "
            "deadlock в .items()/.values()/.copy()",
        )
        self.assertEqual(
            errors,
            [],
            ".items()/.values()/.copy() над bus._data_store не должны "
            "падать при конкурентных set_data/pop_data",
        )


if __name__ == "__main__":
    unittest.main()
