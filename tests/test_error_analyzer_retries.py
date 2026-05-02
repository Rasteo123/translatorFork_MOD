import os
import tempfile
import unittest
import zipfile

from gemini_translator.api.errors import PartialGenerationError, ValidationFailedError, WorkerAction
from gemini_translator.core.worker_helpers.error_analyzer import ErrorAnalyzer
from gemini_translator.core.worker_helpers.emerger_tasks import EmergencyTask


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
        self.chunking = False
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

    def test_partial_completion_does_not_create_chunk_when_chunk_options_are_disabled(self):
        worker = _DummyWorker()
        emerger = EmergencyTask(worker)
        task_info = ("task-id", ("epub", "missing.epub", "Text/ch.xhtml"))
        exc = PartialGenerationError("partial", "<p>translated</p>", "MAX_TOKENS")

        mutated = emerger._mutate_task_for_completion(task_info, exc)

        self.assertEqual(mutated, task_info)
        self.assertIn("epub_chunk", worker.events[-1][1]["message"])

    def test_partial_completion_still_uses_chunk_when_chunk_on_error_is_enabled(self):
        with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as epub_file:
            epub_path = epub_file.name
        self.addCleanup(lambda: os.path.exists(epub_path) and os.remove(epub_path))
        with zipfile.ZipFile(epub_path, "w") as epub_zip:
            epub_zip.writestr("Text/ch.xhtml", "<html><body><p>source</p></body></html>")

        worker = _DummyWorker()
        worker.chunk_on_error = True
        emerger = EmergencyTask(worker)
        task_info = ("task-id", ("epub", epub_path, "Text/ch.xhtml"))
        exc = PartialGenerationError("partial", "<p>translated</p>", "MAX_TOKENS")

        task_id, payload = emerger._mutate_task_for_completion(task_info, exc)

        self.assertEqual(task_id, "task-id")
        self.assertEqual(payload[0], "epub_chunk")
        self.assertEqual(payload[5], 1)
        self.assertEqual(payload[-1], "<p>translated</p>")


if __name__ == "__main__":
    unittest.main()
