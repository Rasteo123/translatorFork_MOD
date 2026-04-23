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
        self.worker_progress = _Signal()
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


class _ReplacementHarness:
    _active_worker_target_count = reader.MainWindow._active_worker_target_count
    _next_replacement_worker_id = reader.MainWindow._next_replacement_worker_id
    _active_worker_api_keys = reader.MainWindow._active_worker_api_keys
    _active_required_model_ids = reader.MainWindow._active_required_model_ids
    _replacement_api_keys = reader.MainWindow._replacement_api_keys
    _connect_reader_worker_signals = reader.MainWindow._connect_reader_worker_signals
    _start_replacement_worker_if_possible = reader.MainWindow._start_replacement_worker_if_possible
    _flush_worker_progress = reader.MainWindow._flush_worker_progress
    _on_worker_finished = reader.MainWindow._on_worker_finished

    def __init__(self):
        self._active_manager_queue = queue.Queue()
        self._active_manager_queue.put(1)
        self._project_quota_message = ""
        self._parallel_live_state = None
        self._active_job_kind = "tts"
        self._active_reader_engine = "live"
        self._active_flash_run_mode = None
        self.workers = [_RunningWorker(0, "used-key")]
        self.worker_widgets = {}
        self._pending_worker_progress = {}
        self.spin_workers = _Spin(2)
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

    def statusBar(self):
        return self.status_bar

    def _get_available_api_keys(self, required_model_ids=None):
        return ["used-key", "new-key"]

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

    def _enqueue_worker_progress(self, *args):
        pass

    def on_chapter_done_ui(self, *args):
        pass

    def _on_invalid_worker_key(self, *args):
        pass

    def _on_quota_worker_key(self, *args):
        pass

    def _on_project_quota_worker(self, *args):
        pass


class ReaderKeyReplacementTests(unittest.TestCase):
    def setUp(self):
        _FakeWorker.instances.clear()

    def test_replacement_skips_active_key_and_continues_existing_queue(self):
        harness = _ReplacementHarness()

        with (
            mock.patch.object(reader, "DashboardRow", _FakeRow),
            mock.patch.object(reader, "GeminiWorker", _FakeWorker),
        ):
            started = harness._start_replacement_worker_if_possible()

        self.assertTrue(started)
        self.assertEqual(len(_FakeWorker.instances), 1)
        replacement = _FakeWorker.instances[0]
        self.assertEqual(replacement.worker_id, 1)
        self.assertEqual(replacement.api_key, "new-key")
        self.assertIs(replacement.manager_chapter_queue, harness._active_manager_queue)
        self.assertTrue(replacement.started)
        self.assertIn(replacement, harness.workers)

    def test_replacement_is_not_started_without_spare_keys(self):
        harness = _ReplacementHarness()
        harness._get_available_api_keys = lambda required_model_ids=None: ["used-key"]

        with (
            mock.patch.object(reader, "DashboardRow", _FakeRow),
            mock.patch.object(reader, "GeminiWorker", _FakeWorker),
        ):
            started = harness._start_replacement_worker_if_possible()

        self.assertFalse(started)
        self.assertEqual(_FakeWorker.instances, [])
        self.assertEqual(len(harness.workers), 1)

    def test_replacement_is_not_started_while_stop_is_requested(self):
        harness = _ReplacementHarness()
        harness._stop_requested = True

        with (
            mock.patch.object(reader, "DashboardRow", _FakeRow),
            mock.patch.object(reader, "GeminiWorker", _FakeWorker),
        ):
            started = harness._start_replacement_worker_if_possible()

        self.assertFalse(started)
        self.assertEqual(_FakeWorker.instances, [])
        self.assertEqual(len(harness.workers), 1)

    def test_finished_worker_is_replaced_before_queue_is_marked_stopped(self):
        harness = _ReplacementHarness()
        harness.spin_workers = _Spin(1)
        harness.worker_widgets = {0: _FakeRow(0)}
        harness._get_available_api_keys = lambda required_model_ids=None: ["new-key"]

        with (
            mock.patch.object(reader, "DashboardRow", _FakeRow),
            mock.patch.object(reader, "GeminiWorker", _FakeWorker),
        ):
            harness._on_worker_finished(0)

        self.assertEqual(len(_FakeWorker.instances), 1)
        replacement = _FakeWorker.instances[0]
        self.assertEqual(replacement.api_key, "new-key")
        self.assertEqual(harness.workers, [replacement])
        self.assertTrue(replacement.started)


if __name__ == "__main__":
    unittest.main()
