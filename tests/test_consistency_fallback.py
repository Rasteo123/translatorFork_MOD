# tests/test_consistency_fallback.py
import unittest

from gemini_translator.api.errors import ContentFilterError, NetworkError
from gemini_translator.core import consistency_engine as ce
from gemini_translator.core.consistency_engine import ConsistencyEngine


class FakeSettings:
    def load_key_statuses(self):
        return [
            {"key": "g1", "provider": "nvidia"},
            {"key": "g2", "provider": "nvidia"},
            {"key": "s1", "provider": "second"},
        ]

    def is_key_limit_active(self, key_info, model_id):
        return False


class FakeEngine:
    """Minimal stand-in exposing only what the fallback method touches."""

    def __init__(self):
        self.settings_manager = FakeSettings()
        self.logs = []
        self.calls = []
        self._script = []

    def _emit_log_message(self, msg):
        self.logs.append(msg)

    def _call_api_with_cached_handler(self, prompt, config, api_key):
        self.calls.append((api_key, config.get("_is_fallback_attempt")))
        action = self._script.pop(0)
        if isinstance(action, Exception):
            raise action
        return action


class ConsistencyFallbackTests(unittest.TestCase):
    def setUp(self):
        self._orig = ce._load_providers_config
        ce._load_providers_config = lambda: {
            "nvidia": {"handler_class": "X", "models": {"big": {"id": "big"}}},
            "second": {"handler_class": "X", "models": {"small": {"id": "small"}}},
        }

    def tearDown(self):
        ce._load_providers_config = self._orig

    def _config(self):
        return {
            "content_filter_fallback_enabled": True,
            "content_filter_fallback_provider": "nvidia",
            "content_filter_fallback_model": "big",
            "content_filter_fallback_temperature": 0.3,
        }

    def test_success_returns_text_with_guard_flag(self):
        eng = FakeEngine()
        eng._script = ["FALLBACK_OK"]
        out = ConsistencyEngine._run_consistency_content_filter_fallback(
            eng, "PROMPT", self._config()
        )
        self.assertEqual(out, "FALLBACK_OK")
        self.assertEqual(eng.calls, [("g1", True)])

    def test_real_engine_logs_with_existing_signal_method(self):
        eng = ConsistencyEngine.__new__(ConsistencyEngine)
        eng.settings_manager = FakeSettings()
        eng._handler_cache = {}
        eng.log_message = type(
            "Signal",
            (),
            {
                "__init__": lambda self: setattr(self, "messages", []),
                "emit": lambda self, msg: self.messages.append(msg),
            },
        )()
        calls = []

        def fake_call(prompt, config, api_key):
            calls.append((api_key, config.get("_is_fallback_attempt")))
            return "FALLBACK_OK"

        eng._call_api_with_cached_handler = fake_call

        out = eng._run_consistency_content_filter_fallback("PROMPT", self._config())

        self.assertEqual(out, "FALLBACK_OK")
        self.assertEqual(calls, [("g1", True)])
        self.assertTrue(eng.log_message.messages)

    def test_fallback_block_reraises(self):
        eng = FakeEngine()
        eng._script = [ContentFilterError("blocked")]
        with self.assertRaises(ContentFilterError):
            ConsistencyEngine._run_consistency_content_filter_fallback(
                eng, "PROMPT", self._config()
            )

    def test_second_fallback_after_first_block(self):
        eng = FakeEngine()
        eng._script = [ContentFilterError("first blocked"), "SECOND_OK"]
        config = self._config()
        config.update({
            "content_filter_fallback_second_enabled": True,
            "content_filter_fallback_second_provider": "second",
            "content_filter_fallback_second_model": "small",
            "content_filter_fallback_second_temperature": 0.8,
        })

        self.assertEqual(
            ConsistencyEngine._run_consistency_content_filter_fallback(eng, "PROMPT", config),
            "SECOND_OK",
        )
        self.assertEqual([key for key, _ in eng.calls], ["g1", "s1"])

    def test_transient_rotates(self):
        eng = FakeEngine()
        eng._script = [NetworkError("temp"), "RECOVERED"]
        out = ConsistencyEngine._run_consistency_content_filter_fallback(
            eng, "PROMPT", self._config()
        )
        self.assertEqual(out, "RECOVERED")
        self.assertEqual([c[0] for c in eng.calls], ["g1", "g2"])

    def test_no_keys_raises(self):
        eng = FakeEngine()
        eng.settings_manager.load_key_statuses = lambda: []
        with self.assertRaises(ce.NoFallbackKeysError):
            ConsistencyEngine._run_consistency_content_filter_fallback(
                eng, "PROMPT", self._config()
            )


if __name__ == "__main__":
    unittest.main()
