# tests/test_content_filter_fallback_run.py
import asyncio
import unittest

from gemini_translator.api.errors import ContentFilterError, NetworkError
from gemini_translator.core.worker_helpers import content_filter_fallback as cff
from gemini_translator.core.worker_helpers.provider_orchestrator import (
    ProviderAttemptResult,
)


class FakeSettings:
    def load_key_statuses(self):
        return [
            {"key": "g1", "provider": "gemini"},
            {"key": "g2", "provider": "gemini"},
            {"key": "n1", "provider": "nvidia"},
        ]

    def is_key_limit_active(self, key_info, model_id):
        return False


class FakeWorker:
    def __init__(self):
        self.settings_manager = FakeSettings()
        self.content_filter_fallback_enabled = True
        self.content_filter_fallback_provider = "gemini"
        self.content_filter_fallback_model = "gemini-2.0-flash"
        self.content_filter_fallback_temperature = 0.4
        self.content_filter_fallback_temperature_override = True
        self.content_filter_fallback_thinking_enabled = False
        self.content_filter_fallback_thinking_budget = None
        self.content_filter_fallback_thinking_level = None
        self.content_filter_fallback_second_enabled = False
        self.content_filter_fallback_second_provider = "nvidia"
        self.content_filter_fallback_second_model = "second-model"
        self.content_filter_fallback_second_temperature = 0.7
        self.content_filter_fallback_second_temperature_override = True
        self.content_filter_fallback_second_thinking_enabled = False
        self.content_filter_fallback_second_thinking_budget = None
        self.content_filter_fallback_second_thinking_level = None
        self.logs = []

    def _post_event(self, name, payload):
        self.logs.append((name, payload.get("message")))


def _run(coro):
    return asyncio.run(coro)


class RunFallbackTests(unittest.TestCase):
    def setUp(self):
        self._orig_resolve = cff._resolve_model
        self._orig_run = cff._run_attempt
        cff._resolve_model = lambda pid, name: (name, {"id": name, "provider": pid})

    def tearDown(self):
        cff._resolve_model = self._orig_resolve
        cff._run_attempt = self._orig_run

    def test_no_green_keys_raises(self):
        worker = FakeWorker()
        worker.settings_manager.load_key_statuses = lambda: []

        async def fake_run(*a, **k):
            raise AssertionError("should not be called")

        cff._run_attempt = fake_run
        with self.assertRaises(cff.NoFallbackKeysError):
            _run(self._call(worker))

    def test_success_returns_text(self):
        worker = FakeWorker()
        seen = {}

        async def fake_run(w, attempt, prompt, log_prefix, call_kwargs):
            seen["attempt"] = attempt
            return ProviderAttemptResult(attempt=attempt, text="TRANSLATED")

        cff._run_attempt = fake_run
        out = _run(self._call(worker))
        self.assertEqual(out, "TRANSLATED")
        self.assertEqual(seen["attempt"].provider_id, "gemini")
        self.assertEqual(seen["attempt"].api_key, "g1")
        self.assertAlmostEqual(seen["attempt"].temperature, 0.4)

    def test_fallback_block_reraises(self):
        worker = FakeWorker()

        async def fake_run(w, attempt, prompt, log_prefix, call_kwargs):
            return ProviderAttemptResult(
                attempt=attempt, error="blocked", exception=ContentFilterError("x")
            )

        cff._run_attempt = fake_run
        with self.assertRaises(ContentFilterError):
            _run(self._call(worker))

    def test_second_fallback_runs_only_after_first_content_block(self):
        worker = FakeWorker()
        worker.content_filter_fallback_second_enabled = True
        calls = []

        async def fake_run(w, attempt, prompt, log_prefix, call_kwargs):
            calls.append(attempt)
            if attempt.provider_id == "gemini":
                return ProviderAttemptResult(
                    attempt=attempt, error="blocked", exception=ContentFilterError("x")
                )
            return ProviderAttemptResult(attempt=attempt, text="SECOND_OK")

        cff._run_attempt = fake_run
        self.assertEqual(_run(self._call(worker)), "SECOND_OK")
        self.assertEqual([a.provider_id for a in calls], ["gemini", "nvidia"])
        self.assertEqual(calls[1].api_key, "n1")
        self.assertAlmostEqual(calls[1].temperature, 0.7)

    def test_second_fallback_does_not_run_after_first_fatal_error(self):
        worker = FakeWorker()
        worker.content_filter_fallback_second_enabled = True
        calls = []

        async def fake_run(w, attempt, prompt, log_prefix, call_kwargs):
            calls.append(attempt.provider_id)
            return ProviderAttemptResult(
                attempt=attempt, error="fatal", exception=ValueError("fatal")
            )

        cff._run_attempt = fake_run
        with self.assertRaises(ValueError):
            _run(self._call(worker))
        self.assertEqual(calls, ["gemini"])

    def test_second_content_block_is_final(self):
        worker = FakeWorker()
        worker.content_filter_fallback_second_enabled = True
        calls = []

        async def fake_run(w, attempt, prompt, log_prefix, call_kwargs):
            calls.append(attempt.provider_id)
            return ProviderAttemptResult(
                attempt=attempt,
                error="blocked",
                exception=ContentFilterError(attempt.provider_id),
            )

        cff._run_attempt = fake_run
        with self.assertRaisesRegex(ContentFilterError, "nvidia"):
            _run(self._call(worker))
        self.assertEqual(calls, ["gemini", "nvidia"])

    def test_transient_rotates_then_succeeds(self):
        worker = FakeWorker()
        calls = []

        async def fake_run(w, attempt, prompt, log_prefix, call_kwargs):
            calls.append(attempt.api_key)
            if len(calls) == 1:
                return ProviderAttemptResult(
                    attempt=attempt, error="net", exception=NetworkError("temp")
                )
            return ProviderAttemptResult(attempt=attempt, text="OK")

        cff._run_attempt = fake_run
        out = _run(self._call(worker))
        self.assertEqual(out, "OK")
        self.assertEqual(calls, ["g1", "g2"])

    def _call(self, worker):
        return cff.run_content_filter_fallback(
            worker,
            "PROMPT",
            "[Test]",
            task_info=("tid", ("epub_chunk",)),
            operation_context={},
            call_kwargs={"use_stream": False},
        )


if __name__ == "__main__":
    unittest.main()
