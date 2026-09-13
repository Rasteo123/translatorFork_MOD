"""Два экземпляра настроек пишут один settings.json в одно и то же время.

С 16 августа по 11 сентября 2026 года ~/.epub_translator/settings.json 14 раз
уходил в карантин как нечитаемый. У всех копий одна подпись: целый JSON, а за
ним хвост предыдущей версии (в шести случаях ровно 24 байта — одна метка
запроса к API). Так выглядят две перекрывшиеся записи через общий временный
файл settings.json.tmp: оба писателя усекли его при открытии, и короткий
документ лёг поверх длинного. Писали два процесса переводчика, переводившие
разные книги.

Тесты сводят записи двух экземпляров в одну точку во времени: open() для записи
в каталог настроек подменён и управляет порядком самих записей.
"""

import builtins
import json
import logging
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets  # noqa: E402

from gemini_translator.utils import settings as settings_module  # noqa: E402
from gemini_translator.utils.settings import SettingsManager  # noqa: E402

_REAL_OPEN = builtins.open


def _manager(path):
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return SettingsManager(config_file=str(path))


def _start(action, errors):
    def run():
        try:
            action()
        except Exception as error:  # noqa: BLE001 - ошибка потока должна дойти до теста
            errors.append(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


class _GatedFile:
    def __init__(self, handle, gate):
        self._handle = handle
        self._gate = gate

    def write(self, data):
        self._gate.before_write(len(data))
        written = self._handle.write(data)
        self._handle.flush()
        self._gate.after_write(len(data))
        return written

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return self._handle.__exit__(*exc_info)

    def __getattr__(self, name):
        return getattr(self._handle, name)


class _WriteGate:
    """Пропускает запись в каталог настроек через before_write/after_write."""

    def __init__(self, directory):
        self._directory = os.path.abspath(str(directory))

    def open(self, file, mode="r", *args, **kwargs):
        handle = _REAL_OPEN(file, mode, *args, **kwargs)
        writes = bool(set(mode) & set("wax+"))
        if writes and isinstance(file, (str, os.PathLike)):
            if os.path.dirname(os.path.abspath(os.fspath(file))) == self._directory:
                return _GatedFile(handle, self)
        return handle

    def before_write(self, size):
        pass

    def after_write(self, size):
        pass


class _OverlappingWrites(_WriteGate):
    """Оба писателя сначала открывают файл и только потом пишут: длинный первым."""

    def __init__(self, directory, timeout):
        super().__init__(directory)
        self._arrived = threading.Barrier(2, timeout=timeout)
        self._timeout = timeout
        self._lock = threading.Lock()
        self._sizes = {}
        self._longer_written = threading.Event()

    def _is_longer(self):
        return self._sizes[threading.get_ident()] == max(self._sizes.values())

    def before_write(self, size):
        with self._lock:
            self._sizes[threading.get_ident()] = size
        try:
            self._arrived.wait()
        except threading.BrokenBarrierError:
            return  # второй писатель не пришёл — записи не пересеклись
        with self._lock:
            longer = self._is_longer()
        if not longer:
            self._longer_written.wait(self._timeout)

    def after_write(self, size):
        with self._lock:
            if self._is_longer():
                self._longer_written.set()


class _FirstWriteWaitsForSecondSave(_WriteGate):
    """Первая запись ждёт, пока другой экземпляр целиком сохранит свои настройки."""

    def __init__(self, directory, timeout):
        super().__init__(directory)
        self.first_writer_waiting = threading.Event()
        self.second_save_finished = threading.Event()
        self._timeout = timeout
        self._lock = threading.Lock()
        self._first_writer = None

    def before_write(self, size):
        with self._lock:
            if self._first_writer is None:
                self._first_writer = threading.get_ident()
            first = self._first_writer == threading.get_ident()
        if first and not self.first_writer_waiting.is_set():
            self.first_writer_waiting.set()
            self.second_save_finished.wait(self._timeout)


def _read_settings(path):
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        pytest.fail(f"settings.json не читается: {error.msg}, позиция {error.pos} из {len(text)}")


def test_overlapping_saves_of_two_instances_leave_a_readable_file(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    first, second = _manager(path), _manager(path)
    long_prompt, short_prompt = "длинный промпт " * 50, "короткий"
    gate = _OverlappingWrites(tmp_path, timeout=1.0)
    monkeypatch.setattr(builtins, "open", gate.open)

    errors = []
    threads = [
        _start(lambda: first.save_custom_prompt(long_prompt), errors),
        _start(lambda: second.save_custom_prompt(short_prompt), errors),
    ]
    for thread in threads:
        thread.join(10)
    monkeypatch.undo()

    assert _read_settings(path)["custom_prompt"] in {long_prompt, short_prompt}
    assert errors == []
    assert list(tmp_path.glob("*.tmp")) == []


def test_save_that_overlaps_another_instance_keeps_both_changes(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    first, second = _manager(path), _manager(path)
    gate = _FirstWriteWaitsForSecondSave(tmp_path, timeout=1.0)
    monkeypatch.setattr(builtins, "open", gate.open)

    def save_in_second_window():
        try:
            second.save_last_project_folder("/books/second")
        finally:
            gate.second_save_finished.set()

    errors = []
    first_thread = _start(lambda: first.save_custom_prompt("промпт первого окна"), errors)
    assert gate.first_writer_waiting.wait(5)
    second_thread = _start(save_in_second_window, errors)
    first_thread.join(10)
    second_thread.join(10)
    monkeypatch.undo()

    saved = _read_settings(path)
    assert saved.get("custom_prompt") == "промпт первого окна"
    assert saved.get("last_project_folder") == "/books/second"
    assert errors == []


# Второй процесс берёт замок настроек; модуль грузится по пути к файлу.
_LOCK_HOLDER = textwrap.dedent(
    """
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("interprocess_lock", sys.argv[1])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with module.interprocess_lock(sys.argv[2], timeout=5) as locked:
        print("locked" if locked else "not locked", flush=True)
        sys.stdin.readline()
    """
)


def test_save_does_not_hang_while_another_process_keeps_the_settings_lock(
    tmp_path, monkeypatch, caplog
):
    path = tmp_path / "settings.json"
    manager = _manager(path)
    monkeypatch.setattr(settings_module, "SETTINGS_LOCK_TIMEOUT_SECONDS", 0.2)
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            _LOCK_HOLDER,
            str(Path(settings_module.__file__).with_name("interprocess_lock.py")),
            settings_module.settings_lock_path(str(path)),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "locked"
        started = time.monotonic()
        with caplog.at_level(logging.WARNING, logger=settings_module.__name__):
            manager.save_custom_prompt("сохранено без замка")
        elapsed = time.monotonic() - started
    finally:
        holder.communicate("\n", timeout=10)

    assert _read_settings(path)["custom_prompt"] == "сохранено без замка"
    assert elapsed < 2
    assert any(
        record.name == settings_module.__name__ and record.levelno == logging.WARNING
        for record in caplog.records
    )
