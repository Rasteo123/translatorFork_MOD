import unittest

from gemini_translator.api.errors import ValidationFailedError, WorkerAction
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


class _DummyWorker:
    def __init__(self):
        self.task_manager = _DummyTaskManager()
        self.events = []
        self.chunk_on_error = False

    def _post_event(self, name, data=None):
        self.events.append((name, data or {}))


class ErrorAnalyzerRetryTests(unittest.TestCase):
    def test_validation_errors_retry_beyond_default_total_limit(self):
        worker = _DummyWorker()
        analyzer = ErrorAnalyzer(worker)
        task_info = ("task-id", ("epub", "book.epub", "Text/ch.xhtml"))
        history = {"total_count": 4, "errors": {"VALIDATION": 4}}

        action, error_type, _ = analyzer.analyze_and_act(
            ValidationFailedError("invalid html"),
            task_info,
            history,
        )

        self.assertEqual(action, WorkerAction.RETRY_COUNTABLE)
        self.assertEqual(error_type.name, "VALIDATION")
        self.assertEqual(worker.task_manager.failures, ["VALIDATION"])

    def test_validation_errors_fail_on_sixth_invalid_response(self):
        worker = _DummyWorker()
        analyzer = ErrorAnalyzer(worker)
        task_info = ("task-id", ("epub", "book.epub", "Text/ch.xhtml"))
        history = {"total_count": 5, "errors": {"VALIDATION": 5}}

        action, error_type, _ = analyzer.analyze_and_act(
            ValidationFailedError("invalid html"),
            task_info,
            history,
        )

        self.assertEqual(action, WorkerAction.FAIL_PERMANENTLY)
        self.assertEqual(error_type.name, "VALIDATION")


if __name__ == "__main__":
    unittest.main()
