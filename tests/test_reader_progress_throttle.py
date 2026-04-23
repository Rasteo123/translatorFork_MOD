import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gemini_reader_v3 import GeminiWorker


class _SignalSpy:
    def __init__(self):
        self.calls = []

    def emit(self, *args):
        self.calls.append(args)


class _WorkerProgressHarness:
    _emit_worker_progress = GeminiWorker._emit_worker_progress

    def __init__(self):
        self.worker_id = 7
        self.worker_progress = _SignalSpy()
        self._last_progress_emit_payload = None
        self._last_progress_emit_at = 0.0


class _WorkerCrashHarness:
    run = GeminiWorker.run
    _emit_finished = GeminiWorker._emit_finished

    def __init__(self):
        self.worker_id = 9
        self.error_signal = _SignalSpy()
        self.finished_signal = _SignalSpy()
        self._is_running = True
        self._finished_emitted = False

    async def main_loop(self):
        raise RuntimeError("boom")


class ReaderProgressThrottleTests(unittest.TestCase):
    def test_duplicate_progress_payload_is_suppressed(self):
        harness = _WorkerProgressHarness()

        harness._emit_worker_progress(3, 10, 100)
        harness._emit_worker_progress(3, 10, 100)

        self.assertEqual(len(harness.worker_progress.calls), 1)
        self.assertEqual(harness.worker_progress.calls[0], (7, 3, 10, 100))

    def test_force_progress_bypasses_throttle(self):
        harness = _WorkerProgressHarness()

        harness._emit_worker_progress(3, 100, 100)
        harness._emit_worker_progress(3, 100, 100, force=True)

        self.assertEqual(len(harness.worker_progress.calls), 2)

    def test_worker_crash_still_emits_finished_once(self):
        harness = _WorkerCrashHarness()

        harness.run()
        harness._emit_finished()

        self.assertEqual(len(harness.error_signal.calls), 1)
        self.assertIn("CRASH: boom", harness.error_signal.calls[0][1])
        self.assertEqual(harness.finished_signal.calls, [(9,)])


if __name__ == "__main__":
    unittest.main()
