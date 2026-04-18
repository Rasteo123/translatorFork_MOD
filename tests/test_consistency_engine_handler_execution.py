import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore

from gemini_translator.api.base import BaseApiHandler
from gemini_translator.core.consistency_engine import ConsistencyEngine


class _SettingsStub:
    def __init__(self):
        self.increment_calls = []

    def is_key_limit_active(self, key_info, model_id):
        return False

    def load_proxy_settings(self):
        return None

    def increment_request_count(self, key_to_update, model_id):
        self.increment_calls.append((key_to_update, model_id))
        return True

    def decrement_request_count(self, key_to_update, model_id):
        return True


class _SyncLocalLikeHandler(BaseApiHandler):
    def __init__(self, worker):
        super().__init__(worker)
        self.execute_calls = 0

    def setup_client(self, client_override=None, proxy_settings=None):
        super().setup_client(client_override, proxy_settings)
        self.worker.api_key = client_override.api_key
        self.worker.model_id = self.worker.model_config.get("id", "fake-local-model")
        return True

    async def execute_api_call(self, *args, **kwargs):
        self.execute_calls += 1
        return await super().execute_api_call(*args, **kwargs)

    def call_api(self, prompt, log_prefix, allow_incomplete=False, use_stream=True, debug=False, max_output_tokens=None):
        return '{"ok": true}'


class ConsistencyEngineHandlerExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])

    def test_consistency_engine_routes_sync_handlers_through_execute_api_call(self):
        settings = _SettingsStub()
        engine = ConsistencyEngine(settings)
        config = {
            "provider": "local_test",
            "model": "Fake Local Model",
            "temperature": 0.3,
        }
        providers_config = {
            "local_test": {
                "handler_class": "SyncLocalLikeHandler",
                "is_async": False,
                "base_timeout": 5,
                "models": {
                    "Fake Local Model": {
                        "id": "fake-local-model",
                    }
                },
            }
        }

        with patch("gemini_translator.core.consistency_engine._load_providers_config", return_value=providers_config), \
             patch("gemini_translator.core.consistency_engine.get_api_handler_class", return_value=_SyncLocalLikeHandler):
            try:
                result = engine._call_api("prompt", config, "local-key")
                cache = engine._get_current_thread_handler_cache()
                handler = next(iter(cache.values()))["handler"]
            finally:
                engine.close_session_resources()

        self.assertEqual(result, '{"ok": true}')
        self.assertEqual(handler.execute_calls, 1)
        self.assertEqual(settings.increment_calls, [("local-key", "fake-local-model")])


if __name__ == "__main__":
    unittest.main()
