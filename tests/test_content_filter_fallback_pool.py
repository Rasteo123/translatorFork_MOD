# tests/test_content_filter_fallback_pool.py
import unittest

from gemini_translator.api.errors import (
    ContentFilterError,
    NetworkError,
    PartialGenerationError,
    RateLimitExceededError,
    TemporaryRateLimitError,
)
from gemini_translator.core.worker_helpers import content_filter_fallback as cff


class FakeSettings:
    def __init__(self, statuses, blocked_keys=()):
        self._statuses = statuses
        self._blocked = set(blocked_keys)

    def load_key_statuses(self):
        return list(self._statuses)

    def is_key_limit_active(self, key_info, model_id):
        return key_info["key"] in self._blocked


class GreenPoolTests(unittest.TestCase):
    def test_filters_by_provider_and_excludes_blocked(self):
        settings = FakeSettings(
            statuses=[
                {"key": "g1", "provider": "gemini"},
                {"key": "g2", "provider": "gemini"},
                {"key": "n1", "provider": "nvidia"},
                {"key": "g3", "provider": "gemini"},
            ],
            blocked_keys=["g2"],
        )
        pool = cff.green_keys_for_provider(settings, "gemini", "model-x")
        self.assertEqual(pool, ["g1", "g3"])

    def test_empty_when_no_settings_manager(self):
        self.assertEqual(cff.green_keys_for_provider(None, "gemini", "m"), [])

    def test_empty_when_no_keys_for_provider(self):
        settings = FakeSettings(statuses=[{"key": "n1", "provider": "nvidia"}])
        self.assertEqual(cff.green_keys_for_provider(settings, "gemini", "m"), [])


class ClassifierTests(unittest.TestCase):
    def test_content_block_detection(self):
        self.assertTrue(cff.is_content_block_exception(ContentFilterError("x")))
        self.assertTrue(
            cff.is_content_block_exception(PartialGenerationError("x", "", "SAFETY"))
        )
        self.assertTrue(
            cff.is_content_block_exception(
                PartialGenerationError("x", "", "prohibited_content")
            )
        )
        self.assertFalse(
            cff.is_content_block_exception(PartialGenerationError("x", "tail", "OTHER"))
        )
        self.assertFalse(cff.is_content_block_exception(NetworkError("x")))

    def test_content_filter_reason_from_openai_compatible_handlers_is_a_block(self):
        # OpenRouterApiHandler reports ``finish_reason: "content_filter"`` this way.
        self.assertTrue(
            cff.is_content_block_exception(PartialGenerationError("x", "tail", "CONTENT_FILTER"))
        )

    def test_transient_detection(self):
        self.assertTrue(cff.is_transient_exception(NetworkError("x")))
        self.assertTrue(cff.is_transient_exception(RateLimitExceededError("x")))
        self.assertTrue(cff.is_transient_exception(TemporaryRateLimitError("x")))
        self.assertFalse(cff.is_transient_exception(ContentFilterError("x")))

    def test_decision(self):
        self.assertEqual(cff.fallback_decision(ContentFilterError("x")), "block")
        self.assertEqual(cff.fallback_decision(NetworkError("x")), "transient")
        self.assertEqual(cff.fallback_decision(ValueError("x")), "fatal")


class SyncLoopTests(unittest.TestCase):
    def test_returns_first_success(self):
        calls = []

        def call_for_key(key):
            calls.append(key)
            return f"ok:{key}"

        out = cff.run_sync_fallback_loop(pool=["a", "b"], call_for_key=call_for_key)
        self.assertEqual(out, "ok:a")
        self.assertEqual(calls, ["a"])

    def test_content_block_raises_immediately(self):
        def call_for_key(key):
            raise ContentFilterError("blocked")

        with self.assertRaises(ContentFilterError):
            cff.run_sync_fallback_loop(pool=["a", "b"], call_for_key=call_for_key)

    def test_transient_rotates_then_succeeds(self):
        calls = []

        def call_for_key(key):
            calls.append(key)
            if len(calls) == 1:
                raise NetworkError("temp")
            return "recovered"

        out = cff.run_sync_fallback_loop(pool=["a", "b"], call_for_key=call_for_key)
        self.assertEqual(out, "recovered")
        self.assertEqual(calls, ["a", "b"])

    def test_empty_pool_raises_no_fallback_keys(self):
        with self.assertRaises(cff.NoFallbackKeysError):
            cff.run_sync_fallback_loop(pool=[], call_for_key=lambda k: "x")

    def test_temporary_rate_limit_rotates(self):
        calls = []

        def call_for_key(key):
            calls.append(key)
            if len(calls) == 1:
                raise TemporaryRateLimitError("slow down")
            return "ok"

        out = cff.run_sync_fallback_loop(pool=["a", "b"], call_for_key=call_for_key)
        self.assertEqual(out, "ok")
        self.assertEqual(calls, ["a", "b"])


if __name__ == "__main__":
    unittest.main()
