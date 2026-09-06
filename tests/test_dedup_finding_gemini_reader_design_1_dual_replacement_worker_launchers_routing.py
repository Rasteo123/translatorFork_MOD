"""Тесты для dedup-finding
finding-gemini-reader_design_1-dual-replacement-worker-launchers.

До рефакторинга в gemini_reader_v3.MainWindow существовало два независимых
механизма запуска replacement-воркера:

* ``_start_replacement_worker_if_possible`` (вызывается из
  ``_on_worker_finished``) — с полным набором защитных проверок
  (``_stop_requested``, ``_project_quota_message``,
  ``parallel_live_state['cancelled']``, лимит ``_active_worker_target_count``).
* ``_start_replacement_worker`` (вызывается из ``_on_quota_worker_key``) —
  без единой из этих проверок.

Канонической реализацией становится ``_start_replacement_worker_if_possible``;
``_on_quota_worker_key`` должен вызывать её же. Поскольку в момент вызова
``_on_quota_worker_key`` воркер, упёршийся в лимit, ещё не удалён из
``self.workers`` (удаление происходит позже, в ``_on_worker_finished``, когда
придёт нативный Qt-сигнал ``finished``), канонический метод обязан уметь
исключить этот умирающий воркер из подсчёта занятых слотов — иначе проверка
лимита воркеров будет почти всегда блокировать замену по квоте, что было бы
регрессией уже работающей функциональности.
"""

import os
import queue
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gemini_reader_v3 as reader


class _Signal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)


class _FakeWorker:
    instances = []

    def __init__(
        self,
        worker_id,
        api_key,
        bm,
        audio_queue,
        model_id,
        voice,
        style_prompt,
        speed,
        record,
        fast,
        chunk,
        segment_mode,
        manager_chapter_queue,
        **kwargs,
    ):
        self.worker_id = worker_id
        self.api_key = api_key
        self.manager_chapter_queue = manager_chapter_queue
        self.started = False
        self.start_stagger_index = worker_id
        self.worker_progress = _Signal()
        self.finished = _Signal()
        self.finished_signal = _Signal()
        self.chapter_done_ui_signal = _Signal()
        self.invalid_key_signal = _Signal()
        self.quota_key_signal = _Signal()
        self.project_quota_signal = _Signal()
        self.error_signal = _Signal()
        _FakeWorker.instances.append(self)

    def start(self):
        self.started = True


class _FakeRow:
    def __init__(self, worker_id):
        self.worker_id = worker_id
        self.parent = object()

    def setParent(self, parent):
        self.parent = parent


class _Layout:
    def __init__(self):
        self.widgets = []

    def addWidget(self, widget):
        self.widgets.append(widget)


class _StatusBar:
    def __init__(self):
        self.messages = []

    def showMessage(self, message):
        self.messages.append(message)


class _Spin:
    def __init__(self, value):
        self._value = value

    def value(self):
        return self._value


class _Check:
    def __init__(self, checked):
        self._checked = checked

    def isChecked(self):
        return self._checked


class _Combo:
    def __init__(self, data=None, text=None):
        self._data = data
        self._text = text if text is not None else data

    def currentData(self):
        return self._data

    def currentText(self):
        return self._text


class _RunningWorker:
    def __init__(self, worker_id, api_key):
        self.worker_id = worker_id
        self.api_key = api_key


class _Harness:
    """Мини-MainWindow: только то, что нужно
    ``_start_replacement_worker_if_possible``/``_on_quota_worker_key``."""

    _active_worker_target_count = reader.MainWindow._active_worker_target_count
    _next_replacement_worker_id = reader.MainWindow._next_replacement_worker_id
    _active_worker_api_keys = reader.MainWindow._active_worker_api_keys
    _active_required_model_ids = reader.MainWindow._active_required_model_ids
    _replacement_api_keys = reader.MainWindow._replacement_api_keys
    _connect_reader_worker_signals = reader.MainWindow._connect_reader_worker_signals
    _add_dashboard_row = reader.MainWindow._add_dashboard_row
    _build_live_worker = reader.MainWindow._build_live_worker
    _start_replacement_worker_if_possible = reader.MainWindow._start_replacement_worker_if_possible
    _on_quota_worker_key = reader.MainWindow._on_quota_worker_key

    def __init__(
        self,
        workers=None,
        stop_requested=False,
        project_quota_message="",
        parallel_live_state=None,
        spin_workers_value=2,
        available_keys=("used-key", "new-key"),
    ):
        self._active_manager_queue = queue.Queue()
        self._active_manager_queue.put(1)
        self._project_quota_message = project_quota_message
        self._parallel_live_state = parallel_live_state
        self._active_job_kind = "tts"
        self._active_reader_engine = "live"
        self._active_flash_run_mode = None
        self._stop_requested = stop_requested
        self.workers = workers if workers is not None else [_RunningWorker(0, "used-key")]
        self.worker_widgets = {}
        self._pending_worker_progress = {}
        self.spin_workers = _Spin(spin_workers_value)
        self.dash_layout = _Layout()
        self.status_bar = _StatusBar()
        self.bm = object()
        self.audio_queue = queue.Queue()
        self.player = None
        self.daily_request_limiter = None
        self.combo_voices = _Combo("Puck")
        self.combo_voice_secondary = _Combo("Kore")
        self.combo_voice_tertiary = _Combo("Charon")
        self.combo_speed = _Combo(text="Normal")
        self.chk_mp3 = _Check(True)
        self.chk_fast = _Check(True)
        self.chk_edge_fallback = _Check(False)
        self.spin_chunk = _Spin(2)
        self.disabled_api_keys = set()
        self._run_had_invalid_keys = False
        self.settings_manager = None
        self._available_keys = list(available_keys)

    def statusBar(self):
        return self.status_bar

    def _get_available_api_keys(self, required_model_ids=None):
        return [key for key in self._available_keys if key not in self.disabled_api_keys]

    def _selected_model_id(self):
        return "model-live"

    def _selected_live_segment_mode(self):
        return "sentences"

    def _selected_voice_mode(self):
        return "single"

    def _selected_pipeline_mode(self):
        return "auto"

    def _selected_preprocess_model_id(self):
        return "model-pre"

    def _update_worker_spinbox_limit(self, *args):
        pass

    def _update_key_state_ui(self):
        pass

    def _enqueue_worker_progress(self, *args):
        pass

    def on_chapter_done_ui(self, *args):
        pass

    def _on_invalid_worker_key(self, *args):
        pass

    def _on_project_quota_worker(self, *args):
        pass


class QuotaReplacementRoutingTests(unittest.TestCase):
    """Тест-маршрутизация: _on_quota_worker_key обязан идти через
    каноническую _start_replacement_worker_if_possible.

    До рефакторинга ``_on_quota_worker_key`` вызывал отдельный
    ``_start_replacement_worker`` — этот тест должен ПАДАТЬ на старом коде и
    ПРОХОДИТЬ после того, как вызов будет переведён на канонический метод.
    """

    def setUp(self):
        _FakeWorker.instances.clear()

    def test_on_quota_worker_key_routes_through_canonical_launcher(self):
        harness = _Harness()

        with mock.patch.object(
            _Harness, "_start_replacement_worker_if_possible", return_value="new-key"
        ) as fake_launcher:
            harness._on_quota_worker_key(0, "used-key", "model-live", "quota hit", 3)

        fake_launcher.assert_called_once()
        # Умирающий воркер (worker_id=0) ещё не удалён из self.workers на
        # момент вызова — канонический метод должен получить его id, чтобы
        # корректно исключить из подсчёта занятых слотов.
        _, kwargs = fake_launcher.call_args
        self.assertEqual(kwargs.get("excluded_worker_id"), 0)


class QuotaReplacementCharacterizationTests(unittest.TestCase):
    """Характеризационные тесты канонического поведения после объединения."""

    def setUp(self):
        _FakeWorker.instances.clear()

    def test_quota_replacement_blocked_when_stop_requested(self):
        harness = _Harness(stop_requested=True)

        with (
            mock.patch.object(reader, "DashboardRow", _FakeRow),
            mock.patch.object(reader, "GeminiWorker", _FakeWorker),
        ):
            harness._on_quota_worker_key(0, "used-key", "model-live", "quota hit", 3)

        self.assertEqual(_FakeWorker.instances, [])
        self.assertIn("свободной замены нет", harness.status_bar.messages[-1])

    def test_quota_replacement_blocked_when_project_quota_exhausted(self):
        harness = _Harness(project_quota_message="Дневной лимит проекта исчерпан.")

        with (
            mock.patch.object(reader, "DashboardRow", _FakeRow),
            mock.patch.object(reader, "GeminiWorker", _FakeWorker),
        ):
            harness._on_quota_worker_key(0, "used-key", "model-live", "quota hit", 3)

        self.assertEqual(_FakeWorker.instances, [])
        self.assertIn("свободной замены нет", harness.status_bar.messages[-1])

    def test_quota_replacement_blocked_when_parallel_live_cancelled(self):
        harness = _Harness(parallel_live_state={"cancelled": True})

        with (
            mock.patch.object(reader, "DashboardRow", _FakeRow),
            mock.patch.object(reader, "GeminiWorker", _FakeWorker),
        ):
            harness._on_quota_worker_key(0, "used-key", "model-live", "quota hit", 3)

        self.assertEqual(_FakeWorker.instances, [])
        self.assertIn("свободной замены нет", harness.status_bar.messages[-1])

    def test_quota_replacement_spawns_when_dying_worker_excluded_from_capacity(self):
        # Лимит воркеров = 1, единственный воркер как раз упёрся в квоту.
        # Он ещё числится в self.workers (реальное удаление произойдёт позже,
        # когда придёт нативный сигнал QThread.finished), но он не должен
        # мешать замене — иначе замена по квоте никогда бы не срабатывала
        # при работе на полном пуле воркеров.
        harness = _Harness(
            workers=[_RunningWorker(0, "used-key")],
            spin_workers_value=1,
        )

        with (
            mock.patch.object(reader, "DashboardRow", _FakeRow),
            mock.patch.object(reader, "GeminiWorker", _FakeWorker),
        ):
            harness._on_quota_worker_key(0, "used-key", "model-live", "quota hit", 3)

        self.assertEqual(len(_FakeWorker.instances), 1)
        replacement = _FakeWorker.instances[0]
        self.assertEqual(replacement.api_key, "new-key")
        self.assertTrue(replacement.started)
        self.assertEqual(replacement.start_stagger_index, 0)
        self.assertIn(replacement, harness.workers)
        self.assertIn("замена: ", harness.status_bar.messages[-1])

    def test_quota_replacement_blocked_when_still_at_capacity_excluding_dying_worker(self):
        # Лимит воркеров = 1, но помимо умирающего воркера уже есть другой
        # активный воркер — свободного слота нет даже без учёта умирающего.
        harness = _Harness(
            workers=[_RunningWorker(0, "used-key"), _RunningWorker(1, "other-key")],
            spin_workers_value=1,
            available_keys=("used-key", "other-key", "new-key"),
        )

        with (
            mock.patch.object(reader, "DashboardRow", _FakeRow),
            mock.patch.object(reader, "GeminiWorker", _FakeWorker),
        ):
            harness._on_quota_worker_key(0, "used-key", "model-live", "quota hit", 3)

        self.assertEqual(_FakeWorker.instances, [])
        self.assertIn("свободной замены нет", harness.status_bar.messages[-1])


if __name__ == "__main__":
    unittest.main()
