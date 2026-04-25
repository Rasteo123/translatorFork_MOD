import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gemini_reader_v3 as reader


class _Signal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def emit(self, *args):
        for slot in list(self.slots):
            slot(*args)


class _FakeAudioCombinerWorker:
    instances = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.progress_signal = _Signal()
        self.finished_signal = _Signal()
        self.finished = _Signal()
        self.started = False
        self.deleted_later = False
        _FakeAudioCombinerWorker.instances.append(self)

    def start(self):
        self.started = True

    def deleteLater(self):
        self.deleted_later = True


class _StatusBar:
    def __init__(self):
        self.messages = []

    def showMessage(self, message):
        self.messages.append(message)


class _Check:
    def __init__(self, checked=False):
        self._checked = checked

    def isChecked(self):
        return self._checked


class _Combo:
    def __init__(self, data):
        self._data = data

    def currentData(self):
        return self._data


class _CombinerHarness:
    _start_audio_combiner = reader.MainWindow._start_audio_combiner

    def __init__(self):
        self.bm = object()
        self.combo_voices = _Combo("Puck")
        self.chk_selected_only = _Check(False)
        self.combiner = None
        self.status_bar = _StatusBar()
        self.refresh_count = 0

    def statusBar(self):
        return self.status_bar

    def _refresh_runtime_controls(self):
        self.refresh_count += 1


class ReaderVideoExportLifecycleTests(unittest.TestCase):
    def setUp(self):
        _FakeAudioCombinerWorker.instances.clear()

    def test_combiner_reference_is_kept_until_qthread_finished(self):
        harness = _CombinerHarness()

        with (
            mock.patch.object(reader, "_resolve_tool_path", return_value="tool-test"),
            mock.patch.object(reader, "AudioCombinerWorker", _FakeAudioCombinerWorker),
            mock.patch.object(reader.QMessageBox, "information") as information,
        ):
            harness._start_audio_combiner(video_image_path="cover.png")

            worker = _FakeAudioCombinerWorker.instances[0]
            self.assertIs(harness.combiner, worker)
            self.assertTrue(worker.started)

            worker.finished_signal.emit("Done")
            self.assertIs(harness.combiner, worker)
            information.assert_not_called()

            worker.finished.emit()

        self.assertIsNone(harness.combiner)
        self.assertTrue(worker.deleted_later)
        information.assert_called_once_with(harness, "Видео", "Done")
        self.assertGreaterEqual(harness.refresh_count, 2)


if __name__ == "__main__":
    unittest.main()
