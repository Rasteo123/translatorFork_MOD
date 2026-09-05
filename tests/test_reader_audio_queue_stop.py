"""Читалка: «Стоп» не должен вешать поток воркера на переполненной очереди звука.

Регрессия аудита gemini-reader/bugs/1: audio_queue = queue.Queue(maxsize=100), а
воркер клал чанки блокирующим put() без таймаута и без проверки флага остановки.
После force_stop() AudioPlayer больше не читал очередь, put() блокировался навсегда,
QThread не завершался, и closeEvent отказывался закрывать окно.
"""
import os
import queue
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gemini_reader_v3 as reader  # noqa: E402
from gemini_reader_v3 import GeminiWorker  # noqa: E402


class _WorkerHarness:
    """Боевые тела методов GeminiWorker на минимальном объекте."""

    _commit_live_audio_bytes = GeminiWorker._commit_live_audio_bytes
    _enqueue_live_audio = GeminiWorker._enqueue_live_audio
    stop = GeminiWorker.stop

    def __init__(self, audio_queue):
        self.audio_queue = audio_queue
        self.fast = False
        self.record = False
        self.c_idx = 0
        self.s_idx = 0
        self.buffer_lock = threading.Lock()
        self._is_running = True


def _full_queue():
    audio_queue = queue.Queue(maxsize=100)
    for _ in range(100):
        audio_queue.put_nowait((b"\x00" * 4800, 0, 0, False))
    return audio_queue


def _run_with_deadline(target, seconds: float) -> bool:
    """Запускает target в потоке; исключение внутри потока валит тест, а не маскирует зависание."""
    outcome = {}

    def runner():
        try:
            outcome["result"] = target()
        except BaseException as exc:  # noqa: BLE001 - пробрасываем в тестовый поток
            outcome["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join(seconds)
    if "error" in outcome:
        raise outcome["error"]
    return not thread.is_alive()


def test_commit_returns_promptly_after_stop_on_full_queue():
    worker = _WorkerHarness(_full_queue())
    worker.stop()
    finished = _run_with_deadline(lambda: worker._commit_live_audio_bytes(b"\x00" * 4800), seconds=3.0)
    assert finished, "put() на полной очереди после Стоп заблокировал поток воркера"
    assert worker._enqueue_live_audio((b"\x00", 0, 0, False)) is False
    assert worker.audio_queue.qsize() == 100, "после Стоп чанк должен отбрасываться, а не добавляться"


def test_commit_unblocks_when_stop_arrives_while_waiting():
    worker = _WorkerHarness(_full_queue())
    started = time.monotonic()

    def stop_soon():
        time.sleep(0.3)
        worker.stop()

    threading.Thread(target=stop_soon, daemon=True).start()
    finished = _run_with_deadline(lambda: worker._commit_live_audio_bytes(b"\x00" * 4800), seconds=3.0)
    assert finished, "воркер не заметил Стоп, пока ждал место в очереди"
    assert time.monotonic() - started < 3.0


class _FakeStream:
    def write(self, data):
        pass

    def stop_stream(self):
        pass

    def close(self):
        pass


class _FakePyAudio:
    paInt16 = 8

    class PyAudio:
        def open(self, **kwargs):
            return _FakeStream()

        def terminate(self):
            pass


def test_player_stop_drains_the_queue(monkeypatch):
    monkeypatch.setattr(reader, "pyaudio", _FakePyAudio())
    audio_queue = _full_queue()
    player = reader.AudioPlayer(audio_queue, 80)
    player.stop()
    assert audio_queue.qsize() == 0, "после остановки плеера очередь должна быть пуста, иначе производители остаются заблокированными"
