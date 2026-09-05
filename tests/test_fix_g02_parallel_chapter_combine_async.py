"""Регресс для gemini-reader/bugs/3-parallel-combine-blocks-gui + фиксы по
замечаниям рецензента к первой версии этой правки.

До фикса `_finalize_parallel_live_chapter` вызывала `_combine_mp3_sequence`
(блокирующий ffmpeg-`subprocess.run`) синхронно прямо в обработчике Qt-сигнала
`_on_worker_finished`, исполняющемся в главном потоке — это замораживало GUI на
время склейки главы. После фикса склейка выполняется в отдельном QThread
(`ParallelLiveChapterCombineWorker`), а `_finalize_parallel_live_chapter`
возвращает `None` сразу же, не дожидаясь ffmpeg.

Рецензент нашёл три major-дефекта в первой версии этой асинхронной правки —
все воспроизведены здесь до фикса и проверены после:

1. `_running_tasks_exist()` не знал о фоновом `ParallelLiveChapterCombineWorker`
   — во время склейки приложение считало себя простаивающим (можно закрыть
   окно, кнопка "СТАРТ" снова активна).
2. Отложенный колбэк завершения склейки читал ТЕКУЩИЙ
   `self._parallel_live_state`, а не тот, для которого запускался, — если
   пользователь успевал запустить новую параллельную озвучку поверх ещё
   работающей склейки, колбэк старой склейки удалял temp_dir и обнулял state
   уже НОВОЙ, ещё идущей сессии.
3. Ссылка на воркер обнулялась в слоте кастомного `finished_signal`, который
   эмитируется ИЗ ТЕЛА `run()` до фактического завершения потока, — классическая
   гонка "QThread destroyed while still running".

По образцу tests/test_reader_video_export_lifecycle.py (и по факту той же
идее, что уже реализована в этом модуле для `AudioCombinerWorker`): тело
`_finalize_parallel_live_chapter` реальное (боевое), сам класс воркера
подменяется управляемой фейковой реализацией с РАЗДЕЛЬНЫМИ ручными сигналами
`finished_signal` (кастомный, эмитируется из run()) и `finished` (встроенный
QThread-сигнал, эмитируется отдельно и позже) — так тест может проверить
поведение именно в опасном промежутке между ними.
"""
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QThread

import gemini_reader_v3 as reader


class _Signal:
    """Синхронный сигнал: emit сразу вызывает подключённые слоты (как в
    tests/test_reader_video_export_lifecycle.py)."""

    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def emit(self, *args):
        for slot in list(self.slots):
            slot(*args)


class _FakeParallelCombineWorker:
    """Подменяет ParallelLiveChapterCombineWorker: не запускает реальный поток,
    а лишь фиксирует переданные аргументы и ждёт РУЧНОГО emit двух РАЗНЫХ
    сигналов — finished_signal (результат) и finished (реальное завершение
    потока), — чтобы тест мог различить момент "run() досказал результат" и
    момент "поток фактически завершился"."""

    instances = []

    def __init__(self, book_manager, chapter_index, output_paths, total_tasks, worker_count):
        self.bm = book_manager
        self.chapter_index = chapter_index
        self.output_paths = list(output_paths)
        self.total_tasks = total_tasks
        self.worker_count = worker_count
        self.finished_signal = _Signal()
        self.finished = _Signal()
        self.started = False
        self.deleted_later = False
        self._running = False
        _FakeParallelCombineWorker.instances.append(self)

    def start(self):
        self.started = True
        self._running = True

    def isRunning(self):
        return self._running

    def deleteLater(self):
        self.deleted_later = True


class _FakeBookManager:
    def __init__(self):
        self.mark_done_calls = []

    def get_mp3_path(self, chapter_index):
        return f"/fake/book/Ch{chapter_index + 1}.mp3"

    def mark_chapter_done(self, chapter_index):
        self.mark_done_calls.append(chapter_index)


class _StubWorker:
    def __init__(self, worker_id):
        self.worker_id = worker_id


class _ParallelLiveFinalizeHarness:
    _finalize_parallel_live_chapter = reader.MainWindow._finalize_parallel_live_chapter
    _cleanup_parallel_live_state = reader.MainWindow._cleanup_parallel_live_state
    _complete_reading_session = reader.MainWindow._complete_reading_session
    _running_tasks_exist = reader.MainWindow._running_tasks_exist

    def __init__(self, state):
        self.bm = _FakeBookManager()
        self._parallel_live_state = state
        self._parallel_live_combine_worker = None
        self.workers = []
        self.combiner = None
        self.tester_worker = None
        self.chapter_done_calls = []
        self.session_messages = []
        self.refresh_calls = 0
        self._active_manager_queue = None
        self._active_reader_engine = "live"
        self._active_flash_run_mode = None
        self._run_had_invalid_keys = False
        self._project_quota_message = ""
        self._stop_requested = False

    def on_chapter_done_ui(self, idx):
        self.chapter_done_calls.append(idx)

    def statusBar(self):
        return self

    def showMessage(self, message):
        self.session_messages.append(message)

    def _refresh_runtime_controls(self):
        self.refresh_calls += 1


class _OnWorkerFinishedHarness:
    """Минимальный харнесс на реальное тело _on_worker_finished — проверяет
    только контракт "финализация в фоне -> ранний return без повторного
    завершения сессии" (minor-замечание рецензента #4)."""

    _on_worker_finished = reader.MainWindow._on_worker_finished
    _finalize_parallel_live_chapter = reader.MainWindow._finalize_parallel_live_chapter
    _cleanup_parallel_live_state = reader.MainWindow._cleanup_parallel_live_state

    def __init__(self, state, worker_id=0):
        self.bm = _FakeBookManager()
        self._parallel_live_state = state
        self._parallel_live_combine_worker = None
        self._active_job_kind = "tts_parallel_live"
        self.workers = [_StubWorker(worker_id)]
        self._pending_worker_progress = {}
        self.worker_widgets = {}
        self.player = None
        self._current_chapter_index = None
        self._active_manager_queue = None
        self._run_had_invalid_keys = False
        self._project_quota_message = ""
        self._stop_requested = False
        self.complete_calls = []

    def _flush_worker_progress(self):
        pass

    def _start_replacement_worker_if_possible(self):
        return False

    def _set_reading_controls_running(self, running):
        pass

    def refresh_chapters_list(self):
        pass

    def _load_script_for_chapter(self, idx):
        pass

    def on_chapter_done_ui(self, idx):
        pass

    def _refresh_runtime_controls(self):
        pass

    def _complete_reading_session(self, final_message):
        self.complete_calls.append(final_message)


def _make_ready_state(tmp_dir, chapter_index=2, total_tasks=2):
    output_paths = []
    tasks = []
    for i in range(total_tasks):
        path = os.path.join(tmp_dir, f"seg_{i:05d}.wav")
        with open(path, "w") as f:
            f.write("x")
        output_paths.append(path)
        tasks.append({"task_index": i, "output_path": path})
    return {
        "chapter_index": chapter_index,
        "total_tasks": total_tasks,
        "completed_count": total_tasks,
        "task_queue": None,
        "tasks": tasks,
        "worker_count": 2,
        "cancelled": False,
        "temp_dir": tmp_dir,
    }


class ParallelChapterCombineAsyncTests(unittest.TestCase):
    def setUp(self):
        _FakeParallelCombineWorker.instances.clear()

    def test_combine_worker_is_a_qthread_subclass(self):
        # Тест был честным по механике, но ничто не мешало
        # ParallelLiveChapterCombineWorker перестать быть QThread — подмена
        # класса в остальных тестах этого файла это не заметила бы.
        self.assertTrue(issubclass(reader.ParallelLiveChapterCombineWorker, QThread))

    def test_finalize_offloads_combine_to_background_worker_without_blocking(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            state = _make_ready_state(tmp_dir)
            harness = _ParallelLiveFinalizeHarness(state)

            with mock.patch.object(
                reader, "ParallelLiveChapterCombineWorker", _FakeParallelCombineWorker
            ), mock.patch.object(
                reader, "_combine_mp3_sequence"
            ) as combine_mock, mock.patch(
                # Реальный NotificationManager.show на macOS запускает osascript
                # через subprocess.Popen — не имеет отношения к проверяемому
                # дефекту, подменяем, чтобы тест не плодил внешние процессы.
                "gemini_translator.ui.notifications.NotificationManager.show"
            ):
                result = harness._finalize_parallel_live_chapter()

                # Главное: метод не дожидается склейки внутри себя (не блокирует
                # вызывающий Qt-слот) — combine-функция не вызвана напрямую,
                # а лишь опосредованно, через воркер (в его run(), который тут
                # не выполняется, т.к. start() подменён).
                combine_mock.assert_not_called()
                self.assertIsNone(result)

                self.assertEqual(len(_FakeParallelCombineWorker.instances), 1)
                worker = _FakeParallelCombineWorker.instances[0]
                self.assertTrue(worker.started)
                self.assertIs(harness._parallel_live_combine_worker, worker)

                # Major #1: пока склейка идёт (self.workers/combiner/tester_worker
                # уже пусты — как это и было в реальном дефекте), приложение
                # обязано считать себя занятым.
                self.assertTrue(harness._running_tasks_exist())
                self.assertGreaterEqual(harness.refresh_calls, 1)
                self.assertEqual(harness.session_messages, [])
                self.assertEqual(harness.chapter_done_calls, [])

                # Major #3: finished_signal эмитируется из тела run() ДО
                # фактического завершения потока — ссылка на воркер и признак
                # "занятости" не должны исчезать в этот момент, иначе QThread
                # может быть уничтожен ещё живым.
                worker.finished_signal.emit(
                    True, 2, "Глава 3 озвучена параллельно: 2 блок(ов), 2 воркер(ов)."
                )
                self.assertIs(harness._parallel_live_combine_worker, worker)
                self.assertTrue(harness._running_tasks_exist())
                self.assertEqual(harness.session_messages, [])
                self.assertEqual(harness.chapter_done_calls, [])
                self.assertFalse(worker.deleted_later)

                # Только теперь эмулируем настоящее завершение потока —
                # встроенный сигнал finished, приходящий ПОСЛЕ finished_signal.
                worker._running = False
                worker.finished.emit()

                self.assertIsNone(harness._parallel_live_combine_worker)
                self.assertFalse(harness._running_tasks_exist())
                self.assertTrue(worker.deleted_later)
                self.assertEqual(harness.chapter_done_calls, [2])
                self.assertEqual(
                    harness.session_messages,
                    ["Глава 3 озвучена параллельно: 2 блок(ов), 2 воркер(ов)."],
                )
                # Успешная склейка должна удалить temp_dir своей же сессии.
                self.assertFalse(os.path.isdir(tmp_dir))

    def test_new_session_state_survives_stale_combine_completion(self):
        # Major #2: пока идёт фоновая склейка ПЕРВОЙ главы, GUI отзывчив, и
        # пользователь успевает начать параллельную озвучку ВТОРОЙ главы —
        # свежий self._parallel_live_state не должен пострадать, когда (пусть
        # даже позже) придёт колбэк завершения ПЕРВОЙ склейки.
        import tempfile

        with tempfile.TemporaryDirectory() as first_tmp_dir, tempfile.TemporaryDirectory() as second_tmp_dir:
            first_state = _make_ready_state(first_tmp_dir, chapter_index=2, total_tasks=1)
            harness = _ParallelLiveFinalizeHarness(first_state)

            with mock.patch.object(
                reader, "ParallelLiveChapterCombineWorker", _FakeParallelCombineWorker
            ), mock.patch.object(reader, "_combine_mp3_sequence"), mock.patch(
                "gemini_translator.ui.notifications.NotificationManager.show"
            ):
                result = harness._finalize_parallel_live_chapter()
                self.assertIsNone(result)
                # Состояние сессии отвязывается сразу при уходе в фон — до того,
                # как в главном потоке успеет отработать что-то ещё.
                self.assertIsNone(harness._parallel_live_state)

                # Пользователь успевает запустить новую параллельную озвучку
                # главы 5 поверх ещё работающей фоновой склейки главы 3.
                second_state = _make_ready_state(second_tmp_dir, chapter_index=4, total_tasks=1)
                harness._parallel_live_state = second_state

                first_worker = _FakeParallelCombineWorker.instances[0]
                first_worker.finished_signal.emit(
                    True, 2, "Глава 3 озвучена параллельно: 1 блок(ов), 2 воркер(ов)."
                )
                first_worker._running = False
                first_worker.finished.emit()

                # Колбэк первой (уже неактуальной) склейки не должен трогать
                # temp_dir и state второй, ещё идущей сессии.
                self.assertIs(harness._parallel_live_state, second_state)
                self.assertTrue(os.path.isdir(second_tmp_dir))
                self.assertFalse(os.path.isdir(first_tmp_dir))
                self.assertEqual(harness.chapter_done_calls, [2])

    def test_running_tasks_exist_true_while_combine_worker_alive(self):
        # Major #1, воспроизведена изолированно на _running_tasks_exist
        # напрямую: self.workers/combiner/tester_worker пусты (как это уже
        # к моменту старта склейки), но фоновый комбайнер жив.
        harness = _ParallelLiveFinalizeHarness(None)
        harness._parallel_live_combine_worker = _FakeParallelCombineWorker(
            _FakeBookManager(), 0, [], 0, 1
        )
        harness._parallel_live_combine_worker.start()
        self.assertTrue(harness._running_tasks_exist())
        harness._parallel_live_combine_worker._running = False
        self.assertFalse(harness._running_tasks_exist())

    def test_combine_worker_run_body_calls_ffmpeg_and_marks_chapter_done(self):
        # Проверяем реальное тело run() воркера (а не мок) на файловой системе,
        # без реального ffmpeg — сама _combine_mp3_sequence подменена, чтобы не
        # тянуть внешний бинарник в юнит-тест.
        bm = _FakeBookManager()
        worker = reader.ParallelLiveChapterCombineWorker(bm, 4, ["a.wav", "b.wav"], 2, 3)
        received = []
        worker.finished_signal = _Signal()
        worker.finished_signal.connect(lambda *args: received.append(args))

        with mock.patch.object(reader, "_combine_mp3_sequence") as combine_mock:
            worker.run()

        combine_mock.assert_called_once_with(["a.wav", "b.wav"], "/fake/book/Ch5.mp3")
        self.assertEqual(bm.mark_done_calls, [4])
        self.assertEqual(len(received), 1)
        success, chapter_index, message = received[0]
        self.assertTrue(success)
        self.assertEqual(chapter_index, 4)
        self.assertIn("Глава 5", message)

    def test_combine_worker_run_reports_failure_without_marking_done(self):
        bm = _FakeBookManager()
        worker = reader.ParallelLiveChapterCombineWorker(bm, 0, ["a.wav"], 1, 1)
        received = []
        worker.finished_signal = _Signal()
        worker.finished_signal.connect(lambda *args: received.append(args))

        with mock.patch.object(reader, "_combine_mp3_sequence", side_effect=RuntimeError("ffmpeg упал")):
            worker.run()

        self.assertEqual(bm.mark_done_calls, [])
        self.assertEqual(len(received), 1)
        success, chapter_index, message = received[0]
        self.assertFalse(success)
        self.assertIn("ffmpeg упал", message)


class OnWorkerFinishedParallelContractTests(unittest.TestCase):
    """Minor-замечание рецензента #4 (вторая половина): ранний return в
    _on_worker_finished, когда финализация ушла в фон, ничем не покрыт."""

    def setUp(self):
        _FakeParallelCombineWorker.instances.clear()

    def test_early_return_when_finalize_offloads_to_background(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            state = _make_ready_state(tmp_dir, chapter_index=1, total_tasks=1)
            harness = _OnWorkerFinishedHarness(state, worker_id=0)

            with mock.patch.object(
                reader, "ParallelLiveChapterCombineWorker", _FakeParallelCombineWorker
            ), mock.patch.object(reader, "_combine_mp3_sequence"):
                harness._on_worker_finished(0)

            # _complete_reading_session не должен вызываться синхронно — иначе
            # сессия завершилась бы дважды: сразу и повторно из колбэка фоновой
            # склейки.
            self.assertEqual(harness.complete_calls, [])
            self.assertEqual(len(_FakeParallelCombineWorker.instances), 1)
            self.assertIs(
                harness._parallel_live_combine_worker,
                _FakeParallelCombineWorker.instances[0],
            )


if __name__ == "__main__":
    unittest.main()
