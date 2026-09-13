"""interprocess_lock: замок, который видят другие процессы и другие потоки.

Им SettingsManager выстраивает в очередь «прочитать → слить → записать» над
settings.json, который делят несколько окон приложения. Главное свойство —
чужой держатель не пускает внутрь — проверяется на настоящем втором процессе.
"""

import subprocess
import sys
import textwrap
import threading

from gemini_translator.utils import interprocess_lock as lock_module

# Модуль грузится по пути к файлу: пакет gemini_translator второму процессу не нужен.
_HOLDER = textwrap.dedent(
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


def _hold_in_another_process(lock_path):
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, lock_module.__file__, str(lock_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout.readline().strip() == "locked"
    return holder


def _release(holder):
    holder.communicate("\n", timeout=10)


def test_lock_held_by_another_process_is_not_granted(tmp_path):
    lock_path = tmp_path / ".settings.json.lock"
    holder = _hold_in_another_process(lock_path)
    try:
        with lock_module.interprocess_lock(lock_path, timeout=0.2) as locked:
            assert locked is False
    finally:
        _release(holder)


def test_lock_is_granted_after_the_other_process_releases_it(tmp_path):
    lock_path = tmp_path / ".settings.json.lock"
    _release(_hold_in_another_process(lock_path))

    with lock_module.interprocess_lock(lock_path, timeout=1) as locked:
        assert locked is True


def test_lock_held_by_another_thread_of_this_process_is_not_granted(tmp_path):
    lock_path = tmp_path / ".settings.json.lock"
    held, release = threading.Event(), threading.Event()

    def hold():
        with lock_module.interprocess_lock(lock_path, timeout=1) as locked:
            assert locked
            held.set()
            release.wait(10)

    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    try:
        assert held.wait(5)
        with lock_module.interprocess_lock(lock_path, timeout=0.2) as locked:
            assert locked is False
    finally:
        release.set()
        holder.join(10)


def test_thread_that_holds_the_lock_can_enter_it_again(tmp_path):
    lock_path = tmp_path / ".settings.json.lock"

    with lock_module.interprocess_lock(lock_path, timeout=1) as outer:
        with lock_module.interprocess_lock(lock_path, timeout=0) as inner:
            assert (outer, inner) == (True, True)
