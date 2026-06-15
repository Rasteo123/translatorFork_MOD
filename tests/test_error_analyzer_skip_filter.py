import unittest

from gemini_translator.api.errors import (
    ContentFilterError,
    NetworkError,
    PartialGenerationError,
    WorkerAction,
)
from gemini_translator.core.worker_helpers.error_analyzer import ErrorAnalyzer


class _DummyTaskManager:
    def __init__(self):
        self.failures = []

    def _get_task_display_name(self, payload):
        return payload[2] if len(payload) > 2 else str(payload)

    def record_failure(self, task_info, error_type):
        self.failures.append(error_type)

    def get_failure_history(self, task_info):
        counts = {}
        for error_type in self.failures:
            counts[error_type] = counts.get(error_type, 0) + 1
        return {"total_count": len(self.failures), "errors": counts}


class _DummyRpmLimiter:
    def update_last_request_time(self, delay):
        pass

    def get_rpm(self):
        return 10

    def decrease_rpm(self, percentage=0):
        pass


class _DummyWorker:
    def __init__(self, skip_content_filter_retry=False):
        self.task_manager = _DummyTaskManager()
        self.events = []
        self.chunking = False
        self.chunk_on_error = False
        self.skip_content_filter_retry = skip_content_filter_retry
        self.rpm_limiter = _DummyRpmLimiter()

    def _post_event(self, name, data=None):
        self.events.append((name, data or {}))


TASK = ("task-id", ("epub", "book.epub", "Text/ch.xhtml"))
EMPTY = {"total_count": 0, "errors": {}}


class SkipContentFilterRetryTests(unittest.TestCase):
    def test_content_filter_fails_immediately_when_flag_on(self):
        worker = _DummyWorker(skip_content_filter_retry=True)
        action, error_type, _ = ErrorAnalyzer(worker).analyze_and_act(
            ContentFilterError("blocked"), TASK, EMPTY
        )
        self.assertEqual(action, WorkerAction.FAIL_PERMANENTLY)
        self.assertEqual(error_type.name, "CONTENT_FILTER")
        self.assertIn("CONTENT_FILTER", worker.task_manager.failures)

    def test_partial_safety_with_tail_fails_immediately_when_flag_on(self):
        worker = _DummyWorker(skip_content_filter_retry=True)
        action, error_type, _ = ErrorAnalyzer(worker).analyze_and_act(
            PartialGenerationError("interrupted", "<p>часть</p>", "SAFETY"), TASK, EMPTY
        )
        self.assertEqual(action, WorkerAction.FAIL_PERMANENTLY)
        self.assertEqual(error_type.name, "CONTENT_FILTER")

    def test_partial_prohibited_empty_tail_fails_immediately_when_flag_on(self):
        worker = _DummyWorker(skip_content_filter_retry=True)
        action, error_type, _ = ErrorAnalyzer(worker).analyze_and_act(
            PartialGenerationError("interrupted", "", "PROHIBITED_CONTENT"), TASK, EMPTY
        )
        self.assertEqual(action, WorkerAction.FAIL_PERMANENTLY)
        self.assertEqual(error_type.name, "CONTENT_FILTER")

    def test_content_filter_still_retries_when_flag_off(self):
        worker = _DummyWorker(skip_content_filter_retry=False)
        action, _, _ = ErrorAnalyzer(worker).analyze_and_act(
            ContentFilterError("blocked"), TASK, EMPTY
        )
        self.assertEqual(action, WorkerAction.RETRY_COUNTABLE)

    def test_network_error_unaffected_by_flag(self):
        worker = _DummyWorker(skip_content_filter_retry=True)
        action, error_type, _ = ErrorAnalyzer(worker).analyze_and_act(
            NetworkError("conn reset"), TASK, EMPTY
        )
        self.assertEqual(action, WorkerAction.RETRY_COUNTABLE)
        self.assertEqual(error_type.name, "NETWORK")


if __name__ == "__main__":
    unittest.main()
