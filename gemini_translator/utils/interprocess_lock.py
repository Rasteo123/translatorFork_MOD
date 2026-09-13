"""Межпроцессный эксклюзивный замок на файле-замке.

Нужен там, где несколько процессов приложения по очереди делают «прочитать →
слить → записать» над одним файлом. Атомарная подмена (``io_utils``) спасает от
битого файла, но не от потери правок: если чтения и записи двух процессов
перекрываются, более поздняя запись затирает изменения более ранней.

POSIX — ``fcntl.flock``: замок принадлежит открытому описанию файла, поэтому
исключает и другие процессы, и второй дескриптор в том же процессе. Windows —
``msvcrt.locking`` на первый байт, замок принадлежит дескриптору. Процесс,
завершившийся с замком, освобождает его вместе со своими дескрипторами.

Только стандартная библиотека: тесты загружают модуль во втором процессе по
пути к файлу, без пакета ``gemini_translator``.
"""

from __future__ import annotations

import contextlib
import errno
import os
import threading
import time

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt

# msvcrt.locking с LK_NBLCK сообщает о занятом байте через EACCES.
_WINDOWS_BUSY_ERRNOS = {errno.EACCES, errno.EDEADLK}

# Сколько раз текущий поток уже вошёл в замок: (поток, путь) -> глубина.
_depth_by_holder: dict[tuple[int, str], int] = {}
_depth_guard = threading.Lock()


class _Busy(Exception):
    """Замок держит кто-то другой."""


def _try_lock(fd: int) -> None:
    if fcntl is not None:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise _Busy from error
        return
    os.lseek(fd, 0, os.SEEK_SET)
    try:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    except OSError as error:
        if error.errno in _WINDOWS_BUSY_ERRNOS:
            raise _Busy from error
        raise


def _unlock(fd: int) -> None:
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return
    os.lseek(fd, 0, os.SEEK_SET)
    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


def _acquire(path: str, timeout: float) -> int | None:
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        return None
    deadline = time.monotonic() + max(0.0, timeout)
    pause = 0.001
    while True:
        try:
            _try_lock(fd)
            return fd
        except _Busy:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(pause, remaining))
            pause = min(pause * 2, 0.05)
        except OSError:
            break  # файловая система не поддерживает замки
    os.close(fd)
    return None


@contextlib.contextmanager
def interprocess_lock(lock_path, *, timeout: float):
    """Держит эксклюзивный замок на ``lock_path`` на время блока ``with``.

    Отдаёт ``True``, если замок взят, и ``False``, если за ``timeout`` секунд
    этого не случилось или файловая система замков не поддерживает: вызывающий
    сам решает, продолжать ли без замка. Поток, который уже держит замок, входит
    в него повторно без ожидания.
    """
    holder = (threading.get_ident(), os.path.normcase(os.path.abspath(os.fspath(lock_path))))
    with _depth_guard:
        depth = _depth_by_holder.get(holder, 0)
        if depth:
            _depth_by_holder[holder] = depth + 1
    if depth:
        try:
            yield True
        finally:
            with _depth_guard:
                _depth_by_holder[holder] -= 1
        return

    fd = _acquire(holder[1], timeout)
    if fd is None:
        yield False
        return
    with _depth_guard:
        _depth_by_holder[holder] = 1
    try:
        yield True
    finally:
        with _depth_guard:
            del _depth_by_holder[holder]
        try:
            _unlock(fd)
        finally:
            os.close(fd)
